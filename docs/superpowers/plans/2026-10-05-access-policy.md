# Axi access policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AxiTools enforces the Axi access denylist. Specifically:
- The bot leaves revoked Discord servers.
- It answers revoked users with an ephemeral "Unavailable."
- It refuses to link revoked GW2 accounts or guilds.
- The REST API returns 403 for revoked servers.
- An owner-only `/access` command in the private admin server manages the list.

**Architecture:** `axitools/remote_config.py` is the Python port of the axi-config identity normalization and hashing, verified against the shared vectors. It also contains a `RemoteConfig` client that holds the last good manifest in memory, refreshed at startup and every 5 minutes. All checks are synchronous lookups against that in-memory hash set. Callers reach it through `policy_for(bot)`, which returns `None` for anything that is not a real `RemoteConfig`, so the existing tests' `MagicMock` bots are never "blocked".

The owner-only `cogs/access_admin.py` calls the axi-config admin API. It is loaded only when the admin guild, owner ID and admin token are all configured, and it is registered only in the admin guild.

**Tech Stack:** Python 3.11+, discord.py ≥ 2.3 (app_commands), aiohttp, pytest + pytest-asyncio + aioresponses + pytest-aiohttp.

**Spec:** `darkharasho/axi-config`, file `docs/superpowers/specs/2026-10-05-axi-config-design.md`. The relevant sections are "Server-side enforcement → axitools" and "Administration → Discord command". The spec repo is private; the rules this plan needs are copied into Global Constraints.

## Spec deltas (decided here)

- **The REST API cannot refuse a user.** API requests carry a bearer key scoped to a Discord server or channel, never a Discord user. The API therefore refuses by server only: the path `guild_id`, the key's scoped guild, or the bridge key's guild. It also refuses revoked GW2 guilds on the two routes that set a GW2 guild id.
- **Same refusal on the REST guild-role and alliance routes.** The spec names only the `/guildroles` cog for refusing revoked GW2 guilds. The REST routes that do the same thing (`PUT …/guild-roles/{gw2_guild_id}` and `PUT …/alliance` with `guild_id`) refuse them too.
- **Linking also refuses an account in a revoked guild.** A GW2 guild ban covers every member, so `/apikey add` and `/apikey refresh` refuse a key whose account is revoked or belongs to any revoked guild.
- **Button and select-menu interactions are not covered.** The global check is `CommandTree.interaction_check`, which covers slash and context-menu commands. Persistent view components (buttons, selects) are not routed through it. A revoked server is left by the bot entirely; a revoked user in a non-revoked server can still click existing buttons. This is accepted as a soft limit.
- **Discord command shape.** `/access revoke <kind> <value> [reason]`, `/access revoke-user <user> [reason]` (the user picker for `discord_user`), `/access restore <ban_id>`, `/access list`.

## Global Constraints

- Normalization: NFC → trim → lowercase → NFC. Then the per-kind pattern applies, and a value that fails it is invalid:
  - `gw2_account`: `^.+\.\d{4}$`
  - `gw2_guild`: lowercase UUID
  - `discord_user` and `discord_server`: `^\d{17,20}$`
  - `github_user`: `^[1-9]\d{0,19}$`

  Here "trim" is JavaScript's `String.prototype.trim` set and `\d` is ASCII 0–9. `hash = sha256_hex(kind + ":" + normalized)`. Every hash must reproduce `tests/fixtures/axi_config_hashing.json`, which is a verbatim copy of axi-config `test-vectors/hashing.json` at commit 7fccf7d.
- Manifest: `GET {AXI_CONFIG_URL}/v1/manifest?app=axitools`. The default `AXI_CONFIG_URL` is `https://config.axi.link`; `off` or empty disables the policy. The response is `{ version, flags, minVersion, notice, denylist: string[] }`, where `denylist` holds only SHA-256 hex strings. Requests send `If-None-Match` with the last `ETag`.
- Refresh every 300 s. Removing a ban restores access within 5 minutes.
- The policy fails open: before the first successful fetch, or on any error, nothing is refused. A failed refresh keeps the last good list.
- Refusal texts are neutral and never say which identifier matched:
  - interactions and linking: `Unavailable.`
  - REST: HTTP 403 `{"error": "unavailable"}`
- Revoked servers: the bot leaves them on startup, on join, and after every refresh. `data/guild_<id>/` is left in place.
- No telemetry: nothing reports a match anywhere. Leaving a server is logged locally with the server id only.
- Admin cog env vars: `AXI_ADMIN_GUILD_ID`, `AXI_OWNER_ID`, `AXI_CONFIG_ADMIN_TOKEN` (plus `AXI_CONFIG_URL`). If any of the first three is missing, the cog is not loaded. All of its replies are ephemeral, and it replies only to `AXI_OWNER_ID`.
- Admin API: `Authorization: Bearer <AXI_CONFIG_ADMIN_TOKEN>`.
  - `POST /v1/admin/bans {kind, value, reason, createdBy}` → `{ban, created}`
  - `DELETE /v1/admin/bans/{id}` → `{ban, changed}`
  - `GET /v1/admin/bans` → `{bans}`

  Errors are `{error: <code>}`. `createdBy` is the operator's Discord user id string.
- Work on a new branch `feat/access-policy` created from `main`. The current checkout is the unrelated branch `feat/guildroles-plan-flag`.
- Tests: `PYTHONPATH=. pytest tests`. Commits use Conventional Commits and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Existing tests build cogs with `MagicMock()` bots. `getattr(MagicMock(), "remote_config")` is a truthy mock, and any code that skips `policy_for` would refuse everyone in those tests, or in production if the attribute is missing.
2. `config.axi.link` is not live yet, so DNS fails. Startup must not hang: the first refresh has a 10 s timeout and never raises. The bot and API start normally with nothing refused.
3. A server revoked while the bot is in it must be left within one refresh. If `guild.leave()` fails with an `HTTPException`, the loop must log it and continue with the other servers.
4. Unauthenticated API requests to a revoked server stay 401, not 403. Auth runs first, so the endpoint does not confirm revocation status to strangers.
5. `/access list` with many bans must stay under Discord's 2000-character message limit.

## File Structure

- Create `axitools/remote_config.py`: identity normalization/hashing, the `RemoteConfig` manifest client, and `policy_for`.
- Create `tests/fixtures/axi_config_hashing.json`: the shared vectors (copied verbatim).
- Create `tests/test_remote_config.py`.
- Modify `axitools/bot.py`: `AxiCommandTree`, `remote_config`, `leave_blocked_guilds`, `_policy_tick` and the refresh loop, plus loading `access_admin`.
- Create `tests/test_bot_access_policy.py`.
- Modify `axitools/cogs/account_self.py` (`_validate_api_key`) and `axitools/cogs/guild_roles.py` (`set_guild_role`, `set_alliance_guild`).
- Create `tests/test_access_policy_cogs.py`.
- Modify `axitools/api/server.py`: `_policy_middleware`, and the GW2 guild checks in `_handle_guild_roles_put` and `_handle_alliance_put`.
- Create `tests/test_api_access_policy.py`.
- Create `axitools/cogs/access_admin.py` and `tests/test_cogs_access_admin.py`.
- Modify `.env.example`.

---

### Task 1: Identity hashing and the manifest client

**Files:**
- Create: `axitools/remote_config.py`
- Create: `tests/fixtures/axi_config_hashing.json`
- Test: `tests/test_remote_config.py`

**Interfaces:**
- Produces:
  - `IDENTITY_KINDS: tuple[str, ...]`
  - `InvalidIdentityError(ValueError)`
  - `normalize_identity(kind: str, value: str) -> str`
  - `hash_identity(kind: str, value: str) -> str`
  - `try_hash_identity(kind: str, value: str) -> str | None`
  - class `RemoteConfig(base_url: str | None, *, app_id: str = "axitools")` with:
    - `.url`, `.version`, `.flags`, `.enabled`
    - `from_env()`
    - `set_denylist(hashes) -> bool`
    - `is_blocked(kind, value) -> bool`
    - `any_blocked(identities: Iterable[tuple[str, str]]) -> bool`
    - `flag(key, default=None)`
    - `async refresh() -> bool`
  - `policy_for(obj) -> RemoteConfig | None`
  - Constants `DEFAULT_BASE_URL`, `REFRESH_SECONDS = 300`, `FETCH_TIMEOUT_SECONDS = 10`.

- [ ] **Step 1: Copy the shared vectors**

Create `tests/fixtures/axi_config_hashing.json` with exactly this content. It is a copy of axi-config `test-vectors/hashing.json` at 7fccf7d. The `\u` escapes must stay literal JSON escapes; check this with `grep -c 'u0303' tests/fixtures/axi_config_hashing.json`, which should print `1`.

```json
{
    "valid": [
        { "kind": "gw2_account", "input": "Name.1234", "normalized": "name.1234", "hash": "57618b5eb6509d74c9625c25e02225cd901544920541936c5d31539679547509" },
        { "kind": "gw2_account", "input": "  Test Account.0001 ", "normalized": "test account.0001", "hash": "ed0473b6f47426585d104930984e9db32e9af8c8f9bbabd66919e9a3ca0fbf3e" },
        { "kind": "gw2_account", "input": "Ñoño.4321", "normalized": "ñoño.4321", "hash": "bd29e2f0e5ad9840fa6b33af7ced10475e5b49098da660ae3496dc4f69ad587e" },
        { "kind": "gw2_guild", "input": "4BBB52AA-D768-4FC6-8EDE-C299F2822F0F", "normalized": "4bbb52aa-d768-4fc6-8ede-c299f2822f0f", "hash": "8370f0eea5044c9f489b3da126719d673ed5f3f83135027c0aa0e4f68a0cea75" },
        { "kind": "discord_user", "input": "123456789012345678", "normalized": "123456789012345678", "hash": "40a1a7f30577de59d0c1475d5c1a3c22d1fcea99a07e321b31476fe8cc158d97" },
        { "kind": "discord_user", "input": " 98765432109876543 ", "normalized": "98765432109876543", "hash": "10ac4980cafe2f25d59e193debda1a573c954ca6e3e0bf46f9c19ac33780f433" },
        { "kind": "discord_server", "input": "1100000000000000001", "normalized": "1100000000000000001", "hash": "d4b7ba484cc768e7100cc3782813da1fe4c68910d08ac878a6f94f250c68503c" },
        { "kind": "github_user", "input": "1234567", "normalized": "1234567", "hash": "ba52ca31dec642ad640ce0bbbf8382b6f89493a8ac7d965d2e69659c654a6b2f" },
        { "kind": "gw2_account", "input": "T̈est.1234", "normalized": "ẗest.1234", "hash": "bb3b9cf9e29c0cd17444f1d352057fcc16b770d584330b5c1cdb12bd3b666400" }
    ],
    "invalid": [
        { "kind": "gw2_account", "input": "NoNumber" },
        { "kind": "gw2_account", "input": "Name.12" },
        { "kind": "gw2_account", "input": "   " },
        { "kind": "gw2_guild", "input": "not-a-uuid" },
        { "kind": "gw2_guild", "input": "4bbb52aa-d768-4fc6-8ede-c299f2822f0" },
        { "kind": "discord_user", "input": "12345" },
        { "kind": "discord_user", "input": "abc123456789012345678" },
        { "kind": "discord_server", "input": "" },
        { "kind": "github_user", "input": "octocat" },
        { "kind": "github_user", "input": "0123" },
        { "kind": "steam_user", "input": "1" }
    ]
}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_remote_config.py`:

```python
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
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_remote_config.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'axitools.remote_config'`.

- [ ] **Step 4: Implement `axitools/remote_config.py`**

```python
"""Axi remote config client: feature flags and the access denylist.

The Axi apps can revoke access for people, GW2 guilds and Discord servers that
violate their terms of use (see README, "Access"). The list is published at
config.axi.link as a manifest whose ``denylist`` holds only SHA-256 hashes of
``kind:normalized`` identifiers, so it names nobody. This module is the Python
port of the identity normalization in darkharasho/axi-config and must
reproduce ``tests/fixtures/axi_config_hashing.json`` exactly.

Checks are synchronous lookups against the last good manifest held in memory.
The bot refreshes it every ``REFRESH_SECONDS``. Before the first successful
fetch, or when the manifest is unreachable, nothing is refused (fail open).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import unicodedata
from typing import Any, Iterable

import aiohttp

LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://config.axi.link"
APP_ID = "axitools"
REFRESH_SECONDS = 300
FETCH_TIMEOUT_SECONDS = 10

IDENTITY_KINDS = ("gw2_account", "gw2_guild", "discord_user", "discord_server", "github_user")

# JavaScript's String.prototype.trim set (WhiteSpace + LineTerminator). Python's
# str.strip() also removes U+001C-U+001F and U+0085, which JS keeps, so the set
# is spelled out to keep both implementations identical.
_JS_WHITESPACE = (
    "\t\n\v\f\r          "
    "        　﻿"
)

# Ports of the JS patterns. In JS, "." (with the u flag) excludes the four line
# terminators and \d is ASCII-only. All patterns are applied with fullmatch.
_PATTERNS = {
    "gw2_account": re.compile(r"[^\n\r  ]+\.[0-9]{4}"),
    "gw2_guild": re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
    "discord_user": re.compile(r"[0-9]{17,20}"),
    "discord_server": re.compile(r"[0-9]{17,20}"),
    "github_user": re.compile(r"[1-9][0-9]{0,19}"),
}


class InvalidIdentityError(ValueError):
    """Raised for an unknown kind or a value that is not a valid identifier."""


def normalize_identity(kind: str, value: str) -> str:
    if kind not in _PATTERNS or not isinstance(value, str):
        raise InvalidIdentityError(f"invalid {kind} identifier")
    normalized = unicodedata.normalize(
        "NFC", unicodedata.normalize("NFC", value).strip(_JS_WHITESPACE).lower()
    )
    if not _PATTERNS[kind].fullmatch(normalized):
        raise InvalidIdentityError(f"invalid {kind} identifier")
    return normalized


def _hash_normalized(kind: str, normalized: str) -> str:
    return hashlib.sha256(f"{kind}:{normalized}".encode("utf-8")).hexdigest()


def hash_identity(kind: str, value: str) -> str:
    return _hash_normalized(kind, normalize_identity(kind, value))


def try_hash_identity(kind: str, value: str) -> str | None:
    try:
        return hash_identity(kind, value)
    except InvalidIdentityError:
        return None


class RemoteConfig:
    """The last good Axi manifest for this app, held in memory."""

    def __init__(self, base_url: str | None, *, app_id: str = APP_ID) -> None:
        self.url = f"{base_url.rstrip('/')}/v1/manifest?app={app_id}" if base_url else None
        self.version: int | None = None
        self.flags: dict[str, Any] = {}
        self._denylist: frozenset[str] = frozenset()
        self._etag: str | None = None

    @classmethod
    def from_env(cls) -> "RemoteConfig":
        raw = os.getenv("AXI_CONFIG_URL", DEFAULT_BASE_URL).strip()
        return cls(None if raw.lower() in {"", "off"} else raw)

    @property
    def enabled(self) -> bool:
        return self.url is not None

    def set_denylist(self, hashes: Iterable[str]) -> bool:
        """Replace the hash set. Returns True when it changed."""
        new = frozenset(h for h in hashes if isinstance(h, str))
        changed = new != self._denylist
        self._denylist = new
        return changed

    def is_blocked(self, kind: str, value: str) -> bool:
        if not self._denylist:
            return False
        digest = try_hash_identity(kind, value)
        return digest is not None and digest in self._denylist

    def any_blocked(self, identities: Iterable[tuple[str, str]]) -> bool:
        return any(self.is_blocked(kind, value) for kind, value in identities)

    def flag(self, key: str, default: Any = None) -> Any:
        return self.flags.get(key, default)

    async def refresh(self) -> bool:
        """Fetch the manifest. Returns True when the denylist changed. Never raises."""
        if self.url is None:
            return False
        headers = {"Accept": "application/json"}
        if self._etag:
            headers["If-None-Match"] = self._etag
        try:
            timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self.url, headers=headers) as resp:
                    if resp.status == 304:
                        return False
                    if resp.status != 200:
                        LOGGER.warning("Axi manifest fetch failed: HTTP %s", resp.status)
                        return False
                    body = await resp.json(content_type=None)
                    etag = resp.headers.get("ETag")
        except Exception as exc:  # network, timeout, or a body that is not JSON
            LOGGER.warning("Axi manifest fetch failed: %s", exc)
            return False
        if not isinstance(body, dict) or not isinstance(body.get("denylist"), list):
            LOGGER.warning("Axi manifest has no denylist; keeping the last good one")
            return False
        self._etag = etag
        version = body.get("version")
        self.version = version if isinstance(version, int) else None
        flags = body.get("flags")
        self.flags = dict(flags) if isinstance(flags, dict) else {}
        return self.set_denylist(body["denylist"])


def policy_for(obj: Any) -> RemoteConfig | None:
    """The object's ``remote_config`` if it is a real RemoteConfig, else None.

    Cogs and the API receive bots that tests replace with MagicMock; a mock
    attribute must never be mistaken for a policy that blocks everyone.
    """
    policy = getattr(obj, "remote_config", None)
    return policy if isinstance(policy, RemoteConfig) else None


__all__ = [
    "DEFAULT_BASE_URL",
    "FETCH_TIMEOUT_SECONDS",
    "IDENTITY_KINDS",
    "InvalidIdentityError",
    "REFRESH_SECONDS",
    "RemoteConfig",
    "hash_identity",
    "normalize_identity",
    "policy_for",
    "try_hash_identity",
]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_remote_config.py -q`
Expected: PASS (9 valid + 11 invalid vector cases, and the remaining tests).

- [ ] **Step 6: Commit**

```bash
git add axitools/remote_config.py tests/fixtures/axi_config_hashing.json tests/test_remote_config.py
git commit -m "feat(access): Axi identity hashing and manifest client

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Bot-level enforcement

**Files:**
- Modify: `axitools/bot.py`
- Test: `tests/test_bot_access_policy.py`

**Interfaces:**
- Consumes from Task 1: `RemoteConfig`, `REFRESH_SECONDS`, `policy_for`.
- Produces:
  - `AxiToolsBot.remote_config: RemoteConfig`
  - `AxiToolsBot.leave_blocked_guilds(guilds: Iterable[discord.Guild] | None = None) -> int`
  - `AxiToolsBot._policy_tick() -> None`
  - class `AxiCommandTree(app_commands.CommandTree)` with an `interaction_check` override. It is used as the bot's `tree_cls`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bot_access_policy.py`:

```python
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from axitools.bot import AxiCommandTree, AxiToolsBot
from axitools.remote_config import RemoteConfig, hash_identity

USER = 123456789012345678
SERVER = 1100000000000000001
OTHER_SERVER = 1100000000000000002


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.leave = AsyncMock()


@pytest.fixture
def bot(tmp_path: Path) -> AxiToolsBot:
    bot = AxiToolsBot(storage_root=tmp_path)
    bot.remote_config = RemoteConfig(None)
    return bot


def _interaction(user_id: int, guild_id: int | None) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.guild_id = guild_id
    interaction.response.send_message = AsyncMock()
    return interaction


def test_bot_uses_the_policy_tree(bot):
    assert isinstance(bot.tree, AxiCommandTree)
    assert isinstance(bot.remote_config, RemoteConfig)


@pytest.mark.asyncio
async def test_revoked_user_is_answered_unavailable(bot):
    bot.remote_config.set_denylist([hash_identity("discord_user", str(USER))])
    interaction = _interaction(USER, None)
    assert await bot.tree.interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once_with("Unavailable.", ephemeral=True)


@pytest.mark.asyncio
async def test_interaction_in_revoked_server_is_refused(bot):
    bot.remote_config.set_denylist([hash_identity("discord_server", str(SERVER))])
    assert await bot.tree.interaction_check(_interaction(USER, SERVER)) is False


@pytest.mark.asyncio
async def test_other_users_pass(bot):
    bot.remote_config.set_denylist([hash_identity("discord_user", "223456789012345678")])
    interaction = _interaction(USER, OTHER_SERVER)
    assert await bot.tree.interaction_check(interaction) is True
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_refusal_survives_an_expired_interaction(bot):
    bot.remote_config.set_denylist([hash_identity("discord_user", str(USER))])
    interaction = _interaction(USER, None)
    interaction.response.send_message.side_effect = discord.NotFound(MagicMock(status=404), "gone")
    assert await bot.tree.interaction_check(interaction) is False


@pytest.mark.asyncio
async def test_leave_blocked_guilds_leaves_only_revoked_servers(bot):
    bot.remote_config.set_denylist([hash_identity("discord_server", str(SERVER))])
    revoked, kept = FakeGuild(SERVER), FakeGuild(OTHER_SERVER)
    assert await bot.leave_blocked_guilds([revoked, kept]) == 1
    revoked.leave.assert_awaited_once()
    kept.leave.assert_not_awaited()


@pytest.mark.asyncio
async def test_leave_failure_is_logged_and_does_not_stop_the_loop(bot, caplog):
    bot.remote_config.set_denylist([
        hash_identity("discord_server", str(SERVER)),
        hash_identity("discord_server", str(OTHER_SERVER)),
    ])
    failing, second = FakeGuild(SERVER), FakeGuild(OTHER_SERVER)
    failing.leave.side_effect = discord.HTTPException(MagicMock(status=500), "boom")
    assert await bot.leave_blocked_guilds([failing, second]) == 1
    second.leave.assert_awaited_once()
    assert "Could not leave" in caplog.text


@pytest.mark.asyncio
async def test_on_guild_join_leaves_a_revoked_server_without_syncing(bot):
    bot.remote_config.set_denylist([hash_identity("discord_server", str(SERVER))])
    bot._sync_global_commands = AsyncMock()
    bot._sync_guild_commands = AsyncMock()
    guild = FakeGuild(SERVER)
    await bot.on_guild_join(guild)
    guild.leave.assert_awaited_once()
    bot._sync_guild_commands.assert_not_awaited()


@pytest.mark.asyncio
async def test_policy_tick_refreshes_then_leaves(bot):
    bot.remote_config.refresh = AsyncMock(return_value=True)
    bot.leave_blocked_guilds = AsyncMock(return_value=0)
    await bot._policy_tick()
    bot.remote_config.refresh.assert_awaited_once()
    bot.leave_blocked_guilds.assert_awaited_once()


@pytest.mark.asyncio
async def test_policy_tick_never_raises(bot, caplog):
    bot.remote_config.refresh = AsyncMock(side_effect=RuntimeError("bug"))
    await bot._policy_tick()
    assert "Access policy refresh failed" in caplog.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_bot_access_policy.py -q`
Expected: FAIL with `ImportError: cannot import name 'AxiCommandTree'`.

- [ ] **Step 3: Implement the bot changes**

In `axitools/bot.py`:

1. Update the imports. Add `asyncio` and `Iterable`. Import `app_commands`. Import the policy.

```python
import asyncio
import logging
import os
import time
from typing import Iterable, Set

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

from .api.server import start_api
from .remote_config import REFRESH_SECONDS, RemoteConfig, policy_for
from .storage import DEFAULT_STORAGE_ROOT, GuildConfig, StorageManager
```

2. Add the tree class above `class AxiToolsBot`:

```python
class AxiCommandTree(app_commands.CommandTree):
    """Command tree that refuses users and servers on the Axi access denylist."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        policy = policy_for(self.client)
        if policy is None:
            return True
        identities = [("discord_user", str(interaction.user.id))]
        if interaction.guild_id is not None:
            identities.append(("discord_server", str(interaction.guild_id)))
        if not policy.any_blocked(identities):
            return True
        try:
            await interaction.response.send_message("Unavailable.", ephemeral=True)
        except discord.HTTPException:
            LOGGER.debug("Could not answer a refused interaction", exc_info=True)
        return False
```

3. In `AxiToolsBot.__init__`, add `tree_cls=AxiCommandTree` to the `super().__init__(...)` call. After `self.storage = ...`, add:

```python
        self.remote_config = RemoteConfig.from_env()
        self._policy_task: asyncio.Task | None = None
```

4. In `setup_hook`, after the last `load_extension` line (`axitools.cogs.dev`), add:

```python
        await self.load_extension("axitools.cogs.access_admin")
```

Before `self.bridge_queue = BridgeSendQueue(self)`, add:

```python
        # Load the access denylist before the API accepts requests. refresh()
        # never raises and times out after 10 s; on failure nothing is refused.
        await self.remote_config.refresh()
        self._policy_task = asyncio.create_task(self._policy_loop())
```

The `access_admin` extension is created in Task 4. Until then, add a minimal `axitools/cogs/access_admin.py` with only:

```python
async def setup(bot) -> None:  # replaced in Task 4
    return None
```

5. Add these methods after `send_bridge_report`:

```python
    async def leave_blocked_guilds(self, guilds: Iterable[discord.Guild] | None = None) -> int:
        """Leave every server on the Axi access denylist. Returns how many were left."""
        left = 0
        for guild in list(self.guilds if guilds is None else guilds):
            if not self.remote_config.is_blocked("discord_server", str(guild.id)):
                continue
            try:
                await guild.leave()
            except discord.HTTPException:
                LOGGER.exception("Could not leave server %s (access revoked)", guild.id)
                continue
            LOGGER.info("Left server %s: access revoked", guild.id)
            left += 1
        return left

    async def _policy_tick(self) -> None:
        try:
            await self.remote_config.refresh()
            await self.leave_blocked_guilds()
        except Exception:
            LOGGER.exception("Access policy refresh failed")

    async def _policy_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            await asyncio.sleep(REFRESH_SECONDS)
            await self._policy_tick()
```

6. In `close()`, cancel the loop before cleaning up the API runner:

```python
        if self._policy_task is not None:
            self._policy_task.cancel()
            self._policy_task = None
```

7. Make `on_ready` leave revoked servers before syncing:

```python
    async def on_ready(self) -> None:
        await self.leave_blocked_guilds()
        await self._sync_global_commands()
        for guild in self.guilds:
            await self._sync_guild_commands(guild)
        LOGGER.info("AxiTools is ready. Logged in as %s (%s)", self.user, getattr(self.user, "id", "unknown"))
```

8. Make `on_guild_join` refuse a revoked server:

```python
    async def on_guild_join(self, guild: discord.Guild) -> None:
        if await self.leave_blocked_guilds([guild]):
            return
        await self._sync_global_commands()
        await self._sync_guild_commands(guild)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_bot_access_policy.py tests/test_bot_sanity.py tests/test_bot_emoji_registry.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=. pytest tests -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add axitools/bot.py axitools/cogs/access_admin.py tests/test_bot_access_policy.py
git commit -m "feat(access): refuse revoked users and leave revoked servers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Linking, guild roles and the REST API

**Files:**
- Modify: `axitools/cogs/account_self.py` (`_validate_api_key`)
- Modify: `axitools/cogs/guild_roles.py` (`set_guild_role`, `set_alliance_guild`)
- Modify: `axitools/api/server.py` (`_policy_middleware`, `_handle_guild_roles_put`, `_handle_alliance_put`, `build_app`)
- Test: `tests/test_access_policy_cogs.py`, `tests/test_api_access_policy.py`

**Interfaces:**
- Consumes from Task 1: `policy_for(obj) -> RemoteConfig | None` and `RemoteConfig.any_blocked` / `is_blocked`.
- Produces:
  - `_validate_api_key` raises `ValueError("Unavailable.")` for a revoked account or guild.
  - API responses: 403 `{"error": "unavailable"}`.

- [ ] **Step 1: Write the failing cog tests**

Create `tests/test_access_policy_cogs.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from axitools.cogs.account_self import AccountSelfCog
from axitools.cogs.guild_roles import GuildRolesCog
from axitools.remote_config import RemoteConfig, hash_identity
from axitools.storage import GuildConfig

GUILD = "4bbb52aa-d768-4fc6-8ede-c299f2822f0f"


def _bot(denylist):
    bot = MagicMock()
    bot.remote_config = RemoteConfig(None)
    bot.remote_config.set_denylist(denylist)
    bot.ensure_authorised = AsyncMock(return_value=True)
    bot.get_config = MagicMock(return_value=GuildConfig())
    return bot


def _account_cog(bot, account):
    cog = AccountSelfCog(bot)
    cog._fetch_json = AsyncMock(side_effect=[{"permissions": ["account", "guilds"]}, account])
    cog._fetch_guild_details = AsyncMock(return_value={})
    cog._fetch_character_names = AsyncMock(return_value=[])
    return cog


@pytest.mark.asyncio
async def test_linking_refuses_a_revoked_account():
    cog = _account_cog(_bot([hash_identity("gw2_account", "Name.1234")]), {"name": "Name.1234", "guilds": []})
    with pytest.raises(ValueError, match=r"^Unavailable\.$"):
        await cog._validate_api_key("KEY", allow_missing_permissions=True)
    cog._fetch_guild_details.assert_not_awaited()


@pytest.mark.asyncio
async def test_linking_refuses_a_member_of_a_revoked_guild():
    cog = _account_cog(_bot([hash_identity("gw2_guild", GUILD)]), {"name": "Ok.1234", "guilds": [GUILD.upper()]})
    with pytest.raises(ValueError, match=r"^Unavailable\.$"):
        await cog._validate_api_key("KEY", allow_missing_permissions=True)


@pytest.mark.asyncio
async def test_linking_allows_everyone_else():
    cog = _account_cog(_bot([hash_identity("gw2_account", "Name.1234")]), {"name": "Ok.1234", "guilds": [GUILD]})
    result = await cog._validate_api_key("KEY", allow_missing_permissions=True)
    assert result[3] == "Ok.1234"


@pytest.mark.asyncio
async def test_linking_ignores_a_mock_bot_without_a_policy():
    bot = MagicMock()  # remote_config is a MagicMock, not a RemoteConfig
    cog = _account_cog(bot, {"name": "Name.1234", "guilds": []})
    result = await cog._validate_api_key("KEY", allow_missing_permissions=True)
    assert result[3] == "Name.1234"


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["set_guild_role", "set_alliance_guild"])
async def test_guild_role_setup_refuses_a_revoked_guild(command):
    bot = _bot([hash_identity("gw2_guild", GUILD)])
    cog = GuildRolesCog(bot)
    cog._send_embed = AsyncMock()
    cog._cached_guild_labels = AsyncMock(return_value={})
    interaction = MagicMock()
    interaction.guild.id = 1
    args = (interaction, GUILD, MagicMock()) if command == "set_guild_role" else (interaction, GUILD)
    await getattr(cog, command).callback(cog, *args)
    bot.save_config.assert_not_called()
    assert cog._send_embed.await_args.kwargs["description"] == "Unavailable."
```

- [ ] **Step 2: Write the failing API tests**

Create `tests/test_api_access_policy.py`:

```python
from pathlib import Path

import pytest
import pytest_asyncio

from axitools.api.server import build_app
from axitools.remote_config import RemoteConfig, hash_identity
from axitools.storage import StorageManager

SERVER = 1100000000000000001
OTHER = 1100000000000000002
GW2_GUILD = "4bbb52aa-d768-4fc6-8ede-c299f2822f0f"


class FakeGuild:
    def __init__(self, guild_id: int, name: str) -> None:
        self.id = guild_id
        self.name = name
        self.roles = []
        self.channels = []

    def get_role(self, role_id):
        return None

    def get_channel(self, channel_id):
        return None


class FakeBot:
    def __init__(self, root: Path) -> None:
        self.storage = StorageManager(root)
        self.guilds = [FakeGuild(SERVER, "Revoked"), FakeGuild(OTHER, "Fine")]
        self.remote_config = RemoteConfig(None)
        self.remote_config.set_denylist([
            hash_identity("discord_server", str(SERVER)),
            hash_identity("gw2_guild", GW2_GUILD),
        ])

    def get_guild(self, guild_id):
        return next((g for g in self.guilds if g.id == guild_id), None)


@pytest.fixture(autouse=True)
def _allow_global_token(monkeypatch):
    monkeypatch.setenv("AXITOOLS_ALLOW_GLOBAL_TOKEN", "1")


@pytest_asyncio.fixture
async def client(aiohttp_client, tmp_path):
    return await aiohttp_client(build_app(FakeBot(tmp_path), token="test-token"))


AUTH = {"Authorization": "Bearer test-token"}


@pytest.mark.asyncio
async def test_revoked_server_gets_403_unavailable(client):
    resp = await client.get(f"/guilds/{SERVER}/config", headers=AUTH)
    assert resp.status == 403
    assert await resp.json() == {"error": "unavailable"}


@pytest.mark.asyncio
async def test_unauthenticated_request_to_a_revoked_server_stays_401(client):
    resp = await client.get(f"/guilds/{SERVER}/config")
    assert resp.status == 401


@pytest.mark.asyncio
async def test_other_servers_are_served(client):
    resp = await client.get(f"/guilds/{OTHER}/config", headers=AUTH)
    assert resp.status == 200


@pytest.mark.asyncio
async def test_guild_role_mapping_refuses_a_revoked_gw2_guild(client):
    resp = await client.put(f"/guilds/{OTHER}/guild-roles/{GW2_GUILD}", headers=AUTH, json={"role_id": "1"})
    assert resp.status == 403
    assert await resp.json() == {"error": "unavailable"}


@pytest.mark.asyncio
async def test_alliance_refuses_a_revoked_gw2_guild(client):
    resp = await client.put(f"/guilds/{OTHER}/alliance", headers=AUTH, json={"guild_id": GW2_GUILD.upper()})
    assert resp.status == 403
    assert await resp.json() == {"error": "unavailable"}
```

Before running these tests, open `tests/test_api_server.py` and the `_resolve_discord_guild`, `_role_in_guild` and `_channel_in_guild` helpers in `server.py`. Check how a guild is resolved, whether via `bot.get_guild` or `bot.guilds`, and which attributes are read. Then adjust only the `FakeGuild`/`FakeBot` doubles above so those helpers work. Do not change the assertions. The two GW2-guild routes must return 403 before any role or channel lookup, so the doubles only matter for reaching the handler.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_access_policy_cogs.py tests/test_api_access_policy.py -q`
Expected: FAIL. The revoked account links, the revoked server gets 200, and the revoked GW2 guild is saved.

- [ ] **Step 4: Implement the cog checks**

In `axitools/cogs/account_self.py`, add `from ..remote_config import policy_for` to the imports. In `_validate_api_key`, directly after the `guild_ids = sorted(...)` statement and before `guild_details = await self._fetch_guild_details(...)`, insert:

```python
        policy = policy_for(self.bot)
        if policy is not None and policy.any_blocked(
            [("gw2_account", account_name)] + [("gw2_guild", gid) for gid in guild_ids]
        ):
            raise ValueError("Unavailable.")
```

In `axitools/cogs/guild_roles.py`, add `from ..remote_config import policy_for` to the imports. Add this method to `GuildRolesCog`, next to `set_guild_role`:

```python
    async def _refuse_revoked_guild(self, interaction: discord.Interaction, gw2_guild_id: str, title: str) -> bool:
        """Answer "Unavailable." and return True when the GW2 guild is revoked."""
        policy = policy_for(self.bot)
        if policy is None or not policy.is_blocked("gw2_guild", gw2_guild_id):
            return False
        await self._send_embed(interaction, title=title, description="Unavailable.", colour=BRAND_COLOUR)
        return True
```

In `set_guild_role`, directly after the `if not cleaned_guild_id: ... return` block, insert:

```python
        if await self._refuse_revoked_guild(interaction, cleaned_guild_id, "Guild role mapping"):
            return
```

In `set_alliance_guild`, at the same place, insert:

```python
        if await self._refuse_revoked_guild(interaction, cleaned_guild_id, "Alliance guild"):
            return
```

- [ ] **Step 5: Implement the API checks**

In `axitools/api/server.py`, add `from ..remote_config import policy_for` to the imports. Then add the middleware directly after `_auth_middleware`:

```python
_UNAVAILABLE = {"error": "unavailable"}


@web.middleware
async def _policy_middleware(request: web.Request, handler):
    """Refuse requests for Discord servers on the Axi access denylist.

    Runs after _auth_middleware, so an unauthenticated caller still gets 401
    and learns nothing about the list. API callers carry no Discord user
    identity, so only servers can be refused here.
    """
    policy = policy_for(request.app["bot"])
    if policy is not None:
        guild_ids = set()
        scope = request.get("bridge_scope")
        if scope:
            guild_ids.add(scope[0])
        if request.get("scoped_guild_id") is not None:
            guild_ids.add(request["scoped_guild_id"])
        if request.match_info.get("guild_id") is not None:
            guild_ids.add(request.match_info["guild_id"])
        if any(policy.is_blocked("discord_server", str(gid)) for gid in guild_ids):
            return web.json_response(_UNAVAILABLE, status=403)
    return await handler(request)
```

In `build_app`, change the middleware list:

```python
        middlewares=[_auth_middleware, _policy_middleware],
```

Before using `scope[0]`, read `get_bridge_key_scope` in `storage.py` and check that the scope's first element is the Discord guild id. If the scope is a mapping or dataclass instead of a tuple, use its guild-id field. Record the choice in your report.

In `_handle_guild_roles_put`, directly after the `if not gw2_guild_id: return ... 400` block, insert:

```python
    policy = policy_for(request.app["bot"])
    if policy is not None and policy.is_blocked("gw2_guild", gw2_guild_id):
        return web.json_response(_UNAVAILABLE, status=403)
```

In `_handle_alliance_put`, directly after the `updates["alliance_guild_id"] = cleaned` line inside the `if "guild_id" in body:` branch, insert:

```python
            policy = policy_for(request.app["bot"])
            if policy is not None and policy.is_blocked("gw2_guild", cleaned):
                return web.json_response(_UNAVAILABLE, status=403)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_access_policy_cogs.py tests/test_api_access_policy.py tests/test_api_server.py tests/test_api_bridge_keys.py tests/test_api_app_keys.py tests/test_cogs_account_self.py tests/test_cogs_guild_roles.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full suite**

Run: `PYTHONPATH=. pytest tests -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add axitools/cogs/account_self.py axitools/cogs/guild_roles.py axitools/api/server.py tests/test_access_policy_cogs.py tests/test_api_access_policy.py
git commit -m "feat(access): refuse revoked accounts, guilds and servers in linking and the API

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Owner-only `/access` command

**Files:**
- Modify: `axitools/cogs/access_admin.py` (replaces the Task 2 stub)
- Modify: `.env.example`
- Test: `tests/test_cogs_access_admin.py`

**Interfaces:**
- Consumes from Task 1: `DEFAULT_BASE_URL`.
- Produces:
  - `AdminApi(base_url, token)` with `ban(kind, value, reason, created_by)`, `unban(ban_id)` and `list_bans()`.
  - `AdminApiError(code)`.
  - `AccessAdminCog(bot, api, owner_id)`.
  - `async setup(bot)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cogs_access_admin.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_cogs_access_admin.py -q`
Expected: FAIL with `ImportError: cannot import name 'AccessAdminCog'`.

- [ ] **Step 3: Implement `axitools/cogs/access_admin.py`**

```python
"""Owner-only /access commands: revoke and restore access to the Axi apps.

Access can be revoked for people, GW2 guilds and Discord servers that violate
the Axi apps' terms of use (see README, "Access"). These commands call the
axi-config admin API. They are registered only in the author's private admin
server (AXI_ADMIN_GUILD_ID), answer only AXI_OWNER_ID, and reply ephemerally,
because the list holds personal identifiers and reasons. The cog is not loaded
unless AXI_ADMIN_GUILD_ID, AXI_OWNER_ID and AXI_CONFIG_ADMIN_TOKEN are all set.
"""
from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from ..remote_config import DEFAULT_BASE_URL

LOGGER = logging.getLogger(__name__)

MESSAGE_LIMIT = 2000
REQUEST_TIMEOUT_SECONDS = 15

KIND_CHOICES = [
    app_commands.Choice(name="GW2 account (Name.1234)", value="gw2_account"),
    app_commands.Choice(name="GW2 guild (UUID)", value="gw2_guild"),
    app_commands.Choice(name="Discord user (ID)", value="discord_user"),
    app_commands.Choice(name="Discord server (ID)", value="discord_server"),
    app_commands.Choice(name="GitHub user (numeric ID)", value="github_user"),
]


class AdminApiError(Exception):
    """The admin API answered with an error code."""


class AdminApi:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token

    async def _request(self, method: str, path: str, body: dict | None = None) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method,
                self.base_url + path,
                json=body,
                headers={"Authorization": f"Bearer {self._token}"},
            ) as resp:
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
                if resp.status >= 400:
                    code = data.get("error") if isinstance(data, dict) else None
                    raise AdminApiError(code if isinstance(code, str) else f"HTTP {resp.status}")
                return data if isinstance(data, dict) else {}

    async def ban(self, kind: str, value: str, reason: str | None, created_by: str) -> dict[str, Any]:
        return await self._request(
            "POST", "/v1/admin/bans", {"kind": kind, "value": value, "reason": reason, "createdBy": created_by}
        )

    async def unban(self, ban_id: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/v1/admin/bans/{quote(ban_id, safe='')}")

    async def list_bans(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/admin/bans")


def _describe(ban: dict[str, Any]) -> str:
    reason = f" — {ban['reason']}" if ban.get("reason") else ""
    return f"`{ban.get('id')}` {ban.get('kind')} `{ban.get('value')}`{reason}"


class AccessAdminCog(commands.Cog):
    access = app_commands.Group(name="access", description="Revoke or restore access to the Axi apps.")

    def __init__(self, bot: commands.Bot, api: AdminApi, owner_id: int) -> None:
        self.bot = bot
        self.api = api
        self.owner_id = owner_id

    async def _owner_only(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Not allowed.", ephemeral=True)
        return False

    async def _call(self, interaction: discord.Interaction, action: str, coro) -> dict[str, Any] | None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            return await coro
        except AdminApiError as exc:
            await interaction.followup.send(f"{action} failed: {exc}", ephemeral=True)
        except (aiohttp.ClientError, TimeoutError, OSError):
            LOGGER.warning("axi-config admin API unreachable", exc_info=True)
            await interaction.followup.send(f"{action} failed: admin API unreachable.", ephemeral=True)
        return None

    async def _ban(self, interaction: discord.Interaction, kind: str, value: str, reason: str | None) -> None:
        result = await self._call(
            interaction, "Revoke", self.api.ban(kind, value, reason, str(interaction.user.id))
        )
        if result is None:
            return
        verb = "Revoked" if result.get("created") else "Already revoked"
        await interaction.followup.send(f"{verb}: {_describe(result.get('ban') or {})}", ephemeral=True)

    @access.command(name="revoke", description="Revoke access for an identifier.")
    @app_commands.describe(kind="What kind of identifier", value="The identifier", reason="Private note")
    @app_commands.choices(kind=KIND_CHOICES)
    async def revoke(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        value: str,
        reason: str | None = None,
    ) -> None:
        if await self._owner_only(interaction):
            await self._ban(interaction, kind.value, value, reason)

    @access.command(name="revoke-user", description="Revoke access for a Discord user.")
    @app_commands.describe(user="The Discord user", reason="Private note")
    async def revoke_user(
        self, interaction: discord.Interaction, user: discord.User, reason: str | None = None
    ) -> None:
        if await self._owner_only(interaction):
            await self._ban(interaction, "discord_user", str(user.id), reason)

    @access.command(name="restore", description="Restore access by ban id.")
    @app_commands.describe(ban_id="The ban id, e.g. b_abcdefghij")
    async def restore(self, interaction: discord.Interaction, ban_id: str) -> None:
        if not await self._owner_only(interaction):
            return
        result = await self._call(interaction, "Restore", self.api.unban(ban_id))
        if result is None:
            return
        verb = "Restored" if result.get("changed") else "Already restored"
        await interaction.followup.send(f"{verb}: {_describe(result.get('ban') or {})}", ephemeral=True)

    @access.command(name="list", description="List active bans.")
    async def list_cmd(self, interaction: discord.Interaction) -> None:
        if not await self._owner_only(interaction):
            return
        result = await self._call(interaction, "List", self.api.list_bans())
        if result is None:
            return
        bans = result.get("bans") or []
        if not bans:
            await interaction.followup.send("No active bans.", ephemeral=True)
            return
        lines: list[str] = []
        for index, ban in enumerate(bans):
            line = _describe(ban)
            remaining = len(bans) - index
            tail = f"\n… ({remaining} more)"
            if len("\n".join(lines + [line])) + len(tail) > MESSAGE_LIMIT:
                lines.append(tail.strip())
                break
            lines.append(line)
        await interaction.followup.send("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    guild_id = os.getenv("AXI_ADMIN_GUILD_ID", "").strip()
    owner_id = os.getenv("AXI_OWNER_ID", "").strip()
    token = os.getenv("AXI_CONFIG_ADMIN_TOKEN", "").strip()
    if not (guild_id.isdigit() and owner_id.isdigit() and token):
        LOGGER.info("access_admin not loaded: AXI_ADMIN_GUILD_ID, AXI_OWNER_ID and AXI_CONFIG_ADMIN_TOKEN are required")
        return
    base_url = os.getenv("AXI_CONFIG_URL", "").strip()
    if not base_url or base_url.lower() == "off":
        base_url = DEFAULT_BASE_URL
    await bot.add_cog(
        AccessAdminCog(bot, AdminApi(base_url, token), int(owner_id)),
        guild=discord.Object(id=int(guild_id)),
    )
```

The `… (N more)` tail must keep `len(text) <= 2000` and end with `more)`. The loop appends the tail only when the next line would push the message over the limit, and the tail counts the current line and every line after it.

- [ ] **Step 4: Document the settings**

Append to `.env.example`:

```
# Axi access policy (axitools/remote_config.py). Default https://config.axi.link; "off" disables it.
AXI_CONFIG_URL=
# Owner-only /access command (axitools/cogs/access_admin.py). Loaded only when all three are set.
AXI_ADMIN_GUILD_ID=
AXI_OWNER_ID=
AXI_CONFIG_ADMIN_TOKEN=
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_cogs_access_admin.py -q`, then `PYTHONPATH=. pytest tests -q`.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add axitools/cogs/access_admin.py tests/test_cogs_access_admin.py .env.example
git commit -m "feat(access): owner-only /access command in the admin server

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Operator follow-up (not part of implementation)

Restart the bot with the new env vars set to activate `/access` in the admin server. Until `config.axi.link` is live, the manifest fetch fails and nothing is refused.
