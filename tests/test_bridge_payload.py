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
    "author": {
        "name": "AxiBridge",
        "icon_url": "https://raw.githubusercontent.com/darkharasho/axibridge/main/public/img/AxiBridge-glyph.png",
    },
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
    assert embed["author"] == REAL_CLIENT_EMBED["author"]
    assert len(embed["fields"]) == len(REAL_CLIENT_EMBED["fields"])


def test_strips_username_and_avatar():
    """A bot cannot set these, and accepting them would imply it can."""
    out = validate_report({**VALID, "username": "AxiBridge", "avatar_url": "http://x"})
    assert "username" not in out
    assert "avatar_url" not in out


@pytest.mark.parametrize(
    "payload,reason",
    [
        # `image` is now an allowed key, but only with an `attachment://`
        # url -- this row still rejects the remote-URL form, which is the
        # property it was written to protect.
        ({"embeds": [{"image": {"url": "http://x"}}]}, "image"),
        ({"embeds": [{"author": {"url": "http://x"}}]}, "author"),
        (
            {"embeds": [{"author": {"name": "x", "icon_url": "http://x/y.png"}}]},
            "icon_url",
        ),
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


def _image_report(image):
    return {"embeds": [{"title": "t", "image": image}]}


def test_attachment_image_is_allowed():
    validate_report(_image_report({"url": "attachment://slice.png"}))


def test_attachment_image_survives_validation():
    # Validating without copying the key through would silently drop the
    # slice: the relay would accept the report and post it with no image.
    out = validate_report(_image_report({"url": "attachment://slice.png"}))
    assert out["embeds"][0]["image"] == {"url": "attachment://slice.png"}


def test_remote_image_url_is_rejected():
    # An unrestricted image.url would let any paired client make the bot
    # render an arbitrary remote image.
    with pytest.raises(ValueError):
        validate_report(_image_report({"url": "https://evil.example/x.png"}))


def test_image_without_url_is_rejected():
    with pytest.raises(ValueError):
        validate_report(_image_report({}))


def test_unknown_image_key_is_rejected():
    with pytest.raises(ValueError):
        validate_report(
            _image_report({"url": "attachment://slice.png", "proxy_url": "https://x/y"})
        )


def test_author_without_an_icon_is_allowed():
    """The header does not require the glyph -- name alone is a valid author."""
    out = validate_report({"embeds": [{"author": {"name": "AxiBridge"}}]})
    assert out["embeds"][0]["author"] == {"name": "AxiBridge"}


def test_author_icon_must_come_from_the_axibridge_repo():
    """The bot fetches this URL, so a paired client cannot aim it anywhere."""
    with pytest.raises(ValueError, match="icon_url"):
        validate_report(
            {
                "embeds": [
                    {
                        "author": {
                            "name": "AxiBridge",
                            "icon_url": "https://evil.example/tracker.png",
                        }
                    }
                ]
            }
        )


def test_author_name_is_bounded():
    with pytest.raises(ValueError, match="author.name"):
        validate_report({"embeds": [{"author": {"name": "x" * 257}}]})
