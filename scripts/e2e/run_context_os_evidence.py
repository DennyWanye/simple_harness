#!/usr/bin/env python3
"""Context OS evidence entrypoint.

Join mode delegates to :mod:`context_os_evidence`.  ``--manifest`` mode runs
the immutable automation manifest with a separate userdata/session/control
reset envelope for every selected case and writes an evidence manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from context_os_evidence import main as evidence_main

ROOT = Path(__file__).resolve().parents[2]
AUTOMATION_RUNNER = Path(__file__).with_name("context_os_run_automated.py")


def _request(url: str, path: str, body: dict[str, Any] | None = None) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.load(response)


def _reset_controls(env: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    daemon = env.get("DESKPET_CONTEXT_OS_E2E_DAEMON_URL")
    provider = env.get("DESKPET_CONTEXT_OS_E2E_PROVIDER_URL", "").removesuffix(
        "/v1"
    )
    if daemon:
        try:
            _request(daemon, "/reset", {})
            result["daemon"] = _request(daemon, "/health")
        except (OSError, urllib.error.URLError, ValueError) as exc:
            result["daemon"] = {"unavailable": type(exc).__name__}
    else:
        result["daemon"] = {"status": "not_configured"}
    if provider:
        try:
            state = _request(provider, "/__control/state")
            dirty = bool(
                state.get("faults")
                or state.get("fallback")
                or state.get("force_finish")
                or state.get("paused")
                or state.get("scenario_count")
            )
            if dirty:
                raise RuntimeError("provider control state is not clean")
            result["provider"] = state
        except (OSError, urllib.error.URLError, ValueError) as exc:
            result["provider"] = {"unavailable": type(exc).__name__}
    else:
        result["provider"] = {"status": "not_configured"}
    return result


def _parse_automation_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--id", action="append")
    parser.add_argument("--subcommand", action="append")
    parser.add_argument("--merge-existing", action="store_true")
    return parser.parse_args(argv)


def run_automation_isolated(argv: list[str]) -> int:
    args = _parse_automation_args(argv)
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = [
        case
        for case in manifest["cases"]
        if not args.id or case["id"] in args.id
    ]
    args.out.mkdir(parents=True, exist_ok=True)
    envelopes: list[dict[str, Any]] = []
    exit_code = 0

    for case in cases:
        case_id = str(case["id"])
        case_root = args.out / ".evidence" / case_id
        userdata = case_root / "userdata"
        userdata.mkdir(parents=True, exist_ok=True)
        session_id = f"context-os-{case_id.lower()}"
        env = {
            **os.environ,
            "DESKPET_USER_DATA_DIR": str(userdata.resolve()),
            "DESKPET_CONTEXT_OS_E2E_CASE_ID": case_id,
            "DESKPET_CONTEXT_OS_E2E_SESSION_ID": session_id,
        }
        controls = _reset_controls(env)
        case_manifest = case_root / "automation-case.json"
        case_manifest.write_text(
            json.dumps(
                {"schema_version": manifest.get("schema_version", 1), "cases": [case]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        command = [
            sys.executable,
            str(AUTOMATION_RUNNER),
            "--manifest",
            str(case_manifest),
            "--out",
            str(args.out),
        ]
        for subcommand in args.subcommand or ():
            command.extend(("--subcommand", subcommand))
        if args.merge_existing:
            command.append("--merge-existing")
        completed = subprocess.run(command, cwd=ROOT, env=env, check=False)
        exit_code = max(exit_code, completed.returncode)
        result_path = (args.out / f"{case_id}.json").resolve()
        envelopes.append(
            {
                "case_id": case_id,
                "userdata": str(userdata.resolve()),
                "session_id": session_id,
                "control_reset": controls,
                "case_manifest": str(case_manifest.resolve()),
                "result": str(result_path),
                "result_exists": result_path.is_file(),
                "exit_code": completed.returncode,
            }
        )

    results = []
    for path in sorted(args.out.glob("AT-CTX-*.json")):
        try:
            results.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    summary = {
        "selected": len(results),
        "failed": sum(not item.get("passed", False) for item in results),
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    raw_manifest = manifest_path.read_bytes()
    evidence_manifest = {
        "schema_version": 1,
        "source_manifest": str(manifest_path),
        "source_manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases": envelopes,
    }
    (args.out / "evidence-manifest.json").write_text(
        json.dumps(evidence_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 1 if exit_code or summary["failed"] else 0


if __name__ == "__main__":
    if "--manifest" in sys.argv[1:]:
        raise SystemExit(run_automation_isolated(sys.argv[1:]))
    evidence_main()
