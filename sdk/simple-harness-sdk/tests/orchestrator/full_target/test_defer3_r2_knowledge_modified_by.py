# SPDX-License-Identifier: Apache-2.0
"""推后必补第 3 批车道 R2，K07：知识血缘记"被谁改过"。

原计划 09-10《Agent 编排层完整设计方案》§11.2：每条正式知识都应该知道"由谁修改过"。知识入库后
只有两种改动：别的声明反驳它（``disputed_by`` 加一项），新版本取代它（状态改 SUPERSEDED）。两处都经
同一个函数在同一次写入里追加一项修改记录：改了什么、改它的声明 / 新知识编号、那一方的任务与尝试、
提出者、库时钟时刻。只记事实，不判语义。

产品上的反驳那一处由 ``step04/test_claims_knowledge_main_loop.py`` 的产品同形世界守；产品上模型取代
一律被拒（同文件），所以取代这一处在这里直接调提交服务的取代写方。

**改坏检验**：取代写方不追加修改记录 → 本文件第二条失败。
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from agent_orchestrator.contracts import ContractError
from agent_orchestrator.memory.verified_knowledge import KnowledgeRecord, modified
from agent_orchestrator.observability.lineage import _view
from agent_orchestrator.orchestrator.commit_service import CommitService


def _record(kid: str = "k1", **extra: Any) -> KnowledgeRecord:
    fields: dict[str, Any] = dict(
        id=kid, mission_id="m", claim_id=kid, content="空输入返回空列表", type="statement",
        status="VERIFIED", version=1, key="impl_a.empty_input", stance="affirms", proposed_by="agent-1",
        source_task="t1", source_attempt="t1:attempt-1", source_result="r1",
        evidence=("pytest:tests/x.py",), verifier={"basis": "review_confirmed"}, created_at=1.0)
    return KnowledgeRecord(**{**fields, **extra})


def test_a_modification_entry_round_trips_and_is_shown_in_the_lineage() -> None:
    plain = _record()
    assert plain.modified_by == ()
    changed = modified(plain, change="DISPUTED", by="claim-9", task_id="t2", attempt_id="t2:attempt-1",
                       proposed_by="agent-2", at=5.0)
    assert changed.modified_by == ({"change": "DISPUTED", "by": "claim-9", "task_id": "t2",
                                    "attempt_id": "t2:attempt-1", "proposed_by": "agent-2", "at": 5.0},)
    assert KnowledgeRecord.from_json(json.loads(json.dumps(changed.to_json()))) == changed
    assert _view(changed)["modified_by"] == [dict(changed.modified_by[0])]
    with pytest.raises(ContractError, match="modified_by"):
        _record(modified_by=({"change": "EDITED", "by": "x", "task_id": "t", "attempt_id": "a",
                              "proposed_by": "p", "at": 1.0},))
    with pytest.raises(ContractError, match="modified_by"):
        _record(modified_by=({"change": "DISPUTED", "by": "x"},))


def test_superseding_knowledge_records_who_superseded_it() -> None:
    old, new = _record("k1"), _record("k2", source_task="t5", source_attempt="t5:attempt-2", proposed_by="agent-5")
    rows = {old.id: old, new.id: new}
    emitted: list[tuple[str, dict[str, Any]]] = []
    store = SimpleNamespace(
        now=42.0, get_knowledge=rows.get,
        upsert_knowledge=lambda record: rows.__setitem__(record.id, record),
        get_claim=lambda claim_id: None)
    service = SimpleNamespace(_store=store, _emit=lambda kind, mission, **kw: emitted.append((kind, kw["payload"])))
    CommitService._supersede_knowledge(service, old.id, by=new)
    after = rows[old.id]
    assert after.status == "SUPERSEDED" and after.superseded_by == new.id
    assert after.modified_by == ({"change": "SUPERSEDED", "by": new.id, "task_id": "t5",
                                  "attempt_id": "t5:attempt-2", "proposed_by": "agent-5", "at": 42.0},)
    assert [kind for kind, _ in emitted] == ["KnowledgeSuperseded"]
