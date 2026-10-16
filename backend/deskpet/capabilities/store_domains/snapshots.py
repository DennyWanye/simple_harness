# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Run catalog snapshots, snapshot lease intents and catalog projections."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import aiosqlite

from ..contracts import JsonValue, RunCatalogContentStamp, canonical_json, fingerprint_json
from .core import CapabilityStoreCore
from .support import (
    CapabilityRuntimeProjectionReceipt,
    CapabilitySnapshotLeaseIntent,
    CapabilitySnapshotLeaseReleaseReceipt,
    CapabilityStoreConflict,
    CapabilityStoreError,
    PreparedRunCatalogProjection,
    SnapshotLeaseAdoptionReceipt,
    _fetch_mappings,
)


@dataclass(frozen=True, slots=True)
class SnapshotProjectionState:
    lease_intent_id: str
    lease_intent_hash: str
    intent_status: str
    start_fingerprint: str | None
    run_catalog_content_stamp: str
    process_instance_id: str
    process_catalog_stamp: str
    projection_status: str
    projection_receipt_id: str
    projection_receipt_hash: str
    pin_token_hash: str | None


def _snapshot_lease_intent_from_row(
    row: Mapping[str, Any],
) -> CapabilitySnapshotLeaseIntent:
    return CapabilitySnapshotLeaseIntent(
        lease_intent_id=str(row["lease_intent_id"]),
        lease_intent_hash=str(row["lease_intent_hash"]),
        snapshot_ref=str(row["snapshot_ref"]),
        snapshot_ref_schema=str(row["snapshot_ref_schema"]),
        run_id=str(row["run_id"]),
        root_run_id=str(row["root_run_id"]),
        request_id=str(row["request_id"]),
        turn_id=str(row["turn_id"]),
        lease_generation=int(row["lease_generation"]),
        owner_operation_id=str(row["owner_operation_id"]),
        lease_owner_kind=str(row["lease_owner_kind"]),
        owner_record_ref=(
            None if row["owner_record_ref"] is None else str(row["owner_record_ref"])
        ),
        owner_record_hash=(
            None if row["owner_record_hash"] is None else str(row["owner_record_hash"])
        ),
        start_fingerprint=(
            None if row["start_fingerprint"] is None else str(row["start_fingerprint"])
        ),
        run_catalog_content_stamp=str(row["run_catalog_content_stamp"]),
        entry_set_hash=str(row["entry_set_hash"]),
        expected_entry_count=int(row["expected_entry_count"]),
        status=str(row["status"]),
        prepared_at=float(row["prepared_at"]),
        bound_at=None if row["bound_at"] is None else float(row["bound_at"]),
        released_at=(None if row["released_at"] is None else float(row["released_at"])),
        last_error=None if row["last_error"] is None else str(row["last_error"]),
    )


def _runtime_projection_receipt_from_row(
    row: Mapping[str, Any],
) -> CapabilityRuntimeProjectionReceipt:
    return CapabilityRuntimeProjectionReceipt(
        projection_receipt_id=str(row["projection_receipt_id"]),
        projection_receipt_hash=str(row["projection_receipt_hash"]),
        purpose=str(row["purpose"]),
        lease_intent_id=(None if row["lease_intent_id"] is None else str(row["lease_intent_id"])),
        run_catalog_content_stamp=(
            None
            if row["run_catalog_content_stamp"] is None
            else str(row["run_catalog_content_stamp"])
        ),
        process_instance_id=str(row["process_instance_id"]),
        process_catalog_stamp=(
            None if row["process_catalog_stamp"] is None else str(row["process_catalog_stamp"])
        ),
        prepared_tool_set_hash=(
            None if row["prepared_tool_set_hash"] is None else str(row["prepared_tool_set_hash"])
        ),
        pin_token_hash=(None if row["pin_token_hash"] is None else str(row["pin_token_hash"])),
        status=str(row["status"]),
        created_at=float(row["created_at"]),
        ready_at=(None if row["ready_at"] is None else float(row["ready_at"])),
        retired_at=(None if row["retired_at"] is None else float(row["retired_at"])),
    )


def _snapshot_release_receipt_from_row(
    row: Mapping[str, Any],
) -> CapabilitySnapshotLeaseReleaseReceipt:
    return CapabilitySnapshotLeaseReleaseReceipt(
        release_receipt_id=str(row["release_receipt_id"]),
        release_receipt_hash=str(row["release_receipt_hash"]),
        lease_intent_id=str(row["lease_intent_id"]),
        run_id=str(row["run_id"]),
        snapshot_ref=str(row["snapshot_ref"]),
        release_reason=str(row["release_reason"]),
        owner_terminal_or_transition_ref=str(row["owner_terminal_or_transition_ref"]),
        owner_terminal_or_transition_hash=str(row["owner_terminal_or_transition_hash"]),
        last_active_member_for_snapshot=bool(row["last_active_member_for_snapshot"]),
        cleanup_ticket_ref=str(row["cleanup_ticket_ref"]),
        cleanup_ticket_hash=str(row["cleanup_ticket_hash"]),
        cleanup_status=str(row["cleanup_status"]),
        status="released",
        created_at=float(row["created_at"]),
        cleaned_at=(None if row["cleaned_at"] is None else float(row["cleaned_at"])),
        last_error=(None if row["last_error"] is None else str(row["last_error"])),
    )


class RunSnapshotMixin(CapabilityStoreCore):
    """Run catalog snapshots, snapshot lease intents and catalog projections."""

    __slots__ = ()

    async def _put_run_catalog_snapshot_tx(
        self,
        db: aiosqlite.Connection,
        stamp: RunCatalogContentStamp,
        *,
        request_owner_key: str,
        catalog_generation_vector: Mapping[str, int],
        created_at: float | None = None,
        require_complete: bool = False,
    ) -> str:
        vector = {str(key): int(value) for key, value in sorted(catalog_generation_vector.items())}
        if any(value < 0 for value in vector.values()):
            raise CapabilityStoreError(
                "invalid_catalog_generation_vector",
                "catalog generations must be non-negative",
            )
        vector_json = canonical_json(vector)
        vector_hash = fingerprint_json(vector)
        now = self._clock() if created_at is None else float(created_at)
        existing = await (
            await db.execute(
                """SELECT * FROM capability_run_catalog_snapshots
                   WHERE run_catalog_content_stamp=?""",
                (stamp.fingerprint,),
            )
        ).fetchone()
        header = (
            request_owner_key,
            stamp.request_scope.canonical,
            stamp.request_scope_hash,
            vector_json,
            vector_hash,
            stamp.entry_set_hash,
            len(stamp.entries),
        )
        expected_rows: list[tuple[Any, ...]] = []
        empty_hash = fingerprint_json([])
        for ordinal, entry in enumerate(stamp.entries):
            envelope = dict(entry.canonical_envelope)
            visible = envelope.get("visible_bindings", [])
            if not isinstance(visible, list):
                raise CapabilityStoreError(
                    "invalid_visible_bindings",
                    "visible bindings must be a list",
                )
            selected = envelope.get("selected_binding")
            if selected is not None and not isinstance(selected, dict):
                raise CapabilityStoreError(
                    "invalid_selected_binding",
                    "selected binding must be an object",
                )
            selected = selected or {}
            tool_fingerprints = envelope.get("tool_spec_fingerprints", [])
            if not isinstance(tool_fingerprints, list):
                raise CapabilityStoreError(
                    "invalid_tool_fingerprints",
                    "tool fingerprints must be a list",
                )
            required_hashes = {}
            for field_name in (
                "instruction_refs_hash",
                "workflow_refs_hash",
                "runtime_descriptor_hash",
            ):
                value = envelope.get(field_name)
                if require_complete and (not isinstance(value, str) or len(value) != 64):
                    raise CapabilityStoreError(
                        "invalid_catalog_entry",
                        f"{field_name} is required for durable catalog entries",
                    )
                required_hashes[field_name] = (
                    value if isinstance(value, str) and len(value) == 64 else empty_hash
                )
            host_build = envelope.get("host_build_identity")
            expected_rows.append(
                (
                    stamp.fingerprint,
                    ordinal,
                    entry.entry_kind,
                    selected.get("binding_id"),
                    selected.get("owner_key"),
                    selected.get("scope"),
                    selected.get("scope_key"),
                    selected.get("generation"),
                    envelope.get("stable_host_binding_id"),
                    canonical_json(visible),
                    fingerprint_json(visible),
                    canonical_json(envelope),
                    entry.descriptor_fingerprint,
                    envelope.get("pack_id"),
                    envelope.get("version"),
                    envelope.get("manifest_hash"),
                    envelope.get("host_provider_name"),
                    envelope.get("host_source"),
                    envelope.get("host_spec_version"),
                    envelope.get("host_schema_hash"),
                    envelope.get("host_content_hash"),
                    (None if host_build is None else canonical_json(host_build)),
                    canonical_json(tool_fingerprints),
                    required_hashes["instruction_refs_hash"],
                    required_hashes["workflow_refs_hash"],
                    required_hashes["runtime_descriptor_hash"],
                )
            )
        if existing is not None:
            actual = (
                str(existing["request_owner_key"]),
                str(existing["request_scope_canonical_json"]),
                str(existing["request_scope_hash"]),
                str(existing["catalog_generation_vector_json"]),
                str(existing["catalog_generation_vector_hash"]),
                str(existing["entry_set_hash"]),
                int(existing["expected_entry_count"]),
            )
            cursor = await db.execute(
                """SELECT run_catalog_content_stamp,ordinal,entry_kind,
                    selected_binding_id,selected_owner_key,selected_scope,
                    selected_scope_key,selected_binding_generation,
                    stable_host_binding_id,visible_bindings_json,
                    visible_binding_set_hash,descriptor_envelope_json,
                    descriptor_fingerprint,pack_id,version,manifest_hash,
                    host_provider_name,host_source,host_spec_version,
                    host_schema_hash,host_content_hash,host_build_identity,
                    tool_spec_fingerprints_json,instruction_refs_hash,
                    workflow_refs_hash,runtime_descriptor_hash
                   FROM capability_run_catalog_snapshot_entries
                   WHERE run_catalog_content_stamp=?
                   ORDER BY ordinal""",
                (stamp.fingerprint,),
            )
            stored_rows = [tuple(row) for row in await cursor.fetchall()]
            if actual != header or stored_rows != expected_rows:
                raise CapabilityStoreConflict(
                    "run_catalog_snapshot_conflict",
                    "stored catalog snapshot differs from immutable content",
                )
            return stamp.fingerprint
        await db.execute(
            """INSERT INTO capability_run_catalog_snapshots(
                run_catalog_content_stamp,request_owner_key,
                request_scope_canonical_json,request_scope_hash,
                catalog_generation_vector_json,
                catalog_generation_vector_hash,entry_set_hash,
                expected_entry_count,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (stamp.fingerprint, *header, now),
        )
        for row in expected_rows:
            await db.execute(
                """INSERT INTO capability_run_catalog_snapshot_entries(
                        run_catalog_content_stamp,ordinal,entry_kind,
                        selected_binding_id,selected_owner_key,selected_scope,
                        selected_scope_key,selected_binding_generation,
                        stable_host_binding_id,visible_bindings_json,
                        visible_binding_set_hash,descriptor_envelope_json,
                        descriptor_fingerprint,pack_id,version,manifest_hash,
                        host_provider_name,host_source,host_spec_version,
                        host_schema_hash,host_content_hash,host_build_identity,
                        tool_spec_fingerprints_json,instruction_refs_hash,
                        workflow_refs_hash,runtime_descriptor_hash
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
        return stamp.fingerprint

    async def put_run_catalog_snapshot(
        self,
        stamp: RunCatalogContentStamp,
        *,
        request_owner_key: str,
        catalog_generation_vector: Mapping[str, int],
        created_at: float | None = None,
    ) -> str:
        async with self.write_transaction() as db:
            return await self._put_run_catalog_snapshot_tx(
                db,
                stamp,
                request_owner_key=request_owner_key,
                catalog_generation_vector=catalog_generation_vector,
                created_at=created_at,
            )

    async def _prepare_snapshot_lease_intent_tx(
        self,
        db: aiosqlite.Connection,
        *,
        snapshot_ref: str,
        snapshot_ref_schema: str,
        run_id: str,
        root_run_id: str,
        request_id: str,
        turn_id: str,
        owner_operation_id: str,
        lease_owner_kind: str,
        run_catalog_content_stamp: str,
        entry_set_hash: str,
        expected_entry_count: int,
    ) -> CapabilitySnapshotLeaseIntent:
        if snapshot_ref_schema not in {"legacy_v1", "run_catalog_v2"}:
            raise CapabilityStoreError("invalid_snapshot_ref_schema", "unknown snapshot ref schema")
        if lease_owner_kind not in {
            "run_start",
            "refresh_commit",
            "legacy_boundary",
            "queued_child_legacy",
        }:
            raise CapabilityStoreError("invalid_lease_owner_kind", "unknown lease owner kind")
        if snapshot_ref_schema == "run_catalog_v2" and lease_owner_kind not in {
            "run_start",
            "refresh_commit",
        }:
            raise CapabilityStoreError(
                "invalid_lease_owner_kind",
                "run_catalog_v2 supports only run start or refresh commit",
            )
        identity = {
            "domain": "capability-lease-intent-v1",
            "snapshot_ref": snapshot_ref,
            "run_id": run_id,
            "root_run_id": root_run_id,
            "owner_operation_id": owner_operation_id,
            "run_catalog_content_stamp": run_catalog_content_stamp,
            "entry_set_hash": entry_set_hash,
            "expected_entry_count": int(expected_entry_count),
        }
        existing_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_lease_intents
                   WHERE run_id=? AND owner_operation_id=?""",
                (run_id, owner_operation_id),
            )
        )
        if existing_rows:
            existing = existing_rows[0]
            record = _snapshot_lease_intent_from_row(existing)
            candidate = {
                **identity,
                "lease_generation": record.lease_generation,
            }
            immutable = (
                record.snapshot_ref_schema,
                record.request_id,
                record.turn_id,
                record.lease_owner_kind,
            )
            requested = (
                snapshot_ref_schema,
                request_id,
                turn_id,
                lease_owner_kind,
            )
            if record.lease_intent_hash != fingerprint_json(candidate) or immutable != requested:
                raise CapabilityStoreConflict(
                    "snapshot_lease_intent_conflict",
                    "owner operation already has another lease intent",
                )
            return record
        previous_rows = await _fetch_mappings(
            await db.execute(
                """SELECT status,lease_generation
                   FROM capability_snapshot_lease_intents
                   WHERE run_id=?
                   ORDER BY lease_generation DESC LIMIT 1""",
                (run_id,),
            )
        )
        previous = previous_rows[0] if previous_rows else None
        if (
            previous is not None
            and str(previous["status"]) != "released"
            and snapshot_ref_schema != "legacy_v1"
        ):
            raise CapabilityStoreConflict(
                "snapshot_lease_intent_active",
                "previous run lease intent is not released",
            )
        lease_generation = 1 if previous is None else int(previous["lease_generation"]) + 1
        identity["lease_generation"] = lease_generation
        lease_intent_hash = fingerprint_json(identity)
        now = self._clock()
        await db.execute(
            """INSERT INTO capability_snapshot_lease_intents(
                lease_intent_id,lease_intent_hash,snapshot_ref,
                snapshot_ref_schema,run_id,root_run_id,request_id,turn_id,
                lease_generation,owner_operation_id,lease_owner_kind,
                owner_record_ref,owner_record_hash,start_fingerprint,
                run_catalog_content_stamp,entry_set_hash,
                expected_entry_count,status,prepared_at,bound_at,
                released_at,last_error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'prepared',?,
                     NULL,NULL,NULL)""",
            (
                lease_intent_hash,
                lease_intent_hash,
                snapshot_ref,
                snapshot_ref_schema,
                run_id,
                root_run_id,
                request_id,
                turn_id,
                lease_generation,
                owner_operation_id,
                lease_owner_kind,
                None,
                None,
                None,
                run_catalog_content_stamp,
                entry_set_hash,
                int(expected_entry_count),
                now,
            ),
        )
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_lease_intents
                   WHERE lease_intent_id=?""",
                (lease_intent_hash,),
            )
        )
        if not rows:  # pragma: no cover
            raise CapabilityStoreError(
                "snapshot_lease_intent_missing",
                "prepared lease intent disappeared",
            )
        return _snapshot_lease_intent_from_row(rows[0])

    async def prepare_snapshot_lease_intent(
        self,
        **kwargs: Any,
    ) -> CapabilitySnapshotLeaseIntent:
        async with self.write_transaction() as db:
            intent = await self._prepare_snapshot_lease_intent_tx(db, **kwargs)
            await self._put_snapshot_lease_rows_tx(db, intent)
            return intent

    async def _put_snapshot_lease_rows_tx(
        self,
        db: aiosqlite.Connection,
        intent: CapabilitySnapshotLeaseIntent,
    ) -> None:
        catalog_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_run_catalog_snapshot_entries
                   WHERE run_catalog_content_stamp=?
                   ORDER BY ordinal""",
                (intent.run_catalog_content_stamp,),
            )
        )
        if len(catalog_rows) != intent.expected_entry_count:
            raise CapabilityStoreConflict(
                "snapshot_lease_entry_count_mismatch",
                "catalog entry count differs from the lease intent",
            )
        expected: list[tuple[Any, ...]] = []
        for row in catalog_rows:
            ordinal = int(row["ordinal"])
            lease_entry_id = fingerprint_json(
                {
                    "domain": "capability-lease-entry-v2",
                    "lease_intent_id": intent.lease_intent_id,
                    "entry_ordinal": ordinal,
                }
            )
            expected.append(
                (
                    lease_entry_id,
                    intent.lease_intent_id,
                    intent.snapshot_ref,
                    intent.run_id,
                    intent.root_run_id,
                    intent.run_catalog_content_stamp,
                    ordinal,
                    str(row["entry_kind"]),
                    row["selected_binding_id"],
                    row["selected_owner_key"],
                    row["selected_scope"],
                    row["selected_scope_key"],
                    row["selected_binding_generation"],
                    row["stable_host_binding_id"],
                    str(row["visible_binding_set_hash"]),
                    str(row["descriptor_fingerprint"]),
                    str(row["runtime_descriptor_hash"]),
                    row["pack_id"],
                    row["version"],
                    row["manifest_hash"],
                    row["host_provider_name"],
                    row["host_source"],
                    row["host_spec_version"],
                    row["host_schema_hash"],
                    row["host_content_hash"],
                    row["host_build_identity"],
                    str(row["tool_spec_fingerprints_json"]),
                )
            )
        current = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_leases
                   WHERE lease_intent_id=? ORDER BY entry_ordinal""",
                (intent.lease_intent_id,),
            )
        )
        if current:
            actual = [
                (
                    str(row["lease_entry_id"]),
                    str(row["lease_intent_id"]),
                    str(row["snapshot_ref"]),
                    str(row["run_id"]),
                    str(row["root_run_id"]),
                    str(row["run_catalog_content_stamp"]),
                    int(row["entry_ordinal"]),
                    str(row["entry_kind"]),
                    row["selected_binding_id"],
                    row["selected_owner_key"],
                    row["selected_scope"],
                    row["selected_scope_key"],
                    row["selected_binding_generation"],
                    row["stable_host_binding_id"],
                    str(row["visible_binding_set_hash"]),
                    str(row["descriptor_fingerprint"]),
                    str(row["runtime_descriptor_hash"]),
                    row["pack_id"],
                    row["version"],
                    row["manifest_hash"],
                    row["host_provider_name"],
                    row["host_source"],
                    row["host_spec_version"],
                    row["host_schema_hash"],
                    row["host_content_hash"],
                    row["host_build_identity"],
                    str(row["tool_spec_fingerprints_json"]),
                )
                for row in current
            ]
            if actual != expected:
                raise CapabilityStoreConflict(
                    "snapshot_lease_rows_conflict",
                    "stored lease entries differ from immutable catalog entries",
                )
            return
        now = self._clock()
        for row in expected:
            await db.execute(
                """INSERT INTO capability_snapshot_leases(
                    lease_entry_id,lease_intent_id,snapshot_ref,run_id,
                    root_run_id,run_catalog_content_stamp,entry_ordinal,
                    entry_kind,selected_binding_id,selected_owner_key,
                    selected_scope,selected_scope_key,
                    selected_binding_generation,stable_host_binding_id,
                    visible_binding_set_hash,descriptor_fingerprint,
                    runtime_descriptor_hash,pack_id,version,manifest_hash,
                    host_provider_name,host_source,host_spec_version,
                    host_schema_hash,host_content_hash,host_build_identity,
                    tool_spec_fingerprints_json,acquired_at,released_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                         NULL)""",
                (*row, now),
            )

    async def prepare_run_catalog_projection_in_tx(
        self,
        db: aiosqlite.Connection,
        *,
        content: RunCatalogContentStamp,
        process_stamp: ProcessCatalogStamp,
        snapshot_ref: str,
        request_owner_key: str,
        catalog_generation_vector: Mapping[str, int],
        run_id: str,
        root_run_id: str,
        request_id: str,
        turn_id: str,
        owner_operation_id: str,
        lease_owner_kind: str,
        prepared_tool_set_envelope: Mapping[str, JsonValue],
        prepared_tool_set_hash: str,
    ) -> PreparedRunCatalogProjection:
        if process_stamp.run_catalog_content_stamp != content.fingerprint:
            raise CapabilityStoreConflict(
                "process_catalog_stamp_conflict",
                "process stamp belongs to another run catalog",
            )
        if snapshot_ref != content.snapshot_ref:
            raise CapabilityStoreConflict(
                "snapshot_ref_conflict",
                "snapshot ref does not match the run catalog",
            )
        tool_set_json = canonical_json(dict(prepared_tool_set_envelope))
        if fingerprint_json(json.loads(tool_set_json)) != prepared_tool_set_hash:
            raise CapabilityStoreConflict(
                "prepared_tool_set_conflict",
                "prepared tool set hash does not match its envelope",
            )
        await self._put_run_catalog_snapshot_tx(
            db,
            content,
            request_owner_key=request_owner_key,
            catalog_generation_vector=catalog_generation_vector,
            require_complete=True,
        )
        intent = await self._prepare_snapshot_lease_intent_tx(
            db,
            snapshot_ref=snapshot_ref,
            snapshot_ref_schema="run_catalog_v2",
            run_id=run_id,
            root_run_id=root_run_id,
            request_id=request_id,
            turn_id=turn_id,
            owner_operation_id=owner_operation_id,
            lease_owner_kind=lease_owner_kind,
            run_catalog_content_stamp=content.fingerprint,
            entry_set_hash=content.entry_set_hash,
            expected_entry_count=len(content.entries),
        )
        await self._put_snapshot_lease_rows_tx(db, intent)
        receipt_payload = {
            "domain": "runtime-projection-receipt-v1",
            "purpose": "snapshot_pin",
            "lease_intent_id": intent.lease_intent_id,
            "run_catalog_content_stamp": content.fingerprint,
            "process_instance_id": process_stamp.process_instance_id,
            "process_catalog_stamp": process_stamp.fingerprint,
            "prepared_tool_set_hash": prepared_tool_set_hash,
        }
        projection_receipt_id = fingerprint_json(receipt_payload)
        projection_receipt_hash = projection_receipt_id
        existing_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_runtime_projection_receipts
                   WHERE projection_receipt_id=? OR lease_intent_id=?""",
                (projection_receipt_id, intent.lease_intent_id),
            )
        )
        if existing_rows:
            if len(existing_rows) != 1:
                raise CapabilityStoreConflict(
                    "runtime_projection_receipt_conflict",
                    "lease intent resolves to multiple projection receipts",
                )
            current = _runtime_projection_receipt_from_row(existing_rows[0])
            immutable = (
                current.projection_receipt_id,
                current.projection_receipt_hash,
                current.purpose,
                current.lease_intent_id,
                current.run_catalog_content_stamp,
                current.process_instance_id,
                current.process_catalog_stamp,
                current.prepared_tool_set_hash,
            )
            expected = (
                projection_receipt_id,
                projection_receipt_hash,
                "snapshot_pin",
                intent.lease_intent_id,
                content.fingerprint,
                process_stamp.process_instance_id,
                process_stamp.fingerprint,
                prepared_tool_set_hash,
            )
            if immutable != expected:
                raise CapabilityStoreConflict(
                    "runtime_projection_receipt_conflict",
                    "stored projection receipt differs from immutable request",
                )
            receipt = current
        else:
            now = self._clock()
            await db.execute(
                """INSERT INTO capability_runtime_projection_receipts(
                    projection_receipt_id,projection_receipt_hash,purpose,
                    operation_id,owner_activation_id,lease_activation_id,
                    lease_intent_id,owner_binding_set_stamp,
                    run_catalog_content_stamp,process_instance_id,
                    process_catalog_stamp,process_projection_fingerprint,
                    runtime_set_set_hash,prepared_tool_set_envelope_json,
                    prepared_tool_set_hash,pin_token_hash,status,created_at,
                    ready_at,retired_at
                ) VALUES(?,?,'snapshot_pin',NULL,NULL,NULL,?,NULL,?,?,?,NULL,
                         NULL,?,?,NULL,'prepared',?,NULL,NULL)""",
                (
                    projection_receipt_id,
                    projection_receipt_hash,
                    intent.lease_intent_id,
                    content.fingerprint,
                    process_stamp.process_instance_id,
                    process_stamp.fingerprint,
                    tool_set_json,
                    prepared_tool_set_hash,
                    now,
                ),
            )
            receipt_row = (
                await _fetch_mappings(
                    await db.execute(
                        """SELECT *
                           FROM capability_runtime_projection_receipts
                           WHERE projection_receipt_id=?""",
                        (projection_receipt_id,),
                    )
                )
            )[0]
            receipt = _runtime_projection_receipt_from_row(receipt_row)
        return PreparedRunCatalogProjection(
            intent=intent,
            projection_receipt=receipt,
            process_catalog_stamp=process_stamp.fingerprint,
        )

    async def _bind_snapshot_lease_intent_tx(
        self,
        db: aiosqlite.Connection,
        lease_intent_id: str,
        *,
        owner_record_ref: str,
        owner_record_hash: str,
        start_fingerprint: str,
    ) -> CapabilitySnapshotLeaseIntent:
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_lease_intents
                   WHERE lease_intent_id=?""",
                (lease_intent_id,),
            )
        )
        if not rows:
            raise CapabilityStoreError("snapshot_lease_intent_missing", "lease intent is absent")
        current = _snapshot_lease_intent_from_row(rows[0])
        expected = (
            owner_record_ref,
            owner_record_hash,
            start_fingerprint,
        )
        actual = (
            current.owner_record_ref,
            current.owner_record_hash,
            current.start_fingerprint,
        )
        if current.status == "bound":
            if actual != expected:
                raise CapabilityStoreConflict(
                    "snapshot_lease_intent_conflict",
                    "bound lease intent owner record differs",
                )
            return current
        if current.status != "prepared":
            raise CapabilityStoreConflict(
                "snapshot_lease_intent_terminal",
                "lease intent is no longer preparable",
            )
        lease_count = int(
            (
                await (
                    await db.execute(
                        """SELECT COUNT(*) FROM capability_snapshot_leases
                           WHERE lease_intent_id=?""",
                        (lease_intent_id,),
                    )
                ).fetchone()
            )[0]
        )
        if lease_count != current.expected_entry_count:
            raise CapabilityStoreConflict(
                "snapshot_lease_entry_count_mismatch",
                "cannot adopt an incomplete snapshot lease",
            )
        cursor = await db.execute(
            """UPDATE capability_snapshot_lease_intents
               SET status='bound',owner_record_ref=?,owner_record_hash=?,
                   start_fingerprint=?,bound_at=?
               WHERE lease_intent_id=? AND status='prepared'""",
            (
                owner_record_ref,
                owner_record_hash,
                start_fingerprint,
                self._clock(),
                lease_intent_id,
            ),
        )
        if cursor.rowcount != 1:
            raise CapabilityStoreConflict(
                "snapshot_lease_intent_conflict",
                "lease intent changed before bind",
            )
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_lease_intents
                   WHERE lease_intent_id=?""",
                (lease_intent_id,),
            )
        )
        return _snapshot_lease_intent_from_row(rows[0])

    async def bind_snapshot_lease_intent(
        self,
        lease_intent_id: str,
        *,
        owner_record_ref: str,
        owner_record_hash: str,
        start_fingerprint: str,
    ) -> CapabilitySnapshotLeaseIntent:
        async with self.write_transaction() as db:
            return await self._bind_snapshot_lease_intent_tx(
                db,
                lease_intent_id,
                owner_record_ref=owner_record_ref,
                owner_record_hash=owner_record_hash,
                start_fingerprint=start_fingerprint,
            )

    async def adopt_snapshot_lease_intent_in_tx(
        self,
        db: aiosqlite.Connection,
        lease_intent_id: str,
        *,
        intent_hash: str,
        owner_record_ref: str,
        owner_record_hash: str,
        start_fingerprint: str,
    ) -> SnapshotLeaseAdoptionReceipt:
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT lease_intent_hash
                   FROM capability_snapshot_lease_intents
                   WHERE lease_intent_id=?""",
                (lease_intent_id,),
            )
        )
        if not rows or str(rows[0]["lease_intent_hash"]) != intent_hash:
            raise CapabilityStoreConflict(
                "snapshot_lease_intent_conflict",
                "intent hash differs from the prepared lease",
            )
        intent = await self._bind_snapshot_lease_intent_tx(
            db,
            lease_intent_id,
            owner_record_ref=owner_record_ref,
            owner_record_hash=owner_record_hash,
            start_fingerprint=start_fingerprint,
        )
        projection_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_runtime_projection_receipts
                   WHERE lease_intent_id=? AND purpose='snapshot_pin'""",
                (lease_intent_id,),
            )
        )
        if len(projection_rows) != 1:
            raise CapabilityStoreConflict(
                "runtime_projection_receipt_missing",
                "snapshot pin projection receipt is absent or ambiguous",
            )
        projection = _runtime_projection_receipt_from_row(projection_rows[0])
        return SnapshotLeaseAdoptionReceipt(
            intent_id=intent.lease_intent_id,
            intent_hash=intent.lease_intent_hash,
            status=intent.status,
            owner_record_ref=owner_record_ref,
            owner_record_hash=owner_record_hash,
            start_fingerprint=start_fingerprint,
            projection_receipt_id=projection.projection_receipt_id,
            projection_receipt_hash=projection.projection_receipt_hash,
        )

    async def release_snapshot_lease_intent_in_tx(
        self,
        db: aiosqlite.Connection,
        lease_intent_id: str,
        *,
        intent_hash: str,
        release_reason: str,
        owner_terminal_or_transition_ref: str,
        owner_terminal_or_transition_hash: str,
    ) -> CapabilitySnapshotLeaseReleaseReceipt:
        if release_reason not in {
            "terminal",
            "cancel",
            "refresh_replaced",
            "prepared_orphan",
            "revoked",
            "legacy_terminal",
        }:
            raise CapabilityStoreError(
                "invalid_snapshot_release_reason",
                "unknown snapshot lease release reason",
            )
        intent_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_lease_intents
                   WHERE lease_intent_id=?""",
                (lease_intent_id,),
            )
        )
        if not intent_rows:
            raise CapabilityStoreError("snapshot_lease_intent_missing", "lease intent is absent")
        intent = _snapshot_lease_intent_from_row(intent_rows[0])
        if intent.lease_intent_hash != intent_hash:
            raise CapabilityStoreConflict(
                "snapshot_lease_intent_conflict",
                "intent hash differs from the prepared lease",
            )
        receipt_payload = {
            "domain": "snapshot-lease-release-receipt-v1",
            "lease_intent_id": lease_intent_id,
            "intent_hash": intent_hash,
            "run_id": intent.run_id,
            "snapshot_ref": intent.snapshot_ref,
            "release_reason": release_reason,
            "owner_terminal_or_transition_ref": (owner_terminal_or_transition_ref),
            "owner_terminal_or_transition_hash": (owner_terminal_or_transition_hash),
        }
        release_receipt_id = fingerprint_json(receipt_payload)
        release_receipt_hash = release_receipt_id
        existing_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_lease_release_receipts
                   WHERE lease_intent_id=? OR release_receipt_id=?""",
                (lease_intent_id, release_receipt_id),
            )
        )
        if existing_rows:
            if len(existing_rows) != 1:
                raise CapabilityStoreConflict(
                    "snapshot_release_receipt_conflict",
                    "lease intent resolves to multiple release receipts",
                )
            existing = _snapshot_release_receipt_from_row(existing_rows[0])
            immutable = (
                existing.release_receipt_id,
                existing.release_receipt_hash,
                existing.release_reason,
                existing.owner_terminal_or_transition_ref,
                existing.owner_terminal_or_transition_hash,
            )
            expected = (
                release_receipt_id,
                release_receipt_hash,
                release_reason,
                owner_terminal_or_transition_ref,
                owner_terminal_or_transition_hash,
            )
            if immutable != expected:
                raise CapabilityStoreConflict(
                    "snapshot_release_receipt_conflict",
                    "lease intent was released by another owner transition",
                )
            return existing
        if intent.status not in {"prepared", "bound"}:
            raise CapabilityStoreConflict(
                "snapshot_lease_intent_terminal",
                "lease intent cannot be released from its current status",
            )
        now = self._clock()
        if intent.status == "prepared":
            prepared_release_fingerprint = fingerprint_json(
                {
                    "domain": "prepared-lease-release-v1",
                    "lease_intent_id": lease_intent_id,
                    "owner_transition_hash": (owner_terminal_or_transition_hash),
                }
            )
            owner_record_ref = owner_terminal_or_transition_ref
            owner_record_hash = owner_terminal_or_transition_hash
            start_fingerprint = prepared_release_fingerprint
        else:
            owner_record_ref = intent.owner_record_ref
            owner_record_hash = intent.owner_record_hash
            start_fingerprint = intent.start_fingerprint
        cursor = await db.execute(
            """UPDATE capability_snapshot_lease_intents
               SET status='released',owner_record_ref=?,owner_record_hash=?,
                   start_fingerprint=?,released_at=?
               WHERE lease_intent_id=? AND status IN ('prepared','bound')""",
            (
                owner_record_ref,
                owner_record_hash,
                start_fingerprint,
                now,
                lease_intent_id,
            ),
        )
        if cursor.rowcount != 1:
            raise CapabilityStoreConflict(
                "snapshot_lease_intent_conflict",
                "lease intent changed before release",
            )
        await db.execute(
            """UPDATE capability_snapshot_leases SET released_at=?
               WHERE lease_intent_id=? AND released_at IS NULL""",
            (now, lease_intent_id),
        )
        active_count = int(
            (
                await (
                    await db.execute(
                        """SELECT COUNT(*)
                           FROM capability_snapshot_lease_intents
                           WHERE snapshot_ref=? AND lease_intent_id<>?
                             AND status IN ('prepared','bound')""",
                        (intent.snapshot_ref, lease_intent_id),
                    )
                ).fetchone()
            )[0]
        )
        last_active = active_count == 0
        cleanup_ticket_ref = (
            f"capability-cleanup:{intent.snapshot_ref}:{'last' if last_active else lease_intent_id}"
        )
        cleanup_ticket_hash = fingerprint_json(
            {
                "cleanup_ticket_ref": cleanup_ticket_ref,
                "lease_intent_id": lease_intent_id,
                "last_active_member_for_snapshot": last_active,
            }
        )
        await db.execute(
            """INSERT INTO capability_snapshot_lease_release_receipts(
                release_receipt_id,release_receipt_hash,lease_intent_id,
                run_id,snapshot_ref,release_reason,
                owner_terminal_or_transition_ref,
                owner_terminal_or_transition_hash,
                last_active_member_for_snapshot,cleanup_ticket_ref,
                cleanup_ticket_hash,cleanup_status,created_at,cleaned_at,
                last_error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending',?,NULL,NULL)""",
            (
                release_receipt_id,
                release_receipt_hash,
                lease_intent_id,
                intent.run_id,
                intent.snapshot_ref,
                release_reason,
                owner_terminal_or_transition_ref,
                owner_terminal_or_transition_hash,
                int(last_active),
                cleanup_ticket_ref,
                cleanup_ticket_hash,
                now,
            ),
        )
        await db.execute(
            """UPDATE capability_runtime_projection_receipts
               SET status='retired',retired_at=?
               WHERE lease_intent_id=? AND status IN ('prepared','ready')""",
            (now, lease_intent_id),
        )
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_snapshot_lease_release_receipts
                   WHERE release_receipt_id=?""",
                (release_receipt_id,),
            )
        )
        return _snapshot_release_receipt_from_row(rows[0])

    async def release_snapshot_lease_intent(
        self, lease_intent_id: str
    ) -> CapabilitySnapshotLeaseIntent:
        async with self.write_transaction() as db:
            rows = await _fetch_mappings(
                await db.execute(
                    """SELECT * FROM capability_snapshot_lease_intents
                       WHERE lease_intent_id=?""",
                    (lease_intent_id,),
                )
            )
            if not rows:
                raise CapabilityStoreError(
                    "snapshot_lease_intent_missing", "lease intent is absent"
                )
            current = _snapshot_lease_intent_from_row(rows[0])
            await self.release_snapshot_lease_intent_in_tx(
                db,
                lease_intent_id,
                intent_hash=current.lease_intent_hash,
                release_reason="cancel",
                owner_terminal_or_transition_ref=(f"legacy-release:{lease_intent_id}"),
                owner_terminal_or_transition_hash=fingerprint_json(
                    {"legacy_release": lease_intent_id}
                ),
            )
            rows = await _fetch_mappings(
                await db.execute(
                    """SELECT * FROM capability_snapshot_lease_intents
                       WHERE lease_intent_id=?""",
                    (lease_intent_id,),
                )
            )
            return _snapshot_lease_intent_from_row(rows[0])

    async def get_snapshot_lease_release_receipt(
        self, lease_intent_id: str
    ) -> CapabilitySnapshotLeaseReleaseReceipt | None:
        async with self.read_connection() as db:
            rows = await _fetch_mappings(
                await db.execute(
                    "SELECT * FROM capability_snapshot_lease_release_receipts "
                    "WHERE lease_intent_id=?",
                    (lease_intent_id,),
                )
            )
            if not rows:
                return None
            if len(rows) != 1:
                raise CapabilityStoreConflict(
                    "snapshot_release_receipt_conflict",
                    "lease intent resolves to multiple release receipts",
                )
            return _snapshot_release_receipt_from_row(rows[0])

    async def activate_snapshot_projection_ready(
        self,
        lease_intent_id: str,
        *,
        intent_hash: str,
        start_fingerprint: str,
        process_instance_id: str,
        process_catalog_stamp: str,
        pin_token_hash: str,
    ) -> CapabilityRuntimeProjectionReceipt:
        async with self.write_transaction() as db:
            intent_rows = await _fetch_mappings(
                await db.execute(
                    """SELECT * FROM capability_snapshot_lease_intents
                       WHERE lease_intent_id=?""",
                    (lease_intent_id,),
                )
            )
            if not intent_rows:
                raise CapabilityStoreError(
                    "snapshot_lease_intent_missing", "lease intent is absent"
                )
            intent = _snapshot_lease_intent_from_row(intent_rows[0])
            if (
                intent.lease_intent_hash != intent_hash
                or intent.status != "bound"
                or intent.start_fingerprint != start_fingerprint
            ):
                raise CapabilityStoreConflict(
                    "snapshot_projection_not_bound",
                    "snapshot intent is not the expected bound owner record",
                )
            projection_rows = await _fetch_mappings(
                await db.execute(
                    """SELECT * FROM capability_runtime_projection_receipts
                       WHERE lease_intent_id=? AND purpose='snapshot_pin'""",
                    (lease_intent_id,),
                )
            )
            if len(projection_rows) != 1:
                raise CapabilityStoreConflict(
                    "runtime_projection_receipt_missing",
                    "snapshot pin projection receipt is absent or ambiguous",
                )
            current = _runtime_projection_receipt_from_row(projection_rows[0])
            expected_process = (
                process_instance_id,
                process_catalog_stamp,
            )
            actual_process = (
                current.process_instance_id,
                current.process_catalog_stamp,
            )
            if actual_process != expected_process:
                raise CapabilityStoreConflict(
                    "runtime_projection_receipt_conflict",
                    "projection receipt belongs to another process catalog",
                )
            if current.status == "ready":
                if current.pin_token_hash != pin_token_hash:
                    raise CapabilityStoreConflict(
                        "runtime_projection_receipt_conflict",
                        "ready projection pin token differs",
                    )
                return current
            if current.status != "prepared":
                raise CapabilityStoreConflict(
                    "runtime_projection_receipt_terminal",
                    "projection receipt cannot become ready",
                )
            await db.execute(
                """UPDATE capability_runtime_projection_receipts
                   SET status='ready',pin_token_hash=?,ready_at=?
                   WHERE projection_receipt_id=? AND status='prepared'""",
                (
                    pin_token_hash,
                    self._clock(),
                    current.projection_receipt_id,
                ),
            )
            projection_rows = await _fetch_mappings(
                await db.execute(
                    """SELECT * FROM capability_runtime_projection_receipts
                       WHERE projection_receipt_id=?""",
                    (current.projection_receipt_id,),
                )
            )
            return _runtime_projection_receipt_from_row(projection_rows[0])

    async def read_snapshot_projection_state(
        self, lease_intent_id: str
    ) -> SnapshotProjectionState | None:
        async with self.read_connection() as db:
            rows = await _fetch_mappings(
                await db.execute(
                    """SELECT intent.lease_intent_id,intent.lease_intent_hash,
                              intent.status AS intent_status,
                              intent.start_fingerprint,
                              intent.run_catalog_content_stamp,
                              projection.process_instance_id,
                              projection.process_catalog_stamp,
                              projection.status AS projection_status,
                              projection.projection_receipt_id,
                              projection.projection_receipt_hash,
                              projection.pin_token_hash
                       FROM capability_snapshot_lease_intents AS intent
                       JOIN capability_runtime_projection_receipts AS projection
                         ON projection.lease_intent_id=intent.lease_intent_id
                        AND projection.purpose='snapshot_pin'
                       WHERE intent.lease_intent_id=?""",
                    (lease_intent_id,),
                )
            )
            if not rows:
                return None
            if len(rows) != 1:
                raise CapabilityStoreConflict(
                    "runtime_projection_receipt_conflict",
                    "snapshot intent has multiple projection receipts",
                )
            row = rows[0]
            return SnapshotProjectionState(
                lease_intent_id=str(row["lease_intent_id"]),
                lease_intent_hash=str(row["lease_intent_hash"]),
                intent_status=str(row["intent_status"]),
                start_fingerprint=(
                    None if row["start_fingerprint"] is None else str(row["start_fingerprint"])
                ),
                run_catalog_content_stamp=str(row["run_catalog_content_stamp"]),
                process_instance_id=str(row["process_instance_id"]),
                process_catalog_stamp=str(row["process_catalog_stamp"]),
                projection_status=str(row["projection_status"]),
                projection_receipt_id=str(row["projection_receipt_id"]),
                projection_receipt_hash=str(row["projection_receipt_hash"]),
                pin_token_hash=(
                    None if row["pin_token_hash"] is None else str(row["pin_token_hash"])
                ),
            )
