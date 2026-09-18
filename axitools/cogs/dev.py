"""Developer/test commands, only registered outside production."""
from __future__ import annotations

import os

import discord
from discord import app_commands
from discord.ext import commands

from ..bot import AxiToolsBot

PRODUCTION = os.getenv("PRODUCTION", "true").lower() in {"1", "true", "yes", "on"}


class DevCog(commands.GroupCog, name="dev", group_extras={"category": "Dev"}):
    """Developer-only test triggers."""

    def __init__(self, bot: AxiToolsBot) -> None:
        super().__init__()
        self.bot = bot

    @app_commands.command(name="arcdps", description="Send a test ArcDPS notification.")
    async def arcdps(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("ArcDpsUpdatesCog")
        await cog.run_force_notification(interaction)

    @app_commands.command(
        name="updatenotes",
        description="Send the latest game update notes notification.",
    )
    async def updatenotes(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("UpdateNotesCog")
        await cog.run_force_notification(interaction)

    @app_commands.command(
        name="rsstest",
        description="Post the latest entry from a configured RSS feed to its channel.",
    )
    async def rsstest(self, interaction: discord.Interaction) -> None:
        cog = self.bot.get_cog("RssFeedsCog")
        await cog.run_test_feed(interaction)

    @app_commands.command(
        name="bridgetest",
        description="Post a canned AxiBridge report here to check emoji spacing.",
    )
    async def bridgetest(self, interaction: discord.Interaction) -> None:
        from ..emoji_registry import enforce_limits, substitute_payload

        # Same no-silent-fallback rule as the HTTP relay handler: if the send
        # queue isn't up, tell the invoker instead of quietly sending inline
        # (which would skip the rate limiter and the worker's exception
        # handling that real reports always go through).
        queue = getattr(self.bot, "bridge_queue", None)
        if queue is None or not queue.is_running():
            await interaction.response.send_message(
                "Bridge send queue is not running; nothing was posted.",
                ephemeral=True,
            )
            return

        # Deliberately unfenced: custom Discord emoji do not render inside
        # fenced code blocks (they show as literal <:name:id> text), so the
        # rows must stay as plain text for this smoke test to be meaningful.
        rows = "\n".join(
            f"{{{{spec:{spec}}}}} {i + 1:>2} Player{i:02d}  {1234 - i * 37:>5}"
            for i, spec in enumerate(
                ["firebrand", "scourge", "spellbreaker", "herald", "tempest"]
            )
        )
        payload = {
            "content": "**Bridge test** — canned report",
            "embeds": [
                {
                    "title": "{{spec:firebrand}} Squad Summary",
                    "color": 3447003,
                    "fields": [
                        {"name": "Damage", "value": rows, "inline": True},
                        {"name": "Healing", "value": rows, "inline": True},
                    ],
                    "footer": {"text": "AxiBridge · /dev bridgetest"},
                }
            ],
        }
        registry = getattr(self.bot, "emoji_registry", {}) or {}
        await queue.submit(
            interaction.channel, enforce_limits(substitute_payload(payload, registry))
        )
        await interaction.response.send_message(
            f"Queued with {len(registry)} emoji in the registry.", ephemeral=True
        )


async def setup(bot: AxiToolsBot) -> None:
    if not PRODUCTION:
        await bot.add_cog(DevCog(bot))
