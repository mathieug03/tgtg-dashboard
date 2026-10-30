from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bot_manager import BotManager, BotState
from reservation_store import ReservationStore, order_details
from telegram_bot import handle_command, poll_telegram
import threading


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'receipts.json'
        self.store = ReservationStore(self.path)
        self.manager = BotManager(self.store)
        self.client = Mock()
        self.client.user_id = 7
        self.client.get_item.return_value = {'items_available': 1, 'item': {'name': 'Test Bag'}}
        self.client.create_order.return_value = {'id': 'order-1', 'state': 'RESERVED'}
        self.client.get_order_status.return_value = {'id':'order-1','state':'RESERVED'}

    def tearDown(self):
        self.temp.cleanup()

    def reserve(self):
        self.manager.add_sniper('item-1', None, {})
        with patch('bot_manager.credential_session', return_value=nullcontext(self.client)), patch('bot_manager.send_telegram_alert') as alert:
            def verify_receipt(message):
                self.assertTrue(self.path.exists(), 'Receipt must be saved before notification')
                self.assertEqual(self.store.find(7)['order_id'], 'order-1')
            alert.side_effect = verify_receipt
            self.manager.tick()
            alert.assert_called_once()
        return self.manager.snipers['item-1']

    def test_top_level_id_success_is_saved_before_message(self):
        bot = self.reserve()
        self.assertEqual(bot.order_id, 'order-1')
        self.assertEqual(bot.status, 'RESERVED')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn('token', self.path.read_text())

    def test_abort_survives_dismiss_and_restart(self):
        self.reserve()
        self.manager.remove_sniper('item-1')
        self.assertEqual(self.manager.get_status(), [])
        restarted = BotManager(ReservationStore(self.path))
        with patch('bot_manager.credential_session', return_value=nullcontext(self.client)):
            reply = handle_command('/abort', restarted)
        self.assertIn('Cancellation accepted', reply)
        self.client.abort_order.assert_called_once_with('order-1')
        self.assertIsNone(self.store.find(7))

    def test_abort_is_account_scoped_and_recorded_orders_only(self):
        self.store.record('other-account', 'item', 8)
        with patch('bot_manager.credential_session', return_value=nullcontext(self.client)):
            reply = handle_command('/abort other-account', self.manager)
        self.assertIn('No saved bot reservation', reply)
        self.client.abort_order.assert_not_called()
        self.client.get_order_status.assert_not_called()

    def test_paid_order_is_not_canceled(self):
        self.store.record('order-1', 'item', 7)
        self.client.get_order_status.return_value = {'state':'PAID'}
        with patch('bot_manager.credential_session', return_value=nullcontext(self.client)):
            reply = self.manager.abort_reservation()
        self.assertIn('PAID', reply)
        self.client.abort_order.assert_not_called()

    def test_latest_receipt_is_by_reservation_time_not_insertion(self):
        self.store.record('newer', 'item', 7, 200)
        self.store.record('older', 'item', 7, 100)
        self.assertEqual(self.store.find(7)['order_id'], 'newer')
        self.assertEqual(self.store.find(7, 'older')['order_id'], 'older')

    def test_aborted_order_updates_bot_and_durable_receipt(self):
        self.reserve()
        self.manager.mark_order_aborted('order-1')
        self.assertEqual(self.manager.get_status()[0]['status'], 'ABORTED_REMOTELY')
        self.assertIsNone(self.store.find(7))

    def test_unknown_or_timed_out_reservation_is_not_retried(self):
        for result in [None, {'status':'success'}, {'id':'x','state':'PAID'}]:
            with self.subTest(result=result):
                self.manager.snipers.clear()
                self.manager.add_sniper('item', None, {})
                self.client.create_order.reset_mock(side_effect=True)
                if result is None:
                    self.client.create_order.side_effect = TimeoutError()
                else:
                    self.client.create_order.return_value = result
                with patch('bot_manager.credential_session', return_value=nullcontext(self.client)), patch('bot_manager.send_telegram_alert') as alert:
                    self.manager.tick()
                    self.manager.tick()
                self.client.create_order.assert_called_once()
                alert.assert_not_called()
                self.assertEqual(self.manager.snipers['item'].status, 'UNKNOWN')

    def test_stop_during_stock_check_prevents_reservation(self):
        self.manager.add_sniper('item', None, {})
        def check(item_id):
            self.manager.remove_sniper(item_id)
            return {'items_available': 1}
        self.client.get_item.side_effect = check
        with patch('bot_manager.credential_session', return_value=nullcontext(self.client)):
            self.manager.tick()
        self.client.create_order.assert_not_called()

    def test_command_parser_does_not_treat_abort_prefix_as_command(self):
        manager = Mock()
        self.assertIsNone(handle_command('/abort_all', manager))
        manager.abort_reservation.assert_not_called()
        handle_command('/abort@mybot order-1', manager)
        manager.abort_reservation.assert_called_once_with('order-1')

    def test_order_response_variants(self):
        for payload in [{'id':'1','state':'RESERVED'}, {'order_id':'1','state':'ACTIVE'},
                        {'order':{'id':'1','state':'RESERVED'}}, {'state':'ACTIVE','order':{'order_id':'1'}}]:
            self.assertEqual(order_details(payload)[0], '1')
        self.assertIsNone(order_details({'id':'Unknown','state':'ACTIVE'})[0])

    def test_constructing_manager_never_starts_workers(self):
        with patch('threading.Thread.start') as start:
            BotManager(self.store)
        start.assert_not_called()

    def test_old_or_foreign_telegram_commands_are_not_executed(self):
        stop=threading.Event()
        manager=Mock()
        messages=[
            {'update_id':1,'message':{'date':99,'chat':{'id':'42'},'text':'/abort'}},
            {'update_id':2,'message':{'date':101,'chat':{'id':'99'},'text':'/abort'}},
            {'update_id':3,'message':{'date':101,'chat':{'id':'42'},'text':'/ping'}}]
        def reply(message): stop.set()
        with patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN':'fake','TELEGRAM_CHAT_ID':'42'}), patch('telegram_bot.time.time',return_value=100), patch('telegram_bot.requests.get',return_value=Mock(json=lambda:{'ok':True,'result':messages})), patch('telegram_bot.send_telegram_alert',side_effect=reply):
            poll_telegram(manager,stop)
        manager.abort_reservation.assert_not_called()



class WorkerLifecycleTests(unittest.TestCase):
    def test_lifespan_starts_and_stops_workers_explicitly(self):
        import asyncio
        import importlib.util
        import sys
        import types
        fake = types.ModuleType('bot_manager')
        fake.bot_manager = Mock()
        spec=importlib.util.spec_from_file_location('lifecycle_test_app',Path(__file__).resolve().parents[1]/'app.py')
        app=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'bot_manager':fake}): spec.loader.exec_module(app)
        fake.bot_manager.start.assert_not_called()
        async def run():
            async with app.lifespan(app.app):
                fake.bot_manager.start.assert_called_once()
                fake.bot_manager.stop.assert_not_called()
        asyncio.run(run())
        fake.bot_manager.stop.assert_called_once()

if __name__ == '__main__': unittest.main()
