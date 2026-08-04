"""Managed process start-ACK boundary for local capability runtimes."""

from __future__ import annotations

import json
import hashlib
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .runtime_prepare import (
    PreparedRuntimeInstanceSpec,
    RuntimeHealthOutcome,
    RuntimeLaunchAuthorization,
    RuntimeNotStarted,
    RuntimeStartedAck,
    RuntimeStartResult,
    RuntimeStartUnknown,
)


@dataclass(frozen=True, slots=True)
class ManagedProcessStartReceipt:
    outcome: str
    runtime_instance_id: str
    adapter_identity: str
    job_identity: str | None = None
    pid: int | None = None
    process_create_time: float | None = None
    command_line_hash: str | None = None
    reason_code: str = ""


class ManagedProcessStartPort(Protocol):
    """Native helper owning suspended -> Job assign -> resume -> pipe ACK."""

    async def start_and_ack(
        self,
        *,
        runtime_instance_id: str,
        start_envelope: Mapping[str, Any],
        authorization_nonce: str,
    ) -> ManagedProcessStartReceipt: ...

    async def await_health(
        self,
        receipt: ManagedProcessStartReceipt,
        health_envelope: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    async def abort_exact(self, receipt: ManagedProcessStartReceipt) -> None: ...

    async def recover_exact(
        self,
        *,
        runtime_instance_id: str,
        start_identity: Mapping[str, Any],
    ) -> ManagedProcessStartReceipt | None: ...

    async def abort_unacknowledged(
        self,
        *,
        runtime_instance_id: str,
        authorization_nonce: str,
    ) -> None: ...


class UnavailableManagedProcessStartPort:
    """Production-safe placeholder until the native Job helper is injected."""

    async def start_and_ack(
        self,
        *,
        runtime_instance_id: str,
        start_envelope: Mapping[str, Any],
        authorization_nonce: str,
    ) -> ManagedProcessStartReceipt:
        del start_envelope, authorization_nonce
        return ManagedProcessStartReceipt(
            outcome="not_started",
            runtime_instance_id=runtime_instance_id,
            adapter_identity="managed-process-job-unavailable-v1",
            reason_code="native_job_helper_unavailable",
        )

    async def await_health(
        self,
        receipt: ManagedProcessStartReceipt,
        health_envelope: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        del receipt, health_envelope
        raise RuntimeError("native_job_helper_unavailable")

    async def abort_exact(self, receipt: ManagedProcessStartReceipt) -> None:
        del receipt

    async def recover_exact(
        self,
        *,
        runtime_instance_id: str,
        start_identity: Mapping[str, Any],
    ) -> ManagedProcessStartReceipt | None:
        del runtime_instance_id, start_identity
        return None

    async def abort_unacknowledged(
        self,
        *,
        runtime_instance_id: str,
        authorization_nonce: str,
    ) -> None:
        del runtime_instance_id, authorization_nonce


class ManagedProcessJobAdapter:
    """Prepared runtime adapter backed by the native managed-process helper."""

    adapter_id = "managed-process-job-v1"

    def __init__(
        self,
        port: ManagedProcessStartPort,
        *,
        adapter_fingerprint: str,
        clock=time.time,
    ) -> None:
        self._port = port
        self.adapter_fingerprint = str(adapter_fingerprint)
        self._clock = clock
        self._receipts: dict[str, ManagedProcessStartReceipt] = {}

    async def start_prepared(
        self,
        spec: PreparedRuntimeInstanceSpec,
        authorization: RuntimeLaunchAuthorization,
    ) -> RuntimeStartResult:
        runtime_instance_id = authorization.runtime_instance_id
        try:
            receipt = await self._port.start_and_ack(
                runtime_instance_id=runtime_instance_id,
                start_envelope=spec.start_envelope,
                authorization_nonce=authorization.nonce,
            )
        except BaseException:
            # The native helper contract must make this exact abort
            # idempotent.  It closes a possibly-created Job before the short
            # launch fence is released and prevents a cancelled await from
            # becoming a late process start.
            await self._port.abort_unacknowledged(
                runtime_instance_id=runtime_instance_id,
                authorization_nonce=authorization.nonce,
            )
            raise
        if receipt.runtime_instance_id != runtime_instance_id:
            await self._port.abort_unacknowledged(
                runtime_instance_id=runtime_instance_id,
                authorization_nonce=authorization.nonce,
            )
            raise ValueError("managed process runtime identity mismatch")
        if receipt.outcome == "not_started":
            return RuntimeNotStarted(
                authorization.operation_id,
                spec.entry_id,
                receipt.reason_code or "process_not_started",
                runtime_instance_id,
            )
        if receipt.outcome == "unknown":
            self._receipts[runtime_instance_id] = receipt
            return RuntimeStartUnknown(
                authorization.operation_id,
                spec.entry_id,
                receipt.reason_code or "process_start_unknown",
                runtime_instance_id,
            )
        if receipt.outcome != "started":
            await self._port.abort_unacknowledged(
                runtime_instance_id=runtime_instance_id,
                authorization_nonce=authorization.nonce,
            )
            raise ValueError("managed process start outcome invalid")
        if (
            not receipt.job_identity
            or receipt.pid is None
            or receipt.process_create_time is None
            or not receipt.command_line_hash
        ):
            await self._port.abort_exact(receipt)
            raise ValueError("managed process started ACK is incomplete")
        self._receipts[runtime_instance_id] = receipt
        return RuntimeStartedAck(
            operation_id=authorization.operation_id,
            runtime_set_hash=authorization.runtime_set_hash,
            entry_id=spec.entry_id,
            runtime_instance_id=runtime_instance_id,
            adapter_identity=receipt.adapter_identity,
            start_identity={
                "job_identity": receipt.job_identity,
                "pid": receipt.pid,
                "process_create_time": receipt.process_create_time,
                "command_line_hash": receipt.command_line_hash,
            },
            acknowledged_at=float(self._clock()),
        )

    async def await_health(
        self,
        spec: PreparedRuntimeInstanceSpec,
        ack: RuntimeStartedAck,
    ) -> RuntimeHealthOutcome:
        receipt = self._receipts.get(ack.runtime_instance_id)
        if receipt is None:
            receipt = await self._port.recover_exact(
                runtime_instance_id=ack.runtime_instance_id,
                start_identity=ack.start_identity,
            )
            if receipt is not None:
                self._receipts[ack.runtime_instance_id] = receipt
        if receipt is None or receipt.runtime_instance_id != ack.runtime_instance_id:
            raise RuntimeError("managed process receipt unavailable")
        if (
            receipt.job_identity != ack.start_identity.get("job_identity")
            or receipt.pid != ack.start_identity.get("pid")
            or receipt.process_create_time
            != ack.start_identity.get("process_create_time")
            or receipt.command_line_hash
            != ack.start_identity.get("command_line_hash")
        ):
            raise RuntimeError("managed process recovered identity mismatch")
        detail = dict(
            await self._port.await_health(receipt, spec.health_envelope)
        )
        healthy = detail.get("healthy") is True
        return RuntimeHealthOutcome(
            operation_id=ack.operation_id,
            entry_id=spec.entry_id,
            healthy=healthy,
            outcome_hash=hashlib.sha256(
                json.dumps(
                    detail, sort_keys=True, separators=(",", ":"), default=str
                ).encode("utf-8")
            ).hexdigest(),
            detail=detail,
        )

    async def abort(
        self,
        spec: PreparedRuntimeInstanceSpec,
        start: RuntimeStartResult | None,
    ) -> None:
        runtime_instance_id = str(
            getattr(start, "runtime_instance_id", "") or ""
        )
        receipt = (
            None
            if not runtime_instance_id
            else self._receipts.pop(runtime_instance_id, None)
        )
        if receipt is None and isinstance(start, RuntimeStartedAck):
            receipt = await self._port.recover_exact(
                runtime_instance_id=start.runtime_instance_id,
                start_identity=start.start_identity,
            )
        if receipt is not None:
            await self._port.abort_exact(receipt)


__all__ = [
    "ManagedProcessJobAdapter",
    "ManagedProcessStartPort",
    "ManagedProcessStartReceipt",
    "UnavailableManagedProcessStartPort",
]
