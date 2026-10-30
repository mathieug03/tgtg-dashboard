"""Durable bot-order receipts, independent of dashboard cards. No tokens stored."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time

DEFAULT_PATH = Path(__file__).resolve().parent / 'data' / 'bot_reservations.json'


def order_details(payload):
    """Normalize the top-level and wrapped order responses used by TGTG."""
    nested = payload.get('order')
    order = nested if isinstance(nested, dict) else payload
    order_id = order.get('id') or order.get('order_id') or payload.get('order_id')
    state = order.get('state') or payload.get('state')
    if order_id is None or str(order_id).lower() in {'unknown', 'none', ''}:
        order_id = None
    return str(order_id) if order_id else None, str(state or '').upper()


class ReservationStore:
    def __init__(self, path=DEFAULT_PATH):
        self.path = Path(path)
        self.lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return {}
        with self.path.open() as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError('Invalid reservation journal; restore it before using Telegram abort.')
        return data

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False) as handle:
                name = handle.name
                json.dump(data, handle, indent=2)
            os.replace(name, self.path)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)

    def record(self, order_id, item_id, user_id, reserved_at=None):
        with self.lock:
            data = self._read()
            receipt = {'order_id': str(order_id), 'item_id': str(item_id),
                       'user_id': str(user_id) if user_id is not None else None,
                       'reserved_at': reserved_at if reserved_at is not None else time.time(),
                       'status': 'RESERVED'}
            data[str(order_id)] = receipt
            self._write(data)
            return receipt.copy()

    def find(self, user_id, order_id=None):
        if user_id is None:
            return None
        with self.lock:
            candidates = [r for r in self._read().values()
                          if r['user_id'] == str(user_id) and r['status'] == 'RESERVED'
                          and (order_id is None or r['order_id'] == str(order_id))]
            return max(candidates, key=lambda r: r['reserved_at']).copy() if candidates else None

    def mark(self, order_id, status):
        with self.lock:
            data = self._read()
            if str(order_id) in data:
                data[str(order_id)]['status'] = status
                self._write(data)
