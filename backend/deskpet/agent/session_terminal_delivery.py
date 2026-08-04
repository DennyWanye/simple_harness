"""Durable projection of root Run failures into the owning chat Session."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from weakref import WeakValueDictionary

from deskpet.execution.contracts import (
    DeliveryPolicy,
    DeliverySpec,
    OutcomeStatus,
    RunEvent,
    canonical_json,
    thaw_json,
)
from deskpet.execution.uow_ports import ProductProjectionUnitOfWork
from deskpet.harness.projector import DeliveryDiscarded


_WINDOWS_PATH = re.compile(r"(?i)\b[a-z]:\\[^\s\"']+")
_TOKEN = re.compile(r"(?i)\b(?:tsk|key|sk)_[a-z0-9_-]+\b")


@dataclass(frozen=True, slots=True)
class SessionTerminalDeliveryTargetV1:
    session_id: str
    session_epoch: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not str(self.session_id or "").strip():
            raise ValueError("session terminal target requires session_id")
        if (
            isinstance(self.session_epoch, bool)
            or not isinstance(self.session_epoch, int)
            or self.session_epoch < 0
        ):
            raise ValueError("session terminal target epoch is invalid")
        if self.schema_version != 1:
            raise ValueError("unsupported session terminal target schema")

    @property
    def target_id(self) -> str:
        payload = canonical_json(
            {
                "schema_version": self.schema_version,
                "session_id": self.session_id,
                "session_epoch": self.session_epoch,
            }
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @classmethod
    def parse(cls, target_id: str) -> "SessionTerminalDeliveryTargetV1":
        text = str(target_id or "").strip()
        if not text:
            raise ValueError("session terminal target is empty")
        padding = "=" * (-len(text) % 4)
        try:
            value = json.loads(
                base64.urlsafe_b64decode(text + padding).decode("utf-8")
            )
        except Exception as exc:
            raise ValueError("session terminal target is invalid") from exc
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "session_id",
            "session_epoch",
        }:
            raise ValueError("session terminal target shape is invalid")
        return cls(
            session_id=str(value["session_id"]),
            session_epoch=int(value["session_epoch"]),
            schema_version=int(value["schema_version"]),
        )


def public_terminal_error_summary(event: RunEvent) -> str:
    """Return one stable, secret-free message shared by projection/read-through."""

    correlation = thaw_json(event.candidate.correlation)
    error = (
        {}
        if event.candidate.error is None
        else thaw_json(event.candidate.error)
    )
    if not isinstance(correlation, Mapping):
        correlation = {}
    if not isinstance(error, Mapping):
        error = {}
    code = str(
        correlation.get("failure_code")
        or error.get("code")
        or event.status.value
    ).strip()
    detail = str(error.get("message") or "").strip()
    detail = _TOKEN.sub("[credential]", _WINDOWS_PATH.sub("[local path]", detail))
    detail = " ".join(detail.split())[:500]
    if event.status is OutcomeStatus.CANCELLED:
        return "刚才的任务已取消。"
    prefix = f"刚才的任务执行失败（{code}）。"
    return prefix if not detail else f"{prefix} {detail}"


class SessionTerminalDeliveryContributor:
    sink_kind = "session_terminal"
    sink_instance = "session-transcript-v1"

    @staticmethod
    def _is_foreground_root_request(request: object) -> bool:
        venue = str(getattr(request, "venue", "") or "").strip().lower()
        return venue in {"text", "voice"}

    def requires_durable(self, request: object, _host: object) -> bool:
        return self._is_foreground_root_request(request)

    def freeze_deliveries(
        self, request: object, host: object
    ) -> tuple[DeliverySpec, ...]:
        if not self._is_foreground_root_request(request):
            return ()
        target = SessionTerminalDeliveryTargetV1(
            session_id=str(getattr(host, "session_id", "") or ""),
            session_epoch=int(getattr(host, "auth_epoch", 0) or 0),
        )
        return (
            DeliverySpec(
                self.sink_kind,
                self.sink_instance,
                target.target_id,
                DeliveryPolicy.DURABLE_REQUIRED,
            ),
        )


class SessionTerminalDeliverySink:
    def __init__(self, session_db: Any) -> None:
        self._session_db = session_db

    async def is_bound(self, target_id: str) -> bool:
        target = SessionTerminalDeliveryTargetV1.parse(target_id)
        state = await self._session_db.get_session_delivery_state(
            target.session_id
        )
        return (
            state.get("deleted_at") is None
            and int(state.get("epoch", 0)) == target.session_epoch
        )

    async def deliver(self, event: RunEvent, target_id: str) -> None:
        target = SessionTerminalDeliveryTargetV1.parse(target_id)
        if event.run_id != event.root_run_id:
            raise DeliveryDiscarded("child_terminal_not_projected")
        if event.status not in {
            OutcomeStatus.FAILED,
            OutcomeStatus.CANCELLED,
        }:
            return
        message_id = await self._session_db.append_projection_if_epoch(
            target.session_id,
            "assistant",
            public_terminal_error_summary(event),
            expected_epoch=target.session_epoch,
            projection_event_id=event.event_id,
            projection_kind="assistant_message",
            workflow_event_id=event.event_id,
            root_run_id=event.root_run_id,
            task_scope_id=event.root_run_id,
        )
        if message_id is None:
            raise DeliveryDiscarded("session_epoch_mismatch")


class SessionProjectionStore(Protocol):
    async def get_session_delivery_state(
        self, session_id: str
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class SessionProjectionSyncResult:
    session_id: str
    session_epoch: int
    source_event_count: int
    deleted: bool = False


class SessionProjectionConsistencyError(RuntimeError):
    """The product view could not be proven current at its read barrier."""


class SessionTerminalProjectionConsistencyGate:
    """Bring state.db current before any product read is allowed to continue.

    workflow.db remains authoritative.  The gate takes one source snapshot,
    idempotently projects every matching terminal event, then verifies that
    the Session epoch did not move while it worked.  A per-session lock keeps
    concurrent history/context reads from doing duplicate repair work.
    """

    def __init__(
        self,
        execution_uow: ProductProjectionUnitOfWork,
        session_db: SessionProjectionStore,
        sink: SessionTerminalDeliverySink,
    ) -> None:
        self._uow = execution_uow
        self._session_db = session_db
        self._sink = sink
        self._locks: WeakValueDictionary[str, asyncio.Lock] = (
            WeakValueDictionary()
        )
        self._locks_guard = asyncio.Lock()

    async def _lock_for(self, session_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(session_id, asyncio.Lock())

    async def ensure_current(
        self, session_id: str
    ) -> SessionProjectionSyncResult:
        session_key = str(session_id or "").strip()
        if not session_key:
            raise ValueError("session_id must be non-empty")
        lock = await self._lock_for(session_key)
        async with lock:
            try:
                # One retry handles a concurrent delete/recreate epoch
                # rotation.
                for _attempt in range(2):
                    before = (
                        await self._session_db.get_session_delivery_state(
                            session_key
                        )
                    )
                    epoch = int(before.get("epoch", 0))
                    if before.get("deleted_at") is not None:
                        return SessionProjectionSyncResult(
                            session_key, epoch, 0, deleted=True
                        )
                    target = SessionTerminalDeliveryTargetV1(
                        session_id=session_key,
                        session_epoch=epoch,
                    )
                    events = await self._uow.list_session_terminal_events(
                        session_key,
                        target.target_id,
                        session_epoch=epoch,
                        limit=None,
                    )
                    fenced = False
                    for event in events:
                        try:
                            await self._sink.deliver(
                                event, target.target_id
                            )
                        except DeliveryDiscarded as exc:
                            if str(exc) == "session_epoch_mismatch":
                                fenced = True
                                break
                            raise SessionProjectionConsistencyError(
                                f"terminal projection discarded: {exc}"
                            ) from exc
                    after = (
                        await self._session_db.get_session_delivery_state(
                            session_key
                        )
                    )
                    stable = (
                        after.get("deleted_at") is None
                        and int(after.get("epoch", 0)) == epoch
                    )
                    if stable and not fenced:
                        return SessionProjectionSyncResult(
                            session_key,
                            epoch,
                            len(events),
                        )
                raise SessionProjectionConsistencyError(
                    "session projection epoch changed during consistency sync"
                )
            except SessionProjectionConsistencyError:
                raise
            except Exception as exc:
                raise SessionProjectionConsistencyError(
                    "session projection sync failed"
                ) from exc

    async def sync(self, session_id: str) -> int:
        """Return the projected source count after the strict consistency gate."""

        result = await self.ensure_current(session_id)
        return result.source_event_count


def __getattr__(name: str):
    """Resolve removed names only through the explicit compatibility package."""

    if name == "SessionTerminalReadThrough":
        from deskpet.compat.session_projection import SessionTerminalReadThrough

        return SessionTerminalReadThrough
    raise AttributeError(name)


__all__ = [
    "SessionTerminalDeliveryContributor",
    "SessionProjectionConsistencyError",
    "SessionProjectionSyncResult",
    "SessionTerminalProjectionConsistencyGate",
    "SessionTerminalDeliverySink",
    "SessionTerminalDeliveryTargetV1",
    "public_terminal_error_summary",
]
