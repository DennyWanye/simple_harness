#!/usr/bin/env python3
"""Validate the recorded black-box full-surface smoke result.

This checker never drives or simulates the product. The real UI/runtime runner must
write the result JSON and evidence paths after executing every declared surface.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REQUIRED = {
    "chat-batch", "chat-single", "chat-natural", "settings-url",
    "runtime-page-in", "first-party", "capability-pack", "permission",
    "project-scope", "ordinary-shell", "startup",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.result.read_text(encoding="utf-8"))
    surfaces = payload.get("surfaces")
    if not isinstance(surfaces, dict):
        print("FAIL: surfaces must be an object")
        return 1
    errors: list[str] = []
    for surface in sorted(REQUIRED):
        record = surfaces.get(surface)
        if not isinstance(record, dict):
            errors.append(f"{surface}: missing")
            continue
        if record.get("status") != "PASS":
            errors.append(f"{surface}: status={record.get('status')!r}")
        evidence = record.get("evidence")
        if not isinstance(evidence, str) or not evidence.startswith(".local-test-evidence/"):
            errors.append(f"{surface}: evidence must be under .local-test-evidence/")
        if not isinstance(record.get("facts"), dict) or not record["facts"]:
            errors.append(f"{surface}: non-empty facts required")
    if errors:
        print("FULL_SURFACE_SMOKE: FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"FULL_SURFACE_SMOKE: PASS ({len(REQUIRED)} surfaces)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

