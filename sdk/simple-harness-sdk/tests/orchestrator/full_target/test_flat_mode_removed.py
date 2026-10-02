"""删旧平面模式第三刀：平面模式在三个入口被拒，库里遗留的平面任务被主循环按名停掉。

2026-10-02 用户决定旧平面编排模式整条线删除、开发期不做旧数据兼容。

* 入口：``MissionSpec(orchestration_semantics_version="legacy")`` 在写库前就报错；
  绕过 ``__post_init__`` 的规格到 ``create_mission`` 再被拒一次；门面 ``create`` 把它
  报成 ``invalid_request``。三处都不写库。
* 遗留：库里一条平面任务行（直接改写任务文档，不经已删的提交函数）在主循环每一轮
  开头被停掉，原因 ``unsupported_orchestration_semantics``；同一个库里的分层任务不受
  这道门影响；停掉的任务门面照样能读。

**改坏检验**：
* 去掉主循环里那道门（``_cycle_inner`` 开头的 ``_refuse_unsupported_contract`` 循环）
  → 第二条失败（平面任务不被停）；
* ``normalise_semantics`` 放行 ``legacy`` → 第一条失败。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

from agent_orchestrator.api.facade import FacadeError, MissionControlV1
from agent_orchestrator.contracts import ContractError, MissionStatus
from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.orchestrator.commit_service import CommitRejected, MissionSpec
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.runtime.assembly import OrchestratorConfig
from agent_orchestrator.testing.fixtures import RoleScriptedProvider

TENANT = "tenant-flat-removed"


def _spec(key: str, **extra) -> MissionSpec:
    return MissionSpec(
        goal="写一份说明", success_criteria=("说明写清楚",), tenant_id=TENANT,
        idempotency_key=key, **extra,
    )


def _missions(loop) -> int:
    return loop.store.connection.execute("SELECT COUNT(*) FROM missions").fetchone()[0]


def test_flat_mode_is_refused_at_every_door_and_nothing_is_written(tmp_path) -> None:
    with pytest.raises(ContractError, match="flat orchestration mode was removed"):
        _spec("flat-spec", orchestration_semantics_version="legacy")

    async def case():
        async with Orchestrator(OrchestratorConfig(evidence_root=tmp_path), RoleScriptedProvider({})) as loop:
            before = _missions(loop)
            # A spec that skipped ``__post_init__`` is refused again before any write.
            sneaked = _spec("flat-sneaked")
            object.__setattr__(sneaked, "orchestration_semantics_version", "legacy")
            with pytest.raises(CommitRejected, match="flat orchestration mode was removed"):
                loop.commit.create_mission(sneaked)
            control = MissionControlV1(loop, tenant_id=TENANT, principal=Principal("person"))
            with pytest.raises(FacadeError) as refused:
                control.create({
                    "goal": "写一份说明", "success_criteria": ["说明写清楚"],
                    "idempotency_key": "flat-facade", "orchestration_semantics_version": "legacy",
                })
            assert refused.value.code == "invalid_request"
            assert "flat orchestration mode was removed" in str(refused.value)
            assert _missions(loop) == before

    asyncio.run(case())


def _make_flat(loop, mission_id: str) -> None:
    """把一条已建的任务改写成平面任务行：只动任务文档里的模式字段（测试库，不经提交函数）。"""

    row = loop.store.connection.execute(
        "SELECT json FROM missions WHERE mission_id=?", (mission_id,)
    ).fetchone()
    document = json.loads(row[0])
    report = dict(document.get("final_report") or {})
    report["orchestration_semantics_version"] = "legacy"
    report.pop("planning_protocol_version", None)
    document["final_report"] = report
    with loop.store.transaction():
        loop.store.connection.execute(
            "UPDATE missions SET json=? WHERE mission_id=?",
            (json.dumps(document, ensure_ascii=False, sort_keys=True), mission_id),
        )


def test_a_flat_mission_left_in_the_library_is_stopped_by_name(tmp_path) -> None:
    async def case():
        provider = RoleScriptedProvider({})
        async with Orchestrator(OrchestratorConfig(evidence_root=tmp_path), provider) as loop:
            flat, _ = loop.commit.create_mission(_spec("left-over"))
            layered, _ = loop.commit.create_mission(_spec("layered"))
            _make_flat(loop, flat.id)
            assert loop.store.get_mission(flat.id).final_report["orchestration_semantics_version"] == "legacy"

            await loop._cycle()

            stopped = loop.store.get_mission(flat.id)
            assert stopped.status is MissionStatus.FAILED
            failed = [e for e in loop.store.list_events(flat.id) if e.type == "MissionFailed"]
            assert len(failed) == 1
            assert "unsupported_orchestration_semantics" in json.dumps(failed[0].payload)
            # The layered Mission is not touched by the gate (no assembly is installed
            # here, so it waits for one — visibly — and is not failed).
            other = loop.store.get_mission(layered.id)
            assert other.status is not MissionStatus.FAILED
            assert not [e for e in loop.store.list_events(layered.id) if e.type == "MissionFailed"]
            assert provider.calls == 0

            # A second round does not stop it twice.
            await loop._cycle()
            assert len([e for e in loop.store.list_events(flat.id) if e.type == "MissionFailed"]) == 1

            control = MissionControlV1(loop, tenant_id=TENANT, principal=Principal("person"))
            listed = {item["mission_id"]: item for item in control.missions()}
            assert listed[flat.id]["status"] == "FAILED"
            snapshot = control.snapshot(flat.id)
            assert snapshot["mission_id"] == flat.id
            assert "FAILED" in json.dumps(snapshot["snapshot"])

    asyncio.run(case())


def test_the_spec_keeps_writing_the_one_mode(tmp_path) -> None:
    spec = _spec("default")
    assert spec.orchestration_semantics_version == "hierarchical"
    assert dataclasses.asdict(spec)["orchestration_semantics_version"] == "hierarchical"
    assert spec.to_json()["orchestration_semantics_version"] == "hierarchical"
