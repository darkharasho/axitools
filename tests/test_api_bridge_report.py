import io
from pathlib import Path

import pytest
import pytest_asyncio

from axitools.api.bridge_worker import RateLimiter
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

# A minimal valid 1x1 PNG.
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


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
        self.emoji_registry = {}
        self.sent_payloads = []

    async def send_bridge_report(self, channel, payload, files=None):
        self.sent_payloads.append(payload)


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


@pytest.fixture
def bridge_key(bot):
    """A key scoped to guild 123, channel 999."""
    key = generate_bridge_key()
    bot.storage.add_bridge_key(123, 999, hash_app_key(key), created_by=42)
    return key


def test_rate_limiter_allows_burst_then_blocks():
    limiter = RateLimiter(rate_per_minute=10, burst=5)
    for _ in range(5):
        assert limiter.check("hash") is None
    retry_after = limiter.check("hash")
    assert retry_after is not None and retry_after > 0


def test_rate_limiter_is_per_key():
    limiter = RateLimiter(rate_per_minute=10, burst=1)
    assert limiter.check("a") is None
    assert limiter.check("b") is None
    assert limiter.check("a") is not None


@pytest.mark.asyncio
async def test_report_queues_and_substitutes(api_client, bridge_key, bot):
    bot.emoji_registry = {"firebrand": "<:firebrand:111111111111111111>"}
    resp = await api_client.post(
        "/bridge/report",
        headers=_bearer(bridge_key),
        json={
            "embeds": [
                {"fields": [{"name": "Damage", "value": "{{spec:firebrand}} Alice"}]}
            ]
        },
    )
    assert resp.status == 202
    sent = bot.sent_payloads[-1]
    assert sent["embeds"][0]["fields"][0]["value"] == (
        "<:firebrand:111111111111111111> Alice"
    )


@pytest.mark.asyncio
async def test_report_rejects_unknown_keys(api_client, bridge_key):
    resp = await api_client.post(
        "/bridge/report",
        headers=_bearer(bridge_key),
        json={"embeds": [{"image": {"url": "http://x"}}]},
    )
    assert resp.status == 400
    assert "image" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_report_403_when_channel_is_gone(api_client, bot, monkeypatch):
    from axitools.api.server import generate_bridge_key, hash_app_key

    key = generate_bridge_key()
    bot.storage.add_bridge_key(123, 5555, hash_app_key(key), created_by=42)
    resp = await api_client.post(
        "/bridge/report", headers=_bearer(key), json={"content": "hi"}
    )
    assert resp.status == 403


@pytest.mark.asyncio
async def test_report_429_includes_retry_after(api_client, bridge_key):
    for _ in range(5):
        await api_client.post(
            "/bridge/report", headers=_bearer(bridge_key), json={"content": "hi"}
        )
    resp = await api_client.post(
        "/bridge/report", headers=_bearer(bridge_key), json={"content": "hi"}
    )
    assert resp.status == 429
    assert "Retry-After" in resp.headers


@pytest.mark.asyncio
async def test_report_accepts_multipart_with_png(api_client, bridge_key, bot):
    form = _build_form({"content": "hi"}, [("screenshot.png", PNG_BYTES, "image/png")])
    resp = await api_client.post(
        "/bridge/report", headers=_bearer(bridge_key), data=form
    )
    assert resp.status == 202
    assert bot.sent_payloads[-1]["content"] == "hi"


@pytest.mark.asyncio
async def test_report_rejects_multipart_non_png(api_client, bridge_key):
    form = _build_form(
        {"content": "hi"}, [("payload.txt", b"not a png", "text/plain")]
    )
    resp = await api_client.post(
        "/bridge/report", headers=_bearer(bridge_key), data=form
    )
    assert resp.status == 400


def _build_form(payload_json: dict, files):
    import json as _json

    from aiohttp import FormData

    form = FormData()
    form.add_field(
        "payload_json",
        _json.dumps(payload_json),
        content_type="application/json",
    )
    for filename, content, content_type in files:
        form.add_field(
            "file",
            content,
            filename=filename,
            content_type=content_type,
        )
    return form
