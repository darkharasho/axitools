from unittest.mock import AsyncMock, MagicMock

import pytest

from axitools.cogs.comps import CompSignupView
from axitools.remote_config import RemoteConfig, hash_identity

USER = 123456789012345678
SERVER = 1100000000000000001


def _view(denylist):
    bot = MagicMock()
    if denylist is not None:
        bot.remote_config = RemoteConfig(None)
        bot.remote_config.set_denylist(denylist)
    cog = MagicMock()
    cog.bot = bot
    cog.resolve_comp_context.return_value = (None, None, None)
    return CompSignupView(cog, SERVER)


def _interaction():
    interaction = MagicMock()
    interaction.user.id = USER
    interaction.guild_id = SERVER
    interaction.response.send_message = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_revoked_user_is_refused_on_the_persistent_signup_view():
    view = _view([hash_identity("discord_user", str(USER))])
    interaction = _interaction()
    assert await view.interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once_with("Unavailable.", ephemeral=True)


@pytest.mark.asyncio
async def test_allowed_user_passes_the_signup_view():
    view = _view([hash_identity("discord_user", "223456789012345678")])
    interaction = _interaction()
    assert await view.interaction_check(interaction) is True
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_signup_view_passes_without_a_policy():
    view = _view(None)
    assert await view.interaction_check(_interaction()) is True
