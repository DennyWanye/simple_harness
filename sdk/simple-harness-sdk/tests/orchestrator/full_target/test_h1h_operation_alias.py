# ruff: noqa: E501
"""O02 Store-backed regression draft; do not map this file to O09.

The tests call the trusted ``planning_origin`` seam of the ledger's write entry directly;
the real candidate-result producer path is ``test_h1h_operation_two_real_producers.py``.
They cover ActionCommits and the authoritative Store bridge, but do not prove producer
wiring or crash recovery.

HTN 补齐阶段 A′（分诊裁决③ D′）：任务建在产品同形世界上（``helpers_step07.ledger_world``：主循环
真跑到第一版计划提交、执行者被扣住）。``_origin`` 经操作绑定唯一的写入函数 ``HtnStore.bind_operation``
写绑定（裁决①c：它在产品里的真实写入方是操作物化 ``operation_materialization.py``），动作只经台账
写入口 ``propose_action`` 记账。

偏离：
* 删 ``test_o02_same_target_different_operation_does_not_join_latest``：产品上能挂效果的连接器只有
  文件发布，一个目标只有 ``publish`` 一种操作，"同一目标、不同操作"在产品上造不出来；"别名不串
  链接"由本文件其余三条与 ``test_h1h_operation_two_real_producers.py`` 守住。
* 删 ``test_o03_unknown_action_outside_active_plan_membership_still_blocks``：它直接改写台账行造出
  "结果不明"（违反裁决①b2 / ③条件 2）。真实的"未了结"动作（发布服务拒绝 / 连接中断）经全任务
  范围的存储读取器挡住操作闸门，由 ``step07/test_action_ledger.py::test_the_ledger_never_rewrites_what_reality_did``
  的 ``rejected`` / ``lost`` 两档覆盖；O03 门禁节点见 ``h1h_stage_runner.py``。
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

import pytest

_SDK_TESTS = Path(__file__).resolve().parent.parent
for _directory in (_SDK_TESTS / "full_target", _SDK_TESTS / "step07"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

from helpers_step07 import ENABLED, candidate, ledger_world  # noqa: E402
from test_htn_store import HASH_B, envelope  # noqa: E402

from agent_orchestrator.orchestrator.action_commits import (  # noqa: E402
    ActionCommitError,
)
from agent_orchestrator.runtime.planning_operations import (  # noqa: E402
    BoundPlanningOperationOrigin,
)
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.storage.planning_admission_store import (  # noqa: E402
    PlanningAdmissionStore,
)
from agent_orchestrator.storage.store import StoreConflict  # noqa: E402


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _origin(service, mission, task, *, operation_id: str, occurrence: str, request_hash=None):
    frozen = dataclasses.replace(
        envelope(
            operation_id=operation_id,
            occurrence=occurrence,
            **({} if request_hash is None else {"request_hash": request_hash}),
        ),
        mission_id=mission.id,
        scope_id="mission",
    )
    HtnStore(service.store).bind_operation(frozen, principal_id="origin-principal")
    binding = PlanningAdmissionStore(service.store).get_operation_binding(occurrence)
    assert binding is not None
    return BoundPlanningOperationOrigin(
        operation_id=binding["operation_id"],
        operation_occurrence_id=binding["operation_occurrence_id"],
        request_hash=binding["request_hash"],
        mission_id=binding["mission_id"],
        envelope_hash=binding["envelope_hash"],
        principal_id=binding["principal_id"],
        scope_id=binding["scope_id"],
        obligation_id=binding["obligation_id"],
        producer_task_id=task.id,
        producer_htn_occurrence_id=f"htn-{occurrence}",
        producer_contract_revision=1,
        producer_plan_revision=1,
        provenance_receipt_id=f"receipt-{occurrence}",
    )


def _propose(service, mission, task, connectors, value, origin, *, target="reports/weekly.md"):
    return service.propose_action(
        candidate(target=target, value=value),
        mission_id=mission.id,
        task_id=task.id,
        result_id=f"result-{origin.operation_occurrence_id}",
        attempt_id=f"attempt-{origin.operation_occurrence_id}",
        artifact_id=f"artifact-{origin.operation_occurrence_id}",
        artifact_hash=("a" if value == "on" else "b") * 64,
        connectors=connectors,
        deployment=ENABLED,
        planning_origin=origin,
    )


def test_o02_second_occurrence_cannot_alias_or_overwrite_original_action_link(tmp_path):
    async def case():
        async with ledger_world(tmp_path, key="o02-alias") as w:
            service, mission, tasks = w.service, w.mission, w.tasks
            first_origin = _origin(
                service, mission, tasks["A"], operation_id="operation-first", occurrence="occ-first"
            )
            second_origin = _origin(
                service, mission, tasks["B"], operation_id="operation-second", occurrence="occ-second"
            )
            other_origin = _origin(
                service, mission, tasks["B"], operation_id="operation-other", occurrence="occ-other"
            )
            first = _propose(service, mission, tasks["A"], w.connectors, "on", first_origin)
            adapter = PlanningAdmissionStore(service.store)
            original_link = adapter.get_operation_action_link("operation-first")
            original_action = service.store.get_action(first["action_key"])
            original_events = tuple(service.store.list_events(mission.id))

            with pytest.raises(ActionCommitError, match="operation_occurrence_alias_conflict"):
                _propose(service, mission, tasks["B"], w.connectors, "on", second_origin)

            assert adapter.get_operation_action_link("operation-first") == original_link
            assert adapter.get_operation_action_link("operation-second") is None
            assert service.store.get_action(first["action_key"]) == original_action
            assert tuple(service.store.list_events(mission.id)) == original_events

            # a distinct operation on another target gets its own action and its own exact link,
            # never the latest action of the Mission
            other = _propose(service, mission, tasks["B"], w.connectors, "on", other_origin, target="a.md")
            assert other["action_id"] != first["action_id"]
            links = PlanningAdmissionStore(service.store).list_operation_action_links(mission.id)
            assert {item["operation_id"]: item["action_key"] for item in links} == {
                "operation-first": first["action_key"],
                "operation-other": other["action_key"],
            }

    asyncio.run(case())


def test_o02_same_origin_same_action_replay_is_idempotent(tmp_path):
    """Replay compares the same persisted identity without rewriting the action."""

    async def case():
        async with ledger_world(tmp_path, key="o02-replay") as w:
            service, mission, tasks = w.service, w.mission, w.tasks
            origin = _origin(
                service, mission, tasks["A"], operation_id="operation-replay", occurrence="occ-replay"
            )
            first = _propose(service, mission, tasks["A"], w.connectors, "on", origin)
            adapter = PlanningAdmissionStore(service.store)
            before_link = adapter.get_operation_action_link(origin.operation_id)
            before_action = service.store.get_action(first["action_key"])
            before_events = tuple(service.store.list_events(mission.id))
            before_rows = tuple(service.store.list_actions(mission.id))
            before_changes = service.store.connection.total_changes

            replay = _propose(service, mission, tasks["A"], w.connectors, "on", origin)

            assert replay == first
            assert adapter.get_operation_action_link(origin.operation_id) == before_link
            assert service.store.get_action(first["action_key"]) == before_action
            assert tuple(service.store.list_actions(mission.id)) == before_rows
            assert tuple(service.store.list_events(mission.id)) == before_events
            assert service.store.connection.total_changes == before_changes

    asyncio.run(case())


def test_o02_same_operation_id_with_different_request_hash_is_conflict(tmp_path):
    async def case():
        async with ledger_world(tmp_path, key="o02-conflict") as w:
            service, mission, tasks = w.service, w.mission, w.tasks
            _origin(service, mission, tasks["A"], operation_id="operation-stable", occurrence="occ-a")

            with pytest.raises(StoreConflict, match="OPERATION_PAYLOAD_CONFLICT"):
                _origin(
                    service,
                    mission,
                    tasks["B"],
                    operation_id="operation-stable",
                    occurrence="occ-b",
                    request_hash=HASH_B,
                )

            bindings = PlanningAdmissionStore(service.store).list_operation_bindings(mission.id)
            assert [(item["operation_id"], item["operation_occurrence_id"]) for item in bindings] == [
                ("operation-stable", "occ-a")
            ]

    asyncio.run(case())
