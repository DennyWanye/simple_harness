"""Direct RunKernel adapter for durable Companion background jobs.

The adapter deliberately has no presenter, WebSocket, TTS, usage-UI, codifier,
or growth dependency.  It observes canonical Run events and returns the single
job settlement consumed by ``CompanionRuntime``.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, fields, replace
from typing import Any, TypeAlias

from deskpet.execution.contracts import OutcomeStatus, RunEvent, thaw_json
from deskpet.harness.adapters.venues import KernelRunClient
from deskpet.harness.contracts import (
    HostContext,
    HostExtensionRefV1,
    PreparedRunContextV1,
)
from deskpet.execution.contracts import root_idempotency_key, RunRef

from .clock import ClockPort, SystemClock
from .contracts import LeaseClaim, OwnerRef
from .runtime import CompanionJobResult


_PURPOSES = frozenset({"reflection", "evaluation", "delegated_task"})
_DELEGATED_PREPARATION_SCOPES = frozenset(
    {"read", "draft", "reversible_local"}
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RESERVED_PAYLOAD_KEYS = frozenset(
    {
        "owner",
        "owner_key",
        "profile_id",
        "profile_generation",
        "job_id",
        "purpose",
        "evidence_ids",
        "capture_growth",
        "persistence_required",
        "prepared",
        "prepared_run_context",
        "delegated_grants",
        "canonical_result",
        "postprocessor",
        "prepared_context_factory",
        "text_factory",
        "result_postprocessor",
        "terminal_commit_extension",
        "terminal_commit_extensions",
    }
)


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _required(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _required_hash(value: object, name: str) -> str:
    normalized = _required(value, name)
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return normalized


@dataclass(frozen=True, slots=True)
class DelegatedPreparationGrant:
    """A durable grant that can prepare work but never authorize risky effects."""

    grant_id: str
    scope: str
    target: str
    expires_at: str
    version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "grant_id", _required(self.grant_id, "grant_id"))
        scope = _required(self.scope, "scope")
        if scope not in _DELEGATED_PREPARATION_SCOPES:
            raise ValueError("delegated_preparation_grant_scope_forbidden")
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "target", _required(self.target, "target"))
        object.__setattr__(
            self, "expires_at", _required(self.expires_at, "expires_at")
        )
        if isinstance(self.version, bool) or self.version < 1:
            raise ValueError("delegated_preparation_grant_version_invalid")

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> "DelegatedPreparationGrant":
        if set(value) != {
            "grant_id",
            "scope",
            "target",
            "expires_at",
            "version",
        }:
            raise ValueError("delegated_preparation_grant_shape_invalid")
        return cls(
            grant_id=str(value["grant_id"]),
            scope=str(value["scope"]),
            target=str(value["target"]),
            expires_at=str(value["expires_at"]),
            version=int(value["version"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "scope": self.scope,
            "target": self.target,
            "expires_at": self.expires_at,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class ExternalDecisionWait:
    execution_run_id: str
    execution_session_id: str
    decision_id: str
    decision_nonce: str
    decision_version: int
    decision_kind: str
    call_id: str
    effect_id: str
    tool_name: str
    args_hash: str
    capability_hash: str
    scope_hash: str

    @classmethod
    def from_event(
        cls,
        *,
        execution_run_id: str,
        execution_session_id: str,
        payload: Mapping[str, Any],
    ) -> "ExternalDecisionWait":
        kind = _required(payload.get("kind"), "decision_kind")
        if kind != "permission":
            raise ValueError("delegated_external_decision_must_be_permission")
        version = payload.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise ValueError("delegated_external_decision_version_invalid")
        payload_run_id = _required(payload.get("run_id"), "decision_run_id")
        if payload_run_id != execution_run_id:
            raise ValueError("delegated_external_decision_run_mismatch")
        return cls(
            execution_run_id=execution_run_id,
            execution_session_id=_required(
                execution_session_id, "execution_session_id"
            ),
            decision_id=_required(payload.get("decision_id"), "decision_id"),
            decision_nonce=_required(payload.get("nonce"), "decision_nonce"),
            decision_version=version,
            decision_kind=kind,
            call_id=_required(payload.get("call_id"), "call_id"),
            effect_id=_required(payload.get("effect_id"), "effect_id"),
            tool_name=_required(payload.get("tool_name"), "tool_name"),
            args_hash=_required_hash(payload.get("args_hash"), "args_hash"),
            capability_hash=_required_hash(
                payload.get("capability_hash"), "capability_hash"
            ),
            scope_hash=_required_hash(payload.get("scope_hash"), "scope_hash"),
        )


@dataclass(frozen=True, slots=True)
class CompanionPreparedRunFacts:
    """Host-issued facts which JSON request payloads cannot replace."""

    owner: OwnerRef
    owner_key: str
    job_id: str
    purpose: str
    evidence_ids: tuple[str, ...]
    delegated_grants: tuple[DelegatedPreparationGrant, ...] = ()
    persistence_required: bool = True
    capture_growth: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_key", _required(self.owner_key, "owner_key"))
        object.__setattr__(self, "job_id", _required(self.job_id, "job_id"))
        purpose = _required(self.purpose, "purpose")
        if purpose not in _PURPOSES:
            raise ValueError("companion_background_purpose_invalid")
        object.__setattr__(self, "purpose", purpose)
        evidence_ids = tuple(_required(item, "evidence_id") for item in self.evidence_ids)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("companion_background_evidence_duplicate")
        object.__setattr__(self, "evidence_ids", evidence_ids)
        grants = tuple(self.delegated_grants)
        if self.purpose != "delegated_task" and grants:
            raise ValueError("delegated_preparation_grant_purpose_invalid")
        if len({item.grant_id for item in grants}) != len(grants):
            raise ValueError("delegated_preparation_grant_duplicate")
        object.__setattr__(self, "delegated_grants", grants)
        if not self.persistence_required:
            raise ValueError("companion_background_requires_persistence")
        if self.purpose in {"reflection", "evaluation"} and self.capture_growth:
            raise ValueError("companion_background_secondary_growth_forbidden")
        if self.schema_version != 1:
            raise ValueError("companion_background_schema_unsupported")

    @property
    def descriptor(self) -> HostExtensionRefV1:
        facts = {
            "schema_version": self.schema_version,
            "owner": {
                "profile_id": self.owner.profile_id,
                "profile_generation": self.owner.profile_generation,
            },
            "owner_key": self.owner_key,
            "job_id": self.job_id,
            "purpose": self.purpose,
            "evidence_ids": list(self.evidence_ids),
            "delegated_grants": [
                item.to_dict() for item in self.delegated_grants
            ],
            "persistence_required": self.persistence_required,
            "capture_growth": self.capture_growth,
        }
        return HostExtensionRefV1(
            kind="deskpet.companion.background_run.v1",
            ref=(
                f"companion-job:{self.owner.profile_id}:"
                f"{self.owner.profile_generation}:{self.job_id}"
            ),
            content_hash=_canonical_hash(facts),
        )

    def prepared_context(self) -> PreparedRunContextV1:
        descriptor = self.descriptor
        return PreparedRunContextV1(
            persistence_required=True,
            host_extensions={descriptor.kind: descriptor},
        )


@dataclass(frozen=True, slots=True)
class BackgroundRunCollection:
    run_id: str
    status: str
    result_ref: str
    result_hash: str
    reason_code: str
    budget_actual_tokens: int
    budget_actual_ms: int
    failure_context: Mapping[str, Any] | None = None

    def to_job_result(self) -> CompanionJobResult:
        return CompanionJobResult(
            status=self.status,
            result_ref=self.result_ref,
            result_hash=self.result_hash,
            reason_code=self.reason_code,
            budget_actual_tokens=self.budget_actual_tokens,
            budget_actual_ms=self.budget_actual_ms,
            failure_context=self.failure_context,
        )


@dataclass(frozen=True, slots=True)
class BackgroundRunCanonicalResultV1:
    """Detached host view of one terminal background Run result.

    ``result_ref`` and ``result_hash`` identify the same canonical body used by
    the existing job-settlement contract.  ``structured_result`` is only a
    derived convenience view: the host must still validate it against the
    purpose-specific schema before granting any authority.  A postprocessor
    must durably deduplicate the stable ``result_ref``/``result_hash`` pair
    because RunKernel recovery may replay the same committed terminal after a
    process crash.
    """

    owner: OwnerRef
    job_id: str
    claim_owner: str
    claim_epoch: int
    purpose: str
    evidence_ids: tuple[str, ...]
    execution_run_id: str
    execution_session_id: str
    status: str
    result_ref: str
    result_hash: str
    text: str
    structured_result: Mapping[str, Any] | None
    terminal_payload: Mapping[str, Any]
    artifact_refs: tuple[str, ...]
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class BackgroundRunDurableResultV1:
    """Host-normalized result body that the Runtime must persist on the job.

    Most background stages retain the RunKernel result identity.  Stages such
    as ``reminder_draft`` need the generated local artifact itself to survive a
    restart before a dependent outbox row can be delivered.  A trusted
    postprocessor may therefore replace only the settlement identity; it
    cannot change status, usage, or any prepared host facts.
    """

    result_ref: str
    result_hash: str
    reason_code: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "result_ref",
            _required(self.result_ref, "durable_result_ref"),
        )
        object.__setattr__(
            self,
            "result_hash",
            _required_hash(self.result_hash, "durable_result_hash"),
        )
        object.__setattr__(
            self,
            "reason_code",
            _required(self.reason_code, "durable_result_reason_code"),
        )
        if self.schema_version != 1:
            raise ValueError("durable_result_schema_unsupported")


BackgroundRunResultPostprocessor: TypeAlias = Callable[
    [BackgroundRunCanonicalResultV1],
    (
        BackgroundRunDurableResultV1
        | None
        | Awaitable[BackgroundRunDurableResultV1 | None]
    ),
]
BackgroundPreparedContextFactory: TypeAlias = Callable[
    [
        OwnerRef,
        LeaseClaim,
        CompanionPreparedRunFacts,
        PreparedRunContextV1,
        str,
    ],
    PreparedRunContextV1 | Awaitable[PreparedRunContextV1],
]
BackgroundTextFactory: TypeAlias = Callable[
    [
        OwnerRef,
        LeaseClaim,
        CompanionPreparedRunFacts,
        str,
    ],
    str | Awaitable[str],
]


def _validate_extended_prepared_context(
    *,
    base: PreparedRunContextV1,
    extended: object,
) -> PreparedRunContextV1:
    """Allow a factory to append terminal callbacks without replacing facts."""

    if not isinstance(extended, PreparedRunContextV1):
        raise TypeError("companion_background_prepared_context_invalid")
    for item in fields(PreparedRunContextV1):
        if item.name == "terminal_commit_extensions":
            continue
        if getattr(extended, item.name) != getattr(base, item.name):
            raise ValueError(
                "companion_background_prepared_fact_override:"
                + item.name
            )
    base_extensions = base.terminal_commit_extensions
    extended_extensions = extended.terminal_commit_extensions
    if len(extended_extensions) < len(base_extensions) or any(
        current is not original
        for current, original in zip(
            extended_extensions[: len(base_extensions)],
            base_extensions,
            strict=True,
        )
    ):
        raise ValueError(
            "companion_background_terminal_extension_replaced"
        )
    return extended


def _structured_result_view(
    *,
    text: str,
    terminal_payload: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Return a detached strict-JSON object without changing result hashing."""

    candidate: object | None = None
    for key in ("structured_result", "result"):
        value = terminal_payload.get(key)
        if isinstance(value, Mapping):
            candidate = value
            break
    if candidate is None and text.strip():
        serialized = text.strip()
        lines = serialized.splitlines()
        if (
            len(lines) >= 3
            and lines[0].strip().lower() in {"```", "```json"}
            and lines[-1].strip() == "```"
        ):
            serialized = "\n".join(lines[1:-1]).strip()
        try:
            decoded = json.loads(serialized)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, Mapping):
            candidate = decoded
    if candidate is None:
        return None
    # Canonical round-trip provides a detached JSON-only value so a host
    # postprocessor cannot mutate the terminal payload that was hashed.
    detached = json.loads(
        json.dumps(
            dict(candidate),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    assert isinstance(detached, dict)
    return detached


class BackgroundRunAdapter:
    """Companion job handler backed directly by ``KernelRunClient``."""

    def __init__(
        self,
        *,
        client: KernelRunClient,
        store: Any,
        host_factory: Callable[
            [OwnerRef, LeaseClaim],
            HostContext | Awaitable[HostContext],
        ],
        prepared_context_factory: BackgroundPreparedContextFactory | None = None,
        text_factory: BackgroundTextFactory | None = None,
        result_postprocessor: BackgroundRunResultPostprocessor | None = None,
        clock: ClockPort | None = None,
    ) -> None:
        self._client = client
        self._store = store
        self._host_factory = host_factory
        self._prepared_context_factory = prepared_context_factory
        self._text_factory = text_factory
        self._result_postprocessor = result_postprocessor
        self._clock = clock or SystemClock()
        self._completed: dict[tuple[str, int, str, int], CompanionJobResult] = {}
        self._inflight: dict[
            tuple[str, int, str, int], asyncio.Task[CompanionJobResult]
        ] = {}
        self._lock = asyncio.Lock()

    async def __call__(
        self, owner: OwnerRef, claim: LeaseClaim
    ) -> CompanionJobResult:
        key = (
            owner.profile_id,
            owner.profile_generation,
            claim.item_id,
            claim.claim_epoch,
        )
        async with self._lock:
            completed = self._completed.get(key)
            if completed is not None:
                return completed
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(
                    self._run(owner, claim),
                    name=f"companion-background-run:{claim.item_id}",
                )
                self._inflight[key] = task
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            # The Runtime's budget cancellation is authoritative for this
            # attempt.  Propagate it through the single-flight task so _run()
            # can cancel the durable Run before the job becomes retryable.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        finally:
            async with self._lock:
                if self._inflight.get(key) is task and task.done():
                    self._inflight.pop(key, None)
        async with self._lock:
            existing = self._completed.setdefault(key, result)
        return existing

    async def _run(
        self, owner: OwnerRef, claim: LeaseClaim
    ) -> CompanionJobResult:
        row = self._store.get_job(owner, job_id=claim.item_id)
        payload = dict(claim.payload)
        purpose = str(payload.get("purpose") or row.get("kind") or "").strip()
        raw_evidence = payload.get("evidence_ids", ())
        if isinstance(raw_evidence, (str, bytes)) or not isinstance(
            raw_evidence, Sequence
        ):
            raise ValueError("companion_background_evidence_invalid")
        request_payload = payload.get("request_payload", {})
        if not isinstance(request_payload, Mapping):
            raise ValueError("companion_background_request_payload_invalid")
        conflicting = _RESERVED_PAYLOAD_KEYS.intersection(request_payload)
        if conflicting:
            raise ValueError(
                "companion_background_host_fact_override:"
                + ",".join(sorted(conflicting))
            )
        raw_grants = payload.get("delegated_grants", ())
        if isinstance(raw_grants, (str, bytes)) or not isinstance(
            raw_grants, Sequence
        ):
            raise ValueError("delegated_preparation_grants_invalid")
        grants: list[DelegatedPreparationGrant] = []
        for raw_grant in raw_grants:
            if not isinstance(raw_grant, Mapping):
                raise ValueError("delegated_preparation_grant_shape_invalid")
            grants.append(DelegatedPreparationGrant.from_mapping(raw_grant))
        facts = CompanionPreparedRunFacts(
            owner=owner,
            owner_key=_required(payload.get("owner_key"), "owner_key"),
            job_id=claim.item_id,
            purpose=purpose,
            evidence_ids=tuple(str(item) for item in raw_evidence),
            delegated_grants=tuple(grants),
            capture_growth=bool(payload.get("capture_growth", False)),
        )
        text = _required(payload.get("text") or payload.get("prompt"), "text")
        if self._text_factory is not None:
            resolved_text = self._text_factory(owner, claim, facts, text)
            if inspect.isawaitable(resolved_text):
                resolved_text = await resolved_text
            text = _required(resolved_text, "text")
        prior_failure = payload.get("replan_failure")
        if isinstance(prior_failure, Mapping):
            text = (
                f"{text}\n\n上一次模型执行失败。请先吸取下面的失败原因，"
                "重新规划这次判断；不要重复导致失败的做法，然后仍按本任务"
                "要求的严格 JSON 输出：\n"
                + json.dumps(
                    dict(prior_failure),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
        request_id = (
            f"companion-background:{owner.profile_id}:"
            f"{owner.profile_generation}:{claim.item_id}:"
            f"attempt:{claim.attempt}"
        )
        host = self._host_factory(owner, claim)
        if inspect.isawaitable(host):
            host = await host
        if not isinstance(host, HostContext):
            raise TypeError("companion_background_host_invalid")
        execution_key = root_idempotency_key(
            host.session_id,
            request_id,
            request_id,
        )
        execution_ref = RunRef(
            uuid.uuid5(uuid.NAMESPACE_URL, f"deskpet:{execution_key}").hex,
            host.session_id,
        )
        prepared_context = facts.prepared_context()
        if self._prepared_context_factory is not None:
            extended = self._prepared_context_factory(
                owner,
                claim,
                facts,
                prepared_context,
                execution_ref.run_id,
            )
            if inspect.isawaitable(extended):
                extended = await extended
            prepared_context = _validate_extended_prepared_context(
                base=prepared_context,
                extended=extended,
            )
        started = self._clock.monotonic()
        handle = await self._client.start(
            {
                "text": text,
                "request_id": request_id,
                "turn_id": request_id,
                "venue": "background",
                "mode": purpose,
                "workspace_context": False,
                "proposed_tools": (),
                "canonical_messages": ({"role": "user", "content": text},),
                "payload": {
                    **dict(request_payload),
                    "companion_background": {
                        "schema_version": 1,
                        "job_id": claim.item_id,
                        "purpose": purpose,
                        "evidence_ids": list(facts.evidence_ids),
                        "capture_growth": facts.capture_growth,
                    },
                },
            },
            host,
            prepared=prepared_context,
        )
        try:
            collected = await self._collect(
                handle.events,
                run_id=handle.run_id,
                job_id=claim.item_id,
                owner=owner,
                claim=claim,
                execution_session_id=host.session_id,
                purpose=purpose,
                evidence_ids=facts.evidence_ids,
                reserved_tokens=int(row["budget_reserved_tokens"]),
                reserved_ms=int(row["budget_reserved_ms"]),
                started=started,
            )
        except asyncio.CancelledError:
            # The Runtime owns the wall-clock budget.  If that budget expires,
            # the durable Run must be cancelled as well; merely closing the
            # observer would leave a provider Run executing after its job was
            # requeued and could commit a stale candidate later.
            await handle.cancel("companion_background_runtime_cancelled")
            raise
        return collected.to_job_result()

    async def _collect(
        self,
        events,
        *,
        run_id: str,
        job_id: str,
        owner: OwnerRef,
        claim: LeaseClaim,
        execution_session_id: str,
        purpose: str,
        evidence_ids: tuple[str, ...],
        reserved_tokens: int,
        reserved_ms: int,
        started: float,
    ) -> BackgroundRunCollection:
        chunks: list[str] = []
        terminal: RunEvent | None = None
        usage_tokens: int | None = None
        async for event in events:
            payload = thaw_json(event.candidate.payload)
            assert isinstance(payload, dict)
            if event.kind in {"token", "transcript"}:
                content = payload.get("content", payload.get("text"))
                if isinstance(content, str):
                    chunks.append(content)
            usage = payload.get("usage", payload.get("provider_usage"))
            if isinstance(usage, Mapping):
                raw_total = usage.get("total_tokens")
                has_split_usage = (
                    "prompt_tokens" in usage or "completion_tokens" in usage
                )
                if raw_total is None and has_split_usage:
                    raw_total = int(usage.get("prompt_tokens") or 0) + int(
                        usage.get("completion_tokens") or 0
                    )
                if raw_total is not None:
                    usage_tokens = max(0, int(raw_total))
            if event.kind == "decision" and event.status is OutcomeStatus.WAITING:
                if purpose != "delegated_task":
                    raise RuntimeError(
                        "companion_background_decision_requires_delegated_task"
                    )
                wait = ExternalDecisionWait.from_event(
                    execution_run_id=run_id,
                    execution_session_id=execution_session_id,
                    payload=payload,
                )
                park = getattr(self._store, "park_job_waiting_decision", None)
                if not callable(park):
                    raise RuntimeError(
                        "companion_job_waiting_decision_store_unavailable"
                    )
                row = park(
                    owner,
                    job_id=job_id,
                    claim_owner=claim.claim_owner,
                    claim_epoch=claim.claim_epoch,
                    execution_run_id=wait.execution_run_id,
                    execution_session_id=wait.execution_session_id,
                    decision_id=wait.decision_id,
                    decision_nonce=wait.decision_nonce,
                    decision_version=wait.decision_version,
                    decision_kind=wait.decision_kind,
                    call_id=wait.call_id,
                    effect_id=wait.effect_id,
                    tool_name=wait.tool_name,
                    args_hash=wait.args_hash,
                    capability_hash=wait.capability_hash,
                    scope_hash=wait.scope_hash,
                )
                wait_result = {
                    "schema_version": 1,
                    "job_id": job_id,
                    "execution_run_id": run_id,
                    "decision_id": wait.decision_id,
                    "wait_fingerprint": row["wait_fingerprint"],
                }
                close = getattr(events, "aclose", None)
                if callable(close):
                    await close()
                return BackgroundRunCollection(
                    run_id=run_id,
                    status="waiting_decision",
                    result_ref=(
                        f"companion-wait:{job_id}:{run_id}:{wait.decision_id}"
                    ),
                    result_hash=_canonical_hash(wait_result),
                    reason_code="background_run_waiting_execution_decision",
                    budget_actual_tokens=0,
                    budget_actual_ms=max(
                        0, int((self._clock.monotonic() - started) * 1000)
                    ),
                )
            if event.status in {
                OutcomeStatus.SUCCEEDED,
                OutcomeStatus.FAILED,
                OutcomeStatus.CANCELLED,
            }:
                terminal = event
        if terminal is None:
            raise RuntimeError("companion_background_terminal_missing")
        payload = thaw_json(terminal.candidate.payload)
        assert isinstance(payload, dict)
        terminal_text = payload.get("text", payload.get("content"))
        if isinstance(terminal_text, str):
            chunks = [terminal_text]
        result_body = {
            "schema_version": 1,
            "run_id": run_id,
            "job_id": job_id,
            "status": terminal.status.value,
            "text": "".join(chunks),
            "payload": payload,
            "error": (
                None
                if terminal.candidate.error is None
                else thaw_json(terminal.candidate.error)
            ),
            "artifact_refs": list(terminal.artifact_refs),
        }
        succeeded = terminal.status is OutcomeStatus.SUCCEEDED
        usage_missing = usage_tokens is None
        elapsed_ms = max(0, int((self._clock.monotonic() - started) * 1000))
        collection = BackgroundRunCollection(
            run_id=run_id,
            status="succeeded" if succeeded else "failed",
            result_ref=f"companion-background:{job_id}:{run_id}",
            result_hash=_canonical_hash(result_body),
            reason_code=(
                "background_run_succeeded"
                if succeeded
                else f"background_run_{terminal.status.value}"
            ),
            budget_actual_tokens=(
                reserved_tokens if usage_missing else int(usage_tokens)
            ),
            budget_actual_ms=(
                max(reserved_ms, elapsed_ms) if usage_missing else elapsed_ms
            ),
            failure_context=(
                None
                if succeeded
                else {
                    "execution_run_id": run_id,
                    "terminal_status": terminal.status.value,
                    "terminal_error": result_body["error"],
                    "terminal_payload": payload,
                }
            ),
        )
        if succeeded and self._result_postprocessor is not None:
            structured_result = _structured_result_view(
                text=result_body["text"],
                terminal_payload=payload,
            )
            postprocess_result = self._result_postprocessor(
                BackgroundRunCanonicalResultV1(
                    owner=owner,
                    job_id=job_id,
                    claim_owner=claim.claim_owner,
                    claim_epoch=claim.claim_epoch,
                    purpose=purpose,
                    evidence_ids=evidence_ids,
                    execution_run_id=run_id,
                    execution_session_id=execution_session_id,
                    status=terminal.status.value,
                    result_ref=collection.result_ref,
                    result_hash=collection.result_hash,
                    text=result_body["text"],
                    structured_result=structured_result,
                    terminal_payload=json.loads(
                        json.dumps(
                            payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                        )
                    ),
                    artifact_refs=tuple(terminal.artifact_refs),
                )
            )
            if inspect.isawaitable(postprocess_result):
                postprocess_result = await postprocess_result
            if postprocess_result is not None:
                if not isinstance(
                    postprocess_result,
                    BackgroundRunDurableResultV1,
                ):
                    raise TypeError(
                        "companion_background_durable_result_invalid"
                    )
                collection = replace(
                    collection,
                    result_ref=postprocess_result.result_ref,
                    result_hash=postprocess_result.result_hash,
                    reason_code=postprocess_result.reason_code,
                )
        return collection


__all__ = [
    "BackgroundRunAdapter",
    "BackgroundRunCanonicalResultV1",
    "BackgroundRunCollection",
    "BackgroundRunDurableResultV1",
    "BackgroundPreparedContextFactory",
    "BackgroundRunResultPostprocessor",
    "CompanionPreparedRunFacts",
    "DelegatedPreparationGrant",
    "ExternalDecisionWait",
]
