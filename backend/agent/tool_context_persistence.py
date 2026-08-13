"""Durable tool-context persistence collaborator for AgentLoop."""

from __future__ import annotations

import asyncio
from typing import Any


class ToolContextPersistenceMixin:
    """Keep snapshot CAS mechanics out of the provider completion loop."""

    async def _persist_attempt_tool_context(
        self,
        *,
        session_id: str,
        request_id: str,
        attempt_id: str,
        prepared_context: Any,
        current_tool_set: Any,
        report: Any,
    ) -> None:
        if (
            self.context_snapshot_store is None
            or prepared_context is None
            or current_tool_set is None
        ):
            return
        scope_store = getattr(self.tools, "capability_scope_store", None)
        if scope_store is None:
            return
        record = scope_store.get(
            current_tool_set.scope_id,
            session_id=session_id,
            request_id=request_id,
        )
        if record is None:
            raise RuntimeError("tool_context_persist_failed:scope_missing")
        handle = record.snapshot_handle or getattr(
            prepared_context, "active_snapshot_handle", None
        )
        if handle is None:
            return
        schema_hashes = {
            cap.ref.name: cap.ref.schema_hash
            for cap in (*current_tool_set.direct, *current_tool_set.activated)
        }
        summary = {
            "direct_names": [cap.ref.name for cap in current_tool_set.direct],
            "activated_names": [cap.ref.name for cap in current_tool_set.activated],
            "schema_hashes": schema_hashes,
            "selection_reasons": [
                {
                    "name": decision.name,
                    "disposition": decision.disposition,
                    "reason": decision.reason,
                }
                for decision in current_tool_set.decisions
            ],
            "schema_tokens": int(report.tool_tokens),
            "provider_adapter_id": report.provider_id or report.adapter_id,
            "provider_adapter_version": report.adapter_version,
            "wire_payload_hash": report.wire_tool_hash,
            "wire_tokens": int(report.tool_tokens),
            "attempt_id": attempt_id,
            "adapter_state": "prepared",
            "persisted_tool_scope_revision": int(current_tool_set.revision),
            "registry_revision": int(current_tool_set.registry_revision),
            "policy_fingerprint": current_tool_set.policy_fingerprint,
            "schema_fingerprint": current_tool_set.schema_fingerprint,
        }
        from deskpet.memory.context_snapshot_store import (
            SnapshotCommitCancelled,
            SnapshotConflictError,
            await_snapshot_commit_ack,
        )

        async with scope_store.lock_for(current_tool_set.scope_id):
            current_record = scope_store.get(
                current_tool_set.scope_id,
                session_id=session_id,
                request_id=request_id,
            )
            if current_record is None:
                raise RuntimeError("tool_context_persist_failed:scope_expired")
            handle = current_record.snapshot_handle or handle

            async def _write(expected_revision: int):
                task = asyncio.create_task(
                    self.context_snapshot_store.update_tool_context_cas(
                        session_id,
                        handle.task_scope_id,
                        expected_row_revision=expected_revision,
                        prepared_toolset_summary=summary,
                    )
                )
                return await await_snapshot_commit_ack(task)

            try:
                receipt = await _write(int(handle.row_revision))
            except SnapshotConflictError:
                latest = await self.context_snapshot_store.get(
                    session_id, handle.task_scope_id
                )
                if latest is None:
                    raise RuntimeError("tool_context_persist_failed:snapshot_missing")
                persisted = latest.prepared_toolset_summary
                if (
                    persisted.get("schema_fingerprint")
                    not in (None, "", current_tool_set.schema_fingerprint)
                    or persisted.get("persisted_tool_scope_revision")
                    not in (None, current_tool_set.revision)
                ):
                    raise RuntimeError("tool_context_persist_failed:cas_conflict")
                try:
                    handle = latest.handle
                    receipt = await _write(int(handle.row_revision))
                except SnapshotConflictError as exc:
                    raise RuntimeError(
                        "tool_context_persist_failed:cas_conflict"
                    ) from exc
            except SnapshotCommitCancelled as exc:
                if exc.receipt is not None:
                    scope_store.advance_snapshot_handle_prevalidated(
                        current_tool_set.scope_id, exc.receipt.new_handle
                    )
                    prepared_context.active_snapshot_handle = exc.receipt.new_handle
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise RuntimeError("tool_context_persist_failed") from exc

            scope_store.advance_snapshot_handle_prevalidated(
                current_tool_set.scope_id, receipt.new_handle
            )
            prepared_context.active_snapshot_handle = receipt.new_handle

    async def _persist_activation_tool_context_locked(
        self,
        *,
        session_id: str,
        scope_store: Any,
        scope_record: Any,
        candidate: Any,
        prepared_context: Any,
        tool_payload: Any,
    ) -> Any:
        if self.context_snapshot_store is None or prepared_context is None:
            return None
        record_handle = getattr(scope_record, "snapshot_handle", None)
        prepared_handle = getattr(prepared_context, "active_snapshot_handle", None)
        handles = [item for item in (record_handle, prepared_handle) if item is not None]
        if not handles:
            return None
        handle = max(handles, key=lambda item: int(getattr(item, "row_revision", 0)))
        summary = self._prepared_toolset_summary(candidate)
        summary.update(
            {
                "selection_reasons": [
                    {
                        "name": decision.name,
                        "disposition": decision.disposition,
                        "reason": decision.reason,
                    }
                    for decision in tuple(getattr(candidate, "decisions", ()) or ())
                ],
                "schema_tokens": int(getattr(tool_payload, "wire_tokens", 0) or 0),
                "wire_tokens": int(getattr(tool_payload, "wire_tokens", 0) or 0),
                "adapter_state": "prepared",
            }
        )
        from deskpet.memory.context_snapshot_store import (
            SnapshotCommitCancelled,
            SnapshotConflictError,
            await_snapshot_commit_ack,
        )

        write_task = asyncio.create_task(
            self.context_snapshot_store.update_tool_context_cas(
                session_id,
                handle.task_scope_id,
                expected_row_revision=int(handle.row_revision),
                prepared_toolset_summary=summary,
            )
        )
        try:
            return await await_snapshot_commit_ack(write_task)
        except SnapshotConflictError as exc:
            raise RuntimeError("tool_activation_snapshot_conflict") from exc
        except SnapshotCommitCancelled as exc:
            if exc.receipt is not None:
                scope_store.advance_snapshot_handle_prevalidated(
                    candidate.scope_id, exc.receipt.new_handle
                )
                prepared_context.active_snapshot_handle = exc.receipt.new_handle
            raise


__all__ = ["ToolContextPersistenceMixin"]
