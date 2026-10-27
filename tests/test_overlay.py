import asyncio

from twitch_bot.commands import COMMANDS, CommandContext
from twitch_bot.market import MarketEngine
from twitch_bot.overlay import build_overlay_state


def test_market_command_updates_channel_sse_state():
    engine = MarketEngine(":memory:")
    try:
        context = CommandContext(
            channel_id="broadcaster-123",
            user_id="moderator-1",
            display_name="Mod",
            is_moderator=True,
            is_broadcaster=False,
            market=engine,
        )

        reply = asyncio.run(COMMANDS["market"](context, '"Will I win this round?"'))

        assert reply and "Will I win this round?" in reply
        state = build_overlay_state(engine, "broadcaster-123")
        assert state["status"] == "open"
        assert state["question"] == "Will I win this round?"
        assert build_overlay_state(engine, "another-channel")["status"] == "idle"
    finally:
        engine.close()
