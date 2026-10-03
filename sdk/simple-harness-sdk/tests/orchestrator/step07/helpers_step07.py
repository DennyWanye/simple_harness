# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""第 7 步（动作台账）与 h1h 对外操作用例的共用构造器（HTN 补齐阶段 A′，分诊裁决③ D′）。

**偏离**：分诊裁决③要求这个构造器放在 ``src/agent_orchestrator/testing/``；本轮不许改 src，
先放在 tests/ 下这一处，21 条 D′ 用例都用它，不各写一份。

任务全部经产品那一份部署建出（:func:`~agent_orchestrator.testing.product_world.product_world`：
建任务时初始化根、绑定执行图、走保证通道、原生执行池），只有三样替身：脚本化模型回复、测试
计数器、通用"用户目标"规划世界；外界真会发生的事（模型慢、发布服务慢 / 拒绝 / 连接中断、
磁盘字节被改坏）在各用例里模拟。

* 产品里能挂"必须完成的效果"的连接器只有文件发布（``FilePublishConnector``：只有它有操作档案
  与里程碑，确认页才能把 ``action:`` 要求映射成效果；``TestConfigService`` 的 ``action:`` 要求在
  确认页上映射不了，任务永远开不了工）。所以台账用例的连接器是真实的文件发布服务，候选动作是
  ``file_publish.publish:<目标>``。
* 带 ``action:`` 要求的任务，自动模式不代签完成映射：由"人"在确认页把内容要求照单确认、把每条
  ``action:`` 要求作为必须完成的效果（:func:`confirm_effects`，与代表用例 3 同一份确认）。

:func:`ledger_world`（D′）：主循环真跑到第一版计划提交，执行者那次模型调用被扣住（叶子刚派发、
尝试在跑），此时台账还是空的。之后用例只能经动作台账自己对外公开的写入口（``propose_action`` /
``decide_approval`` / ``revoke_approval`` / ``expire_approvals`` / ``begin_handoff`` /
``cancel_open_actions``）或用户门面写台账，不手建尝试、不手记结果；存储层断言随便做。

:func:`operation_world` + :func:`until_pending`：代表用例 3 的变体（``product_world/test_operation.py``），
执行者照常答，系统按已批准效果自己准备申请单（带真实的操作链接），停在"等人批准"。
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_orchestrator.governance.permissions import Principal
from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.runtime.connectors_publish import FilePublishConnector
from agent_orchestrator.storage.htn_store import HtnStore
from agent_orchestrator.testing.product_world import USER_GOAL_NAMES, ProductWorld, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

_FULL_TARGET = Path(__file__).resolve().parents[1] / "full_target"
if str(_FULL_TARGET) not in sys.path:
    sys.path.append(str(_FULL_TARGET))

from h1i_seed import CONFIG, run_until  # noqa: E402

ALICE = Principal("alice", "Alice")
BOB = Principal("bob", "Bob")

PUBLISH = "action:file_publish.publish:"
TARGET = "reports/weekly.md"
#: 台账用例的任务要求：一份内容文件，外加四个发布目标（任务的动作范围就是它的 action: 要求）。
LEDGER_TARGETS = (TARGET, "a.md", "b.md", "c.md")
LEDGER_CRITERIA = ("file:CHANGE.md", *(PUBLISH + target for target in LEDGER_TARGETS))
#: 代表用例 3 的任务要求：写一份周报并发布它。
PUBLISH_CRITERIA = ("file:" + TARGET, PUBLISH + TARGET)
# D7-3'：部署默认什么都不启用；这里只启用文件发布（与代表用例 3 同一份策略）
ENABLED = DeploymentPolicy(enabled_connectors=("file_publish",), max_action_level="L2")


def candidate(
    target: str = TARGET, value: str = "on", *, operation: str = "publish", connector: str = "file_publish"
) -> dict[str, Any]:
    """执行者 / 系统递交的一条候选动作；``value`` 换了，参数（发布内容的哈希）就换了。"""

    return {
        "connector": connector,
        "operation": operation,
        "target": target,
        "params": {"artifact_path": "CHANGE.md", "content_hash": hashlib.sha256(value.encode()).hexdigest()},
        "reason": "按需求发布变更说明",
    }


@dataclass
class OperationWorld:
    product: ProductWorld
    mission_id: str
    publish: FilePublishConnector
    published: Path
    provider: LayeredScriptedProvider
    deployment: DeploymentPolicy = ENABLED

    @property
    def loop(self) -> Any:
        return self.product.loop

    @property
    def store(self) -> Any:
        return self.product.loop.store

    @property
    def service(self) -> Any:
        """产品的提交服务（动作台账的写入口都在它上面）。"""
        return self.product.loop.commit

    @property
    def control(self) -> Any:
        """用户门面（固定的调用人）。"""
        return self.product.control

    @property
    def mission(self) -> Any:
        return self.store.get_mission(self.mission_id)

    @property
    def connectors(self) -> dict[str, Any]:
        return {"file_publish": self.publish}

    @property
    def root(self) -> Any:
        return self.store.get_task(USER_GOAL_NAMES.task_prefix + self.mission_id)

    @property
    def leaf(self) -> Any:
        """第一版计划里唯一的叶子步骤（一步做法）。"""
        [leaf] = [task for task in self.store.list_tasks(self.mission_id) if task.id != self.root.id]
        return leaf

    @property
    def tasks(self) -> dict[str, Any]:
        """``"A"`` = 叶子步骤，``"B"`` = 根任务（两个都是真实建出的任务行）。"""
        return {"A": self.leaf, "B": self.root}

    def events(self, kind: str) -> list[dict[str, Any]]:
        return [dict(event.payload) for event in self.store.list_events(self.mission_id) if event.type == kind]

    def propose(self, cand: dict[str, Any], *, task: Any = None, artifact_hash: str = "a" * 64,
                result: str = "result-1", deployment: DeploymentPolicy | None = None, **kwargs: Any) -> dict[str, Any]:
        """经台账的写入口递交一条候选动作（执行者结果核验通过后，产品就是这样记账的）。"""
        task = task or self.leaf
        return self.service.propose_action(
            cand, mission_id=self.mission_id, task_id=task.id, result_id=result,
            attempt_id=f"{task.id}:attempt-1", artifact_id=f"artifact-{artifact_hash[:8]}",
            artifact_hash=artifact_hash, connectors=self.connectors,
            deployment=deployment or self.deployment, **kwargs)

    def publish_ledger(self) -> list[str]:
        """发布服务自己账本里的每条记录的状态（服务被调用过几次、每次走到哪一步）。"""
        if not self.publish.ledger_path.exists():
            return []
        import json  # noqa: PLC0415

        return [json.loads(line)["state"] for line in
                self.publish.ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def published_files(self) -> list[str]:
        return sorted(p.relative_to(self.published).as_posix() for p in self.published.rglob("*") if p.is_file())


def confirm_effects(product: ProductWorld, mission_id: str) -> dict[str, Any]:
    """人在确认页做的事（与代表用例 3 同一份）：内容要求照单确认；全部 ``action:`` 要求作为一个
    必须完成的效果，挂在根义务上，完成标准选"内容哈希一致"。"""

    workspace = product.control.snapshot(mission_id)["snapshot"]["operation_workspace"]
    assert workspace["state"] == "CONFIRMATION_REQUIRED", workspace
    actions = [c["id"] for c in workspace["criteria"] if c["statement"].startswith("action:")]
    content = [c["id"] for c in workspace["criteria"] if c["required"] and c["id"] not in actions]
    [obligation] = workspace["obligations"]
    milestone = next(m for m in workspace["milestones"] if m["id"] == "CONTENT_HASH_VERIFIED")
    ref = workspace["requirements_ref"]
    return product.control.approve_operation_completion_spec({
        "mission_id": mission_id, "command_id": "confirm-publish-completion",
        "expected_requirements_ref": ref,
        "proposal": {
            "schema_version": 1, "mission_id": mission_id,
            "requirements_ref": {"id": ref["id"], "revision": ref["revision"], "content_hash": ref["content_hash"]},
            "mode": "REQUIRED_EFFECTS", "content_criterion_ids": content,
            "effects": [{
                "effect_key": "publish", "source_slot_key": "publish",
                "obligation_id": obligation["id"], "criterion_ids": actions,
                "required_milestone": milestone["id"],
                "milestone_policy_ref": milestone["milestone_policy_ref"],
                "evidence_policy_ref": milestone["evidence_policy_ref"],
            }],
        },
    })


@asynccontextmanager
async def operation_world(tmp_path: Path, *, key: str, criteria: tuple[str, ...] = PUBLISH_CRITERIA,
                          goal: str = "写一份周报 reports/weekly.md 并发布", hold_worker: bool = False,
                          publish_setup: Callable[[FilePublishConnector], None] | None = None,
                          worker: Callable[[Any], Any] | None = None,
                          ) -> AsyncIterator[OperationWorld]:
    """产品部署上建好一个带发布要求的任务，人已在确认页确认完成映射（主循环还没开跑）。

    ``hold_worker``：执行者的模型调用一律扣住（见 :func:`ledger_world`）。``publish_setup`` 在
    任务建出前拿到真实的发布服务，用来模拟外界（服务慢、连接中断）。``worker`` 换掉脚本化执行者。"""

    published = tmp_path / "published"
    published.mkdir()
    publish = FilePublishConnector(published, tmp_path / "root" / "connectors" / "file_publish")
    if publish_setup is not None:
        publish_setup(publish)
    provider = LayeredScriptedProvider() if worker is None else LayeredScriptedProvider(worker=worker)
    if hold_worker:
        provider.held.add("worker")
    try:
        async with product_world(tmp_path / "root", provider, connectors={"file_publish": publish},
                                 deployment_policy=ENABLED, **CONFIG) as product:
            created = product.create({"goal": goal, "success_criteria": list(criteria), "idempotency_key": key})
            mission_id = created["mission_id"]
            await product.drain()
            # 自动模式不代签带 action: 的任务：确认页等着人
            assert str(product.store.get_mission(mission_id).status.value) == "CREATED"
            assert confirm_effects(product, mission_id)["authority"]["kind"] == "USER_CONFIRMED"
            yield OperationWorld(product, mission_id, publish, published, provider)
    finally:
        provider.release.set()


@asynccontextmanager
async def ledger_world(tmp_path: Path, *, key: str = "ledger", criteria: tuple[str, ...] = LEDGER_CRITERIA,
                       worker: Callable[[Any], Any] | None = None) -> AsyncIterator[OperationWorld]:
    """D′：主循环真跑到第一版计划提交（提做法 → 独立审阅 → 采用），停在叶子刚派发、执行者那次
    模型调用被扣住；任务 ACTIVE，台账还是空的。主循环此后不再跑。"""

    async with operation_world(tmp_path, key=key, criteria=criteria, goal="写一份变更说明并按要求发布",
                               hold_worker=True, worker=worker) as world:
        await run_until(world.product, world.provider.entered.is_set)
        revision = HtnStore(world.store).active_plan_revision(world.mission_id)
        assert revision is not None and revision.revision == 1
        assert str(world.mission.status.value) == "ACTIVE"
        assert world.store.list_actions(world.mission_id) == []
        yield world


async def until_pending(world: OperationWorld, *, timeout: float = 30.0) -> dict[str, Any]:
    """跑主循环，直到系统按已批准效果准备好申请单、等人批准；返回那条动作（主循环随后停下）。"""

    def pending() -> bool:
        return any(a.get("state") == "PENDING" for a in world.control.approvals(world.mission_id))

    await run_until(world.product, pending, timeout=timeout)
    [action] = world.store.list_actions(world.mission_id)
    assert action["state"] == "AWAITING_APPROVAL" and action["reason_source"] == "system", action
    assert not world.published_files() and world.publish_ledger() == []  # 没批准之前什么都没发布
    return action


__all__ = (
    "ALICE",
    "BOB",
    "ENABLED",
    "LEDGER_CRITERIA",
    "LEDGER_TARGETS",
    "PUBLISH",
    "PUBLISH_CRITERIA",
    "TARGET",
    "OperationWorld",
    "candidate",
    "confirm_effects",
    "ledger_world",
    "operation_world",
    "run_until",
    "until_pending",
)
