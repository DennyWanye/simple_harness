# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Live plan view (plan 2026-09-25 live-view §4 steps 1 and 5).

The library is built by the SDK's own migrations (``Store.open``), so a column the SDK
renames or drops fails here first.  Rows are inserted with foreign keys off; every NOT NULL
column this test does not care about gets a type-shaped filler.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agent_orchestrator.storage.store import Store

from deskpet.orchestration.live_graph import read_live_graph, read_planning_decisions
from deskpet.orchestration.service import OrchestrationRequestError

M = "mission-live"
ROOT = "occ-root"


class _Control:
    def __init__(self, owned: set[str]) -> None:
        self.owned = owned

    def _mission(self, mission_id: str) -> None:
        if mission_id not in self.owned:
            error = RuntimeError("not found")
            error.code = "not_found"  # type: ignore[attr-defined]
            raise error


def _service(root: Path, owned: set[str] = frozenset({M})) -> Any:  # type: ignore[assignment]
    control = _Control(set(owned))
    return SimpleNamespace(root=root, _require=lambda: control)


def _insert(db: sqlite3.Connection, table: str, **values: Any) -> None:
    for _, name, kind, notnull, default, _pk in db.execute(f"PRAGMA table_info({table})"):
        if name in values or not notnull or default is not None:
            continue
        values[name] = {"INTEGER": 1, "REAL": 1.0}.get(kind.upper(), "a" * 64)
    columns = ",".join(values)
    db.execute(f"INSERT INTO {table}({columns}) VALUES ({','.join('?' * len(values))})", tuple(values.values()))


def _event(db: sqlite3.Connection, seq: int, type_: str, task_id: str | None = None,
           payload: dict[str, Any] | None = None, created_at: float = 100.0) -> None:
    _insert(db, "events", seq=seq, event_id=f"e{seq}", idempotency_key=f"k{seq}", type=type_,
            mission_id=M, task_id=task_id, attempt_id=None, actor_type="system",
            payload_json=json.dumps(payload or {}), created_at=created_at)


def _phase(db: sqlite3.Connection, seq: int, revision: int, phase: str, reason: str = "NEEDS_REFINEMENT") -> None:
    _event(db, seq, "CompoundPhaseChanged", "task-root", {"occurrence_id": ROOT, "task_id": "task-root",
           "plan_revision": revision, "phase": phase, "display_status": "ACTIVE",
           "readiness_reason": reason, "secret": "never leaves"})


@pytest.fixture()
def library(tmp_path: Path) -> Path:
    Store.open(tmp_path / "orchestrator.db").close()
    db = sqlite3.connect(tmp_path / "orchestrator.db", isolation_level=None)
    db.execute("PRAGMA foreign_keys = OFF")
    db.create_function("assurance_change_receipt", -1, lambda *args: None)  # SDK trigger hook
    _insert(db, "missions", mission_id=M)
    for revision, state in ((1, "RETIRED"), (2, "ACTIVE")):
        _insert(db, "plan_revisions", mission_id=M, revision=revision, state=state, base_revision=None)
    _insert(db, "method_instances", mission_id=M, instance_id="inst-1", goal_task_id="task-root",
            goal_occurrence_id=ROOT, method_id="synth-a", method_version=1, plan_revision=1, state="ADOPTED")
    contract = {"composition": {"criterion_links": [
        {"child_step": "positioning", "evidence_requirement": "positioning 步骤产出 01-定位.md"},
        {"child_step": "menu", "evidence_requirement": "menu 步骤产出 02-菜单.md"},
        {"child_step": "menu", "evidence_requirement": "menu 步骤给出至少 8 个饮品"}]}}
    _insert(db, "method_contracts", method_id="synth-a", method_version=1, registry_status="TRIAL_ADMITTED", author="system", trial_scope_mission=M,
            contract_json=json.dumps(contract, ensure_ascii=False))
    for kid, slot in (("occ-a", "positioning"), ("occ-b", "menu")):
        _insert(db, "method_child_occurrences", instance_id="inst-1", slot_key=slot, mission_id=M,
                occurrence_id=kid, obligation_id="o", goal_occurrence_id=kid, requiredness="required",
                reuse_policy="new_work")
    children = {1: ["occ-a", "occ-b"], 2: ["occ-a", "occ-b", "occ-c"]}
    for revision, kids in children.items():
        _insert(db, "plan_memberships", mission_id=M, revision=revision, occurrence_id=ROOT, task_id="task-root",
                instance_id=None, form="compound", requiredness="required", adopted=1)
        for kid in kids:
            _insert(db, "plan_memberships", mission_id=M, revision=revision, occurrence_id=kid,
                    task_id="task-" + kid[-1], instance_id="inst-1", form="primitive",
                    requiredness="required", adopted=1)
    _insert(db, "order_constraints", mission_id=M, plan_revision=2, before_occurrence="occ-a",
            after_occurrence="occ-b", release_condition="accepted")
    _insert(db, "order_constraints", mission_id=M, plan_revision=2, before_occurrence="occ-b",
            after_occurrence="occ-c", release_condition="accepted")
    _insert(db, "data_requirements", mission_id=M, plan_revision=2, requirement_id="r1",
            producer_occurrence="occ-a", output_port="o", consumer_occurrence="occ-c", input_port="i",
            source_revision_policy="PINNED")
    for ordinal, (task, status) in enumerate((("task-root", "ACTIVE"), ("task-a", "COMPLETED"),
                                              ("task-b", "ACTIVE"), ("task-c", "BLOCKED"))):
        _insert(db, "tasks", task_id=task, mission_id=M, ordinal=ordinal, status=status)
    for n in range(2):
        _insert(db, "attempts", attempt_id=f"att-{n}", task_id="task-b", mission_id=M, ordinal=n, status="RUNNING")
    _phase(db, 1, 1, "refining")
    _event(db, 2, "TaskCompleted", "task-a", created_at=150.0)
    _phase(db, 3, 1, "waiting_children", "WAITING_ORDER")
    _event(db, 4, "AttemptStarted", "task-b", created_at=200.0)
    _phase(db, 5, 3, "composition_review")  # a later revision's phase must not leak into 2
    _insert(db, "planning_requests", request_id="req-1", mission_id=M, base_plan_revision=1)
    _insert(db, "planning_decisions", decision_id="d1", request_id="req-1", attempt_ordinal=1,
            decision_type="REFINE", status="COMMITTED", rejection_codes_json="[]", created_at=10.0)
    _insert(db, "planning_decisions", decision_id="d2", request_id="req-1", attempt_ordinal=2,
            decision_type="REPAIR", status="REJECTED", rejection_codes_json='["ORDER_CYCLE"]', created_at=11.0)
    db.close()
    return tmp_path


def _nodes(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["occurrence_id"]: node for node in view["nodes"]}


def test_current_revision_structure_status_and_phase(library: Path) -> None:
    view = read_live_graph(_service(library), {"mission_id": M})
    assert (view["source"], view["plan_revision"], view["revisions"], view["through_seq"]) == ("htn", 2, [1, 2], 5)
    nodes = _nodes(view)
    assert set(nodes) == {ROOT, "occ-a", "occ-b", "occ-c"}
    assert nodes[ROOT]["parent"] is None and nodes[ROOT]["method"] is None
    assert {nodes[k]["parent"] for k in ("occ-a", "occ-b", "occ-c")} == {ROOT}
    assert nodes["occ-a"]["method"] == "synth-a@1"
    # revision 2 has no phase event yet: the latest phase of an earlier revision stays
    assert (nodes[ROOT]["phase"], nodes[ROOT]["readiness_reason"]) == ("waiting_children", "WAITING_ORDER")
    assert nodes["occ-b"] == {"occurrence_id": "occ-b", "task_id": "task-b", "form": "primitive", "parent": ROOT,
                              "method": "synth-a@1", "task_status": "ACTIVE", "phase": None,
                              "readiness_reason": None, "attempt_count": 2, "last_event_at": 200.0,
                              "step": {"key": "menu", "evidence": ["menu 步骤产出 02-菜单.md", "menu 步骤给出至少 8 个饮品"]}}
    assert nodes["occ-a"]["step"] == {"key": "positioning", "evidence": ["positioning 步骤产出 01-定位.md"]}
    assert nodes["occ-c"]["step"] is None and nodes[ROOT]["step"] is None
    assert nodes["occ-a"]["last_event_at"] == 150.0 and nodes["occ-c"]["attempt_count"] == 0
    assert view["edges"] == [{"kind": "order", "source": "occ-a", "target": "occ-b"},
                             {"kind": "order", "source": "occ-b", "target": "occ-c"},
                             {"kind": "data", "source": "occ-a", "target": "occ-c"}]
    assert "never leaves" not in json.dumps(view)


def test_historical_revision_shows_its_own_structure(library: Path) -> None:
    view = read_live_graph(_service(library), {"mission_id": M, "revision": 1})
    assert view["plan_revision"] == 1 and set(_nodes(view)) == {ROOT, "occ-a", "occ-b"}
    assert view["edges"] == []


def test_no_active_revision_is_planning(library: Path) -> None:
    db = sqlite3.connect(library / "orchestrator.db", isolation_level=None)
    db.execute("UPDATE plan_revisions SET state='RETIRED'")
    db.close()
    view = read_live_graph(_service(library), {"mission_id": M})
    assert (view["source"], view["nodes"], view["plan_revision"]) == ("planning", [], None)


@pytest.mark.parametrize("request_body", [{}, {"mission_id": M, "extra": 1}, {"mission_id": ""},
                                          {"mission_id": M, "revision": -1}, {"mission_id": M, "revision": "2"},
                                          {"mission_id": M, "revision": True}])
def test_invalid_requests_are_refused(library: Path, request_body: dict[str, Any]) -> None:
    with pytest.raises(OrchestrationRequestError):
        read_live_graph(_service(library), request_body)


def test_unknown_revision_is_refused(library: Path) -> None:
    with pytest.raises(OrchestrationRequestError) as caught:
        read_live_graph(_service(library), {"mission_id": M, "revision": 9})
    assert caught.value.code == "invalid_revision"


def test_ownership_is_checked_before_reading(library: Path) -> None:
    with pytest.raises(RuntimeError, match="not found"):
        read_live_graph(_service(library, owned=set()), {"mission_id": M})
    with pytest.raises(RuntimeError, match="not found"):
        read_planning_decisions(_service(library, owned=set()), {"mission_id": M})


def test_connection_is_read_only(library: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from deskpet.orchestration import live_graph

    seen: list[sqlite3.Connection] = []
    original = live_graph._connect
    monkeypatch.setattr(live_graph, "_connect", lambda service: seen.append(original(service)) or seen[-1])
    read_live_graph(_service(library), {"mission_id": M})
    reopened = original(_service(library))
    with pytest.raises(sqlite3.OperationalError):
        reopened.execute("DELETE FROM events")
    reopened.close()
    assert seen


def test_planning_decisions_whitelist(library: Path) -> None:
    view = read_planning_decisions(_service(library), {"mission_id": M})
    assert view["decisions"] == [
        {"decision_id": "d1", "decision_type": "REFINE", "status": "COMMITTED", "rejection_codes": [],
         "created_at": 10.0, "base_plan_revision": 1},
        {"decision_id": "d2", "decision_type": "REPAIR", "status": "REJECTED", "rejection_codes": ["ORDER_CYCLE"],
         "created_at": 11.0, "base_plan_revision": 1},
    ]


_REAL = (Path(__file__).resolve().parents[3] / ".local-test-evidence/2026-09-25/opt/ui-full/userdata/data"
         / "agent-orchestrator")


@pytest.mark.skipif(not (_REAL / "orchestrator.db").exists(), reason="real UI run library not on this machine")
def test_real_library_reads_every_mission() -> None:
    db = sqlite3.connect(f"file:{_REAL / 'orchestrator.db'}?mode=ro", uri=True)
    missions = [row[0] for row in db.execute("SELECT mission_id FROM missions")]
    db.close()
    service = _service(_REAL, owned=set(missions))
    for mission_id in missions:
        view = read_live_graph(service, {"mission_id": mission_id})
        if view["source"] == "htn":
            assert view["nodes"] and sum(node["parent"] is None for node in view["nodes"]) == 1
        read_planning_decisions(service, {"mission_id": mission_id})
