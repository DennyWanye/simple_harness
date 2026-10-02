# SPDX-License-Identifier: Apache-2.0
"""分层世界里"可以直接驱动的步骤"（删旧平面模式 第 1 步，2026-10-02）。

老目录的机制测试（记账、崩溃恢复、租约、派发、额度、产物、校验层……）一直借"先造一个
平面任务"来得到可驱动的 Task 行。这里给出分层版本，两档：

* :func:`leaf_world` —— 提交层：一个带协议绑定、完成范围已确认、计划已提交的分层
  Mission，根做法就是 ``leaves`` 里列的几个原子步骤；每个步骤是真实物化出来的 Task 行，
  可以直接 ``create_attempt`` / ``record_result`` / ``reject_result``。只适合"第一次尝试"。
* :func:`loop_leaf` —— 真实主循环：规划器选做法、计划提交；失败后的"再试一次"按生产
  顺序先拿规划器的"原样重试"决定（分层任务没有这个决定就建不出下一次尝试）。

这两档世界与分层测试枢纽是同一个：**没装保证通道、没开执行图**。老平面测试保护的也正是
这些分支，迁过来保护范围不增不减。产品同形的世界（分层 + 执行图 + 保证通道）SDK 测试里
还没有；在那里尝试只能由真实派发建、执行者必须是真的，手工伪造不了——那是做执行图那
一轮要搭的（见 ``plans/2026-09-27-desktop-next/删旧平面模式-方案.md``）。
"""
from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
for extra in (HERE, HERE / "fixtures" / "htn"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from htn_world import Env, method, param, step, task_binding  # noqa: E402
from scripted_plans import (  # noqa: E402
    apply_scripted_plan,
    approve_content_only_completion,
    plan_revision_proposal_step,
)

from agent_orchestrator.contracts import Budget  # noqa: E402
from agent_orchestrator.contracts.htn import TaskForm  # noqa: E402
from agent_orchestrator.contracts.obligations import Obligation  # noqa: E402
from agent_orchestrator.orchestrator.commit_service import (  # noqa: E402
    CommitService,
    MissionSpec,
    Reservation,
)
from agent_orchestrator.orchestrator.hierarchical_dispatch import HierarchicalDispatch  # noqa: E402
from agent_orchestrator.orchestrator.plan_commits import PlanPrincipal  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.storage.obligation_store import ObligationStore  # noqa: E402
from agent_orchestrator.storage.store import Store  # noqa: E402

TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list", "run_tests")
ROOT_TASK = "task-root"
ROOT_DUTY = "obl-root"
GOAL_TYPE = "leafw.goal"
STEP_TYPE = "leafw.step"
METHOD_ID = "leafw.method"
FUEL = 8


# --------------------------------------------------------------------------------------
# 提交层
# --------------------------------------------------------------------------------------


@dataclass
class LeafWorld:
    service: CommitService
    mission: Any
    dispatch: HierarchicalDispatch
    env: Env
    path: Path
    #: 步骤名 → 物化出来的 Task 行（提交计划那一刻读到的）
    tasks: dict[str, Any]

    @property
    def store(self) -> Store:
        return self.service.store

    def task(self, name: str) -> Any:
        """这个步骤当前的 Task 行（重新读库）。"""

        return self.store.get_task(self.tasks[name].id)


def leaf_world(
    tmp_path,
    *,
    key: str = "leaf-world",
    leaves: Sequence[str] = ("a",),
    ordering: Sequence[tuple[str, str]] = (),
    goal: str = "交付几个可以逐步验收的步骤",
    success_criteria: Sequence[str] = ("file:a.md",),
    tenant_id: str = "tenant-leaf",
    tools: Sequence[str] = TOOLS,
    budget: Budget | None = None,
    task_max_tokens: int | None = None,
    clock: Any = None,
    name: str = "orchestrator.db",
    spec_overrides: Mapping[str, Any] | None = None,
) -> LeafWorld:
    """一个已提交计划的分层 Mission；``leaves`` 的每一项是根做法的一个原子步骤，
    ``ordering`` 给出"谁在谁之前"。"""

    path = Path(tmp_path) / name
    store = Store.open(path) if clock is None else Store.open(path, clock=clock)
    service = CommitService(store, task_max_tokens=task_max_tokens)
    mission, _ = service.create_mission(
        MissionSpec(
            **{
                "goal": goal,
                "success_criteria": tuple(success_criteria),
                "tenant_id": tenant_id,
                "idempotency_key": key,
                "allowed_tools": tuple(tools),
                "budget": budget or Budget(max_tokens=200_000, max_attempts=12),
                **dict(spec_overrides or {}),
            }
        )
    )
    env = Env(mission=mission.id)
    env.register_type(
        GOAL_TYPE,
        form=TaskForm.COMPOUND,
        parameters=(("subject", "string"),),
        criteria=("c-root",),
        domain="leafw",
    )
    names = tuple(leaves)
    # 每个步骤一个任务类型：计划网络里的出现项编号是哈希，靠类型名把步骤认回来。
    for item in names:
        env.register_type(
            f"{STEP_TYPE}.{item}",
            parameters=(("subject", "string"),),
            outputs=(("result", "leafw.result"),),
            capabilities=("leafw.read",),
            domain="leafw",
        )
    contract = method(
        METHOD_ID,
        GOAL_TYPE,
        parameter_schema=f"{GOAL_TYPE}.params",
        steps=tuple(
            step(
                item,
                f"{STEP_TYPE}.{item}",
                TaskForm.PRIMITIVE,
                {"subject": param("subject")},
                capabilities=("leafw.read",),
            )
            for item in names
        ),
        ordering=tuple(ordering),
        links=(("c-root", names[-1], "c-done"),),
        finalizer=names[-1],
    )
    receipt = env.admit(contract)
    assert receipt.admitted, receipt.problems
    binding = task_binding(
        env, GOAL_TYPE, task_id=ROOT_TASK, obligation=ROOT_DUTY, parameters={"subject": "alpha"}
    )
    ObligationStore(store).register(
        Obligation(
            obligation_id=ROOT_DUTY,  # type: ignore[arg-type]
            mission_id=mission.id,
            requirement_refs=("req-1",),
            goal_signature_id=GOAL_TYPE,
        ),
        recursion_fuel=FUEL,
    )
    HtnStore(store).put_task_semantics(mission.id, binding)
    HtnStore(store).register_method(contract, env.registry.registration(contract.method_ref()))
    approve_content_only_completion(service, mission, binding, command_id=f"approve-{key}")
    service.begin_planning(mission.id)
    env.semantics = HtnStore(store)
    dispatch = HierarchicalDispatch(store, service, planning=env)
    reference = contract.method_ref()
    outcome = apply_scripted_plan(
        dispatch,
        mission.id,
        plan_revision_proposal_step(
            proposal_id="prop-1",
            expected_plan_revision=0,
            read_set=[
                {
                    "kind": "method",
                    "id": reference.method_id,
                    "semantic_revision": reference.version,
                    "content_hash": reference.content_hash,
                }
            ],
            operations=[
                {
                    "op": "refine",
                    "goal_id": ROOT_TASK,
                    "obligation_id": ROOT_DUTY,
                    "method_ref": {
                        "id": reference.method_id,
                        "version": reference.version,
                        "content_hash": reference.content_hash,
                    },
                    "bindings": {},
                }
            ],
        ),
        principal=PlanPrincipal("manager-1", "mission", 0),
        command_id="cmd-a",
    )
    assert outcome.committed, outcome.last_reason
    network = dispatch.network(mission.id)
    by_step: dict[str, Any] = {}
    for spec in network.occurrences:
        signature = str(network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id)
        if signature.startswith(f"{STEP_TYPE}."):
            by_step[signature[len(STEP_TYPE) + 1:]] = store.get_task(str(spec.task_id))
    assert set(by_step) == set(names), (sorted(by_step), names)
    return LeafWorld(
        service=service,
        mission=store.get_mission(mission.id),
        dispatch=dispatch,
        env=env,
        path=path,
        tasks=by_step,
    )


def drive_to_running(
    service: CommitService,
    task: Any,
    *,
    owner: str = "orch-1",
    agent: str = "agent-1",
    turn: str = "turn-1",
    role: str = "worker",
    tokens: int = 4_000,
    input_hash: str = "h",
):
    """把一个步骤的第一次尝试推进到"已创建、已认领、已提交给执行者"。"""

    attempt, intent = service.create_attempt(
        task.id,
        role=role,
        model="agent-model",
        prompt_version="worker-v2",
        context_version="ctx",
        reservation=Reservation(tokens=tokens, cost_micros=0),
        intent_config={"agent_config": {}, "message": "do"},
        input_hash=input_hash,
    )
    service.claim_intent(intent.intent_id, owner=owner, lease_seconds=60)
    service.record_agent_created(intent.intent_id, agent_id=agent, expected_turn_id=turn)
    service.record_submitted(intent.intent_id, receipt={"turn_id": turn, "seq": 1})
    return service.store.get_attempt(attempt.id)


# --------------------------------------------------------------------------------------
# 真实主循环：失败后的"再试一次"要先有规划器的"原样重试"决定
# --------------------------------------------------------------------------------------


@dataclass
class LoopLeaf:
    """主循环里一个已提交计划的分层 Mission，和其中一个可以直接驱动的步骤。"""

    loop: Any
    mission: Any
    dispatch: Any
    task_id: str
    _ordinal: int = 1
    _turn: int = 0

    @property
    def service(self) -> CommitService:
        return self.loop.commit

    @property
    def store(self) -> Store:
        return self.loop.store

    @property
    def task(self) -> Any:
        return self.store.get_task(self.task_id)

    def running(self, *, retry_of: str | None = None, tokens: int = 1_000) -> Any:
        """新建一次尝试并推进到"已提交给执行者"；``retry_of`` 是它要重做的那次失败。"""

        self._turn += 1
        turn = f"turn-{self._turn}"
        attempt, intent = self.service.create_attempt(
            self.task_id,
            role="worker",
            model="fixture-worker",
            prompt_version="fixture-worker-v1",
            context_version="leaf-world-v1",
            reservation=Reservation(tokens=tokens, cost_micros=0),
            intent_config={"message": "do the leaf"},
            input_hash=f"{self._turn:064x}",
            inputs=(),
            retry_of=retry_of,
        )
        self.service.claim_intent(intent.intent_id, owner=self.loop._owner, lease_seconds=60)
        self.service.record_agent_created(
            intent.intent_id, agent_id=f"agent-{self._turn}", expected_turn_id=turn
        )
        self.service.record_submitted(intent.intent_id, receipt={"turn_id": turn, "seq": 1})
        return self.store.get_attempt(attempt.id)

    def turn_of(self, attempt: Any) -> str:
        return self.store.get_intent_for_subject(attempt.id).expected_turn_id

    async def authorize_retry(self, failed: Any) -> Any:
        """按生产顺序拿到"原样重试"的许可：系统把失败记成修复请求，这一轮规划决定是
        "同一做法再试一次"，决定提交后许可才存在。返回提交了的决定行。"""

        from test_h4_retry_runtime_entry import repair, retry_payload

        from agent_orchestrator.orchestrator.planning_repair_requests import collect_triggers

        collect_triggers(self.loop, self.store.get_mission(self.mission.id))
        self._ordinal += 1
        _, row = await repair(
            self.loop,
            self.store.get_mission(self.mission.id),
            self.dispatch,
            self.task_id,
            lambda package: retry_payload(
                self.dispatch, self.mission, self.task_id, failed.id, package
            ),
            ordinal=self._ordinal,
        )
        assert row["status"] == "COMMITTED", row["detail_json"]
        return row


async def loop_leaf(loop: Any, tmp_path, *, key: str) -> LoopLeaf:
    """在一个真实的 ``Orchestrator`` 里：建分层 Mission、规划器选做法、计划提交。"""

    from test_h4_retry_runtime_entry import refined

    mission, dispatch, task_id = await refined(loop, tmp_path, key)
    return LoopLeaf(loop=loop, mission=mission, dispatch=dispatch, task_id=task_id)


def loop_config(tmp_path) -> Any:
    from test_h1i_production_entry import _config

    return _config(Path(tmp_path))


__all__ = (
    "LeafWorld",
    "LoopLeaf",
    "drive_to_running",
    "leaf_world",
    "loop_config",
    "loop_leaf",
)
