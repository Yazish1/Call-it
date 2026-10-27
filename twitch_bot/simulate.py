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
from .market import MarketEngine, MarketError
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


async def run_auto_demo(engine: MarketEngine, channel: str, viewers: int, duration: float) -> None:
    """Exercise an automatically opened market with short fake chat traffic."""
    engine.set_auto(channel, True)
    for i in range(max(3, viewers)):
        user=f"viewer-{i}"; engine.touch_user(channel,user,f"Viewer{i}")
        engine.record_chat(channel,user,"Pog",time.time())
    market=engine.maybe_open_auto(channel,duration=duration)
    if not market:
        raise RuntimeError("Could not open auto market")
    print(f'[{channel}] Auto market: {market["question"]}')
    deadline=time.monotonic()+duration
    trade_index=0
    while time.monotonic()<deadline:
        for i in range(max(3,viewers)):
            user=f"viewer-{i}"
            engine.record_chat(channel,user,"Pog",time.time())
            # Keep the auto market active with small, affordable virtual trades.
            if engine.connection.execute("SELECT status FROM markets WHERE id=?",(market["id"],)).fetchone()[0]=="open":
                engine.buy(channel,user,"yes" if trade_index%2==0 else "no","10")
                trade_index+=1
        await asyncio.sleep(min(.05,max(0,deadline-time.monotonic())))
    settled=engine.settle_due_auto(channel)
    if settled: print(f'[{channel}] Auto market {settled[0]}: {settled[1]}')


def _demo_profiles(viewers: int, rng: random.Random) -> list[tuple[str, str, str]]:
    """Give the demo enough bold and contrarian viewers for the staged swing."""
    bold_count=min(viewers,max(2,viewers//5))
    contra_count=min(viewers-bold_count,max(2,viewers//10))
    profiles=["bold"]*bold_count+["contrarian"]*contra_count
    remaining=viewers-len(profiles)
    profiles.extend(rng.choices(("cautious","contrarian","noisy"),weights=(3,4,3),k=remaining))
    rng.shuffle(profiles)
    return [(f"viewer-{i}",f"Viewer{i}",personality) for i,personality in enumerate(profiles)]


def _demo_side(personality: str, yes_price: float, rng: random.Random) -> str:
    if personality=="bold":
        return "yes" if yes_price>=0.5 else "no"
    if personality=="cautious":
        return "yes" if yes_price<0.5 else "no"
    if personality=="contrarian":
        return "no" if yes_price>=0.5 else "yes"
    return rng.choice(("yes","no"))


async def run_demo_cycle(engine: MarketEngine, channel: str, viewers: int,
                         trade_rate: float, *, cycle_duration: float = 60,
                         pause: float = 6, seed: int | None = None) -> dict[str, object]:
    """Run one auto-template market with simulated chat and personality-driven trades."""
    if viewers < 1 or trade_rate <= 0 or cycle_duration <= 0:
        raise ValueError("viewers, trade rate, and cycle duration must be positive")
    rng=random.Random(seed)
    engine.set_auto(channel,True)
    players=_demo_profiles(viewers,rng)
    for user_id,name,_ in players: engine.touch_user(channel,user_id,name)
    market=engine.maybe_open_auto(channel,duration=cycle_duration,cooldown_seconds=0)
    if not market: raise RuntimeError("Auto market could not open for the demo cycle")
    info=engine.auto_market_info(channel)
    template=int(info["template"])
    if template==0:
        threshold=int(market["question"].split("send ",1)[1].split("+",1)[0])
    elif template==1:
        import re
        match=re.search(r"post '(.+)' (\d+)",market["question"])
        emote,threshold=match.group(1),int(match.group(2))
    else:
        threshold=int(market["question"].split("Will ",1)[1].split("+",1)[0])
    print(f'[{channel}] Auto market opened: "{market["question"]}"')
    started=time.monotonic(); deadline=started+cycle_duration; trade_count=0
    bold=[p for p in players if p[2]=="bold"]
    contrarians=[p for p in players if p[2]=="contrarian"]
    news_announced=False; pushback_announced=False
    news_at=cycle_duration*.35; pushback_at=cycle_duration*.55
    if template==0: news_message="Chat is picking up speed"
    elif template==1: news_message=f"Emote spam incoming: {emote}"
    else: news_message="More different viewers are joining chat"
    # Weight a small core of viewers so familiar names recur in the ticker.
    weights=[4.0 if index<min(6,len(players)) else 1.0 for index in range(len(players))]
    goes_yes=rng.choice((True,False))
    goal=max(3,threshold+rng.randint(1,max(2,threshold//5))) if goes_yes else max(3,threshold-rng.randint(max(1,threshold//5),max(1,threshold//3)))
    event_count=goal
    chat_text=emote if template==1 else "Pog"
    pool_size=max(3,min(max(viewers,1),max(3,goal)))
    chatter_ids=[f"chat-{i}" for i in range(pool_size)]
    rng.shuffle(chatter_ids)

    async def generate_chat() -> None:
        for index in range(event_count):
            scheduled=cycle_duration*.85*(index+1)/event_count
            await asyncio.sleep(max(0,scheduled-(time.monotonic()-started)))
            chatter=f"chat-{index}" if template==2 else chatter_ids[index%len(chatter_ids)]
            engine.touch_user(channel,chatter,chatter)
            engine.record_chat(channel,chatter,chat_text)

    traffic_task=asyncio.create_task(generate_chat())
    while time.monotonic()<deadline:
        elapsed=time.monotonic()-started
        if news_at<=elapsed<pushback_at and bold:
            user_id,name,personality=rng.choice(bold)
            side="yes"
            if not news_announced:
                print(f"[{channel}] NEWS: {news_message}"); news_announced=True
        elif pushback_at<=elapsed<cycle_duration*.72 and contrarians:
            user_id,name,personality=rng.choice(contrarians)
            side="no"
            if not pushback_announced:
                print(f"[{channel}] CHAT PUSHBACK: Contrarians lean against the move"); pushback_announced=True
        else:
            user_id,name,personality=rng.choices(players,weights=weights,k=1)[0]
            current=engine.current_market(channel)
            side=_demo_side(personality,float(current["yes"]),rng)

        position=engine.points(channel,user_id)
        shares=float(position["yes_shares"] if side=="yes" else position["no_shares"])
        try:
            if shares>0 and rng.random()<0.14:
                engine.sell(channel,user_id,side,f"{max(0.1,shares*rng.uniform(.12,.4)):.5f}")
                trade_count+=1
            else:
                if personality=="bold": amount=rng.randint(100,300)
                elif personality=="cautious": amount=rng.randint(10,40)
                elif personality=="noisy": amount=rng.randint(10,300)
                else: amount=rng.randint(40,180)
                affordable=int(engine.points(channel,user_id)["balance"] or 0)
                if affordable>0:
                    engine.buy(channel,user_id,side,str(min(amount,affordable)))
                    trade_count+=1
        except MarketError:
            pass
        if rng.random()<0.003:
            await dispatch(engine,channel,user_id,name,"!buy maybe 20")
        await asyncio.sleep(min(max(rng.uniform(.5,1.5)/trade_rate,.02),max(.02,deadline-time.monotonic())))

    await traffic_task
    await asyncio.sleep(max(0,float(info["deadline"])-time.time()))
    settled=engine.settle_due_auto(channel)
    if settled is None: raise RuntimeError("Auto market did not settle at its deadline")
    action,result=settled
    outcome=result.get("outcome",action)
    actual_count=event_count
    print(f'[{channel}] Auto market {action} ({outcome.upper()}): "{result["question"]}" after {trade_count} trades and {actual_count} chat messages')
    if pause>0: await asyncio.sleep(pause)
    return {"market_id":market["id"],"trades":trade_count,"outcome":outcome,
            "template":template,"threshold":threshold,"chat_messages":actual_count}


async def run_demo(engine: MarketEngine, channel: str, viewers: int,
                   trade_rate: float, auto_duration: float = 60) -> None:
    """Repeat rotating auto-template market cycles until stopped."""
    while True:
        await run_demo_cycle(engine,channel,viewers,trade_rate,cycle_duration=auto_duration)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an offline fake Twitch chat simulation.")
    parser.add_argument("--channel", action="append", dest="channels",
                        help="Fake channel ID; repeat to simulate multiple isolated channels")
    parser.add_argument("--viewers", type=int, default=30)
    parser.add_argument("--duration", type=float, default=10.0, metavar="SECONDS")
    parser.add_argument("--serve", action="store_true", help="Serve the OBS overlay during the simulation")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Simulation speed multiplier (2 runs roughly twice as fast)")
    parser.add_argument("--auto", action="store_true", help="Demonstrate an automatically generated market")
    parser.add_argument("--auto-duration", type=float, default=None, metavar="SECONDS",
                        help="Auto/demo market window (default: 60 seconds in --demo, 5 in --auto)")
    parser.add_argument("--demo", action="store_true", help="Run looping, lively 60-second demo markets")
    parser.add_argument("--trade-rate", type=float, default=2.0, metavar="TRADES_PER_SECOND",
                        help="Average demo trades per second (default: 2)")
    args = parser.parse_args()
    channels = args.channels or ["fake-channel-a", "fake-channel-b"]
    if args.viewers < 1 or args.duration < 0 or args.speed <= 0 or (args.auto_duration is not None and args.auto_duration <= 0) or args.trade_rate <= 0:
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
                        if args.demo: await run_demo(engine,channels[0],args.viewers,args.trade_rate,args.auto_duration or 60)
                        elif args.auto: await run_auto_demo(engine, channels[0], args.viewers, args.auto_duration or 5)
                        else: await run_simulation(engine, channels, args.viewers, args.duration, speed=args.speed)
                else:
                    if args.demo: await run_demo(engine,channels[0],args.viewers,args.trade_rate,args.auto_duration or 60)
                    elif args.auto: await run_auto_demo(engine, channels[0], args.viewers, args.auto_duration or 5)
                    else: await run_simulation(engine, channels, args.viewers, args.duration, speed=args.speed)
            asyncio.run(run())
        finally:
            engine.close()


if __name__ == "__main__":
    main()
