#!/usr/bin/env python3
"""Offline dual-database Host-control downgrade gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from deskpet.product_state.downgrade import (  # noqa: E402
    HostControlDowngradeError,
    VerifierRunDisposition,
    execute_host_control_downgrade,
    pinned_sdk_062_reopen_probe,
)


def _load_dispositions(path: Path):  # type: ignore[no-untyped-def]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "host-control-run-dispositions-v1":
        raise ValueError("run disposition file has an unsupported schema")
    raw = payload.get("runs")
    if not isinstance(raw, list):
        raise ValueError("run disposition file requires a runs array")
    records: dict[str, VerifierRunDisposition] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("run disposition entries must be objects")
        record = VerifierRunDisposition(
            run_id=str(item["run_id"]),
            session_id=str(item["session_id"]),
            request_id=str(item["request_id"]),
            turn_id=str(item["turn_id"]),
            state=str(item["state"]),
            terminal=item.get("terminal") is True,
            recoverable=item.get("recoverable") is True,
        )
        if record.run_id in records:
            raise ValueError(f"duplicate Run disposition: {record.run_id}")
        records[record.run_id] = record
    return lambda attempt: records.get(attempt.expected_run_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-database", type=Path, required=True)
    parser.add_argument("--execution-database", type=Path, required=True)
    parser.add_argument("--product-pre-v3-backup", type=Path, required=True)
    parser.add_argument("--run-dispositions", type=Path, required=True)
    parser.add_argument("--sdk-062-python", type=Path, required=True)
    parser.add_argument("--confirm-ingress-closed", action="store_true")
    parser.add_argument("--confirm-app-stopped", action="store_true")
    parser.add_argument("--allow-execution-quarantine", action="store_true")
    arguments = parser.parse_args()
    try:
        receipt = execute_host_control_downgrade(
            product_database=arguments.product_database,
            execution_database=arguments.execution_database,
            product_pre_v3_backup=arguments.product_pre_v3_backup,
            ingress_closed=arguments.confirm_ingress_closed,
            app_stopped=arguments.confirm_app_stopped,
            run_probe=_load_dispositions(arguments.run_dispositions),
            sdk_062_probe=pinned_sdk_062_reopen_probe(arguments.sdk_062_python),
            allow_execution_quarantine=arguments.allow_execution_quarantine,
        )
    except (HostControlDowngradeError, ValueError, OSError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps({
        "status": "ok",
        "outcome": receipt.outcome,
        "attempt_count": receipt.attempt_count,
        "execution_backup": str(receipt.execution_backup),
        "execution_quarantine": (
            None if receipt.execution_quarantine is None else str(receipt.execution_quarantine)
        ),
        "product_recovery_artifact": str(receipt.product_recovery_artifact),
        "sdk_probe_detail": receipt.sdk_probe_detail,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
