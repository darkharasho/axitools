from unittest.mock import AsyncMock, MagicMock

import pytest

from axitools.cogs.account_self import AccountSelfCog
from axitools.cogs.guild_roles import GuildRolesCog
from axitools.remote_config import RemoteConfig, hash_identity
from axitools.storage import GuildConfig

GUILD = "4bbb52aa-d768-4fc6-8ede-c299f2822f0f"


def _bot(denylist):
    bot = MagicMock()
    bot.remote_config = RemoteConfig(None)
    bot.remote_config.set_denylist(denylist)
    bot.ensure_authorised = AsyncMock(return_value=True)
    bot.get_config = MagicMock(return_value=GuildConfig(moderator_role_ids=[]))
    return bot


def _account_cog(bot, account):
    cog = AccountSelfCog(bot)
    cog._fetch_json = AsyncMock(side_effect=[{"permissions": ["account", "guilds"]}, account])
    cog._fetch_guild_details = AsyncMock(return_value={})
    cog._fetch_character_names = AsyncMock(return_value=[])
    return cog


@pytest.mark.asyncio
async def test_linking_refuses_a_revoked_account():
    cog = _account_cog(_bot([hash_identity("gw2_account", "Name.1234")]), {"name": "Name.1234", "guilds": []})
    with pytest.raises(ValueError, match=r"^Unavailable\.$"):
        await cog._validate_api_key("KEY", allow_missing_permissions=True)
    cog._fetch_guild_details.assert_not_awaited()


@pytest.mark.asyncio
async def test_linking_refuses_a_member_of_a_revoked_guild():
    cog = _account_cog(_bot([hash_identity("gw2_guild", GUILD)]), {"name": "Ok.1234", "guilds": [GUILD.upper()]})
    with pytest.raises(ValueError, match=r"^Unavailable\.$"):
        await cog._validate_api_key("KEY", allow_missing_permissions=True)


@pytest.mark.asyncio
async def test_linking_allows_everyone_else():
    cog = _account_cog(_bot([hash_identity("gw2_account", "Name.1234")]), {"name": "Ok.1234", "guilds": [GUILD]})
    result = await cog._validate_api_key("KEY", allow_missing_permissions=True)
    assert result[3] == "Ok.1234"


@pytest.mark.asyncio
async def test_linking_ignores_a_mock_bot_without_a_policy():
    bot = MagicMock()  # remote_config is a MagicMock, not a RemoteConfig
    cog = _account_cog(bot, {"name": "Name.1234", "guilds": []})
    result = await cog._validate_api_key("KEY", allow_missing_permissions=True)
    assert result[3] == "Name.1234"


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["set_guild_role", "set_alliance_guild"])
async def test_guild_role_setup_refuses_a_revoked_guild(command):
    bot = _bot([hash_identity("gw2_guild", GUILD)])
    cog = GuildRolesCog(bot)
    cog._send_embed = AsyncMock()
    cog._cached_guild_labels = AsyncMock(return_value={})
    interaction = MagicMock()
    interaction.guild.id = 1
    args = (interaction, GUILD, MagicMock()) if command == "set_guild_role" else (interaction, GUILD)
    await getattr(cog, command).callback(cog, *args)
    bot.save_config.assert_not_called()
    assert cog._send_embed.await_args.kwargs["description"] == "Unavailable."
