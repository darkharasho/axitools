"""Application-emoji token substitution for relayed AxiBridge reports.

AxiBridge emits semantic tokens (``{{spec:firebrand}}``) rather than emoji
markup, because a changed icon is a delete-plus-create in Discord's API and so
gets a NEW id — an id cached in a desktop app would rot. The bot substitutes at
send time from its own registry.

Substitution GROWS the text: ``{{spec:firebrand}}`` is 18 characters,
``<:firebrand:1234567890123456789>`` is ~32. So Discord's limits have to be
enforced here, after substitution, not by the client beforehand.
"""
from __future__ import annotations

import re
from typing import Dict

TOKEN_RE = re.compile(r"\{\{spec:([a-z0-9]+)\}\}")

FIELD_VALUE_LIMIT = 1024
EMBED_CHAR_LIMIT = 6000
FIELDS_PER_EMBED = 25
MAX_EMBEDS = 10
CONTENT_LIMIT = 2000


def emoji_key_for_asset(filename: str) -> str:
    """Registry key for an icon file in ``media/gw2classicons``.

    Assets are bare spec/profession names (``Firebrand.png``) with one
    exception, ``Revenant_icon.png``, so a trailing ``_icon`` is stripped.

    This ``_icon`` carve-out is a legacy-set quirk. The package-sourced set
    (``media/gw2-class-icons``, used for Discord application emoji) ships
    ``Revenant.png`` with no such suffix, so the strip is a no-op there; it
    is kept because it is still correct for the legacy set.
    """
    stem = filename.rsplit(".", 1)[0]
    if stem.endswith("_icon"):
        stem = stem[: -len("_icon")]
    return stem.lower()


def substitute(text: str, registry: Dict[str, str]) -> str:
    """Replace ``{{spec:x}}`` with its emoji markup.

    An unknown key degrades to the capitalized name so a report from a newer
    AxiBridge naming a spec this bot has not synced still posts readably.
    """
    if not text:
        return text

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        return registry.get(key) or key.capitalize()

    return TOKEN_RE.sub(_replace, text)


def substitute_payload(payload: dict, registry: Dict[str, str]) -> dict:
    """Return a copy of *payload* with every text field substituted."""
    out = dict(payload)
    if "content" in out:
        out["content"] = substitute(out["content"], registry)

    embeds = []
    for embed in out.get("embeds") or []:
        new_embed = dict(embed)
        for key in ("title", "description"):
            if key in new_embed:
                new_embed[key] = substitute(new_embed[key], registry)
        if isinstance(new_embed.get("footer"), dict) and "text" in new_embed["footer"]:
            footer = dict(new_embed["footer"])
            footer["text"] = substitute(footer["text"], registry)
            new_embed["footer"] = footer
        if new_embed.get("fields"):
            new_embed["fields"] = [
                {
                    **field,
                    "name": substitute(field.get("name", ""), registry),
                    "value": substitute(field.get("value", ""), registry),
                }
                for field in new_embed["fields"]
            ]
        embeds.append(new_embed)
    if embeds or "embeds" in out:
        out["embeds"] = embeds
    return out


def _truncate_rows(value: str, limit: int) -> str:
    """Drop trailing newline-separated rows until *value* fits.

    Rows are never split, so an ``<:name:id>`` reference can never be cut in
    half — a half-reference renders as literal text and looks broken.
    """
    if len(value) <= limit:
        return value
    rows = value.split("\n")
    while rows:
        rows.pop()
        candidate = "\n".join(rows)
        if len(candidate) <= limit:
            return candidate
    return ""


def enforce_limits(payload: dict) -> dict:
    """Return a copy of *payload* trimmed to Discord's documented maxima."""
    out = dict(payload)
    if out.get("content"):
        out["content"] = _truncate_rows(out["content"], CONTENT_LIMIT)

    embeds = []
    for embed in (out.get("embeds") or [])[:MAX_EMBEDS]:
        new_embed = dict(embed)
        remaining = EMBED_CHAR_LIMIT

        # Process title and description with running budget.
        for key in ("title", "description"):
            text = new_embed.get(key)
            if text:
                if len(text) > remaining:
                    text = _truncate_rows(text, remaining)
                if text:
                    new_embed[key] = text
                    remaining -= len(text)
                else:
                    new_embed.pop(key, None)

        # Process footer with running budget.
        footer = new_embed.get("footer")
        if footer and footer.get("text"):
            text = footer["text"]
            if len(text) > remaining:
                text = _truncate_rows(text, remaining)
            if text:
                new_embed["footer"] = {**footer, "text": text}
                remaining -= len(text)
            else:
                new_embed.pop("footer", None)

        # Process fields within remaining budget.
        if new_embed.get("fields"):
            kept = []
            for field in new_embed["fields"][:FIELDS_PER_EMBED]:
                value = _truncate_rows(field.get("value", ""), FIELD_VALUE_LIMIT)
                if not value:
                    continue
                cost = len(field.get("name", "")) + len(value)
                if cost > remaining:
                    break
                remaining -= cost
                kept.append({**field, "value": value})
            new_embed["fields"] = kept
        embeds.append(new_embed)
    if embeds or "embeds" in out:
        out["embeds"] = embeds
    return out
