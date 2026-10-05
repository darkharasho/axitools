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
import discord

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
    "\t\n\v\f\r   "
    "           "
    "    　﻿"
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
    # JS TextEncoder turns a lone surrogate into U+FFFD; strict UTF-8 would raise.
    text = f"{kind}:{normalized}".encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


async def refuse_if_revoked(client: Any, interaction: discord.Interaction) -> bool:
    """Answer "Unavailable." and return True when the user or server is revoked.

    Shared by the command tree and by persistent views, which bypass the tree.
    An autocomplete interaction cannot receive a message, so it is refused
    silently. Returns False (nothing sent) when the interaction may proceed.
    """
    policy = policy_for(client)
    if policy is None:
        return False
    identities = [("discord_user", str(interaction.user.id))]
    if interaction.guild_id is not None:
        identities.append(("discord_server", str(interaction.guild_id)))
    if not policy.any_blocked(identities):
        return False
    if interaction.type is discord.InteractionType.autocomplete:
        return True
    try:
        await interaction.response.send_message("Unavailable.", ephemeral=True)
    except discord.HTTPException:
        LOGGER.debug("Could not answer a refused interaction", exc_info=True)
    return True


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
    "refuse_if_revoked",
    "try_hash_identity",
]
