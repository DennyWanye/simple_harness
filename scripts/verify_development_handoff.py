#!/usr/bin/env python3
"""Verify the frozen private development handoff, without starting runtime or tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(root: Path, manifest: Path) -> int:
    document = json.loads(manifest.read_text(encoding="utf-8"))
    seen: set[str] = set()
    for item in document["files"]:
        relative = item["path"]
        if relative in seen:
            raise SystemExit(f"duplicate manifest path: {relative}")
        seen.add(relative)
        path = root / relative
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            raise SystemExit(f"missing or invalid source path: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != item["sha256"]:
            raise SystemExit(f"source differs from handoff checkpoint: {relative}")
    return len(seen)


def main() -> None:
    sdk = ROOT / "sdk/simple-harness-sdk"
    overlay = ROOT / "development/taskgraph-host-overlay"
    counts = {
        "sdk": verify(sdk, sdk / "HANDOFF-SOURCE-MANIFEST.json"),
        "taskgraph_host_overlay": verify(overlay, overlay / "SOURCE-MANIFEST.json"),
        "handoff_assets": verify(ROOT, ROOT / "plans/Assurance/HANDOFF-ASSETS-2026-09-23.json"),
    }
    print(json.dumps({"status": "PASS", "scope": "source integrity only; not feature acceptance", "files": counts}))


if __name__ == "__main__":
    main()
