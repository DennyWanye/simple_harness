# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3v: record upstream write-leaf products after retire, and bound identical
verification failures.

Grok fifth-batch H-L4-M2-r1: seed method ``code.fix-by-patch`` was root-review
REJECTED and retired; the adopted ``code.fix-by-diagnose-patch-verify-explain``
apply leaf (``code.apply-patch``) wrote ``net/retry.py`` to the *same* hash the
retired apply had already accepted, listed it on the envelope, and P2.3m's
same-hash filter dropped it from recorded artifacts.  ``rule_check`` then failed
42 times with ``artifact 'net/retry.py' is not a recorded workspace file`` until
``budget_exhausted(attempts=48)``.  Hidden grader PASS.

H-L4-M3-r0: the ``code.reproduce-failure`` leaf failed ``code_test`` 21 times
with the same SyntaxError until the task token account died.  Not the same
recorded-file hole, but the same unbounded retry of one identical failure.

Two repairs, no new config, no 20th ``_new_mode`` site:

* a write leaf's listed / changed files are recorded even when the bytes match a
  retired method's accepted artifact; a producer that only delivered a unified
  diff has that diff applied onto the seed and the patched paths recorded.
* N identical verification failures on one occurrence (same layer + same
  problems hash) escalate to ``PlanningRejected{repeated_verification_failure}``
  instead of burning the attempts wall.

2026-10-03（HTN 补齐阶段 A′）：主循环那半（第 2 节起的脚本化执行者与旧代码领域搭建）早已没有
用例，随旧构造器删掉；这里只留指纹与 diff 应用的七条纯函数用例。
"""


from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_verify_workspace_inputs import (  # noqa: E402
    NOT_RECORDED,
    PATCH_DIFF,
    PATCH_TEXT,
    PATCHED_WINDOW,
    REPORT,
    SEED_WINDOW,
    WINDOW,
)

from agent_orchestrator.artifacts.bound_workspace import (  # noqa: E402
    UnifiedDiffApplyError,
    decode_unified_diff_text,
    files_patched_by_unified_diff,
)
from agent_orchestrator.orchestrator.occurrence_tasks import (  # noqa: E402
    verification_failure_fingerprint,
)

# ======================================================================================
# 1. Constants and the fingerprint
# ======================================================================================


def test_the_fingerprint_is_layer_plus_problems_not_timing() -> None:
    """M2's 42 rule_check rows share one problems list.  M3's code_test summaries
    differ by ``0.06s`` / ``0.07s`` and by the attempt workspace path."""

    m2 = {
        "layer": "rule_check",
        "summary": NOT_RECORDED,
        "detail": {
            "problems": [NOT_RECORDED],
            "checked_artifacts": [WINDOW, PATCH_DIFF, REPORT],
        },
    }
    assert verification_failure_fingerprint([m2]) == verification_failure_fingerprint(
        [{**m2, "detail": {**m2["detail"], "checked_artifacts": [REPORT, WINDOW]}}]
    )
    a = {
        "layer": "code_test",
        "summary": "pytest failed: 1 error in 0.06s",
        "detail": {},
    }
    b = {
        "layer": "code_test",
        "summary": "pytest failed: 1 error in 0.08s",
        "detail": {},
    }
    assert verification_failure_fingerprint([a]) == verification_failure_fingerprint([b])
    other = {
        "layer": "code_test",
        "summary": "pytest failed: 2 failed in 0.06s",
        "detail": {},
    }
    assert verification_failure_fingerprint([a]) != verification_failure_fingerprint([other])
    assert verification_failure_fingerprint([m2]) != verification_failure_fingerprint([a])


def _code_test_stdout(exc: str, *, attempt: str, seconds: str) -> dict[str, Any]:
    """M3-r0 shape: summary is only the last pytest line; the exception is in stdout."""

    stdout = (
        "==================================== ERRORS ====================================\n"
        "___________________ ERROR collecting tests/test_dispatch.py ____________________\n"
        f"E     File \"/tmp/workspaces/task-d6d9:{attempt}-verify/service/beta/broken.py\""
        ", line 5\n"
        "E       def dispatch(queue, sink)\n"
        "E                                ^\n"
        f"E   {exc}\n"
        "ERROR tests/test_dispatch.py\n"
        f"1 error in {seconds}\n"
    )
    return {
        "layer": "code_test",
        "summary": f"pytest failed: 1 error in {seconds}",
        "detail": {"runs": [{"stdout": stdout, "passed": False, "returncode": 2}]},
    }


def test_code_test_fingerprint_uses_the_exception_not_just_error_count() -> None:
    """P1-2: M3-r0's 21 SyntaxError rows stay identical; SyntaxError vs ImportError
    must not share a fingerprint just because both say ``1 error in Xs``."""

    syntax_a = _code_test_stdout("SyntaxError: expected ':'", attempt="attempt-1", seconds="0.06s")
    syntax_b = _code_test_stdout("SyntaxError: expected ':'", attempt="attempt-21", seconds="0.08s")
    imported = _code_test_stdout(
        "ImportError: cannot import name 'dispatch'", attempt="attempt-2", seconds="0.06s"
    )
    assert verification_failure_fingerprint([syntax_a]) == verification_failure_fingerprint(
        [syntax_b]
    )
    assert verification_failure_fingerprint([syntax_a]) != verification_failure_fingerprint(
        [imported]
    )


def test_c3_unified_diff_patches_the_seed_window() -> None:
    """P2.3o's leftover: a producer that only delivered ``patch.diff`` still
    names the patched seed file after the diff is applied."""

    patched = files_patched_by_unified_diff(PATCH_TEXT, {WINDOW: SEED_WINDOW})
    assert patched[WINDOW] == PATCHED_WINDOW
    assert "end - 1" not in patched[WINDOW]


OTHER = "stats/other.py"
OTHER_SEED = "def n():\n    return 1\n"
OTHER_PATCHED = "def n():\n    return 2\n"
TWO_FILE_DIFF = PATCH_TEXT.rstrip() + (
    "\n--- a/stats/other.py\n"
    "+++ b/stats/other.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def n():\n"
    "-    return 1\n"
    "+    return 2\n"
)


def test_a_two_file_unified_diff_patches_both_seed_files() -> None:
    """P1-1: switching to the next ``--- `` must flush the current hunk."""

    patched = files_patched_by_unified_diff(
        TWO_FILE_DIFF, {WINDOW: SEED_WINDOW, OTHER: OTHER_SEED}
    )
    assert patched[WINDOW] == PATCHED_WINDOW
    assert patched[OTHER] == OTHER_PATCHED


def test_a_hunk_mismatch_on_one_file_rejects_the_whole_diff() -> None:
    """P1-1: one failed file must not leave the other file registered."""

    broken = TWO_FILE_DIFF.replace("-    return 1\n", "-    return 99\n")
    with pytest.raises(UnifiedDiffApplyError) as caught:
        files_patched_by_unified_diff(broken, {WINDOW: SEED_WINDOW, OTHER: OTHER_SEED})
    assert caught.value.path == OTHER
    assert caught.value.reason == "hunk_mismatch"


def test_a_parent_directory_path_in_the_diff_is_rejected() -> None:
    """P1-1: ``../`` is fail-closed, not skipped."""

    text = (
        "--- a/../secret.py\n"
        "+++ b/../secret.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+y\n"
    )
    with pytest.raises(UnifiedDiffApplyError) as caught:
        files_patched_by_unified_diff(text, {"secret.py": "x\n", WINDOW: SEED_WINDOW})
    assert ".." in caught.value.path or caught.value.reason == "unsafe_path"


def test_a_binary_diff_document_is_rejected_as_not_utf8() -> None:
    """P1-1: a non-utf8 patch document is a named refusal, not a silent skip."""

    with pytest.raises(UnifiedDiffApplyError) as caught:
        decode_unified_diff_text(b"\xff\xfe--- a/x\n", path="patch.diff")
    assert caught.value.path == "patch.diff"
    assert caught.value.reason == "not_utf8"
