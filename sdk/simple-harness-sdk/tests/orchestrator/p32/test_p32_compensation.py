# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""P3.2 slice D · P32-8 / P32-11 (ledger half): recovery and compensation are different
things (plan v3 D6 / D8; plan review round 2 P2-1 / P2-3 / P2-7).

Recovery re-establishes what one action version was already allowed to do — same business
key, same idempotency key.  Compensation is a *new* action: something in the world must
change again, so it has its own business key (``<action>#comp-<n>``), its own approval and
its own idempotency key, and the original fact stays exactly as it was recorded.  Without
that separation, changing the content after a success would either be silently refused
(``action_already_executed``) or, worse, ride on the old approval.

删旧平面模式 第三刀：其余六条要先把原动作交接成功，靠的是平面任务"不查操作链接"的交接支；
分层任务交接时一律要求操作链接（``operation_link_missing``），补偿新建的动作又不带操作链接
（施工清单第一节第 18 条，补偿入口去留另立一项），所以随平面支删。只剩一条不需要交接的
提议层规则，底座换成分层的 ``ledger_service``。
"""

from __future__ import annotations

import pytest
from helpers_step07 import candidate, ledger_service

from agent_orchestrator.orchestrator.action_commits import ActionCommitError

HASH_A = "a" * 64
HASH_B = "b" * 64


def _propose(service, mission, task, connectors, deployment, cand, artifact_hash=HASH_A):
    return service.propose_action(
        cand,
        mission_id=mission.id,
        task_id=task.id,
        result_id="result-1",
        attempt_id=f"{task.id}:attempt-1",
        artifact_id=f"artifact-{artifact_hash[:8]}",
        artifact_hash=artifact_hash,
        connectors=connectors,
        deployment=deployment,
    )


@pytest.fixture
def ledger(tmp_path):
    service, mission, tasks, config, connectors, deployment = ledger_service(tmp_path)
    return service, mission, next(iter(tasks.values())), config, connectors, deployment


def test_p32_8_only_a_settled_action_can_be_compensated(ledger):
    service, mission, task, _config, connectors, deployment = ledger
    open_action = _propose(service, mission, task, connectors, deployment, candidate())
    with pytest.raises(ActionCommitError):
        service.propose_compensation(
            open_action["action_key"],
            operation="set",
            params={"value": "off"},
            reason="还没执行就想补偿",
            artifact_id="artifact-1",
            artifact_hash=HASH_B,
            connectors=connectors,
            deployment=deployment,
        )
