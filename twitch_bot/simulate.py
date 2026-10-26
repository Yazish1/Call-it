"""Offline fake-viewer simulator for demos and command testing."""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import tempfile
import time
from pathlib import Path

from .commands import COMMANDS, CommandContext
from .market import MarketEngine
from .overlay import overlay_server

PERSONALITIES = ("bold", "cautious", "contrarian", "noisy")


async def dispatch(engine: MarketEngine, channel_id: str, user_id: str,
                   display_name: str, text: str, *, broadcaster: bool = False) -> str | None:
    """Build a chat-like event and call the registered command handler directly."""
    event = {
        "broadcaster_user_id": channel_id,
        "chatter_user_id": user_id,
        "chatter_user_name": display_name,
        "badges": ([{"set_id": "broadcaster"}] if broadcaster else []),
        "message": {"text": text},
    }
    message = event["message"]["text"]
    if not message.startswith("!"):
        return None
    parts = message[1:].split(maxsplit=1)
    handler = COMMANDS.get(parts[0].lower()) if parts and parts[0] else None
    if handler is None:
        return None
    event_channel = event["broadcaster_user_id"]
    event_user = event["chatter_user_id"]
    event_name = event["chatter_user_name"]
    engine.touch_user(event_channel, event_user, event_name)
    badges = {badge["set_id"] for badge in event["badges"]}
    context = CommandContext(event_channel, event_user, event_name,
                             "moderator" in badges, "broadcaster" in badges, engine)
    reply = await handler(context, parts[1] if len(parts) > 1 else "")
    if reply:
        print(f"[{channel_id}] {display_name}: {reply[:500]}")
    return reply


async def run_simulation(engine: MarketEngine, channels: list[str], viewers: int,
                         duration: float, *, seed: int | None = None,
                         speed: float = 1.0) -> dict[str, dict[str, float]]:
    """Run an offline simulation and return each channel's settlement accounting."""
    if viewers < 1:
        raise ValueError("viewers must be at least 1")
    if duration < 0:
        raise ValueError("duration must be non-negative")
    if speed <= 0:
        raise ValueError("speed must be positive")
    rng = random.Random(seed)
    results: dict[str, dict[str, float]] = {}

    for channel in channels:
        broadcaster_id = f"broadcaster-{channel}"
        await dispatch(engine, channel, broadcaster_id, f"Broadcaster-{channel}",
                       '!market "Will the demo market resolve YES?"', broadcaster=True)
        players = [(f"viewer-{i}", f"Viewer{i}", rng.choice(PERSONALITIES))
                   for i in range(viewers)]
        # Enroll everyone before the clock starts so even a zero-duration run
        # has a complete cohort with the same starting stake.
        for user_id, name, _personality in players:
            engine.touch_user(channel, user_id, name)

        # Exercise expected validation paths without disrupting the market.
        await dispatch(engine, channel, players[0][0], players[0][1], "!buy maybe 20")
        await dispatch(engine, channel, players[0][0], players[0][1], "!sell yes 2")

        deadline = time.monotonic() + duration / speed
        while time.monotonic() < deadline:
            user_id, name, personality = rng.choice(players)
            market = engine.current_market(channel)
            if personality == "bold":
                side, points = rng.choice(("yes", "no")), rng.randint(100, 400)
                command_text = f"!buy {side} {points}"
            elif personality == "cautious":
                state = engine.points(channel, user_id)
                if state["yes_shares"] + state["no_shares"] > 0 and rng.random() < 0.45:
                    side = "yes" if state["yes_shares"] else "no"
                    shares = state["yes_shares"] if side == "yes" else state["no_shares"]
                    command_text = f"!sell {side} {max(0.1, shares * rng.uniform(0.1, 0.5)):.4f}"
                else:
                    command_text = f"!buy {rng.choice(('yes', 'no'))} {rng.randint(10, 60)}"
            elif personality == "contrarian":
                side = "yes" if market["yes"] < 0.5 else "no"
                command_text = f"!buy {side} {rng.randint(20, 120)}"
            else:
                command_text = f"!buy {rng.choice(('yes', 'no'))} {rng.randint(10, 200)}"
            await dispatch(engine, channel, user_id, name, command_text)
            await asyncio.sleep(rng.uniform(0.01, 0.08) / speed)

        await dispatch(engine, channel, broadcaster_id, f"Broadcaster-{channel}",
                       "!resolve yes", broadcaster=True)
        await dispatch(engine, channel, broadcaster_id, f"Broadcaster-{channel}", "!leaderboard")

        rows = engine.connection.execute(
            "SELECT stake,balance FROM positions WHERE channel_id=? ORDER BY user_id",
            (channel,),
        ).fetchall()
        stakes = sum(float(row["stake"]) for row in rows)
        payouts = sum(float(row["balance"]) for row in rows)
        profit = sum(float(row["cumulative_profit"]) for row in engine.connection.execute(
            "SELECT cumulative_profit FROM users WHERE channel_id=?", (channel,)
        ))
        if abs(payouts - (stakes + profit)) > 0.02:
            raise AssertionError(f"{channel}: points accounting does not balance")
        print(f"[{channel}] points check: payouts {payouts:.2f} = stakes {stakes:.2f} "
              f"+ profit {profit:.2f}")
        results[channel] = {"stakes": stakes, "payouts": payouts, "profit": profit}
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an offline fake Twitch chat simulation.")
    parser.add_argument("--channel", action="append", dest="channels",
                        help="Fake channel ID; repeat to simulate multiple isolated channels")
    parser.add_argument("--viewers", type=int, default=30)
    parser.add_argument("--duration", type=float, default=10.0, metavar="SECONDS")
    parser.add_argument("--serve", action="store_true", help="Serve the OBS overlay during the simulation")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Simulation speed multiplier (2 runs roughly twice as fast)")
    args = parser.parse_args()
    channels = args.channels or ["fake-channel-a", "fake-channel-b"]
    if args.viewers < 1 or args.duration < 0 or args.speed <= 0:
        parser.error("--viewers must be positive, --duration non-negative, and --speed positive")
    # A temporary file-backed DB keeps the simulator independent of market.db.
    with tempfile.TemporaryDirectory(prefix="twitch-market-sim-") as directory:
        engine = MarketEngine(Path(directory) / "simulation.sqlite3")
        try:
            async def run() -> None:
                if args.serve:
                    async with overlay_server(engine):
                        port = int(os.getenv("OVERLAY_PORT", "8080"))
                        for channel in channels:
                            print(f"[{channel}] OBS overlay: http://127.0.0.1:{port}/overlay/{channel}")
                        await run_simulation(engine, channels, args.viewers, args.duration,
                                             speed=args.speed)
                else:
                    await run_simulation(engine, channels, args.viewers, args.duration,
                                         speed=args.speed)
            asyncio.run(run())
        finally:
            engine.close()


if __name__ == "__main__":
    main()
