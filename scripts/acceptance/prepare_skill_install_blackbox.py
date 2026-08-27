#!/usr/bin/env python3
"""Prepare isolated paths and validate immutable remote fixture declarations."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse


def valid_source(item: dict[str, object]) -> bool:
    url = str(item.get("url", ""))
    commit = str(item.get("commit", ""))
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == "github.com" and bool(re.fullmatch(r"[0-9a-f]{40}", commit))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sources = json.loads(args.fixtures.read_text(encoding="utf-8")).get("sources", [])
    if not isinstance(sources, list) or not sources or any(not isinstance(x, dict) or not valid_source(x) for x in sources):
        print("FAIL: every fixture needs HTTPS github.com URL and exact 40-char commit")
        return 1
    root = args.evidence_root.resolve()
    if ".local-test-evidence" not in root.parts:
        print("FAIL: evidence root must be under .local-test-evidence")
        return 1
    for name in ("project-a", "project-b", "projectless", "logs", "screenshots", "manifests"):
        (root / name).mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "1.0",
        "evidence_root": str(root),
        "fixtures_file_sha256": hashlib.sha256(args.fixtures.read_bytes()).hexdigest(),
        "sources": sources,
        "precondition": "managed catalogs and Project source trees recorded before execution"
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "manifest": str(args.output), "sources": len(sources)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

