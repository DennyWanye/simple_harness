"""Profiles, profile generations, control challenges and control commands."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from ..contracts import CompanionConflictError, CompanionLeaseError, CompanionOwnerError, OwnerRef
from .core import CompanionStoreCore
from .support import canonical_hash

if TYPE_CHECKING:
    pass


class ProfileControlMixin(CompanionStoreCore):
    """Profiles, profile generations, control challenges and control commands."""

    __slots__ = ()

    # ------------------------------------------------------------------
    # Profile lifecycle

    def create_profile(
        self,
        *,
        profile_id: str,
        generation: int,
        identity_namespace_hash: str,
        reason_code: str = "profile_created",
    ) -> OwnerRef:
        owner = OwnerRef(profile_id, generation)
        now = self._now()
        expected = {
            "identity_namespace_hash": identity_namespace_hash,
            "status": "active",
            "reason_code": reason_code,
            "schema_version": 1,
        }
        with self._write() as db:
            row = db.execute(
                "SELECT * FROM profiles WHERE profile_id=? AND generation=?",
                (profile_id, generation),
            ).fetchone()
            if row is not None:
                if not self._same(row, expected):
                    raise CompanionConflictError(f"profile_conflict:{profile_id}:{generation}")
                return owner
            db.execute(
                """INSERT INTO profiles(
                     profile_id,generation,identity_namespace_hash,status,reason_code,
                     schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    profile_id,
                    generation,
                    identity_namespace_hash,
                    "active",
                    reason_code,
                    1,
                    now,
                    now,
                ),
            )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(["profile_created", profile_id, generation]),
                now=now,
            )
        return owner

    def adopt_legacy_local_identity(self, *, identity_namespace_hash: str) -> bool:
        """Replace Task-1's placeholder with this user-data domain UUID."""

        now = self._now()
        with self._write() as db:
            cursor = db.execute(
                """UPDATE profiles
                   SET identity_namespace_hash=?,updated_at=?,
                       reason_code='legacy_local_identity_adopted'
                   WHERE profile_id='legacy_local_profile' AND generation=1
                     AND status='active'
                     AND identity_namespace_hash='legacy-local-profile-v1'""",
                (identity_namespace_hash, now),
            )
            return cursor.rowcount == 1

    def delete_profile_generation(
        self,
        owner: OwnerRef | tuple[str, int],
        *,
        reason_code: str,
    ) -> bool:
        resolved = self._owner(owner)
        now = self._now()
        with self._write() as db:
            row = db.execute(
                "SELECT status FROM profiles WHERE profile_id=? AND generation=?",
                (resolved.profile_id, resolved.profile_generation),
            ).fetchone()
            if row is None:
                raise CompanionOwnerError("companion_owner_missing")
            if row["status"] == "deleted":
                return False
            self._require_owner(db, resolved)
            owner_deleted_audit_id = canonical_hash(
                [
                    "owner_deleted",
                    resolved.profile_id,
                    resolved.profile_generation,
                ]
            )
            owner_deleted_hash = canonical_hash(
                {
                    "profile_id": resolved.profile_id,
                    "profile_generation": resolved.profile_generation,
                    "reason_code": reason_code,
                }
            )
            db.execute(
                """INSERT INTO audit_events(
                     profile_id,profile_generation,audit_id,actor,action,
                     reason_code,before_hash,after_hash,lineage_ref,audit_hash,
                     schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    resolved.profile_id,
                    resolved.profile_generation,
                    owner_deleted_audit_id,
                    "companion_host",
                    "owner_deleted",
                    reason_code,
                    None,
                    owner_deleted_hash,
                    f"profile:{resolved.profile_id}:{resolved.profile_generation}",
                    owner_deleted_hash,
                    now,
                ),
            )
            db.execute(
                """UPDATE notifications
                   SET status='superseded',actions_json='[]',
                       reason_code='owner_deleted',updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND status<>'superseded'""",
                (
                    now,
                    resolved.profile_id,
                    resolved.profile_generation,
                ),
            )
            db.execute(
                """UPDATE outbox
                   SET status='dead_letter',lease_expires_at=NULL,
                       reason_code='owner_deleted',updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND sink_kind IN ('companion_notification','session_projection')
                     AND status IN ('pending','claimed')""",
                (
                    now,
                    resolved.profile_id,
                    resolved.profile_generation,
                ),
            )
            db.execute(
                """UPDATE profiles SET status='deleted',reason_code=?,deleted_at=?,updated_at=?
                   WHERE profile_id=? AND generation=? AND status='active'""",
                (
                    reason_code,
                    now,
                    now,
                    resolved.profile_id,
                    resolved.profile_generation,
                ),
            )
            db.execute(
                """UPDATE companion_run_bindings
                   SET status='revoked',reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND status IN ('prepared','active')""",
                (reason_code, now, resolved.profile_id, resolved.profile_generation),
            )
            outbox_id = canonical_hash(
                ["profile_generation_revoke", resolved.profile_id, resolved.profile_generation]
            )
            payload = {
                "schema_version": 1,
                "profile_id": resolved.profile_id,
                "profile_generation": resolved.profile_generation,
                "reason_code": reason_code,
            }
            self._insert_outbox(
                db,
                resolved,
                outbox_id=outbox_id,
                event_kind="profile_generation_revoke",
                event_id=f"{resolved.profile_id}:{resolved.profile_generation}",
                sink_kind="execution_fence",
                payload=payload,
                now=now,
                reason_code=reason_code,
            )
            self._bump_detail(
                db,
                resolved,
                mutation_hash=canonical_hash(["profile_deleted", reason_code]),
                now=now,
            )
        return True

    # ------------------------------------------------------------------
    # Trusted window control plane

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def open_profile_control_challenge(
        self,
        *,
        device_scope: str,
        requested_window_label: str,
        requested_scope: str,
        backend_process_instance_id: str,
        challenged_ttl_seconds: int = 45,
        challenged_quota: int = 16,
    ) -> Mapping[str, Any]:
        if challenged_ttl_seconds < 1 or challenged_quota < 1:
            raise ValueError("invalid_control_challenge_limits")
        now = self._now()
        expires_at = (
            (self._parse_timestamp(now) + timedelta(seconds=challenged_ttl_seconds))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        connection_id = str(uuid.uuid4())
        challenge = secrets.token_urlsafe(32)
        challenge_hash = hashlib.sha256(challenge.encode("utf-8")).hexdigest()
        with self._write() as db:
            db.execute(
                """DELETE FROM profile_control_leases
                   WHERE status='challenged' AND expires_at<=?""",
                (now,),
            )
            rows = db.execute(
                """SELECT connection_id FROM profile_control_leases
                   WHERE device_scope=? AND backend_process_instance_id=?
                     AND status='challenged'
                   ORDER BY issued_at,connection_id""",
                (device_scope, backend_process_instance_id),
            ).fetchall()
            overflow = len(rows) - challenged_quota + 1
            if overflow > 0:
                db.executemany(
                    """DELETE FROM profile_control_leases
                       WHERE device_scope=? AND connection_id=?
                         AND backend_process_instance_id=?
                         AND status='challenged'""",
                    (
                        (
                            device_scope,
                            str(row["connection_id"]),
                            backend_process_instance_id,
                        )
                        for row in rows[:overflow]
                    ),
                )
            next_epoch = int(
                db.execute(
                    """SELECT COALESCE(MAX(control_epoch),0)+1 AS value
                       FROM profile_control_leases WHERE device_scope=?""",
                    (device_scope,),
                ).fetchone()["value"]
            )
            db.execute(
                """INSERT INTO profile_control_leases(
                     device_scope,connection_id,requested_window_label,requested_scope,
                     window_label,scope,control_epoch,challenge_hash,last_seq,
                     backend_process_instance_id,issued_at,expires_at,revoked_at,
                     status,reason_code,schema_version
                   ) VALUES (?,?,?,?,NULL,NULL,?,?,0,?,?,?,NULL,'challenged',
                             'control_challenged',1)""",
                (
                    device_scope,
                    connection_id,
                    requested_window_label,
                    requested_scope,
                    next_epoch,
                    challenge_hash,
                    backend_process_instance_id,
                    now,
                    expires_at,
                ),
            )
            binding = db.execute(
                "SELECT * FROM profile_bindings WHERE device_scope=?",
                (device_scope,),
            ).fetchone()
            binding_epoch = int(binding["binding_epoch"]) if binding else 1
        return {
            "connection_id": connection_id,
            "control_epoch": next_epoch,
            "challenge": challenge,
            "challenge_hash": challenge_hash,
            "request_seq": 1,
            "binding_epoch": binding_epoch,
            "expires_at": expires_at,
        }

    def cleanup_expired_control_challenges(self) -> int:
        now = self._now()
        with self._write() as db:
            cursor = db.execute(
                """DELETE FROM profile_control_leases
                   WHERE status='challenged' AND expires_at<=?""",
                (now,),
            )
            return max(0, int(cursor.rowcount))

    def get_profile_binding(self, *, device_scope: str) -> Mapping[str, Any] | None:
        with self.read() as db:
            row = db.execute(
                "SELECT * FROM profile_bindings WHERE device_scope=?",
                (device_scope,),
            ).fetchone()
            return dict(row) if row is not None else None

    def resolve_active_profile(self, *, identity_namespace_hash: str) -> OwnerRef | None:
        """Resolve an identity without reviving a tombstoned generation."""

        with self.read() as db:
            row = db.execute(
                """SELECT profile_id,generation FROM profiles
                   WHERE identity_namespace_hash=? AND status='active'""",
                (identity_namespace_hash,),
            ).fetchone()
            if row is None:
                return None
            return OwnerRef(str(row["profile_id"]), int(row["generation"]))

    def next_profile_generation(self, *, profile_id: str) -> int:
        with self.read() as db:
            row = db.execute(
                """SELECT COALESCE(MAX(generation),0)+1 AS value
                   FROM profiles WHERE profile_id=?""",
                (profile_id,),
            ).fetchone()
            return int(row["value"])

    @staticmethod
    def _claim_control_command_db(
        db: sqlite3.Connection,
        *,
        device_scope: str,
        connection_id: str,
        backend_process_instance_id: str,
        control_epoch: int,
        challenge_hash: str,
        window_label: str,
        scope: str,
        request_seq: int,
        command_kind: str,
        request_hash: str,
        binding_epoch: int,
        credential_nonce_hash: str,
        now: str,
    ) -> Mapping[str, Any]:
        lease = db.execute(
            """SELECT * FROM profile_control_leases
               WHERE device_scope=? AND connection_id=?""",
            (device_scope, connection_id),
        ).fetchone()
        if lease is None:
            raise CompanionLeaseError("control_lease_missing")
        expected = {
            "backend_process_instance_id": backend_process_instance_id,
            "control_epoch": control_epoch,
            "challenge_hash": challenge_hash,
        }
        if not CompanionStoreCore._same(lease, expected):
            raise CompanionLeaseError("control_lease_facts_mismatch")
        if (lease["status"] == "challenged" and lease["expires_at"] <= now) or lease["status"] in {
            "revoked",
            "expired",
        }:
            raise CompanionLeaseError("control_lease_expired")
        if lease["status"] == "active" and (
            lease["window_label"] != window_label or lease["scope"] != scope
        ):
            raise CompanionLeaseError("control_lease_scope_mismatch")

        existing = db.execute(
            """SELECT * FROM profile_control_commands
               WHERE device_scope=? AND connection_id=? AND request_seq=?""",
            (device_scope, connection_id, request_seq),
        ).fetchone()
        immutable = {
            "backend_process_instance_id": backend_process_instance_id,
            "credential_nonce_hash": credential_nonce_hash,
            "command_kind": command_kind,
            "canonical_schema": "control-command-canonical-v1",
            "request_hash": request_hash,
            "binding_epoch": binding_epoch,
        }
        if existing is not None:
            if not CompanionStoreCore._same(existing, immutable):
                raise CompanionConflictError("control_command_replay_drift")
            return dict(existing)
        if request_seq != int(lease["last_seq"]) + 1:
            raise CompanionLeaseError("control_request_seq_not_next")

        if lease["status"] == "challenged":
            db.execute(
                """UPDATE profile_control_leases
                   SET status='revoked',revoked_at=?,reason_code='trusted_reconnect'
                   WHERE device_scope=? AND window_label=? AND scope=?
                     AND status='active' AND connection_id<>?""",
                (now, device_scope, window_label, scope, connection_id),
            )
            updated = db.execute(
                """UPDATE profile_control_leases
                   SET window_label=?,scope=?,status='active',
                       reason_code='credential_promoted'
                   WHERE device_scope=? AND connection_id=?
                     AND status='challenged'""",
                (
                    window_label,
                    scope,
                    device_scope,
                    connection_id,
                ),
            )
            if updated.rowcount != 1:
                raise CompanionLeaseError("control_lease_promote_conflict")

        db.execute(
            """INSERT INTO profile_control_commands(
                 device_scope,connection_id,request_seq,
                 backend_process_instance_id,credential_nonce_hash,command_kind,
                 canonical_schema,request_hash,binding_epoch,status,result_ref,
                 result_hash,reason_code,schema_version,created_at,updated_at
               ) VALUES (?,?,?,?,?,?, 'control-command-canonical-v1',?,?,
                         'claimed',NULL,NULL,'control_command_claimed',1,?,?)""",
            (
                device_scope,
                connection_id,
                request_seq,
                backend_process_instance_id,
                credential_nonce_hash,
                command_kind,
                request_hash,
                binding_epoch,
                now,
                now,
            ),
        )
        db.execute(
            """UPDATE profile_control_leases SET last_seq=?
               WHERE device_scope=? AND connection_id=?""",
            (request_seq, device_scope, connection_id),
        )
        row = db.execute(
            """SELECT * FROM profile_control_commands
               WHERE device_scope=? AND connection_id=? AND request_seq=?""",
            (device_scope, connection_id, request_seq),
        ).fetchone()
        assert row is not None
        return dict(row)

    def bind_profile_with_control_command(
        self,
        *,
        device_scope: str,
        profile_id: str,
        profile_generation: int,
        identity_namespace_hash: str,
        expected_binding_epoch: int,
        control_facts: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        owner = OwnerRef(profile_id, profile_generation)
        now = self._now()
        with self._write() as db:
            profile = db.execute(
                """SELECT * FROM profiles WHERE profile_id=? AND generation=?""",
                (owner.profile_id, owner.profile_generation),
            ).fetchone()
            if profile is None:
                db.execute(
                    """INSERT INTO profiles(
                         profile_id,generation,identity_namespace_hash,status,
                         reason_code,schema_version,created_at,updated_at
                       ) VALUES (?,?,?,'active','trusted_identity_bind',1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        identity_namespace_hash,
                        now,
                        now,
                    ),
                )
                self._bump_detail(
                    db,
                    owner,
                    mutation_hash=canonical_hash(
                        ["profile_created", owner.profile_id, owner.profile_generation]
                    ),
                    now=now,
                )
            elif (
                profile["status"] != "active"
                or profile["identity_namespace_hash"] != identity_namespace_hash
            ):
                raise CompanionConflictError("profile_identity_conflict")

            command = self._claim_control_command_db(
                db, device_scope=device_scope, now=now, **control_facts
            )
            current = db.execute(
                "SELECT * FROM profile_bindings WHERE device_scope=?",
                (device_scope,),
            ).fetchone()
            if current is None:
                if expected_binding_epoch != 1:
                    raise CompanionConflictError("binding_epoch_mismatch")
                next_epoch = 1
                changed_owner = True
                db.execute(
                    """INSERT INTO profile_bindings(
                         device_scope,profile_id,profile_generation,binding_epoch,
                         status,reason_code,schema_version,updated_at
                       ) VALUES (?,?,?,?, 'unready','trusted_identity_bind',1,?)""",
                    (
                        device_scope,
                        owner.profile_id,
                        owner.profile_generation,
                        next_epoch,
                        now,
                    ),
                )
            else:
                old_owner = (
                    str(current["profile_id"]),
                    int(current["profile_generation"]),
                )
                changed_owner = old_owner != (
                    owner.profile_id,
                    owner.profile_generation,
                )
                current_epoch = int(current["binding_epoch"])
                if expected_binding_epoch != current_epoch:
                    raise CompanionConflictError("binding_epoch_mismatch")
                next_epoch = current_epoch + 1 if changed_owner else current_epoch
                db.execute(
                    """UPDATE profile_bindings
                       SET profile_id=?,profile_generation=?,binding_epoch=?,
                           status=?,reason_code='trusted_identity_bind',updated_at=?
                       WHERE device_scope=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        next_epoch,
                        "unready" if changed_owner else current["status"],
                        now,
                        device_scope,
                    ),
                )
            return {
                "owner": owner,
                "binding_epoch": next_epoch,
                "changed_owner": changed_owner,
                "command": command,
            }

    def mark_profile_binding_ready(
        self,
        *,
        device_scope: str,
        owner: OwnerRef,
        expected_binding_epoch: int,
    ) -> Mapping[str, Any]:
        now = self._now()
        with self._write() as db:
            updated = db.execute(
                """UPDATE profile_bindings
                   SET status='ready',reason_code='owner_projection_ready',updated_at=?
                   WHERE device_scope=? AND profile_id=? AND profile_generation=?
                     AND binding_epoch=? AND status='unready'""",
                (
                    now,
                    device_scope,
                    owner.profile_id,
                    owner.profile_generation,
                    expected_binding_epoch,
                ),
            )
            if updated.rowcount != 1:
                row = db.execute(
                    "SELECT * FROM profile_bindings WHERE device_scope=?",
                    (device_scope,),
                ).fetchone()
                if (
                    row is None
                    or row["status"] != "ready"
                    or row["profile_id"] != owner.profile_id
                    or int(row["profile_generation"]) != owner.profile_generation
                    or int(row["binding_epoch"]) != expected_binding_epoch
                ):
                    raise CompanionConflictError("profile_binding_ready_conflict")
            row = db.execute(
                "SELECT * FROM profile_bindings WHERE device_scope=?",
                (device_scope,),
            ).fetchone()
            assert row is not None
            return dict(row)

    def unbind_profile_with_control_command(
        self,
        *,
        device_scope: str,
        expected_binding_epoch: int,
        control_facts: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        now = self._now()
        with self._write() as db:
            binding = db.execute(
                "SELECT * FROM profile_bindings WHERE device_scope=?",
                (device_scope,),
            ).fetchone()
            if binding is None or int(binding["binding_epoch"]) != expected_binding_epoch:
                raise CompanionConflictError("binding_epoch_mismatch")
            command = self._claim_control_command_db(
                db, device_scope=device_scope, now=now, **control_facts
            )
            next_epoch = expected_binding_epoch + 1
            db.execute(
                """UPDATE profile_bindings
                   SET binding_epoch=?,status='unready',
                       reason_code='trusted_identity_unbind',updated_at=?
                   WHERE device_scope=? AND binding_epoch=?""",
                (next_epoch, now, device_scope, expected_binding_epoch),
            )
            return {
                "owner": OwnerRef(
                    str(binding["profile_id"]),
                    int(binding["profile_generation"]),
                ),
                "binding_epoch": next_epoch,
                "command": command,
            }
