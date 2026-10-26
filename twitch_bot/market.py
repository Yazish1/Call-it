"""Channel-scoped SQLite virtual-points prediction market."""

from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path
from typing import Any

from .pricing import cost, prices, shares_for_cost

LIQUIDITY_B = 100.0
STAKE_PER_MARKET = 1000.0


class MarketError(ValueError):
    """An expected market or command validation error."""


class MarketEngine:
    def __init__(self, database_path: str | Path = "market.db",
                 legacy_channel_id: str | None = None) -> None:
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema(legacy_channel_id)

    def close(self) -> None:
        self.connection.close()

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None

    def _has_channel_schema(self) -> bool:
        if not self._table_exists(self.connection, "users"):
            return False
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(users)")}
        return "channel_id" in columns

    @staticmethod
    def _schema_statements() -> tuple[str, ...]:
        return (
            """CREATE TABLE IF NOT EXISTS users (
                channel_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                cumulative_profit REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(channel_id,user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS markets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                question TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('open','resolved','cancelled')),
                outcome TEXT CHECK(outcome IN ('yes','no') OR outcome IS NULL),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT,
                liquidity_b REAL NOT NULL,
                UNIQUE(channel_id,id)
            )""",
            """CREATE TABLE IF NOT EXISTS positions (
                channel_id TEXT NOT NULL,
                market_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                yes_shares REAL NOT NULL DEFAULT 0,
                no_shares REAL NOT NULL DEFAULT 0,
                cost_basis REAL NOT NULL DEFAULT 0,
                balance REAL NOT NULL,
                stake REAL NOT NULL,
                PRIMARY KEY(channel_id,market_id,user_id),
                FOREIGN KEY(channel_id,market_id) REFERENCES markets(channel_id,id),
                FOREIGN KEY(channel_id,user_id) REFERENCES users(channel_id,user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                market_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                action TEXT NOT NULL CHECK(action IN ('buy','sell')),
                side TEXT NOT NULL CHECK(side IN ('yes','no')),
                shares REAL NOT NULL,
                price REAL NOT NULL,
                points REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(channel_id,market_id) REFERENCES markets(channel_id,id),
                FOREIGN KEY(channel_id,user_id) REFERENCES users(channel_id,user_id)
            )""",
            "CREATE UNIQUE INDEX IF NOT EXISTS one_open_market_per_channel "
            "ON markets(channel_id) WHERE status='open'",
            "CREATE INDEX IF NOT EXISTS trades_channel_market_idx ON trades(channel_id,market_id)",
        )

    def _create_schema(self, legacy_channel_id: str | None) -> None:
        if not self._table_exists(self.connection, "users"):
            with self.connection:
                for statement in self._schema_statements():
                    self.connection.execute(statement)
            return
        if self._has_channel_schema():
            with self.connection:
                for statement in self._schema_statements():
                    self.connection.execute(statement)
            return
        if not legacy_channel_id:
            raise RuntimeError(
                "Migrating the single-channel market database requires TWITCH_USER_ID."
            )
        self._migrate_single_channel(legacy_channel_id)

    def _migrate_single_channel(self, channel_id: str) -> None:
        """Move the original single-channel schema into the configured home channel."""
        self.connection.execute("PRAGMA foreign_keys = OFF")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            for table in ("trades", "positions", "markets", "users"):
                if self._table_exists(self.connection, table):
                    self.connection.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
            for statement in self._schema_statements()[:4]:
                self.connection.execute(statement)

            if self._table_exists(self.connection, "users_legacy"):
                self.connection.execute(
                    """INSERT INTO users(channel_id,user_id,display_name,cumulative_profit)
                       SELECT ?,twitch_user_id,display_name,balance-? FROM users_legacy""",
                    (channel_id, STAKE_PER_MARKET),
                )
            if self._table_exists(self.connection, "markets_legacy"):
                self.connection.execute(
                    """INSERT INTO markets(id,channel_id,question,status,outcome,created_at,
                       resolved_at,liquidity_b)
                       SELECT id,?,question,status,outcome,created_at,resolved_at,liquidity_b
                       FROM markets_legacy""", (channel_id,),
                )
            if self._table_exists(self.connection, "positions_legacy"):
                self.connection.execute(
                    """INSERT INTO positions(channel_id,market_id,user_id,yes_shares,no_shares,
                       cost_basis,balance,stake)
                       SELECT ?,p.market_id,p.user_id,p.yes_shares,p.no_shares,p.cost_basis,
                       CASE WHEN m.status='open' THEN COALESCE(u.balance,?) ELSE ? END,?
                       FROM positions_legacy p
                       JOIN markets_legacy m ON m.id=p.market_id
                       LEFT JOIN users_legacy u ON u.twitch_user_id=p.user_id""",
                    (channel_id, STAKE_PER_MARKET, STAKE_PER_MARKET, STAKE_PER_MARKET),
                )
            if self._table_exists(self.connection, "trades_legacy"):
                self.connection.execute(
                    """INSERT INTO trades(id,channel_id,market_id,user_id,action,side,shares,
                       price,points,created_at)
                       SELECT id,?,market_id,user_id,action,side,shares,price,points,created_at
                       FROM trades_legacy""", (channel_id,),
                )
            for table in ("trades", "positions", "markets", "users"):
                legacy = f"{table}_legacy"
                if self._table_exists(self.connection, legacy):
                    self.connection.execute(f"DROP TABLE {legacy}")
            for statement in self._schema_statements()[4:]:
                self.connection.execute(statement)
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        finally:
            self.connection.execute("PRAGMA foreign_keys = ON")

    def touch_user(self, channel_id: str, user_id: str, display_name: str) -> None:
        """Register a viewer and grant their stake for the channel's open market."""
        with self.connection:
            self.connection.execute(
                """INSERT INTO users(channel_id,user_id,display_name) VALUES (?,?,?)
                   ON CONFLICT(channel_id,user_id) DO UPDATE SET display_name=excluded.display_name""",
                (channel_id, user_id, display_name),
            )
            market = self.connection.execute(
                "SELECT id FROM markets WHERE channel_id=? AND status='open'",
                (channel_id,),
            ).fetchone()
            if market:
                self._enroll(channel_id, int(market["id"]), user_id)

    def _enroll(self, channel_id: str, market_id: int, user_id: str) -> None:
        self.connection.execute(
            """INSERT OR IGNORE INTO positions
               (channel_id,market_id,user_id,balance,stake) VALUES (?,?,?,?,?)""",
            (channel_id, market_id, user_id, STAKE_PER_MARKET, STAKE_PER_MARKET),
        )

    def _user(self, channel_id: str, user_id: str) -> sqlite3.Row:
        row = self.connection.execute(
            "SELECT * FROM users WHERE channel_id=? AND user_id=?",
            (channel_id, user_id),
        ).fetchone()
        if row is None:
            raise MarketError("Your account is not initialized yet; send a chat message first.")
        return row

    def open_market(self, channel_id: str, question: str, opener_id: str | None = None,
                    opener_name: str | None = None) -> dict[str, Any]:
        question = question.strip()
        if not question:
            raise MarketError('Usage: !market "question"')
        with self.connection:
            if opener_id:
                self.connection.execute(
                    """INSERT INTO users(channel_id,user_id,display_name) VALUES (?,?,?)
                       ON CONFLICT(channel_id,user_id) DO UPDATE SET display_name=excluded.display_name""",
                    (channel_id, opener_id, opener_name or opener_id),
                )
            if self.connection.execute(
                "SELECT 1 FROM markets WHERE channel_id=? AND status='open' LIMIT 1",
                (channel_id,),
            ).fetchone():
                raise MarketError("A market is already open in this channel.")
            cursor = self.connection.execute(
                "INSERT INTO markets(channel_id,question,status,liquidity_b) VALUES (?,?,'open',?)",
                (channel_id, question, LIQUIDITY_B),
            )
            market_id = int(cursor.lastrowid)
            self.connection.execute(
                """INSERT INTO positions(channel_id,market_id,user_id,balance,stake)
                   SELECT channel_id,?,user_id,?,? FROM users WHERE channel_id=?""",
                (market_id, STAKE_PER_MARKET, STAKE_PER_MARKET, channel_id),
            )
            return {"id": market_id, "question": question, "yes": 0.5, "no": 0.5}

    def _open_market(self, channel_id: str) -> sqlite3.Row:
        market = self.connection.execute(
            "SELECT * FROM markets WHERE channel_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (channel_id,),
        ).fetchone()
        if market is None:
            raise MarketError("There is no open market in this channel.")
        return market

    def _quantities(self, channel_id: str, market_id: int) -> tuple[float, float]:
        row = self.connection.execute(
            "SELECT COALESCE(SUM(yes_shares),0),COALESCE(SUM(no_shares),0) "
            "FROM positions WHERE channel_id=? AND market_id=?",
            (channel_id, market_id),
        ).fetchone()
        return float(row[0]), float(row[1])

    def current_market(self, channel_id: str) -> dict[str, Any]:
        market = self._open_market(channel_id)
        q_yes, q_no = self._quantities(channel_id, int(market["id"]))
        yes, no = prices(q_yes, q_no, float(market["liquidity_b"]))
        return {"id": market["id"], "question": market["question"], "yes": yes, "no": no}

    @staticmethod
    def _amount(value: str, label: str) -> int:
        try:
            amount = int(value)
        except (TypeError, ValueError):
            raise MarketError(f"{label} must be a positive integer.") from None
        if amount <= 0 or not value.strip().isdecimal():
            raise MarketError(f"{label} must be a positive integer.")
        return amount

    def buy(self, channel_id: str, user_id: str, side: str,
            amount_text: str) -> dict[str, Any]:
        side = side.lower()
        if side not in {"yes", "no"}:
            raise MarketError("Choose yes or no.")
        amount = self._amount(amount_text, "Points")
        with self.connection:
            self._user(channel_id, user_id)
            market = self._open_market(channel_id)
            market_id = int(market["id"])
            self._enroll(channel_id, market_id, user_id)
            position = self.connection.execute(
                "SELECT * FROM positions WHERE channel_id=? AND market_id=? AND user_id=?",
                (channel_id, market_id, user_id),
            ).fetchone()
            if float(position["balance"]) + 1e-9 < amount:
                raise MarketError("Your market balance is too low.")
            q_yes, q_no = self._quantities(channel_id, market_id)
            b = float(market["liquidity_b"])
            shares = shares_for_cost(q_yes, q_no, side, amount, b)
            column = "yes_shares" if side == "yes" else "no_shares"
            self.connection.execute(
                f"INSERT INTO positions(channel_id,market_id,user_id,{column},cost_basis,balance,stake) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(channel_id,market_id,user_id) DO UPDATE SET "
                f"{column}={column}+excluded.{column},cost_basis=cost_basis+excluded.cost_basis,"
                "balance=balance-excluded.cost_basis",
                (channel_id, market_id, user_id, shares, float(amount),
                 float(position["balance"]), float(position["stake"])),
            )
            self.connection.execute(
                "INSERT INTO trades(channel_id,market_id,user_id,action,side,shares,price,points) "
                "VALUES (?,?,?,'buy',?,?,?,?)",
                (channel_id, market_id, user_id, side, shares, amount / shares, amount),
            )
            yes, no = prices(*self._quantities(channel_id, market_id), b)
            return {"shares": shares, "points": amount, "yes": yes, "no": no, "side": side}

    def sell(self, channel_id: str, user_id: str, side: str,
             shares_text: str) -> dict[str, Any]:
        side = side.lower()
        if side not in {"yes", "no"}:
            raise MarketError("Choose yes or no.")
        try:
            shares = float(shares_text)
        except ValueError:
            raise MarketError("Shares must be a positive number.") from None
        if not math.isfinite(shares) or shares <= 0:
            raise MarketError("Shares must be a positive number.")
        with self.connection:
            self._user(channel_id, user_id)
            market = self._open_market(channel_id)
            market_id = int(market["id"])
            position = self.connection.execute(
                "SELECT * FROM positions WHERE channel_id=? AND market_id=? AND user_id=?",
                (channel_id, market_id, user_id),
            ).fetchone()
            column = "yes_shares" if side == "yes" else "no_shares"
            if position is None or float(position[column]) + 1e-9 < shares:
                raise MarketError(f"You do not have that many {side.upper()} shares.")
            q_yes, q_no = self._quantities(channel_id, market_id)
            b = float(market["liquidity_b"])
            before = cost(q_yes, q_no, b)
            after = cost(q_yes - shares if side == "yes" else q_yes,
                         q_no - shares if side == "no" else q_no, b)
            proceeds = round(max(0.0, before - after), 2)
            total_shares = float(position["yes_shares"]) + float(position["no_shares"])
            sold_basis = float(position["cost_basis"]) * shares / total_shares if total_shares else 0.0
            self.connection.execute(
                f"UPDATE positions SET {column}={column}-?,cost_basis=MAX(0,cost_basis-?),"
                "balance=round(balance+?,2) WHERE channel_id=? AND market_id=? AND user_id=?",
                (shares, sold_basis, proceeds, channel_id, market_id, user_id),
            )
            self.connection.execute(
                "INSERT INTO trades(channel_id,market_id,user_id,action,side,shares,price,points) "
                "VALUES (?,?,?,'sell',?,?,?,?)",
                (channel_id, market_id, user_id, side, shares, proceeds / shares, proceeds),
            )
            yes, no = prices(*self._quantities(channel_id, market_id), b)
            return {"shares": shares, "points": proceeds, "yes": yes, "no": no, "side": side}

    def points(self, channel_id: str, user_id: str) -> dict[str, Any]:
        user = self._user(channel_id, user_id)
        market = self.connection.execute(
            "SELECT id,question FROM markets WHERE channel_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (channel_id,),
        ).fetchone()
        position = None
        if market:
            position = self.connection.execute(
                "SELECT * FROM positions WHERE channel_id=? AND market_id=? AND user_id=?",
                (channel_id, market["id"], user_id),
            ).fetchone()
        return {
            "balance": float(position["balance"]) if position else None,
            "question": market["question"] if market else None,
            "yes_shares": float(position["yes_shares"]) if position else 0.0,
            "no_shares": float(position["no_shares"]) if position else 0.0,
            "cumulative_profit": float(user["cumulative_profit"]),
        }

    def leaderboard(self, channel_id: str) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT display_name,cumulative_profit FROM users WHERE channel_id=? "
            "ORDER BY cumulative_profit DESC,display_name LIMIT 5", (channel_id,),
        ).fetchall()

    def resolve(self, channel_id: str, outcome: str) -> dict[str, Any]:
        outcome = outcome.lower()
        if outcome not in {"yes", "no"}:
            raise MarketError("Usage: !resolve <yes|no>")
        with self.connection:
            market = self._open_market(channel_id)
            market_id = int(market["id"])
            positions = self.connection.execute(
                "SELECT * FROM positions WHERE channel_id=? AND market_id=?",
                (channel_id, market_id),
            ).fetchall()
            winning_column = "yes_shares" if outcome == "yes" else "no_shares"
            winners: list[tuple[str, float]] = []
            for position in positions:
                payout = round(float(position[winning_column]), 2)
                settled_balance = round(float(position["balance"]) + payout, 2)
                profit = round(settled_balance - float(position["stake"]), 2)
                self.connection.execute(
                    "UPDATE positions SET balance=? WHERE channel_id=? AND market_id=? AND user_id=?",
                    (settled_balance, channel_id, market_id, position["user_id"]),
                )
                self.connection.execute(
                    "UPDATE users SET cumulative_profit=round(cumulative_profit+?,2) "
                    "WHERE channel_id=? AND user_id=?",
                    (profit, channel_id, position["user_id"]),
                )
                if payout > 0:
                    name = self.connection.execute(
                        "SELECT display_name FROM users WHERE channel_id=? AND user_id=?",
                        (channel_id, position["user_id"]),
                    ).fetchone()[0]
                    winners.append((name, payout))
            self.connection.execute(
                "UPDATE markets SET status='resolved',outcome=?,resolved_at=CURRENT_TIMESTAMP "
                "WHERE channel_id=? AND id=?",
                (outcome, channel_id, market_id),
            )
            winners.sort(key=lambda item: item[1], reverse=True)
            return {"question": market["question"], "outcome": outcome, "winners": winners[:3]}

    def cancel(self, channel_id: str) -> dict[str, Any]:
        with self.connection:
            market = self._open_market(channel_id)
            market_id = int(market["id"])
            self.connection.execute(
                """UPDATE positions SET balance=stake,yes_shares=0,no_shares=0,cost_basis=0
                   WHERE channel_id=? AND market_id=?""", (channel_id, market_id),
            )
            self.connection.execute(
                "UPDATE markets SET status='cancelled',resolved_at=CURRENT_TIMESTAMP "
                "WHERE channel_id=? AND id=?", (channel_id, market_id),
            )
            return {"question": market["question"]}
