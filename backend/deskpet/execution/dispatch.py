from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterator, Mapping, Protocol, TypeAlias, runtime_checkable


def _required(value: str, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _hash(value: str, name: str) -> str:
    normalized = _required(value, name)
    if (
        len(normalized) != 64
        or any(char not in "0123456789abcdef" for char in normalized)
    ):
        raise ValueError(f"{name} must be lowercase SHA-256")
    return normalized


@dataclass(frozen=True, slots=True)
class DispatchIdentity:
    run_id: str
    invocation_id: str
    provider_id: str
    model_id: str
    adapter_id: str
    stream_epoch: str

    def __post_init__(self) -> None:
        for field_name in (
            "run_id",
            "invocation_id",
            "provider_id",
            "model_id",
            "adapter_id",
            "stream_epoch",
        ):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True, slots=True)
class DispatchStartedAck:
    identity: DispatchIdentity
    adapter_identity: str
    ack_ref: str
    ack_hash: str
    started_at: float


@dataclass(frozen=True, slots=True)
class DispatchNotStarted:
    identity: DispatchIdentity
    reason_code: str


@dataclass(frozen=True, slots=True)
class DispatchStartUnknown:
    identity: DispatchIdentity
    reason_code: str


@dataclass(slots=True)
class DispatchOperation:
    completion: asyncio.Task[Any]
    handoff: "DispatchHandoff"


@runtime_checkable
class PreparedDispatch(Protocol):
    identity: DispatchIdentity
    request_hash: str

    def start_dispatch(self) -> DispatchOperation: ...


@dataclass(frozen=True, slots=True)
class PreparedToolDispatchIdentity:
    """Stable identity for one prepared physical tool effect.

    This is intentionally separate from ``DispatchIdentity``: the latter is
    the provider transport boundary and its semantics must not drift when the
    tool runtime gains a prepare/start/completion protocol.
    """

    run_id: str
    call_id: str
    effect_id: str
    tool_name: str
    adapter_id: str
    runtime_identity: str

    def __post_init__(self) -> None:
        for field_name in (
            "run_id",
            "call_id",
            "effect_id",
            "tool_name",
            "adapter_id",
            "runtime_identity",
        ):
            object.__setattr__(
                self,
                field_name,
                _required(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True, slots=True)
class PreparedToolDispatchStartedAck:
    identity: PreparedToolDispatchIdentity
    ack_ref: str
    ack_hash: str
    started_at: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "ack_ref", _required(self.ack_ref, "ack_ref"))
        object.__setattr__(self, "ack_hash", _hash(self.ack_hash, "ack_hash"))
        if self.started_at <= 0:
            raise ValueError("started_at must be positive")


@dataclass(frozen=True, slots=True)
class PreparedToolDispatchNotStarted:
    identity: PreparedToolDispatchIdentity
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "reason_code", _required(self.reason_code, "reason_code")
        )


@dataclass(frozen=True, slots=True)
class PreparedToolDispatchStartUnknown:
    identity: PreparedToolDispatchIdentity
    reason_code: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "reason_code", _required(self.reason_code, "reason_code")
        )


PreparedToolDispatchStartResult: TypeAlias = (
    PreparedToolDispatchStartedAck
    | PreparedToolDispatchNotStarted
    | PreparedToolDispatchStartUnknown
)


@runtime_checkable
class PreparedToolDispatch(Protocol):
    """Prepared tool effect with explicit physical-start and completion stages."""

    identity: PreparedToolDispatchIdentity
    request_hash: str

    async def start(
        self, *, deadline: float
    ) -> PreparedToolDispatchStartResult: ...

    async def completion(self) -> Any: ...

    async def abort_unstarted(self) -> None: ...


@runtime_checkable
class PreparedToolDispatchAdapter(Protocol):
    """Product-neutral adapter seam; Task 7 supplies production adapters."""

    adapter_id: str
    adapter_version: str
    adapter_fingerprint: str

    async def begin_prepared(
        self,
        *,
        spec: Mapping[str, Any],
        prepared: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> PreparedToolDispatch: ...


class FunctionPreparedToolDispatch:
    """Three-stage boundary for an in-process callable tool.

    Construction is side-effect free.  ``start`` is the sole physical handoff
    and returns before the scheduled handler gets an event-loop turn.
    """

    def __init__(
        self,
        *,
        identity: PreparedToolDispatchIdentity,
        request_hash: str,
        invoke: Callable[[], Awaitable[Any]],
        release_unstarted: Callable[[], Awaitable[None]] | None = None,
        on_waiter_cancelled: Callable[[asyncio.Task[Any]], None] | None = None,
    ) -> None:
        self.identity = identity
        self.request_hash = _hash(request_hash, "request_hash")
        self._invoke = invoke
        self._release_unstarted = release_unstarted
        self._on_waiter_cancelled = on_waiter_cancelled
        self._completion: asyncio.Task[Any] | None = None
        self._started_ack: PreparedToolDispatchStartedAck | None = None
        self._completion_observation_armed = False
        self._aborted = False

    async def start(
        self, *, deadline: float
    ) -> PreparedToolDispatchStartResult:
        if self._started_ack is not None:
            return self._started_ack
        if self._aborted:
            return PreparedToolDispatchNotStarted(
                self.identity, "dispatch_aborted_before_start"
            )
        if deadline <= time.time():
            return PreparedToolDispatchNotStarted(
                self.identity, "dispatch_start_deadline_elapsed"
            )
        if self._completion is not None:
            return PreparedToolDispatchStartUnknown(
                self.identity, "dispatch_task_exists_without_ack"
            )
        try:
            self._completion = asyncio.create_task(
                self._invoke(),
                name=(
                    f"tool-dispatch:{self.identity.run_id}:"
                    f"{self.identity.call_id}"
                ),
            )
            started_at = time.time()
            ack_ref = (
                f"tool-dispatch:{self.identity.run_id}:"
                f"{self.identity.call_id}:{self.identity.effect_id}:"
                f"{self.identity.adapter_id}"
            )
            self._started_ack = PreparedToolDispatchStartedAck(
                identity=self.identity,
                ack_ref=ack_ref,
                ack_hash=_fingerprint(
                    {
                        "identity": {
                            "run_id": self.identity.run_id,
                            "call_id": self.identity.call_id,
                            "effect_id": self.identity.effect_id,
                            "tool_name": self.identity.tool_name,
                            "adapter_id": self.identity.adapter_id,
                            "runtime_identity": self.identity.runtime_identity,
                        },
                        "request_hash": self.request_hash,
                        "ack_ref": ack_ref,
                        "started_at": started_at,
                    }
                ),
                started_at=started_at,
            )
            # From the instant StartedAck is visible, cancellation may detach
            # the consumer while the physical task continues.  Arm that
            # observation synchronously before returning the acknowledgement.
            self._completion_observation_armed = True
            return self._started_ack
        except Exception:
            if self._completion is not None:
                return PreparedToolDispatchStartUnknown(
                    self.identity, "dispatch_start_ack_failed"
                )
            return PreparedToolDispatchNotStarted(
                self.identity, "dispatch_task_not_created"
            )

    async def completion(self) -> Any:
        if self._completion is None or self._started_ack is None:
            raise RuntimeError("prepared tool dispatch has not started")
        try:
            return await asyncio.shield(self._completion)
        except asyncio.CancelledError:
            self.detach_completion()
            raise

    def detach_completion(self) -> None:
        """Retain the physical completion after its logical consumer leaves."""

        if (
            self._completion_observation_armed
            and self._completion is not None
            and self._on_waiter_cancelled is not None
        ):
            self._on_waiter_cancelled(self._completion)

    async def abort_unstarted(self) -> None:
        if self._started_ack is not None or self._completion is not None:
            return
        if self._aborted:
            return
        self._aborted = True
        if self._release_unstarted is not None:
            await self._release_unstarted()


class DispatchHandoff:
    """One-shot bridge between a provider transport and its coordinator."""

    def __init__(self, identity: DispatchIdentity) -> None:
        self.identity = identity
        self.started: asyncio.Future[DispatchStartedAck] = (
            asyncio.get_running_loop().create_future()
        )
        self.handoff_attempted = False
        self.transport_entered = False

    def mark_transport_entered(self) -> None:
        self.transport_entered = True

    def acknowledge(self, adapter_identity: str) -> DispatchStartedAck:
        self.handoff_attempted = True
        adapter = _required(adapter_identity, "adapter_identity")
        started_at = time.time()
        ack_ref = (
            f"dispatch:{self.identity.run_id}:{self.identity.invocation_id}:"
            f"{self.identity.adapter_id}"
        )
        ack = DispatchStartedAck(
            identity=self.identity,
            adapter_identity=adapter,
            ack_ref=ack_ref,
            ack_hash=_fingerprint(
                {
                    "ack_ref": ack_ref,
                    "adapter_identity": adapter,
                    "started_at": started_at,
                }
            ),
            started_at=started_at,
        )
        if not self.started.done():
            self.started.set_result(ack)
        return ack


_CURRENT_HANDOFF: ContextVar[DispatchHandoff | None] = ContextVar(
    "deskpet_current_dispatch_handoff",
    default=None,
)


def current_dispatch_handoff() -> DispatchHandoff | None:
    return _CURRENT_HANDOFF.get()


@contextmanager
def dispatch_handoff_scope(handoff: DispatchHandoff) -> Iterator[None]:
    token: Token[DispatchHandoff | None] = _CURRENT_HANDOFF.set(handoff)
    try:
        yield
    finally:
        _CURRENT_HANDOFF.reset(token)


def create_dispatch_operation(
    identity: DispatchIdentity,
    invoke: Callable[[], Awaitable[Any]],
) -> DispatchOperation:
    handoff = DispatchHandoff(identity)

    async def _run() -> Any:
        with dispatch_handoff_scope(handoff):
            return await invoke()

    return DispatchOperation(
        completion=asyncio.create_task(
            _run(),
            name=f"provider-dispatch:{identity.invocation_id}",
        ),
        handoff=handoff,
    )


def provisional_stream_envelope(
    identity: DispatchIdentity,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        **payload,
        "invocation_id": identity.invocation_id,
        "stream_epoch": identity.stream_epoch,
        "provisional": True,
    }


def provisional_stream_retract(
    identity: DispatchIdentity,
    *,
    reason: str,
) -> dict[str, Any]:
    return provisional_stream_envelope(
        identity,
        {
            "type": "retract",
            "reason": _required(reason, "reason"),
        },
    )


async def dispatch_with_run_fence(
    *,
    acquire_fence: Callable[[str], Awaitable[Any]] | None,
    run_id: str,
    operation_kind: str,
    operation_id: str,
    invoke: Callable[[], Awaitable[Any]],
    return_fence_epoch: bool = False,
) -> Any:
    """Acquire the shared run fence at the final physical effect boundary."""

    if acquire_fence is None:
        result = await invoke()
        return (result, 0) if return_fence_epoch else result
    lease = await acquire_fence(_required(run_id, "run_id"))
    if str(getattr(lease, "run_id", "")) != run_id:
        raise RuntimeError("physical dispatch fence run mismatch")
    try:
        result = await invoke()
        if return_fence_epoch:
            return result, int(getattr(lease, "revocation_epoch", 0))
        return result
    finally:
        release = getattr(lease, "release", None)
        if not callable(release):
            raise RuntimeError(
                "physical dispatch fence lease must expose release()"
            )
        released = release()
        if asyncio.iscoroutine(released):
            await released
