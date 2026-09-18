import pytest

from axitools.api.bridge_payload import validate_report

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
