import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_403_recovery import response
from tgtg_client import TgtgApiClient, TgtgAuthError, API_URL, token_expiry
import credential_store as store


def token(subject, expiry=2000000000):
    payload = base64.urlsafe_b64encode(json.dumps({'sub':str(subject),'exp':expiry}).encode()).decode().rstrip('=')
    return 'header.' + payload + '.signature'


class RefreshTests(unittest.TestCase):
    def test_403_then_401_refreshes_and_uses_new_authorization(self):
        client = TgtgApiClient(access_token='old', refresh_token='refresh-old', cookie='datadome=old')
        with patch.object(client, '_fetch_datadome_cookie'), patch.object(client.session, 'post', side_effect=[
            response(403), response(401), response(200,{'access_token':token(7),'refresh_token':'refresh-new'}), response(200)
        ]) as post:
            client.create_order('fake')
        self.assertEqual(post.call_count, 4)
        self.assertTrue(post.call_args_list[2].args[0].endswith('token/v1/refresh'))
        self.assertEqual(post.call_args_list[3].kwargs['headers']['authorization'], 'Bearer '+token(7))
        self.assertEqual(client.user_id, 7)

    def test_missing_rotated_refresh_token_keeps_existing_one(self):
        client=TgtgApiClient(access_token='old',refresh_token='keep',cookie='datadome=old')
        with patch.object(client.session,'post',return_value=response(200,{'access_token':token(3)})):
            client.refresh_credentials()
        self.assertEqual(client.refresh_token,'keep')

    def test_refresh_rejected_does_not_recurse(self):
        client=TgtgApiClient(access_token='old',refresh_token='bad',cookie='datadome=old')
        with patch.object(client.session,'post',return_value=response(401)) as post:
            with self.assertRaises(TgtgAuthError): client.get_favorites()
        self.assertEqual(post.call_count,2)

    def test_bad_refresh_response_does_not_erase_existing_tokens(self):
        client=TgtgApiClient(access_token='old',refresh_token='keep',cookie='datadome=old')
        with patch.object(client.session,'post',return_value=response(200,{})):
            with self.assertRaises(TgtgAuthError): client.refresh_credentials()
        self.assertEqual(client.access_token,'old')
        self.assertEqual(client.refresh_token,'keep')

    def test_token_diagnostics_are_local_and_handle_non_jwt(self):
        self.assertEqual(token_expiry(token(1,1234)),1234)
        self.assertIsNone(token_expiry('not-a-jwt'))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'temp.json'
        self.patcher=patch.object(store,'CREDENTIALS_FILE',self.path)
        self.patcher.start()
        self.old={'access_token':'old','refresh_token':'r-old','cookie':'datadome=one'}
        store.save_credentials(self.old)
    def tearDown(self):
        self.patcher.stop(); self.temp.cleanup()

    def test_rotation_survives_failure_and_old_dashboard_or_bot_payload(self):
        with self.assertRaisesRegex(RuntimeError,'order failed'):
            with store.credential_session(self.old) as client:
                client.access_token='new';client.refresh_token='r-new'
                raise RuntimeError('order failed')
        self.assertEqual(store.load_credentials()['access_token'],'new')
        with store.credential_session(self.old) as client:
            self.assertEqual(client.access_token,'new')
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)

    def test_unrelated_caller_does_not_get_or_overwrite_saved_account(self):
        with store.credential_session({'access_token':'stranger','refresh_token':'other'}) as client:
            self.assertEqual(client.access_token,'stranger')
            client.access_token='unrelated-new'
        self.assertEqual(store.load_credentials()['access_token'],'old')

    def test_new_notebook_login_invalidates_old_account_aliases(self):
        store.save_credentials({'access_token':'new-account','refresh_token':'new-refresh'})
        with store.credential_session(self.old) as client:
            self.assertEqual(client.access_token,'old')
        with store.credential_session() as client:
            self.assertEqual(client.access_token,'new-account')

    def test_reads_legacy_top_level_credentials(self):
        self.path.write_text(json.dumps(self.old))
        self.assertEqual(store.load_credentials()['access_token'],'old')


class DashboardIntegrationTests(unittest.TestCase):
    setUp = PersistenceTests.setUp
    tearDown = PersistenceTests.tearDown
    def test_favorites_refresh_then_reserve_with_stale_browser_tokens(self):
        import importlib.util
        import sys
        import types
        fake_bot = types.ModuleType('bot_manager')
        fake_bot.bot_manager = object()
        spec = importlib.util.spec_from_file_location('dashboard_test', Path(__file__).resolve().parents[1] / 'app.py')
        app = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'bot_manager':fake_bot}):
            spec.loader.exec_module(app)
        with patch('tgtg_client.requests.Session.post', side_effect=[
            response(401), response(200,{'access_token':'new','refresh_token':'r-new'}),
            response(200,{'mobile_bucket':{'items':[]}}), response(200,{'id':'fake-order'})
        ]) as post:
            result=app.get_favorites(app.ItemsRequest(**self.old))
            self.assertEqual(result['status'],'success')
            result=app.create_order(app.OrderCreateRequest(**self.old,item_id='fake-item'))
            self.assertEqual(result['order']['id'],'fake-order')
        self.assertEqual(post.call_args.kwargs['headers']['authorization'],'Bearer new')
        self.assertEqual(store.load_credentials()['refresh_token'],'r-new')

class NotebookTests(unittest.TestCase):
    def test_email_pin_and_favorites_workflow_with_fake_network(self):
        import io
        from contextlib import redirect_stdout
        notebook=json.loads((Path(__file__).resolve().parents[1]/'login_session.ipynb').read_text())
        namespace={}
        with tempfile.TemporaryDirectory() as directory, patch.object(store,'CREDENTIALS_FILE',Path(directory)/'temp.json'), patch('getpass.getpass',side_effect=['fake@example.org','123456']), patch.object(TgtgApiClient,'_fetch_datadome_cookie'), patch('tgtg_client.requests.Session.post',side_effect=[
            response(200,{'polling_id':'fake-poll'}),
            response(200,{'access_token':token(1),'refresh_token':'fake-refresh'}),
            response(200,{'mobile_bucket':{'items':[]}})
        ]) as post, redirect_stdout(io.StringIO()) as output:
            for cell in notebook['cells']:
                if cell['cell_type']=='code':
                    code=''.join(cell['source']).replace('RUN_REFRESH = True', 'RUN_REFRESH = False').replace('RUN_NEW_LOGIN = False','RUN_NEW_LOGIN = True').replace('RUN_FAVORITES = False','RUN_FAVORITES = True')
                    exec(compile(code,'login_session.ipynb','exec'),namespace)
            self.assertEqual(store.load_credentials()['refresh_token'],'fake-refresh')
        self.assertEqual(post.call_count,3)
        self.assertNotIn('fake-refresh',output.getvalue())
        self.assertNotIn(token(1),output.getvalue())
        self.assertIn('Live favorites request succeeded',output.getvalue())


class ProactiveRefreshTests(unittest.TestCase):
    def test_nearly_expired_token_is_refreshed_before_favorites_request(self):
        client=TgtgApiClient(access_token=token(1,1040),refresh_token='r-old',cookie='datadome=one')
        with patch('tgtg_client.time.time',return_value=1000), patch.object(client.session,'post',side_effect=[
            response(200,{'access_token':token(1,5000),'refresh_token':'r-new'}),
            response(200,{'mobile_bucket':{'items':[]}}), response(200,{'mobile_bucket':{'items':[]}})
        ]) as post:
            client.get_favorites()
            client.get_favorites()
        self.assertTrue(post.call_args_list[0].args[0].endswith('token/v1/refresh'))
        self.assertEqual(post.call_count,3)
        self.assertEqual(post.call_args_list[1].kwargs['headers']['authorization'],'Bearer '+token(1,5000))

    def test_fresh_token_does_not_trigger_refresh(self):
        client=TgtgApiClient(access_token=token(1,5000),refresh_token='r',cookie='datadome=one')
        with patch('tgtg_client.time.time',return_value=1000), patch.object(client,'_refresh') as refresh:
            self.assertFalse(client.refresh_if_needed())
        refresh.assert_not_called()

    def test_manual_refresh_route_saves_rotation(self):
        import importlib.util
        import sys
        import types
        fake_bot=types.ModuleType('bot_manager');fake_bot.bot_manager=object()
        spec=importlib.util.spec_from_file_location('refresh_api_test',Path(__file__).resolve().parents[1]/'app.py')
        app=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'bot_manager':fake_bot}): spec.loader.exec_module(app)
        with tempfile.TemporaryDirectory() as directory, patch.object(store,'CREDENTIALS_FILE',Path(directory)/'temp.json'):
            original={'access_token':'old','refresh_token':'r-old','cookie':'datadome=one'}
            store.save_credentials(original)
            with patch('tgtg_client.requests.Session.post',return_value=response(200,{'access_token':token(1),'refresh_token':'r-new'})):
                result=app.auth_refresh(app.BaseTokensRequest(**original))
            self.assertEqual(result['status'],'success')
            self.assertEqual(store.load_credentials()['refresh_token'],'r-new')
            self.assertNotIn('access_token',result)

if __name__ == '__main__': unittest.main()
