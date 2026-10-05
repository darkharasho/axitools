"""Bot setup for AxiTools."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Iterable, Set

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

from .api.server import start_api
from .remote_config import REFRESH_SECONDS, RemoteConfig, policy_for
from .storage import DEFAULT_STORAGE_ROOT, GuildConfig, StorageManager

LOGGER = logging.getLogger(__name__)

# Minimum interval between lazy registry re-fetch attempts, so a persistent
# Discord-side failure cannot make every bridged report hammer
# fetch_application_emojis(). See AxiToolsBot.maybe_refresh_emoji_registry.
EMOJI_REGISTRY_REFRESH_COOLDOWN_SECONDS = 300


class AxiCommandTree(app_commands.CommandTree):
    """Command tree that refuses users and servers on the Axi access denylist."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        policy = policy_for(self.client)
        if policy is None:
            return True
        identities = [("discord_user", str(interaction.user.id))]
        if interaction.guild_id is not None:
            identities.append(("discord_server", str(interaction.guild_id)))
        if not policy.any_blocked(identities):
            return True
        try:
            await interaction.response.send_message("Unavailable.", ephemeral=True)
        except discord.HTTPException:
            LOGGER.debug("Could not answer a refused interaction", exc_info=True)
        return False


class AxiToolsBot(commands.Bot):
    """Discord bot implementation for AxiTools."""

    def __init__(self, *, storage_root=DEFAULT_STORAGE_ROOT) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.message_content = True
        super().__init__(
            command_prefix=commands.when_mentioned_or("gw2!"),
            intents=intents,
            application_id=None,
            tree_cls=AxiCommandTree,
        )
        self.storage = StorageManager(storage_root)
        self.remote_config = RemoteConfig.from_env()
        self._policy_task: asyncio.Task | None = None
        self.tree.on_error = self.on_app_command_error
        self._global_sync_done = False
        self._synced_guilds: Set[int] = set()
        self._api_runner = None
        self.bridge_queue = None
        self.emoji_registry: dict = {}
        self._emoji_registry_last_attempt: float | None = None

    # ------------------------------------------------------------------
    async def setup_hook(self) -> None:
        """Load cogs on startup."""

        await self.load_extension("axitools.cogs.config")
        await self.load_extension("axitools.cogs.audit")
        await self.load_extension("axitools.cogs.help")
        await self.load_extension("axitools.cogs.account_self")
        await self.load_extension("axitools.cogs.guild_roles")
        await self.load_extension("axitools.cogs.select")
        await self.load_extension("axitools.cogs.bridge")
        await self.load_extension("axitools.cogs.builds")
        await self.load_extension("axitools.cogs.arcdps")
        await self.load_extension("axitools.cogs.update_notes")
        await self.load_extension("axitools.cogs.game_news")
        await self.load_extension("axitools.cogs.rss")
        await self.load_extension("axitools.cogs.comps")
        await self.load_extension("axitools.cogs.wvw_alliance")
        await self.load_extension("axitools.cogs.reset")
        await self.load_extension("axitools.cogs.streaming")
        await self.load_extension("axitools.cogs.dev")
        await self.load_extension("axitools.cogs.access_admin")

        # The bridge queue (and emoji registry) must exist and be running
        # BEFORE the HTTP API starts accepting requests. Otherwise there is a
        # window where POST /bridge/report is reachable but bot.bridge_queue
        # is still None, and the handler would have nowhere safe to hand the
        # send off to.
        from .api.bridge_worker import BridgeSendQueue

        # Load the access denylist before the API accepts requests. refresh()
        # never raises and times out after 10 s; on failure nothing is refused.
        await self.remote_config.refresh()
        self._policy_task = asyncio.create_task(self._policy_loop())

        self.bridge_queue = BridgeSendQueue(self)
        self.bridge_queue.start()
        await self.refresh_emoji_registry()

        try:
            self._api_runner = await start_api(self)
        except OSError as exc:
            LOGGER.warning("AxiTools API failed to start: %s", exc)

    async def refresh_emoji_registry(self) -> int:
        """Re-fetch application emoji from Discord and rebuild the registry.

        Called from ``setup_hook``, from ``/dev emojireload``, and (rate
        limited) from ``maybe_refresh_emoji_registry``. A failure here is
        non-fatal by design -- tokens degrade to plain spec names -- but it
        must never raise, since two of its three callers are on paths that
        must not 500/crash.
        """
        from .emoji_registry import build_registry

        self._emoji_registry_last_attempt = time.monotonic()
        try:
            self.emoji_registry = build_registry(
                [
                    {"name": e.name, "id": e.id}
                    for e in await self.fetch_application_emojis()
                ]
            )
        except Exception:
            LOGGER.exception(
                "could not load application emoji; tokens will degrade to names"
            )
            self.emoji_registry = {}
        return len(self.emoji_registry)

    async def maybe_refresh_emoji_registry(self) -> None:
        """Lazily re-fetch the registry when it is empty, rate limited.

        A transient Discord 5xx (or rate limit) during ``setup_hook`` leaves
        ``emoji_registry`` empty with no in-process way to recover until a
        restart -- every subsequent report would silently post plain text
        forever. This gives it one retry per bridged report, but never more
        than once per ``EMOJI_REGISTRY_REFRESH_COOLDOWN_SECONDS``, so a
        persistent outage cannot turn "one report every few seconds" into
        "one fetch_application_emojis() call every few seconds".
        """
        if self.emoji_registry:
            return
        now = time.monotonic()
        last = self._emoji_registry_last_attempt
        if last is not None and now - last < EMOJI_REGISTRY_REFRESH_COOLDOWN_SECONDS:
            return
        await self.refresh_emoji_registry()

    async def send_bridge_report(self, channel, payload: dict, files=None) -> None:
        """Send a relayed AxiBridge report as this bot."""
        embeds = [discord.Embed.from_dict(e) for e in payload.get("embeds") or []]
        await channel.send(
            content=payload.get("content") or None,
            embeds=embeds or None,
            files=files or None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def leave_blocked_guilds(self, guilds: Iterable[discord.Guild] | None = None) -> int:
        """Leave every server on the Axi access denylist. Returns how many were left."""
        left = 0
        for guild in list(self.guilds if guilds is None else guilds):
            if not self.remote_config.is_blocked("discord_server", str(guild.id)):
                continue
            try:
                await guild.leave()
            except discord.HTTPException:
                LOGGER.exception("Could not leave server %s (access revoked)", guild.id)
                continue
            LOGGER.info("Left server %s: access revoked", guild.id)
            left += 1
        return left

    async def _policy_tick(self) -> None:
        try:
            await self.remote_config.refresh()
            await self.leave_blocked_guilds()
        except Exception:
            LOGGER.exception("Access policy refresh failed")

    async def _policy_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            await asyncio.sleep(REFRESH_SECONDS)
            await self._policy_tick()

    async def close(self) -> None:
        if self._policy_task is not None:
            self._policy_task.cancel()
            self._policy_task = None
        if self._api_runner is not None:
            await self._api_runner.cleanup()
            self._api_runner = None
        await super().close()

    async def on_ready(self) -> None:
        await self.leave_blocked_guilds()
        await self._sync_global_commands()
        for guild in self.guilds:
            await self._sync_guild_commands(guild)
        LOGGER.info("AxiTools is ready. Logged in as %s (%s)", self.user, getattr(self.user, "id", "unknown"))

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if await self.leave_blocked_guilds([guild]):
            return
        await self._sync_global_commands()
        await self._sync_guild_commands(guild)

    async def on_guild_available(self, guild: discord.Guild) -> None:
        await self._sync_global_commands()
        await self._sync_guild_commands(guild)

    async def _sync_global_commands(self) -> None:
        if self._global_sync_done:
            return
        try:
            synced = await self.tree.sync()
        except Exception:  # pragma: no cover - defensive logging
            LOGGER.exception("Failed to sync global application commands")
        else:
            LOGGER.info("Synced %s global application commands", len(synced))
            self._global_sync_done = True

    async def _sync_guild_commands(self, guild: discord.Guild) -> None:
        if guild.id in self._synced_guilds:
            return
        try:
            synced = await self.tree.sync(guild=guild)
        except Exception:  # pragma: no cover - defensive logging
            LOGGER.exception("Failed to sync application commands for guild %s", guild.id)
        else:
            LOGGER.info("Synced %s application commands for guild %s", len(synced), guild.id)
            self._synced_guilds.add(guild.id)

    # ------------------------------------------------------------------
    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
        LOGGER.exception("App command error: %s", error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(
                    "An unexpected error occurred while processing the command.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "An unexpected error occurred while processing the command.", ephemeral=True
                )
        except discord.NotFound:
            LOGGER.warning("Interaction expired before an error message could be sent")
        except Exception:  # pragma: no cover - defensive logging
            LOGGER.exception("Failed to send error response for interaction")

    # ------------------------------------------------------------------
    def get_config(self, guild_id: int) -> GuildConfig:
        return self.storage.get_config(guild_id)

    def save_config(self, guild_id: int, config: GuildConfig) -> None:
        self.storage.save_config(guild_id, config)

    # ------------------------------------------------------------------
    def is_authorised(
        self,
        guild: discord.Guild,
        member: discord.Member,
        *,
        permissions: discord.Permissions | None = None,
    ) -> bool:
        config = self.get_config(guild.id)

        if permissions is None:
            try:
                permissions = member.guild_permissions
            except AttributeError:
                LOGGER.warning(
                    "Unable to resolve guild permissions for member %s in guild %s; assuming none",
                    getattr(member, "id", "unknown"),
                    getattr(guild, "id", "unknown"),
                )
                permissions = discord.Permissions.none()

        if permissions.administrator:
            return True

        if not config.moderator_role_ids:
            return False

        role_ids = set(getattr(member, "_roles", ()))
        role_ids.update(role.id for role in getattr(member, "roles", []) if role is not None)

        return bool(role_ids.intersection(config.moderator_role_ids))

    async def ensure_authorised(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return False
        if not self.is_authorised(
            interaction.guild,
            interaction.user,
            permissions=getattr(interaction, "permissions", None),
        ):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return False
        return True


# ----------------------------------------------------------------------
# Public helpers
# ----------------------------------------------------------------------

def create_bot() -> AxiToolsBot:
    logging.basicConfig(level=logging.INFO)
    return AxiToolsBot()


def run() -> None:
    """Entry point for running the bot via ``python -m axitools``."""

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Set the DISCORD_TOKEN environment variable before running the bot.")
    bot = create_bot()
    bot.run(token)


__all__ = ["AxiToolsBot", "create_bot", "run"]
