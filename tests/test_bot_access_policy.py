from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from axitools.bot import AxiCommandTree, AxiToolsBot
from axitools.remote_config import RemoteConfig, hash_identity

USER = 123456789012345678
SERVER = 1100000000000000001
OTHER_SERVER = 1100000000000000002


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.leave = AsyncMock()


@pytest.fixture
def bot(tmp_path: Path) -> AxiToolsBot:
    bot = AxiToolsBot(storage_root=tmp_path)
    bot.remote_config = RemoteConfig(None)
    return bot


def _interaction(user_id: int, guild_id: int | None) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.guild_id = guild_id
    interaction.response.send_message = AsyncMock()
    return interaction


def test_bot_uses_the_policy_tree(bot):
    assert isinstance(bot.tree, AxiCommandTree)
    assert isinstance(bot.remote_config, RemoteConfig)


@pytest.mark.asyncio
async def test_revoked_user_is_answered_unavailable(bot):
    bot.remote_config.set_denylist([hash_identity("discord_user", str(USER))])
    interaction = _interaction(USER, None)
    assert await bot.tree.interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once_with("Unavailable.", ephemeral=True)


@pytest.mark.asyncio
async def test_interaction_in_revoked_server_is_refused(bot):
    bot.remote_config.set_denylist([hash_identity("discord_server", str(SERVER))])
    assert await bot.tree.interaction_check(_interaction(USER, SERVER)) is False


@pytest.mark.asyncio
async def test_other_users_pass(bot):
    bot.remote_config.set_denylist([hash_identity("discord_user", "223456789012345678")])
    interaction = _interaction(USER, OTHER_SERVER)
    assert await bot.tree.interaction_check(interaction) is True
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_refusal_survives_an_expired_interaction(bot):
    bot.remote_config.set_denylist([hash_identity("discord_user", str(USER))])
    interaction = _interaction(USER, None)
    interaction.response.send_message.side_effect = discord.NotFound(MagicMock(status=404), "gone")
    assert await bot.tree.interaction_check(interaction) is False


@pytest.mark.asyncio
async def test_leave_blocked_guilds_leaves_only_revoked_servers(bot):
    bot.remote_config.set_denylist([hash_identity("discord_server", str(SERVER))])
    revoked, kept = FakeGuild(SERVER), FakeGuild(OTHER_SERVER)
    assert await bot.leave_blocked_guilds([revoked, kept]) == 1
    revoked.leave.assert_awaited_once()
    kept.leave.assert_not_awaited()


@pytest.mark.asyncio
async def test_leave_failure_is_logged_and_does_not_stop_the_loop(bot, caplog):
    bot.remote_config.set_denylist([
        hash_identity("discord_server", str(SERVER)),
        hash_identity("discord_server", str(OTHER_SERVER)),
    ])
    failing, second = FakeGuild(SERVER), FakeGuild(OTHER_SERVER)
    failing.leave.side_effect = discord.HTTPException(MagicMock(status=500), "boom")
    assert await bot.leave_blocked_guilds([failing, second]) == 1
    second.leave.assert_awaited_once()
    assert "Could not leave" in caplog.text


@pytest.mark.asyncio
async def test_on_guild_join_leaves_a_revoked_server_without_syncing(bot):
    bot.remote_config.set_denylist([hash_identity("discord_server", str(SERVER))])
    bot._sync_global_commands = AsyncMock()
    bot._sync_guild_commands = AsyncMock()
    guild = FakeGuild(SERVER)
    await bot.on_guild_join(guild)
    guild.leave.assert_awaited_once()
    bot._sync_guild_commands.assert_not_awaited()


@pytest.mark.asyncio
async def test_policy_tick_refreshes_then_leaves(bot):
    bot.remote_config.refresh = AsyncMock(return_value=True)
    bot.leave_blocked_guilds = AsyncMock(return_value=0)
    await bot._policy_tick()
    bot.remote_config.refresh.assert_awaited_once()
    bot.leave_blocked_guilds.assert_awaited_once()


@pytest.mark.asyncio
async def test_policy_tick_never_raises(bot, caplog):
    bot.remote_config.refresh = AsyncMock(side_effect=RuntimeError("bug"))
    await bot._policy_tick()
    assert "Access policy refresh failed" in caplog.text


@pytest.mark.asyncio
async def test_revoked_autocomplete_is_refused_without_sending(bot):
    bot.remote_config.set_denylist([hash_identity("discord_user", str(USER))])
    interaction = _interaction(USER, None)
    interaction.type = discord.InteractionType.autocomplete
    assert await bot.tree.interaction_check(interaction) is False
    interaction.response.send_message.assert_not_awaited()
