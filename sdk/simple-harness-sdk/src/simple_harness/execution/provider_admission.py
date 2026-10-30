# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Optional deployment admission before each physical provider handoff.

Input estimators describe a particular wire protocol, not a universal tokenizer
bound. The request is the final copy after tool-call restoration. No estimator
is supplied by default. Accounting and the physical slot belong to the caller.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from simple_harness.providers import CancelToken, ProviderRequest
from simple_harness.providers.errors import ProviderRequestRejectedError

from .provider_invocations import ProviderInvocationRecord, provider_request_fingerprint

ProviderHandoffFence = Callable[[str, str], AbstractContextManager[None]]


class TokenEstimatorPort(Protocol):
    fingerprint: str
    bound_protocol: str
    requires_prior_output_reserve: bool

    def estimate_input_tokens(self, request: ProviderRequest) -> int:
        """Public-wire input allowance; the guard adds prior output when required."""
        ...


@dataclass(frozen=True, slots=True)
class ProviderAdmissionFailure:
    """Safe durable denial data; requested/remaining describe incremental growth."""

    reason_code: str = "authority_rejected"
    account_id: str | None = None
    dimension: str | None = None
    requested: int | None = None
    remaining: int | None = None
    invocation_id: str | None = None
    handoff_ordinal: int | None = None
    mission_id: str | None = None
    subject_id: str | None = None
    bound_protocol: str | None = None
    request_tokens: int | None = None
    request_cost_micros: int | None = None

    def to_json(self) -> dict:
        return {"schema_version": 1, **asdict(self)}


class ProviderAdmissionDenied(ProviderRequestRejectedError):
    """No handoff was authorized. Retrying must obtain fresh admission."""

    error_code = "provider_admission_denied"

    def __init__(
        self,
        *,
        public_message: str | None = None,
        admission_detail: ProviderAdmissionFailure | None = None,
    ) -> None:
        super().__init__(public_message=public_message, retryable=False)
        self.admission_detail = admission_detail or ProviderAdmissionFailure()
        # Existing SDK failure persistence consumes Mapping-valued ``detail``.
        self.detail = self.admission_detail.to_json()


@dataclass(frozen=True, slots=True)
class ProviderAdmissionTicket:
    invocation_id: str
    handoff_ordinal: int
    wire_fingerprint: str
    authority_fingerprint: str


class ProviderAdmissionPort(Protocol):
    fingerprint: str

    def waiting_for_slot(self, *, agent_id: str, turn_id: str) -> bool:
        """Whether this executor is actually queued, with no physical handoff."""
        ...

    async def acquire(
        self,
        *,
        request: ProviderRequest,
        record: ProviderInvocationRecord,
        cancel: CancelToken,
        uow: object,
        execution_lease: object,
    ) -> ProviderAdmissionTicket:
        """Wait outside transactions; atomically reserve tokens and a shared slot."""
        ...

    def handoff(
        self,
        ticket: ProviderAdmissionTicket,
        *,
        request: ProviderRequest,
        cancel: CancelToken,
    ) -> AbstractContextManager[None]:
        """Fence public cancellation around synchronous SDK handoff; never await."""
        ...

    def observe(
        self,
        ticket: ProviderAdmissionTicket,
        *,
        record: ProviderInvocationRecord | None,
    ) -> None:
        """Keep uncertain handoffs held; settle only durable terminal usage."""
        ...

    def recover(self, uow: object) -> None:
        """Reconcile durable grants against this pool's actual SDK records."""
        ...


class LocalProviderAdmission:
    """Legacy local concurrency, before SDK handoff, with a caller lifecycle fence.

    This does not estimate tokens or create budget grants. It replaces only the
    wire semaphore: acquired slots last through the physical call, including its
    settlement, and release on transport uncertainty just as the old wire did.
    UNKNOWN ledger/accounting records are never resolved here.
    """

    fingerprint = "base-agent-local-admission-v1"

    def __init__(self, max_concurrent: int | None, fence: ProviderHandoffFence) -> None:
        self._semaphore = None if max_concurrent is None else asyncio.Semaphore(max_concurrent)
        self._fence = fence
        self._waiting: dict[object, tuple[str, str]] = {}
        self._acquired: dict[int, tuple[ProviderAdmissionTicket, tuple[str, str]]] = {}

    def waiting_for_slot(self, *, agent_id: str, turn_id: str) -> bool:
        return (agent_id, turn_id) in self._waiting.values()

    def with_admission(self, admission: ProviderAdmissionPort) -> ProviderAdmissionPort:
        """Share physical capacity without replacing the caller's durable authority."""
        return self if admission is self else _SharedProviderAdmission(self, admission)

    async def acquire(
        self,
        *,
        request: ProviderRequest,
        record: ProviderInvocationRecord,
        cancel: CancelToken,
        uow: Any,
        execution_lease: object,
    ) -> ProviderAdmissionTicket:

        binding = uow.read_agent_binding_for_run(record.run_id.value)
        turn = uow.read_open_agent_turn(record.run_id.value)
        if binding is None or turn is None or binding.agent_id != turn.agent_id:
            raise ProviderAdmissionDenied(public_message="Provider requires a live Agent turn.")
        identity = (binding.agent_id, turn.turn_id)
        # 不同执行库可以生成相同调用号；进程内名额按本次申请的对象区分。
        waiting = object()
        self._waiting[waiting] = identity
        acquired = False
        try:
            if self._semaphore is not None:
                await self._semaphore.acquire()
                acquired = True
            if cancel.is_cancelled or uow.read_agent_turn_cancel(turn.turn_id) is not None:
                raise ProviderAdmissionDenied(
                    admission_detail=ProviderAdmissionFailure(reason_code="cancelled")
                )
            ticket = ProviderAdmissionTicket(
                record.invocation_id,
                record.handoff_attempt + 1,
                provider_request_fingerprint(request),
                self.fingerprint,
            )
            self._acquired[id(ticket)] = (ticket, identity)
            return ticket
        except BaseException:
            if acquired and self._semaphore is not None:
                self._semaphore.release()
            raise
        finally:
            self._waiting.pop(waiting, None)

    @contextmanager
    def handoff(
        self,
        ticket: ProviderAdmissionTicket,
        *,
        request: ProviderRequest,
        cancel: CancelToken,
    ) -> Iterator[None]:
        if cancel.is_cancelled:
            raise ProviderAdmissionDenied(
                admission_detail=ProviderAdmissionFailure(reason_code="cancelled")
            )
        # The caller owns the transaction; no await separates its fresh lifecycle
        # check from the coordinator's synchronous SDK handoff.
        with self._fence(*self._acquired[id(ticket)][1]):
            yield

    def observe(
        self,
        ticket: ProviderAdmissionTicket,
        *,
        record: ProviderInvocationRecord | None,
    ) -> None:
        if self._acquired.pop(id(ticket), None) is not None:
            if self._semaphore is not None:
                self._semaphore.release()

    def recover(self, uow: object) -> None:
        # Local slots have no durable authority over existing UNKNOWN invocations.
        pass


class _SharedProviderAdmission:
    """A runtime's existing authority behind one host-owned local gate."""

    def __init__(self, shared: LocalProviderAdmission, admission: ProviderAdmissionPort) -> None:
        self.shared = shared
        self.admission = admission
        self.fingerprint = admission.fingerprint
        self._tickets: dict[int, tuple[ProviderAdmissionTicket, ProviderAdmissionTicket]] = {}

    def accepts(self, fingerprint: object) -> bool:
        accepts = getattr(self.admission, "accepts", None)
        return accepts(fingerprint) if callable(accepts) else fingerprint == self.fingerprint

    def waiting_for_slot(self, *, agent_id: str, turn_id: str) -> bool:
        return self.shared.waiting_for_slot(
            agent_id=agent_id, turn_id=turn_id
        ) or self.admission.waiting_for_slot(agent_id=agent_id, turn_id=turn_id)

    async def acquire(self, **kwargs: Any) -> ProviderAdmissionTicket:
        ticket = await self.shared.acquire(**kwargs)
        try:
            admitted = await self.admission.acquire(**kwargs)
            self._tickets[id(admitted)] = (admitted, ticket)
            return admitted
        except BaseException:
            self.shared.observe(ticket, record=None)
            raise

    @contextmanager
    def handoff(
        self,
        ticket: ProviderAdmissionTicket,
        *,
        request: ProviderRequest,
        cancel: CancelToken,
    ) -> Iterator[None]:
        with self.shared.handoff(self._tickets[id(ticket)][1], request=request, cancel=cancel):
            with self.admission.handoff(ticket, request=request, cancel=cancel):
                yield

    def observe(
        self, ticket: ProviderAdmissionTicket, *, record: ProviderInvocationRecord | None
    ) -> None:
        try:
            self.admission.observe(ticket, record=record)
        finally:
            shared = self._tickets.pop(id(ticket), None)
            if shared is not None:
                self.shared.observe(shared[1], record=record)

    def recover(self, uow: object) -> None:
        self.admission.recover(uow)


__all__ = (
    "LocalProviderAdmission",
    "ProviderAdmissionDenied",
    "ProviderAdmissionFailure",
    "ProviderAdmissionPort",
    "ProviderAdmissionTicket",
    "ProviderHandoffFence",
    "TokenEstimatorPort",
)
