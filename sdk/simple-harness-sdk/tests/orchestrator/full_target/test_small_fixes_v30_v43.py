# SPDX-License-Identifier: Apache-2.0
"""2026-10-11 推后清单里核验员的小建议 V30～V42（09 清单第三节），opt.186。

每条一个用例，名字说明修的是什么；改坏检验见各用例说明。
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------- V30 宽限口径一致


def test_v30_both_grace_counters_treat_an_unknown_outcome_as_unanswered():
    """``_planning_attempts`` 与 ``_planner_turn_failure_forgiven`` 读同一个判断：结果不明也是没被听到。

    **Mutation**: ``_planner_turn_failure_forgiven`` 改回只认 ``_turn_failed`` → red."""
    from agent_orchestrator.orchestrator import event_handler as eh
    from agent_orchestrator.orchestrator.orchestrator_domains import planning_feedback

    def rejected(**payload):
        return SimpleNamespace(type="PlanningRejected", payload=payload)

    events = [rejected(reason="provider_outcome_unknown", refusals=[])]
    fake = SimpleNamespace(store=SimpleNamespace(list_events=lambda mission_id: events))
    assert planning_feedback._planning_turn_unanswered(events[0]) is True
    assert eh.Orchestrator._planner_turn_failure_forgiven(fake, "m") is True
    assert eh.Orchestrator._planning_attempts(fake, "m") == 0
    # 一条真正答错的回合两边都不宽限
    wrong = [rejected(reason="format", refusals=[{"code": "SCHEMA_INVALID"}])]
    fake_wrong = SimpleNamespace(store=SimpleNamespace(list_events=lambda mission_id: wrong))
    assert eh.Orchestrator._planner_turn_failure_forgiven(fake_wrong, "m") is False


# ---------------------------------------------------------------- V32 在途判断只读三列


def test_v32_provider_call_facts_read_only_the_liveness_columns():
    """卡死判断只问执行库三列（状态、交出时刻、结清时刻），不再反序列化整行。

    **Mutation**: 改回 ``list_provider_invocations`` → 假执行库没有它，red。"""
    from agent_orchestrator.orchestrator.event_handler import Orchestrator

    rows = [("settled", 10.0, 20.0), ("handed_off", 100.0, None), ("settled", 30.0, 50.0)]
    uow = SimpleNamespace(
        read_agent_binding=lambda agent_id: SimpleNamespace(run_id="run-1"),
        list_provider_invocation_liveness=lambda run_id: tuple(rows),
    )
    fake = SimpleNamespace(
        bridge_for=lambda intent: SimpleNamespace(runtime=SimpleNamespace(uow=uow)),
        _config=SimpleNamespace(provider_call_seconds=900.0),
    )
    intent = SimpleNamespace(agent_id="agent-1")
    assert Orchestrator._provider_call_facts(fake, intent, now=400.0) == (True, 50.0)
    assert Orchestrator._provider_call_facts(fake, intent, now=1_100.0) == (False, 50.0)


# ---------------------------------------------------------------- V34 超额记录可见


def test_v34_the_usage_ledger_reports_overrun_calls_and_tokens():
    """任务用量里带"超出上限的调用"次数与多用的 token——只显示，不拦（可多算不可少算）。

    **Mutation**: 去掉 ``overrun_calls`` 两行 → red。"""
    from agent_orchestrator.storage.store import Store

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE budget_accounts(mission_id TEXT, scope TEXT, reserved_tokens INT, "
        "settled_tokens INT, attempts_created INT, version INT)"
    )
    connection.execute("INSERT INTO budget_accounts VALUES ('m-1','mission',0,500,2,3)")
    connection.execute(
        "CREATE TABLE provider_token_grants(mission_id TEXT, state TEXT, actual_tokens INT, total_upper INT)"
    )
    connection.executemany(
        "INSERT INTO provider_token_grants VALUES (?,?,?,?)",
        [
            ("m-1", "OVERRUN", 1_200, 1_000),
            ("m-1", "OVERRUN", 900, 1_000),
            ("m-1", "SETTLED", 100, 1_000),
            ("m-2", "OVERRUN", 5_000, 1_000),
        ],
    )
    fake = SimpleNamespace(connection=connection, has_table=lambda name: True)
    usage = Store.mission_budget_usage(fake, "m-1")
    assert usage["settled_tokens"] == 500
    assert usage["overrun_calls"] == 2 and usage["overrun_tokens"] == 200  # 多用的只算超出部分
    assert Store.mission_budget_usage(fake, "m-3") is None


# ---------------------------------------------------------------- V35 收尾步骤按槽位取


def test_v35_the_finalizer_is_read_off_the_adopted_children_not_respelled():
    """共享槽位时，收尾步骤的任务号来自采用做法的子绑定（槽位→出现→任务），不是按本实例重新推导的号。

    **Mutation**: 改回 ``derive_id("task", instance, slot)`` → red。"""
    from agent_orchestrator.graph.terminal import finalizer_task_id

    children = (
        SimpleNamespace(slot_key="prepare", occurrence_id="occ-p"),
        SimpleNamespace(slot_key="finish", occurrence_id="occ-shared"),
    )
    draft = SimpleNamespace(
        method_ref=SimpleNamespace(method_id="m", version=1),
        instance_id="inst-1",
        child_bindings=children,
    )
    network = SimpleNamespace(
        root_occurrence_ids=("root",),
        adopted_instance_for=lambda occ: draft,
        adopted_children=lambda occ: children,
        occurrence=lambda occ: SimpleNamespace(
            task_id={"occ-shared": "task-from-other-instance", "occ-p": "task-p"}[occ]
        ),
    )
    semantics = SimpleNamespace(
        get_method=lambda m, v: SimpleNamespace(
            contract=SimpleNamespace(composition=SimpleNamespace(finalizer_step="finish"))
        )
    )
    assert finalizer_task_id(network, semantics, "m-1") == "task-from-other-instance"
    none = SimpleNamespace(
        get_method=lambda m, v: SimpleNamespace(
            contract=SimpleNamespace(composition=SimpleNamespace(finalizer_step=None))
        )
    )
    assert finalizer_task_id(network, none, "m-1") is None


# ---------------------------------------------------------------- V36 血缘与报告同一个终结步


def test_v36_lineage_reports_the_finalizer_the_report_names_or_none():
    """血缘里的 ``terminal_task_id`` 就是报告的收尾步骤；没有收尾步骤就是 None，不再自己推导"最后一个叶"。

    **Mutation**: 改回 ``terminal_task(tasks)`` → 第二段 red。"""
    from agent_orchestrator.contracts import TaskStatus
    from agent_orchestrator.observability.lineage import lineage

    tasks = [
        SimpleNamespace(
            id="t-a", status=TaskStatus.COMPLETED, accepted_result_id="r-a", dependency_ids=()
        ),
        SimpleNamespace(
            id="t-b", status=TaskStatus.COMPLETED, accepted_result_id="r-b", dependency_ids=("t-a",)
        ),
    ]
    store = SimpleNamespace(
        list_tasks=lambda mission_id: tasks,
        get_result=lambda rid: None,
        get_knowledge=lambda kid: None,
        list_claims=lambda rid: [],
    )
    with_finalizer = lineage(store, "m", terminal_task_id="t-a")
    assert (with_finalizer["terminal_task_id"], with_finalizer["terminal_result_id"]) == (
        "t-a",
        "r-a",
    )
    without = lineage(store, "m", terminal_task_id=None)
    assert without["terminal_task_id"] is None and without["terminal_result_id"] is None


# ---------------------------------------------------------------- V38 暂停的步骤不算"正要开工"


def test_v38_a_paused_ready_step_does_not_postpone_the_stop_condition_question():
    """停止条件到了：有放行未开工的步骤才等；暂停的步骤不会开工，不算。

    **Mutation**: 去掉 ``and not task.paused`` → 第二段 red。"""
    from agent_orchestrator.contracts import ContractError, TaskStatus
    from agent_orchestrator.orchestrator import event_handler as eh

    notes: list[str] = []

    def fake(tasks):
        return SimpleNamespace(
            _config=SimpleNamespace(deployment_policy=None, max_planning_attempts=3),
            store=SimpleNamespace(
                iter_events=lambda mission_id: iter(()), list_tasks=lambda mission_id: tasks
            ),
            _note=notes.append,
        )

    mission = SimpleNamespace(id="m", stop_conditions={"x": 1})
    new_mode = SimpleNamespace(
        network=lambda mission_id: (_ for _ in ()).throw(ContractError("unreadable"))
    )
    rules = SimpleNamespace(
        parse_stop_conditions=lambda conditions, policy: True,
        reached_stop_conditions=lambda *a, **k: [{"condition": "x"}],
        knowledge_streak=lambda events: 0,
        result_hashes=lambda store, mission_id, events: {},
    )
    import agent_orchestrator.orchestrator.stop_conditions as stop_mod

    original = {
        name: getattr(stop_mod, name)
        for name in (
            "parse_stop_conditions",
            "reached_stop_conditions",
            "knowledge_streak",
            "result_hashes",
        )
    }
    for name in original:
        setattr(stop_mod, name, getattr(rules, name))
    try:
        ready = [SimpleNamespace(status=TaskStatus.READY, paused=False)]
        assert (
            asyncio.run(eh.Orchestrator._stop_conditions_reached(fake(ready), mission, new_mode))
            is False
        )
        assert notes == []  # 有步骤正要开工：没往下问
        paused = [SimpleNamespace(status=TaskStatus.READY, paused=True)]
        assert (
            asyncio.run(eh.Orchestrator._stop_conditions_reached(fake(paused), mission, new_mode))
            is False
        )
        assert notes and "unreadable" in notes[0]  # 走到了读计划那一步：暂停的步骤没挡住
    finally:
        for name, value in original.items():
            setattr(stop_mod, name, value)


# ---------------------------------------------------------------- V39 续读被拒说清要带什么


def test_v39_a_knowledge_read_continuation_without_the_digest_is_told_what_to_send(monkeypatch):
    """``knowledge_read`` 翻页同样说清：带第一次读返回的 sha256；sha256 对不上说"从 0 重读"。"""
    from agent_orchestrator.context import knowledge_tools as kt

    text = "x" * 5_000
    row = {
        "id": "k-1",
        "layer": kt.VERIFIED_LAYER,
        "_text": text,
        "version": 1,
        "summary_sha256": "0" * 64,
        "_record": None,
        "ref": "k-1@1",
        "basis": {},
    }
    monkeypatch.setattr(kt, "_catalogue", lambda store, mission_id: [row])
    digest = kt._digest(text)
    with pytest.raises(ValueError) as refused:
        kt.read_knowledge_tool(
            SimpleNamespace(), "m", "knowledge_read", {"id": "k-1", "offset": 2048}
        )
    assert "knowledge_read paging needs expected_sha256" in str(refused.value) and digest in str(
        refused.value
    )
    with pytest.raises(ValueError) as changed:
        kt.read_knowledge_tool(
            SimpleNamespace(),
            "m",
            "knowledge_read",
            {"id": "k-1", "offset": 2048, "expected_sha256": "1" * 64},
        )
    assert "restart reading from offset 0" in str(changed.value) and digest in str(changed.value)


# ---------------------------------------------------------------- V40 账户范围读表不猜名


def test_v40_the_account_scope_is_read_from_the_accounts_table_not_the_id_prefix():
    """一个账户是步骤账户还是任务总账，看账户表的 scope 列；名字怎么拼不算数。

    **Mutation**: 改回 ``account_id.startswith("budget:task-")`` → red（名字像步骤账户、表里却是任务总账）。"""
    from agent_orchestrator.orchestrator.assurance_tick import AssuranceTick

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE budget_accounts(account_id TEXT, scope TEXT)")
    connection.executemany(
        "INSERT INTO budget_accounts VALUES (?,?)",
        [("budget:task-9", "mission"), ("acct-step", "task")],
    )
    connection.execute(
        "CREATE TABLE budget_reservations(subject_id TEXT, account_id TEXT, state TEXT, mission_id TEXT)"
    )
    connection.executemany(
        "INSERT INTO budget_reservations VALUES (?,?,?,?)",
        [
            ("task-1:attempt-1", "acct-other", "RESERVED", "m-1"),  # 任务下别的步骤还在跑
            ("task-9:attempt-1", "budget:task-9", "SETTLED", "m-1"),
        ],
    )
    store = SimpleNamespace(
        connection=connection,
        get_intent_for_subject=lambda s: (
            SimpleNamespace(state="SUBMITTED") if s == "task-1:attempt-1" else None
        ),
        get_attempt=lambda s: None,
    )
    tick = SimpleNamespace(store=store)
    tick._account_scope = lambda account_id: AssuranceTick._account_scope(tick, account_id)
    assert AssuranceTick._account_scope(tick, "budget:task-9") == "mission"
    assert AssuranceTick._account_scope(tick, "acct-step") == "task"
    assert AssuranceTick._account_scope(tick, "nobody") is None
    # 名字像步骤账户、表里是任务总账：按任务范围看，别的步骤还在跑 → 还可能变
    assert AssuranceTick._budget_can_still_change(tick, "budget:task-9", "m-1") is True
    # 表里是步骤账户：只看它自己的预留，没有 → 不会变
    assert AssuranceTick._budget_can_still_change(tick, "acct-step", "m-1") is False
