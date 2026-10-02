# SPDX-License-Identifier: Apache-2.0
"""强杀后端再重启：被打断的那一轮原地重做，不判步骤失败（2026-10-02 真机）。

真机经过（`mission-baddf1eb2442858e`）：执行者的模型调用进行中强杀后端；重启后新进程接着
跑那一轮，准入拒绝了它——这次尝试的执行权还记在旧进程名下。主循环把这种拒绝当成"不可
重试"，判步骤失败、任务失败（`runtime_unavailable`）。用户 2026-09-28 定过：重启打断不扣
次数、原地重做。准入守卫现在给这种情况单独的原因码 ``lease_lost``，主循环按"被打断"处理。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from leaf_world import loop_config, loop_leaf

from agent_orchestrator.contracts import AttemptStatus, MissionStatus, TaskStatus
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.orchestrator.failure_classes import INTERRUPTED, classify_failure
from agent_orchestrator.testing.fixtures import RoleScriptedProvider
from simple_harness.agents import AgentTurnState


def _denied(turn_id: str, reason_code: str) -> SimpleNamespace:
    """执行层交回来的一轮失败：模型调用在准入处被拒，没有交出去。"""

    return SimpleNamespace(
        state=AgentTurnState.FAILED,
        turn_id=turn_id,
        public_output=None,
        error={
            "error_code": "provider_admission_denied",
            "error_type": "ProviderAdmissionDenied",
            "source_kind": "provider_admission",
            "retryable": False,
            "detail": {"schema_version": 1, "reason_code": reason_code},
        },
    )


def _collect(tmp_path, reason_code: str):
    seen: dict[str, object] = {}

    async def case() -> None:
        async with Orchestrator(loop_config(tmp_path), RoleScriptedProvider({"planner": []})) as loop:
            leaf = await loop_leaf(loop, tmp_path, key="lease-" + reason_code)
            attempt = leaf.running()
            intent = loop.store.get_intent_for_subject(attempt.id)
            await loop._collect_attempt(intent, _denied(leaf.turn_of(attempt), reason_code))
            seen["attempt"] = loop.store.get_attempt(attempt.id)
            seen["task"] = leaf.task
            seen["mission"] = loop.store.get_mission(leaf.mission.id)
            seen["released"] = loop.store.count_events(leaf.mission.id, "AttemptChargeReleased")
            if reason_code == "lease_lost":
                # 重做要走的还是那条路：系统记下修复请求，"原样重试"的决定提交后才能建下一次。
                await leaf.authorize_retry(attempt)
                seen["second"] = leaf.running(retry_of=attempt.id)

    asyncio.run(case())
    return seen


def test_a_turn_whose_lease_was_lost_waits_for_a_retry_and_is_not_charged(tmp_path) -> None:
    seen = _collect(tmp_path, "lease_lost")
    attempt = seen["attempt"]
    assert attempt.status is AttemptStatus.RETRY_WAIT
    assert classify_failure(attempt.failure) == INTERRUPTED
    assert seen["task"].status is TaskStatus.ACTIVE  # 这一步没有被判失败
    assert seen["mission"].status is MissionStatus.ACTIVE
    assert seen["released"] == 1  # 不算这一步的次数
    assert seen["second"].ordinal == 2 and seen["second"].retry_of == attempt.id


def test_any_other_admission_refusal_still_stops_the_step(tmp_path) -> None:
    """授权真的不对（身份、配置）不是被打断：照旧判这一步失败，不原地打转。"""

    seen = _collect(tmp_path, "authority_rejected")
    assert seen["task"].status is TaskStatus.FAILED
    assert seen["mission"].status is MissionStatus.FAILED
    assert seen["mission"].stop_reason == "runtime_unavailable"
    assert seen["released"] == 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
