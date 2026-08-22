# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3

import pytest

from simple_harness import (
    ROOT_PROFILE_KEY,
    RuntimePorts,
    RuntimeProfile,
    SqliteContextPort,
    build_runtime,
)
from simple_harness.execution.sqlite import Database, SqliteExecutionUnitOfWork

from deskpet.sdk_adapters.composition import (
    OwnedResourceCloser,
    ProductSdkRuntimeStack,
    ProductionRuntimeBuild,
    SdkRuntimeBuildInputs,
    SdkRuntimeNotReady,
    WorkflowFactoryResourceScope,
    WorkflowRuntimeBuild,
)
from deskpet.sdk_adapters.runtime_paths import ProductRuntimePathsAdapter
from deskpet.sdk_adapters.sdk_candidate import build_candidate_identity, sdk_wheel_path
from context import ServiceContext


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
    async def invoke(self, value):  # pragma: no cover - ingress remains closed
        raise AssertionError(f"driver must not run during startup: {value!r}")


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
        ).fetchone()[0] == 4
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
