import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from axitools.cogs.dev import DevCog


def _make_cog_with_delegate(attr):
    delegate = MagicMock()
    setattr(delegate, attr, AsyncMock())
    bot = MagicMock()
    bot.get_cog = MagicMock(return_value=delegate)
    cog = DevCog(bot)
    return cog, delegate


@pytest.mark.asyncio
async def test_dev_arcdps_delegates():
    cog, delegate = _make_cog_with_delegate("run_force_notification")
    interaction = MagicMock()

    await cog.arcdps.callback(cog, interaction)

    cog.bot.get_cog.assert_called_once_with("ArcDpsUpdatesCog")
    delegate.run_force_notification.assert_awaited_once_with(interaction)


@pytest.mark.asyncio
async def test_dev_updatenotes_delegates():
    cog, delegate = _make_cog_with_delegate("run_force_notification")
    interaction = MagicMock()

    await cog.updatenotes.callback(cog, interaction)

    cog.bot.get_cog.assert_called_once_with("UpdateNotesCog")
    delegate.run_force_notification.assert_awaited_once_with(interaction)


@pytest.mark.asyncio
async def test_dev_rsstest_delegates():
    cog, delegate = _make_cog_with_delegate("run_test_feed")
    interaction = MagicMock()

    await cog.rsstest.callback(cog, interaction)

    cog.bot.get_cog.assert_called_once_with("RssFeedsCog")
    delegate.run_test_feed.assert_awaited_once_with(interaction)


def test_dev_command_surface():
    cog = DevCog(MagicMock())
    qualified_names = {cmd.qualified_name for cmd in cog.walk_app_commands()}
    assert qualified_names == {
        "dev arcdps",
        "dev updatenotes",
        "dev rsstest",
        "dev bridgetest",
        "dev emojireload",
    }


@pytest.mark.asyncio
async def test_dev_bridgetest_reports_when_queue_missing():
    bot = MagicMock()
    bot.bridge_queue = None
    cog = DevCog(bot)
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    await cog.bridgetest.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "not running" in args[0]
    assert kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_dev_bridgetest_reports_when_queue_not_running():
    bot = MagicMock()
    bot.bridge_queue = MagicMock()
    bot.bridge_queue.is_running.return_value = False
    bot.bridge_queue.submit = AsyncMock()
    cog = DevCog(bot)
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    await cog.bridgetest.callback(cog, interaction)

    bot.bridge_queue.submit.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "not running" in args[0]
    assert kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_dev_bridgetest_submits_through_queue_when_running():
    bot = MagicMock()
    bot.bridge_queue = MagicMock()
    bot.bridge_queue.is_running.return_value = True
    bot.bridge_queue.submit = AsyncMock()
    bot.emoji_registry = {"firebrand": "<:firebrand:1>"}
    cog = DevCog(bot)
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    with patch(
        "axitools.emoji_registry.substitute_payload", side_effect=lambda p, r: p
    ) as mock_sub, patch(
        "axitools.emoji_registry.enforce_limits", side_effect=lambda p: p
    ) as mock_limits:
        await cog.bridgetest.callback(cog, interaction)

    mock_sub.assert_called_once()
    mock_limits.assert_called_once()
    bot.bridge_queue.submit.assert_awaited_once()
    call_args = bot.bridge_queue.submit.call_args
    assert call_args.args[0] is interaction.channel
    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "Queued with 1 emoji" in args[0]
    assert kwargs.get("ephemeral") is True


@pytest.mark.asyncio
async def test_dev_emojireload_refreshes_and_reports_count():
    bot = MagicMock()
    bot.refresh_emoji_registry = AsyncMock(return_value=7)
    cog = DevCog(bot)
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()

    await cog.emojireload.callback(cog, interaction)

    bot.refresh_emoji_registry.assert_awaited_once()
    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.call_args
    assert "7 emoji" in args[0]
    assert kwargs.get("ephemeral") is True
