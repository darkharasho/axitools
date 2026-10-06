"""Owner-only /access commands: revoke and restore access to the Axi apps.

Access can be revoked for people, GW2 guilds and Discord servers that violate
the Axi apps' terms of use (see README, "Access"). These commands call the
axi-config admin API with the dedicated bot token, and send the operator's
Discord id as `X-Axi-Actor: discord:<id>` on every write so the API's audit log
records who acted. They are registered only in the author's private admin
server (AXI_ADMIN_GUILD_ID), answer only AXI_OWNER_ID, and reply ephemerally,
because the list holds personal identifiers and reasons. The cog is not loaded
unless AXI_ADMIN_GUILD_ID, AXI_OWNER_ID and AXI_CONFIG_BOT_TOKEN are all set.
"""
from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from ..remote_config import DEFAULT_BASE_URL

LOGGER = logging.getLogger(__name__)

MESSAGE_LIMIT = 2000
REQUEST_TIMEOUT_SECONDS = 15

KIND_CHOICES = [
    app_commands.Choice(name="GW2 account (Name.1234)", value="gw2_account"),
    app_commands.Choice(name="GW2 guild (UUID)", value="gw2_guild"),
    app_commands.Choice(name="Discord user (ID)", value="discord_user"),
    app_commands.Choice(name="Discord server (ID)", value="discord_server"),
    app_commands.Choice(name="GitHub user (numeric ID)", value="github_user"),
]


class AdminApiError(Exception):
    """The admin API answered with an error code."""


class AdminApi:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token

    async def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        actor: int | str | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token}"}
        if actor is not None:
            headers["X-Axi-Actor"] = f"discord:{actor}"
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method,
                self.base_url + path,
                json=body,
                headers=headers,
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
                if resp.status >= 400:
                    code = data.get("error") if isinstance(data, dict) else None
                    raise AdminApiError(code if isinstance(code, str) else f"HTTP {resp.status}")
                return data if isinstance(data, dict) else {}

    async def ban(self, kind: str, value: str, reason: str | None, actor_id: int | str) -> dict[str, Any]:
        return await self._request(
            "POST", "/v1/admin/bans", {"kind": kind, "value": value, "reason": reason}, actor=actor_id
        )

    async def unban(self, ban_id: str, actor_id: int | str) -> dict[str, Any]:
        return await self._request("DELETE", f"/v1/admin/bans/{quote(ban_id, safe='')}", actor=actor_id)

    async def list_bans(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/admin/bans")


REASON_LIMIT = 200


def _describe(ban: dict[str, Any]) -> str:
    reason = ""
    if ban.get("reason"):
        text = str(ban["reason"])
        if len(text) > REASON_LIMIT:
            text = text[: REASON_LIMIT - 1] + "…"
        reason = f" — {text}"
    return f"`{ban.get('id')}` {ban.get('kind')} `{ban.get('value')}`{reason}"


class AccessAdminCog(commands.Cog):
    access = app_commands.Group(name="access", description="Revoke or restore access to the Axi apps.")

    def __init__(self, bot: commands.Bot, api: AdminApi, owner_id: int) -> None:
        self.bot = bot
        self.api = api
        self.owner_id = owner_id

    async def _owner_only(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Not allowed.", ephemeral=True)
        return False

    async def _call(self, interaction: discord.Interaction, action: str, coro) -> dict[str, Any] | None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            return await coro
        except AdminApiError as exc:
            await interaction.followup.send(f"{action} failed: {exc}", ephemeral=True)
        except (aiohttp.ClientError, TimeoutError, OSError):
            LOGGER.warning("axi-config admin API unreachable", exc_info=True)
            await interaction.followup.send(f"{action} failed: admin API unreachable.", ephemeral=True)
        return None

    async def _ban(self, interaction: discord.Interaction, kind: str, value: str, reason: str | None) -> None:
        result = await self._call(
            interaction, "Revoke", self.api.ban(kind, value, reason, interaction.user.id)
        )
        if result is None:
            return
        verb = "Revoked" if result.get("created") else "Already revoked"
        await interaction.followup.send(f"{verb}: {_describe(result.get('ban') or {})}"[:MESSAGE_LIMIT], ephemeral=True)

    @access.command(name="revoke", description="Revoke access for an identifier.")
    @app_commands.describe(kind="What kind of identifier", value="The identifier", reason="Private note")
    @app_commands.choices(kind=KIND_CHOICES)
    async def revoke(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        value: str,
        reason: str | None = None,
    ) -> None:
        if await self._owner_only(interaction):
            await self._ban(interaction, kind.value, value, reason)

    @access.command(name="revoke-user", description="Revoke access for a Discord user.")
    @app_commands.describe(user="The Discord user", reason="Private note")
    async def revoke_user(
        self, interaction: discord.Interaction, user: discord.User, reason: str | None = None
    ) -> None:
        if await self._owner_only(interaction):
            await self._ban(interaction, "discord_user", str(user.id), reason)

    @access.command(name="restore", description="Restore access by ban id.")
    @app_commands.describe(ban_id="The ban id, e.g. b_abcdefghij")
    async def restore(self, interaction: discord.Interaction, ban_id: str) -> None:
        if not await self._owner_only(interaction):
            return
        result = await self._call(interaction, "Restore", self.api.unban(ban_id, interaction.user.id))
        if result is None:
            return
        verb = "Restored" if result.get("changed") else "Already restored"
        await interaction.followup.send(f"{verb}: {_describe(result.get('ban') or {})}"[:MESSAGE_LIMIT], ephemeral=True)

    @access.command(name="list", description="List active bans.")
    async def list_cmd(self, interaction: discord.Interaction) -> None:
        if not await self._owner_only(interaction):
            return
        result = await self._call(interaction, "List", self.api.list_bans())
        if result is None:
            return
        bans = result.get("bans") or []
        if not bans:
            await interaction.followup.send("No active bans.", ephemeral=True)
            return
        lines: list[str] = []
        for index, ban in enumerate(bans):
            line = _describe(ban)
            remaining = len(bans) - index
            tail = f"\n… ({remaining} more)"
            if len("\n".join(lines + [line])) + len(tail) > MESSAGE_LIMIT:
                lines.append(tail.strip())
                break
            lines.append(line)
        await interaction.followup.send("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    guild_id = os.getenv("AXI_ADMIN_GUILD_ID", "").strip()
    owner_id = os.getenv("AXI_OWNER_ID", "").strip()
    token = os.getenv("AXI_CONFIG_BOT_TOKEN", "").strip()
    if not (guild_id.isdigit() and owner_id.isdigit() and token):
        LOGGER.info("access_admin not loaded: AXI_ADMIN_GUILD_ID, AXI_OWNER_ID and AXI_CONFIG_BOT_TOKEN are required")
        return
    base_url = os.getenv("AXI_CONFIG_URL", "").strip()
    if not base_url or base_url.lower() == "off":
        base_url = DEFAULT_BASE_URL
    await bot.add_cog(
        AccessAdminCog(bot, AdminApi(base_url, token), int(owner_id)),
        guild=discord.Object(id=int(guild_id)),
    )
