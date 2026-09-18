import base64
from pathlib import Path

import pytest
import pytest_asyncio

from axitools.api.server import (
    APP_KEY_PREFIX,
    BRIDGE_KEY_PREFIX,
    DEFAULT_PUBLIC_URL,
    build_app,
    generate_app_key,
    generate_bridge_key,
    hash_app_key,
)
from axitools.storage import StorageManager


class FakeChannel:
    def __init__(self, channel_id: int, name: str) -> None:
        self.id = channel_id
        self.name = name


class FakeGuild:
    def __init__(self, guild_id: int, name: str) -> None:
        self.id = guild_id
        self.name = name
        self._channels = {999: FakeChannel(999, "wvw-reports")}

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)


class FakeBot:
    """Minimal stand-in for AxiToolsBot: just .storage and .guilds."""

    def __init__(self, root: Path) -> None:
        self.storage = StorageManager(root)
        self.guilds = [FakeGuild(123, "Vigil Keep"), FakeGuild(456, "Durmand Priory")]


@pytest.fixture(autouse=True)
def _global_token_disabled_by_default(monkeypatch):
    """Default posture: global token off unless a test opts in explicitly."""
    monkeypatch.delenv("AXITOOLS_ALLOW_GLOBAL_TOKEN", raising=False)


@pytest.fixture
def bot(tmp_path):
    return FakeBot(tmp_path)


@pytest_asyncio.fixture
async def api_client(aiohttp_client, bot):
    app = build_app(bot, token="test-token")
    return await aiohttp_client(app)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _decode_base64url(value: str) -> str:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")


@pytest.fixture
def bridge_key(bot):
    """A key scoped to guild 123, channel 999."""
    key = generate_bridge_key()
    bot.storage.add_bridge_key(123, 999, hash_app_key(key), created_by=42)
    return key


def test_generate_bridge_key_format(monkeypatch):
    monkeypatch.setenv("AXITOOLS_PUBLIC_URL", "https://bot.example.com")
    key = generate_bridge_key()
    assert key.startswith(BRIDGE_KEY_PREFIX)
    prefix, encoded_url, secret = key.split(".", 2)
    assert prefix == "axb1"
    assert _decode_base64url(encoded_url) == "https://bot.example.com"
    assert len(secret) == 43
    assert "." not in secret


def test_bridge_and_app_prefixes_differ():
    assert BRIDGE_KEY_PREFIX != APP_KEY_PREFIX
    assert not generate_bridge_key().startswith(APP_KEY_PREFIX)


def test_bridge_key_storage_roundtrip(tmp_path):
    storage = StorageManager(tmp_path)
    info = storage.add_bridge_key(123, 999, "hash-one", created_by=42)
    assert info.guild_id == 123
    assert info.channel_id == 999
    assert storage.get_bridge_key_scope("hash-one") == (123, 999)

    assert [k.id for k in storage.list_bridge_keys(123)] == [info.id]
    assert storage.revoke_bridge_key(123, info.id) == 1
    assert storage.get_bridge_key_scope("hash-one") is None


def test_bridge_key_revoke_all_for_guild(tmp_path):
    storage = StorageManager(tmp_path)
    storage.add_bridge_key(123, 999, "hash-a", created_by=1)
    storage.add_bridge_key(123, 888, "hash-b", created_by=1)
    storage.add_bridge_key(456, 777, "hash-c", created_by=1)
    assert storage.revoke_bridge_key(123) == 2
    assert storage.get_bridge_key_scope("hash-c") == (456, 777)


def test_touch_bridge_key_sets_last_used(tmp_path):
    storage = StorageManager(tmp_path)
    info = storage.add_bridge_key(123, 999, "hash-one", created_by=42)
    assert storage.list_bridge_keys(123)[0].last_used_at is None
    storage.touch_bridge_key("hash-one")
    assert storage.list_bridge_keys(123)[0].last_used_at is not None
    assert info.id == storage.list_bridge_keys(123)[0].id


@pytest.mark.asyncio
async def test_bridge_key_authenticates_whoami(api_client, bridge_key):
    resp = await api_client.get("/bridge/whoami", headers=_bearer(bridge_key))
    assert resp.status != 401


@pytest.mark.asyncio
async def test_unknown_bridge_key_is_401(api_client):
    resp = await api_client.get("/bridge/whoami", headers=_bearer(generate_bridge_key()))
    assert resp.status == 401


@pytest.mark.asyncio
async def test_app_key_rejected_on_bridge_route(api_client, bot):
    key = generate_app_key()
    bot.storage.add_app_key(123, hash_app_key(key), created_by=42)
    resp = await api_client.get("/bridge/whoami", headers=_bearer(key))
    assert resp.status == 401


@pytest.mark.asyncio
async def test_global_token_rejected_on_bridge_route(aiohttp_client, bot, monkeypatch):
    monkeypatch.setenv("AXITOOLS_ALLOW_GLOBAL_TOKEN", "1")
    client = await aiohttp_client(build_app(bot, token="test-token"))
    resp = await client.get("/bridge/whoami", headers=_bearer("test-token"))
    assert resp.status == 401


@pytest.mark.asyncio
async def test_bridge_key_rejected_on_guild_routes(api_client, bridge_key):
    resp = await api_client.get("/guilds/123/builds", headers=_bearer(bridge_key))
    assert resp.status == 401


@pytest.mark.asyncio
async def test_bridge_key_rejected_on_guilds_index(api_client, bridge_key):
    resp = await api_client.get("/guilds", headers=_bearer(bridge_key))
    assert resp.status == 401
