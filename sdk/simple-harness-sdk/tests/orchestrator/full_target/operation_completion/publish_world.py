# SPDX-License-Identifier: Apache-2.0
"""对外操作族的产品同形种子（HTN 补齐阶段 A′，代表用例 3 的变体共用）。

世界与 ``tests/orchestrator/product_world/test_operation.py`` 同一条路：产品那一份部署组装、
原生执行池、保证通道、建任务即绑定执行图，加一个真实的 ``FilePublishConnector`` 和启用它的
部署策略。替身只有外界会发生的事：

* 模型回复：:class:`LayeredScriptedProvider`（各角色可换成自己的函数）；
* 发布服务那一侧的意外：:func:`flaky_connector` 只在连接器实例上包一层，模拟"服务端已经
  发布但回执丢了""服务暂时连不上"——连接器类型与源码不变（操作档案按类型和源码哈希认它）。

确认页由用例自己点（:func:`confirm_completion`，与前端默认一致）；人批准 / 拒绝发布用门面
命令 ``decide``。"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.runtime.connectors import ConnectorTransportError
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector
from agent_orchestrator.testing.product_world import ProductWorld, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

TARGET = "reports/weekly.md"
PUBLISH = "action:file_publish.publish:" + TARGET
GOAL = "写一份周报 reports/weekly.md 并发布"
TERMINAL = {"COMPLETED", "FAILED", "STOPPED", "CANCELLED"}


@dataclass
class Faults:
    """发布服务那一侧要发生的意外（计数器，用例可以随时改）。"""

    lose_replies: int = 0  # 前几次发布：服务端已经落盘，回执在路上丢了
    drop_requests: int = 0  # 前几次发布：请求没到服务端（什么都没发生），调用方只看到连接断了
    remove_published: bool = False  # 发布落盘后，用户随即从自己的目录里删掉了那个文件
    unreachable_lookups: int = 0  # 前几次核对：服务连不上
    executed: int = 0
    lookups: int = 0
    log: list[str] = field(default_factory=list)


def flaky_connector(connector: FilePublishConnector, faults: Faults) -> FilePublishConnector:
    real_execute, real_lookup = connector.execute, connector.lookup

    def execute(*args: Any, **kwargs: Any) -> Any:
        if faults.drop_requests > 0:
            faults.drop_requests -= 1
            faults.log.append("request-dropped")
            raise ConnectorTransportError("the connection dropped before the request reached the service")
        receipt = real_execute(*args, **kwargs)
        faults.executed += 1
        if faults.remove_published:
            for path in connector.root.rglob("*"):
                if path.is_file():
                    path.unlink()
                    faults.log.append("removed-by-user")
        if faults.lose_replies > 0:
            faults.lose_replies -= 1
            faults.log.append("reply-lost")
            raise ConnectorTransportError("the service applied the publish; its reply was lost")
        return receipt

    def lookup(key: str) -> Any:
        faults.lookups += 1
        if faults.unreachable_lookups > 0:
            faults.unreachable_lookups -= 1
            faults.log.append("lookup-unreachable")
            raise ConnectorTransportError("the publishing service is unreachable")
        return real_lookup(key)

    real_record = connector.ledger_record

    def ledger_record(key: str) -> Any:  # the reconciler's read, unreachable the same way
        faults.lookups += 1
        if faults.unreachable_lookups > 0:
            faults.unreachable_lookups -= 1
            faults.log.append("lookup-unreachable")
            raise ConnectorTransportError("the publishing service is unreachable")
        return real_record(key)

    connector.execute = execute  # type: ignore[method-assign]
    connector.lookup = lookup  # type: ignore[method-assign]
    connector.ledger_record = ledger_record  # type: ignore[method-assign]
    return connector


@dataclass
class Publishing:
    world: ProductWorld
    mission_id: str
    published: Path
    ledger_dir: Path
    faults: Faults

    @property
    def store(self) -> Any:
        return self.world.store

    def mission(self) -> Any:
        return self.store.get_mission(self.mission_id)

    def status(self) -> str:
        return str(self.mission().status.value)

    def actions(self) -> list[dict[str, Any]]:
        return list(self.store.list_actions(self.mission_id))

    def published_files(self) -> list[Path]:
        return sorted(p for p in self.published.rglob("*") if p.is_file())

    def pending_approvals(self) -> list[dict[str, Any]]:
        return [a for a in self.world.control.approvals(self.mission_id) if a.get("state") == "PENDING"]

    def events(self, event_type: str | None = None) -> list[Any]:
        return [e for e in self.store.iter_events(self.mission_id) if event_type is None or e.type == event_type]

    async def drain_until(self, done: Callable[[], bool], *, rounds: int = 20) -> bool:
        for _ in range(rounds):
            await self.world.drain()
            if done() or self.status() in TERMINAL:
                return done()
        return done()

    async def run_until(self, done: Callable[[], bool], *, timeout: float = 30.0) -> None:
        """主循环（连同部署每轮职责）一直跑到 ``done()`` 成立，再像产品 ``drain`` 的期限那样停下。
        有角色的模型调用被扣住时用它（``drain`` 要等主循环空闲，扣住时等不到）。"""

        async def drive() -> None:
            while True:
                await self.world.loop.run()
                await self.world.deployment.between_cycles(auto=self.world.auto)
                await asyncio.sleep(0.01)

        task = asyncio.create_task(drive())
        try:
            async with asyncio.timeout(timeout):
                while not done():
                    if task.done():
                        task.result()
                    await asyncio.sleep(0.01)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def until_approval(self, *, rounds: int = 20) -> dict[str, Any]:
        """跑到"系统准备好申请单、审阅通过、等人批准"那一刻；返回那张待批的卡片。"""

        await self.drain_until(lambda: bool(self.pending_approvals()), rounds=rounds)
        approvals = self.pending_approvals()
        assert len(approvals) == 1, (self.status(), self.mission().final_report, self.actions())
        return approvals[0]

    def approve(self, approval: dict[str, Any]) -> dict[str, Any]:
        return self.world.control.decide(approval["request_id"], "approve")


def workspace(world: ProductWorld, mission_id: str) -> dict[str, Any]:
    return world.control.snapshot(mission_id)["snapshot"]["operation_workspace"]


def confirm_completion(world: ProductWorld, mission_id: str, *, command_id: str = "confirm-publish-completion",
                       ) -> dict[str, Any]:
    """确认页做的事（与前端默认一致）：内容要求照单确认，``action:`` 要求作为必须完成的效果挂在根
    义务上，完成标准选"内容哈希一致"。"""

    current = workspace(world, mission_id)
    assert current["state"] == "CONFIRMATION_REQUIRED" and current["editable"] is True, current
    actions = [c["id"] for c in current["criteria"] if c["statement"].startswith("action:")]
    content = [c["id"] for c in current["criteria"] if c["required"] and c["id"] not in actions]
    [obligation] = current["obligations"]
    milestone = next(m for m in current["milestones"] if m["id"] == "CONTENT_HASH_VERIFIED")
    ref = current["requirements_ref"]
    return world.control.approve_operation_completion_spec({
        "mission_id": mission_id, "command_id": command_id,
        "expected_requirements_ref": ref,
        "proposal": {
            "schema_version": 1, "mission_id": mission_id,
            "requirements_ref": {"id": ref["id"], "revision": ref["revision"], "content_hash": ref["content_hash"]},
            "mode": "REQUIRED_EFFECTS", "content_criterion_ids": content,
            "effects": [{
                "effect_key": "publish-weekly", "source_slot_key": "publish-weekly",
                "obligation_id": obligation["id"], "criterion_ids": actions,
                "required_milestone": milestone["id"],
                "milestone_policy_ref": milestone["milestone_policy_ref"],
                "evidence_policy_ref": milestone["evidence_policy_ref"],
            }],
        },
    })


@asynccontextmanager
async def publishing(tmp_path: Path, *, provider: Any = None, faults: Faults | None = None,
                     criteria: tuple[str, ...] = ("file:" + TARGET, PUBLISH), confirm: bool = True,
                     key: str = "publish-1", goal: str = GOAL, reopen: str | None = None,
                     **config: Any) -> AsyncIterator[Publishing]:
    """一个带发布的任务：建好、（默认）确认页已经确认，主循环还没开跑内容步骤。

    ``reopen`` 给出已有任务号时不建任务：同一个库、同一套连接器与部署策略重新起一个编排服务
    （模拟进程重启）。"""

    published = tmp_path / "published"
    published.mkdir(parents=True, exist_ok=True)
    ledger_dir = tmp_path / "root" / "connectors" / "file_publish"
    faults = faults if faults is not None else Faults()
    connector = flaky_connector(FilePublishConnector(published, ledger_dir), faults)
    policy = config.pop("deployment_policy", None) or DeploymentPolicy(enabled_connectors=("file_publish",),
                                                                       max_action_level="L2")
    provider = provider if provider is not None else LayeredScriptedProvider()
    async with product_world(tmp_path / "root", provider, connectors={"file_publish": connector},
                             deployment_policy=policy, **config) as world:
        if reopen is not None:
            yield Publishing(world, reopen, published, ledger_dir, faults)
            return
        created = world.create({"goal": goal, "success_criteria": list(criteria), "idempotency_key": key})
        mission_id = created["mission_id"]
        if confirm:
            # 自动模式不代签带 action: 的任务：确认页等着人（这一轮主循环只走到确认页）。
            await world.drain()
            assert str(world.store.get_mission(mission_id).status.value) == "CREATED"
            receipt = confirm_completion(world, mission_id)
            assert receipt["authority"]["kind"] == "USER_CONFIRMED"
        yield Publishing(world, mission_id, published, ledger_dir, faults)


def quick_waits(monkeypatch: Any) -> None:
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


async def settle(case: Publishing, *, rounds: int = 20) -> Any:
    return await case.world.run_until_settled(case.mission_id, rounds=rounds)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


__all__ = ("Faults", "GOAL", "PUBLISH", "Publishing", "TARGET", "confirm_completion", "flaky_connector", "plan_committed", "root_scope",
           "publishing", "quick_waits", "run", "settle", "workspace")


@asynccontextmanager
async def plan_committed(tmp_path: Path, **options: Any) -> AsyncIterator[Publishing]:
    """主循环真把第一版计划提交了（提做法 → 独立审阅 → 采用），停在叶子刚派发出去那一刻：执行者
    那次模型调用被扣住，用例跑完才放开。完成范围此时已随计划冻结。"""

    provider = options.pop("provider", None) or LayeredScriptedProvider()
    provider.held.add("worker")
    try:
        async with publishing(tmp_path, provider=provider, **options) as case:
            await case.run_until(provider.entered.is_set)
            yield case
    finally:
        provider.release.set()


def root_scope(case: Publishing) -> dict[str, Any]:
    """根（复合目标，承担发布效果）的完成范围那一行，经产品的精确读侧读出。"""

    from agent_orchestrator.storage.operation_completion_store import OperationCompletionStore

    rows = case.store.connection.execute(
        "SELECT plan_revision, occurrence_id FROM operation_completion_scopes "
        "WHERE mission_id=? AND json_extract(document_json,'$.role')='AGGREGATE'", (case.mission_id,)).fetchall()
    assert len(rows) == 1, [tuple(r) for r in rows]
    stored = OperationCompletionStore(case.store).get_scope_exact(
        case.mission_id, int(rows[0]["plan_revision"]), str(rows[0]["occurrence_id"]))
    assert stored is not None
    return stored


class ReviewHeld(LayeredScriptedProvider):
    """审阅员对某一类审查（``package.purpose``）的那次模型调用停在半路，直到 ``go`` 置位
    （一次很慢的模型调用；用来在审阅进行中改坏字节、重启服务）。其余角色照常回答。"""

    def __init__(self, purpose: str, **roles: Any) -> None:
        super().__init__(**roles)
        self.held_purpose = purpose
        self.go = asyncio.Event()
        self.review_entered = asyncio.Event()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        from agent_orchestrator.testing.scripted_replies import review_input

        package = review_input(request)
        if package is not None and package["package"].get("purpose") == self.held_purpose:
            self.review_entered.set()
            await self.go.wait()
        return await super().invoke(request, cancel=cancel)
