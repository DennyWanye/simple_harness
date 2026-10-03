# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P2.3m: a read-only rewrite refusal is bounded and escalates to planning.

Grok H-L3-C1-r0 / r1 (third batch): a synthesised method *did* carry an apply-patch
step, that step was accepted (ports facts/diagnosis/patch), and then the
``code.verify-tests`` leaf — ``side_effect_kind=external_read``, capability
``tests.run`` — rewrote product files.  P2.3k's collector refused each Attempt
``ResultRejected{read_only_leaf_rewrote_workspace}`` and the leaf retried until the
mission attempts budget died (9 times, ``MissionFailed{budget_exhausted}``).  r1's
hidden grader had already PASSed: the system turned a correct patch into a failure.

Two different writes were inside that one reason code:

* r0: verify rewrote ``metrics/collector.py`` and ``metrics/reporter.py`` to the
  **same hashes** the accepted apply-patch leaf had already produced — re-applying
  the accepted patch onto a workspace that still started from the unpatched seed.
* r1: verify rewrote ``metrics/reporter.py`` to a **new** hash (attempt-1's REPORT
  is a fix write-up; attempts 2–9 claim they did not touch source and still carry
  the new bytes).

So the collector has to tell those apart, and a genuine new write may not retry
the same occurrence past the existing ask bound (2).  After that the named
feedback goes to the planning layer the way P2.3j handed root-review findings
back — ``PlanningRejected{read_only_leaf_needs_write}`` — and the stop reason is
that name, not ``budget_exhausted``.

2026-10-03 A′：原"真跑 Orchestrator.run()"一节（代码领域种子做法、旧手搭世界与
``install_hierarchical(planning=)``）早已没有用例，只剩辅助代码，删除；这里只留规则本身的两条（纯函数）。
"""


from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_read_only_leaf_policy import _binding, _File  # noqa: E402

from agent_orchestrator.contracts.htn import SideEffectKind  # noqa: E402
from agent_orchestrator.orchestrator.occurrence_tasks import read_only_rewrites  # noqa: E402

SEED_COLLECTOR = "def record(self, name, value):\n    self._samples.append(value)\n"
PATCHED_COLLECTOR = (
    "import threading\n"
    "class Collector:\n"
    "    def __init__(self):\n"
    "        self._lock = threading.Lock()\n"
    "        self._samples = []\n"
    "    def record(self, name, value):\n"
    "        with self._lock:\n"
    "            self._samples.append(value)\n"
)
NEW_COLLECTOR = PATCHED_COLLECTOR + "# extra rewrite the patch step did not accept\n"
PATCHED_HASH = hashlib.sha256(PATCHED_COLLECTOR.encode("utf-8")).hexdigest()
NEW_HASH = hashlib.sha256(NEW_COLLECTOR.encode("utf-8")).hexdigest()
SEED_HASH = hashlib.sha256(SEED_COLLECTOR.encode("utf-8")).hexdigest()


# ======================================================================================
# The rule: accepted-consistent vs new
# ======================================================================================


def test_a_rewrite_that_matches_an_accepted_artifact_is_not_a_new_write() -> None:
    """r0: verify re-applied the accepted patch.  Same path, same hash → not refused."""

    binding = _binding(side_effect=SideEffectKind.EXTERNAL_READ, capabilities=("tests.run",))
    initial = {"metrics/collector.py": SEED_HASH, "metrics/reporter.py": "b" * 64}
    artifacts = [_File("metrics/collector.py", PATCHED_HASH), _File("REPORT.md", "d" * 64)]
    assert read_only_rewrites(binding, artifacts, initial) == ["metrics/collector.py"]
    assert (
        read_only_rewrites(
            binding, artifacts, initial, accepted={"metrics/collector.py": PATCHED_HASH}
        )
        == []
    )


def test_a_rewrite_to_a_new_hash_is_still_refused() -> None:
    """r1: reporter.py's bytes were not the accepted patch.  That stays a rewrite."""

    binding = _binding(side_effect=SideEffectKind.EXTERNAL_READ, capabilities=("tests.run",))
    initial = {"metrics/reporter.py": SEED_HASH}
    artifacts = [_File("metrics/reporter.py", NEW_HASH)]
    assert read_only_rewrites(
        binding, artifacts, initial, accepted={"metrics/reporter.py": PATCHED_HASH}
    ) == ["metrics/reporter.py"]
