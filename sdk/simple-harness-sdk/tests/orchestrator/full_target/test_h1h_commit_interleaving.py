"""I06 deterministic SQLite interleavings.

The authority interleaving runs on the product's deployment: the main loop proposed a
method, had it reviewed and opened the adoption round (``h1i_seed.reviewed``); while the
collector is between preview and commit, the person renews the grant on another
connection that holds the write lock until the plan writer's BEGIN IMMEDIATE.  The
handoff interleaving still uses the materialised file-publish fixture (the external
operation world is rewritten together with representative case 3).
"""

from __future__ import annotations

import hashlib
import dataclasses
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


_FULL = Path.cwd() / "tests" / "orchestrator" / "full_target"
if str(_FULL) not in sys.path:
    sys.path.insert(0, str(_FULL))

from h1i_seed import plan_reply, reviewed  # noqa: E402
from test_h1h_commit_guard import _plan_revision_count  # noqa: E402
from test_htn_store import envelope  # noqa: E402
from test_plan_commits import ROOT_TASK  # noqa: E402
from operation_completion.operation_runtime_fixture import materialized_file_publish  # noqa: E402

from agent_orchestrator.api.planning_authorization import PlanningAuthorizationApi  # noqa: E402
from agent_orchestrator.contracts.models import sha256_hex  # noqa: E402
from agent_orchestrator.contracts.htn import RunningWorkPolicy  # noqa: E402
from agent_orchestrator.contracts.planning_decisions import (  # noqa: E402
    PLANNING_DECISION_V1,
    PlanningRequestBinding,
)
from agent_orchestrator.contracts.semantic_base import content_hash_of  # noqa: E402
from agent_orchestrator.governance.permissions import Principal  # noqa: E402
from agent_orchestrator.governance.planning_authorization import (  # noqa: E402
    StorePlanningAuthorityReader,
    build_planning_authorization,
    planning_policy_for_mission,
)
from agent_orchestrator.orchestrator.commit_service import CommitService  # noqa: E402
from agent_orchestrator.orchestrator.plan_commits import (  # noqa: E402
    PLAN_REVISION_COMMITTED,
    PlanCommitRejected,
)
from agent_orchestrator.orchestrator.planning_admission_commits import (  # noqa: E402
    PlanningCommitAdmission,
)
from agent_orchestrator.orchestrator.planning_protocol_binding import (  # noqa: E402
    bind_planning_protocol,
)
from agent_orchestrator.planning.plan_preview import _source_snapshot_payload  # noqa: E402
from agent_orchestrator.runtime.connectors import TestConfigService  # noqa: E402
from agent_orchestrator.runtime.connectors import params_hash  # noqa: E402
from agent_orchestrator.runtime.planning_operations import (  # noqa: E402
    StoreOperationReader,
    build_operation_snapshot,
    read_running_work,
)
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.storage.planning_admission_store import (  # noqa: E402
    PlanningAdmissionStore,
)
from agent_orchestrator.storage.planning_decision_store import PlanningDecisionStore  # noqa: E402
from agent_orchestrator.storage.store import Store  # noqa: E402
from simple_harness.contracts import canonical_json  # noqa: E402


def _admission_for_active_revision(world, command):
    """Freeze a real request/grant/producer snapshot for the second revision."""

    if PlanningDecisionStore(world.store).get_mission_protocol(world.mission.id) is None:
        bind_planning_protocol(world.store, world.mission.id, PLANNING_DECISION_V1)
    protocol = PlanningDecisionStore(world.store).get_mission_protocol(world.mission.id)
    assert protocol is not None
    request = PlanningRequestBinding(
        request_id="request-h1h-i06-active",
        mission_id=world.mission.id,
        protocol_version=PLANNING_DECISION_V1,
        package_version=int(protocol["package_version"]),
        package_hash="a" * 64,
        base_plan_revision=1,
        requirements_revision=int(command.read_set.requirements_revision),
        scope_epoch_digest="b" * 64,
        subject_bindings_hash="c" * 64,
        visible_refs_digest="d" * 64,
        prompt_version=str(protocol["prompt_version"]),
        prompt_hash="e" * 64,
        created_at=world.store.now,
        intent_id="intent-h1h-i06-active",
    )
    PlanningDecisionStore(world.store).insert_planning_request(request)
    api = PlanningAuthorizationApi(
        world.store,
        tenant_id=world.mission.tenant_id,
        principal=Principal(world.principal.principal_id),
    )
    grant = api.issue(
        world.mission.id,
        command_id="grant-h1h-i06-active",
        request_id=request.request_id,
    )
    authority = build_planning_authorization(
        request.request_id,
        read=StorePlanningAuthorityReader(PlanningAdmissionStore(world.store), world.store),
        caller=world.principal,
        policy=planning_policy_for_mission(world.store, world.mission.id),
        now_ms=int(world.store.now * 1000),
    )
    operations = build_operation_snapshot(
        world.mission.id, reader=StoreOperationReader(world.store)
    )
    runtime_work = read_running_work(
        world.mission.id, (), reader=StoreOperationReader(world.store)
    )
    compilation_hash = sha256_hex(
        {"delta": command.delta.to_json(), "network": _source_snapshot_payload(command.network)}
    )
    return api, grant, PlanningCommitAdmission(
        request_id=request.request_id,
        decision_hash="f" * 64,
        decision_key="REFINE",
        authority=authority,
        operations=operations,
        runtime_work=runtime_work,
        preview_request_id=request.request_id,
        preview_decision_hash="f" * 64,
        preview_compilation_hash=compilation_hash,
        preview_read_set_hash=content_hash_of(command.read_set.to_json()),
    )


def _install_proposed_linked_action(world, tmp_path):
    frozen = dataclasses.replace(
        envelope(operation_id="operation-i06", occurrence="occurrence-i06"),
        mission_id=world.mission.id,
        scope_id="mission",
    )
    HtnStore(world.store).bind_operation(frozen, principal_id="origin-principal")
    binding = PlanningAdmissionStore(world.store).get_operation_binding("occurrence-i06")
    assert binding is not None
    action_id = "action-i06"
    action_key = f"{action_id}:v1"
    action = {
        "action_key": action_key,
        "action_id": action_id,
        "version": 1,
        "mission_id": world.mission.id,
        "task_id": ROOT_TASK,
        "result_id": "result-i06",
        "attempt_id": "attempt-i06",
        "artifact_id": "artifact-i06",
        "artifact_hash": "a" * 64,
        "connector": "test_config",
        "operation": "read",
        "target": "feature_flags.new_ui",
        "params": {},
        "params_hash": params_hash({}),
        "reason": "I06 concurrent handoff",
        "level": "L0",
        "required_approvals": 0,
        "idempotency_key": f"{action_id}:v1",
        "approval_request_id": None,
        "after": None,
        "state": "PROPOSED",
        "handoffs": 0,
        "receipt": None,
        "history": [],
        "created_at": world.store.now,
    }
    world.store.put_action(action)
    link = {
        "operation_id": binding["operation_id"],
        "request_hash": binding["request_hash"],
        "operation_occurrence_id": binding["operation_occurrence_id"],
        "mission_id": binding["mission_id"],
        "envelope_hash": binding["envelope_hash"],
        "principal_id": binding["principal_id"],
        "scope_id": binding["scope_id"],
        "obligation_id": binding["obligation_id"],
        "producer_task_id": ROOT_TASK,
        "producer_htn_occurrence_id": "htn-occurrence-i06",
        "producer_contract_revision": 1,
        "producer_plan_revision": 0,
        "action_key": action_key,
        "action_id": action_id,
        "action_version": 1,
        "params_hash": action["params_hash"],
        "idempotency_key": action["idempotency_key"],
        "provenance_receipt_id": "receipt-i06",
    }
    link["link_hash"] = hashlib.sha256(canonical_json(link).encode()).hexdigest()
    link["link_json"] = canonical_json(link)
    PlanningAdmissionStore(world.store).put_operation_action_link(link)
    return action, {
        "test_config": TestConfigService(tmp_path / "test-config.json"),
    }


def _second_materialized_revision(command, *, command_id: str):
    """Build the no-op revision from the plan that actually owns the Operation."""

    delta = dataclasses.replace(
        command.delta,
        delta_id="delta-second-materialized",
        base_plan_revision=1,
        occurrences=(),
        method_instances=(),
        data_requirements=(),
        retired_instance_ids=(),
        referenced_occurrences=tuple(
            occurrence.occurrence_id for occurrence in command.delta.occurrences
        ),
    )
    return dataclasses.replace(
        command,
        command_id=command_id,
        delta=delta,
        network=dataclasses.replace(command.network, plan_revision=2),
        task_bindings=(),
        superseded_occurrences=(),
        running_work_policy=RunningWorkPolicy.RETAIN_IF_BINDINGS_UNCHANGED,
    )


def test_i06_concurrent_authority_write_makes_the_collectors_plan_commit_stale(tmp_path) -> None:
    """The renewal is held open on another connection while the collector goes on to
    write; the plan writer contends on a real BEGIN IMMEDIATE, and after the renewal
    commits, the commit's in-transaction authority reread refuses the old binding."""

    import asyncio

    async def case() -> None:
        async with reviewed(tmp_path, key="h1h-i06-interleave-authority") as ((loop, mission, _world, _root, dispatch, product), opener, _provider):
            binding = PlanningAdmissionStore(loop.store).get_request_binding(opener.intent_id)
            assert binding is not None
            mutation_written = threading.Event()
            plan_begin_immediate = threading.Event()
            results: dict[str, object] = {}

            def renew_on_another_connection() -> None:
                other_store = Store.open(loop.store.path)
                try:
                    with other_store.transaction():
                        results["renewed"] = PlanningAuthorizationApi(
                            other_store, tenant_id=mission.tenant_id,
                            principal=product.deployment.principal,
                        ).renew(binding["grant_id"], expected_revision=int(binding["grant_revision"]),
                                command_id="renew-h1h-i06-interleaved")
                        mutation_written.set()
                        assert plan_begin_immediate.wait(timeout=5), "plan writer never began"
                finally:
                    other_store.close()

            def trace(statement: str) -> None:
                if statement.strip().upper() == "BEGIN IMMEDIATE":
                    plan_begin_immediate.set()

            original = dispatch.preview_plan_proposal
            pool = ThreadPoolExecutor(max_workers=1)
            future = None

            def preview_then_contend(proposal, *, inputs):  # type: ignore[no-untyped-def]
                nonlocal future
                result = original(proposal, inputs=inputs)
                future = pool.submit(renew_on_another_connection)
                assert mutation_written.wait(timeout=5), "renewal did not take the write lock"
                loop.store.connection.set_trace_callback(trace)
                return result

            dispatch.preview_plan_proposal = preview_then_contend  # type: ignore[method-assign]
            try:
                await loop._collect_plan_decision(
                    opener, object(), mission, plan_reply(opener.config["planning_package"]), dispatch
                )
            finally:
                loop.store.connection.set_trace_callback(None)
                dispatch.preview_plan_proposal = original  # type: ignore[method-assign]
                pool.shutdown(wait=True)
            assert future is not None
            future.result(timeout=10)
            assert "renewed" in results

            stored = PlanningDecisionStore(loop.store).get_planning_decision_by_attempt(
                opener.intent_id, 0
            )
            assert stored is not None
            assert stored["status"] == "COMMIT_REJECTED", stored
            assert stored["rejection_codes"] == ["REQUEST_BINDING_STALE"], stored
            assert HtnStore(loop.store).list_plan_revisions(mission.id) == ()
            assert not [e for e in loop.store.list_events(mission.id) if e.type == PLAN_REVISION_COMMITTED]
            assert not loop.store.list_actions(mission.id)

    asyncio.run(case())


def test_i06_concurrent_action_commit_makes_plan_commit_stale(tmp_path) -> None:
    """The handoff writer is held open while the plan writer starts.

    The plan commit therefore contends on a real BEGIN IMMEDIATE.  After the
    first writer commits, its in-transaction producer reread must reject the old
    admission; no guard result or SQLite error is mocked.
    """

    operation_fixture = materialized_file_publish(tmp_path)
    world = operation_fixture.world
    assert world.store.get_mission(world.mission.id).status.value == "ACTIVE"
    command = _second_materialized_revision(
        operation_fixture.plan_command, command_id="cmd-i06-handoff"
    )
    action, connectors = operation_fixture.action, operation_fixture.connectors
    action_deployment = operation_fixture.deployment
    _api, _grant, admission = _admission_for_active_revision(world, command)
    before_events = tuple(world.store.list_events(world.mission.id))
    before_revisions = _plan_revision_count(world)
    assert before_revisions == 1

    mutation_written = threading.Event()
    plan_begin_immediate = threading.Event()

    def mutate() -> object:
        other_store = Store.open(world.store.path)
        try:
            service = CommitService(other_store)
            with other_store.transaction():
                changed = service.begin_handoff(
                    action["action_key"],
                    owner="i06-concurrent-owner",
                    lease_seconds=30.0,
                    connectors=connectors,
                    deployment=action_deployment,
                )
                assert changed[0] is not None and changed[1] is None
                assert changed[0]["state"] == "HANDED_OFF"
                mutation_written.set()
                assert plan_begin_immediate.wait(
                    timeout=5
                ), "plan writer never executed BEGIN IMMEDIATE"
            return changed
        finally:
            other_store.close()

    def commit_plan():
        assert mutation_written.wait(timeout=5), "mutation writer did not acquire SQLite lock"
        try:
            return world.service.commit_planning_revision(
                command, world.principal, admission=admission
            )
        except Exception as error:  # asserted by the parent thread
            return error

    def trace(statement: str) -> None:
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            plan_begin_immediate.set()

    world.store.connection.set_trace_callback(trace)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            mutation_future = pool.submit(mutate)
            commit_future = pool.submit(commit_plan)
            mutation_future.result(timeout=10)
            commit_result = commit_future.result(timeout=10)
    finally:
        world.store.connection.set_trace_callback(None)

    assert isinstance(commit_result, PlanCommitRejected)
    assert "OPERATION_SNAPSHOT_STALE" in str(commit_result)
    assert _plan_revision_count(world) == before_revisions

    after_events = tuple(world.store.list_events(world.mission.id))
    assert len([event for event in after_events if event.type == PLAN_REVISION_COMMITTED]) == 1
    handed = [event for event in after_events if event.type == "ActionHandedOff"]
    assert len(handed) == 1
    assert handed[0].payload["action_key"] == action["action_key"]
    # Only the real competing handoff may add an outbox/event.  The stale
    # planning transaction must contribute none.
    assert len(after_events) == len(before_events) + 1
