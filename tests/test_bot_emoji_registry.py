"""I3: the emoji registry must be able to recover without a process restart.

setup_hook loads the registry exactly once; these tests cover the two paths
that were added to make a transient boot-time failure (or a later sync)
observable/recoverable in-process: an unconditional refresh (used by
/dev emojireload) and a rate-limited lazy refresh (used when a bridge report
arrives and the registry is empty).
"""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from axitools.bot import AxiToolsBot


class _FakeEmoji:
    def __init__(self, name: str, id: int) -> None:
        self.name = name
        self.id = id


@pytest.fixture
def bot(tmp_path: Path) -> AxiToolsBot:
    return AxiToolsBot(storage_root=tmp_path)


@pytest.mark.asyncio
async def test_refresh_emoji_registry_builds_from_fetch(bot):
    bot.fetch_application_emojis = AsyncMock(
        return_value=[_FakeEmoji("firebrand", 111)]
    )
    count = await bot.refresh_emoji_registry()
    assert count == 1
    assert bot.emoji_registry == {"firebrand": "<:firebrand:111>"}


@pytest.mark.asyncio
async def test_refresh_emoji_registry_degrades_to_empty_on_failure(bot):
    bot.fetch_application_emojis = AsyncMock(side_effect=RuntimeError("discord 5xx"))
    count = await bot.refresh_emoji_registry()
    assert count == 0
    assert bot.emoji_registry == {}


@pytest.mark.asyncio
async def test_maybe_refresh_skips_when_registry_already_populated(bot):
    bot.emoji_registry = {"firebrand": "<:firebrand:111>"}
    bot.fetch_application_emojis = AsyncMock(return_value=[])
    await bot.maybe_refresh_emoji_registry()
    bot.fetch_application_emojis.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_refresh_attempts_once_when_empty(bot):
    bot.emoji_registry = {}
    bot.fetch_application_emojis = AsyncMock(
        return_value=[_FakeEmoji("firebrand", 111)]
    )
    await bot.maybe_refresh_emoji_registry()
    bot.fetch_application_emojis.assert_awaited_once()
    assert bot.emoji_registry == {"firebrand": "<:firebrand:111>"}


@pytest.mark.asyncio
async def test_maybe_refresh_is_rate_limited_after_a_failed_attempt(bot):
    """The core I3 guard: a persistent failure must not re-fetch on every
    single report. Verified by mutation: removing this cooldown check makes
    this test fail (see final-fix-axitools-report.md)."""
    bot.emoji_registry = {}
    bot.fetch_application_emojis = AsyncMock(side_effect=RuntimeError("still down"))

    await bot.maybe_refresh_emoji_registry()
    assert bot.fetch_application_emojis.await_count == 1

    # Registry is still empty, so a naive implementation would refetch again
    # immediately -- the cooldown must suppress this second attempt.
    await bot.maybe_refresh_emoji_registry()
    assert bot.fetch_application_emojis.await_count == 1
