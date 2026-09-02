# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from simple_harness import (
    ROOT_PROFILE_KEY,
    DriverResult,
    RuntimePorts,
    RuntimeProfile,
    SqliteContextPort,
    build_runtime,
)
from simple_harness.execution.sqlite import Database, SqliteExecutionUnitOfWork
from simple_harness.execution.uow import RunState

from context import ServiceContext
from deskpet.capabilities.store import CapabilitySkillInstallVerificationAttempt
from deskpet.sdk_adapters.composition import (
    OwnedResourceCloser,
    ProductionRuntimeBuild,
    ProductSdkRuntimeStack,
    SdkRuntimeBuildInputs,
    SdkRuntimeNotReady,
    WorkflowFactoryResourceScope,
    WorkflowRuntimeBuild,
)
from deskpet.sdk_adapters.ingress import SdkRuntimeIngress
from deskpet.sdk_adapters.runtime_paths import ProductRuntimePathsAdapter
from deskpet.sdk_adapters.sdk_candidate import build_candidate_identity, sdk_wheel_path
from deskpet.sdk_adapters.skill_install_verification import (
    skill_install_verification_authority_hash,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WHEEL = sdk_wheel_path()
IDENTITY = build_candidate_identity()


class _Noop:
    async def reconcile(self):
        return None

    async def run_once(self):
        return False

    def current_generation(self):
        return 1


class _Driver:
    async def start(self, invocation, *, context, cancel):  # type: ignore[no-untyped-def]
        del invocation, context, cancel
        return DriverResult(
            RunState.COMPLETED,
            {
                "schema": "skill-install-verification-final-v1",
                "status": "succeeded",
                "provider_invocation_count": 0,
                "effect_count": 0,
                "checkpoint_count": 0,
                "continuation_count": 0,
            },
        )


def _ports(database, uow, reconciliation):  # type: ignore[no-untyped-def]
    noop = _Noop()
    return RuntimePorts(
        provider=noop,
        tools=noop,
        authorization=noop,
        context=SqliteContextPort(database, clock=lambda: 10.0),
        delivery=noop,
        tool_reconciliation=noop,
        reconciliation=reconciliation,
        provider_reconciliation=noop,
        react_checkpoint=uow,
        tool_catalog=noop,
        owner_id="product-sdk-test",
        clock=lambda: 10.0,
    )


def _inputs(reconciliation: object | None = None) -> SdkRuntimeBuildInputs:
    selected = reconciliation or _Noop()
    return SdkRuntimeBuildInputs(
        profiles={ROOT_PROFILE_KEY: RuntimeProfile(ROOT_PROFILE_KEY, "fixture")},
        drivers={"fixture": _Driver()},
        ports_factory=lambda database, uow: _ports(database, uow, selected),
        workflow_catalog_digest="catalog-v1",
        workflow_registrations=(),
    )


@pytest.mark.asyncio
async def test_production_runtime_factory_starts_and_publishes_registrations(
    tmp_path: Path,
) -> None:
    opened_database: Database | None = None
    base = _inputs()

    def runtime_factory(execution_path: object) -> ProductionRuntimeBuild:
        nonlocal opened_database
        opened_database = Database.open(execution_path)
        uow = SqliteExecutionUnitOfWork(opened_database)
        runtime = build_runtime(
            uow,
            base.profiles,
            base.drivers,
            _ports(opened_database, uow, _Noop()),
        )
        return ProductionRuntimeBuild(
            runtime=runtime,
            transaction_owner=uow,
            workflow_registrations=("production",),
        )

    stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(tmp_path / "user-data"),
        candidate_identity=IDENTITY,
        dependency_loader=lambda: SdkRuntimeBuildInputs(
            profiles=base.profiles,
            drivers=base.drivers,
            ports_factory=base.ports_factory,
            workflow_catalog_digest=base.workflow_catalog_digest,
            runtime_factory=runtime_factory,
        ),
    )

    ready = await stack.start()
    assert ready.workflow_registrations == ("production",)
    assert stack.phase == "ready"
    await stack.close()
    assert stack.phase == "closed"
    assert opened_database is not None
    opened_database.close()


class _OwnedResource:
    def __init__(self, name: str, closed: list[str], *, asynchronous: bool) -> None:
        self.name = name
        self.closed = closed
        self.close_calls = 0
        self.asynchronous = asynchronous

    def close(self):  # type: ignore[no-untyped-def]
        self.close_calls += 1
        self.closed.append(self.name)
        if self.asynchronous:
            return asyncio.sleep(0)
        return None


@pytest.mark.asyncio
async def test_start_reconcile_recover_query_close_and_schema_independence(
    tmp_path: Path,
) -> None:
    loads = 0

    def load():
        nonlocal loads
        loads += 1
        return _inputs()

    paths = ProductRuntimePathsAdapter(tmp_path / "user-data")
    stack = ProductSdkRuntimeStack(
        paths=paths, candidate_identity=IDENTITY, dependency_loader=load
    )
    with pytest.raises(SdkRuntimeNotReady):
        stack.require_ready()
    with pytest.raises(SdkRuntimeNotReady):
        stack.query("before-ready")

    ready = await stack.start()
    assert ready.generation == 1
    assert ready.runtime.state.value == "ready"
    assert stack.require_ready() is ready
    assert stack.query("missing-run") is None
    await stack.reconcile()
    await stack.recover()
    await stack.close()
    assert loads == 1
    with pytest.raises(SdkRuntimeNotReady):
        stack.require_ready()

    reopened = ProductSdkRuntimeStack(
        paths=paths, candidate_identity=IDENTITY, dependency_loader=load
    )
    reopened_ready = await reopened.start()
    assert reopened_ready.runtime.state.value == "ready"
    await reopened.close()
    assert loads == 2

    with sqlite3.connect(paths.execution_database) as connection:
        assert connection.execute(
            "SELECT max(version) FROM sdk_schema_migrations"
        ).fetchone()[0] == 7
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "runs" in tables
    assert tables.isdisjoint(
        {"sessions", "capabilities", "task_grants", "permission_policies"}
    )


@pytest.mark.asyncio
async def test_preflight_block_creates_idempotent_failed_root_without_driver(
    tmp_path: Path,
) -> None:
    stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(tmp_path / "user-data"),
        candidate_identity=IDENTITY,
        dependency_loader=_inputs,
        clock=lambda: 10.0,
    )
    await stack.start()
    assert stack._uow is not None
    stack._uow.create_with_start_snapshot(
        execution_session_id="session-missing-root",
        run_id="prior-run",
        request_id="prior-request",
        profile_key=ROOT_PROFILE_KEY,
        driver_kind="react",
        snapshot={"schema_version": 1},
        event_id="prior-run:created",
        now=9.0,
        user_id="existing-user",
    )

    first = await stack.commit_preflight_blocked_root(
        execution_session_id="session-missing-root",
        run_id="run-missing-root",
        request_id="request-missing-root",
        turn_id="turn-missing-root",
        task_scope_id="scope-missing-root",
        text="read project-canary.txt",
        reason_code="workspace_unavailable",
        evidence_refs=("workspace-binding:project_root_missing",),
    )
    replay = await stack.commit_preflight_blocked_root(
        execution_session_id="session-missing-root",
        run_id="run-missing-root",
        request_id="request-missing-root",
        turn_id="turn-missing-root",
        task_scope_id="scope-missing-root",
        text="read project-canary.txt",
        reason_code="workspace_unavailable",
        evidence_refs=("workspace-binding:project_root_missing",),
    )

    assert first.state.value == replay.state.value == "failed"
    assert stack.query("run-missing-root").state.value == "failed"
    snapshot = stack._uow.read_start_snapshot("run-missing-root")
    assert snapshot is not None
    assert snapshot["preflight_block"] == {
        "reason_code": "workspace_unavailable",
        "evidence_refs": ["workspace-binding:project_root_missing"],
    }
    event = stack._uow.database.connection.execute(
        "SELECT payload_json FROM run_events "
        "WHERE run_id=? AND kind='run.failed'",
        ("run-missing-root",),
    ).fetchone()
    assert event is not None
    assert '"provider_invocation_created":false' in str(event["payload_json"])
    await stack.close()


def test_composition_does_not_import_legacy_generic_authority() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "deskpet/sdk_adapters/composition.py"
    ).read_text(encoding="utf-8")
    assert "deskpet.harness" not in source
    assert "ProductHarnessUnitOfWork" not in source
    assert ".kernel._uow" not in source
    assert ".reconciler" not in source


def test_desktop_composition_uses_sdk_production_builder_with_memory_on() -> None:
    source = (PROJECT_ROOT / "backend/main.py").read_text(encoding="utf-8")
    assert "ProductionRuntimeConfig(" in source
    assert "build_production_runtime(config)" in source
    assert "agent_memory=agent_memory_port" in source
    assert "context_provider=context_provider" in source
    assert "ContextPreparationMode" not in source
    assert "memory=agent_memory_port" in source
    assert "context_provider=context_provider" in source
    assert "conversation_query=" not in source
    assert "conversation_sink=" not in source
    assert 'if str(spec.name) in {"memory_recall", "memory_search"}' in source
    assert "def resolve(self, generation, content_fingerprint):" in source
    assert ".resolve(generation, content_fingerprint)" in source
    assert "**_sdk_runtime_authority_bindings()" in source


@pytest.mark.asyncio
async def test_sdk_07_required_authorities_are_constructor_bound_and_fail_closed() -> None:
    import main

    bindings = main._sdk_runtime_authority_bindings()
    assert set(bindings) == {
        "run_context_authority",
        "runtime_decision_sink",
        "task_execution_authority",
    }
    with pytest.raises(RuntimeError, match="sdk_run_context_authority_unavailable"):
        await bindings["run_context_authority"].prepare_snapshot(object())
    with pytest.raises(RuntimeError, match="sdk_runtime_decision_sink_unavailable"):
        await bindings["runtime_decision_sink"].record_no_recall(run_id="run")
    with pytest.raises(RuntimeError, match="sdk_task_execution_authority_unavailable"):
        await bindings["task_execution_authority"].issue_envelope(object())


def test_desktop_composition_shares_physical_capability_scope_store() -> None:
    source = (PROJECT_ROOT / "backend/main.py").read_text(encoding="utf-8")

    assert (
        'capability_scope_store = service_context.get("tool_capability_scope_store")'
        in source
    )
    assert "scope_store=capability_scope_store" in source
    assert source.index(
        'capability_scope_store = service_context.get("tool_capability_scope_store")'
    ) < source.index("scope_store=capability_scope_store")
    assert 'frozen_catalog["policy_fingerprint"] = (' in source
    assert "visibility_registry=live_tool_registry" in source
    assert "reason=global_visibility_not_ready" in source
    assert (
        "deskpet_tool_registry_v2.read_policy_snapshot(strict=True).fingerprint"
        in source
    )


@pytest.mark.asyncio
async def test_concurrent_start_is_single_generation_and_close_is_idempotent(
    tmp_path: Path,
) -> None:
    loads = 0

    async def load():
        nonlocal loads
        loads += 1
        await asyncio.sleep(0)
        return _inputs()

    stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(tmp_path / "user-data"),
        candidate_identity=IDENTITY,
        dependency_loader=load,
    )
    first, second = await asyncio.gather(stack.start(), stack.start())
    assert first is second
    assert loads == 1
    await asyncio.gather(stack.close(), stack.close())
    with pytest.raises(SdkRuntimeNotReady):
        stack.require_ready()


class _FailOnceReconciliation(_Noop):
    def __init__(self) -> None:
        self.calls = 0

    async def reconcile(self):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("startup reconciliation failed")


@pytest.mark.asyncio
async def test_startup_failure_cleans_up_and_retry_rereads_dependencies(
    tmp_path: Path,
) -> None:
    loads = 0
    reconciliation = _FailOnceReconciliation()

    def load():
        nonlocal loads
        loads += 1
        return _inputs(reconciliation)

    paths = ProductRuntimePathsAdapter(tmp_path / "user-data")
    stack = ProductSdkRuntimeStack(
        paths=paths, candidate_identity=IDENTITY, dependency_loader=load
    )
    with pytest.raises(RuntimeError, match="startup reconciliation failed"):
        await stack.start()
    with pytest.raises(SdkRuntimeNotReady):
        stack.require_ready()

    ready = await stack.start()
    assert ready.generation == 1
    assert loads == 2
    assert reconciliation.calls == 2
    await stack.close()


@pytest.mark.asyncio
async def test_runtime_start_failure_closes_dependency_resources_once_in_reverse(
    tmp_path: Path,
) -> None:
    closed: list[str] = []
    product_state = _OwnedResource("product-state", closed, asynchronous=False)
    workflow = _OwnedResource("workflow", closed, asynchronous=True)
    reconciliation = _FailOnceReconciliation()

    def load():
        base = _inputs(reconciliation)
        return SdkRuntimeBuildInputs(
            profiles=base.profiles,
            drivers=base.drivers,
            ports_factory=base.ports_factory,
            workflow_catalog_digest=base.workflow_catalog_digest,
            owned_resources=(
                OwnedResourceCloser("product-state", product_state.close),
                OwnedResourceCloser("workflow", workflow.close),
            ),
        )

    stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(tmp_path / "user-data"),
        candidate_identity=IDENTITY,
        dependency_loader=load,
    )
    with pytest.raises(RuntimeError, match="startup reconciliation failed"):
        await stack.start()
    assert closed == ["workflow", "product-state"]
    assert workflow.close_calls == product_state.close_calls == 1


@pytest.mark.asyncio
async def test_host_control_ingress_yields_bounded_zero_side_state_evidence(
    tmp_path: Path,
) -> None:
    attempt = CapabilitySkillInstallVerificationAttempt(
        attempt_id="attempt-evidence",
        intent_id="intent-evidence",
        attempt_generation=1,
        state_version=1,
        status="start_submitted",
        verifier_session_id="session-evidence",
        request_id="request-evidence",
        turn_id="turn-evidence",
        expected_run_id="run-evidence",
        actual_run_id=None,
        manager_operation_id="operation-evidence",
        manager_receipt_hash=hashlib.sha256(b"manager").hexdigest(),
        committed_set_stamp=hashlib.sha256(b"committed").hexdigest(),
        project_scope_key="scope-evidence",
        expected_member_set_stamp=hashlib.sha256(b"members").hexdigest(),
        lease_intent_id="lease-evidence",
        lease_intent_hash=hashlib.sha256(b"lease").hexdigest(),
        capability_snapshot_ref=hashlib.sha256(b"snapshot").hexdigest(),
        run_catalog_content_stamp=None,
        process_catalog_stamp=None,
        projection_receipt_id=None,
        projection_receipt_hash=None,
        terminal_event_id=None,
        terminal_event_hash=None,
        evidence_hash=None,
        release_receipt_id=None,
        release_receipt_hash=None,
        release_owner_event_hash=None,
        superseded_by_attempt_id=None,
        migration_classification=None,
        migration_classification_hash=None,
        error=None,
        created_at=1.0,
        updated_at=1.0,
        terminal_at=None,
    )
    stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(tmp_path / "user-data"),
        candidate_identity=IDENTITY,
        dependency_loader=_inputs,
    )
    ingress = SdkRuntimeIngress(stack)
    await stack.start()
    ingress.open()
    await ingress.start_skill_install_verification(
        session_id=attempt.verifier_session_id,
        run_id=attempt.expected_run_id,
        request_id=attempt.request_id,
        turn_id=attempt.turn_id,
        user_id="host-user",
        attempt_id=attempt.attempt_id,
        attempt_generation=attempt.attempt_generation,
        authority_hash=skill_install_verification_authority_hash(attempt),
        input={"attempt_id": attempt.attempt_id},
        tool_catalog_generation=1,
    )
    await ingress.wait_idle(attempt.expected_run_id)

    evidence = stack.read_skill_install_verification_evidence(attempt)

    assert evidence.status == "terminal_succeeded"
    assert evidence.run_id == attempt.expected_run_id
    assert evidence.terminal_event_id
    assert evidence.terminal_event_hash
    assert evidence.evidence_hash
    ordinary_terminal = stack.read_run_terminal_evidence(
        attempt.expected_run_id
    )
    assert ordinary_terminal is not None
    assert ordinary_terminal.event_id == evidence.terminal_event_id
    assert ordinary_terminal.event_hash == evidence.terminal_event_hash
    assert ordinary_terminal.state == "completed"
    mismatched = stack.read_skill_install_verification_evidence(
        replace(attempt, project_scope_key="different-scope")
    )
    assert mismatched.status == "corrupt"
    assert mismatched.reason_code == "start_authority_mismatch"

    stale_attempt = replace(
        attempt,
        attempt_id="attempt-stale-catalog",
        intent_id="intent-stale-catalog",
        verifier_session_id="session-stale-catalog",
        request_id="request-stale-catalog",
        turn_id="turn-stale-catalog",
        expected_run_id="run-stale-catalog",
    )
    await ingress.start_skill_install_verification(
        session_id=stale_attempt.verifier_session_id,
        run_id=stale_attempt.expected_run_id,
        request_id=stale_attempt.request_id,
        turn_id=stale_attempt.turn_id,
        user_id="host-user",
        attempt_id=stale_attempt.attempt_id,
        attempt_generation=stale_attempt.attempt_generation,
        authority_hash=skill_install_verification_authority_hash(stale_attempt),
        input={"attempt_id": stale_attempt.attempt_id},
        tool_catalog_generation=999,
    )
    await ingress.wait_idle(stale_attempt.expected_run_id)

    stale_evidence = stack.read_skill_install_verification_evidence(stale_attempt)
    assert stale_evidence.status == "terminal_failed"
    assert stale_evidence.reason_code == "tool_catalog_stale"
    await stack.close()


@pytest.mark.asyncio
async def test_close_during_start_prevents_ready_publication(tmp_path: Path) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    closed: list[str] = []
    product_state = _OwnedResource("product-state", closed, asynchronous=False)
    workflow = _OwnedResource("workflow", closed, asynchronous=True)

    async def workflow_factory(_database, uow):  # type: ignore[no-untyped-def]
        entered.set()
        await release.wait()
        return WorkflowRuntimeBuild(
            transaction_owner=uow,
            runner=None,
            owned_resources=(
                OwnedResourceCloser("workflow", workflow.close),
            ),
        )

    def load():
        base = _inputs()
        return SdkRuntimeBuildInputs(
            profiles=base.profiles,
            drivers=base.drivers,
            ports_factory=base.ports_factory,
            workflow_catalog_digest=base.workflow_catalog_digest,
            workflow_factory=workflow_factory,
            owned_resources=(
                OwnedResourceCloser("product-state", product_state.close),
            ),
        )

    stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(tmp_path / "user-data"),
        candidate_identity=IDENTITY,
        dependency_loader=load,
    )
    starting = asyncio.create_task(stack.start())
    await entered.wait()
    closing = asyncio.create_task(stack.close())
    await asyncio.sleep(0)
    with pytest.raises(SdkRuntimeNotReady):
        stack.require_ready()
    release.set()
    with pytest.raises(SdkRuntimeNotReady):
        await starting
    await closing
    with pytest.raises(SdkRuntimeNotReady):
        stack.require_ready()
    assert closed == ["workflow", "product-state"]
    assert workflow.close_calls == product_state.close_calls == 1


@pytest.mark.asyncio
async def test_service_context_publishes_only_one_immutable_ready_slot(
    tmp_path: Path,
) -> None:
    services = ServiceContext()
    services.register("sdk_runtime_ready", None)
    stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(tmp_path / "user-data"),
        candidate_identity=IDENTITY,
        dependency_loader=_inputs,
        ready_publisher=lambda ready: services.register("sdk_runtime_ready", ready),
    )
    ready = await stack.start()
    assert services.get("sdk_runtime_ready") is ready
    assert services.snapshot()["sdk_runtime_ready"] is ready
    await stack.close()
    assert services.get("sdk_runtime_ready") is None


@pytest.mark.asyncio
async def test_normal_close_owns_dependency_resources_exactly_once(
    tmp_path: Path,
) -> None:
    closed: list[str] = []
    resource = _OwnedResource("product-state", closed, asynchronous=True)
    base = _inputs()

    def load():
        return SdkRuntimeBuildInputs(
            profiles=base.profiles,
            drivers=base.drivers,
            ports_factory=base.ports_factory,
            workflow_catalog_digest=base.workflow_catalog_digest,
            owned_resources=(OwnedResourceCloser("product-state", resource.close),),
        )

    stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(tmp_path / "user-data"),
        candidate_identity=IDENTITY,
        dependency_loader=load,
    )
    await stack.start()
    await asyncio.gather(stack.close(), stack.close())
    assert closed == ["product-state"]
    assert resource.close_calls == 1


@pytest.mark.asyncio
async def test_post_database_workflow_factory_binds_same_transaction_owner(
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}
    closed: list[str] = []
    workflow_resource = _OwnedResource(
        "workflow-registry", closed, asynchronous=True
    )
    base = _inputs()

    async def workflow_factory(database, uow):  # type: ignore[no-untyped-def]
        seen["workflow_database"] = database
        seen["workflow_uow"] = uow
        async with WorkflowFactoryResourceScope() as scope:
            scope.own(
                OwnedResourceCloser("workflow-registry", workflow_resource.close)
            )
            return WorkflowRuntimeBuild(
                transaction_owner=uow,
                runner=None,
                registrations=("host", "official"),
                owned_resources=scope.transfer(),
            )

    def ports_factory(database, uow):  # type: ignore[no-untyped-def]
        seen["ports_database"] = database
        seen["ports_uow"] = uow
        return _ports(database, uow, _Noop())

    def load():
        return SdkRuntimeBuildInputs(
            profiles=base.profiles,
            drivers=base.drivers,
            ports_factory=ports_factory,
            workflow_catalog_digest=base.workflow_catalog_digest,
            workflow_factory=workflow_factory,
        )

    stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(tmp_path / "user-data"),
        candidate_identity=IDENTITY,
        dependency_loader=load,
    )
    ready = await stack.start()
    assert ready.workflow_registrations == ("host", "official")
    assert seen["workflow_database"] is seen["ports_database"]
    assert seen["workflow_uow"] is seen["ports_uow"]
    assert seen["workflow_uow"] is stack._uow
    await stack.close()
    assert closed == ["workflow-registry"]
    assert workflow_resource.close_calls == 1


@pytest.mark.asyncio
async def test_foreign_workflow_owner_fails_closed_and_cleans_binding(
    tmp_path: Path,
) -> None:
    closed: list[str] = []
    workflow_resource = _OwnedResource(
        "workflow-registry", closed, asynchronous=False
    )
    foreign_database = None
    base = _inputs()

    def workflow_factory(_database, uow):  # type: ignore[no-untyped-def]
        nonlocal foreign_database
        foreign_database = sqlite3.connect(tmp_path / "foreign.sqlite")
        foreign_database.close()
        # A real SDK UoW with a distinct owner identity is sufficient to prove
        # that equality-like substitutes cannot cross this boundary.
        from simple_harness.execution.sqlite import Database, SqliteExecutionUnitOfWork

        other_database = Database.open(tmp_path / "other.sqlite")
        foreign_owner = SqliteExecutionUnitOfWork(other_database)
        return WorkflowRuntimeBuild(
            transaction_owner=foreign_owner,
            runner=None,
            owned_resources=(
                OwnedResourceCloser("workflow-registry", workflow_resource.close),
                OwnedResourceCloser("foreign-database", other_database.close),
            ),
        )

    def load():
        return SdkRuntimeBuildInputs(
            profiles=base.profiles,
            drivers=base.drivers,
            ports_factory=base.ports_factory,
            workflow_catalog_digest=base.workflow_catalog_digest,
            workflow_factory=workflow_factory,
        )

    stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(tmp_path / "user-data"),
        candidate_identity=IDENTITY,
        dependency_loader=load,
    )
    with pytest.raises(ValueError, match="foreign transaction owner"):
        await stack.start()
    assert closed == ["workflow-registry"]
    assert workflow_resource.close_calls == 1


@pytest.mark.asyncio
async def test_post_database_workflow_resources_close_before_dependencies_on_failure(
    tmp_path: Path,
) -> None:
    closed: list[str] = []
    dependency = _OwnedResource("product-state", closed, asynchronous=False)
    workflow = _OwnedResource("workflow-registry", closed, asynchronous=True)
    base = _inputs(_FailOnceReconciliation())

    def load():
        return SdkRuntimeBuildInputs(
            profiles=base.profiles,
            drivers=base.drivers,
            ports_factory=base.ports_factory,
            workflow_catalog_digest=base.workflow_catalog_digest,
            workflow_factory=lambda _database, uow: WorkflowRuntimeBuild(
                transaction_owner=uow,
                runner=None,
                registrations=("host",),
                owned_resources=(
                    OwnedResourceCloser("workflow-registry", workflow.close),
                ),
            ),
            owned_resources=(
                OwnedResourceCloser("product-state", dependency.close),
            ),
        )

    stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(tmp_path / "user-data"),
        candidate_identity=IDENTITY,
        dependency_loader=load,
    )
    with pytest.raises(RuntimeError, match="startup reconciliation failed"):
        await stack.start()
    assert closed == ["workflow-registry", "product-state"]
    assert workflow.close_calls == dependency.close_calls == 1


@pytest.mark.asyncio
async def test_workflow_factory_scope_closes_partial_resources_before_return(
    tmp_path: Path,
) -> None:
    closed: list[str] = []
    first = _OwnedResource("first", closed, asynchronous=False)
    second = _OwnedResource("second", closed, asynchronous=True)
    base = _inputs()

    async def workflow_factory(_database, _uow):  # type: ignore[no-untyped-def]
        async with WorkflowFactoryResourceScope() as scope:
            scope.own(OwnedResourceCloser("first", first.close))
            scope.own(OwnedResourceCloser("second", second.close))
            raise RuntimeError("workflow factory failed before return")

    stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(tmp_path / "user-data"),
        candidate_identity=IDENTITY,
        dependency_loader=lambda: SdkRuntimeBuildInputs(
            profiles=base.profiles,
            drivers=base.drivers,
            ports_factory=base.ports_factory,
            workflow_catalog_digest=base.workflow_catalog_digest,
            workflow_factory=workflow_factory,
        ),
    )
    with pytest.raises(RuntimeError, match="failed before return"):
        await stack.start()
    assert closed == ["second", "first"]
    assert first.close_calls == second.close_calls == 1


@pytest.mark.asyncio
async def test_prebuilt_workflow_runner_is_rejected_in_favor_of_post_db_factory(
    tmp_path: Path,
) -> None:
    base = _inputs()
    inputs = SdkRuntimeBuildInputs(
        profiles=base.profiles,
        drivers=base.drivers,
        ports_factory=base.ports_factory,
        workflow_catalog_digest=base.workflow_catalog_digest,
        workflow_runner=object(),
    )
    stack = ProductSdkRuntimeStack(
        paths=ProductRuntimePathsAdapter(tmp_path / "user-data"),
        candidate_identity=IDENTITY,
        dependency_loader=lambda: inputs,
    )
    with pytest.raises(ValueError, match="prebuilt workflow bindings are forbidden"):
        await stack.start()
    with pytest.raises(SdkRuntimeNotReady):
        stack.require_ready()


def test_s5b_effect_gate_and_root_resolver_are_constructor_wired_in_main() -> None:
    """S5b Task 1：resolver / gate / 故障备忘的接线与服务层同 commit；缺任一 → startup stable fail。"""
    source = (PROJECT_ROOT / "backend/main.py").read_text(encoding="utf-8")

    assert "root_resolver=BindingRootResolver(" in source
    assert "fault_sink=_ensure_run_fault_memo()" in source
    assert "effect_gate=_sdk_effect_gate" in source or "effect_gate=effect_gate" in source
    assert 'service_context.register("sdk_effect_gate"' in source
    assert '"sdk_effect_gate",' in source  # 缺槽断言 tuple
    assert "run_fault_sink=_ensure_run_fault_memo()" in source
    assert "run_fault_memo=_ensure_run_fault_memo()" in source


@pytest.mark.asyncio
async def test_production_context_authority_injects_closure_instruction_when_admission_scope_dirty(tmp_path: Path) -> None:
    """Task 3 审查 F-2（P1）：生产装配（``main._build_run_context_authority``）的 ``ProductRunContextAuthority``
    必须带真实 ``closure_reader``；fresh state.db 上上一 Run pending → 本 Run ``prepare_snapshot`` 的 protected
    分区含 ``source=semantic_closure`` 收口指令；缺 reader → 组合缺件（startup fail）。"""

    import json as _json
    from types import SimpleNamespace

    from simple_harness.contracts import RunId
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.execution.context_authority import (
        ContextRouteState,
        RunContextAuthorityRequest,
    )
    from simple_harness.providers import ProviderToolSpec
    from simple_harness.runtime.context import ContextSnapshot

    import main
    from deskpet.sdk_adapters.context_authority import (
        ContextRouteLedgerStore,
        ProductRunContextAuthority,
    )
    from tests.sdk_adapters import s5b_closure_harness as ch

    # 上一 Run：脏 + 模型不收口 → pending receipt，终态照常。
    env = await ch.bound_run(tmp_path, "sdk-run-prev")
    await ch.material_write(env, "e-1")
    facts = ch.FakeRunFacts(env.run_id)
    observed = await ch.observe_terminal(env, facts)
    fallback, _ = ch.build_fallback(env, facts, ch.FakeAdapter([ch.plain_answer()]))
    assert (await ch.settle(env, fallback)).status == "pending"
    await ch.record_terminal(env, observed)
    # 本 Run（同 admission scope）。
    env2 = await ch.next_run(env, "sdk-run-next")

    class _Context:
        revision = 1
        messages = (Message(role=MessageRole.USER, content="继续"),)

        def load(self, run_id):
            return ContextSnapshot(self.revision, self.messages)

    ports = SimpleNamespace(
        context=_Context(),
        react_checkpoint=SimpleNamespace(read_start_snapshot=lambda run_id: {"input": {"context_metadata": {"budget": {"context_window": 32768}}}}),
    )
    exposure = SimpleNamespace(provider_specs=lambda run_id: (ProviderToolSpec("task_scope_update", "close", {"type": "object"}),))
    authority = main._build_run_context_authority(
        state_db_path=env2.db_path,
        ports_resolver=lambda: ports,
        exposure_resolver=lambda run_id: exposure,
        ledger=ContextRouteLedgerStore(env2.db_path),
        reconcile=None,
    )
    assert isinstance(authority, ProductRunContextAuthority)
    assert authority._closure_reader is not None
    snapshot = await authority.prepare_snapshot(
        RunContextAuthorityRequest(RunId("sdk-run-next"), 1, 1, ContextRouteState.UNROUTED, None, "c" * 64)
    )
    protected = [
        m for m in snapshot.messages
        if m.role is MessageRole.SYSTEM and dict(getattr(m, "metadata", {}) or {}).get("source") == "semantic_closure"
    ]
    assert len(protected) == 1
    body = _json.loads(str(protected[0].content))
    assert body["kind"] == "task_scope_closure_required" and body["task_scope_id"] == ch.SCOPE
    assert [p["reason_code"] for p in body["pending_receipts"]] == ["closure_model_declined"]
    # 缺件：生产装配把 reader 登记为 service_context 槽 ``sdk_closure_instruction_reader`` 并进缺槽断言。
    source = Path(main.__file__).read_text(encoding="utf-8")
    assert 'service_context.register("sdk_closure_instruction_reader"' in source
    assert '"sdk_closure_instruction_reader",' in source.split("for slot in (")[1].split(")")[0]


# ---- S5b Task 6 / P0：真实启动顺序下的 stack 构建与缺件 fail-closed ----


def _snapshot_slots(context, names):  # type: ignore[no-untyped-def]
    return {name: context.get(name) for name in names}


def _restore_slots(context, snapshot) -> None:  # type: ignore[no-untyped-def]
    for name, value in snapshot.items():
        context.register(name, value)


@pytest.mark.asyncio
async def test_product_sdk_runtime_stack_builds_on_real_startup_order(tmp_path: Path, monkeypatch) -> None:
    """P0（真实桌面预演 2026-09-03）：真实启动顺序 `_activate_product_sdk_runtime` →
    `_build_product_sdk_runtime_stack` → `_activate_memory_analysis_lane` 时，模块全局
    `_sdk_provider_binding_resolver` 尚未赋值（它在 stack 构建之后才写），而 builder 早已把
    resolver 注册进 `service_context["sdk_provider_binding_resolver"]`。lane 激活必须读 service_context
    真相；缺件必须让启动 raise（不是 `product_sdk_runtime_skipped` warning）。"""

    import main
    from deskpet.memory.analysis_executor import HostMemoryAnalysisExecutor
    from deskpet.memory.evidence_authority import HostEvidenceAuthority
    from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
    from deskpet.memory.memory_ingestion_outbox import MemoryAnalysisLane
    from deskpet.memory.schema import initialize_human_memory_program_state_db

    state_db = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(state_db)
    started: list[str] = []
    monkeypatch.setattr(MemoryAnalysisLane, "start", lambda self: started.append("lane"))
    monkeypatch.setattr(main, "_state_db_path", state_db)
    # 真实构建期状态：全局尚为 None、无 provider chain、lane 未建。
    monkeypatch.setattr(main, "_sdk_provider_binding_resolver", None)
    monkeypatch.setattr(main, "_provider_registry", None)
    monkeypatch.setattr(main, "_memory_analysis_lane", None)
    v7 = HumanMemoryV7Runtime(
        tmp_path / "v7.db",
        embedder_getter=lambda: None,
        evidence_authority=HostEvidenceAuthority(state_db),
        analysis_authority=HostMemoryAnalysisExecutor(state_db, adapter_factory=lambda record: None),
    )
    resolver = object()
    slots = (
        "human_memory_v7_runtime", "sdk_provider_binding_resolver", "sdk_evidence_authority",
        "sdk_memory_analysis_executor", "sdk_memory_ingestion_outbox",
    )
    saved = _snapshot_slots(main.service_context, slots)
    try:
        main.service_context.register("human_memory_v7_runtime", v7)
        main.service_context.register("sdk_provider_binding_resolver", resolver)
        for slot in slots[2:]:
            main.service_context.register(slot, None)
        # ① 与真实启动同序：全局 None + service_context 已注册 → 激活成功、槽齐全。
        main._activate_memory_analysis_lane()
        assert started == ["lane"]
        assert main.service_context.get("sdk_evidence_authority") is v7.evidence_authority
        assert main.service_context.get("sdk_memory_analysis_executor") is v7.analysis_authority
        assert isinstance(main.service_context.get("sdk_memory_ingestion_outbox"), MemoryAnalysisLane)
        # ② resolver 槽缺失 → 稳定缺件码（不是 AttributeError/静默）。
        main.service_context.register("sdk_provider_binding_resolver", None)
        monkeypatch.setattr(main, "_memory_analysis_lane", None)
        with pytest.raises(RuntimeError, match="sdk_context_authority_composition_missing:sdk_provider_binding_resolver"):
            main._activate_memory_analysis_lane()
    finally:
        _restore_slots(main.service_context, saved)

    # ③ `_activate_product_sdk_runtime`：composition 缺件 → 启动抛错，而不是 warning + skip。
    from types import SimpleNamespace

    class _Uow:
        async def initialize(self) -> None: ...

        async def get_runtime_state(self):  # type: ignore[no-untyped-def]
            return SimpleNamespace(phase="open", generation=1)

        async def activate_runtime(self, *, command):  # type: ignore[no-untyped-def]
            raise AssertionError("no activation transition expected")

    async def _broken_build(generation: int):  # type: ignore[no-untyped-def]
        raise RuntimeError("sdk_context_authority_composition_missing:sdk_effect_gate")

    saved = _snapshot_slots(main.service_context, ("workflow_service", "provider_registry"))
    try:
        main.service_context.register("workflow_service", SimpleNamespace(execution_uow=_Uow()))
        main.service_context.register("provider_registry", object())
        monkeypatch.setattr(main, "_build_product_sdk_runtime_stack", _broken_build)
        with pytest.raises(RuntimeError, match="sdk_context_authority_composition_missing:sdk_effect_gate"):
            await main._activate_product_sdk_runtime()
    finally:
        _restore_slots(main.service_context, saved)
