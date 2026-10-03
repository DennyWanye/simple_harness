# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""收尾前复查"通过的依据还有效吗"（2026-10-01 完成度评估第 4 项；2026-10-03 迁到产品同形世界）。

两步计划（先写 facts.md，再写 NOTES.md），第一步验收通过、第二步的执行者被挂住时：
* 产品自己在这期间做了别的写入（派发第二步等）：第一步验收的证书仍有效；
* 用 SQL 改坏第一步那条已验收审阅记录的字节（外界真会发生的磁盘损坏）：读侧 ``changed_items``
  点名就是这条记录（REF_BODY_CONFLICT）；
* 放行后：任务不带着过期依据完成；收尾判 NOT_READY + EVIDENCE_STALE，规划器收到一条证据失效修复
  请求，里面点名了这条记录——事实如实交给了 LLM（2026-10-03 核实：不是缺陷）。

红线：不写 goal_resolutions.validity。
观察表变化（验收公式读的观察表）等带观察器的测试世界，并入"前提 / 观测"那条主循环用例。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from agent_orchestrator.assurance.codec import decode
from agent_orchestrator.orchestrator.assurance_consumers import AssuranceValidityConsumer
from agent_orchestrator.orchestrator.assurance_recheck import changed_items
from agent_orchestrator.testing.fixtures import package_of, role_of
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider
from agent_orchestrator.testing.fixtures import lift_immutable_guards

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "taskgraph_exec"))
from production_fixture import CHAIN_CRITERIA, chain_planner  # noqa: E402


class _SecondStepHeld(LayeredScriptedProvider):
    """第二步（写 NOTES.md）的执行者停在半路，直到 ``go`` 置位；记下每个修复轮的规划包。"""

    def __init__(self) -> None:
        self.repair_packages: list[dict[str, Any]] = []

        def planner(request: Any) -> Any:
            package = package_of(request)
            if package.get("repair_requests"):
                self.repair_packages.append(package)
            return chain_planner(request)

        super().__init__(planner=planner)
        self.go = asyncio.Event()
        self.second_entered = asyncio.Event()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        if role_of(request) == "worker" and "NOTES.md" in (
                package_of(request).get("task_contract", {}).get("outputs") or []):
            self.second_entered.set()
            await self.go.wait()
        return await super().invoke(request, cancel=cancel)


def _acceptance_certificate(store: Any, mission_id: str) -> Any:
    [row] = store.connection.execute(
        "SELECT * FROM assurance_use_certificates WHERE mission_id=? AND consumer_kind='ACCEPTANCE'",
        (mission_id,)).fetchall()
    assert decode(row["certificate_json"])["decision"] == "USABLE"
    return row


def test_a_tampered_accepted_review_is_named_stale_and_reaches_the_planner(tmp_path):
    async def case():
        provider = _SecondStepHeld()
        async with product_world(tmp_path / "root", provider) as world:
            mission_id = world.create({"goal": "先整理事实再写笔记", "idempotency_key": "evidence-recheck",
                                       "success_criteria": list(CHAIN_CRITERIA)})["mission_id"]
            store, tenant = world.loop.store, world.deployment.tenant_id
            stop = asyncio.Event()

            async def drive() -> None:
                while not stop.is_set():
                    await world.loop.run()
                    await world.deployment.between_cycles(auto=True)
                    await asyncio.sleep(0.05)

            runner = asyncio.create_task(drive())
            try:
                await asyncio.wait_for(provider.second_entered.wait(), 60)
                row = _acceptance_certificate(store, mission_id)
                certificate = decode(row["certificate_json"])
                consumer = AssuranceValidityConsumer(world.loop.commit, tenant_id=tenant)
                with store.read_view():
                    observed = consumer._observe_locked(row, now_ms=int(store.now * 1000))
                # The product kept writing after this certificate (the second step was
                # dispatched): unrelated changes leave it current.
                assert observed["validity"] == "CURRENT" and observed["changed_items"] == [], observed

                review_key = next(item["key"] for item in certificate["read_set"]
                                  if item["channel"] == "OBJECT" and decode(item["key"])["kind"] == "review")
                with store.transaction():
                    lift_immutable_guards(store.connection, "review_records")
                    store.connection.execute(
                        "UPDATE review_records SET record_json=json_set(record_json,'$.reviewer_turn_id',"
                        "'tampered-on-disk') WHERE record_id=?", (decode(review_key)["id"],))
                with store.read_view():
                    changed = changed_items(store, tenant_id=tenant, certificate=certificate)
                assert {"channel": "OBJECT", "key": review_key, "reason": "REF_BODY_CONFLICT"} in changed

                provider.go.set()
                for _ in range(600):
                    if provider.repair_packages:
                        break
                    await asyncio.sleep(0.1)
            finally:
                stop.set()
                provider.go.set()
                await asyncio.wait_for(runner, 30)

            assert provider.repair_packages, "the stale evidence never reached the planner"
            [request] = [entry["request"] for entry in provider.repair_packages[0]["repair_requests"]
                         if entry["request"]["trigger_source"] == "EVIDENCE_INVALIDATION"]
            context = request["context"]
            assert context["reason"] == "evidence_stale"
            assert context["certificate_id"] == row["certificate_id"]
            assert {"channel": "OBJECT", "key": review_key, "reason": "REF_BODY_CONFLICT"} in context["changed_items"]
            assert store.get_mission(mission_id).status.value != "COMPLETED"
            closeout = store.connection.execute(
                "SELECT state, check_body_json FROM assurance_closeouts WHERE mission_id=?", (mission_id,)).fetchone()
            assert closeout["state"] == "NOT_READY"
            assert "EVIDENCE_STALE" in decode(closeout["check_body_json"])["reasons"]
            # Red line: nothing here touched the resolutions' validity column.
            assert store.connection.execute(
                "SELECT count(*) FROM goal_resolutions WHERE validity!='CURRENT'").fetchone()[0] == 0

    asyncio.run(case())
