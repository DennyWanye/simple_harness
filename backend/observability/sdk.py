# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Host composition for the local-only SDK observability side channel."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import stat
import threading
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

SDK_EVENTS_FILENAME = "sdk-observability-events.jsonl"
SDK_RING_FILENAME = "sdk-observability-ring.json"
SDK_SNAPSHOT_FILENAME = "sdk-observability-snapshot.json"

_RING_CAPACITY = 256
_JSONL_MAX_BYTES = 1_048_576
_JSONL_MAX_FILES = 3
_EXPORT_MAX_BYTES = 512 * 1024


class HostCorrelationPolicy:
    """Create diagnostic identities from authority IDs without granting authority."""

    def __init__(self) -> None:
        self._current: ContextVar[tuple[str, Any] | None] = ContextVar(
            "sdk_observability_correlation", default=None
        )

    @staticmethod
    def _context(*, run_id: str, session_id: str | None, request_id: str | None):
        from simple_harness.observability import CorrelationContext

        return CorrelationContext.from_authority_ids(
            run_id=run_id,
            execution_session_id=session_id,
            request_id=request_id,
        )

    def bind_ingress(
        self, *, run_id: str, session_id: str, request_id: str
    ) -> Token[tuple[str, Any] | None]:
        context = self._context(
            run_id=run_id, session_id=session_id, request_id=request_id
        )
        return self._current.set((session_id, context))

    def reset(self, token: Token[tuple[str, Any] | None]) -> None:
        self._current.reset(token)

    def bind_unavailable(self, session_id: str) -> Token[tuple[str, Any] | None]:
        return self._current.set((session_id, None))

    def __call__(self, entity_id: str, session_id: str | None = None):
        current = self._current.get()
        if current is not None and session_id is not None and current[0] == session_id:
            return current[1].child()
        return self._context(run_id=entity_id, session_id=session_id, request_id=None)


class HostSdkObservability:
    """Own the shared SDK sink and bounded, explicit diagnostic exports."""

    def __init__(self, log_dir: Path, *, logger: logging.Logger | None = None) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.correlation = HostCorrelationPolicy()
        self._logger = logger or logging.getLogger("simple_harness.host_observability")
        self._lock = threading.Lock()
        self._snapshot_sources: dict[str, Callable[[], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]] = {}
        self._degraded_codes: set[str] = set()

        try:
            from simple_harness.observability import (
                CompositeSink,
                JsonlSink,
                LoggingSink,
                RingBufferSink,
            )
        except ImportError:
            self.available = False
            self.ring = _UnavailableRing()
            self.sink = _NoopSink()
            self._degraded_codes.add("sdk_observability_unavailable")
            return

        self.available = True
        self.ring = RingBufferSink(capacity=_RING_CAPACITY)
        children: list[Any] = [self.ring, LoggingSink(self._logger)]
        try:
            children.insert(
                1,
                JsonlSink(
                    self.log_dir / SDK_EVENTS_FILENAME,
                    max_bytes=_JSONL_MAX_BYTES,
                    max_files=_JSONL_MAX_FILES,
                ),
            )
        except BaseException:
            self._degraded_codes.add("sdk_jsonl_sink_unavailable")
        self.sink = CompositeSink(children, child_capacity=256, close_timeout=1.0)

    def register_snapshot_source(
        self, name: str, source: Callable[[], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
    ) -> None:
        with self._lock:
            self._snapshot_sources[name] = source

    def bind_ingress(self, *, run_id: str, session_id: str, request_id: str):
        if not self.available:
            return self.correlation.bind_unavailable(session_id)
        return self.correlation.bind_ingress(
            run_id=run_id, session_id=session_id, request_id=request_id
        )

    def reset_ingress(self, token: Token[tuple[str, Any] | None]) -> None:
        self.correlation.reset(token)

    def export(self) -> Mapping[str, str]:
        """Synchronous compatibility; async sources explicitly require export_async."""
        with self._lock:
            sources = tuple(self._snapshot_sources.items())
        snapshots, degraded = {}, set(self._degraded_codes)
        for name, source in sources:
            try:
                value = source()
                if inspect.isawaitable(value):
                    if inspect.iscoroutine(value):
                        value.close()
                    snapshots[name] = {"health": "degraded", "error_code": "snapshot_requires_async_export"}
                    degraded.add(f"{name}_snapshot_requires_async_export")
                else:
                    snapshots[name] = dict(value)
            except Exception:
                snapshots[name] = {"health": "degraded", "error_code": "snapshot_unavailable"}
                degraded.add(f"{name}_snapshot_unavailable")
        return self._export_snapshots(snapshots, degraded)

    async def export_async(self) -> Mapping[str, str]:
        """Collect SDK-owned aggregate snapshots with a bounded await per source.

        No background tasks or cross-loop database access. Caller cancellation
        propagates; snapshot exceptions are represented without exception text.
        """
        with self._lock:
            sources = tuple(self._snapshot_sources.items())
        snapshots, degraded = {}, set(self._degraded_codes)
        for name, source in sources:
            try:
                value = source()
                if inspect.isawaitable(value):
                    value = await asyncio.wait_for(value, timeout=0.5)
                snapshots[name] = dict(value)
            except Exception:
                snapshots[name] = {"health": "degraded", "error_code": "snapshot_unavailable"}
                degraded.add(f"{name}_snapshot_unavailable")
        return self._export_snapshots(snapshots, degraded)

    def _export_snapshots(self, snapshots, degraded) -> Mapping[str, str]:
        """Best-effort bounded exports; never inspect product payload or databases."""

        statuses: dict[str, str] = {}
        ring_payload = {
            "schema_version": 1,
            "capacity": _RING_CAPACITY,
            "overflow_count": self.ring.overflow_count,
            "events": [event.to_dict() for event in self.ring.events()],
        }
        statuses[SDK_RING_FILENAME] = self._write_bounded_json(
            self.log_dir / SDK_RING_FILENAME, ring_payload
        )

        snapshot_payload = {
            "schema_version": 1,
            "health": "degraded" if degraded else "ok",
            "degraded_codes": sorted(degraded),
            "sources": snapshots,
        }
        statuses[SDK_SNAPSHOT_FILENAME] = self._write_bounded_json(
            self.log_dir / SDK_SNAPSHOT_FILENAME, snapshot_payload
        )
        return statuses

    @staticmethod
    def _write_bounded_json(path: Path, payload: Mapping[str, Any]) -> str:
        try:
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            if len(encoded) > _EXPORT_MAX_BYTES:
                return "degraded:export_too_large"
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                    return "degraded:unsafe_export_path"
            except FileNotFoundError:
                pass
            temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
            descriptor = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                os.chmod(path, 0o600)
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
            return "ok"
        except BaseException:
            return "degraded:export_failed"


__all__ = (
    "HostCorrelationPolicy",
    "HostSdkObservability",
    "SDK_EVENTS_FILENAME",
    "SDK_RING_FILENAME",
    "SDK_SNAPSHOT_FILENAME",
)


class _NoopSink:
    def emit(self, _event: Any) -> None:
        return None


class _UnavailableRing:
    overflow_count = 0

    @staticmethod
    def events() -> tuple[()]:
        return ()
