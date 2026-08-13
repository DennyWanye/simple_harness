#!/usr/bin/env python3
"""Run the fail-closed Harness crash/recovery acceptance gate.

This gate deliberately stays smaller than the full repository suite.  It
guards the durable boundaries that must hold before adding more Harness
capabilities: authorization, effect handoff, child reconciliation, terminal
delivery, reconnect replay, and architectural dependency direction.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TAURI_DIR = ROOT / "tauri-app"


@dataclass(frozen=True, slots=True)
class GateLane:
    name: str
    command: tuple[str, ...]
    cwd: Path


BACKEND_SELECTORS = (
    "backend/tests/harness_simplification/test_fault_matrix.py",
    "backend/tests/harness_simplification/test_harness_bootstrap.py::"
    "test_event_worker_retries_transient_lane_failure_without_new_event",
    "backend/tests/harness_simplification/test_harness_bootstrap.py::"
    "test_event_worker_retries_unhandled_reconcile_failure",
    "backend/tests/harness_simplification/test_wi5_react_driver.py::"
    "test_permission_batch_waits_for_durable_grant_before_execution",
    "backend/tests/harness_simplification/test_wi5_react_driver.py::"
    "test_recovery_backfills_successful_effect_without_regenerating_or_reexecuting",
    "backend/tests/harness_simplification/test_wi5_react_driver.py::"
    "test_child_terminal_persisted_before_resume_recovers_into_model_once",
    "backend/tests/harness_simplification/test_wi8_child_run_coordinator.py::"
    "test_ack_crash_reuses_same_child_and_operation_without_duplicate_rows",
    "backend/tests/harness_simplification/test_execution_projector.py::"
    "test_terminal_session_message_is_visible_once_after_crash_restart",
    "backend/tests/harness_simplification/test_run_kernel.py::"
    "test_restart_after_close_bound_keeps_missing_late_evidence_unknown",
    "backend/tests/harness_simplification/test_execution_contracts.py::"
    "test_execution_package_has_no_product_layer_dependency",
)

FRONTEND_SELECTORS = (
    "src/hooks/usePermissionRequests.test.tsx",
    "src/code-panel/runEventReducer.test.ts",
)


def _run(lane: GateLane) -> int:
    print(f"\n=== {lane.name} ===", flush=True)
    environment = os.environ.copy()
    backend_path = str(ROOT / "backend")
    environment["PYTHONPATH"] = os.pathsep.join(
        item
        for item in (backend_path, environment.get("PYTHONPATH", ""))
        if item
    )
    completed = subprocess.run(
        lane.command,
        cwd=lane.cwd,
        env=environment,
        check=False,
    )
    return int(completed.returncode)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backend-only",
        action="store_true",
        help="Skip reconnect/UI replay checks explicitly.",
    )
    args = parser.parse_args()

    lanes = [
        GateLane(
            "durable crash and recovery",
            (sys.executable, "-m", "pytest", "-q", *BACKEND_SELECTORS),
            ROOT,
        )
    ]
    if not args.backend_only:
        node = shutil.which("node")
        vitest = TAURI_DIR / "node_modules" / "vitest" / "vitest.mjs"
        if node is None or not vitest.is_file():
            missing = "node" if node is None else str(vitest)
            print(f"HARNESS_RELIABILITY: FAIL (missing {missing})")
            return 2
        lanes.append(
            GateLane(
                "reconnect and replay",
                (node, str(vitest), "run", *FRONTEND_SELECTORS),
                TAURI_DIR,
            )
        )

    failed = [lane.name for lane in lanes if _run(lane) != 0]
    if failed:
        print(f"\nHARNESS_RELIABILITY: FAIL ({', '.join(failed)})")
        return 1
    print("\nHARNESS_RELIABILITY: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
