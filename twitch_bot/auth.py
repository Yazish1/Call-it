"""Twitch access-token validation, refresh, and local .env persistence."""

import asyncio
import logging
import os
from pathlib import Path

import httpx
from dotenv import set_key

logger = logging.getLogger(__name__)
VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"


class TwitchAuth:
    def __init__(self, client_id: str, client_secret: str, access_token: str,
                 refresh_token: str, env_path: Path | None = None) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.env_path = env_path or Path(".env")
        self.expires_at = 0.0
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=20)

    @classmethod
    def from_environment(cls) -> "TwitchAuth":
        return cls(
            os.environ["TWITCH_CLIENT_ID"], os.environ["TWITCH_CLIENT_SECRET"],
            os.environ["TWITCH_ACCESS_TOKEN"], os.environ["TWITCH_REFRESH_TOKEN"],
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def validate(self) -> int:
        response = await self._client.get(
            VALIDATE_URL, headers={"Authorization": f"OAuth {self.access_token}"}
        )
        response.raise_for_status()
        data = response.json()
        if data.get("client_id") != self.client_id:
            raise RuntimeError("The access token belongs to a different Twitch client")
        self.expires_at = asyncio.get_running_loop().time() + int(data["expires_in"])
        return int(data["expires_in"])

    async def validate_or_refresh(self) -> None:
        try:
            remaining = await self.validate()
            logger.info("Twitch access token validated")
            if remaining <= 300:
                await self.refresh()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            logger.info("Access token validation requires refresh")
            await self.refresh()

    async def refresh(self) -> None:
        async with self._lock:
            response = await self._client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            response.raise_for_status()
            data = response.json()
            new_access = data["access_token"]
            new_refresh = data.get("refresh_token", self.refresh_token)
            expires_in = int(data.get("expires_in", 0))

            # Persist the rotated pair before using it so a restart cannot lose it.
            set_key(str(self.env_path), "TWITCH_ACCESS_TOKEN", new_access)
            set_key(str(self.env_path), "TWITCH_REFRESH_TOKEN", new_refresh)
            os.environ["TWITCH_ACCESS_TOKEN"] = new_access
            os.environ["TWITCH_REFRESH_TOKEN"] = new_refresh
            self.access_token = new_access
            self.refresh_token = new_refresh
            self.expires_at = asyncio.get_running_loop().time() + expires_in
            logger.info("Twitch access token refreshed")

    async def authorization_headers(self) -> dict[str, str]:
        async with self._lock:
            if self.expires_at and self.expires_at - asyncio.get_running_loop().time() <= 300:
                # Release the lock before refresh acquires it.
                should_refresh = True
            else:
                should_refresh = False
            token = self.access_token
        if should_refresh:
            await self.refresh()
            token = self.access_token
        return {"Authorization": f"Bearer {token}", "Client-Id": self.client_id}

    async def maintain_token(self) -> None:
        """Refresh proactively even while the chat connection is idle."""
        while True:
            remaining = self.expires_at - asyncio.get_running_loop().time()
            await asyncio.sleep(max(1.0, remaining - 300.0))
            await self.refresh()
