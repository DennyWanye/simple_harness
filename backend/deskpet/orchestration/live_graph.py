# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Read-only live view of a hierarchical Mission's plan (plan 2026-09-25 live-view §4 step 1).

The strict TaskGraph read (``taskgraph.py``) answers ``NOT_ENABLED`` for every real Mission,
because the TaskGraph kernel is never enabled on this Host.  This view reads the facts the
SDK already stores for every hierarchical Mission instead: the plan tables (structure),
``tasks.status`` (leaves) and the latest ``CompoundPhaseChanged`` event (compound phase the
SDK computed).  It derives no state of its own and never writes the library; like the
change pump it opens ``orchestrator.db`` read-only, after the ownership check.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .service import OrchestrationRequestError

logger = logging.getLogger(__name__)

MAX_NODES = 2000
DECISION_LIMIT = 200


def _invalid() -> None:
    raise OrchestrationRequestError("invalid_request", "执行图请求的字段或版本无效")


def _checked(request: Mapping[str, Any], optional: set[str]) -> str:
    if "mission_id" not in request or set(request) - ({"mission_id"} | optional):
        _invalid()
    mission_id = request["mission_id"]
    if not isinstance(mission_id, str) or not mission_id.strip() or len(mission_id) > 512:
        _invalid()
    revision = request.get("revision")
    if revision is not None and (type(revision) is not int or not 0 <= revision <= 2**53 - 1):
        _invalid()
    return mission_id


def _connect(service: Any) -> sqlite3.Connection:
    path = Path(service.root) / "orchestrator.db"
    if not path.exists():
        raise OrchestrationRequestError("source_unavailable", "编排数据库不存在")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1.0)
    connection.execute("BEGIN")  # one consistent read of every table below (WAL)
    return connection


def read_live_graph(service: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    mission_id = _checked(request, {"revision"})
    service._require()._mission(mission_id)  # ownership first; tenant never comes from IPC
    connection = _connect(service)
    try:
        return _read(connection, mission_id, request.get("revision"))
    except sqlite3.Error as error:
        logger.warning("live graph read failed for %s: %s", mission_id, error)
        raise OrchestrationRequestError("source_unavailable", "执行图来源暂时不可读，请稍后重试") from error
    finally:
        connection.rollback()
        connection.close()


def _read(db: sqlite3.Connection, mission_id: str, revision: int | None) -> dict[str, Any]:
    through = int(db.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE mission_id=?",
                             (mission_id,)).fetchone()[0])
    revisions = [int(row[0]) for row in db.execute(
        "SELECT revision FROM plan_revisions WHERE mission_id=? ORDER BY revision", (mission_id,))]
    body: dict[str, Any] = {"schema_version": 1, "mission_id": mission_id, "source": "planning",
                            "plan_revision": None, "through_seq": through, "nodes": [], "edges": [],
                            "revisions": revisions}
    if revision is None:
        active = [int(row[0]) for row in db.execute(
            "SELECT revision FROM plan_revisions WHERE mission_id=? AND state='ACTIVE'", (mission_id,))]
        if not active:
            return body
        if len(active) > 1:
            raise OrchestrationRequestError("graph_integrity", "计划同时有多个生效版本，需要人工修复")
        revision = active[0]
    elif revision not in revisions:
        raise OrchestrationRequestError("invalid_revision", "没有这个计划版本")

    rows = db.execute(
        "SELECT m.occurrence_id, m.task_id, m.form, i.goal_occurrence_id, i.method_id, i.method_version"
        " FROM plan_memberships m LEFT JOIN method_instances i"
        " ON i.mission_id=m.mission_id AND i.instance_id=m.instance_id"
        " WHERE m.mission_id=? AND m.revision=? ORDER BY m.occurrence_id",
        (mission_id, revision)).fetchall()
    if len(rows) > MAX_NODES:
        raise OrchestrationRequestError("bound_reached", "执行图节点太多，暂时无法显示")
    members = {str(row[0]) for row in rows}
    statuses = dict(db.execute("SELECT task_id, status FROM tasks WHERE mission_id=?", (mission_id,)))
    attempts = dict(db.execute("SELECT task_id, COUNT(*) FROM attempts WHERE mission_id=? GROUP BY task_id",
                               (mission_id,)))
    active_at = dict(db.execute("SELECT task_id, MAX(created_at) FROM events"
                                " WHERE mission_id=? AND task_id IS NOT NULL GROUP BY task_id", (mission_id,)))
    # SQLite: with MAX(seq) the bare columns come from the row holding the maximum.  ``<=``
    # keeps the last known phase after a new revision until the phase moves again.
    phases = {str(row[0]): (row[1], row[2]) for row in db.execute(
        "SELECT json_extract(payload_json,'$.occurrence_id'), json_extract(payload_json,'$.phase'),"
        " json_extract(payload_json,'$.readiness_reason'), MAX(seq) FROM events"
        " WHERE mission_id=? AND type='CompoundPhaseChanged'"
        " AND json_extract(payload_json,'$.plan_revision')<=? GROUP BY 1", (mission_id, revision))
        if row[0] is not None}

    steps = _step_duties(db, mission_id, [(r[0], r[4], r[5]) for r in rows])
    nodes = []
    for occurrence, task_id, form, parent, method_id, method_version in rows:
        if parent is not None and str(parent) not in members:
            logger.warning("live graph %s: parent %s of %s outside revision %s",
                           mission_id, parent, occurrence, revision)
            parent = None
        phase, reason = phases.get(str(occurrence), (None, None)) if form == "compound" else (None, None)
        nodes.append({
            "occurrence_id": str(occurrence), "task_id": str(task_id), "form": str(form),
            "parent": None if parent is None else str(parent),
            "method": None if method_id is None else f"{method_id}@{method_version}",
            "task_status": None if statuses.get(task_id) is None else str(statuses[task_id]),
            "phase": None if phase is None else str(phase),
            "readiness_reason": None if reason is None else str(reason),
            "attempt_count": int(attempts.get(task_id, 0)),
            "last_event_at": active_at.get(task_id),
            "step": steps.get(str(occurrence)),
        })
    edges = [{"kind": "order", "source": str(a), "target": str(b)} for a, b in db.execute(
        "SELECT before_occurrence, after_occurrence FROM order_constraints"
        " WHERE mission_id=? AND plan_revision=? ORDER BY 1, 2", (mission_id, revision))]
    edges += [{"kind": "data", "source": str(a), "target": str(b)} for a, b in db.execute(
        "SELECT DISTINCT producer_occurrence, consumer_occurrence FROM data_requirements"
        " WHERE mission_id=? AND plan_revision=? ORDER BY 1, 2", (mission_id, revision))]
    body.update(source="htn", plan_revision=revision, nodes=nodes,
                edges=[e for e in edges if e["source"] in members and e["target"] in members])
    return body


def _step_duties(db: sqlite3.Connection, mission_id: str,
                 nodes: list[tuple[Any, Any, Any]]) -> dict[str, dict[str, Any]]:
    """What each method step is answerable for (2026-09-26 真机：every child's task goal
    is the whole Mission goal, so the graph could not say what a step does).

    The step's name is its method ``local_id`` (``method_child_occurrences.slot_key``);
    its duty is the ``evidence_requirement`` of every ``criterion_links`` entry the
    method contract ties to that step — the same text the Worker is told it owes."""

    slots = {str(occ): str(slot) for occ, slot in db.execute(
        "SELECT occurrence_id, slot_key FROM method_child_occurrences WHERE mission_id=?", (mission_id,))}
    contracts: dict[tuple[str, int], list[dict[str, Any]]] = {}
    duties: dict[str, dict[str, Any]] = {}
    for occurrence, method_id, method_version in nodes:
        slot = slots.get(str(occurrence))
        if slot is None or method_id is None:
            continue
        key = (str(method_id), int(method_version))
        if key not in contracts:
            row = db.execute("SELECT contract_json FROM method_contracts WHERE method_id=? AND method_version=?",
                             key).fetchone()
            try:
                links = json.loads(row[0]).get("composition", {}).get("criterion_links", []) if row else []
            except (ValueError, AttributeError):
                links = []
            contracts[key] = [link for link in links if isinstance(link, dict)]
        evidence = [str(link.get("evidence_requirement") or "")[:600] for link in contracts[key]
                    if link.get("child_step") == slot and link.get("evidence_requirement")]
        duties[str(occurrence)] = {"key": slot[:80], "evidence": evidence[:12]}
    return duties


def read_planning_decisions(service: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    mission_id = _checked(request, set())
    service._require()._mission(mission_id)
    connection = _connect(service)
    try:
        rows = connection.execute(
            "SELECT d.decision_id, d.decision_type, d.status, d.rejection_codes_json, d.created_at, r.base_plan_revision"
            " FROM planning_decisions d JOIN planning_requests r ON r.request_id=d.request_id"
            " WHERE r.mission_id=? ORDER BY d.created_at, d.decision_id LIMIT ?",
            (mission_id, DECISION_LIMIT)).fetchall()
    except sqlite3.Error as error:
        logger.warning("planning decisions read failed for %s: %s", mission_id, error)
        raise OrchestrationRequestError("source_unavailable", "规划记录暂时不可读，请稍后重试") from error
    finally:
        connection.rollback()
        connection.close()
    decisions = []
    for decision_id, decision_type, status, codes, created_at, base_revision in rows:
        try:
            parsed = json.loads(codes) if codes else []
        except ValueError:
            parsed = []
        decisions.append({"decision_id": str(decision_id), "decision_type": None if decision_type is None
                          else str(decision_type), "status": str(status),
                          "rejection_codes": [str(c) for c in parsed][:16] if isinstance(parsed, list) else [],
                          "created_at": created_at, "base_plan_revision": int(base_revision)})
    return {"schema_version": 1, "mission_id": mission_id, "decisions": decisions}


__all__ = ("read_live_graph", "read_planning_decisions")
