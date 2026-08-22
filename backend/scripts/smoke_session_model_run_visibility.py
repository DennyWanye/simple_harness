"""Re-runnable full-surface automation smoke for Session/Run visibility.

This smoke covers the deterministic backend boundaries and the public UI
consumer.  It does not replace the required Windows click/input matrix.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_MATRIX = (
    "backend/tests/test_session_model_run_visibility_smoke.py",
    (
        "backend/tests/test_p5s2_session_provider_resolution.py::"
        "test_pinned_to_deleted_provider_fails_closed"
    ),
    "backend/tests/test_agent_activity_projection.py",
    "backend/tests/test_public_tool_projection.py",
    (
        "backend/tests/test_workflow_fault_matrix.py::"
        "test_cancelled_run_rejects_late_result_instead_of_advancing"
    ),
    "backend/tests/test_execute_sdk_run.py",
    "backend/tests/sdk_adapters/test_provider_projection_pump.py",
    "backend/tests/sdk_adapters/test_official_memory_product_integration.py",
)
FRONTEND_MATRIX = (
    "src/stores/sessionsStore.test.ts",
    "src/code-panel/ws.chat.test.ts",
    "src/chat/HarnessInspectorPanel.test.tsx",
    "src/components/AgentActivityMessage.test.tsx",
    "src/components/workflow/WorkflowProgressGroup.test.tsx",
    "src/stores/harnessPublicSnapshotStore.test.ts",
    "src/hooks/usePermissionRequests.test.tsx",
    "src/components/PermissionPopup.test.tsx",
)


class VisibilitySmokeError(RuntimeError):
    pass


def _run(command: Sequence[str], *, cwd: Path) -> None:
    print(f"[run] cwd={cwd} command={' '.join(command)}", flush=True)
    completed = subprocess.run(command, cwd=cwd, check=False)
    if completed.returncode:
        raise VisibilitySmokeError(
            f"surface command failed with exit {completed.returncode}"
        )


def run_backend_matrix() -> None:
    _run(
        (sys.executable, "-m", "pytest", "-q", *BACKEND_MATRIX),
        cwd=REPO_ROOT,
    )


def run_frontend_matrix() -> None:
    frontend = REPO_ROOT / "tauri-app"
    if not (frontend / "node_modules").exists():
        raise VisibilitySmokeError(
            "tauri-app/node_modules is missing; run pnpm install --frozen-lockfile"
        )
    node = shutil.which("node.exe") or shutil.which("node")
    if node is None and os.name == "nt":
        bundled = (
            Path.home()
            / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
        )
        if bundled.exists():
            node = str(bundled)
    vitest = frontend / "node_modules/vitest/vitest.mjs"
    if node is None or not vitest.exists():
        raise VisibilitySmokeError("Node.js and local Vitest are required")
    _run((node, str(vitest), "run", *FRONTEND_MATRIX), cwd=frontend)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-only",
        action="store_true",
        help="run backend surfaces; explicitly omit frontend",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    run_backend_matrix()
    if not args.backend_only:
        run_frontend_matrix()
    print("[pass] Session/Run visibility automated full-surface smoke", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VisibilitySmokeError as exc:
        print(f"[fail] {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
