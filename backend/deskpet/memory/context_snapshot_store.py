# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""CAS store for Context OS task snapshots.

The schema is migration-owned.  This module never creates tables lazily and
never restores an in-memory capability scope from persisted diagnostics.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol

import aiosqlite


JsonValue = Any


class ContextSnapshotError(RuntimeError):
    """Base class for typed snapshot-store failures."""


class SnapshotConflictError(ContextSnapshotError):
    def __init__(self, expected: int, actual: int | None) -> None:
        super().__init__(
            f"context snapshot revision conflict: expected={expected}, actual={actual}"
        )
        self.expected_row_revision = expected
        self.actual_row_revision = actual


class SnapshotMissingError(ContextSnapshotError):
    pass


class SnapshotDataError(ContextSnapshotError):
    pass


class SnapshotStoreDisabled(ContextSnapshotError):
    pass


@dataclass(frozen=True, slots=True)
class ContextSnapshotHandle:
    session_id: str
    task_scope_id: str
    row_revision: int
    snapshot_hash: str


@dataclass(frozen=True, slots=True)
class SnapshotWriteReceipt:
    previous_row_revision: int
    new_handle: ContextSnapshotHandle
    persisted_tool_scope_revision: int | None = None
    attempt_id: str | None = None
    changed: bool = True
    idempotent: bool = False


class SnapshotCommitCancelled(asyncio.CancelledError):
    """Cancellation propagated only after the underlying DB outcome is known."""

    def __init__(
        self,
        *,
        receipt: SnapshotWriteReceipt | None,
        write_error: BaseException | None = None,
        cancellation_count: int = 1,
    ) -> None:
        super().__init__("snapshot write outcome settled before cancellation")
        self.receipt = receipt
        self.write_error = write_error
        self.cancellation_count = cancellation_count


@dataclass(frozen=True, slots=True)
class StoredContextSnapshot:
    handle: ContextSnapshotHandle
    source_revisions: Mapping[str, JsonValue]
    objective: str
    decisions: tuple[JsonValue, ...]
    completed: tuple[JsonValue, ...]
    pending: tuple[JsonValue, ...]
    artifacts: tuple[JsonValue, ...]
    blockers: tuple[JsonValue, ...]
    narrative_summary: str
    prepared_toolset_summary: Mapping[str, JsonValue]
    tool_policy_fingerprint: str | None
    tool_registry_revision: int | None
    last_compaction_cycle_id: str | None
    created_at: str
    updated_at: str


class SupportsSnapshotProjection(Protocol):
    def to_store_record(self) -> Mapping[str, Any]: ...


_PREPARED_TOOLSET_KEYS = frozenset(
    {
        "direct_names",
        "activated_names",
        "schema_hashes",
        "selection_reasons",
        "schema_tokens",
        "provider_adapter_id",
        "provider_adapter_version",
        "wire_payload_hash",
        "wire_tokens",
        "attempt_id",
        "adapter_state",
        "persisted_tool_scope_revision",
        "registry_revision",
        "policy_fingerprint",
        "schema_fingerprint",
    }
)

# Internal exact-once ledger.  This deliberately lives inside the existing
# prepared-toolset JSON column so the v1 store can reject an old cycle after
# newer cycles have committed (A -> B -> A) without a schema migration.  It is
# never accepted from caller-provided summaries; only the store mutates it.
_COMPACTION_CYCLE_IDS_KEY = "_compaction_cycle_ids"


def _committed_cycle_ids(summary: Mapping[str, Any]) -> list[str]:
    value = summary.get(_COMPACTION_CYCLE_IDS_KEY, [])
    if not isinstance(value, list):
        raise SnapshotDataError("stored compaction cycle ledger must be a list")
    if any(not isinstance(item, str) or not item for item in value):
        raise SnapshotDataError("stored compaction cycle ids must be non-empty strings")
    # Preserve order for deterministic JSON while tolerating historical
    # duplicate values should an older build have written them.
    return list(dict.fromkeys(value))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_json(value: Any, *, path: str = "$") -> JsonValue:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise SnapshotDataError(f"non-finite JSON number at {path}")
        return value
    if isinstance(value, (list, tuple)):
        return [
            _validate_json(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise SnapshotDataError(f"non-string JSON key at {path}")
            normalized[key] = _validate_json(item, path=f"{path}.{key}")
        return normalized
    raise SnapshotDataError(f"unsupported JSON value {type(value).__name__} at {path}")


def canonical_json(value: Any) -> str:
    normalized = _validate_json(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _load_json(value: str, *, expected: type) -> JsonValue:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SnapshotDataError(f"invalid snapshot JSON: {exc}") from exc
    normalized = _validate_json(parsed)
    if not isinstance(normalized, expected):
        raise SnapshotDataError(
            f"snapshot JSON expected {expected.__name__}, got {type(normalized).__name__}"
        )
    return normalized


def _projection_record(projection: SupportsSnapshotProjection | Mapping[str, Any]) -> dict[str, Any]:
    raw = (
        projection.to_store_record()
        if hasattr(projection, "to_store_record")
        else projection
    )
    if not isinstance(raw, Mapping):
        raise SnapshotDataError("projection must be a mapping or implement to_store_record")
    session_id = str(raw.get("session_id", "") or "").strip()
    task_scope_id = str(raw.get("task_scope_id", "") or "").strip()
    if not session_id or not task_scope_id:
        raise SnapshotDataError("projection requires session_id and task_scope_id")
    return {
        "session_id": session_id,
        "task_scope_id": task_scope_id,
        "source_revisions": _validate_json(raw.get("source_revisions", {})),
        "objective": str(raw.get("objective", "") or ""),
        "decisions": _validate_json(raw.get("decisions", [])),
        "completed": _validate_json(raw.get("completed", [])),
        "pending": _validate_json(raw.get("pending", [])),
        "artifacts": _validate_json(raw.get("artifacts", [])),
        "blockers": _validate_json(raw.get("blockers", [])),
        "narrative_summary": str(raw.get("narrative_summary", "") or ""),
    }


def sanitize_prepared_toolset_summary(value: Mapping[str, Any] | None) -> dict[str, JsonValue]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise SnapshotDataError("prepared toolset summary must be an object")
    normalized = {
        key: _validate_json(item, path=f"$.{key}")
        for key, item in value.items()
        if key in _PREPARED_TOOLSET_KEYS
    }
    state = normalized.get("adapter_state")
    if state not in (None, "canonical", "prepared"):
        raise SnapshotDataError("adapter_state must be canonical or prepared")
    for key in ("schema_tokens", "wire_tokens", "persisted_tool_scope_revision", "registry_revision"):
        item = normalized.get(key)
        if item is not None and (not isinstance(item, int) or isinstance(item, bool) or item < 0):
            raise SnapshotDataError(f"{key} must be a non-negative integer")
    for key in ("direct_names", "activated_names"):
        item = normalized.get(key)
        if item is not None and (
            not isinstance(item, list) or any(not isinstance(name, str) for name in item)
        ):
            raise SnapshotDataError(f"{key} must be a string array")
    hashes = normalized.get("schema_hashes")
    if hashes is not None and (
        not isinstance(hashes, dict)
        or any(
            not isinstance(name, str) or not isinstance(digest, str)
            for name, digest in hashes.items()
        )
    ):
        raise SnapshotDataError("schema_hashes must map tool names to string digests")
    for key in (
        "provider_adapter_id",
        "provider_adapter_version",
        "wire_payload_hash",
        "attempt_id",
        "policy_fingerprint",
        "schema_fingerprint",
    ):
        item = normalized.get(key)
        if item is not None and not isinstance(item, str):
            raise SnapshotDataError(f"{key} must be a string")
    return normalized


def _snapshot_hash(record: Mapping[str, Any], tool_summary: Mapping[str, Any]) -> str:
    payload = {
        "source_revisions": record["source_revisions"],
        "objective": record["objective"],
        "decisions": record["decisions"],
        "completed": record["completed"],
        "pending": record["pending"],
        "artifacts": record["artifacts"],
        "blockers": record["blockers"],
        "narrative_summary": record["narrative_summary"],
        "prepared_toolset_summary": tool_summary,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


async def await_snapshot_commit_ack(
    write_task: "asyncio.Task[SnapshotWriteReceipt]",
    *,
    on_cancelled_after_commit: Callable[[SnapshotWriteReceipt], Any] | None = None,
) -> SnapshotWriteReceipt:
    """Shield a DB CAS and settle its outcome before propagating cancellation."""

    cancellation_count = 0
    while True:
        try:
            receipt = await asyncio.shield(write_task)
            break
        except asyncio.CancelledError:
            cancellation_count += 1
            if write_task.cancelled():
                raise SnapshotCommitCancelled(
                    receipt=None,
                    write_error=asyncio.CancelledError("underlying snapshot write cancelled"),
                    cancellation_count=cancellation_count,
                )
            continue
        except BaseException as exc:
            if cancellation_count:
                raise SnapshotCommitCancelled(
                    receipt=None,
                    write_error=exc,
                    cancellation_count=cancellation_count,
                ) from exc
            raise

    if cancellation_count == 0:
        return receipt
    if on_cancelled_after_commit is not None:
        diagnostic = on_cancelled_after_commit(receipt)
        if inspect.isawaitable(diagnostic):
            await diagnostic
    raise SnapshotCommitCancelled(
        receipt=receipt,
        cancellation_count=cancellation_count,
    )


class ContextSnapshotStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        enabled: bool = True,
        fault_hook: Callable[[str], Awaitable[None] | None] | None = None,
        diagnostic_sink: Callable[[str, Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._enabled = bool(enabled)
        self._fault_hook = fault_hook
        self._diagnostic_sink = diagnostic_sink

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise SnapshotStoreDisabled("Context OS snapshot store is disabled")

    async def _fault(self, stage: str) -> None:
        if self._fault_hook is None:
            return
        value = self._fault_hook(stage)
        if inspect.isawaitable(value):
            await value

    async def _diagnostic(self, event: str, payload: Mapping[str, Any]) -> None:
        if self._diagnostic_sink is None:
            return
        try:
            result = self._diagnostic_sink(event, payload)
            if inspect.isawaitable(result):
                await result
        except Exception:
            # Diagnostics must never turn a corrupt optional cache into a chat
            # failure.  The authoritative task stores remain untouched.
            return

    async def get(self, session_id: str, task_scope_id: str) -> StoredContextSnapshot | None:
        if not self._enabled:
            return None
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM session_context_snapshots "
                "WHERE session_id=? AND task_scope_id=?",
                (session_id, task_scope_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            return None
        try:
            return self._decode_row(row)
        except SnapshotDataError as exc:
            await self._diagnostic(
                "context_snapshot_corrupt",
                {
                    "session_id": session_id,
                    "task_scope_id": task_scope_id,
                    "error": str(exc),
                },
            )
            return None

    @staticmethod
    def _decode_row(row: aiosqlite.Row) -> StoredContextSnapshot:
        source_revisions = _load_json(row["source_revisions_json"], expected=dict)
        decisions = _load_json(row["decisions_json"], expected=list)
        completed = _load_json(row["completed_json"], expected=list)
        pending = _load_json(row["pending_json"], expected=list)
        artifacts = _load_json(row["artifacts_json"], expected=list)
        blockers = _load_json(row["blockers_json"], expected=list)
        tool_summary = _load_json(row["last_prepared_toolset_json"], expected=dict)
        assert isinstance(source_revisions, dict)
        assert isinstance(tool_summary, dict)
        # Store-private exact-once metadata is not part of the public prepared
        # toolset contract consumed by assemblers or diagnostics.
        tool_summary.pop(_COMPACTION_CYCLE_IDS_KEY, None)
        return StoredContextSnapshot(
            handle=ContextSnapshotHandle(
                session_id=str(row["session_id"]),
                task_scope_id=str(row["task_scope_id"]),
                row_revision=int(row["revision"]),
                snapshot_hash=str(row["snapshot_hash"]),
            ),
            source_revisions=source_revisions,
            objective=str(row["objective"]),
            decisions=tuple(decisions),
            completed=tuple(completed),
            pending=tuple(pending),
            artifacts=tuple(artifacts),
            blockers=tuple(blockers),
            narrative_summary=str(row["narrative_summary"]),
            prepared_toolset_summary=tool_summary,
            tool_policy_fingerprint=(
                str(row["tool_policy_fingerprint"])
                if row["tool_policy_fingerprint"] is not None
                else None
            ),
            tool_registry_revision=(
                int(row["tool_registry_revision"])
                if row["tool_registry_revision"] is not None
                else None
            ),
            last_compaction_cycle_id=(
                str(row["last_compaction_cycle_id"])
                if row["last_compaction_cycle_id"] is not None
                else None
            ),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    async def compare_and_swap(
        self,
        projection: SupportsSnapshotProjection | Mapping[str, Any],
        *,
        expected_row_revision: int,
    ) -> SnapshotWriteReceipt:
        return await self._write_projection(
            projection,
            expected_row_revision=expected_row_revision,
            prepared_toolset_summary=None,
            last_compaction_cycle_id=None,
        )

    async def persist_projection_with_tool_context_cas(
        self,
        projection: SupportsSnapshotProjection | Mapping[str, Any],
        *,
        expected_row_revision: int,
        prepared_toolset_summary: Mapping[str, Any],
    ) -> SnapshotWriteReceipt:
        return await self._write_projection(
            projection,
            expected_row_revision=expected_row_revision,
            prepared_toolset_summary=prepared_toolset_summary,
            last_compaction_cycle_id=None,
        )

    async def flush_once(
        self,
        cycle_id: str,
        projection: SupportsSnapshotProjection | Mapping[str, Any],
        *,
        expected_row_revision: int,
        prepared_toolset_summary: Mapping[str, Any] | None = None,
    ) -> SnapshotWriteReceipt:
        if not str(cycle_id or "").strip():
            raise SnapshotDataError("cycle_id must be non-empty")
        return await self._write_projection(
            projection,
            expected_row_revision=expected_row_revision,
            prepared_toolset_summary=prepared_toolset_summary,
            last_compaction_cycle_id=str(cycle_id),
        )

    async def _write_projection(
        self,
        projection: SupportsSnapshotProjection | Mapping[str, Any],
        *,
        expected_row_revision: int,
        prepared_toolset_summary: Mapping[str, Any] | None,
        last_compaction_cycle_id: str | None,
    ) -> SnapshotWriteReceipt:
        self._require_enabled()
        if expected_row_revision < 0:
            raise SnapshotDataError("expected_row_revision must be non-negative")
        record = _projection_record(projection)
        now = _utc_now()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            try:
                await db.execute("BEGIN IMMEDIATE")
                row = await self._fetch_row(db, record["session_id"], record["task_scope_id"])
                actual = int(row["revision"]) if row is not None else None
                if expected_row_revision == 0:
                    if row is not None:
                        raise SnapshotConflictError(0, actual)
                    current_tool_summary: dict[str, JsonValue] = {}
                    created_at = now
                else:
                    if row is None:
                        raise SnapshotMissingError(
                            f"snapshot missing: {record['session_id']}/{record['task_scope_id']}"
                        )
                    if actual != expected_row_revision:
                        raise SnapshotConflictError(expected_row_revision, actual)
                    loaded = _load_json(row["last_prepared_toolset_json"], expected=dict)
                    assert isinstance(loaded, dict)
                    current_tool_summary = loaded
                    created_at = str(row["created_at"])
                    if last_compaction_cycle_id is not None and (
                        last_compaction_cycle_id
                        in _committed_cycle_ids(current_tool_summary)
                        or row["last_compaction_cycle_id"] == last_compaction_cycle_id
                    ):
                        await db.rollback()
                        return self._receipt_from_row(
                            row,
                            previous=expected_row_revision,
                            changed=False,
                            idempotent=True,
                        )

                tool_summary = (
                    sanitize_prepared_toolset_summary(prepared_toolset_summary)
                    if prepared_toolset_summary is not None
                    else current_tool_summary
                )
                committed_cycle_ids = _committed_cycle_ids(current_tool_summary)
                if last_compaction_cycle_id is not None:
                    committed_cycle_ids.append(last_compaction_cycle_id)
                if committed_cycle_ids:
                    tool_summary[_COMPACTION_CYCLE_IDS_KEY] = list(
                        dict.fromkeys(committed_cycle_ids)
                    )
                digest = _snapshot_hash(record, tool_summary)
                if (
                    row is not None
                    and digest == str(row["snapshot_hash"])
                    and last_compaction_cycle_id is None
                ):
                    await db.rollback()
                    return self._receipt_from_row(
                        row,
                        previous=expected_row_revision,
                        changed=False,
                        idempotent=False,
                    )

                new_revision = 1 if row is None else expected_row_revision + 1
                effective_cycle_id = (
                    last_compaction_cycle_id
                    if last_compaction_cycle_id is not None
                    else str(row["last_compaction_cycle_id"])
                    if row is not None and row["last_compaction_cycle_id"] is not None
                    else None
                )
                values = self._projection_values(
                    record,
                    tool_summary,
                    revision=new_revision,
                    snapshot_hash=digest,
                    created_at=created_at,
                    updated_at=now,
                    last_compaction_cycle_id=effective_cycle_id,
                )
                if row is None:
                    await db.execute(
                        "INSERT INTO session_context_snapshots(" + ",".join(values) + ") "
                        "VALUES (" + ",".join("?" for _ in values) + ")",
                        tuple(values.values()),
                    )
                else:
                    assignments = ",".join(
                        f"{name}=?" for name in values if name not in {"session_id", "task_scope_id"}
                    )
                    params = [
                        value
                        for name, value in values.items()
                        if name not in {"session_id", "task_scope_id"}
                    ]
                    params.extend(
                        [record["session_id"], record["task_scope_id"], expected_row_revision]
                    )
                    cursor = await db.execute(
                        f"UPDATE session_context_snapshots SET {assignments} "
                        "WHERE session_id=? AND task_scope_id=? AND revision=?",
                        tuple(params),
                    )
                    if cursor.rowcount != 1:
                        raise SnapshotConflictError(expected_row_revision, None)
                await self._fault("before_commit")
                await db.commit()
                receipt = SnapshotWriteReceipt(
                    previous_row_revision=expected_row_revision,
                    new_handle=ContextSnapshotHandle(
                        record["session_id"],
                        record["task_scope_id"],
                        new_revision,
                        digest,
                    ),
                    persisted_tool_scope_revision=self._summary_int(
                        tool_summary, "persisted_tool_scope_revision"
                    ),
                    attempt_id=self._summary_str(tool_summary, "attempt_id"),
                )
            except BaseException:
                await db.rollback()
                raise
        await self._fault("after_commit")
        return receipt

    async def update_tool_context_cas(
        self,
        session_id: str,
        task_scope_id: str,
        *,
        expected_row_revision: int,
        prepared_toolset_summary: Mapping[str, Any],
    ) -> SnapshotWriteReceipt:
        self._require_enabled()
        incoming = sanitize_prepared_toolset_summary(prepared_toolset_summary)
        now = _utc_now()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            try:
                await db.execute("BEGIN IMMEDIATE")
                row = await self._fetch_row(db, session_id, task_scope_id)
                if row is None:
                    raise SnapshotMissingError(f"snapshot missing: {session_id}/{task_scope_id}")
                actual = int(row["revision"])
                if actual != expected_row_revision:
                    raise SnapshotConflictError(expected_row_revision, actual)
                current = _load_json(row["last_prepared_toolset_json"], expected=dict)
                assert isinstance(current, dict)
                merged = dict(current)
                merged.update(incoming)
                merged = sanitize_prepared_toolset_summary(merged)
                committed_cycle_ids = _committed_cycle_ids(current)
                if committed_cycle_ids:
                    merged[_COMPACTION_CYCLE_IDS_KEY] = committed_cycle_ids
                record = self._record_from_row(row)
                digest = _snapshot_hash(record, merged)
                if digest == str(row["snapshot_hash"]):
                    await db.rollback()
                    return self._receipt_from_row(
                        row,
                        previous=expected_row_revision,
                        changed=False,
                        idempotent=False,
                    )
                new_revision = expected_row_revision + 1
                cursor = await db.execute(
                    "UPDATE session_context_snapshots SET revision=?, "
                    "last_prepared_toolset_json=?, tool_policy_fingerprint=?, "
                    "tool_registry_revision=?, snapshot_hash=?, updated_at=? "
                    "WHERE session_id=? AND task_scope_id=? AND revision=?",
                    (
                        new_revision,
                        canonical_json(merged),
                        self._summary_str(merged, "policy_fingerprint"),
                        self._summary_int(merged, "registry_revision"),
                        digest,
                        now,
                        session_id,
                        task_scope_id,
                        expected_row_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise SnapshotConflictError(expected_row_revision, None)
                await self._fault("before_commit")
                await db.commit()
                receipt = SnapshotWriteReceipt(
                    previous_row_revision=expected_row_revision,
                    new_handle=ContextSnapshotHandle(
                        session_id, task_scope_id, new_revision, digest
                    ),
                    persisted_tool_scope_revision=self._summary_int(
                        merged, "persisted_tool_scope_revision"
                    ),
                    attempt_id=self._summary_str(merged, "attempt_id"),
                )
            except BaseException:
                await db.rollback()
                raise
        await self._fault("after_commit")
        return receipt

    async def delete_session_scope(self, session_id: str) -> int:
        """Delete only the derived ``session:*`` row for a deleted session."""

        self._require_enabled()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "DELETE FROM session_context_snapshots "
                "WHERE session_id=? AND task_scope_id=?",
                (session_id, f"session:{session_id}"),
            )
            await db.commit()
            return int(cursor.rowcount)

    @staticmethod
    async def _fetch_row(
        db: aiosqlite.Connection,
        session_id: str,
        task_scope_id: str,
    ) -> aiosqlite.Row | None:
        cursor = await db.execute(
            "SELECT * FROM session_context_snapshots "
            "WHERE session_id=? AND task_scope_id=?",
            (session_id, task_scope_id),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row

    @staticmethod
    def _record_from_row(row: aiosqlite.Row) -> dict[str, Any]:
        return {
            "source_revisions": _load_json(row["source_revisions_json"], expected=dict),
            "objective": str(row["objective"]),
            "decisions": _load_json(row["decisions_json"], expected=list),
            "completed": _load_json(row["completed_json"], expected=list),
            "pending": _load_json(row["pending_json"], expected=list),
            "artifacts": _load_json(row["artifacts_json"], expected=list),
            "blockers": _load_json(row["blockers_json"], expected=list),
            "narrative_summary": str(row["narrative_summary"]),
        }

    @staticmethod
    def _projection_values(
        record: Mapping[str, Any],
        tool_summary: Mapping[str, Any],
        *,
        revision: int,
        snapshot_hash: str,
        created_at: str,
        updated_at: str,
        last_compaction_cycle_id: str | None,
    ) -> dict[str, Any]:
        return {
            "session_id": record["session_id"],
            "task_scope_id": record["task_scope_id"],
            "revision": revision,
            "source_revisions_json": canonical_json(record["source_revisions"]),
            "objective": record["objective"],
            "decisions_json": canonical_json(record["decisions"]),
            "completed_json": canonical_json(record["completed"]),
            "pending_json": canonical_json(record["pending"]),
            "artifacts_json": canonical_json(record["artifacts"]),
            "blockers_json": canonical_json(record["blockers"]),
            "narrative_summary": record["narrative_summary"],
            "last_prepared_toolset_json": canonical_json(tool_summary),
            "tool_policy_fingerprint": ContextSnapshotStore._summary_str(
                tool_summary, "policy_fingerprint"
            ),
            "tool_registry_revision": ContextSnapshotStore._summary_int(
                tool_summary, "registry_revision"
            ),
            "last_compaction_cycle_id": last_compaction_cycle_id,
            "snapshot_hash": snapshot_hash,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    @staticmethod
    def _summary_int(summary: Mapping[str, Any], key: str) -> int | None:
        value = summary.get(key)
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None

    @staticmethod
    def _summary_str(summary: Mapping[str, Any], key: str) -> str | None:
        value = summary.get(key)
        return str(value) if isinstance(value, str) and value else None

    @staticmethod
    def _receipt_from_row(
        row: aiosqlite.Row,
        *,
        previous: int,
        changed: bool,
        idempotent: bool,
    ) -> SnapshotWriteReceipt:
        summary = _load_json(row["last_prepared_toolset_json"], expected=dict)
        assert isinstance(summary, dict)
        return SnapshotWriteReceipt(
            previous_row_revision=previous,
            new_handle=ContextSnapshotHandle(
                str(row["session_id"]),
                str(row["task_scope_id"]),
                int(row["revision"]),
                str(row["snapshot_hash"]),
            ),
            persisted_tool_scope_revision=ContextSnapshotStore._summary_int(
                summary, "persisted_tool_scope_revision"
            ),
            attempt_id=ContextSnapshotStore._summary_str(summary, "attempt_id"),
            changed=changed,
            idempotent=idempotent,
        )


__all__ = [
    "ContextSnapshotError",
    "ContextSnapshotHandle",
    "ContextSnapshotStore",
    "SnapshotCommitCancelled",
    "SnapshotConflictError",
    "SnapshotDataError",
    "SnapshotMissingError",
    "SnapshotStoreDisabled",
    "SnapshotWriteReceipt",
    "StoredContextSnapshot",
    "await_snapshot_commit_ack",
    "canonical_json",
    "sanitize_prepared_toolset_summary",
]
