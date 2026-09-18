"""Re-vendor ``media/gw2-class-icons`` from the ``gw2-class-icons`` npm package.

The npm package is the authority for these icons; the files under
``media/gw2-class-icons`` are a committed vendored copy so the bot never needs
``npm``/``node`` at runtime or at sync time. This script re-downloads the pinned
version's tarball and rewrites that vendored copy so it stays reproducible
instead of hand-copied.

Run this manually, from a developer's machine, when bumping ``PACKAGE_VERSION``.
It must NEVER be invoked at bot startup or from any request path — it makes a
network call to the npm registry.

Discord consequence: Discord has no emoji-update endpoint. Application emoji
already uploaded by ``sync_emoji.py`` keep their existing art forever unless
manually deleted and re-created — re-running this script (and then
``sync_emoji``) will NOT retroactively change any emoji a Discord user has
already seen. See ``sync_emoji.py``'s module docstring for the full picture.

Usage:

    python -m axitools.scripts.refresh_class_icons            # dry run
    python -m axitools.scripts.refresh_class_icons --write
"""
from __future__ import annotations

import argparse
import hashlib
import io
import tarfile
import urllib.request
from pathlib import Path
from typing import Dict

from ..constants import EMOJI_ICON_PATH

PACKAGE_VERSION = "0.3.0"
PACKAGE_NAME = "gw2-class-icons"
TARBALL_URL = (
    f"https://registry.npmjs.org/{PACKAGE_NAME}/-/{PACKAGE_NAME}-{PACKAGE_VERSION}.tgz"
)
# Member paths inside the tarball live under this prefix (npm always wraps
# tarball contents in a top-level "package/" directory).
MEMBER_PREFIX = "package/wiki/150px/"
USER_AGENT = "axitools-refresh-class-icons/1.0 (+https://github.com/darkharasho/axitools)"


def _fetch_tarball(url: str = TARBALL_URL) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request) as response:
        return response.read()


def _safe_relative_name(member_name: str) -> str | None:
    """Return the plain filename for a wanted tar member, or ``None``.

    Guards against path traversal: rejects any member whose name, once
    stripped of the known prefix, is not a single path-separator-free
    filename. This is untrusted archive content by policy even though the
    source (the npm registry, over TLS, for a package we own) is trusted.
    """
    if not member_name.startswith(MEMBER_PREFIX):
        return None
    rest = member_name[len(MEMBER_PREFIX) :]
    if not rest or not rest.endswith(".png"):
        return None
    # Reject anything that isn't a bare filename: no separators, no "..",
    # nothing that would escape the target directory once joined.
    candidate = Path(rest)
    if candidate.name != rest or ".." in candidate.parts:
        return None
    return rest


def extract_icons(tarball_bytes: bytes) -> Dict[str, bytes]:
    """Return ``{filename: png_bytes}`` for every wanted, safe tar member."""
    icons: Dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(tarball_bytes), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = _safe_relative_name(member.name)
            if name is None:
                continue
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            icons[name] = extracted.read()
    return icons


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plan_refresh(
    fetched: Dict[str, bytes], target_dir: Path
) -> Dict[str, list]:
    """Classify *fetched* files against what's on disk in *target_dir*."""
    added, updated, unchanged = [], [], []
    for name, data in sorted(fetched.items()):
        existing_path = target_dir / name
        if not existing_path.exists():
            added.append(name)
        elif _sha256(existing_path.read_bytes()) != _sha256(data):
            updated.append(name)
        else:
            unchanged.append(name)
    return {"added": added, "updated": updated, "unchanged": unchanged}


def refresh(target_dir: Path = EMOJI_ICON_PATH, *, write: bool) -> Dict[str, list]:
    tarball_bytes = _fetch_tarball()
    fetched = extract_icons(tarball_bytes)
    if not fetched:
        raise RuntimeError(
            f"no PNGs found under {MEMBER_PREFIX!r} in the fetched tarball"
        )

    plan = plan_refresh(fetched, target_dir)

    if write:
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in plan["added"] + plan["updated"]:
            (target_dir / name).write_bytes(fetched[name])

    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing files (default)",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="actually write the vendored files",
    )
    args = parser.parse_args()

    write = args.write and not args.dry_run
    plan = refresh(write=write)

    print(f"added: {len(plan['added'])} {plan['added'] or ''}")
    print(f"updated: {len(plan['updated'])} {plan['updated'] or ''}")
    print(f"unchanged: {len(plan['unchanged'])}")
    total = len(plan["added"]) + len(plan["updated"]) + len(plan["unchanged"])
    print(f"total files: {total}")
    if not write:
        print("dry run — pass --write to write files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
