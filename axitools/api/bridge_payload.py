"""Whitelist validation for relayed AxiBridge report payloads.

The relay is public, so it must never forward client-supplied JSON to Discord
verbatim — that is an open "post anything as this bot" proxy. Only the keys a
fight report actually uses are accepted; everything else is a hard error.
"""
from __future__ import annotations

from typing import Any, Dict

ALLOWED_TOP_KEYS = frozenset({"content", "embeds"})
# Every key the client's baseEmbed/embedFields builders can emit
# (axibridge/src/main/discord.ts, ~:1107-1116 and the field builders). ``url``
# is on every complex embed the client has ever sent (it linkifies the title
# to the dps.report permalink) -- a bare URL cannot inject content, so it is
# no more dangerous than the fields already allowed here. Keep this set in
# sync with the client, not with the spec prose: see
# tests/test_bridge_payload.py's captured fixture.
ALLOWED_EMBED_KEYS = frozenset(
    {
        "title",
        "description",
        "color",
        "url",
        "footer",
        "fields",
        "timestamp",
        "image",
        "author",
    }
)
ALLOWED_FIELD_KEYS = frozenset({"name", "value", "inline"})
ALLOWED_FOOTER_KEYS = frozenset({"text"})
ALLOWED_IMAGE_KEYS = frozenset({"url"})
ALLOWED_AUTHOR_KEYS = frozenset({"name", "icon_url"})

# The author icon is a remote URL, so it gets the same treatment as embed
# images: pinned to content this project controls rather than left open. The
# client's glyph lives in the axibridge repo, served raw from GitHub.
AUTHOR_ICON_PREFIX = "https://raw.githubusercontent.com/darkharasho/axibridge/"

MAX_EMBEDS = 10
MAX_FIELDS = 25
MAX_CONTENT = 2000
MAX_TEXT = 6000  # per string; enforce_limits does the per-embed accounting
MAX_AUTHOR_NAME = 256  # Discord's own cap on embed.author.name

# Dropped silently rather than rejected: a bot cannot set them, and AxiBridge's
# webhook path legitimately includes them.
IGNORED_TOP_KEYS = frozenset({"username", "avatar_url"})


def _require_str(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if len(value) > MAX_TEXT:
        raise ValueError(f"{label} is too long")
    return value


def _validate_footer(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("footer must be an object")
    unknown = set(raw) - ALLOWED_FOOTER_KEYS
    if unknown:
        raise ValueError(f"footer key not allowed: {sorted(unknown)[0]}")
    return {"text": _require_str(raw.get("text", ""), "footer.text")}


def _validate_image(raw: Any) -> Dict[str, Any]:
    """Embed images must reference a file uploaded in the same request.

    Only the ``attachment://`` scheme is accepted. Allowing arbitrary URLs
    would let any paired client make the bot render remote images, so the
    value is bound to a part the request already carries -- which the existing
    attachment count and byte limits already bound.
    """
    if not isinstance(raw, dict):
        raise ValueError("embed image must be an object")
    unknown = set(raw) - ALLOWED_IMAGE_KEYS
    if unknown:
        raise ValueError(f"embed image key not allowed: {sorted(unknown)[0]}")
    url = raw.get("url")
    if not isinstance(url, str) or not url.startswith("attachment://"):
        raise ValueError("embed image url must use the attachment:// scheme")
    return {"url": url}


def _validate_author(raw: Any) -> Dict[str, Any]:
    """The embed header: who posted this.

    ``name`` is free text the client fills with its own product name, so it
    is bounded like any other string. ``icon_url`` is a remote fetch the bot
    would perform on a paired client's say-so, so it is restricted to the
    axibridge repo -- the same reasoning that limits ``image`` to
    ``attachment://``, applied to the one remote host this key needs.
    """
    if not isinstance(raw, dict):
        raise ValueError("embed author must be an object")
    unknown = set(raw) - ALLOWED_AUTHOR_KEYS
    if unknown:
        raise ValueError(f"embed author key not allowed: {sorted(unknown)[0]}")

    name = _require_str(raw.get("name", ""), "author.name")
    if len(name) > MAX_AUTHOR_NAME:
        raise ValueError("author.name is too long")
    author: Dict[str, Any] = {"name": name}

    if "icon_url" in raw:
        icon_url = raw["icon_url"]
        if not isinstance(icon_url, str) or not icon_url.startswith(AUTHOR_ICON_PREFIX):
            raise ValueError(
                f"author.icon_url must start with {AUTHOR_ICON_PREFIX}"
            )
        author["icon_url"] = icon_url
    return author


def _validate_field(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("each field must be an object")
    unknown = set(raw) - ALLOWED_FIELD_KEYS
    if unknown:
        raise ValueError(f"field key not allowed: {sorted(unknown)[0]}")
    field = {
        "name": _require_str(raw.get("name", ""), "field.name"),
        "value": _require_str(raw.get("value", ""), "field.value"),
    }
    if "inline" in raw:
        if not isinstance(raw["inline"], bool):
            raise ValueError("field.inline must be a boolean")
        field["inline"] = raw["inline"]
    return field


def _validate_embed(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("each embed must be an object")
    unknown = set(raw) - ALLOWED_EMBED_KEYS
    if unknown:
        raise ValueError(f"embed key not allowed: {sorted(unknown)[0]}")

    embed: Dict[str, Any] = {}
    for key in ("title", "description", "timestamp", "url"):
        if key in raw:
            embed[key] = _require_str(raw[key], f"embed.{key}")
    if "color" in raw:
        if not isinstance(raw["color"], int) or isinstance(raw["color"], bool):
            raise ValueError("embed.color must be an integer")
        embed["color"] = raw["color"]
    if "footer" in raw:
        embed["footer"] = _validate_footer(raw["footer"])
    if "image" in raw:
        embed["image"] = _validate_image(raw["image"])
    if "author" in raw:
        embed["author"] = _validate_author(raw["author"])
    if "fields" in raw:
        if not isinstance(raw["fields"], list):
            raise ValueError("embed.fields must be a list")
        if len(raw["fields"]) > MAX_FIELDS:
            raise ValueError(f"too many fields (max {MAX_FIELDS})")
        embed["fields"] = [_validate_field(f) for f in raw["fields"]]
    return embed


def validate_report(body: Any) -> Dict[str, Any]:
    """Return a whitelisted copy of *body*, or raise ``ValueError``."""
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")

    unknown = set(body) - ALLOWED_TOP_KEYS - IGNORED_TOP_KEYS
    if unknown:
        raise ValueError(f"key not allowed: {sorted(unknown)[0]}")

    payload: Dict[str, Any] = {}
    if "content" in body:
        content = _require_str(body["content"], "content")
        if len(content) > MAX_CONTENT:
            raise ValueError("content is too long")
        if "@everyone" in content or "@here" in content:
            raise ValueError("content may not mention everyone or here")
        payload["content"] = content

    if "embeds" in body:
        if not isinstance(body["embeds"], list):
            raise ValueError("embeds must be a list")
        if len(body["embeds"]) > MAX_EMBEDS:
            raise ValueError(f"too many embeds (max {MAX_EMBEDS})")
        payload["embeds"] = [_validate_embed(e) for e in body["embeds"]]

    if not payload.get("content") and not payload.get("embeds"):
        raise ValueError("report is empty")
    return payload
