# Too Good To Go dashboard

A local dashboard for finding bags, managing your saved login, and scheduling stock-watching bots. Start here; the notebook is optional.

## Run

```bash
source venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

Open **http://127.0.0.1:8000**. Keep one server instance and one worker running. `--reload` is convenient while editing but stops active watchers on a restart; run without it when leaving bots scheduled.

## Login and refresh

In **Authentication**, use **Reconnect account** for an email/PIN login or **Refresh session** to renew the existing session without requesting a new PIN. The panel shows the access-token expiry.

Before authenticated requests, the client refreshes when the JWT expires within **60 seconds**. This is based on the actual expiry, not an arbitrary number of days. Idle accounts cause no refresh traffic. If expiry cannot be read, a 401 triggers one refresh attempt. Rotated tokens are saved and shared with later dashboard and bot requests. Invalid/revoked refresh tokens still require a new email/PIN login; refresh cannot guarantee permanent access.

Favorites are cached in the browser. Seeing them does not prove the session is valid now; **Refresh** makes a fresh request. An upstream 403 is a separate block, not token expiry; the bot stops after a bounded retry instead of looping indefinitely.

[login_session.ipynb](../login_session.ipynb) provides local token inspection, explicit refresh, email/PIN login, and a live favorites check. Select the project `venv` kernel. If needed:

```bash
venv/bin/python -m pip install -r requirements-notebook.txt
```

Stop the dashboard before renewing credentials in the notebook: the credential lock is in-process. After a new login, restart the dashboard and any old watchers. Your existing `temp.json` format and notebook remain supported.

## Bags and bots

Choose a map location, then open Discover or Favorites. The map uses OpenStreetMap with no API key; discovery uses a 5 km radius. Reserve manually or select **Snipe** to watch a bag, optionally around a scheduled release time.

Bots wake 30 seconds before the target time, preserving the existing polling schedule. A stop requested while checking stock prevents the subsequent reservation. A stop cannot retract a reservation request that was already sent. Unknown responses/timeouts enter **UNKNOWN** and require checking the official app; the bot does not blindly replay them.

Reservations affect your real account. Complete payment in the official app. The dashboard's four-minute countdown is only an estimate and does not verify server cancellation.

## Telegram

Configure `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` to enable notifications and commands:

| Command | Behavior |
| --- | --- |
| `/ping` | Check that the bot service is running. |
| `/status` | List active watchers. |
| `/stop_all` | Stop active watchers, retaining reservation receipts. |
| `/abort` | Select the latest recorded bot reservation for the current account and check its server state before canceling. |
| `/abort ORDER_ID` | Select that specific recorded bot reservation. Success messages include this command. |

Receipts are written to `data/bot_reservations.json` **before** sending a success notification. Dismissing a bot card or restarting does not delete them. The journal contains order/account IDs and timestamps, never tokens. Only recorded bot orders in a supported reservable state are eligible; paid orders, unknown states, and other accounts' receipts are not canceled.

Receipts from before this change cannot be reconstructed automatically. Manage those orders in the official app. Old queued Telegram commands are ignored on startup to avoid replaying an earlier cancellation.

## Where things live

| File or folder | Purpose |
| --- | --- |
| `app.py` | Web routes and explicit worker startup/shutdown. |
| `tgtg_client.py` | Upstream HTTP client, cookies, token expiry and refresh. |
| `credential_store.py` | Serialized credential rotation and atomic saving. |
| `bot_manager.py` | Stock-watching state machine and reservation handling. |
| `reservation_store.py` | Durable bot-order receipts and response normalization. |
| `telegram_bot.py` | Telegram transport, polling and command parsing. |
| `static/` | Dashboard HTML/CSS/JavaScript; map code is in `map.js`. |
| `login_session.ipynb` | Guided login/session diagnostics. |
| `scripts/check_session.py` | One supported read-only diagnostic CLI. |
| `tests/` | Offline regression tests with fake credentials and network responses. |
| `docs/IMPROVEMENTS.md` | Prioritized ideas for later, not implemented scope. |
| `archive/` | Original patch and obsolete exploratory scripts, retained for reference. |

`temp.json` is the saved login. `.env` contains optional configuration. `temp-favorite.json` is an old response snapshot, not a live favorites cache. Keep these private; they and runtime data are ignored by Git. This remains a trusted, single-user localhost application with no dashboard user authentication—do not expose it publicly.

## Diagnostics and tests

```bash
# Local expiry inspection only; no network or raw token output
venv/bin/python scripts/check_session.py status

# Read-only live requests; may renew and save credentials
venv/bin/python scripts/check_session.py favorites
venv/bin/python scripts/check_session.py orders

# Offline regression suite; no account actions or Telegram messages
venv/bin/python -m unittest discover -s tests -v
node --test tests/map_session.test.cjs
```

Historical scripts are in `archive/legacy/`, not in the test suite. Their old auth endpoints and optional dependencies are not supported.

## Upstream references

The original [upstream patch discussion](https://github.com/ahivert/tgtg-python/pull/378) came from [tgtg-python PR #378](https://github.com/ahivert/tgtg-python/pull/378). [Issue #396](https://github.com/ahivert/tgtg-python/issues/396) documents recent 403 reports and client-version workarounds; they do not guarantee checkout success. `TGTG_APK_VERSION` and `TGTG_USER_AGENT` remain configurable. The current app-version default is `26.7.2`, not a claim that it is the latest release.
