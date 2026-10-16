"""Snapshot manifests, continuation sources, lineage and child runs from snapshots."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .core import ResearchRepositoryCore
from .support import _CONTINUABLE_DELIVERY, ResearchRepositoryError, _canonical


class SnapshotLineageMixin(ResearchRepositoryCore):
    """Snapshot manifests, continuation sources, lineage and child runs from snapshots."""

    __slots__ = ()

    async def persist_snapshot_manifest(
        self,
        *,
        run_id: str,
        operation_id: str,
        manifest: Mapping[str, Any],
        manifest_ref: str,
        continue_until: float,
        pin_kind: str = "continue_parent",
        pin_id: str | None = None,
    ) -> tuple[str, bool]:
        """Persist and parent-pin a content-addressed manifest atomically."""

        content = copy.deepcopy(dict(manifest))
        declared_hash = content.pop("snapshot_hash", None)
        encoded = _canonical(content)
        snapshot_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if declared_hash is not None and declared_hash != snapshot_hash:
            raise ResearchRepositoryError(
                "snapshot_hash_mismatch", "manifest hash is not canonical"
            )
        schema_version = content.get("schema_version")
        if not isinstance(schema_version, int) or schema_version <= 0:
            raise ResearchRepositoryError(
                "snapshot_schema_invalid", "snapshot schema_version must be positive"
            )
        if content.get("parent_run_id") != run_id:
            raise ResearchRepositoryError(
                "snapshot_parent_mismatch", "manifest parent does not match run"
            )
        if not manifest_ref or continue_until <= float(self._clock()):
            raise ResearchRepositoryError(
                "snapshot_expiry_invalid", "snapshot must have a future continue_until"
            )
        if len(manifest_ref) != 64 or any(ch not in "0123456789abcdef" for ch in manifest_ref):
            raise ResearchRepositoryError(
                "snapshot_manifest_ref_invalid", "manifest_ref must be a blob SHA-256"
            )
        if manifest_ref != snapshot_hash:
            raise ResearchRepositoryError(
                "snapshot_manifest_ref_mismatch",
                "manifest_ref must address the exact canonical snapshot manifest",
            )
        passage_refs = content.get("passage_blob_refs")
        if not isinstance(passage_refs, list) or any(
            not isinstance(ref, str)
            or len(ref) != 64
            or any(ch not in "0123456789abcdef" for ch in ref)
            for ref in passage_refs
        ):
            raise ResearchRepositoryError(
                "snapshot_passage_refs_invalid", "snapshot passages must be blob SHA-256 references"
            )
        now = float(self._clock())
        pin_id = (
            pin_id or hashlib.sha256(f"{snapshot_hash}|{run_id}|{pin_kind}".encode()).hexdigest()
        )
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            run = await (
                await db.execute("SELECT run_id FROM workflow_runs WHERE run_id=?", (run_id,))
            ).fetchone()
            if run is None:
                raise ResearchRepositoryError("run_not_found", "snapshot run was not found")
            blob_refs = {manifest_ref, *passage_refs}
            placeholders = ",".join("?" for _ in blob_refs)
            registered = await (
                await db.execute(
                    f"SELECT sha256 FROM workflow_blobs WHERE sha256 IN ({placeholders})",
                    tuple(sorted(blob_refs)),
                )
            ).fetchall()
            registered_refs = {str(row[0]) for row in registered}
            if registered_refs != blob_refs:
                raise ResearchRepositoryError(
                    "snapshot_blob_missing", "snapshot manifest or passage blob is not registered"
                )
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_research_snapshots WHERE snapshot_hash=?",
                    (snapshot_hash,),
                )
            ).fetchone()
            created = existing is None
            if existing is not None and (
                existing["run_id"] != run_id
                or existing["operation_id"] != operation_id
                or int(existing["schema_version"]) != schema_version
                or existing["manifest_ref"] != manifest_ref
                or float(existing["expires_at"]) != float(continue_until)
            ):
                raise ResearchRepositoryError(
                    "snapshot_conflict", "snapshot hash has conflicting metadata"
                )
            if existing is None:
                await db.execute(
                    """INSERT INTO workflow_research_snapshots(
                    snapshot_hash,run_id,operation_id,schema_version,manifest_ref,created_at,expires_at
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        snapshot_hash,
                        run_id,
                        operation_id,
                        schema_version,
                        manifest_ref,
                        now,
                        continue_until,
                    ),
                )
            for blob_ref in sorted(blob_refs):
                await db.execute(
                    """INSERT OR IGNORE INTO workflow_blob_refs(
                    sha256,owner_kind,owner_id,created_at
                    ) VALUES(?,'research_snapshot',?,?)""",
                    (blob_ref, snapshot_hash, now),
                )
            await db.execute(
                """INSERT INTO workflow_research_snapshot_pins(
                pin_id,snapshot_hash,run_id,pin_kind,expires_at,created_at
                ) VALUES(?,?,?,?,?,?) ON CONFLICT(snapshot_hash,run_id,pin_kind) DO NOTHING""",
                (pin_id, snapshot_hash, run_id, pin_kind, continue_until, now),
            )
            pin = await (
                await db.execute(
                    """SELECT * FROM workflow_research_snapshot_pins
                    WHERE snapshot_hash=? AND run_id=? AND pin_kind=?""",
                    (snapshot_hash, run_id, pin_kind),
                )
            ).fetchone()
            if pin is None or float(pin["expires_at"]) != float(continue_until):
                raise ResearchRepositoryError(
                    "snapshot_pin_conflict", "parent snapshot pin conflicts"
                )
            await db.commit()
            return snapshot_hash, created
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def resolve_continuation_source(self, parent_run_id: str) -> dict[str, Any]:
        """Resolve the only authoritative, currently continuable parent snapshot.

        The UI deliberately has no role in choosing lineage or snapshot identity.
        A later create transaction revalidates every value returned here, so an
        expiry or terminal-state change between resolution and creation fails
        closed.
        """

        now = float(self._clock())
        await self.initialize()
        db = await self._connect()
        try:
            parent = await (
                await db.execute(
                    "SELECT status,workflow_version FROM workflow_runs WHERE run_id=?",
                    (parent_run_id,),
                )
            ).fetchone()
            if (
                parent is None
                or str(parent["status"]) != "completed"
                or str(parent["workflow_version"]) != "v5"
            ):
                raise ResearchRepositoryError(
                    "parent_not_continuable", "parent is not a completed v5 run"
                )
            lineage = await (
                await db.execute(
                    "SELECT * FROM workflow_research_lineage WHERE run_id=?",
                    (parent_run_id,),
                )
            ).fetchone()
            if lineage is None:
                raise ResearchRepositoryError(
                    "parent_lineage_missing", "parent lineage was not found"
                )
            snapshot = await (
                await db.execute(
                    """SELECT s.* FROM workflow_research_snapshots s
                    JOIN workflow_research_snapshot_pins p
                      ON p.snapshot_hash=s.snapshot_hash AND p.run_id=s.run_id
                    WHERE s.run_id=? AND s.operation_id=?
                      AND p.pin_kind='continue_parent'
                      AND (p.expires_at IS NULL OR p.expires_at>?)
                      AND (s.expires_at IS NULL OR s.expires_at>?)
                    ORDER BY s.created_at DESC,s.snapshot_hash DESC LIMIT 1""",
                    (parent_run_id, str(lineage["operation_id"]), now, now),
                )
            ).fetchone()
            if snapshot is None:
                raise ResearchRepositoryError(
                    "snapshot_expired", "continuation snapshot is missing or expired"
                )
            events = await (
                await db.execute(
                    """SELECT payload_json FROM workflow_events
                    WHERE run_id=? AND event_type='workflow.final' ORDER BY seq DESC""",
                    (parent_run_id,),
                )
            ).fetchall()
            final_payload: dict[str, Any] | None = None
            for event in events:
                payload = json.loads(str(event["payload_json"]))
                if payload.get("delivery_status") in _CONTINUABLE_DELIVERY:
                    final_payload = payload
                    break
            if final_payload is None:
                raise ResearchRepositoryError(
                    "parent_delivery_not_continuable", "parent delivery is not continuable"
                )
            return {
                "parent_run_id": parent_run_id,
                "parent_operation_id": str(lineage["operation_id"]),
                "snapshot_hash": str(snapshot["snapshot_hash"]),
                "manifest_ref": str(snapshot["manifest_ref"]),
                "parent_report_ref": (
                    str(final_payload["report_ref"])
                    if final_payload.get("report_ref") is not None
                    else None
                ),
                "delivery_status": str(final_payload["delivery_status"]),
            }
        finally:
            await db.close()

    async def ensure_root_lineage(
        self, *, run_id: str, operation_id: str, budget_lease_id: str
    ) -> tuple[dict[str, Any], bool]:
        now = float(self._clock())
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_research_lineage WHERE operation_id=? OR run_id=?",
                    (operation_id, run_id),
                )
            ).fetchone()
            if existing is not None:
                if (
                    existing["operation_id"] != operation_id
                    or existing["run_id"] != run_id
                    or existing["parent_run_id"] is not None
                    or existing["budget_lease_id"] != budget_lease_id
                ):
                    raise ResearchRepositoryError("lineage_conflict", "root lineage is immutable")
                await db.commit()
                return dict(existing), False
            await db.execute(
                """INSERT INTO workflow_research_lineage(
                operation_id,run_id,parent_run_id,parent_operation_id,snapshot_hash,
                parent_report_ref,budget_lease_id,created_at
                ) VALUES(?,?,NULL,NULL,NULL,NULL,?,?)""",
                (operation_id, run_id, budget_lease_id, now),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_research_lineage WHERE operation_id=?", (operation_id,)
                )
            ).fetchone()
            await db.commit()
            assert row is not None
            return dict(row), True
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

    async def ensure_snapshot_lineage(
        self, *, run_id: str, operation_id: str, budget_lease_id: str
    ) -> tuple[dict[str, Any], bool]:
        """Ensure a snapshot owner exists without rewriting child lineage."""

        await self.initialize()
        db = await self._connect()
        try:
            existing = await (
                await db.execute(
                    """SELECT * FROM workflow_research_lineage
                    WHERE operation_id=? OR run_id=?""",
                    (operation_id, run_id),
                )
            ).fetchone()
        finally:
            await db.close()
        if existing is not None:
            if str(existing["operation_id"]) != operation_id or str(existing["run_id"]) != run_id:
                raise ResearchRepositoryError(
                    "lineage_conflict", "snapshot lineage identity is immutable"
                )
            return dict(existing), False
        return await self.ensure_root_lineage(
            run_id=run_id,
            operation_id=operation_id,
            budget_lease_id=budget_lease_id,
        )

    async def create_child_from_snapshot(
        self,
        *,
        parent_run_id: str,
        parent_operation_id: str,
        snapshot_hash: str,
        child_run_id: str,
        child_operation_id: str,
        child_trace_id: str,
        child_thread_id: str,
        child_request_key: str,
        child_request_id: str,
        child_turn_id: str,
        budget_lease_id: str,
        start_payload: Mapping[str, Any],
        parent_report_ref: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Create a continuation run, pin, lineage and start payload in one txn."""

        now = float(self._clock())
        request_hash = hashlib.sha256(_canonical(dict(start_payload)).encode("utf-8")).hexdigest()
        start_operation_id = f"{child_operation_id}:start"
        if start_payload.get("snapshot_hash") != snapshot_hash:
            raise ResearchRepositoryError(
                "child_start_snapshot_mismatch", "child start payload must bind the snapshot hash"
            )
        await self.initialize()
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            existing = await (
                await db.execute(
                    "SELECT * FROM workflow_research_lineage WHERE operation_id=? OR run_id=?",
                    (child_operation_id, child_run_id),
                )
            ).fetchone()
            if existing is not None:
                child = await (
                    await db.execute(
                        """SELECT trace_id,thread_id,request_id,turn_id
                        FROM workflow_runs WHERE run_id=?""",
                        (child_run_id,),
                    )
                ).fetchone()
                start = await (
                    await db.execute(
                        """SELECT request_hash FROM workflow_operations
                        WHERE operation_id=? AND run_id=? AND operation_kind='research_continue_start'""",
                        (start_operation_id, child_run_id),
                    )
                ).fetchone()
                start_request = await (
                    await db.execute(
                        "SELECT request_key FROM workflow_start_requests WHERE run_id=?",
                        (child_run_id,),
                    )
                ).fetchone()
                if (
                    existing["operation_id"] != child_operation_id
                    or existing["run_id"] != child_run_id
                    or existing["parent_run_id"] != parent_run_id
                    or existing["parent_operation_id"] != parent_operation_id
                    or existing["snapshot_hash"] != snapshot_hash
                    or existing["parent_report_ref"] != parent_report_ref
                    or existing["budget_lease_id"] != budget_lease_id
                    or child is None
                    or child["trace_id"] != child_trace_id
                    or child["thread_id"] != child_thread_id
                    or child["request_id"] != child_request_id
                    or child["turn_id"] != child_turn_id
                    or start is None
                    or start["request_hash"] != request_hash
                    or start_request is None
                    or start_request["request_key"] != child_request_key
                ):
                    raise ResearchRepositoryError("lineage_conflict", "child lineage is immutable")
                await db.commit()
                return dict(existing), False
            parent = await (
                await db.execute("SELECT * FROM workflow_runs WHERE run_id=?", (parent_run_id,))
            ).fetchone()
            if (
                parent is None
                or parent["status"] != "completed"
                or str(parent["workflow_version"]) != "v5"
            ):
                raise ResearchRepositoryError(
                    "parent_not_continuable", "parent is not a completed v5 run"
                )
            parent_capability = await (
                await db.execute(
                    "SELECT snapshot_json FROM workflow_capabilities WHERE capability_hash=?",
                    (parent["capability_hash"],),
                )
            ).fetchone()
            if parent_capability is None:
                raise ResearchRepositoryError(
                    "parent_start_snapshot_missing", "parent start snapshot was not found"
                )
            parent_snapshot = json.loads(str(parent_capability["snapshot_json"]))
            parent_start = (
                parent_snapshot.get("_workflow_start")
                if isinstance(parent_snapshot, Mapping)
                else None
            )
            parent_identity = (
                parent_start.get("identity") if isinstance(parent_start, Mapping) else None
            )
            if not isinstance(parent_snapshot, Mapping):
                raise ResearchRepositoryError(
                    "parent_start_snapshot_invalid", "parent start snapshot is invalid"
                )
            if not isinstance(parent_identity, Mapping):
                parent_identity = {
                    "venue": "chat",
                    "base_session_id": str(parent["session_id"]),
                    "code_session_id": "",
                    "delivery_session_id": str(parent["session_id"]),
                    "base_epoch": 0,
                    "code_epoch": 0,
                    "request_id": str(parent["request_id"]),
                    "turn_id": str(parent["turn_id"]),
                    "workflow_name": str(parent["workflow_name"]),
                    "logical_slot": f"legacy:{parent_run_id}",
                }
            original_capabilities = {
                str(key): copy.deepcopy(value)
                for key, value in parent_snapshot.items()
                if key != "_workflow_start"
            }
            child_identity = {
                **copy.deepcopy(dict(parent_identity)),
                "request_id": child_request_id,
                "turn_id": child_turn_id,
                "logical_slot": child_request_key,
            }
            capability_content_hash = hashlib.sha256(
                _canonical(original_capabilities).encode("utf-8")
            ).hexdigest()
            args_hash = hashlib.sha256(_canonical(dict(start_payload)).encode("utf-8")).hexdigest()
            child_start = {
                **original_capabilities,
                "_workflow_start": {
                    "identity": child_identity,
                    "identity_key": hashlib.sha256(
                        _canonical(child_identity).encode("utf-8")
                    ).hexdigest(),
                    "request_hash": request_hash,
                    "args_hash": args_hash,
                    "capability_hash": capability_content_hash,
                    "start_payload": copy.deepcopy(dict(start_payload)),
                },
            }
            child_snapshot_json = _canonical(child_start)
            child_capability_hash = hashlib.sha256(child_snapshot_json.encode("utf-8")).hexdigest()
            parent_lineage = await (
                await db.execute(
                    "SELECT * FROM workflow_research_lineage WHERE operation_id=? AND run_id=?",
                    (parent_operation_id, parent_run_id),
                )
            ).fetchone()
            if parent_lineage is None:
                raise ResearchRepositoryError(
                    "parent_lineage_missing", "parent lineage was not found"
                )
            snapshot = await (
                await db.execute(
                    "SELECT * FROM workflow_research_snapshots WHERE snapshot_hash=? AND run_id=?",
                    (snapshot_hash, parent_run_id),
                )
            ).fetchone()
            pin = await (
                await db.execute(
                    """SELECT * FROM workflow_research_snapshot_pins
                    WHERE snapshot_hash=? AND run_id=? AND pin_kind='continue_parent'
                      AND (expires_at IS NULL OR expires_at>?)""",
                    (snapshot_hash, parent_run_id, now),
                )
            ).fetchone()
            if (
                snapshot is None
                or pin is None
                or (snapshot["expires_at"] is not None and float(snapshot["expires_at"]) <= now)
            ):
                raise ResearchRepositoryError(
                    "snapshot_expired", "continuation snapshot is missing or expired"
                )
            if snapshot["operation_id"] != parent_operation_id:
                raise ResearchRepositoryError(
                    "snapshot_operation_mismatch", "snapshot is not owned by the parent operation"
                )
            manifest_blob = await (
                await db.execute(
                    "SELECT 1 FROM workflow_blobs WHERE sha256=?", (snapshot["manifest_ref"],)
                )
            ).fetchone()
            if manifest_blob is None:
                raise ResearchRepositoryError(
                    "snapshot_blob_missing", "snapshot manifest blob is missing"
                )
            events = await (
                await db.execute(
                    """SELECT payload_json FROM workflow_events
                    WHERE run_id=? AND event_type='workflow.final' ORDER BY seq DESC""",
                    (parent_run_id,),
                )
            ).fetchall()
            final_payload: dict[str, Any] | None = None
            for event in events:
                payload = json.loads(str(event["payload_json"]))
                if payload.get("delivery_status") in _CONTINUABLE_DELIVERY:
                    final_payload = payload
                    break
            if final_payload is None:
                raise ResearchRepositoryError(
                    "parent_delivery_not_continuable", "parent delivery is not continuable"
                )
            persisted_report = final_payload.get("report_ref")
            if persisted_report is not None and parent_report_ref != persisted_report:
                raise ResearchRepositoryError(
                    "parent_report_mismatch", "parent report reference changed"
                )
            await db.execute(
                """INSERT OR IGNORE INTO workflow_capabilities(
                capability_hash,snapshot_json,created_at
                ) VALUES(?,?,?)""",
                (child_capability_hash, child_snapshot_json, now),
            )
            await db.execute(
                """INSERT INTO workflow_runs(
                run_id,trace_id,thread_id,checkpoint_ns,head_checkpoint_ns,head_checkpoint_id,
                parent_run_id,source_checkpoint_id,session_id,request_id,turn_id,
                workflow_name,workflow_version,manifest_hash,implementation_hash,capability_hash,
                state_schema_version,status,active_nodes_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,?,?,'created','[]',?,?)""",
                (
                    child_run_id,
                    child_trace_id,
                    child_thread_id,
                    parent["checkpoint_ns"],
                    parent["checkpoint_ns"],
                    parent_run_id,
                    parent["head_checkpoint_id"],
                    parent["session_id"],
                    child_request_id,
                    child_turn_id,
                    parent["workflow_name"],
                    parent["workflow_version"],
                    parent["manifest_hash"],
                    parent["implementation_hash"],
                    child_capability_hash,
                    parent["state_schema_version"],
                    now,
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO workflow_start_requests(
                request_key,session_id,request_id,turn_id,workflow_name,capability_hash,run_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    child_request_key,
                    parent["session_id"],
                    child_request_id,
                    child_turn_id,
                    parent["workflow_name"],
                    parent["capability_hash"],
                    child_run_id,
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO workflow_session_refs(
                run_id,session_kind,session_id,session_epoch,deleted_at
                ) SELECT ?,session_kind,session_id,session_epoch,NULL
                FROM workflow_session_refs WHERE run_id=? AND deleted_at IS NULL""",
                (child_run_id, parent_run_id),
            )
            await db.execute(
                """INSERT INTO workflow_research_lineage(
                operation_id,run_id,parent_run_id,parent_operation_id,snapshot_hash,
                parent_report_ref,budget_lease_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    child_operation_id,
                    child_run_id,
                    parent_run_id,
                    parent_operation_id,
                    snapshot_hash,
                    parent_report_ref,
                    budget_lease_id,
                    now,
                ),
            )
            child_pin_id = hashlib.sha256(
                f"{snapshot_hash}|{child_run_id}|continue_child".encode()
            ).hexdigest()
            await db.execute(
                """INSERT INTO workflow_research_snapshot_pins(
                pin_id,snapshot_hash,run_id,pin_kind,expires_at,created_at
                ) VALUES(?,?,?,'continue_child',NULL,?)""",
                (child_pin_id, snapshot_hash, child_run_id, now),
            )
            start_result = {
                "schema_version": 1,
                "parent_run_id": parent_run_id,
                "parent_operation_id": parent_operation_id,
                "snapshot_hash": snapshot_hash,
                "payload": copy.deepcopy(dict(start_payload)),
            }
            await db.execute(
                """INSERT INTO workflow_operations(
                operation_id,run_id,operation_kind,request_hash,result_json,created_at
                ) VALUES(?,?,'research_continue_start',?,?,?)""",
                (start_operation_id, child_run_id, request_hash, _canonical(start_result), now),
            )
            row = await (
                await db.execute(
                    "SELECT * FROM workflow_research_lineage WHERE operation_id=?",
                    (child_operation_id,),
                )
            ).fetchone()
            await db.commit()
            assert row is not None
            return dict(row), True
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()
