# Too Good To Go dashboard

**An unofficial, local-only dashboard for discovering bags, managing a session, and watching stock.**

Personal automation project exploring API clients, background workers, and reservation state. Provided **as-is**, without warranty. Not affiliated with or endorsed by Too Good To Go.

## What is here

- Map-based discovery and favorites.
- Email/PIN login and saved-session refresh.
- Scheduled stock watchers and reservation handling.
- Optional Telegram notifications and explicit commands.
- A durable reservation journal and offline tests for response handling, session rotation, and uncertain order states.

The interesting engineering work is handling stale credentials and ambiguous requests: receipts are saved before success notifications, and uncertain order responses require checking the official app rather than blindly retrying.

## Run on your own computer

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Use one server worker. Optional Telegram configuration is shown in `.env.example`. For the full workflow and state-handling details, see [local usage](docs/LOCAL_USAGE.md).

## Disclaimer and limits

This application has **no dashboard-user authentication** and stores a local service session. Keep it bound to localhost; do not expose it through public hosting or a tunnel. API actions can reserve or cancel real bags on your account. Payment is handled in the official app. Use only with your own account and in accordance with the service's rules.

It uses unofficial endpoints; upstream changes or blocking can break login, discovery, and reservation behavior. No availability, checkout, or anti-blocking guarantee is made.

No saved login, cookies, account records, favorites snapshot, real order history, Telegram credentials, or legacy patch archive is included. The optional notebook ships with outputs cleared and network actions disabled by default. Supply your own credentials locally; never commit `temp.json`, `.env`, or runtime data.

## Offline checks

```bash
python -m unittest discover -s tests -v
node --test tests/map_session.test.cjs
```

These use fake credentials and mocked responses. They do not validate live upstream access or send Telegram messages.

## Credits

Portfolio maintained by Mathieu Gautier. The client work was informed by the upstream [tgtg-python project](https://github.com/ahivert/tgtg-python), including [PR #378](https://github.com/ahivert/tgtg-python/pull/378). Map tiles use OpenStreetMap with attribution retained in the interface. Team, upstream, and bundled dependency rights remain with their authors; this archive does not assert a new blanket license over them.
