import io
from pathlib import Path

import pytest
from PIL import Image

from axitools.constants import CLASS_ICON_PATH
from axitools.scripts.sync_emoji import (
    EMOJI_MAX_BYTES,
    EMOJI_SIZE,
    build_registry,
    load_local_icons,
    normalize_icon,
    plan_sync,
)


@pytest.mark.parametrize(
    "asset",
    ["Bladesworn.png", "Amalgam.png", "Firebrand.png", "Revenant_icon.png"],
)
def test_normalize_icon_real_assets(asset):
    """The shipped assets are 256–750px, some non-square, some over 256KB."""
    data = normalize_icon(CLASS_ICON_PATH / asset)
    assert len(data) <= EMOJI_MAX_BYTES
    image = Image.open(io.BytesIO(data))
    assert image.size == (EMOJI_SIZE, EMOJI_SIZE)
    assert image.format == "PNG"


def test_normalize_pads_rather_than_stretches(tmp_path):
    """A wide source keeps its aspect ratio; the remainder is transparent."""
    source = tmp_path / "wide.png"
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(source)
    image = Image.open(io.BytesIO(normalize_icon(source)))
    assert image.size == (EMOJI_SIZE, EMOJI_SIZE)
    # Middle row is opaque red, top row is transparent padding.
    assert image.getpixel((EMOJI_SIZE // 2, EMOJI_SIZE // 2))[3] == 255
    assert image.getpixel((EMOJI_SIZE // 2, 0))[3] == 0


def test_load_local_icons_keys_by_registry_key():
    icons = load_local_icons(CLASS_ICON_PATH)
    assert "firebrand" in icons
    assert "revenant" in icons  # from Revenant_icon.png
    assert "revenant_icon" not in icons
    assert all(len(v) <= EMOJI_MAX_BYTES for v in icons.values())


def test_plan_sync_uploads_missing():
    upload, delete = plan_sync({"firebrand": b"x"}, {})
    assert upload == ["firebrand"]
    assert delete == []


def test_plan_sync_leaves_matching_alone():
    upload, delete = plan_sync({"firebrand": b"x"}, {"firebrand": "111"})
    assert upload == []
    assert delete == []


def test_plan_sync_deletes_orphans():
    upload, delete = plan_sync({}, {"scrapper": "222"})
    assert upload == []
    assert delete == ["scrapper"]


def test_build_registry_formats_markup():
    registry = build_registry(
        [{"name": "firebrand", "id": "111111111111111111"}]
    )
    assert registry == {"firebrand": "<:firebrand:111111111111111111>"}
