# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""黑板门面（``memory/blackboard.py``，§11）：只读，把"已核实知识"和"候选结论"两层分开。

迁自 ``test_claims_knowledge.py`` 第一条（删旧平面模式 第三刀：那个文件随平面图夹具整删，
这一条只需要一个步骤行，换成分层的两个并列步骤）。"""

from __future__ import annotations

from knowledge_helpers import (
    claim,
    drive_to_running,
    envelope,
    passed_layers,
    submit,
    two_leaf_service,
)

from agent_orchestrator.contracts import ClaimStatus, TaskStatus
from agent_orchestrator.memory.blackboard import Blackboard


def test_blackboard_is_read_only_and_keeps_knowledge_and_candidates_apart(tmp_path):
    service, mission, (task_a, _task_b) = two_leaf_service(tmp_path)
    attempt = drive_to_running(service, task_a)
    stored = submit(
        service,
        attempt,
        envelope(
            attempt,
            claims=[
                claim(
                    "impl_a 对空输入抛 ValueError",
                    key="impl_a.empty_input",
                    stance="refutes",
                    evidence=["pytest:tests/probe/test_impl_a.py"],
                ),
                claim("impl_a 的实现风格良好", evidence=["tests/probe/test_impl_a.py"]),
            ],
        ),
    )
    completed = service.accept_result(
        stored.envelope.id, verifier_results=passed_layers("tests/probe/test_impl_a.py")
    )
    assert completed.status is TaskStatus.COMPLETED
    claims = service.store.list_claims(stored.envelope.id)
    verified = [c.id for c in claims if c.status is ClaimStatus.VERIFIED]
    assert verified  # 至少一条进了已核实知识，两层才有得分
    assert [k.id for k in service.store.list_knowledge(mission.id)] == verified

    board = Blackboard(service.store)
    assert [k.id for k in board.verified_knowledge(mission.id)] == verified
    assert {c.id for c in board.candidate_claims(mission.id)} == {
        c.id for c in claims if c.status is not ClaimStatus.VERIFIED
    }
    assert board.candidate_claims(mission.id)  # 候选层不空
    assert not hasattr(board, "upsert_knowledge") and not hasattr(board, "write")
