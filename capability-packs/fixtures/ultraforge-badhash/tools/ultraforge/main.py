"""Deliberately untrusted acceptance fixture.

This file must never run: ``deskpet-pack.json`` declares a different SHA-256.
The canary makes an accidental execution visible without touching product data.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


canary = os.environ.get("DESKPET_ULTRAFORGE_CANARY", "").strip()
if canary:
    Path(canary).write_text("UNTRUSTED_FIXTURE_EXECUTED\n", encoding="utf-8")

request = json.loads(sys.stdin.readline() or "{}")
print(
    json.dumps(
        {
            "ok": True,
            "operation": request.get("operation"),
            "fixture": "ultraforge-badhash",
        }
    ),
    flush=True,
)
