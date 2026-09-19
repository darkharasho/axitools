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


# --- failure modes caught by the review ---


def test_real_failure_mode_tokens_fit_before_substitution_overflow_after():
    """The core failure mode: short tokens become long emoji markup."""
    # Before substitution, this payload is well under limits.
    payload = {
        "embeds": [
            {
                "fields": [
                    {"name": "Damage", "value": "\n".join(f"{{{{spec:firebrand}}}} Player{i:02d}" for i in range(50))}
                ]
            }
        ]
    }
    # Substitution expands tokens.
    after_sub = substitute_payload(payload, REGISTRY)
    # enforce_limits must truncate the oversized field.
    limited = enforce_limits(after_sub)
    value = limited["embeds"][0]["fields"][0]["value"]
    assert len(value) <= FIELD_VALUE_LIMIT
    # Each row is ~39 chars, so ~26 rows fit in 1024 chars.
    # Just verify it's truncated and doesn't have all 50.
    num_rows = len(value.split("\n"))
    assert num_rows < 50
    assert num_rows >= 20  # Should keep a reasonable number


def test_title_description_only_embed_without_fields_is_budgeted():
    """CRITICAL 1: embeds without fields must have their title/description counted and enforced."""
    # An embed with only a large title should be either removed or truncated.
    # Since _truncate_rows works on newline-separated content, a single line
    # that exceeds the limit is dropped entirely (not split).
    large_title = "x" * 7000
    payload = {"embeds": [{"title": large_title}]}
    result = enforce_limits(payload)
    embed = result["embeds"][0]
    # Title should either be absent or <= EMBED_CHAR_LIMIT.
    if "title" in embed:
        assert len(embed["title"]) <= EMBED_CHAR_LIMIT


def test_large_description_without_fields_is_truncated():
    """CRITICAL 1: description in field-less embed must be enforced against the budget."""
    large_desc = "y" * 7000
    payload = {"embeds": [{"description": large_desc}]}
    result = enforce_limits(payload)
    embed = result["embeds"][0]
    # Description should either be absent or <= EMBED_CHAR_LIMIT.
    if "description" in embed:
        assert len(embed["description"]) <= EMBED_CHAR_LIMIT


def test_footer_text_counted_in_embed_budget():
    """CRITICAL 2: footer.text must be included in the 6000-char budget."""
    # A field that fits when footer is NOT counted, but overflows when it IS.
    # Footer is 6000 chars, leaving 0 for the field.
    footer_text = "x" * 6000
    field_value = "y" * 100
    payload = {
        "embeds": [
            {
                "footer": {"text": footer_text},
                "fields": [{"name": "Field", "value": field_value}]
            }
        ]
    }
    result = enforce_limits(payload)
    # Field should be dropped because footer+field > 6000.
    assert len(result["embeds"][0].get("fields", [])) == 0


def test_footer_text_counted_with_title_and_description():
    """CRITICAL 2: footer.text is part of the per-embed budget alongside title/description."""
    # A scenario where title + description + footer + field must all fit in 6000.
    title = "a" * 1000
    description = "b" * 1000
    footer = "c" * 1000
    field_value = "d" * 3500
    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "footer": {"text": footer},
                "fields": [{"name": "Field", "value": field_value}]
            }
        ]
    }
    result = enforce_limits(payload)
    # All text must sum to <= 6000.
    total = (
        len(result["embeds"][0].get("title") or "") +
        len(result["embeds"][0].get("description") or "") +
        len(result["embeds"][0].get("footer", {}).get("text") or "") +
        sum(len(f["name"]) + len(f["value"]) for f in result["embeds"][0].get("fields", []))
    )
    assert total <= EMBED_CHAR_LIMIT


def test_content_with_emoji_is_not_split_mid_markup():
    """IMPORTANT 3: content must use _truncate_rows to avoid splitting emoji markup."""
    # Build content that has emoji markup repeated enough to exceed CONTENT_LIMIT.
    emoji_row = REGISTRY["firebrand"] + " Player\n"
    content = emoji_row * 100  # Will exceed 2000 chars.
    payload = {"content": content}
    result = enforce_limits(payload)
    # Should be truncated but not mid-emoji.
    truncated = result["content"]
    assert len(truncated) <= 2000
    assert "<:firebrand:111111111111111" not in truncated.replace(REGISTRY["firebrand"], "")


def test_25_field_cap_with_small_fields_under_char_budget():
    """IMPORTANT 5: 25-field cap must be enforced even when char budget is not exceeded."""
    # Many small fields that individually fit but exceed 25 count.
    fields = [{"name": f"F{i}", "value": f"v{i}"} for i in range(30)]
    payload = {"embeds": [{"fields": fields}]}
    result = enforce_limits(payload)
    # Should keep at most 25 fields.
    assert len(result["embeds"][0].get("fields", [])) <= 25
    # Verify char budget is NOT the limiting factor here.
    total_chars = sum(len(f["name"]) + len(f["value"]) for f in result["embeds"][0]["fields"])
    assert total_chars < EMBED_CHAR_LIMIT


# --- Round-2 fixes: CRITICAL 1 and 2 regressions ---


def test_title_and_description_combined_must_fit_within_embed_limit():
    """CRITICAL 1 (round 2): title + description combined must fit within EMBED_CHAR_LIMIT.

    This tests the exact case the reviewer identified: no fields, no footer,
    title = "a"*4000 + description = "b"*4000. Neither exceeds 6000 individually,
    but together they are 8000. The fix must ensure the combined total stays <= 6000.
    """
    payload = {
        "embeds": [
            {
                "title": "a" * 4000,
                "description": "b" * 4000,
            }
        ]
    }
    result = enforce_limits(payload)
    embed = result["embeds"][0]
    total = len(embed.get("title") or "") + len(embed.get("description") or "")
    assert total <= EMBED_CHAR_LIMIT


def test_large_footer_without_title_description_fields_is_truncated():
    """CRITICAL 2 (round 2): footer.text must be truncated and the embed rewritten.

    This tests the exact case the reviewer identified: no fields, no title,
    no description, footer.text = "x"*7000. The footer exceeds EMBED_CHAR_LIMIT
    and must be either dropped or its text truncated.
    """
    payload = {
        "embeds": [
            {
                "footer": {"text": "x" * 7000},
            }
        ]
    }
    result = enforce_limits(payload)
    embed = result["embeds"][0]
    # Footer must either be absent or have its text length <= EMBED_CHAR_LIMIT.
    if "footer" in embed and embed["footer"].get("text"):
        assert len(embed["footer"]["text"]) <= EMBED_CHAR_LIMIT
    # If footer is present, assert its text is definitely truncated.
    if "footer" in embed:
        assert len(embed["footer"].get("text", "")) <= EMBED_CHAR_LIMIT


def test_embed_char_limit_is_a_whole_message_budget_not_per_embed():
    """Discord's 6000-char cap is the combined sum across every embed.

    Budgeting it per-embed let a multi-embed report through at a multiple of
    the real limit, which Discord answers with a 400 -- so the whole report
    fails rather than losing its tail.
    """
    field = {"name": "Damage", "value": _rows(20)}
    payload = {
        "embeds": [
            {"fields": [dict(field) for _ in range(25)]},
            {"fields": [dict(field) for _ in range(25)]},
        ]
    }
    embeds = enforce_limits(payload)["embeds"]
    total = sum(
        len(e.get("title", "")) + len(e.get("description", ""))
        + len((e.get("footer") or {}).get("text", ""))
        + sum(len(f["name"]) + len(f["value"]) for f in e.get("fields", []))
        for e in embeds
    )
    assert total <= EMBED_CHAR_LIMIT


def test_author_name_is_charged_against_the_shared_budget():
    """The header repeats per embed, so it eats the same 6000 the body does."""
    payload = {
        "embeds": [
            {"author": {"name": "AxiBridge"}, "description": "d" * 5995},
            {"author": {"name": "AxiBridge"}, "description": "second"},
        ]
    }
    out = enforce_limits(payload)
    total = sum(
        len(e.get("author", {}).get("name", "")) + len(e.get("description", ""))
        for e in out["embeds"]
    )
    assert total <= 6000


def test_author_is_dropped_when_no_budget_remains():
    payload = {
        "embeds": [
            {"description": "d" * 6000},
            {"author": {"name": "AxiBridge"}, "description": "second"},
        ]
    }
    out = enforce_limits(payload)
    assert "author" not in out["embeds"][1]
