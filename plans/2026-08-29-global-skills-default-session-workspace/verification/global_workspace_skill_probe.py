#!/usr/bin/env python3
"""Validate the frozen global-Skill fixture and emit a reproducible probe receipt.

This probe prepares immutable input evidence only.  UI/provider execution is
recorded by the manual current-build scenarios and is never inferred here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    archive_url = str(fixture["archive_url"])
    expected_hash = str(fixture["archive_sha256"])
    expected_bytes = int(fixture["archive_bytes"])
    with urllib.request.urlopen(archive_url, timeout=30) as response:  # noqa: S310
        archive = response.read(expected_bytes + 1)
    actual_hash = hashlib.sha256(archive).hexdigest()
    if len(archive) != expected_bytes or actual_hash != expected_hash:
        raise SystemExit("frozen fixture archive identity mismatch")

    receipt = {
        "schema": "global-workspace-skill-fixture-probe-v1",
        "fixture": str(args.fixture),
        "archive_sha256": actual_hash,
        "archive_bytes": len(archive),
        "selected_subdirectory": fixture["selected_subdirectory"],
        "expected_skill_name": fixture["expected_skill_name"],
        "runtime_execution_proven": False,
        "runtime_evidence_authority": "current-build-manual-scenarios",
    }
    args.evidence_root.mkdir(parents=True, exist_ok=True)
    output = args.evidence_root / "fixture-probe.json"
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**receipt, "receipt_path": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
