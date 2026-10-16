# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Snapshot leases and search receipts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from ..contracts import (
    LEGACY_LOCAL_OWNER_KEY,
    CapabilityCatalogEntry,
    CapabilityScope,
    JsonValue,
    RunCatalogContentStamp,
    RunCatalogEntryIdentity,
    canonical_json,
    fingerprint_json,
)
from .core import CapabilityStoreCore
from .support import CapabilityStoreConflict, CapabilityStoreError, _fetch_mappings


class SnapshotLeaseMixin(CapabilityStoreCore):
    """Snapshot leases and search receipts."""

    __slots__ = ()

    async def _acquire_snapshot_lease_tx(
        self,
        db: aiosqlite.Connection,
        *,
        snapshot_ref: str,
        run_id: str,
        root_run_id: str,
        entries: Sequence[CapabilityCatalogEntry],
    ) -> None:
        pending = await (
            await db.execute(
                """SELECT COUNT(*) FROM capability_publish_intents
                   WHERE status IN ('pending','unknown')"""
            )
        ).fetchone()
        if pending and int(pending[0]) > 0:
            raise CapabilityStoreConflict(
                "publish_reconciliation_required",
                "cannot lease a catalog while publish state is unresolved",
            )
        catalog_entries: list[RunCatalogEntryIdentity] = []
        empty_hash = fingerprint_json([])
        for entry in entries:
            if not entry.bindings:
                raise CapabilityStoreConflict(
                    "snapshot_lease_binding_missing",
                    "legacy catalog entries require a selected binding",
                )
            selected = entry.bindings[0]
            envelope: dict[str, JsonValue] = {
                "selected_binding": selected.to_dict(),
                "visible_bindings": [binding.to_dict() for binding in entry.bindings],
                "pack_id": entry.version.capability_id,
                "version": entry.version.version,
                "manifest_hash": entry.version.manifest_hash,
                "tool_spec_fingerprints": list(entry.expected_tool_fingerprints),
                "instruction_refs_hash": empty_hash,
                "workflow_refs_hash": empty_hash,
                "runtime_descriptor_hash": fingerprint_json(
                    {
                        "domain": "legacy-runtime-descriptor-v1",
                        "descriptor_fingerprint": entry.version.fingerprint,
                    }
                ),
            }
            catalog_entries.append(
                RunCatalogEntryIdentity(
                    entry_kind="pack",
                    descriptor_fingerprint=entry.version.fingerprint,
                    canonical_envelope=envelope,
                )
            )
        content = RunCatalogContentStamp(
            request_scope=CapabilityScope(run_key=root_run_id),
            entries=tuple(catalog_entries),
        )
        request_owner_key = (
            entries[0].bindings[0].owner_key
            if entries and entries[0].bindings
            else LEGACY_LOCAL_OWNER_KEY
        )
        await self._put_run_catalog_snapshot_tx(
            db,
            content,
            request_owner_key=request_owner_key,
            catalog_generation_vector={},
            require_complete=True,
        )
        owner_operation_id = fingerprint_json(
            {
                "domain": "legacy-snapshot-acquire-v2",
                "snapshot_ref": snapshot_ref,
                "run_id": run_id,
            }
        )
        intent = await self._prepare_snapshot_lease_intent_tx(
            db,
            snapshot_ref=snapshot_ref,
            snapshot_ref_schema="legacy_v1",
            run_id=run_id,
            root_run_id=root_run_id,
            request_id=f"legacy:{run_id}",
            turn_id=f"legacy:{run_id}",
            owner_operation_id=owner_operation_id,
            lease_owner_kind="legacy_boundary",
            run_catalog_content_stamp=content.fingerprint,
            entry_set_hash=content.entry_set_hash,
            expected_entry_count=len(content.entries),
        )
        await self._put_snapshot_lease_rows_tx(db, intent)
        owner_record_ref = f"legacy-boundary:{run_id}:{snapshot_ref}"
        owner_record_hash = fingerprint_json(
            {"domain": "legacy-owner-record-v1", "ref": owner_record_ref}
        )
        await self._bind_snapshot_lease_intent_tx(
            db,
            intent.lease_intent_id,
            owner_record_ref=owner_record_ref,
            owner_record_hash=owner_record_hash,
            start_fingerprint=fingerprint_json(
                {
                    "domain": "legacy-start-v1",
                    "snapshot_ref": snapshot_ref,
                    "run_id": run_id,
                    "run_catalog_content_stamp": content.fingerprint,
                }
            ),
        )

    async def acquire_snapshot_lease(self, **kwargs: Any) -> None:
        async with self.write_transaction() as db:
            await self._acquire_snapshot_lease_tx(db, **kwargs)

    async def _release_snapshot_lease_tx(
        self,
        db: aiosqlite.Connection,
        snapshot_ref: str,
        run_id: str,
    ) -> int:
        affected = await _fetch_mappings(
            await db.execute(
                """SELECT DISTINCT pack_id,version,manifest_hash
                   FROM capability_snapshot_leases
                   WHERE snapshot_ref=? AND run_id=? AND released_at IS NULL""",
                (snapshot_ref, run_id),
            )
        )
        intent_rows = await _fetch_mappings(
            await db.execute(
                """SELECT lease_intent_id,lease_intent_hash
                   FROM capability_snapshot_lease_intents
                   WHERE snapshot_ref=? AND run_id=?
                     AND status IN ('prepared','bound')
                   ORDER BY lease_generation""",
                (snapshot_ref, run_id),
            )
        )
        released_count = 0
        for intent_row in intent_rows:
            count_row = await (
                await db.execute(
                    """SELECT COUNT(*) FROM capability_snapshot_leases
                       WHERE lease_intent_id=? AND released_at IS NULL""",
                    (str(intent_row["lease_intent_id"]),),
                )
            ).fetchone()
            released_count += int(count_row[0])
            terminal_ref = f"legacy-release:{run_id}:{snapshot_ref}"
            await self.release_snapshot_lease_intent_in_tx(
                db,
                str(intent_row["lease_intent_id"]),
                intent_hash=str(intent_row["lease_intent_hash"]),
                release_reason="legacy_terminal",
                owner_terminal_or_transition_ref=terminal_ref,
                owner_terminal_or_transition_hash=fingerprint_json(
                    {"domain": "legacy-terminal-v1", "ref": terminal_ref}
                ),
            )
        for row in affected:
            await self._drain_unreferenced_runtimes_tx(
                db,
                pack_id=str(row["pack_id"]),
                version=str(row["version"]),
                manifest_hash=str(row["manifest_hash"]),
            )
        return released_count

    async def release_snapshot_lease(self, snapshot_ref: str, run_id: str) -> int:
        async with self.write_transaction() as db:
            return await self._release_snapshot_lease_tx(db, snapshot_ref, run_id)

    async def _snapshot_lease_fingerprints_tx(
        self,
        db: aiosqlite.Connection,
        *,
        snapshot_ref: str,
        run_id: str,
    ) -> tuple[str, ...]:
        rows = await (
            await db.execute(
                """SELECT tool_spec_fingerprints_json
                   FROM capability_snapshot_leases
                   WHERE snapshot_ref=? AND run_id=? AND released_at IS NULL
                   ORDER BY pack_id,version,manifest_hash""",
                (snapshot_ref, run_id),
            )
        ).fetchall()
        fingerprints: set[str] = set()
        for row in rows:
            payload = json.loads(str(row["tool_spec_fingerprints_json"]))
            if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
                raise CapabilityStoreError(
                    "corrupt_snapshot_lease",
                    "snapshot lease tool fingerprints are invalid",
                )
            fingerprints.update(payload)
        return tuple(sorted(fingerprints))

    async def snapshot_lease_fingerprints(
        self, *, snapshot_ref: str, run_id: str
    ) -> tuple[str, ...]:
        async with self.read_connection() as db:
            return await self._snapshot_lease_fingerprints_tx(
                db, snapshot_ref=snapshot_ref, run_id=run_id
            )

    async def _clone_snapshot_lease_tx(
        self,
        db: aiosqlite.Connection,
        *,
        snapshot_ref: str,
        source_run_id: str,
        target_run_id: str,
        root_run_id: str,
    ) -> tuple[str, ...]:
        if not all(
            isinstance(value, str) and value
            for value in (
                snapshot_ref,
                source_run_id,
                target_run_id,
                root_run_id,
            )
        ):
            raise ValueError("snapshot and run identities are required")
        rows = await _fetch_mappings(
            await db.execute(
                """SELECT l.tool_spec_fingerprints_json,
                          i.run_catalog_content_stamp,i.entry_set_hash,
                          i.expected_entry_count
                   FROM capability_snapshot_leases AS l
                   JOIN capability_snapshot_lease_intents AS i
                     ON i.lease_intent_id=l.lease_intent_id
                   WHERE l.snapshot_ref=? AND l.run_id=?
                     AND l.released_at IS NULL AND i.status='bound'
                   ORDER BY l.entry_ordinal""",
                (snapshot_ref, source_run_id),
            )
        )
        fingerprints: set[str] = set()
        for row in rows:
            payload = str(row["tool_spec_fingerprints_json"])
            parsed = json.loads(payload)
            if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
                raise CapabilityStoreError(
                    "corrupt_snapshot_lease",
                    "snapshot lease tool fingerprints are invalid",
                )
            fingerprints.update(parsed)
        if not rows:
            return ()
        first = rows[0]
        owner_operation_id = fingerprint_json(
            {
                "domain": "legacy-snapshot-clone-v2",
                "snapshot_ref": snapshot_ref,
                "source_run_id": source_run_id,
                "target_run_id": target_run_id,
            }
        )
        intent = await self._prepare_snapshot_lease_intent_tx(
            db,
            snapshot_ref=snapshot_ref,
            snapshot_ref_schema="legacy_v1",
            run_id=target_run_id,
            root_run_id=root_run_id,
            request_id=f"legacy:{target_run_id}",
            turn_id=f"legacy:{target_run_id}",
            owner_operation_id=owner_operation_id,
            lease_owner_kind="queued_child_legacy",
            run_catalog_content_stamp=str(first["run_catalog_content_stamp"]),
            entry_set_hash=str(first["entry_set_hash"]),
            expected_entry_count=int(first["expected_entry_count"]),
        )
        await self._put_snapshot_lease_rows_tx(db, intent)
        owner_record_ref = f"legacy-child-boundary:{source_run_id}:{target_run_id}:{snapshot_ref}"
        await self._bind_snapshot_lease_intent_tx(
            db,
            intent.lease_intent_id,
            owner_record_ref=owner_record_ref,
            owner_record_hash=fingerprint_json(
                {"domain": "legacy-child-owner-v1", "ref": owner_record_ref}
            ),
            start_fingerprint=fingerprint_json(
                {
                    "domain": "legacy-child-start-v1",
                    "snapshot_ref": snapshot_ref,
                    "source_run_id": source_run_id,
                    "target_run_id": target_run_id,
                }
            ),
        )
        return tuple(sorted(fingerprints))

    async def clone_snapshot_lease(
        self,
        *,
        snapshot_ref: str,
        source_run_id: str,
        target_run_id: str,
        root_run_id: str,
    ) -> tuple[str, ...]:
        async with self.write_transaction() as db:
            return await self._clone_snapshot_lease_tx(
                db,
                snapshot_ref=snapshot_ref,
                source_run_id=source_run_id,
                target_run_id=target_run_id,
                root_run_id=root_run_id,
            )

    async def active_snapshot_refs_for_run(self, run_id: str) -> tuple[str, ...]:
        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT DISTINCT snapshot_ref
                       FROM capability_snapshot_leases
                       WHERE run_id=? AND released_at IS NULL
                       ORDER BY snapshot_ref""",
                    (run_id,),
                )
            ).fetchall()
            return tuple(str(row["snapshot_ref"]) for row in rows)

    async def terminal_snapshot_lease_owners(
        self,
    ) -> tuple[tuple[str, str], ...]:
        """Find exact terminal run leases left by a post-commit crash."""

        async with self.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT DISTINCT lease.snapshot_ref,lease.run_id
                       FROM capability_snapshot_leases AS lease
                       JOIN execution_runs AS run ON run.run_id=lease.run_id
                       WHERE lease.released_at IS NULL
                         AND run.status IN ('completed','failed','cancelled')
                       ORDER BY lease.run_id,lease.snapshot_ref"""
                )
            ).fetchall()
            return tuple((str(row["snapshot_ref"]), str(row["run_id"])) for row in rows)

    async def version_has_active_lease(
        self, pack_id: str, version: str, manifest_hash: str
    ) -> bool:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT 1 FROM capability_snapshot_leases
                       WHERE pack_id=? AND version=? AND manifest_hash=?
                         AND released_at IS NULL LIMIT 1""",
                    (pack_id, version, manifest_hash),
                )
            ).fetchone()
            return row is not None

    async def record_search_receipt(
        self,
        *,
        receipt_id: str,
        root_run_id: str,
        catalog_stamp_fingerprint: str,
        query_hash: str,
        result: Mapping[str, JsonValue],
        created_at: float,
    ) -> None:
        result_json = canonical_json(dict(result))
        async with self.write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM capability_search_receipts
                       WHERE receipt_id=? OR (
                           root_run_id=? AND catalog_stamp_fingerprint=?
                           AND query_hash=?
                       )""",
                    (
                        receipt_id,
                        root_run_id,
                        catalog_stamp_fingerprint,
                        query_hash,
                    ),
                )
            ).fetchone()
            if row is not None:
                if str(row["receipt_id"]) != receipt_id or str(row["result_json"]) != result_json:
                    raise CapabilityStoreConflict(
                        "search_receipt_conflict",
                        "search receipt identity changed",
                    )
                return
            await db.execute(
                """INSERT INTO capability_search_receipts(
                    receipt_id,root_run_id,catalog_stamp_fingerprint,query_hash,
                    result_json,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    receipt_id,
                    root_run_id,
                    catalog_stamp_fingerprint,
                    query_hash,
                    result_json,
                    created_at,
                ),
            )

    async def has_search_evidence(
        self, *, root_run_id: str, catalog_stamp_fingerprint: str
    ) -> bool:
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT 1 FROM capability_search_receipts
                       WHERE root_run_id=? AND catalog_stamp_fingerprint=?
                       LIMIT 1""",
                    (root_run_id, catalog_stamp_fingerprint),
                )
            ).fetchone()
            return row is not None
