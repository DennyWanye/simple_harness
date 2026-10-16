# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Pack versions, version storage, skill bindings and detail tokens."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import aiosqlite

from ..contracts import (
    EMPTY_OWNER_BINDING_SET_STAMP,
    EMPTY_RECEIPT_SET_HASH,
    CapabilityBinding,
    JsonValue,
    OwnerScopeKey,
    PlatformDetailSnapshot,
    PlatformDetailToken,
    PlatformDetailTokenVector,
    canonical_json,
    fingerprint_json,
)
from .core import CapabilityStoreCore
from .support import (
    CapabilityStoreConflict,
    CapabilityStoreError,
    CapabilityVersionRecord,
    CapabilityVersionStorage,
    _binding_from_row,
    _default_owner_key,
    _detail_token_from_row,
    _fetch_mappings,
    _json_array,
    _json_object,
    _version_from_row,
)


def _version_storage_from_row(
    row: Mapping[str, Any],
) -> CapabilityVersionStorage:
    return CapabilityVersionStorage(
        pack_id=str(row["pack_id"]),
        version=str(row["version"]),
        manifest_hash=str(row["manifest_hash"]),
        storage_key=str(row["storage_key"]),
        pack_storage_schema=str(row["pack_storage_schema"]),
        pack_root_hash=str(row["pack_root_hash"]),
        environment_storage_schema=str(row["environment_storage_schema"]),
        environment_root_hash=str(row["environment_root_hash"]),
        archive_hash=str(row["archive_hash"]),
        created_at=float(row["created_at"]),
    )


class VersionBindingMixin(CapabilityStoreCore):
    """Pack versions, version storage, skill bindings and detail tokens."""

    __slots__ = ()

    async def _record_version_tx(
        self, db: aiosqlite.Connection, record: CapabilityVersionRecord
    ) -> CapabilityVersionRecord:
        descriptor = record.descriptor
        install_path = Path(record.install_path).resolve(strict=False)
        if record.validation_status not in {"pending", "healthy", "degraded", "failed"}:
            raise CapabilityStoreError(
                "invalid_validation_status",
                f"unknown validation status: {record.validation_status}",
            )
        source_json = canonical_json({"source": descriptor.source})
        payload = (
            descriptor.capability_id,
            descriptor.version,
            descriptor.manifest_hash,
            canonical_json(descriptor.to_dict()),
            source_json,
            str(install_path),
            record.validation_status,
            canonical_json(list(record.expected_tool_fingerprints)),
            record.parent_version,
            record.parent_manifest_hash,
            record.derived_from_receipt_ref,
            record.created_at,
        )
        existing = await (
            await db.execute(
                "SELECT * FROM capability_versions WHERE pack_id=? AND version=?",
                (descriptor.capability_id, descriptor.version),
            )
        ).fetchone()
        if existing is not None:
            current = _version_from_row(existing)
            if (
                current.descriptor.manifest_hash != descriptor.manifest_hash
                or current.descriptor.fingerprint != descriptor.fingerprint
                or current.install_path != install_path
                or current.expected_tool_fingerprints != record.expected_tool_fingerprints
                or current.parent_version != record.parent_version
                or current.parent_manifest_hash != record.parent_manifest_hash
                or current.derived_from_receipt_ref != record.derived_from_receipt_ref
            ):
                raise CapabilityStoreConflict(
                    "capability_version_conflict",
                    "same capability version already exists with different immutable data",
                )
            return current
        await db.execute(
            """INSERT INTO capability_versions(
                pack_id,version,manifest_hash,descriptor_json,source_json,
                install_path,validation_status,expected_tool_fingerprints_json,
                parent_version,parent_manifest_hash,derived_from_receipt_ref,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            payload,
        )
        await self._bump_generation_tx(db, catalog=True, binding=False)
        return record

    async def record_version(self, record: CapabilityVersionRecord) -> CapabilityVersionRecord:
        async with self.write_transaction() as db:
            return await self._record_version_tx(db, record)

    async def migrate_version_tool_fingerprint_schema(
        self,
        *,
        pack_id: str,
        version: str,
        manifest_hash: str,
        expected_fingerprints: Sequence[str],
        replacement_fingerprints: Sequence[str],
    ) -> bool:
        """CAS one validated ToolSpec fingerprint-schema upgrade.

        Capability pack identity and files remain immutable.  This narrowly
        migrates only the host-computed fingerprint projection after the
        caller has reproduced every legacy value from the validated pack.
        """

        expected = tuple(str(value) for value in expected_fingerprints)
        replacement = tuple(str(value) for value in replacement_fingerprints)
        if not expected or len(expected) != len(replacement):
            raise ValueError("fingerprint migration requires equal non-empty sets")
        if any(
            len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
            for value in expected + replacement
        ):
            raise ValueError("fingerprint migration values must be sha256 hex")
        async with self.write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT expected_tool_fingerprints_json
                       FROM capability_versions
                       WHERE pack_id=? AND version=? AND manifest_hash=?""",
                    (pack_id, version, manifest_hash),
                )
            ).fetchone()
            if row is None:
                raise CapabilityStoreError(
                    "capability_version_missing",
                    "fingerprint migration target is absent",
                )
            current = tuple(_json_array(str(row[0])))
            if current == replacement:
                return False
            if current != expected:
                raise CapabilityStoreConflict(
                    "capability_fingerprint_migration_conflict",
                    "capability ToolSpec fingerprints changed concurrently",
                )
            cursor = await db.execute(
                """UPDATE capability_versions
                   SET expected_tool_fingerprints_json=?
                   WHERE pack_id=? AND version=? AND manifest_hash=?
                     AND expected_tool_fingerprints_json=?""",
                (
                    json.dumps(list(replacement), separators=(",", ":")),
                    pack_id,
                    version,
                    manifest_hash,
                    str(row[0]),
                ),
            )
            if cursor.rowcount != 1:
                raise CapabilityStoreConflict(
                    "capability_fingerprint_migration_conflict",
                    "capability ToolSpec fingerprints changed concurrently",
                )
            await self._bump_generation_tx(
                db,
                catalog=True,
                binding=False,
            )
            return True

    async def _put_version_storage_tx(
        self,
        db: aiosqlite.Connection,
        record: CapabilityVersionStorage,
    ) -> CapabilityVersionStorage:
        identity = (record.pack_id, record.version, record.manifest_hash)
        version_row = await (
            await db.execute(
                """SELECT 1 FROM capability_versions
                   WHERE pack_id=? AND version=? AND manifest_hash=?""",
                identity,
            )
        ).fetchone()
        if version_row is None:
            raise CapabilityStoreError(
                "capability_version_missing",
                "version storage cannot reference an absent capability version",
            )
        identity_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_version_storage
                   WHERE pack_id=? AND version=? AND manifest_hash=?""",
                identity,
            )
        )
        if identity_rows:
            current = _version_storage_from_row(identity_rows[0])
            if current != record:
                raise CapabilityStoreConflict(
                    "version_storage_conflict",
                    "immutable version storage mapping changed",
                )
            return current
        key_rows = await _fetch_mappings(
            await db.execute(
                """SELECT * FROM capability_version_storage
                   WHERE storage_key=?""",
                (record.storage_key,),
            )
        )
        if key_rows:
            occupied = _version_storage_from_row(key_rows[0])
            if (
                occupied.pack_id,
                occupied.version,
                occupied.manifest_hash,
            ) != identity:
                raise CapabilityStoreConflict(
                    "storage_key_collision",
                    "storage key belongs to another full capability identity",
                )
        await db.execute(
            """INSERT INTO capability_version_storage(
                pack_id,version,manifest_hash,storage_key,
                pack_storage_schema,pack_root_hash,
                environment_storage_schema,environment_root_hash,
                archive_hash,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                record.pack_id,
                record.version,
                record.manifest_hash,
                record.storage_key,
                record.pack_storage_schema,
                record.pack_root_hash,
                record.environment_storage_schema,
                record.environment_root_hash,
                record.archive_hash,
                record.created_at,
            ),
        )
        return record

    async def put_version_storage(
        self, record: CapabilityVersionStorage
    ) -> CapabilityVersionStorage:
        async with self.write_transaction() as db:
            return await self._put_version_storage_tx(db, record)

    async def get_version_storage(
        self, pack_id: str, version: str, manifest_hash: str
    ) -> CapabilityVersionStorage | None:
        async with self.read_connection() as db:
            rows = await _fetch_mappings(
                await db.execute(
                    """SELECT * FROM capability_version_storage
                       WHERE pack_id=? AND version=? AND manifest_hash=?""",
                    (pack_id, version, manifest_hash),
                )
            )
            return None if not rows else _version_storage_from_row(rows[0])

    async def get_version(
        self, pack_id: str, version: str, manifest_hash: str | None = None
    ) -> CapabilityVersionRecord | None:
        async with self.read_connection() as db:
            if manifest_hash is None:
                row = await (
                    await db.execute(
                        "SELECT * FROM capability_versions WHERE pack_id=? AND version=?",
                        (pack_id, version),
                    )
                ).fetchone()
            else:
                row = await (
                    await db.execute(
                        """SELECT * FROM capability_versions
                           WHERE pack_id=? AND version=? AND manifest_hash=?""",
                        (pack_id, version, manifest_hash),
                    )
                ).fetchone()
            return None if row is None else _version_from_row(row)

    async def list_versions(
        self, pack_id: str | None = None
    ) -> tuple[CapabilityVersionRecord, ...]:
        async with self.read_connection() as db:
            if pack_id is None:
                rows = await (
                    await db.execute(
                        "SELECT * FROM capability_versions ORDER BY pack_id,created_at"
                    )
                ).fetchall()
            else:
                rows = await (
                    await db.execute(
                        """SELECT * FROM capability_versions WHERE pack_id=?
                           ORDER BY created_at""",
                        (pack_id,),
                    )
                ).fetchall()
            return tuple(_version_from_row(row) for row in rows)

    @staticmethod
    def _binding_id(owner_key: str, scope: str, scope_key: str, pack_id: str) -> str:
        return hashlib.sha256(f"{owner_key}|{scope}|{scope_key}|{pack_id}".encode()).hexdigest()

    async def _set_binding_tx(
        self,
        db: aiosqlite.Connection,
        *,
        scope: str,
        scope_key: str,
        pack_id: str,
        version: str,
        manifest_hash: str,
        expected_generation: int,
        enabled: bool = True,
        owner_key: str | None = None,
        management_policy: str | None = None,
    ) -> CapabilityBinding:
        if scope not in {"builtin", "run", "project", "user"}:
            raise CapabilityStoreError("invalid_scope", f"unknown scope: {scope}")
        resolved_owner_key = str(owner_key or _default_owner_key(scope)).strip()
        if not resolved_owner_key:
            raise CapabilityStoreError("invalid_owner_key", "owner key is required")
        resolved_management_policy = str(
            management_policy or ("host_managed" if scope == "builtin" else "legacy_import")
        )
        if resolved_management_policy not in {
            "host_managed",
            "user_managed",
            "legacy_import",
        }:
            raise CapabilityStoreError("invalid_management_policy", "unknown management policy")
        version_row = await (
            await db.execute(
                """SELECT 1 FROM capability_versions
                   WHERE pack_id=? AND version=? AND manifest_hash=?""",
                (pack_id, version, manifest_hash),
            )
        ).fetchone()
        if version_row is None:
            raise CapabilityStoreError(
                "capability_version_not_found",
                f"unknown capability version {pack_id}@{version}",
            )
        row = await (
            await db.execute(
                """SELECT * FROM capability_bindings
                   WHERE owner_key=? AND scope=? AND scope_key=? AND pack_id=?""",
                (resolved_owner_key, scope, scope_key, pack_id),
            )
        ).fetchone()
        now = self._clock()
        binding_id = self._binding_id(resolved_owner_key, scope, scope_key, pack_id)
        previous_identity: tuple[str, str, str] | None = None
        if row is None:
            if expected_generation != 0:
                raise CapabilityStoreConflict(
                    "binding_generation_conflict", "binding was not created"
                )
            generation = 1
            await db.execute(
                """INSERT INTO capability_bindings(
                    binding_id,owner_key,scope,scope_key,pack_id,active_version,
                    active_manifest_hash,generation,enabled,management_policy,
                    management_generation,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    binding_id,
                    resolved_owner_key,
                    scope,
                    scope_key,
                    pack_id,
                    version,
                    manifest_hash,
                    generation,
                    int(enabled),
                    resolved_management_policy,
                    1,
                    now,
                ),
            )
        else:
            current = _binding_from_row(row)
            if (
                current.version == version
                and current.manifest_hash == manifest_hash
                and current.active == enabled
                and current.management_policy == resolved_management_policy
            ):
                if expected_generation not in {
                    current.generation,
                    current.generation - 1,
                }:
                    raise CapabilityStoreConflict(
                        "binding_generation_conflict",
                        "binding generation changed",
                    )
                return current
            if current.generation != expected_generation:
                raise CapabilityStoreConflict(
                    "binding_generation_conflict", "binding generation changed"
                )
            if current.active:
                previous_identity = (
                    current.capability_id,
                    current.version,
                    current.manifest_hash,
                )
            generation = current.generation + 1
            management_generation = current.management_generation + 1
            cursor = await db.execute(
                """UPDATE capability_bindings SET
                    active_version=?,active_manifest_hash=?,generation=?,
                    enabled=?,management_policy=?,management_generation=?,
                    updated_at=?
                   WHERE binding_id=? AND generation=? AND owner_key=?""",
                (
                    version,
                    manifest_hash,
                    generation,
                    int(enabled),
                    resolved_management_policy,
                    management_generation,
                    now,
                    current.binding_id,
                    expected_generation,
                    resolved_owner_key,
                ),
            )
            if cursor.rowcount != 1:
                raise CapabilityStoreConflict(
                    "binding_generation_conflict", "binding generation changed"
                )
        await self._bump_generation_tx(db, catalog=True, binding=True)
        key = OwnerScopeKey(
            resolved_owner_key,
            scope,  # type: ignore[arg-type]
            scope_key,
        )
        await self._touch_owner_detail_tx(db, key)
        if previous_identity is not None and (
            previous_identity != (pack_id, version, manifest_hash) or not enabled
        ):
            await self._drain_unreferenced_runtimes_tx(
                db,
                pack_id=previous_identity[0],
                version=previous_identity[1],
                manifest_hash=previous_identity[2],
            )
        return CapabilityBinding(
            binding_id=binding_id,
            capability_id=pack_id,
            version=version,
            manifest_hash=manifest_hash,
            scope=scope,  # type: ignore[arg-type]
            scope_key=scope_key,
            active=enabled,
            generation=generation,
            owner_key=resolved_owner_key,
            management_policy=resolved_management_policy,  # type: ignore[arg-type]
            management_generation=(1 if row is None else current.management_generation + 1),
        )

    async def set_binding(self, **kwargs: Any) -> CapabilityBinding:
        async with self.write_transaction() as db:
            return await self._set_binding_tx(db, **kwargs)

    async def converge_legacy_project_skill_bindings(
        self, *, global_owner_key: str
    ) -> tuple[Mapping[str, JsonValue], ...]:
        """Deterministically promote exact legacy Project Skill bindings.

        Every Project source for a pack participates in one frozen stamp.  A
        promotion is legal only when version, manifest, member content and
        durable install receipt all agree.  Promotion and retirement happen
        in the same SQLite transaction; ambiguous state is recorded durably
        and left untouched for an explicit reinstall/choice.
        """

        owner_key = str(global_owner_key).strip()
        if not owner_key.startswith("user:v2:"):
            raise CapabilityStoreError(
                "invalid_global_owner_key", "validated user-global owner is required"
            )
        active_statuses = (
            "staging",
            "awaiting_confirmation",
            "publishing",
            "published_pending_runtime_verification",
            "succeeded",
            "unknown",
        )
        results: list[Mapping[str, JsonValue]] = []
        async with self.write_transaction() as db:
            rows = await (
                await db.execute(
                    f"""SELECT b.binding_id,b.owner_key,b.scope_key,b.pack_id,
                           b.active_version AS version,
                           b.active_manifest_hash AS manifest_hash,b.generation,
                           i.intent_id,i.status,i.settlement_ref,i.verification_ref,
                           i.member_set_stamp,m.ordinal,m.content_hash,
                           h.operation_id,o.kind AS operation_kind,
                           o.phase AS operation_phase,o.status AS operation_status,
                           o.request_json,h.member_set_stamp AS handoff_member_set_stamp,
                           pe.status AS evidence_status,pe.evidence_json,
                           om.committed_version,om.committed_manifest_hash,
                           om.committed_set_stamp
                    FROM capability_bindings b
                    JOIN capability_skill_install_members m
                      ON m.pack_id=b.pack_id AND m.version=b.active_version
                     AND m.manifest_hash=b.active_manifest_hash
                    JOIN capability_skill_install_intents i
                      ON i.intent_id=m.intent_id AND i.project_scope_key=b.scope_key
                    LEFT JOIN capability_skill_install_handoffs h
                      ON h.intent_id=i.intent_id
                    LEFT JOIN capability_operations o
                      ON o.operation_id=h.operation_id
                    LEFT JOIN capability_operation_phase_evidence pe
                      ON pe.operation_id=h.operation_id AND pe.phase='batch_committed'
                    LEFT JOIN capability_operation_members om
                      ON om.operation_id=h.operation_id AND om.ordinal=m.ordinal
                    WHERE b.scope='project' AND b.enabled=1
                      AND i.source_json NOT LIKE '%global-skill-install-source-v2%'
                      AND i.status IN ({",".join("?" for _ in active_statuses)})
                    ORDER BY b.pack_id,b.owner_key,b.scope_key,b.binding_id,i.intent_id""",
                    active_statuses,
                )
            ).fetchall()
            by_pack: dict[str, list[Mapping[str, Any]]] = {}
            for row in rows:
                by_pack.setdefault(str(row["pack_id"]), []).append(row)
            for pack_id, candidates in sorted(by_pack.items()):
                frozen = []
                for row in candidates:
                    evidence = (
                        _json_object(
                            None if row["evidence_json"] is None else str(row["evidence_json"])
                        )
                        or {}
                    )
                    evidence_members = tuple(
                        item for item in evidence.get("members", ()) if isinstance(item, Mapping)
                    )
                    operation_member_rows = await (
                        await db.execute(
                            """SELECT ordinal,normalized_name,pack_id,version,
                                  manifest_hash,content_hash,source_digest,
                                  committed_version,committed_manifest_hash,
                                  committed_set_stamp
                           FROM capability_operation_members
                           WHERE operation_id=? ORDER BY ordinal""",
                            (str(row["operation_id"] or ""),),
                        )
                    ).fetchall()
                    intent_member_rows = await (
                        await db.execute(
                            """SELECT ordinal,normalized_name,pack_id,version,
                                  manifest_hash,content_hash,source_digest
                           FROM capability_skill_install_members
                           WHERE intent_id=? ORDER BY ordinal""",
                            (str(row["intent_id"]),),
                        )
                    ).fetchall()
                    rebuilt_members = [
                        {
                            "pack_id": str(item["pack_id"]),
                            "version": str(item["version"]),
                            "manifest_hash": str(item["manifest_hash"]),
                            "content_hash": str(item["content_hash"]),
                        }
                        for item in operation_member_rows
                    ]
                    intended_members = [
                        {
                            "pack_id": str(item["pack_id"]),
                            "version": str(item["version"]),
                            "manifest_hash": str(item["manifest_hash"]),
                            "content_hash": str(item["content_hash"]),
                        }
                        for item in intent_member_rows
                    ]
                    receipt_member_identities = [
                        {
                            "pack_id": str(item.get("pack_id") or ""),
                            "version": str(item.get("version") or ""),
                            "manifest_hash": str(item.get("manifest_hash") or ""),
                            "content_hash": str(item.get("content_hash") or ""),
                        }
                        for item in evidence_members
                    ]
                    full_member_set_valid = (
                        rebuilt_members == intended_members == receipt_member_identities
                        and tuple(int(item["ordinal"]) for item in operation_member_rows)
                        == tuple(range(len(operation_member_rows)))
                        and all(
                            str(item["committed_version"] or "") == str(item["version"])
                            and str(item["committed_manifest_hash"] or "")
                            == str(item["manifest_hash"])
                            and str(item["committed_set_stamp"] or "")
                            == str(row["member_set_stamp"])
                            for item in operation_member_rows
                        )
                    )
                    expected_member = {
                        "pack_id": str(row["pack_id"]),
                        "version": str(row["version"]),
                        "manifest_hash": str(row["manifest_hash"]),
                        "content_hash": str(row["content_hash"]),
                    }
                    matching_members = [
                        item
                        for item in evidence_members
                        if all(
                            str(item.get(key) or "") == value
                            for key, value in expected_member.items()
                        )
                    ]
                    receipt_payload = {
                        key: value
                        for key, value in evidence.items()
                        if key not in {"manager_receipt_hash", "install_root"}
                    }
                    receipt_hash = str(evidence.get("manager_receipt_hash") or "")
                    operation_request = (
                        _json_object(
                            None if row["request_json"] is None else str(row["request_json"])
                        )
                        or {}
                    )
                    evidence_valid = all(
                        (
                            str(row["status"]) == "succeeded",
                            str(row["operation_kind"] or "") == "skill_install_batch",
                            str(row["operation_phase"] or "") == "batch_committed",
                            str(row["operation_status"] or "") == "succeeded",
                            str(row["evidence_status"] or "") == "committed",
                            str(evidence.get("schema") or "")
                            == "capability-batch-manager-receipt-v1",
                            str(evidence.get("operation_id") or "")
                            == str(row["operation_id"] or ""),
                            str(evidence.get("scope") or "") == "project",
                            str(evidence.get("scope_key") or "") == str(row["scope_key"]),
                            str(evidence.get("project_scope_key") or "") == str(row["scope_key"]),
                            str(evidence.get("owner_key") or "") == str(row["owner_key"]),
                            str(evidence.get("committed_set_stamp") or "")
                            == str(row["member_set_stamp"]),
                            str(row["handoff_member_set_stamp"] or "")
                            == str(row["member_set_stamp"]),
                            str(operation_request.get("member_set_stamp") or "")
                            == str(row["member_set_stamp"]),
                            str(evidence.get("publication_state") or "") == "active",
                            len(matching_members) == 1,
                            full_member_set_valid,
                            str(row["committed_version"] or "") == str(row["version"]),
                            str(row["committed_manifest_hash"] or "") == str(row["manifest_hash"]),
                            str(row["committed_set_stamp"] or "") == str(row["member_set_stamp"]),
                            len(receipt_hash) == 64,
                            receipt_hash == fingerprint_json(receipt_payload),
                            str(row["settlement_ref"] or "") == receipt_hash,
                            bool(str(row["verification_ref"] or "")),
                        )
                    )
                    frozen.append(
                        {
                            "binding_id": str(row["binding_id"]),
                            "owner_key": str(row["owner_key"]),
                            "scope_key": str(row["scope_key"]),
                            "generation": int(row["generation"]),
                            "intent_id": str(row["intent_id"]),
                            "intent_status": str(row["status"]),
                            "version": str(row["version"]),
                            "manifest_hash": str(row["manifest_hash"]),
                            "content_hash": str(row["content_hash"]),
                            "manager_receipt_hash": receipt_hash,
                            "runtime_verification_ref": str(row["verification_ref"] or ""),
                            "manager_evidence_valid": evidence_valid,
                            "ordered_member_set_hash": fingerprint_json(rebuilt_members),
                        }
                    )
                source_stamp = fingerprint_json(frozen)
                prior = await (
                    await db.execute(
                        """SELECT * FROM capability_legacy_global_convergence
                       WHERE global_owner_key=? AND pack_id=?""",
                        (owner_key, pack_id),
                    )
                ).fetchone()
                if prior is not None:
                    if str(prior["source_binding_set_stamp"]) != source_stamp:
                        raise CapabilityStoreConflict(
                            "legacy_global_convergence_source_changed",
                            f"legacy source set changed after decision for {pack_id}",
                        )
                    results.append(
                        {
                            "pack_id": pack_id,
                            "outcome": str(prior["outcome"]),
                            "source_binding_set_stamp": source_stamp,
                        }
                    )
                    continue
                identities = {
                    (item["version"], item["manifest_hash"], item["content_hash"])
                    for item in frozen
                }
                selected = next(iter(identities)) if len(identities) == 1 else None
                existing = await (
                    await db.execute(
                        """SELECT b.*,v.derived_from_receipt_ref
                       FROM capability_bindings b
                       JOIN capability_versions v
                         ON v.pack_id=b.pack_id AND v.version=b.active_version
                        AND v.manifest_hash=b.active_manifest_hash
                       WHERE b.owner_key=? AND b.scope='user' AND b.scope_key=?
                         AND b.pack_id=?""",
                        (owner_key, owner_key, pack_id),
                    )
                ).fetchone()
                conflict_reasons: list[str] = []
                if selected is None:
                    conflict_reasons.append("legacy_sources_differ")
                if any(not item["manager_evidence_valid"] for item in frozen):
                    conflict_reasons.append("legacy_manager_receipt_invalid")
                if (
                    existing is not None
                    and selected is not None
                    and (str(existing["active_version"]), str(existing["active_manifest_hash"]))
                    != selected[:2]
                ):
                    conflict_reasons.append("existing_global_binding_differs")
                if existing is not None and selected is not None:
                    receipt_hashes = {str(item["manager_receipt_hash"]) for item in frozen}
                    existing_receipt = str(existing["derived_from_receipt_ref"] or "")
                    # Version/manifest alone do not identify installed bytes.  The
                    # immutable version row must point at one of the Manager
                    # receipts just validated above; that receipt commits the
                    # exact ordered content hashes.  No receipt means content is
                    # unprovable, not "probably the same".
                    if existing_receipt not in receipt_hashes:
                        conflict_reasons.append("existing_global_content_or_receipt_differs")
                if conflict_reasons:
                    conflict = {"reasons": sorted(set(conflict_reasons)), "sources": frozen}
                    await db.execute(
                        """INSERT INTO capability_legacy_global_convergence(
                           global_owner_key,pack_id,source_binding_set_stamp,outcome,
                           selected_identity_json,conflict_json,created_at
                           ) VALUES(?,?,?,'legacy_global_conflict',NULL,?,?)""",
                        (owner_key, pack_id, source_stamp, canonical_json(conflict), self._clock()),
                    )
                    results.append(
                        {
                            "pack_id": pack_id,
                            "outcome": "legacy_global_conflict",
                            "source_binding_set_stamp": source_stamp,
                        }
                    )
                    continue
                assert selected is not None
                await self._set_binding_tx(
                    db,
                    scope="user",
                    scope_key=owner_key,
                    pack_id=pack_id,
                    version=selected[0],
                    manifest_hash=selected[1],
                    expected_generation=(0 if existing is None else int(existing["generation"])),
                    owner_key=owner_key,
                    management_policy="user_managed",
                )
                retired_binding_ids: set[str] = set()
                for item in frozen:
                    if item["binding_id"] in retired_binding_ids:
                        continue
                    cursor = await db.execute(
                        """DELETE FROM capability_bindings
                           WHERE binding_id=? AND generation=?""",
                        (item["binding_id"], item["generation"]),
                    )
                    if cursor.rowcount != 1:
                        raise CapabilityStoreConflict(
                            "legacy_project_retirement_cas_conflict",
                            f"legacy Project binding changed for {pack_id}",
                        )
                    retired_binding_ids.add(item["binding_id"])
                    await self._touch_owner_detail_tx(
                        db, OwnerScopeKey(item["owner_key"], "project", item["scope_key"])
                    )
                await self._bump_generation_tx(db, catalog=True, binding=True)
                selected_json = {
                    "version": selected[0],
                    "manifest_hash": selected[1],
                    "content_hash": selected[2],
                    "manager_receipt_set_hash": fingerprint_json(
                        sorted(str(item["manager_receipt_hash"]) for item in frozen)
                    ),
                }
                await db.execute(
                    """INSERT INTO capability_legacy_global_convergence(
                       global_owner_key,pack_id,source_binding_set_stamp,outcome,
                       selected_identity_json,conflict_json,created_at
                       ) VALUES(?,?,?,'promoted',?,NULL,?)""",
                    (
                        owner_key,
                        pack_id,
                        source_stamp,
                        canonical_json(selected_json),
                        self._clock(),
                    ),
                )
                results.append(
                    {
                        "pack_id": pack_id,
                        "outcome": "promoted",
                        "source_binding_set_stamp": source_stamp,
                    }
                )
        return tuple(results)

    async def get_binding(
        self,
        scope: str,
        scope_key: str,
        pack_id: str,
        *,
        owner_key: str | None = None,
    ) -> CapabilityBinding | None:
        resolved_owner_key = str(owner_key or _default_owner_key(scope))
        async with self.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM capability_bindings
                       WHERE owner_key=? AND scope=? AND scope_key=? AND pack_id=?""",
                    (resolved_owner_key, scope, scope_key, pack_id),
                )
            ).fetchone()
            return None if row is None else _binding_from_row(row)

    async def ensure_owner_detail_key(self, key: OwnerScopeKey) -> PlatformDetailToken:
        async with self.write_transaction() as db:
            return await self._ensure_owner_detail_key_tx(db, key)

    async def _read_detail_tokens_tx(
        self,
        db: aiosqlite.Connection,
        keys: Sequence[OwnerScopeKey],
    ) -> PlatformDetailTokenVector:
        ordered = tuple(sorted(set(keys)))
        if not ordered:
            return PlatformDetailTokenVector(())
        clauses = " OR ".join("(owner_key=? AND scope=? AND scope_key=?)" for _ in ordered)
        params = tuple(
            value for key in ordered for value in (key.owner_key, key.scope, key.scope_key)
        )
        rows = await (
            await db.execute(
                f"""SELECT * FROM capability_owner_detail_versions
                    WHERE {clauses}""",
                params,
            )
        ).fetchall()
        existing = {
            OwnerScopeKey(
                str(row["owner_key"]),
                str(row["scope"]),  # type: ignore[arg-type]
                str(row["scope_key"]),
            ): _detail_token_from_row(row)
            for row in rows
        }
        return PlatformDetailTokenVector(
            tuple(
                existing.get(key)
                or PlatformDetailToken(
                    key=key,
                    exists=False,
                    version=0,
                    owner_catalog_generation=0,
                    committed_owner_binding_set_stamp=(EMPTY_OWNER_BINDING_SET_STAMP),
                    manager_receipt_set_hash=EMPTY_RECEIPT_SET_HASH,
                )
                for key in ordered
            )
        )

    async def read_detail_token_vector(
        self, keys: Sequence[OwnerScopeKey]
    ) -> PlatformDetailTokenVector:
        async with self.read_connection() as db:
            return await self._read_detail_tokens_tx(db, keys)

    async def read_detail_snapshot(self, keys: Sequence[OwnerScopeKey]) -> PlatformDetailSnapshot:
        """Read exact owner keys in one Store transaction.

        Platform-level publish locks, CatalogGate checks, Manager receipts, and
        fallback/source reconciliation are intentionally outside this Store
        foundation.
        """

        ordered = tuple(sorted(set(keys)))
        async with self.read_connection() as db:
            await db.execute("BEGIN")
            try:
                tokens = await self._read_detail_tokens_tx(db, ordered)
                if not ordered:
                    bindings: tuple[CapabilityBinding, ...] = ()
                else:
                    clauses = " OR ".join(
                        "(owner_key=? AND scope=? AND scope_key=?)" for _ in ordered
                    )
                    params = tuple(
                        value
                        for key in ordered
                        for value in (key.owner_key, key.scope, key.scope_key)
                    )
                    rows = await (
                        await db.execute(
                            f"""SELECT * FROM capability_bindings
                                WHERE {clauses}
                                ORDER BY owner_key,scope,scope_key,pack_id""",
                            params,
                        )
                    ).fetchall()
                    bindings = tuple(_binding_from_row(row) for row in rows)
            finally:
                if db.in_transaction:
                    await db.rollback()
        return PlatformDetailSnapshot(tokens=tokens, bindings=bindings)

    async def delete_binding(
        self,
        *,
        owner_key: str,
        scope: str,
        scope_key: str,
        pack_id: str,
        expected_generation: int,
    ) -> bool:
        key = OwnerScopeKey(
            owner_key,
            scope,
            scope_key,  # type: ignore[arg-type]
        )
        async with self.write_transaction() as db:
            cursor = await db.execute(
                """DELETE FROM capability_bindings
                   WHERE owner_key=? AND scope=? AND scope_key=? AND pack_id=?
                     AND generation=?""",
                (
                    key.owner_key,
                    key.scope,
                    key.scope_key,
                    pack_id,
                    expected_generation,
                ),
            )
            if cursor.rowcount != 1:
                row = await (
                    await db.execute(
                        """SELECT generation FROM capability_bindings
                           WHERE owner_key=? AND scope=? AND scope_key=?
                             AND pack_id=?""",
                        (key.owner_key, key.scope, key.scope_key, pack_id),
                    )
                ).fetchone()
                if row is None:
                    return False
                raise CapabilityStoreConflict(
                    "binding_generation_conflict", "binding generation changed"
                )
            await self._bump_generation_tx(db, catalog=True, binding=True)
            await self._touch_owner_detail_tx(db, key)
            return True
