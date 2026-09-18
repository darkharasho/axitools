import pytest

from axitools.api.bridge_payload import validate_report

# Captured from the client's real renderer, not hand-written: this is the
# exact key shape `DiscordNotifier` produces for a bridge-destination complex
# embed (axibridge/src/main/discord.ts, `baseEmbed` ~:1107-1116 plus the
# `embedFields`/`buildEmbeds` builders around it). The hand-written fixture
# this replaced omitted `url` and `timestamp` -- exactly the two keys the
# client actually sends on every embed -- which is how eleven task reviews
# passed over a total outage (relay whitelist review, C1). If the client's
# embed shape changes, RE-CAPTURE this fixture from `DiscordNotifier` (or from
# `baseEmbed`/the field builders directly); do not hand-edit it to make a test
# pass -- that is the exact defect this fixture exists to prevent.
REAL_CLIENT_EMBED = {
    "title": "[EU] Stonemist Keep",
    "url": "https://dps.report/AbCd-20260917-120300_wvw",
    "description": "**Duration:** 5m 12s\n**Outcome:** Victory",
    "color": 0xFFFFFF,
    "timestamp": "2026-09-17T12:03:00.000Z",
    "footer": {"text": "AxiBridge • 8:03:00 AM"},
    "fields": [
        {"name": "Squad Summary:", "value": "```\nCount:     35\nDMG:   1,200,000\n```", "inline": True},
        {"name": "Damage", "value": "{{spec:firebrand}} Alice 1.2M", "inline": True},
    ],
}

VALID = {
    "content": "**Stonemist Keep** — 12:03",
    "embeds": [
        {
            "title": "Squad Summary",
            "description": "35 players",
            "color": 3447003,
            "footer": {"text": "AxiBridge"},
            "fields": [
                {"name": "Damage", "value": "{{spec:firebrand}} Alice 1.2M", "inline": True}
            ],
        }
    ],
}


def test_accepts_a_real_report():
    out = validate_report(VALID)
    assert out["embeds"][0]["fields"][0]["name"] == "Damage"
    assert out["content"] == VALID["content"]


def test_accepts_the_captured_client_embed():
    """Pins the relay whitelist to the client's actual output, not to prose.

    This is the test that would have caught C1: it fails immediately if
    `url` (or any other key the real client sends) is dropped from
    ALLOWED_EMBED_KEYS.
    """
    out = validate_report({"embeds": [REAL_CLIENT_EMBED]})
    embed = out["embeds"][0]
    assert embed["title"] == REAL_CLIENT_EMBED["title"]
    assert embed["url"] == REAL_CLIENT_EMBED["url"]
    assert embed["description"] == REAL_CLIENT_EMBED["description"]
    assert embed["color"] == REAL_CLIENT_EMBED["color"]
    assert embed["timestamp"] == REAL_CLIENT_EMBED["timestamp"]
    assert embed["footer"] == REAL_CLIENT_EMBED["footer"]
    assert len(embed["fields"]) == len(REAL_CLIENT_EMBED["fields"])


def test_strips_username_and_avatar():
    """A bot cannot set these, and accepting them would imply it can."""
    out = validate_report({**VALID, "username": "AxiBridge", "avatar_url": "http://x"})
    assert "username" not in out
    assert "avatar_url" not in out


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"embeds": [{"image": {"url": "http://x"}}]}, "image"),
        ({"embeds": [{"author": {"name": "x"}}]}, "author"),
        ({"embeds": [{"fields": [{"name": "a", "value": "b", "url": "http://x"}]}]}, "url"),
        ({"allowed_mentions": {"parse": ["everyone"]}, "embeds": []}, "allowed_mentions"),
        ({"mentions": ["123"], "embeds": []}, "mentions"),
        ({"components": [], "embeds": []}, "components"),
        ({"embeds": [{"color": "blurple"}]}, "color"),
        ({"embeds": "nope"}, "embeds"),
        ({"embeds": [{"fields": {"a": 1}}]}, "fields"),
    ],
)
def test_rejects_unknown_or_malformed_keys(payload, reason):
    with pytest.raises(ValueError) as excinfo:
        validate_report(payload)
    assert reason in str(excinfo.value)


def test_rejects_too_many_embeds():
    with pytest.raises(ValueError, match="embeds"):
        validate_report({"embeds": [{"description": "x"} for _ in range(11)]})


def test_rejects_too_many_fields():
    embed = {"fields": [{"name": "a", "value": "b"} for _ in range(26)]}
    with pytest.raises(ValueError, match="fields"):
        validate_report({"embeds": [embed]})


def test_rejects_empty_message():
    with pytest.raises(ValueError, match="empty"):
        validate_report({"embeds": []})


def test_content_only_message_is_valid():
    assert validate_report({"content": "hello"})["content"] == "hello"


def test_rejects_at_everyone_in_content():
    with pytest.raises(ValueError, match="mention"):
        validate_report({"content": "@everyone look"})
