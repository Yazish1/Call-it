import asyncio
import logging
import os

from dotenv import load_dotenv

from .auth import TwitchAuth
from .twitch import TwitchClient


async def run() -> None:
    load_dotenv()
    required = (
        "TWITCH_CLIENT_ID",
        "TWITCH_CLIENT_SECRET",
        "TWITCH_ACCESS_TOKEN",
        "TWITCH_REFRESH_TOKEN",
        "TWITCH_USER_ID",
    )
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing required .env settings: " + ", ".join(missing))

    auth = TwitchAuth.from_environment()
    await auth.validate_or_refresh()
    token_task = asyncio.create_task(auth.maintain_token())
    try:
        async with TwitchClient(auth, os.environ["TWITCH_USER_ID"]) as client:
            await client.run()
    finally:
        token_task.cancel()
        await asyncio.gather(token_task, return_exceptions=True)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Bot stopped")


if __name__ == "__main__":
    main()
