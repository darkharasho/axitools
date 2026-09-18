import asyncio
import io
from pathlib import Path

import pytest
import pytest_asyncio

from axitools.api.bridge_worker import BridgeSendQueue, RateLimiter
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


class FakePermissions:
    def __init__(self, send_messages: bool = True) -> None:
        self.send_messages = send_messages


class FakeChannel:
    def __init__(self, channel_id: int, name: str, *, can_send: bool = True) -> None:
        self.id = channel_id
        self.name = name
        self._can_send = can_send

    def permissions_for(self, member) -> FakePermissions:
        return FakePermissions(self._can_send)


class FakeMe:
    id = 1


class FakeGuild:
    def __init__(self, guild_id: int, name: str) -> None:
        self.id = guild_id
        self.name = name
        self._channels = {999: FakeChannel(999, "wvw-reports")}
        self.me = FakeMe()

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)


class FakeBot:
    """Minimal stand-in for AxiToolsBot: just .storage and .guilds.

    ``bridge_queue`` mirrors the real bot's default of ``None`` until
    something starts one — the startup-race fix means the HTTP layer must
    treat that as "not ready" (503), never fall back to an inline send.
    """

    def __init__(self, root: Path) -> None:
        self.storage = StorageManager(root)
        self.guilds = [FakeGuild(123, "Vigil Keep"), FakeGuild(456, "Durmand Priory")]
        self.emoji_registry = {}
        self.sent_payloads = []
        self.bridge_queue = None

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
    # Mirror the fixed bot startup order: the queue must exist and be
    # running before the server is reachable, so every test through this
    # fixture exercises the real queued path, not a since-removed inline
    # fallback.
    bot.bridge_queue = BridgeSendQueue(bot)
    bot.bridge_queue.start()
    app = build_app(bot, token="test-token")
    return await aiohttp_client(app)


async def _drain(bot) -> None:
    """Wait for the bot's bridge queue to finish processing submitted items."""
    await bot.bridge_queue._queue.join()


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
    await _drain(bot)
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
async def test_report_403_when_bot_cannot_send_in_channel(api_client, bot, bridge_key):
    """I2: View Channel but not Send Messages must be caught here, not
    silently swallowed later inside the async send worker."""
    bot.guilds[0]._channels[999]._can_send = False
    resp = await api_client.post(
        "/bridge/report", headers=_bearer(bridge_key), json={"content": "hi"}
    )
    assert resp.status == 403
    assert "send messages" in (await resp.json())["error"]
    await _drain(bot)
    assert bot.sent_payloads == []


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
    await _drain(bot)
    assert bot.sent_payloads[-1]["content"] == "hi"


@pytest.mark.asyncio
async def test_report_accepts_a_png_over_aiohttps_default_body_limit(
    api_client, bridge_key, bot
):
    # aiohttp's default client_max_size is 1 MiB and its 413 fires before any
    # handler code, so a map-slice PNG over that size would be rejected no
    # matter what the explicit attachment limits say. build_app() raises the
    # cap for exactly this reason; without that, this test 413s.
    big_png = PNG_BYTES + b"\x00" * (2 * 1024 * 1024)
    form = _build_form({"content": "hi"}, [("slice.png", big_png, "image/png")])
    resp = await api_client.post(
        "/bridge/report", headers=_bearer(bridge_key), data=form
    )
    assert resp.status == 202
    await _drain(bot)
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


# ---------------------------------------------------------------------------
# 503: no inline-send fallback. If bridge_queue is missing or not running the
# handler must refuse loudly rather than sending inline from the request.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_503_when_queue_missing(aiohttp_client, bot, bridge_key):
    # bot fixture's bridge_queue defaults to None, unlike the api_client
    # fixture which starts one — this reproduces the pre-queue startup window.
    app = build_app(bot, token="test-token")
    client = await aiohttp_client(app)
    resp = await client.post(
        "/bridge/report", headers=_bearer(bridge_key), json={"content": "hi"}
    )
    assert resp.status == 503
    assert bot.sent_payloads == []


@pytest.mark.asyncio
async def test_report_503_when_queue_not_started(aiohttp_client, bot, bridge_key):
    bot.bridge_queue = BridgeSendQueue(bot)  # constructed, but .start() never called
    app = build_app(bot, token="test-token")
    client = await aiohttp_client(app)
    resp = await client.post(
        "/bridge/report", headers=_bearer(bridge_key), json={"content": "hi"}
    )
    assert resp.status == 503
    assert bot.sent_payloads == []


# ---------------------------------------------------------------------------
# BridgeSendQueue itself: every HTTP-level test above only proves the handler
# calls queue.submit(); none of them previously exercised the consumer loop.
# ---------------------------------------------------------------------------


class _RecordingSendBot:
    def __init__(self) -> None:
        self.calls = []

    async def send_bridge_report(self, channel, payload, files=None):
        self.calls.append((channel, payload, files))


@pytest.mark.asyncio
async def test_bridge_send_queue_delivers_submitted_report():
    bot = _RecordingSendBot()
    queue = BridgeSendQueue(bot)
    queue.start()

    channel = object()
    payload = {"content": "hi"}
    await queue.submit(channel, payload, files=None)
    await queue._queue.join()

    assert bot.calls == [(channel, payload, None)]


class _FlakyOnceSendBot:
    """Raises on the first send, then delivers normally."""

    def __init__(self) -> None:
        self.calls = []

    async def send_bridge_report(self, channel, payload, files=None):
        self.calls.append(payload)
        if len(self.calls) == 1:
            raise RuntimeError("simulated Discord failure")


@pytest.mark.asyncio
async def test_bridge_send_queue_survives_send_exception():
    bot = _FlakyOnceSendBot()
    queue = BridgeSendQueue(bot)
    queue.start()

    await queue.submit(object(), {"content": "first"})
    await queue._queue.join()
    # The consumer must still be alive after a failed send.
    assert queue.is_running()

    await queue.submit(object(), {"content": "second"})
    await queue._queue.join()

    assert [p["content"] for p in bot.calls] == ["first", "second"]


@pytest.mark.asyncio
async def test_bridge_send_queue_full_backlog_raises_queue_full():
    bot = _RecordingSendBot()
    queue = BridgeSendQueue(bot)
    # Don't start the consumer: fill the (shrunk) backlog to prove submit()
    # surfaces asyncio.QueueFull rather than hanging or silently dropping.
    queue._queue = asyncio.Queue(maxsize=1)

    await queue.submit(object(), {"content": "one"})
    with pytest.raises(asyncio.QueueFull):
        await queue.submit(object(), {"content": "two"})


# ---------------------------------------------------------------------------
# Previously-untested 400 branches.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_400_on_malformed_top_level_json(api_client, bridge_key):
    resp = await api_client.post(
        "/bridge/report",
        headers={**_bearer(bridge_key), "Content-Type": "application/json"},
        data=b"{not valid json",
    )
    assert resp.status == 400
    assert "invalid JSON body" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_report_400_on_multipart_missing_payload_json(api_client, bridge_key):
    from aiohttp import FormData

    form = FormData()
    form.add_field(
        "file", PNG_BYTES, filename="screenshot.png", content_type="image/png"
    )
    resp = await api_client.post(
        "/bridge/report", headers=_bearer(bridge_key), data=form
    )
    assert resp.status == 400
    assert "payload_json" in (await resp.json())["error"]


@pytest.mark.asyncio
async def test_report_400_on_invalid_json_inside_payload_json(api_client, bridge_key):
    from aiohttp import FormData

    form = FormData()
    form.add_field(
        "payload_json", "{not valid json", content_type="application/json"
    )
    resp = await api_client.post(
        "/bridge/report", headers=_bearer(bridge_key), data=form
    )
    assert resp.status == 400
    assert "invalid JSON in payload_json" in (await resp.json())["error"]


# ---------------------------------------------------------------------------
# I1: the central invariant is enforce_limits(substitute_payload(payload,
# registry)). tests/test_emoji_registry.py:127 proves the two functions work
# correctly IN THAT ORDER when composed directly -- it does not prove the
# handler actually composes them that way. This test goes through the real
# HTTP handler so a reversal at server.py's call site turns it red.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_field_fitting_before_substitution_overflows_after_stays_row_aligned(
    api_client, bridge_key, bot
):
    bot.emoji_registry = {"firebrand": "<:firebrand:111111111111111111>"}
    row = "{{spec:firebrand}} Player00"
    value = "\n".join(row for _ in range(36))
    assert len(value) < 1024  # fits comfortably BEFORE substitution

    resp = await api_client.post(
        "/bridge/report",
        headers=_bearer(bridge_key),
        json={"embeds": [{"fields": [{"name": "Damage", "value": value}]}]},
    )
    assert resp.status == 202
    await _drain(bot)

    delivered = bot.sent_payloads[-1]["embeds"][0]["fields"][0]["value"]
    assert len(delivered) <= 1024
    # Row-aligned: substitution's token growth must never leave a half
    # `<:name:id>` reference -- each surviving row is a complete substituted
    # row, not a partial one.
    lines = delivered.split("\n")
    assert lines  # something survived truncation
    for line in lines:
        assert line == "<:firebrand:111111111111111111> Player00"
