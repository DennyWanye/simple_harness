# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The one planning protocol: defaults, the durable binding, and the removed name.

2026-10-01: the proposal-text protocol and every historical package pairing are gone.
A charter that names nothing is a hierarchical Mission on ``planning-decision-v1``;
the old name is refused with a message that says it was removed; a hierarchical
Mission already in the library without a current binding is stopped by name.

HTN 补齐阶段 A′：建任务一律经产品那一份部署组装（:func:`product_world`；建任务事务里初始化根、
绑定执行图、走保证通道），不再裸建 ``CommitService``。存储损坏用 SQL 改坏已存字节造（分诊裁决①b1）；
建任务中途写入失败用 SQLite 触发器在真实事务里注入（外界真会发生的写入故障），不再替换产品的
``_emit``。"冻结的规划部署身份与本进程不同"改为：真跑一轮规划冻结身份 → 停机 → 改坏冻结记录的字节 →
重开（原用例手插一条产品自己才写的事件，违反①b2）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace

import pytest

from agent_orchestrator.api.facade import FacadeError
from agent_orchestrator.contracts import Budget, ContractError, MissionStatus
from agent_orchestrator.contracts import planning_decisions as decision_contracts
from agent_orchestrator.contracts.planning_decisions import (
    PLANNING_DECISION_V1,
    UnsupportedPlanningPackage,
)
from agent_orchestrator.orchestrator.commit_service import (
    CommitRejected,
    MissionConflict,
    MissionSpec,
)
from agent_orchestrator.orchestrator.plan_commits import (
    HIERARCHICAL_SEMANTICS,
    semantics_of,
)
from agent_orchestrator.orchestrator.planning_protocol_binding import (
    PLANNING_PROTOCOLS,
    current_planning_protocol,
    planning_protocol_binding_hash,
    planning_protocol_for_mission,
    planning_protocol_replay_conflict,
)
from agent_orchestrator.runtime.role_templates import (
    PLANNING_DECISION_PACKAGE_VERSION,
    PLANNING_DECISION_PROMPT_VERSION,
)
from agent_orchestrator.storage.store import Store
from agent_orchestrator.testing.product_world import TENANT, product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider
from agent_orchestrator.testing.fixtures import lift_immutable_guards

REMOVED_NAME = "legacy-plan-proposal-v1"


def _spec(key: str, **kwargs: object) -> MissionSpec:
    return MissionSpec(
        goal="g",
        success_criteria=("ok",),
        tenant_id=TENANT,
        idempotency_key=key,
        budget=Budget(max_tokens=1000, max_attempts=1),
        **kwargs,
    )


def _spec_with_field_set_behind_the_constructor(key: str, protocol_version: str) -> MissionSpec:
    """A spec whose ``planning_protocol_version`` slot was assigned directly (§8.1).

    ``object.__setattr__`` writes the frozen field without ``__post_init__`` — the same
    escape hatch a deserialiser or a hand-built record has.  The door must refuse the
    value regardless, before the document is digested or stored.
    """

    spec = _spec(key)
    object.__setattr__(spec, "planning_protocol_version", protocol_version)
    return spec


def _world(tmp_path, provider=None):
    """产品同形部署（不跑主循环时就是"经产品组装建任务"）。"""
    return product_world(tmp_path / "root", provider if provider is not None else LayeredScriptedProvider())


def _create(world, key: str):
    created = world.create({"goal": "写一份 NOTES.md", "success_criteria": ["file:NOTES.md"], "idempotency_key": key})
    return world.store.get_mission(created["mission_id"])


def binding_rows(store: Store, mission_id: str) -> int:
    return store.connection.execute(
        "SELECT count(*) FROM mission_planning_protocols WHERE mission_id = ?", (mission_id,)
    ).fetchone()[0]


# ---------------------------------------------------------------------------------------
# Defaults: a charter that names nothing is hierarchical, on the one protocol.
# ---------------------------------------------------------------------------------------


def test_the_default_spec_is_hierarchical_on_the_current_protocol(tmp_path) -> None:
    spec = _spec("default")
    assert spec.orchestration_semantics_version == HIERARCHICAL_SEMANTICS
    assert spec.planning_protocol_version == PLANNING_DECISION_V1
    document = spec.to_json()
    assert document["orchestration_semantics_version"] == HIERARCHICAL_SEMANTICS
    assert document["planning_protocol_version"] == PLANNING_DECISION_V1

    async def case() -> None:
        async with _world(tmp_path) as world:
            store = world.store
            mission, created = world.loop.commit.create_mission(spec)
            assert created
            assert semantics_of(mission) == HIERARCHICAL_SEMANTICS
            binding = planning_protocol_for_mission(store, mission.id)
            assert binding is not None
            assert binding["protocol_version"] == PLANNING_DECISION_V1
            assert binding["package_version"] == PLANNING_DECISION_PACKAGE_VERSION
            assert binding["prompt_version"] == PLANNING_DECISION_PROMPT_VERSION
            assert current_planning_protocol(store, mission.id) == binding

    asyncio.run(case())


def test_the_request_parser_default_is_hierarchical_on_the_current_protocol() -> None:
    from agent_orchestrator.api.missions import spec_from_request

    plain = spec_from_request("tenant", {"idempotency_key": "y"})
    assert plain.orchestration_semantics_version == HIERARCHICAL_SEMANTICS
    assert plain.planning_protocol_version == PLANNING_DECISION_V1
    named = spec_from_request(
        "tenant", {"idempotency_key": "x", "planning_protocol_version": PLANNING_DECISION_V1}
    )
    assert named.to_json() == replace(plain, idempotency_key="x").to_json()


# ---------------------------------------------------------------------------------------
# The removed protocol name is refused, by name, at every door.
# ---------------------------------------------------------------------------------------


def test_the_removed_protocol_name_no_longer_exists_in_the_contract() -> None:
    assert PLANNING_PROTOCOLS == frozenset({"planning-decision-v1"})
    assert PLANNING_DECISION_V1 == "planning-decision-v1"
    assert not hasattr(decision_contracts, "LEGACY_PLANNING_PROTOCOL")
    from agent_orchestrator.orchestrator import commit_service

    assert not hasattr(commit_service, "LEGACY_PLANNING_PROTOCOL")


def test_the_removed_protocol_name_is_refused_with_a_message_that_says_so(tmp_path) -> None:
    with pytest.raises(ContractError, match="was removed"):
        _spec("old", planning_protocol_version=REMOVED_NAME)

    from agent_orchestrator.api.missions import MissionRequestError, spec_from_request

    with pytest.raises(MissionRequestError, match="was removed"):
        spec_from_request(
            "tenant", {"idempotency_key": "old", "planning_protocol_version": REMOVED_NAME}
        )

    async def case() -> None:
        async with _world(tmp_path) as world:
            forged = _spec_with_field_set_behind_the_constructor("forged-old", REMOVED_NAME)
            with pytest.raises(CommitRejected, match="was removed"):
                world.loop.commit.create_mission(forged)
            assert world.store.find_mission(TENANT, "forged-old") is None

    asyncio.run(case())


def test_unknown_protocol_values_are_rejected(tmp_path) -> None:
    with pytest.raises(ContractError, match="planning protocol"):
        _spec("unknown", planning_protocol_version="planning-decision-v99")
    for invalid in ([], {}):
        with pytest.raises(ContractError, match="planning protocol"):
            _spec("invalid-type", planning_protocol_version=invalid)

    from agent_orchestrator.api.missions import MissionRequestError, spec_from_request

    for value in (1, {}, [PLANNING_DECISION_V1], "planning-decision-v99"):
        with pytest.raises(MissionRequestError, match="planning protocol"):
            spec_from_request(
                "tenant", {"idempotency_key": "bad", "planning_protocol_version": value}
            )

    async def case() -> None:
        async with _world(tmp_path) as world:
            forged = _spec_with_field_set_behind_the_constructor("forged", "planning-decision-v99")
            with pytest.raises(CommitRejected, match="planning protocol"):
                world.loop.commit.create_mission(forged)
            assert world.store.find_mission(TENANT, "forged") is None

    asyncio.run(case())


# ---------------------------------------------------------------------------------------
# The durable binding of a hierarchical Mission (created through the product deployment).
# ---------------------------------------------------------------------------------------


def test_creation_writes_one_binding_with_the_frozen_hash_and_survives_a_restart(tmp_path) -> None:
    document = {
        "protocol_version": PLANNING_DECISION_V1,
        "package_version": PLANNING_DECISION_PACKAGE_VERSION,
        "prompt_version": PLANNING_DECISION_PROMPT_VERSION,
    }
    expected = hashlib.sha256(
        json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()

    async def create() -> str:
        async with _world(tmp_path) as world:
            mission = _create(world, "new")
            binding = planning_protocol_for_mission(world.store, mission.id)
            assert binding is not None and binding["binding_hash"] == expected
            assert planning_protocol_binding_hash(PLANNING_DECISION_V1) == expected
            assert binding_rows(world.store, mission.id) == 1
            return mission.id

    mission_id = asyncio.run(create())

    async def reopen() -> None:  # a restart reads the same binding
        async with _world(tmp_path) as world:
            assert current_planning_protocol(world.store, mission_id)["protocol_version"] == PLANNING_DECISION_V1

    asyncio.run(reopen())


def test_binding_is_transactional_on_creation_failure(tmp_path) -> None:
    """建任务事务里，绑定写下之后的那次写入失败（磁盘满 / 库被锁这类外界故障，用触发器在真实事务
    里注入；触发器只在绑定已写入时才报错，所以它报错就证明绑定确实写过）→ 整体回滚，什么也不留；
    故障消失后同一请求照常建成。"""

    async def case() -> None:
        async with _world(tmp_path) as world:
            store = world.store
            store.connection.execute(
                "CREATE TEMP TRIGGER fail_after_binding BEFORE INSERT ON events "
                "WHEN NEW.type = 'MissionCreated' AND "
                "(SELECT count(*) FROM mission_planning_protocols WHERE mission_id = NEW.mission_id) = 1 "
                "BEGIN SELECT RAISE(ABORT, 'disk full'); END"
            )
            with pytest.raises(Exception, match="disk full"):
                _create(world, "rollback")
            assert store.connection.execute("SELECT count(*) FROM mission_planning_protocols").fetchone()[0] == 0
            assert store.find_mission(TENANT, "rollback") is None
            assert store.list_missions() == []
            store.connection.execute("DROP TRIGGER fail_after_binding")
            mission = _create(world, "rollback")
            assert binding_rows(store, mission.id) == 1

    asyncio.run(case())


def test_replay_is_idempotent_and_keeps_exactly_one_binding_row(tmp_path) -> None:
    async def case() -> None:
        async with _world(tmp_path) as world:
            store, service = world.store, world.loop.commit
            mission = _create(world, "idem")
            first = planning_protocol_for_mission(store, mission.id)
            again = _create(world, "idem")
            assert again.id == mission.id
            assert binding_rows(store, mission.id) == 1
            assert planning_protocol_for_mission(store, mission.id) == first
            assert planning_protocol_replay_conflict(store, mission.id, PLANNING_DECISION_V1) is None
            # 提交层收到同一幂等键的同一份规格（门面补齐后的那份）也是重放。
            spec_hash = store.find_mission(TENANT, "idem")[1]
            assert isinstance(spec_hash, str) and len(spec_hash) == 64
            assert service is world.loop.commit

    asyncio.run(case())


def test_a_replay_against_a_tampered_binding_is_a_conflict(tmp_path) -> None:
    async def case() -> None:
        async with _world(tmp_path) as world:
            mission = _create(world, "tampered")
            lift_immutable_guards(world.store.connection, "mission_planning_protocols")
            world.store.connection.execute(  # ①b1: the stored binding bytes are damaged
                "UPDATE mission_planning_protocols SET package_version = 7 WHERE mission_id = ?", (mission.id,))
            with pytest.raises(FacadeError) as refused:
                _create(world, "tampered")
            assert refused.value.code == "conflict"
            cause = refused.value.__cause__ or refused.value.__context__
            assert isinstance(cause, MissionConflict) and "durably bound" in str(cause)

    asyncio.run(case())


def test_the_durable_binding_ignores_the_ambient_environment(tmp_path, monkeypatch) -> None:
    """§8.2: the mode is never guessed from the environment, on write or on read."""

    monkeypatch.setenv("SIMPLE_HARNESS_PLANNING_PROTOCOL", REMOVED_NAME)
    monkeypatch.setenv("PLANNING_PROTOCOL_VERSION", REMOVED_NAME)

    async def case() -> None:
        async with _world(tmp_path) as world:
            enabled = _create(world, "env-enabled")
            assert planning_protocol_for_mission(world.store, enabled.id)["protocol_version"] == PLANNING_DECISION_V1

    asyncio.run(case())


def test_policy_snapshot_digest_does_not_include_planning_protocol(tmp_path) -> None:
    from agent_orchestrator.governance import policies
    from agent_orchestrator.runtime.assembly import OrchestratorConfig

    snapshot = policies.policy_snapshot(OrchestratorConfig(evidence_root=tmp_path / "a"))
    assert "planning_protocol_version" not in snapshot["config"]
    assert "planning_protocol_version" not in json.dumps(snapshot)
    assert "planning_protocol_version" not in policies.SNAPSHOT_FIELDS
    assert "planning_protocol_version" not in {
        field.name for field in __import__("dataclasses").fields(OrchestratorConfig)
    }


# ---------------------------------------------------------------------------------------
# A hierarchical Mission built under a contract this build dropped is stopped by name.
# ---------------------------------------------------------------------------------------


def test_a_missing_or_stale_binding_is_named_unsupported(tmp_path) -> None:
    async def case() -> None:
        async with _world(tmp_path) as world:
            store = world.store
            unbound = _create(world, "unbound")
            lift_immutable_guards(store.connection, "mission_planning_protocols")
            store.connection.execute(  # ①b1
                "DELETE FROM mission_planning_protocols WHERE mission_id = ?", (unbound.id,))
            with pytest.raises(UnsupportedPlanningPackage, match="removed"):
                current_planning_protocol(store, unbound.id)
            stale = _create(world, "stale")
            lift_immutable_guards(store.connection, "mission_planning_protocols")
            store.connection.execute(  # ①b1
                "UPDATE mission_planning_protocols SET package_version = 7 WHERE mission_id = ?", (stale.id,))
            with pytest.raises(UnsupportedPlanningPackage, match="package 7"):
                current_planning_protocol(store, stale.id)

    asyncio.run(case())


async def _until(world, done, *, timeout: float = 30.0) -> None:
    async with asyncio.timeout(timeout):
        while not done():
            await world.deployment.between_cycles(auto=True)
            await world.loop._cycle()
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("damage", ["unbound", "stale_package", "stale_deployment"])
def test_the_loop_stops_an_old_contract_mission_and_leaves_the_others_alone(
    tmp_path, damage: str
) -> None:
    """Old development data is not migrated and not served on a fallback path: the
    Mission ends with ``unsupported_planning_package`` and the loop carries on.

    ``stale_deployment``：任务第一轮规划冻结了规划部署身份；停机后这份冻结记录的字节变了（①b1，
    代表"旧版本写下、本版本不再产出的身份"），重开后主循环在入口按名停掉它。"""

    async def freeze_first_round() -> str:
        provider = LayeredScriptedProvider()
        provider.held.add("planner")
        try:
            async with _world(tmp_path, provider) as world:
                old = _create(world, "old-contract")
                await _until(world, provider.entered.is_set)
                assert any(event.type == "PlanningDeploymentBound" for event in world.store.list_events(old.id))
                return old.id
        finally:
            provider.release.set()

    async def case(old_id: str | None) -> None:
        provider = LayeredScriptedProvider()
        async with _world(tmp_path, provider) as world:
            store = world.store
            if old_id is None:
                old_id = _create(world, "old-contract").id
            if damage == "unbound":
                lift_immutable_guards(store.connection, "mission_planning_protocols")
                store.connection.execute("DELETE FROM mission_planning_protocols WHERE mission_id = ?", (old_id,))
            elif damage == "stale_package":
                lift_immutable_guards(store.connection, "mission_planning_protocols")
                store.connection.execute(
                    "UPDATE mission_planning_protocols SET package_version = 7 WHERE mission_id = ?", (old_id,))
            healthy = _create(world, "healthy")
            await _until(world, lambda: store.get_mission(old_id).status is MissionStatus.FAILED)
            stopped = store.get_mission(old_id)
            failure = (stopped.final_report or {}).get("planning_failure") or {}
            assert failure.get("reason") == "unsupported_planning_package"
            assert {"unbound": "removed", "stale_package": "package 7",
                    "stale_deployment": "deployment"}[damage] in failure["error"]
            # the other Mission is not touched by the stop: it plans on.
            await _until(world, lambda: any(event.type == "PlanRevisionCommitted"
                                            for event in store.list_events(healthy.id)))
            assert store.get_mission(healthy.id).status is not MissionStatus.FAILED

    old_id = None
    if damage == "stale_deployment":
        old_id = asyncio.run(freeze_first_round())
        from agent_orchestrator.storage.store import Store as _Store

        store = _Store.open(tmp_path / "root" / "orchestrator.db")
        try:
            row = store.connection.execute(
                "SELECT seq, payload_json FROM events WHERE mission_id = ? AND type = 'PlanningDeploymentBound'",
                (old_id,)).fetchone()
            payload = {**json.loads(row["payload_json"]), "repair_enabled": True}
            store.connection.execute("UPDATE events SET payload_json = ? WHERE seq = ?",
                                     (json.dumps(payload, sort_keys=True), row["seq"]))
        finally:
            store.close()
    asyncio.run(case(old_id))
