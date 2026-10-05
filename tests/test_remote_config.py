import json
from pathlib import Path

import pytest
from aioresponses import aioresponses

from axitools.remote_config import (
    InvalidIdentityError,
    RemoteConfig,
    hash_identity,
    normalize_identity,
    policy_for,
    try_hash_identity,
)

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "axi_config_hashing.json").read_text(encoding="utf-8")
)
URL = "https://config.test/v1/manifest?app=axitools"


@pytest.mark.parametrize("v", VECTORS["valid"], ids=lambda v: f"{v['kind']}:{v['input']!r}")
def test_valid_vectors(v):
    assert normalize_identity(v["kind"], v["input"]) == v["normalized"]
    assert hash_identity(v["kind"], v["input"]) == v["hash"]
    # Idempotent: normalizing the normalized form changes nothing.
    assert normalize_identity(v["kind"], v["normalized"]) == v["normalized"]


@pytest.mark.parametrize("v", VECTORS["invalid"], ids=lambda v: f"{v['kind']}:{v['input']!r}")
def test_invalid_vectors(v):
    with pytest.raises(InvalidIdentityError):
        normalize_identity(v["kind"], v["input"])
    assert try_hash_identity(v["kind"], v["input"]) is None


def test_trim_matches_javascript_not_python_isspace():
    # JS trim strips U+FEFF and U+3000 ...
    assert normalize_identity("gw2_account", "﻿　Name.1234\n") == "name.1234"
    # ... but keeps U+001C and U+0085, which Python's str.strip() would remove.
    assert normalize_identity("gw2_account", "\x1cName.1234") == "\x1cname.1234"
    with pytest.raises(InvalidIdentityError):
        normalize_identity("gw2_account", "Name.1234\x85")


def test_patterns_match_javascript():
    with pytest.raises(InvalidIdentityError):
        normalize_identity("gw2_account", "Na\nme.1234")  # "." never matches a line terminator
    with pytest.raises(InvalidIdentityError):
        normalize_identity("gw2_account", "Name.١٢٣٤")  # \d is ASCII-only
    with pytest.raises(InvalidIdentityError):
        normalize_identity("discord_user", "123456789012345678\n" + "x")


def test_policy_for_only_accepts_a_real_remote_config():
    from unittest.mock import MagicMock

    assert policy_for(MagicMock()) is None
    assert policy_for(object()) is None
    holder = MagicMock()
    holder.remote_config = RemoteConfig(None)
    assert policy_for(holder) is holder.remote_config


def test_from_env(monkeypatch):
    monkeypatch.delenv("AXI_CONFIG_URL", raising=False)
    assert RemoteConfig.from_env().url == "https://config.axi.link/v1/manifest?app=axitools"
    monkeypatch.setenv("AXI_CONFIG_URL", "http://localhost:8787/")
    assert RemoteConfig.from_env().url == "http://localhost:8787/v1/manifest?app=axitools"
    monkeypatch.setenv("AXI_CONFIG_URL", "off")
    assert RemoteConfig.from_env().enabled is False


def test_is_blocked_and_any_blocked():
    policy = RemoteConfig(None)
    assert policy.is_blocked("gw2_account", "Name.1234") is False
    policy.set_denylist([hash_identity("gw2_account", "Name.1234")])
    assert policy.is_blocked("gw2_account", " NAME.1234 ") is True
    assert policy.is_blocked("gw2_account", "Other.1234") is False
    assert policy.is_blocked("gw2_account", "not valid") is False
    assert policy.any_blocked([("gw2_guild", "x"), ("gw2_account", "name.1234")]) is True
    assert policy.any_blocked([]) is False


@pytest.mark.asyncio
async def test_refresh_loads_denylist_flags_and_etag():
    policy = RemoteConfig("https://config.test")
    banned = hash_identity("discord_server", "1100000000000000001")
    with aioresponses() as m:
        m.get(URL, payload={"version": 7, "flags": {"contactUrl": "mailto:a@b.c"}, "minVersion": None,
                            "notice": None, "denylist": [banned]}, headers={"ETag": '"v7"'})
        assert await policy.refresh() is True
    assert policy.version == 7
    assert policy.flag("contactUrl") == "mailto:a@b.c"
    assert policy.is_blocked("discord_server", "1100000000000000001") is True

    with aioresponses() as m:
        m.get(URL, status=304)
        assert await policy.refresh() is False
        sent = list(m.requests.values())[0][0].kwargs["headers"]
        assert sent["If-None-Match"] == '"v7"'
    assert policy.is_blocked("discord_server", "1100000000000000001") is True


@pytest.mark.asyncio
async def test_refresh_reports_unchanged_list():
    policy = RemoteConfig("https://config.test")
    with aioresponses() as m:
        m.get(URL, payload={"version": 1, "flags": {}, "denylist": []})
        m.get(URL, payload={"version": 2, "flags": {"x": 1}, "denylist": []})
        assert await policy.refresh() is False
        assert await policy.refresh() is False
    assert policy.version == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"status": 503},
    {"body": "not json", "status": 200},
    {"payload": {"version": 1}},
    {"exception": OSError("dns")},
])
async def test_refresh_fails_open_and_keeps_last_good_list(kwargs, caplog):
    policy = RemoteConfig("https://config.test")
    policy.set_denylist([hash_identity("gw2_account", "Name.1234")])
    with aioresponses() as m:
        m.get(URL, **kwargs)
        assert await policy.refresh() is False
    assert policy.is_blocked("gw2_account", "Name.1234") is True
    assert "Axi manifest" in caplog.text


@pytest.mark.asyncio
async def test_refresh_is_a_noop_when_disabled():
    policy = RemoteConfig(None)
    with aioresponses() as m:
        assert await policy.refresh() is False
        assert m.requests == {}
