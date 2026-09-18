"""Sync ``media/gw2-class-icons`` into this application's Discord emoji.

Run manually — NOT at bot startup. Application emoji are global to the
application, so a startup uploader races every restart and every deployed
replica, and one bad asset would break boot instead of failing one command:

    python -m axitools.scripts.sync_emoji            # dry run
    python -m axitools.scripts.sync_emoji --apply

Discord consequence of re-running after the icon source changes: ``plan_sync``
diffs by emoji *presence* only (``to_upload`` = local keys absent remotely).
Discord has no emoji-update endpoint, so re-running this sync after swapping
in new art for an already-uploaded key uploads nothing for that key and does
NOT replace the art of the emoji already uploaded — it keeps the old art until
it is deleted and re-created. Only genuinely new keys (not previously
uploaded) get created with the new art. Deleting and re-creating existing
emoji to force an art refresh is a deliberate, human-approved action, not
something this script does automatically.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import logging
import os
from pathlib import Path
from typing import Dict, List, Tuple

import aiohttp
from PIL import Image

from ..constants import EMOJI_ICON_PATH
from ..emoji_registry import emoji_key_for_asset

LOGGER = logging.getLogger(__name__)

EMOJI_SIZE = 128          # what Discord serves emoji at anyway
EMOJI_MAX_BYTES = 256_000  # Discord's cap is 256 KB
API_BASE = "https://discord.com/api/v10"


def normalize_icon(path: Path) -> bytes:
    """Return upload-ready PNG bytes: square, ``EMOJI_SIZE``², under the cap.

    Pads the shorter axis with transparency instead of stretching — the shipped
    icons include non-square art (``Bladesworn.png`` is 150×116) and stretching
    makes it look subtly wrong.
    """
    with Image.open(path) as source:
        image = source.convert("RGBA")
        side = max(image.size)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
        canvas = canvas.resize((EMOJI_SIZE, EMOJI_SIZE), Image.LANCZOS)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    data = buffer.getvalue()
    if len(data) > EMOJI_MAX_BYTES:
        raise ValueError(f"{path.name} is {len(data)}B after normalization")
    return data


def load_local_icons(directory: Path = EMOJI_ICON_PATH) -> Dict[str, bytes]:
    """Normalized PNG bytes for every icon, keyed by registry key.

    Defaults to ``EMOJI_ICON_PATH``, the package-sourced set vendored from
    ``gw2-class-icons`` — not ``CLASS_ICON_PATH``, which is the legacy set
    used by ``/builds`` and ``/comps`` thumbnails.
    """
    return {
        emoji_key_for_asset(path.name): normalize_icon(path)
        for path in sorted(directory.glob("*.png"))
    }


def plan_sync(
    local: Dict[str, bytes], remote: Dict[str, str]
) -> Tuple[List[str], List[str]]:
    """Return ``(to_upload, to_delete)`` keys.

    Discord has no emoji-update endpoint, so a changed icon is a delete plus a
    create and therefore a new id. Callers delete before uploading. A name-only
    diff detects presence; a changed icon must be deleted manually before it
    will re-upload.
    """
    to_upload = sorted(key for key in local if key not in remote)
    to_delete = sorted(key for key in remote if key not in local)
    return to_upload, to_delete


def build_registry(emojis: List[dict]) -> Dict[str, str]:
    """Map registry key -> ``<:name:id>`` markup."""
    return {
        str(emoji["name"]): f"<:{emoji['name']}:{emoji['id']}>" for emoji in emojis
    }


async def fetch_remote(session: aiohttp.ClientSession, app_id: str) -> Dict[str, str]:
    async with session.get(f"{API_BASE}/applications/{app_id}/emojis") as response:
        response.raise_for_status()
        body = await response.json()
    return {str(e["name"]): str(e["id"]) for e in body.get("items", body)}


async def main_async(apply: bool) -> int:
    token = os.environ["DISCORD_TOKEN"]
    app_id = os.environ["DISCORD_APPLICATION_ID"]
    local = load_local_icons()
    headers = {"Authorization": f"Bot {token}"}

    async with aiohttp.ClientSession(headers=headers) as session:
        remote = await fetch_remote(session, app_id)
        to_upload, to_delete = plan_sync(local, remote)
        print(f"{len(local)} local, {len(remote)} remote")
        print(f"upload: {to_upload or 'none'}")
        print(f"delete: {to_delete or 'none'}")
        if not apply:
            print("dry run — pass --apply to write")
            return 0

        for key in to_delete:
            async with session.delete(
                f"{API_BASE}/applications/{app_id}/emojis/{remote[key]}"
            ) as response:
                response.raise_for_status()
            print(f"deleted {key}")

        for key in to_upload:
            encoded = base64.b64encode(local[key]).decode("ascii")
            async with session.post(
                f"{API_BASE}/applications/{app_id}/emojis",
                json={"name": key, "image": f"data:image/png;base64,{encoded}"},
            ) as response:
                response.raise_for_status()
            print(f"uploaded {key}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform the writes")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    return asyncio.run(main_async(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
