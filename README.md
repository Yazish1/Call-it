# Twitch prediction-market chat bot

The bot connects to Twitch EventSub and runs a virtual-points prediction market
with SQLite storage. It uses no real money.

## Requirements

- Python 3.11 or newer
- A Twitch application and a user access token authorized for EventSub chat
  (`user:read:chat` and `user:write:chat` scopes)

Create a `.env` file in the project root with these values:

```dotenv
TWITCH_CLIENT_ID=your_client_id
TWITCH_CLIENT_SECRET=your_client_secret
TWITCH_ACCESS_TOKEN=your_user_access_token
TWITCH_REFRESH_TOKEN=your_refresh_token
TWITCH_USER_ID=your_twitch_user_id
# Optional: comma-separated broadcaster user IDs (defaults to TWITCH_USER_ID)
CHANNEL_IDS=123456789,987654321
```

Keep `.env` private. It is ignored by Git. The bot updates its access and
rotating refresh tokens in this file when it refreshes the token.

## Install and run

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m twitch_bot
```

The bot validates its token at startup, maintains an EventSub WebSocket
connection, and logs connection, subscription, and command activity without
logging credentials. Press `Ctrl+C` to stop it.

## Chat commands

| Command | Use |
| --- | --- |
| `!market "question"` | Broadcaster or moderator opens a market (one at a time). |
| `!odds` | Show current YES/NO odds and the question. |
| `!buy <yes|no> <points>` | Buy shares with whole-number virtual points. |
| `!sell <yes|no> <shares>` | Sell held shares back to the market. |
| `!points` | Show your market bankroll, open-market positions, and cumulative profit. |
| `!leaderboard` | Show the top five cumulative profits in this channel. |
| `!resolve <yes|no>` | Broadcaster or moderator resolves the market and pays winners. |
| `!cancel` | Broadcaster or moderator cancels this channel's market and resets its stakes. |
| `!ping` | Check that the bot is responding. |
| `!auto on\|off\|status` | Broadcaster/moderator control for per-channel auto markets (off by default). |

The bot subscribes once to chat for each channel in `CHANNEL_IDS`. A viewer's
identity, points, positions, markets, and leaderboard are isolated by channel.
Each viewer gets 1,000 points for every market in that channel. The leaderboard
ranks cumulative profit across that channel's resolved markets. LMSR liquidity
is controlled by `LIQUIDITY_B` and the per-market bankroll by
`STAKE_PER_MARKET` in `twitch_bot/market.py`. The `market.db` database is
created automatically. Existing single-channel databases are migrated to the
channel in `TWITCH_USER_ID` at startup.

Run the market tests with `python -m pytest`.

## OBS overlay

The bot serves a transparent, channel-specific overlay on `127.0.0.1:8080`.
Set `OVERLAY_PORT` in `.env` to use a different port. In OBS, add a **Browser
Source**, set its URL to `http://127.0.0.1:8080/overlay/<channel_id>` (replace
`<channel_id>` with the broadcaster's Twitch user ID), and set width to **480**
and height to **600**. The page uses a live Server-Sent Events stream and reconnects
automatically if the connection drops.

To preview it with fake channels, run `python -m twitch_bot.simulate --serve`.
The simulator prints one overlay URL per channel. Use `--speed 2` to run the
simulation about twice as fast, or `--speed 0.5` to slow it down.
Run `python -m twitch_bot.simulate --auto --auto-duration 5 --serve` to preview
an auto market resolving after five seconds with fake chat traffic.
For looping demo markets, run `python -m twitch_bot.simulate --demo --serve`.
Each cycle lasts about 60 seconds and includes personality-driven virtual trading
and a mid-market odds swing. Set `--trade-rate 3` for about three trades per
second across all viewers; the default is two. Demo mode rotates through the real
auto-market templates; `--auto-duration 30` sets each window to 30 seconds.
`--demo` implies auto markets, and it can also be combined with `--auto`.

Moderators can enable rotating activity markets with `!auto on`. A market opens
only when the channel has no open market and the cooldown has elapsed. Markets
use recent chat volume, common chat words, and unique chatter counts; deadlines
resolve through the normal payout logic. Windows with fewer than three chatters
are cancelled and refunded. `AUTO_MARKET_COOLDOWN_SECONDS` defaults to 120.
The overlay includes a live deadline countdown.
