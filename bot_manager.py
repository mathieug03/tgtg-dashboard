"""Scheduled stock polling and reservation lifecycle. Workers start via app lifespan."""
import logging
import random
import threading
import time

from credential_store import credential_session
from reservation_store import ReservationStore, order_details
from telegram_bot import poll_telegram, send_telegram_alert
from tgtg_client import TgtgAuthError, TgtgBlockedError

logger = logging.getLogger(__name__)
TERMINAL_STATES = {'RESERVED', 'FAILED', 'STOPPED', 'ABORTED_REMOTELY', 'UNKNOWN'}


class BotState:
    def __init__(self, item_id, target_time, credentials, quantity=1):
        self.item_id = item_id
        self.target_time = target_time
        self.credentials = credentials
        self.quantity = quantity
        self.order_id = None
        self.reserved_at = None
        self.status = 'SCHEDULED' if target_time and target_time - time.time() > 30 else 'WATCHING'
        self.stop_requested = False
        self.next_poll = 0
        self.logs = []
        self.log(f'Bot initialized. Status: {self.status}')

    def log(self, message):
        self.logs.insert(0, f"[{time.strftime('%H:%M:%S')}] {message}")
        del self.logs[10:]


class BotManager:
    def __init__(self, reservations=None):
        self.snipers = {}
        self.reservations = reservations if reservations is not None else ReservationStore()
        self.lock = threading.RLock()
        self.abort_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.threads = []

    def start(self):
        if any(thread.is_alive() for thread in self.threads):
            return
        self.stop_event.clear()
        self.threads = [threading.Thread(target=self._loop, daemon=True, name='stock-poller'),
                        threading.Thread(target=poll_telegram, args=(self, self.stop_event), daemon=True, name='telegram-poller')]
        for thread in self.threads:
            thread.start()

    def stop(self):
        self.stop_event.set()
        self.stop_all()
        for thread in self.threads:
            # Let in-flight requests finish so a successful order can be journaled.
            thread.join()

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception('Unexpected bot loop failure.')
            self.stop_event.wait(1)

    def tick(self):
        with self.lock:
            bots = list(self.snipers.values())
        for bot in bots:
            with self.lock:
                if self.stop_event.is_set() or bot.stop_requested or bot.status in TERMINAL_STATES:
                    continue
                now = time.time()
                if bot.status == 'SCHEDULED':
                    if bot.target_time - now <= 30:
                        bot.status = 'WATCHING'
                        bot.log('Drop time approaching. Watching stock.')
                    continue
                if bot.status != 'WATCHING' or now < bot.next_poll:
                    continue
                jitter = random.uniform(8, 12)
                if bot.target_time:
                    until_drop = bot.target_time - now
                    if 0 < until_drop <= jitter:
                        jitter = until_drop
                    elif -30 <= until_drop <= 0:
                        jitter = random.uniform(2.5, 5.5)
                bot.next_poll = now + jitter
            self._poll(bot)

    def _poll(self, bot):
        notification = None
        try:
            with credential_session(bot.credentials) as client:
                item = client.get_item(bot.item_id)
                stock = item.get('items_available', 0)
                name = item.get('item', {}).get('name', bot.item_id)
                with self.lock:
                    if bot.stop_requested or self.stop_event.is_set():
                        return
                    if stock <= 0:
                        bot.log(f'Checked {name} — not available.')
                        return
                    bot.status = 'SNIPING'
                    bot.log(f'Detected {stock} available. Requesting reservation.')
                # A stop during this request cannot retract an already-sent order.
                data = client.create_order(bot.item_id, item_count=bot.quantity)
                order_id, state = order_details(data)
                with self.lock:
                    if not order_id or state not in {'RESERVED', 'ACTIVE'}:
                        bot.status = 'UNKNOWN'
                        bot.order_id = order_id
                        bot.log('Unrecognized reservation response. Check the official app; no automatic retry.')
                        return
                    bot.order_id = order_id
                    bot.reserved_at = time.time()
                    # Persist first: dismissing a card or reloading cannot erase the receipt.
                    self.reservations.record(order_id, bot.item_id, client.user_id, bot.reserved_at)
                    bot.status = 'RESERVED'
                    bot.log(f'Reservation confirmed. ID: {order_id}')
                notification = (f'🎯 Bag reserved!\nStore: {name}\nOrder ID: {order_id}\n'
                                f'Complete payment in the official app.\n/abort {order_id} to cancel this reservation.')
        except (TgtgBlockedError, TgtgAuthError) as error:
            with self.lock:
                bot.status = 'FAILED'
                bot.log(str(error))
        except Exception as error:
            with self.lock:
                if bot.status == 'SNIPING':
                    bot.status = 'UNKNOWN'
                    bot.log('Reservation outcome is uncertain. Check the official app; no automatic retry.')
                elif not bot.stop_requested:
                    bot.next_poll = time.time() + random.uniform(12, 16)
                    bot.log(f'Could not check stock ({type(error).__name__}). Will retry later.')
        if notification:
            send_telegram_alert(notification)

    def abort_reservation(self, order_id=None):
        """Only target receipts from this bot and the currently authenticated account."""
        with self.abort_lock, credential_session() as client:
            # Resolve account identity before selecting a receipt (including after refresh).
            client.refresh_if_needed()
            receipt = self.reservations.find(client.user_id, order_id)
            if receipt is None:
                return 'No saved bot reservation for this account. Older reservations from before this update must be managed in the official app.'
            order_id = receipt['order_id']
            data = client.get_order_status(order_id)
            _, state = order_details(data)
            if state not in {'RESERVED', 'ACTIVE'}:
                if state:
                    self.reservations.mark(order_id, state)
                return f'Order {order_id} is {state or "in an unrecognized state"}; it was not canceled. Check the official app.'
            client.abort_order(order_id)
            self.mark_order_aborted(order_id)
            return f'✅ Cancellation accepted for order {order_id}.'

    def mark_order_aborted(self, order_id):
        self.reservations.mark(order_id, 'ABORTED_REMOTELY')
        with self.lock:
            for bot in self.snipers.values():
                if bot.order_id == str(order_id):
                    bot.status = 'ABORTED_REMOTELY'
                    bot.log(f'Order {order_id} canceled.')

    def add_sniper(self, item_id, target_time, credentials):
        with self.lock:
            existing = self.snipers.get(item_id)
            if existing and existing.status not in TERMINAL_STATES:
                return False
            self.snipers[item_id] = BotState(item_id, target_time, credentials)
            return True

    def remove_sniper(self, item_id):
        with self.lock:
            bot = self.snipers.get(item_id)
            if bot is None:
                return
            if bot.status in TERMINAL_STATES:
                del self.snipers[item_id]  # The reservation journal is deliberately retained.
            else:
                bot.stop_requested = True
                if bot.status != 'SNIPING':
                    bot.status = 'STOPPED'
                bot.log('Stop requested. Any in-flight reservation still needs checking.')

    def stop_all(self):
        with self.lock:
            active_ids = [item_id for item_id, bot in self.snipers.items() if bot.status not in TERMINAL_STATES]
        for item_id in active_ids:
            self.remove_sniper(item_id)

    def get_status(self):
        with self.lock:
            return [{'item_id': bot.item_id, 'status': bot.status, 'logs': list(bot.logs),
                     'target_time': bot.target_time, 'order_id': bot.order_id,
                     'reserved_at': bot.reserved_at} for bot in self.snipers.values()]


bot_manager = BotManager()
