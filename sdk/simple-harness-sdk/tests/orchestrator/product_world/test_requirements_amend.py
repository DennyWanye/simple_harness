# SPDX-License-Identifier: Apache-2.0
"""用户中途改要求（HTN 补齐阶段 E）。"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from agent_orchestrator.api.facade import FacadeError
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.storage.obligation_store import ObligationStore
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider, planner_reply

SOURCE = {"kind": "MAIN_AGENT", "run_id": "run-1", "call_id": "call-1", "permission_mode": "auto"}


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def latest_ref(store: Any, mission_id: str) -> dict[str, Any]:
    latest = HtnStore(store).latest_requirements_revision(mission_id)
    return {"id": str(latest.revision_id), "revision": int(latest.revision), "content_hash": latest.content_hash()}


def amend(world: Any, mission_id: str, changes: list[dict[str, Any]], *, command_id: str = "amend-1",
          expected: dict[str, Any] | None = None) -> dict[str, Any]:
    return world.control.amend_requirements({
        "mission_id": mission_id, "command_id": command_id,
        "expected_requirements_ref": expected or latest_ref(world.store, mission_id),
        "changes": changes, "reason": "用户补充了要求", "source": SOURCE})


def written(store: Any, mission_id: str) -> dict[str, Any]:
    """The seven things an amendment writes, as they stand."""
    htn = HtnStore(store)
    root_task = next(row[0] for row in store.connection.execute(
        "SELECT task_id FROM task_semantics WHERE mission_id=? AND task_id LIKE 'user-root-%'", (mission_id,)))
    root = htn.latest_task_semantics(root_task)
    duty = ObligationStore(store).obligation(mission_id, root.obligation_id)
    return {
        "revisions": [int(item.revision) for item in htn.list_requirements_revisions(mission_id)],
        "root_contract": int(root.contract_revision), "root_refs": tuple(root.requirement_refs),
        "duty_refs": tuple(duty.requirement_refs), "epoch": htn.epoch(mission_id, "mission"),
        "events": len([e for e in store.list_events(mission_id) if e.type == "RequirementsAmended"]),
        "receipts": store.connection.execute(
            "SELECT count(*) FROM commit_receipts WHERE kind='requirements_amended' AND subject_id=?",
            (mission_id,)).fetchone()[0],
    }


async def until_first_plan(world: Any, mission_id: str) -> None:
    for _ in range(12):
        await world.drain(timeout=20)
        plan = HtnStore(world.store).active_plan_revision(mission_id)
        if plan is not None and int(plan.revision) >= 1:
            return
    raise AssertionError("the first plan never committed")


def test_amend_writes_everything_in_one_transaction(tmp_path):
    """改写一条、删一条、加两条：七样在一个事务里写；编号不复用；同一命令重放回同一回执；
    旧版本号、收尾中的任务、写到一半出错——都按名拒绝且一样都没写。

    **改坏检验**：根义务改写挪到事务外 → 注入失败的子情形里义务已被改 → 变红；
    新增编号取"条数 + 1" → 先删后加撞上已用过的编号 → 变红。"""
    async def case():
        provider = LayeredScriptedProvider(planner=planner_reply)
        provider.held.add("worker")
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "写三份文件", "idempotency_key": "amend-tx",
                                       "success_criteria": ["file:a.md", "file:b.md", "file:c.md"]})["mission_id"]
            await until_first_plan(world, mission_id)
            before = written(world.store, mission_id)
            assert before["revisions"] == [1] and before["root_contract"] == 1

            stale = dict(latest_ref(world.store, mission_id), content_hash="0" * 64)
            with pytest.raises(FacadeError) as refused:
                amend(world, mission_id, [{"op": "add", "statement": "file:d.md"}], expected=stale)
            assert refused.value.code == "AMEND_REQUIREMENTS_STALE"
            with pytest.raises(FacadeError) as refused:
                amend(world, mission_id, [{"op": "remove", "criterion_id": "c-user-9"}])
            assert refused.value.code == "AMEND_UNKNOWN_CRITERION"
            # 写到一半出错：事件写入失败 → 前面写的全部回滚
            import agent_orchestrator.orchestrator.requirements_amendment as amendment
            import agent_orchestrator.orchestrator.hierarchical_dispatch as hierarchical

            real = hierarchical.append_hierarchical_event

            def broken(store, event_type, *args, **kwargs):
                if event_type == amendment.EVENT:
                    raise amendment.StoreError("injected fault")
                return real(store, event_type, *args, **kwargs)

            hierarchical.append_hierarchical_event = broken
            try:
                with pytest.raises(FacadeError):
                    amend(world, mission_id, [{"op": "add", "statement": "file:d.md"}])
            finally:
                hierarchical.append_hierarchical_event = real
            assert written(world.store, mission_id) == before  # 一样都没写

            changes = [{"op": "rewrite", "criterion_id": "c-user-1", "statement": "file:a.md 且用中文写"},
                       {"op": "remove", "criterion_id": "c-user-3"},
                       {"op": "add", "statement": "file:d.md"}]
            receipt = amend(world, mission_id, changes)
            after = written(world.store, mission_id)
            assert after == {"revisions": [1, 2], "root_contract": 2,
                             "root_refs": ("c-user-1", "c-user-2", "c-user-4"),
                             "duty_refs": ("c-user-1", "c-user-2", "c-user-4"),
                             "epoch": before["epoch"] + 1, "events": 1, "receipts": 1}
            latest = HtnStore(world.store).latest_requirements_revision(mission_id)
            assert latest.amendment_credential_ref == "amend-1"
            assert {str(c.criterion_id): int(c.revision) for c in latest.criteria} == {
                "c-user-1": 2, "c-user-2": 1, "c-user-4": 1}
            assert receipt["changes"] == {"added": ["c-user-4"], "rewritten": ["c-user-1"], "removed": ["c-user-3"]}
            [bumped_by] = [row[0] for row in world.store.connection.execute(
                "SELECT bumped_by FROM validity_epochs WHERE mission_id=? AND scope_id='mission'", (mission_id,))]
            assert bumped_by == f"requirements:{latest.revision_id}"
            # 同一命令重放：同一回执，不再写
            assert amend(world, mission_id, changes, expected=receipt["previous_requirements_ref"]) == receipt
            assert written(world.store, mission_id) == after
            # 先删后加：删掉的 c-user-3 不复用
            again = amend(world, mission_id, [{"op": "remove", "criterion_id": "c-user-4"},
                                              {"op": "add", "statement": "file:e.md"}], command_id="amend-2")
            assert again["changes"]["added"] == ["c-user-5"]

    asyncio.run(case())
