#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Minimal compute-only DeskPet capability worker."""

from __future__ import annotations

import json
import sys
from typing import Any


def compute(request: dict[str, Any]) -> dict[str, Any]:
    inputs = request.get("args", {}).get("input_snapshot", {}).get("inputs", [])
    actions = [
        {
            "kind": "rename_file",
            "source_ref": f"input:{index}",
            "target_name": f"renamed_{index:03d}{item.get('metadata', {}).get('suffix', '')}",
        }
        for index, item in enumerate(inputs)
        if item.get("kind") in {"file", "artifact"}
    ]
    return {
        "ok": True,
        "request_id": request["request_id"],
        "value": {"planned_actions": len(actions)},
        "effect_plan": {"actions": actions},
        "artifacts": [],
        "observations": [],
    }


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if request.get("protocol") != "deskpet-json-tool-v1":
            raise ValueError("unsupported protocol")
        response = compute(request)
    except Exception as exc:  # worker boundary: return one structured failure
        response = {
            "ok": False,
            "request_id": locals().get("request", {}).get("request_id", "unknown"),
            "error": {"code": "worker_error", "message": str(exc)},
        }
    json.dump(response, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
