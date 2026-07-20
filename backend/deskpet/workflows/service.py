"""Framework-neutral application facade for durable workflows."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import inspect
import json
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from deskpet.execution.contracts import (
    DeliveryPolicy,
    DeliverySpec,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunStatus,
    WorkflowRunSeed,
    WorkflowSessionRef,
    fingerprint_json,
    stable_event_id as stable_execution_event_id,
)

from .contracts import (
    TERMINAL_RUN_STATUSES,
    JsonValue,
    WorkflowContext,
    canonical_json,
    validate_json_value,
)
from .evaluation.models import (
    EvaluationOutcome,
    HumanEvaluationRecord,
    PairwiseEvaluationRecord,
)
from .evaluation.store import EvaluationStore
from .delivery import DeliveryDisposition, normalize_v6_delivery_result
from .human import HumanDecision, HumanDecisionStore
from .outbox import MAX_PAGE_SIZE, WorkflowOutbox, hydrate_event
from .runner import WorkflowRunner, manifest_hash
from .execution_ports import WorkflowExecutionPorts
from .runtime_adapters import (
    RuntimeIdentity,
    WorkflowRuntimeAdapterRegistry,
)
from .store.run_store import WorkflowRunStore
from .store.execution_uow import SqliteExecutionUnitOfWork
from .store.checkpointer import NativeCheckpointStore
from .store.research_repository import ResearchRepositoryError, ResearchWorkflowRepository
from .terminal_projection import VersionedActionMatrix, WorkflowActionContext
from .definitions.deep_research_v5_contracts import (
    ResearchControlCommand,
    ResearchEvidenceSnapshot,
)
from .trace.store import TraceStore


START_SNAPSHOT_KEY = "_workflow_start"
_UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class WorkflowServiceError(RuntimeError):
    """Stable error contract consumed by IPC without framework coupling."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        current_version: int | None = None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.current_version = current_version
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class WorkflowStartIdentity:
    venue: str
    base_session_id: str
    code_session_id: str
    delivery_session_id: str
    base_epoch: int
    code_epoch: int
    request_id: str
    turn_id: str
    workflow_name: str
    logical_slot: str

    def to_dict(self) -> dict[str, JsonValue]:
        return asdict(self)

    @property
    def identity_key(self) -> str:
        return _hash_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class PreparedWorkflowStart:
    """Pure, immutable-enough description of one workflow start intent."""

    identity: WorkflowStartIdentity
    workflow_version: str
    manifest_hash: str
    implementation_hash: str
    state_schema_version: int
    capability_snapshot: Mapping[str, JsonValue]
    capability_hash: str
    start_payload: Mapping[str, JsonValue]
    args_hash: str
    request_hash: str
    delivery_targets: tuple[tuple[str, str], ...]
    run_id: str
    trace_id: str
    thread_id: str
    checkpoint_ns: str

    @property
    def profile_key(self) -> str:
        return f"{self.identity.workflow_name}/{self.workflow_version}"


class DurableResearchControlPort:
    """Project the single durable v5 control fence into the graph contract."""

    def __init__(self, repository: object) -> None:
        self.repository = repository

    @staticmethod
    def _timestamp(value: object, *, fallback: float) -> str:
        seconds = float(value) if isinstance(value, (int, float)) else fallback
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()

    async def poll(
        self,
        *,
        run_id: str,
        checkpoint_id: str | None,
    ) -> ResearchControlCommand | None:
        row = await self.repository.active_control(str(run_id))
        if row is None or str(row.get("status")) not in {"accepted", "observed"}:
            return None
        payload = copy.deepcopy(dict(row.get("payload") or {}))
        meta = payload.get("_repository")
        meta = meta if isinstance(meta, Mapping) else {}
        status = str(row["status"])
        if status == "accepted" and checkpoint_id:
            row = await self.repository.transition_control(
                str(row["command_id"]),
                expected_status="accepted",
                new_status="observed",
                checkpoint_ns=str(row.get("head_checkpoint_ns") or ""),
                checkpoint_id=str(checkpoint_id),
            )
            payload = copy.deepcopy(dict(row.get("payload") or {}))
            meta = payload.get("_repository")
            meta = meta if isinstance(meta, Mapping) else {}
            status = str(row["status"])
        cancel_marker = payload.get("_cancel_settle")
        action = "cancel_settle" if isinstance(cancel_marker, Mapping) else str(row["action"])
        idempotency_key = (
            str(cancel_marker.get("idempotency_key"))
            if isinstance(cancel_marker, Mapping)
            else str(row["idempotency_key"])
        )
        now = datetime.now(tz=timezone.utc).timestamp()
        observed_checkpoint = (
            str(row.get("head_checkpoint_id") or checkpoint_id or "") or None
            if status in {"observed", "settled", "consumed"}
            else None
        )
        expires = row.get("settle_deadline") or (float(row.get("created_at") or now) + 30.0)
        result = payload.get("_result")
        return ResearchControlCommand(
            command_id=str(row["command_id"]),
            run_id=str(row["run_id"]),
            action=action,  # type: ignore[arg-type]
            idempotency_key=idempotency_key,
            status=status,  # type: ignore[arg-type]
            expected_run_version=int(meta.get("expected_run_version", 0)),
            observed_checkpoint_id=observed_checkpoint,
            payload=payload,
            result=copy.deepcopy(dict(result)) if isinstance(result, Mapping) else {},
            expires_at=self._timestamp(expires, fallback=now + 30.0),
            created_at=self._timestamp(row.get("created_at"), fallback=now),
            updated_at=self._timestamp(row.get("updated_at"), fallback=now),
        )

    async def settle(
        self,
        *,
        command_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
        result: Mapping[str, JsonValue] | None = None,
    ) -> dict[str, Any]:
        """CAS observed -> settled against the latest durable run head."""

        return _plain(
            await self.repository.transition_control(
                str(command_id),
                expected_status="observed",
                new_status="settled",
                checkpoint_ns=str(checkpoint_ns),
                checkpoint_id=str(checkpoint_id),
                result=dict(result or {}),
            )
        )

    async def consume(
        self,
        *,
        command_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> dict[str, Any]:
        """CAS settled -> consumed against the latest durable terminal head."""

        return _plain(
            await self.repository.transition_control(
                str(command_id),
                expected_status="settled",
                new_status="consumed",
                checkpoint_ns=str(checkpoint_ns),
                checkpoint_id=str(checkpoint_id),
            )
        )


def _hash_json(value: JsonValue) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _encode_cursor(created_at: float, item_id: str) -> str:
    raw = json.dumps([created_at, item_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[float, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError
        return float(value[0]), str(value[1])
    except Exception as exc:
        raise WorkflowServiceError("invalid_cursor", "Workflow cursor is invalid") from exc


def _json_value(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [_plain(item) for item in value]
    return value


def _error_text(value: Any) -> str | None:
    if value is None:
        return None
    parsed = _json_value(value, value)
    if isinstance(parsed, Mapping):
        for key in ("message", "message_ref", "reason", "code"):
            if parsed.get(key):
                return str(parsed[key])
        return canonical_json(dict(parsed))
    return str(parsed)


class WorkflowService:
    """Facade over current stores and replaceable runner/fork/delivery adapters."""

    def __init__(
        self,
        run_store: WorkflowRunStore | str | Path | None = None,
        runner: WorkflowRunner | object | None = None,
        human_store: HumanDecisionStore | object | None = None,
        trace_store: TraceStore | object | None = None,
        evaluation_store: EvaluationStore | object | None = None,
        outbox: WorkflowOutbox | object | None = None,
        *,
        store: WorkflowRunStore | str | Path | None = None,
        decision_store: HumanDecisionStore | object | None = None,
        forker: Callable[..., Any | Awaitable[Any]] | None = None,
        delivery_handlers: Mapping[str, Callable[..., Any | Awaitable[Any]]] | None = None,
        session_delivery_state_reader: Callable[[str], Awaitable[Mapping[str, Any]]] | None = None,
        research_repository: object | None = None,
        research_snapshot_loader: Callable[
            [str], Awaitable[bytes | Mapping[str, JsonValue]]
        ] | None = None,
        action_matrix: VersionedActionMatrix | None = None,
        runtime_adapters: WorkflowRuntimeAdapterRegistry | None = None,
        execution_ports: WorkflowExecutionPorts | None = None,
        runtime_activation_required: bool = False,
        runtime_activation_hooks: Sequence[Callable[[], Awaitable[None]]] = (),
    ) -> None:
        selected = run_store if run_store is not None else store
        if selected is None and runner is not None:
            selected = getattr(runner, "store", None)
        if selected is None:
            raise ValueError("WorkflowService requires a workflow run store")
        self.run_store = selected if isinstance(selected, WorkflowRunStore) else WorkflowRunStore(selected)
        self.runner = runner
        self.human_store = human_store or decision_store or HumanDecisionStore(self.run_store.path)
        self.trace_store = trace_store or TraceStore(self.run_store.path)
        self.evaluation_store = evaluation_store or EvaluationStore(self.run_store.path)
        self.outbox = outbox or WorkflowOutbox(self.run_store)
        self._forker = forker
        self._delivery_handlers = dict(delivery_handlers or {})
        self._session_delivery_state_reader = session_delivery_state_reader
        self.research_repository = research_repository or ResearchWorkflowRepository(
            self.run_store.path
        )
        self._research_snapshot_loader = research_snapshot_loader
        self.action_matrix = action_matrix or VersionedActionMatrix()
        self.runtime_adapters = runtime_adapters or WorkflowRuntimeAdapterRegistry()
        self.execution_ports = execution_ports
        self._owner_uow = execution_ports.unit_of_work if execution_ports else SqliteExecutionUnitOfWork(self.run_store.path)
        if execution_ports is not None and runner is not None:
            configure_execution = getattr(runner, "configure_execution_ports", None)
            if not callable(configure_execution):
                raise ValueError("workflow service requires an execution-aware runner")
            configure_execution(execution_ports)
        self._runtime_activation_required = bool(runtime_activation_required)
        self._runtime_activation_hooks = tuple(runtime_activation_hooks)
        self._start_locks: dict[str, asyncio.Lock] = {}
        self._delivery_locks: dict[str, asyncio.Lock] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}

    @property
    def deep_research_versions(self):
        """Typed view retained as a compatibility name without a second map."""

        return self.runtime_adapters.deep_research

    def ensure_runtime_active(self) -> None:
        if self._runtime_activation_required:
            self.runtime_adapters.require_active()

    async def activate_runtime(
        self,
        *,
        required_runtime_identities: Sequence[RuntimeIdentity] = (),
    ) -> None:
        """Seal registered adapters, validate durable recovery, then activate.

        Production bootstrap calls this only after Main has registered every
        DeepResearch, Code, and PPT runtime closure.  Existing direct service
        tests are not forced through the product bootstrap gate.
        """

        persisted: set[RuntimeIdentity] = set()
        terminal = {str(status.value) for status in TERMINAL_RUN_STATUSES}
        for row in await self.run_store.list_runs(limit=10_000):
            if str(row.get("status") or "") in terminal:
                continue
            persisted.add(
                (str(row.get("workflow_name") or ""), str(row.get("workflow_version") or ""))
            )
        required = tuple(sorted(set(required_runtime_identities) | persisted))
        self.runtime_adapters.seal(required)
        self.runtime_adapters.activate()
        for hook in self._runtime_activation_hooks:
            await hook()

    def session_lock(self, session_id: str) -> asyncio.Lock:
        return self._session_locks.setdefault(str(session_id), asyncio.Lock())

    async def persist_v6_continuation_snapshot(self, run_id: str) -> bool:
        """Pin the terminal v6 snapshot from the canonical manifest event.

        This runs only after the native runner has committed the terminal run,
        and before delivery dispatch.  Replays derive the expiry from the
        persisted terminal timestamp so they cannot move the retention window.
        """

        row = await self.run_store.get_run(str(run_id))
        if row is None or (
            str(row.get("workflow_name")) != "deep_research"
            or str(row.get("workflow_version")) != "v6"
            or str(row.get("status")) != "completed"
        ):
            return False
        manifest_ref: str | None = None
        answer_status: str | None = None
        for event in reversed(await self.run_store.events_after(str(run_id), 0)):
            if str(event.get("event_type")) != "workflow.final":
                continue
            payload = event.get("payload")
            if isinstance(payload, Mapping) and payload.get("manifest_ref"):
                manifest_ref = str(payload["manifest_ref"])
                answer_status = str(
                    payload.get("answer_status") or payload.get("delivery_status") or ""
                )
                break
        if manifest_ref is None:
            raise WorkflowServiceError(
                "v6_terminal_manifest_missing",
                "completed v6 run has no canonical terminal manifest event",
            )
        if answer_status not in {"partial", "insufficient_evidence"}:
            return False
        terminal_at = row.get("ended_at") or row.get("updated_at")
        if not isinstance(terminal_at, (int, float)):
            raise WorkflowServiceError(
                "v6_terminal_timestamp_missing",
                "completed v6 run has no terminal timestamp",
            )
        continue_until = float(terminal_at) + 30.0 * 24.0 * 60.0 * 60.0
        persist = getattr(self.research_repository, "persist_v6_continuation_snapshot", None)
        if not callable(persist):
            raise WorkflowServiceError(
                "workflow_action_unavailable",
                "v6 continuation snapshot persistence is unavailable",
            )
        await persist(
            run_id=str(run_id),
            operation_id=f"research:{run_id}",
            terminal_manifest_ref=manifest_ref,
            continue_until=continue_until,
        )
        return True

    async def recover_v6_continuation_snapshots(self) -> list[str]:
        """Recover a terminal-commit -> snapshot-pin crash window."""

        recovered: list[str] = []
        for row in await self.run_store.list_runs(limit=10_000):
            if (
                str(row.get("workflow_name")) == "deep_research"
                and str(row.get("workflow_version")) == "v6"
                and str(row.get("status")) == "completed"
            ):
                terminal_at = row.get("ended_at") or row.get("updated_at")
                if isinstance(terminal_at, (int, float)) and (
                    float(terminal_at) + 30.0 * 24.0 * 60.0 * 60.0 > datetime.now(
                        tz=timezone.utc
                    ).timestamp()
                ):
                    if await self.persist_v6_continuation_snapshot(str(row["run_id"])):
                        recovered.append(str(row["run_id"]))
        return recovered

    async def _load_research_snapshot_manifest(
        self, *, manifest_ref: str, expected_hash: str
    ) -> dict[str, JsonValue]:
        loader = self._research_snapshot_loader
        if loader is None:
            raise WorkflowServiceError(
                "workflow_action_unavailable",
                "continuation snapshot loader is unavailable",
            )
        loaded = await loader(str(manifest_ref))
        try:
            if isinstance(loaded, (bytes, bytearray)):
                raw = json.loads(bytes(loaded).decode("utf-8"))
            elif isinstance(loaded, Mapping):
                raw = copy.deepcopy(dict(loaded))
            else:
                raise TypeError("snapshot loader returned an unsupported value")
            if not isinstance(raw, Mapping):
                raise TypeError("snapshot manifest must be an object")
            snapshot_payload = copy.deepcopy(dict(raw))
            # The content-addressed blob intentionally omits its own digest;
            # reattach the repository-selected address before strict validation.
            snapshot_payload.setdefault("snapshot_hash", str(expected_hash))
            manifest = ResearchEvidenceSnapshot.from_json(snapshot_payload).to_json()
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkflowServiceError(
                "snapshot_manifest_invalid", "continuation snapshot manifest is invalid"
            ) from exc
        if str(manifest["snapshot_hash"]) != str(expected_hash):
            raise WorkflowServiceError(
                "snapshot_hash_mismatch", "continuation snapshot hash changed"
            )
        return manifest

    async def _parent_report_ref(self, run_id: str) -> str | None:
        """Read the private terminal decision from the durable native head."""

        saver = getattr(self.runner, "saver", None)
        load_head = getattr(saver, "load_head", None)
        if not callable(load_head):
            return None
        head = await load_head(str(run_id))
        if not isinstance(head, Mapping):
            return None
        state = head.get("state")
        values = state.get("values") if isinstance(state, Mapping) else None
        decision = values.get("delivery_decision") if isinstance(values, Mapping) else None
        report_ref = decision.get("report_ref") if isinstance(decision, Mapping) else None
        return str(report_ref) if report_ref is not None else None

    async def _connect(self):
        await self.run_store.initialize()
        return await self.run_store._connect()

    @staticmethod
    def _required(value: str, field: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise WorkflowServiceError("invalid_request", f"{field} must not be empty")
        return normalized

    async def _start_record(self, identity_key: str) -> dict[str, Any] | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute(
                    """SELECT s.run_id,c.snapshot_json FROM workflow_start_requests s
                    JOIN workflow_runs r ON r.run_id=s.run_id
                    LEFT JOIN workflow_capabilities c ON c.capability_hash=r.capability_hash
                    WHERE s.request_key=?""",
                    (identity_key,),
                )
            ).fetchone()
            return dict(row) if row else None
        finally:
            await db.close()

    @staticmethod
    def _assert_same_start(existing: Mapping[str, Any] | None, request_hash: str) -> None:
        if existing is None:
            return
        snapshot = _json_value(existing.get("snapshot_json"), {})
        metadata = snapshot.get(START_SNAPSHOT_KEY) if isinstance(snapshot, Mapping) else None
        if not isinstance(metadata, Mapping) or metadata.get("request_hash") != request_hash:
            raise WorkflowServiceError(
                "start_payload_conflict",
                "The workflow start identity already has a different request snapshot",
                details={"existing_run_id": str(existing["run_id"])},
            )

    def _manifest(self, workflow_name: str, workflow_version: str) -> object:
        registry = getattr(self.runner, "registry", None)
        if registry is None:
            raise WorkflowServiceError(
                "workflow_start_unavailable",
                "The workflow runner does not expose a version registry",
            )
        return registry.require(workflow_name, workflow_version).manifest

    @staticmethod
    def _session_refs(identity: WorkflowStartIdentity) -> tuple[tuple[str, str, int], ...]:
        refs: list[tuple[str, str, int]] = [
            ("base", identity.base_session_id, identity.base_epoch),
        ]
        if identity.code_session_id:
            refs.append(("code", identity.code_session_id, identity.code_epoch))
        delivery_epoch = (
            identity.code_epoch
            if identity.code_session_id and identity.delivery_session_id == identity.code_session_id
            else identity.base_epoch
        )
        refs.append(("delivery", identity.delivery_session_id, delivery_epoch))
        return tuple(refs)

    def prepare_start(
        self,
        *,
        venue: str,
        base_session_id: str,
        delivery_session_id: str,
        request_id: str,
        turn_id: str,
        workflow_name: str,
        workflow_version: str,
        capability_snapshot: Mapping[str, JsonValue],
        start_payload: Mapping[str, JsonValue] | None = None,
        code_session_id: str | None = None,
        base_epoch: int = 0,
        code_epoch: int = 0,
        logical_slot: str = "accepted_async:0",
        delivery_targets: Sequence[tuple[str, str] | Mapping[str, str]] | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        thread_id: str | None = None,
        checkpoint_ns: str = "",
    ) -> PreparedWorkflowStart:
        """Validate and hash a start without touching SQLite or an outbox."""

        if self.runner is None or getattr(self.runner, "registry", None) is None:
            raise WorkflowServiceError(
                "workflow_start_unavailable",
                "The workflow runner does not expose a version registry",
            )
        if (
            isinstance(base_epoch, bool)
            or base_epoch < 0
            or isinstance(code_epoch, bool)
            or code_epoch < 0
        ):
            raise WorkflowServiceError("invalid_request", "session epochs must be non-negative")
        identity = WorkflowStartIdentity(
            venue=self._required(venue, "venue"),
            base_session_id=self._required(base_session_id, "base_session_id"),
            code_session_id=str(code_session_id or ""),
            delivery_session_id=self._required(
                delivery_session_id, "delivery_session_id"
            ),
            base_epoch=int(base_epoch),
            code_epoch=int(code_epoch),
            request_id=self._required(request_id, "request_id"),
            turn_id=self._required(turn_id, "turn_id"),
            workflow_name=self._required(workflow_name, "workflow_name"),
            logical_slot=self._required(logical_slot, "logical_slot"),
        )
        version = self._required(workflow_version, "workflow_version")
        capabilities = copy.deepcopy(dict(capability_snapshot))
        args = copy.deepcopy(dict(start_payload or {}))
        validate_json_value(capabilities, path="$.capability_snapshot")
        validate_json_value(args, path="$.start_payload")
        if START_SNAPSHOT_KEY in capabilities:
            raise WorkflowServiceError(
                "reserved_snapshot_key",
                f"capability_snapshot may not contain {START_SNAPSHOT_KEY}",
            )
        manifest = self._manifest(identity.workflow_name, version)
        original_capability_hash = _hash_json(capabilities)
        args_hash = _hash_json(args)
        request_hash = _hash_json(
            {
                "workflow_version": version,
                "definition_hash": str(
                    getattr(
                        manifest,
                        "definition_hash",
                        getattr(manifest, "implementation_bundle_hash", "unversioned"),
                    )
                ),
                "args_hash": args_hash,
                "capability_hash": original_capability_hash,
            }
        )
        persisted_snapshot: dict[str, JsonValue] = {
            **capabilities,
            START_SNAPSHOT_KEY: {
                "identity": identity.to_dict(),
                "identity_key": identity.identity_key,
                "request_hash": request_hash,
                "args_hash": args_hash,
                "capability_hash": original_capability_hash,
                "start_payload": args,
            },
        }
        normalized_targets: list[tuple[str, str]] = []
        raw_targets = delivery_targets or (
            ("session_message", identity.delivery_session_id),
            ("websocket", identity.delivery_session_id),
        )
        for target in raw_targets:
            if isinstance(target, Mapping):
                channel = str(target.get("channel") or target.get("sink_kind") or "").strip()
                target_id = str(target.get("target_id") or "").strip()
            else:
                channel, target_id = (str(target[0]).strip(), str(target[1]).strip())
            if not channel or not target_id:
                raise WorkflowServiceError(
                    "invalid_request", "delivery target requires channel and target_id"
                )
            normalized_targets.append((channel, target_id))
        if len(set(normalized_targets)) != len(normalized_targets):
            raise WorkflowServiceError("invalid_request", "delivery targets must be unique")
        resolved_run_id = str(run_id or uuid.uuid5(uuid.NAMESPACE_URL, identity.identity_key).hex)
        resolved_trace_id = str(
            trace_id
            or uuid.uuid5(uuid.NAMESPACE_URL, f"trace:{identity.identity_key}").hex
        )
        return PreparedWorkflowStart(
            identity=identity,
            workflow_version=version,
            manifest_hash=manifest_hash(manifest),
            implementation_hash=str(getattr(manifest, "implementation_bundle_hash")),
            state_schema_version=int(getattr(manifest, "state_schema_version")),
            capability_snapshot=persisted_snapshot,
            capability_hash=fingerprint_json(persisted_snapshot),
            start_payload=args,
            args_hash=args_hash,
            request_hash=request_hash,
            delivery_targets=tuple(normalized_targets),
            run_id=resolved_run_id,
            trace_id=resolved_trace_id,
            thread_id=str(thread_id or resolved_run_id),
            checkpoint_ns=str(checkpoint_ns or ""),
        )

    @staticmethod
    def execution_spec(
        prepared: PreparedWorkflowStart,
        *,
        principal_id: str | None = None,
        workspace: Mapping[str, JsonValue] | None = None,
        provider_plan: Mapping[str, JsonValue] | None = None,
    ) -> RunCreate:
        """Build the generic root-run contract for DR/PPT/Code workflow profiles."""

        identity = prepared.identity
        context = RunContext(
            session_id=identity.base_session_id,
            root_run_id=prepared.run_id,
            parent_run_id=None,
            request_id=identity.request_id,
            turn_id=identity.turn_id,
            venue=identity.venue,
            workspace=dict(workspace or {}),
            capability_hash=prepared.capability_hash,
            provider_plan=dict(
                provider_plan
                or {
                    "driver_kind": "workflow",
                    "workflow_name": identity.workflow_name,
                    "workflow_version": prepared.workflow_version,
                }
            ),
            trace_id=prepared.trace_id,
            principal_id=str(principal_id or identity.base_session_id),
            auth_epoch=identity.base_epoch,
        )
        return RunCreate(
            run_id=prepared.run_id,
            idempotency_key=f"root:{identity.identity_key}",
            context=context,
            payload_fingerprint=prepared.request_hash,
            capability_fingerprint=prepared.capability_hash,
            driver_kind="workflow",
            profile_key=prepared.profile_key,
            persistence_level=PersistenceLevel.DURABLE,
            status=RunStatus.CREATED,
        )

    async def start_prepared(
        self,
        prepared: PreparedWorkflowStart,
        spec: RunCreate,
        *,
        execution_ports: WorkflowExecutionPorts | None = None,
    ) -> dict[str, Any]:
        """Atomically create generic + workflow + accepted facts (opt-in only)."""

        ports = execution_ports or self.execution_ports
        if ports is None:
            raise WorkflowServiceError(
                "workflow_execution_ports_required",
                "precreated workflow start requires explicit execution ports",
            )
        identity = prepared.identity
        expected = {
            "run_id": prepared.run_id,
            "driver_kind": "workflow",
            "profile_key": prepared.profile_key,
            "payload_fingerprint": prepared.request_hash,
            "capability_fingerprint": prepared.capability_hash,
        }
        if any(getattr(spec, key) != value for key, value in expected.items()):
            raise WorkflowServiceError(
                "workflow_execution_spec_conflict",
                "generic execution spec differs from the prepared workflow start",
            )
        context_expected = {
            "session_id": identity.base_session_id,
            "root_run_id": prepared.run_id,
            "parent_run_id": None,
            "request_id": identity.request_id,
            "turn_id": identity.turn_id,
            "venue": identity.venue,
            "capability_hash": prepared.capability_hash,
            "trace_id": prepared.trace_id,
            "auth_epoch": identity.base_epoch,
        }
        if any(getattr(spec.context, key) != value for key, value in context_expected.items()):
            raise WorkflowServiceError(
                "workflow_execution_context_conflict",
                "generic run context differs from the prepared workflow start",
            )
        refs = tuple(
            WorkflowSessionRef(kind, session_id, epoch)
            for kind, session_id, epoch in self._session_refs(identity)
        )
        seed = WorkflowRunSeed(
            request_key=identity.identity_key,
            workflow_name=identity.workflow_name,
            workflow_version=prepared.workflow_version,
            manifest_hash=prepared.manifest_hash,
            implementation_hash=prepared.implementation_hash,
            capability_hash=prepared.capability_hash,
            capability_snapshot=prepared.capability_snapshot,
            state_schema_version=prepared.state_schema_version,
            trace_id=prepared.trace_id,
            thread_id=prepared.thread_id,
            checkpoint_ns=prepared.checkpoint_ns,
            session_refs=refs,
        )
        card: dict[str, JsonValue] = {
            "run_id": prepared.run_id,
            "request_id": identity.request_id,
            "turn_id": identity.turn_id,
            "workflow_name": identity.workflow_name,
            "workflow_version": prepared.workflow_version,
            "status": "running",
            "recovery_action": None,
            "error": None,
        }
        accepted_event = RunEventCandidate(
            event_key="run:create:accepted",
            kind="workflow.accepted",
            status=OutcomeStatus.ACCEPTED,
            driver_kind="workflow",
            correlation={
                "request_id": identity.request_id,
                "turn_id": identity.turn_id,
                "identity_key": identity.identity_key,
            },
            payload={
                "kind": "accepted",
                "status": "running",
                "identity_key": identity.identity_key,
                "request_hash": prepared.request_hash,
                "card": card,
            },
        )
        deliveries = tuple(
            DeliverySpec(
                sink_kind=channel,
                sink_instance="workflow",
                target_id=target_id,
                policy=(
                    DeliveryPolicy.DURABLE_REQUIRED
                    if channel in {"session_message", "receipt", "artifact"}
                    else DeliveryPolicy.RETRY_WHILE_BOUND
                ),
            )
            for channel, target_id in prepared.delivery_targets
        )
        result = await ports.unit_of_work.start_workflow(
            spec,
            seed,
            accepted_event=accepted_event,
            deliveries=deliveries,
        )
        return {
            "run_id": result.record.run_id,
            "created": result.created,
            "identity_key": identity.identity_key,
            "request_hash": prepared.request_hash,
            "accepted_event_id": stable_execution_event_id(
                result.record.run_id, accepted_event.event_key
            ),
        }

    async def run_precreated(
        self,
        run_id: str,
        state: object,
        context: WorkflowContext | None = None,
        *, active_lease: Any | None = None,
    ) -> Any:
        runner = self.runner
        call = getattr(runner, "run_precreated", None)
        if not callable(call):
            raise WorkflowServiceError(
                "workflow_execution_ports_required",
                "workflow runner cannot drive a precreated execution",
            )
        return await call(str(run_id), state, context, **({} if active_lease is None else {"active_lease": active_lease}))

    async def resume_precreated(
        self,
        run_id: str,
        responses: Mapping[str, JsonValue],
        context: WorkflowContext | None = None,
        *, active_lease: Any | None = None,
    ) -> Any:
        runner = self.runner
        call = getattr(runner, "resume_precreated", None)
        if not callable(call):
            raise WorkflowServiceError(
                "workflow_execution_ports_required",
                "workflow runner cannot resume a precreated execution",
            )
        return await call(str(run_id), responses, context, **({} if active_lease is None else {"active_lease": active_lease}))

    async def cancel_precreated(
        self, run_id: str, reason: str = "user"
    ) -> dict[str, Any]:
        runner = self.runner
        call = getattr(runner, "request_cancel_precreated", None)
        if not callable(call):
            raise WorkflowServiceError(
                "workflow_execution_ports_required",
                "workflow runner cannot cancel a precreated execution",
            )
        return await call(str(run_id), reason)

    async def start_workflow(
        self,
        *,
        venue: str,
        base_session_id: str,
        delivery_session_id: str,
        request_id: str,
        turn_id: str,
        workflow_name: str,
        workflow_version: str,
        capability_snapshot: Mapping[str, JsonValue],
        start_payload: Mapping[str, JsonValue] | None = None,
        code_session_id: str | None = None,
        base_epoch: int = 0,
        code_epoch: int = 0,
        logical_slot: str = "accepted_async:0",
        delivery_targets: Sequence[tuple[str, str] | Mapping[str, str]] | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        thread_id: str | None = None,
        checkpoint_ns: str = "",
    ) -> dict[str, Any]:
        self.ensure_runtime_active()
        if self.runner is None or not callable(getattr(self.runner, "start", None)):
            raise WorkflowServiceError(
                "workflow_start_unavailable", "The workflow runner cannot start workflows"
            )
        if isinstance(base_epoch, bool) or base_epoch < 0 or isinstance(code_epoch, bool) or code_epoch < 0:
            raise WorkflowServiceError("invalid_request", "session epochs must be non-negative")
        identity = WorkflowStartIdentity(
            venue=self._required(venue, "venue"),
            base_session_id=self._required(base_session_id, "base_session_id"),
            code_session_id=str(code_session_id or ""),
            delivery_session_id=self._required(delivery_session_id, "delivery_session_id"),
            base_epoch=int(base_epoch),
            code_epoch=int(code_epoch),
            request_id=self._required(request_id, "request_id"),
            turn_id=self._required(turn_id, "turn_id"),
            workflow_name=self._required(workflow_name, "workflow_name"),
            logical_slot=self._required(logical_slot, "logical_slot"),
        )
        version = self._required(workflow_version, "workflow_version")
        capabilities = dict(capability_snapshot)
        args = dict(start_payload or {})
        validate_json_value(capabilities, path="$.capability_snapshot")
        validate_json_value(args, path="$.start_payload")
        if START_SNAPSHOT_KEY in capabilities:
            raise WorkflowServiceError(
                "reserved_snapshot_key",
                f"capability_snapshot may not contain {START_SNAPSHOT_KEY}",
            )
        manifest = self._manifest(identity.workflow_name, version)
        capability_hash = _hash_json(capabilities)
        args_hash = _hash_json(args)
        request_hash = _hash_json(
            {
                "workflow_version": version,
                "definition_hash": str(
                    getattr(
                        manifest,
                        "definition_hash",
                        getattr(manifest, "implementation_bundle_hash", "unversioned"),
                    )
                ),
                "args_hash": args_hash,
                "capability_hash": capability_hash,
            }
        )
        persisted_snapshot: dict[str, JsonValue] = {
            **capabilities,
            START_SNAPSHOT_KEY: {
                "identity": identity.to_dict(),
                "identity_key": identity.identity_key,
                "request_hash": request_hash,
                "args_hash": args_hash,
                "capability_hash": capability_hash,
                "start_payload": args,
            },
        }
        lock = self._start_locks.setdefault(identity.identity_key, asyncio.Lock())
        async with lock:
            existing = await self._start_record(identity.identity_key)
            self._assert_same_start(existing, request_hash)
            resolved_run_id = await self.runner.start(
                session_id=identity.base_session_id,
                request_id=identity.request_id,
                turn_id=identity.turn_id,
                workflow_name=identity.workflow_name,
                workflow_version=version,
                capability_snapshot=persisted_snapshot,
                request_key=identity.identity_key,
                capability_hash=_hash_json(persisted_snapshot),
                run_id=run_id,
                trace_id=trace_id,
                thread_id=thread_id,
                checkpoint_ns=checkpoint_ns,
            )
            await self.require_legacy_owner(str(resolved_run_id), "start")
            persisted = await self._start_record(identity.identity_key)
            self._assert_same_start(persisted, request_hash)
            await self.run_store.bind_session_refs(
                str(resolved_run_id),
                self._session_refs(identity),
            )
            targets = list(
                delivery_targets
                or (
                    ("session_message", identity.delivery_session_id),
                    ("websocket", identity.delivery_session_id),
                )
            )
            card: dict[str, JsonValue] = {
                "run_id": str(resolved_run_id),
                "request_id": identity.request_id,
                "turn_id": identity.turn_id,
                "workflow_name": identity.workflow_name,
                "workflow_version": version,
                "status": "running",
                "recovery_action": None,
                "error": None,
            }
            accepted = await self.outbox.ensure_event(
                run_id=str(resolved_run_id),
                event_key="run:create:accepted",
                event_type="workflow.accepted",
                payload={
                    "kind": "accepted",
                    "run_id": str(resolved_run_id),
                    "request_id": identity.request_id,
                    "turn_id": identity.turn_id,
                    "workflow_name": identity.workflow_name,
                    "workflow_version": version,
                    "status": "running",
                    "identity_key": identity.identity_key,
                    "request_hash": request_hash,
                    "card": card,
                },
                deliveries=targets,
            )
            return {
                "run_id": str(resolved_run_id),
                "created": existing is None,
                "identity_key": identity.identity_key,
                "request_hash": request_hash,
                "accepted_event_id": accepted["event_id"],
                "accepted_event": accepted,
            }

    start = start_workflow

    @staticmethod
    def _run_summary(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "run_id": str(row["run_id"]),
            "trace_id": str(row["trace_id"]),
            "thread_id": str(row["thread_id"]),
            "session_id": str(row["session_id"]),
            "request_id": row.get("request_id"),
            "turn_id": row.get("turn_id"),
            "workflow_name": str(row["workflow_name"]),
            "workflow_version": str(row["workflow_version"]),
            "status": str(row["status"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "started_at": row.get("started_at"),
            "ended_at": row.get("ended_at"),
            "active_nodes": list(_json_value(row.get("active_nodes_json"), [])),
            "recovery_action": row.get("recovery_action"),
            "error": _json_value(row.get("error_json"), None),
            "run_version": int(row.get("run_version", 0)),
            "event_seq": int(row.get("event_seq", 0)),
            "head_checkpoint_id": row.get("head_checkpoint_id"),
            "head_checkpoint_ns": row.get("head_checkpoint_ns"),
        }

    async def _require_run(self, run_id: str) -> dict[str, Any]:
        row = await self.run_store.get_run(run_id)
        if row is None:
            raise WorkflowServiceError("run_not_found", f"Workflow run not found: {run_id}")
        return row

    async def execution_owner(self, run_id: str) -> tuple[str, int] | None:
        return await self._owner_uow.get_execution_owner(str(run_id))

    async def require_legacy_owner(self, run_id: str, operation: str) -> None:
        owner = await self.execution_owner(run_id)
        if owner is not None:
            raise WorkflowServiceError("execution_owner", f"legacy {operation} is forbidden for execution-owned run: {owner[0]}/{owner[1]}")

    async def list_runs(
        self,
        *,
        session_id: str | None = None,
        cursor: str | None = None,
        limit: int = MAX_PAGE_SIZE,
    ) -> dict[str, Any]:
        if isinstance(limit, bool) or limit < 1 or limit > MAX_PAGE_SIZE:
            raise WorkflowServiceError(
                "invalid_limit", f"limit must be between 1 and {MAX_PAGE_SIZE}"
            )
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id=?")
            params.append(session_id)
        if cursor:
            created_at, run_id = _decode_cursor(cursor)
            clauses.append("(created_at<? OR (created_at=? AND run_id<?))")
            params.extend((created_at, created_at, run_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    f"""SELECT * FROM workflow_runs{where}
                    ORDER BY created_at DESC,run_id DESC LIMIT ?""",
                    (*params, limit + 1),
                )
            ).fetchall()
        finally:
            await db.close()
        selected = rows[:limit]
        next_cursor = None
        if len(rows) > limit and selected:
            tail = selected[-1]
            next_cursor = _encode_cursor(float(tail["created_at"]), str(tail["run_id"]))
        return {
            "runs": [self._run_summary(dict(row)) for row in selected],
            "next_cursor": next_cursor,
        }

    def _definition_projection(
        self, run: Mapping[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        registry = getattr(self.runner, "registry", None)
        if registry is None:
            return [], []
        entry = registry.get(str(run["workflow_name"]), str(run["workflow_version"]))
        definition = getattr(getattr(entry, "workflow", None), "definition", None)
        if definition is None:
            return [], []
        nodes = [
            {"id": str(node.node_id), "label": str(node.node_id), "status": "pending"}
            for node in getattr(definition, "nodes", ())
        ]
        edges: list[dict[str, Any]] = []
        for edge in getattr(definition, "edges", ()):
            for source in edge.sources:
                edges.append({"source": str(source), "target": str(edge.target)})
        for edge in getattr(definition, "conditional_edges", ()):
            for label, target in sorted(dict(edge.routes).items()):
                edges.append(
                    {"source": str(edge.source), "target": str(target), "label": str(label)}
                )
        return nodes, edges

    async def _node_projection(self, run_id: str) -> list[dict[str, Any]]:
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    """SELECT n.*,a.error_ref FROM workflow_nodes n
                    LEFT JOIN workflow_node_attempts a ON a.node_execution_id=n.node_execution_id
                    AND a.retry_attempt=n.latest_attempt WHERE n.run_id=?
                    ORDER BY n.updated_at,n.node_id,n.node_execution_id""",
                    (run_id,),
                )
            ).fetchall()
        finally:
            await db.close()
        return [
            {
                "id": str(row["node_id"]),
                "label": str(row["node_id"]),
                "status": str(row["latest_status"]),
                "attempt": int(row["latest_attempt"]),
                "error": row["error_ref"],
                "node_execution_id": str(row["node_execution_id"]),
            }
            for row in rows
        ]

    async def trace_tree(
        self, *, run_id: str | None = None, trace_id: str | None = None
    ) -> dict[str, Any]:
        run = await self._require_run(run_id) if run_id is not None else None
        resolved_trace_id = trace_id or (str(run["trace_id"]) if run else None)
        if not resolved_trace_id:
            raise WorkflowServiceError("invalid_request", "run_id or trace_id is required")
        raw = await self.trace_store.tree(resolved_trace_id)
        if raw is None:
            return {
                "run_id": run_id,
                "trace_id": resolved_trace_id,
                "run": None,
                "spans": [],
                "roots": [],
            }
        trace_run = dict(raw["run"])
        trace_run["error"] = _json_value(trace_run.pop("error_json", None), None)
        spans: list[dict[str, Any]] = []
        for item in raw["spans"]:
            span = dict(item)
            span["attributes"] = _json_value(span.pop("attributes_json", None), {})
            span["error"] = _error_text(span.pop("error_json", None))
            spans.append(span)
        children: dict[str | None, list[dict[str, Any]]] = {}
        for span in spans:
            children.setdefault(span.get("parent_span_id"), []).append(span)

        def branch(span: Mapping[str, Any]) -> dict[str, Any]:
            return {
                **dict(span),
                "children": [branch(item) for item in children.get(str(span["span_id"]), [])],
            }

        return {
            "run_id": trace_run.get("run_id") or run_id,
            "trace_id": resolved_trace_id,
            "run": trace_run,
            "spans": spans,
            "roots": [branch(item) for item in children.get(None, [])],
        }

    get_trace_tree = trace_tree

    async def checkpoint_history(
        self, run_id: str, *, limit: int | None = None
    ) -> dict[str, Any]:
        await self._require_run(run_id)
        method = getattr(self.runner, "get_state_history", None)
        history = [] if not callable(method) else [dict(item) for item in await method(run_id, limit=limit)]
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    "SELECT checkpoint_id,created_at FROM workflow_checkpoint_owners WHERE run_id=?",
                    (run_id,),
                )
            ).fetchall()
        finally:
            await db.close()
        created = {str(row["checkpoint_id"]): float(row["created_at"]) for row in rows}
        checkpoints = []
        for item in history:
            checkpoint_id = str(item["checkpoint_id"])
            metadata = dict(item.get("metadata") or {})
            checkpoints.append(
                {
                    **item,
                    "checkpoint_id": checkpoint_id,
                    "node_id": metadata.get("deskpet_node_id") or metadata.get("node_id"),
                    "created_at": created.get(checkpoint_id, 0.0),
                    "status": str(metadata.get("status") or "checkpointed"),
                }
            )
        return {"run_id": run_id, "checkpoints": checkpoints, "count": len(checkpoints)}

    get_checkpoint_history = checkpoint_history

    @staticmethod
    def _decision_dict(decision: HumanDecision | object) -> dict[str, Any]:
        return _plain(decision)

    async def resolve_decision(
        self,
        decision_id: str,
        *,
        nonce: str,
        response: JsonValue,
        expected_version: int,
    ) -> dict[str, Any]:
        decision = await self.human_store.resolve_decision(
            decision_id,
            nonce=nonce,
            response=response,
            expected_version=expected_version,
        )
        responses = await self.human_store.build_resume_payload(decision_id)
        launcher = getattr(self, "launcher", None)
        schedule = getattr(launcher, "schedule_resume", None)
        if callable(schedule):
            accepted = schedule(decision.run_id, responses)
            if inspect.isawaitable(accepted):
                accepted = await accepted
            return {
                **self._decision_dict(decision),
                "resume": {
                    "triggered": True,
                    "accepted": True,
                    "completion_semantics": "accepted_async",
                    "interrupt_ids": list(responses),
                    "dispatch": _plain(accepted),
                },
            }
        resumed = await self.resume_run(decision.run_id, responses, trusted=True)
        return {
            **self._decision_dict(decision),
            "resume": {
                "triggered": True,
                "interrupt_ids": list(responses),
                "result": resumed,
            },
        }

    async def resume_run(
        self,
        run_id: str,
        responses: Mapping[str, JsonValue],
        context: WorkflowContext | None = None,
        trusted: bool = False,
    ) -> dict[str, Any]:
        if not trusted:
            authoritative = await self.human_store.build_run_resume_payload(run_id)
            if canonical_json(dict(responses)) != canonical_json(authoritative):
                raise WorkflowServiceError(
                    "invalid_resume_payload",
                    "Workflow resume responses must come from resolved decisions",
                )
        launcher = getattr(self, "launcher", None)
        method = getattr(launcher, "resume_run", None)
        uses_launcher = callable(method)
        if not callable(method):
            method = getattr(self.runner, "resume", None)
            await self.require_legacy_owner(run_id, "resume")
        if not callable(method):
            raise WorkflowServiceError(
                "workflow_resume_unavailable", "The workflow runner cannot resume runs"
            )
        if uses_launcher:
            return _plain(await method(run_id, dict(responses)))
        return _plain(await method(run_id, dict(responses), context))

    resume = resume_run

    async def cancel_run(self, run_id: str, *, reason: str = "user") -> dict[str, Any]:
        if await self.execution_owner(run_id) is not None:
            return _plain(await self.cancel_precreated(run_id, reason))
        method = getattr(self.runner, "request_cancel", None)
        if not callable(method):
            raise WorkflowServiceError(
                "workflow_cancel_unavailable", "The workflow runner cannot cancel runs"
            )
        return _plain(await method(run_id, reason))

    cancel = cancel_run

    async def execute_run_action(
        self,
        run_id: str,
        *,
        action_id: str,
        idempotency_key: str,
        expected_version: int,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> dict[str, Any]:
        """Execute one action through the immutable workflow-version matrix."""

        run_id = self._required(run_id, "run_id")
        action_id = self._required(action_id, "action_id")
        idempotency_key = self._required(idempotency_key, "idempotency_key")
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0
        ):
            raise WorkflowServiceError("invalid_run_version", "expected_version must be non-negative")
        action_payload = copy.deepcopy(dict(payload or {}))
        validate_json_value(action_payload, path="$.action_payload")
        row = await self.run_store.get_run(run_id)
        if row is None:
            raise WorkflowServiceError("not_found", "workflow run was not found")
        if int(row.get("run_version", -1)) != expected_version:
            raise WorkflowServiceError(
                "stale_run_version",
                "workflow run version changed",
                current_version=int(row.get("run_version", -1)),
            )
        workflow_name = str(row.get("workflow_name") or "")
        workflow_version = str(row.get("workflow_version") or "")
        engine_status = str(row.get("status") or "")

        if (workflow_name, workflow_version, action_id) == (
            "deep_research", "v4", "retry_from_start"
        ):
            if not self.action_matrix.allows(
                workflow_name,
                workflow_version,
                action_id,
                WorkflowActionContext(engine_status=engine_status),
            ):
                raise WorkflowServiceError("workflow_action_not_allowed", "workflow action is not allowed")
            return await self.retry_run_from_start(
                run_id, action_id=action_id, retry_key=idempotency_key
            )

        if workflow_name != "deep_research" or workflow_version not in {"v5", "v6"}:
            raise WorkflowServiceError(
                "workflow_action_not_supported",
                "workflow version does not support this action",
            )
        repository = self.research_repository
        try:
            if workflow_version == "v6" and action_id == "continue_research":
                if action_payload:
                    raise WorkflowServiceError(
                        "invalid_action_payload",
                        "v6 continue_research accepts an empty action payload only; "
                        "a new scope requires a new root run",
                    )
                create_or_get = getattr(repository, "create_or_get_continuation_v6", None)
                if not callable(create_or_get):
                    raise WorkflowServiceError(
                        "workflow_action_unavailable",
                        "v6 continuation repository support is unavailable",
                    )
                # The repository is the sole manifest/snapshot/lineage owner.  Do
                # not resolve any parent material here: doing so would create a
                # check/use window before the continuation-head transaction.
                result = _plain(await create_or_get(run_id, idempotency_key))
                if not isinstance(result, Mapping):
                    raise WorkflowServiceError(
                        "invalid_continuation_result",
                        "v6 continuation repository returned an invalid result",
                    )
                expected_result_keys = {
                    "schema_version", "parent_run_id", "child_run_id",
                    "child_operation_id", "created", "start_payload",
                    "start_request_hash", "audit_operation_id",
                }
                if set(result) != expected_result_keys or result.get("schema_version") != 1:
                    raise WorkflowServiceError(
                        "invalid_continuation_result",
                        "v6 continuation repository result keys differ",
                    )
                child_run_id = self._required(str(result["child_run_id"]), "child_run_id")
                start_payload = result["start_payload"]
                if not isinstance(start_payload, Mapping) or set(start_payload) != {
                    "schema_version", "parent_run_id", "source_snapshot_hash"
                }:
                    raise WorkflowServiceError(
                        "invalid_continuation_result",
                        "v6 continuation persisted start payload differs",
                    )
                launcher = getattr(self, "launcher", None)
                launch = getattr(launcher, "launch_existing_run", None)
                if not callable(launch):
                    raise WorkflowServiceError(
                        "workflow_action_unavailable", "continuation launcher is unavailable"
                    )
                # Notify/launch for both the first and replay audit result.  A
                # commit-before-notify crash is recovered by the launcher's
                # generic created-run scan using this same persisted payload.
                await launch(child_run_id, start_payload=dict(start_payload))
                return {
                    "run_id": child_run_id,
                    "parent_run_id": run_id,
                    "action_id": action_id,
                    "accepted": True,
                    "created": bool(result["created"]),
                    "child_operation_id": str(result["child_operation_id"]),
                    "audit_operation_id": str(result["audit_operation_id"]),
                    "start_request_hash": str(result["start_request_hash"]),
                }

            if action_id == "retry_from_start":
                if action_payload:
                    raise WorkflowServiceError(
                        "invalid_action_payload", "retry_from_start does not accept action payload"
                    )
                if not self.action_matrix.allows(
                    workflow_name,
                    workflow_version,
                    action_id,
                    WorkflowActionContext(engine_status=engine_status),
                ):
                    raise WorkflowServiceError("workflow_action_not_allowed", "retry_from_start is not allowed")
                return await self.retry_run_from_start(
                    run_id, action_id=action_id, retry_key=idempotency_key
                )
            if action_id == "generate_now":
                if workflow_version == "v6" and action_payload:
                    raise WorkflowServiceError(
                        "invalid_action_payload", "v6 generate_now does not accept action payload"
                    )
                if workflow_version == "v6":
                    brief_ns = str(row.get("head_checkpoint_ns") or "")
                    brief_id = self._required(
                        str(row.get("head_checkpoint_id") or ""), "head_checkpoint_id"
                    )
                elif not action_payload:
                    # The public v5 projection deliberately does not expose internal
                    # checkpoint identities. Resolve the exact current head on the
                    # server and verify that it contains the committed modeling
                    # contract before using it as the brief CAS ancestor.
                    brief_ns = str(row.get("head_checkpoint_ns") or "")
                    brief_id = self._required(
                        str(row.get("head_checkpoint_id") or ""), "head_checkpoint_id"
                    )
                    checkpoint = await NativeCheckpointStore(self.run_store.path).get_checkpoint(
                        run_id,
                        brief_id,
                        checkpoint_ns=brief_ns,
                    )
                    state = checkpoint.get("state") if isinstance(checkpoint, Mapping) else None
                    values = state.get("values") if isinstance(state, Mapping) else None
                    if not (
                        isinstance(values, Mapping)
                        and isinstance(values.get("research_brief"), Mapping)
                        and isinstance(values.get("dimension_coverages"), list)
                    ):
                        raise WorkflowServiceError(
                            "workflow_action_not_allowed",
                            "generate_now requires a committed research brief",
                        )
                elif set(action_payload) == {"brief_checkpoint_ns", "brief_checkpoint_id"}:
                    brief_ns = str(action_payload["brief_checkpoint_ns"])
                    brief_id = self._required(
                        str(action_payload["brief_checkpoint_id"]), "brief_checkpoint_id"
                    )
                else:
                    raise WorkflowServiceError(
                        "invalid_action_payload",
                        "generate_now accepts no payload or exactly brief_checkpoint_ns/brief_checkpoint_id",
                    )
                if not self.action_matrix.allows(
                    workflow_name,
                    workflow_version,
                    action_id,
                    WorkflowActionContext(
                        engine_status=engine_status,
                        brief_committed=True,
                    ),
                ):
                    raise WorkflowServiceError("workflow_action_not_allowed", "generate_now is not allowed")
                opened, created = await repository.open_control(
                    run_id=run_id,
                    idempotency_key=idempotency_key,
                    action=action_id,
                    expected_run_version=expected_version,
                    expected_head_checkpoint_ns=str(row.get("head_checkpoint_ns") or ""),
                    expected_head_checkpoint_id=(
                        str(row["head_checkpoint_id"])
                        if row.get("head_checkpoint_id") is not None
                        else None
                    ),
                    payload={},
                )
                command = await repository.accept_generate_now(
                    str(opened["command_id"]),
                    brief_checkpoint_ns=brief_ns,
                    brief_checkpoint_id=brief_id,
                )
                if str(command.get("status")) != "accepted":
                    raise WorkflowServiceError(
                        "workflow_action_rejected",
                        "generate_now lost the run/head/brief compare-and-swap",
                    )
                launcher = getattr(self, "launcher", None)
                wake = getattr(launcher, "wake_run_control", None)
                if callable(wake):
                    await wake(run_id)
                return {
                    "run_id": run_id,
                    "action_id": action_id,
                    "accepted": True,
                    "created": bool(created),
                    "command": _plain(command),
                }

            active = await repository.active_control(run_id)
            if action_id == "cancel_settle":
                if not self.action_matrix.allows(
                    workflow_name,
                    workflow_version,
                    action_id,
                    WorkflowActionContext(
                        engine_status=engine_status,
                        active_control_status=(str(active.get("status")) if active else None),
                    ),
                ):
                    raise WorkflowServiceError("workflow_action_not_allowed", "cancel_settle is not allowed")
                request_cancel = getattr(repository, "request_cancel_settle", None)
                if not callable(request_cancel):
                    raise WorkflowServiceError(
                        "workflow_action_unavailable", "cancel_settle repository support is unavailable"
                    )
                existing_marker = (
                    active.get("payload", {}).get("_cancel_settle")
                    if active and isinstance(active.get("payload"), Mapping)
                    else None
                )
                command = await request_cancel(
                    run_id,
                    idempotency_key=idempotency_key,
                    expected_run_version=expected_version,
                )
                launcher = getattr(self, "launcher", None)
                wake = getattr(launcher, "wake_run_control", None)
                if callable(wake):
                    await wake(run_id)
                return {
                    "run_id": run_id,
                    "action_id": action_id,
                    "accepted": True,
                    "created": bool(
                        command.get("created", not isinstance(existing_marker, Mapping))
                    ),
                    "command": _plain(command),
                }

            if workflow_version == "v5" and action_id == "continue_research":
                if action_payload:
                    raise WorkflowServiceError(
                        "invalid_action_payload",
                        "continue_research does not accept client snapshot or lineage identity",
                    )
                resolve_source = getattr(repository, "resolve_continuation_source", None)
                if not callable(resolve_source):
                    raise WorkflowServiceError(
                        "workflow_action_unavailable",
                        "continuation repository resolver is unavailable",
                    )
                source = await resolve_source(run_id)
                final_payload: Mapping[str, Any] | None = None
                for event in reversed(await self.run_store.events_after(run_id, 0)):
                    if str(event.get("event_type")) == "workflow.final" and isinstance(
                        event.get("payload"), Mapping
                    ):
                        final_payload = event["payload"]
                        break
                delivery_status = (
                    str(final_payload.get("delivery_status")) if final_payload is not None else None
                )
                if not self.action_matrix.allows(
                    workflow_name,
                    workflow_version,
                    action_id,
                    WorkflowActionContext(
                        engine_status=engine_status,
                        delivery_status=delivery_status,
                        # The repository is the authoritative snapshot/pin/expiry
                        # validator; this is only a coarse matrix precondition.
                        snapshot_available=True,
                    ),
                ):
                    raise WorkflowServiceError("workflow_action_not_allowed", "continue_research is not allowed")
                stable = uuid.uuid5(uuid.NAMESPACE_URL, f"deskpet:{run_id}:{idempotency_key}")
                child_run_id = str(stable)
                child_operation_id = f"research:{child_run_id}"
                snapshot_hash = self._required(str(source["snapshot_hash"]), "snapshot_hash")
                parent_operation_id = self._required(
                    str(source["parent_operation_id"]), "parent_operation_id"
                )
                manifest = await self._load_research_snapshot_manifest(
                    manifest_ref=self._required(str(source["manifest_ref"]), "manifest_ref"),
                    expected_hash=snapshot_hash,
                )
                start = await self.run_store.get_start_snapshot(run_id)
                start_payload = start.get("start_payload") if isinstance(start, Mapping) else None
                if not isinstance(start_payload, Mapping):
                    raise WorkflowServiceError(
                        "invalid_start_snapshot", "parent workflow start payload is invalid"
                    )
                topic = self._required(str(start_payload.get("topic") or ""), "topic")
                parent_report_ref = (
                    str(source["parent_report_ref"])
                    if source.get("parent_report_ref") is not None
                    else await self._parent_report_ref(run_id)
                )
                if delivery_status == "partial" and not parent_report_ref:
                    raise WorkflowServiceError(
                        "parent_report_missing",
                        "partial continuation parent has no durable report reference",
                    )
                child_payload: dict[str, JsonValue] = {
                    "topic": topic,
                    "mode": str(start_payload.get("mode") or "standard"),
                    "research_config": copy.deepcopy(
                        dict(start_payload.get("research_config") or {})
                    ),
                    "blob_root": str(start_payload.get("blob_root") or ""),
                    "snapshot_hash": snapshot_hash,
                    "parent_operation_id": parent_operation_id,
                    "operation_id": child_operation_id,
                    "continuation": True,
                    "only_gaps": True,
                    "continuation_snapshot": manifest,
                }
                launcher = getattr(self, "launcher", None)
                launch = getattr(launcher, "launch_existing_run", None)
                if not callable(launch):
                    raise WorkflowServiceError(
                        "workflow_action_unavailable", "continuation launcher is unavailable"
                    )
                lineage, created = await repository.create_child_from_snapshot(
                    parent_run_id=run_id,
                    parent_operation_id=parent_operation_id,
                    snapshot_hash=snapshot_hash,
                    child_run_id=child_run_id,
                    child_operation_id=child_operation_id,
                    child_trace_id=uuid.uuid5(stable, "trace").hex,
                    child_thread_id=uuid.uuid5(stable, "thread").hex,
                    child_request_key=f"continue:{run_id}:{idempotency_key}",
                    child_request_id=f"continue:{idempotency_key}",
                    child_turn_id=uuid.uuid5(stable, "turn").hex,
                    budget_lease_id=uuid.uuid5(stable, "budget").hex,
                    start_payload=child_payload,
                    parent_report_ref=parent_report_ref,
                )
                if created:
                    await launch(child_run_id, start_payload=child_payload)
                return {
                    "run_id": child_run_id,
                    "parent_run_id": run_id,
                    "action_id": action_id,
                    "accepted": True,
                    "created": bool(created),
                    "lineage": _plain(lineage),
                }
        except WorkflowServiceError:
            raise
        except ResearchRepositoryError as exc:
            raise WorkflowServiceError(exc.code, str(exc)) from exc
        raise WorkflowServiceError(
            "workflow_action_not_supported", "workflow version does not support this action"
        )

    async def retry_run_from_start(
        self,
        run_id: str,
        *,
        action_id: str,
        retry_key: str,
    ) -> dict[str, Any]:
        """Create one idempotent versioned research retry under the session lock."""

        source_run_id = self._required(run_id, "run_id")
        if action_id != "retry_from_start":
            raise WorkflowServiceError("invalid_retry_action", "retry action is not supported")
        if not _UUID_V4_RE.fullmatch(str(retry_key)):
            raise WorkflowServiceError("invalid_retry_key", "retry_key must be a lowercase UUID v4")
        initial = await self.run_store.get_start_snapshot(source_run_id)
        if initial is None:
            raise WorkflowServiceError("not_found", "workflow run was not found")
        initial_identity = initial.get("identity")
        if not isinstance(initial_identity, Mapping):
            raise WorkflowServiceError("invalid_start_snapshot", "workflow start identity is invalid")
        delivery_session_id = self._required(
            str(initial_identity.get("delivery_session_id") or ""), "delivery_session_id"
        )
        lock = self.session_lock(delivery_session_id)
        async with lock:
            source = await self.run_store.get_start_snapshot(source_run_id)
            if source is None:
                raise WorkflowServiceError("not_found", "workflow run was not found")
            identity_value = source.get("identity")
            if not isinstance(identity_value, Mapping):
                raise WorkflowServiceError("invalid_start_snapshot", "workflow start identity is invalid")
            try:
                identity = WorkflowStartIdentity(**dict(identity_value))
            except (TypeError, ValueError) as exc:
                raise WorkflowServiceError(
                    "invalid_start_snapshot", "workflow start identity is invalid"
                ) from exc
            if identity.delivery_session_id != delivery_session_id:
                raise WorkflowServiceError("retry_session_changed", "workflow delivery session changed")
            if (
                str(source.get("status") or "") != "failed"
                or str(source.get("workflow_name") or "") != "deep_research"
                or str(source.get("workflow_version") or "") not in {"v4", "v5"}
                or identity.workflow_name != "deep_research"
            ):
                raise WorkflowServiceError(
                    "workflow_retry_not_allowed",
                    "only failed supported deep_research runs can be retried from start",
                )
            delivery_ref = await self.run_store.get_session_ref(source_run_id, "delivery")
            if (
                delivery_ref is None
                or delivery_ref.get("deleted_at") is not None
                or str(delivery_ref.get("session_id") or "") != delivery_session_id
            ):
                raise WorkflowServiceError("session_deleted", "workflow delivery session is deleted")
            if self._session_delivery_state_reader is None:
                raise WorkflowServiceError(
                    "session_state_unavailable", "session delivery state reader is unavailable"
                )
            delivery_state = await self._session_delivery_state_reader(delivery_session_id)
            if delivery_state.get("deleted_at") is not None:
                raise WorkflowServiceError("session_deleted", "workflow delivery session is deleted")
            if int(delivery_state.get("epoch", -1)) != int(delivery_ref.get("session_epoch", -2)):
                raise WorkflowServiceError(
                    "session_epoch_mismatch", "workflow delivery session epoch changed"
                )
            launcher = getattr(self, "launcher", None)
            method = getattr(launcher, "retry_from_start_locked", None)
            if not callable(method):
                raise WorkflowServiceError(
                    "workflow_retry_unavailable", "workflow retry launcher is unavailable"
                )
            return _plain(
                await method(
                    source,
                    retry_key=retry_key,
                    session_lock=lock,
                )
            )

    async def cancel_runs_for_session(
        self,
        session_id: str,
        *,
        session_kind: str | None = None,
        reason: str = "session_deleted",
    ) -> list[dict[str, Any]]:
        """Fence every nonterminal run bound to a deleted session."""

        run_ids = await self.run_store.tombstone_session_refs(
            self._required(session_id, "session_id"),
            session_kind=session_kind,
        )
        cancelled: list[dict[str, Any]] = []
        for run_id in run_ids:
            cancelled.append(await self.cancel_run(run_id, reason=reason))
        return cancelled

    async def fork_checkpoint(
        self,
        *,
        run_id: str,
        checkpoint_id: str,
        expected_version: int,
        state_patch: Mapping[str, JsonValue] | None = None,
        fork_key: str | None = None,
        confirm_dangerous_effects: bool = False,
    ) -> dict[str, Any]:
        method = self._forker
        if method is None:
            method = getattr(self.runner, "fork_checkpoint", None) or getattr(self.runner, "fork", None)
        if not callable(method):
            raise WorkflowServiceError(
                "workflow_fork_unavailable",
                "Checkpoint fork support is not available in the current runner",
            )
        patch = dict(state_patch or {})
        validate_json_value(patch, path="$.state_patch")
        arguments = {
            "run_id": run_id,
            "checkpoint_id": checkpoint_id,
            "expected_version": expected_version,
            "state_patch": patch,
            "fork_key": fork_key,
        }
        parameters = inspect.signature(method).parameters
        accepts_confirmation = "confirm_dangerous_effects" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        if accepts_confirmation:
            arguments["confirm_dangerous_effects"] = confirm_dangerous_effects
        elif confirm_dangerous_effects:
            raise WorkflowServiceError(
                "workflow_fork_confirmation_unsupported",
                "The configured fork adapter cannot enforce dangerous effect confirmation",
            )
        result = method(**arguments)
        if inspect.isawaitable(result):
            result = await result
        plain = _plain(result)
        launcher = getattr(self, "launcher", None)
        child_run_id = plain.get("run_id") if isinstance(plain, Mapping) else None
        if child_run_id and callable(getattr(launcher, "recover_pending", None)):
            await launcher.recover_pending(only_run_ids={str(child_run_id)})
        return plain

    fork = fork_checkpoint

    async def events_after_seq(
        self, run_id: str, after_seq: int, *, limit: int = MAX_PAGE_SIZE
    ) -> dict[str, Any]:
        return await self.outbox.events_after(run_id, after_seq, limit=limit)

    events_after = events_after_seq

    async def hydrate_history_event_ids(
        self, run_id: str, event_ids: Sequence[str]
    ) -> dict[str, Any]:
        if not event_ids:
            return {
                "run_id": run_id,
                "events": [],
                "missing_event_ids": [],
                "degraded": False,
                "mark_seen_after_reduce": True,
            }
        run = await self._require_run(run_id)
        placeholders = ",".join("?" for _ in event_ids)
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    f"""SELECT * FROM workflow_events WHERE run_id=?
                    AND event_id IN ({placeholders})""",
                    (run_id, *event_ids),
                )
            ).fetchall()
        finally:
            await db.close()
        by_id = {str(row["event_id"]): dict(row) for row in rows}
        aggregate = await self.outbox.delivery_aggregate(run_id)
        events = [hydrate_event(by_id[event_id], run=run) for event_id in event_ids if event_id in by_id]
        if aggregate is not None:
            for event in events:
                event["delivery_aggregate"] = aggregate
        missing = [event_id for event_id in event_ids if event_id not in by_id]
        return {
            "run_id": run_id,
            "events": events,
            "missing_event_ids": missing,
            "degraded": bool(missing),
            "mark_seen_after_reduce": True,
        }

    async def hydrate_session_history_event_ids(
        self,
        session_id: str,
        event_ids: Sequence[str],
        *,
        current_session_epoch: int | None = None,
        session_deleted: bool = False,
    ) -> dict[str, Any]:
        """Hydrate SessionDB event references without crossing a session fence.

        Session history only stores ``workflow_event_id``.  This adapter restores
        the durable envelope in one batch, but only when the event was projected
        to the requested session and the run's persisted delivery reference still
        matches the current SessionDB epoch.
        """

        target = self._required(session_id, "session_id")
        normalized_ids = list(
            dict.fromkeys(str(event_id).strip() for event_id in event_ids if str(event_id).strip())
        )
        if len(normalized_ids) > MAX_PAGE_SIZE:
            raise WorkflowServiceError(
                "history_page_too_large",
                f"At most {MAX_PAGE_SIZE} workflow history events can be hydrated",
            )
        if not normalized_ids or session_deleted:
            return {
                "session_id": target,
                "events": [],
                "missing_event_ids": normalized_ids if session_deleted else [],
                "fenced_event_ids": normalized_ids if session_deleted else [],
                "degraded": bool(normalized_ids and session_deleted),
                "mark_seen_after_reduce": True,
            }

        placeholders = ",".join("?" for _ in normalized_ids)
        db = await self._connect()
        try:
            event_rows = await (
                await db.execute(
                    f"SELECT * FROM workflow_events WHERE event_id IN ({placeholders})",
                    tuple(normalized_ids),
                )
            ).fetchall()
            event_by_id = {str(row["event_id"]): dict(row) for row in event_rows}
            run_ids = list(dict.fromkeys(str(row["run_id"]) for row in event_rows))
            run_by_id: dict[str, dict[str, Any]] = {}
            ref_by_run: dict[str, dict[str, Any]] = {}
            if run_ids:
                run_placeholders = ",".join("?" for _ in run_ids)
                run_rows = await (
                    await db.execute(
                        f"SELECT * FROM workflow_runs WHERE run_id IN ({run_placeholders})",
                        tuple(run_ids),
                    )
                ).fetchall()
                run_by_id = {str(row["run_id"]): dict(row) for row in run_rows}
                ref_rows = await (
                    await db.execute(
                        f"""SELECT * FROM workflow_session_refs
                        WHERE session_kind='delivery' AND run_id IN ({run_placeholders})""",
                        tuple(run_ids),
                    )
                ).fetchall()
                ref_by_run = {str(row["run_id"]): dict(row) for row in ref_rows}

            delivery_rows = await (
                await db.execute(
                    f"""SELECT DISTINCT event_id FROM workflow_deliveries
                    WHERE event_id IN ({placeholders})
                    AND channel IN ('session_message','websocket') AND target_id=?""",
                    (*normalized_ids, target),
                )
            ).fetchall()
            delivered_ids = {str(row["event_id"]) for row in delivery_rows}
        finally:
            await db.close()

        events: list[dict[str, Any]] = []
        missing: list[str] = []
        fenced: list[str] = []
        for event_id in normalized_ids:
            row = event_by_id.get(event_id)
            if row is None:
                missing.append(event_id)
                continue
            run_id = str(row["run_id"])
            run = run_by_id.get(run_id)
            ref = ref_by_run.get(run_id)
            epoch_matches = (
                current_session_epoch is None
                or (ref is not None and int(ref["session_epoch"]) == int(current_session_epoch))
            )
            if (
                run is None
                or ref is None
                or str(ref["session_id"]) != target
                or ref.get("deleted_at") is not None
                or event_id not in delivered_ids
                or not epoch_matches
            ):
                fenced.append(event_id)
                continue
            hydrated = hydrate_event(row, run=run)
            aggregate = await self.outbox.delivery_aggregate(run_id)
            if aggregate is not None:
                hydrated["delivery_aggregate"] = aggregate
            events.append(hydrated)

        return {
            "session_id": target,
            "events": events,
            "missing_event_ids": missing,
            "fenced_event_ids": fenced,
            "degraded": bool(missing or fenced),
            "mark_seen_after_reduce": True,
        }

    async def list_deliveries(
        self,
        *,
        run_id: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = MAX_PAGE_SIZE,
    ) -> dict[str, Any]:
        return await self.outbox.list_deliveries(
            run_id=run_id, status=status, cursor=cursor, limit=limit
        )

    async def retry_delivery(
        self, delivery_id: str, *, expected_version: int, reason: str | None = None
    ) -> dict[str, Any]:
        if (delivery := await self.outbox.get_delivery(delivery_id)) is not None:
            await self.require_legacy_owner(str(delivery["run_id"]), "delivery retry")
        lock = self._delivery_locks.setdefault(delivery_id, asyncio.Lock())
        async with lock:
            return await self.outbox.retry_delivery(
                delivery_id, expected_version=expected_version, reason=reason
            )

    async def discard_delivery(
        self, delivery_id: str, *, expected_version: int, reason: str | None = None
    ) -> dict[str, Any]:
        if (delivery := await self.outbox.get_delivery(delivery_id)) is not None:
            await self.require_legacy_owner(str(delivery["run_id"]), "delivery discard")
        lock = self._delivery_locks.setdefault(delivery_id, asyncio.Lock())
        async with lock:
            return await self.outbox.discard_delivery(
                delivery_id, expected_version=expected_version, reason=reason
            )

    async def deliver_event_once(self, event_id: str) -> dict[str, Any]:
        """Attempt unfinished channels independently; completed channels are skipped."""

        event = await self.outbox.get_event(event_id)
        if event is None:
            raise WorkflowServiceError("event_not_found", f"Workflow event not found: {event_id}")
        await self.require_legacy_owner(str(event["run_id"]), "delivery")
        results: list[dict[str, Any]] = []
        for delivery in await self.outbox.list_event_deliveries(event_id):
            if delivery["status"] in {"delivered", "discarded", "delivering"}:
                results.append(delivery)
                continue
            is_v6 = delivery.get("manifest_ref") is not None
            if is_v6 and delivery["status"] == "failed" and (
                delivery.get("next_attempt_at") is None
                or int(delivery["attempts"]) >= 5
                or float(delivery["next_attempt_at"]) > self.outbox._clock()
            ):
                results.append(delivery)
                continue
            handler = self._delivery_handlers.get(delivery["channel"])
            if handler is None:
                results.append(delivery)
                continue
            lock = self._delivery_locks.setdefault(delivery["delivery_id"], asyncio.Lock())
            async with lock:
                claimed = await self.outbox.mutate_delivery(
                    delivery["delivery_id"], action="begin", expected_version=delivery["version"]
                )
                current = claimed["delivery"]
                handler_event = event
                if is_v6:
                    handler_event = {
                        **event,
                        "delivery_aggregate": await self.outbox.delivery_aggregate(
                            str(event["run_id"])
                        ),
                    }
                try:
                    outcome = handler(handler_event, current)
                    if inspect.isawaitable(outcome):
                        outcome = await outcome
                except asyncio.CancelledError:
                    if is_v6:
                        try:
                            await self.outbox.mutate_delivery(
                                current["delivery_id"],
                                action="failed",
                                expected_version=current["version"],
                                reason="handler_cancelled",
                            )
                        except Exception:
                            pass
                    raise
                except Exception as exc:
                    failed = await self.outbox.mutate_delivery(
                        current["delivery_id"],
                        action="failed",
                        expected_version=current["version"],
                        reason="handler_exception" if is_v6 else str(exc),
                    )
                    results.append(failed["delivery"])
                else:
                    if not is_v6:
                        delivered = await self.outbox.mutate_delivery(
                            current["delivery_id"],
                            action="delivered",
                            expected_version=current["version"],
                        )
                        results.append(delivered["delivery"])
                        continue
                    try:
                        typed = normalize_v6_delivery_result(outcome)
                    except (TypeError, ValueError):
                        typed = None
                    if typed is None:
                        settled = await self.outbox.mutate_delivery(
                            current["delivery_id"],
                            action="failed",
                            expected_version=current["version"],
                            reason="handler_contract_error",
                        )
                    elif typed.disposition is DeliveryDisposition.DELIVERED:
                        settled = await self.outbox.mutate_delivery(
                            current["delivery_id"],
                            action="delivered",
                            expected_version=current["version"],
                        )
                    elif typed.disposition is DeliveryDisposition.DISCARDED_FENCED:
                        settled = await self.outbox.mutate_delivery(
                            current["delivery_id"],
                            action="discard",
                            expected_version=current["version"],
                            reason=f"fenced:{typed.reason_code}",
                        )
                    else:
                        settled = await self.outbox.mutate_delivery(
                            current["delivery_id"],
                            action="failed",
                            expected_version=current["version"],
                            reason=typed.reason_code,
                        )
                    results.append(settled["delivery"])
        aggregate = await self.outbox.delivery_aggregate(str(event["run_id"]))
        return {
            "event_id": event_id,
            "deliveries": results,
            "delivery_aggregate": aggregate,
        }

    async def delivery_aggregate(
        self, run_id: str, *, manifest_ref: str | None = None
    ) -> dict[str, Any] | None:
        await self._require_run(run_id)
        return await self.outbox.delivery_aggregate(run_id, manifest_ref=manifest_ref)

    @staticmethod
    def _evaluation_dict(record: object) -> dict[str, Any]:
        value = _plain(record)
        if isinstance(value, Mapping) and isinstance(value.get("outcome"), Mapping):
            return {
                "evaluation_id": value["evaluation_id"],
                "trace_id": value["trace_id"],
                "run_id": value.get("run_id"),
                "span_id": value.get("span_id"),
                "created_at": value["created_at"],
                **dict(value["outcome"]),
            }
        return dict(value)

    async def submit_evaluation(
        self,
        *,
        trace_id: str,
        evaluator_name: str,
        evaluator_version: str,
        evaluator_type: str,
        verdict: str,
        score: float | None = None,
        labels: Sequence[str] = (),
        explanation: str | None = None,
        evidence_refs: Sequence[str] = (),
        run_id: str | None = None,
        span_id: str | None = None,
        evaluation_id: str | None = None,
        degraded: bool = False,
        left_version_key: str | None = None,
        right_version_key: str | None = None,
    ) -> dict[str, Any]:
        evaluator_type = str(evaluator_type).strip().lower()
        common = dict(
            trace_id=trace_id,
            evaluator_name=evaluator_name,
            evaluator_version=evaluator_version,
            score=score,
            labels=tuple(labels),
            explanation=explanation,
            evidence_refs=tuple(evidence_refs),
            run_id=run_id,
            span_id=span_id,
            evaluation_id=evaluation_id,
        )
        if evaluator_type == "human":
            record = await self.evaluation_store.record_human(
                HumanEvaluationRecord(verdict=verdict, **common)
            )
        elif evaluator_type == "pairwise":
            if not left_version_key or not right_version_key:
                raise WorkflowServiceError(
                    "invalid_evaluation",
                    "Pairwise evaluation requires both version keys",
                )
            record = await self.evaluation_store.record_pairwise(
                PairwiseEvaluationRecord(
                    winner=verdict,
                    left_version_key=left_version_key,
                    right_version_key=right_version_key,
                    **common,
                )
            )
        else:
            record = await self.evaluation_store.record_evaluation(
                trace_id=trace_id,
                run_id=run_id,
                span_id=span_id,
                evaluation_id=evaluation_id,
                outcome=EvaluationOutcome(
                    evaluator_name=evaluator_name,
                    evaluator_version=evaluator_version,
                    evaluator_type=evaluator_type,
                    verdict=verdict,
                    score=score,
                    labels=tuple(labels),
                    explanation=explanation,
                    evidence_refs=tuple(evidence_refs),
                    degraded=degraded,
                ),
            )
        return self._evaluation_dict(record)

    async def compare_experiments(
        self, left_experiment_id: str, right_experiment_id: str
    ) -> dict[str, Any]:
        return _plain(
            await self.evaluation_store.compare(left_experiment_id, right_experiment_id)
        )

    async def run_detail(self, run_id: str) -> dict[str, Any]:
        run = await self._require_run(run_id)
        declared_nodes, edges = self._definition_projection(run)
        by_id = {item["id"]: item for item in declared_nodes}
        for item in await self._node_projection(run_id):
            by_id[item["id"]] = {**by_id.get(item["id"], {}), **item}
        trace = await self.trace_tree(run_id=run_id)
        history = await self.checkpoint_history(run_id)
        evaluations = [
            self._evaluation_dict(item)
            for item in await self.evaluation_store.list_evaluations(run_id=run_id)
        ]
        decisions = [
            self._decision_dict(item)
            for item in await self.human_store.list_open_decisions(run_id=run_id)
        ]
        deliveries = await self.list_deliveries(run_id=run_id)
        delivery_aggregate = await self.outbox.delivery_aggregate(run_id)
        return {
            "run_id": run_id,
            "run": self._run_summary(run),
            "nodes": list(by_id.values()),
            "edges": edges,
            "spans": trace["spans"],
            "checkpoints": history["checkpoints"],
            "evaluations": evaluations,
            "decisions": decisions,
            "deliveries": deliveries["items"],
            "delivery_aggregate": delivery_aggregate,
        }

    get_run_detail = run_detail
    get_run = run_detail


__all__ = [
    "START_SNAPSHOT_KEY",
    "WorkflowService",
    "WorkflowServiceError",
    "WorkflowStartIdentity",
]
