from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aioresponses import aioresponses
from discord import app_commands

from axitools.cogs import access_admin
from axitools.cogs.access_admin import AccessAdminCog, AdminApi, AdminApiError

BASE = "https://config.test"
OWNER = 111111111111111111
BAN = {"id": "b_abcdefghij", "kind": "gw2_account", "value": "name.1234", "reason": "spam",
       "createdAt": "2026-10-05T00:00:00Z", "createdBy": str(OWNER), "revokedAt": None}


def _interaction(user_id=OWNER):
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _cog():
    return AccessAdminCog(MagicMock(), AdminApi(BASE, "t" * 40), OWNER)


def test_commands():
    names = {c.qualified_name for c in AccessAdminCog.access.walk_commands()}
    assert names == {"access revoke", "access revoke-user", "access restore", "access list"}


@pytest.mark.asyncio
async def test_non_owner_is_refused_and_api_is_not_called():
    cog = _cog()
    interaction = _interaction(user_id=222222222222222222)
    with aioresponses() as m:
        await cog.list_cmd.callback(cog, interaction)
        assert m.requests == {}
    interaction.response.send_message.assert_awaited_once_with("Not allowed.", ephemeral=True)


@pytest.mark.asyncio
async def test_revoke_posts_to_the_admin_api():
    cog = _cog()
    interaction = _interaction()
    with aioresponses() as m:
        m.post(f"{BASE}/v1/admin/bans", payload={"ban": BAN, "created": True}, status=201)
        await cog.revoke.callback(cog, interaction, app_commands.Choice(name="GW2 account", value="gw2_account"), "Name.1234", "spam")
        request = list(m.requests.values())[0][0]
        assert request.kwargs["json"] == {"kind": "gw2_account", "value": "Name.1234", "reason": "spam", "createdBy": str(OWNER)}
        assert request.kwargs["headers"]["Authorization"] == "Bearer " + "t" * 40
    text = interaction.followup.send.await_args.args[0]
    assert text.startswith("Revoked")
    assert "b_abcdefghij" in text
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_revoke_user_uses_the_picked_user_id():
    cog = _cog()
    user = MagicMock(spec=discord.User)
    user.id = 333333333333333333
    with aioresponses() as m:
        m.post(f"{BASE}/v1/admin/bans", payload={"ban": {**BAN, "kind": "discord_user", "value": "333333333333333333"}, "created": False})
        interaction = _interaction()
        await cog.revoke_user.callback(cog, interaction, user, None)
        assert list(m.requests.values())[0][0].kwargs["json"]["value"] == "333333333333333333"
    assert interaction.followup.send.await_args.args[0].startswith("Already revoked")


@pytest.mark.asyncio
async def test_api_error_code_is_shown():
    cog = _cog()
    interaction = _interaction()
    with aioresponses() as m:
        m.post(f"{BASE}/v1/admin/bans", payload={"error": "invalid_identity"}, status=400)
        await cog.revoke.callback(cog, interaction, app_commands.Choice(name="GW2 account", value="gw2_account"), "nope", None)
    assert interaction.followup.send.await_args.args[0] == "Revoke failed: invalid_identity"


@pytest.mark.asyncio
async def test_unreachable_api_is_reported():
    cog = _cog()
    interaction = _interaction()
    with aioresponses() as m:
        m.delete(f"{BASE}/v1/admin/bans/b_abcdefghij", exception=OSError("dns"))
        await cog.restore.callback(cog, interaction, "b_abcdefghij")
    assert interaction.followup.send.await_args.args[0] == "Restore failed: admin API unreachable."


@pytest.mark.asyncio
async def test_restore():
    cog = _cog()
    interaction = _interaction()
    with aioresponses() as m:
        m.delete(f"{BASE}/v1/admin/bans/b_abcdefghij", payload={"ban": {**BAN, "revokedAt": "x"}, "changed": True})
        await cog.restore.callback(cog, interaction, "b_abcdefghij")
    assert interaction.followup.send.await_args.args[0].startswith("Restored")


@pytest.mark.asyncio
async def test_list_stays_under_discords_message_limit():
    cog = _cog()
    interaction = _interaction()
    bans = [{**BAN, "id": f"b_{i:010d}", "reason": "x" * 80} for i in range(200)]
    with aioresponses() as m:
        m.get(f"{BASE}/v1/admin/bans", payload={"bans": bans})
        await cog.list_cmd.callback(cog, interaction)
    text = interaction.followup.send.await_args.args[0]
    assert len(text) <= 2000
    assert text.endswith("more)")


@pytest.mark.asyncio
async def test_setup_skips_without_configuration(monkeypatch):
    for name in ("AXI_ADMIN_GUILD_ID", "AXI_OWNER_ID", "AXI_CONFIG_ADMIN_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    await access_admin.setup(bot)
    bot.add_cog.assert_not_awaited()


@pytest.mark.asyncio
async def test_setup_registers_only_in_the_admin_guild(monkeypatch):
    monkeypatch.setenv("AXI_ADMIN_GUILD_ID", "444444444444444444")
    monkeypatch.setenv("AXI_OWNER_ID", str(OWNER))
    monkeypatch.setenv("AXI_CONFIG_ADMIN_TOKEN", "t" * 40)
    monkeypatch.delenv("AXI_CONFIG_URL", raising=False)
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    await access_admin.setup(bot)
    cog = bot.add_cog.await_args.args[0]
    assert bot.add_cog.await_args.kwargs["guild"] == discord.Object(id=444444444444444444)
    assert cog.owner_id == OWNER
    assert cog.api.base_url == "https://config.axi.link"
