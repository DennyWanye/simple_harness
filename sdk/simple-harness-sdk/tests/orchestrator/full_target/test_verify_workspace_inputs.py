# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3o: a downstream leaf's workspace must hold the upstream accepted patch.

Grok H-L3-C3-r0 (third batch): seed method ``code.fix-by-patch@2``, facts / diagnosis
/ patch accepted, hidden grader PASS on the patched ``stats/window.py``.  The verify
leaf then failed ``rule_check`` nine times with
``artifact 'stats/window.py' is not a recorded workspace file`` and the Mission
died ``budget_exhausted``.

The InputManifest bound only ``patch.diff``.  The verify workspace started from the
unpatched seed.  The Worker applied the accepted bytes itself, listed
``stats/window.py`` on the envelope, and P2.3m's same-hash filter dropped the path
from the Attempt's recorded artifacts — so ``rule_check`` could not see it, and
``code_test`` rebuilt from seed + ``patch.diff`` + ``REPORT.md`` ran against the
red baseline.

The fixture under ``fixtures/htn/c3_verify_workspace/`` is the real
``VerificationFailed`` payload and the seed / patched bytes.  After the fix the
verify leaf is dispatched on the patched snapshot, bound files count as recorded
workspace files, ``rule_check`` / ``code_test`` pass, and the root review ACCEPT
completes the Mission.

2026-10-03（HTN 补齐阶段 A′）：主循环那一节（旧代码领域搭建、``install_hierarchical(planning=)``）
删掉；``inspect@2`` 覆盖后仍收到补丁端口那条按分诊裁决④并入代码领域测试世界的参数化主循环用例
（``test_code_domain_world.py``）。这里只留覆盖层与 rule_check 的六条纯函数用例。
"""


from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_orchestrator.artifacts.bound_workspace import (
    bound_artifacts_named_in_envelope,
)
from agent_orchestrator.artifacts.versioning import UpstreamInput
from agent_orchestrator.contracts.models import Artifact

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "htn" / "c3_verify_workspace"
NOT_RECORDED = "artifact 'stats/window.py' is not a recorded workspace file"
WINDOW = "stats/window.py"
PATCH_DIFF = "patch.diff"
REPORT = "REPORT.md"

SEED_WINDOW = (FIXTURE / "seed_window.py").read_text(encoding="utf-8")
PATCHED_WINDOW = (FIXTURE / "patched_window.py").read_text(encoding="utf-8")
PATCH_TEXT = (FIXTURE / "patch.diff").read_text(encoding="utf-8")
TEST_TEXT = (FIXTURE / "seed_test_public_window.py").read_text(encoding="utf-8")
SEED_HASH = hashlib.sha256(SEED_WINDOW.encode("utf-8")).hexdigest()
PATCHED_HASH = hashlib.sha256(PATCHED_WINDOW.encode("utf-8")).hexdigest()

SEED = {
    "README-task.md": "Make the named failing test pass.\n",
    "stats/__init__.py": (FIXTURE / "seed_init.py").read_text(encoding="utf-8"),
    WINDOW: SEED_WINDOW,
    "tests/test_public_window.py": TEST_TEXT,
}


def _artifact(task_id: str, path: str, data: bytes, *, artifact_id: str) -> Artifact:
    digest = hashlib.sha256(data).hexdigest()
    return Artifact(
        id=artifact_id,
        mission_id="mission-c3",
        task_id=task_id,
        attempt_id=f"{task_id}:attempt-1",
        type="file",
        path=path,
        version=1,
        content_hash=digest,
        size_bytes=len(data),
        produced_by="w",
        storage_uri="",
    )


# ======================================================================================
# 1. The defect, pinned on the real VerificationFailed payload
# ======================================================================================


def test_c3_r0_failed_rule_check_because_window_py_was_not_a_recorded_workspace_file() -> None:
    """The fixture is the defect, verbatim: verify listed the patched source and
    ``REPORT.md``, and ``rule_check`` refused the source as unrecorded."""

    payload = json.loads((FIXTURE / "verification_failed.json").read_text())
    failure = payload["failures"][0]
    assert failure["layer"] == "rule_check"
    assert failure["status"] == "FAIL"
    assert failure["summary"] == NOT_RECORDED
    assert failure["detail"]["problems"] == [NOT_RECORDED]
    assert failure["detail"]["checked_artifacts"] == [WINDOW, REPORT]
    assert SEED_HASH != PATCHED_HASH
    assert "end - 1" in SEED_WINDOW
    assert "end - 1" not in PATCHED_WINDOW


# ======================================================================================
# 2. Overlay: a patch binding also places the producer's accepted seed files
# ======================================================================================


# ======================================================================================
# 3. rule_check: a bound input the envelope named is a recorded workspace file
# ======================================================================================


def test_an_envelope_path_that_names_a_bound_input_is_a_recorded_workspace_file() -> None:
    """C3's envelope listed ``stats/window.py``.  After overlay that path is a bound
    input whose artifact already lives on the producer Attempt."""

    recorded = [
        _artifact("task-verify", REPORT, b"# verify\n", artifact_id="artifact-report"),
    ]
    bound = [
        UpstreamInput("task-patch", PATCH_DIFF, "d" * 64, "artifact-diff"),
        UpstreamInput("task-patch", WINDOW, PATCHED_HASH, "artifact-w"),
    ]
    window = _artifact(
        "task-patch", WINDOW, PATCHED_WINDOW.encode("utf-8"), artifact_id="artifact-w"
    )
    by_id = {window.id: window}

    def lookup(artifact_id: str) -> Artifact | None:
        return by_id.get(artifact_id)

    combined = bound_artifacts_named_in_envelope([WINDOW, REPORT], recorded, bound, lookup)
    assert {item.path for item in combined} == {WINDOW, REPORT}
    assert next(item for item in combined if item.path == WINDOW).id == "artifact-w"


def test_an_envelope_path_that_is_neither_recorded_nor_bound_stays_missing() -> None:
    recorded = [_artifact("task-verify", REPORT, b"# v\n", artifact_id="artifact-report")]
    combined = bound_artifacts_named_in_envelope(
        [WINDOW, REPORT],
        recorded,
        [UpstreamInput("task-patch", PATCH_DIFF, "d" * 64, "artifact-diff")],
        lambda _artifact_id: None,
    )
    assert [item.path for item in combined] == [REPORT]
