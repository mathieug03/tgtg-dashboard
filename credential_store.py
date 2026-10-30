"""Shared, serialized credential rotation for this single-user local dashboard."""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import tempfile
import threading

from tgtg_client import TgtgApiClient

CREDENTIALS_FILE = Path(__file__).resolve().parent / 'temp.json'
_lock = threading.RLock()
_known_pairs = set()
_current_pair = None


def _pair(credentials):
    return credentials.get('access_token'), credentials.get('refresh_token')


def load_credentials():
    with _lock:
        if not CREDENTIALS_FILE.exists():
            return {}
        with CREDENTIALS_FILE.open() as handle:
            data = json.load(handle)
        return data.get('credentials', data)


def _write(credentials):
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=CREDENTIALS_FILE.parent, delete=False) as handle:
            name = handle.name
            json.dump({'status': 'success', 'credentials': credentials}, handle, indent=4)
        os.replace(name, CREDENTIALS_FILE)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def save_credentials(credentials):
    """Install a new login explicitly; old-account aliases are discarded."""
    global _current_pair
    if not all(_pair(credentials)):
        raise ValueError('Both access and refresh tokens are required.')
    with _lock:
        _write(credentials)
        _known_pairs.clear()
        _known_pairs.add(_pair(credentials))
        _current_pair = _pair(credentials)


@contextmanager
def credential_session(credentials=None):
    """Serialize rotations, reuse newer tokens, and persist even if the next call fails.

    A supplied session is linked only by exact token pairs, never unverified JWT
    identity. Unrelated API callers cannot overwrite or receive the saved login.
    Run one server worker; stop it while renewing tokens from another process.
    """
    global _current_pair
    with _lock:
        saved = load_credentials()
        if _pair(saved) != _current_pair:
            _known_pairs.clear()
            _current_pair = _pair(saved)
        if all(_pair(saved)):
            _known_pairs.add(_pair(saved))
        managed = credentials is None or _pair(credentials) in _known_pairs
        source = saved if managed else credentials
        client = TgtgApiClient(**{key: source.get(key) for key in ('access_token', 'refresh_token', 'cookie')})
        before = client.get_credentials()
        try:
            yield client
        finally:
            after = client.get_credentials()
            if managed and all(_pair(after)) and after != before:
                _write(after)
                _known_pairs.add(_pair(after))
                _current_pair = _pair(after)
