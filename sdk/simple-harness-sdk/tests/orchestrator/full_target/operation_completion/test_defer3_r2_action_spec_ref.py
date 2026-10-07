# SPDX-License-Identifier: Apache-2.0
"""推后必补第 3 批车道 R2，A11：完成规格引用有真实产生方。

原计划 Assurance §3.1（:57、:77）：每个公共引用种类都有精确读者，完成规格经原 OCC 读者读；§2（:41）：
申请单审查"接实际 payload/spec/owner"。此前完成规格的读者写了，产品上没有一处产生这种引用。现在
系统准备申请单审查时，把申请单所属完成范围钉住的那一份已批准规格放进审查材料：它经 OCC 读者
读出、进证据目录，审阅员能按引用读到。

产品同形发布世界：建任务 → 人确认完成映射 → 内容步骤写出文件 → 系统准备申请单、审阅通过 → 等人批准。

**改坏检验**：申请单审查材料不放完成规格 → 证据目录里没有这一条 → 本条失败。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from agent_orchestrator.assurance.codec import fingerprint
from agent_orchestrator.storage.assurance_reads import AssuranceReader
from agent_orchestrator.assurance.refs import AssuranceRef, Pin
from agent_orchestrator.testing.product_world import TENANT
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from publish_world import publishing, quick_waits, root_scope  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    quick_waits(monkeypatch)


def test_the_action_proposal_review_carries_the_approved_completion_spec(tmp_path):
    async def run() -> None:
        async with publishing(tmp_path, provider=LayeredScriptedProvider()) as case:
            await case.until_approval()
            scope = root_scope(case)
            spec_id, spec_hash = str(scope["spec_id"]), str(scope["spec_hash"])
            rows = case.store.connection.execute(
                "SELECT binding_json FROM assurance_review_bindings WHERE mission_id=?",
                (case.mission_id,)).fetchall()
            proposals = [json.loads(row[0]) for row in rows
                         if json.loads(row[0])["subject"]["purpose"] == "ACTION_PROPOSAL"]
            assert proposals, "no ACTION_PROPOSAL review was prepared"
            for binding in proposals:
                specs = [entry["ref"] for entry in binding["evidence_catalogue"]
                         if entry["ref"]["kind"] == "completion_spec"]
                assert specs == [{"kind": "completion_spec",
                                  "pin": {"id": spec_id, "revision": 0, "content_hash": spec_hash}}]
            # 引用经 OCC 读者精确读回：正文就是那一份已批准的规格。
            reader = AssuranceReader(case.store, tenant_id=TENANT, mission_id=case.mission_id)
            resolved = reader.read_exact_metadata(AssuranceRef("completion_spec", Pin(spec_id, 0, spec_hash)))
            assert fingerprint(json.loads(resolved.body_json)) == spec_hash
            assert resolved.issuer == "operation_completion_specs"

    asyncio.run(run())
