# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 7 · slice A (D7-2 / D7-3 / D7-4 / D7-6): the action ledger, the risk policy, the
approvals bound to one action version, and the test configuration service.

HTN 补齐阶段 A′（分诊裁决③ D′）：台账用例跑在产品同形世界上（``helpers_step07.ledger_world``：
主循环真跑到第一版计划提交、执行者被扣住），之后只经台账自己的写入口记账。

* 删除（10-02 定"两人审批暂不做"，分诊裁决⑥）：``l3_needs_two_independent_grants_from_different_people_by_default``、
  ``without_the_distinct_people_rule_two_distinct_receipts_of_one_person_count``；
  ``every_recorded_grant_counts_and_only_a_granted_request_can_be_revoked`` 也是两人各批一次的 L3 用例，
  其中"只有已批准的请求能撤回"并入 ``test_a_grant_counts_once_per_nonce...``。
* 原来三条靠直接改写台账行（``_force``）造出"已交接 / 已成功 / 已失败 / 结果不明"的用例，改为
  代表用例 3 的变体 :func:`test_the_ledger_never_rewrites_what_reality_did`：这些状态由真实的
  交接与发布服务（慢、拒绝、连接中断）造出来。
* 原"L0 不用审批"那一半：产品能挂效果的连接器只有文件发布（L2），L0 动作在产品上提不出来，
  只留风险等级的纯函数断言（``test_levels_follow...``）。
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import PurePosixPath

import pytest
from helpers_step07 import (
    ALICE,
    BOB,
    ENABLED,
    TARGET,
    candidate,
    ledger_world,
    operation_world,
    run_until,
    until_pending,
)

from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.governance.policies import DeploymentPolicy, action_decision
from agent_orchestrator.orchestrator.action_commits import ActionCommitError, CandidateRejected
from agent_orchestrator.runtime.connectors import (
    ConnectorRejected,
    ConnectorTransportError,
    OperationSpec,
    PaymentConnectorStub,
    TestConfigService,
    params_hash,
)
from agent_orchestrator.runtime.connectors_publish import _name_for
from agent_orchestrator.runtime.planning_operations import (
    OperationEffect,
    SourceUnavailable,
    StoreOperationReader,
    build_operation_snapshot,
    operation_gate,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


# ------------------------------------------------------------------ D7-3 policy (E)
def test_levels_follow_the_connector_declaration_and_only_rise_with_overrides(tmp_path):
    config = TestConfigService(tmp_path / "c.json")
    plain = DeploymentPolicy(enabled_connectors=("test_config",))
    assert DeploymentPolicy().enabled_connectors == ()  # D7-3': the switch is off by default
    assert action_decision(DeploymentPolicy(), config, "set").refused == "connector_not_enabled"
    assert action_decision(plain, config, "read").to_json() == {
        "level": "L0",
        "required_approvals": 0,
        "refused": None,
    }
    assert action_decision(plain, config, "set").required_approvals == 1
    assert action_decision(plain, config, "delete").to_json() == {
        "level": "L3",
        "required_approvals": 2,
        "refused": None,
    }
    raised = DeploymentPolicy(
        enabled_connectors=("test_config",),
        level_overrides=(("test_config.set", "L3"), ("test_config.delete", "L0")),
    )
    assert action_decision(raised, config, "set").level == "L3"  # raised
    assert action_decision(raised, config, "delete").level == "L3"  # an override never lowers
    assert action_decision(plain, config, "rename").refused == "unknown_operation"
    assert action_decision(plain, PaymentConnectorStub(), "pay").refused == "connector_not_enabled"
    enabled = DeploymentPolicy(enabled_connectors=("test_config", "payment"))
    assert (
        action_decision(enabled, PaymentConnectorStub(), "pay").refused
        == "connector_without_idempotency_or_reconciliation"
    )
    capped = DeploymentPolicy(enabled_connectors=("test_config",), max_action_level="L2")
    assert action_decision(capped, config, "delete").refused == "above_deployment_ceiling:L2"
    appender = TestConfigService(
        tmp_path / "e.json",
        operations={"append": OperationSpec("append", "L1", ("value",), kind="event")},
    )
    assert action_decision(plain, appender, "append").refused == "event_operation_not_supported"


# ------------------------------------------------------------------ D7-6 test service (E)
def test_the_test_service_applies_a_key_once_and_can_be_asked_what_happened(tmp_path):
    service = TestConfigService(tmp_path / "config.json")
    first = service.execute("set", "feature_flags.new_ui", {"value": "on"}, idempotency_key="k-1")
    again = service.execute("set", "feature_flags.new_ui", {"value": "on"}, idempotency_key="k-1")
    assert first.receipt_hash == again.receipt_hash and first.applied and first.after == "on"
    assert service.state()["applied_count"] == 1 and service.state()["config"] == {
        "feature_flags.new_ui": "on"
    }
    assert (
        service.lookup("k-1").receipt_hash == first.receipt_hash and service.lookup("k-2") is None
    )
    assert first.params_hash == params_hash({"value": "on"})
    service.lose_receipt_after_apply = 1
    with pytest.raises(ConnectorTransportError):
        service.execute("set", "feature_flags.dark", {"value": "on"}, idempotency_key="k-3")
    assert (
        service.lookup("k-3") is not None and service.state()["applied_count"] == 2
    )  # applied, receipt lost
    service.reject_next = 1
    with pytest.raises(ConnectorRejected):
        service.execute("set", "feature_flags.x", {"value": "on"}, idempotency_key="k-4")
    assert (
        service.lookup("k-4") is None and service.state()["applied_count"] == 2
    )  # refused: not applied
    read = service.execute("read", "feature_flags.new_ui", {}, idempotency_key="k-5")
    assert (
        read.applied is False and read.after == "on" and service.lookup("k-5") is None
    )  # reads are not ledgered


# ------------------------------------------------------------------ D7-2 ledger (D′)
def test_an_l2_candidate_waits_for_one_approval_bound_to_its_hashes(tmp_path):
    async def case():
        async with ledger_world(tmp_path) as w:
            action = w.propose(candidate())
            assert (action["state"], action["level"], action["version"]) == ("AWAITING_APPROVAL", "L2", 1)
            assert action["idempotency_key"] == f"{action['action_id']}:v1"
            assert action["params_hash"] == params_hash(candidate()["params"])
            assert action["artifact_hash"] == HASH_A and action["task_id"] == w.leaf.id
            request = w.store.get_approval(action["approval_request_id"])
            assert (request["state"], request["required_count"], request["kind"]) == ("PENDING", 1, "action")
            assert request["binding"] == {
                "mission_id": w.mission_id,
                "task_id": w.leaf.id,
                "action_id": action["action_id"],
                "version": 1,
                "params_hash": action["params_hash"],
                "artifact_hash": HASH_A,
            }
            assert w.events("ActionProposed") and w.events("ApprovalRequested")
            assert w.publish_ledger() == [] and w.published_files() == []  # nothing touched the service

    asyncio.run(case())


def test_the_same_candidate_twice_is_one_version_and_a_changed_one_supersedes_it(tmp_path):
    async def case():
        async with ledger_world(tmp_path) as w:
            first = w.propose(candidate())
            again = w.propose(candidate())
            assert again["action_key"] == first["action_key"]  # idempotent: same params, same artifact
            assert len(w.store.list_approvals(w.mission_id)) == 1
            changed = w.propose(candidate(value="beta"), artifact_hash=HASH_B, result="result-2")
            assert changed["action_id"] == first["action_id"] and changed["version"] == 2  # same business action
            old = w.store.get_action(first["action_key"])
            assert old["state"] == "SUPERSEDED" and old["superseded_by"] == changed["action_key"]
            assert w.store.get_approval(first["approval_request_id"])["state"] == "SUPERSEDED"
            assert w.store.get_approval(changed["approval_request_id"])["state"] == "PENDING"
            assert len(w.events("ApprovalSuperseded")) == 1

    asyncio.run(case())


def test_policy_scope_and_schema_refusals_are_checked_before_anything_is_written(tmp_path):
    async def case():
        async with ledger_world(tmp_path) as w:
            before = w.store.connection.total_changes
            refusals = {
                "payment": {"connector": "payment", "operation": "pay", "target": "acct-1",
                            "params": {"amount": 5}, "reason": "x"},
                "nowhere": {"connector": "nowhere", "operation": "set", "target": "x", "params": {}, "reason": "x"},
            }
            for cand in refusals.values():
                with pytest.raises(CandidateRejected) as refused:
                    w.propose(cand)
                assert refused.value.reason == "connector_not_enabled"
            with pytest.raises(CandidateRejected) as scope:  # D7-3': outside the Mission's action criteria
                w.propose(candidate(target="reports/other.md"))
            assert scope.value.reason == "action_out_of_scope"
            for smuggled in ({"approved": True}, {"level": "L0"}, {"idempotency_key": "k"}):  # S7-08
                with pytest.raises(CandidateRejected) as schema:
                    w.propose({**candidate(), **smuggled})
                assert schema.value.reason == "invalid_candidate"
            assert w.store.connection.total_changes == before  # nothing was written
            assert w.store.list_actions(w.mission_id) == [] and not w.events("ActionRefused")
            # the target is normalised by the connector before it is compared and keyed
            spaced = w.propose(candidate(target="./reports//weekly.md"))
            plain = w.propose(candidate())
            assert spaced["action_key"] == plain["action_key"] and spaced["target"] == TARGET
            assert w.publish_ledger() == []

    asyncio.run(case())


def test_a_decision_needs_an_active_mission_and_an_ending_mission_cancels_open_work(tmp_path):
    async def case():
        async with ledger_world(tmp_path) as w:
            open_action = w.propose(candidate(target="a.md"))
            granted = w.propose(candidate(target="b.md"))
            w.service.decide_approval(granted["approval_request_id"], principal=ALICE, decision="grant",
                                      nonce="n-b", deployment=ENABLED)
            cancelled = w.service.cancel_open_actions(w.mission_id, reason="mission_cancelled")
            assert sorted(a["action_key"] for a in cancelled) == sorted(
                [open_action["action_key"], granted["action_key"]])
            assert w.store.get_approval(open_action["approval_request_id"])["state"] == "CANCELLED"
            with pytest.raises(ActionCommitError):  # a closed request takes no decision
                w.service.decide_approval(open_action["approval_request_id"], principal=ALICE,
                                          decision="grant", nonce="n-a", deployment=ENABLED)
            late = w.propose(candidate(target="c.md"))
            assert w.control.cancel(w.mission_id)["changed"] is True  # the person cancels the Mission
            with pytest.raises(ActionCommitError):  # D7-4': no grant on a Mission that ended
                w.service.decide_approval(late["approval_request_id"], principal=ALICE, decision="grant",
                                          nonce="n-c", deployment=ENABLED)
            with pytest.raises(CandidateRejected) as ended:
                w.propose(candidate(target="a.md", value="x"), artifact_hash=HASH_B)
            assert ended.value.reason == "mission_not_active"

    asyncio.run(case())


# ------------------------------------------------------------------ D7-4 approvals (D′)
def test_a_grant_counts_once_per_nonce_and_only_a_human_principal_decides(tmp_path):
    async def case():
        async with ledger_world(tmp_path) as w:
            action = w.propose(candidate())
            request_id = action["approval_request_id"]
            with pytest.raises(ValueError):
                Principal("model", kind="agent")
            with pytest.raises(ActionCommitError):  # review P2-2: a PENDING request is rejected, not revoked
                w.service.revoke_approval(request_id, principal=BOB, reason="还没批就撤")
            granted, receipt = w.service.decide_approval(
                request_id, principal=ALICE, decision="grant", nonce="n-1", deployment=ENABLED)
            replay, receipt_again = w.service.decide_approval(
                request_id, principal=ALICE, decision="grant", nonce="n-1", deployment=ENABLED)
            assert granted["state"] == "GRANTED" and receipt == receipt_again and len(receipt) == 64
            assert len(w.store.list_decisions(request_id)) == 1  # the replayed receipt is not counted twice
            assert w.store.get_action(action["action_key"])["state"] == "APPROVED"
            [grant] = w.events("ApprovalGranted")
            assert grant["counted"] is True
            assert [d["principal_id"] for d in w.store.list_decisions(request_id)] == ["alice"]

    asyncio.run(case())


def test_reject_revoke_and_expiry_each_close_the_request_without_success(tmp_path):
    """Short real approval lifetimes (the Store clock is the wall clock in the product world)."""

    async def case():
        async with ledger_world(tmp_path) as w:
            short = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2",
                                     approval_ttl_seconds=0.6)
            a = w.propose(candidate(target="a.md"), deployment=short)
            b = w.propose(candidate(target="b.md"), deployment=short)
            c = w.propose(candidate(target="c.md"), deployment=short)
            d = w.propose(candidate(), deployment=short)
            rejected, _ = w.service.decide_approval(a["approval_request_id"], principal=ALICE, decision="reject",
                                                    nonce="n-a", deployment=short, reason="不同意")
            assert rejected["state"] == "REJECTED"
            assert w.store.get_action(a["action_key"])["state"] == "REJECTED"
            w.service.decide_approval(b["approval_request_id"], principal=ALICE, decision="grant",
                                      nonce="n-b", deployment=short)
            revoked = w.service.revoke_approval(b["approval_request_id"], principal=BOB, reason="撤回")
            assert revoked["state"] == "REVOKED"
            assert w.store.get_action(b["action_key"])["state"] == "REVOKED"
            await asyncio.sleep(0.7)
            # review P2-3: an expiry found by a decision is committed, not rolled back with the
            # refusal; the decision sweeps every request that is due (c as well as d)
            with pytest.raises(ActionCommitError):
                w.service.decide_approval(d["approval_request_id"], principal=ALICE, decision="grant",
                                          nonce="n-d", deployment=short)
            for due in (c, d):
                assert w.store.get_approval(due["approval_request_id"])["state"] == "EXPIRED"
                assert w.store.get_action(due["action_key"])["state"] == "EXPIRED"
            assert w.service.expire_approvals(w.mission_id) == []  # nothing left to expire
            with pytest.raises(ActionCommitError):  # a closed request takes no more decisions
                w.service.decide_approval(a["approval_request_id"], principal=BOB, decision="grant",
                                          nonce="n-late", deployment=short)
            kinds = {event.type for event in w.store.list_events(w.mission_id)}
            assert {"ApprovalRejected", "ApprovalRevoked", "ApprovalExpired"} <= kinds
            assert len(w.events("ApprovalExpired")) == 2

    asyncio.run(case())


# ------------------------------------------------------------------ D7-2' reality is not rewritten
@pytest.mark.parametrize("outcome", ("in_flight_then_succeeded", "rejected", "lost"))
def test_the_ledger_never_rewrites_what_reality_did(tmp_path, outcome):
    """代表用例 3 的变体：系统按已批准效果准备申请单，人批准，真实的文件发布服务被调用；外界的
    三种情形各造出一种状态，台账规则都按它办：

    * ``in_flight_then_succeeded``：服务很慢，动作停在"已交接"——改内容的新候选被拒
      （``action_in_flight``），已交接的批准不能撤回；服务答完后动作成功——同样内容的候选还是那一版，
      改内容的被拒（``action_already_executed``），没有新的待办版本。
    * ``rejected``：目标处已有别人放的同名文件，服务拒绝——动作失败。刚失败时没有否定证明，
      对外操作快照判它"未了结"、挡住操作闸门，这时任何新版本都被拒（``action_outcome_unproven``）；
      随后对账由发布台账证明它从未落地（阶段 B 裁决第 1 类），闸门打开，系统按原内容出新的一张卡，
      卡上带着上次的结局和服务原文理由；别人的文件原样不动。
    * ``lost``：服务写下意图后连接中断——动作结果不明；任务结束时取消待办工作也不碰它，
      它的批准仍是已批准；改内容的候选被拒（``action_in_flight``）。
    """

    gate = threading.Event()

    def setup(publish):
        if outcome == "in_flight_then_succeeded":
            real = publish.execute

            def slow(*args, **kwargs):  # the publishing service takes its time
                gate.wait(20)
                return real(*args, **kwargs)

            publish.execute = slow
        elif outcome == "lost":
            publish.fail_after = "intent"  # the connection drops after the intent is on disk

    async def case():
        async with operation_world(tmp_path, key=f"reality-{outcome}", publish_setup=setup) as w:
            action = await until_pending(w)
            key = action["action_key"]
            if outcome == "rejected":  # someone else's file already sits at the exact published name
                name = _name_for(action["idempotency_key"], PurePosixPath(TARGET))
                (w.published / "reports").mkdir(parents=True, exist_ok=True)
                (w.published / "reports" / name).write_text("别人放的", encoding="utf-8")
            request = [a for a in w.control.approvals(w.mission_id) if a.get("state") == "PENDING"][0]
            assert w.control.decide(request["request_id"], "approve")["request_state"] == "GRANTED"
            same = {**candidate(), "params": dict(action["params"])}
            changed = candidate(value="改过的内容")
            seen = {}

            def state() -> str:
                return str(w.store.get_action(key)["state"])

            if outcome == "in_flight_then_succeeded":
                def probe() -> bool:
                    if state() == "HANDED_OFF" and "in_flight" not in seen:
                        seen["in_flight"] = w.propose(changed, artifact_hash=HASH_B, result="result-2")
                        with pytest.raises(ActionCommitError):  # no revocation once handed off
                            w.service.revoke_approval(action["approval_request_id"], principal=BOB, reason="太晚了")
                        seen["still"] = state()
                        gate.set()
                    return state() == "SUCCEEDED"

                await run_until(w.product, probe)
                busy = seen["in_flight"]
                assert busy["state"] == "REFUSED" and busy["refused"] == "action_in_flight", busy
                assert seen["still"] == "HANDED_OFF"
                again = w.propose(same, artifact_hash=action["artifact_hash"], result=action["result_id"])
                assert again["action_key"] == key  # the same content: nothing new
                done = w.propose(candidate(value="又改了"), artifact_hash="c" * 64, result="result-3")
                assert done["state"] == "REFUSED" and done["refused"] == "action_already_executed"
                assert state() == "SUCCEEDED" and len(w.events("ActionRefused")) == 2
                assert not w.store.list_actions(w.mission_id, "AWAITING_APPROVAL", "APPROVED", "PROPOSED")
                assert w.publish_ledger().count("PREPARED") == 1  # published exactly once
                return
            terminal = "FAILED" if outcome == "rejected" else "UNKNOWN"
            await run_until(w.product, lambda: state() == terminal)
            ran = w.store.get_action(key)
            assert ran["handoffs"] == 1 and w.published_files() == ([] if outcome == "lost" else
                                                                    [f"reports/{_name_for(action['idempotency_key'], PurePosixPath(TARGET))}"])
            snapshot = build_operation_snapshot(w.mission_id, reader=StoreOperationReader(w.store))
            assert [effect for _operation, effect in snapshot.effects] == [OperationEffect.UNRESOLVED]
            with pytest.raises(SourceUnavailable, match="operation_unresolved"):
                operation_gate(snapshot)
            if outcome == "rejected":
                early = w.propose(changed, artifact_hash=HASH_B, result="result-2")
                assert early["state"] == "REFUSED" and early["refused"] == "action_outcome_unproven", early
                await run_until(w.product, lambda: any(
                    a["state"] == "AWAITING_APPROVAL" for a in w.store.list_actions(w.mission_id)))
                [proof] = [e.payload for e in w.store.list_events(w.mission_id)
                           if e.type == "ActionScopedReconciled" and e.payload["action_key"] == key]
                assert proof["outcome"] == "NOT_APPLIED_FINAL"
                operation_gate(build_operation_snapshot(w.mission_id, reader=StoreOperationReader(w.store)))
                [v2] = [a for a in w.store.list_actions(w.mission_id) if a["state"] == "AWAITING_APPROVAL"]
                assert v2["params_hash"] == ran["params_hash"] and v2["after"] == "FAILED"
                assert v2["previous_attempt"]["outcome"] == "service_refused"
                assert "already exists" in v2["previous_attempt"]["reason"]
                assert w.store.get_approval(v2["approval_request_id"])["summary"]["previous_attempt"] == v2["previous_attempt"]
                name = _name_for(action["idempotency_key"], PurePosixPath(TARGET))
                assert (w.published / "reports" / name).read_text(encoding="utf-8") == "别人放的"
                return
            busy = w.propose(changed, artifact_hash=HASH_B, result="result-2")
            assert busy["state"] == "REFUSED" and busy["refused"] == "action_in_flight"
            assert w.service.cancel_open_actions(w.mission_id, reason="mission_cancelled") == []
            assert state() == "UNKNOWN"  # never touched
            assert w.store.get_approval(action["approval_request_id"])["state"] == "GRANTED"

    started = time.monotonic()
    asyncio.run(case())
    assert time.monotonic() - started < 30
