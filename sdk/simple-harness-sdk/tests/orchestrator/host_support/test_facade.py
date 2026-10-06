# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""S2 · P3.1 external control facade (SB-1 … SB-5; user's Phase3 plan §3.3–§3.4).

``MissionControlV1(orchestrator, tenant_id=…, principal=…)`` is the one surface a product
(the Host) talks to: strict request fields, persistent create receipts, ownership on every
read and write (a foreign object is ``not_found`` — its existence is not revealed), a
snapshot whose ``through_seq`` comes from the same read as the snapshot, gap-free event
pages and content-addressed artifact reads.

Draft (moved into tests/orchestrator/host_support/ when S2 starts).
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from agent_orchestrator.api.facade import FacadeError, MissionControlV1
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.testing.fixtures import RoleScriptedProvider
from agent_orchestrator.testing.product_world import product_world

TOOLS3 = ("workspace_read_file", "workspace_write_file", "workspace_list")
OFF = DeploymentPolicy(allowed_tools=TOOLS3, local_code_execution=False)
ME = Principal("local-user:me", "我")


def _provider() -> RoleScriptedProvider:
    # 门面测试只建任务、读快照、取消，不驱动规划（删旧平面模式 第三刀：原来是平面规划器
    # 加执行者/审阅员的整套脚本）。
    return RoleScriptedProvider({})


def _command(key: str, **overrides):
    command = {
        "goal": "写一份 NOTES.md，列出三个要点",
        "success_criteria": ["file:NOTES.md"],
        "idempotency_key": key,
        "budget": {"max_tokens": 200_000, "max_attempts": 4},
    }
    command.update(overrides)
    return command


def _with(tmp_path, body, provider=None):  # type: ignore[no-untyped-def]
    # 编排器只接受部署给的原生执行池（A′ 第 4 步删旧执行池），夹具因此走产品同形的装配：
    # ``product_world`` 用的就是产品那一份部署组装 + 原生执行池。``auto=False``：门面测试只建
    # 任务、读快照、取消，不让部署职责代签确认/授权去推进规划。门面就是部署绑定的那一个
    # （``world.control``），租户/主体照旧是 local/我。
    async def run():
        async with product_world(
            Path(tmp_path) / "evidence", provider or _provider(), auto=False,
            tenant_id="local", principal=ME, max_concurrency=1, deployment_policy=OFF,
        ) as world:
            orchestrator = world.loop
            result = body(orchestrator, world.control)
            if asyncio.iscoroutine(result):
                result = await result
            return result

    return asyncio.run(run())


# ------------------------------------------------------------------ SB-1
def test_open_fields_round_trip(tmp_path):
    def body(orchestrator, control):
        receipt = control.create(
            _command(
                "k-fields",
                untrusted_sources=["docs/"],
                workspace_seed={"docs/brief.md": "资料"},
                stop_conditions=["verification_passed", "budget_exhausted"],
            )
        )
        return control.snapshot(receipt["mission_id"])["snapshot"]["mission"]

    mission = _with(tmp_path, body)
    report = mission["final_report"]
    assert report["untrusted_sources"] == ["docs/"]
    assert report["workspace_seed"] == {"docs/brief.md": "资料"}
    assert mission["stop_conditions"] == ["verification_passed", "budget_exhausted"]
    assert mission["budget"]["max_tokens"] == 200_000 and mission["budget"]["max_attempts"] == 4


@pytest.mark.parametrize("name", ["success_criteria", "stop_conditions", "untrusted_sources"])
def test_a_string_is_not_a_list_of_strings(tmp_path, name):
    """Review round 2 P2-1: a string would split into characters."""

    def body(orchestrator, control):
        with pytest.raises(FacadeError) as refused:
            control.create(_command("k-str", **{name: "file:NOTES.md"}))
        return refused.value.code, len(orchestrator.store.list_missions())

    assert _with(tmp_path, body) == ("invalid_request", 0)


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"surprise": 1}, "surprise"),
        # removed on 2026-10-02 with conflict Tasks and the final synthesis Task
        ({"conflict_reserve_tokens": 12_000}, "conflict_reserve_tokens"),
        ({"synthesis": {"goal": "合成"}}, "synthesis"),
        ({"allowed_tools": list(TOOLS3)}, "allowed_tools"),
        ({"risk_level": "production"}, "risk_level"),
        ({"task_kind": "research"}, "task_kind"),
        # money is not a budget dimension: an unknown budget field, refused by name
        ({"budget": {"max_tokens": 1000, "max_cost_micros": 5}}, "budget.max_cost_micros"),
    ],
)
def test_unknown_and_closed_fields_are_refused_by_name(tmp_path, overrides, field):
    def body(orchestrator, control):
        with pytest.raises(FacadeError) as refused:
            control.create(_command("k-closed", **overrides))
        return refused.value, len(orchestrator.store.list_missions())

    error, count = _with(tmp_path, body)
    assert error.code == "invalid_request" and field in str(error)
    assert count == 0


# ------------------------------------------------------------------ SB-2
def test_the_create_receipt_is_persistent_and_a_different_body_conflicts(tmp_path):
    def body(orchestrator, control):
        first = control.create(_command("k-receipt"))
        again = control.create(_command("k-receipt"))
        with pytest.raises(FacadeError) as conflict:
            control.create(_command("k-receipt", goal="另一件事"))
        return first, again, conflict.value.code, len(orchestrator.store.list_missions())

    first, again, code, count = _with(tmp_path, body)
    assert first["created"] is True and again["created"] is False
    assert again["mission_id"] == first["mission_id"] and again["spec_hash"] == first["spec_hash"]
    assert code == "conflict" and count == 1


def _accounts(tmp_path) -> int:
    with sqlite3.connect(Path(tmp_path) / "evidence" / "orchestrator.db") as db:
        return int(db.execute("SELECT COUNT(*) FROM budget_accounts").fetchone()[0])


def test_a_repeated_create_reserves_nothing_twice(tmp_path):
    def body(orchestrator, control):
        control.create(_command("k-once"))
        before = _accounts(tmp_path)
        control.create(_command("k-once"))
        return before, _accounts(tmp_path)

    before, after = _with(tmp_path, body)
    assert before == after


# ------------------------------------------------------------------ SB-3
def test_a_foreign_tenant_sees_nothing(tmp_path):
    def body(orchestrator, control):
        mission_id = control.create(_command("k-mine"))["mission_id"]
        stranger = MissionControlV1(
            orchestrator, tenant_id="other", principal=Principal("other:x", "x")
        )
        codes = []
        for call in (
            lambda: stranger.snapshot(mission_id),
            lambda: stranger.events(mission_id, after_seq=0),
            lambda: stranger.cancel(mission_id),
            lambda: stranger.comment(mission_id, "hi"),
            lambda: stranger.snapshot("mission-does-not-exist"),
        ):
            with pytest.raises(FacadeError) as refused:
                call()
            codes.append((refused.value.code, str(refused.value)))
        return mission_id, codes

    mission_id, codes = _with(tmp_path, body)
    assert all(code == "not_found" for code, _ in codes)
    # a foreign id and a missing id read the same — existence is not revealed
    assert (
        len(
            {
                message.replace(mission_id, "<id>").replace("mission-does-not-exist", "<id>")
                for _, message in codes
            }
        )
        == 1
    )


def test_cancel_is_idempotent_and_leaves_an_ended_mission_alone(tmp_path):
    async def body(orchestrator, control):
        running = control.create(_command("k-cancel"))["mission_id"]
        first = control.cancel(running)
        second = control.cancel(running)
        done = control.create(_command("k-done"))["mission_id"]
        orchestrator_provider_note = None
        return first, second, done, orchestrator_provider_note

    first, second, _done, _ = _with(tmp_path, body)
    assert first == {**first, "status": "CANCELLED", "changed": True}
    assert second["status"] == "CANCELLED" and second["changed"] is False


# ------------------------------------------------------------------ SB-4
def test_the_snapshot_cursor_comes_from_the_same_read(tmp_path, monkeypatch):
    """Review round 2 P1-C: an event committed by another connection *between* the
    snapshot's SELECTs is not counted in its ``through_seq`` (it would be without the
    read view)."""

    def body(orchestrator, control):
        mission_id = control.create(_command("k-race"))["mission_id"]
        store = orchestrator.store
        original = store.list_tasks
        path = Path(tmp_path) / "evidence" / "orchestrator.db"
        inserted: list[int] = []
        before = store.mission_budget_usage(mission_id)

        def list_tasks_then_write(mid):  # type: ignore[no-untyped-def]
            rows = original(mid)
            if not inserted:
                with sqlite3.connect(path) as other:
                    cursor = other.execute(
                        "INSERT INTO events(event_id, idempotency_key, type, trace_id, mission_id,"
                        " task_id, attempt_id, actor_type, actor_id, payload_json, created_at,"
                        " schema_version) VALUES (?, ?, 'HumanCommentAdded', 'trace', ?, NULL,"
                        " NULL, 'user', 'probe', '{}', 0, 1)",
                        ("event-race-probe", "race-probe", mid),
                    )
                    inserted.append(int(cursor.lastrowid))
                    other.execute(
                        "UPDATE budget_accounts SET reserved_tokens = reserved_tokens + 77 "
                        "WHERE mission_id = ? AND scope = 'mission'", (mid,),
                    )
            return rows

        monkeypatch.setattr(store, "list_tasks", list_tasks_then_write)
        view = control.snapshot(mission_id)
        assert view["snapshot"]["budget_usage"] == before
        assert store.mission_budget_usage(mission_id)["reserved_tokens"] == before["reserved_tokens"] + 77
        return view["through_seq"], inserted[0]

    through, inserted = _with(tmp_path, body)
    assert through < inserted


def test_event_page_size_is_bounded(tmp_path):
    def body(orchestrator, control):
        mission_id = control.create(_command("k-bound"))["mission_id"]
        with pytest.raises(FacadeError) as refused:
            control.events(mission_id, after_seq=0, limit=10_000)
        return refused.value.code

    assert _with(tmp_path, body) == "invalid_request"
