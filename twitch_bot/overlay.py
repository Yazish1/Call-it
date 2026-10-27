"""OBS browser-source overlay and channel-scoped SSE state."""

from __future__ import annotations

import asyncio
import errno
import json
import math
import os
from contextlib import asynccontextmanager
from typing import Any

from aiohttp import web

from .market import MarketEngine
from .pricing import prices


def format_countdown(seconds: float) -> str:
    """Format nonnegative seconds as a ceiling-rounded M:SS countdown."""
    remaining = max(0, math.ceil(seconds))
    return f"{remaining // 60}:{remaining % 60:02d}"


def build_overlay_state(engine: MarketEngine, channel_id: str) -> dict[str, Any]:
    """Build a JSON-safe view using only rows belonging to ``channel_id``."""
    market = engine.connection.execute(
        "SELECT * FROM markets WHERE channel_id=? ORDER BY id DESC LIMIT 1", (channel_id,)
    ).fetchone()
    open_market = market is not None and market["status"] == "open"
    history: list[float] = []
    recent: list[dict[str, Any]] = []
    yes_price = no_price = 0.5
    question = None
    status = "idle"
    if open_market:
        question = market["question"]
        status = "open"
        auto_info=engine.auto_market_info(channel_id)
        trading_closed=bool(auto_info and __import__('time').time() >= float(auto_info['deadline'])-int(auto_info['lock_seconds']))
        trade_rows = engine.connection.execute(
            """SELECT t.user_id,u.display_name,t.action,t.side,t.shares,t.points
               FROM trades t JOIN users u ON u.channel_id=t.channel_id AND u.user_id=t.user_id
               WHERE t.channel_id=? AND t.market_id=? ORDER BY t.id""",
            (channel_id, market["id"]),
        ).fetchall()
        q_yes = q_no = 0.0
        b = float(market["liquidity_b"])
        for row in trade_rows:
            signed = float(row["shares"]) * (1 if row["action"] == "buy" else -1)
            if row["side"] == "yes":
                q_yes += signed
            else:
                q_no += signed
            yes_price, no_price = prices(q_yes, q_no, b)
            history.append(yes_price)
    elif market is not None:
        question = market["question"]
        status = market["status"]

    recent_rows = engine.connection.execute(
        """SELECT u.display_name,t.side,t.action,t.points FROM trades t
           JOIN users u ON u.channel_id=t.channel_id AND u.user_id=t.user_id
           WHERE t.channel_id=? ORDER BY t.id DESC LIMIT 5""", (channel_id,)
    ).fetchall()
    recent = [
        {"user": row["display_name"], "side": row["side"], "action": row["action"],
         "points": round(float(row["points"]))}
        for row in recent_rows
    ]

    if open_market:
        leaderboard_rows = engine.connection.execute(
            """SELECT u.display_name,
                      u.cumulative_profit + COALESCE(p.balance,0)
                      + COALESCE(p.yes_shares,0) * ? + COALESCE(p.no_shares,0) * ?
                      - COALESCE(p.stake,0) AS live_profit
               FROM users u LEFT JOIN positions p
                 ON p.channel_id=u.channel_id AND p.user_id=u.user_id AND p.market_id=?
               WHERE u.channel_id=? ORDER BY live_profit DESC,u.display_name LIMIT 3""",
            (yes_price, no_price, market["id"], channel_id),
        ).fetchall()
    else:
        leaderboard_rows = engine.connection.execute(
            "SELECT display_name,cumulative_profit AS live_profit FROM users WHERE channel_id=? "
            "ORDER BY live_profit DESC,display_name LIMIT 3", (channel_id,)
        ).fetchall()
    leaderboard = [
        {"user": row["display_name"], "profit": round(float(row["live_profit"]), 2)}
        for row in leaderboard_rows
    ]
    resolved = engine.connection.execute(
        "SELECT question,outcome,resolved_at FROM markets "
        "WHERE channel_id=? AND status='resolved' ORDER BY id DESC LIMIT 1", (channel_id,)
    ).fetchone()
    return {
        "question": question, "status": status, "yes_price": yes_price,
        "no_price": no_price, "price_history": history, "trades": recent,
        "leaderboard": leaderboard,
        "last_result": ({"question": resolved["question"], "outcome": resolved["outcome"],
                         "resolved_at": resolved["resolved_at"]} if resolved else None),
        "deadline": (float(engine.auto_market_info(channel_id)["deadline"]) if engine.auto_market_info(channel_id) else None),
        "trading_closed": trading_closed if open_market else False,
    }


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
*{box-sizing:border-box}html,body{margin:0;width:480px;height:600px;overflow:hidden;background:transparent;color:#f5f7fb;font:15px/1.35 Inter,Segoe UI,Arial,sans-serif}.panel{position:relative;overflow:hidden;width:480px;height:600px;padding:22px;border:1px solid #ffffff20;border-radius:22px;background:linear-gradient(145deg,#111827ed,#070b13e8);box-shadow:0 18px 50px #0008;display:flex;flex-direction:column;gap:15px}.eyebrow{color:#93a4bc;font-size:11px;font-weight:700;letter-spacing:.16em;text-transform:uppercase}.countdown{font-size:15px;font-weight:800;color:#6ee7b7}.countdown.urgent{color:#fb7185;text-shadow:0 0 12px #fb718588}.question{font-size:22px;font-weight:750;line-height:1.2;min-height:54px}.idle{margin:auto;text-align:center;color:#fff;font-size:24px;font-weight:800;text-shadow:0 2px 14px #42e6a3aa}.idle small{display:block;margin-top:10px;color:#c2d2e5;font-size:14px;font-weight:600;text-shadow:none}.prices{display:flex;justify-content:space-between;font-weight:750}.yes{color:#67e8a5}.no{color:#fb7185}.bar{height:20px;background:#fb7185;border-radius:12px;overflow:hidden}.fill{height:100%;width:50%;background:linear-gradient(90deg,#10b981,#5ee6a8);transition:width .7s cubic-bezier(.2,.8,.2,1)}.section{border-top:1px solid #ffffff16;padding-top:10px}.section-title{color:#91a1b7;font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;margin-bottom:6px}canvas{display:block;width:100%;height:100px}.ticker,.leader{display:flex;flex-direction:column;gap:5px}.row{display:flex;justify-content:space-between;gap:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:13px}.muted{color:#a0aec0}.banner{position:absolute;inset:0;display:none;align-items:center;justify-content:center;text-align:center;background:#07101bef;border-radius:22px;padding:35px;opacity:0;transition:opacity .8s ease}.banner.visible{opacity:1}.banner strong{display:block;font-size:34px;margin:12px 0}.banner .eyebrow{color:#bec9d8}
</style></head><body><main class="panel"><div class="eyebrow">LIVE CHAT MARKET</div><div id="idle" class="idle" style="display:none">No open market<small>Mods: !market "question" to start</small></div><div id="content"><div id="question" class="question"></div><div id="countdown" class="countdown"></div><div class="prices"><span class="yes">YES <b id="yp">50%</b></span><span class="no"><b id="np">50%</b> NO</span></div><div class="bar"><div id="fill" class="fill"></div></div><section class="section"><div class="section-title">YES price history</div><canvas id="chart" width="432" height="100"></canvas></section><section class="section"><div class="section-title">Recent trades</div><div id="trades" class="ticker"></div></section><section class="section"><div class="section-title">Top players</div><div id="leaders" class="leader"></div></section></div><div id="banner" class="banner"><div><div class="eyebrow">MARKET RESOLVED</div><strong id="winner"></strong><span id="resolvedQuestion" class="muted"></span></div></div></main><script>
const channel=location.pathname.split('/').pop();let source,bannerTimer,autoDeadline=null;const $=id=>document.getElementById(id);function pct(v){return Math.round(v*100)+'%'}function countdown(seconds){const n=Math.max(0,Math.ceil(seconds));return Math.floor(n/60)+':'+String(n%60).padStart(2,'0')}function updateCountdown(){if(!autoDeadline)return;const left=Math.max(0,autoDeadline-Date.now()/1000),el=$('countdown');el.textContent='RESOLVES IN '+countdown(left);el.classList.toggle('urgent',left<=10)}function chart(values){const c=$('chart'),x=c.getContext('2d'),w=c.width,h=c.height;x.clearRect(0,0,w,h);x.strokeStyle='#ffffff18';x.lineWidth=1;for(let i=1;i<4;i++){x.beginPath();x.moveTo(0,h*i/4);x.lineTo(w,h*i/4);x.stroke()}x.save();x.setLineDash([4,5]);x.strokeStyle='#ffffff30';x.beginPath();x.moveTo(0,h/2);x.lineTo(w,h/2);x.stroke();x.restore();if(!values.length)values=[.5];x.beginPath();values.forEach((v,i)=>{const px=values.length===1?w/2:i*w/(values.length-1),py=h-8-Math.max(0,Math.min(1,v))*(h-16);i?x.lineTo(px,py):x.moveTo(px,py)});x.strokeStyle='#54e5a1';x.lineWidth=3;x.lineJoin='round';x.stroke();x.lineTo(w,h);x.lineTo(0,h);x.closePath();x.fillStyle='#26c28122';x.fill();x.fillStyle='#d8e5f4';x.font='11px Segoe UI,Arial';x.textAlign='right';x.fillText('YES '+pct(values[values.length-1]),w-3,12)}function render(s){const open=s.status==='open';$('idle').style.display=open?'none':'block';$('content').style.display=open?'block':'none';if(open){$('question').textContent=s.question||'';autoDeadline=s.deadline;updateCountdown();$('yp').textContent=pct(s.yes_price);$('np').textContent=pct(s.no_price);$('fill').style.width=pct(s.yes_price);chart(s.price_history);$('trades').innerHTML=s.trades.length?s.trades.map(t=>`<div class="row"><span>${esc(t.user)} ${t.action==='buy'?'bought':'sold'} <b class="${t.side}">${t.side.toUpperCase()}</b></span><span>${t.points} pts</span></div>`).join(''):'<div class="muted">No trades yet</div>';$('leaders').innerHTML=s.leaderboard.length?s.leaderboard.map((u,i)=>`<div class="row"><span>${i+1}. ${esc(u.user)}</span><span>${u.profit>=0?'+':''}${u.profit.toFixed(0)} pts</span></div>`).join(''):'<div class="muted">No players yet</div>'}const r=s.last_result,remaining=r&&r.resolved_at?10000-(Date.now()-Date.parse(r.resolved_at.replace(' ','T')+'Z')):0,show=remaining>0;$('banner').style.display=show?'flex':'none';$('banner').classList.toggle('visible',show);if(show){$('winner').textContent=r.outcome.toUpperCase()+' WINS';$('resolvedQuestion').textContent=r.question;clearTimeout(bannerTimer);bannerTimer=setTimeout(()=>{$('banner').classList.remove('visible');setTimeout(()=>$('banner').style.display='none',850)},remaining)}}function esc(v){const d=document.createElement('div');d.textContent=v;return d.innerHTML}function connect(){source=new EventSource('/events/'+encodeURIComponent(channel));source.onmessage=e=>{try{render(JSON.parse(e.data))}catch(_){}};source.onerror=()=>{source.close();setTimeout(connect,1500)}}connect();setInterval(updateCountdown,250);
</script></body></html>"""


def create_overlay_app(engine: MarketEngine) -> web.Application:
    app = web.Application()

    async def page(request: web.Request) -> web.Response:
        return web.Response(text=PAGE, content_type="text/html")

    async def events(request: web.Request) -> web.StreamResponse:
        channel_id = request.match_info["channel_id"]
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream",
                                               "Cache-Control": "no-cache",
                                               "Connection": "keep-alive",
                                               "X-Accel-Buffering": "no"})
        await response.prepare(request)
        previous = None
        last_keepalive = asyncio.get_running_loop().time()
        try:
            while True:
                state = build_overlay_state(engine, channel_id)
                encoded = json.dumps(state, separators=(",", ":"), ensure_ascii=False)
                if encoded != previous:
                    await response.write(f"data: {encoded}\n\n".encode())
                    previous = encoded
                elif asyncio.get_running_loop().time() - last_keepalive >= 10:
                    await response.write(b": keepalive\n\n")
                    last_keepalive = asyncio.get_running_loop().time()
                await asyncio.sleep(0.5)
        except (asyncio.CancelledError, ConnectionResetError, asyncio.TimeoutError):
            pass
        return response

    app.router.add_get("/overlay/{channel_id}", page)
    app.router.add_get("/events/{channel_id}", events)
    return app


@asynccontextmanager
async def overlay_server(engine: MarketEngine, port: int | None = None):
    runner = web.AppRunner(create_overlay_app(engine))
    await runner.setup()
    selected_port = port if port is not None else int(os.getenv("OVERLAY_PORT", "8080"))
    site = web.TCPSite(runner, "127.0.0.1", selected_port)
    try:
        await site.start()
    except OSError as exc:
        await runner.cleanup()
        if exc.errno == errno.EADDRINUSE:
            raise RuntimeError(
                f"Overlay port {selected_port} is already in use; set OVERLAY_PORT to a free port."
            ) from exc
        raise
    try:
        yield site
    finally:
        await runner.cleanup()
