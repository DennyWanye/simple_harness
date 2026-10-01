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
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_inspect_leaf_patch_input import (  # noqa: E402
    _c1_method,
    _CodeWorld,
)
from test_read_only_leaf_policy import _binding, _File  # noqa: E402

from agent_orchestrator.contracts.htn import SideEffectKind  # noqa: E402
from agent_orchestrator.contracts.state_machines import (  # noqa: E402
    TERMINAL_MISSION,
)
from agent_orchestrator.orchestrator.commit_service import mission_account  # noqa: E402
from agent_orchestrator.orchestrator.event_handler import Orchestrator  # noqa: E402
from agent_orchestrator.orchestrator.occurrence_tasks import (  # noqa: E402
    read_only_rewrites,
)
from agent_orchestrator.runtime.assembly import OrchestratorConfig  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.testing.fixtures import (  # noqa: E402
    RoleScriptedProvider,
    envelope_step,
    package_of,
)
from scripted_plans import plan_revision_proposal_step  # noqa: E402

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

TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list")
SEED = {
    "metrics/collector.py": SEED_COLLECTOR,
    "metrics/reporter.py": "def summary(c):\n    return {'n': len(c._samples)}\n",
    "tests/test_public_collector.py": "def test_ok():\n    assert True\n",
}


def _four_step(method_id: str, *, suffix: str = "") -> dict[str, Any]:
    """facts → reproduce → apply-patch → verify; both root criteria hang on verify."""

    rename = {
        "read-facts": f"read-facts{suffix}",
        "reproduce": f"reproduce{suffix}",
        "apply-patch": f"apply-patch{suffix}",
        "verify": f"verify{suffix}",
    }
    body = copy.deepcopy(_c1_method())
    body["method_id"] = method_id
    kept = []
    for item in body["steps"]:
        if item["local_id"] not in rename:
            continue
        item["local_id"] = rename[item["local_id"]]
        for binding in item.get("arguments", {}).values():
            if isinstance(binding, dict) and binding.get("step") in rename:
                binding["step"] = rename[binding["step"]]
        kept.append(item)
    body["steps"] = kept
    body["ordering"] = [
        {"after": rename["reproduce"], "before": rename["read-facts"]},
        {"after": rename["apply-patch"], "before": rename["reproduce"]},
        {"after": rename["verify"], "before": rename["apply-patch"]},
    ]
    body["composition"] = {
        "criterion_links": [
            {
                "child_criterion_id": "c-tests-verified",
                "child_step": rename["verify"],
                "evidence_requirement": "verify-tests report after the applied patch",
                "parent_criterion_id": "c-test-passes",
            },
            {
                "child_criterion_id": "c-tests-verified",
                "child_step": rename["verify"],
                "evidence_requirement": "the report explains the applied change",
                "parent_criterion_id": "c-change-explained",
            },
        ],
        "finalizer_step": rename["verify"],
        "independent_review_required": True,
        "outputs": {},
    }
    body["required_capabilities"] = ["repo.read", "repo.write", "tests.run"]
    return body


def _planner_picks_named(needle: str):
    """Retire the rejected instance; refine with the library id containing ``needle``."""

    def step(request: Any) -> str:
        package = package_of(request)
        rejected = package["rejected_refinements"]
        assert rejected, package.keys()
        entry = rejected[0]
        library = [
            item
            for item in package["method_library"]
            if item["goal_signature_id"] == entry["goal_signature_id"]
            and not item["rejected_by_root_review"]
            and not item.get("rejected_by_read_only_leaf")
            and needle in str(item.get("refine_method_ref", {}).get("id", ""))
        ]
        assert library, [item.get("refine_method_ref") for item in package["method_library"]]
        chosen = library[0]["refine_method_ref"]
        return plan_revision_proposal_step(
            proposal_id=f"p-replace-{package['plan']['plan_revision']}",
            expected_plan_revision=int(package["plan"]["plan_revision"]),
            read_set=[
                {
                    "kind": "method",
                    "id": chosen["id"],
                    "semantic_revision": chosen["version"],
                    "content_hash": chosen["content_hash"],
                }
            ],
            operations=[
                {
                    "op": "retire_method",
                    "method_instance_id": entry["rejected_method_instance_id"],
                    "reason": entry["findings"][0]["detail"][:120],
                },
                {
                    "op": "refine",
                    "goal_id": entry["goal_id"],
                    "obligation_id": entry["obligation_id"],
                    "method_ref": dict(chosen),
                    "bindings": {},
                },
            ],
            rationale="switching to a method that keeps writes on a patch step",
        )

    return step


def _accepting_reviewer(request: Any) -> str:
    shown = json.loads(
        next(m.content for m in reversed(request.messages) if str(m.role).endswith("user"))
    )
    return (
        "<critic_verdict>"
        + json.dumps(
            {
                "verdict": "PASS",
                "findings": [],
                "mission_criteria": [
                    {"criterion": item["criterion_id"], "met": True, "reason": "scripted"}
                    for item in shown["criteria"]
                ],
            }
        )
        + "</critic_verdict>"
    )


class _LeafWorker:
    """One scripted Worker that drives the four-step method.

    ``mode``:

    * ``match`` — verify rewrites collector.py to the accepted patch bytes
    * ``new`` — verify rewrites collector.py to a *new* hash (the r1 shape)
    * ``clean`` — verify only writes REPORT.md
    """

    def __init__(
        self,
        *,
        mode: str,
        rewrite_limit: int | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self.mode = mode
        self.rewrite_limit = rewrite_limit
        self.workspace_root = workspace_root
        self.verify_attempts = 0
        self._queues: dict[str, list[Any]] = {}

    def __call__(self, request: Any) -> Any:
        package = package_of(request)
        attempt_id = str((package.get("attempt") or {}).get("attempt_id") or "")
        if attempt_id not in self._queues:
            self._queues[attempt_id] = self._script(package)
        queue = self._queues[attempt_id]
        if not queue:
            raise AssertionError(f"worker script exhausted for {attempt_id}")
        step = queue.pop(0)
        if callable(step) and not isinstance(step, (str, tuple)):
            return step(request)
        return step

    def _script(self, package: dict[str, Any]) -> list[Any]:
        goal = str((package.get("task_contract") or {}).get("goal") or "")
        if "read the repository" in goal:
            return _write_and_envelope(
                [("facts.json", '{"tests": ["tests/test_public_collector.py"]}')],
                ["facts.json"],
                {"facts": "facts.json"},
            )
        if "reproduce" in goal:
            return _write_and_envelope(
                [("diagnosis.md", "# diagnosis\nconcurrent record loses counts\n")],
                ["diagnosis.md"],
                {"diagnosis": "diagnosis.md"},
            )
        if "apply a patch" in goal:
            return _write_and_envelope(
                [
                    ("metrics/collector.py", PATCHED_COLLECTOR),
                    ("applied.patch", "--- a/metrics/collector.py\n+++ b/metrics/collector.py\n"),
                    ("REPORT.md", "# patch\nlocked collector.record\n"),
                ],
                ["metrics/collector.py", "applied.patch", "REPORT.md"],
                {"patch": "applied.patch"},
            )
        if "run the test suite" in goal:
            self.verify_attempts += 1
            if self.mode == "clean" or (
                self.rewrite_limit is not None and self.verify_attempts > self.rewrite_limit
            ):
                return _write_and_envelope(
                    [("REPORT.md", "# verify\nvisible tests passed\n")],
                    ["REPORT.md"],
                    {"report": "REPORT.md"},
                )
            content = PATCHED_COLLECTOR if self.mode == "match" else NEW_COLLECTOR
            writes = [
                ("metrics/collector.py", content),
                ("REPORT.md", "# verify\nrewrote collector to run tests\n"),
            ]
            artifacts = ["metrics/collector.py", "REPORT.md"]
            outputs = {"report": "REPORT.md"}
            if self.mode == "new":
                # P2.3u: the gateway refuses workspace_write_file on existing
                # source.  These two tests still exercise the collector fallback,
                # so the rewrite is applied on the attempt tree directly.
                if self.workspace_root is None:
                    raise AssertionError(
                        "mode='new' needs workspace_root to bypass the write guard"
                    )
                attempt_id = str((package.get("attempt") or {}).get("attempt_id") or "")
                root = self.workspace_root

                def poke(_request: Any, *, _aid: str = attempt_id, _body: str = content) -> Any:
                    target = root / _aid / "metrics/collector.py"
                    target.write_text(_body, encoding="utf-8")
                    return (
                        "workspace_write_file",
                        {
                            "path": "REPORT.md",
                            "content": "# verify\nrewrote collector to run tests\n",
                        },
                    )

                return [
                    poke,
                    envelope_step(
                        summary="scripted leaf",
                        artifacts=artifacts,
                        claims=["scripted"],
                        override=lambda body: {**body, "outputs": dict(outputs)},
                    ),
                ]
            return _write_and_envelope(writes, artifacts, outputs)
        raise AssertionError(f"unexpected leaf goal: {goal!r}")


def _write_and_envelope(
    writes: list[tuple[str, str]], artifacts: list[str], outputs: dict[str, str]
) -> list[Any]:
    steps: list[Any] = [
        ("workspace_write_file", {"path": path, "content": text}) for path, text in writes
    ]
    steps.append(
        envelope_step(
            summary="scripted leaf",
            artifacts=artifacts,
            claims=["scripted"],
            override=lambda body: {**body, "outputs": dict(outputs)},
        )
    )
    return steps


def _world(tmp_path, *, key: str, method_id: str = "code.fix-by-patch-then-verify") -> _CodeWorld:
    evidence = Path(tmp_path) / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    return _CodeWorld(
        evidence,
        method=_four_step(method_id),
        key=key,
        db_name="orchestrator.db",
        allowed_tools=TOOLS,
        workspace_seed=SEED,
        success_criteria=("file:REPORT.md",),
        max_attempts=20,
    )


def _conservation(loop: Orchestrator, mission_id: str) -> dict[str, Any]:
    report = loop.commit.ledger.costs_report(mission_id)
    account = next(
        item for item in report["accounts"] if item["account_id"] == mission_account(mission_id)
    )
    remaining = int(account["remaining_tokens"] or 0)
    reserved = int(account["reserved_tokens"])
    settled = int(account["settled_tokens"])
    pool = int(account["limits"]["max_tokens"])
    return {
        "holds": remaining + reserved + settled == pool,
        "remaining": remaining,
        "reserved": reserved,
        "settled": settled,
        "pool": pool,
        "held_reservations": list(report["held_reservations"]),
    }


def _run(
    world: _CodeWorld,
    tmp_path,
    provider: RoleScriptedProvider,
    *,
    cycles: int = 400,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = Path(tmp_path) / "evidence"
    world.store.close()

    async def case() -> dict[str, Any]:
        config = OrchestratorConfig(
            evidence_root=evidence,
            max_concurrency=1,
            test_timeout_seconds=30,
            max_planning_attempts=1,
            **(extra or {}),
        )
        async with Orchestrator(config, provider, poll_interval=0.02) as loop:
            world.world.semantics = HtnStore(loop.store)
            loop.install_hierarchical(planning=world.world)
            for _ in range(cycles):
                progressed = await loop._cycle()
                await asyncio.sleep(0.02)
                mission = loop.store.get_mission(world.mission.id)
                if mission is not None and mission.status in TERMINAL_MISSION:
                    break
                if not progressed and not loop._has_inflight():
                    await loop._record_hierarchical_stall()
                    await loop._confirm_and_stop_stalled()
            mission = loop.store.get_mission(world.mission.id)
            assert mission is not None
            events = list(loop.store.list_events(world.mission.id))
            return {
                "status": mission.status,
                "stop_reason": mission.stop_reason,
                "report": dict(mission.final_report or {}),
                "types": [item.type for item in events],
                "events": events,
                "roles": dict(provider.by_role),
                "progress": list(loop.progress_log),
                "conservation": _conservation(loop, mission.id),
                "attempts": [
                    (task.id, attempt.ordinal, str(attempt.status))
                    for task in loop.store.list_tasks(mission.id)
                    for attempt in loop.store.list_attempts(task.id)
                ],
            }

    return asyncio.run(case())


# ======================================================================================
# 1. The rule: accepted-consistent vs new
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


# ======================================================================================
# 2. True Orchestrator.run()
# ======================================================================================


# ======================================================================================
# 3. Prompt: a code-change method must include a write step
# ======================================================================================


