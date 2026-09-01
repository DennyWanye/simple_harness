# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Public-only adapter for the frozen S4 value-smoke runner.

This adapter is a verification seam, not an HTTP payload model.  The fixture's
principal is bound once as a trusted Host snapshot; request ``subject`` fields
are assertions used to exercise wrong-principal rejection and never enter a
facade DTO.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast

from simple_harness import (
    ROOT_PROFILE_KEY,
    RuntimePorts,
    RuntimeProfile,
    SqliteContextPort,
)
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.execution.budget import BudgetPolicy
from simple_harness.execution.context_authority import ToolCatalogSnapshot
from simple_harness.execution.delivery import DeliveryDispatcher
from simple_harness.execution.dispatch import (
    ProviderBinding,
    ProviderInvocationCoordinator,
)
from simple_harness.providers import ProviderResponse, ProviderTarget, ProviderToolSpec
from simple_harness.runtime import AgentLoopCollaborator, EffectBatchExecutor
from simple_harness.runtime.drivers import ReActDriver
from simple_harness.tools import (
    AuthorizationDecision,
    AuthorizationResult,
    EffectExecutor,
    ReconciliationObservation,
    ReconciliationState,
    ToolRegistry,
)

from deskpet.execution.foreground_queue import (
    ForegroundQueueError,
    ForegroundQueueStore,
)
from deskpet.execution.foreground_runtime import (
    ForegroundRuntimeExecutionAuthority,
    SqliteSdkTerminalObserver,
)
from deskpet.execution.foreground_runtime_ports import (
    ProductForegroundProviderPort,
    ProductForegroundToolPort,
    TaskScopeForegroundContextPort,
)
from deskpet.memory.human_memory_service import (
    AppendBindingRequest,
    AppendDeterministicEventsRequest,
    AppendPrimaryEventRequest,
    AuditRefsRequest,
    AuthenticatedHostSnapshot,
    ControlRunRequest,
    CreateTaskScopeRequest,
    DecideManualBindingRequest,
    ForegroundSchedulerWakePort,
    HumanMemoryHostService,
    HumanMemoryHostServiceError,
    HumanMemoryHostServiceFactory,
    ListEvidenceGroupsRequest,
    MutateTaskScopeRequest,
    OpenTaskScopeRequest,
    QueueTurnRequest,
    ReadEvidencePageRequest,
    ReadTaskScopeViewRequest,
    SaveCheckpointRequest,
    SearchTaskScopesRequest,
)
from deskpet.memory.recovery_fence import (
    RecoveryLifecyclePort,
    build_recovery_lifecycle_port,
)
from deskpet.memory.schema import dispatch_startup_epoch, inspect_startup_epoch
from deskpet.sdk_adapters.composition import (
    ProductSdkRuntimeStack,
    SdkRuntimeBuildInputs,
)
from deskpet.sdk_adapters.ingress import SdkRuntimeIngress
from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
from deskpet.sdk_adapters.runtime_paths import ProductRuntimePathsAdapter
from deskpet.sdk_adapters.sdk_candidate import (
    SDK_CANDIDATE_MANIFEST_SHA256,
    SDK_SOURCE_COMMIT,
    SDK_VERSION,
    SDK_WHEEL_FILENAME,
    SDK_WHEEL_SHA256,
    build_candidate_identity,
)
from deskpet.sdk_adapters.tool_authority import SdkRunToolAuthorityRegistry
from deskpet.sdk_adapters.tools import ProductToolInventoryEntry
from deskpet.task_scope.protocol import canonical_hash
from deskpet.task_scope.runtime_binding_authority import (
    WorkspaceBindingRuntimeAuthority,
)
from deskpet.task_scope.store import CanonicalTaskScopeStore
from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore

# Single source for the v44 audit-set alias → Host table mapping used by both
# the execution-audit projection and the recovery/export projection.
_V44_AUDIT_TABLE_ALIASES: tuple[tuple[str, str], ...] = (
    ("foreground_claimed_payloads", "foreground_run_preparation_bindings"),
    ("foreground_execution_preparations", "foreground_execution_preparations"),
    ("foreground_start_intents", "foreground_execution_start_intents"),
    ("foreground_start_observations", "foreground_execution_start_observations"),
    (
        "foreground_reconciliation_receipts",
        "foreground_execution_reconciliations",
    ),
    ("foreground_terminal_lineage", "foreground_terminal_receipts"),
)


class _CanonicalBulkSeed:
    def __init__(self, db_path: Path) -> None:
        self._store = CanonicalTaskScopeStore(db_path)

    async def append_deterministic_events(self, **values):  # type: ignore[no-untyped-def]
        values.pop("idempotency_key", None)
        receipt = await self._store.append_deterministic_events(**values)
        return {
            "scope_ref": receipt.task_scope_id,
            "event_count": receipt.count,
            "first_event_ref": receipt.first_event_id,
            "first_event_sequence": receipt.first_event_sequence,
            "last_event_ref": receipt.last_event_id,
            "last_event_sequence": receipt.last_event_sequence,
            "source_ref": receipt.source_id,
            "source_hash": receipt.source_hash,
        }


class _Policy:
    def __init__(self) -> None:
        self.mode = "manual"
        self.generation = 0

    async def get_policy_state(self):  # type: ignore[no-untyped-def]
        return SimpleNamespace(mode=self.mode, generation=self.generation)

    def select(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self.generation += 1


class _Noop:
    async def reconcile(self):  # type: ignore[no-untyped-def]
        return None

    async def run_once(self):  # type: ignore[no-untyped-def]
        return False

    def current_generation(self) -> int:
        return 1


class _Authorization:
    async def authorize(self, prepared):  # type: ignore[no-untyped-def]
        return AuthorizationResult(
            AuthorizationDecision.ALLOW,
            receipt_ref=f"s4-authorization:{prepared.effect_id.value}",
        )


class _Reconciliation:
    async def observe(self, prepared):  # type: ignore[no-untyped-def]
        return ReconciliationObservation(
            ReconciliationState.STILL_UNKNOWN,
            f"s4-reconciliation:{prepared.effect_id.value}",
        )


class _DeliverySink:
    async def deliver(self, payload, *, idempotency_key):  # type: ignore[no-untyped-def]
        del payload, idempotency_key


class _DeterministicProvider:
    target = ProviderTarget(
        "deterministic-text-terminal",
        "gpt-4o-mini",
        "gpt-4o-mini",
        "local",
        "s4-value",
    )

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def invoke(self, request, *, cancel):  # type: ignore[no-untyped-def]
        self.started.set()
        while not self.release.is_set():
            if cancel.is_cancelled:
                raise asyncio.CancelledError
            try:
                await asyncio.wait_for(self.release.wait(), timeout=0.05)
            except TimeoutError:
                pass
        return ProviderResponse(
            request.request_id,
            Message(MessageRole.ASSISTANT, "deterministic terminal answer"),
            model="gpt-4o-mini",
            finish_reason="stop",
        )


class _DeterministicProviderRegistry:
    def __init__(self) -> None:
        self._entry = SimpleNamespace(
            enabled=True,
            model="gpt-4o-mini",
            incarnation_id="s4-provider-incarnation-v1",
            config_revision=1,
        )

    def get_chain(self):  # type: ignore[no-untyped-def]
        return ({"id": "deterministic-text-terminal", "model": "gpt-4o-mini"},)

    def get_entry(self, provider_id: str):  # type: ignore[no-untyped-def]
        return self._entry if provider_id == "deterministic-text-terminal" else None

    def snapshot_digest(self) -> str:
        return canonical_hash(
            {
                "provider_id": "deterministic-text-terminal",
                "incarnation_id": self._entry.incarnation_id,
                "config_revision": self._entry.config_revision,
            }
        )


class _DeterministicProviderBindingResolver:
    """In-process Provider harness behind the production Provider port."""

    def __init__(self) -> None:
        self._bindings: dict[str, SdkRunBindingV1] = {}
        self._authorities: dict[str, ProviderBinding] = {}
        self._providers: dict[str, _DeterministicProvider] = {}

    def create_binding(self, **raw: Any) -> SdkRunBindingV1:
        run_id = str(raw["run_id"])
        provider = self._providers.get(run_id)
        if provider is None:
            provider = _DeterministicProvider()
            self._providers[run_id] = provider
        authority = ProviderBinding(provider, None, BudgetPolicy())
        binding = SdkRunBindingV1.build(
            **raw, budget_fingerprint=authority.budget_fingerprint
        )
        current = self._bindings.get(run_id)
        if current is not None and current.binding_fingerprint != binding.binding_fingerprint:
            raise RuntimeError("sdk_run_binding_conflict")
        self._bindings[run_id] = current or binding
        self._authorities.setdefault(run_id, authority)
        return self._bindings[run_id]

    def resolve(self, run_id):  # type: ignore[no-untyped-def]
        value = str(getattr(run_id, "value", run_id))
        authority = self._authorities.get(value)
        if authority is None:
            raise KeyError(value)
        return authority

    async def wait_started(self, run_id: str) -> None:
        provider = self._providers.get(run_id)
        if provider is None:
            raise RuntimeError("deterministic_provider_missing")
        await provider.started.wait()

    def finish(self, run_id: str) -> None:
        provider = self._providers.get(run_id)
        if provider is None:
            raise RuntimeError("deterministic_provider_missing")
        provider.release.set()

    def mark_terminal(self, run_id: str, state: str) -> None:
        if state not in {"completed", "failed", "cancelled", "stopped"}:
            raise ValueError("terminal state required")


class _Catalog:
    def __init__(self, catalog: Mapping[str, object]) -> None:
        self._generation = int(cast(int, catalog["generation"]))
        self._fingerprint = str(catalog["content_fingerprint"])
        self._specs = tuple(
            ProviderToolSpec(
                str(item["name"]),
                str(item["description"]),
                dict(cast(Mapping[str, Any], item["input_schema"])),
            )
            for item in cast(list[Mapping[str, object]], catalog["specs"])
        )

    def current_generation(self) -> int:
        return self._generation

    def resolve(self, generation: int, content_fingerprint: str):  # type: ignore[no-untyped-def]
        if generation != self._generation or content_fingerprint != self._fingerprint:
            return None
        return ToolCatalogSnapshot(
            self._generation, self._fingerprint, self._specs, 0.0
        )


class _RuntimeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def record(self, event: str, payload: Mapping[str, object]) -> None:
        self.events.append((event, dict(payload)))


class _InjectedFault(RuntimeError):
    code = "test_fault_injected"


class _FaultController:
    """One-shot crash point used around real durable production operations."""

    _QUEUE_POINTS: ClassVar[dict[str, str]] = {
        "prepare.after_commit": "prepare.after_draft",
        "claim.after_commit": "claim.after_commit",
        "start_intent.after_commit": "start_intent.after_commit",
        "terminal.before_commit": "terminal.before_commit",
        "terminal.after_commit": "terminal.after_commit",
    }

    def __init__(self) -> None:
        self.armed_boundary: str | None = None
        self.triggered_boundary: str | None = None
        self._triggered = asyncio.Event()

    def arm(self, boundary: str) -> None:
        if self.armed_boundary is not None:
            raise HumanMemoryHostServiceError("test_fault_already_armed")
        self.armed_boundary = boundary
        self.triggered_boundary = None
        self._triggered = asyncio.Event()

    def queue_hook(self, point: str) -> None:
        self.trigger(self._QUEUE_POINTS.get(point, point))

    def trigger(self, boundary: str) -> None:
        if self.armed_boundary != boundary:
            return
        self.armed_boundary = None
        self.triggered_boundary = boundary
        self._triggered.set()
        raise _InjectedFault(boundary)

    async def wait(self, boundary: str, *, timeout: float = 30.0) -> None:
        await asyncio.wait_for(self._triggered.wait(), timeout=timeout)
        if self.triggered_boundary != boundary:
            raise HumanMemoryHostServiceError("test_fault_boundary_drift")


class _FaultingForegroundQueueStore(ForegroundQueueStore):
    """Production queue with hooks only where the queue has no native hook."""

    def __init__(self, db_path: Path, controller: _FaultController) -> None:
        super().__init__(db_path, fault_hook=controller.queue_hook)
        self._controller = controller

    async def read_claimed_execution(self, **values):  # type: ignore[no-untyped-def]
        result = await super().read_claimed_execution(**values)
        self._controller.trigger("authority.after_claim")
        return result

    async def bind_sdk_run(self, **values):  # type: ignore[no-untyped-def]
        result = await super().bind_sdk_run(**values)
        self._controller.trigger("host_sdk_bind.after_commit")
        return result

    async def record_sdk_started(self, **values):  # type: ignore[no-untyped-def]
        result = await super().record_sdk_started(**values)
        self._controller.trigger("started_observation.after_commit")
        return result


class _FaultingSdkIngress:
    """Decorate the real SDK ingress at its durable acceptance boundary."""

    def __init__(self, delegate: SdkRuntimeIngress, controller: _FaultController) -> None:
        self._delegate = delegate
        self._controller = controller

    async def start(self, **values):  # type: ignore[no-untyped-def]
        result = await self._delegate.start(**values)
        self._controller.trigger("sdk_start.after_accept")
        return result

    def query(self, run_id: str):  # type: ignore[no-untyped-def]
        return self._delegate.query(run_id)

    async def cancel(self, run_id: str) -> None:
        await self._delegate.cancel(run_id)

    async def signal(self, **values):  # type: ignore[no-untyped-def]
        return await self._delegate.signal(**values)

    def __getattr__(self, name: str) -> object:
        return getattr(self._delegate, name)


class _DeterministicWakeGate:
    """Let the frozen runner persist its complete queue before execution starts."""

    def __init__(
        self, runtime: ForegroundRuntimeExecutionAuthority, *, release_after: int
    ) -> None:
        self._runtime = runtime
        self._release_after = release_after
        self._enqueue_count = 0

    async def after_enqueue(self, *, subject: str) -> None:
        self._enqueue_count += 1
        if self._enqueue_count >= self._release_after:
            await self._runtime.after_enqueue(subject=subject)

    async def after_control(self, *, subject: str) -> None:
        await self._runtime.after_control(subject=subject)


class _HeldWakeGate:
    """Never wake the scheduler: archive/durability oracles assert queued turns
    survive cold restart untouched, so execution must not start."""

    async def after_enqueue(self, *, subject: str) -> None:
        del subject

    async def after_control(self, *, subject: str) -> None:
        del subject


class S4ValuePublicAdapter:
    def __init__(self, *, fixture: Mapping[str, Any], artifact_dir: Path) -> None:
        self._fixture = dict(fixture)
        self._artifact_dir = artifact_dir.resolve()
        if ".local-test-evidence" not in self._artifact_dir.parts:
            raise ValueError("S4 adapter artifacts require .local-test-evidence")
        subject = str(self._fixture["subject"])
        self._auth = AuthenticatedHostSnapshot(
            subject=subject,
            principal_id=f"fixture-principal:{subject}",
            authority_ref=f"fixture-auth:{self._fixture['fixture_id']}",
        )
        self._db_path: Path | None = None
        self._service: HumanMemoryHostService | None = None
        self._recovery: RecoveryLifecyclePort | None = None
        self._primary_ref: str | None = None
        self._foreground: ForegroundQueueStore | None = None
        self._binding: WorkspaceBindingRuntimeAuthority | None = None
        self._policy = _Policy()
        self._runtime: ForegroundRuntimeExecutionAuthority | None = None
        self._runtime_stack: ProductSdkRuntimeStack | None = None
        self._ingress: SdkRuntimeIngress | None = None
        self._sdk_db_path: Path | None = None
        self._provider_registry = _DeterministicProviderRegistry()
        self._provider_bindings = _DeterministicProviderBindingResolver()
        self._tool_authorities = SdkRunToolAuthorityRegistry()
        self._runtime_audit = _RuntimeAudit()
        self._faults = _FaultController()
        self._scenario: str | None = None
        self._scheduler_held = False
        self._explicit_recovery = False
        self._planned_host_run_id: str | None = None
        self._planned_sdk_run_id: str | None = None
        self._owner_generation = 0
        self._session_db_reads = 0
        self._logical_roots: dict[str, Path] = {}
        self._catalog, self._inventory = self._build_catalog()
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._run_event_loop,
            name=f"s4-value-adapter-{uuid.uuid4().hex[:8]}",
            daemon=True,
        )
        self._loop_thread.start()

    @staticmethod
    def _build_catalog() -> tuple[dict[str, object], tuple[ProductToolInventoryEntry, ...]]:
        specs = [
            {
                "name": name,
                "description": f"S4 deterministic {name}",
                "input_schema": {"type": "object", "properties": {}},
            }
            for name in ("tool_search", "tool_describe", "tool_activate")
        ]
        schema_fingerprints = {
            str(item["name"]): canonical_hash(item["input_schema"])
            for item in specs
        }
        catalog: dict[str, object] = {
            "generation": 1,
            "content_fingerprint": canonical_hash(specs),
            "tool_names": [str(item["name"]) for item in specs],
            "tool_count": len(specs),
            "schema_token_count": sum(max(1, len(repr(item)) // 4) for item in specs),
            "schema_fingerprints": schema_fingerprints,
            "specs": specs,
        }
        inventory = tuple(
            ProductToolInventoryEntry(
                name=str(item["name"]),
                dispatch_kind="control",
                permission_category="read_file",
                source="s4-deterministic-runtime",
                version="v1",
                execution_identity=canonical_hash(item),
                projectless_admission="safe",
            )
            for item in specs
        )
        return catalog, inventory

    def _run_event_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def invoke(self, operation: str, request: dict[str, Any]) -> dict[str, Any]:
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._invoke(operation, dict(request)), self._loop
            )
            payload = future.result(timeout=180.0)
        except Exception as exc:  # noqa: BLE001 - stable black-box projection
            return {
                "ok": False,
                "status": 409,
                "code": getattr(exc, "code", str(exc) or type(exc).__name__),
            }
        if (
            isinstance(payload, Mapping)
            and payload.get("status") == "authorization_required"
            and payload.get("code")
        ):
            return {
                "ok": False,
                "status": 409,
                "code": str(payload["code"]),
                "payload": dict(payload),
            }
        return {"ok": True, "status": 200, "payload": payload}

    async def _start_sdk_stack(self) -> None:
        runtime_root = self._artifact_dir / "sdk-runtime"
        runtime_paths = ProductRuntimePathsAdapter(runtime_root)
        self._sdk_db_path = runtime_paths.execution_database
        catalog_port = _Catalog(self._catalog)

        def ports_factory(database, uow):  # type: ignore[no-untyped-def]
            authorization = _Authorization()
            reconciliation = _Reconciliation()
            provider = ProviderInvocationCoordinator(
                uow=uow,
                resolver=self._provider_bindings,
                clock=time.time,
            )
            tools = EffectExecutor(
                uow=uow,
                registry=ToolRegistry(),
                authorization=authorization,
                reconciliation=reconciliation,
                clock=time.time,
            )
            return RuntimePorts(
                provider=provider,
                tools=tools,
                authorization=authorization,
                context=SqliteContextPort(database, clock=time.time),
                delivery=DeliveryDispatcher(
                    uow, {"s4-value": _DeliverySink()}, clock=time.time
                ),
                tool_reconciliation=reconciliation,
                reconciliation=_Noop(),
                provider_reconciliation=_Noop(),
                react_checkpoint=uow,
                tool_catalog=catalog_port,
                owner_id="s4-sdk-runtime-owner",
                clock=time.time,
            )

        self._runtime_stack = ProductSdkRuntimeStack(
            paths=runtime_paths,
            candidate_identity=build_candidate_identity(),
            dependency_loader=lambda: SdkRuntimeBuildInputs(
                profiles={
                    ROOT_PROFILE_KEY: RuntimeProfile(ROOT_PROFILE_KEY, "react")
                },
                drivers={
                    "react": ReActDriver(
                        collaborator=AgentLoopCollaborator(),
                        effects=EffectBatchExecutor(),
                        clock=time.time,
                    )
                },
                ports_factory=ports_factory,
                workflow_catalog_digest="s4-value-runtime-v1",
            ),
        )
        await self._runtime_stack.start()
        self._ingress = SdkRuntimeIngress(self._runtime_stack)
        self._ingress.open()

    async def _bind_host(self, *, startup, restart: bool) -> None:  # type: ignore[no-untyped-def]
        path = self._require_db()
        self._foreground = _FaultingForegroundQueueStore(path, self._faults)
        configured = self._logical_roots["configured_root"]
        self._binding = WorkspaceBindingRuntimeAuthority(
            path,
            subject=self._auth.subject,
            foreground=self._foreground,
            policy=self._policy,
            configured_workspace_root=configured,
            home_directory=self._artifact_dir,
        )
        if self._runtime_stack is None or self._ingress is None:
            raise HumanMemoryHostServiceError("human_memory_sdk_runtime_unavailable")
        self._owner_generation += 1
        ingress = _FaultingSdkIngress(self._ingress, self._faults)
        self._runtime = ForegroundRuntimeExecutionAuthority(
            store=self._foreground,
            subject=self._auth.subject,
            owner_id=f"s4-foreground-owner-{self._owner_generation}",
            ingress=cast(SdkRuntimeIngress, ingress),
            context=TaskScopeForegroundContextPort(path, subject=self._auth.subject),
            provider=ProductForegroundProviderPort(
                self._provider_registry, self._provider_bindings
            ),
            tools=ProductForegroundToolPort(
                path,
                catalog=self._catalog,
                inventory=self._inventory,
                registry=self._tool_authorities,
            ),
            terminal_observer=SqliteSdkTerminalObserver(
                str(path), cast(SdkRuntimeIngress, ingress), self._runtime_stack
            ),
            audit_sink=self._runtime_audit,
            lease_seconds=300.0,
        )
        release_after = 1 if self._scenario is not None else 2
        if self._scheduler_held:
            scheduler_wake: ForegroundSchedulerWakePort = _HeldWakeGate()
        elif restart:
            scheduler_wake = self._runtime
        else:
            scheduler_wake = _DeterministicWakeGate(
                self._runtime, release_after=release_after
            )
        self._recovery = build_recovery_lifecycle_port(
            db_path=path, artifact_dir=self._artifact_dir
        )
        self._service = HumanMemoryHostServiceFactory(path, startup).bind(
            self._auth,
            deterministic_event_seed=_CanonicalBulkSeed(path),
            binding_append=self._binding,
            recovery=self._recovery,
            scheduler_wake=scheduler_wake,
        )
        if (
            restart
            and not self._scheduler_held
            and (
                await self._foreground.current_snapshot(self._auth.subject)
                is not None
                or await self._foreground.read_next_preparation_candidate(
                    self._auth.subject
                )
                is not None
            )
        ):
            await self._runtime.after_enqueue(subject=self._auth.subject)

    async def _invoke(
        self, operation: str, request: dict[str, Any]
    ) -> Mapping[str, object]:
        if operation == "runtime.identity":
            return self._runtime_identity()
        if operation == "host.reset_fresh":
            if request.get("data_format") != "human-memory-v1":
                raise HumanMemoryHostServiceError("human_memory_data_format_rejected")
            if self._runtime is not None:
                await self._runtime.close(timeout=0.05)
            if self._runtime_stack is not None:
                await self._runtime_stack.close()
            self._scenario = (
                None if request.get("scenario") is None else str(request["scenario"])
            )
            self._scheduler_held = str(request.get("scheduler") or "") == "held"
            self._faults = _FaultController()
            self._explicit_recovery = False
            self._planned_host_run_id = None
            self._planned_sdk_run_id = None
            self._db_path = self._artifact_dir / f"s4-value-{uuid.uuid4().hex}.db"
            workspace_root = self._artifact_dir / "workspace"
            self._logical_roots = {
                "manual_root": workspace_root / "runtime-alpha",
                "second_manual_root": workspace_root / "runtime-alpha-dependency",
                "auto_root": workspace_root / "configured" / "runtime-alpha-auto",
                "outside_root": workspace_root / "outside",
                "configured_root": workspace_root / "configured",
            }
            for root in self._logical_roots.values():
                root.mkdir(parents=True, exist_ok=True)
            startup = await dispatch_startup_epoch(
                self._db_path, approved_fresh_lane=True
            )
            await self._start_sdk_stack()
            await self._bind_host(startup=startup, restart=False)
            return {
                "format_epoch": startup.composition_mode.value,
                "startup_epoch": startup.epoch.value,
            }
        if operation == "host.cold_restart":
            path = self._require_db()
            if self._runtime is not None:
                await self._runtime.close(timeout=0.01)
            startup = inspect_startup_epoch(path, approved_fresh_lane=False)
            await self._bind_host(startup=startup, restart=True)
            if self._scenario is None and not self._scheduler_held:
                await self._wait_for(
                    lambda: self._start_observation_count("QUERY_FOUND") > 0,
                    "foreground_runtime_restart_reconciliation_timeout",
                )
            return {
                "format_epoch": startup.composition_mode.value,
                "startup_epoch": startup.epoch.value,
                "reconciled": True,
            }

        service = self._require_service()
        self._assert_fixture_principal(request)
        if operation == "host.composition_snapshot":
            ports: list[str] = []
            if self._binding is not None:
                ports.extend(
                    (
                        "human_memory_binding_append_authority",
                        "human_memory_recovery_lifecycle",
                    )
                )
            if self._runtime is not None:
                ports.extend(
                    (
                        "human_memory_foreground_scheduler_wake",
                        "human_memory_foreground_runtime_execution_authority",
                    )
                )
            return {
                "epoch": "HUMAN",
                "registered_ports": ports,
                "fixture_ports": [],
                "legacy_future_registered_ports": [],
            }
        if operation == "execution.await_current":
            expected = str(request.get("state") or "RUNNING")
            await self._wait_for_current_state(expected)
            snapshot = await self._require_foreground().current_snapshot(
                self._auth.subject
            )
            if snapshot is None or snapshot.sdk_run_id is None:
                raise HumanMemoryHostServiceError(
                    "human_memory_foreground_run_not_found"
                )
            await self._provider_bindings.wait_started(snapshot.sdk_run_id)
            await self._wait_for(
                lambda: self._sdk_checkpoint_exists(snapshot.sdk_run_id),
                "foreground_runtime_checkpoint_timeout",
            )
            return self._execution_projection(snapshot.host_run_id)
        if operation == "runtime.finish_current":
            if str(request.get("terminal") or "").upper() != "COMPLETED":
                raise HumanMemoryHostServiceError(
                    "human_memory_test_terminal_unsupported"
                )
            snapshot = await self._require_foreground().current_snapshot(
                self._auth.subject
            )
            sdk_run_id = str(request.get("sdk_run_id") or "")
            if snapshot is None or snapshot.sdk_run_id != sdk_run_id:
                raise HumanMemoryHostServiceError("foreground_run_stale_generation")
            host_run_id = snapshot.host_run_id
            self._provider_bindings.finish(sdk_run_id)
            try:
                await self._wait_for(
                    lambda: self._terminal_recorded(host_run_id),
                    "foreground_runtime_terminal_timeout",
                )
            except HumanMemoryHostServiceError:
                if self._faults.triggered_boundary != "terminal.before_commit":
                    raise
                path = self._require_db()
                if self._runtime is not None:
                    await self._runtime.close(timeout=0.01)
                await self._bind_host(
                    startup=inspect_startup_epoch(
                        path, approved_fresh_lane=False
                    ),
                    restart=True,
                )
                await self._wait_for(
                    lambda: self._terminal_recorded(host_run_id),
                    "foreground_runtime_terminal_recovery_timeout",
                )
            return {
                "host_run_id": host_run_id,
                "sdk_run_id": sdk_run_id,
                "terminal": "COMPLETED",
            }
        if operation == "execution.audit":
            return self._execution_audit_projection(
                str(request.get("host_run_id") or "")
            )
        if operation == "fault.arm":
            boundary = str(request.get("boundary") or "")
            allowed = tuple(str(item) for item in self._fixture["fault_boundaries"])
            if boundary not in allowed:
                raise HumanMemoryHostServiceError("test_fault_boundary_unknown")
            delivery_key = (
                f"{self._fixture['queue']['turns'][0]['delivery_key']}:{boundary}"
            )
            turn_id = self._stable_uuid(
                f"foreground-turn:{self._auth.subject}:{delivery_key}"
            )
            host_run_id = self._stable_uuid(f"foreground-run:{turn_id}")
            execution_session_id = self._execution_session_id(host_run_id)
            sdk_run_id = SdkRuntimeIngress._compute_run_id(
                execution_session_id,
                f"foreground-request-{turn_id}",
                turn_id,
            ).value
            self._planned_host_run_id = host_run_id
            self._planned_sdk_run_id = sdk_run_id
            self._faults.arm(boundary)
            return {
                "fault_ref": f"sha256:{canonical_hash({'boundary': boundary, 'host_run_id': host_run_id, 'sdk_run_id': sdk_run_id})}",
                "planned_host_run_id": host_run_id,
                "planned_sdk_run_id": sdk_run_id,
            }
        if operation == "fault.reclaim_current_lease":
            snapshot = await self._require_foreground().current_snapshot(
                self._auth.subject
            )
            if snapshot is None or snapshot.host_run_id != str(request["host_run_id"]):
                raise HumanMemoryHostServiceError(
                    "human_memory_foreground_run_not_found"
                )
            if self._runtime is not None:
                await self._runtime.close(timeout=0.01)
            receipt = await self._require_foreground().reclaim_expired(
                host_run_id=snapshot.host_run_id,
                new_owner_id=f"s4-fault-reclaimer-{self._owner_generation + 1}",
                expected_generation=snapshot.generation,
                lease_seconds=300.0,
                idempotency_key=(
                    f"fixture-reclaim:{snapshot.host_run_id}:g{snapshot.generation + 1}"
                ),
            )
            return {
                "host_run_id": receipt.host_run_id,
                "owner_id": receipt.owner_id,
                "generation": receipt.generation,
                "receipt_ref": receipt.lease_receipt_id,
                "receipt_hash": receipt.lease_hash,
            }
        if operation == "runtime.signal":
            try:
                control_receipt = await self._require_foreground().request_control(
                    host_run_id=str(request["host_run_id"]),
                    subject=self._auth.subject,
                    generation=int(request["generation"]),
                    control_kind=str(request["signal"]),
                    reason="fixture-generation-fence",
                    idempotency_key=(
                        f"fixture-stale-signal:{request['host_run_id']}:g{request['generation']}"
                    ),
                )
            except ForegroundQueueError as exc:
                if exc.code == "foreground_generation_stale":
                    raise HumanMemoryHostServiceError(
                        "foreground_run_stale_generation"
                    ) from exc
                raise
            return {
                "receipt_ref": control_receipt.control_id,
                "generation": control_receipt.generation,
                "outcome": control_receipt.outcome,
            }
        if operation == "effect.project":
            snapshot = await self._require_foreground().current_snapshot(
                self._auth.subject
            )
            if snapshot is None or snapshot.host_run_id != str(request.get("run_ref")):
                raise HumanMemoryHostServiceError(
                    "workspace_binding_current_run_authority_required"
                )
            receipt = await WorkspaceBindingAuthorityStore(
                self._require_db()
            ).current_receipt(str(snapshot.task_scope_id))
            if len(receipt.root_identity_hashes) != 1:
                raise HumanMemoryHostServiceError(
                    "workspace_binding_effect_root_selector_required"
                )
            return {"effect": str(request.get("effect") or ""), "authorized": True}
        if operation == "primary.open":
            result = await service.open_primary()
            self._primary_ref = str(result["primary_ref"])
            return result
        if operation == "primary.append":
            event = request.get("event")
            if not isinstance(event, Mapping):
                raise HumanMemoryHostServiceError("primary_event_payload_rejected")
            return await service.append_primary_event(
                AppendPrimaryEventRequest(
                    event=dict(event),
                    idempotency_key="fixture-primary-append",
                )
            )
        if operation == "task_scope.create":
            scope = request.get("scope")
            if not isinstance(scope, Mapping):
                raise HumanMemoryHostServiceError("task_scope_payload_rejected")
            return await service.create_task_scope(
                CreateTaskScopeRequest(
                    fixture_key=str(scope["fixture_key"]),
                    title=str(scope["title"]),
                    goal=str(scope["goal"]),
                    idempotency_key=f"fixture-create:{scope['fixture_key']}",
                )
            )
        if operation == "task_scope.append_deterministic_events":
            return await service.append_deterministic_events(
                AppendDeterministicEventsRequest(
                    scope_ref=str(request["scope_ref"]),
                    count=request["count"],
                    canary=str(request["canary"]),
                    idempotency_key=f"fixture-events:{request['scope_ref']}",
                )
            )
        if operation == "task_scope.save_checkpoint":
            checkpoint = request.get("checkpoint")
            if not isinstance(checkpoint, Mapping):
                raise HumanMemoryHostServiceError("checkpoint_payload_rejected")
            return await service.save_checkpoint(
                SaveCheckpointRequest(
                    scope_ref=str(request["scope_ref"]),
                    checkpoint=dict(checkpoint),
                    idempotency_key=f"fixture-checkpoint:{request['scope_ref']}",
                )
            )
        if operation == "queue.enqueue":
            result = await service.enqueue_turn(
                QueueTurnRequest(
                    scope_ref=str(request["scope_ref"]),
                    delivery_key=str(request["delivery_key"]),
                    text=str(request.get("text") or "fixture queued turn"),
                )
            )
            queued_boundary = self._faults.armed_boundary
            if queued_boundary is not None and not queued_boundary.startswith(
                "terminal."
            ):
                await self._faults.wait(queued_boundary)
                raise _InjectedFault(queued_boundary)
            return result
        if operation == "authority.snapshot":
            return await service.authority_snapshot()
        if operation == "derived.drop_rebuildable":
            return await service.drop_rebuildable(str(request["scope_ref"]))
        if operation == "derived.rebuild":
            return await service.rebuild_derived(str(request["scope_ref"]))
        if operation == "task_scope.search":
            if request.get("public_fault") == "fts-unavailable":
                raise HumanMemoryHostServiceError("human_memory_search_unavailable")
            return await service.search_task_scopes(
                SearchTaskScopesRequest(
                    query=str(request["query"]),
                    max_candidates=int(request.get("max_candidates", 8)),
                    cursor=None
                    if request.get("cursor") is None
                    else str(request["cursor"]),
                )
            )
        if operation == "task_scope.open_exact":
            probe = request.get("live_probe")
            return await service.open_task_scope(
                OpenTaskScopeRequest(
                    scope_ref=str(request["scope_ref"]),
                    live_probe=None if probe is None else dict(probe),
                    expected_source_hash=None
                    if request.get("expected_source_hash") is None
                    else str(request["expected_source_hash"]),
                )
            )
        if operation == "task_scope.mutate":
            mutation = request.get("mutation")
            if not isinstance(mutation, Mapping):
                raise HumanMemoryHostServiceError("task_scope_mutation_payload_rejected")
            return await service.mutate_task_scope(
                MutateTaskScopeRequest(
                    scope_ref=str(request["scope_ref"]),
                    kind=str(mutation["kind"]),
                    value=str(mutation["value"]),
                    idempotency_key="fixture-scope-mutation",
                )
            )
        if operation == "binding.append":
            return await service.append_binding(
                AppendBindingRequest(
                    scope_ref=str(request["scope_ref"]),
                    root=str(self._physical_root(str(request["root"]))),
                    idempotency_key="fixture-binding-append",
                )
            )
        if operation == "binding.seed_single_root":
            self._policy.select("manual")
            scope_ref = str(request["scope_ref"])
            proposal = await service.propose_manual_binding(
                AppendBindingRequest(
                    scope_ref=scope_ref,
                    root=str(self._physical_root(str(request["root"]))),
                    idempotency_key=f"fixture-binding-seed:{scope_ref}",
                )
            )
            return await service.decide_manual_binding(
                DecideManualBindingRequest(
                    challenge_ref=str(proposal["challenge_ref"]),
                    decision="allow",
                    idempotency_key=f"fixture-binding-seed-decision:{scope_ref}",
                )
            )
        if operation == "binding.propose_manual":
            self._policy.select("manual")
            return await service.propose_manual_binding(
                AppendBindingRequest(
                    scope_ref=str(request["scope_ref"]),
                    root=str(self._physical_root(str(request["root"]))),
                    idempotency_key=f"fixture-binding-propose:{request['scope_ref']}",
                )
            )
        if operation == "binding.decide_manual":
            if str(request.get("actor") or "") != self._auth.subject:
                raise HumanMemoryHostServiceError(
                    "workspace_binding_decision_actor_mismatch"
                )
            return await service.decide_manual_binding(
                DecideManualBindingRequest(
                    challenge_ref=str(request["challenge_ref"]),
                    decision=str(request["decision"]),
                    idempotency_key=f"fixture-binding-decision:{request['challenge_ref']}",
                )
            )
        if operation == "binding.append_auto_current":
            snapshot = await self._require_foreground().current_snapshot(
                self._auth.subject
            )
            if snapshot is None or snapshot.host_run_id != str(request.get("run_ref")):
                raise HumanMemoryHostServiceError(
                    "workspace_binding_current_run_authority_required"
                )
            if request.get("generation") is not None and snapshot.generation != int(
                request["generation"]
            ):
                raise HumanMemoryHostServiceError(
                    "foreground_run_stale_generation"
                )
            self._policy.select("auto")
            result = dict(
                await service.append_binding(
                    AppendBindingRequest(
                        scope_ref=str(snapshot.task_scope_id),
                        root=str(self._physical_root(str(request["root"]))),
                        idempotency_key=f"fixture-auto-binding:{snapshot.host_run_id}:{request['root']}",
                    )
                )
            )
            result.update(
                {
                    "run_ref": snapshot.host_run_id,
                    "generation": snapshot.generation,
                    "context_snapshot_ref": snapshot.context_snapshot_id,
                    "configuration_revision": self._policy.generation + 1,
                }
            )
            return result
        if operation == "queue.control":
            return await service.control_current_run(
                ControlRunRequest(
                    control=str(request["control"]),
                    reason="fixture-public-control",
                    idempotency_key=f"fixture-control:{request['control']}",
                )
            )
        if operation == "audit.refs":
            return await service.audit_refs(
                AuditRefsRequest(scope_ref=str(request["scope_ref"]))
            )
        if operation == "view.read":
            view = dict(
                await service.read_view(
                    ReadTaskScopeViewRequest(
                        scope_ref=str(request["scope_ref"]),
                        kind=str(request["kind"]),
                    )
                )
            )
            if str(request["kind"]) != "EVIDENCE":
                return view
            pages: list[dict[str, object]] = []
            groups_cursor: str | None = None
            while True:
                group_page = await service.list_evidence_groups(
                    ListEvidenceGroupsRequest(
                        scope_ref=str(view["scope_ref"]),
                        source_ref=str(view["source_ref"]),
                        source_hash=str(view["source_hash"]),
                        cursor=groups_cursor,
                    )
                )
                groups = cast(list[Mapping[str, object]], group_page["groups"])
                for group in groups:
                    page_cursor: str | None = None
                    while True:
                        result = await service.read_evidence_page(
                            ReadEvidencePageRequest(
                                scope_ref=str(view["scope_ref"]),
                                source_ref=str(view["source_ref"]),
                                source_hash=str(view["source_hash"]),
                                group_ref=str(group["group_ref"]),
                                group_hash=str(group["group_hash"]),
                                cursor=page_cursor,
                            )
                        )
                        page = dict(cast(Mapping[str, object], result["page"]))
                        decoded = json.loads(str(page["content"]))
                        if "events" in decoded:
                            page["events"] = decoded["events"]
                        if "event_chunk" in decoded:
                            page["event_chunk"] = decoded["event_chunk"]
                        pages.append(page)
                        page_cursor = cast(str | None, result["next_cursor"])
                        if page_cursor is None:
                            break
                groups_cursor = cast(str | None, group_page["next_cursor"])
                if groups_cursor is None:
                    break
            view["pages"] = pages
            return view
        if operation == "queue.snapshot":
            return await service.queue_snapshot()
        if operation == "recovery.manifest":
            if self._explicit_recovery:
                result = await self._require_recovery().sealed_manifest(
                    subject=self._auth.subject
                )
            else:
                result = await service.recovery_manifest()
            return self._recovery_projection(result)
        if operation == "recovery.begin_close":
            self._explicit_recovery = True
            return await self._require_recovery().begin_close(
                subject=self._auth.subject
            )
        if operation == "recovery.drain_checkpoint_seal":
            result = await self._require_recovery().drain_checkpoint_seal(
                subject=self._auth.subject
            )
            return self._recovery_projection(result)
        if operation == "recovery.emergency_export":
            result = await service.emergency_export()
            return self._recovery_projection(result)
        if operation.startswith("legacy_session."):
            target = str(request.get("target") or "")
            if target and target == self._primary_ref:
                raise HumanMemoryHostServiceError(
                    "human_memory_primary_authority_immutable"
                )
            raise HumanMemoryHostServiceError("human_memory_legacy_session_unavailable")
        raise HumanMemoryHostServiceError("human_memory_operation_unavailable")

    def _physical_root(self, logical: str) -> Path:
        workspace = cast(Mapping[str, object], self._fixture.get("workspace", {}))
        for key, value in workspace.items():
            if logical == str(value) and key in self._logical_roots:
                return self._logical_roots[key]
        raise HumanMemoryHostServiceError("workspace_root_unavailable")

    def _require_foreground(self) -> ForegroundQueueStore:
        if self._foreground is None:
            raise HumanMemoryHostServiceError("human_memory_adapter_not_initialized")
        return self._foreground

    async def _wait_for(self, predicate, code: str, *, timeout: float = 120.0) -> None:  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except (OSError, sqlite3.OperationalError):
                pass
            if self._runtime is not None and self._runtime.last_error is not None:
                error = self._runtime.last_error
                raise HumanMemoryHostServiceError(
                    str(getattr(error, "code", str(error) or type(error).__name__))
                )
            await asyncio.sleep(0.01)
        raise HumanMemoryHostServiceError(code)

    async def _wait_for_current_state(self, expected: str) -> None:
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            try:
                snapshot = await self._require_foreground().current_snapshot(
                    self._auth.subject
                )
            except sqlite3.OperationalError:
                snapshot = None
            if snapshot is not None and snapshot.state.value == expected:
                return
            if self._runtime is not None and self._runtime.last_error is not None:
                error = self._runtime.last_error
                raise HumanMemoryHostServiceError(
                    str(getattr(error, "code", str(error) or type(error).__name__))
                )
            await asyncio.sleep(0.01)
        raise HumanMemoryHostServiceError("foreground_runtime_state_timeout")

    def _host_row_count(self, table: str, where: str = "", values: tuple = ()) -> int:
        path = self._require_db()
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
            row = db.execute(f"SELECT COUNT(*) FROM {table} {where}", values).fetchone()
        return int(row[0]) if row is not None else 0

    def _host_table_exists(self, table: str) -> bool:
        path = self._require_db()
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
        return row is not None

    def _start_observation_count(self, outcome: str) -> int:
        return self._host_row_count(
            "foreground_execution_start_observations",
            "WHERE outcome=?",
            (outcome,),
        )

    def _terminal_recorded(self, host_run_id: str) -> bool:
        return bool(
            self._host_row_count(
                "foreground_terminal_receipts",
                "WHERE host_run_id=?",
                (host_run_id,),
            )
        )

    def _sdk_checkpoint_exists(self, sdk_run_id: str) -> bool:
        path = self._sdk_db_path
        if path is None or not path.exists():
            return False
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT checkpoint_json FROM workflow_checkpoints "
                "WHERE run_id=? AND namespace='react.termination.v1' "
                "ORDER BY version DESC LIMIT 1",
                (sdk_run_id,),
            ).fetchone()
        if row is None:
            return False
        value = json.loads(str(row[0]))
        return bool(
            isinstance(value, dict)
            and value.get("route_receipt_hash")
            and value.get("route_receipt")
        )

    def _runtime_identity(self) -> dict[str, object]:
        import simple_harness

        return {
            "filename": SDK_WHEEL_FILENAME,
            "sha256": SDK_WHEEL_SHA256,
            "distribution": importlib.metadata.distribution(
                "simple-harness-sdk"
            ).metadata["Name"],
            "version": SDK_VERSION,
            "source_commit": SDK_SOURCE_COMMIT,
            "module_origin": str(Path(simple_harness.__file__).resolve()),
            "public_contract_manifest_hash": SDK_CANDIDATE_MANIFEST_SHA256,
        }

    def _execution_projection(self, host_run_id: str) -> Mapping[str, object]:
        path = self._require_db()
        sdk_path = self._sdk_db_path
        if sdk_path is None:
            raise HumanMemoryHostServiceError("human_memory_sdk_runtime_unavailable")
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            run = db.execute(
                "SELECT r.*,h.current_state,h.sdk_run_id,h.owner_id,h.generation "
                "FROM foreground_runs r JOIN foreground_run_heads h "
                "ON h.host_run_id=r.host_run_id WHERE r.host_run_id=?",
                (host_run_id,),
            ).fetchone()
            draft = db.execute(
                "SELECT d.*,b.binding_hash FROM foreground_run_preparation_bindings b "
                "JOIN foreground_preparation_drafts d ON d.draft_id=b.draft_id "
                "WHERE b.host_run_id=?",
                (host_run_id,),
            ).fetchone()
            intent = db.execute(
                "SELECT * FROM foreground_execution_start_intents WHERE host_run_id=?",
                (host_run_id,),
            ).fetchone()
            start_count = db.execute(
                "SELECT COUNT(*) FROM foreground_execution_start_intents "
                "WHERE host_run_id=?",
                (host_run_id,),
            ).fetchone()[0]
        if run is None or draft is None or intent is None or run["sdk_run_id"] is None:
            raise HumanMemoryHostServiceError("foreground_runtime_audit_incomplete")
        sdk_run_id = str(run["sdk_run_id"])
        with sqlite3.connect(f"file:{sdk_path.resolve()}?mode=ro", uri=True) as db:
            start_row = db.execute(
                "SELECT snapshot_json FROM run_start_snapshots WHERE run_id=?",
                (sdk_run_id,),
            ).fetchone()
            checkpoint_row = db.execute(
                "SELECT checkpoint_json FROM workflow_checkpoints "
                "WHERE run_id=? AND namespace='react.termination.v1' "
                "ORDER BY version DESC LIMIT 1",
                (sdk_run_id,),
            ).fetchone()
        if start_row is None or checkpoint_row is None:
            raise HumanMemoryHostServiceError("foreground_runtime_sdk_audit_incomplete")
        start = json.loads(str(start_row[0]))
        checkpoint = json.loads(str(checkpoint_row[0]))
        route = start.get("initial_route_receipt")
        route_hash = str(start.get("initial_route_receipt_hash") or "")
        if (
            not isinstance(route, dict)
            or canonical_hash(route) != route_hash
            or checkpoint.get("route_receipt_hash") != route_hash
            or checkpoint.get("route_receipt") != route
        ):
            raise HumanMemoryHostServiceError("foreground_runtime_route_audit_mismatch")
        draft_payload = json.loads(str(draft["draft_json"]))
        if not isinstance(draft_payload, dict):
            raise HumanMemoryHostServiceError("foreground_runtime_draft_audit_invalid")
        return {
            "host_run_id": host_run_id,
            "sdk_run_id": sdk_run_id,
            "owner_id": str(run["owner_id"]),
            "generation": int(run["generation"]),
            "delivery_key": self._delivery_key(str(run["turn_id"])),
            "task_scope_id": str(run["task_scope_id"]),
            "state": str(run["current_state"]),
            "draft": {
                **draft_payload,
                "draft_hash": str(draft["draft_hash"]),
                "executable": False,
            },
            "claim": {
                "draft_id": str(draft["draft_id"]),
                "draft_hash": str(draft["draft_hash"]),
                "binding_hash": str(draft["binding_hash"]),
            },
            "authority": {
                "host_run_id": host_run_id,
                "sdk_run_id": sdk_run_id,
                "owner_id": str(run["owner_id"]),
                "generation": int(run["generation"]),
                "claimed_execution_hash": str(draft["binding_hash"]),
                "start_intent_hash": str(intent["intent_hash"]),
            },
            "route": {
                **route,
                "outcome": "ROUTED_TASK",
                "binding_set_receipt_ref": route["binding_set_receipt_id"],
                "route_hash": route_hash,
            },
            "start_snapshot": {
                "schema_version": int(start["schema_version"]),
                "route_hash": route_hash,
            },
            "react_checkpoint": {
                "schema_version": int(checkpoint["schema_version"]),
                "route_hash": str(checkpoint["route_receipt_hash"]),
            },
            "wheel_identity": self._runtime_identity(),
            "sdk_start_count": int(start_count),
            "session_db_read_count": self._session_db_reads,
        }

    def _delivery_key(self, turn_id: str) -> str:
        path = self._require_db()
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT idempotency_key FROM foreground_turns WHERE turn_id=?",
                (turn_id,),
            ).fetchone()
        if row is None:
            raise HumanMemoryHostServiceError("foreground_turn_missing")
        return str(row[0])

    def _execution_audit_projection(
        self, host_run_id: str
    ) -> Mapping[str, object]:
        facts = (
            ("preparation_draft", "foreground_preparation_drafts"),
            ("atomic_claim", "foreground_run_preparation_bindings"),
            ("generation_authority", "foreground_lease_receipts"),
            ("start_intent", "foreground_execution_start_intents"),
            ("sdk_start_observation", "foreground_execution_start_observations"),
            ("host_sdk_binding", "foreground_run_sdk_bindings"),
            (
                "running_observation",
                "foreground_run_transitions WHERE to_state='RUNNING'",
            ),
            ("terminal_observation", "foreground_terminal_receipts"),
            (
                "settlement",
                "foreground_turn_transitions WHERE to_state='SETTLED'",
            ),
        )
        sequence = []
        for name, source in facts:
            table, _, where = source.partition(" WHERE ")
            if self._host_row_count(table, f"WHERE {where}" if where else ""):
                sequence.append(name)
        sets = [
            alias
            for alias, table in _V44_AUDIT_TABLE_ALIASES
            if self._host_table_exists(table)
        ]
        path = self._require_db()
        with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
            row = db.execute(
                "SELECT sdk_run_id FROM foreground_run_heads WHERE host_run_id=?",
                (host_run_id,),
            ).fetchone()
            intent_count = db.execute(
                "SELECT COUNT(*) FROM foreground_execution_start_intents "
                "WHERE host_run_id=?",
                (host_run_id,),
            ).fetchone()[0]
            binding_count = db.execute(
                "SELECT COUNT(*) FROM foreground_run_sdk_bindings "
                "WHERE host_run_id=?",
                (host_run_id,),
            ).fetchone()[0]
        sdk_run_id = None if row is None or row[0] is None else str(row[0])
        sdk_snapshot_count = 0
        if sdk_run_id is not None and self._sdk_db_path is not None:
            with sqlite3.connect(
                f"file:{self._sdk_db_path.resolve()}?mode=ro", uri=True
            ) as db:
                sdk_snapshot_count = int(
                    db.execute(
                        "SELECT COUNT(*) FROM run_start_snapshots WHERE run_id=?",
                        (sdk_run_id,),
                    ).fetchone()[0]
                )
        recovered_same_identity = (
            host_run_id == self._planned_host_run_id
            and sdk_run_id == self._planned_sdk_run_id
        )
        duplicate_start_observed = (
            int(intent_count) != 1
            or int(binding_count) != 1
            or sdk_snapshot_count != 1
        )
        return {
            "schema_version": 1,
            "lifecycle_sequence": sequence,
            "v44_audit_sets": sets,
            "session_db_read_count": self._session_db_reads,
            "runtime_events": len(self._runtime_audit.events),
            "recovered_same_identity": recovered_same_identity,
            "duplicate_start_observed": duplicate_start_observed,
        }

    def _recovery_projection(
        self, result: Mapping[str, object]
    ) -> Mapping[str, object]:
        projected = dict(result)
        if self._db_path is None:
            return projected
        manifest_ref = str(projected.get("manifest_ref") or "")
        sets = [
            alias
            for alias, table in _V44_AUDIT_TABLE_ALIASES
            if self._host_table_exists(table)
        ]
        roots: dict[str, str] = {}
        if manifest_ref:
            path = self._require_db()
            with sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True) as db:
                rows = {
                    str(row[0]): str(row[1])
                    for row in db.execute(
                        "SELECT table_name,row_root FROM "
                        "human_memory_recovery_manifest_tables WHERE manifest_id=?",
                        (manifest_ref,),
                    ).fetchall()
                }
            roots = {
                alias: rows[table]
                for alias, table in _V44_AUDIT_TABLE_ALIASES
                if table in rows
            }
        projected.update(
            {
                "v44_audit_sets": sets,
                "protected_existing_row_roots": roots,
            }
        )
        return projected

    @staticmethod
    def _stable_uuid(label: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"simple-harness:{label}"))

    @staticmethod
    def _execution_session_id(host_run_id: str) -> str:
        return f"foreground-execution-{hashlib.sha256(host_run_id.encode('utf-8')).hexdigest()}"

    def _assert_fixture_principal(self, request: Mapping[str, object]) -> None:
        asserted = request.get("subject")
        if asserted is not None and asserted != self._auth.subject:
            raise HumanMemoryHostServiceError("human_memory_permission_denied")

    def _require_db(self) -> Path:
        if self._db_path is None:
            raise HumanMemoryHostServiceError("human_memory_adapter_not_initialized")
        return self._db_path

    def _require_service(self) -> HumanMemoryHostService:
        if self._service is None:
            raise HumanMemoryHostServiceError("human_memory_adapter_not_initialized")
        return self._service

    def _require_recovery(self) -> RecoveryLifecyclePort:
        if self._recovery is None:
            raise HumanMemoryHostServiceError(
                "human_memory_recovery_authority_unavailable"
            )
        return self._recovery


def create_adapter(*, fixture: dict[str, Any], artifact_dir: Path) -> S4ValuePublicAdapter:
    return S4ValuePublicAdapter(fixture=fixture, artifact_dir=artifact_dir)


__all__ = ("S4ValuePublicAdapter", "create_adapter")
