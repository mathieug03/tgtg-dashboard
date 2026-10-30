"""Telegram transport and commands. Importing this module never starts polling."""
import logging
import os
import time
import requests

logger = logging.getLogger(__name__)


def send_telegram_alert(message):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return
    try:
        result = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                               json={'chat_id': chat_id, 'text': message}, timeout=10)
        if not result.ok or not result.json().get('ok'):
            logger.warning('Telegram notification rejected (HTTP %s).', result.status_code)
    except Exception:
        # Exception URLs can contain the bot token: do not log them.
        logger.warning('Telegram notification could not be delivered.')


def handle_command(text, manager):
    parts = text.strip().split()
    if not parts:
        return None
    command = parts[0].split('@', 1)[0].lower()
    if command == '/ping':
        return '🟢 Bot service is running.'
    if command == '/status':
        active = [b for b in manager.get_status() if b['status'] in {'SCHEDULED', 'WATCHING', 'SNIPING'}]
        return '\n'.join(f"Item {b['item_id']} | {b['status']}" for b in active) or 'No snipers currently active.'
    if command == '/stop_all':
        manager.stop_all()
        return '🛑 Active snipers halted. Saved reservations are still available to /abort.'
    if command == '/abort':
        if len(parts) > 2:
            return 'Use /abort for the latest bot reservation, or /abort ORDER_ID.'
        try:
            return manager.abort_reservation(parts[1] if len(parts) == 2 else None)
        except Exception as error:
            logger.warning('Telegram abort failed (%s).', type(error).__name__)
            return 'Could not abort the recorded order. Check its state in the official app and refresh your login if needed.'
    return None


def poll_telegram(manager, stop_event):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return
    offset = 0
    started_at = int(time.time())
    while not stop_event.is_set():
        try:
            response = requests.get(f'https://api.telegram.org/bot{token}/getUpdates',
                                    params={'timeout': 5, 'offset': offset, 'allowed_updates': '["message"]'}, timeout=10)
            result = response.json()
            if not result.get('ok'):
                logger.warning('Telegram polling rejected (HTTP %s). Check for another bot instance.', response.status_code)
                stop_event.wait(5)
                continue
            for update in result.get('result', []):
                offset = update['update_id'] + 1
                message = update.get('message', {})
                if stop_event.is_set():
                    break
                if str(message.get('chat', {}).get('id')) != str(chat_id):
                    continue
                # Never replay queued cancellation commands from an earlier run.
                if message.get('date', 0) < started_at:
                    continue
                reply = handle_command(message.get('text', ''), manager)
                if reply:
                    send_telegram_alert(reply)
        except Exception:
            logger.warning('Telegram polling failed; retrying after a short pause.')
            stop_event.wait(5)
