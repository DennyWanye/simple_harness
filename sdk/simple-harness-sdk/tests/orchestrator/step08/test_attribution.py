# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 8 · slice B (plan D8-4'; S8-01, S8-05): contribution attribution — from the
final products to the Tasks, Attempts, Agents, roles and models that produced them,
with exploration spending listed and the usage reconciled row by row."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from fixtures_provider import RoleScriptedProvider, critic_step, proposal_step

from agent_orchestrator.__main__ import main
from agent_orchestrator.contracts import Budget, MissionStatus
from agent_orchestrator.observability.replay import library_copy
from agent_orchestrator.observability.traces import attribution
from agent_orchestrator.orchestrator.commit_service import MissionSpec
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.storage.store import Store
from agent_orchestrator.testing.fixtures import (
    DEMO_BAD,
    DEMO_PROPOSAL,
    DEMO_SEED,
    demo_worker_script,
)


def _demo(tmp_path, scenario):
    evidence = Path(tmp_path) / scenario
    assert (
        main(
            [
                "demo",
                "--scenario",
                scenario,
                "--provider",
                "fixtures",
                "--evidence-dir",
                str(evidence),
                "--idempotency-key",
                f"a-{scenario}",
            ]
        )
        == 0
    )
    report = json.loads((evidence / "test-report.json").read_text(encoding="utf-8"))
    return evidence, report["mission_id"]


def _read(evidence, mission_id):
    store = Store.open_readonly(
        library_copy(evidence / "orchestrator.db", evidence.parent / f"{evidence.name}-copy")
    )
    try:
        return attribution(store, mission_id), store.snapshot(mission_id)
    finally:
        store.close()


def _reconciles(report):
    cost = report["cost"]
    assert cost["unclassified"]["tokens"] == 0 and cost["unclassified"]["subjects"] == []
    services = sum(v["tokens"] for v in cost["services"].values())
    assert (
        cost["success_path"]["tokens"] + cost["exploration"]["tokens"] + services
        == cost["total"]["tokens"]
        > 0
    )
    assert cost["reconciled"] is True
    assert (
        cost["total"]["cost_micros"] is None and "unpriced" in cost["total"]["cost_note"]
    )  # never 0


# ------------------------------------------------------------------ S8-01
def test_s8_01_a_static_dag_names_who_made_every_final_product(tmp_path, capsys):
    evidence, mission_id = _demo(tmp_path, "static-dag")
    capsys.readouterr()
    report, snapshot = _read(evidence, mission_id)
    assert report["success_path"] and report["breaks"] == []
    assert {p["path"] for p in report["final_products"]} >= {
        "textkit/__init__.py",
        "textkit/slug.py",
        "textkit/count.py",
        "DELIVERY.md",
    }
    for product in report["final_products"]:
        assert (
            product["attempt_id"]
            and product["agent_id"]
            and product["role"]
            and product["model"]
            and product["prompt_version"]
        )
        assert {"format_check", "rule_check"} <= {
            layer["layer"] for layer in product["verified_by"]
        }
    assert len(report["path_tasks"]) == len(
        snapshot["tasks"]
    )  # every Task of this plan fed the result
    assert all(a["on_success_path"] for a in report["attempts"])
    assert set(report["cost"]["services"]) == {"planner"}
    _reconciles(report)
    written = json.loads((evidence / "attribution.json").read_text(encoding="utf-8"))
    assert written["cost"]["total"] == report["cost"]["total"]


def test_s8_01_an_approved_action_and_its_people_are_on_the_path(tmp_path, capsys):
    evidence, mission_id = _demo(tmp_path, "approval-action")
    capsys.readouterr()
    report, _snapshot = _read(evidence, mission_id)
    [action] = report["actions"]
    assert (
        action["approved_by"] == ["demo-operator"]
        and action["receipt_hash"]
        and action["decision_receipts"]
    )
    assert report["human"]["decisions"] == 1 and report["human"]["wait_seconds"] >= 0
    assert (
        report["action_reservations"]
        and report["action_reservations"][0]["settled_tool_calls"] == 1
    )
    _reconciles(report)


def test_s8_01_a_failed_mission_has_no_success_path_and_every_token_is_exploration(tmp_path):
    proposal = {**DEMO_PROPOSAL, "budget": {"max_tokens": 50_000, "max_attempts": 2}}
    provider = RoleScriptedProvider(
        {
            "planner": [proposal_step(proposal)],
            "worker": demo_worker_script(DEMO_BAD) + demo_worker_script(DEMO_BAD),
            "critic": [critic_step(verdict="PASS", criteria_met=True)] * 2,
        }
    )
    config = OrchestratorConfig(
        evidence_root=Path(tmp_path) / "evidence", max_concurrency=1, test_timeout_seconds=60
    )
    spec = MissionSpec(
        goal="实现 parse_kv",
        success_criteria=("pytest:tests/test_parse_kv.py",),
        tenant_id="t-8",
        idempotency_key="fail",
        allowed_tools=(
            "workspace_read_file",
            "workspace_write_file",
            "workspace_list",
            "run_tests",
        ),
        budget=Budget(max_tokens=300_000, max_attempts=4),
        workspace_seed=DEMO_SEED,
        orchestration_semantics_version="legacy",
    )

    async def case():
        async with Orchestrator(config, provider) as orchestrator:
            mission = await orchestrator.submit_mission(spec)
            await orchestrator.run()
            assert orchestrator.store.get_mission(mission.id).status is MissionStatus.FAILED
            return attribution(orchestrator.store, mission.id)

    report = asyncio.run(case())
    assert report["success_path"] is False and report["final_products"] == []
    assert [a["exploration_reason"] for a in report["attempts"]] == ["attempt_retry_wait"] * 2
    assert report["cost"]["success_path"]["tokens"] == 0
    # Failed deterministic pytest prevents both Critic calls. Attribution must
    # report their actual zero cost, while retaining all failed Worker spending.
    assert all(a["verification"]["tokens"] == 0 for a in report["attempts"])
    assert provider.by_role.get("critic", 0) == 0
    _reconciles(report)


# ------------------------------------------------------------------ S8-05
def test_s8_05_a_missing_record_is_a_break_never_a_bridge(tmp_path, capsys):
    evidence, mission_id = _demo(tmp_path, "static-dag")
    capsys.readouterr()
    damaged = library_copy(evidence / "orchestrator.db", Path(tmp_path) / "damaged")
    connection = sqlite3.connect(damaged)
    result_id, task_id = connection.execute(
        "SELECT json_extract(json, '$.accepted_result_id'), task_id FROM tasks WHERE mission_id = ? AND json_extract(json, '$.accepted_result_id') IS NOT NULL LIMIT 1",
        (mission_id,),
    ).fetchone()
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("DELETE FROM results WHERE result_id = ?", (result_id,))
    connection.commit()
    connection.close()
    store = Store.open_readonly(damaged)
    try:
        report = attribution(store, mission_id)
    finally:
        store.close()
    assert {"missing": "result", "id": result_id, "task_id": task_id} in report["breaks"]
    broken = [p for p in report["final_products"] if p["task_id"] == task_id]
    assert all(
        p["attempt_id"] is None and p["agent_id"] is None for p in broken
    )  # no invented producer


# ------------------------------------------------------------------ code review round 1
def test_review_p1_4_reconciliation_fails_on_a_stray_subject_or_a_ledger_mismatch(tmp_path, capsys):
    evidence, mission_id = _demo(tmp_path, "static-dag")
    capsys.readouterr()

    def damaged(name, sql, params):
        copy = library_copy(evidence / "orchestrator.db", Path(tmp_path) / name)
        connection = sqlite3.connect(copy)
        connection.execute(sql, params)
        connection.commit()
        connection.close()
        store = Store.open_readonly(copy)
        try:
            return attribution(store, mission_id)["cost"]
        finally:
            store.close()

    clean, _snapshot = _read(evidence, mission_id)
    assert clean["cost"]["ledger"]["mismatched_subjects"] == []
    assert clean["cost"]["ledger"]["settled_tokens"] == clean["cost"]["total"]["tokens"]
    stray = damaged(
        "stray",
        "INSERT INTO imported_usage (usage_ref, subject_id, mission_id, input_tokens,"
        " output_tokens, cost_micros, unpriced, unknown, imported_at)"
        " VALUES (?, ?, ?, 7, 0, NULL, 1, 0, 0)",
        ("stray-1", "somebody-else", mission_id),
    )
    assert stray["reconciled"] is False and stray["unclassified"]["subjects"] == ["somebody-else"]
    skewed = damaged(
        "skewed",
        "UPDATE budget_reservations SET settled_tokens = settled_tokens + 1"
        " WHERE reservation_id = (SELECT MIN(reservation_id) FROM budget_reservations"
        " WHERE mission_id = ? AND subject_id NOT LIKE 'action:%')",
        (mission_id,),
    )
    assert skewed["reconciled"] is False and len(skewed["ledger"]["mismatched_subjects"]) == 1


# ------------------------------------------------------------------ code re-review (round 2)
def test_re_review_a_finished_mission_with_usage_left_unsettled_is_not_reconciled(tmp_path, capsys):
    evidence, mission_id = _demo(tmp_path, "static-dag")
    capsys.readouterr()
    copy = library_copy(evidence / "orchestrator.db", Path(tmp_path) / "held")
    connection = sqlite3.connect(copy)
    connection.execute(
        "UPDATE budget_reservations SET state = 'HELD' WHERE reservation_id = ("
        "SELECT reservation_id FROM budget_reservations WHERE mission_id = ?"
        " AND subject_id NOT LIKE 'action:%' ORDER BY settled_tokens DESC LIMIT 1)",
        (mission_id,),
    )
    connection.commit()
    connection.close()
    store = Store.open_readonly(copy)
    try:
        cost = attribution(store, mission_id)["cost"]
    finally:
        store.close()
    assert cost["ledger"]["unsettled_usage_tokens"] > 0 and cost["reconciled"] is False
