
import discord
import pytest
from unittest.mock import AsyncMock, MagicMock
from discord import app_commands
from axitools.cogs.guild_roles import GuildRolesCog
from axitools.storage import GuildConfig

@pytest.fixture
def mock_bot_accounts():
    bot = MagicMock()
    return bot

@pytest.mark.asyncio
async def test_guild_roles_init(mock_bot_accounts):
    cog = GuildRolesCog(mock_bot_accounts)
    assert cog is not None

@pytest.mark.asyncio
async def test_guild_roles_strip_emoji():
    # Helper method test
    # GuildRolesCog._strip_emoji removes emoji but might leave spaces depending on impl
    # "Hello 😃" -> "Hello "
    assert GuildRolesCog._strip_emoji("Hello 😃").strip() == "Hello"
    assert GuildRolesCog._strip_emoji("Guild [TAG]") == "Guild [TAG]"


def _qualified_command_names():
    names = set()
    for command in GuildRolesCog.guild_roles.walk_commands():
        if isinstance(command, app_commands.Command):
            names.add(command.qualified_name)
    return names


def test_guild_roles_new_qualified_names_present():
    names = _qualified_command_names()
    expected = {
        "guildroles set",
        "guildroles list",
        "guildroles remove",
        "guildroles audit",
        "guildroles alliance set",
        "guildroles alliance clear",
        "guildroles allowlist add",
        "guildroles allowlist remove",
        "guildroles allowlist list",
    }
    assert expected <= names


def test_guild_roles_old_qualified_names_absent():
    names = _qualified_command_names()
    stale = {
        "guildroles setalliance",
        "guildroles clearalliance",
        "guildroles whitelist add",
        "guildroles whitelist remove",
        "guildroles whitelist list",
    }
    assert not (stale & names)


# ----------------------------------------------------------------------
# /guildroles alliance clear — cleanup_roles
# ----------------------------------------------------------------------


class _FakeRole:
    def __init__(self, role_id: int, name: str, members=None):
        self.id = role_id
        self.name = name
        self.mention = f"<@&{role_id}>"
        self.members = members if members is not None else []

    def is_default(self) -> bool:
        return False


class _FakeMember:
    def __init__(self, user_id: int, name: str, display_name: str, roles):
        self.id = user_id
        self.name = name
        self.display_name = display_name
        self.roles = list(roles)
        self.removed: list = []

    async def remove_roles(self, *roles, reason=None):
        for role in roles:
            self.removed.append(role)
            if role in self.roles:
                self.roles.remove(role)
            if self in role.members:
                role.members.remove(self)


def _alliance_clear_bot(config, roles, api_keys=None):
    bot = MagicMock()
    bot.ensure_authorised = AsyncMock(return_value=True)
    bot.get_config = MagicMock(return_value=config)
    bot.save_config = MagicMock()
    bot.storage.query_api_keys = MagicMock(return_value=api_keys or [])
    return bot


def _alliance_clear_interaction(roles):
    interaction = MagicMock()
    interaction.guild = MagicMock()
    interaction.guild.id = 1
    interaction.guild.get_role = MagicMock(side_effect=lambda rid: roles.get(rid))
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _sent_description(interaction):
    if interaction.response.send_message.await_args:
        kwargs = interaction.response.send_message.await_args.kwargs
    else:
        kwargs = interaction.followup.send.await_args.kwargs
    return kwargs["embed"].description


@pytest.mark.asyncio
async def test_alliance_clear_without_cleanup_leaves_roles():
    config = GuildConfig.default()
    config.alliance_guild_id = "abc123"
    config.alliance_guild_name = "Alliance [ALLY]"
    config.guild_role_ids = {"abc123": 10}
    alliance_role = _FakeRole(10, "Alliance")
    member = _FakeMember(100, "member", "Member", [alliance_role])
    alliance_role.members.append(member)
    roles = {10: alliance_role}

    cog = GuildRolesCog(_alliance_clear_bot(config, roles))
    interaction = _alliance_clear_interaction(roles)

    await cog.clear_alliance_guild.callback(cog, interaction)

    assert config.alliance_guild_id is None
    assert config.alliance_guild_name is None
    assert member.removed == []
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_alliance_clear_cleanup_without_mapped_role_reports_noop():
    config = GuildConfig.default()
    config.alliance_guild_id = "abc123"
    config.guild_role_ids = {"def456": 20}
    roles = {20: _FakeRole(20, "Other")}

    cog = GuildRolesCog(_alliance_clear_bot(config, roles))
    interaction = _alliance_clear_interaction(roles)

    await cog.clear_alliance_guild.callback(cog, interaction, cleanup_roles=True)

    assert config.alliance_guild_id is None
    assert "no role" in _sent_description(interaction).casefold()
    interaction.followup.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_alliance_clear_cleanup_removes_alliance_role_and_reports():
    config = GuildConfig.default()
    config.alliance_guild_id = "abc123"
    config.guild_role_ids = {"abc123": 10, "def456": 20}
    alliance_role = _FakeRole(10, "Alliance")
    other_role = _FakeRole(20, "Other")
    alliance_only = _FakeMember(100, "solo", "Solo", [alliance_role])
    multi_guild = _FakeMember(200, "multi", "Multi", [alliance_role, other_role])
    alliance_role.members.extend([alliance_only, multi_guild])
    other_role.members.append(multi_guild)
    roles = {10: alliance_role, 20: other_role}

    record_solo = MagicMock()
    record_solo.account_name = "Solo.1234"
    record_multi = MagicMock()
    record_multi.account_name = "Multi.5678"
    api_keys = [(1, 100, record_solo), (1, 200, record_multi)]

    cog = GuildRolesCog(_alliance_clear_bot(config, roles, api_keys))
    interaction = _alliance_clear_interaction(roles)

    await cog.clear_alliance_guild.callback(cog, interaction, cleanup_roles=True)

    assert alliance_only.removed == [alliance_role]
    assert multi_guild.removed == [alliance_role]
    assert other_role in multi_guild.roles

    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    report = kwargs["files"][0].fp.getvalue()
    assert "Solo.1234" in report
    assert "Multi.5678" in report
    assert "solo" in report and "Solo" in report


@pytest.mark.asyncio
async def test_alliance_clear_cleanup_reports_permission_failure():
    config = GuildConfig.default()
    config.alliance_guild_id = "abc123"
    config.guild_role_ids = {"abc123": 10}
    alliance_role = _FakeRole(10, "Alliance")
    member = _FakeMember(100, "member", "Member", [alliance_role])
    alliance_role.members.append(member)
    member.remove_roles = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "nope")
    )
    roles = {10: alliance_role}

    cog = GuildRolesCog(_alliance_clear_bot(config, roles))
    interaction = _alliance_clear_interaction(roles)

    await cog.clear_alliance_guild.callback(cog, interaction, cleanup_roles=True)

    assert config.alliance_guild_id is None
    assert "permission" in _sent_description(interaction).casefold()


def test_alliance_clear_exposes_cleanup_roles_option():
    option_names = {param.name for param in GuildRolesCog.clear_alliance_guild.parameters}
    assert "cleanup_roles" in option_names
