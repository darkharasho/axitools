"""Guards for the package-sourced emoji icon set (``media/gw2-class-icons``).

These run against the real vendored directory, not a fixture — the point is
to catch a bad vendor/refresh (a filename that produces a dead key, a missing
spec, or art that blows Discord's emoji size cap) before it ships.
"""
import io
import re

import pytest
from PIL import Image

from axitools.constants import EMOJI_ICON_PATH
from axitools.emoji_registry import emoji_key_for_asset
from axitools.scripts.sync_emoji import EMOJI_MAX_BYTES, EMOJI_SIZE, load_local_icons, normalize_icon

TOKEN_GRAMMAR = re.compile(r"^[a-z0-9]+$")


def _vendored_pngs():
    return sorted(EMOJI_ICON_PATH.glob("*.png"))


def test_vendored_directory_has_46_files():
    assert len(_vendored_pngs()) == 46


@pytest.mark.parametrize("path", _vendored_pngs(), ids=lambda p: p.name)
def test_every_vendored_key_matches_token_grammar(path):
    """A key with punctuation/underscore is a key the client can never emit.

    ``getProfessionEmojiToken`` (AxiBridge-side) derives keys as
    ``.toLowerCase().replace(/[^a-z0-9]/g, '')`` — i.e. only ``[a-z0-9]``
    survives. If a vendored filename's derived key doesn't match that
    grammar, the emoji it produces is permanently unreachable.
    """
    key = emoji_key_for_asset(path.name)
    assert TOKEN_GRAMMAR.match(key), f"{path.name} -> {key!r} is not reachable"


def test_renegade_and_willbender_have_icons():
    """Regression guard: these two elite specs were missing before this change."""
    names = {path.stem for path in _vendored_pngs()}
    assert "Renegade" in names
    assert "Willbender" in names


def test_load_local_icons_default_reads_package_source():
    """Must fail if the default reverts to CLASS_ICON_PATH (43 files, no renegade)."""
    result = load_local_icons()
    assert len(result) == 46
    assert "renegade" in result
    assert "willbender" in result


@pytest.mark.parametrize("path", _vendored_pngs(), ids=lambda p: p.name)
def test_every_normalized_vendored_icon_fits_discord_limits(path):
    """A tight-cropped, non-square source must not blow the emoji size cap."""
    data = normalize_icon(path)
    assert len(data) <= EMOJI_MAX_BYTES
    image = Image.open(io.BytesIO(data))
    assert image.size == (EMOJI_SIZE, EMOJI_SIZE)
