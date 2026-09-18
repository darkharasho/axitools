import io
from pathlib import Path

import pytest
from PIL import Image

from axitools.constants import CLASS_ICON_PATH
from axitools.scripts.sync_emoji import (
    EMOJI_MAX_BYTES,
    EMOJI_SIZE,
    SyncEmojiConfigError,
    _require_env,
    build_registry,
    check_delete_confirmed,
    ensure_local_icons_present,
    load_local_icons,
    normalize_icon,
    plan_sync,
)


@pytest.mark.parametrize(
    "asset",
    ["Bladesworn.png", "Amalgam.png", "Firebrand.png", "Revenant_icon.png"],
)
def test_normalize_icon_real_assets(asset):
    """The shipped assets are 256–750px, some non-square, some over 256KB."""
    data = normalize_icon(CLASS_ICON_PATH / asset)
    assert len(data) <= EMOJI_MAX_BYTES
    image = Image.open(io.BytesIO(data))
    assert image.size == (EMOJI_SIZE, EMOJI_SIZE)
    assert image.format == "PNG"


def test_normalize_pads_rather_than_stretches(tmp_path):
    """A wide source keeps its aspect ratio; the remainder is transparent."""
    source = tmp_path / "wide.png"
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(source)
    image = Image.open(io.BytesIO(normalize_icon(source)))
    assert image.size == (EMOJI_SIZE, EMOJI_SIZE)
    # Middle row is opaque red, top row is transparent padding.
    assert image.getpixel((EMOJI_SIZE // 2, EMOJI_SIZE // 2))[3] == 255
    assert image.getpixel((EMOJI_SIZE // 2, 0))[3] == 0


def test_load_local_icons_keys_by_registry_key():
    icons = load_local_icons(CLASS_ICON_PATH)
    assert "firebrand" in icons
    assert "revenant" in icons  # from Revenant_icon.png
    assert "revenant_icon" not in icons
    assert all(len(v) <= EMOJI_MAX_BYTES for v in icons.values())


def test_plan_sync_uploads_missing():
    upload, delete = plan_sync({"firebrand": b"x"}, {})
    assert upload == ["firebrand"]
    assert delete == []


def test_plan_sync_leaves_matching_alone():
    upload, delete = plan_sync({"firebrand": b"x"}, {"firebrand": "111"})
    assert upload == []
    assert delete == []


def test_plan_sync_deletes_orphans():
    upload, delete = plan_sync({}, {"scrapper": "222"})
    assert upload == []
    assert delete == ["scrapper"]


def test_build_registry_formats_markup():
    registry = build_registry(
        [{"name": "firebrand", "id": "111111111111111111"}]
    )
    assert registry == {"firebrand": "<:firebrand:111111111111111111>"}


# ---------------------------------------------------------------------------
# I6: --apply must never be able to delete all remote emoji because of an
# empty/missing local directory, and any real delete requires --allow-delete.
# ---------------------------------------------------------------------------


def test_ensure_local_icons_present_raises_on_empty(tmp_path):
    with pytest.raises(SyncEmojiConfigError, match="no local icons found"):
        ensure_local_icons_present({}, directory=tmp_path)


def test_ensure_local_icons_present_allows_nonempty(tmp_path):
    ensure_local_icons_present({"firebrand": b"x"}, directory=tmp_path)  # no raise


def test_check_delete_confirmed_true_when_nothing_to_delete():
    assert check_delete_confirmed([], allow_delete=False) is True


def test_check_delete_confirmed_false_without_flag():
    assert check_delete_confirmed(["scrapper"], allow_delete=False) is False


def test_check_delete_confirmed_true_with_flag():
    assert check_delete_confirmed(["scrapper"], allow_delete=True) is True


def test_require_env_raises_clear_message_not_keyerror(monkeypatch):
    monkeypatch.delenv("DISCORD_APPLICATION_ID", raising=False)
    with pytest.raises(SyncEmojiConfigError, match="DISCORD_APPLICATION_ID"):
        _require_env("DISCORD_APPLICATION_ID")


@pytest.mark.asyncio
async def test_main_async_refuses_when_local_directory_is_empty(monkeypatch):
    """The exact I6 failure scenario: an empty/missing local dir must never
    reach the delete loop, even under --apply.

    N4: the session is mocked here too, the same way its two siblings below
    mock it, even though the guard under test should raise before any session
    is ever built. If that guard regresses, this test must fail loudly rather
    than fall through to a real (unmocked) aiohttp.ClientSession and make an
    unauthenticated call to Discord's API -- exactly the shape of a prior
    guard breach. A raising fake session (instead of a "no calls" recorder)
    is what turns a silent guard regression into a hard test failure instead
    of quietly asserting nothing.
    """
    import axitools.scripts.sync_emoji as sync_emoji

    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "1")
    monkeypatch.setattr(sync_emoji, "load_local_icons", lambda: {})

    def _network_forbidden(**kwargs):
        raise AssertionError(
            "no ClientSession should be constructed when local icons are empty"
        )

    monkeypatch.setattr(sync_emoji.aiohttp, "ClientSession", _network_forbidden)

    with pytest.raises(SyncEmojiConfigError, match="no local icons found"):
        await sync_emoji.main_async(apply=True)


class _FakeResponse:
    def __init__(self, calls, label):
        self._calls = calls
        self._label = label

    async def __aenter__(self):
        self._calls.append(self._label)
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    async def json(self):
        return {"scrapper": "222"}


class _FakeSession:
    def __init__(self, calls, remote):
        self._calls = calls
        self._remote = remote

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, *a, **k):
        return _FakeResponse(self._calls, "get")

    def delete(self, *a, **k):
        return _FakeResponse(self._calls, "delete")

    def post(self, *a, **k):
        return _FakeResponse(self._calls, "post")


@pytest.mark.asyncio
async def test_main_async_apply_without_allow_delete_never_calls_delete(monkeypatch):
    """Mutation-guarded: a non-empty to_delete under --apply must exit
    non-zero and must not reach the delete endpoint without --allow-delete."""
    import axitools.scripts.sync_emoji as sync_emoji

    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "1")
    monkeypatch.setattr(sync_emoji, "load_local_icons", lambda: {"firebrand": b"x"})

    async def fake_fetch_remote(session, app_id):
        return {"firebrand": "111", "scrapper": "222"}

    monkeypatch.setattr(sync_emoji, "fetch_remote", fake_fetch_remote)

    calls: list[str] = []
    monkeypatch.setattr(
        sync_emoji.aiohttp, "ClientSession", lambda **k: _FakeSession(calls, {})
    )

    result = await sync_emoji.main_async(apply=True, allow_delete=False)

    assert result == 1
    assert "delete" not in calls
    assert "post" not in calls


@pytest.mark.asyncio
async def test_main_async_apply_with_allow_delete_proceeds(monkeypatch):
    import axitools.scripts.sync_emoji as sync_emoji

    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("DISCORD_APPLICATION_ID", "1")
    monkeypatch.setattr(sync_emoji, "load_local_icons", lambda: {"firebrand": b"x"})

    async def fake_fetch_remote(session, app_id):
        return {"firebrand": "111", "scrapper": "222"}

    monkeypatch.setattr(sync_emoji, "fetch_remote", fake_fetch_remote)

    calls: list[str] = []
    monkeypatch.setattr(
        sync_emoji.aiohttp, "ClientSession", lambda **k: _FakeSession(calls, {})
    )

    result = await sync_emoji.main_async(apply=True, allow_delete=True)

    assert result == 0
    assert "delete" in calls
