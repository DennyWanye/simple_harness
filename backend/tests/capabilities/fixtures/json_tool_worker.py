"""Protocol fixture used by local-runtime tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


request = json.load(sys.stdin)
mode = sys.argv[1]

if mode == "hang-child":
    subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(30)
elif mode == "crash":
    raise SystemExit(7)
elif mode == "malformed":
    sys.stdout.write("not-json")
elif mode == "multiple":
    sys.stdout.write("{}{}")
elif mode == "oversized":
    sys.stdout.write(json.dumps({"blob": "x" * 100_000}))
elif mode == "wrong-request":
    json.dump({"ok": True, "request_id": "wrong", "value": {}}, sys.stdout)
elif mode == "host-spoof":
    json.dump(
        {
            "ok": True,
            "request_id": request["request_id"],
            "value": {},
            "receipt_ref": "worker-forged",
        },
        sys.stdout,
    )
elif mode == "failure":
    json.dump(
        {
            "ok": False,
            "request_id": request["request_id"],
            "error": {"code": "fixture_failure", "message": "expected"},
        },
        sys.stdout,
    )
elif mode == "brokered":
    inputs = request["args"]["input_snapshot"]["inputs"]
    assert all("canonical_resource" not in item for item in inputs)
    assert request["context"]["workspace_roots"] == []
    assert request["context"]["temp_dir"] == ""
    json.dump(
        {
            "ok": True,
            "request_id": request["request_id"],
            "value": {"count": len(inputs)},
            "effect_plan": {
                "actions": [
                    {
                        "kind": "rename_file",
                        "source_ref": "input:0",
                        "target_name": "renamed 文件.txt",
                    }
                ]
            },
            "artifacts": [],
            "observations": [],
        },
        sys.stdout,
    )
else:
    sys.stderr.write("bounded fixture log")
    json.dump(
        {
            "ok": True,
            "request_id": request["request_id"],
            "value": {"echo": request["args"]},
            "artifacts": [],
            "observations": [],
        },
        sys.stdout,
    )
