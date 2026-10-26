import pytest

from twitch_bot.market import MarketEngine
from twitch_bot.overlay import build_overlay_state
from twitch_bot.pricing import prices, shares_for_cost, cost


def test_prices_sum_to_one():
    yes_price, no_price = prices(175.0, 40.0, 100.0)
    assert yes_price + no_price == pytest.approx(1.0)


def test_lmsr_share_solution_matches_spend():
    before = cost(0.0, 0.0, 100.0)
    shares = shares_for_cost(0.0, 0.0, "yes", 50.0, 100.0)
    after = cost(shares, 0.0, 100.0)
    assert after - before == pytest.approx(50.0, abs=1e-9)


def test_buy_sell_round_trip_restores_balance():
    engine = MarketEngine(":memory:")
    try:
        engine.touch_user("channel-a", "alice", "Alice")
        engine.open_market("channel-a", "Will it happen?")
        purchase = engine.buy("channel-a", "alice", "yes", "50")
        sale = engine.sell("channel-a", "alice", "yes", repr(purchase["shares"]))
        assert sale["points"] == pytest.approx(50, abs=0.01)
        assert engine.points("channel-a", "alice")["balance"] == pytest.approx(1000, abs=0.01)
    finally:
        engine.close()


def test_resolve_pays_winning_shares():
    engine = MarketEngine(":memory:")
    try:
        engine.touch_user("channel-a", "alice", "Alice")
        engine.touch_user("channel-a", "bob", "Bob")
        engine.open_market("channel-a", "Will it happen?")
        engine.buy("channel-a", "alice", "yes", "100")
        engine.buy("channel-a", "bob", "no", "100")
        alice_shares = engine.points("channel-a", "alice")["yes_shares"]
        result = engine.resolve("channel-a", "yes")
        assert result["outcome"] == "yes"
        assert engine.points("channel-a", "alice")["cumulative_profit"] == pytest.approx(
            alice_shares - 100, abs=0.01
        )
        settled_balance = engine.connection.execute(
            "SELECT balance FROM positions WHERE channel_id='channel-a' AND user_id='alice'"
        ).fetchone()[0]
        assert settled_balance == pytest.approx(900 + alice_shares, abs=0.01)
        with pytest.raises(ValueError, match="no open market"):
            engine.current_market("channel-a")
    finally:
        engine.close()


def test_cancel_refunds_cost_basis():
    engine = MarketEngine(":memory:")
    try:
        engine.touch_user("channel-a", "alice", "Alice")
        engine.open_market("channel-a", "Will it happen?")
        engine.buy("channel-a", "alice", "yes", "125")
        result = engine.cancel("channel-a")
        assert result["question"] == "Will it happen?"
        with pytest.raises(ValueError, match="no open market"):
            engine.current_market("channel-a")
    finally:
        engine.close()


def test_same_user_has_independent_channel_markets_balances_and_profit():
    engine = MarketEngine(":memory:")
    try:
        engine.touch_user("channel-a", "shared-user", "Viewer A")
        engine.touch_user("channel-b", "shared-user", "Viewer B")
        engine.open_market("channel-a", "Channel A question")
        engine.open_market("channel-b", "Channel B question")
        engine.buy("channel-a", "shared-user", "yes", "300")

        a = engine.points("channel-a", "shared-user")
        b = engine.points("channel-b", "shared-user")
        assert a["balance"] == pytest.approx(700)
        assert b["balance"] == pytest.approx(1000)
        assert engine.current_market("channel-a")["question"] == "Channel A question"
        assert engine.current_market("channel-b")["question"] == "Channel B question"

        engine.resolve("channel-a", "yes")
        assert engine.points("channel-a", "shared-user")["cumulative_profit"] != 0
        assert engine.points("channel-b", "shared-user")["cumulative_profit"] == 0
        assert engine.leaderboard("channel-a")[0]["display_name"] == "Viewer A"
        assert engine.leaderboard("channel-b")[0]["cumulative_profit"] == 0

        engine.open_market("channel-a", "Second A market")
        engine.buy("channel-a", "shared-user", "no", "100")
        engine.cancel("channel-a")
        with pytest.raises(ValueError, match="no open market"):
            engine.current_market("channel-a")
        assert engine.current_market("channel-b")["question"] == "Channel B question"
        assert engine.points("channel-b", "shared-user")["balance"] == pytest.approx(1000)
    finally:
        engine.close()


def test_overlay_state_is_channel_scoped_and_tracks_trade_history():
    engine = MarketEngine(":memory:")
    try:
        engine.touch_user("channel-a", "alice", "Alice")
        engine.touch_user("channel-b", "bob", "Bob")
        engine.open_market("channel-a", "A question")
        engine.open_market("channel-b", "B question")
        engine.buy("channel-a", "alice", "yes", "100")
        engine.buy("channel-b", "bob", "no", "200")

        state_a = build_overlay_state(engine, "channel-a")
        state_b = build_overlay_state(engine, "channel-b")
        assert state_a["question"] == "A question"
        assert state_a["status"] == "open"
        assert state_a["yes_price"] > 0.5
        assert state_a["price_history"] == [state_a["yes_price"]]
        assert state_a["trades"] == [{"user": "Alice", "side": "yes", "action": "buy",
                                      "points": 100}]
        assert state_b["question"] == "B question"
        assert state_b["yes_price"] < 0.5
        assert state_b["trades"][0]["user"] == "Bob"
        assert all(entry["user"] != "Bob" for entry in state_a["trades"])
        assert all(entry["user"] != "Alice" for entry in state_b["trades"])

        engine.resolve("channel-a", "yes")
        resolved = build_overlay_state(engine, "channel-a")
        assert resolved["status"] == "resolved"
        assert resolved["last_result"]["outcome"] == "yes"
        assert build_overlay_state(engine, "channel-b")["status"] == "open"
    finally:
        engine.close()


def test_overlay_leaderboard_uses_live_profit_then_final_profit():
    engine = MarketEngine(":memory:")
    try:
        engine.touch_user("channel-a", "alice", "Alice")
        engine.touch_user("channel-a", "bob", "Bob")
        engine.open_market("channel-a", "Live ranking market")
        engine.buy("channel-a", "alice", "yes", "300")
        engine.buy("channel-a", "bob", "no", "200")

        market = engine.current_market("channel-a")
        expected = {}
        for user_id, name in (("alice", "Alice"), ("bob", "Bob")):
            position = engine.connection.execute(
                "SELECT * FROM positions WHERE channel_id=? AND market_id=? AND user_id=?",
                ("channel-a", market["id"], user_id),
            ).fetchone()
            past_profit = engine.connection.execute(
                "SELECT cumulative_profit FROM users WHERE channel_id=? AND user_id=?",
                ("channel-a", user_id),
            ).fetchone()[0]
            expected[name] = round(
                past_profit + position["balance"]
                + position["yes_shares"] * market["yes"]
                + position["no_shares"] * market["no"] - position["stake"], 2
            )

        state = build_overlay_state(engine, "channel-a")
        assert {row["user"]: row["profit"] for row in state["leaderboard"]} == pytest.approx(
            expected
        )
        assert [row["user"] for row in state["leaderboard"]] == sorted(
            expected, key=expected.get, reverse=True
        )

        engine.resolve("channel-a", "yes")
        final = build_overlay_state(engine, "channel-a")
        profits = {
            row["display_name"]: row["cumulative_profit"]
            for row in engine.leaderboard("channel-a")
        }
        assert {row["user"]: row["profit"] for row in final["leaderboard"]} == {
            name: pytest.approx(profits[name]) for name in profits
        }
    finally:
        engine.close()


def test_single_channel_migration_assigns_legacy_rows():
    import sqlite3
    from pathlib import Path
    from uuid import uuid4

    database_path = Path(__file__).with_name(f".legacy-{uuid4().hex}.db")
    connection = None
    engine = None
    try:
        connection = sqlite3.connect(database_path)
        connection.executescript("""
        CREATE TABLE users(twitch_user_id TEXT PRIMARY KEY, display_name TEXT,
                           balance REAL, last_award_minute INTEGER);
        CREATE TABLE markets(id INTEGER PRIMARY KEY, question TEXT, status TEXT,
                             outcome TEXT, created_at TEXT, resolved_at TEXT,
                             liquidity_b REAL);
        CREATE TABLE positions(market_id INTEGER REFERENCES markets(id),
                               user_id TEXT REFERENCES users(twitch_user_id), yes_shares REAL,
                               no_shares REAL, cost_basis REAL,
                               PRIMARY KEY(market_id,user_id));
        CREATE TABLE trades(id INTEGER PRIMARY KEY, market_id INTEGER REFERENCES markets(id),
                            user_id TEXT REFERENCES users(twitch_user_id),
                            action TEXT, side TEXT, shares REAL, price REAL, points REAL,
                            created_at TEXT);
        CREATE INDEX trades_market_idx ON trades(market_id);
        INSERT INTO users VALUES ('viewer','Viewer',1000,123);
        INSERT INTO markets VALUES (4,'Legacy market','open',NULL,'2026-01-01',NULL,100);
        INSERT INTO positions VALUES (4,'viewer',2,0,20);
        INSERT INTO trades VALUES (8,4,'viewer','buy','yes',2,0.5,20,'2026-01-01');
        """)
        connection.close()

        engine = MarketEngine(database_path, legacy_channel_id="home-channel")
        assert engine.current_market("home-channel")["question"] == "Legacy market"
        migrated = engine.points("home-channel", "viewer")
        assert migrated["balance"] == pytest.approx(1000)
        assert engine.connection.execute(
            "SELECT channel_id FROM trades WHERE id=8"
        ).fetchone()[0] == "home-channel"
    finally:
        if engine is not None:
            engine.close()
        if connection is not None:
            connection.close()
        if database_path.exists():
            database_path.unlink()
