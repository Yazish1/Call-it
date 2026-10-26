import asyncio

import pytest

from twitch_bot.market import MarketEngine
from twitch_bot.simulate import run_simulation


def test_short_simulation_conserves_points_and_isolates_channels(tmp_path):
    engine = MarketEngine(tmp_path / "simulation.sqlite3")
    try:
        results = asyncio.run(run_simulation(
            engine, ["channel-a", "channel-b"], viewers=4, duration=0.05, seed=7
        ))

        assert set(results) == {"channel-a", "channel-b"}
        for channel, accounting in results.items():
            assert accounting["payouts"] == pytest.approx(
                accounting["stakes"] + accounting["profit"], abs=0.02
            )
            assert accounting["stakes"] == pytest.approx(5000)

        assert engine.connection.execute(
            "SELECT question FROM markets WHERE channel_id='channel-a'"
        ).fetchone()[0] == "Will the demo market resolve YES?"
        assert engine.connection.execute(
            "SELECT question FROM markets WHERE channel_id='channel-b'"
        ).fetchone()[0] == "Will the demo market resolve YES?"
        assert engine.connection.execute(
            "SELECT COUNT(*) FROM positions WHERE channel_id='channel-a'"
        ).fetchone()[0] == 5
        assert engine.connection.execute(
            "SELECT COUNT(*) FROM positions WHERE channel_id='channel-b'"
        ).fetchone()[0] == 5
        # The identical viewer IDs are stored as separate channel-scoped users.
        assert engine.connection.execute(
            "SELECT COUNT(*) FROM users WHERE user_id='viewer-0'"
        ).fetchone()[0] == 2
    finally:
        engine.close()
