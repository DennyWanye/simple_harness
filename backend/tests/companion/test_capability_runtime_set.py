from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import replace
from pathlib import Path

import pytest

from deskpet.capabilities.local_runtime import LocalToolRuntime
from deskpet.capabilities.manifest import PackEnvironment, load_and_validate_pack
from deskpet.capabilities.platform import ManagedEnvironmentPreparer
from deskpet.capabilities.runtime_prepare import (
    CapabilityRuntimeSetCoordinator,
    PreparedRuntimeInstanceSpec,
    RuntimeHealthOutcome,
    RuntimeLaunchAuthorization,
    RuntimeNotStarted,
    RuntimeStartedAck,
    RuntimeStartUnknown,
)
from deskpet.mcp.manager import MCPManager


class FakeLedger:
    def __init__(self) -> None:
        self.generations = {}
        self.sets = {}
        self.starts = {}
        self.health = {}
        self.activated = []
        self.aborted = []

    async def next_owner_runtime_generation(self, owner_key, scope, scope_key):
        key = (owner_key, scope, scope_key)
        self.generations[key] = self.generations.get(key, 0) + 1
        return self.generations[key]

    async def create_owner_runtime_activation(self, activation):
        return activation

    async def create_runtime_set(self, prepared_set):
        existing = self.sets.setdefault(prepared_set.operation_id, prepared_set)
        if existing.runtime_set_hash != prepared_set.runtime_set_hash:
            raise RuntimeError("runtime_set_conflict")
        return existing

    async def claim_runtime_instance(
        self, prepared_set, instance, authorization
    ):
        return f"claim:{prepared_set.operation_id}:{instance.entry_id}"

    async def record_start_result(self, prepared_set, instance, result):
        self.starts[(prepared_set.operation_id, instance.entry_id)] = result

    async def get_runtime_instance_start_result(self, prepared_set, instance):
        return self.starts.get(
            (prepared_set.operation_id, instance.entry_id)
        )

    async def record_health_outcome(self, prepared_set, instance, outcome):
        self.health[(prepared_set.operation_id, instance.entry_id)] = outcome

    async def assert_set_health_passed(self, operation_id, runtime_set_hash):
        prepared = self.sets[operation_id]
        assert prepared.runtime_set_hash == runtime_set_hash
        for instance in prepared.instances:
            outcome = self.health.get((operation_id, instance.entry_id))
            if outcome is None or not outcome.healthy:
                raise RuntimeError("runtime_set_not_healthy")

    async def mark_set_activated(self, operation_id, runtime_set_hash):
        self.activated.append((operation_id, runtime_set_hash))

    async def mark_set_aborted(self, operation_id, runtime_set_hash, reason_code):
        self.aborted.append((operation_id, runtime_set_hash, reason_code))


class FakeAdapter:
    adapter_id = "fake"
    adapter_fingerprint = "f" * 64

    def __init__(self, *, outcome="started", delay=0.0) -> None:
        self.outcome = outcome
        self.delay = delay
        self.starts = 0
        self.healths = 0
        self.aborts = []
        self.cancelled = 0

    async def start_prepared(self, spec, authorization):
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if self.outcome == "not_started":
            return RuntimeNotStarted(
                authorization.operation_id, spec.entry_id, "not_started"
            )
        if self.outcome == "unknown":
            return RuntimeStartUnknown(
                authorization.operation_id, spec.entry_id, "unknown"
            )
        self.starts += 1
        return RuntimeStartedAck(
            operation_id=authorization.operation_id,
            runtime_set_hash=authorization.runtime_set_hash,
            entry_id=spec.entry_id,
            runtime_instance_id=authorization.runtime_instance_id,
            adapter_identity="fake-runtime",
            start_identity={"session": spec.entry_id},
            acknowledged_at=time.time(),
        )

    async def await_health(self, spec, ack):
        self.healths += 1
        return RuntimeHealthOutcome(
            operation_id=ack.operation_id,
            entry_id=spec.entry_id,
            healthy=True,
            outcome_hash=hashlib.sha256(spec.entry_id.encode()).hexdigest(),
        )

    async def abort(self, spec, start):
        self.aborts.append(spec.entry_id)


class FakeProjection:
    def __init__(self):
        self.calls = []

    async def activate_prepared_projection(self, prepared_set):
        self.calls.append(prepared_set.runtime_set_hash)
        return {"activated": True}


def _spec(entry_id: str, ordinal: int) -> PreparedRuntimeInstanceSpec:
    return PreparedRuntimeInstanceSpec(
        entry_id=entry_id,
        runtime_kind="local_runtime",
        adapter_id="fake",
        adapter_fingerprint="f" * 64,
        start_envelope={"argv": [entry_id]},
        health_envelope={"kind": "ping"},
        ordinal=ordinal,
    )


def _coordinator(adapter=None):
    ledger = FakeLedger()
    projection = FakeProjection()
    adapter = adapter or FakeAdapter()
    coordinator = CapabilityRuntimeSetCoordinator(
        ledger=ledger,
        adapters={"fake": adapter},
        projection=projection,
    )
    return coordinator, ledger, projection, adapter


@pytest.mark.asyncio
async def test_owner_generation_is_monotonic_per_owner_and_a_b_isolated() -> None:
    coordinator, _, _, adapter = _coordinator()
    values = []
    for owner, operation in (
        ("owner-a", "op-a1"),
        ("owner-b", "op-b1"),
        ("owner-a", "op-a2"),
    ):
        activation, prepared = await coordinator.prepare_owner_runtime_activation(
            owner_key=owner,
            scope="user",
            scope_key=owner,
            owner_binding_set_stamp=f"stamp:{owner}",
            startup_or_profile_epoch=1,
            operation_id=operation,
            instances=(_spec("entry-1", 0),),
            launch_revocation_epoch=4,
        )
        values.append((owner, activation.generation))
        assert adapter.starts == 0
        assert prepared.expected_instance_count == 1
    assert values == [("owner-a", 1), ("owner-b", 1), ("owner-a", 2)]


@pytest.mark.asyncio
async def test_prepare_start_ack_health_activate_are_separate_stages() -> None:
    coordinator, ledger, projection, adapter = _coordinator()
    _, prepared = await coordinator.prepare_owner_runtime_activation(
        owner_key="owner-a",
        scope="user",
        scope_key="profile-a",
        owner_binding_set_stamp="stamp-a",
        startup_or_profile_epoch=2,
        operation_id="operation-1",
        instances=(_spec("entry-1", 0),),
        launch_revocation_epoch=9,
    )
    instance = prepared.instances[0]
    assert adapter.starts == 0
    authorization = RuntimeLaunchAuthorization.issue(prepared, instance)

    ack = await coordinator.start_prepared_runtime_instance(
        prepared, instance, authorization
    )
    assert isinstance(ack, RuntimeStartedAck)
    assert adapter.starts == 1
    assert adapter.healths == 0
    assert projection.calls == []

    health = await coordinator.await_prepared_runtime_instance_health(
        prepared, instance
    )
    assert health.healthy
    assert adapter.healths == 1
    assert projection.calls == []

    receipt = await coordinator.activate_prepared_set(prepared)
    assert receipt == {"activated": True}
    assert ledger.activated == []


@pytest.mark.asyncio
async def test_launch_authorization_rejects_drifted_runtime_instance_id() -> None:
    coordinator, _, _, adapter = _coordinator()
    _, prepared = await coordinator.prepare_owner_runtime_activation(
        owner_key="owner-a",
        scope="user",
        scope_key="profile-a",
        owner_binding_set_stamp="stamp-a",
        startup_or_profile_epoch=2,
        operation_id="operation-drifted-instance",
        instances=(_spec("entry-1", 0),),
        launch_revocation_epoch=9,
    )
    instance = prepared.instances[0]
    authorization = replace(
        RuntimeLaunchAuthorization.issue(prepared, instance),
        runtime_instance_id="drifted-runtime-instance",
    )

    with pytest.raises(ValueError, match="runtime launch authorization is stale"):
        await coordinator.start_prepared_runtime_instance(
            prepared, instance, authorization
        )
    assert adapter.starts == 0


@pytest.mark.asyncio
async def test_start_ack_timeout_is_unknown_and_cancels_late_start() -> None:
    adapter = FakeAdapter(delay=1.0)
    coordinator, _, _, _ = _coordinator(adapter)
    _, prepared = await coordinator.prepare_owner_runtime_activation(
        owner_key="owner-a",
        scope="user",
        scope_key="profile-a",
        owner_binding_set_stamp="stamp-a",
        startup_or_profile_epoch=2,
        operation_id="operation-1",
        instances=(_spec("entry-1", 0),),
        launch_revocation_epoch=9,
    )
    instance = prepared.instances[0]

    result = await coordinator.start_prepared_runtime_instance(
        prepared,
        instance,
        RuntimeLaunchAuthorization.issue(prepared, instance),
        ack_timeout_seconds=0.01,
    )

    assert isinstance(result, RuntimeStartUnknown)
    assert adapter.starts == 0
    assert adapter.cancelled == 1


@pytest.mark.asyncio
async def test_new_coordinator_recovers_start_result_from_durable_ledger() -> None:
    coordinator, ledger, projection, adapter = _coordinator()
    _, prepared = await coordinator.prepare_owner_runtime_activation(
        owner_key="owner-a",
        scope="user",
        scope_key="profile-a",
        owner_binding_set_stamp="stamp-a",
        startup_or_profile_epoch=2,
        operation_id="operation-recover",
        instances=(_spec("entry-1", 0),),
        launch_revocation_epoch=9,
    )
    instance = prepared.instances[0]
    await coordinator.start_prepared_runtime_instance(
        prepared,
        instance,
        RuntimeLaunchAuthorization.issue(prepared, instance),
    )
    recovered = CapabilityRuntimeSetCoordinator(
        ledger=ledger,
        adapters={"fake": adapter},
        projection=projection,
    )

    outcome = await recovered.await_prepared_runtime_instance_health(
        prepared, instance
    )

    assert outcome.healthy is True
    assert adapter.healths == 1


@pytest.mark.asyncio
async def test_abort_walks_runtime_set_in_reverse_order() -> None:
    coordinator, ledger, _, adapter = _coordinator()
    _, prepared = await coordinator.prepare_owner_runtime_activation(
        owner_key="owner-a",
        scope="user",
        scope_key="profile-a",
        owner_binding_set_stamp="stamp-a",
        startup_or_profile_epoch=2,
        operation_id="operation-1",
        instances=(_spec("entry-1", 0), _spec("entry-2", 1)),
        launch_revocation_epoch=9,
    )

    await coordinator.abort_prepared_set(prepared, reason_code="test_abort")

    assert adapter.aborts == ["entry-2", "entry-1"]
    assert ledger.aborted[0][2] == "test_abort"


def test_local_and_mcp_static_plans_do_not_start_runtime(monkeypatch) -> None:
    calls = {"execute": 0, "mcp_start": 0}

    async def execute(*args, **kwargs):
        calls["execute"] += 1

    runtime = LocalToolRuntime()
    monkeypatch.setattr(runtime, "execute", execute)
    local = runtime.plan_prepared_instance(
        entry_id="runtime:local:one",
        ordinal=0,
        command=("python", "worker.py"),
        cwd=".",
        health_envelope={"tool": "health"},
        adapter_fingerprint="a" * 64,
    )
    manager = MCPManager({})

    async def start():
        calls["mcp_start"] += 1

    monkeypatch.setattr(manager, "start", start)
    mcp = manager.plan_prepared_session(
        server_name="filesystem",
        config={"transport": "stdio", "command": "server"},
        ordinal=1,
        health_operation={"kind": "list_tools"},
        adapter_fingerprint="b" * 64,
    )

    assert local.runtime_kind == "local_runtime"
    assert mcp.runtime_kind == "mcp"
    assert calls == {"execute": 0, "mcp_start": 0}


def test_environment_static_materialization_and_planning_never_execute(
    monkeypatch, tmp_path: Path
) -> None:
    repository_root = Path(__file__).resolve().parents[3]
    validation = load_and_validate_pack(
        repository_root / "capability-packs" / "godot",
        environment=PackEnvironment(
            deskpet_version="0.6.0-beta.9",
            os="windows",
            architecture="x86_64",
            python_version="3.11.9",
        ),
    )
    runtime = LocalToolRuntime()

    async def forbidden_execute(*args, **kwargs):
        raise AssertionError("static preparation executed a local runtime")

    async def forbidden_spawn(*args, **kwargs):
        raise AssertionError("static preparation spawned a process")

    monkeypatch.setattr(runtime, "execute", forbidden_execute)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_spawn)
    preparer = ManagedEnvironmentPreparer(
        runtime=runtime,
        command_finder=lambda _name: r"C:\tools\godot.exe",
    )

    materialization = preparer.materialize_static(
        validation,
        environment_root=tmp_path / "environment",
        operation_id="operation-static",
    )
    instances = preparer.plan_executable_checks(
        validation,
        materialization=materialization,
        operation_id="operation-static",
        adapter_fingerprint="c" * 64,
    )

    assert materialization["kind"] == "managed-local-static-v1"
    assert [item.runtime_kind for item in instances] == [
        "dependency_probe",
        "tool_health",
    ]
    assert [item.ordinal for item in instances] == [0, 1]
