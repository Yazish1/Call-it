import asyncio
import logging
import os

from dotenv import load_dotenv

from .auth import TwitchAuth
from .market import MarketEngine
from .overlay import overlay_server
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
    home_channel_id = os.environ["TWITCH_USER_ID"]
    configured_channels = os.getenv("CHANNEL_IDS", home_channel_id)
    channel_ids = list(dict.fromkeys(
        item.strip() for item in configured_channels.split(",") if item.strip()
    ))
    if not channel_ids:
        raise RuntimeError("CHANNEL_IDS must contain at least one broadcaster user id")
    market = MarketEngine(legacy_channel_id=home_channel_id)
    token_task = asyncio.create_task(auth.maintain_token())
    try:
        async with overlay_server(market):
            overlay_port = os.getenv("OVERLAY_PORT", "8080")
            for channel_id in channel_ids:
                logging.getLogger(__name__).info(
                    "OBS overlay for channel %s: http://127.0.0.1:%s/overlay/%s",
                    channel_id, overlay_port, channel_id,
                )
            async with TwitchClient(auth, home_channel_id, market, channel_ids) as client:
                await client.run()
    finally:
        market.close()
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
