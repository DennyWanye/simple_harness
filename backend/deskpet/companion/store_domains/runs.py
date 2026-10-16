"""Run growth snapshots, memory read scope and run bindings."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any

from ..contracts import (
    CompanionConflictError,
    CompanionStateError,
    GrowthDependency,
    OwnerRef,
    RunGrowthSnapshot,
)
from .core import CompanionStoreCore
from .support import canonical_hash, canonical_json


class RunBindingMixin(CompanionStoreCore):
    """Run growth snapshots, memory read scope and run bindings."""

    __slots__ = ()

    # ------------------------------------------------------------------
    # Frozen run growth snapshots

    def create_run_growth_snapshot(
        self,
        owner: OwnerRef,
        snapshot: RunGrowthSnapshot,
        dependencies: Sequence[GrowthDependency],
        *,
        owner_memory_read_scopes: Sequence[Mapping[str, Any]] = (),
    ) -> Mapping[str, Any]:
        normalized = sorted(
            dependencies, key=lambda item: (item.dependency_kind, item.dependency_id)
        )
        memory_scopes = {str(scope["scope_ref"]): dict(scope) for scope in owner_memory_read_scopes}
        memory_dependencies = {
            item.dependency_id: item
            for item in normalized
            if item.dependency_kind == "memory_scope"
        }
        if set(memory_scopes) != set(memory_dependencies):
            raise CompanionStateError("memory_scope_dependency_payload_mismatch")
        for scope_ref, scope in memory_scopes.items():
            dependency = memory_dependencies[scope_ref]
            if (
                str(scope.get("profile_id")) != owner.profile_id
                or int(scope.get("profile_generation", 0)) != owner.profile_generation
                or str(scope.get("scope_hash")) != dependency.content_hash
                or str(scope.get("scope_ref")) != scope_ref
            ):
                raise CompanionStateError("memory_scope_owner_or_hash_mismatch")
        dependency_payload = [asdict(item) for item in normalized]
        computed_hash = canonical_hash(
            {
                "schema_version": snapshot.schema_version,
                "profile_id": owner.profile_id,
                "profile_generation": owner.profile_generation,
                "snapshot_id": snapshot.snapshot_id,
                "request_id": snapshot.request_id,
                "run_id": snapshot.run_id,
                "snapshot_generation": snapshot.snapshot_generation,
                "prior_snapshot_id": snapshot.prior_snapshot_id,
                "prior_snapshot_hash": snapshot.prior_snapshot_hash,
                "dependencies": dependency_payload,
            }
        )
        if snapshot.snapshot_hash != computed_hash:
            raise CompanionConflictError("run_growth_snapshot_hash_mismatch")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM run_growth_snapshots
                   WHERE profile_id=? AND profile_generation=?
                     AND (snapshot_id=? OR
                          (request_id=? AND snapshot_generation=0) OR
                          (run_id=? AND snapshot_generation=?))""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    snapshot.snapshot_id,
                    snapshot.request_id,
                    snapshot.run_id,
                    snapshot.snapshot_generation,
                ),
            ).fetchone()
            if row is not None:
                if (
                    row["snapshot_id"] != snapshot.snapshot_id
                    or row["snapshot_hash"] != snapshot.snapshot_hash
                    or row["request_id"] != snapshot.request_id
                    or row["run_id"] != snapshot.run_id
                ):
                    raise CompanionConflictError(
                        f"run_growth_snapshot_conflict:{snapshot.request_id}"
                    )
                existing_items = db.execute(
                    """SELECT dependency_kind,dependency_id,pack_id,version,manifest_hash,
                              binding_generation,catalog_content_stamp,content_hash,effect_hash
                       FROM run_growth_dependency_items
                       WHERE profile_id=? AND profile_generation=? AND snapshot_id=?
                       ORDER BY dependency_kind,dependency_id""",
                    (owner.profile_id, owner.profile_generation, snapshot.snapshot_id),
                ).fetchall()
                if [dict(item) for item in existing_items] != [
                    {
                        "dependency_kind": item.dependency_kind,
                        "dependency_id": item.dependency_id,
                        "pack_id": item.pack_id,
                        "version": item.version,
                        "manifest_hash": item.manifest_hash,
                        "binding_generation": item.binding_generation,
                        "catalog_content_stamp": item.catalog_content_stamp,
                        "content_hash": item.content_hash,
                        "effect_hash": item.effect_hash,
                    }
                    for item in normalized
                ]:
                    raise CompanionConflictError("run_growth_snapshot_dependencies_conflict")
                for scope_ref, scope in memory_scopes.items():
                    persisted = db.execute(
                        """SELECT scope_hash,binding_epoch,session_set_version,
                                  session_set_hash,as_of_message_id,session_ids_json
                           FROM owner_memory_read_scopes
                           WHERE profile_id=? AND profile_generation=? AND scope_ref=?""",
                        (owner.profile_id, owner.profile_generation, scope_ref),
                    ).fetchone()
                    expected = (
                        str(scope["scope_hash"]),
                        int(scope["binding_epoch"]),
                        int(scope["session_set_version"]),
                        str(scope["session_set_hash"]),
                        int(scope["as_of_message_id"]),
                        canonical_json(list(scope["session_ids"])),
                    )
                    if persisted is None or tuple(persisted) != expected:
                        raise CompanionConflictError("run_growth_snapshot_memory_scope_conflict")
                return dict(row)
            if snapshot.snapshot_generation > 0:
                prior = db.execute(
                    """SELECT snapshot_hash,status FROM run_growth_snapshots
                       WHERE profile_id=? AND profile_generation=? AND snapshot_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        snapshot.prior_snapshot_id,
                    ),
                ).fetchone()
                if prior is None or prior["snapshot_hash"] != snapshot.prior_snapshot_hash:
                    raise CompanionStateError("run_growth_snapshot_prior_missing")
            db.execute(
                """INSERT INTO run_growth_snapshots(
                     profile_id,profile_generation,snapshot_id,request_id,run_id,
                     snapshot_generation,snapshot_hash,prior_snapshot_id,prior_snapshot_hash,
                     status,reason_code,schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,'prepared','snapshot_frozen',1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    snapshot.snapshot_id,
                    snapshot.request_id,
                    snapshot.run_id,
                    snapshot.snapshot_generation,
                    snapshot.snapshot_hash,
                    snapshot.prior_snapshot_id,
                    snapshot.prior_snapshot_hash,
                    now,
                    now,
                ),
            )
            for scope_ref, scope in sorted(memory_scopes.items()):
                db.execute(
                    """INSERT INTO owner_memory_read_scopes(
                         profile_id,profile_generation,scope_ref,scope_hash,binding_epoch,
                         session_set_version,session_set_hash,as_of_message_id,session_ids_json,
                         reason_code,schema_version,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,'run_scope_frozen',1,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        scope_ref,
                        str(scope["scope_hash"]),
                        int(scope["binding_epoch"]),
                        int(scope["session_set_version"]),
                        str(scope["session_set_hash"]),
                        int(scope["as_of_message_id"]),
                        canonical_json(list(scope["session_ids"])),
                        now,
                    ),
                )
            for item in normalized:
                db.execute(
                    """INSERT INTO run_growth_dependency_items(
                         profile_id,profile_generation,snapshot_id,dependency_kind,dependency_id,
                         pack_id,version,manifest_hash,binding_generation,catalog_content_stamp,
                         content_hash,effect_hash,reason_code,schema_version,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        snapshot.snapshot_id,
                        item.dependency_kind,
                        item.dependency_id,
                        item.pack_id,
                        item.version,
                        item.manifest_hash,
                        item.binding_generation,
                        item.catalog_content_stamp,
                        item.content_hash,
                        item.effect_hash,
                        "snapshot_dependency",
                        now,
                    ),
                )
                for event_id in sorted(set(item.evidence_event_ids)):
                    event = db.execute(
                        """SELECT content_state FROM growth_events
                           WHERE profile_id=? AND profile_generation=? AND event_id=?""",
                        (owner.profile_id, owner.profile_generation, event_id),
                    ).fetchone()
                    if event is None or event["content_state"] != "live":
                        raise CompanionStateError("snapshot_evidence_missing_or_tombstoned")
                    db.execute(
                        """INSERT INTO run_growth_dependency_evidence(
                             profile_id,profile_generation,snapshot_id,dependency_kind,dependency_id,
                             event_id,reason_code,schema_version,created_at
                           ) VALUES (?,?,?,?,?,?,?,1,?)""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            snapshot.snapshot_id,
                            item.dependency_kind,
                            item.dependency_id,
                            event_id,
                            "snapshot_evidence",
                            now,
                        ),
                    )
            return dict(
                db.execute(
                    """SELECT * FROM run_growth_snapshots
                       WHERE profile_id=? AND profile_generation=? AND snapshot_id=?""",
                    (owner.profile_id, owner.profile_generation, snapshot.snapshot_id),
                ).fetchone()
            )

    def resolve_owner_memory_read_scope_for_run(
        self,
        run_id: str,
        *,
        expected_ref: str | None = None,
        expected_hash: str | None = None,
    ) -> Mapping[str, Any]:
        """Reopen the unique generation-0 memory scope frozen for ``run_id``."""

        if not str(run_id or "").strip():
            raise ValueError("memory scope resolution requires run_id")
        with self.read() as db:
            rows = db.execute(
                """SELECT s.profile_id,s.profile_generation,d.dependency_id AS scope_ref,
                          d.content_hash AS dependency_hash,m.scope_hash,m.binding_epoch,
                          m.session_set_version,m.session_set_hash,m.as_of_message_id,
                          m.session_ids_json
                   FROM run_growth_snapshots s
                   JOIN run_growth_dependency_items d
                     ON d.profile_id=s.profile_id
                    AND d.profile_generation=s.profile_generation
                    AND d.snapshot_id=s.snapshot_id
                    AND d.dependency_kind='memory_scope'
                   JOIN owner_memory_read_scopes m
                     ON m.profile_id=d.profile_id
                    AND m.profile_generation=d.profile_generation
                    AND m.scope_ref=d.dependency_id
                   WHERE s.run_id=? AND s.snapshot_generation=0
                     AND s.status IN ('prepared','bound','terminal')""",
                (run_id,),
            ).fetchall()
        if len(rows) != 1:
            raise CompanionStateError("owner_memory_scope_missing_or_ambiguous")
        row = rows[0]
        if row["dependency_hash"] != row["scope_hash"]:
            raise CompanionStateError("owner_memory_scope_dependency_hash_mismatch")
        if expected_ref is not None and row["scope_ref"] != expected_ref:
            raise CompanionStateError("owner_memory_scope_ref_mismatch")
        if expected_hash is not None and row["scope_hash"] != expected_hash:
            raise CompanionStateError("owner_memory_scope_hash_mismatch")
        return {
            "schema_version": 1,
            "profile_id": str(row["profile_id"]),
            "profile_generation": int(row["profile_generation"]),
            "binding_epoch": int(row["binding_epoch"]),
            "session_ids": tuple(json.loads(str(row["session_ids_json"]))),
            "session_set_version": int(row["session_set_version"]),
            "session_set_hash": str(row["session_set_hash"]),
            "as_of_message_id": int(row["as_of_message_id"]),
            "scope_hash": str(row["scope_hash"]),
            "scope_ref": str(row["scope_ref"]),
        }

    def activate_run_binding(
        self,
        owner: OwnerRef,
        *,
        run_id: str,
        request_id: str,
        root_run_id: str,
        snapshot_id: str,
        snapshot_hash: str,
        snapshot_generation: int,
        all_generations_root_hash: str,
        start_fingerprint: str,
        job_id: str | None = None,
    ) -> Mapping[str, Any]:
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            snapshot = db.execute(
                """SELECT * FROM run_growth_snapshots
                   WHERE profile_id=? AND profile_generation=? AND snapshot_id=?""",
                (owner.profile_id, owner.profile_generation, snapshot_id),
            ).fetchone()
            if (
                snapshot is None
                or snapshot["status"] == "revoked"
                or snapshot["snapshot_hash"] != snapshot_hash
                or snapshot["run_id"] != run_id
                or snapshot["request_id"] != request_id
                or int(snapshot["snapshot_generation"]) != snapshot_generation
            ):
                raise CompanionStateError("run_growth_snapshot_not_bindable")
            existing = db.execute(
                """SELECT * FROM companion_run_bindings
                   WHERE profile_id=? AND profile_generation=? AND run_id=?""",
                (owner.profile_id, owner.profile_generation, run_id),
            ).fetchone()
            expected = {
                "request_id": request_id,
                "root_run_id": root_run_id,
                "job_id": job_id,
                "snapshot_generation": snapshot_generation,
                "snapshot_id": snapshot_id,
                "snapshot_hash": snapshot_hash,
                "all_generations_root_hash": all_generations_root_hash,
                "start_fingerprint": start_fingerprint,
            }
            if existing is not None:
                if not self._same(existing, expected):
                    raise CompanionConflictError(f"run_binding_conflict:{run_id}")
                return dict(existing)
            db.execute(
                """INSERT INTO companion_run_bindings(
                     profile_id,profile_generation,run_id,request_id,root_run_id,job_id,
                     snapshot_generation,snapshot_id,snapshot_hash,all_generations_root_hash,
                     start_fingerprint,status,reason_code,schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,'active','run_start_bound',1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    run_id,
                    request_id,
                    root_run_id,
                    job_id,
                    snapshot_generation,
                    snapshot_id,
                    snapshot_hash,
                    all_generations_root_hash,
                    start_fingerprint,
                    now,
                    now,
                ),
            )
            db.execute(
                """UPDATE run_growth_snapshots SET status='bound',updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND snapshot_id=?
                     AND status='prepared'""",
                (now, owner.profile_id, owner.profile_generation, snapshot_id),
            )
            return dict(
                db.execute(
                    """SELECT * FROM companion_run_bindings
                       WHERE profile_id=? AND profile_generation=? AND run_id=?""",
                    (owner.profile_id, owner.profile_generation, run_id),
                ).fetchone()
            )
