#!/usr/bin/env python3
"""Acquire DeskPet's pinned Chromium Headless Shell into a private cache."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from deskpet.playwright_bundle import (  # noqa: E402
    CONTRACT,
    PlaywrightBundleError,
    publish_archive,
    validate_archive,
    validate_browser_owner,
    validate_playwright_package,
)


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".partial", dir=target.parent)
    os.close(fd)
    temp_path = Path(temporary)
    try:
        shutil.copyfile(source, temp_path)
        validate_archive(temp_path)
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)


def _download(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "DeskPet-build/playwright-1.61.0"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(CONTRACT.archive_url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - locked HTTPS origin
        status = getattr(response, "status", 200)
        if offset and status != 206:
            partial.unlink(missing_ok=True)
            offset = 0
        mode = "ab" if offset and status == 206 else "wb"
        with partial.open(mode) as stream:
            shutil.copyfileobj(response, stream, 1024 * 1024)
    validate_archive(partial)
    os.replace(partial, target)


def acquire(cache_root: Path, archive_source: Path | None, offline: bool) -> dict[str, object]:
    validate_playwright_package()
    cache_root = cache_root.resolve()
    owner = cache_root / CONTRACT.owner_dir
    try:
        revision_root = validate_browser_owner(owner)
        reused = True
    except PlaywrightBundleError:
        archive = cache_root / "downloads" / CONTRACT.archive_name
        if archive.is_file():
            validate_archive(archive)
        elif archive_source is not None:
            _atomic_copy(archive_source.resolve(), archive)
        elif offline:
            raise PlaywrightBundleError(f"offline archive is missing: {archive}")
        else:
            _download(archive)
        revision_root = publish_archive(archive, owner)
        reused = False
    return {
        "status": "ok",
        "reused": reused,
        "playwright_version": CONTRACT.playwright_version,
        "revision": CONTRACT.revision,
        "browser_version": CONTRACT.browser_version,
        "owner": str(owner),
        "revision_root": str(revision_root),
        "archive_sha256": CONTRACT.archive_sha256,
        "executable_sha256": CONTRACT.executable_sha256,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        result = acquire(args.cache_root, args.archive, args.offline)
    except PlaywrightBundleError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else result["revision_root"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
