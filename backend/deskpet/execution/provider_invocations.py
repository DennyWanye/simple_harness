from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import inspect
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Protocol

from deskpet.execution.dispatch import (
    DispatchIdentity,
    DispatchNotStarted,
    DispatchOperation,
    DispatchStartedAck,
    DispatchStartUnknown,
    create_dispatch_operation,
)
from deskpet.security import TraceRedactor, redact_sensitive_text

logger = logging.getLogger(__name__)

_PROVIDER_INPUT_MAX_BYTES = 64 * 1024
_PROVIDER_INPUT_RECENT_MESSAGES = 12
_PROVIDER_INPUT_STRING_LIMIT = 6_000


class ProviderFenceLease(Protocol):
    run_id: str
    revocation_epoch: int

    async def release(self) -> None: ...


class ProviderInvocationUnitOfWork(Protocol):
    async def claim_provider_invocation(
        self, record: Any, **kwargs: Any
    ) -> Any: ...
    async def complete_provider_invocation(
        self,
        run_id: str,
        invocation_id: str,
        **kwargs: Any,
    ) -> Any: ...
    async def fail_provider_invocation(
        self,
        run_id: str,
        invocation_id: str,
        **kwargs: Any,
    ) -> Any: ...
    async def mark_unknown_provider_invocation(
        self,
        run_id: str,
        invocation_id: str,
        **kwargs: Any,
    ) -> Any: ...
    async def read_provider_invocation(
        self,
        run_id: str,
        invocation_id: str,
    ) -> Any | None: ...
    async def read_provider_invocation_outcome(
        self,
        run_id: str,
        invocation_id: str,
    ) -> Any | None: ...


class ProviderDispatchUnknownError(RuntimeError):
    """A physical request may have completed and must not be retried blindly."""


class ProviderDispatchNotSentError(RuntimeError):
    """The transport proved that no upstream connection was established."""


class ProviderDispatchRetryableResponseError(RuntimeError):
    """The provider returned a definite transient HTTP response."""

    def __init__(self, status_code: int) -> None:
        self.status_code = int(status_code)
        super().__init__(f"provider_retryable_http_status:{self.status_code}")


@dataclass(frozen=True, slots=True)
class ProviderAttemptSnapshot:
    run_id: str
    provider_id: str
    model_id: str
    adapter_id: str
    idempotency_group_id: str
    attempt_ordinal: int
    provider_chain_slot: int
    retry_ordinal: int
    fallback_ordinal: int
    stream_epoch: str
    request_payload: Any
    invoke: Callable[[], Awaitable[Any]] = field(repr=False, compare=False)
    policy_snapshot: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PreparedProviderDispatch:
    identity: DispatchIdentity
    request_hash: str
    claim_hash: str
    snapshot: ProviderAttemptSnapshot
    _operation: DispatchOperation | None = field(default=None, init=False, repr=False)

    def start_dispatch(self) -> DispatchOperation:
        if self._operation is not None:
            raise RuntimeError("provider dispatch may only be started once")
        self._operation = create_dispatch_operation(
            self.identity,
            self.snapshot.invoke,
        )
        return self._operation


@dataclass(slots=True)
class ClaimedProviderDispatch:
    prepared: PreparedProviderDispatch
    claim_record: Any
    fence_lease: ProviderFenceLease
    operation: DispatchOperation | None = None
    start_resolution: (
        DispatchStartedAck | DispatchNotStarted | DispatchStartUnknown | None
    ) = None

    @property
    def identity(self) -> DispatchIdentity:
        return self.prepared.identity

    @property
    def request_hash(self) -> str:
        return self.prepared.request_hash


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _required(source: Any, name: str) -> str:
    value = str(_value(source, name, "") or "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _ordinal(source: Any, name: str, default: int = 0) -> int:
    value = _value(source, name, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _exception_code(exc: BaseException) -> str:
    return str(
        getattr(exc, "code", "")
        or getattr(exc, "error_code", "")
        or ""
    )


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump(mode="json"))
    as_dict = getattr(value, "dict", None)
    if callable(as_dict):
        return _jsonable(as_dict())
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"type": type(value).__name__, "repr": repr(value)}


def _bounded_observability_value(
    value: Any,
    *,
    depth: int = 0,
) -> Any:
    """Bound a redacted value before it enters the observability ledger."""

    if depth >= 8:
        return "[TRUNCATED:DEPTH]"
    if isinstance(value, str):
        if len(value) <= _PROVIDER_INPUT_STRING_LIMIT:
            return value
        return (
            value[:_PROVIDER_INPUT_STRING_LIMIT]
            + f"\n[TRUNCATED:{len(value) - _PROVIDER_INPUT_STRING_LIMIT} chars]"
        )
    if isinstance(value, Mapping):
        items = list(value.items())
        projected = {
            str(key): _bounded_observability_value(item, depth=depth + 1)
            for key, item in items[:80]
        }
        if len(items) > 80:
            projected["_truncated_fields"] = len(items) - 80
        return projected
    if isinstance(value, (list, tuple)):
        projected = [
            _bounded_observability_value(item, depth=depth + 1)
            for item in value[:80]
        ]
        if len(value) > 80:
            projected.append({"_truncated_items": len(value) - 80})
        return projected
    return value


def _redact_provider_input_strings(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): _redact_provider_input_strings(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_provider_input_strings(item) for item in value]
    return value


def build_provider_input_projection(request_payload: Any) -> dict[str, Any]:
    """Create the immutable, redacted input shown by Harness Inspector.

    Small requests retain their complete redacted payload. Large prompts are
    represented by the first system/developer message plus the most recent
    messages and tool names, so observability cannot multiply a near-window
    context into an unbounded durable database.
    """

    redacted = _redact_provider_input_strings(
        TraceRedactor().redact(_jsonable(request_payload))
    )
    raw_size = len(_canonical_bytes(redacted))
    if raw_size <= _PROVIDER_INPUT_MAX_BYTES:
        return {
            "schema_version": 1,
            "mode": "full",
            "size_bytes": raw_size,
            "payload": redacted,
        }

    source = redacted if isinstance(redacted, Mapping) else {}
    raw_messages = source.get("messages", [])
    messages = raw_messages if isinstance(raw_messages, list) else []
    leading_indexes = [
        index
        for index, message in enumerate(messages)
        if isinstance(message, Mapping)
        and str(message.get("role") or "") in {"system", "developer"}
    ][:1]
    recent_start = max(0, len(messages) - _PROVIDER_INPUT_RECENT_MESSAGES)
    included_indexes = sorted(
        set((*leading_indexes, *range(recent_start, len(messages))))
    )
    included_messages = [
        {
            "_message_index": index,
            **(
                _bounded_observability_value(messages[index])
                if isinstance(messages[index], Mapping)
                else {"content": _bounded_observability_value(messages[index])}
            ),
        }
        for index in included_indexes
    ]
    tools = source.get("tools")
    tool_items = tools if isinstance(tools, list) else []
    tool_names: list[str] = []
    for tool in tool_items:
        if not isinstance(tool, Mapping):
            continue
        function = tool.get("function")
        function = function if isinstance(function, Mapping) else {}
        name = str(function.get("name") or tool.get("name") or "").strip()
        if name:
            tool_names.append(name)

    return {
        "schema_version": 1,
        "mode": "truncated",
        "size_bytes": raw_size,
        "message_count": len(messages),
        "included_message_indexes": included_indexes,
        "omitted_message_count": max(0, len(messages) - len(included_indexes)),
        "messages": included_messages,
        "tool_count": len(tool_items),
        "tool_names": tool_names[:200],
        "model": source.get("model"),
        "purpose": source.get("purpose"),
        "stream": source.get("stream"),
    }


async def _release_lease(lease: ProviderFenceLease) -> None:
    release = getattr(lease, "release", None)
    if callable(release):
        result = release()
        if inspect.isawaitable(result):
            await result


class ProviderInvocationCoordinator:
    """The single durable owner of one physical provider dispatch."""

    def __init__(
        self,
        uow: ProviderInvocationUnitOfWork,
        *,
        fence_reacquirer: Callable[
            [str], Awaitable[ProviderFenceLease]
        ] | None = None,
        claim_owner: str = "provider-invocation-coordinator",
        fault_script: Any | None = None,
    ) -> None:
        self._uow = uow
        self._fence_reacquirer = fence_reacquirer
        self._claim_owner = str(claim_owner or "").strip()
        self._fault_script = fault_script
        if not self._claim_owner:
            raise ValueError("claim_owner is required")

    def prepare_attempt(self, snapshot: Any) -> PreparedProviderDispatch:
        normalized = ProviderAttemptSnapshot(
            run_id=_required(snapshot, "run_id"),
            provider_id=_required(snapshot, "provider_id"),
            model_id=_required(snapshot, "model_id"),
            adapter_id=_required(snapshot, "adapter_id"),
            idempotency_group_id=_required(snapshot, "idempotency_group_id"),
            attempt_ordinal=_ordinal(snapshot, "attempt_ordinal"),
            provider_chain_slot=_ordinal(snapshot, "provider_chain_slot"),
            retry_ordinal=_ordinal(snapshot, "retry_ordinal"),
            fallback_ordinal=_ordinal(snapshot, "fallback_ordinal"),
            stream_epoch=_required(snapshot, "stream_epoch"),
            request_payload=_value(snapshot, "request_payload"),
            invoke=_value(snapshot, "invoke"),
            policy_snapshot=dict(_value(snapshot, "policy_snapshot", {}) or {}),
        )
        if not callable(normalized.invoke):
            raise ValueError("invoke must be callable")
        request_hash = _hash(normalized.request_payload)
        identity_seed = {
            "run_id": normalized.run_id,
            "idempotency_group_id": normalized.idempotency_group_id,
            "attempt_ordinal": normalized.attempt_ordinal,
            "provider_chain_slot": normalized.provider_chain_slot,
            "retry_ordinal": normalized.retry_ordinal,
            "fallback_ordinal": normalized.fallback_ordinal,
            "provider_id": normalized.provider_id,
            "model_id": normalized.model_id,
            "adapter_id": normalized.adapter_id,
            "request_hash": request_hash,
        }
        invocation_id = hashlib.sha256(
            b"provider-invocation-v1|" + _canonical_bytes(identity_seed)
        ).hexdigest()
        identity = DispatchIdentity(
            run_id=normalized.run_id,
            invocation_id=invocation_id,
            provider_id=normalized.provider_id,
            model_id=normalized.model_id,
            adapter_id=normalized.adapter_id,
            stream_epoch=normalized.stream_epoch,
        )
        return PreparedProviderDispatch(
            identity=identity,
            request_hash=request_hash,
            claim_hash=_hash(
                {
                    **identity_seed,
                    "invocation_id": invocation_id,
                    "policy_snapshot": normalized.policy_snapshot,
                }
            ),
            snapshot=normalized,
        )

    async def claim_prepared(
        self,
        dispatch: PreparedProviderDispatch,
        fence_lease: ProviderFenceLease,
    ) -> ClaimedProviderDispatch:
        if fence_lease.run_id != dispatch.identity.run_id:
            raise RuntimeError("provider fence lease run mismatch")
        record = self._build_claim_record(dispatch, fence_lease)
        input_projection = build_provider_input_projection(
            dispatch.snapshot.request_payload
        )
        input_payload_json = _canonical_bytes(input_projection).decode("utf-8")
        input_kwargs = {
            "input_payload_json": input_payload_json,
            "input_payload_hash": hashlib.sha256(
                input_payload_json.encode("utf-8")
            ).hexdigest(),
            "input_created_at": float(record.claimed_at),
        }
        try:
            claimed = await self._uow.claim_provider_invocation(
                record, **input_kwargs
            )
        except BaseException as exc:
            if _exception_code(exc) != "write_outcome_unknown":
                await _release_lease(fence_lease)
                raise
            try:
                claimed = await self._uow.read_provider_invocation(
                    dispatch.identity.run_id,
                    dispatch.identity.invocation_id,
                )
            except BaseException:
                await _release_lease(fence_lease)
                raise
            if claimed is None:
                # The exact transaction is proven absent.  Retrying the same
                # immutable claim is safe and still precedes any transport.
                try:
                    claimed = await self._uow.claim_provider_invocation(
                        record, **input_kwargs
                    )
                except BaseException:
                    await _release_lease(fence_lease)
                    raise
            elif not self._claim_matches(claimed, dispatch, fence_lease):
                await _release_lease(fence_lease)
                raise RuntimeError("provider_invocation_claim_conflict") from exc
        if not self._claim_matches(claimed, dispatch, fence_lease):
            await _release_lease(fence_lease)
            raise RuntimeError("provider_invocation_claim_conflict")
        return ClaimedProviderDispatch(
            prepared=dispatch,
            claim_record=claimed,
            fence_lease=fence_lease,
        )

    async def start_and_ack(
        self,
        dispatch: ClaimedProviderDispatch,
        deadline: float,
    ) -> DispatchStartedAck | DispatchNotStarted | DispatchStartUnknown:
        if dispatch.operation is not None:
            raise RuntimeError("claimed provider dispatch already started")
        if deadline <= 0:
            raise ValueError("deadline must be positive")
        if self._fault_script is not None:
            fault = await self._fault_script.consume_main(
                run_id=dispatch.identity.run_id,
                session_id=(
                    str(
                        dispatch.prepared.snapshot.policy_snapshot.get(
                            "session_id"
                        )
                        or ""
                    )
                    or None
                ),
                root_run_id=(
                    str(
                        dispatch.prepared.snapshot.policy_snapshot.get(
                            "root_run_id"
                        )
                        or ""
                    )
                    or None
                ),
                parent_run_id=(
                    str(
                        dispatch.prepared.snapshot.policy_snapshot.get(
                            "parent_run_id"
                        )
                        or ""
                    )
                    or None
                ),
                profile_key=(
                    str(
                        dispatch.prepared.snapshot.policy_snapshot.get(
                            "profile_key"
                        )
                        or ""
                    )
                    or None
                ),
                purpose=str(
                    dispatch.prepared.snapshot.policy_snapshot.get("purpose")
                    or ""
                ),
            )
            if fault is not None:
                error = fault.to_exception()
                dispatch.start_resolution = DispatchNotStarted(
                    dispatch.identity,
                    f"provider_fault_injected:{fault.injection_ref}",
                )
                try:
                    await self._fail(
                        dispatch,
                        error_class="provider_fault_injected",
                        reason=f"provider_fault_injected:{fault.injection_ref}",
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
                finally:
                    await _release_lease(dispatch.fence_lease)
                raise error
        operation = dispatch.prepared.start_dispatch()
        dispatch.operation = operation
        try:
            done, _ = await asyncio.wait(
                {operation.handoff.started, operation.completion},
                timeout=deadline,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if operation.handoff.started in done:
                resolution: (
                    DispatchStartedAck
                    | DispatchNotStarted
                    | DispatchStartUnknown
                ) = operation.handoff.started.result()
            elif operation.completion in done:
                resolution: DispatchNotStarted | DispatchStartUnknown = (
                    DispatchNotStarted(dispatch.identity, "transport_not_entered")
                )
            elif operation.handoff.transport_entered:
                resolution = DispatchStartUnknown(
                    dispatch.identity,
                    "dispatch_ack_timeout_after_transport_entered",
                )
            else:
                operation.completion.cancel()
                await asyncio.gather(operation.completion, return_exceptions=True)
                if operation.handoff.transport_entered:
                    resolution = DispatchStartUnknown(
                        dispatch.identity,
                        "dispatch_cancel_raced_transport",
                    )
                else:
                    resolution = DispatchNotStarted(
                        dispatch.identity,
                        "dispatch_cancelled_before_transport",
                    )
        except BaseException:
            await _release_lease(dispatch.fence_lease)
            raise
        dispatch.start_resolution = resolution
        await _release_lease(dispatch.fence_lease)
        return dispatch.start_resolution

    async def complete_or_unknown(self, dispatch: ClaimedProviderDispatch) -> Any:
        resolution = dispatch.start_resolution
        operation = dispatch.operation
        if resolution is None or operation is None:
            raise RuntimeError("provider dispatch was not started")
        if isinstance(resolution, DispatchNotStarted):
            await self._fail(
                dispatch,
                error_class="dispatch_not_started",
                reason=resolution.reason_code,
            )
            if not operation.completion.done():
                operation.completion.cancel()
                await asyncio.gather(operation.completion, return_exceptions=True)
            raise RuntimeError(f"provider_dispatch_not_started:{resolution.reason_code}")
        if isinstance(resolution, DispatchStartUnknown):
            await self._mark_unknown(
                dispatch,
                reason=resolution.reason_code,
            )
            if not operation.completion.done():
                operation.completion.cancel()
            raise RuntimeError(f"provider_dispatch_start_unknown:{resolution.reason_code}")
        try:
            result = await operation.completion
        except asyncio.CancelledError:
            await self._mark_unknown(
                dispatch,
                reason="cancelled_after_handoff",
                error_type="CancelledError",
            )
            raise

        except BaseException as exc:
            root_error = exc
            seen: set[int] = set()
            while (
                root_error.__cause__ is not None
                and id(root_error) not in seen
            ):
                seen.add(id(root_error))
                root_error = root_error.__cause__
            root_error_type = type(root_error).__name__
            root_error_message = str(root_error) or root_error_type
            response = getattr(root_error, "response", None)
            status_code = getattr(response, "status_code", None)
            if isinstance(status_code, int) and (
                status_code in {408, 425, 429} or 500 <= status_code <= 599
            ):
                reason = f"http_status_{status_code}"
                logger.warning(
                    "provider_dispatch_retryable_response "
                    "run_id=%s invocation_id=%s provider_id=%s model_id=%s "
                    "adapter_id=%s status_code=%s error=%s",
                    dispatch.identity.run_id,
                    dispatch.identity.invocation_id,
                    dispatch.identity.provider_id,
                    dispatch.identity.model_id,
                    dispatch.identity.adapter_id,
                    status_code,
                    root_error_message[:500],
                )
                await self._fail(
                    dispatch,
                    error_class="provider_retryable_response",
                    reason=reason,
                    error_type=root_error_type,
                    error_message=root_error_message,
                )
                raise ProviderDispatchRetryableResponseError(status_code) from exc
            if root_error_type == "ConnectTimeout":
                reason = "connect_timeout_before_request"
                logger.warning(
                    "provider_dispatch_not_sent "
                    "run_id=%s invocation_id=%s provider_id=%s model_id=%s "
                    "adapter_id=%s error_type=%s error=%s",
                    dispatch.identity.run_id,
                    dispatch.identity.invocation_id,
                    dispatch.identity.provider_id,
                    dispatch.identity.model_id,
                    dispatch.identity.adapter_id,
                    root_error_type,
                    root_error_message[:500],
                )
                await self._fail(
                    dispatch,
                    error_class="transport_not_sent",
                    reason=reason,
                    error_type=root_error_type,
                    error_message=root_error_message,
                )
                raise ProviderDispatchNotSentError(
                    "provider_dispatch_not_sent:ConnectTimeout"
                ) from exc
            reason = f"transport_error_after_handoff:{root_error_type}"
            elapsed_ms = (
                None
                if not isinstance(resolution, DispatchStartedAck)
                else max(0.0, (time.time() - resolution.started_at) * 1000.0)
            )
            logger.warning(
                "provider_dispatch_unknown_after_handoff "
                "run_id=%s invocation_id=%s provider_id=%s model_id=%s "
                "adapter_id=%s error_type=%s error=%s elapsed_ms=%s",
                dispatch.identity.run_id,
                dispatch.identity.invocation_id,
                dispatch.identity.provider_id,
                dispatch.identity.model_id,
                dispatch.identity.adapter_id,
                root_error_type,
                root_error_message[:500],
                None if elapsed_ms is None else round(elapsed_ms, 1),
                exc_info=exc,
            )
            await self._mark_unknown(
                dispatch,
                reason=reason,
                error_type=root_error_type,
                error_message=root_error_message,
            )
            raise ProviderDispatchUnknownError(
                "provider_dispatch_unknown_after_handoff"
            ) from exc

        if self._fence_reacquirer is None:
            await self._mark_unknown(dispatch, reason="fence_reacquirer_unavailable")
            raise RuntimeError("provider_completion_fence_unavailable")
        refreshed = await self._fence_reacquirer(dispatch.identity.run_id)
        try:
            if (
                refreshed.run_id != dispatch.identity.run_id
                or refreshed.revocation_epoch
                != dispatch.fence_lease.revocation_epoch
            ):
                await self._mark_unknown(
                    dispatch,
                    reason="run_fence_revoked_after_dispatch",
                )
                raise RuntimeError("provider_completion_suppressed_by_fence")
            payload = _jsonable(result)
            outcome = self._build_outcome(dispatch, payload)
            try:
                await self._uow.complete_provider_invocation(
                    dispatch.identity.run_id,
                    dispatch.identity.invocation_id,
                    expected_claim_epoch=int(
                        getattr(dispatch.claim_record, "claim_epoch", 1)
                    ),
                    outcome=outcome,
                    dispatch_started_ack_ref=resolution.ack_ref,
                    dispatch_started_ack_hash=resolution.ack_hash,
                    dispatch_started_at=resolution.started_at,
                )
            except BaseException as exc:
                if _exception_code(exc) == "write_outcome_unknown":
                    stored = await self._uow.read_provider_invocation(
                        dispatch.identity.run_id,
                        dispatch.identity.invocation_id,
                    )
                    read_outcome = getattr(
                        self._uow,
                        "read_provider_invocation_outcome",
                        None,
                    )
                    durable_outcome = (
                        await read_outcome(
                            dispatch.identity.run_id,
                            dispatch.identity.invocation_id,
                        )
                        if callable(read_outcome)
                        else None
                    )
                    if (
                        stored is not None
                        and str(getattr(stored, "status", "")).split(".")[-1].lower()
                        == "completed"
                        and durable_outcome == outcome
                    ):
                        return result
                    if (
                        stored is not None
                        and str(getattr(stored, "status", "")).split(".")[-1].lower()
                        == "claimed"
                    ):
                        await self._mark_unknown(
                            dispatch,
                            reason="provider_outcome_commit_unknown",
                        )
                raise
            return result
        finally:
            await _release_lease(refreshed)

    async def abort_claimed(
        self,
        dispatch: ClaimedProviderDispatch,
        *,
        reason: str,
        error_type: str = "CancelledError",
    ) -> None:
        """Settle a claimed dispatch when its consumer is cancelled or closed."""

        operation = dispatch.operation
        if operation is not None and not operation.completion.done():
            operation.completion.cancel()
            await asyncio.gather(operation.completion, return_exceptions=True)
        stored = await self._uow.read_provider_invocation(
            dispatch.identity.run_id,
            dispatch.identity.invocation_id,
        )
        if (
            stored is None
            or str(getattr(stored, "status", "")).split(".")[-1].lower()
            != "claimed"
        ):
            return
        transport_entered = bool(
            operation is not None and operation.handoff.transport_entered
        )
        if transport_entered or isinstance(
            dispatch.start_resolution,
            (DispatchStartedAck, DispatchStartUnknown),
        ):
            await self._mark_unknown(
                dispatch,
                reason=reason,
                error_type=error_type,
            )
            return
        await self._fail(
            dispatch,
            error_class="dispatch_cancelled",
            reason=reason,
            error_type=error_type,
        )

    def _build_claim_record(
        self,
        dispatch: PreparedProviderDispatch,
        lease: ProviderFenceLease,
    ) -> Any:
        from deskpet.execution import contracts

        record_type = getattr(contracts, "ProviderInvocationRecord", None)
        values = {
            "run_id": dispatch.identity.run_id,
            "invocation_id": dispatch.identity.invocation_id,
            "provider_id": dispatch.identity.provider_id,
            "model_id": dispatch.identity.model_id,
            "adapter_id": dispatch.identity.adapter_id,
            "policy_snapshot_json": _canonical_bytes(
                dict(dispatch.snapshot.policy_snapshot)
            ).decode("utf-8"),
            "policy_snapshot": dict(dispatch.snapshot.policy_snapshot),
            "request_hash": dispatch.request_hash,
            "idempotency_group_id": dispatch.snapshot.idempotency_group_id,
            "attempt_ordinal": dispatch.snapshot.attempt_ordinal,
            "provider_chain_slot": dispatch.snapshot.provider_chain_slot,
            "retry_ordinal": dispatch.snapshot.retry_ordinal,
            "fallback_ordinal": dispatch.snapshot.fallback_ordinal,
            "status": "claimed",
            "claim_owner": self._claim_owner,
            "claim_epoch": 1,
            "claimed_at": time.time(),
            "revocation_epoch": lease.revocation_epoch,
            "stream_epoch": self._durable_stream_epoch(
                dispatch.identity.stream_epoch
            ),
            "claim_hash": dispatch.claim_hash,
            "dispatch_started_ack_ref": None,
            "dispatch_started_ack_hash": None,
            "dispatch_started_at": None,
            "outcome_ref": None,
            "outcome_hash": None,
            "updated_at": time.time(),
        }
        if record_type is None:
            return type("ProviderInvocationClaim", (), values)()
        signature = inspect.signature(record_type)
        return record_type(
            **{name: value for name, value in values.items() if name in signature.parameters}
        )

    def _build_outcome(
        self,
        dispatch: ClaimedProviderDispatch,
        payload: Any,
    ) -> Any:
        from deskpet.execution import contracts

        outcome_type = getattr(contracts, "ProviderInvocationOutcome", None)
        payload_json = _canonical_bytes(payload).decode("utf-8")
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        values = {
            "run_id": dispatch.identity.run_id,
            "invocation_id": dispatch.identity.invocation_id,
            "payload_json": payload_json,
            "payload": payload,
            "payload_hash": payload_hash,
            "created_at": time.time(),
        }
        if outcome_type is None:
            return type("ProviderInvocationOutcomeValue", (), values)()
        signature = inspect.signature(outcome_type)
        return outcome_type(
            **{name: value for name, value in values.items() if name in signature.parameters}
        )

    @staticmethod
    def _durable_stream_epoch(stream_epoch: str) -> int:
        try:
            parsed = int(stream_epoch)
        except ValueError:
            parsed = int(hashlib.sha256(stream_epoch.encode("utf-8")).hexdigest()[:8], 16)
        return max(0, parsed)

    def _claim_matches(
        self,
        record: Any,
        dispatch: PreparedProviderDispatch,
        lease: ProviderFenceLease,
    ) -> bool:
        return (
            str(getattr(record, "run_id", "")) == dispatch.identity.run_id
            and str(getattr(record, "invocation_id", ""))
            == dispatch.identity.invocation_id
            and str(getattr(record, "request_hash", "")) == dispatch.request_hash
            and int(getattr(record, "revocation_epoch", -1))
            == lease.revocation_epoch
            and str(getattr(record, "provider_id", ""))
            == dispatch.identity.provider_id
            and str(getattr(record, "model_id", ""))
            == dispatch.identity.model_id
            and str(getattr(record, "adapter_id", ""))
            == dispatch.identity.adapter_id
            and str(getattr(record, "idempotency_group_id", ""))
            == dispatch.snapshot.idempotency_group_id
            and int(getattr(record, "attempt_ordinal", -1))
            == dispatch.snapshot.attempt_ordinal
            and int(getattr(record, "stream_epoch", -1))
            == self._durable_stream_epoch(dispatch.identity.stream_epoch)
            and str(getattr(record, "policy_snapshot_json", ""))
            == _canonical_bytes(dict(dispatch.snapshot.policy_snapshot)).decode("utf-8")
            and str(getattr(record, "status", "")).split(".")[-1].lower()
            == "claimed"
        )

    async def _mark_unknown(
        self,
        dispatch: ClaimedProviderDispatch,
        *,
        reason: str,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        resolution = dispatch.start_resolution
        ack = resolution if isinstance(resolution, DispatchStartedAck) else None
        audit_ref, audit_hash = self._audit_receipt(
            dispatch,
            status="unknown",
            reason=reason,
        )
        await self._uow.mark_unknown_provider_invocation(
            dispatch.identity.run_id,
            dispatch.identity.invocation_id,
            expected_claim_epoch=int(
                getattr(dispatch.claim_record, "claim_epoch", 1)
            ),
            outcome_ref=audit_ref,
            outcome_hash=audit_hash,
            dispatch_started_ack_ref=None if ack is None else ack.ack_ref,
            dispatch_started_ack_hash=None if ack is None else ack.ack_hash,
            dispatch_started_at=None if ack is None else ack.started_at,
            audit_reason=reason,
            audit_error_type=error_type,
            audit_error_message=error_message,
        )

    async def _fail(
        self,
        dispatch: ClaimedProviderDispatch,
        *,
        error_class: str,
        reason: str,
        error_type: str | None = None,
        error_message: str | None = None,
    ) -> None:
        resolution = dispatch.start_resolution
        ack = resolution if isinstance(resolution, DispatchStartedAck) else None
        audit_ref, audit_hash = self._audit_receipt(
            dispatch,
            status=error_class,
            reason=reason,
        )
        await self._uow.fail_provider_invocation(
            dispatch.identity.run_id,
            dispatch.identity.invocation_id,
            expected_claim_epoch=int(
                getattr(dispatch.claim_record, "claim_epoch", 1)
            ),
            outcome_ref=audit_ref,
            outcome_hash=audit_hash,
            dispatch_started_ack_ref=None if ack is None else ack.ack_ref,
            dispatch_started_ack_hash=None if ack is None else ack.ack_hash,
            dispatch_started_at=None if ack is None else ack.started_at,
            audit_reason=reason,
            audit_error_type=error_type or error_class,
            audit_error_message=error_message,
        )

    @staticmethod
    def _audit_receipt(
        dispatch: ClaimedProviderDispatch,
        *,
        status: str,
        reason: str,
    ) -> tuple[str, str]:
        payload = {
            "run_id": dispatch.identity.run_id,
            "invocation_id": dispatch.identity.invocation_id,
            "status": status,
            "reason": reason,
        }
        digest = _hash(payload)
        return (
            f"provider-audit:{dispatch.identity.run_id}:"
            f"{dispatch.identity.invocation_id}:{status}",
            digest,
        )


def _agent_attempt_snapshot(
    *,
    run_id: str,
    session_id: str | None,
    root_run_id: str | None,
    parent_run_id: str | None,
    profile_key: str | None,
    provider: Any,
    attempt_id: str,
    purpose: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    fallback_model: str,
    invoke: Callable[[], Awaitable[Any]],
    stream: bool,
    retry_ordinal: int = 0,
) -> dict[str, Any]:
    provider_id = str(
        getattr(provider, "provider_id", "")
        or getattr(provider, "id", "")
        or getattr(provider, "name", "")
        or type(provider).__name__
    )
    adapter_id = str(
        getattr(provider, "adapter_id", "")
        or type(provider).__name__
    )
    model_id = str(
        getattr(provider, "model", "")
        or getattr(provider, "default_model", "")
        or fallback_model
        or "unknown-model"
    )
    provider_slot = 0
    if ":provider:" in attempt_id:
        try:
            provider_slot = int(attempt_id.rsplit(":provider:", 1)[1])
        except ValueError:
            pass
    return {
        "run_id": run_id,
        "provider_id": provider_id,
        "model_id": model_id,
        "adapter_id": adapter_id,
        "idempotency_group_id": attempt_id,
        "attempt_ordinal": 0,
        "provider_chain_slot": provider_slot,
        "retry_ordinal": retry_ordinal,
        "fallback_ordinal": provider_slot,
        "stream_epoch": f"{run_id}:{attempt_id}",
        "request_payload": {
            "messages": messages,
            "tools": tools,
            "model": model_id,
            "purpose": purpose,
            "stream": stream,
        },
        "policy_snapshot": {
            "purpose": purpose,
            "session_id": session_id,
            "root_run_id": root_run_id,
            "parent_run_id": parent_run_id,
            "profile_key": profile_key,
            "coordinated": True,
            "sdk_retries": 0,
            "stream": stream,
        },
        "invoke": invoke,
    }


async def coordinate_provider_call(
    *,
    coordinator: ProviderInvocationCoordinator | None,
    acquire_fence: Callable[[str], Awaitable[ProviderFenceLease]] | None,
    run_id: str,
    session_id: str | None = None,
    root_run_id: str | None = None,
    parent_run_id: str | None = None,
    profile_key: str | None = None,
    provider: Any,
    attempt_id: str,
    purpose: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    fallback_model: str,
    invoke: Callable[[], Awaitable[Any]],
    ack_deadline: float = 2.0,
    retry_delay: float = 1.0,
) -> Any:
    if coordinator is None or acquire_fence is None:
        return await invoke()
    for retry_ordinal in range(2):
        prepared = coordinator.prepare_attempt(
            _agent_attempt_snapshot(
                run_id=run_id,
                session_id=session_id,
                root_run_id=root_run_id,
                parent_run_id=parent_run_id,
                profile_key=profile_key,
                provider=provider,
                attempt_id=attempt_id,
                purpose=purpose,
                messages=messages,
                tools=tools,
                fallback_model=fallback_model,
                invoke=invoke,
                stream=False,
                retry_ordinal=retry_ordinal,
            )
        )
        claimed = await coordinator.claim_prepared(
            prepared,
            await acquire_fence(run_id),
        )
        try:
            await coordinator.start_and_ack(claimed, ack_deadline)
            return await coordinator.complete_or_unknown(claimed)
        except ProviderDispatchNotSentError as exc:
            if retry_ordinal == 0:
                logger.warning(
                    "provider_dispatch_safe_retry run_id=%s attempt_id=%s",
                    run_id,
                    attempt_id,
                )
                continue
            if exc.__cause__ is not None:
                raise exc.__cause__
            raise
        except ProviderDispatchRetryableResponseError as exc:
            if retry_ordinal == 0:
                logger.warning(
                    "provider_dispatch_transient_retry "
                    "run_id=%s attempt_id=%s status_code=%s",
                    run_id,
                    attempt_id,
                    exc.status_code,
                )
                if retry_delay > 0:
                    await asyncio.sleep(retry_delay)
                continue
            if exc.__cause__ is not None:
                raise exc.__cause__
            raise
        except asyncio.CancelledError:
            await coordinator.abort_claimed(
                claimed,
                reason="provider_consumer_cancelled",
            )
            raise
    raise RuntimeError("provider safe retry loop exited unexpectedly")


async def coordinate_provider_stream(
    *,
    coordinator: ProviderInvocationCoordinator | None,
    acquire_fence: Callable[[str], Awaitable[ProviderFenceLease]] | None,
    run_id: str,
    session_id: str | None = None,
    root_run_id: str | None = None,
    parent_run_id: str | None = None,
    profile_key: str | None = None,
    provider: Any,
    attempt_id: str,
    purpose: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    fallback_model: str,
    iterator: Callable[[], AsyncIterator[Any]],
    ack_deadline: float = 2.0,
) -> AsyncIterator[Any]:
    if coordinator is None or acquire_fence is None:
        async for item in iterator():
            yield item
        return
    from deskpet.execution.dispatch import (
        DispatchStartedAck,
        provisional_stream_envelope,
        provisional_stream_retract,
    )

    queue: asyncio.Queue[Any] = asyncio.Queue()
    done = object()
    pumped_final: Any = None

    async def _pump() -> Any:
        nonlocal pumped_final
        pending_delta_type: str | None = None
        pending_delta_parts: list[str] = []
        pending_delta_chars = 0

        async def _flush_pending_delta() -> None:
            nonlocal pending_delta_type, pending_delta_chars
            if pending_delta_type is None:
                return
            await queue.put(
                {
                    "type": pending_delta_type,
                    "content": "".join(pending_delta_parts),
                }
            )
            pending_delta_type = None
            pending_delta_parts.clear()
            pending_delta_chars = 0

        try:
            async for item in iterator():
                if (
                    isinstance(item, Mapping)
                    and item.get("type") in {"delta", "delta_reasoning"}
                    and set(item).issubset({"type", "content"})
                    and isinstance(item.get("content"), str)
                ):
                    item_type = str(item["type"])
                    if (
                        pending_delta_type is not None
                        and item_type != pending_delta_type
                    ):
                        await _flush_pending_delta()
                    pending_delta_type = item_type
                    content = str(item.get("content") or "")
                    pending_delta_parts.append(content)
                    pending_delta_chars += len(content)
                    if (
                        pending_delta_chars >= 256
                        or len(pending_delta_parts) >= 32
                    ):
                        await _flush_pending_delta()
                    continue
                await _flush_pending_delta()
                if isinstance(item, Mapping) and item.get("type") == "final":
                    pumped_final = dict(item)
                await queue.put(item)
        finally:
            await _flush_pending_delta()
            await queue.put(done)
        return pumped_final

    prepared = coordinator.prepare_attempt(
        _agent_attempt_snapshot(
            run_id=run_id,
            session_id=session_id,
            root_run_id=root_run_id,
            parent_run_id=parent_run_id,
            profile_key=profile_key,
            provider=provider,
            attempt_id=attempt_id,
            purpose=purpose,
            messages=messages,
            tools=tools,
            fallback_model=fallback_model,
            invoke=_pump,
            stream=True,
        )
    )
    claimed = await coordinator.claim_prepared(
        prepared,
        await acquire_fence(run_id),
    )
    try:
        started = await coordinator.start_and_ack(claimed, ack_deadline)
    except BaseException as exc:
        await coordinator.abort_claimed(
            claimed,
            reason="provider_stream_start_aborted",
            error_type=type(exc).__name__,
        )
        raise
    if not isinstance(started, DispatchStartedAck):
        await coordinator.complete_or_unknown(claimed)
        return
    final_item: Any = None
    try:
        while True:
            item = await queue.get()
            if item is done:
                break
            if isinstance(item, Mapping) and item.get("type") == "final":
                final_item = dict(item)
            elif isinstance(item, Mapping):
                yield provisional_stream_envelope(claimed.identity, dict(item))
            else:
                yield item
        await coordinator.complete_or_unknown(claimed)
    except BaseException as exc:
        await coordinator.abort_claimed(
            claimed,
            reason="provider_stream_consumer_aborted",
            error_type=type(exc).__name__,
        )
        if isinstance(exc, Exception):
            yield provisional_stream_retract(
                claimed.identity,
                reason=type(exc).__name__,
            )
        raise
    if isinstance(final_item, Mapping):
        yield {
            **dict(final_item),
            "invocation_id": claimed.identity.invocation_id,
            "stream_epoch": claimed.identity.stream_epoch,
            "provisional": False,
            "replace_provisional": True,
        }
