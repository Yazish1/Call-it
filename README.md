# Twitch prediction-market chat bot

Step 1 is the bot foundation: it connects to Twitch EventSub, listens for chat,
and responds to `!ping` with `pong`. Market commands and persistence are not
implemented yet.

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
