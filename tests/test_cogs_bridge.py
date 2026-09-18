from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from axitools.api.server import BRIDGE_KEY_PREFIX, hash_app_key
from axitools.cogs.bridge import BridgeCog
from axitools.storage import StorageManager


def _interaction(*, manage_guild=True, guild_id=123, channel_id=999):
    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.guild.name = "Vigil Keep"
    interaction.channel = MagicMock()
    interaction.channel.id = channel_id
    interaction.channel.name = "wvw-reports"
    interaction.channel.mention = "#wvw-reports"
    interaction.user = MagicMock()
    interaction.user.id = 42
    interaction.user.guild_permissions.manage_guild = manage_guild
    interaction.response.send_message = AsyncMock()
    return interaction


def _cog(tmp_path: Path) -> BridgeCog:
    bot = MagicMock()
    bot.storage = StorageManager(tmp_path)
    return BridgeCog(bot)


@pytest.mark.asyncio
async def test_pair_requires_manage_guild(tmp_path):
    cog = _cog(tmp_path)
    interaction = _interaction(manage_guild=False)
    await cog.bridge_pair.callback(cog, interaction)
    assert cog.bot.storage.list_bridge_keys(123) == []
    message = interaction.response.send_message.await_args.args[0]
    assert "Manage Server" in message


@pytest.mark.asyncio
async def test_pair_binds_invoking_channel_and_is_ephemeral(tmp_path):
    cog = _cog(tmp_path)
    interaction = _interaction()
    await cog.bridge_pair.callback(cog, interaction)

    keys = cog.bot.storage.list_bridge_keys(123)
    assert len(keys) == 1
    assert keys[0].channel_id == 999
    assert keys[0].created_by == 42

    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    message = interaction.response.send_message.await_args.args[0]
    assert BRIDGE_KEY_PREFIX in message
    assert "only shown once" in message


@pytest.mark.asyncio
async def test_paired_key_hash_is_what_is_stored(tmp_path):
    cog = _cog(tmp_path)
    interaction = _interaction()
    await cog.bridge_pair.callback(cog, interaction)
    message = interaction.response.send_message.await_args.args[0]
    key = next(t for t in message.split() if t.startswith(BRIDGE_KEY_PREFIX))
    assert cog.bot.storage.get_bridge_key_scope(hash_app_key(key)) == (123, 999)


@pytest.mark.asyncio
async def test_list_shows_only_this_guilds_pairings(tmp_path):
    cog = _cog(tmp_path)
    cog.bot.storage.add_bridge_key(123, 999, "hash-a", created_by=42)
    cog.bot.storage.add_bridge_key(456, 777, "hash-b", created_by=42)
    interaction = _interaction()
    await cog.bridge_list.callback(cog, interaction)
    message = interaction.response.send_message.await_args.args[0]
    assert "999" in message
    assert "777" not in message


@pytest.mark.asyncio
async def test_revoke_removes_one_key(tmp_path):
    cog = _cog(tmp_path)
    info = cog.bot.storage.add_bridge_key(123, 999, "hash-a", created_by=42)
    interaction = _interaction()
    await cog.bridge_revoke.callback(cog, interaction, info.id)
    assert cog.bot.storage.list_bridge_keys(123) == []


@pytest.mark.asyncio
async def test_revoke_cannot_touch_another_guilds_key(tmp_path):
    cog = _cog(tmp_path)
    other = cog.bot.storage.add_bridge_key(456, 777, "hash-b", created_by=42)
    interaction = _interaction(guild_id=123)
    await cog.bridge_revoke.callback(cog, interaction, other.id)
    assert cog.bot.storage.get_bridge_key_scope("hash-b") == (456, 777)
