"""Small async chat-command registry."""

from collections.abc import Awaitable, Callable

CommandHandler = Callable[[str, str], Awaitable[str | None]]
COMMANDS: dict[str, CommandHandler] = {}


def command(name: str) -> Callable[[CommandHandler], CommandHandler]:
    def register(handler: CommandHandler) -> CommandHandler:
        COMMANDS[name.lower()] = handler
        return handler
    return register


@command("ping")
async def ping(_user_name: str, _arguments: str) -> str:
    return "pong"
