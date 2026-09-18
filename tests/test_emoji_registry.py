import pytest

from axitools.emoji_registry import (
    EMBED_CHAR_LIMIT,
    FIELD_VALUE_LIMIT,
    emoji_key_for_asset,
    enforce_limits,
    substitute,
    substitute_payload,
)

REGISTRY = {
    "firebrand": "<:firebrand:111111111111111111>",
    "revenant": "<:revenant:222222222222222222>",
}


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("Firebrand.png", "firebrand"),
        ("Revenant_icon.png", "revenant"),
        ("Guardian.png", "guardian"),
        ("Bladesworn.png", "bladesworn"),
    ],
)
def test_emoji_key_for_asset(filename, expected):
    assert emoji_key_for_asset(filename) == expected


def test_substitute_known_token():
    assert substitute("{{spec:firebrand}} Alice", REGISTRY) == (
        "<:firebrand:111111111111111111> Alice"
    )


def test_substitute_unknown_token_degrades_to_name():
    assert substitute("{{spec:harbinger}} Bob", REGISTRY) == "Harbinger Bob"


def test_substitute_leaves_other_text_alone():
    assert substitute("no tokens {here}", REGISTRY) == "no tokens {here}"


def test_substitute_payload_covers_content_and_fields():
    payload = {
        "content": "{{spec:firebrand}}",
        "embeds": [
            {
                "title": "{{spec:revenant}} fight",
                "description": "{{spec:firebrand}}",
                "fields": [
                    {"name": "{{spec:firebrand}}", "value": "{{spec:revenant}} x", "inline": True}
                ],
                "footer": {"text": "{{spec:firebrand}}"},
            }
        ],
    }
    out = substitute_payload(payload, REGISTRY)
    assert out["content"] == REGISTRY["firebrand"]
    embed = out["embeds"][0]
    assert embed["title"] == f"{REGISTRY['revenant']} fight"
    assert embed["description"] == REGISTRY["firebrand"]
    assert embed["fields"][0]["name"] == REGISTRY["firebrand"]
    assert embed["fields"][0]["value"] == f"{REGISTRY['revenant']} x"
    assert embed["footer"]["text"] == REGISTRY["firebrand"]


def test_substitute_payload_does_not_mutate_input():
    payload = {"embeds": [{"description": "{{spec:firebrand}}"}]}
    substitute_payload(payload, REGISTRY)
    assert payload["embeds"][0]["description"] == "{{spec:firebrand}}"


# --- limit enforcement -----------------------------------------------------
# These are the cases the whole design exists to prevent: a field that fits
# BEFORE substitution and overflows AFTER.

def _rows(n: int) -> str:
    # Each row is 30 chars of emoji markup + a short name, like a real report.
    return "\n".join(f"{REGISTRY['firebrand']} Player{i:02d}" for i in range(n))


def test_field_value_under_limit_is_untouched():
    value = _rows(5)
    payload = {"embeds": [{"fields": [{"name": "Damage", "value": value}]}]}
    assert enforce_limits(payload)["embeds"][0]["fields"][0]["value"] == value


def test_oversized_field_value_is_truncated_to_whole_rows():
    value = _rows(40)  # ~40 × 40 chars ≫ 1024
    assert len(value) > FIELD_VALUE_LIMIT
    payload = {"embeds": [{"fields": [{"name": "Damage", "value": value}]}]}
    out = enforce_limits(payload)["embeds"][0]["fields"][0]["value"]
    assert len(out) <= FIELD_VALUE_LIMIT
    # Never split a row, and never split an emoji reference.
    original_rows = value.split("\n")
    assert out.split("\n") == original_rows[: len(out.split("\n"))]
    assert "<:firebrand:111111111111111111" not in out.replace(
        REGISTRY["firebrand"], ""
    )


def test_single_row_longer_than_limit_is_dropped_not_split():
    payload = {"embeds": [{"fields": [{"name": "X", "value": "a" * 2000}]}]}
    fields = enforce_limits(payload)["embeds"][0].get("fields", [])
    assert fields == []


def test_embed_char_limit_drops_trailing_fields():
    field = {"name": "Damage", "value": _rows(20)}
    payload = {"embeds": [{"fields": [dict(field) for _ in range(30)]}]}
    embed = enforce_limits(payload)["embeds"][0]
    total = sum(len(f["name"]) + len(f["value"]) for f in embed["fields"])
    assert total <= EMBED_CHAR_LIMIT
    assert len(embed["fields"]) <= 25


def test_too_many_embeds_are_dropped():
    payload = {"embeds": [{"description": "x"} for _ in range(14)]}
    assert len(enforce_limits(payload)["embeds"]) == 10
