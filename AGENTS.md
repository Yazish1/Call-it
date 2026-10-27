# AGENTS.md

## Project

Prediction market for Twitch chat (hackathon project, built with Codex).
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
!auto off|suggest|on, !approve, !skip, !auto templates <list>, !auto window <seconds>, !auto lock <seconds>, !auto status

Auto markets default to suggest mode per channel: a moderator/broadcaster must approve
proposals within 60 seconds. Modes are off, suggest, and on. Message-volume, emote,
and unique-chatter markets exclude traders from activity counters and lock trading
before the deadline. Settings are channel-scoped.
`AUTO_MARKET_COOLDOWN_SECONDS` defaults to 120. Use simulator flags
`--auto --auto-duration seconds` for a short end-to-end demonstration.
Use `--demo` for looping auto-template cycles (60-second windows by default);
`--trade-rate` controls the approximate aggregate trades per second. `--demo`
implies auto mode and may be combined with `--auto`. The regular simulator
remains fixed-seed and deterministic when `--demo` is not selected.
