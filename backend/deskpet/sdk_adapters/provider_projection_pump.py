# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Product pump from the SDK Provider outbox into Session authority."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from simple_harness import thaw_json

from .context_authority import SnapshotContractConflict, canonical_sha256
from .provider_projection import (
    ProviderProjectionEnvelopeV1,
    SdkProviderSettlementReconciler,
)

logger = logging.getLogger(__name__)

_USAGE_MEASUREMENT_KEYS = frozenset({
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_tokens",
    "reasoning_tokens",
})


class ProviderProjectionReceiptSource(Protocol):
    def list_provider_projection_receipts(
        self, *, after_sequence: int = 0, limit: int = 256
    ) -> tuple[object, ...]: ...


@dataclass(frozen=True, slots=True)
class ProviderProjectionContextV1:
    session_id: str
    root_run_id: str
    request_id: str
    snapshot_id: str
    binding_epoch: int
    context_window: int
    effective_ceiling: int

    @classmethod
    def from_value(cls, value: object) -> ProviderProjectionContextV1:
        def read(key: str, default: Any = None) -> Any:
            if isinstance(value, Mapping):
                return value.get(key, default)
            return getattr(value, key, default)

        fields = {
            key: str(read(key) or "").strip()
            for key in ("session_id", "root_run_id", "request_id", "snapshot_id")
        }
        if any(not item for item in fields.values()):
            raise ValueError("provider projection context identity is incomplete")
        return cls(
            **fields,
            binding_epoch=int(read("binding_epoch", 0)),
            context_window=int(read("context_window", 0)),
            effective_ceiling=int(read("effective_ceiling", 0)),
        )


ProjectionContextResolver = Callable[
    [object], ProviderProjectionContextV1 | object | Awaitable[object]
]
ProjectionCommitted = Callable[
    [ProviderProjectionContextV1], object | Awaitable[object]
]
FaultHook = Callable[[str, object], None]


def _read(value: object, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


class SdkProviderProjectionPump:
    """Replay ordered SDK receipts; advance only after SessionDB is durable."""

    def __init__(
        self,
        source: ProviderProjectionReceiptSource,
        session_db: Any,
        *,
        context_resolver: ProjectionContextResolver,
        consumer_id: str = "sdk-provider-projection-v1",
        batch_size: int = 256,
        interval_seconds: float = 2.0,
        fault: FaultHook | None = None,
        on_committed: ProjectionCommitted | None = None,
    ) -> None:
        if not callable(context_resolver):
            raise TypeError("context_resolver must be callable")
        if not 1 <= int(batch_size) <= 10_000:
            raise ValueError("batch_size is out of range")
        self._source = source
        self._db = session_db
        self._context_resolver = context_resolver
        self._consumer_id = str(consumer_id or "").strip()
        if not self._consumer_id:
            raise ValueError("consumer_id is required")
        self._batch_size = int(batch_size)
        self._interval = max(0.05, float(interval_seconds))
        self._fault = fault
        self._on_committed = on_committed
        self._reconciler = SdkProviderSettlementReconciler(
            session_db, consumer_id=self._consumer_id
        )
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def _hit(self, point: str, receipt: object) -> None:
        if self._fault is not None:
            self._fault(point, receipt)

    async def _context(self, receipt: object) -> ProviderProjectionContextV1:
        value = self._context_resolver(receipt)
        if inspect.isawaitable(value):
            value = await value
        return ProviderProjectionContextV1.from_value(value)

    async def _envelope(
        self, receipt: object
    ) -> ProviderProjectionEnvelopeV1:
        sequence = int(_read(receipt, "sequence", 0))
        if sequence < 1:
            raise ValueError("provider projection receipt sequence is invalid")
        payload_value = _read(receipt, "payload")
        payload = thaw_json(payload_value)
        if not isinstance(payload, Mapping):
            raise ValueError("provider projection receipt payload must be an object")
        expected_hash = str(_read(receipt, "payload_hash", ""))
        if not expected_hash or canonical_sha256(payload) != expected_hash:
            raise SnapshotContractConflict("provider projection receipt hash mismatch")
        context = await self._context(receipt)
        target = payload.get("target")
        if not isinstance(target, Mapping):
            raise ValueError("provider projection target is missing")
        usage_envelope = payload.get("usage")
        measurement: object | None = None
        if isinstance(usage_envelope, Mapping):
            nested_usage = usage_envelope.get("usage")
            if isinstance(nested_usage, Mapping):
                measurement = nested_usage
            elif _USAGE_MEASUREMENT_KEYS.intersection(usage_envelope):
                # Older receipts exposed the measurement directly.  A modern
                # terminal receipt may instead contain only ``budget``
                # metadata (notably after cancellation); that is not a token
                # measurement and must not poison the ordered projection
                # cursor.
                measurement = usage_envelope
        state = str(payload.get("state") or "").strip().lower()
        return ProviderProjectionEnvelopeV1.from_value({
            "invocation_id": _read(receipt, "invocation_id"),
            "settlement_version": _read(
                receipt, "invocation_version", payload.get("invocation_version")
            ),
            "settled_at": payload.get("settled_at", _read(receipt, "created_at")),
            "session_id": context.session_id,
            "root_run_id": context.root_run_id,
            "request_id": context.request_id,
            "snapshot_id": context.snapshot_id,
            "state": state,
            "provider_id": target.get("provider_id"),
            "model_id": target.get("model"),
            "binding_epoch": context.binding_epoch,
            "context_window": context.context_window,
            "effective_ceiling": context.effective_ceiling,
            "usage": measurement,
            "provider_request_id": payload.get("request_id"),
            "error_code": payload.get("error_code"),
            "handoff_attempt": payload.get("handoff_attempt"),
        })

    async def run_once(self) -> int:
        cursor = await self._db.get_sdk_provider_projection_cursor(self._consumer_id)
        after = int(cursor.get("source_sequence", 0)) if cursor else 0
        receipts = self._source.list_provider_projection_receipts(
            after_sequence=after, limit=self._batch_size
        )
        projected = 0
        for receipt in receipts:
            sequence = int(_read(receipt, "sequence", 0))
            if sequence <= after:
                raise SnapshotContractConflict("provider projection outbox regressed")
            self._hit("provider_projection.outbox.after_read", receipt)
            envelope = await self._envelope(receipt)
            await self._reconciler.project_attempt(envelope)
            self._hit("provider_projection.session.after_write", receipt)
            # Failed/cancelled/unknown attempts are durable audit facts, but
            # they do not advance Context Usage authority.  Broadcasting an
            # unchanged usage version can conflict with newly attached
            # snapshot metadata and permanently pin the ordered cursor on the
            # same terminal receipt.
            if self._on_committed is not None and envelope.has_trusted_usage:
                notified = self._on_committed(await self._context(receipt))
                if inspect.isawaitable(notified):
                    await notified
            await self._reconciler.advance_cursor(
                envelope, source_sequence=sequence
            )
            after = sequence
            projected += 1
        return projected

    async def run_until_idle(self) -> int:
        total = 0
        while True:
            count = await self.run_once()
            total += count
            if count < self._batch_size:
                return total

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._run(), name=f"{self._consumer_id}:provider-projection"
        )

    def trigger(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while True:
            try:
                await self.run_until_idle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # projection retries; physical Run is untouched
                logger.warning(
                    "sdk_provider_projection_failed",
                    extra={"error_type": type(exc).__name__},
                )
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            except TimeoutError:
                pass

    async def close(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


__all__ = [
    "ProviderProjectionContextV1",
    "ProviderProjectionReceiptSource",
    "SdkProviderProjectionPump",
]
