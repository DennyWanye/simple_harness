"""Product-owned lifecycle for the SDK Runtime with ingress kept closed."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, TypeAlias

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
from simple_harness.execution.uow import RunState
from simple_harness.runtime import StartSnapshot

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
    workflow_registrations: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, Runtime):
            raise TypeError("production runtime must be Runtime")
        if not isinstance(self.transaction_owner, SqliteExecutionUnitOfWork):
            raise TypeError("production transaction owner must be SQLite UoW")
        object.__setattr__(
            self, "workflow_registrations", tuple(self.workflow_registrations)
        )


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


@dataclass(frozen=True, slots=True)
class SkillInstallVerificationEvidence:
    status: str
    run_id: str
    terminal_event_id: str | None = None
    terminal_event_hash: str | None = None
    evidence_hash: str | None = None
    payload: Mapping[str, object] | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class SdkRunTerminalEvidence:
    """Authenticated terminal event read from the SDK transaction owner."""

    run_id: str
    state: str
    event_id: str
    event_hash: str
    occurred_at: float
    # SDK public error code of a failed Run (``driver_failed`` …); the frozen SDK
    # never exposes the private cause, so Host-raised whole-Run faults are
    # labelled by the Host RunFaultMemo instead (foreground terminal observer).
    error_code: str | None = None


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
        self._preflight_lock = asyncio.Lock()
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
            workflow_registrations: tuple[object, ...] = ()
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
                    workflow_registrations = production.workflow_registrations
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
                    workflow_registrations = workflow.registrations
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
                    workflow_registrations=workflow_registrations,
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

    def read_skill_install_verification_evidence(
        self, expected_attempt: object
    ) -> SkillInstallVerificationEvidence:
        """Read and validate one Host-control terminal proof atomically."""

        self.require_ready()
        uow = self._uow
        if uow is None:
            raise SdkRuntimeNotReady("SDK Runtime transaction owner is unavailable")
        expected_run_id = str(getattr(expected_attempt, "expected_run_id", ""))
        attempt_id = str(getattr(expected_attempt, "attempt_id", ""))
        if not expected_run_id or not attempt_id:
            raise ValueError("complete Skill verification attempt is required")
        with uow.database.transaction() as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE run_id=?", (expected_run_id,)
            ).fetchone()
            if run is None:
                return SkillInstallVerificationEvidence("not_started", expected_run_id)
            snapshot_row = connection.execute(
                "SELECT snapshot_json FROM run_start_snapshots WHERE run_id=?",
                (expected_run_id,),
            ).fetchone()
            if snapshot_row is None:
                return _corrupt_verification(expected_run_id, "start_snapshot_missing")
            try:
                snapshot = StartSnapshot.from_json(json.loads(str(snapshot_row[0])))
            except (TypeError, ValueError, json.JSONDecodeError):
                return _corrupt_verification(expected_run_id, "start_snapshot_corrupt")
            authority = snapshot.host_control_authority
            from .skill_install_verification import (
                SKILL_INSTALL_VERIFICATION_PURPOSE,
                skill_install_verification_authority_hash,
            )

            expected_authority_hash = skill_install_verification_authority_hash(
                expected_attempt  # type: ignore[arg-type]
            )
            if (
                snapshot.start_mode != "host_control"
                or authority is None
                or authority.purpose != SKILL_INSTALL_VERIFICATION_PURPOSE
                or authority.authority_ref != attempt_id
                or authority.authority_hash != expected_authority_hash
                or authority.generation
                != int(getattr(expected_attempt, "attempt_generation", -1))
                or str(run["execution_session_id"])
                != str(getattr(expected_attempt, "verifier_session_id", ""))
                or str(run["request_id"])
                != str(getattr(expected_attempt, "request_id", ""))
                or snapshot.turn_id != str(getattr(expected_attempt, "turn_id", ""))
            ):
                return _corrupt_verification(expected_run_id, "start_authority_mismatch")
            state = RunState(str(run["state"]))
            if state not in {RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED}:
                return SkillInstallVerificationEvidence("nonterminal", expected_run_id)
            events = connection.execute(
                "SELECT event_id,kind,payload_json FROM run_events "
                "WHERE run_id=? AND kind IN ('run.completed','run.failed','run.cancelled')",
                (expected_run_id,),
            ).fetchall()
            if len(events) != 1:
                return _corrupt_verification(expected_run_id, "terminal_event_ambiguous")
            event = events[0]
            raw_payload = str(event["payload_json"])
            if len(raw_payload.encode("utf-8")) > 4096:
                return _corrupt_verification(expected_run_id, "terminal_payload_oversize")
            try:
                payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                return _corrupt_verification(expected_run_id, "terminal_payload_corrupt")
            if not isinstance(payload, dict):
                return _corrupt_verification(expected_run_id, "terminal_payload_not_object")
            counts = {
                "provider_invocation_count": connection.execute(
                    "SELECT COUNT(*) FROM provider_invocations WHERE run_id=?",
                    (expected_run_id,),
                ).fetchone()[0],
                "effect_count": connection.execute(
                    "SELECT COUNT(*) FROM execution_effects WHERE run_id=?",
                    (expected_run_id,),
                ).fetchone()[0],
                "checkpoint_count": connection.execute(
                    "SELECT COUNT(*) FROM workflow_checkpoints WHERE run_id=?",
                    (expected_run_id,),
                ).fetchone()[0],
                "continuation_count": connection.execute(
                    "SELECT COUNT(*) FROM continuations WHERE run_id=?",
                    (expected_run_id,),
                ).fetchone()[0],
            }
            if any(int(value) != 0 for value in counts.values()):
                return _corrupt_verification(expected_run_id, "verification_side_effects_present")
            verification_terminal = (
                payload.get("schema") == "skill-install-verification-final-v1"
                and payload.get("status") in {"succeeded", "failed"}
            )
            if verification_terminal:
                if any(int(payload.get(name, -1)) != 0 for name in counts):
                    return _corrupt_verification(
                        expected_run_id, "terminal_zero_count_mismatch"
                    )
            elif state is RunState.COMPLETED:
                # Only the verification Driver can legitimately complete this
                # Host-control Run. A completed root without its bounded final
                # contract is corrupt. SDK admission/runtime failures, however,
                # use the SDK's own structured failed/cancelled envelope before
                # the Driver starts; the authoritative DB counts above prove
                # that those terminal paths remained side-effect free.
                return _corrupt_verification(
                    expected_run_id, "terminal_contract_mismatch"
                )
            event_hash = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
            evidence_payload = {
                "schema": "skill-install-verification-evidence-v1",
                "run_id": expected_run_id,
                "attempt_id": attempt_id,
                "terminal_event_id": str(event["event_id"]),
                "terminal_event_hash": event_hash,
                **counts,
            }
            evidence_hash = hashlib.sha256(
                json.dumps(evidence_payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            succeeded = (
                state is RunState.COMPLETED
                and payload.get("schema") == "skill-install-verification-final-v1"
                and payload.get("status") == "succeeded"
            )
            return SkillInstallVerificationEvidence(
                "terminal_succeeded" if succeeded else "terminal_failed",
                expected_run_id,
                str(event["event_id"]),
                event_hash,
                evidence_hash,
                payload,
                (
                    None
                    if succeeded
                    else str(
                        payload.get("reason_code")
                        or payload.get("code")
                        or "verification_failed"
                    )
                ),
            )

    async def read_reserved_fact(self, reservation: Any) -> Any | None:
        """S5b Task 2 terminal drain: resolve a still-reserved Harness row.

        ``effect:{effect_id}`` → the SDK effect ledger (terminal record →
        ``ToolInvocationFact`` incl. the objective event);
        ``provider:{request_id}`` → the SDK provider invocation ledger
        (terminal record → ``ProviderInvocationFact``).  Anything else, or a
        record that is not terminal, returns ``None`` and is tombstoned.
        """

        from simple_harness import EffectId, RequestId, RunId, thaw_json
        from simple_harness.execution.provider_invocations import (
            TERMINAL_PROVIDER_INVOCATION_STATES,
            provider_invocation_id,
            provider_response_from_json,
        )

        from deskpet.execution.evidence_ingress import (
            ProviderInvocationFact,
            ToolInvocationFact,
        )
        from deskpet.sdk_adapters.effect_gate import classify_objective_event

        uow = self._uow
        if uow is None:
            return None
        source_event_id = str(reservation.source_event_id)
        if source_event_id.startswith("effect:"):
            record = uow.read_effect(EffectId(source_event_id.removeprefix("effect:")))
            if record is None or not record.terminal or record.result is None:
                return None
            if record.run_id.value != reservation.run_id:
                return None
            result = record.result
            return ToolInvocationFact(
                run_id=record.run_id.value,
                effect_id=record.effect_id.value,
                call_id=record.call_id.value,
                tool_name=record.tool_name,
                effect_state=record.state.value,
                outcome=result.outcome.value,
                error_code=result.error_code,
                objective=classify_objective_event(
                    record.tool_name,
                    dict(thaw_json(record.arguments)),
                    result,
                    effect_id=record.effect_id.value,
                    call_id=record.call_id.value,
                ),
            )
        if source_event_id.startswith("provider:"):
            request_id = source_event_id.removeprefix("provider:")
            invocation = uow.read_provider_invocation(
                provider_invocation_id(RunId(reservation.run_id), RequestId(request_id))
            )
            if invocation is None or invocation.state not in TERMINAL_PROVIDER_INVOCATION_STATES:
                return None
            response = None
            if invocation.response_json is not None:
                try:
                    response = provider_response_from_json(thaw_json(invocation.response_json))
                except Exception:  # noqa: BLE001 - a malformed durable response is public-safe as None
                    response = None
            usage = None
            if isinstance(invocation.usage_json, Mapping):
                raw_usage = invocation.usage_json.get("usage")
                if isinstance(raw_usage, Mapping):
                    usage = {
                        key: raw_usage.get(key)
                        for key in ("input_tokens", "output_tokens", "total_tokens")
                    }
            return ProviderInvocationFact(
                run_id=reservation.run_id,
                request_id=request_id,
                model=None if response is None else response.model,
                finish_reason=None if response is None else response.finish_reason,
                tool_call_count=0 if response is None else len(response.tool_calls),
                error_code=invocation.error_code,
                usage=usage,
            )
        return None

    def read_closure_run_facts(self, run_id: str):  # type: ignore[no-untyped-def]
        """S5b Task 3 closure fallback inputs from the durable SDK ledger.

        The Run binding (``context_metadata.run_binding`` of the start
        snapshot — the same record the provider binding resolver rebuilds the
        adapter from) and the last assistant message of the Run context.
        """

        from simple_harness import RunId
        from simple_harness.runtime import SqliteContextPort

        from deskpet.execution.semantic_closure import ClosureRunFacts

        self.require_ready()
        uow = self._uow
        if uow is None:
            raise SdkRuntimeNotReady("SDK Runtime transaction owner is unavailable")
        expected = str(run_id).strip()
        if not expected:
            raise ValueError("run_id is required")
        start = uow.read_start_snapshot(expected)
        start_input = start.get("input") if isinstance(start, Mapping) else None
        metadata = start_input.get("context_metadata") if isinstance(start_input, Mapping) else None
        raw_binding = metadata.get("run_binding") if isinstance(metadata, Mapping) else None
        binding = dict(raw_binding) if isinstance(raw_binding, Mapping) else None
        snapshot = SqliteContextPort(uow.database).load(RunId(expected))
        last_answer: str | None = None
        for message in reversed(tuple(snapshot.messages)):
            role = str(getattr(getattr(message, "role", ""), "value", getattr(message, "role", "")))
            if role == "assistant":
                content = str(getattr(message, "content", "") or "").strip()
                if content:
                    last_answer = content
                    break
        return ClosureRunFacts(binding_record=binding, last_assistant_message=last_answer)

    def read_settled_primary_run(self, run_id: str, *, current_text: str):
        """Rebuild pre-S6 history from the real terminal and public Context."""
        terminal = self.read_run_terminal_evidence(run_id)
        if terminal is None:
            raise RuntimeError("primary_history_sdk_terminal_missing")
        return terminal, self.read_primary_run_messages(run_id, current_text=current_text)

    def read_primary_run_messages(self, run_id: str, *, current_text: str) -> tuple[dict, ...]:
        """Public transcript of this turn, excluding seeded history/system data.

        Read the real SDK Context through its public port, not a checkpoint JSON
        layout. The admitted current user message anchors the turn suffix. Missing
        or changed anchors fail closed rather than inventing history.
        """
        from simple_harness.runtime import SqliteContextPort

        self.require_ready()
        if self._uow is None:
            raise SdkRuntimeNotReady("SDK Runtime transaction owner is unavailable")
        messages = tuple(SqliteContextPort(self._uow.database).load(RunId(run_id)).messages)

        return project_primary_transcript(messages, current_text=current_text)

    def read_run_terminal_evidence(
        self, run_id: str
    ) -> SdkRunTerminalEvidence | None:
        """Read one ordinary Run's exact terminal event under one DB snapshot."""

        self.require_ready()
        uow = self._uow
        if uow is None:
            raise SdkRuntimeNotReady("SDK Runtime transaction owner is unavailable")
        expected = str(run_id).strip()
        if not expected:
            raise ValueError("run_id is required")
        with uow.database.transaction() as connection:
            run = connection.execute(
                "SELECT state FROM runs WHERE run_id=?", (expected,)
            ).fetchone()
            if run is None:
                return None
            state = str(run["state"])
            if state not in {"completed", "failed", "cancelled"}:
                return None
            events = connection.execute(
                "SELECT event_id,kind,payload_json,created_at FROM run_events "
                "WHERE run_id=? AND kind IN "
                "('run.completed','run.failed','run.cancelled')",
                (expected,),
            ).fetchall()
            if len(events) != 1 or str(events[0]["kind"]) != f"run.{state}":
                raise SdkRuntimeNotReady("SDK terminal event is ambiguous")
            event = events[0]
            raw_payload = str(event["payload_json"])
            error_code: str | None = None
            if state == "failed":
                try:
                    payload = json.loads(raw_payload)
                except ValueError:
                    payload = None
                if isinstance(payload, Mapping):
                    code = payload.get("code")
                    if isinstance(code, str) and code.strip():
                        error_code = code.strip()
            return SdkRunTerminalEvidence(
                expected,
                state,
                str(event["event_id"]),
                hashlib.sha256(raw_payload.encode("utf-8")).hexdigest(),
                float(event["created_at"]),
                error_code,
            )

    async def commit_preflight_blocked_root(
        self,
        *,
        execution_session_id: str,
        run_id: str,
        request_id: str,
        turn_id: str,
        task_scope_id: str,
        text: str,
        reason_code: str,
        evidence_refs: tuple[str, ...],
    ):  # type: ignore[no-untyped-def]
        """Create one durable failed Root without entering provider/tool execution."""

        self.require_ready()
        uow = self._uow
        if uow is None:
            raise SdkRuntimeNotReady("SDK Runtime transaction owner is unavailable")
        reason = str(reason_code).strip()
        refs = tuple(str(item).strip() for item in evidence_refs)
        if not reason or not refs or any(not item for item in refs):
            raise ValueError("preflight block requires reason and evidence refs")
        snapshot = {
            "schema_version": 1,
            "profile_key": ROOT_PROFILE_KEY,
            "driver_kind": "react",
            "turn_id": turn_id,
            "task_scope_id": task_scope_id,
            "tool_catalog_generation": 0,
            "input": {"text": text},
            "preflight_block": {
                "reason_code": reason,
                "evidence_refs": list(refs),
            },
        }
        terminal_payload = {
            "kind": "final",
            "explanation_code": reason,
            "route_availability": "unavailable",
            "provider_invocation_created": False,
            "evidence_refs": list(refs),
        }
        async with self._preflight_lock:
            self.require_ready()
            owner_row = uow.database.connection.execute(
                "SELECT user_id FROM execution_sessions WHERE session_id=?",
                (execution_session_id,),
            ).fetchone()
            user_id = (
                str(owner_row["user_id"])
                if owner_row is not None
                else "harness-system"
            )
            existing = uow.read_run(run_id)
            if existing is not None and existing.state is RunState.FAILED:
                # Re-run the SDK idempotency check so a reused identity with a
                # different request or snapshot still fails closed.
                uow.create_with_start_snapshot(
                    execution_session_id=execution_session_id,
                    run_id=run_id,
                    request_id=request_id,
                    profile_key=ROOT_PROFILE_KEY,
                    driver_kind="react",
                    snapshot=snapshot,
                    event_id=f"{run_id}:created",
                    now=float(self._clock()),
                    user_id=user_id,
                )
                return existing

            now = float(self._clock())
            uow.create_with_start_snapshot(
                execution_session_id=execution_session_id,
                run_id=run_id,
                request_id=request_id,
                profile_key=ROOT_PROFILE_KEY,
                driver_kind="react",
                snapshot=snapshot,
                event_id=f"{run_id}:created",
                now=now,
                user_id=user_id,
            )
            active, execution_lease = uow.claim_runtime_activation(
                run_id=run_id,
                owner_id=f"product-preflight:{run_id}",
                namespace="runtime.kernel",
                now=now,
                lease_ttl_seconds=30.0,
            )
            fence = None
            try:
                fence = await uow.acquire(RunId(run_id), execution_lease, now=now)
                result = uow.commit_root_terminal_with_deliveries(
                    run_id=run_id,
                    expected_version=active.version,
                    terminal_state=RunState.FAILED,
                    event_id=f"{run_id}:preflight-blocked",
                    terminal_payload=terminal_payload,
                    deliveries=(),
                    fence=fence,
                    execution_lease=execution_lease,
                    terminal_fence_receipt_ref=f"preflight-block:{run_id}",
                    now=now,
                )
                return result.run
            except BaseException:
                if fence is not None:
                    await uow.release(fence)
                raise
            finally:
                uow.release_runtime_lease(execution_lease, now=float(self._clock()))

    def read_authorization_decision(self, *, run_id: str, decision_id: str):
        """Exact lookup through the public ExecutionUnitOfWork port."""
        self.require_ready()
        if self._uow is None:
            raise SdkRuntimeNotReady("SDK Runtime transaction owner is unavailable")
        record = self._uow.read_decision(decision_id)
        return record if record is not None and record.run_id == run_id else None

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


def _corrupt_verification(run_id: str, reason: str) -> SkillInstallVerificationEvidence:
    return SkillInstallVerificationEvidence(
        "corrupt", run_id, reason_code=reason
    )


__all__ = (
    "OwnedResourceCloser",
    "ProductSdkRuntimeStack",
    "ProductionRuntimeBuild",
    "SdkRunTerminalEvidence",
    "SdkRuntimeBuildInputs",
    "SdkRuntimeNotReady",
    "SdkRuntimeReady",
    "SkillInstallVerificationEvidence",
    "WorkflowFactory",
    "WorkflowFactoryResourceScope",
    "WorkflowRuntimeBuild",
    "build_product_runtime",
)


def project_primary_transcript(messages, *, current_text):
    """Whitelist public SDK Context messages; no provider/private block promotion."""
    from deskpet.task_scope.protocol import redact_credential_shapes
    def text_content(message):
        if isinstance(message.content, str):
            return message.content
        return "".join(str(block.data.get("text", "")) for block in message.content
                       if block.type in {"text", "input_text", "output_text"})

    anchors = [i for i, message in enumerate(messages)
               if message.role.value == "user" and text_content(message) == current_text]
    if not anchors:
        raise RuntimeError("primary_history_current_turn_missing")
    public = []
    for message in messages[anchors[-1]:]:
        if message.role.value == "system":
            continue
        content = redact_credential_shapes(text_content(message))[0]
        if not isinstance(message.content, str):
            artifacts = []
            for block in message.content:
                if block.type != "artifact":
                    continue
                data = {key: redact_credential_shapes(block.data[key])[0]
                        for key in ("artifact_ref", "name", "mime_type", "uri")
                        if isinstance(block.data.get(key), str)}
                if data:
                    artifacts.append({"type": "artifact", "data": data})
            if artifacts:
                content = ([{"type": "text", "data": {"text": content}}] if content else []) + artifacts
        item = {"role": message.role.value, "content": content}
        if message.role.value == "tool":
            item["call_id"] = message.call_id.value
            if message.name:
                item["name"] = message.name
        public.append(item)
    return tuple(public)
