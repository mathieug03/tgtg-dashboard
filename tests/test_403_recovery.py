"""Offline tests; never load saved credentials or send network requests."""
import importlib.util
from contextlib import nullcontext
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
from tgtg_client import TgtgApiClient, TgtgBlockedError, API_URL


def response(status, payload=None):
    result = requests.Response()
    result.status_code = status
    result._content = b'{}'
    if payload is not None:
        result.json = lambda: payload
    return result


def sent_cookie(client, url=API_URL):
    return client.session.prepare_request(requests.Request('POST', url)).headers.get('Cookie', '')


class CookieRecoveryTests(unittest.TestCase):
    def test_old_saved_format_imports_into_jar_without_attributes(self):
        client = TgtgApiClient(cookie='datadome=old; Path=/; Secure; HttpOnly; SameSite=Lax')
        self.assertNotIn('Cookie', client.session.headers)
        self.assertEqual(sent_cookie(client), 'datadome=old')
        self.assertEqual(sent_cookie(client, 'https://example.org/'), '')

    def test_existing_cookie_avoids_unnecessary_sdk_request(self):
        client = TgtgApiClient(cookie='datadome=existing')
        with patch.object(client, '_fetch_datadome_cookie') as fetch, patch.object(client.session, 'post', return_value=response(200)):
            client.get_items()
            fetch.assert_not_called()

    def test_retry_actually_sends_new_cookie_and_same_order_payload(self):
        client = TgtgApiClient(cookie='datadome=old; Path=/; Secure')
        cookies, bodies = [], []
        def post(url, **kwargs):
            cookies.append(sent_cookie(client, url))
            bodies.append(kwargs['json'])
            self.assertEqual(kwargs['timeout'], 20)
            return response(403 if len(cookies) == 1 else 200)
        def refresh(url):
            client._store_datadome_cookie('datadome=new; Path=/; Secure')
        with patch.object(client.session, 'post', side_effect=post), patch.object(client, '_fetch_datadome_cookie', side_effect=refresh) as fetch:
            client.create_order('test-only')
            fetch.assert_called_once()
        self.assertEqual(cookies, ['datadome=old', 'datadome=new'])
        self.assertEqual(bodies, [{'item_count': 1}, {'item_count': 1}])

    def test_persistent_403_is_bounded_and_does_not_expose_challenge_url(self):
        client = TgtgApiClient(cookie='datadome=old')
        with patch.object(client, '_fetch_datadome_cookie'), patch.object(client.session, 'post', return_value=response(403)) as post:
            with self.assertRaisesRegex(TgtgBlockedError, 'Stop automated retries'):
                client.create_order('test-only')
            self.assertEqual(post.call_count, 2)

    def test_timeout_does_not_replay_reservation(self):
        client = TgtgApiClient(cookie='datadome=old')
        with patch.object(client.session, 'post', side_effect=requests.Timeout) as post:
            with self.assertRaises(requests.Timeout):
                client.create_order('test-only')
            post.assert_called_once()

    def test_login_saves_sdk_cookie_if_pin_response_has_none(self):
        client = TgtgApiClient(cookie='datadome=sdk-cookie')
        with patch.object(client, '_post', return_value=response(200, {'access_token':'fake', 'refresh_token':'fake'})):
            data = client.submit_pin('fake@example.org', '000000', 'test')
        self.assertEqual(data['cookie'], 'datadome=sdk-cookie')

    def test_custom_user_agent_version_matches_sdk(self):
        with patch.dict(os.environ, {'TGTG_USER_AGENT': 'TGTG/26.7.2 test', 'TGTG_APK_VERSION':'24.11.0'}):
            client = TgtgApiClient()
        self.assertEqual(client.apk_version, '26.7.2')
        with patch('tgtg_client.requests.post', return_value=response(200, {'status':200,'cookie':'datadome=from-sdk; Secure'})) as post:
            client._fetch_datadome_cookie(API_URL)
        self.assertEqual(post.call_args.kwargs['data']['ddvc'], '26.7.2')
        self.assertEqual(sent_cookie(client), 'datadome=from-sdk')

    def test_bot_stops_after_block_at_poll_or_reservation(self):
        # Import without starting the polling or Telegram threads.
        with patch('threading.Thread.start'):
            spec = importlib.util.spec_from_file_location('bot_under_test', Path(__file__).resolve().parents[1] / 'bot_manager.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            manager = module.BotManager()
        for stage in ['poll', 'reserve']:
            with self.subTest(stage=stage):
                bot = module.BotState('test', None, {})
                manager.snipers = {'test': bot}
                client = Mock()
                if stage == 'poll':
                    client.get_item.side_effect = TgtgBlockedError()
                else:
                    client.get_item.return_value = {'items_available': 1}
                    client.create_order.side_effect = TgtgBlockedError()
                with patch.object(module, 'credential_session', return_value=nullcontext(client)):
                    manager.tick()
                self.assertEqual(bot.status, 'FAILED')
                self.assertIn('API Error 403', bot.logs[0])
                self.assertNotIn('Not yet available', bot.logs[0])

if __name__ == '__main__':
    unittest.main()
