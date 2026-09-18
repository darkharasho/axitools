"""AxiBridge report-relay pairing commands."""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from ..api.server import generate_bridge_key, hash_app_key
from ..bot import AxiToolsBot

LOGGER = logging.getLogger(__name__)


class BridgeCog(
    commands.GroupCog, name="bridge", group_extras={"category": "Server Setup"}
):
    """Pair AxiBridge desktop clients to a channel in this server."""

    def __init__(self, bot: AxiToolsBot) -> None:
        super().__init__()
        self.bot = bot

    @staticmethod
    async def _check_manage_guild(interaction: discord.Interaction) -> bool:
        """Gate pairing behind Manage Server, replying ephemerally on failure."""
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used inside a server.", ephemeral=True
            )
            return False
        if not isinstance(interaction.user, discord.Member) and not hasattr(
            interaction.user, "guild_permissions"
        ):
            await interaction.response.send_message(
                "Unable to resolve your server membership.", ephemeral=True
            )
            return False
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "You need the **Manage Server** permission to pair AxiBridge.",
                ephemeral=True,
            )
            return False
        return True

    @app_commands.command(
        name="pair",
        description="Generate an AxiBridge key that posts fight reports to this channel.",
    )
    async def bridge_pair(self, interaction: discord.Interaction) -> None:
        if not await self._check_manage_guild(interaction):
            return

        # The channel is the invocation context, never a parameter: a key can
        # never be minted for a channel the invoker cannot see.
        key = generate_bridge_key()
        self.bot.storage.add_bridge_key(
            interaction.guild.id,
            interaction.channel.id,
            hash_app_key(key),
            interaction.user.id,
        )
        await interaction.response.send_message(
            f"AxiBridge key for **{interaction.guild.name}** → "
            f"{interaction.channel.mention}:\n"
            f"```\n{key}\n```\n"
            "Paste this into AxiBridge → Settings → Discord → **Link AxiTools "
            "channel**. Reports will be posted by this bot, not a webhook.\n"
            "⚠️ This key is only shown once. Use `/bridge list` to review or "
            "`/bridge revoke` to remove it.",
            ephemeral=True,
        )

    @app_commands.command(
        name="list", description="List the AxiBridge pairings for this server."
    )
    async def bridge_list(self, interaction: discord.Interaction) -> None:
        if not await self._check_manage_guild(interaction):
            return

        keys = self.bot.storage.list_bridge_keys(interaction.guild.id)
        if not keys:
            await interaction.response.send_message(
                "No AxiBridge pairings on this server. Create one with "
                "`/bridge pair` in the channel that should receive reports.",
                ephemeral=True,
            )
            return

        lines = []
        for info in keys:
            # Render the mention directly (<#id>) rather than resolving the
            # channel object: Discord renders this correctly whether or not
            # the channel still exists, and it works without a cache lookup.
            where = f"<#{info.channel_id}>"
            used = info.last_used_at or "never used"
            lines.append(
                f"`#{info.id}` → {where} · created by <@{info.created_by}> "
                f"on {info.created_at} · last used {used}"
            )
        await interaction.response.send_message(
            "**AxiBridge pairings**\n"
            + "\n".join(lines)
            + "\n\nRemove one with `/bridge revoke id:<number>`.",
            ephemeral=True,
        )

    @app_commands.command(name="revoke", description="Revoke an AxiBridge pairing by id.")
    @app_commands.describe(id="The pairing id from /bridge list.")
    async def bridge_revoke(self, interaction: discord.Interaction, id: int) -> None:
        if not await self._check_manage_guild(interaction):
            return

        removed = self.bot.storage.revoke_bridge_key(interaction.guild.id, id)
        await interaction.response.send_message(
            f"Revoked pairing `#{id}`. That key can no longer post here."
            if removed
            else f"No pairing `#{id}` on this server. See `/bridge list`.",
            ephemeral=True,
        )


async def setup(bot: AxiToolsBot) -> None:
    await bot.add_cog(BridgeCog(bot))
