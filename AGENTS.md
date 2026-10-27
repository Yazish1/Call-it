# AGENTS.md

## Project

Kalshi-style prediction market for Twitch chat (hackathon project, built with Codex).
Viewers trade virtual points on YES/NO outcomes via chat commands. Prices move with
LMSR. Virtual points only: never add real money, purchases, or cash-out of any kind.

## Stack

- Python 3.11+, asyncio, `websockets`, `httpx`, `python-dotenv`, SQLite (stdlib)
- The OBS overlay is a small HTML/JS page served by the bot. All logic stays in Python.

## Layout

- `twitch_bot/` main package. Run with `python -m twitch_bot`
  - `auth.py` token validate and refresh
  - `twitch.py` EventSub WebSocket and Helix calls
  - `commands.py` command registry (command name -> async function)
  - `market.py` LMSR pricing and market logic (`LIQUIDITY_B` constant)
- `market.db` SQLite database (gitignored)
- Tests: `python -m pytest`

## Rules

- Never log, print, or commit secrets. `.env`, `.venv/`, `market.db` stay gitignored.
- Config comes from `.env`: TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET,
  TWITCH_ACCESS_TOKEN, TWITCH_REFRESH_TOKEN, TWITCH_USER_ID, CHANNEL_IDS (optional).
- All state is scoped per channel (broadcaster id): balances, markets, positions,
  trades, leaderboards. Channels never share data.
- Every viewer in a channel gets the same starting stake.
- Keep chat replies under 500 characters. Ignore only messages the bot sent itself
  (tracked by message_id), because the bot and broadcaster may be the same account.
- Broadcaster/mod-only commands check badges on the chat event.
- Do all balance changes inside one SQLite transaction.
- Run `python -m pytest` after changing market or pricing code. Add tests for new logic.
- Keep changes scoped to the task. Do not refactor unrelated code.

## Commands

!market "question", !odds, !buy <yes|no> <points>, !sell <yes|no> <shares>,
!points, !leaderboard, !resolve <yes|no>, !cancel, !ping,
!auto on|off|status

Auto markets are disabled by default per channel and rotate through chat volume,
common-word, and unique-chatter templates. They resolve at their deadline, or
cancel and refund if fewer than three distinct chatters participated.
`AUTO_MARKET_COOLDOWN_SECONDS` defaults to 120. Use simulator flags
`--auto --auto-duration seconds` for a short end-to-end demonstration.
Use `--demo` for looping auto-template cycles (60-second windows by default);
`--trade-rate` controls the approximate aggregate trades per second. `--demo`
implies auto mode and may be combined with `--auto`. The regular simulator
remains fixed-seed and deterministic when `--demo` is not selected.

## Codex sessions (for submission)

- Session 1: 01a176e8-602e-7131-a02c-52dc82a0fa0e
- Session 2: 01a17708-048c-76e1-8115-1f59c12a2d28
- Session 3: 01a1770e-59d6-7393-aba0-4a10817222bb
- Session 4: 01a1771b-c58c-73d2-a789-b95567a0f36e
- Session 5: 01a17b24-70da-7f42-9a92-c520a8799757
