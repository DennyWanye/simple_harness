"""Product-owned lifecycle for the SDK Runtime with ingress kept closed."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import inspect
import time
from types import MappingProxyType
from typing import TypeAlias

from simple_harness import (
    ROOT_PROFILE_KEY,
    RunId,
    Runtime,
    RuntimeDriver,
    RuntimePorts,
    RuntimeProfile,
    build_runtime,
)
from simple_harness.execution.sqlite import Database, SqliteExecutionUnitOfWork

from .runtime_paths import (
    ProductRuntimePathsAdapter,
    SdkCandidateIdentity,
    verify_sdk_candidate,
)


PortsFactory: TypeAlias = Callable[
    [Database, SqliteExecutionUnitOfWork], RuntimePorts
]
@dataclass(frozen=True, slots=True)
class ProductionRuntimeBuild:
    runtime: Runtime
    transaction_owner: SqliteExecutionUnitOfWork

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, Runtime):
            raise TypeError("production runtime must be Runtime")
        if not isinstance(self.transaction_owner, SqliteExecutionUnitOfWork):
            raise TypeError("production transaction owner must be SQLite UoW")


RuntimeFactory: TypeAlias = Callable[[object], ProductionRuntimeBuild]


@dataclass(frozen=True, slots=True)
class OwnedResourceCloser:
    """One product-created resource transferred to stack lifecycle ownership."""

    name: str
    close: Callable[[], object]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("owned resource name is required")
        if not callable(self.close):
            raise TypeError("owned resource close must be callable")


async def _close_owned_resources(
    resources: tuple[OwnedResourceCloser, ...],
) -> None:
    errors: list[BaseException] = []
    for resource in reversed(resources):
        try:
            closed = resource.close()
            if inspect.isawaitable(closed):
                await closed
        except BaseException as exc:
            errors.append(exc)
    if errors:
        raise BaseExceptionGroup("owned resource cleanup failed", errors)


class WorkflowFactoryResourceScope:
    """Own resources until a post-DB workflow factory returns successfully.

    A factory must keep every resource it creates in this scope until it can
    return a complete :class:`WorkflowRuntimeBuild`. Calling ``transfer``
    moves ownership to that build; an exception before transfer closes the
    partial resources in reverse order.
    """

    def __init__(self) -> None:
        self._resources: list[OwnedResourceCloser] = []
        self._transferred = False

    async def __aenter__(self) -> WorkflowFactoryResourceScope:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        if not self._transferred:
            resources = tuple(self._resources)
            self._resources.clear()
            await _close_owned_resources(resources)

    def own(self, resource: OwnedResourceCloser) -> None:
        if self._transferred:
            raise RuntimeError("workflow factory resources already transferred")
        if not isinstance(resource, OwnedResourceCloser):
            raise TypeError("workflow factory resource must be OwnedResourceCloser")
        if any(existing.name == resource.name for existing in self._resources):
            raise ValueError("workflow factory resource names must be unique")
        self._resources.append(resource)

    def transfer(self) -> tuple[OwnedResourceCloser, ...]:
        if self._transferred:
            raise RuntimeError("workflow factory resources already transferred")
        self._transferred = True
        return tuple(self._resources)


@dataclass(frozen=True, slots=True)
class WorkflowRuntimeBuild:
    """Post-database workflow binding transferred to the runtime stack."""

    transaction_owner: SqliteExecutionUnitOfWork
    runner: object | None
    registrations: tuple[object, ...] = ()
    owned_resources: tuple[OwnedResourceCloser, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.transaction_owner, SqliteExecutionUnitOfWork):
            raise TypeError(
                "workflow transaction_owner must be SqliteExecutionUnitOfWork"
            )
        object.__setattr__(self, "registrations", tuple(self.registrations))
        resources = tuple(self.owned_resources)
        if any(not isinstance(item, OwnedResourceCloser) for item in resources):
            raise TypeError(
                "workflow owned_resources must contain OwnedResourceCloser"
            )
        if len({item.name for item in resources}) != len(resources):
            raise ValueError("workflow owned resource names must be unique")
        object.__setattr__(self, "owned_resources", resources)


WorkflowFactory: TypeAlias = Callable[
    [Database, SqliteExecutionUnitOfWork],
    WorkflowRuntimeBuild | Awaitable[WorkflowRuntimeBuild],
]


@dataclass(frozen=True, slots=True)
class SdkRuntimeBuildInputs:
    """Fresh product dependencies read for one isolated startup attempt."""

    profiles: Mapping[str, RuntimeProfile]
    drivers: Mapping[str, RuntimeDriver]
    ports_factory: PortsFactory
    workflow_catalog_digest: str
    workflow_factory: WorkflowFactory | None = None
    # Retained only to fail closed for stale callers instead of silently
    # binding a runner that was created before the SDK transaction owner.
    workflow_registrations: tuple[object, ...] = ()
    workflow_runner: object | None = None
    owned_resources: tuple[OwnedResourceCloser, ...] = ()
    runtime_factory: RuntimeFactory | None = None

    def __post_init__(self) -> None:
        profiles = dict(self.profiles)
        drivers = dict(self.drivers)
        if ROOT_PROFILE_KEY not in profiles:
            raise ValueError("SDK root profile is required")
        if not drivers:
            raise ValueError("at least one SDK runtime driver is required")
        if not callable(self.ports_factory):
            raise TypeError("ports_factory must be callable")
        if self.runtime_factory is not None and not callable(self.runtime_factory):
            raise TypeError("runtime_factory must be callable")
        if not self.workflow_catalog_digest.strip():
            raise ValueError("workflow_catalog_digest is required")
        if self.workflow_factory is not None and not callable(self.workflow_factory):
            raise TypeError("workflow_factory must be callable")
        object.__setattr__(self, "profiles", MappingProxyType(profiles))
        object.__setattr__(self, "drivers", MappingProxyType(drivers))
        object.__setattr__(
            self, "workflow_registrations", tuple(self.workflow_registrations)
        )
        resources = tuple(self.owned_resources)
        if any(not isinstance(item, OwnedResourceCloser) for item in resources):
            raise TypeError("owned_resources must contain OwnedResourceCloser")
        if len({item.name for item in resources}) != len(resources):
            raise ValueError("owned resource names must be unique")
        object.__setattr__(self, "owned_resources", resources)


def _validate_owned_resources(
    dependency_resources: tuple[OwnedResourceCloser, ...],
    workflow_resources: tuple[OwnedResourceCloser, ...],
) -> None:
    combined = dependency_resources + workflow_resources
    if len({item.name for item in combined}) != len(combined):
        raise ValueError("owned resource names must be unique across runtime layers")


@dataclass(frozen=True, slots=True)
class SdkRuntimeReady:
    """The sole immutable publication proving the complete stack is ready."""

    generation: int
    runtime: Runtime
    client: object
    workflow_catalog_digest: str
    workflow_registrations: tuple[object, ...]
    ready_at: float


class SdkRuntimeNotReady(RuntimeError):
    """Raised by the closed ingress barrier before atomic ready publication."""


DependencyLoader: TypeAlias = Callable[
    [], SdkRuntimeBuildInputs | Awaitable[SdkRuntimeBuildInputs]
]


class ProductSdkRuntimeStack:
    """Serialize SDK startup/retry/close and publish one immutable ready slot."""

    def __init__(
        self,
        *,
        paths: ProductRuntimePathsAdapter,
        candidate_identity: SdkCandidateIdentity,
        dependency_loader: DependencyLoader,
        ready_publisher: Callable[[SdkRuntimeReady | None], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(paths, ProductRuntimePathsAdapter):
            raise TypeError("paths must be ProductRuntimePathsAdapter")
        if not callable(dependency_loader):
            raise TypeError("dependency_loader must be callable")
        if not isinstance(candidate_identity, SdkCandidateIdentity):
            raise TypeError("candidate_identity must be SdkCandidateIdentity")
        self._paths = paths
        self._candidate_identity = candidate_identity
        self._dependency_loader = dependency_loader
        self._ready_publisher = ready_publisher or (lambda _ready: None)
        self._clock = clock
        self._lock = asyncio.Lock()
        self._ready: SdkRuntimeReady | None = None
        self._runtime: Runtime | None = None
        self._database: Database | None = None
        self._uow: SqliteExecutionUnitOfWork | None = None
        self._owned_resources: tuple[OwnedResourceCloser, ...] = ()
        self._successful_generation = 0
        self._close_requested = False
        self._phase = "new"

    @property
    def phase(self) -> str:
        return self._phase

    def require_ready(self) -> SdkRuntimeReady:
        ready = self._ready
        if ready is None or self._close_requested:
            raise SdkRuntimeNotReady("SDK Runtime ingress is not ready")
        return ready

    async def _load_dependencies(self) -> SdkRuntimeBuildInputs:
        loaded = self._dependency_loader()
        if inspect.isawaitable(loaded):
            loaded = await loaded
        if not isinstance(loaded, SdkRuntimeBuildInputs):
            raise TypeError("dependency_loader must return SdkRuntimeBuildInputs")
        return loaded

    @staticmethod
    async def _build_workflow_runtime(
        dependencies: SdkRuntimeBuildInputs,
        database: Database,
        uow: SqliteExecutionUnitOfWork,
    ) -> WorkflowRuntimeBuild:
        if dependencies.workflow_runner is not None or dependencies.workflow_registrations:
            raise ValueError(
                "prebuilt workflow bindings are forbidden; use workflow_factory"
            )
        factory = dependencies.workflow_factory
        if factory is None:
            return WorkflowRuntimeBuild(uow, None)
        built = factory(database, uow)
        if inspect.isawaitable(built):
            built = await built
        if not isinstance(built, WorkflowRuntimeBuild):
            raise TypeError("workflow_factory must return WorkflowRuntimeBuild")
        return built

    @staticmethod
    async def _cleanup(
        runtime: Runtime | None,
        owned_resources: tuple[OwnedResourceCloser, ...],
        database: Database | None,
    ) -> None:
        errors: list[BaseException] = []
        if runtime is not None:
            try:
                await runtime.close()
            except BaseException as exc:  # cleanup must continue in reverse order
                errors.append(exc)
        try:
            await _close_owned_resources(owned_resources)
        except BaseException as exc:  # cleanup must continue to the database
            errors.append(exc)
        if database is not None:
            try:
                database.close()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise BaseExceptionGroup("SDK Runtime resource cleanup failed", errors)

    async def start(self) -> SdkRuntimeReady:
        async with self._lock:
            if self._ready is not None and not self._close_requested:
                return self._ready
            self._close_requested = False
            self._phase = "starting"
            runtime: Runtime | None = None
            database: Database | None = None
            owned_resources: tuple[OwnedResourceCloser, ...] = ()
            try:
                verify_sdk_candidate(self._candidate_identity)
                dependencies = await self._load_dependencies()
                owned_resources = dependencies.owned_resources
                if dependencies.runtime_factory is not None:
                    if dependencies.workflow_factory is not None:
                        raise ValueError(
                            "production runtime factory owns workflow composition"
                        )
                    production = dependencies.runtime_factory(
                        self._paths.execution_database
                    )
                    if not isinstance(production, ProductionRuntimeBuild):
                        raise TypeError(
                            "runtime_factory must return ProductionRuntimeBuild"
                        )
                    runtime = production.runtime
                    uow = production.transaction_owner
                else:
                    database = Database.open(self._paths.execution_database)
                    uow = SqliteExecutionUnitOfWork(database)
                    workflow = await self._build_workflow_runtime(
                        dependencies, database, uow
                    )
                    dependency_resources = owned_resources
                    # Transfer the factory-created resources before validation so
                    # even a cross-layer identity collision is cleaned up.
                    owned_resources = dependency_resources + workflow.owned_resources
                    _validate_owned_resources(
                        dependency_resources, workflow.owned_resources
                    )
                    if workflow.transaction_owner is not uow:
                        raise ValueError(
                            "workflow factory returned a foreign transaction owner"
                        )
                    ports = dependencies.ports_factory(database, uow)
                    if not isinstance(ports, RuntimePorts):
                        raise TypeError("ports_factory must return RuntimePorts")
                    runtime = build_runtime(
                        uow,
                        dependencies.profiles,
                        dependencies.drivers,
                        ports,
                        workflow_runner=workflow.runner,
                    )
                await runtime.start()
                if self._close_requested:
                    raise SdkRuntimeNotReady(
                        "SDK Runtime close raced with startup publication"
                    )
                generation = self._successful_generation + 1
                ready = SdkRuntimeReady(
                    generation=generation,
                    runtime=runtime,
                    client=runtime.client,
                    workflow_catalog_digest=dependencies.workflow_catalog_digest,
                    workflow_registrations=workflow.registrations,
                    ready_at=float(self._clock()),
                )
                self._runtime = runtime
                self._database = database
                self._uow = uow
                self._owned_resources = owned_resources
                self._ready = ready
                self._successful_generation = generation
                self._phase = "ready"
                self._ready_publisher(ready)
                return ready
            except BaseException as startup_error:
                self._ready = None
                self._runtime = None
                self._database = None
                self._uow = None
                self._owned_resources = ()
                self._phase = "failed"
                try:
                    await self._cleanup(runtime, owned_resources, database)
                except BaseException as cleanup_error:
                    startup_error.add_note(
                        "resource cleanup also failed: "
                        f"{type(cleanup_error).__name__}"
                    )
                finally:
                    self._ready_publisher(None)
                raise

    async def reconcile(self) -> None:
        await self.require_ready().runtime.reconcile()

    async def recover(self) -> None:
        await self.require_ready().runtime.recover()

    def query(self, run_id: str):  # type: ignore[no-untyped-def]
        return self.require_ready().client.query(RunId(run_id))

    def list_open_authorization_decisions(
        self,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[tuple[object, Mapping[str, object]], ...]:
        """Read durable open Tool decisions without exposing the SDK UoW.

        The desktop host needs this projection after a Run becomes WAITING and
        again after a control-channel reconnect.  Keep the SQLite/schema access
        inside the SDK lifecycle boundary instead of letting ``main.py`` reach
        into ``RunClient._runtime``.
        """

        self.require_ready()
        uow = self._uow
        if uow is None:
            raise SdkRuntimeNotReady("SDK Runtime transaction owner is unavailable")
        clauses = ["d.state='open'", "d.kind='tool_authorization'"]
        values: list[str] = []
        if run_id is not None:
            clauses.append("d.run_id=?")
            values.append(str(run_id))
        if session_id is not None:
            clauses.append("r.execution_session_id=?")
            values.append(str(session_id))
        rows = uow.database.connection.execute(
            "SELECT d.decision_id,d.run_id FROM decisions AS d "
            "JOIN runs AS r ON r.run_id=d.run_id WHERE "
            + " AND ".join(clauses)
            + " ORDER BY d.created_at,d.decision_id",
            tuple(values),
        ).fetchall()
        projected: list[tuple[object, Mapping[str, object]]] = []
        for row in rows:
            decision = uow.read_decision(str(row["decision_id"]))
            start = uow.read_start_snapshot(str(row["run_id"]))
            if decision is None or not isinstance(start, Mapping):
                continue
            projected.append((decision, start))
        return tuple(projected)

    async def close(self) -> None:
        self._close_requested = True
        try:
            self._ready_publisher(None)
        finally:
            async with self._lock:
                runtime = self._runtime
                database = self._database
                owned_resources = self._owned_resources
                self._ready = None
                self._runtime = None
                self._database = None
                self._uow = None
                self._owned_resources = ()
                self._phase = "closing"
                try:
                    await self._cleanup(runtime, owned_resources, database)
                finally:
                    self._phase = "closed"


async def build_product_runtime(
    *,
    paths: ProductRuntimePathsAdapter,
    candidate_identity: SdkCandidateIdentity,
    dependency_loader: DependencyLoader,
    ready_publisher: Callable[[SdkRuntimeReady | None], None] | None = None,
) -> ProductSdkRuntimeStack:
    """Build and start the closed-ingress product SDK lifecycle facade."""

    stack = ProductSdkRuntimeStack(
        paths=paths,
        candidate_identity=candidate_identity,
        dependency_loader=dependency_loader,
        ready_publisher=ready_publisher,
    )
    await stack.start()
    return stack


__all__ = (
    "OwnedResourceCloser",
    "ProductSdkRuntimeStack",
    "ProductionRuntimeBuild",
    "SdkRuntimeBuildInputs",
    "SdkRuntimeNotReady",
    "SdkRuntimeReady",
    "WorkflowFactory",
    "WorkflowFactoryResourceScope",
    "WorkflowRuntimeBuild",
    "build_product_runtime",
)
