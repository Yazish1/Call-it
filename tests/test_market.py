import pytest

from twitch_bot.market import MarketEngine
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
