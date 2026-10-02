# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 7 · slice B (D7-5 / D7-5'): hand-off re-checks what the approval was bound to and
refuses a ledger row that no longer matches its own hash.

删旧平面模式 第三刀：这个文件原来靠平面任务直接提动作、走"不查操作链接"的交接支，交接
之后的回执 / UNKNOWN / 对账 / 交接上限 / 人工裁决 / 再交接用尽这些条随平面支删了（分层
任务交接时一律要求操作链接，``operation_link_missing``）；只剩交接前就拒绝的两条。"""

from __future__ import annotations

import asyncio

from helpers_step07 import ALICE, ENABLED, candidate, ledger_service

from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.runtime.actions import ActionExecutor

HASH_A = "a" * 64
HASH_B = "b" * 64


def _propose(service, mission, task, connectors, deployment, cand, artifact_hash=HASH_A):
    return service.propose_action(
        cand,
        mission_id=mission.id,
        task_id=task.id,
        result_id=f"result-{artifact_hash[:4]}",
        attempt_id=f"{task.id}:attempt-1",
        artifact_id=f"artifact-{artifact_hash[:8]}",
        artifact_hash=artifact_hash,
        connectors=connectors,
        deployment=deployment,
    )


def _approve(service, action, deployment=ENABLED, nonce="n-1"):
    if action.get("approval_request_id"):
        service.decide_approval(
            action["approval_request_id"],
            principal=ALICE,
            decision="grant",
            nonce=nonce,
            deployment=deployment,
        )


def _approved(tmp_path):
    service, mission, t, config, connectors, _ = ledger_service(tmp_path)
    action = _propose(service, mission, t["A"], connectors, ENABLED, candidate())
    _approve(service, action, ENABLED)
    return service, mission, t, config, connectors, action


def _events(service, mission, kind):
    return [dict(e.payload) for e in service.store.list_events(mission.id) if e.type == kind]


# ------------------------------------------------------------------ hand-off re-checks
def test_hand_off_rechecks_everything_the_approval_was_bound_to(tmp_path):
    clock = {"now": 1_000.0}
    deployment = DeploymentPolicy(enabled_connectors=("test_config",), approval_ttl_seconds=60.0)
    service, mission, t, config, connectors, _ = ledger_service(
        tmp_path, deployment=deployment, clock=lambda: clock["now"]
    )
    executor = ActionExecutor(service, connectors, deployment, owner="orch-1")
    pending = _propose(service, mission, t["A"], connectors, deployment, candidate(target="a"))
    assert asyncio.run(executor.hand_off(pending["action_key"])) is None  # not approved
    late = _propose(service, mission, t["A"], connectors, deployment, candidate(target="b"))
    _approve(service, late, deployment)
    clock["now"] += 61.0
    assert asyncio.run(executor.hand_off(late["action_key"])) is None
    assert service.store.get_action(late["action_key"])["state"] == "EXPIRED"
    v1 = _propose(service, mission, t["A"], connectors, deployment, candidate(target="c"))
    _approve(service, v1, deployment, nonce="n-c1")
    _propose(
        service,
        mission,
        t["A"],
        connectors,
        deployment,
        candidate(target="c", value="beta"),
        artifact_hash=HASH_B,
    )
    assert asyncio.run(executor.hand_off(v1["action_key"])) is None  # the approval was for v1 only
    live = _propose(service, mission, t["A"], connectors, deployment, candidate())
    _approve(service, live, deployment, nonce="n-live")
    switched_off = ActionExecutor(service, connectors, DeploymentPolicy(), owner="orch-1")
    assert asyncio.run(switched_off.hand_off(live["action_key"])) is None  # the deployment changed
    reasons = [e["reason"] for e in _events(service, mission, "ActionHandoffRefused")]
    assert reasons == [
        "not_ready:AWAITING_APPROVAL",
        "approval_expired",
        "not_ready:SUPERSEDED",
        "connector_not_enabled",
    ]
    assert config.calls == []
    service.cancel_mission(mission.id)
    assert asyncio.run(executor.hand_off(live["action_key"])) is None
    assert _events(service, mission, "ActionHandoffRefused")[-1]["reason"] == "mission_not_active"


def test_altered_stored_parameters_are_refused_at_hand_off(tmp_path):
    service, mission, _t, config, connectors, action = _approved(tmp_path)
    tampered = service.store.get_action(action["action_key"])
    tampered["params"] = {"value": "evil"}  # the ledger row no longer matches its own hash
    service.store.put_action(tampered)
    assert (
        asyncio.run(
            ActionExecutor(service, connectors, ENABLED, owner="orch-1").hand_off(
                action["action_key"]
            )
        )
        is None
    )
    assert _events(service, mission, "ActionHandoffRefused")[-1]["reason"] == "params_hash_mismatch"
    assert config.calls == []
