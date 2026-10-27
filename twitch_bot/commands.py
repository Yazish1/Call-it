"""Async chat command registry and prediction-market commands."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import shlex

from .market import MarketEngine, MarketError


@dataclass(slots=True)
class CommandContext:
    channel_id: str
    user_id: str
    display_name: str
    is_moderator: bool
    is_broadcaster: bool
    market: MarketEngine


CommandHandler = Callable[[CommandContext, str], Awaitable[str | None]]
COMMANDS: dict[str, CommandHandler] = {}


def command(name: str) -> Callable[[CommandHandler], CommandHandler]:
    def register(handler: CommandHandler) -> CommandHandler:
        COMMANDS[name.lower()] = handler
        return handler
    return register


def _admin(context: CommandContext) -> bool:
    return context.is_moderator or context.is_broadcaster


def _error(error: MarketError) -> str:
    return str(error)


@command("ping")
async def ping(_context: CommandContext, _arguments: str) -> str:
    return "pong"


@command("auto")
async def auto(context: CommandContext, arguments: str) -> str:
    if not _admin(context): return "Only the broadcaster or a moderator can change auto markets."
    parts=arguments.lower().split(); action=parts[0] if parts else "status"
    try:
        if action in {"on","off","suggest"}:
            context.market.set_auto(context.channel_id,action); return f"Auto markets set to {action}."
        if action=="templates":
            context.market.configure_auto(context.channel_id,templates=parts[1:]); return "Auto templates updated."
        if action=="window" and len(parts)==2:
            context.market.configure_auto(context.channel_id,window_seconds=int(parts[1])); return "Auto window updated."
        if action=="lock" and len(parts)==2:
            context.market.configure_auto(context.channel_id,lock_seconds=int(parts[1])); return "Auto trading lock updated."
        if action=="status":
            s=context.market.auto_settings(context.channel_id)
            return f"Auto {s['mode']}; templates {s['templates']}; window {s['window_seconds']}s; lock {s['lock_seconds']}s."
    except (MarketError,ValueError) as exc: return str(exc)
    return "Usage: !auto off|suggest|on; !auto templates <list>; !auto window <30-600>; !auto lock <seconds>; !auto status"

@command("approve")
async def approve(context: CommandContext, _arguments: str) -> str:
    if not _admin(context): return "Only the broadcaster or a moderator can approve a proposal."
    try: m=context.market.approve_auto(context.channel_id)
    except MarketError as exc: return str(exc)
    return f'Auto market approved: "{m["question"]}".'

@command("skip")
async def skip(context: CommandContext, _arguments: str) -> str:
    if not _admin(context): return "Only the broadcaster or a moderator can skip a proposal."
    return "Auto market proposal skipped." if context.market.skip_auto(context.channel_id) else "There is no pending proposal."


@command("market")
async def new_market(context: CommandContext, arguments: str) -> str:
    if not _admin(context):
        return "Only the broadcaster or a moderator can open a market."
    try:
        question = " ".join(shlex.split(arguments))
        market = context.market.open_market(
            context.channel_id, question, context.user_id, context.display_name
        )
    except (MarketError, ValueError) as exc:
        return _error(exc) if isinstance(exc, MarketError) else 'Usage: !market "question"'
    return f'New market: "{market["question"]}" YES 50% / NO 50%. Buy with !buy <yes|no> <points>.'


@command("odds")
async def odds(context: CommandContext, _arguments: str) -> str:
    try:
        market = context.market.current_market(context.channel_id)
    except MarketError as exc:
        return _error(exc)
    return f'{market["question"]} — YES {market["yes"]:.0%} / NO {market["no"]:.0%}.'


@command("buy")
async def buy(context: CommandContext, arguments: str) -> str:
    parts = arguments.split()
    if len(parts) != 2:
        return "Usage: !buy <yes|no> <points>"
    try:
        result = context.market.buy(context.channel_id, context.user_id, parts[0], parts[1])
    except MarketError as exc:
        return _error(exc)
    return (f'@{context.display_name} bought {result["shares"]:.1f} '
            f'{result["side"].upper()} shares for {result["points"]:.0f} pts. '
            f'YES is now {result["yes"]:.0%}.')


@command("sell")
async def sell(context: CommandContext, arguments: str) -> str:
    parts = arguments.split()
    if len(parts) != 2:
        return "Usage: !sell <yes|no> <shares>"
    try:
        result = context.market.sell(context.channel_id, context.user_id, parts[0], parts[1])
    except MarketError as exc:
        return _error(exc)
    return (f'@{context.display_name} sold {result["shares"]:.1f} '
            f'{result["side"].upper()} shares for {result["points"]:.2f} pts. '
            f'YES is now {result["yes"]:.0%}.')


@command("points")
async def points(context: CommandContext, _arguments: str) -> str:
    result = context.market.points(context.channel_id, context.user_id)
    if result["balance"] is None:
        return (f'@{context.display_name} has no open-market bankroll; '
                f'cumulative profit {result["cumulative_profit"]:.2f} points.')
    text = (f'@{context.display_name} has {result["balance"]:.2f} market points '
            f'(cumulative profit {result["cumulative_profit"]:.2f})')
    if result["question"]:
        text += (f'; open position: {result["yes_shares"]:.1f} YES, '
                 f'{result["no_shares"]:.1f} NO')
    return text + "."


@command("leaderboard")
async def leaderboard(context: CommandContext, _arguments: str) -> str:
    rows = context.market.leaderboard(context.channel_id)
    if not rows:
        return "No players yet."
    return "Leaderboard: " + " | ".join(
        f'{index}. {row["display_name"]} {float(row["cumulative_profit"]):+.0f}'
        for index, row in enumerate(rows, 1)
    )


@command("resolve")
async def resolve(context: CommandContext, arguments: str) -> str:
    if not _admin(context):
        return "Only the broadcaster or a moderator can resolve a market."
    try:
        result = context.market.resolve(context.channel_id, arguments.strip())
    except MarketError as exc:
        return _error(exc)
    winners = ", ".join(f"{name} +{payout:.2f}" for name, payout in result["winners"])
    suffix = f" Top winners: {winners}." if winners else " No winning positions."
    return f'Market resolved {result["outcome"].upper()}: "{result["question"]}".{suffix}'


@command("cancel")
async def cancel(context: CommandContext, _arguments: str) -> str:
    if not _admin(context):
        return "Only the broadcaster or a moderator can cancel a market."
    try:
        result = context.market.cancel(context.channel_id)
    except MarketError as exc:
        return _error(exc)
    return f'Market cancelled: "{result["question"]}". Market stakes reset.'
