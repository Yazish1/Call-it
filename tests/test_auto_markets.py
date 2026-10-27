import asyncio
import time
import pytest

from twitch_bot.market import MarketEngine
from twitch_bot.overlay import format_countdown
from twitch_bot.simulate import run_auto_demo, run_demo_cycle


def test_countdown_formats_minutes_and_seconds():
    assert format_countdown(272) == "4:32"
    assert format_countdown(9) == "0:09"
    assert format_countdown(0) == "0:00"


def test_auto_demo_places_trades_and_uses_configured_window():
    engine=MarketEngine(":memory:")
    try:
        asyncio.run(run_auto_demo(engine,"demo",4,.15))
        market=engine.connection.execute("SELECT id,question,status FROM markets WHERE channel_id='demo'").fetchone()
        assert "0.15 seconds" in market["question"]
        assert engine.connection.execute("SELECT COUNT(*) FROM trades WHERE market_id=?",(market["id"],)).fetchone()[0] > 0
        assert market["status"] == "resolved"
    finally:
        engine.close()


def test_demo_cycle_trades_and_resolves_market():
    engine=MarketEngine(":memory:")
    try:
        result=asyncio.run(run_demo_cycle(engine,"demo",8,2,cycle_duration=.15,pause=0,seed=3))
        assert result["trades"] > 0
        market=engine.connection.execute("SELECT status,question,outcome FROM markets WHERE id=?",(result["market_id"],)).fetchone()
        assert market["status"]=="resolved"
        assert result["template"]==0 and market["question"].startswith("Will chat send ")
        assert engine.connection.execute("SELECT COUNT(*) FROM trades WHERE market_id=?",(result["market_id"],)).fetchone()[0]==result["trades"]
        data=engine.connection.execute("SELECT opened_at,deadline FROM auto_market_data WHERE market_id=?",(result["market_id"],)).fetchone()
        actual=engine.connection.execute("SELECT COUNT(*) FROM chat_activity WHERE channel_id='demo' AND at>=? AND at<=?",(data["opened_at"],data["deadline"])).fetchone()[0]
        threshold=int(market["question"].split("send ",1)[1].split("+",1)[0])
        assert actual==result["chat_messages"]
        assert market["outcome"]==("yes" if actual>=threshold else "no")
    finally:
        engine.close()


def test_auto_threshold_and_deadline_resolution():
    engine=MarketEngine(":memory:")
    try:
        engine.set_auto("a",True)
        for i in range(10): engine.record_chat("a",f"u{i}","hello",100+i)
        opened=engine.maybe_open_auto("a",now=110,duration=300)
        assert "11+ messages" in opened["question"]
        for i in range(3): engine.record_chat("a",f"u{i}","hello",112)
        assert engine.settle_due_auto("a",409) is None
        kind,result=engine.settle_due_auto("a",410)
        assert kind=="resolve" and result["outcome"]=="no"
        assert engine.connection.execute("SELECT status FROM markets").fetchone()[0]=="resolved"
    finally: engine.close()


def test_auto_cancels_with_fewer_than_three_chatters():
    engine=MarketEngine(":memory:")
    try:
        engine.set_auto("a",True); opened=engine.maybe_open_auto("a",now=100,duration=2)
        engine.record_chat("a","u1","hey",101); engine.record_chat("a","u2","hey",101)
        kind,_=engine.settle_due_auto("a",102)
        assert kind=="cancel"
        assert engine.connection.execute("SELECT status FROM markets").fetchone()[0]=="cancelled"
    finally: engine.close()


def test_auto_cooldown_and_channel_isolation():
    engine=MarketEngine(":memory:")
    try:
        engine.set_auto("a",True)
        first=engine.maybe_open_auto("a",now=100,duration=1)
        for i in range(3): engine.record_chat("a",str(i),"x",100.5)
        engine.settle_due_auto("a",101)
        assert engine.maybe_open_auto("a",now=150) is None
        engine.set_auto("b",True)
        assert engine.maybe_open_auto("b",now=150) is not None
        assert not engine.auto_enabled("c")
    finally: engine.close()


def test_suggest_approve_expiry_and_channel_settings():
    engine=MarketEngine(":memory:")
    try:
        for i in range(3): engine.record_chat("a",f"u{i}","Pog",100+i)
        proposal=engine.maybe_open_auto("a",now=110,duration=60)
        assert proposal["proposal"] and engine.auto_market_info("a") is None
        assert engine.approve_auto("a",now=120)["question"]==proposal["question"]
        assert engine.auto_market_info("b") is None
        engine.cancel("a")
        second=engine.maybe_open_auto("a",now=time.time()+1000,duration=60,cooldown_seconds=0)
        assert second["proposal"]
        with pytest.raises(ValueError): engine.approve_auto("a",now=time.time()+1061)
        engine.configure_auto("a",templates=["emote","unique-chatters"],window_seconds=90,lock_seconds=5)
        assert engine.auto_settings("a")["window_seconds"]==90
        assert engine.auto_settings("b")["window_seconds"]==300
    finally: engine.close()


def test_trader_excluded_and_auto_trade_lock():
    engine=MarketEngine(":memory:")
    try:
        engine.set_auto("lock","on")
        opened=engine.maybe_open_auto("lock",now=time.time(),duration=60,cooldown_seconds=0)
        engine.touch_user("lock","trader","Trader")
        engine.buy("lock","trader","yes","10")
        engine.record_chat("lock","trader","Pog Pog",time.time())
        engine.record_chat("lock","other","Pog",time.time())
        engine.touch_user("lock","other","Other")
        assert engine.connection.execute("SELECT COUNT(*) FROM chat_activity WHERE channel_id='lock'").fetchone()[0]==1
        engine.configure_auto("lock",lock_seconds=60)
        with pytest.raises(ValueError,match="Trading is closed"):
            engine.buy("lock","other","yes","10")
    finally: engine.close()
