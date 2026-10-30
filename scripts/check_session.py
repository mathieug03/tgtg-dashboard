"""One entry point for local inspection and read-only live checks; no orders created."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from credential_store import credential_session, load_credentials
from tgtg_client import token_expiry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('check', choices=['status', 'favorites', 'orders'], nargs='?', default='status')
    args = parser.parse_args()
    if args.check == 'status':
        saved = load_credentials()
        expiry = token_expiry(saved.get('access_token'))
        print('Access token saved:', bool(saved.get('access_token')))
        print('Refresh token saved:', bool(saved.get('refresh_token')))
        print('Access expiry (UTC):', datetime.fromtimestamp(expiry, timezone.utc).isoformat() if expiry else 'unknown')
        print('This was a local inspection, not an authentication check.')
        return
    try:
        with credential_session() as client:
            results = client.get_favorites() if args.check == 'favorites' else client.get_active_orders()
        print(f'Live {args.check} check succeeded: {len(results)} results.')
    except Exception as error:
        # Upstream bodies can include private details. Leave raw responses out of logs.
        print(f'Live check failed ({type(error).__name__}). Use Authentication or the login notebook.')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
