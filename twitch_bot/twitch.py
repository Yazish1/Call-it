"""EventSub WebSocket and Helix chat operations."""

import asyncio
import json
import logging
import random
import time
from collections import deque

import httpx
import websockets
from websockets.exceptions import WebSocketException

from .auth import TwitchAuth
from .commands import COMMANDS, CommandContext
from .market import MarketEngine

logger = logging.getLogger(__name__)
EVENTSUB_URL = "wss://eventsub.wss.twitch.tv/ws"
HELIX_SUBSCRIPTIONS = "https://api.twitch.tv/helix/eventsub/subscriptions"
HELIX_CHAT_MESSAGES = "https://api.twitch.tv/helix/chat/messages"


class TwitchClient:
    def __init__(self, auth: TwitchAuth, user_id: str, market: MarketEngine,
                 channel_ids: list[str]) -> None:
        self.auth = auth
        self.user_id = user_id
        self.market = market
        self.channel_ids = tuple(dict.fromkeys(channel_ids))
        self.http = httpx.AsyncClient(timeout=20)
        self._stopping = False
        self._sent_message_ids: deque[str] = deque(maxlen=200)
        self._sent_message_id_set: set[str] = set()
        self._last_command_at: dict[tuple[str, str], float] = {}

    async def __aenter__(self) -> "TwitchClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        self._stopping = True
        await self.http.aclose()
        await self.auth.close()

    async def _helix(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        for attempt in range(2):
            headers = await self.auth.authorization_headers()
            response = await self.http.request(method, url, headers=headers, **kwargs)
            if response.status_code != 401 or attempt:
                response.raise_for_status()
                return response
            logger.info("Helix authorization expired; refreshing access token")
            await self.auth.refresh()
        raise RuntimeError("Helix request failed after token refresh")

    async def subscribe_chat(self, session_id: str) -> None:
        for channel_id in self.channel_ids:
            await self._helix(
                "POST", HELIX_SUBSCRIPTIONS,
                json={
                    "type": "channel.chat.message",
                    "version": "1",
                    "condition": {
                        "broadcaster_user_id": channel_id,
                        "user_id": self.user_id,
                    },
                    "transport": {"method": "websocket", "session_id": session_id},
                },
            )
            logger.info("Subscribed to chat messages for channel %s", channel_id)

    async def send_chat_message(self, message: str, broadcaster_id: str) -> None:
        response = await self._helix(
            "POST", HELIX_CHAT_MESSAGES,
            json={
                "broadcaster_id": broadcaster_id,
                "sender_id": self.user_id,
                "message": message,
            },
        )
        for item in response.json().get("data", []):
            message_id = item.get("message_id")
            if not message_id:
                continue
            message_id = str(message_id)
            if len(self._sent_message_ids) == self._sent_message_ids.maxlen:
                oldest = self._sent_message_ids[0]
                self._sent_message_id_set.discard(oldest)
            self._sent_message_ids.append(message_id)
            self._sent_message_id_set.add(message_id)

    async def _handle_chat(self, event: dict[str, object]) -> None:
        message_id = str(event.get("message_id", ""))
        if message_id in self._sent_message_id_set:
            return
        channel_id = str(event.get("broadcaster_user_id", ""))
        if channel_id not in self.channel_ids:
            return
        chatter_id = str(event.get("chatter_user_id", ""))
        if not chatter_id:
            return
        user_name = str(event.get("chatter_user_name", "viewer"))
        self.market.touch_user(channel_id, chatter_id, user_name)
        message = event.get("message")
        if not isinstance(message, dict):
            return
        text = message.get("text")
        if not isinstance(text, str) or not text.startswith("!"):
            return
        parts = text[1:].split(maxsplit=1)
        name = parts[0].lower() if parts else ""
        handler = COMMANDS.get(name)
        if handler is None:
            return
        arguments = parts[1] if len(parts) > 1 else ""
        logger.info("Chat command received: !%s from user %s", name, chatter_id)
        now = time.monotonic()
        rate_key = (channel_id, chatter_id)
        if now - self._last_command_at.get(rate_key, float("-inf")) < 1.0:
            return
        self._last_command_at[rate_key] = now
        badges = event.get("badges", [])
        badge_ids = {
            str(badge.get("set_id", ""))
            for badge in badges if isinstance(badge, dict)
        } if isinstance(badges, list) else set()
        context = CommandContext(
            channel_id=channel_id,
            user_id=chatter_id,
            display_name=user_name,
            is_moderator="moderator" in badge_ids,
            is_broadcaster="broadcaster" in badge_ids,
            market=self.market,
        )
        reply = await handler(context, arguments)
        if reply:
            await self.send_chat_message(reply[:500], channel_id)

    async def _session(self, url: str, is_reconnect: bool = False) -> str | None:
        async with websockets.connect(url, ping_interval=None, close_timeout=10) as ws:
            logger.info("Connected to Twitch EventSub")
            async for raw in ws:
                envelope = json.loads(raw)
                metadata = envelope.get("metadata", {})
                payload = envelope.get("payload", {})
                message_type = metadata.get("message_type")
                if message_type == "session_welcome":
                    session_id = payload["session"]["id"]
                    if not is_reconnect:
                        await self.subscribe_chat(session_id)
                    else:
                        logger.info("EventSub session reconnected")
                    is_reconnect = False
                    continue
                if message_type == "session_keepalive":
                    continue
                if message_type == "session_reconnect":
                    reconnect_url = payload["session"]["reconnect_url"]
                    logger.info("Twitch requested EventSub session reconnect")
                    return reconnect_url
                if message_type == "notification":
                    subscription = payload.get("subscription", {})
                    if subscription.get("type") == "channel.chat.message":
                        await self._handle_chat(payload.get("event", {}))
        raise ConnectionError("EventSub WebSocket closed")

    async def run(self) -> None:
        backoff = 1.0
        reconnect_url: str | None = None
        reconnecting = False
        while not self._stopping:
            try:
                requested_url = reconnect_url or EVENTSUB_URL
                replacement_url = await self._session(
                    requested_url, is_reconnect=reconnecting
                )
                if replacement_url:
                    reconnect_url = replacement_url
                    reconnecting = True
                else:
                    reconnect_url = None
                    reconnecting = False
                    backoff = 1.0
            except asyncio.CancelledError:
                raise
            except (WebSocketException, OSError, httpx.HTTPError, json.JSONDecodeError) as exc:
                logger.warning("EventSub connection ended (%s); retrying", type(exc).__name__)
                reconnect_url = None
                reconnecting = False
                delay = min(backoff, 60.0) + random.uniform(0, min(backoff * 0.2, 5.0))
                await asyncio.sleep(delay)
                backoff = min(backoff * 2, 60.0)
            # A session_reconnect URL is returned only while a replacement is expected.
            if reconnect_url:
                reconnecting = True
