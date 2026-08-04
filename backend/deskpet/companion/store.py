"""The single transactional writer for ``companion.db``.

All public mutations start with ``BEGIN IMMEDIATE``.  Idempotent replays verify
the complete immutable input before returning an existing row; they never bump
the owner detail version.
"""

from __future__ import annotations

import hashlib
import io
import json
import secrets
import sqlite3
import uuid
import zipfile
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from .contracts import (
    CandidateAttempt,
    CandidateMode,
    CandidatePackage,
    CandidatePackageBlob,
    CandidatePackageFile,
    CapabilityGuardIncident,
    CapabilityMutationReceipt,
    CompanionConflictError,
    CompanionLeaseError,
    CompanionOwnerError,
    CompanionStateError,
    GrowthDependency,
    GrowthEvent,
    EvaluationCaseLaunch,
    EvaluationExecutionPermit,
    LeaseClaim,
    MutationRequest,
    OwnerRef,
    RunGrowthSnapshot,
)
from .schema import configure_connection, initialize_schema


def canonical_json(value: Any) -> str:
    """Return the only JSON encoding accepted by the Companion store."""

    if is_dataclass(value):
        value = asdict(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _clone_companion_json_object(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CompanionStateError(code)
    cloned = json.loads(canonical_json(dict(value)))
    if not isinstance(cloned, dict):
        raise CompanionStateError(code)
    return cloned


SAFE_STATIC_RUNNER_POLICY_HASH = canonical_hash(
    {
        "schema": "background-pairwise-runner-v1",
        "zero_tools": True,
        "blind_labels": True,
    }
)


def _bytes_hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class CompanionStore:
    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime | str] | None = None,
        initialize: bool = True,
    ) -> None:
        self.path = str(path)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._active_write: ContextVar[sqlite3.Connection | None] = ContextVar(
            f"companion_store_write_{id(self)}", default=None
        )
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        if initialize:
            db = self._connect()
            try:
                initialize_schema(db)
            finally:
                # sqlite3.Connection's context manager commits/rolls back but
                # does not close.  Leaving the schema connection alive keeps
                # companion.db locked on Windows across profile deletion and
                # application shutdown.
                db.close()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, isolation_level=None, timeout=5.0)
        configure_connection(db)
        return db

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        active = self._active_write.get()
        if active is not None:
            yield active
            return
        db = self._connect()
        token = self._active_write.set(db)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            self._active_write.reset(token)
            db.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def _now(self) -> str:
        value = self._clock()
        if isinstance(value, str):
            return value
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _owner(owner: OwnerRef | tuple[str, int] | Mapping[str, Any]) -> OwnerRef:
        if isinstance(owner, OwnerRef):
            return owner
        if isinstance(owner, tuple):
            return OwnerRef(str(owner[0]), int(owner[1]))
        return OwnerRef(
            str(owner["profile_id"]),
            int(owner.get("profile_generation", owner.get("generation"))),
        )

    @staticmethod
    def _require_owner(db: sqlite3.Connection, owner: OwnerRef) -> None:
        row = db.execute(
            "SELECT status FROM profiles WHERE profile_id=? AND generation=?",
            (owner.profile_id, owner.profile_generation),
        ).fetchone()
        if row is None or row["status"] != "active":
            raise CompanionOwnerError(
                f"companion_owner_stale:{owner.profile_id}:{owner.profile_generation}"
            )

    @staticmethod
    def _same(row: sqlite3.Row, expected: Mapping[str, Any]) -> bool:
        return all(row[key] == value for key, value in expected.items())

    def _bump_detail(
        self,
        db: sqlite3.Connection,
        owner: OwnerRef,
        *,
        mutation_hash: str,
        now: str,
    ) -> int:
        row = db.execute(
            """SELECT detail_version,detail_hash FROM companion_detail_versions
               WHERE profile_id=? AND profile_generation=?""",
            (owner.profile_id, owner.profile_generation),
        ).fetchone()
        if row is None:
            version = 1
            detail_hash = canonical_hash(
                {"version": version, "prior": None, "mutation_hash": mutation_hash}
            )
            db.execute(
                """INSERT INTO companion_detail_versions(
                     profile_id,profile_generation,detail_version,detail_hash,updated_at
                   ) VALUES (?,?,?,?,?)""",
                (owner.profile_id, owner.profile_generation, version, detail_hash, now),
            )
            return version
        version = int(row["detail_version"]) + 1
        detail_hash = canonical_hash(
            {
                "version": version,
                "prior": str(row["detail_hash"]),
                "mutation_hash": mutation_hash,
            }
        )
        db.execute(
            """UPDATE companion_detail_versions
               SET detail_version=?,detail_hash=?,updated_at=?
               WHERE profile_id=? AND profile_generation=? AND detail_version=?""",
            (
                version,
                detail_hash,
                now,
                owner.profile_id,
                owner.profile_generation,
                version - 1,
            ),
        )
        return version

    def get_detail_version(self, owner: OwnerRef | tuple[str, int]) -> Mapping[str, Any]:
        resolved = self._owner(owner)
        with self.read() as db:
            self._require_owner(db, resolved)
            row = db.execute(
                """SELECT detail_version,detail_hash,updated_at
                   FROM companion_detail_versions
                   WHERE profile_id=? AND profile_generation=?""",
                (resolved.profile_id, resolved.profile_generation),
            ).fetchone()
            if row is None:
                return {"detail_version": 0, "detail_hash": canonical_hash([]), "updated_at": None}
            return dict(row)

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

    def adopt_legacy_local_identity(
        self, *, identity_namespace_hash: str
    ) -> bool:
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
            self._parse_timestamp(now) + timedelta(seconds=challenged_ttl_seconds)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
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

    def get_profile_binding(
        self, *, device_scope: str
    ) -> Mapping[str, Any] | None:
        with self.read() as db:
            row = db.execute(
                "SELECT * FROM profile_bindings WHERE device_scope=?",
                (device_scope,),
            ).fetchone()
            return dict(row) if row is not None else None

    def resolve_active_profile(
        self, *, identity_namespace_hash: str
    ) -> OwnerRef | None:
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
        if not CompanionStore._same(lease, expected):
            raise CompanionLeaseError("control_lease_facts_mismatch")
        if (
            lease["status"] == "challenged" and lease["expires_at"] <= now
        ) or lease["status"] in {"revoked", "expired"}:
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
            if not CompanionStore._same(existing, immutable):
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

    def claim_companion_action_control_command(
        self,
        *,
        device_scope: str,
        expected_owner: OwnerRef,
        expected_binding_epoch: int,
        control_facts: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        now = self._now()
        with self._write() as db:
            binding = db.execute(
                "SELECT * FROM profile_bindings WHERE device_scope=?",
                (device_scope,),
            ).fetchone()
            if (
                binding is None
                or binding["status"] != "ready"
                or binding["profile_id"] != expected_owner.profile_id
                or int(binding["profile_generation"])
                != expected_owner.profile_generation
                or int(binding["binding_epoch"]) != expected_binding_epoch
            ):
                raise CompanionLeaseError("companion_identity_not_ready")
            return self._claim_control_command_db(
                db, device_scope=device_scope, now=now, **control_facts
            )

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

    # ------------------------------------------------------------------
    # Evidence and packages

    def _record_growth_event_db(
        self,
        db: sqlite3.Connection,
        event: GrowthEvent,
        *,
        now: str,
    ) -> Mapping[str, Any]:
        """Insert/replay one event inside the caller-owned Companion txn."""

        payload_json = canonical_json(dict(event.payload))
        event_hash = event.event_hash or canonical_hash(
            {
                "schema_version": event.schema_version,
                "event_id": event.event_id,
                "source_kind": event.source_kind,
                "source_ref": event.source_ref,
                "context_key": event.context_key,
                "root_run_id": event.root_run_id,
                "retry_of": event.retry_of,
                "payload": dict(event.payload),
            }
        )
        expected = {
            "event_hash": event_hash,
            "source_kind": event.source_kind,
            "source_ref": event.source_ref,
            "context_key": event.context_key,
            "root_run_id": event.root_run_id,
            "retry_of": event.retry_of,
            "payload_json": payload_json,
            "content_state": "live",
            "reason_code": event.reason_code,
            "schema_version": event.schema_version,
        }
        self._require_owner(db, event.owner)
        row = db.execute(
            """SELECT * FROM growth_events
               WHERE profile_id=? AND profile_generation=? AND event_id=?""",
            (event.owner.profile_id, event.owner.profile_generation, event.event_id),
        ).fetchone()
        if row is not None:
            if not self._same(row, expected):
                raise CompanionConflictError(f"growth_event_conflict:{event.event_id}")
            return dict(row)
        try:
            db.execute(
                """INSERT INTO growth_events(
                     profile_id,profile_generation,event_id,event_hash,source_kind,source_ref,
                     context_key,root_run_id,retry_of,payload_json,content_state,reason_code,
                     schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event.owner.profile_id,
                    event.owner.profile_generation,
                    event.event_id,
                    event_hash,
                    event.source_kind,
                    event.source_ref,
                    event.context_key,
                    event.root_run_id,
                    event.retry_of,
                    payload_json,
                    "live",
                    event.reason_code,
                    event.schema_version,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise CompanionConflictError(
                f"growth_event_source_conflict:{event.source_kind}:{event.source_ref}"
            ) from exc
        self._bump_detail(db, event.owner, mutation_hash=event_hash, now=now)
        row = db.execute(
            """SELECT * FROM growth_events
               WHERE profile_id=? AND profile_generation=? AND event_id=?""",
            (event.owner.profile_id, event.owner.profile_generation, event.event_id),
        ).fetchone()
        assert row is not None
        return dict(row)

    def record_growth_event(self, event: GrowthEvent) -> Mapping[str, Any]:
        now = self._now()
        with self._write() as db:
            return self._record_growth_event_db(db, event, now=now)

    def load_live_growth_evidence(
        self,
        owner: OwnerRef,
        *,
        event_ids: Sequence[str],
    ) -> tuple[Any, ...]:
        """Return an exact owner-fenced typed evidence set.

        Background reflection code must not depend on SQLite rows or infer
        authority from model text.  This facade rehydrates only live events
        owned by the requested profile generation and rejects partial sets.
        """

        from .growth import GrowthEvidenceV1

        requested = tuple(sorted({str(item).strip() for item in event_ids}))
        if not requested or any(not item for item in requested):
            raise ValueError("growth_evidence_ids_required")
        placeholders = ",".join("?" for _ in requested)
        with self.read() as db:
            self._require_owner(db, owner)
            rows = db.execute(
                f"""SELECT * FROM growth_events
                    WHERE profile_id=? AND profile_generation=?
                      AND content_state='live'
                      AND event_id IN ({placeholders})
                    ORDER BY event_id""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    *requested,
                ),
            ).fetchall()
        if tuple(str(row["event_id"]) for row in rows) != requested:
            raise CompanionStateError(
                "growth_evidence_missing_or_tombstoned"
            )
        evidence: list[GrowthEvidenceV1] = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict):
                raise CompanionStateError("growth_evidence_payload_invalid")
            event = GrowthEvent(
                owner=owner,
                event_id=str(row["event_id"]),
                source_kind=str(row["source_kind"]),
                source_ref=str(row["source_ref"]),
                context_key=str(row["context_key"]),
                root_run_id=str(row["root_run_id"]),
                reason_code=str(row["reason_code"]),
                payload=payload,
                retry_of=(
                    None
                    if row["retry_of"] is None
                    else str(row["retry_of"])
                ),
                event_hash=str(row["event_hash"]),
                schema_version=int(row["schema_version"]),
            )
            evidence.append(
                GrowthEvidenceV1.from_growth_event(
                    event,
                    occurred_at=datetime.fromisoformat(
                        str(row["created_at"]).replace("Z", "+00:00")
                    ),
                )
            )
        return tuple(evidence)

    def apply_preference_event(
        self,
        event: GrowthEvent,
        *,
        preference_key: str,
        value: Any,
        signal_kind: str,
        weight: float,
        policy: Any,
    ) -> Mapping[str, Any]:
        """Atomically apply event, evidence, state CAS, audit and detail.

        ``preferences.py`` owns deterministic preference semantics; this Store
        method owns the only write transaction and exposes no connection or
        private mutation primitive to the resolver.
        """

        from .preferences import apply_preference_event_db

        now = self._now()
        with self._write() as db:
            self._record_growth_event_db(db, event, now=now)
            return apply_preference_event_db(
                db,
                store=self,
                event=event,
                preference_key=preference_key,
                value=value,
                signal_kind=signal_kind,
                weight=weight,
                policy=policy,
                now=now,
            )

    def get_preference_turn_decision_receipt(
        self,
        owner: OwnerRef,
        *,
        source_message_ref: str,
        source_message_hash: str,
    ) -> Mapping[str, Any] | None:
        """Load the settled semantic decision for one exact source message.

        A receipt is reusable across process restarts even when the surrounding
        preference projection has advanced.  The source-message hash is the
        immutable replay fence; assessment context is retained for audit but
        is deliberately not re-evaluated during replay.
        """

        if not source_message_ref.strip() or not source_message_hash.strip():
            raise ValueError("preference_turn_source_identity_required")
        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM preference_turn_decision_receipts
                   WHERE profile_id=? AND profile_generation=?
                     AND source_message_ref=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    source_message_ref,
                ),
            ).fetchone()
            if row is None:
                return None
            if str(row["source_message_hash"]) != source_message_hash:
                raise CompanionConflictError(
                    "preference_turn_source_message_conflict"
                )
            payload = json.loads(str(row["decision_json"]))
            if (
                not isinstance(payload, dict)
                or canonical_hash(payload) != str(row["decision_hash"])
            ):
                raise CompanionStateError(
                    "preference_turn_decision_receipt_corrupt"
                )
            return dict(row)

    def commit_preference_turn_decision_receipt(
        self,
        owner: OwnerRef,
        *,
        source_message_ref: str,
        source_message_hash: str,
        assessment_input_hash: str,
        decision: Mapping[str, Any],
        interpreter_id: str,
        interpreter_version: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Settle one model decision; concurrent first-writer wins.

        Model inference is non-deterministic.  If two preparations race for
        the same source message, the first durable receipt becomes the fact
        replayed by both callers instead of treating the second model sample
        as an immutable-input conflict.
        """

        required = (
            source_message_ref,
            source_message_hash,
            assessment_input_hash,
            interpreter_id,
            interpreter_version,
            reason_code,
        )
        if any(not str(value).strip() for value in required):
            raise ValueError("preference_turn_receipt_fields_required")
        decision_payload = dict(decision)
        decision_json = canonical_json(decision_payload)
        decision_hash = canonical_hash(decision_payload)
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM preference_turn_decision_receipts
                   WHERE profile_id=? AND profile_generation=?
                     AND source_message_ref=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    source_message_ref,
                ),
            ).fetchone()
            if row is not None:
                if str(row["source_message_hash"]) != source_message_hash:
                    raise CompanionConflictError(
                        "preference_turn_source_message_conflict"
                    )
                persisted = json.loads(str(row["decision_json"]))
                if (
                    not isinstance(persisted, dict)
                    or canonical_hash(persisted) != str(row["decision_hash"])
                ):
                    raise CompanionStateError(
                        "preference_turn_decision_receipt_corrupt"
                    )
                return dict(row)
            db.execute(
                """INSERT INTO preference_turn_decision_receipts(
                     profile_id,profile_generation,source_message_ref,
                     source_message_hash,assessment_input_hash,decision_json,
                     decision_hash,interpreter_id,interpreter_version,
                     reason_code,schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    source_message_ref,
                    source_message_hash,
                    assessment_input_hash,
                    decision_json,
                    decision_hash,
                    interpreter_id,
                    interpreter_version,
                    reason_code,
                    now,
                ),
            )
            row = db.execute(
                """SELECT * FROM preference_turn_decision_receipts
                   WHERE profile_id=? AND profile_generation=?
                     AND source_message_ref=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    source_message_ref,
                ),
            ).fetchone()
            assert row is not None
            return dict(row)

    @staticmethod
    def _validate_relative_path(path: str) -> None:
        normalized = path.replace("\\", "/")
        if (
            not path
            or normalized.startswith("/")
            or ":" in normalized.split("/")[0]
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
        ):
            raise ValueError(f"candidate_relative_path_invalid:{path}")

    def create_growth_target(
        self,
        owner: OwnerRef,
        *,
        target_id: str,
        kind: str,
        stable_name: str,
        pack_id: str,
        reason_code: str = "target_created",
    ) -> Mapping[str, Any]:
        now = self._now()
        expected = {
            "kind": kind,
            "stable_name": stable_name,
            "pack_id": pack_id,
            "governance_domain": "companion_growth",
        }
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM growth_targets
                   WHERE profile_id=? AND profile_generation=? AND target_id=?""",
                (owner.profile_id, owner.profile_generation, target_id),
            ).fetchone()
            if row is not None:
                if not self._same(row, expected):
                    raise CompanionConflictError(f"growth_target_conflict:{target_id}")
                return dict(row)
            db.execute(
                """INSERT INTO growth_targets(
                     profile_id,profile_generation,target_id,kind,stable_name,pack_id,
                     governance_domain,reason_code,schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    target_id,
                    kind,
                    stable_name,
                    pack_id,
                    "companion_growth",
                    reason_code,
                    now,
                    now,
                ),
            )
            self._bump_detail(
                db, owner, mutation_hash=canonical_hash(["growth_target", target_id]), now=now
            )
            return dict(
                db.execute(
                    """SELECT * FROM growth_targets
                       WHERE profile_id=? AND profile_generation=? AND target_id=?""",
                    (owner.profile_id, owner.profile_generation, target_id),
                ).fetchone()
            )

    def admit_explicit_build(self, admission: Any) -> Mapping[str, Any]:
        """Atomically persist trusted explicit evidence, proposal and build."""

        from .build_admission import ExplicitGrowthBuildAdmissionV1

        if not isinstance(admission, ExplicitGrowthBuildAdmissionV1):
            raise TypeError("explicit_growth_build_admission_required")
        owner = admission.owner
        expected_owner_key = (
            f"companion:{owner.profile_id}:{owner.profile_generation}"
        )
        if admission.owner_key != expected_owner_key:
            raise CompanionOwnerError("explicit_build_owner_key_mismatch")
        proposal = admission.proposal
        assert proposal.target is not None and proposal.target_fence is not None
        now = self._now()
        proposal_json = canonical_json(proposal.to_dict())
        source_fence_json = (
            None
            if proposal.source_fence is None
            else canonical_json(proposal.source_fence.to_dict())
        )
        target_fence_json = canonical_json(proposal.target_fence.to_dict())
        source_fence_hash = canonical_hash(
            None
            if proposal.source_fence is None
            else proposal.source_fence.to_dict()
        )
        target_fence_hash = canonical_hash(proposal.target_fence.to_dict())
        evidence_ids = tuple(sorted(set(proposal.evidence_event_ids)))
        if not evidence_ids:
            raise CompanionStateError("explicit_build_evidence_required")
        builder_launch_id = "builder-launch:" + canonical_hash(
            ["candidate-builder-launch-v1", admission.build_id]
        )
        child_run_id = "candidate-child:" + canonical_hash(
            ["candidate-builder-child-v1", admission.build_id]
        )
        expected_child_start_hash = canonical_hash(
            {
                "schema": "candidate-builder-start-v1",
                "build_id": admission.build_id,
                "proposal_ref": admission.proposal_ref,
                "proposal_hash": admission.proposal_hash,
                "evidence_set_hash": admission.evidence_set_hash,
                "builder_launch_id": builder_launch_id,
                "child_run_id": child_run_id,
            }
        )
        with self._write() as db:
            self._require_owner(db, owner)
            for event_id in evidence_ids:
                self._record_growth_event_db(
                    db,
                    GrowthEvent(
                        owner=owner,
                        event_id=event_id,
                        source_kind="explicit_user_build",
                        source_ref=f"{admission.source_ref}:{event_id}",
                        context_key=admission.user_message_ref,
                        root_run_id=admission.root_run_id,
                        reason_code="explicit_user_build_evidence",
                        payload={
                            "request_id": admission.request_id,
                            "user_message_ref": admission.user_message_ref,
                            "user_message_hash": admission.user_message_hash,
                            "source_hash": admission.source_hash,
                            "proposal_ref": admission.proposal_ref,
                        },
                    ),
                    now=now,
                )
            target = db.execute(
                """SELECT * FROM growth_targets
                   WHERE profile_id=? AND profile_generation=? AND target_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    proposal.target.target_id,
                ),
            ).fetchone()
            target_expected = {
                "kind": proposal.target.kind.value,
                "stable_name": proposal.target.stable_name,
                "pack_id": proposal.target.pack_id,
                "governance_domain": "companion_growth",
            }
            if target is None:
                db.execute(
                    """INSERT INTO growth_targets(
                    profile_id,profile_generation,target_id,kind,stable_name,
                    pack_id,governance_domain,reason_code,schema_version,
                    created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,'explicit_user_build',1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        proposal.target.target_id,
                        proposal.target.kind.value,
                        proposal.target.stable_name,
                        proposal.target.pack_id,
                        "companion_growth",
                        now,
                        now,
                    ),
                )
            elif not self._same(target, target_expected):
                raise CompanionConflictError(
                    f"growth_target_conflict:{proposal.target.target_id}"
                )
            if proposal.candidate_mode.value == "genesis":
                reservation = db.execute(
                    """SELECT * FROM growth_target_reservations
                       WHERE profile_id=? AND profile_generation=?
                         AND kind=? AND stable_name=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        proposal.target.kind.value,
                        proposal.target.stable_name,
                    ),
                ).fetchone()
                if reservation is None:
                    db.execute(
                        """INSERT INTO growth_target_reservations(
                        profile_id,profile_generation,kind,stable_name,target_id,
                        pack_id,candidate_id,reservation_version,status,
                        reason_code,schema_version,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,NULL,1,'held',
                                 'explicit_user_build_reserved',1,?,?)""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            proposal.target.kind.value,
                            proposal.target.stable_name,
                            proposal.target.target_id,
                            proposal.target.pack_id,
                            now,
                            now,
                        ),
                    )
                else:
                    replay_build = db.execute(
                        """SELECT 1 FROM candidate_builds
                           WHERE profile_id=? AND profile_generation=?
                             AND (build_id=? OR
                               (source_kind='explicit_user_build' AND source_ref=?))
                           LIMIT 1""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            admission.build_id,
                            admission.source_ref,
                        ),
                    ).fetchone()
                    if (
                        str(reservation["target_id"])
                        != proposal.target.target_id
                        or str(reservation["pack_id"])
                        != proposal.target.pack_id
                        or (
                            replay_build is None
                            and (
                                str(reservation["status"]) != "held"
                                or reservation["candidate_id"] is not None
                            )
                        )
                    ):
                        raise CompanionConflictError(
                            f"growth_target_reserved:{proposal.target.target_id}"
                        )
            existing = db.execute(
                """SELECT * FROM candidate_builds
                   WHERE profile_id=? AND profile_generation=?
                     AND (build_id=? OR (source_kind='explicit_user_build'
                       AND source_ref=?))""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    admission.build_id,
                    admission.source_ref,
                ),
            ).fetchone()
            immutable = {
                "build_id": admission.build_id,
                "source_kind": "explicit_user_build",
                "source_ref": admission.source_ref,
                "reflection_job_id": None,
                "proposal_ref": admission.proposal_ref,
                "proposal_json": proposal_json,
                "proposal_hash": admission.proposal_hash,
                "evidence_set_json": canonical_json(evidence_ids),
                "evidence_set_hash": admission.evidence_set_hash,
                "candidate_mode": proposal.candidate_mode.value,
                "source_fence_json": source_fence_json,
                "source_fence_hash": source_fence_hash,
                "target_fence_json": target_fence_json,
                "target_fence_hash": target_fence_hash,
                "builder_launch_id": builder_launch_id,
                "builder_child_run_id": child_run_id,
                "expected_child_start_hash": expected_child_start_hash,
            }
            if existing is not None:
                if not self._same(existing, immutable):
                    raise CompanionConflictError(
                        f"candidate_build_conflict:{admission.build_id}"
                    )
                return dict(existing)
            db.execute(
                """INSERT INTO candidate_builds(
                profile_id,profile_generation,build_id,source_kind,source_ref,
                reflection_job_id,proposal_ref,proposal_json,proposal_hash,
                evidence_set_json,
                evidence_set_hash,candidate_mode,source_fence_json,
                source_fence_hash,target_fence_json,target_fence_hash,
                builder_launch_id,builder_child_run_id,
                expected_child_start_hash,status,reason_code,schema_version,
                created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                         'proposed','explicit_user_build_admitted',1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    *immutable.values(),
                    now,
                    now,
                ),
            )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    [
                        "explicit_candidate_build",
                        admission.build_id,
                        admission.proposal_hash,
                    ]
                ),
                now=now,
            )
            row = db.execute(
                """SELECT * FROM candidate_builds
                   WHERE profile_id=? AND profile_generation=? AND build_id=?""",
                (owner.profile_id, owner.profile_generation, admission.build_id),
            ).fetchone()
            assert row is not None
            result = dict(row)
            result["proposal_json"] = proposal_json
            return result

    def admit_reflection_decision(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        decision_id: str,
        decision: str,
        reason_code: str,
        evidence_event_ids: Sequence[str],
        source_ref: str,
        proposal_ref: str | None = None,
        proposal: Any | None = None,
        build_id: str | None = None,
    ) -> Mapping[str, Any]:
        """Persist one reflection outcome and its candidate admission atomically.

        The caller may supply model-produced proposal fields, but this method
        rehydrates the closed proposal type and derives all host identities,
        hashes, owner fences, reservation rows, and child launch identities.
        Non-candidate outcomes never create a build.
        """

        from .growth import (
            GrowthProposalDecision,
            StructuredGrowthProposalV1,
            evidence_set_hash,
        )

        allowed = {"candidate", "abstain", "insufficient", "stale", "noop"}
        if decision not in allowed:
            raise ValueError("reflection_decision_invalid")
        job_id = str(job_id).strip()
        decision_id = str(decision_id).strip()
        source_ref = str(source_ref).strip()
        if not job_id or not decision_id or not source_ref or not reason_code:
            raise ValueError("reflection_decision_identity_required")
        evidence_ids = tuple(
            sorted({str(item).strip() for item in evidence_event_ids})
        )
        if any(not item for item in evidence_ids):
            raise ValueError("reflection_evidence_id_invalid")
        evidence_hash = evidence_set_hash(evidence_ids)

        typed_proposal: StructuredGrowthProposalV1 | None
        if proposal is None:
            typed_proposal = None
        elif isinstance(proposal, StructuredGrowthProposalV1):
            typed_proposal = proposal
        elif isinstance(proposal, Mapping):
            typed_proposal = StructuredGrowthProposalV1.from_mapping(proposal)
        else:
            raise TypeError("structured_growth_proposal_required")
        if decision == "candidate":
            if (
                typed_proposal is None
                or typed_proposal.decision
                is not GrowthProposalDecision.CANDIDATE
            ):
                raise CompanionStateError(
                    "reflection_candidate_proposal_required"
                )
            if not proposal_ref:
                raise ValueError("reflection_proposal_ref_required")
        elif (
            typed_proposal is not None
            and typed_proposal.decision
            is not GrowthProposalDecision.ABSTAIN
        ):
            raise CompanionStateError(
                "reflection_non_candidate_proposal_invalid"
            )
        if typed_proposal is not None and (
            typed_proposal.evidence_event_ids != evidence_ids
            or typed_proposal.evidence_set_hash != evidence_hash
        ):
            raise CompanionConflictError(
                "reflection_proposal_evidence_mismatch"
            )

        proposal_json = (
            None
            if typed_proposal is None
            else canonical_json(typed_proposal.to_dict())
        )
        proposal_hash = (
            None if typed_proposal is None else typed_proposal.proposal_hash
        )
        normalized_proposal_ref = (
            None if proposal_ref is None else str(proposal_ref).strip()
        )
        if proposal_ref is not None and not normalized_proposal_ref:
            raise ValueError("reflection_proposal_ref_invalid")
        if decision == "candidate":
            assert typed_proposal is not None
            assert normalized_proposal_ref is not None
            expected_build_id = "candidate-build:" + canonical_hash(
                {
                    "schema": "reflection-candidate-build-v1",
                    "owner": {
                        "profile_id": owner.profile_id,
                        "profile_generation": owner.profile_generation,
                    },
                    "job_id": job_id,
                    "proposal_ref": normalized_proposal_ref,
                    "proposal_hash": proposal_hash,
                    "evidence_set_hash": evidence_hash,
                }
            )
            if build_id is not None and str(build_id) != expected_build_id:
                raise CompanionConflictError(
                    "reflection_build_id_not_host_derived"
                )
            normalized_build_id = expected_build_id
        else:
            if build_id is not None:
                raise CompanionStateError(
                    "reflection_non_candidate_cannot_build"
                )
            normalized_build_id = None

        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            job = db.execute(
                """SELECT * FROM jobs
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            if (
                job is None
                or str(job["kind"]) != "reflection"
                or str(job["status"]) not in {"leased", "succeeded"}
            ):
                raise CompanionStateError(
                    "reflection_job_not_admissible"
                )
            if evidence_ids:
                placeholders = ",".join("?" for _ in evidence_ids)
                live = db.execute(
                    f"""SELECT event_id FROM growth_events
                        WHERE profile_id=? AND profile_generation=?
                          AND content_state='live'
                          AND event_id IN ({placeholders})""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        *evidence_ids,
                    ),
                ).fetchall()
                if {str(row["event_id"]) for row in live} != set(evidence_ids):
                    raise CompanionStateError(
                        "reflection_evidence_missing_or_tombstoned"
                    )

            existing = db.execute(
                """SELECT * FROM reflection_decisions
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                   ORDER BY decision_id""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchall()
            expected_decision = {
                "job_id": job_id,
                "decision_id": decision_id,
                "decision": decision,
                "reason_code": reason_code,
                "evidence_set_hash": evidence_hash,
                "source_kind": "reflection",
                "source_ref": source_ref,
                "proposal_ref": normalized_proposal_ref,
                "proposal_json": proposal_json,
                "proposal_hash": proposal_hash,
                "schema_version": 1,
            }
            if existing:
                if (
                    len(existing) != 1
                    or not self._same(existing[0], expected_decision)
                ):
                    raise CompanionConflictError(
                        "reflection_decision_conflict"
                    )
                decision_row = dict(existing[0])
                build_row = None
                if normalized_build_id is not None:
                    build_row = self._find_candidate_build(
                        db, normalized_build_id, owner=owner
                    )
                return {
                    "decision": decision_row,
                    "candidate_build": (
                        None if build_row is None else dict(build_row)
                    ),
                }

            db.execute(
                """INSERT INTO reflection_decisions(
                     profile_id,profile_generation,job_id,decision_id,
                     decision,reason_code,evidence_set_hash,source_kind,
                     source_ref,proposal_ref,proposal_json,proposal_hash,
                     schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                    decision_id,
                    decision,
                    reason_code,
                    evidence_hash,
                    "reflection",
                    source_ref,
                    normalized_proposal_ref,
                    proposal_json,
                    proposal_hash,
                    now,
                ),
            )
            build_row: sqlite3.Row | None = None
            if decision == "candidate":
                assert typed_proposal is not None
                assert typed_proposal.target is not None
                assert typed_proposal.target_fence is not None
                assert normalized_build_id is not None
                assert normalized_proposal_ref is not None
                target = typed_proposal.target
                target_expected = {
                    "kind": target.kind.value,
                    "stable_name": target.stable_name,
                    "pack_id": target.pack_id,
                    "governance_domain": "companion_growth",
                }
                target_row = db.execute(
                    """SELECT * FROM growth_targets
                       WHERE profile_id=? AND profile_generation=?
                         AND target_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        target.target_id,
                    ),
                ).fetchone()
                if target_row is None:
                    db.execute(
                        """INSERT INTO growth_targets(
                             profile_id,profile_generation,target_id,kind,
                             stable_name,pack_id,governance_domain,reason_code,
                             schema_version,created_at,updated_at
                           ) VALUES (?,?,?,?,?,?,?,'reflection_candidate',1,?,?)""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            target.target_id,
                            target.kind.value,
                            target.stable_name,
                            target.pack_id,
                            "companion_growth",
                            now,
                            now,
                        ),
                    )
                elif not self._same(target_row, target_expected):
                    raise CompanionConflictError(
                        f"growth_target_conflict:{target.target_id}"
                    )
                if typed_proposal.candidate_mode.value == "genesis":
                    reservation = db.execute(
                        """SELECT * FROM growth_target_reservations
                           WHERE profile_id=? AND profile_generation=?
                             AND kind=? AND stable_name=?""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            target.kind.value,
                            target.stable_name,
                        ),
                    ).fetchone()
                    if reservation is None:
                        db.execute(
                            """INSERT INTO growth_target_reservations(
                                 profile_id,profile_generation,kind,stable_name,
                                 target_id,pack_id,candidate_id,
                                 reservation_version,status,reason_code,
                                 schema_version,created_at,updated_at
                               ) VALUES (?,?,?,?,?,?,NULL,1,'held',
                                         'reflection_candidate_reserved',1,?,?)""",
                            (
                                owner.profile_id,
                                owner.profile_generation,
                                target.kind.value,
                                target.stable_name,
                                target.target_id,
                                target.pack_id,
                                now,
                                now,
                            ),
                        )
                    elif (
                        str(reservation["target_id"]) == target.target_id
                        and str(reservation["pack_id"]) == target.pack_id
                        and str(reservation["status"]) == "released"
                        and reservation["candidate_id"] is None
                    ):
                        cursor = db.execute(
                            """UPDATE growth_target_reservations
                               SET status='held',
                                   reason_code='reflection_candidate_replanned',
                                   updated_at=?
                               WHERE profile_id=? AND profile_generation=?
                                 AND kind=? AND stable_name=?
                                 AND target_id=? AND pack_id=?
                                 AND status='released'
                                 AND candidate_id IS NULL
                                 AND reservation_version=?""",
                            (
                                now,
                                owner.profile_id,
                                owner.profile_generation,
                                target.kind.value,
                                target.stable_name,
                                target.target_id,
                                target.pack_id,
                                int(reservation["reservation_version"]),
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise CompanionConflictError(
                                f"growth_target_reservation_replan_conflict:{target.target_id}"
                            )
                    elif (
                        str(reservation["target_id"]) != target.target_id
                        or str(reservation["pack_id"]) != target.pack_id
                        or str(reservation["status"]) != "held"
                        or reservation["candidate_id"] is not None
                    ):
                        raise CompanionConflictError(
                            f"growth_target_reserved:{target.target_id}"
                        )

                source_fence_json = (
                    None
                    if typed_proposal.source_fence is None
                    else canonical_json(
                        typed_proposal.source_fence.to_dict()
                    )
                )
                target_fence_json = canonical_json(
                    typed_proposal.target_fence.to_dict()
                )
                builder_launch_id = "builder-launch:" + canonical_hash(
                    ["candidate-builder-launch-v1", normalized_build_id]
                )
                child_run_id = "candidate-child:" + canonical_hash(
                    ["candidate-builder-child-v1", normalized_build_id]
                )
                expected_child_start_hash = canonical_hash(
                    {
                        "schema": "candidate-builder-start-v1",
                        "build_id": normalized_build_id,
                        "proposal_ref": normalized_proposal_ref,
                        "proposal_hash": proposal_hash,
                        "evidence_set_hash": evidence_hash,
                        "builder_launch_id": builder_launch_id,
                        "child_run_id": child_run_id,
                    }
                )
                db.execute(
                    """INSERT INTO candidate_builds(
                         profile_id,profile_generation,build_id,source_kind,
                         source_ref,reflection_job_id,proposal_ref,
                         proposal_json,proposal_hash,evidence_set_json,
                         evidence_set_hash,candidate_mode,source_fence_json,
                         source_fence_hash,target_fence_json,target_fence_hash,
                         builder_launch_id,builder_child_run_id,
                         expected_child_start_hash,status,reason_code,
                         schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                                 'proposed','reflection_candidate_admitted',
                                 1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        normalized_build_id,
                        "reflection",
                        source_ref,
                        job_id,
                        normalized_proposal_ref,
                        proposal_json,
                        proposal_hash,
                        canonical_json(evidence_ids),
                        evidence_hash,
                        typed_proposal.candidate_mode.value,
                        source_fence_json,
                        canonical_hash(
                            None
                            if typed_proposal.source_fence is None
                            else typed_proposal.source_fence.to_dict()
                        ),
                        target_fence_json,
                        canonical_hash(
                            typed_proposal.target_fence.to_dict()
                        ),
                        builder_launch_id,
                        child_run_id,
                        expected_child_start_hash,
                        now,
                        now,
                    ),
                )
                build_row = self._find_candidate_build(
                    db, normalized_build_id, owner=owner
                )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    [
                        "reflection_decision",
                        job_id,
                        decision_id,
                        decision,
                        proposal_hash,
                    ]
                ),
                now=now,
            )
            decision_row = db.execute(
                """SELECT * FROM reflection_decisions
                   WHERE profile_id=? AND profile_generation=?
                     AND job_id=? AND decision_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                    decision_id,
                ),
            ).fetchone()
            assert decision_row is not None
            return {
                "decision": dict(decision_row),
                "candidate_build": (
                    None if build_row is None else dict(build_row)
                ),
            }

    @staticmethod
    def _candidate_build_owner(row: sqlite3.Row) -> OwnerRef:
        return OwnerRef(
            str(row["profile_id"]), int(row["profile_generation"])
        )

    @staticmethod
    def _candidate_build_permit_matches(row: sqlite3.Row, permit: Any) -> bool:
        return (
            str(row["build_id"]) == permit.build_id
            and str(row["build_permit_ref"] or "") == permit.permit_id
            and str(row["build_permit_hash"] or "") == permit.permit_hash
            and int(row["claim_epoch"]) == permit.lease_epoch
            and str(row["proposal_ref"]) == permit.proposal_ref
            and str(row["proposal_hash"]) == permit.proposal_hash
            and str(row["evidence_set_hash"]) == permit.evidence_set_hash
            and str(row["source_fence_hash"]) == permit.source_fence_hash
            and str(row["target_fence_hash"]) == permit.target_fence_hash
        )

    @staticmethod
    def _candidate_build_state(row: sqlite3.Row) -> Any:
        from .build_admission import CandidateDraftReceiptExpectationV1
        from .candidate_build_coordinator import CandidateBuildLaunchStateV1

        expectation = None
        if row["draft_receipt_expectation_json"] is not None:
            raw = json.loads(str(row["draft_receipt_expectation_json"]))
            expectation = CandidateDraftReceiptExpectationV1(
                **{
                    name: str(raw[name])
                    for name in CandidateDraftReceiptExpectationV1.__dataclass_fields__
                }
            )
        return CandidateBuildLaunchStateV1(
            build_id=str(row["build_id"]),
            status=str(row["status"]),
            permit_id=str(row["build_permit_ref"]),
            permit_hash=str(row["build_permit_hash"]),
            lease_epoch=int(row["claim_epoch"]),
            builder_launch_id=str(row["builder_launch_id"]),
            child_run_id=str(row["builder_child_run_id"]),
            expected_child_start_hash=str(row["expected_child_start_hash"]),
            task_workspace=str(row["task_workspace"] or ""),
            draft_receipt_expectation=expectation,
            candidate_ref=(
                str(row["candidate_ref"])
                if row["candidate_ref"] is not None
                else None
            ),
            candidate_hash=(
                str(row["candidate_hash"])
                if row["candidate_hash"] is not None
                else None
            ),
            last_error=(
                str(row["last_error"])
                if row["last_error"] is not None
                else None
            ),
        )

    def _find_candidate_build(
        self,
        db: sqlite3.Connection,
        build_id: str,
        *,
        owner: OwnerRef | None = None,
    ) -> sqlite3.Row:
        if owner is None:
            rows = db.execute(
                "SELECT * FROM candidate_builds WHERE build_id=?",
                (str(build_id).strip(),),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT * FROM candidate_builds
                   WHERE profile_id=? AND profile_generation=? AND build_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    str(build_id).strip(),
                ),
            ).fetchall()
        if len(rows) != 1:
            raise CompanionStateError(
                "candidate_build_missing"
                if not rows
                else "candidate_build_identity_ambiguous"
            )
        return rows[0]

    def get_candidate_build_recovery(
        self,
        owner: OwnerRef,
        *,
        build_id: str,
    ) -> Mapping[str, str | None]:
        """Return the minimal exact owner-fenced build recovery view.

        This read seam lets the production pipeline distinguish a partially
        committed retry (the candidate is already built, but downstream
        evaluation admission did not finish) from a fresh build.  It remains
        fail-closed on missing or ambiguous identities and deliberately does
        not manufacture a launch-state object for a merely proposed build.
        """

        with self.read() as db:
            self._require_owner(db, owner)
            row = self._find_candidate_build(
                db,
                build_id,
                owner=owner,
            )
            return {
                "build_id": str(row["build_id"]),
                "status": str(row["status"]),
                "candidate_ref": (
                    str(row["candidate_ref"])
                    if row["candidate_ref"] is not None
                    else None
                ),
            }

    def issue_for_claim(
        self,
        *,
        build_id: str,
        claim_owner: str,
        claim_epoch: int,
        owner: OwnerRef | None = None,
        lease_seconds: float = 300.0,
    ) -> Any:
        """Claim/recover one build and issue its host-only exact permit."""

        from .build_admission import GrowthCandidateBuildPermitV1
        from .candidate_build_coordinator import CandidateBuildStatus

        claim_owner = str(claim_owner).strip()
        if (
            not claim_owner
            or claim_epoch < 1
            or lease_seconds <= 0
            or lease_seconds > 900
        ):
            raise ValueError("candidate_build_claim_invalid")
        now = self._now()
        parsed_now = datetime.fromisoformat(now.replace("Z", "+00:00"))
        expiry = (parsed_now + timedelta(seconds=lease_seconds)).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        with self._write() as db:
            row = self._find_candidate_build(db, build_id, owner=owner)
            build_owner = self._candidate_build_owner(row)
            if owner is not None and build_owner != owner:
                raise CompanionOwnerError("candidate_build_owner_mismatch")
            owner = build_owner
            self._require_owner(db, owner)
            status = CandidateBuildStatus(str(row["status"]))
            if status in {
                CandidateBuildStatus.BUILT,
                CandidateBuildStatus.FAILED,
                CandidateBuildStatus.INCONCLUSIVE,
                CandidateBuildStatus.CANCELLED,
                CandidateBuildStatus.STALE,
            }:
                raise CompanionStateError("candidate_build_terminal")
            target = json.loads(str(row["target_fence_json"]))
            owner_key = str(target.get("owner_key") or "")
            permit = GrowthCandidateBuildPermitV1.issue(
                build_id=str(row["build_id"]),
                owner_key=owner_key,
                proposal_ref=str(row["proposal_ref"]),
                proposal_hash=str(row["proposal_hash"]),
                evidence_set_hash=str(row["evidence_set_hash"]),
                source_fence_hash=str(row["source_fence_hash"]),
                target_fence_hash=str(row["target_fence_hash"]),
                lease_epoch=claim_epoch,
            )
            if (
                int(row["claim_epoch"]) == claim_epoch
                and str(row["claim_owner"] or "") == claim_owner
                and self._candidate_build_permit_matches(row, permit)
            ):
                return permit
            lease_expired = (
                row["lease_expires_at"] is not None
                and str(row["lease_expires_at"]) <= now
            )
            if (
                claim_epoch != int(row["claim_epoch"]) + 1
                or (
                    row["claim_owner"] is not None
                    and not lease_expired
                    and int(row["claim_epoch"]) > 0
                )
            ):
                raise CompanionLeaseError("candidate_build_claim_stale")
            evidence_ids = tuple(json.loads(str(row["evidence_set_json"])))
            placeholders = ",".join("?" for _ in evidence_ids)
            live_count = int(
                db.execute(
                    f"""SELECT COUNT(*) FROM growth_events
                        WHERE profile_id=? AND profile_generation=?
                          AND content_state='live'
                          AND event_id IN ({placeholders})""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        *evidence_ids,
                    ),
                ).fetchone()[0]
            )
            if live_count != len(evidence_ids):
                db.execute(
                    """UPDATE candidate_builds
                       SET status='stale',last_error='stale_evidence',
                           reason_code='stale_evidence',updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND build_id=?""",
                    (
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        build_id,
                    ),
                )
                raise CompanionStateError("candidate_build_evidence_stale")
            db.execute(
                """UPDATE candidate_builds
                   SET claim_owner=?,claim_epoch=?,lease_expires_at=?,
                       attempt=attempt+1,build_permit_ref=?,
                       build_permit_hash=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND build_id=?""",
                (
                    claim_owner,
                    claim_epoch,
                    expiry,
                    permit.permit_id,
                    permit.permit_hash,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    build_id,
                ),
            )
            return permit

    def get_next_candidate_build(
        self,
        owner: OwnerRef,
        *,
        claim_owner: str | None = None,
    ) -> Mapping[str, Any] | None:
        """Return the next recoverable build for exactly one owner.

        This is an advisory read.  ``claim_next_candidate_build`` performs the
        authoritative selection and lease CAS in one writer transaction.
        """

        now = self._now()
        with self.read() as db:
            self._require_owner(db, owner)
            params: list[Any] = [
                owner.profile_id,
                owner.profile_generation,
                now,
            ]
            owner_clause = ""
            if claim_owner is not None:
                normalized = str(claim_owner).strip()
                if not normalized:
                    raise ValueError("candidate_build_claim_owner_required")
                owner_clause = " OR claim_owner=?"
                params.append(normalized)
            row = db.execute(
                f"""SELECT * FROM candidate_builds
                    WHERE profile_id=? AND profile_generation=?
                      AND status IN (
                        'proposed','launch_pending','child_precreated',
                        'running','handoff_pending'
                      )
                      AND (
                        claim_owner IS NULL OR lease_expires_at<=?
                        {owner_clause}
                      )
                    ORDER BY
                      CASE status
                        WHEN 'handoff_pending' THEN 0
                        WHEN 'running' THEN 1
                        WHEN 'child_precreated' THEN 2
                        WHEN 'launch_pending' THEN 3
                        ELSE 4
                      END,
                      created_at,build_id
                    LIMIT 1""",
                tuple(params),
            ).fetchone()
            return None if row is None else dict(row)

    def claim_next_candidate_build(
        self,
        owner: OwnerRef,
        *,
        claim_owner: str,
        lease_seconds: float = 300.0,
        build_id: str | None = None,
    ) -> Any | None:
        """Atomically select an owner-fenced build and issue its exact permit.

        ``build_id`` pins recovery to the build named by an already claimed
        scheduler job.  Omitting it preserves the generic queue-consumer
        behavior that selects the owner's highest-priority recoverable build.
        """

        normalized = str(claim_owner).strip()
        normalized_build_id = (
            None if build_id is None else str(build_id).strip()
        )
        if (
            not normalized
            or (build_id is not None and not normalized_build_id)
            or lease_seconds <= 0
            or lease_seconds > 900
        ):
            raise ValueError("candidate_build_claim_invalid")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM candidate_builds
                   WHERE profile_id=? AND profile_generation=?
                     AND status IN (
                       'proposed','launch_pending','child_precreated',
                       'running','handoff_pending'
                     )
                     AND (? IS NULL OR build_id=?)
                     AND (
                       claim_owner IS NULL OR lease_expires_at<=?
                       OR claim_owner=?
                     )
                   ORDER BY
                     CASE status
                       WHEN 'handoff_pending' THEN 0
                       WHEN 'running' THEN 1
                       WHEN 'child_precreated' THEN 2
                       WHEN 'launch_pending' THEN 3
                       ELSE 4
                     END,
                     created_at,build_id
                   LIMIT 1""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    normalized_build_id,
                    normalized_build_id,
                    now,
                    normalized,
                ),
            ).fetchone()
            if row is None:
                return None
            active_replay = (
                str(row["claim_owner"] or "") == normalized
                and row["lease_expires_at"] is not None
                and str(row["lease_expires_at"]) > now
                and int(row["claim_epoch"]) > 0
                and row["build_permit_ref"] is not None
                and row["build_permit_hash"] is not None
            )
            claim_epoch = (
                int(row["claim_epoch"])
                if active_replay
                else int(row["claim_epoch"]) + 1
            )
            return self.issue_for_claim(
                build_id=str(row["build_id"]),
                claim_owner=normalized,
                claim_epoch=claim_epoch,
                owner=owner,
                lease_seconds=lease_seconds,
            )

    def _candidate_build_transition(
        self,
        permit: Any,
        *,
        allowed: Sequence[str],
        target: str,
        updates: Mapping[str, Any] | None = None,
        reason_code: str,
    ) -> Any:
        now = self._now()
        with self._write() as db:
            row = self._find_candidate_build(db, permit.build_id)
            if not self._candidate_build_permit_matches(row, permit):
                raise CompanionLeaseError("candidate_build_permit_stale")
            if str(row["status"]) == target:
                return self._candidate_build_state(row)
            if str(row["status"]) not in set(allowed):
                raise CompanionStateError(
                    f"candidate_build_transition_invalid:{row['status']}:{target}"
                )
            assignments = ["status=?", "reason_code=?", "updated_at=?"]
            params: list[Any] = [target, reason_code, now]
            for name, value in (updates or {}).items():
                assignments.append(f"{name}=?")
                params.append(value)
            params.extend(
                (
                    row["profile_id"],
                    row["profile_generation"],
                    row["build_id"],
                    row["status"],
                    row["claim_epoch"],
                )
            )
            cursor = db.execute(
                f"""UPDATE candidate_builds SET {','.join(assignments)}
                    WHERE profile_id=? AND profile_generation=? AND build_id=?
                      AND status=? AND claim_epoch=?""",
                tuple(params),
            )
            if cursor.rowcount != 1:
                raise CompanionLeaseError("candidate_build_transition_lost")
            updated = self._find_candidate_build(db, permit.build_id)
            return self._candidate_build_state(updated)

    def prepare_launch_pending(
        self, permit: Any, *, task_workspace: str
    ) -> Any:
        workspace = str(task_workspace).strip()
        if not workspace:
            raise ValueError("candidate_build_workspace_required")
        with self._write() as db:
            row = self._find_candidate_build(db, permit.build_id)
            if not self._candidate_build_permit_matches(row, permit):
                raise CompanionLeaseError("candidate_build_permit_stale")
            existing_workspace = str(row["task_workspace"] or "")
            if existing_workspace and existing_workspace != workspace:
                raise CompanionConflictError("candidate_build_workspace_conflict")
            if str(row["status"]) == "proposed":
                db.execute(
                    """UPDATE candidate_builds
                       SET status='launch_pending',task_workspace=?,
                           reason_code='builder_launch_pending',updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND build_id=?
                         AND status='proposed' AND claim_epoch=?""",
                    (
                        workspace,
                        self._now(),
                        row["profile_id"],
                        row["profile_generation"],
                        row["build_id"],
                        permit.lease_epoch,
                    ),
                )
                row = self._find_candidate_build(db, permit.build_id)
            return self._candidate_build_state(row)

    def ack_child_precreated(
        self,
        permit: Any,
        *,
        builder_launch_id: str,
        child_run_id: str,
        child_start_hash: str,
    ) -> Any:
        self._verify_candidate_child_identity(
            permit,
            builder_launch_id=builder_launch_id,
            child_run_id=child_run_id,
            child_start_hash=child_start_hash,
        )
        return self._candidate_build_transition(
            permit,
            allowed=("launch_pending",),
            target="child_precreated",
            reason_code="builder_child_precreated",
        )

    def ack_running(
        self,
        permit: Any,
        *,
        builder_launch_id: str,
        child_run_id: str,
        child_start_hash: str,
    ) -> Any:
        self._verify_candidate_child_identity(
            permit,
            builder_launch_id=builder_launch_id,
            child_run_id=child_run_id,
            child_start_hash=child_start_hash,
        )
        return self._candidate_build_transition(
            permit,
            allowed=("child_precreated",),
            target="running",
            reason_code="builder_child_running",
        )

    def _verify_candidate_child_identity(
        self,
        permit: Any,
        *,
        builder_launch_id: str,
        child_run_id: str,
        child_start_hash: str,
    ) -> None:
        with self.read() as db:
            row = self._find_candidate_build(db, permit.build_id)
            if (
                not self._candidate_build_permit_matches(row, permit)
                or str(row["builder_launch_id"]) != builder_launch_id
                or str(row["builder_child_run_id"]) != child_run_id
                or str(row["expected_child_start_hash"]) != child_start_hash
            ):
                raise CompanionConflictError(
                    "candidate_builder_child_identity_mismatch"
                )

    def ack_handoff_pending(
        self, permit: Any, *, expectation: Any
    ) -> Any:
        values = {
            name: str(getattr(expectation, name))
            for name in expectation.__dataclass_fields__
        }
        return self._candidate_build_transition(
            permit,
            allowed=("running",),
            target="handoff_pending",
            updates={
                "draft_receipt_ref": expectation.receipt_id,
                "draft_receipt_hash": expectation.receipt_hash,
                "draft_receipt_expectation_json": canonical_json(values),
            },
            reason_code="candidate_handoff_pending",
        )

    def ack_built(
        self,
        permit: Any,
        *,
        expectation: Any,
        handoff: Mapping[str, Any],
    ) -> Any:
        candidate_ref = str(handoff.get("candidate_ref") or "")
        candidate_hash = str(handoff.get("candidate_hash") or "")
        if not candidate_ref or len(candidate_hash) != 64:
            raise CompanionConflictError("candidate_handoff_identity_invalid")
        return self._candidate_build_transition(
            permit,
            allowed=("handoff_pending",),
            target="built",
            updates={
                "candidate_ref": candidate_ref,
                "candidate_hash": candidate_hash,
                "lease_expires_at": None,
            },
            reason_code="candidate_built",
        )

    def mark_stale(self, permit: Any, *, reason_code: str) -> Any:
        return self._candidate_build_transition(
            permit,
            allowed=(
                "proposed",
                "launch_pending",
                "child_precreated",
                "running",
                "handoff_pending",
            ),
            target="stale",
            updates={
                "last_error": str(reason_code),
                "lease_expires_at": None,
            },
            reason_code=str(reason_code),
        )

    def mark_failed(self, permit: Any, *, reason_code: str) -> Any:
        return self._candidate_build_transition(
            permit,
            allowed=(
                "launch_pending",
                "child_precreated",
                "running",
                "handoff_pending",
            ),
            target="failed",
            updates={
                "last_error": str(reason_code),
                "lease_expires_at": None,
            },
            reason_code=str(reason_code),
        )

    @staticmethod
    def _candidate_composition_from_db(
        db: sqlite3.Connection, build_id: str
    ) -> Any:
        from .candidate_composition import CandidateCompositionFactsV1
        from .growth import CandidateBindingFenceV1, GrowthTargetIdentityV1

        rows = db.execute(
            "SELECT * FROM candidate_builds WHERE build_id=?",
            (str(build_id).strip(),),
        ).fetchall()
        if len(rows) != 1:
            raise CompanionStateError(
                "candidate_build_missing"
                if not rows
                else "candidate_build_identity_ambiguous"
            )
        row = rows[0]
        if str(row["status"]) not in {"handoff_pending", "built"}:
            raise CompanionStateError("candidate_build_not_handoff_ready")
        owner = OwnerRef(str(row["profile_id"]), int(row["profile_generation"]))
        proposal = json.loads(str(row["proposal_json"]))
        target_raw = proposal.get("target")
        if not isinstance(target_raw, Mapping):
            raise CompanionStateError("candidate_proposal_target_missing")
        target = GrowthTargetIdentityV1(
            kind=str(target_raw.get("kind") or ""),
            target_id=str(target_raw.get("target_id") or ""),
            stable_name=str(target_raw.get("stable_name") or ""),
            pack_id=str(target_raw.get("pack_id") or ""),
        )
        target_fence_raw = json.loads(str(row["target_fence_json"]))
        target_fence = CandidateBindingFenceV1(**target_fence_raw)
        source_fence = None
        if row["source_fence_json"] not in {None, "", "null"}:
            source_fence = CandidateBindingFenceV1(
                **json.loads(str(row["source_fence_json"]))
            )
        detail = db.execute(
            """SELECT detail_version FROM companion_detail_versions
               WHERE profile_id=? AND profile_generation=?""",
            (owner.profile_id, owner.profile_generation),
        ).fetchone()
        if detail is None:
            raise CompanionStateError("candidate_detail_version_missing")

        existing_attempt = db.execute(
            """SELECT reservation_version,attempt_generation
               FROM candidate_artifacts
               WHERE profile_id=? AND profile_generation=?
                 AND proposal_source_kind=? AND proposal_source_ref=?""",
            (
                owner.profile_id,
                owner.profile_generation,
                str(row["source_kind"]),
                str(row["source_ref"]),
            ),
        ).fetchone()
        reservation_version = None
        if existing_attempt is not None:
            reservation_version = existing_attempt["reservation_version"]
            attempt_generation = int(existing_attempt["attempt_generation"])
        else:
            prior = db.execute(
                """SELECT COALESCE(MAX(attempt_generation),0)
                   FROM candidate_artifacts
                   WHERE profile_id=? AND profile_generation=? AND target_id=?""",
                (owner.profile_id, owner.profile_generation, target.target_id),
            ).fetchone()
            attempt_generation = int(prior[0]) + 1
            if str(row["candidate_mode"]) == "genesis":
                reservation = db.execute(
                    """SELECT reservation_version
                       FROM growth_target_reservations
                       WHERE profile_id=? AND profile_generation=? AND target_id=?
                         AND status='held'
                       ORDER BY reservation_version DESC LIMIT 1""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        target.target_id,
                    ),
                ).fetchone()
                if reservation is None:
                    raise CompanionStateError("candidate_reservation_missing")
                reservation_version = int(reservation["reservation_version"])

        return CandidateCompositionFactsV1.issue_from_store(
            build_id=str(row["build_id"]),
            build_state=str(row["status"]),
            facts_revision=int(detail["detail_version"]),
            owner=owner,
            owner_key=target_fence.owner_key,
            proposal_source_kind=str(row["source_kind"]),
            proposal_ref=str(row["proposal_ref"]),
            proposal_hash=str(row["proposal_hash"]),
            evidence_event_ids=tuple(json.loads(str(row["evidence_set_json"]))),
            evidence_set_hash=str(row["evidence_set_hash"]),
            builder_launch_id=str(row["builder_launch_id"]),
            child_run_id=str(row["builder_child_run_id"]),
            child_start_hash=str(row["expected_child_start_hash"]),
            target=target,
            candidate_mode=str(row["candidate_mode"]),
            target_fence=target_fence,
            source_fence=source_fence,
            reservation_version=(
                None
                if reservation_version is None
                else int(reservation_version)
            ),
            reflection_job_id=(
                None
                if row["reflection_job_id"] is None
                else str(row["reflection_job_id"])
            ),
            attempt_generation=attempt_generation,
        )

    def read_current_candidate_composition(self, *, build_id: str) -> Any:
        """Read the current Store-owned facts after the execution receipt."""

        with self.read() as db:
            return self._candidate_composition_from_db(db, build_id)

    def commit_candidate_build(self, commit: Any) -> Any:
        """CAS current facts and atomically freeze package, attempt and build."""

        from deskpet.capabilities.package_limits import (
            ValidatedCapabilityPackageRefV1,
        )

        from .candidate_composition import (
            CandidateCompositionCommitResultV1,
            CandidateCompositionCommitV1,
            CandidateCompositionError,
        )

        if not isinstance(commit, CandidateCompositionCommitV1):
            raise CandidateCompositionError("candidate_commit_type_invalid")
        if not isinstance(
            commit.validated_package_ref, ValidatedCapabilityPackageRefV1
        ):
            raise CandidateCompositionError("candidate_validated_ref_required")
        package = commit.package
        receipt = commit.receipt
        validated = commit.validated_package_ref
        if (
            package.candidate_manifest_hash != receipt.manifest_hash
            or package.archive_hash != receipt.archive_hash
            or package.effect_topology_hash != receipt.effect_topology_hash
            or validated.source_kind != "companion_growth"
            or validated.manifest_hash != receipt.manifest_hash
            or validated.archive_hash != receipt.archive_hash
        ):
            raise CandidateCompositionError("candidate_commit_hash_mismatch")

        with self._write() as db:
            current = self._candidate_composition_from_db(
                db, commit.attempt.build_id or ""
            )
            if (
                current.owner != commit.owner
                or current.facts_revision != commit.facts_revision
                or current.facts_hash != commit.facts_hash
            ):
                raise CandidateCompositionError("candidate_current_facts_drift")
            artifact = self.create_candidate(
                commit.owner, commit.package, commit.attempt
            )
            row = self._find_candidate_build(db, current.build_id)
            expected_candidate_ref = str(artifact["candidate_id"])
            expected_candidate_hash = package.candidate_package_hash
            if str(row["status"]) == "built":
                if (
                    str(row["candidate_ref"] or "") != expected_candidate_ref
                    or str(row["candidate_hash"] or "")
                    != expected_candidate_hash
                    or str(row["draft_receipt_ref"] or "")
                    != receipt.receipt_id
                    or str(row["draft_receipt_hash"] or "")
                    != receipt.receipt_hash
                ):
                    raise CandidateCompositionError(
                        "candidate_replay_package_drift"
                    )
            else:
                cursor = db.execute(
                    """UPDATE candidate_builds
                       SET status='built',draft_receipt_ref=?,
                           draft_receipt_hash=?,candidate_ref=?,candidate_hash=?,
                           lease_expires_at=NULL,reason_code='candidate_built',
                           updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND build_id=?
                         AND status='handoff_pending'""",
                    (
                        receipt.receipt_id,
                        receipt.receipt_hash,
                        expected_candidate_ref,
                        expected_candidate_hash,
                        self._now(),
                        commit.owner.profile_id,
                        commit.owner.profile_generation,
                        current.build_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise CandidateCompositionError(
                        "candidate_build_commit_lost"
                    )
            return CandidateCompositionCommitResultV1(
                build_id=current.build_id,
                candidate_id=expected_candidate_ref,
                package_id=package.package_id,
            )

    def get_exact_candidate_bundle(
        self,
        owner: OwnerRef,
        *,
        candidate_id: str,
    ) -> Any:
        """Rebuild and verify one committed candidate without leaking SQLite.

        The returned ``ExactCandidateBundleV1`` is suitable for immutable risk,
        evaluation and activation gates.  Every package row, file, blob,
        provenance link, build identity and receipt hash is checked under the
        exact owner generation before any bytes leave the Store boundary.
        """

        from .build_admission import CandidateDraftReceiptV1
        from .candidate_composition import (
            CandidateCompositionError,
            ExactCandidateBundleV1,
        )

        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT
                     a.candidate_id,a.status AS candidate_status,
                     a.package_id,
                     p.candidate_mode,p.pack_id,p.version,
                     p.candidate_content_hash,p.candidate_manifest_hash,
                     p.candidate_package_hash,p.archive_hash,
                     p.source_facts_json,p.target_facts_json,
                     p.effect_topology_hash,p.content_state,
                     p.blob_cleanup_state,p.schema_version AS package_schema_version,
                     s.build_id,s.builder_receipt_ref,
                     s.builder_receipt_hash,s.provenance_state,
                     b.status AS build_status,b.candidate_ref,b.candidate_hash,
                     b.proposal_ref,b.proposal_hash,b.evidence_set_hash,
                     b.target_fence_hash,b.builder_launch_id,
                     b.builder_child_run_id,b.expected_child_start_hash,
                     b.draft_receipt_ref,b.draft_receipt_hash,
                     b.draft_receipt_expectation_json
                   FROM candidate_artifacts a
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   JOIN candidate_package_sources s
                     ON s.profile_id=a.profile_id
                    AND s.profile_generation=a.profile_generation
                    AND s.package_id=a.package_id
                    AND s.candidate_id=a.candidate_id
                   JOIN candidate_builds b
                     ON b.profile_id=a.profile_id
                    AND b.profile_generation=a.profile_generation
                    AND b.build_id=s.build_id
                   WHERE a.profile_id=? AND a.profile_generation=?
                     AND a.candidate_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    str(candidate_id),
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("candidate_bundle_missing")
            if (
                str(row["content_state"]) != "live"
                or str(row["blob_cleanup_state"]) != "live"
                or str(row["provenance_state"]) != "live"
            ):
                raise CompanionStateError(
                    "candidate_bundle_content_unavailable"
                )
            if (
                str(row["build_status"]) != "built"
                or str(row["candidate_ref"]) != str(row["candidate_id"])
                or str(row["candidate_hash"])
                != str(row["candidate_package_hash"])
                or str(row["builder_receipt_ref"])
                != str(row["draft_receipt_ref"])
                or str(row["builder_receipt_hash"])
                != str(row["draft_receipt_hash"])
            ):
                raise CompanionConflictError(
                    "candidate_bundle_build_identity_mismatch"
                )
            raw_expectation = json.loads(
                str(row["draft_receipt_expectation_json"] or "")
            )
            if not isinstance(raw_expectation, dict):
                raise CompanionConflictError(
                    "candidate_bundle_receipt_invalid"
                )
            receipt = CandidateDraftReceiptV1.from_authoritative_row(
                raw_expectation
            )
            receipt_expected = {
                "receipt_id": row["draft_receipt_ref"],
                "receipt_hash": row["draft_receipt_hash"],
                "builder_launch_id": row["builder_launch_id"],
                "child_run_id": row["builder_child_run_id"],
                "child_start_hash": row["expected_child_start_hash"],
                "proposal_ref": row["proposal_ref"],
                "proposal_hash": row["proposal_hash"],
                "evidence_set_hash": row["evidence_set_hash"],
                "target_fence_hash": row["target_fence_hash"],
                "validated_draft_hash": row["candidate_content_hash"],
                "manifest_hash": row["candidate_manifest_hash"],
                "archive_hash": row["archive_hash"],
                "effect_topology_hash": row["effect_topology_hash"],
            }
            for name, expected in receipt_expected.items():
                if getattr(receipt, name) != expected:
                    raise CompanionConflictError(
                        f"candidate_bundle_receipt_{name}_mismatch"
                    )

            blob_rows = db.execute(
                """SELECT * FROM candidate_package_blobs
                   WHERE profile_id=? AND profile_generation=?
                     AND package_id=?
                   ORDER BY blob_kind,blob_id""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    row["package_id"],
                ),
            ).fetchall()
            file_rows = db.execute(
                """SELECT * FROM candidate_package_files
                   WHERE profile_id=? AND profile_generation=?
                     AND package_id=?
                   ORDER BY relative_path""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    row["package_id"],
                ),
            ).fetchall()
            if not blob_rows or not file_rows:
                raise CompanionStateError(
                    "candidate_bundle_children_missing"
                )
            blobs: list[CandidatePackageBlob] = []
            blob_by_key: dict[
                tuple[str, str], CandidatePackageBlob
            ] = {}
            for blob_row in blob_rows:
                payload = bytes(blob_row["payload"])
                blob = CandidatePackageBlob(
                    blob_kind=str(blob_row["blob_kind"]),
                    blob_id=str(blob_row["blob_id"]),
                    content_hash=str(blob_row["content_hash"]),
                    payload=payload,
                )
                key = (blob.blob_kind, blob.blob_id)
                if (
                    key in blob_by_key
                    or str(blob_row["cleanup_state"]) != "live"
                    or int(blob_row["size_bytes"]) != len(payload)
                    or blob.content_hash != _bytes_hash(payload)
                ):
                    raise CompanionConflictError(
                        "candidate_bundle_blob_integrity_mismatch"
                    )
                blob_by_key[key] = blob
                blobs.append(blob)
            manifest_key = (
                "manifest",
                str(row["candidate_manifest_hash"]),
            )
            archive_key = ("archive", str(row["archive_hash"]))
            if (
                manifest_key not in blob_by_key
                or archive_key not in blob_by_key
                or len(
                    [
                        key
                        for key in blob_by_key
                        if key[0] == "manifest"
                    ]
                )
                != 1
                or len(
                    [
                        key
                        for key in blob_by_key
                        if key[0] == "archive"
                    ]
                )
                != 1
            ):
                raise CompanionConflictError(
                    "candidate_bundle_root_blob_mismatch"
                )

            files: list[CandidatePackageFile] = []
            for file_row in file_rows:
                relative_path = str(file_row["relative_path"])
                self._validate_relative_path(relative_path)
                file = CandidatePackageFile(
                    relative_path=relative_path,
                    file_kind=str(file_row["file_kind"]),
                    file_mode=int(file_row["file_mode"]),
                    content_hash=str(file_row["content_hash"]),
                    size_bytes=int(file_row["size_bytes"]),
                    blob_id=str(file_row["blob_id"]),
                )
                blob = blob_by_key.get(("file", file.blob_id))
                if (
                    str(file_row["blob_kind"]) != "file"
                    or blob is None
                    or blob.content_hash != file.content_hash
                    or len(blob.payload) != file.size_bytes
                ):
                    raise CompanionConflictError(
                        "candidate_bundle_file_blob_mismatch"
                    )
                files.append(file)
            expected_blob_keys = {
                manifest_key,
                archive_key,
                *(("file", item.blob_id) for item in files),
            }
            if set(blob_by_key) != expected_blob_keys:
                raise CompanionConflictError(
                    "candidate_bundle_blob_set_mismatch"
                )

            manifest_bytes = blob_by_key[manifest_key].payload
            archive_bytes = blob_by_key[archive_key].payload
            if (
                _bytes_hash(manifest_bytes)
                != str(row["candidate_manifest_hash"])
                or _bytes_hash(archive_bytes) != str(row["archive_hash"])
            ):
                raise CompanionConflictError(
                    "candidate_bundle_root_hash_mismatch"
                )
            try:
                manifest = json.loads(manifest_bytes.decode("utf-8"))
                with zipfile.ZipFile(
                    io.BytesIO(archive_bytes)
                ) as archive:
                    archive_names = archive.namelist()
                    archive_payloads = {
                        name: archive.read(name)
                        for name in archive_names
                    }
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                KeyError,
                zipfile.BadZipFile,
            ) as exc:
                raise CompanionConflictError(
                    "candidate_bundle_archive_invalid"
                ) from exc
            expected_names = [
                "deskpet-pack.json",
                *(item.relative_path for item in files),
            ]
            if (
                not isinstance(manifest, dict)
                or str(manifest.get("id") or "") != str(row["pack_id"])
                or str(manifest.get("version") or "")
                != str(row["version"])
                or archive_names != expected_names
                or archive_payloads["deskpet-pack.json"]
                != manifest_bytes
                or any(
                    archive_payloads[item.relative_path]
                    != blob_by_key[("file", item.blob_id)].payload
                    for item in files
                )
            ):
                raise CompanionConflictError(
                    "candidate_bundle_archive_content_mismatch"
                )
            declared_files = manifest.get("files")
            expected_declared = [
                {
                    "path": item.relative_path,
                    "sha256": item.content_hash,
                }
                for item in files
            ]
            if declared_files != expected_declared:
                raise CompanionConflictError(
                    "candidate_bundle_manifest_file_set_mismatch"
                )

            file_set_hash = canonical_hash(
                {
                    "schema": "candidate-file-set-v1",
                    "files": [
                        {
                            "path": item.relative_path,
                            "mode": item.file_mode,
                            "hash": item.content_hash,
                            "size": item.size_bytes,
                        }
                        for item in files
                    ],
                }
            )
            if file_set_hash != receipt.file_set_hash:
                raise CompanionConflictError(
                    "candidate_bundle_file_set_hash_mismatch"
                )
            package_hash = canonical_hash(
                {
                    "schema": "capability-candidate-package-v1",
                    "pack_id": row["pack_id"],
                    "version": row["version"],
                    "candidate_content_hash": row[
                        "candidate_content_hash"
                    ],
                    "candidate_manifest_hash": row[
                        "candidate_manifest_hash"
                    ],
                    "archive_hash": row["archive_hash"],
                    "file_set_hash": file_set_hash,
                    "effect_topology_hash": row[
                        "effect_topology_hash"
                    ],
                }
            )
            if package_hash != str(row["candidate_package_hash"]):
                raise CompanionConflictError(
                    "candidate_bundle_package_hash_mismatch"
                )
            owner_key = (
                f"companion:{owner.profile_id}:{owner.profile_generation}"
            )
            package_id = canonical_hash(
                {
                    "schema": "candidate-package-id-v1",
                    "owner_key": owner_key,
                    "pack_id": row["pack_id"],
                    "candidate_package_hash": package_hash,
                }
            )
            if package_id != str(row["package_id"]):
                raise CompanionConflictError(
                    "candidate_bundle_package_id_mismatch"
                )
            source_facts = json.loads(str(row["source_facts_json"]))
            target_facts = json.loads(str(row["target_facts_json"]))
            if not isinstance(source_facts, dict) or not isinstance(
                target_facts, dict
            ):
                raise CompanionConflictError(
                    "candidate_bundle_facts_invalid"
                )
            package = CandidatePackage(
                package_id=package_id,
                candidate_mode=CandidateMode(
                    str(row["candidate_mode"])
                ),
                pack_id=str(row["pack_id"]),
                version=str(row["version"]),
                candidate_content_hash=str(
                    row["candidate_content_hash"]
                ),
                candidate_manifest_hash=str(
                    row["candidate_manifest_hash"]
                ),
                candidate_package_hash=package_hash,
                archive_hash=str(row["archive_hash"]),
                effect_topology_hash=str(row["effect_topology_hash"]),
                source_facts=source_facts,
                target_facts=target_facts,
                files=tuple(files),
                blobs=tuple(blobs),
                schema_version=int(row["package_schema_version"]),
            )
            return ExactCandidateBundleV1(
                owner=owner,
                candidate_id=str(row["candidate_id"]),
                candidate_status=str(row["candidate_status"]),
                build_id=str(row["build_id"]),
                build_status=str(row["build_status"]),
                candidate_hash=str(row["candidate_hash"]),
                proposal_ref=str(row["proposal_ref"]),
                proposal_hash=str(row["proposal_hash"]),
                evidence_set_hash=str(row["evidence_set_hash"]),
                target_fence_hash=str(row["target_fence_hash"]),
                package=package,
                receipt=receipt,
            )

    def create_candidate(
        self,
        owner: OwnerRef,
        package: CandidatePackage,
        attempt: CandidateAttempt,
    ) -> Mapping[str, Any]:
        if package.candidate_mode != attempt.candidate_mode:
            raise ValueError("candidate_mode_mismatch")
        now = self._now()
        file_rows = sorted(package.files, key=lambda item: item.relative_path)
        blob_rows = sorted(package.blobs, key=lambda item: (item.blob_kind, item.blob_id))
        for item in file_rows:
            self._validate_relative_path(item.relative_path)
        for blob in blob_rows:
            if blob.content_hash != _bytes_hash(blob.payload):
                raise CompanionConflictError(f"candidate_blob_hash_mismatch:{blob.blob_id}")
        if {item.blob_id for item in file_rows} - {
            item.blob_id for item in blob_rows if item.blob_kind == "file"
        }:
            raise ValueError("candidate_file_blob_missing")
        with self._write() as db:
            self._require_owner(db, owner)
            evidence_ids = tuple(sorted(set(attempt.evidence_event_ids)))
            if not evidence_ids:
                raise CompanionStateError("candidate_requires_evidence")
            placeholders = ",".join("?" for _ in evidence_ids)
            live = db.execute(
                f"""SELECT event_id FROM growth_events
                    WHERE profile_id=? AND profile_generation=?
                      AND event_id IN ({placeholders}) AND content_state='live'""",
                (owner.profile_id, owner.profile_generation, *evidence_ids),
            ).fetchall()
            if {str(row["event_id"]) for row in live} != set(evidence_ids):
                raise CompanionStateError("candidate_evidence_missing_or_tombstoned")

            existing_attempt = db.execute(
                """SELECT * FROM candidate_artifacts
                   WHERE profile_id=? AND profile_generation=?
                     AND (candidate_id=? OR candidate_attempt_key=?
                          OR (proposal_source_kind=? AND proposal_source_ref=?))""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    attempt.candidate_id,
                    attempt.candidate_attempt_key,
                    attempt.proposal_source_kind,
                    attempt.proposal_source_ref,
                ),
            ).fetchone()
            if existing_attempt is not None:
                if (
                    existing_attempt["candidate_id"] != attempt.candidate_id
                    or existing_attempt["candidate_attempt_key"] != attempt.candidate_attempt_key
                    or existing_attempt["package_id"] != package.package_id
                    or existing_attempt["evidence_set_hash"] != attempt.evidence_set_hash
                    or existing_attempt["proposal_source_hash"] != attempt.source_hash
                ):
                    raise CompanionConflictError(
                        f"candidate_attempt_conflict:{attempt.candidate_id}"
                    )
                return dict(existing_attempt)

            package_expected = {
                "candidate_mode": package.candidate_mode.value,
                "pack_id": package.pack_id,
                "version": package.version,
                "candidate_content_hash": package.candidate_content_hash,
                "candidate_manifest_hash": package.candidate_manifest_hash,
                "candidate_package_hash": package.candidate_package_hash,
                "archive_hash": package.archive_hash,
                "source_facts_json": canonical_json(dict(package.source_facts)),
                "target_facts_json": canonical_json(dict(package.target_facts)),
                "effect_topology_hash": package.effect_topology_hash,
                "governance_domain": "companion_growth",
                "content_state": "live",
                "blob_cleanup_state": "live",
                "schema_version": package.schema_version,
            }
            package_row = db.execute(
                """SELECT * FROM candidate_packages
                   WHERE profile_id=? AND profile_generation=?
                     AND (package_id=? OR candidate_content_hash=? OR candidate_package_hash=?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    package.package_id,
                    package.candidate_content_hash,
                    package.candidate_package_hash,
                ),
            ).fetchone()
            if package_row is None:
                db.execute(
                    """INSERT INTO candidate_packages(
                         profile_id,profile_generation,package_id,candidate_mode,pack_id,version,
                         candidate_content_hash,candidate_manifest_hash,candidate_package_hash,
                         archive_hash,source_facts_json,target_facts_json,effect_topology_hash,
                         governance_domain,content_state,blob_cleanup_state,schema_version,
                         reason_code,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        package.package_id,
                        *package_expected.values(),
                        "package_frozen",
                        now,
                        now,
                    ),
                )
            elif (
                package_row["package_id"] != package.package_id
                or not self._same(package_row, package_expected)
            ):
                raise CompanionConflictError(f"candidate_package_conflict:{package.package_id}")

            self._insert_or_verify_package_children(
                db, owner, package.package_id, file_rows, blob_rows, now
            )
            self._validate_or_hold_reservation(db, owner, package, attempt, now)
            db.execute(
                """INSERT INTO candidate_artifacts(
                     profile_id,profile_generation,candidate_id,candidate_attempt_key,package_id,
                     proposal_source_kind,proposal_source_ref,proposal_source_hash,reflection_job_id,
                     candidate_mode,target_id,source_owner_key,source_scope,source_scope_key,
                     source_version,source_manifest_hash,source_binding_generation,target_owner_key,
                     target_scope,target_scope_key,target_expected_absent,
                     target_expected_binding_generation,evidence_set_hash,reservation_version,
                     attempt_generation,status,reason_code,schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    attempt.candidate_id,
                    attempt.candidate_attempt_key,
                    package.package_id,
                    attempt.proposal_source_kind,
                    attempt.proposal_source_ref,
                    attempt.source_hash,
                    attempt.reflection_job_id,
                    attempt.candidate_mode.value,
                    attempt.target_id,
                    attempt.source_owner_key,
                    attempt.source_scope,
                    attempt.source_scope_key,
                    attempt.source_version,
                    attempt.source_manifest_hash,
                    attempt.source_binding_generation,
                    attempt.target_owner_key,
                    attempt.target_scope,
                    attempt.target_scope_key,
                    int(attempt.target_expected_absent),
                    attempt.target_expected_binding_generation,
                    attempt.evidence_set_hash,
                    attempt.reservation_version,
                    attempt.attempt_generation,
                    "proposed",
                    attempt.reason_code,
                    attempt.schema_version,
                    now,
                    now,
                ),
            )
            for event_id in evidence_ids:
                db.execute(
                    """INSERT INTO candidate_evidence(
                         profile_id,profile_generation,candidate_id,event_id,reason_code,
                         schema_version,created_at
                       ) VALUES (?,?,?,?,?,1,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        attempt.candidate_id,
                        event_id,
                        "candidate_evidence",
                        now,
                    ),
                )
                db.execute(
                    """INSERT INTO lineage_edges(
                         profile_id,profile_generation,from_kind,from_id,to_kind,to_id,relation,
                         reason_code,schema_version,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,1,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        "growth_event",
                        event_id,
                        "candidate",
                        attempt.candidate_id,
                        "supports",
                        "candidate_created",
                        now,
                    ),
                )
            db.execute(
                """INSERT INTO candidate_package_sources(
                     profile_id,profile_generation,package_id,candidate_id,build_id,
                     evidence_set_json,evidence_set_hash,builder_receipt_ref,builder_receipt_hash,
                     provenance_state,reason_code,schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    package.package_id,
                    attempt.candidate_id,
                    attempt.build_id,
                    canonical_json(evidence_ids),
                    attempt.evidence_set_hash,
                    attempt.builder_receipt_ref,
                    attempt.builder_receipt_hash,
                    "live",
                    "candidate_source_frozen",
                    now,
                    now,
                ),
            )
            if attempt.candidate_mode.value == "genesis":
                db.execute(
                    """UPDATE growth_target_reservations
                       SET candidate_id=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND target_id=?
                         AND reservation_version=? AND status='held'""",
                    (
                        attempt.candidate_id,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        attempt.target_id,
                        attempt.reservation_version,
                    ),
                )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    ["candidate", package.candidate_package_hash, attempt.candidate_attempt_key]
                ),
                now=now,
            )
            return dict(
                db.execute(
                    """SELECT * FROM candidate_artifacts
                       WHERE profile_id=? AND profile_generation=? AND candidate_id=?""",
                    (owner.profile_id, owner.profile_generation, attempt.candidate_id),
                ).fetchone()
            )

    def _insert_or_verify_package_children(
        self,
        db: sqlite3.Connection,
        owner: OwnerRef,
        package_id: str,
        files: Sequence[Any],
        blobs: Sequence[Any],
        now: str,
    ) -> None:
        expected_blob_keys: set[tuple[str, str]] = set()
        for blob in blobs:
            expected_blob_keys.add((blob.blob_kind, blob.blob_id))
            row = db.execute(
                """SELECT * FROM candidate_package_blobs
                   WHERE profile_id=? AND profile_generation=? AND package_id=?
                     AND blob_kind=? AND blob_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    package_id,
                    blob.blob_kind,
                    blob.blob_id,
                ),
            ).fetchone()
            expected = {
                "content_hash": blob.content_hash,
                "size_bytes": len(blob.payload),
                "payload": blob.payload,
                "cleanup_state": "live",
                "schema_version": 1,
            }
            if row is None:
                db.execute(
                    """INSERT INTO candidate_package_blobs(
                         profile_id,profile_generation,package_id,blob_kind,blob_id,
                         content_hash,size_bytes,payload,cleanup_state,reason_code,
                         schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        package_id,
                        blob.blob_kind,
                        blob.blob_id,
                        blob.content_hash,
                        len(blob.payload),
                        blob.payload,
                        "live",
                        "package_frozen",
                        now,
                        now,
                    ),
                )
            elif not self._same(row, expected):
                raise CompanionConflictError(f"candidate_blob_conflict:{blob.blob_id}")
        actual_blob_keys = {
            (str(row["blob_kind"]), str(row["blob_id"]))
            for row in db.execute(
                """SELECT blob_kind,blob_id FROM candidate_package_blobs
                   WHERE profile_id=? AND profile_generation=? AND package_id=?""",
                (owner.profile_id, owner.profile_generation, package_id),
            ).fetchall()
        }
        if actual_blob_keys != expected_blob_keys:
            raise CompanionConflictError(f"candidate_blob_set_conflict:{package_id}")

        expected_paths: set[str] = set()
        for item in files:
            expected_paths.add(item.relative_path)
            row = db.execute(
                """SELECT * FROM candidate_package_files
                   WHERE profile_id=? AND profile_generation=? AND package_id=? AND relative_path=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    package_id,
                    item.relative_path,
                ),
            ).fetchone()
            expected = {
                "file_kind": item.file_kind,
                "file_mode": item.file_mode,
                "content_hash": item.content_hash,
                "size_bytes": item.size_bytes,
                "blob_kind": "file",
                "blob_id": item.blob_id,
                "schema_version": 1,
            }
            if row is None:
                db.execute(
                    """INSERT INTO candidate_package_files(
                         profile_id,profile_generation,package_id,relative_path,file_kind,file_mode,
                         content_hash,size_bytes,blob_kind,blob_id,reason_code,schema_version,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        package_id,
                        item.relative_path,
                        item.file_kind,
                        item.file_mode,
                        item.content_hash,
                        item.size_bytes,
                        "file",
                        item.blob_id,
                        "package_frozen",
                        now,
                    ),
                )
            elif not self._same(row, expected):
                raise CompanionConflictError(f"candidate_file_conflict:{item.relative_path}")
        actual_paths = {
            str(row["relative_path"])
            for row in db.execute(
                """SELECT relative_path FROM candidate_package_files
                   WHERE profile_id=? AND profile_generation=? AND package_id=?""",
                (owner.profile_id, owner.profile_generation, package_id),
            ).fetchall()
        }
        if actual_paths != expected_paths:
            raise CompanionConflictError(f"candidate_file_set_conflict:{package_id}")

    @staticmethod
    def _validate_or_hold_reservation(
        db: sqlite3.Connection,
        owner: OwnerRef,
        package: CandidatePackage,
        attempt: CandidateAttempt,
        now: str,
    ) -> None:
        target = db.execute(
            """SELECT * FROM growth_targets
               WHERE profile_id=? AND profile_generation=? AND target_id=?""",
            (owner.profile_id, owner.profile_generation, attempt.target_id),
        ).fetchone()
        if target is None or target["pack_id"] != package.pack_id:
            raise CompanionStateError("candidate_target_missing_or_pack_mismatch")
        if attempt.candidate_mode.value != "genesis":
            return
        if attempt.reservation_version is None:
            raise CompanionStateError("genesis_reservation_required")
        reservation = db.execute(
            """SELECT * FROM growth_target_reservations
               WHERE profile_id=? AND profile_generation=? AND kind=? AND stable_name=?""",
            (
                owner.profile_id,
                owner.profile_generation,
                target["kind"],
                target["stable_name"],
            ),
        ).fetchone()
        if reservation is None:
            if attempt.reservation_version != 1:
                raise CompanionStateError("genesis_reservation_version_mismatch")
            db.execute(
                """INSERT INTO growth_target_reservations(
                     profile_id,profile_generation,kind,stable_name,target_id,pack_id,candidate_id,
                     reservation_version,status,reason_code,schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    target["kind"],
                    target["stable_name"],
                    attempt.target_id,
                    package.pack_id,
                    None,
                    1,
                    "held",
                    "genesis_candidate",
                    now,
                    now,
                ),
            )
            return
        if (
            reservation["status"] == "released"
            and int(reservation["reservation_version"]) == attempt.reservation_version
            and reservation["candidate_id"] is None
        ):
            db.execute(
                """UPDATE growth_target_reservations
                   SET status='held',reason_code='genesis_candidate',updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND kind=? AND stable_name=?
                     AND status='released' AND reservation_version=?""",
                (
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    target["kind"],
                    target["stable_name"],
                    attempt.reservation_version,
                ),
            )
            return
        if (
            reservation["status"] != "held"
            or int(reservation["reservation_version"]) != attempt.reservation_version
            or (
                reservation["candidate_id"] is not None
                and reservation["candidate_id"] != attempt.candidate_id
            )
        ):
            raise CompanionStateError("genesis_target_reserved")

    def transition_candidate(
        self,
        owner: OwnerRef,
        *,
        candidate_id: str,
        expected_status: str,
        next_status: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        allowed: dict[str, set[str]] = {
            "proposed": {"preflight_failed", "evaluating", "awaiting_eval_authorization", "invalidated", "expired", "stale"},
            "awaiting_eval_authorization": {"evaluating", "invalidated", "expired", "stale"},
            "evaluating": {"eligible", "awaiting_activation_confirmation", "invalidated", "expired", "stale"},
            "eligible": {"activation_pending", "awaiting_activation_confirmation", "invalidated", "expired", "stale"},
            "awaiting_activation_confirmation": {"activation_pending", "rejected", "stale", "invalidated", "expired"},
            "activation_pending": {"activating", "invalidated", "expired", "stale"},
            "activating": {"active", "activation_failed", "activation_pending", "invalidated", "stale"},
            "active": {"rollback_pending", "quarantined"},
            "rollback_pending": {"rolled_back", "disabled"},
        }
        if next_status not in allowed.get(expected_status, set()):
            raise CompanionStateError(f"candidate_transition_invalid:{expected_status}:{next_status}")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT status,candidate_mode,target_id,reservation_version
                   FROM candidate_artifacts
                   WHERE profile_id=? AND profile_generation=? AND candidate_id=?""",
                (owner.profile_id, owner.profile_generation, candidate_id),
            ).fetchone()
            if row is None:
                raise CompanionStateError("candidate_missing")
            if row["status"] == next_status:
                return dict(row)
            if row["status"] != expected_status:
                raise CompanionConflictError("candidate_state_conflict")
            db.execute(
                """UPDATE candidate_artifacts SET status=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND candidate_id=? AND status=?""",
                (
                    next_status,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                    expected_status,
                ),
            )
            if row["candidate_mode"] == "genesis" and next_status in {
                "rejected",
                "expired",
                "invalidated",
            }:
                db.execute(
                    """UPDATE growth_target_reservations
                       SET candidate_id=NULL,status='released',
                           reservation_version=reservation_version+1,
                           reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND target_id=?
                         AND candidate_id=? AND status='held'""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        row["target_id"],
                        candidate_id,
                    ),
                )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    ["candidate_transition", candidate_id, expected_status, next_status, reason_code]
                ),
                now=now,
            )
            return dict(
                db.execute(
                    """SELECT * FROM candidate_artifacts
                       WHERE profile_id=? AND profile_generation=? AND candidate_id=?""",
                    (owner.profile_id, owner.profile_generation, candidate_id),
                ).fetchone()
            )

    def invalidate_candidate_for_evaluation_replan(
        self,
        owner: OwnerRef,
        *,
        candidate_id: str,
    ) -> Mapping[str, Any]:
        """Invalidate only an evaluating candidate, without reviving other causes.

        An exact replay of this transition is accepted.  A candidate already
        invalidated by forget, stale fencing, or another authority is not
        eligible to create new evidence or work.
        """

        now = self._now()
        reason_code = "evaluation_failed_replan"
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT status,reason_code,candidate_mode,target_id
                   FROM candidate_artifacts
                   WHERE profile_id=? AND profile_generation=?
                     AND candidate_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("candidate_missing")
            if (
                str(row["status"]) == "invalidated"
                and str(row["reason_code"]) == reason_code
            ):
                return {
                    **dict(row),
                    "applied": False,
                    "exact_replay": True,
                }
            if str(row["status"]) != "evaluating":
                return {
                    **dict(row),
                    "applied": False,
                    "exact_replay": False,
                }
            cursor = db.execute(
                """UPDATE candidate_artifacts
                   SET status='invalidated',reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND candidate_id=? AND status='evaluating'""",
                (
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                ),
            )
            if cursor.rowcount != 1:
                raise CompanionConflictError(
                    "candidate_evaluation_replan_transition_lost"
                )
            if str(row["candidate_mode"]) == "genesis":
                db.execute(
                    """UPDATE growth_target_reservations
                       SET candidate_id=NULL,status='released',
                           reservation_version=reservation_version+1,
                           reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=?
                         AND target_id=? AND candidate_id=?
                         AND status='held'""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        row["target_id"],
                        candidate_id,
                    ),
                )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    [
                        "candidate_evaluation_replan",
                        candidate_id,
                        reason_code,
                    ]
                ),
                now=now,
            )
            current = db.execute(
                """SELECT status,reason_code,candidate_mode,target_id
                   FROM candidate_artifacts
                   WHERE profile_id=? AND profile_generation=?
                     AND candidate_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                ),
            ).fetchone()
            assert current is not None
            return {
                **dict(current),
                "applied": True,
                "exact_replay": False,
            }

    def settle_capability_mutation_receipt(
        self,
        owner: OwnerRef,
        receipt: CapabilityMutationReceipt,
    ) -> Mapping[str, Any]:
        """Settle a trusted Manager receipt and all Companion projections once."""

        now = self._now()
        action = receipt.action.value
        candidate_mode = receipt.candidate_mode.value if receipt.candidate_mode else None
        with self._write() as db:
            self._require_owner(db, owner)
            request = db.execute(
                """SELECT * FROM capability_activation_requests
                   WHERE profile_id=? AND profile_generation=? AND activation_request_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    receipt.activation_request_id,
                ),
            ).fetchone()
            if request is None:
                raise CompanionStateError("capability_mutation_request_missing")
            existing = db.execute(
                """SELECT * FROM capability_activation_receipts
                   WHERE profile_id=? AND profile_generation=?
                     AND (activation_request_id=? OR manager_operation_id=?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    receipt.activation_request_id,
                    receipt.manager_operation_id,
                ),
            ).fetchone()
            expected_receipt = {
                "activation_request_id": receipt.activation_request_id,
                "manager_operation_id": receipt.manager_operation_id,
                "action": action,
                "candidate_mode": candidate_mode,
                "pack_id": receipt.pack_id,
                "version": receipt.version,
                "manifest_hash": receipt.manifest_hash,
                "source_fence_hash": receipt.source_fence_hash,
                "runtime_set_ref": receipt.runtime_set_ref,
                "runtime_set_hash": receipt.runtime_set_hash,
                "target_owner_key": receipt.target_owner_key,
                "target_scope": receipt.target_scope,
                "binding_generation": receipt.binding_generation,
                "owner_binding_set_stamp": receipt.owner_binding_set_stamp,
                "process_projection_fingerprint": receipt.process_projection_fingerprint,
                "target_user_owner_binding_set_stamp":
                    receipt.target_user_owner_binding_set_stamp,
                "fallback_owner_key": receipt.fallback_owner_key,
                "fallback_scope": receipt.fallback_scope,
                "fallback_scope_key": receipt.fallback_scope_key,
                "fallback_binding_id": receipt.fallback_binding_id,
                "fallback_binding_generation": receipt.fallback_binding_generation,
                "fallback_pack_id": receipt.fallback_pack_id,
                "fallback_version": receipt.fallback_version,
                "fallback_manifest_hash": receipt.fallback_manifest_hash,
                "fallback_owner_binding_set_stamp":
                    receipt.fallback_owner_binding_set_stamp,
                "fallback_process_projection_fingerprint":
                    receipt.fallback_process_projection_fingerprint,
                "fallback_runtime_set_ref": receipt.fallback_runtime_set_ref,
                "fallback_runtime_set_hash": receipt.fallback_runtime_set_hash,
                "absence_proof_hash": receipt.absence_proof_hash,
                "result_hash": receipt.result_hash,
                "schema_version": receipt.schema_version,
            }
            if existing is not None:
                if not self._same(existing, expected_receipt):
                    raise CompanionConflictError("capability_mutation_receipt_conflict")
                return dict(existing)
            if request["status"] not in {
                "claimed",
                "staging",
                "staged",
                "publishing",
                "unknown",
            }:
                raise CompanionStateError("capability_mutation_not_settleable")
            request_source_hash = (
                canonical_hash(json.loads(str(request["source_fence_json"])))
                if request["source_fence_json"] is not None
                else None
            )
            if (
                request["action"] != action
                or request["candidate_mode"] != candidate_mode
                or request["pack_id"] != receipt.pack_id
                or request["manager_operation_id"] != receipt.manager_operation_id
                or request["runtime_set_ref"] != receipt.runtime_set_ref
                or request["runtime_set_hash"] != receipt.runtime_set_hash
                or request_source_hash != receipt.source_fence_hash
            ):
                raise CompanionConflictError("capability_mutation_receipt_fence_mismatch")
            if action in {"install", "update"}:
                if (
                    request["target_version"] != receipt.version
                    or request["target_manifest_hash"] != receipt.manifest_hash
                    or request["target_owner_key"] != receipt.target_owner_key
                    or request["target_scope"] != receipt.target_scope
                    or receipt.binding_generation is None
                    or receipt.binding_generation
                    <= int(request["target_expected_binding_generation"])
                    or not receipt.owner_binding_set_stamp
                    or not receipt.process_projection_fingerprint
                    or receipt.absence_proof_hash is not None
                ):
                    raise CompanionConflictError("capability_mutation_binding_receipt_invalid")
            elif action == "rollback":
                if request["rollback_kind"] == "remove_override":
                    required = (
                        receipt.target_user_owner_binding_set_stamp,
                        receipt.fallback_owner_key,
                        receipt.fallback_scope,
                        receipt.fallback_scope_key,
                        receipt.fallback_binding_id,
                        receipt.fallback_binding_generation,
                        receipt.fallback_pack_id,
                        receipt.fallback_version,
                        receipt.fallback_manifest_hash,
                        receipt.fallback_owner_binding_set_stamp,
                        receipt.fallback_process_projection_fingerprint,
                        receipt.fallback_runtime_set_ref,
                        receipt.fallback_runtime_set_hash,
                    )
                    if any(value is None for value in required):
                        raise CompanionConflictError("remove_override_receipt_incomplete")
                elif (
                    request["rollback_kind"] != "same_owner_version"
                    or receipt.binding_generation is None
                    or not receipt.owner_binding_set_stamp
                ):
                    raise CompanionConflictError("rollback_receipt_invalid")
            else:
                if receipt.absence_proof_hash is None or any(
                    value is not None
                    for value in (
                        receipt.fallback_owner_key,
                        receipt.fallback_runtime_set_hash,
                        receipt.binding_generation,
                    )
                ):
                    raise CompanionConflictError("capability_absence_receipt_invalid")

            db.execute(
                """INSERT INTO capability_activation_receipts(
                     profile_id,profile_generation,activation_request_id,manager_operation_id,
                     action,candidate_mode,pack_id,version,manifest_hash,source_fence_hash,
                     runtime_set_ref,runtime_set_hash,target_owner_key,target_scope,
                     binding_generation,owner_binding_set_stamp,process_projection_fingerprint,
                     target_user_owner_binding_set_stamp,fallback_owner_key,fallback_scope,
                     fallback_scope_key,fallback_binding_id,fallback_binding_generation,
                     fallback_pack_id,fallback_version,fallback_manifest_hash,
                     fallback_owner_binding_set_stamp,fallback_process_projection_fingerprint,
                     fallback_runtime_set_ref,fallback_runtime_set_hash,absence_proof_hash,
                     result_hash,settled_at,reason_code,schema_version
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    receipt.activation_request_id,
                    receipt.manager_operation_id,
                    action,
                    candidate_mode,
                    receipt.pack_id,
                    receipt.version,
                    receipt.manifest_hash,
                    receipt.source_fence_hash,
                    receipt.runtime_set_ref,
                    receipt.runtime_set_hash,
                    receipt.target_owner_key,
                    receipt.target_scope,
                    receipt.binding_generation,
                    receipt.owner_binding_set_stamp,
                    receipt.process_projection_fingerprint,
                    receipt.target_user_owner_binding_set_stamp,
                    receipt.fallback_owner_key,
                    receipt.fallback_scope,
                    receipt.fallback_scope_key,
                    receipt.fallback_binding_id,
                    receipt.fallback_binding_generation,
                    receipt.fallback_pack_id,
                    receipt.fallback_version,
                    receipt.fallback_manifest_hash,
                    receipt.fallback_owner_binding_set_stamp,
                    receipt.fallback_process_projection_fingerprint,
                    receipt.fallback_runtime_set_ref,
                    receipt.fallback_runtime_set_hash,
                    receipt.absence_proof_hash,
                    receipt.result_hash,
                    now,
                    receipt.reason_code,
                    receipt.schema_version,
                ),
            )
            db.execute(
                """UPDATE capability_activation_requests
                   SET status='succeeded',reason_code=?,lease_expires_at=NULL,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND activation_request_id=?""",
                (
                    receipt.reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    receipt.activation_request_id,
                ),
            )
            settled_candidate_id = request["candidate_id"]
            if (
                settled_candidate_id is None
                and action in {"rollback", "disable"}
            ):
                lineage = db.execute(
                    """SELECT from_id FROM lineage_edges
                       WHERE profile_id=? AND profile_generation=?
                         AND from_kind='candidate'
                         AND to_kind='capability_mutation_request'
                         AND to_id=? AND relation=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        receipt.activation_request_id,
                        action,
                    ),
                ).fetchall()
                if len(lineage) > 1:
                    raise CompanionConflictError(
                        "capability_mutation_candidate_lineage_conflict"
                    )
                if lineage:
                    settled_candidate_id = lineage[0]["from_id"]
            if action in {"install", "update"}:
                db.execute(
                    """UPDATE candidate_artifacts SET status='active',reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND candidate_id=?""",
                    (
                        receipt.reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        request["candidate_id"],
                    ),
                )
                if candidate_mode == "genesis":
                    db.execute(
                        """UPDATE growth_target_reservations
                           SET status='consumed',reason_code=?,updated_at=?
                           WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                             AND status='held'""",
                        (
                            receipt.reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            request["candidate_id"],
                        ),
                    )
            elif action == "rollback":
                if settled_candidate_id is not None:
                    db.execute(
                        """UPDATE candidate_artifacts
                           SET status='rolled_back',reason_code=?,updated_at=?
                           WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                             AND status IN ('active','rollback_pending')""",
                        (
                            receipt.reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            settled_candidate_id,
                        ),
                    )
                db.execute(
                    """UPDATE capability_quarantines
                       SET status='released',release_receipt_hash=?,reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND pack_id=?
                         AND status IN ('active','rollback_pending','release_pending')""",
                    (
                        receipt.result_hash,
                        receipt.reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        receipt.pack_id,
                    ),
                )
            else:
                if settled_candidate_id is not None:
                    db.execute(
                        """UPDATE candidate_artifacts
                           SET status='disabled',reason_code=?,updated_at=?
                           WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                             AND status IN ('active','rollback_pending')""",
                        (
                            receipt.reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            settled_candidate_id,
                        ),
                    )
                db.execute(
                    """UPDATE capability_quarantines
                       SET status='disabled',release_receipt_hash=?,reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND pack_id=?
                         AND status<>'released'""",
                    (
                        receipt.result_hash,
                        receipt.reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        receipt.pack_id,
                    ),
                )

            if action in {"rollback", "disable"}:
                db.execute(
                    """UPDATE capability_activation_guards
                       SET status='rolled_back',rollback_receipt_hash=?,
                           reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=?
                         AND rollback_request_id=?
                         AND status='rollback_pending'""",
                    (
                        receipt.result_hash,
                        receipt.reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        receipt.activation_request_id,
                    ),
                )

            # Any successful binding change supersedes another old open guard.
            db.execute(
                """UPDATE capability_activation_guards
                   SET status='superseded',reason_code='binding_superseded',updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND target_owner_key=?
                     AND target_scope=? AND target_scope_key=? AND pack_id=?
                     AND status='open'""",
                (
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    request["target_owner_key"],
                    request["target_scope"],
                    request["target_scope_key"],
                    request["pack_id"],
                ),
            )
            if receipt.open_guard:
                if (
                    action not in {"install", "update"}
                    or receipt.guard_id is None
                    or receipt.guard_policy_hash is None
                    or receipt.rollback_plan is None
                    or receipt.binding_generation is None
                    or receipt.owner_binding_set_stamp is None
                    or request["candidate_id"] is None
                ):
                    raise CompanionStateError("activation_guard_contract_incomplete")
                risk = db.execute(
                    """SELECT risk FROM risk_assessments
                       WHERE profile_id=? AND profile_generation=? AND risk_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request["risk_id"],
                    ),
                ).fetchone()
                if risk is None or risk["risk"] != "low":
                    raise CompanionStateError("activation_guard_requires_low_risk")
                rollback_plan_json = canonical_json(dict(receipt.rollback_plan))
                rollback_plan_hash = canonical_hash(dict(receipt.rollback_plan))
                expires = (
                    datetime.fromisoformat(now.replace("Z", "+00:00"))
                    + timedelta(hours=24)
                ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                db.execute(
                    """INSERT INTO capability_activation_guards(
                         profile_id,profile_generation,guard_id,activation_request_id,candidate_id,
                         pack_id,version,manifest_hash,receipt_hash,target_owner_key,target_scope,
                         target_scope_key,binding_generation,owner_stamp,opened_at,expires_at,
                         policy_id,policy_hash,window_seconds,threshold,rollback_plan_json,
                         rollback_plan_hash,status,reason_code,schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'companion_guard_v1',?,
                                 86400,1,?,?,'open',?,1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        receipt.guard_id,
                        receipt.activation_request_id,
                        request["candidate_id"],
                        receipt.pack_id,
                        receipt.version,
                        receipt.manifest_hash,
                        receipt.result_hash,
                        request["target_owner_key"],
                        request["target_scope"],
                        request["target_scope_key"],
                        receipt.binding_generation,
                        receipt.owner_binding_set_stamp,
                        now,
                        expires,
                        receipt.guard_policy_hash,
                        rollback_plan_json,
                        rollback_plan_hash,
                        receipt.reason_code,
                        now,
                        now,
                    ),
                )
            self._insert_outbox(
                db,
                owner,
                outbox_id=canonical_hash(
                    ["capability_mutation_settled", receipt.activation_request_id]
                ),
                event_kind="capability_mutation_settled",
                event_id=receipt.activation_request_id,
                sink_kind="companion_notification",
                payload={
                    "schema_version": 1,
                    "activation_request_id": receipt.activation_request_id,
                    "action": action,
                    "pack_id": receipt.pack_id,
                    "version": receipt.version,
                    "manifest_hash": receipt.manifest_hash,
                    "candidate_mode": candidate_mode,
                    "report_id": request["report_id"],
                    "reason_code": receipt.reason_code,
                    "result_hash": receipt.result_hash,
                },
                now=now,
                reason_code=receipt.reason_code,
            )
            self._bump_detail(db, owner, mutation_hash=receipt.result_hash, now=now)
            return dict(
                db.execute(
                    """SELECT * FROM capability_activation_receipts
                       WHERE profile_id=? AND profile_generation=? AND activation_request_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        receipt.activation_request_id,
                    ),
                ).fetchone()
            )

    def get_capability_activation_receipt(
        self,
        owner: OwnerRef,
        *,
        activation_request_id: str,
    ) -> Mapping[str, Any] | None:
        """Read one trusted Manager receipt within the exact owner fence."""

        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM capability_activation_receipts
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    str(activation_request_id),
                ),
            ).fetchone()
            return None if row is None else dict(row)

    def get_candidate_activation_proof(
        self,
        owner: OwnerRef,
        *,
        candidate_id: str,
    ) -> Mapping[str, Any] | None:
        """Return the latest exact decision/request/receipt proof for a candidate.

        A pending request is returned with ``receipt=None``; callers therefore
        cannot mistake an admitted activation intent for a committed Manager
        outcome.
        """

        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT q.*,d.decision_hash,d.actor AS decision_actor,
                          d.decision AS activation_decision,
                          r.risk,r.risk_hash,
                          e.verdict,e.results_root_hash,
                          a.manager_operation_id AS receipt_manager_operation_id,
                          a.result_hash AS receipt_result_hash,
                          a.binding_generation AS receipt_binding_generation,
                          a.owner_binding_set_stamp AS
                            receipt_owner_binding_set_stamp,
                          a.process_projection_fingerprint AS
                            receipt_process_projection_fingerprint,
                          a.settled_at AS receipt_settled_at,
                          a.reason_code AS receipt_reason_code
                   FROM capability_activation_requests q
                   JOIN growth_decisions d
                     ON d.profile_id=q.profile_id
                    AND d.profile_generation=q.profile_generation
                    AND d.decision_id=q.decision_id
                   JOIN risk_assessments r
                     ON r.profile_id=q.profile_id
                    AND r.profile_generation=q.profile_generation
                    AND r.risk_id=q.risk_id
                   JOIN evaluation_reports e
                     ON e.profile_id=q.profile_id
                    AND e.profile_generation=q.profile_generation
                    AND e.report_id=q.report_id
                   LEFT JOIN capability_activation_receipts a
                     ON a.profile_id=q.profile_id
                    AND a.profile_generation=q.profile_generation
                    AND a.activation_request_id=q.activation_request_id
                   WHERE q.profile_id=? AND q.profile_generation=?
                     AND q.candidate_id=?
                   ORDER BY q.created_at DESC,q.activation_request_id DESC
                   LIMIT 1""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    str(candidate_id),
                ),
            ).fetchone()
            if row is None:
                return None
            raw = dict(row)
            receipt = None
            if raw["receipt_result_hash"] is not None:
                receipt = {
                    "activation_request_id": raw[
                        "activation_request_id"
                    ],
                    "manager_operation_id": raw[
                        "receipt_manager_operation_id"
                    ],
                    "result_hash": raw["receipt_result_hash"],
                    "binding_generation": raw[
                        "receipt_binding_generation"
                    ],
                    "owner_binding_set_stamp": raw[
                        "receipt_owner_binding_set_stamp"
                    ],
                    "process_projection_fingerprint": raw[
                        "receipt_process_projection_fingerprint"
                    ],
                    "settled_at": raw["receipt_settled_at"],
                    "reason_code": raw["receipt_reason_code"],
                }
            return {
                "candidate_id": raw["candidate_id"],
                "activation_request_id": raw["activation_request_id"],
                "request_fingerprint": raw["request_fingerprint"],
                "request_status": raw["status"],
                "action": raw["action"],
                "candidate_mode": raw["candidate_mode"],
                "target_owner_key": raw["target_owner_key"],
                "target_scope": raw["target_scope"],
                "target_scope_key": raw["target_scope_key"],
                "candidate_package_hash": raw[
                    "candidate_package_hash"
                ],
                "target_manifest_hash": raw["target_manifest_hash"],
                "archive_hash": raw["archive_hash"],
                "report_id": raw["report_id"],
                "evaluation_verdict": raw["verdict"],
                "evaluation_results_root_hash": raw[
                    "results_root_hash"
                ],
                "risk_id": raw["risk_id"],
                "risk": raw["risk"],
                "risk_hash": raw["risk_hash"],
                "decision_id": raw["decision_id"],
                "decision": raw["activation_decision"],
                "decision_actor": raw["decision_actor"],
                "decision_hash": raw["decision_hash"],
                "receipt": receipt,
            }

    def record_capability_guard_incident(
        self,
        owner: OwnerRef,
        incident: CapabilityGuardIncident,
    ) -> Mapping[str, Any]:
        allowed_failures = {
            "package_integrity",
            "runtime_contract",
            "schema_fingerprint",
            "effect_policy_fingerprint",
        }
        if incident.severity != "critical" or incident.failure_class not in allowed_failures:
            raise CompanionStateError("capability_guard_incident_not_eligible")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM capability_guard_incidents
                   WHERE profile_id=? AND profile_generation=?
                     AND (guard_id=? AND incident_id=?
                          OR source_authority=? AND source_event_id=?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    incident.guard_id,
                    incident.incident_id,
                    incident.source_authority,
                    incident.source_event_id,
                ),
            ).fetchone()
            expected = {
                "guard_id": incident.guard_id,
                "incident_id": incident.incident_id,
                "source_authority": incident.source_authority,
                "source_event_id": incident.source_event_id,
                "binding_generation": incident.binding_generation,
                "pack_id": incident.pack_id,
                "version": incident.version,
                "manifest_hash": incident.manifest_hash,
                "runtime_generation": incident.runtime_generation,
                "failure_class": incident.failure_class,
                "severity": incident.severity,
                "failure_fingerprint": incident.failure_fingerprint,
                "source_receipt_ref": incident.source_receipt_ref,
                "source_receipt_hash": incident.source_receipt_hash,
                "observed_at": incident.observed_at,
                "dedupe_hash": incident.dedupe_hash,
                "schema_version": incident.schema_version,
            }
            if existing is not None:
                if not self._same(existing, expected):
                    raise CompanionConflictError("capability_guard_incident_conflict")
                return dict(existing)
            guard = db.execute(
                """SELECT * FROM capability_activation_guards
                   WHERE profile_id=? AND profile_generation=? AND guard_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    incident.guard_id,
                ),
            ).fetchone()
            if (
                guard is None
                or guard["status"] != "open"
                or guard["expires_at"] <= now
                or incident.observed_at < guard["opened_at"]
                or incident.observed_at > guard["expires_at"]
                or incident.observed_at > now
                or guard["binding_generation"] != incident.binding_generation
                or guard["pack_id"] != incident.pack_id
                or guard["version"] != incident.version
                or guard["manifest_hash"] != incident.manifest_hash
            ):
                raise CompanionStateError("capability_guard_fence_stale")
            candidate = db.execute(
                """SELECT candidate_mode,status FROM candidate_artifacts
                   WHERE profile_id=? AND profile_generation=? AND candidate_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    guard["candidate_id"],
                ),
            ).fetchone()
            if candidate is None or candidate["status"] != "active":
                raise CompanionStateError(
                    "capability_guard_candidate_not_active"
                )
            plan = json.loads(str(guard["rollback_plan_json"]))
            action = str(plan.get("action", "rollback"))
            rollback_kind = plan.get("rollback_kind")
            if action not in {"rollback", "disable"}:
                raise CompanionStateError("capability_guard_rollback_plan_invalid")
            cause_ref = f"guard:{incident.guard_id}:incident:{incident.incident_id}"
            manager_key = canonical_hash(
                [
                    "companion_growth",
                    owner.profile_id,
                    owner.profile_generation,
                    incident.request_fingerprint,
                ]
            )
            db.execute(
                """INSERT INTO capability_activation_requests(
                     profile_id,profile_generation,activation_request_id,request_fingerprint,
                     action,candidate_id,candidate_mode,rollback_kind,activation_mode,
                     target_owner_key,target_scope,target_scope_key,pack_id,target_version,
                     target_manifest_hash,candidate_package_hash,archive_hash,source_fence_json,
                     target_expected_absent,target_expected_binding_generation,cause_ref,
                     manager_idempotency_key,status,claim_epoch,attempt,reason_code,
                     schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,NULL,NULL,?,'normal',?,?,?,?,?,?,NULL,NULL,?,
                             0,?,?,?,'pending',0,0,?,1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    incident.rollback_request_id,
                    incident.request_fingerprint,
                    action,
                    rollback_kind,
                    guard["target_owner_key"],
                    guard["target_scope"],
                    guard["target_scope_key"],
                    guard["pack_id"],
                    plan.get("target_version"),
                    plan.get("target_manifest_hash"),
                    canonical_json(dict(plan.get("source_fence", {})))
                    if plan.get("source_fence") is not None
                    else None,
                    guard["binding_generation"],
                    cause_ref,
                    manager_key,
                    incident.reason_code,
                    now,
                    now,
                ),
            )
            db.execute(
                """INSERT INTO lineage_edges(
                     profile_id,profile_generation,from_kind,from_id,
                     to_kind,to_id,relation,reason_code,
                     schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    "candidate",
                    guard["candidate_id"],
                    "capability_mutation_request",
                    incident.rollback_request_id,
                    action,
                    incident.reason_code,
                    now,
                ),
            )
            db.execute(
                """INSERT INTO capability_guard_incidents(
                     profile_id,profile_generation,guard_id,incident_id,source_authority,
                     source_event_id,binding_generation,pack_id,version,manifest_hash,
                     runtime_generation,failure_class,severity,failure_fingerprint,
                     source_receipt_ref,source_receipt_hash,observed_at,dedupe_hash,status,
                     reason_code,schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'triggered',?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    incident.guard_id,
                    incident.incident_id,
                    incident.source_authority,
                    incident.source_event_id,
                    incident.binding_generation,
                    incident.pack_id,
                    incident.version,
                    incident.manifest_hash,
                    incident.runtime_generation,
                    incident.failure_class,
                    incident.severity,
                    incident.failure_fingerprint,
                    incident.source_receipt_ref,
                    incident.source_receipt_hash,
                    incident.observed_at,
                    incident.dedupe_hash,
                    incident.reason_code,
                    now,
                ),
            )
            db.execute(
                """UPDATE capability_activation_guards
                   SET status='rollback_pending',trigger_incident_id=?,rollback_request_id=?,
                       reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND guard_id=? AND status='open'""",
                (
                    incident.incident_id,
                    incident.rollback_request_id,
                    incident.reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    incident.guard_id,
                ),
            )
            db.execute(
                """UPDATE candidate_artifacts
                   SET status='rollback_pending',reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                     AND status='active'""",
                (
                    incident.reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    guard["candidate_id"],
                ),
            )
            quarantine = db.execute(
                """SELECT fence_generation FROM capability_quarantines
                   WHERE profile_id=? AND profile_generation=? AND pack_id=?
                     AND version=? AND manifest_hash=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    incident.pack_id,
                    incident.version,
                    incident.manifest_hash,
                ),
            ).fetchone()
            if quarantine is None:
                db.execute(
                    """INSERT INTO capability_quarantines(
                         profile_id,profile_generation,pack_id,version,manifest_hash,reason_code,
                         evidence_ref,fence_generation,support_set_hash,status,schema_version,
                         created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,'rollback_pending',1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        incident.pack_id,
                        incident.version,
                        incident.manifest_hash,
                        incident.reason_code,
                        cause_ref,
                        1,
                        canonical_hash([]),
                        now,
                        now,
                    ),
                )
            else:
                db.execute(
                    """UPDATE capability_quarantines
                       SET status='rollback_pending',fence_generation=fence_generation+1,
                           reason_code=?,evidence_ref=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND pack_id=?
                         AND version=? AND manifest_hash=?""",
                    (
                        incident.reason_code,
                        cause_ref,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        incident.pack_id,
                        incident.version,
                        incident.manifest_hash,
                    ),
                )
            self._insert_outbox(
                db,
                owner,
                outbox_id=canonical_hash(
                    ["capability_guard_rollback", incident.guard_id, incident.incident_id]
                ),
                event_kind="capability_mutation",
                event_id=incident.rollback_request_id,
                sink_kind="capability_platform",
                payload={
                    "schema_version": 1,
                    "activation_request_id": incident.rollback_request_id,
                    "request_fingerprint": incident.request_fingerprint,
                    "action": action,
                },
                now=now,
                reason_code=incident.reason_code,
            )
            self._bump_detail(
                db, owner, mutation_hash=incident.dedupe_hash, now=now
            )
            return dict(
                db.execute(
                    """SELECT * FROM capability_guard_incidents
                       WHERE profile_id=? AND profile_generation=? AND guard_id=? AND incident_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        incident.guard_id,
                        incident.incident_id,
                    ),
                ).fetchone()
            )

    # ------------------------------------------------------------------
    # Durable jobs and outbox

    def _enqueue_job_db(
        self,
        db: sqlite3.Connection,
        owner: OwnerRef,
        *,
        job_id: str,
        kind: str,
        dedupe_key: str,
        payload: Mapping[str, Any],
        reason_code: str,
        budget_reserved_tokens: int,
        budget_reserved_ms: int,
        now: str,
    ) -> Mapping[str, Any]:
        payload_json = canonical_json(dict(payload))
        payload_hash = canonical_hash(dict(payload))
        row = db.execute(
            """SELECT * FROM jobs WHERE profile_id=? AND profile_generation=?
               AND (job_id=? OR (kind=? AND dedupe_key=?))""",
            (
                owner.profile_id,
                owner.profile_generation,
                job_id,
                kind,
                dedupe_key,
            ),
        ).fetchone()
        if row is not None:
            if (
                row["job_id"] != job_id
                or row["payload_hash"] != payload_hash
                or row["kind"] != kind
                or row["dedupe_key"] != dedupe_key
            ):
                raise CompanionConflictError(f"job_conflict:{job_id}")
            return dict(row)
        db.execute(
            """INSERT INTO jobs(
                 profile_id,profile_generation,job_id,kind,dedupe_key,payload_json,payload_hash,
                 status,claim_epoch,attempt,budget_reserved_tokens,budget_actual_tokens,
                 budget_reserved_ms,budget_actual_ms,reason_code,schema_version,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,'queued',0,0,?,0,?,0,?,1,?,?)""",
            (
                owner.profile_id,
                owner.profile_generation,
                job_id,
                kind,
                dedupe_key,
                payload_json,
                payload_hash,
                budget_reserved_tokens,
                budget_reserved_ms,
                reason_code,
                now,
                now,
            ),
        )
        return dict(
            db.execute(
                """SELECT * FROM jobs
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                ),
            ).fetchone()
        )

    def enqueue_job(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        kind: str,
        dedupe_key: str,
        payload: Mapping[str, Any],
        reason_code: str = "job_enqueued",
        budget_reserved_tokens: int = 0,
        budget_reserved_ms: int = 0,
    ) -> Mapping[str, Any]:
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            return self._enqueue_job_db(
                db,
                owner,
                job_id=job_id,
                kind=kind,
                dedupe_key=dedupe_key,
                payload=payload,
                reason_code=reason_code,
                budget_reserved_tokens=budget_reserved_tokens,
                budget_reserved_ms=budget_reserved_ms,
                now=now,
            )

    def claim_job(
        self,
        owner: OwnerRef,
        *,
        claim_owner: str,
        lease_seconds: float,
        kinds: Sequence[str] = (),
        max_attempts: int | None = None,
    ) -> LeaseClaim | None:
        if lease_seconds <= 0:
            raise ValueError("job_lease_seconds_invalid")
        if max_attempts is not None and max_attempts < 1:
            raise ValueError("job_max_attempts_invalid")
        now_dt = datetime.fromisoformat(self._now().replace("Z", "+00:00"))
        now = now_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        expiry = (now_dt + timedelta(seconds=lease_seconds)).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        with self._write() as db:
            self._require_owner(db, owner)
            params: list[Any] = [owner.profile_id, owner.profile_generation, now, now]
            kind_filter = ""
            if kinds:
                kind_filter = f" AND kind IN ({','.join('?' for _ in kinds)})"
                params.extend(kinds)
            attempt_filter = ""
            if max_attempts is not None:
                attempt_filter = " AND attempt<?"
                params.append(max_attempts)
            row = db.execute(
                f"""SELECT * FROM jobs
                    WHERE profile_id=? AND profile_generation=?
                      AND (
                        (status='queued' AND (next_retry_at IS NULL OR next_retry_at<=?))
                        OR (status='leased' AND lease_expires_at<=?)
                      )
                      {kind_filter}
                      {attempt_filter}
                    ORDER BY created_at,job_id LIMIT 1""",
                params,
            ).fetchone()
            if row is None:
                return None
            epoch = int(row["claim_epoch"]) + 1
            attempt = int(row["attempt"]) + 1
            db.execute(
                """UPDATE jobs SET status='leased',claim_owner=?,claim_epoch=?,
                     lease_expires_at=?,attempt=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND claim_epoch=?""",
                (
                    claim_owner,
                    epoch,
                    expiry,
                    attempt,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    row["job_id"],
                    row["claim_epoch"],
                ),
            )
            return LeaseClaim(
                item_id=str(row["job_id"]),
                claim_owner=claim_owner,
                claim_epoch=epoch,
                lease_expires_at=expiry,
                attempt=attempt,
                payload=json.loads(str(row["payload_json"])),
            )

    def recover_expired_job_leases(
        self,
        owner: OwnerRef,
        *,
        max_attempts: int,
        retryable_kinds: Sequence[str],
        expire_kinds: Sequence[str] = (),
        reason_code: str = "job_lease_recovered",
    ) -> Mapping[str, int]:
        """Recover expired leases before a runtime accepts new work."""

        if max_attempts < 1:
            raise ValueError("job_max_attempts_invalid")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            params: list[Any] = [
                now,
                owner.profile_id,
                owner.profile_generation,
                now,
            ]
            expire_filter = ""
            if expire_kinds:
                expire_filter = f" AND kind IN ({','.join('?' for _ in expire_kinds)})"
                params.extend(expire_kinds)
            expired = db.execute(
                f"""UPDATE jobs SET status='expired',claim_owner=NULL,
                       lease_expires_at=NULL,reason_code=?,updated_at=?
                     WHERE profile_id=? AND profile_generation=?
                       AND status='leased' AND lease_expires_at<=?
                       {expire_filter}""",
                (reason_code + "_expired", *params),
            ).rowcount
            recovered = 0
            if retryable_kinds:
                retry_params: list[Any] = [
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    now,
                    max_attempts,
                    *retryable_kinds,
                ]
                recovered = db.execute(
                    f"""UPDATE jobs SET status='queued',claim_owner=NULL,
                           lease_expires_at=NULL,next_retry_at=NULL,reason_code=?,updated_at=?
                         WHERE profile_id=? AND profile_generation=?
                           AND status='leased' AND lease_expires_at<=? AND attempt<?
                           AND kind IN ({','.join('?' for _ in retryable_kinds)})""",
                    retry_params,
                ).rowcount
            failed = db.execute(
                """UPDATE jobs SET status='failed',claim_owner=NULL,
                       lease_expires_at=NULL,reason_code=?,updated_at=?
                     WHERE profile_id=? AND profile_generation=?
                       AND status='leased' AND lease_expires_at<=?""",
                (
                    reason_code + "_not_safely_retryable",
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    now,
                ),
            ).rowcount
            return {
                "recovered": int(recovered),
                "expired": int(expired),
                "failed": int(failed),
            }

    def claim_next_job_with_budget(
        self,
        owner: OwnerRef,
        *,
        claim_owner: str,
        lease_seconds: float,
        budget_window_start: str,
        token_budget: int,
        time_budget_ms: int,
        kinds: Sequence[str] = (),
        max_attempts: int,
        max_job_tokens: int,
        max_job_ms: int,
    ) -> LeaseClaim | None:
        """Atomically reserve daily budget and claim the next eligible job."""

        if (
            lease_seconds <= 0
            or max_attempts < 1
            or min(token_budget, time_budget_ms, max_job_tokens, max_job_ms) < 0
        ):
            raise ValueError("job_budget_claim_invalid")
        now_dt = datetime.fromisoformat(self._now().replace("Z", "+00:00"))
        now = now_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        expiry = (now_dt + timedelta(seconds=lease_seconds)).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        with self._write() as db:
            self._require_owner(db, owner)
            totals = db.execute(
                """SELECT
                     COALESCE(SUM(CASE
                       WHEN status='leased' THEN budget_reserved_tokens
                       WHEN status IN ('succeeded','failed') THEN budget_actual_tokens
                       ELSE 0 END),0),
                     COALESCE(SUM(CASE
                       WHEN status='leased' THEN budget_reserved_ms
                       WHEN status IN ('succeeded','failed') THEN budget_actual_ms
                       ELSE 0 END),0)
                   FROM jobs
                   WHERE profile_id=? AND profile_generation=? AND created_at>=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    budget_window_start,
                ),
            ).fetchone()
            remaining_tokens = token_budget - int(totals[0])
            remaining_ms = time_budget_ms - int(totals[1])
            if remaining_tokens < 0 or remaining_ms < 0:
                return None
            params: list[Any] = [
                owner.profile_id,
                owner.profile_generation,
                now,
                now,
                max_attempts,
                min(max_job_tokens, remaining_tokens),
                min(max_job_ms, remaining_ms),
            ]
            kind_filter = ""
            if kinds:
                kind_filter = f" AND kind IN ({','.join('?' for _ in kinds)})"
                params.extend(kinds)
            row = db.execute(
                f"""SELECT * FROM jobs
                    WHERE profile_id=? AND profile_generation=?
                      AND (
                        (status='queued' AND (next_retry_at IS NULL OR next_retry_at<=?))
                        OR (status='leased' AND lease_expires_at<=?)
                      )
                      AND attempt<?
                      AND budget_reserved_tokens<=?
                      AND budget_reserved_ms<=?
                      {kind_filter}
                    ORDER BY created_at,job_id LIMIT 1""",
                params,
            ).fetchone()
            if row is None:
                return None
            epoch = int(row["claim_epoch"]) + 1
            attempt = int(row["attempt"]) + 1
            cursor = db.execute(
                """UPDATE jobs SET status='leased',claim_owner=?,claim_epoch=?,
                     lease_expires_at=?,attempt=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND claim_epoch=?""",
                (
                    claim_owner,
                    epoch,
                    expiry,
                    attempt,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    row["job_id"],
                    row["claim_epoch"],
                ),
            )
            if cursor.rowcount != 1:
                raise CompanionLeaseError(f"job_claim_conflict:{row['job_id']}")
            return LeaseClaim(
                item_id=str(row["job_id"]),
                claim_owner=claim_owner,
                claim_epoch=epoch,
                lease_expires_at=expiry,
                attempt=attempt,
                payload=json.loads(str(row["payload_json"])),
            )

    def defer_job(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        claim_owner: str,
        claim_epoch: int,
        retry_at: datetime | str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        if isinstance(retry_at, str):
            parsed_retry = datetime.fromisoformat(retry_at.replace("Z", "+00:00"))
        else:
            parsed_retry = retry_at
        if parsed_retry.tzinfo is None or parsed_retry.utcoffset() is None:
            raise ValueError("job_retry_at_must_be_timezone_aware")
        retry_value = parsed_retry.astimezone(UTC).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            cursor = db.execute(
                """UPDATE jobs SET status='queued',claim_owner=NULL,
                     lease_expires_at=NULL,next_retry_at=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND status='leased' AND claim_owner=? AND claim_epoch=?""",
                (
                    retry_value,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise CompanionLeaseError(f"job_lease_stale:{job_id}")
            return self.get_job(owner, job_id=job_id, db=db)

    def defer_job_for_replan(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        claim_owner: str,
        claim_epoch: int,
        retry_at: datetime | str,
        failure_context: Mapping[str, Any],
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Persist model-visible failure evidence before a fresh Run attempt."""

        if isinstance(retry_at, str):
            parsed_retry = datetime.fromisoformat(
                retry_at.replace("Z", "+00:00")
            )
        else:
            parsed_retry = retry_at
        if parsed_retry.tzinfo is None or parsed_retry.utcoffset() is None:
            raise ValueError("job_retry_at_must_be_timezone_aware")
        retry_value = parsed_retry.astimezone(UTC).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        detached_failure = json.loads(canonical_json(dict(failure_context)))
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM jobs
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            if (
                row is None
                or row["status"] != "leased"
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError(f"job_lease_stale:{job_id}")
            payload = json.loads(str(row["payload_json"]))
            history = payload.get("failure_history", [])
            if not isinstance(history, list):
                history = []
            failure_record = {
                "attempt": int(row["attempt"]),
                "claim_epoch": claim_epoch,
                "observed_at": now,
                "failure": detached_failure,
            }
            payload["failure_history"] = [*history[-3:], failure_record]
            payload["replan_failure"] = failure_record
            payload_json = canonical_json(payload)
            payload_hash = canonical_hash(payload)
            cursor = db.execute(
                """UPDATE jobs SET status='queued',claim_owner=NULL,
                     lease_expires_at=NULL,next_retry_at=?,payload_json=?,
                     payload_hash=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND status='leased' AND claim_owner=? AND claim_epoch=?""",
                (
                    retry_value,
                    payload_json,
                    payload_hash,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise CompanionLeaseError(f"job_lease_stale:{job_id}")
            return self.get_job(owner, job_id=job_id, db=db)

    def cancel_job(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        reason_code: str,
        claim_owner: str | None = None,
        claim_epoch: int | None = None,
    ) -> Mapping[str, Any]:
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM jobs WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            if row is None:
                raise CompanionStateError("job_missing")
            if row["status"] == "cancelled":
                return dict(row)
            if row["status"] not in {"queued", "leased"}:
                raise CompanionStateError(f"job_not_cancellable:{job_id}")
            if row["status"] == "leased" and (
                claim_owner is None
                or claim_epoch is None
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError(f"job_lease_stale:{job_id}")
            db.execute(
                """UPDATE jobs SET status='cancelled',claim_owner=NULL,
                     lease_expires_at=NULL,next_retry_at=NULL,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                ),
            )
            return self.get_job(owner, job_id=job_id, db=db)

    def get_job(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        db: sqlite3.Connection | None = None,
    ) -> Mapping[str, Any]:
        def read(connection: sqlite3.Connection) -> Mapping[str, Any]:
            row = connection.execute(
                """SELECT * FROM jobs WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            if row is None:
                raise CompanionStateError("job_missing")
            return dict(row)

        if db is not None:
            return read(db)
        with self.read() as connection:
            return read(connection)

    def park_job_waiting_decision(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        claim_owner: str,
        claim_epoch: int,
        execution_run_id: str,
        execution_session_id: str,
        decision_id: str,
        decision_nonce: str,
        decision_version: int,
        decision_kind: str,
        call_id: str,
        effect_id: str,
        tool_name: str,
        args_hash: str,
        capability_hash: str,
        scope_hash: str,
        reason_code: str = "job_waiting_execution_decision",
    ) -> Mapping[str, Any]:
        """Release a job lease while its original durable Run awaits permission."""

        if decision_kind != "permission":
            raise ValueError("companion_job_wait_decision_kind_invalid")
        if decision_version < 0:
            raise ValueError("companion_job_wait_decision_version_invalid")
        wait_facts = {
            "execution_run_id": execution_run_id,
            "execution_session_id": execution_session_id,
            "decision_id": decision_id,
            "decision_nonce": decision_nonce,
            "decision_version": decision_version,
            "decision_kind": decision_kind,
            "call_id": call_id,
            "effect_id": effect_id,
            "tool_name": tool_name,
            "args_hash": args_hash,
            "capability_hash": capability_hash,
            "scope_hash": scope_hash,
        }
        text_facts = {
            key: value
            for key, value in wait_facts.items()
            if key != "decision_version"
        }
        if any(not str(value or "").strip() for value in text_facts.values()):
            raise ValueError("companion_job_wait_decision_identity_invalid")
        for field_name in ("args_hash", "capability_hash", "scope_hash"):
            value = str(wait_facts[field_name])
            if len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise ValueError(
                    f"companion_job_wait_{field_name}_invalid"
                )
        wait_fingerprint = canonical_hash(wait_facts)
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            job = db.execute(
                """SELECT * FROM jobs
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            if job is None:
                raise CompanionStateError("job_missing")
            existing = db.execute(
                """SELECT * FROM job_execution_waits
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["wait_fingerprint"]) != wait_fingerprint
                    or str(existing["status"]) not in {"waiting", "settled"}
                ):
                    raise CompanionConflictError(
                        f"job_execution_wait_conflict:{job_id}"
                    )
                return dict(existing)
            if (
                job["status"] != "leased"
                or job["claim_owner"] != claim_owner
                or int(job["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError(f"job_lease_stale:{job_id}")
            db.execute(
                """INSERT INTO job_execution_waits(
                     profile_id,profile_generation,job_id,execution_run_id,
                     execution_session_id,decision_id,decision_nonce,
                     decision_version,decision_kind,call_id,effect_id,tool_name,
                     args_hash,capability_hash,scope_hash,wait_fingerprint,status,
                     result_ref,result_hash,reason_code,schema_version,
                     created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'waiting',
                             NULL,NULL,?,1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                    execution_run_id,
                    execution_session_id,
                    decision_id,
                    decision_nonce,
                    decision_version,
                    decision_kind,
                    call_id,
                    effect_id,
                    tool_name,
                    args_hash,
                    capability_hash,
                    scope_hash,
                    wait_fingerprint,
                    reason_code,
                    now,
                    now,
                ),
            )
            cursor = db.execute(
                """UPDATE jobs SET status='waiting_decision',claim_owner=NULL,
                     lease_expires_at=NULL,next_retry_at=NULL,reason_code=?,
                     updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND status='leased' AND claim_owner=? AND claim_epoch=?""",
                (
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise CompanionLeaseError(f"job_lease_stale:{job_id}")
            return dict(
                db.execute(
                    """SELECT * FROM job_execution_waits
                       WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                    (owner.profile_id, owner.profile_generation, job_id),
                ).fetchone()
            )

    def get_job_execution_wait(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
    ) -> Mapping[str, Any] | None:
        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM job_execution_waits
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            return None if row is None else dict(row)

    def settle_job_execution_wait(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        execution_run_id: str,
        decision_id: str,
        status: str,
        result_ref: str | None,
        result_hash: str | None,
        reason_code: str,
        budget_actual_tokens: int = 0,
        budget_actual_ms: int = 0,
    ) -> Mapping[str, Any]:
        """Idempotently settle the original job from the Run's terminal delivery."""

        if status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("job_terminal_status_invalid")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            wait = db.execute(
                """SELECT * FROM job_execution_waits
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            if wait is None:
                raise CompanionStateError("job_execution_wait_missing")
            if (
                wait["execution_run_id"] != execution_run_id
                or wait["decision_id"] != decision_id
            ):
                raise CompanionConflictError(
                    f"job_execution_wait_binding_conflict:{job_id}"
                )
            job = db.execute(
                """SELECT * FROM jobs
                   WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            assert job is not None
            if job["status"] == status and wait["status"] == "settled":
                expected = (
                    result_ref,
                    result_hash,
                    reason_code,
                    budget_actual_tokens,
                    budget_actual_ms,
                )
                actual = (
                    job["result_ref"],
                    job["result_hash"],
                    job["reason_code"],
                    int(job["budget_actual_tokens"]),
                    int(job["budget_actual_ms"]),
                )
                if actual != expected:
                    raise CompanionConflictError(
                        f"job_settle_conflict:{job_id}"
                    )
                return dict(job)
            if job["status"] != "waiting_decision" or wait["status"] != "waiting":
                raise CompanionStateError(
                    f"job_execution_wait_not_settleable:{job_id}"
                )
            db.execute(
                """UPDATE jobs SET status=?,result_ref=?,result_hash=?,
                     reason_code=?,budget_actual_tokens=?,budget_actual_ms=?,
                     updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND status='waiting_decision'""",
                (
                    status,
                    result_ref,
                    result_hash,
                    reason_code,
                    budget_actual_tokens,
                    budget_actual_ms,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                ),
            )
            db.execute(
                """UPDATE job_execution_waits SET status='settled',
                     result_ref=?,result_hash=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND status='waiting'""",
                (
                    result_ref,
                    result_hash,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM jobs
                       WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                    (owner.profile_id, owner.profile_generation, job_id),
                ).fetchone()
            )

    def settle_job(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        claim_owner: str,
        claim_epoch: int,
        status: str,
        result_ref: str | None,
        result_hash: str | None,
        reason_code: str,
        budget_actual_tokens: int = 0,
        budget_actual_ms: int = 0,
    ) -> Mapping[str, Any]:
        if status not in {"succeeded", "failed", "cancelled", "expired"}:
            raise ValueError("job_terminal_status_invalid")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM jobs WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                (owner.profile_id, owner.profile_generation, job_id),
            ).fetchone()
            if row is None:
                raise CompanionStateError("job_missing")
            if row["status"] == status:
                if (
                    row["result_ref"] != result_ref
                    or row["result_hash"] != result_hash
                    or row["reason_code"] != reason_code
                    or int(row["budget_actual_tokens"]) != budget_actual_tokens
                    or int(row["budget_actual_ms"]) != budget_actual_ms
                ):
                    raise CompanionConflictError(f"job_settle_conflict:{job_id}")
                return dict(row)
            if (
                row["status"] != "leased"
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError(f"job_lease_stale:{job_id}")
            db.execute(
                """UPDATE jobs SET status=?,result_ref=?,result_hash=?,reason_code=?,
                     budget_actual_tokens=?,budget_actual_ms=?,lease_expires_at=NULL,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND job_id=?
                     AND claim_owner=? AND claim_epoch=? AND status='leased'""",
                (
                    status,
                    result_ref,
                    result_hash,
                    reason_code,
                    budget_actual_tokens,
                    budget_actual_ms,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    job_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM jobs WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                    (owner.profile_id, owner.profile_generation, job_id),
                ).fetchone()
            )

    @staticmethod
    def _insert_outbox(
        db: sqlite3.Connection,
        owner: OwnerRef,
        *,
        outbox_id: str,
        event_kind: str,
        event_id: str,
        sink_kind: str,
        payload: Mapping[str, Any],
        now: str,
        reason_code: str,
    ) -> None:
        payload_json = canonical_json(dict(payload))
        payload_hash = canonical_hash(dict(payload))
        existing = db.execute(
            """SELECT * FROM outbox
               WHERE profile_id=? AND profile_generation=?
                 AND (outbox_id=? OR (event_kind=? AND event_id=? AND sink_kind=?))""",
            (
                owner.profile_id,
                owner.profile_generation,
                outbox_id,
                event_kind,
                event_id,
                sink_kind,
            ),
        ).fetchone()
        if existing is not None:
            if (
                existing["outbox_id"] != outbox_id
                or existing["payload_hash"] != payload_hash
            ):
                raise CompanionConflictError(f"outbox_conflict:{outbox_id}")
            return
        db.execute(
            """INSERT INTO outbox(
                 profile_id,profile_generation,outbox_id,event_kind,event_id,sink_kind,
                 payload_json,payload_hash,status,claim_epoch,attempt,reason_code,
                 schema_version,created_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,'pending',0,0,?,1,?,?)""",
            (
                owner.profile_id,
                owner.profile_generation,
                outbox_id,
                event_kind,
                event_id,
                sink_kind,
                payload_json,
                payload_hash,
                reason_code,
                now,
                now,
            ),
        )

    def enqueue_outbox(
        self,
        owner: OwnerRef,
        *,
        outbox_id: str,
        event_kind: str,
        event_id: str,
        sink_kind: str,
        payload: Mapping[str, Any],
        reason_code: str = "outbox_enqueued",
    ) -> Mapping[str, Any]:
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            self._insert_outbox(
                db,
                owner,
                outbox_id=outbox_id,
                event_kind=event_kind,
                event_id=event_id,
                sink_kind=sink_kind,
                payload=payload,
                now=now,
                reason_code=reason_code,
            )
            return dict(
                db.execute(
                    """SELECT * FROM outbox
                       WHERE profile_id=? AND profile_generation=? AND outbox_id=?""",
                    (owner.profile_id, owner.profile_generation, outbox_id),
                ).fetchone()
            )

    def claim_outbox(
        self,
        owner: OwnerRef,
        *,
        claim_owner: str,
        lease_seconds: float,
        sink_kind: str | None = None,
        event_kinds: Sequence[str] | None = None,
    ) -> LeaseClaim | None:
        now_dt = datetime.fromisoformat(self._now().replace("Z", "+00:00"))
        now = now_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        expiry = (now_dt + timedelta(seconds=lease_seconds)).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        with self._write() as db:
            self._require_owner(db, owner)
            params: list[Any] = [owner.profile_id, owner.profile_generation, now, now]
            sink_filter = ""
            if sink_kind is not None:
                sink_filter = " AND sink_kind=?"
                params.append(sink_kind)
            event_filter = ""
            if event_kinds is not None:
                normalized_kinds = tuple(
                    sorted({str(item).strip() for item in event_kinds if str(item).strip()})
                )
                if not normalized_kinds:
                    return None
                placeholders = ",".join("?" for _ in normalized_kinds)
                event_filter = f" AND event_kind IN ({placeholders})"
                params.extend(normalized_kinds)
            row = db.execute(
                f"""SELECT * FROM outbox
                    WHERE profile_id=? AND profile_generation=?
                      AND (
                        (status='pending' AND (next_retry_at IS NULL OR next_retry_at<=?))
                        OR (status='claimed' AND lease_expires_at<=?)
                      ){sink_filter}{event_filter}
                    ORDER BY created_at,outbox_id LIMIT 1""",
                params,
            ).fetchone()
            if row is None:
                return None
            epoch = int(row["claim_epoch"]) + 1
            attempt = int(row["attempt"]) + 1
            db.execute(
                """UPDATE outbox SET status='claimed',claim_owner=?,claim_epoch=?,
                     lease_expires_at=?,attempt=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND outbox_id=?
                     AND claim_epoch=?""",
                (
                    claim_owner,
                    epoch,
                    expiry,
                    attempt,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    row["outbox_id"],
                    row["claim_epoch"],
                ),
            )
            return LeaseClaim(
                item_id=str(row["outbox_id"]),
                claim_owner=claim_owner,
                claim_epoch=epoch,
                lease_expires_at=expiry,
                attempt=attempt,
                payload=json.loads(str(row["payload_json"])),
                event_kind=str(row["event_kind"]),
                event_id=str(row["event_id"]),
                sink_kind=str(row["sink_kind"]),
                reason_code=str(row["reason_code"]),
            )

    def settle_outbox(
        self,
        owner: OwnerRef,
        *,
        outbox_id: str,
        claim_owner: str,
        claim_epoch: int,
        delivered: bool,
        result_hash: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        now = self._now()
        terminal = "delivered" if delivered else "dead_letter"
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM outbox WHERE profile_id=? AND profile_generation=? AND outbox_id=?""",
                (owner.profile_id, owner.profile_generation, outbox_id),
            ).fetchone()
            if row is None:
                raise CompanionStateError("outbox_missing")
            if row["status"] == terminal:
                if row["result_hash"] != result_hash:
                    raise CompanionConflictError(f"outbox_settle_conflict:{outbox_id}")
                return dict(row)
            if (
                row["status"] != "claimed"
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError(f"outbox_lease_stale:{outbox_id}")
            db.execute(
                """UPDATE outbox SET status=?,result_hash=?,reason_code=?,delivered_at=?,
                     lease_expires_at=NULL,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND outbox_id=?
                     AND claim_owner=? AND claim_epoch=? AND status='claimed'""",
                (
                    terminal,
                    result_hash,
                    reason_code,
                    now if delivered else None,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    outbox_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM outbox
                       WHERE profile_id=? AND profile_generation=? AND outbox_id=?""",
                    (owner.profile_id, owner.profile_generation, outbox_id),
                ).fetchone()
            )

    def retry_outbox(
        self,
        owner: OwnerRef,
        *,
        outbox_id: str,
        claim_owner: str,
        claim_epoch: int,
        retry_at: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Release one exact claim for a later retry without losing the event."""

        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            updated = db.execute(
                """UPDATE outbox SET status='pending',next_retry_at=?,reason_code=?,
                     lease_expires_at=NULL,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND outbox_id=?
                     AND claim_owner=? AND claim_epoch=? AND status='claimed'""",
                (
                    retry_at,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    outbox_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            if updated.rowcount != 1:
                raise CompanionLeaseError(f"outbox_lease_stale:{outbox_id}")
            return dict(
                db.execute(
                    """SELECT * FROM outbox
                       WHERE profile_id=? AND profile_generation=? AND outbox_id=?""",
                    (owner.profile_id, owner.profile_generation, outbox_id),
                ).fetchone()
            )

    # ------------------------------------------------------------------
    # Durable notification inbox

    def create_notification(
        self,
        owner: OwnerRef,
        *,
        notification_id: str,
        kind: str,
        source_refs: Sequence[str],
        summary: str,
        detail: Mapping[str, Any] | Sequence[Any],
        available_actions: Sequence[str],
        reason_code: str = "notification_created",
    ) -> Mapping[str, Any]:
        """Persist the canonical small envelope and its projection outbox atomically."""

        notification_id = str(notification_id or "").strip()
        kind = str(kind or "").strip()
        if not notification_id or not kind:
            raise ValueError("notification_id and kind are required")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                (owner.profile_id, owner.profile_generation, notification_id),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["kind"]) != kind
                    or json.loads(str(existing["source_refs_json"]))
                    != list(source_refs)
                    or json.loads(str(existing["summary_json"])) != str(summary)
                    or json.loads(str(existing["detail_json"])) != detail
                    or json.loads(str(existing["actions_json"]))
                    != list(available_actions)
                ):
                    raise CompanionConflictError(
                        f"notification_conflict:{notification_id}"
                    )
                return self._decode_notification(existing)
            detail_version = self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    ["notification", notification_id, kind, summary, detail]
                ),
                now=now,
            )
            envelope = {
                "notification_id": notification_id,
                "kind": kind,
                "summary": str(summary),
                "detail_ref": notification_id,
                "detail_version": detail_version,
                "available_actions": [str(item) for item in available_actions],
            }
            payload_hash = canonical_hash(envelope)
            sequence = int(
                db.execute(
                    """SELECT COALESCE(MAX(sequence),0)+1 FROM notifications
                       WHERE profile_id=? AND profile_generation=?""",
                    (owner.profile_id, owner.profile_generation),
                ).fetchone()[0]
            )
            db.execute(
                """INSERT INTO notifications(
                     profile_id,profile_generation,notification_id,kind,
                     source_refs_json,sequence,summary_json,detail_json,
                     actions_json,payload_hash,redaction_version,status,reason_code,
                     schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,0,'pending',?,1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    notification_id,
                    kind,
                    canonical_json(list(source_refs)),
                    sequence,
                    canonical_json(str(summary)),
                    canonical_json(detail),
                    canonical_json(list(available_actions)),
                    payload_hash,
                    reason_code,
                    now,
                    now,
                ),
            )
            self._insert_outbox(
                db,
                owner,
                outbox_id=canonical_hash(["notification_projection", notification_id]),
                event_kind="notification_projection",
                event_id=notification_id,
                sink_kind="session_projection",
                payload={
                    "schema_version": 1,
                    "notification_id": notification_id,
                    "payload_hash": payload_hash,
                },
                now=now,
                reason_code=reason_code,
            )
            row = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                (owner.profile_id, owner.profile_generation, notification_id),
            ).fetchone()
            assert row is not None
            return self._decode_notification(row)

    def append_digest_notification(
        self,
        owner: OwnerRef,
        *,
        notification_id: str,
        bucket_start: str,
        bucket_end: str,
        period: str,
        source_ref: str,
        item: Mapping[str, Any],
        reason_code: str = "growth_digest_item_recorded",
    ) -> Mapping[str, Any]:
        """Append one ordinary fact to an unprojected digest bucket exactly once."""

        notification_id = str(notification_id or "").strip()
        source_ref = str(source_ref or "").strip()
        if not notification_id or not source_ref:
            raise ValueError("digest notification_id and source_ref are required")
        if period not in {"daily", "weekly"}:
            raise ValueError("digest period must be daily or weekly")
        normalized_item = {**dict(item), "source_ref": source_ref}
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                (owner.profile_id, owner.profile_generation, notification_id),
            ).fetchone()
            if existing is None:
                source_refs = [source_ref]
                items = [normalized_item]
                sequence = int(
                    db.execute(
                        """SELECT COALESCE(MAX(sequence),0)+1 FROM notifications
                           WHERE profile_id=? AND profile_generation=?""",
                        (owner.profile_id, owner.profile_generation),
                    ).fetchone()[0]
                )
            else:
                if str(existing["kind"]) != "growth_digest":
                    raise CompanionConflictError(
                        f"notification_conflict:{notification_id}"
                    )
                detail = json.loads(str(existing["detail_json"]))
                digest = detail.get("digest") if isinstance(detail, dict) else None
                if (
                    not isinstance(digest, dict)
                    or digest.get("period") != period
                    or digest.get("bucket_start") != bucket_start
                    or digest.get("bucket_end") != bucket_end
                ):
                    raise CompanionConflictError(
                        f"digest_bucket_conflict:{notification_id}"
                    )
                source_refs = [
                    str(value)
                    for value in json.loads(str(existing["source_refs_json"]))
                ]
                if source_ref in source_refs:
                    return self._decode_notification(existing)
                if str(existing["status"]) != "pending":
                    raise CompanionStateError(
                        f"digest_bucket_already_finalized:{notification_id}"
                    )
                raw_items = detail.get("overview")
                if not isinstance(raw_items, list):
                    raise CompanionStateError(
                        f"digest_bucket_items_invalid:{notification_id}"
                    )
                items = [dict(value) for value in raw_items]
                items.append(normalized_item)
                source_refs.append(source_ref)
                sequence = int(existing["sequence"])

            items.sort(key=lambda value: str(value.get("source_ref") or ""))
            source_refs.sort()
            detail = {
                "overview": items,
                "digest": {
                    "period": period,
                    "bucket_start": bucket_start,
                    "bucket_end": bucket_end,
                    "item_count": len(items),
                },
            }
            summary = f"成长摘要：{len(items)} 项低风险优化"
            detail_version = self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    [
                        "growth_digest",
                        notification_id,
                        source_refs,
                        detail,
                    ]
                ),
                now=now,
            )
            envelope = {
                "notification_id": notification_id,
                "kind": "growth_digest",
                "summary": summary,
                "detail_ref": notification_id,
                "detail_version": detail_version,
                "available_actions": ["view_detail"],
            }
            payload_hash = canonical_hash(envelope)
            if existing is None:
                db.execute(
                    """INSERT INTO notifications(
                         profile_id,profile_generation,notification_id,kind,
                         source_refs_json,sequence,summary_json,detail_json,
                         actions_json,payload_hash,redaction_version,status,reason_code,
                         schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,0,'pending',?,1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        notification_id,
                        "growth_digest",
                        canonical_json(source_refs),
                        sequence,
                        canonical_json(summary),
                        canonical_json(detail),
                        canonical_json(["view_detail"]),
                        payload_hash,
                        reason_code,
                        now,
                        now,
                    ),
                )
            else:
                db.execute(
                    """UPDATE notifications
                       SET source_refs_json=?,summary_json=?,detail_json=?,
                           actions_json=?,payload_hash=?,reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=?
                         AND notification_id=? AND status='pending'""",
                    (
                        canonical_json(source_refs),
                        canonical_json(summary),
                        canonical_json(detail),
                        canonical_json(["view_detail"]),
                        payload_hash,
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        notification_id,
                    ),
                )
            row = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                (owner.profile_id, owner.profile_generation, notification_id),
            ).fetchone()
            assert row is not None
            return self._decode_notification(row)

    def finalize_due_digest_notifications(
        self,
        owner: OwnerRef,
        *,
        before: str,
    ) -> int:
        """Publish closed digest buckets; an open bucket never emits partial cards."""

        now = self._now()
        count = 0
        with self._write() as db:
            self._require_owner(db, owner)
            rows = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=?
                     AND kind='growth_digest' AND status='pending'
                   ORDER BY sequence,notification_id""",
                (owner.profile_id, owner.profile_generation),
            ).fetchall()
            for row in rows:
                detail = json.loads(str(row["detail_json"]))
                digest = detail.get("digest") if isinstance(detail, dict) else None
                if (
                    not isinstance(digest, dict)
                    or str(digest.get("bucket_end") or "") > before
                ):
                    continue
                notification_id = str(row["notification_id"])
                self._insert_outbox(
                    db,
                    owner,
                    outbox_id=canonical_hash(
                        ["notification_projection", notification_id]
                    ),
                    event_kind="notification_projection",
                    event_id=notification_id,
                    sink_kind="session_projection",
                    payload={
                        "schema_version": 1,
                        "notification_id": notification_id,
                        "payload_hash": str(row["payload_hash"]),
                    },
                    now=now,
                    reason_code="growth_digest_bucket_closed",
                )
                count += 1
        return count

    @staticmethod
    def _decode_notification(row: sqlite3.Row) -> Mapping[str, Any]:
        result = dict(row)
        result["source_refs"] = json.loads(str(row["source_refs_json"]))
        result["summary"] = json.loads(str(row["summary_json"]))
        result["detail"] = json.loads(str(row["detail_json"]))
        result["available_actions"] = json.loads(str(row["actions_json"]))
        result["notification"] = {
            "notification_id": str(row["notification_id"]),
            "kind": str(row["kind"]),
            "summary": result["summary"],
            "detail_ref": str(row["notification_id"]),
            "detail_version": 0,
            "available_actions": result["available_actions"],
        }
        return result

    def get_notification(
        self,
        owner: OwnerRef | tuple[str, int],
        notification_id: str,
    ) -> Mapping[str, Any] | None:
        resolved = self._owner(owner)
        with self.read() as db:
            self._require_owner(db, resolved)
            row = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                (
                    resolved.profile_id,
                    resolved.profile_generation,
                    str(notification_id),
                ),
            ).fetchone()
            if row is None:
                return None
            result = dict(self._decode_notification(row))
            result["notification"]["detail_version"] = str(
                self.get_detail_version(resolved)["detail_version"]
            )
            return result

    def get_activation_decision_challenge(
        self,
        owner: OwnerRef,
        *,
        nonce: str,
    ) -> Mapping[str, Any] | None:
        """Return the one server-issued activation card challenge."""

        matches: list[Mapping[str, Any]] = []
        for notification in self.list_projectable_notifications(owner):
            if str(notification.get("status") or "") == "redacted":
                continue
            detail = notification.get("detail")
            decision = (
                detail.get("decision")
                if isinstance(detail, Mapping)
                else None
            )
            if (
                isinstance(decision, Mapping)
                and str(decision.get("kind") or "") == "activation"
                and str(decision.get("nonce") or "") == str(nonce)
            ):
                matches.append(dict(decision))
        if len(matches) > 1:
            raise CompanionConflictError(
                "activation_decision_challenge_not_unique"
            )
        return None if not matches else matches[0]

    def list_projectable_notifications(
        self,
        owner: OwnerRef | tuple[str, int],
    ) -> list[Mapping[str, Any]]:
        resolved = self._owner(owner)
        with self.read() as db:
            self._require_owner(db, resolved)
            rows = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=?
                     AND status IN ('pending','projected','redacted')
                   ORDER BY sequence,notification_id""",
                (resolved.profile_id, resolved.profile_generation),
            ).fetchall()
        detail_version = str(self.get_detail_version(resolved)["detail_version"])
        result: list[Mapping[str, Any]] = []
        for row in rows:
            item = dict(self._decode_notification(row))
            item["notification"]["detail_version"] = detail_version
            result.append(item)
        return result

    def mark_notification_projected(
        self,
        owner: OwnerRef,
        *,
        notification_id: str,
        expected_payload_hash: str,
    ) -> Mapping[str, Any]:
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                (owner.profile_id, owner.profile_generation, notification_id),
            ).fetchone()
            if row is None:
                raise CompanionStateError("notification_missing")
            if str(row["payload_hash"]) != str(expected_payload_hash):
                raise CompanionConflictError(
                    f"notification_payload_changed:{notification_id}"
                )
            if str(row["status"]) not in {"redacted", "superseded"}:
                db.execute(
                    """UPDATE notifications SET status='projected',updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                    (
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        notification_id,
                    ),
                )
            return dict(
                db.execute(
                    """SELECT * FROM notifications
                       WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                    (owner.profile_id, owner.profile_generation, notification_id),
                ).fetchone()
            )

    def enqueue_projection_reconcile(
        self,
        owner: OwnerRef,
        *,
        route_version: int,
    ) -> int:
        """Wake every retained inbox item after a durable route change."""

        now = self._now()
        count = 0
        with self._write() as db:
            self._require_owner(db, owner)
            rows = db.execute(
                """SELECT n.notification_id,n.payload_hash FROM notifications AS n
                   WHERE n.profile_id=? AND n.profile_generation=?
                     AND n.status IN ('pending','projected','redacted')
                     AND (
                       n.kind<>'growth_digest' OR n.status<>'pending'
                       OR EXISTS (
                         SELECT 1 FROM outbox AS o
                         WHERE o.profile_id=n.profile_id
                           AND o.profile_generation=n.profile_generation
                           AND o.event_kind='notification_projection'
                           AND o.event_id=n.notification_id
                           AND o.sink_kind='session_projection'
                       )
                     )
                   ORDER BY n.sequence,n.notification_id""",
                (owner.profile_id, owner.profile_generation),
            ).fetchall()
            for row in rows:
                notification_id = str(row["notification_id"])
                self._insert_outbox(
                    db,
                    owner,
                    outbox_id=canonical_hash(
                        ["projection_reconcile", notification_id, int(route_version)]
                    ),
                    event_kind="projection_reconcile",
                    event_id=f"{notification_id}:{int(route_version)}",
                    sink_kind="session_projection",
                    payload={
                        "schema_version": 1,
                        "notification_id": notification_id,
                        "payload_hash": str(row["payload_hash"]),
                        "route_version": int(route_version),
                    },
                    now=now,
                    reason_code="projection_route_changed",
                )
                count += 1
        return count

    def enqueue_projection_repair(
        self,
        owner: OwnerRef,
        *,
        notification_id: str,
        repair_fence: str,
    ) -> Mapping[str, Any]:
        return self.enqueue_outbox(
            owner,
            outbox_id=canonical_hash(
                ["projection_visibility_repair", notification_id, repair_fence]
            ),
            event_kind="projection_visibility_repair",
            event_id=f"{notification_id}:{repair_fence}",
            sink_kind="session_projection",
            payload={
                "schema_version": 1,
                "notification_id": notification_id,
                "repair_fence": repair_fence,
            },
            reason_code="projection_visibility_mismatch",
        )

    def get_redaction_source_hash(
        self,
        owner: OwnerRef | tuple[str, int],
        notification_id: str,
    ) -> str | None:
        resolved = self._owner(owner)
        with self.read() as db:
            self._require_owner(db, resolved)
            rows = db.execute(
                """SELECT payload_json FROM outbox
                   WHERE profile_id=? AND profile_generation=?
                     AND event_kind='projection_redaction'
                   ORDER BY created_at DESC""",
                (resolved.profile_id, resolved.profile_generation),
            ).fetchall()
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if str(payload.get("notification_id")) == str(notification_id):
                return str(payload.get("expected_old_payload_hash") or "") or None
        return None

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
        memory_scopes = {
            str(scope["scope_ref"]): dict(scope)
            for scope in owner_memory_read_scopes
        }
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
                or int(scope.get("profile_generation", 0))
                != owner.profile_generation
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
                        raise CompanionConflictError(
                            "run_growth_snapshot_memory_scope_conflict"
                        )
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

    # ------------------------------------------------------------------
    # Evaluation permits, physical launch claims, results, and reports

    def admit_evaluation_experiment(
        self,
        owner: OwnerRef,
        *,
        evaluation: Mapping[str, Any],
        case_inputs: Sequence[Mapping[str, Any]],
        cases: Sequence[Mapping[str, Any]],
        reason_code: str = "evaluation_experiment_admitted",
    ) -> Mapping[str, Any]:
        """Freeze one evaluation run, its inputs, and all variants atomically.

        This is the only public production admission API for the evaluation
        tables.  It verifies the immutable candidate package fence, derives
        input/fixture hashes from bytes, requires old/candidate variants to
        share the same input, and moves the candidate to ``evaluating`` in the
        same transaction.
        """

        required_run = {
            "evaluation_id",
            "candidate_id",
            "candidate_mode",
            "candidate_package_hash",
            "candidate_manifest_hash",
            "candidate_archive_hash",
            "suite_hash",
            "attempt_key",
            "baseline_kind",
            "old_snapshot_hash",
            "candidate_snapshot_hash",
            "runner_id",
            "runner_policy_hash",
            "provider_id",
            "model_id",
        }
        missing_run = required_run - set(evaluation)
        if missing_run:
            raise ValueError(
                "evaluation_admission_fields_missing:"
                + ",".join(sorted(missing_run))
            )
        candidate_mode = str(evaluation["candidate_mode"])
        baseline_kind = str(evaluation["baseline_kind"])
        if candidate_mode not in {"genesis", "update", "builtin_override"}:
            raise ValueError("evaluation_candidate_mode_invalid")
        if baseline_kind not in {"source_pack", "capability_absent_v1"}:
            raise ValueError("evaluation_baseline_kind_invalid")
        if (
            candidate_mode == "genesis"
            and (
                baseline_kind != "capability_absent_v1"
                or not evaluation.get("absent_baseline_ref")
                or not evaluation.get("absent_baseline_hash")
            )
        ):
            raise ValueError("evaluation_absent_baseline_required")
        if (
            candidate_mode != "genesis"
            and (
                baseline_kind != "source_pack"
                or not evaluation.get("source_owner_key")
                or not evaluation.get("source_scope")
                or not evaluation.get("source_scope_key")
                or not evaluation.get("source_pack_id")
                or not evaluation.get("source_version")
                or not evaluation.get("source_manifest_hash")
                or int(evaluation.get("source_binding_generation") or 0) < 1
            )
        ):
            raise ValueError("evaluation_source_baseline_required")
        if not case_inputs or not cases:
            raise ValueError("evaluation_cases_required")

        def frozen_blob(value: Any, name: str) -> bytes:
            if isinstance(value, bytes):
                return value
            if isinstance(value, (Mapping, list, tuple)):
                return canonical_json(value).encode("utf-8")
            raise TypeError(f"{name}_must_be_bytes_or_json")

        normalized_inputs: list[dict[str, Any]] = []
        seen_input_ids: set[str] = set()
        seen_case_ids: set[str] = set()
        for raw in case_inputs:
            required_input = {
                "input_id",
                "case_id",
                "source_kind",
                "input_envelope",
                "adapter_id",
                "adapter_version",
                "adapter_build_fingerprint",
                "assertion_ref",
                "assertion_hash",
                "read_tool_fixture",
                "evaluation_tool_adapter_map",
            }
            missing = required_input - set(raw)
            if missing:
                raise ValueError(
                    "evaluation_input_fields_missing:"
                    + ",".join(sorted(missing))
                )
            input_id = str(raw["input_id"]).strip()
            case_id = str(raw["case_id"]).strip()
            source_kind = str(raw["source_kind"])
            if (
                not input_id
                or not case_id
                or input_id in seen_input_ids
                or case_id in seen_case_ids
            ):
                raise ValueError("evaluation_input_identity_invalid")
            if source_kind not in {"packaged_suite", "historical_replay"}:
                raise ValueError("evaluation_input_source_kind_invalid")
            seen_input_ids.add(input_id)
            seen_case_ids.add(case_id)
            envelope_blob = frozen_blob(
                raw["input_envelope"], "evaluation_input_envelope"
            )
            fixture_blob = frozen_blob(
                raw["read_tool_fixture"],
                "evaluation_read_tool_fixture",
            )
            adapter_map_json = canonical_json(
                dict(raw["evaluation_tool_adapter_map"])
            )
            source_event_refs = tuple(
                sorted(
                    {
                        str(item).strip()
                        for item in raw.get("source_event_refs", ())
                    }
                )
            )
            if any(not item for item in source_event_refs):
                raise ValueError(
                    "evaluation_source_event_ref_invalid"
                )
            frozen_facts = {
                "schema": "evaluation-case-input-v1",
                "input_id": input_id,
                "case_id": case_id,
                "source_kind": source_kind,
                "resource_ref": raw.get("resource_ref"),
                "resource_hash": raw.get("resource_hash"),
                "source_event_refs": list(source_event_refs),
                "input_envelope_hash": _bytes_hash(envelope_blob),
                "adapter_id": str(raw["adapter_id"]),
                "adapter_version": str(raw["adapter_version"]),
                "adapter_build_fingerprint": str(
                    raw["adapter_build_fingerprint"]
                ),
                "assertion_ref": str(raw["assertion_ref"]),
                "assertion_hash": str(raw["assertion_hash"]),
                "read_tool_fixture_root_hash": _bytes_hash(fixture_blob),
                "evaluation_tool_adapter_map_hash": canonical_hash(
                    json.loads(adapter_map_json)
                ),
            }
            if any(
                not str(frozen_facts[name]).strip()
                for name in (
                    "adapter_id",
                    "adapter_version",
                    "adapter_build_fingerprint",
                    "assertion_ref",
                    "assertion_hash",
                )
            ):
                raise ValueError("evaluation_input_fence_required")
            normalized_inputs.append(
                {
                    **frozen_facts,
                    "source_event_refs_json": (
                        None
                        if not source_event_refs
                        else canonical_json(source_event_refs)
                    ),
                    "input_envelope_blob": envelope_blob,
                    "input_hash": canonical_hash(frozen_facts),
                    "read_tool_fixture_blob": fixture_blob,
                    "evaluation_tool_adapter_map_json": adapter_map_json,
                }
            )

        input_by_case = {
            str(item["case_id"]): item for item in normalized_inputs
        }
        normalized_cases: list[dict[str, str]] = []
        variants_by_case: dict[str, set[str]] = {}
        for raw in cases:
            required_case = {
                "case_id",
                "variant",
                "input_id",
                "manifest_case_version",
                "blind_label",
                "expected_kind",
            }
            missing = required_case - set(raw)
            if missing:
                raise ValueError(
                    "evaluation_case_fields_missing:"
                    + ",".join(sorted(missing))
                )
            case_id = str(raw["case_id"]).strip()
            variant = str(raw["variant"])
            input_id = str(raw["input_id"]).strip()
            input_row = input_by_case.get(case_id)
            if (
                input_row is None
                or input_id != input_row["input_id"]
                or variant not in {"old", "candidate"}
            ):
                raise ValueError("evaluation_case_input_fence_invalid")
            seen_variants = variants_by_case.setdefault(case_id, set())
            if variant in seen_variants:
                raise ValueError("evaluation_case_variant_duplicate")
            seen_variants.add(variant)
            normalized_cases.append(
                {
                    "case_id": case_id,
                    "variant": variant,
                    "input_id": input_id,
                    "input_hash": str(input_row["input_hash"]),
                    "manifest_case_version": str(
                        raw["manifest_case_version"]
                    ),
                    "blind_label": str(raw["blind_label"]),
                    "expected_kind": str(raw["expected_kind"]),
                }
            )
        if any(
            variants != {"old", "candidate"}
            for variants in variants_by_case.values()
        ) or set(variants_by_case) != seen_case_ids:
            raise ValueError("evaluation_case_variants_incomplete")

        evaluation_id = str(evaluation["evaluation_id"]).strip()
        candidate_id = str(evaluation["candidate_id"]).strip()
        if not evaluation_id or not candidate_id:
            raise ValueError("evaluation_identity_required")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            candidate = db.execute(
                """SELECT a.*,p.candidate_package_hash,
                          p.candidate_manifest_hash,p.archive_hash
                   FROM candidate_artifacts a
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   WHERE a.profile_id=? AND a.profile_generation=?
                     AND a.candidate_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                ),
            ).fetchone()
            if candidate is None:
                raise CompanionStateError("evaluation_candidate_missing")
            if (
                str(candidate["candidate_mode"]) != candidate_mode
                or str(candidate["candidate_package_hash"])
                != str(evaluation["candidate_package_hash"])
                or str(candidate["candidate_manifest_hash"])
                != str(evaluation["candidate_manifest_hash"])
                or str(candidate["archive_hash"])
                != str(evaluation["candidate_archive_hash"])
            ):
                raise CompanionConflictError(
                    "evaluation_candidate_package_fence_mismatch"
                )
            existing = db.execute(
                """SELECT * FROM evaluation_runs
                   WHERE profile_id=? AND profile_generation=?
                     AND (evaluation_id=? OR
                          (candidate_id=? AND suite_hash=? AND attempt_key=?))""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    candidate_id,
                    str(evaluation["suite_hash"]),
                    str(evaluation["attempt_key"]),
                ),
            ).fetchone()
            expected_run = {
                "evaluation_id": evaluation_id,
                "candidate_id": candidate_id,
                "candidate_mode": candidate_mode,
                "suite_hash": str(evaluation["suite_hash"]),
                "attempt_key": str(evaluation["attempt_key"]),
                "baseline_kind": baseline_kind,
                "old_snapshot_hash": str(
                    evaluation["old_snapshot_hash"]
                ),
                "candidate_snapshot_hash": str(
                    evaluation["candidate_snapshot_hash"]
                ),
                "source_owner_key": evaluation.get("source_owner_key"),
                "source_scope": evaluation.get("source_scope"),
                "source_scope_key": evaluation.get("source_scope_key"),
                "source_pack_id": evaluation.get("source_pack_id"),
                "source_version": evaluation.get("source_version"),
                "source_manifest_hash": evaluation.get(
                    "source_manifest_hash"
                ),
                "source_binding_generation": evaluation.get(
                    "source_binding_generation"
                ),
                "absent_baseline_ref": evaluation.get(
                    "absent_baseline_ref"
                ),
                "absent_baseline_hash": evaluation.get(
                    "absent_baseline_hash"
                ),
                "runner_id": str(evaluation["runner_id"]),
                "runner_policy_hash": str(
                    evaluation["runner_policy_hash"]
                ),
                "provider_id": str(evaluation["provider_id"]),
                "model_id": str(evaluation["model_id"]),
            }
            if existing is not None:
                if not self._same(existing, expected_run):
                    raise CompanionConflictError(
                        "evaluation_experiment_conflict"
                    )
                stored_inputs = db.execute(
                    """SELECT * FROM evaluation_case_inputs
                       WHERE profile_id=? AND profile_generation=?
                         AND evaluation_id=? ORDER BY input_id""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        evaluation_id,
                    ),
                ).fetchall()
                stored_cases = db.execute(
                    """SELECT * FROM evaluation_cases
                       WHERE profile_id=? AND profile_generation=?
                         AND evaluation_id=? ORDER BY case_id,variant""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        evaluation_id,
                    ),
                ).fetchall()
                expected_inputs = {
                    str(item["input_id"]): {
                        "case_id": item["case_id"],
                        "source_kind": item["source_kind"],
                        "resource_ref": item["resource_ref"],
                        "resource_hash": item["resource_hash"],
                        "source_event_refs_json": item[
                            "source_event_refs_json"
                        ],
                        "input_envelope_blob": item[
                            "input_envelope_blob"
                        ],
                        "input_hash": item["input_hash"],
                        "adapter_id": item["adapter_id"],
                        "adapter_version": item["adapter_version"],
                        "adapter_build_fingerprint": item[
                            "adapter_build_fingerprint"
                        ],
                        "assertion_ref": item["assertion_ref"],
                        "assertion_hash": item["assertion_hash"],
                        "read_tool_fixture_blob": item[
                            "read_tool_fixture_blob"
                        ],
                        "read_tool_fixture_root_hash": item[
                            "read_tool_fixture_root_hash"
                        ],
                        "evaluation_tool_adapter_map_json": item[
                            "evaluation_tool_adapter_map_json"
                        ],
                    }
                    for item in normalized_inputs
                }
                expected_cases = {
                    (str(item["case_id"]), str(item["variant"])): {
                        "input_id": item["input_id"],
                        "input_hash": item["input_hash"],
                        "manifest_case_version": item[
                            "manifest_case_version"
                        ],
                        "blind_label": item["blind_label"],
                        "expected_kind": item["expected_kind"],
                    }
                    for item in normalized_cases
                }
                if (
                    len(stored_inputs) != len(expected_inputs)
                    or any(
                        str(row["input_id"]) not in expected_inputs
                        or not self._same(
                            row, expected_inputs[str(row["input_id"])]
                        )
                        for row in stored_inputs
                    )
                    or len(stored_cases) != len(expected_cases)
                    or any(
                        (
                            str(row["case_id"]),
                            str(row["variant"]),
                        )
                        not in expected_cases
                        or not self._same(
                            row,
                            expected_cases[
                                (
                                    str(row["case_id"]),
                                    str(row["variant"]),
                                )
                            ],
                        )
                        for row in stored_cases
                    )
                ):
                    raise CompanionConflictError(
                        "evaluation_experiment_case_conflict"
                    )
                return {
                    "evaluation": dict(existing),
                    "input_count": len(stored_inputs),
                    "case_count": len(stored_cases),
                }
            if str(candidate["status"]) != "proposed":
                raise CompanionStateError(
                    "evaluation_candidate_not_proposed"
                )
            db.execute(
                """INSERT INTO evaluation_runs(
                     profile_id,profile_generation,evaluation_id,candidate_id,
                     candidate_mode,suite_hash,attempt_key,baseline_kind,
                     old_snapshot_hash,candidate_snapshot_hash,
                     source_owner_key,source_scope,source_scope_key,
                     source_pack_id,source_version,source_manifest_hash,
                     source_binding_generation,absent_baseline_ref,
                     absent_baseline_hash,runner_id,runner_policy_hash,
                     provider_id,model_id,status,claim_epoch,reason_code,
                     schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                             'queued',0,?,1,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    *expected_run.values(),
                    reason_code,
                    now,
                    now,
                ),
            )
            for item in normalized_inputs:
                db.execute(
                    """INSERT INTO evaluation_case_inputs(
                         profile_id,profile_generation,evaluation_id,input_id,
                         case_id,source_kind,resource_ref,resource_hash,
                         source_event_refs_json,input_envelope_blob,input_hash,
                         adapter_id,adapter_version,adapter_build_fingerprint,
                         assertion_ref,assertion_hash,read_tool_fixture_blob,
                         read_tool_fixture_root_hash,
                         evaluation_tool_adapter_map_json,content_state,
                         reason_code,schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                                 'live','evaluation_input_frozen',1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        evaluation_id,
                        item["input_id"],
                        item["case_id"],
                        item["source_kind"],
                        item["resource_ref"],
                        item["resource_hash"],
                        item["source_event_refs_json"],
                        item["input_envelope_blob"],
                        item["input_hash"],
                        item["adapter_id"],
                        item["adapter_version"],
                        item["adapter_build_fingerprint"],
                        item["assertion_ref"],
                        item["assertion_hash"],
                        item["read_tool_fixture_blob"],
                        item["read_tool_fixture_root_hash"],
                        item["evaluation_tool_adapter_map_json"],
                        now,
                        now,
                    ),
                )
            for item in normalized_cases:
                db.execute(
                    """INSERT INTO evaluation_cases(
                         profile_id,profile_generation,evaluation_id,case_id,
                         variant,input_id,input_hash,manifest_case_version,
                         blind_label,expected_kind,status,claim_epoch,attempt,
                         recovery_only,reason_code,schema_version,
                         created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,'queued',0,0,0,
                                 'evaluation_case_queued',1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        evaluation_id,
                        item["case_id"],
                        item["variant"],
                        item["input_id"],
                        item["input_hash"],
                        item["manifest_case_version"],
                        item["blind_label"],
                        item["expected_kind"],
                        now,
                        now,
                    ),
                )
            updated = db.execute(
                """UPDATE candidate_artifacts
                   SET status='evaluating',
                       reason_code='evaluation_experiment_admitted',
                       updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND candidate_id=? AND status='proposed'""",
                (
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                ),
            )
            if updated.rowcount != 1:
                raise CompanionConflictError(
                    "evaluation_candidate_admission_lost"
                )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    [
                        "evaluation_experiment",
                        evaluation_id,
                        candidate_id,
                        str(evaluation["suite_hash"]),
                        sorted(
                            item["input_hash"]
                            for item in normalized_inputs
                        ),
                    ]
                ),
                now=now,
            )
            row = db.execute(
                """SELECT * FROM evaluation_runs
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                ),
            ).fetchone()
            assert row is not None
            return {
                "evaluation": dict(row),
                "input_count": len(normalized_inputs),
                "case_count": len(normalized_cases),
            }

    def create_evaluation_authorization(
        self,
        owner: OwnerRef,
        *,
        authorization_id: str,
        nonce: str,
        evaluation_id: str,
        candidate_id: str,
        package_hash: str,
        suite_hash: str,
        runner_policy_hash: str,
        expires_at: str,
        actor: str,
        risk_ack: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Freeze a one-shot user authorization for evaluation only."""

        if actor != "user" or risk_ack != "no_os_sandbox":
            raise CompanionStateError(
                "evaluation_authorization_acknowledgement_invalid"
            )
        now = self._now()
        expected = {
            "nonce": nonce,
            "candidate_id": candidate_id,
            "package_hash": package_hash,
            "suite_hash": suite_hash,
            "runner_policy_hash": runner_policy_hash,
            "expires_at": expires_at,
            "actor": actor,
            "risk_ack": risk_ack,
            "reason_code": reason_code,
            "schema_version": 1,
        }
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM evaluation_authorizations
                   WHERE profile_id=? AND profile_generation=?
                     AND (authorization_id=? OR nonce=?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    authorization_id,
                    nonce,
                ),
            ).fetchone()
            if existing is not None:
                if (
                    existing["authorization_id"] != authorization_id
                    or not self._same(existing, expected)
                ):
                    raise CompanionConflictError(
                        "evaluation_authorization_conflict"
                    )
                return dict(existing)
            evaluation = db.execute(
                """SELECT e.candidate_id,e.suite_hash,e.runner_policy_hash,
                          p.candidate_package_hash,a.status
                   FROM evaluation_runs e
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id
                    AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   WHERE e.profile_id=? AND e.profile_generation=?
                     AND e.evaluation_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                ),
            ).fetchone()
            if (
                evaluation is None
                or evaluation["candidate_id"] != candidate_id
                or evaluation["candidate_package_hash"] != package_hash
                or evaluation["suite_hash"] != suite_hash
                or evaluation["runner_policy_hash"] != runner_policy_hash
                or evaluation["status"] in {"invalidated", "expired", "stale"}
                or expires_at <= now
            ):
                raise CompanionStateError(
                    "evaluation_authorization_fence_invalid"
                )
            db.execute(
                """INSERT INTO evaluation_authorizations(
                     profile_id,profile_generation,authorization_id,nonce,
                     candidate_id,package_hash,suite_hash,runner_policy_hash,
                     expires_at,consumed_at,actor,risk_ack,reason_code,
                     schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    authorization_id,
                    nonce,
                    candidate_id,
                    package_hash,
                    suite_hash,
                    runner_policy_hash,
                    expires_at,
                    actor,
                    risk_ack,
                    reason_code,
                    1,
                    now,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_authorizations
                       WHERE profile_id=? AND profile_generation=?
                         AND authorization_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        authorization_id,
                    ),
                ).fetchone()
            )

    def record_risk_assessment(
        self,
        owner: OwnerRef,
        *,
        risk_id: str,
        candidate_id: str,
        candidate_package_hash: str,
        static_preflight: Mapping[str, Any],
        effect_topology_diff: Mapping[str, Any],
        risk: str,
        risk_hash: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Freeze deterministic preflight/risk facts before permit issuance."""

        if risk not in {"low", "medium", "high", "unknown"}:
            raise ValueError("risk assessment level invalid")
        now = self._now()
        preflight_json = canonical_json(dict(static_preflight))
        diff_json = canonical_json(dict(effect_topology_diff))
        expected = {
            "candidate_id": candidate_id,
            "candidate_package_hash": candidate_package_hash,
            "static_preflight_json": preflight_json,
            "effect_topology_diff_json": diff_json,
            "risk": risk,
            "risk_hash": risk_hash,
            "reason_code": reason_code,
            "schema_version": 1,
        }
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM risk_assessments
                   WHERE profile_id=? AND profile_generation=?
                     AND (risk_id=? OR
                          (candidate_id=? AND candidate_package_hash=?))""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    risk_id,
                    candidate_id,
                    candidate_package_hash,
                ),
            ).fetchone()
            if existing is not None:
                if existing["risk_id"] != risk_id or not self._same(
                    existing, expected
                ):
                    raise CompanionConflictError("risk_assessment_conflict")
                return dict(existing)
            candidate = db.execute(
                """SELECT a.status,p.candidate_package_hash,p.content_state
                   FROM candidate_artifacts a
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   WHERE a.profile_id=? AND a.profile_generation=?
                     AND a.candidate_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                ),
            ).fetchone()
            if (
                candidate is None
                or candidate["candidate_package_hash"]
                != candidate_package_hash
                or candidate["content_state"] != "live"
                or candidate["status"] in {"invalidated", "expired", "stale"}
            ):
                raise CompanionStateError("risk_candidate_fence_invalid")
            db.execute(
                """INSERT INTO risk_assessments(
                     profile_id,profile_generation,risk_id,candidate_id,
                     candidate_package_hash,static_preflight_json,
                     effect_topology_diff_json,risk,risk_hash,reason_code,
                     schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    risk_id,
                    candidate_id,
                    candidate_package_hash,
                    preflight_json,
                    diff_json,
                    risk,
                    risk_hash,
                    reason_code,
                    now,
                ),
            )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(
                    ["risk_assessment", risk_id, risk_hash]
                ),
                now=now,
            )
            return dict(
                db.execute(
                    """SELECT * FROM risk_assessments
                       WHERE profile_id=? AND profile_generation=? AND risk_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        risk_id,
                    ),
                ).fetchone()
            )

    def issue_evaluation_execution_permit(
        self,
        owner: OwnerRef,
        permit: EvaluationExecutionPermit,
    ) -> Mapping[str, Any]:
        if permit.mode not in {
            "safe_auto",
            "safe_static",
            "user_authorized",
        }:
            raise ValueError("evaluation_permit_mode_invalid")
        if (
            permit.mode in {"safe_auto", "safe_static"}
        ) != (permit.authorization_id is None):
            raise CompanionStateError("evaluation_permit_authorization_mode_mismatch")
        permit_facts = {
            "schema_version": permit.schema_version,
            "evaluation_id": permit.evaluation_id,
            "mode": permit.mode,
            "candidate_id": permit.candidate_id,
            "package_hash": permit.package_hash,
            "manifest_hash": permit.manifest_hash,
            "archive_hash": permit.archive_hash,
            "suite_hash": permit.suite_hash,
            "runner_policy_hash": permit.runner_policy_hash,
            "issued_revocation_epoch": permit.issued_revocation_epoch,
            "preflight_ref": permit.preflight_ref,
            "preflight_hash": permit.preflight_hash,
            "risk_ref": permit.risk_ref,
            "risk_hash": permit.risk_hash,
            "authorization_id": permit.authorization_id,
        }
        if canonical_hash(permit_facts) != permit.permit_hash:
            raise CompanionConflictError("evaluation_permit_hash_mismatch")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            evaluation = db.execute(
                """SELECT e.*,p.candidate_package_hash,p.candidate_manifest_hash,p.archive_hash
                   FROM evaluation_runs e
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   WHERE e.profile_id=? AND e.profile_generation=? AND e.evaluation_id=?""",
                (owner.profile_id, owner.profile_generation, permit.evaluation_id),
            ).fetchone()
            if evaluation is None:
                raise CompanionStateError("evaluation_missing")
            if evaluation["status"] not in {"queued", "running"}:
                raise CompanionStateError("evaluation_terminal")
            expected = {
                "evaluation_id": permit.evaluation_id,
                "mode": permit.mode,
                "candidate_id": permit.candidate_id,
                "package_hash": permit.package_hash,
                "manifest_hash": permit.manifest_hash,
                "archive_hash": permit.archive_hash,
                "suite_hash": permit.suite_hash,
                "runner_policy_hash": permit.runner_policy_hash,
                "issued_revocation_epoch": permit.issued_revocation_epoch,
                "preflight_ref": permit.preflight_ref,
                "preflight_hash": permit.preflight_hash,
                "risk_ref": permit.risk_ref,
                "risk_hash": permit.risk_hash,
                "authorization_id": permit.authorization_id,
                "permit_hash": permit.permit_hash,
                "schema_version": permit.schema_version,
            }
            existing = db.execute(
                """SELECT * FROM evaluation_execution_permits
                   WHERE profile_id=? AND profile_generation=?
                     AND (permit_id=? OR evaluation_id=?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    permit.permit_id,
                    permit.evaluation_id,
                ),
            ).fetchone()
            if existing is not None:
                if existing["permit_id"] != permit.permit_id or not self._same(existing, expected):
                    raise CompanionConflictError("evaluation_permit_conflict")
                return dict(existing)
            if (
                evaluation["candidate_id"] != permit.candidate_id
                or evaluation["candidate_package_hash"] != permit.package_hash
                or evaluation["candidate_manifest_hash"] != permit.manifest_hash
                or evaluation["archive_hash"] != permit.archive_hash
                or evaluation["suite_hash"] != permit.suite_hash
                or evaluation["runner_policy_hash"] != permit.runner_policy_hash
            ):
                raise CompanionConflictError("evaluation_permit_fence_mismatch")
            risk = db.execute(
                """SELECT risk_id,risk_hash,risk,static_preflight_json
                   FROM risk_assessments
                   WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                     AND risk_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    permit.candidate_id,
                    permit.risk_ref,
                ),
            ).fetchone()
            safe_static_valid = False
            if risk is not None and permit.mode == "safe_static":
                try:
                    preflight = json.loads(str(risk["static_preflight_json"]))
                except json.JSONDecodeError as exc:
                    raise CompanionStateError(
                        "evaluation_permit_risk_invalid"
                    ) from exc
                safe_static_valid = (
                    isinstance(preflight, dict)
                    and preflight.get("candidate_kind") == "instruction"
                    and risk["risk"] != "low"
                    and permit.runner_policy_hash
                    == SAFE_STATIC_RUNNER_POLICY_HASH
                )
            if (
                risk is None
                or risk["risk_hash"] != permit.risk_hash
                or (permit.mode == "safe_auto" and risk["risk"] != "low")
                or (permit.mode == "safe_static" and not safe_static_valid)
            ):
                raise CompanionStateError("evaluation_permit_risk_invalid")
            if permit.mode == "user_authorized":
                authorization = db.execute(
                    """SELECT * FROM evaluation_authorizations
                       WHERE profile_id=? AND profile_generation=? AND authorization_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        permit.authorization_id,
                    ),
                ).fetchone()
                if (
                    authorization is None
                    or authorization["candidate_id"] != permit.candidate_id
                    or authorization["package_hash"] != permit.package_hash
                    or authorization["suite_hash"] != permit.suite_hash
                    or authorization["runner_policy_hash"] != permit.runner_policy_hash
                    or authorization["consumed_at"] is not None
                    or authorization["expires_at"] <= now
                ):
                    raise CompanionStateError("evaluation_authorization_invalid")
                db.execute(
                    """UPDATE evaluation_authorizations SET consumed_at=?
                       WHERE profile_id=? AND profile_generation=? AND authorization_id=?
                         AND consumed_at IS NULL""",
                    (
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        permit.authorization_id,
                    ),
                )
            db.execute(
                """INSERT INTO evaluation_execution_permits(
                     profile_id,profile_generation,permit_id,evaluation_id,mode,candidate_id,
                     package_hash,manifest_hash,archive_hash,suite_hash,runner_policy_hash,
                     issued_revocation_epoch,preflight_ref,preflight_hash,risk_ref,risk_hash,
                     authorization_id,status,permit_hash,reason_code,schema_version,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'issued',?,?,?,?,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    permit.permit_id,
                    permit.evaluation_id,
                    permit.mode,
                    permit.candidate_id,
                    permit.package_hash,
                    permit.manifest_hash,
                    permit.archive_hash,
                    permit.suite_hash,
                    permit.runner_policy_hash,
                    permit.issued_revocation_epoch,
                    permit.preflight_ref,
                    permit.preflight_hash,
                    permit.risk_ref,
                    permit.risk_hash,
                    permit.authorization_id,
                    permit.permit_hash,
                    permit.reason_code,
                    permit.schema_version,
                    now,
                    now,
                ),
            )
            db.execute(
                """UPDATE evaluation_runs
                   SET execution_permit_ref=?,execution_permit_hash=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND evaluation_id=?""",
                (
                    permit.permit_id,
                    permit.permit_hash,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    permit.evaluation_id,
                ),
            )
            db.execute(
                """UPDATE candidate_artifacts
                   SET status='evaluating',reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                     AND status IN ('proposed','awaiting_eval_authorization')""",
                (
                    (
                        "safe_auto_evaluation_permit_issued"
                        if permit.mode == "safe_auto"
                        else "safe_static_evaluation_permit_issued"
                        if permit.mode == "safe_static"
                        else "user_authorized_evaluation_permit_issued"
                    ),
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    permit.candidate_id,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_execution_permits
                       WHERE profile_id=? AND profile_generation=? AND permit_id=?""",
                    (owner.profile_id, owner.profile_generation, permit.permit_id),
                ).fetchone()
            )

    def claim_evaluation_case(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_owner: str,
        lease_seconds: float,
    ) -> Mapping[str, Any]:
        now_dt = datetime.fromisoformat(self._now().replace("Z", "+00:00"))
        now = now_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        expiry = (now_dt + timedelta(seconds=lease_seconds)).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT c.*,i.content_state,e.status AS evaluation_status,
                          a.status AS candidate_status,p.status AS permit_status,
                          p.permit_id,p.permit_hash
                   FROM evaluation_cases c
                   JOIN evaluation_case_inputs i
                     ON i.profile_id=c.profile_id AND i.profile_generation=c.profile_generation
                    AND i.evaluation_id=c.evaluation_id AND i.input_id=c.input_id
                   JOIN evaluation_runs e
                     ON e.profile_id=c.profile_id AND e.profile_generation=c.profile_generation
                    AND e.evaluation_id=c.evaluation_id
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN evaluation_execution_permits p
                     ON p.profile_id=e.profile_id AND p.profile_generation=e.profile_generation
                    AND p.evaluation_id=e.evaluation_id
                   WHERE c.profile_id=? AND c.profile_generation=? AND c.evaluation_id=?
                     AND c.case_id=? AND c.variant=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("evaluation_case_missing")
            if (
                row["content_state"] != "live"
                or row["evaluation_status"] not in {"queued", "running"}
                or row["candidate_status"] in {"invalidated", "expired", "stale"}
                or row["permit_status"] not in {"issued", "claimed"}
            ):
                raise CompanionStateError("evaluation_case_fence_closed")
            expired_lease = (
                row["status"] == "leased"
                and row["lease_expires_at"] is not None
                and row["lease_expires_at"] <= now
            )
            if row["status"] != "queued" and not expired_lease:
                raise CompanionLeaseError("evaluation_case_not_claimable")
            epoch = int(row["claim_epoch"]) + 1
            attempt = int(row["attempt"]) + 1
            recovery_only = int(expired_lease)
            db.execute(
                """UPDATE evaluation_cases
                   SET status='leased',claim_owner=?,claim_epoch=?,lease_expires_at=?,
                       attempt=?,recovery_only=?,reason_code='case_claimed',updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND evaluation_id=?
                     AND case_id=? AND variant=? AND claim_epoch=?""",
                (
                    claim_owner,
                    epoch,
                    expiry,
                    attempt,
                    recovery_only,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    row["claim_epoch"],
                ),
            )
            db.execute(
                """UPDATE evaluation_execution_permits SET status='claimed',updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND permit_id=? AND status='issued'""",
                (now, owner.profile_id, owner.profile_generation, row["permit_id"]),
            )
            db.execute(
                """UPDATE evaluation_runs SET status='running',updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND evaluation_id=? AND status='queued'""",
                (now, owner.profile_id, owner.profile_generation, evaluation_id),
            )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_cases
                       WHERE profile_id=? AND profile_generation=? AND evaluation_id=?
                         AND case_id=? AND variant=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        evaluation_id,
                        case_id,
                        variant,
                    ),
                ).fetchone()
            )

    def get_evaluation_execution_fence(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
    ) -> Mapping[str, Any]:
        """Return one joined, owner-scoped snapshot for a short start fence."""

        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT
                     e.evaluation_id,e.status AS evaluation_status,e.candidate_id,
                     e.suite_hash,e.runner_policy_hash,
                     a.status AS candidate_status,
                     p.candidate_package_hash AS package_hash,
                     p.candidate_manifest_hash AS manifest_hash,
                     p.archive_hash,
                     x.permit_id,x.mode AS permit_mode,x.permit_hash,
                     x.status AS permit_status,x.issued_revocation_epoch,
                     x.preflight_ref,x.preflight_hash,x.risk_ref,x.risk_hash,
                     x.authorization_id,
                     r.risk AS risk_level,
                     CASE
                       WHEN x.risk_hash=r.risk_hash
                        AND r.candidate_package_hash=p.candidate_package_hash
                        AND (
                          (x.mode='safe_auto' AND r.risk='low'
                           AND x.authorization_id IS NULL)
                          OR
                          (x.mode='safe_static' AND r.risk<>'low'
                           AND x.authorization_id IS NULL
                           AND e.runner_policy_hash=
                             '557ea1903de386bd8e029119bc6adc0e144e5b44b5fdffad4374766d1fe7319c'
                           AND json_extract(
                             r.static_preflight_json,'$.candidate_kind'
                           )='instruction')
                          OR
                          (x.mode='user_authorized' AND x.authorization_id IS NOT NULL
                           AND z.consumed_at IS NOT NULL
                           AND z.candidate_id=e.candidate_id
                           AND z.package_hash=p.candidate_package_hash
                           AND z.suite_hash=e.suite_hash
                           AND z.runner_policy_hash=e.runner_policy_hash)
                        )
                       THEN 1 ELSE 0
                     END AS policy_verified,
                     CASE WHEN z.consumed_at IS NOT NULL THEN 1 ELSE 0 END
                       AS authorization_consumed,
                     c.case_id,c.variant,c.status AS case_status,c.claim_owner,
                     c.claim_epoch,c.attempt AS attempt_ordinal,c.recovery_only,
                     c.input_hash,i.content_state AS input_content_state,
                     i.adapter_id,i.adapter_version,
                     i.adapter_build_fingerprint AS adapter_fingerprint
                   FROM evaluation_runs e
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id
                    AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   JOIN evaluation_execution_permits x
                     ON x.profile_id=e.profile_id
                    AND x.profile_generation=e.profile_generation
                    AND x.evaluation_id=e.evaluation_id
                   JOIN risk_assessments r
                     ON r.profile_id=e.profile_id
                    AND r.profile_generation=e.profile_generation
                    AND r.risk_id=x.risk_ref
                    AND r.candidate_id=e.candidate_id
                   LEFT JOIN evaluation_authorizations z
                     ON z.profile_id=x.profile_id
                    AND z.profile_generation=x.profile_generation
                    AND z.authorization_id=x.authorization_id
                   JOIN evaluation_cases c
                     ON c.profile_id=e.profile_id
                    AND c.profile_generation=e.profile_generation
                    AND c.evaluation_id=e.evaluation_id
                   JOIN evaluation_case_inputs i
                     ON i.profile_id=c.profile_id
                    AND i.profile_generation=c.profile_generation
                    AND i.evaluation_id=c.evaluation_id
                    AND i.input_id=c.input_id
                   WHERE e.profile_id=? AND e.profile_generation=?
                     AND e.evaluation_id=? AND c.case_id=? AND c.variant=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("evaluation_execution_fence_missing")
            return dict(row)

    def create_evaluation_case_launch(
        self,
        owner: OwnerRef,
        launch: EvaluationCaseLaunch,
        *,
        claim_owner: str,
    ) -> Mapping[str, Any]:
        now = self._now()
        launch_facts = {
            "schema_version": launch.schema_version,
            "launch_id": launch.launch_id,
            "evaluation_id": launch.evaluation_id,
            "case_id": launch.case_id,
            "variant": launch.variant,
            "attempt_ordinal": launch.attempt_ordinal,
            "candidate_package_hash": launch.candidate_package_hash,
            "candidate_manifest_hash": launch.candidate_manifest_hash,
            "candidate_archive_hash": launch.candidate_archive_hash,
            "suite_hash": launch.suite_hash,
            "permit_mode": launch.permit_mode,
            "permit_id": launch.permit_id,
            "permit_hash": launch.permit_hash,
            "case_lease_epoch": launch.case_lease_epoch,
            "revocation_epoch": launch.revocation_epoch,
            "adapter_id": launch.adapter_id,
            "adapter_version": launch.adapter_version,
            "adapter_fingerprint": launch.adapter_fingerprint,
        }
        if canonical_hash(launch_facts) != launch.launch_fingerprint:
            raise CompanionConflictError("evaluation_launch_fingerprint_mismatch")
        expected = {
            "evaluation_id": launch.evaluation_id,
            "case_id": launch.case_id,
            "variant": launch.variant,
            "attempt_ordinal": launch.attempt_ordinal,
            "candidate_package_hash": launch.candidate_package_hash,
            "candidate_manifest_hash": launch.candidate_manifest_hash,
            "candidate_archive_hash": launch.candidate_archive_hash,
            "suite_hash": launch.suite_hash,
            "permit_mode": launch.permit_mode,
            "permit_id": launch.permit_id,
            "permit_hash": launch.permit_hash,
            "case_lease_epoch": launch.case_lease_epoch,
            "revocation_epoch": launch.revocation_epoch,
            "adapter_id": launch.adapter_id,
            "adapter_version": launch.adapter_version,
            "adapter_fingerprint": launch.adapter_fingerprint,
            "launch_fingerprint": launch.launch_fingerprint,
            "schema_version": launch.schema_version,
        }
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM evaluation_case_launches
                   WHERE profile_id=? AND profile_generation=?
                     AND (launch_id=? OR
                          (evaluation_id=? AND case_id=? AND variant=? AND attempt_ordinal=?))""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    launch.launch_id,
                    launch.evaluation_id,
                    launch.case_id,
                    launch.variant,
                    launch.attempt_ordinal,
                ),
            ).fetchone()
            if existing is not None:
                if existing["launch_id"] != launch.launch_id or not self._same(existing, expected):
                    raise CompanionConflictError("evaluation_launch_conflict")
                return dict(existing)
            row = db.execute(
                """SELECT c.*,e.suite_hash,a.status AS candidate_status,
                          p.candidate_package_hash,p.candidate_manifest_hash,p.archive_hash,
                          x.mode AS permit_mode,x.permit_hash,x.issued_revocation_epoch,
                          x.status AS permit_status
                   FROM evaluation_cases c
                   JOIN evaluation_runs e
                     ON e.profile_id=c.profile_id AND e.profile_generation=c.profile_generation
                    AND e.evaluation_id=c.evaluation_id
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   JOIN evaluation_execution_permits x
                     ON x.profile_id=e.profile_id AND x.profile_generation=e.profile_generation
                    AND x.evaluation_id=e.evaluation_id
                   WHERE c.profile_id=? AND c.profile_generation=? AND c.evaluation_id=?
                     AND c.case_id=? AND c.variant=? AND x.permit_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    launch.evaluation_id,
                    launch.case_id,
                    launch.variant,
                    launch.permit_id,
                ),
            ).fetchone()
            if (
                row is None
                or row["status"] != "leased"
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != launch.case_lease_epoch
            ):
                raise CompanionLeaseError("evaluation_case_lease_stale")
            if int(row["recovery_only"]):
                raise CompanionStateError("evaluation_case_recovery_only")
            if launch.attempt_ordinal != int(row["attempt"]):
                raise CompanionConflictError("evaluation_launch_attempt_mismatch")
            if (
                row["candidate_status"] in {"invalidated", "expired", "stale"}
                or row["permit_status"] not in {"issued", "claimed"}
                or row["candidate_package_hash"] != launch.candidate_package_hash
                or row["candidate_manifest_hash"] != launch.candidate_manifest_hash
                or row["archive_hash"] != launch.candidate_archive_hash
                or row["suite_hash"] != launch.suite_hash
                or row["permit_mode"] != launch.permit_mode
                or row["permit_hash"] != launch.permit_hash
                or int(row["issued_revocation_epoch"]) != launch.revocation_epoch
            ):
                raise CompanionStateError("evaluation_launch_fence_mismatch")
            try:
                db.execute(
                    """INSERT INTO evaluation_case_launches(
                         profile_id,profile_generation,evaluation_id,case_id,variant,attempt_ordinal,
                         launch_id,candidate_package_hash,candidate_manifest_hash,
                         candidate_archive_hash,suite_hash,permit_mode,permit_id,permit_hash,
                         case_lease_epoch,revocation_epoch,adapter_id,adapter_version,
                         adapter_fingerprint,launch_fingerprint,status,reason_code,schema_version,
                         created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'claimed',?,?,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        launch.evaluation_id,
                        launch.case_id,
                        launch.variant,
                        launch.attempt_ordinal,
                        launch.launch_id,
                        launch.candidate_package_hash,
                        launch.candidate_manifest_hash,
                        launch.candidate_archive_hash,
                        launch.suite_hash,
                        launch.permit_mode,
                        launch.permit_id,
                        launch.permit_hash,
                        launch.case_lease_epoch,
                        launch.revocation_epoch,
                        launch.adapter_id,
                        launch.adapter_version,
                        launch.adapter_fingerprint,
                        launch.launch_fingerprint,
                        launch.reason_code,
                        launch.schema_version,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise CompanionStateError(str(exc)) from exc
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_case_launches
                       WHERE profile_id=? AND profile_generation=? AND launch_id=?""",
                    (owner.profile_id, owner.profile_generation, launch.launch_id),
                ).fetchone()
            )

    def record_evaluation_case_start_ack(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_owner: str,
        claim_epoch: int,
        ack: Any,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Persist exact Job/process start identity before releasing the fence."""

        now = self._now()
        ack_facts = {
            "schema_version": 1,
            "launch_id": ack.launch_id,
            "runtime_instance_id": ack.runtime_instance_id,
            "adapter_identity": ack.adapter_identity,
            "job_identity": ack.job_identity,
            "pid": ack.pid,
            "process_create_time": ack.process_create_time,
            "command_line_hash": ack.command_line_hash,
        }
        if canonical_hash(ack_facts) != ack.start_receipt_hash:
            raise CompanionConflictError("evaluation_start_ack_hash_mismatch")
        runtime_identity = canonical_json(
            {
                "runtime_instance_id": ack.runtime_instance_id,
                "adapter_identity": ack.adapter_identity,
                "pid": ack.pid,
                "process_create_time": ack.process_create_time,
                "command_line_hash": ack.command_line_hash,
            }
        )
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT l.*,c.status AS case_status,c.claim_owner,c.claim_epoch
                   FROM evaluation_case_launches l
                   JOIN evaluation_cases c
                     ON c.profile_id=l.profile_id
                    AND c.profile_generation=l.profile_generation
                    AND c.evaluation_id=l.evaluation_id
                    AND c.case_id=l.case_id AND c.variant=l.variant
                   WHERE l.profile_id=? AND l.profile_generation=?
                     AND l.evaluation_id=? AND l.case_id=? AND l.variant=?
                     AND l.launch_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    ack.launch_id,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("evaluation_launch_missing")
            if row["status"] == "started":
                if (
                    row["job_identity"] != ack.job_identity
                    or row["runtime_identity"] != runtime_identity
                    or row["outcome_hash"] != ack.start_receipt_hash
                ):
                    raise CompanionConflictError(
                        "evaluation_start_ack_conflict"
                    )
                return dict(row)
            if (
                row["status"] != "claimed"
                or row["case_status"] != "leased"
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != claim_epoch
                or int(row["case_lease_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError("evaluation_start_ack_fence_stale")
            db.execute(
                """UPDATE evaluation_case_launches
                   SET status='started',start_outcome='started',
                       started_ack_at=?,job_identity=?,runtime_identity=?,
                       outcome_hash=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND launch_id=?
                     AND status='claimed'""",
                (
                    now,
                    ack.job_identity,
                    runtime_identity,
                    ack.start_receipt_hash,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    ack.launch_id,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_case_launches
                       WHERE profile_id=? AND profile_generation=? AND launch_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        ack.launch_id,
                    ),
                ).fetchone()
            )

    def commit_evaluation_case_completion(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_owner: str,
        claim_epoch: int,
        launch_id: str,
        completion: Any,
    ) -> Mapping[str, Any]:
        """Atomically settle launch, result, and case terminal state."""

        if (
            completion.cleanup_receipt_hash is None
            or completion.survivor_count != 0
        ):
            raise CompanionStateError(
                "evaluation_completion_cleanup_not_proven"
            )
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM evaluation_results
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=? AND case_id=? AND variant=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                ),
            ).fetchone()
            if existing is not None:
                if existing["result_hash"] != completion.result_hash:
                    raise CompanionConflictError(
                        "evaluation_result_conflict"
                    )
                return dict(existing)
            row = db.execute(
                """SELECT l.status AS launch_status,c.status AS case_status,
                          c.claim_owner,c.claim_epoch
                   FROM evaluation_case_launches l
                   JOIN evaluation_cases c
                     ON c.profile_id=l.profile_id
                    AND c.profile_generation=l.profile_generation
                    AND c.evaluation_id=l.evaluation_id
                    AND c.case_id=l.case_id AND c.variant=l.variant
                   WHERE l.profile_id=? AND l.profile_generation=?
                     AND l.evaluation_id=? AND l.case_id=? AND l.variant=?
                     AND l.launch_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    launch_id,
                ),
            ).fetchone()
            if (
                row is None
                or row["launch_status"] != "started"
                or row["case_status"] != "leased"
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError(
                    "evaluation_completion_fence_stale"
                )
            db.execute(
                """INSERT INTO evaluation_results(
                     profile_id,profile_generation,evaluation_id,case_id,variant,
                     assertions_json,judge_result_json,usage_json,result_hash,
                     reason_code,schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    canonical_json(dict(completion.assertions)),
                    canonical_json(dict(completion.judge_result)),
                    canonical_json(dict(completion.usage)),
                    completion.result_hash,
                    completion.reason_code,
                    now,
                ),
            )
            db.execute(
                """UPDATE evaluation_case_launches
                   SET status='completed',outcome_ref=?,outcome_hash=?,
                       cleanup_receipt_hash=?,survivor_count=0,
                       reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND launch_id=?
                     AND status='started'""",
                (
                    completion.outcome_ref,
                    completion.outcome_hash,
                    completion.cleanup_receipt_hash,
                    completion.reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    launch_id,
                ),
            )
            db.execute(
                """UPDATE evaluation_cases
                   SET status='committed',lease_expires_at=NULL,
                       reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=? AND case_id=? AND variant=?
                     AND claim_owner=? AND claim_epoch=? AND status='leased'""",
                (
                    completion.reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    claim_owner,
                    claim_epoch,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_results
                       WHERE profile_id=? AND profile_generation=?
                         AND evaluation_id=? AND case_id=? AND variant=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        evaluation_id,
                        case_id,
                        variant,
                    ),
                ).fetchone()
            )

    def abort_evaluation_case_execution(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_owner: str,
        claim_epoch: int,
        launch_id: str,
        expected_launch_status: str,
        cleanup_receipt_hash: str | None,
        survivor_count: int | None,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Monotonically make an unknown/started case non-passable."""

        terminal = (
            "aborted"
            if survivor_count == 0 and cleanup_receipt_hash is not None
            else "cleanup_required"
        )
        case_terminal = (
            "inconclusive" if terminal == "aborted" else "cleanup_required"
        )
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT l.*,c.status AS case_status,c.claim_owner,c.claim_epoch
                   FROM evaluation_case_launches l
                   JOIN evaluation_cases c
                     ON c.profile_id=l.profile_id
                    AND c.profile_generation=l.profile_generation
                    AND c.evaluation_id=l.evaluation_id
                    AND c.case_id=l.case_id AND c.variant=l.variant
                   WHERE l.profile_id=? AND l.profile_generation=?
                     AND l.evaluation_id=? AND l.case_id=? AND l.variant=?
                     AND l.launch_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    launch_id,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("evaluation_launch_missing")
            if row["status"] in {"aborted", "cleanup_required"}:
                return dict(row)
            if (
                row["status"] != expected_launch_status
                or row["case_status"] != "leased"
                or row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError("evaluation_abort_fence_stale")
            db.execute(
                """UPDATE evaluation_case_launches
                   SET status=?,cleanup_receipt_hash=?,survivor_count=?,
                       reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND launch_id=?
                     AND status=?""",
                (
                    terminal,
                    cleanup_receipt_hash,
                    survivor_count,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    launch_id,
                    expected_launch_status,
                ),
            )
            db.execute(
                """UPDATE evaluation_cases
                   SET status=?,lease_expires_at=NULL,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=? AND case_id=? AND variant=?
                     AND claim_owner=? AND claim_epoch=? AND status='leased'""",
                (
                    case_terminal,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    claim_owner,
                    claim_epoch,
                ),
            )
            db.execute(
                """UPDATE evaluation_runs
                   SET status='inconclusive',lease_expires_at=NULL,
                       reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=? AND status IN ('queued','running')""",
                (
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_case_launches
                       WHERE profile_id=? AND profile_generation=? AND launch_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        launch_id,
                    ),
                ).fetchone()
            )

    def get_evaluation_case_recovery_state(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_epoch: int,
    ) -> Mapping[str, Any] | None:
        """Expose the previous physical identity; recovery never relaunches it."""

        del claim_epoch
        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT launch_id,status AS launch_status,start_outcome,
                          job_identity,runtime_identity,session_identity,
                          outcome_ref,outcome_hash,cleanup_receipt_hash,
                          survivor_count,reason_code,attempt_ordinal
                   FROM evaluation_case_launches
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=? AND case_id=? AND variant=?
                   ORDER BY attempt_ordinal DESC LIMIT 1""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                ),
            ).fetchone()
            return None if row is None else dict(row)

    def settle_evaluation_case_launch(
        self,
        owner: OwnerRef,
        *,
        launch_id: str,
        expected_status: str,
        status: str,
        outcome_ref: str | None = None,
        outcome_hash: str | None = None,
        cleanup_receipt_hash: str | None = None,
        survivor_count: int | None = None,
        reason_code: str,
    ) -> Mapping[str, Any]:
        allowed = {
            "claimed": {"started", "not_started", "unknown"},
            "started": {"completed", "failed", "unknown", "aborting"},
            "unknown": {"aborting", "cleanup_required"},
            "aborting": {"aborted", "cleanup_required"},
            "cleanup_required": {"aborting"},
        }
        if status not in allowed.get(expected_status, set()):
            raise CompanionStateError("evaluation_launch_transition_invalid")
        if status == "not_started" and (
            cleanup_receipt_hash is None or survivor_count != 0
        ):
            raise CompanionStateError("evaluation_not_started_requires_zero_survivors")
        if status in {"completed", "failed"} and (
            outcome_ref is None or outcome_hash is None
        ):
            raise CompanionStateError("evaluation_launch_outcome_required")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM evaluation_case_launches
                   WHERE profile_id=? AND profile_generation=? AND launch_id=?""",
                (owner.profile_id, owner.profile_generation, launch_id),
            ).fetchone()
            if row is None:
                raise CompanionStateError("evaluation_launch_missing")
            if row["status"] == status:
                if (
                    row["outcome_ref"] != outcome_ref
                    or row["outcome_hash"] != outcome_hash
                    or row["cleanup_receipt_hash"] != cleanup_receipt_hash
                    or row["survivor_count"] != survivor_count
                ):
                    raise CompanionConflictError("evaluation_launch_settle_conflict")
                return dict(row)
            if row["status"] != expected_status:
                raise CompanionConflictError("evaluation_launch_state_conflict")
            db.execute(
                """UPDATE evaluation_case_launches
                   SET status=?,start_outcome=COALESCE(start_outcome,?),
                       started_ack_at=CASE WHEN ?='started' THEN COALESCE(started_ack_at,?) ELSE started_ack_at END,
                       outcome_ref=?,outcome_hash=?,cleanup_receipt_hash=?,survivor_count=?,
                       reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND launch_id=? AND status=?""",
                (
                    status,
                    status,
                    status,
                    now,
                    outcome_ref,
                    outcome_hash,
                    cleanup_receipt_hash,
                    survivor_count,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    launch_id,
                    expected_status,
                ),
            )
            if status == "not_started":
                db.execute(
                    """UPDATE evaluation_cases
                       SET status='queued',claim_owner=NULL,lease_expires_at=NULL,
                           recovery_only=0,next_retry_at=?,reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND evaluation_id=?
                         AND case_id=? AND variant=? AND claim_epoch=?""",
                    (
                        now,
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        row["evaluation_id"],
                        row["case_id"],
                        row["variant"],
                        row["case_lease_epoch"],
                    ),
                )
            elif status in {"unknown", "cleanup_required"}:
                db.execute(
                    """UPDATE evaluation_cases SET status='cleanup_required',
                         reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND evaluation_id=?
                         AND case_id=? AND variant=? AND claim_epoch=?""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        row["evaluation_id"],
                        row["case_id"],
                        row["variant"],
                        row["case_lease_epoch"],
                    ),
                )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_case_launches
                       WHERE profile_id=? AND profile_generation=? AND launch_id=?""",
                    (owner.profile_id, owner.profile_generation, launch_id),
                ).fetchone()
            )

    def record_evaluation_result(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
        case_id: str,
        variant: str,
        claim_owner: str,
        claim_epoch: int,
        assertions: Mapping[str, Any],
        judge_result: Mapping[str, Any],
        usage: Mapping[str, Any],
        result_hash: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        payload = {
            "assertions": dict(assertions),
            "judge_result": dict(judge_result),
            "usage": dict(usage),
        }
        if canonical_hash(payload) != result_hash:
            raise CompanionConflictError("evaluation_result_hash_mismatch")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM evaluation_results
                   WHERE profile_id=? AND profile_generation=? AND evaluation_id=?
                     AND case_id=? AND variant=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                ),
            ).fetchone()
            if existing is not None:
                if existing["result_hash"] != result_hash:
                    raise CompanionConflictError("evaluation_result_conflict")
                return dict(existing)
            case = db.execute(
                """SELECT * FROM evaluation_cases
                   WHERE profile_id=? AND profile_generation=? AND evaluation_id=?
                     AND case_id=? AND variant=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                ),
            ).fetchone()
            launch = db.execute(
                """SELECT * FROM evaluation_case_launches
                   WHERE profile_id=? AND profile_generation=? AND evaluation_id=?
                     AND case_id=? AND variant=? AND case_lease_epoch=?
                     AND status='completed' ORDER BY attempt_ordinal DESC LIMIT 1""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    claim_epoch,
                ),
            ).fetchone()
            if (
                case is None
                or case["status"] != "leased"
                or case["claim_owner"] != claim_owner
                or int(case["claim_epoch"]) != claim_epoch
                or launch is None
            ):
                raise CompanionLeaseError("evaluation_result_lease_or_launch_invalid")
            db.execute(
                """INSERT INTO evaluation_results(
                     profile_id,profile_generation,evaluation_id,case_id,variant,
                     assertions_json,judge_result_json,usage_json,result_hash,reason_code,
                     schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    canonical_json(dict(assertions)),
                    canonical_json(dict(judge_result)),
                    canonical_json(dict(usage)),
                    result_hash,
                    reason_code,
                    now,
                ),
            )
            db.execute(
                """UPDATE evaluation_cases SET status='committed',lease_expires_at=NULL,
                     reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND evaluation_id=?
                     AND case_id=? AND variant=? AND claim_owner=? AND claim_epoch=?""",
                (
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                    case_id,
                    variant,
                    claim_owner,
                    claim_epoch,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_results
                       WHERE profile_id=? AND profile_generation=? AND evaluation_id=?
                         AND case_id=? AND variant=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        evaluation_id,
                        case_id,
                        variant,
                    ),
                ).fetchone()
            )

    def create_evaluation_report(
        self,
        owner: OwnerRef,
        *,
        report_id: str,
        evaluation_id: str,
        candidate_package_hash: str,
        dataset_hash: str,
        suite_hash: str,
        required_cases: Sequence[tuple[str, str]],
        results_root_hash: str,
        verdict: str,
        reason_code: str,
        failed_replan: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if verdict not in {"passed", "failed", "inconclusive"}:
            raise ValueError("evaluation_report_verdict_invalid")
        required = sorted(set(required_cases))
        if not required:
            raise CompanionStateError("evaluation_report_requires_cases")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM evaluation_reports
                   WHERE profile_id=? AND profile_generation=?
                     AND (report_id=? OR evaluation_id=?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    report_id,
                    evaluation_id,
                ),
            ).fetchone()
            if existing is not None:
                if (
                    existing["report_id"] != report_id
                    or existing["candidate_package_hash"] != candidate_package_hash
                    or existing["suite_hash"] != suite_hash
                    or existing["results_root_hash"] != results_root_hash
                    or existing["verdict"] != verdict
                ):
                    raise CompanionConflictError("evaluation_report_conflict")
                return dict(existing)
            evaluation = db.execute(
                """SELECT e.*,p.candidate_package_hash
                   FROM evaluation_runs e
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   WHERE e.profile_id=? AND e.profile_generation=? AND e.evaluation_id=?""",
                (owner.profile_id, owner.profile_generation, evaluation_id),
            ).fetchone()
            if (
                evaluation is None
                or evaluation["candidate_package_hash"] != candidate_package_hash
                or evaluation["suite_hash"] != suite_hash
            ):
                raise CompanionConflictError("evaluation_report_fence_mismatch")
            candidate_facts = db.execute(
                """SELECT
                     e.candidate_id,e.candidate_mode,
                     a.status AS candidate_status,
                     a.reason_code AS candidate_reason_code,
                     a.target_id,
                     a.source_owner_key,a.source_scope,a.source_scope_key,
                     a.source_version,a.source_manifest_hash,
                     a.source_binding_generation,
                     a.target_owner_key,a.target_scope,a.target_scope_key,
                     a.target_expected_absent,
                     a.target_expected_binding_generation,
                     p.pack_id,p.version,p.candidate_content_hash,
                     p.candidate_manifest_hash,p.candidate_package_hash,
                     p.archive_hash,
                     r.risk_id,r.risk_hash,r.risk,
                     r.static_preflight_json,r.effect_topology_diff_json
                   FROM evaluation_runs e
                   JOIN candidate_artifacts a
                     ON a.profile_id=e.profile_id
                    AND a.profile_generation=e.profile_generation
                    AND a.candidate_id=e.candidate_id
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   JOIN risk_assessments r
                     ON r.profile_id=e.profile_id
                    AND r.profile_generation=e.profile_generation
                    AND r.candidate_id=e.candidate_id
                    AND r.candidate_package_hash=p.candidate_package_hash
                   WHERE e.profile_id=? AND e.profile_generation=?
                     AND e.evaluation_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                ),
            ).fetchone()
            if candidate_facts is None:
                raise CompanionStateError(
                    "evaluation_report_risk_assessment_missing"
                )
            result_items: list[dict[str, str]] = []
            for case_id, variant in required:
                row = db.execute(
                    """SELECT c.status,r.result_hash
                       FROM evaluation_cases c
                       LEFT JOIN evaluation_results r
                         ON r.profile_id=c.profile_id AND r.profile_generation=c.profile_generation
                        AND r.evaluation_id=c.evaluation_id AND r.case_id=c.case_id
                        AND r.variant=c.variant
                       WHERE c.profile_id=? AND c.profile_generation=? AND c.evaluation_id=?
                         AND c.case_id=? AND c.variant=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        evaluation_id,
                        case_id,
                        variant,
                    ),
                ).fetchone()
                if row is None or row["status"] != "committed" or row["result_hash"] is None:
                    raise CompanionStateError("evaluation_report_incomplete")
                result_items.append(
                    {
                        "case_id": case_id,
                        "variant": variant,
                        "result_hash": str(row["result_hash"]),
                    }
                )
            if canonical_hash(result_items) != results_root_hash:
                raise CompanionConflictError("evaluation_results_root_mismatch")
            db.execute(
                """INSERT INTO evaluation_reports(
                     profile_id,profile_generation,report_id,evaluation_id,
                     candidate_package_hash,dataset_hash,suite_hash,results_root_hash,
                     verdict,reason_code,required_case_count,committed_result_count,
                     schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    report_id,
                    evaluation_id,
                    candidate_package_hash,
                    dataset_hash,
                    suite_hash,
                    results_root_hash,
                    verdict,
                    reason_code,
                    len(required),
                    len(result_items),
                    now,
                ),
            )
            db.execute(
                """UPDATE evaluation_runs SET status=?,reason_code=?,lease_expires_at=NULL,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND evaluation_id=?""",
                (
                    verdict,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                ),
            )
            if verdict == "passed":
                next_candidate_status = (
                    "eligible"
                    if candidate_facts["risk"] == "low"
                    else "awaiting_activation_confirmation"
                )
                db.execute(
                    """UPDATE candidate_artifacts
                       SET status=?,reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=?
                         AND candidate_id=? AND status='evaluating'""",
                    (
                        next_candidate_status,
                        (
                            "evaluation_passed_low_risk"
                            if next_candidate_status == "eligible"
                            else "evaluation_passed_activation_confirmation_required"
                        ),
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        candidate_facts["candidate_id"],
                    ),
                )
            elif verdict == "failed" and failed_replan is not None:
                replan = dict(failed_replan)
                if str(replan.get("candidate_id") or "") != str(
                    candidate_facts["candidate_id"]
                ):
                    raise CompanionConflictError(
                        "evaluation_replan_candidate_mismatch"
                    )
                exhausted = bool(replan.get("exhausted", False))
                invalidation_reason = (
                    "evaluation_failed_replan_exhausted"
                    if exhausted
                    else "evaluation_failed_replan"
                )
                candidate_status = str(candidate_facts["candidate_status"])
                candidate_reason = str(
                    candidate_facts["candidate_reason_code"] or ""
                )
                exact_replay = (
                    candidate_status == "invalidated"
                    and candidate_reason == invalidation_reason
                )
                applied = False
                if candidate_status == "evaluating":
                    cursor = db.execute(
                        """UPDATE candidate_artifacts
                           SET status='invalidated',reason_code=?,updated_at=?
                           WHERE profile_id=? AND profile_generation=?
                             AND candidate_id=? AND status='evaluating'""",
                        (
                            invalidation_reason,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            candidate_facts["candidate_id"],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise CompanionConflictError(
                            "candidate_evaluation_replan_transition_lost"
                        )
                    applied = True
                    if str(candidate_facts["candidate_mode"]) == "genesis":
                        db.execute(
                            """UPDATE growth_target_reservations
                               SET candidate_id=NULL,status='released',
                                   reservation_version=reservation_version+1,
                                   reason_code=?,updated_at=?
                               WHERE profile_id=? AND profile_generation=?
                                 AND target_id=? AND candidate_id=?
                                 AND status='held'""",
                            (
                                invalidation_reason,
                                now,
                                owner.profile_id,
                                owner.profile_generation,
                                candidate_facts["target_id"],
                                candidate_facts["candidate_id"],
                            ),
                        )
                if (
                    not exhausted
                    and (applied or exact_replay)
                    and replan.get("event") is not None
                    and replan.get("job") is not None
                ):
                    event_payload = _clone_companion_json_object(
                        replan["event"],
                        "evaluation_replan_event_invalid",
                    )
                    growth_event = GrowthEvent(
                        owner=owner,
                        event_id=str(event_payload["event_id"]),
                        source_kind=str(event_payload["source_kind"]),
                        source_ref=str(event_payload["source_ref"]),
                        context_key=str(event_payload["context_key"]),
                        root_run_id=str(event_payload["root_run_id"]),
                        reason_code=str(event_payload["reason_code"]),
                        payload=_clone_companion_json_object(
                            event_payload["payload"],
                            "evaluation_replan_event_payload_invalid",
                        ),
                    )
                    self._record_growth_event_db(
                        db,
                        growth_event,
                        now=now,
                    )
                    job = _clone_companion_json_object(
                        replan["job"],
                        "evaluation_replan_job_invalid",
                    )
                    self._enqueue_job_db(
                        db,
                        owner,
                        job_id=str(job["job_id"]),
                        kind=str(job["kind"]),
                        dedupe_key=str(job["dedupe_key"]),
                        payload=_clone_companion_json_object(
                            job["payload"],
                            "evaluation_replan_job_payload_invalid",
                        ),
                        reason_code=str(job["reason_code"]),
                        budget_reserved_tokens=int(
                            job["budget_reserved_tokens"]
                        ),
                        budget_reserved_ms=int(job["budget_reserved_ms"]),
                        now=now,
                    )
            static_preflight = json.loads(
                str(candidate_facts["static_preflight_json"])
            )
            topology_diff = json.loads(
                str(candidate_facts["effect_topology_diff_json"])
            )
            self._insert_outbox(
                db,
                owner,
                outbox_id=canonical_hash(["evaluation_reported", report_id]),
                event_kind="evaluation_reported",
                event_id=report_id,
                sink_kind="companion_notification",
                payload={
                    "schema_version": 2,
                    "report_id": report_id,
                    "evaluation_id": evaluation_id,
                    "evaluation_report_hash": results_root_hash,
                    "candidate_id": str(candidate_facts["candidate_id"]),
                    "candidate_mode": str(candidate_facts["candidate_mode"]),
                    "pack_id": str(candidate_facts["pack_id"]),
                    "candidate_version": str(candidate_facts["version"]),
                    "candidate_content_hash": str(
                        candidate_facts["candidate_content_hash"]
                    ),
                    "candidate_package_hash": candidate_package_hash,
                    "candidate_manifest_hash": str(
                        candidate_facts["candidate_manifest_hash"]
                    ),
                    "candidate_archive_hash": str(
                        candidate_facts["archive_hash"]
                    ),
                    "candidate_code_digest": None,
                    "risk_id": str(candidate_facts["risk_id"]),
                    "risk_assessment_hash": str(
                        candidate_facts["risk_hash"]
                    ),
                    "risk_level": str(candidate_facts["risk"]),
                    "risk_preflight": static_preflight,
                    "risk_topology_diff": topology_diff,
                    "owner_key": str(candidate_facts["target_owner_key"]),
                    "scope": str(candidate_facts["target_scope"]),
                    "scope_key": str(candidate_facts["target_scope_key"]),
                    "target_owner_key": str(
                        candidate_facts["target_owner_key"]
                    ),
                    "target_scope": str(candidate_facts["target_scope"]),
                    "target_scope_key": str(
                        candidate_facts["target_scope_key"]
                    ),
                    "target_expected_absent": bool(
                        candidate_facts["target_expected_absent"]
                    ),
                    "expected_binding_generation": int(
                        candidate_facts[
                            "target_expected_binding_generation"
                        ]
                    ),
                    "source_owner_key": candidate_facts[
                        "source_owner_key"
                    ],
                    "source_scope": candidate_facts["source_scope"],
                    "source_scope_key": candidate_facts[
                        "source_scope_key"
                    ],
                    "source_version": candidate_facts["source_version"],
                    "source_manifest_hash": candidate_facts[
                        "source_manifest_hash"
                    ],
                    "source_binding_generation": candidate_facts[
                        "source_binding_generation"
                    ],
                    "dataset_hash": dataset_hash,
                    "suite_hash": suite_hash,
                    "results_root_hash": results_root_hash,
                    "verdict": verdict,
                    "reason_code": reason_code,
                },
                now=now,
                reason_code=reason_code,
            )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(["evaluation_report", report_id, results_root_hash]),
                now=now,
            )
            return dict(
                db.execute(
                    """SELECT * FROM evaluation_reports
                       WHERE profile_id=? AND profile_generation=? AND report_id=?""",
                    (owner.profile_id, owner.profile_generation, report_id),
                ).fetchone()
            )

    def get_evaluation_postprocess_recovery(
        self,
        owner: OwnerRef,
        *,
        evaluation_id: str,
    ) -> Mapping[str, Any]:
        """Return frozen report/results before accepting another model output."""

        with self.read() as db:
            self._require_owner(db, owner)
            report = db.execute(
                """SELECT * FROM evaluation_reports
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                ),
            ).fetchone()
            rows = db.execute(
                """SELECT case_id,variant,assertions_json,judge_result_json,
                          usage_json,result_hash,reason_code
                   FROM evaluation_results
                   WHERE profile_id=? AND profile_generation=?
                     AND evaluation_id=?
                   ORDER BY case_id,variant""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    evaluation_id,
                ),
            ).fetchall()
        return {
            "report": None if report is None else dict(report),
            "results": tuple(
                {
                    "case_id": str(row["case_id"]),
                    "variant": str(row["variant"]),
                    "assertions": json.loads(str(row["assertions_json"])),
                    "judge_result": json.loads(
                        str(row["judge_result_json"])
                    ),
                    "usage": json.loads(str(row["usage_json"])),
                    "result_hash": str(row["result_hash"]),
                    "reason_code": str(row["reason_code"]),
                }
                for row in rows
            ),
        }

    def record_activation_decision(
        self,
        owner: OwnerRef,
        *,
        decision_id: str,
        nonce: str,
        candidate_id: str,
        report_id: str,
        risk_id: str,
        decision: str,
        actor: str,
        activation_package_hash: str | None,
        activation_code_digest: str | None,
        activation_risk_ack: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Record an exact activation decision without granting action effects."""

        if decision not in {"activate", "reject"}:
            raise ValueError("growth activation decision invalid")
        if actor not in {"user", "system"}:
            raise ValueError("growth activation actor invalid")
        if activation_risk_ack not in {
            "none",
            "persistent_local_code_no_os_sandbox",
        }:
            raise ValueError("growth activation risk acknowledgement invalid")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM growth_decisions
                   WHERE profile_id=? AND profile_generation=?
                     AND (decision_id=? OR nonce=?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    decision_id,
                    nonce,
                ),
            ).fetchone()
            facts = db.execute(
                """SELECT a.*,p.candidate_package_hash,p.pack_id,p.version,
                          p.candidate_manifest_hash,r.risk,
                          r.static_preflight_json,er.verdict
                   FROM candidate_artifacts a
                   JOIN candidate_packages p
                     ON p.profile_id=a.profile_id
                    AND p.profile_generation=a.profile_generation
                    AND p.package_id=a.package_id
                   JOIN risk_assessments r
                     ON r.profile_id=a.profile_id
                    AND r.profile_generation=a.profile_generation
                    AND r.candidate_id=a.candidate_id AND r.risk_id=?
                   JOIN evaluation_runs e
                     ON e.profile_id=a.profile_id
                    AND e.profile_generation=a.profile_generation
                    AND e.candidate_id=a.candidate_id
                   JOIN evaluation_reports er
                     ON er.profile_id=e.profile_id
                    AND er.profile_generation=e.profile_generation
                    AND er.evaluation_id=e.evaluation_id AND er.report_id=?
                   WHERE a.profile_id=? AND a.profile_generation=?
                     AND a.candidate_id=?""",
                (
                    risk_id,
                    report_id,
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                ),
            ).fetchone()
            if facts is None or facts["verdict"] != "passed":
                raise CompanionStateError(
                    "activation_decision_evaluation_not_passed"
                )
            if facts["status"] not in {
                "eligible",
                "awaiting_activation_confirmation",
                "activation_pending",
            }:
                raise CompanionStateError(
                    "activation_decision_candidate_not_decidable"
                )
            preflight = json.loads(str(facts["static_preflight_json"]))
            candidate_kind = str(preflight.get("candidate_kind") or "unknown")
            executable = candidate_kind in {"code", "hook", "local_runtime"}
            package_hash = str(facts["candidate_package_hash"])
            risk = str(facts["risk"])
            if decision == "activate":
                if risk != "low" and actor != "user":
                    raise CompanionStateError(
                        "activation_confirmation_user_required"
                    )
                if activation_package_hash != package_hash:
                    raise CompanionConflictError(
                        "activation_decision_package_hash_mismatch"
                    )
                if executable and (
                    actor != "user"
                    or not activation_code_digest
                    or activation_risk_ack
                    != "persistent_local_code_no_os_sandbox"
                ):
                    raise CompanionStateError(
                        "executable_activation_confirmation_incomplete"
                    )
                if not executable and activation_risk_ack != "none":
                    raise CompanionStateError(
                        "activation_risk_ack_not_applicable"
                    )
            decision_payload = {
                "schema_version": 1,
                "decision_id": decision_id,
                "nonce": nonce,
                "candidate_id": candidate_id,
                "report_id": report_id,
                "risk_id": risk_id,
                "candidate_mode": facts["candidate_mode"],
                "source_fence": {
                    "owner_key": facts["source_owner_key"],
                    "scope": facts["source_scope"],
                    "scope_key": facts["source_scope_key"],
                    "version": facts["source_version"],
                    "manifest_hash": facts["source_manifest_hash"],
                    "binding_generation": facts["source_binding_generation"],
                }
                if facts["source_owner_key"] is not None
                else None,
                "target_owner_key": facts["target_owner_key"],
                "target_scope": facts["target_scope"],
                "target_scope_key": facts["target_scope_key"],
                "target_expected_absent": bool(
                    facts["target_expected_absent"]
                ),
                "target_expected_binding_generation": int(
                    facts["target_expected_binding_generation"]
                ),
                "decision": decision,
                "actor": actor,
                "reason_code": reason_code,
                "activation_package_hash": activation_package_hash,
                "activation_code_digest": activation_code_digest,
                "activation_risk_ack": activation_risk_ack,
            }
            decision_hash = canonical_hash(decision_payload)
            expected = {
                "nonce": nonce,
                "candidate_id": candidate_id,
                "report_id": report_id,
                "risk_id": risk_id,
                "candidate_mode": facts["candidate_mode"],
                "source_fence_json": (
                    None
                    if decision_payload["source_fence"] is None
                    else canonical_json(decision_payload["source_fence"])
                ),
                "target_owner_key": facts["target_owner_key"],
                "target_scope": facts["target_scope"],
                "target_scope_key": facts["target_scope_key"],
                "target_expected_absent": int(
                    facts["target_expected_absent"]
                ),
                "target_expected_binding_generation": int(
                    facts["target_expected_binding_generation"]
                ),
                "decision": decision,
                "actor": actor,
                "reason_code": reason_code,
                "activation_package_hash": activation_package_hash,
                "activation_code_digest": activation_code_digest,
                "activation_risk_ack": activation_risk_ack,
                "decision_hash": decision_hash,
                "schema_version": 1,
            }
            if existing is not None:
                if existing["decision_id"] != decision_id or not self._same(
                    existing, expected
                ):
                    raise CompanionConflictError(
                        "activation_decision_conflict"
                    )
                return dict(existing)
            db.execute(
                """INSERT INTO growth_decisions(
                     profile_id,profile_generation,decision_id,nonce,
                     candidate_id,report_id,risk_id,candidate_mode,
                     source_fence_json,target_owner_key,target_scope,
                     target_scope_key,target_expected_absent,
                     target_expected_binding_generation,decision,actor,
                     reason_code,activation_package_hash,
                     activation_code_digest,activation_risk_ack,
                     decision_hash,schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    decision_id,
                    nonce,
                    candidate_id,
                    report_id,
                    risk_id,
                    facts["candidate_mode"],
                    expected["source_fence_json"],
                    facts["target_owner_key"],
                    facts["target_scope"],
                    facts["target_scope_key"],
                    facts["target_expected_absent"],
                    facts["target_expected_binding_generation"],
                    decision,
                    actor,
                    reason_code,
                    activation_package_hash,
                    activation_code_digest,
                    activation_risk_ack,
                    decision_hash,
                    now,
                ),
            )
            next_status = (
                "activation_pending" if decision == "activate" else "rejected"
            )
            db.execute(
                """UPDATE candidate_artifacts
                   SET status=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND candidate_id=? AND status IN (
                       'eligible','awaiting_activation_confirmation'
                     )""",
                (
                    next_status,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    candidate_id,
                ),
            )
            self._bump_detail(
                db, owner, mutation_hash=decision_hash, now=now
            )
            return dict(
                db.execute(
                    """SELECT * FROM growth_decisions
                       WHERE profile_id=? AND profile_generation=?
                         AND decision_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        decision_id,
                    ),
                ).fetchone()
            )

    # ------------------------------------------------------------------
    # Capability mutation intent.  CapabilityStore remains the only active
    # binding authority; these rows are an outbox/saga intent only.

    def create_capability_mutation_request(
        self, owner: OwnerRef, request: MutationRequest
    ) -> Mapping[str, Any]:
        now = self._now()
        action = request.action.value
        candidate_mode = request.candidate_mode.value if request.candidate_mode else None
        persisted_candidate_id = (
            request.candidate_id
            if action in {"install", "update"}
            else None
        )
        persisted_candidate_mode = (
            candidate_mode if action in {"install", "update"} else None
        )
        source_fence_json = (
            canonical_json(dict(request.source_fence))
            if request.source_fence is not None
            else None
        )
        manager_key = canonical_hash(
            ["companion_growth", owner.profile_id, owner.profile_generation, request.request_fingerprint]
        )
        with self._write() as db:
            self._require_owner(db, owner)
            replay = db.execute(
                """SELECT * FROM capability_activation_requests
                   WHERE profile_id=? AND profile_generation=? AND request_fingerprint=?""",
                (owner.profile_id, owner.profile_generation, request.request_fingerprint),
            ).fetchone()
            if replay is not None:
                replay_expected = {
                    "activation_request_id": request.request_id,
                    "action": action,
                    "candidate_id": persisted_candidate_id,
                    "candidate_mode": persisted_candidate_mode,
                    "rollback_kind": request.rollback_kind,
                    "activation_mode": request.activation_mode,
                    "target_owner_key": request.target_owner_key,
                    "target_scope": request.target_scope,
                    "target_scope_key": request.target_scope_key,
                    "pack_id": request.pack_id,
                    "target_version": request.target_version,
                    "target_manifest_hash": request.target_manifest_hash,
                    "candidate_package_hash": request.target_package_hash,
                    "archive_hash": request.target_archive_hash,
                    "source_fence_json": source_fence_json,
                    "target_expected_absent": int(
                        request.target_expected_absent
                    ),
                    "target_expected_binding_generation":
                        request.target_expected_binding_generation,
                    "cause_ref": request.cause_ref,
                    "reason_code": request.reason_code,
                }
                # Older trusted message-panel rollback rows did not retain the
                # source candidate identity.  Adopt the same durable intent
                # only when every other immutable field is identical, then
                # backfill the missing lineage so an already-settled rollback
                # can project the candidate terminal state as well.
                replay_identity_expected = {
                    key: value
                    for key, value in replay_expected.items()
                    if key != "reason_code"
                }
                legacy_rollback_backfill = (
                    action in {"rollback", "disable"}
                    and request.candidate_id is not None
                    and candidate_mode is not None
                    and self._same(replay, replay_identity_expected)
                    and (
                        replay["reason_code"] == request.reason_code
                        or replay["status"] in {"succeeded", "failed"}
                    )
                )
                if legacy_rollback_backfill:
                    candidate = db.execute(
                        """SELECT status,candidate_mode FROM candidate_artifacts
                           WHERE profile_id=? AND profile_generation=?
                             AND candidate_id=?""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            request.candidate_id,
                        ),
                    ).fetchone()
                    if (
                        candidate is None
                        or candidate["candidate_mode"] != candidate_mode
                        or candidate["status"]
                        not in {"active", "rollback_pending", "rolled_back"}
                    ):
                        raise CompanionConflictError(
                            "capability_mutation_legacy_candidate_conflict"
                        )
                    db.execute(
                        """INSERT OR IGNORE INTO lineage_edges(
                             profile_id,profile_generation,from_kind,from_id,
                             to_kind,to_id,relation,reason_code,
                             schema_version,created_at
                           ) VALUES (?,?,?,?,?,?,?,?,1,?)""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            "candidate",
                            request.candidate_id,
                            "capability_mutation_request",
                            replay["activation_request_id"],
                            action,
                            request.reason_code,
                            now,
                        ),
                    )
                    if replay["status"] == "succeeded":
                        db.execute(
                            """UPDATE candidate_artifacts
                               SET status='rolled_back',reason_code=?,updated_at=?
                               WHERE profile_id=? AND profile_generation=?
                                 AND candidate_id=?
                                 AND status IN ('active','rollback_pending')""",
                            (
                                replay["reason_code"],
                                now,
                                owner.profile_id,
                                owner.profile_generation,
                                request.candidate_id,
                            ),
                        )
                    elif replay["status"] in {
                        "pending",
                        "claimed",
                        "staging",
                        "staged",
                        "publishing",
                        "unknown",
                        "cleanup_required",
                    }:
                        db.execute(
                            """UPDATE candidate_artifacts
                               SET status='rollback_pending',reason_code=?,updated_at=?
                               WHERE profile_id=? AND profile_generation=?
                                 AND candidate_id=? AND status='active'""",
                            (
                                replay["reason_code"],
                                now,
                                owner.profile_id,
                                owner.profile_generation,
                                request.candidate_id,
                            ),
                        )
                    return dict(
                        db.execute(
                            """SELECT * FROM capability_activation_requests
                               WHERE profile_id=? AND profile_generation=?
                                 AND activation_request_id=?""",
                            (
                                owner.profile_id,
                                owner.profile_generation,
                                replay["activation_request_id"],
                            ),
                        ).fetchone()
                    )
                if not self._same(replay, replay_expected):
                    raise CompanionConflictError("capability_mutation_request_conflict")
                return dict(replay)

            report_id = risk_id = decision_id = None
            if action in {"install", "update"}:
                if request.candidate_id is None:
                    raise CompanionStateError("mutation_candidate_required")
                candidate = db.execute(
                    """SELECT a.*,p.pack_id AS frozen_pack_id,
                              p.version AS frozen_version,
                              p.candidate_package_hash,p.candidate_manifest_hash,
                              p.archive_hash
                       FROM candidate_artifacts a JOIN candidate_packages p
                         ON p.profile_id=a.profile_id AND p.profile_generation=a.profile_generation
                        AND p.package_id=a.package_id
                       WHERE a.profile_id=? AND a.profile_generation=? AND a.candidate_id=?""",
                    (owner.profile_id, owner.profile_generation, request.candidate_id),
                ).fetchone()
                if candidate is None or candidate["status"] not in {
                    "eligible",
                    "activation_pending",
                }:
                    raise CompanionStateError("mutation_candidate_not_eligible")
                frozen_mode = str(candidate["candidate_mode"])
                expected_action = (
                    "update" if frozen_mode == "update" else "install"
                )
                if candidate_mode != frozen_mode or action != expected_action:
                    raise CompanionConflictError(
                        "mutation_candidate_mode_or_action_mismatch"
                    )
                if (
                    request.pack_id != candidate["frozen_pack_id"]
                    or request.target_version != candidate["frozen_version"]
                    or request.target_owner_key != candidate["target_owner_key"]
                    or request.target_scope != candidate["target_scope"]
                    or request.target_scope_key != candidate["target_scope_key"]
                    or int(request.target_expected_absent)
                    != int(candidate["target_expected_absent"])
                    or request.target_expected_binding_generation
                    != int(candidate["target_expected_binding_generation"])
                ):
                    raise CompanionConflictError(
                        "mutation_candidate_fence_mismatch"
                    )
                if request.source_fence is not None:
                    source_fence = dict(request.source_fence)
                    frozen_source = {
                        "owner_key": candidate["source_owner_key"],
                        "scope": candidate["source_scope"],
                        "scope_key": candidate["source_scope_key"],
                        "version": candidate["source_version"],
                        "manifest_hash": candidate["source_manifest_hash"],
                        "binding_generation":
                            candidate["source_binding_generation"],
                    }
                    if any(
                        source_fence.get(key) != value
                        for key, value in frozen_source.items()
                    ):
                        raise CompanionConflictError(
                            "mutation_candidate_source_fence_mismatch"
                        )
                report = db.execute(
                    """SELECT report_id FROM evaluation_reports
                       WHERE profile_id=? AND profile_generation=? AND verdict='passed'
                         AND evaluation_id IN (
                           SELECT evaluation_id FROM evaluation_runs
                           WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                         ) ORDER BY created_at DESC LIMIT 1""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        owner.profile_id,
                        owner.profile_generation,
                        request.candidate_id,
                    ),
                ).fetchone()
                risk = db.execute(
                    """SELECT risk_id FROM risk_assessments
                       WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                         AND risk<>'unknown' ORDER BY created_at DESC LIMIT 1""",
                    (owner.profile_id, owner.profile_generation, request.candidate_id),
                ).fetchone()
                decision = db.execute(
                    """SELECT decision_id FROM growth_decisions
                       WHERE profile_id=? AND profile_generation=? AND candidate_id=?
                         AND decision='activate' ORDER BY created_at DESC LIMIT 1""",
                    (owner.profile_id, owner.profile_generation, request.candidate_id),
                ).fetchone()
                if report is None or risk is None or decision is None:
                    raise CompanionStateError("mutation_evaluation_or_decision_missing")
                report_id, risk_id, decision_id = (
                    report["report_id"],
                    risk["risk_id"],
                    decision["decision_id"],
                )
                if (
                    request.target_package_hash != candidate["candidate_package_hash"]
                    or request.target_manifest_hash != candidate["candidate_manifest_hash"]
                    or request.target_archive_hash != candidate["archive_hash"]
                ):
                    raise CompanionConflictError("mutation_candidate_hash_mismatch")
                if candidate_mode == "genesis":
                    reservation = db.execute(
                        """SELECT candidate_id,status FROM growth_target_reservations
                           WHERE profile_id=? AND profile_generation=? AND target_id=?
                             AND reservation_version=?""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            candidate["target_id"],
                            candidate["reservation_version"],
                        ),
                    ).fetchone()
                    if (
                        reservation is None
                        or reservation["status"] != "held"
                        or reservation["candidate_id"] != request.candidate_id
                    ):
                        raise CompanionStateError("mutation_genesis_reservation_stale")
            elif request.candidate_id is not None:
                candidate = db.execute(
                    """SELECT a.status,a.candidate_mode,a.target_owner_key,
                              a.target_scope,a.target_scope_key,
                              p.pack_id AS frozen_pack_id
                       FROM candidate_artifacts a
                       JOIN candidate_packages p
                         ON p.profile_id=a.profile_id
                        AND p.profile_generation=a.profile_generation
                        AND p.package_id=a.package_id
                       WHERE a.profile_id=? AND a.profile_generation=?
                         AND a.candidate_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request.candidate_id,
                    ),
                ).fetchone()
                if candidate is None or candidate["status"] != "active":
                    raise CompanionStateError(
                        "mutation_candidate_not_active"
                    )
                if (
                    candidate_mode != candidate["candidate_mode"]
                    or request.target_owner_key
                    != candidate["target_owner_key"]
                    or request.target_scope != candidate["target_scope"]
                    or request.target_scope_key
                    != candidate["target_scope_key"]
                    or request.pack_id != candidate["frozen_pack_id"]
                ):
                    raise CompanionConflictError(
                        "mutation_candidate_rollback_fence_mismatch"
                    )
            try:
                db.execute(
                    """INSERT INTO capability_activation_requests(
                         profile_id,profile_generation,activation_request_id,request_fingerprint,
                         action,candidate_id,candidate_mode,rollback_kind,activation_mode,
                         target_owner_key,target_scope,target_scope_key,pack_id,target_version,
                         target_manifest_hash,candidate_package_hash,archive_hash,source_fence_json,
                         target_expected_absent,target_expected_binding_generation,report_id,risk_id,
                         decision_id,cause_ref,manager_idempotency_key,status,claim_epoch,attempt,
                         reason_code,schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                                 'pending',0,0,?,1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request.request_id,
                        request.request_fingerprint,
                        action,
                        persisted_candidate_id,
                        persisted_candidate_mode,
                        request.rollback_kind,
                        request.activation_mode,
                        request.target_owner_key,
                        request.target_scope,
                        request.target_scope_key,
                        request.pack_id,
                        request.target_version,
                        request.target_manifest_hash,
                        request.target_package_hash,
                        request.target_archive_hash,
                        source_fence_json,
                        int(request.target_expected_absent),
                        request.target_expected_binding_generation,
                        report_id,
                        risk_id,
                        decision_id,
                        request.cause_ref,
                        manager_key,
                        request.reason_code,
                        now,
                        now,
                    ),
                )
                if action in {"rollback", "disable"} and request.candidate_id is not None:
                    updated = db.execute(
                        """UPDATE candidate_artifacts
                           SET status='rollback_pending',reason_code=?,updated_at=?
                           WHERE profile_id=? AND profile_generation=?
                             AND candidate_id=? AND status='active'""",
                        (
                            request.reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            request.candidate_id,
                        ),
                    )
                    if updated.rowcount != 1:
                        raise CompanionConflictError(
                            "mutation_candidate_rollback_claim_lost"
                        )
            except sqlite3.IntegrityError as exc:
                in_progress = db.execute(
                    """SELECT * FROM capability_activation_requests
                       WHERE profile_id=? AND profile_generation=? AND target_owner_key=?
                         AND target_scope=? AND target_scope_key=? AND pack_id=?
                         AND status IN (
                           'pending','claimed','staging','staged','publishing','unknown','cleanup_required'
                         )""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request.target_owner_key,
                        request.target_scope,
                        request.target_scope_key,
                        request.pack_id,
                    ),
                ).fetchone()
                if in_progress is not None:
                    # A trusted UI rollback may be retried after the detail
                    # projection advances. Older builds included that read
                    # fence in the request id, so safely adopt the existing
                    # pending saga only when its full rollback target and
                    # cause are identical. Other competing mutations remain
                    # a hard conflict.
                    same_rollback_intent = (
                        action == "rollback"
                        and in_progress["action"] == "rollback"
                        and in_progress["rollback_kind"]
                        == request.rollback_kind
                        and in_progress["target_version"]
                        == request.target_version
                        and in_progress["target_manifest_hash"]
                        == request.target_manifest_hash
                        and in_progress[
                            "target_expected_binding_generation"
                        ]
                        == request.target_expected_binding_generation
                        and in_progress["cause_ref"] == request.cause_ref
                        and in_progress["reason_code"]
                        == request.reason_code
                    )
                    if same_rollback_intent:
                        if request.candidate_id is not None:
                            db.execute(
                                """INSERT OR IGNORE INTO lineage_edges(
                                     profile_id,profile_generation,from_kind,
                                     from_id,to_kind,to_id,relation,reason_code,
                                     schema_version,created_at
                                   ) VALUES (?,?,?,?,?,?,?,?,1,?)""",
                                (
                                    owner.profile_id,
                                    owner.profile_generation,
                                    "candidate",
                                    request.candidate_id,
                                    "capability_mutation_request",
                                    in_progress["activation_request_id"],
                                    action,
                                    request.reason_code,
                                    now,
                                ),
                            )
                            updated = db.execute(
                                """UPDATE candidate_artifacts
                                   SET status='rollback_pending',
                                       reason_code=?,updated_at=?
                                   WHERE profile_id=? AND profile_generation=?
                                     AND candidate_id=? AND status='active'""",
                                (
                                    request.reason_code,
                                    now,
                                    owner.profile_id,
                                    owner.profile_generation,
                                    request.candidate_id,
                                ),
                            )
                            if updated.rowcount != 1:
                                current_candidate = db.execute(
                                    """SELECT status FROM candidate_artifacts
                                       WHERE profile_id=? AND profile_generation=?
                                         AND candidate_id=?""",
                                    (
                                        owner.profile_id,
                                        owner.profile_generation,
                                        request.candidate_id,
                                    ),
                                ).fetchone()
                                if (
                                    current_candidate is None
                                    or current_candidate["status"]
                                    != "rollback_pending"
                                ):
                                    raise CompanionConflictError(
                                        "capability_mutation_adopted_candidate_state_conflict"
                                    ) from exc
                        return dict(
                            db.execute(
                                """SELECT * FROM capability_activation_requests
                                   WHERE profile_id=? AND profile_generation=?
                                     AND activation_request_id=?""",
                                (
                                    owner.profile_id,
                                    owner.profile_generation,
                                    in_progress[
                                        "activation_request_id"
                                    ],
                                ),
                            ).fetchone()
                        )
                    raise CompanionStateError(
                        "capability_mutation_in_progress:"
                        + str(in_progress["activation_request_id"])
                    ) from exc
                raise
            audit_id = canonical_hash(["mutation_request", request.request_id])
            audit_hash = canonical_hash(
                {
                    "request_id": request.request_id,
                    "fingerprint": request.request_fingerprint,
                    "action": action,
                }
            )
            db.execute(
                """INSERT INTO audit_events(
                     profile_id,profile_generation,audit_id,actor,action,reason_code,
                     after_hash,lineage_ref,audit_hash,schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    audit_id,
                    "companion_host",
                    f"capability_{action}_requested",
                    request.reason_code,
                    request.request_fingerprint,
                    request.request_id,
                    audit_hash,
                    now,
                ),
            )
            if request.candidate_id:
                db.execute(
                    """INSERT INTO lineage_edges(
                         profile_id,profile_generation,from_kind,from_id,to_kind,to_id,relation,
                         reason_code,schema_version,created_at
                       ) VALUES (?,?,?,?,?,?,?,?,1,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        "candidate",
                        request.candidate_id,
                        "capability_mutation_request",
                        request.request_id,
                        action,
                        request.reason_code,
                        now,
                    ),
                )
            self._insert_outbox(
                db,
                owner,
                outbox_id=canonical_hash(["capability_mutation", request.request_id]),
                event_kind="capability_mutation",
                event_id=request.request_id,
                sink_kind="capability_platform",
                payload={
                    "schema_version": 1,
                    "activation_request_id": request.request_id,
                    "request_fingerprint": request.request_fingerprint,
                    "action": action,
                },
                now=now,
                reason_code=request.reason_code,
            )
            self._bump_detail(
                db, owner, mutation_hash=request.request_fingerprint, now=now
            )
            return dict(
                db.execute(
                    """SELECT * FROM capability_activation_requests
                       WHERE profile_id=? AND profile_generation=? AND activation_request_id=?""",
                    (owner.profile_id, owner.profile_generation, request.request_id),
                ).fetchone()
            )

    def get_capability_mutation_request(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
    ) -> Mapping[str, Any]:
        """Read one durable mutation intent without exposing another owner."""

        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM capability_activation_requests
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("capability_mutation_request_missing")
            return dict(row)

    def claim_capability_mutation_request(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
        claim_owner: str,
        lease_seconds: float,
    ) -> Mapping[str, Any]:
        """Claim an activation saga or recover an expired claim.

        The request row is the only scheduler authority.  A lease steal never
        authorizes a fresh Manager operation: callers must first reconcile the
        stable ``manager_idempotency_key`` and any persisted operation/set
        references.
        """

        if not claim_owner:
            raise ValueError("claim_owner is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_dt = datetime.fromisoformat(self._now().replace("Z", "+00:00"))
        now = now_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        expiry = (now_dt + timedelta(seconds=lease_seconds)).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM capability_activation_requests
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("capability_mutation_request_missing")
            expired = (
                row["status"] in {"claimed", "staging", "staged", "publishing"}
                and row["lease_expires_at"] is not None
                and row["lease_expires_at"] <= now
            )
            if row["status"] != "pending" and not expired:
                raise CompanionLeaseError("capability_mutation_not_claimable")
            next_epoch = int(row["claim_epoch"]) + 1
            next_attempt = int(row["attempt"]) + 1
            db.execute(
                """UPDATE capability_activation_requests
                   SET status='claimed',claim_owner=?,claim_epoch=?,
                       lease_expires_at=?,attempt=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=? AND claim_epoch=?""",
                (
                    claim_owner,
                    next_epoch,
                    expiry,
                    next_attempt,
                    (
                        "capability_mutation_recovery_claimed"
                        if expired
                        else "capability_mutation_claimed"
                    ),
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                    row["claim_epoch"],
                ),
            )
            claimed = db.execute(
                """SELECT * FROM capability_activation_requests
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                ),
            ).fetchone()
            if (
                claimed is None
                or claimed["claim_owner"] != claim_owner
                or int(claimed["claim_epoch"]) != next_epoch
            ):
                raise CompanionLeaseError("capability_mutation_claim_lost")
            return dict(claimed)

    def record_capability_mutation_prepared(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
        claim_owner: str,
        claim_epoch: int,
        manager_operation_id: str,
        runtime_set_ref: str | None,
        runtime_set_hash: str | None,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Persist trusted staging identities before any runtime start."""

        if not manager_operation_id:
            raise ValueError("manager_operation_id is required")
        if (runtime_set_ref is None) != (runtime_set_hash is None):
            raise ValueError("runtime set ref/hash must be both present or absent")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM capability_activation_requests
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=?""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                ),
            ).fetchone()
            if row is None:
                raise CompanionStateError("capability_mutation_request_missing")
            if (
                row["claim_owner"] != claim_owner
                or int(row["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError("capability_mutation_claim_stale")
            if row["status"] == "staged":
                if (
                    row["manager_operation_id"] != manager_operation_id
                    or row["runtime_set_ref"] != runtime_set_ref
                    or row["runtime_set_hash"] != runtime_set_hash
                ):
                    raise CompanionConflictError(
                        "capability_mutation_prepared_conflict"
                    )
                return dict(row)
            if row["status"] not in {"claimed", "staging"}:
                raise CompanionStateError("capability_mutation_not_stageable")
            db.execute(
                """UPDATE capability_activation_requests
                   SET status='staged',manager_operation_id=?,runtime_set_ref=?,
                       runtime_set_hash=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=? AND claim_owner=?
                     AND claim_epoch=? AND status IN ('claimed','staging')""",
                (
                    manager_operation_id,
                    runtime_set_ref,
                    runtime_set_hash,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            return dict(
                db.execute(
                    """SELECT * FROM capability_activation_requests
                       WHERE profile_id=? AND profile_generation=?
                         AND activation_request_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request_id,
                    ),
                ).fetchone()
            )

    def mark_capability_mutation_publishing(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
        claim_owner: str,
        claim_epoch: int,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Close the final publish boundary for one exact prepared request."""

        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            updated = db.execute(
                """UPDATE capability_activation_requests
                   SET status='publishing',reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=? AND claim_owner=?
                     AND claim_epoch=? AND status='staged'
                     AND manager_operation_id IS NOT NULL""",
                (
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            if updated.rowcount != 1:
                current = db.execute(
                    """SELECT status,claim_owner,claim_epoch
                       FROM capability_activation_requests
                       WHERE profile_id=? AND profile_generation=?
                         AND activation_request_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request_id,
                    ),
                ).fetchone()
                if (
                    current is not None
                    and current["status"] == "publishing"
                    and current["claim_owner"] == claim_owner
                    and int(current["claim_epoch"]) == claim_epoch
                ):
                    return dict(current)
                raise CompanionLeaseError(
                    "capability_mutation_publish_claim_stale"
                )
            return dict(
                db.execute(
                    """SELECT * FROM capability_activation_requests
                       WHERE profile_id=? AND profile_generation=?
                         AND activation_request_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request_id,
                    ),
                ).fetchone()
            )

    def fail_capability_mutation_request(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
        claim_owner: str,
        claim_epoch: int,
        status: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        """Settle an execution failure without erasing durable recovery refs."""

        if status not in {"failed", "stale", "unknown", "cleanup_required"}:
            raise ValueError("capability mutation failure status is invalid")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            updated = db.execute(
                """UPDATE capability_activation_requests
                   SET status=?,lease_expires_at=NULL,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=?
                     AND activation_request_id=? AND claim_owner=?
                     AND claim_epoch=? AND status IN (
                       'claimed','staging','staged','publishing','unknown',
                       'cleanup_required'
                     )""",
                (
                    status,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    request_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            if updated.rowcount != 1:
                raise CompanionLeaseError(
                    "capability_mutation_failure_claim_stale"
                )
            return dict(
                db.execute(
                    """SELECT * FROM capability_activation_requests
                       WHERE profile_id=? AND profile_generation=?
                         AND activation_request_id=?""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        request_id,
                    ),
                ).fetchone()
            )

    # ------------------------------------------------------------------
    # Redaction / forgetting

    def forget_growth_event(
        self,
        owner: OwnerRef,
        *,
        event_id: str,
        reason_code: str,
    ) -> bool:
        now = self._now()
        # This is the single fixed SessionDB tombstone payload.  Keep it
        # byte-for-byte equivalent to memory.companion_message_projection
        # without coupling CompanionStore to the memory package.
        tombstone = {
            "schema_version": 1,
            "kind": "companion_projection_tombstone",
            "summary": "此成长记录已被遗忘。",
            "detail_ref": None,
            "available_actions": [],
        }
        tombstone_json = canonical_json(tombstone)
        tombstone_hash = canonical_hash(tombstone)
        with self._write() as db:
            self._require_owner(db, owner)
            event = db.execute(
                """SELECT * FROM growth_events
                   WHERE profile_id=? AND profile_generation=? AND event_id=?""",
                (owner.profile_id, owner.profile_generation, event_id),
            ).fetchone()
            if event is None:
                raise CompanionStateError("growth_event_missing")
            if event["content_state"] == "tombstoned":
                return False
            db.execute(
                """UPDATE growth_events SET payload_json=NULL,content_state='tombstoned',
                     reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND event_id=?
                     AND content_state='live'""",
                (
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    event_id,
                ),
            )
            # Preference state and its evidence lineage share this Companion
            # transaction with the event tombstone.  A second async writer
            # would leave a crash window where a forgotten preference still
            # influences a new Run.
            from .preferences import recompute_preferences_after_forget_db

            recompute_preferences_after_forget_db(
                db,
                store=self,
                owner=owner,
                event_id=event_id,
                now=now,
            )
            candidate_ids = [
                str(row["candidate_id"])
                for row in db.execute(
                    """SELECT candidate_id FROM candidate_evidence
                       WHERE profile_id=? AND profile_generation=? AND event_id=?""",
                    (owner.profile_id, owner.profile_generation, event_id),
                ).fetchall()
            ]
            if candidate_ids:
                placeholders = ",".join("?" for _ in candidate_ids)
                db.execute(
                    f"""UPDATE candidate_artifacts SET status='invalidated',
                         reason_code=?,updated_at=?
                         WHERE profile_id=? AND profile_generation=?
                           AND candidate_id IN ({placeholders})
                           AND status NOT IN ('rolled_back','disabled','invalidated')""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        *candidate_ids,
                    ),
                )
                db.execute(
                    f"""UPDATE candidate_package_sources SET provenance_state='forgotten',
                         reason_code=?,updated_at=?
                         WHERE profile_id=? AND profile_generation=?
                           AND candidate_id IN ({placeholders})""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        *candidate_ids,
                    ),
                )
                db.execute(
                    f"""UPDATE capability_version_supports
                        SET support_state='forgotten',reason_code=?,updated_at=?
                        WHERE profile_id=? AND profile_generation=?
                          AND candidate_id IN ({placeholders})
                          AND support_state IN ('eligible','active')""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        *candidate_ids,
                    ),
                )
                # Reports and decisions are durable lineage conclusions, not
                # evidence-independent facts.  Keep their hashes/rows for
                # audit, but make them unusable after their evidence is
                # forgotten.
                db.execute(
                    f"""UPDATE evaluation_reports
                        SET verdict='inconclusive',reason_code=?
                        WHERE profile_id=? AND profile_generation=?
                          AND evaluation_id IN (
                            SELECT evaluation_id FROM evaluation_runs
                            WHERE profile_id=? AND profile_generation=?
                              AND candidate_id IN ({placeholders})
                          )""",
                    (
                        reason_code,
                        owner.profile_id,
                        owner.profile_generation,
                        owner.profile_id,
                        owner.profile_generation,
                        *candidate_ids,
                    ),
                )
                db.execute(
                    f"""UPDATE growth_decisions SET decision='stale',reason_code=?
                        WHERE profile_id=? AND profile_generation=?
                          AND candidate_id IN ({placeholders})
                          AND decision<>'stale'""",
                    (
                        reason_code,
                        owner.profile_id,
                        owner.profile_generation,
                        *candidate_ids,
                    ),
                )
                evaluation_ids = [
                    str(row["evaluation_id"])
                    for row in db.execute(
                        f"""SELECT evaluation_id FROM evaluation_runs
                            WHERE profile_id=? AND profile_generation=?
                              AND candidate_id IN ({placeholders})""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            *candidate_ids,
                        ),
                    ).fetchall()
                ]
                if evaluation_ids:
                    e_placeholders = ",".join("?" for _ in evaluation_ids)
                    db.execute(
                        f"""UPDATE evaluation_runs SET status='inconclusive',
                             reason_code=?,updated_at=?
                             WHERE profile_id=? AND profile_generation=?
                               AND evaluation_id IN ({e_placeholders})
                               AND status IN ('queued','running')""",
                        (
                            reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            *evaluation_ids,
                        ),
                    )
                    db.execute(
                        f"""UPDATE evaluation_execution_permits SET status='revoked',
                             reason_code=?,updated_at=?
                             WHERE profile_id=? AND profile_generation=?
                               AND evaluation_id IN ({e_placeholders})
                               AND status IN ('issued','claimed')""",
                        (
                            reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            *evaluation_ids,
                        ),
                    )
                    db.execute(
                        f"""UPDATE evaluation_cases SET status='inconclusive',
                             reason_code=?,lease_expires_at=NULL,updated_at=?
                             WHERE profile_id=? AND profile_generation=?
                               AND evaluation_id IN ({e_placeholders})
                               AND status IN ('queued','leased')""",
                        (
                            reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            *evaluation_ids,
                        ),
                    )
                    launches = db.execute(
                        f"""SELECT launch_id FROM evaluation_case_launches
                            WHERE profile_id=? AND profile_generation=?
                              AND evaluation_id IN ({e_placeholders})
                              AND status IN ('claimed','started','unknown','cleanup_required')""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            *evaluation_ids,
                        ),
                    ).fetchall()
                    db.execute(
                        f"""UPDATE evaluation_case_launches SET status='aborting',
                             reason_code=?,updated_at=?
                             WHERE profile_id=? AND profile_generation=?
                               AND evaluation_id IN ({e_placeholders})
                               AND status IN ('claimed','started','unknown','cleanup_required')""",
                        (
                            reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            *evaluation_ids,
                        ),
                    )
                    for launch in launches:
                        launch_id = str(launch["launch_id"])
                        self._insert_outbox(
                            db,
                            owner,
                            outbox_id=canonical_hash(["abort_evaluation_launch", launch_id]),
                            event_kind="abort_evaluation_launch",
                            event_id=launch_id,
                            sink_kind="managed_process",
                            payload={
                                "schema_version": 1,
                                "launch_id": launch_id,
                                "reason_code": reason_code,
                            },
                            now=now,
                            reason_code=reason_code,
                        )

                # An already activated version cannot remain trusted after its
                # supporting evidence is forgotten.  Companion never changes
                # CapabilityStore here: quarantine the exact Manager receipt
                # fence, then enqueue one rollback when a complete fallback is
                # known, otherwise a conservative disable request.
                active_receipts = db.execute(
                    f"""SELECT r.*,q.candidate_id,q.candidate_mode,
                               q.target_scope_key,q.source_fence_json
                        FROM capability_activation_receipts r
                        JOIN capability_activation_requests q
                          ON q.profile_id=r.profile_id
                         AND q.profile_generation=r.profile_generation
                         AND q.activation_request_id=r.activation_request_id
                        WHERE r.profile_id=? AND r.profile_generation=?
                          AND q.candidate_id IN ({placeholders})
                          AND q.status='succeeded'
                          AND r.action IN ('install','update')""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        *candidate_ids,
                    ),
                ).fetchall()
                for receipt in active_receipts:
                    binding_generation = int(receipt["binding_generation"])
                    support_set_hash = canonical_hash(
                        [
                            "forgotten_support",
                            event_id,
                            receipt["candidate_id"],
                            receipt["result_hash"],
                        ]
                    )
                    db.execute(
                        """INSERT INTO capability_quarantines(
                             profile_id,profile_generation,pack_id,version,manifest_hash,
                             reason_code,evidence_ref,fence_generation,support_set_hash,
                             status,schema_version,created_at,updated_at
                           ) VALUES (?,?,?,?,?,?,?,?,?,'active',1,?,?)
                           ON CONFLICT(profile_id,profile_generation,pack_id,version,manifest_hash)
                           DO UPDATE SET reason_code=excluded.reason_code,
                             evidence_ref=excluded.evidence_ref,
                             support_set_hash=excluded.support_set_hash,
                             status=CASE
                               WHEN capability_quarantines.status='released'
                               THEN 'active' ELSE capability_quarantines.status END,
                             updated_at=excluded.updated_at""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            receipt["pack_id"],
                            receipt["version"],
                            receipt["manifest_hash"],
                            reason_code,
                            f"growth_event:{event_id}",
                            binding_generation,
                            support_set_hash,
                            now,
                            now,
                        ),
                    )
                    db.execute(
                        """UPDATE capability_activation_guards
                           SET status='superseded',reason_code=?,updated_at=?
                           WHERE profile_id=? AND profile_generation=?
                             AND activation_request_id=? AND status='open'""",
                        (
                            reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            receipt["activation_request_id"],
                        ),
                    )
                    in_progress = db.execute(
                        """SELECT activation_request_id
                           FROM capability_activation_requests
                           WHERE profile_id=? AND profile_generation=?
                             AND target_owner_key=? AND target_scope=?
                             AND target_scope_key=? AND pack_id=?
                             AND status IN (
                               'pending','claimed','staging','staged','publishing',
                               'unknown','cleanup_required'
                             )""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            receipt["target_owner_key"],
                            receipt["target_scope"],
                            receipt["target_scope_key"],
                            receipt["pack_id"],
                        ),
                    ).fetchone()
                    if in_progress is not None:
                        continue

                    has_fallback = all(
                        receipt[name] is not None
                        for name in (
                            "fallback_owner_key",
                            "fallback_scope",
                            "fallback_scope_key",
                            "fallback_pack_id",
                            "fallback_version",
                            "fallback_manifest_hash",
                        )
                    )
                    action = "rollback" if has_fallback else "disable"
                    rollback_kind = (
                        "remove_override"
                        if has_fallback
                        and receipt["candidate_mode"] == "builtin_override"
                        else "same_owner_version" if has_fallback else None
                    )
                    target_version = (
                        receipt["fallback_version"] if has_fallback else None
                    )
                    target_manifest_hash = (
                        receipt["fallback_manifest_hash"]
                        if has_fallback
                        else None
                    )
                    source_fence = {
                        "owner_key": receipt["target_owner_key"],
                        "scope": receipt["target_scope"],
                        "scope_key": receipt["target_scope_key"],
                        "pack_id": receipt["pack_id"],
                        "version": receipt["version"],
                        "manifest_hash": receipt["manifest_hash"],
                        "binding_generation": binding_generation,
                    }
                    source_fence_json = canonical_json(source_fence)
                    mutation_identity = {
                        "action": action,
                        "cause": f"growth_event:{event_id}",
                        "source": source_fence,
                        "target_version": target_version,
                        "target_manifest_hash": target_manifest_hash,
                        "rollback_kind": rollback_kind,
                    }
                    request_fingerprint = canonical_hash(mutation_identity)
                    request_id = canonical_hash(
                        [
                            "forget_capability_mutation",
                            owner.profile_id,
                            owner.profile_generation,
                            receipt["activation_request_id"],
                            event_id,
                            action,
                        ]
                    )
                    manager_key = canonical_hash(
                        ["capability_manager", request_id, request_fingerprint]
                    )
                    db.execute(
                        """INSERT INTO capability_activation_requests(
                             profile_id,profile_generation,activation_request_id,
                             request_fingerprint,action,candidate_id,candidate_mode,
                             rollback_kind,activation_mode,target_owner_key,target_scope,
                             target_scope_key,pack_id,target_version,target_manifest_hash,
                             candidate_package_hash,archive_hash,source_fence_json,
                             target_expected_absent,target_expected_binding_generation,
                             report_id,risk_id,decision_id,cause_ref,
                             manager_idempotency_key,expected_quarantine_generation,
                             expected_support_set_hash,status,claim_epoch,attempt,
                             reason_code,schema_version,created_at,updated_at
                           ) VALUES (?,?,?,?,?,NULL,NULL,?,'normal',?,?,?,?,?,?,
                                     NULL,NULL,?,0,?,NULL,NULL,NULL,?,?,?,?,
                                     'pending',0,0,?,1,?,?)""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            request_id,
                            request_fingerprint,
                            action,
                            rollback_kind,
                            receipt["target_owner_key"],
                            receipt["target_scope"],
                            receipt["target_scope_key"],
                            receipt["pack_id"],
                            target_version,
                            target_manifest_hash,
                            source_fence_json,
                            binding_generation,
                            f"growth_event:{event_id}",
                            manager_key,
                            binding_generation,
                            support_set_hash,
                            reason_code,
                            now,
                            now,
                        ),
                    )
                    audit_id = canonical_hash(
                        ["forget_capability_mutation", request_id]
                    )
                    db.execute(
                        """INSERT INTO audit_events(
                             profile_id,profile_generation,audit_id,actor,action,
                             reason_code,after_hash,lineage_ref,audit_hash,
                             schema_version,created_at
                           ) VALUES (?,?,?,?,?,?,?,?,?,1,?)""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            audit_id,
                            "companion_host",
                            f"capability_{action}_requested",
                            reason_code,
                            request_fingerprint,
                            request_id,
                            canonical_hash(
                                {
                                    "request_id": request_id,
                                    "fingerprint": request_fingerprint,
                                    "action": action,
                                }
                            ),
                            now,
                        ),
                    )
                    self._insert_outbox(
                        db,
                        owner,
                        outbox_id=canonical_hash(
                            ["capability_mutation", request_id]
                        ),
                        event_kind="capability_mutation",
                        event_id=request_id,
                        sink_kind="capability_platform",
                        payload={
                            "schema_version": 1,
                            "activation_request_id": request_id,
                            "request_fingerprint": request_fingerprint,
                            "action": action,
                        },
                        now=now,
                        reason_code=reason_code,
                    )

            snapshot_ids = [
                str(row["snapshot_id"])
                for row in db.execute(
                    """SELECT DISTINCT snapshot_id FROM run_growth_dependency_evidence
                       WHERE profile_id=? AND profile_generation=? AND event_id=?""",
                    (owner.profile_id, owner.profile_generation, event_id),
                ).fetchall()
            ]
            if snapshot_ids:
                placeholders = ",".join("?" for _ in snapshot_ids)
                db.execute(
                    f"""UPDATE run_growth_snapshots SET status='revoked',reason_code=?,updated_at=?
                        WHERE profile_id=? AND profile_generation=?
                          AND snapshot_id IN ({placeholders}) AND status<>'revoked'""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        *snapshot_ids,
                    ),
                )
                db.execute(
                    f"""UPDATE companion_run_bindings SET status='revoked',reason_code=?,updated_at=?
                        WHERE profile_id=? AND profile_generation=?
                          AND snapshot_id IN ({placeholders})
                          AND status IN ('prepared','active')""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        *snapshot_ids,
                    ),
                )

            affected_notifications = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=?
                     AND source_refs_json LIKE ? AND status<>'redacted'""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    f'%"{event_id}"%',
                ),
            ).fetchall()
            for notification in affected_notifications:
                redaction_version = int(notification["redaction_version"]) + 1
                db.execute(
                    """UPDATE notifications SET summary_json=?,detail_json=?,actions_json='[]',
                         payload_hash=?,redaction_version=?,status='redacted',
                         reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                    (
                        tombstone_json,
                        tombstone_json,
                        tombstone_hash,
                        redaction_version,
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        notification["notification_id"],
                    ),
                )
                db.execute(
                    """UPDATE outbox SET payload_json=?,payload_hash=?,reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=?
                         AND event_id=? AND status IN ('pending','claimed')""",
                    (
                        canonical_json(
                            {
                                "schema_version": 1,
                                "notification_id": notification["notification_id"],
                                "payload_hash": tombstone_hash,
                            }
                        ),
                        canonical_hash(
                            {
                                "schema_version": 1,
                                "notification_id": notification["notification_id"],
                                "payload_hash": tombstone_hash,
                            }
                        ),
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        notification["notification_id"],
                    ),
                )
                redaction_event_id = (
                    f"{owner.profile_id}:{owner.profile_generation}:"
                    f"{notification['notification_id']}:{redaction_version}"
                )
                self._insert_outbox(
                    db,
                    owner,
                    outbox_id=canonical_hash(["projection_redaction", redaction_event_id]),
                    event_kind="projection_redaction",
                    event_id=redaction_event_id,
                    sink_kind="session_projection",
                    payload={
                        "schema_version": 1,
                        "notification_id": notification["notification_id"],
                        "expected_old_payload_hash": notification["payload_hash"],
                        "tombstone_hash": tombstone_hash,
                        "redaction_version": redaction_version,
                    },
                    now=now,
                    reason_code=reason_code,
                )
            forget_audit_id = canonical_hash(
                [
                    "growth_event_forgotten",
                    owner.profile_id,
                    owner.profile_generation,
                    event_id,
                ]
            )
            forget_audit_hash = canonical_hash(
                {
                    "event_id": event_id,
                    "reason_code": reason_code,
                    "content_state": "tombstoned",
                }
            )
            db.execute(
                """INSERT INTO audit_events(
                     profile_id,profile_generation,audit_id,actor,action,
                     reason_code,before_hash,after_hash,lineage_ref,audit_hash,
                     schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    forget_audit_id,
                    "companion_host",
                    "growth_event_forgotten",
                    reason_code,
                    str(event["event_hash"]),
                    forget_audit_hash,
                    f"growth_event:{event_id}",
                    forget_audit_hash,
                    now,
                ),
            )
            self._insert_outbox(
                db,
                owner,
                outbox_id=canonical_hash(
                    ["growth_event_forgotten", forget_audit_id]
                ),
                event_kind="growth_event_forgotten",
                event_id=forget_audit_id,
                sink_kind="companion_notification",
                payload={
                    "schema_version": 1,
                    "forget_audit_id": forget_audit_id,
                    "reason_code": reason_code,
                },
                now=now,
                reason_code=reason_code,
            )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(["forget_event", event_id, reason_code]),
                now=now,
            )
        return True

    # ------------------------------------------------------------------
    # Persistent reminder mutation receipts

    def mutate_reminder(
        self,
        owner: OwnerRef,
        *,
        effect_id: str,
        operation_kind: str,
        args: Mapping[str, Any],
        request_hash: str,
        reminder_id: str,
        before_schedule_version: int | None,
        schedule: Mapping[str, Any],
        timezone: str,
        quiet_policy: Mapping[str, Any],
        result_payload_ref: str,
        result_hash: str,
        reason_code: str,
        occurrence_id: str | None = None,
        due_at: str | None = None,
    ) -> Mapping[str, Any]:
        if canonical_hash(dict(args)) != request_hash:
            raise CompanionConflictError("reminder_request_hash_mismatch")
        if operation_kind not in {"create", "cancel"}:
            raise ValueError("reminder_operation_invalid")
        if operation_kind == "create" and bool(occurrence_id) != bool(due_at):
            raise ValueError("reminder_occurrence_identity_incomplete")
        if operation_kind == "cancel" and (
            occurrence_id is not None or due_at is not None
        ):
            raise ValueError("reminder_cancel_occurrence_invalid")
        now = self._now()
        args_json = canonical_json(dict(args))
        with self._write() as db:
            self._require_owner(db, owner)
            existing = db.execute(
                """SELECT * FROM reminder_mutation_receipts
                   WHERE profile_id=? AND profile_generation=? AND effect_id=?""",
                (owner.profile_id, owner.profile_generation, effect_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["operation_kind"] != operation_kind
                    or existing["request_hash"] != request_hash
                    or existing["result_hash"] != result_hash
                    or existing["reminder_id"] != reminder_id
                ):
                    raise CompanionConflictError(f"reminder_receipt_conflict:{effect_id}")
                return dict(existing)
            if operation_kind == "create":
                if before_schedule_version is not None:
                    raise CompanionStateError("reminder_create_before_version_invalid")
                db.execute(
                    """INSERT INTO reminders(
                         profile_id,profile_generation,reminder_id,schedule_json,timezone,status,
                         schedule_version,quiet_policy_json,reason_code,schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,'active',1,?,?,1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        reminder_id,
                        canonical_json(dict(schedule)),
                        timezone,
                        canonical_json(dict(quiet_policy)),
                        reason_code,
                        now,
                        now,
                    ),
                )
                after = 1
                if occurrence_id is not None and due_at is not None:
                    db.execute(
                        """INSERT INTO reminder_occurrences(
                             profile_id,profile_generation,occurrence_id,reminder_id,due_at,
                             status,reason_code,schema_version,created_at,updated_at
                           ) VALUES (?,?,?,?,?,'pending',?,1,?,?)""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            occurrence_id,
                            reminder_id,
                            due_at,
                            reason_code,
                            now,
                            now,
                        ),
                    )
            else:
                reminder = db.execute(
                    """SELECT status,schedule_version FROM reminders
                       WHERE profile_id=? AND profile_generation=? AND reminder_id=?""",
                    (owner.profile_id, owner.profile_generation, reminder_id),
                ).fetchone()
                if (
                    reminder is None
                    or reminder["status"] != "active"
                    or int(reminder["schedule_version"]) != before_schedule_version
                ):
                    raise CompanionConflictError("reminder_schedule_version_conflict")
                after = int(before_schedule_version) + 1
                db.execute(
                    """UPDATE reminders SET status='cancelled',schedule_version=?,
                         reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND reminder_id=?
                         AND status='active' AND schedule_version=?""",
                    (
                        after,
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        reminder_id,
                        before_schedule_version,
                    ),
                )
                db.execute(
                    """UPDATE reminder_occurrences SET status='cancelled',reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND reminder_id=?
                         AND status IN ('pending','leased')""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        reminder_id,
                    ),
                )
            db.execute(
                """INSERT INTO reminder_mutation_receipts(
                     profile_id,profile_generation,effect_id,operation_kind,args_json,request_hash,
                     reminder_id,before_schedule_version,after_schedule_version,result_payload_ref,
                     result_hash,reason_code,schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    effect_id,
                    operation_kind,
                    args_json,
                    request_hash,
                    reminder_id,
                    before_schedule_version,
                    after,
                    result_payload_ref,
                    result_hash,
                    reason_code,
                    now,
                ),
            )
            self._insert_outbox(
                db,
                owner,
                outbox_id=canonical_hash(["reminder_mutation", effect_id]),
                event_kind=f"reminder_{operation_kind}",
                event_id=effect_id,
                sink_kind="reminder_scheduler",
                payload={
                    "schema_version": 1,
                    "effect_id": effect_id,
                    "reminder_id": reminder_id,
                    "schedule_version": after,
                },
                now=now,
                reason_code=reason_code,
            )
            self._bump_detail(db, owner, mutation_hash=request_hash, now=now)
            return dict(
                db.execute(
                    """SELECT * FROM reminder_mutation_receipts
                       WHERE profile_id=? AND profile_generation=? AND effect_id=?""",
                    (owner.profile_id, owner.profile_generation, effect_id),
                ).fetchone()
            )

    def get_reminder_mutation_receipt(
        self,
        owner: OwnerRef,
        *,
        effect_id: str,
        request_hash: str | None = None,
    ) -> Mapping[str, Any] | None:
        with self.read() as db:
            self._require_owner(db, owner)
            row = db.execute(
                """SELECT * FROM reminder_mutation_receipts
                   WHERE profile_id=? AND profile_generation=? AND effect_id=?""",
                (owner.profile_id, owner.profile_generation, effect_id),
            ).fetchone()
            if row is None:
                return None
            if request_hash is not None and str(row["request_hash"]) != request_hash:
                raise CompanionConflictError(f"reminder_receipt_conflict:{effect_id}")
            return dict(row)

    def list_reminders(
        self,
        owner: OwnerRef,
        *,
        status: str = "active",
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[Mapping[str, Any], ...]:
        if status not in {"active", "all"}:
            raise ValueError("reminder_status_filter_invalid")
        # The service asks for one extra row to compute a bounded next cursor;
        # model-facing validation still caps the requested page at 50.
        if isinstance(limit, bool) or not 1 <= int(limit) <= 51:
            raise ValueError("reminder_limit_invalid")
        with self.read() as db:
            self._require_owner(db, owner)
            predicates = ["profile_id=?", "profile_generation=?"]
            params: list[Any] = [owner.profile_id, owner.profile_generation]
            if status == "active":
                predicates.append("status='active'")
            if cursor:
                predicates.append("reminder_id>?")
                params.append(cursor)
            params.append(int(limit))
            rows = db.execute(
                f"""SELECT * FROM reminders
                    WHERE {' AND '.join(predicates)}
                    ORDER BY reminder_id
                    LIMIT ?""",
                params,
            ).fetchall()
            return tuple(dict(row) for row in rows)

    def list_reminder_occurrences(
        self,
        owner: OwnerRef,
        *,
        reminder_id: str | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        with self.read() as db:
            self._require_owner(db, owner)
            if reminder_id is None:
                rows = db.execute(
                    """SELECT * FROM reminder_occurrences
                       WHERE profile_id=? AND profile_generation=?
                       ORDER BY due_at,occurrence_id""",
                    (owner.profile_id, owner.profile_generation),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT * FROM reminder_occurrences
                       WHERE profile_id=? AND profile_generation=? AND reminder_id=?
                       ORDER BY due_at,occurrence_id""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        reminder_id,
                    ),
                ).fetchall()
            return tuple(dict(row) for row in rows)

    def count_reserved_reminder_occurrences(
        self,
        owner: OwnerRef,
        *,
        window_start: str,
        window_end: str,
    ) -> int:
        with self.read() as db:
            self._require_owner(db, owner)
            return int(
                db.execute(
                    """SELECT COUNT(*) FROM reminder_occurrences
                       WHERE profile_id=? AND profile_generation=?
                         AND due_at>=? AND due_at<?
                         AND status IN ('leased','delivered')""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        window_start,
                        window_end,
                    ),
                ).fetchone()[0]
            )

    def claim_due_reminder_occurrence(
        self,
        owner: OwnerRef,
        *,
        claim_owner: str,
        lease_seconds: float,
        due_at_or_before: str,
        expire_before: str,
        frequency_window_start: str,
        frequency_window_end: str,
        frequency_limit: int,
    ) -> LeaseClaim | None:
        if lease_seconds <= 0 or frequency_limit < 0:
            raise ValueError("reminder_occurrence_claim_invalid")
        now_dt = datetime.fromisoformat(due_at_or_before.replace("Z", "+00:00"))
        expiry = (now_dt + timedelta(seconds=lease_seconds)).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            db.execute(
                """UPDATE reminder_occurrences
                   SET status='expired',claim_owner=NULL,lease_expires_at=NULL,
                       reason_code='reminder_occurrence_overdue',updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND due_at<?
                     AND (
                       status='pending'
                       OR (status='leased' AND lease_expires_at<=?)
                     )""",
                (
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    expire_before,
                    due_at_or_before,
                ),
            )
            row = db.execute(
                """SELECT o.*,r.schedule_json,r.timezone,r.quiet_policy_json,
                          r.schedule_version
                   FROM reminder_occurrences AS o
                   JOIN reminders AS r
                     ON r.profile_id=o.profile_id
                    AND r.profile_generation=o.profile_generation
                    AND r.reminder_id=o.reminder_id
                   WHERE o.profile_id=? AND o.profile_generation=?
                     AND r.status='active' AND o.due_at>=? AND o.due_at<=?
                     AND (
                       o.status='pending'
                       OR (o.status='leased' AND o.lease_expires_at<=?)
                     )
                   ORDER BY o.due_at,o.occurrence_id LIMIT 1""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    expire_before,
                    due_at_or_before,
                    due_at_or_before,
                ),
            ).fetchone()
            if row is None:
                return None
            if row["status"] == "pending":
                reserved = int(
                    db.execute(
                        """SELECT COUNT(*) FROM reminder_occurrences
                           WHERE profile_id=? AND profile_generation=?
                             AND due_at>=? AND due_at<?
                             AND status IN ('leased','delivered')""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            frequency_window_start,
                            frequency_window_end,
                        ),
                    ).fetchone()[0]
                )
                if reserved >= frequency_limit:
                    return None
            epoch = int(row["claim_epoch"]) + 1
            attempt = int(row["attempt"]) + 1
            cursor = db.execute(
                """UPDATE reminder_occurrences
                   SET status='leased',claim_owner=?,claim_epoch=?,
                       lease_expires_at=?,attempt=?,reason_code='reminder_occurrence_claimed',
                       updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND occurrence_id=?
                     AND claim_epoch=?
                     AND (
                       status='pending'
                       OR (status='leased' AND lease_expires_at<=?)
                     )""",
                (
                    claim_owner,
                    epoch,
                    expiry,
                    attempt,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    row["occurrence_id"],
                    row["claim_epoch"],
                    due_at_or_before,
                ),
            )
            if cursor.rowcount != 1:
                raise CompanionLeaseError(
                    f"reminder_occurrence_claim_conflict:{row['occurrence_id']}"
                )
            return LeaseClaim(
                item_id=str(row["occurrence_id"]),
                claim_owner=claim_owner,
                claim_epoch=epoch,
                lease_expires_at=expiry,
                attempt=attempt,
                payload={
                    "schema_version": 1,
                    "reminder_id": str(row["reminder_id"]),
                    "due_at": str(row["due_at"]),
                    "schedule": json.loads(str(row["schedule_json"])),
                    "timezone": str(row["timezone"]),
                    "quiet_policy": json.loads(str(row["quiet_policy_json"])),
                    "schedule_version": int(row["schedule_version"]),
                },
            )

    def defer_reminder_occurrence(
        self,
        owner: OwnerRef,
        *,
        occurrence_id: str,
        claim_owner: str,
        claim_epoch: int,
        retry_at: str,
        reason_code: str,
    ) -> Mapping[str, Any]:
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            cursor = db.execute(
                """UPDATE reminder_occurrences
                   SET status='pending',claim_owner=NULL,lease_expires_at=NULL,
                       due_at=?,reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND occurrence_id=?
                     AND status='leased' AND claim_owner=? AND claim_epoch=?""",
                (
                    retry_at,
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    occurrence_id,
                    claim_owner,
                    claim_epoch,
                ),
            )
            if cursor.rowcount != 1:
                raise CompanionLeaseError(
                    f"reminder_occurrence_lease_stale:{occurrence_id}"
                )
            return dict(
                db.execute(
                    """SELECT * FROM reminder_occurrences
                       WHERE profile_id=? AND profile_generation=? AND occurrence_id=?""",
                    (owner.profile_id, owner.profile_generation, occurrence_id),
                ).fetchone()
            )

    def authorize_reminder_occurrence_delivery(
        self,
        owner: OwnerRef,
        *,
        occurrence_id: str,
        claim_owner: str,
        claim_epoch: int,
        delivery_payload: Mapping[str, Any],
        draft_payload: Mapping[str, Any] | None,
        grant_expires_at: str,
    ) -> Mapping[str, Any]:
        now = self._now()
        outbox_id = canonical_hash(["reminder_occurrence_delivery", occurrence_id])
        with self._write() as db:
            self._require_owner(db, owner)
            occurrence = db.execute(
                """SELECT * FROM reminder_occurrences
                   WHERE profile_id=? AND profile_generation=? AND occurrence_id=?""",
                (owner.profile_id, owner.profile_generation, occurrence_id),
            ).fetchone()
            if (
                occurrence is None
                or occurrence["status"] != "leased"
                or occurrence["claim_owner"] != claim_owner
                or int(occurrence["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError(
                    f"reminder_occurrence_lease_stale:{occurrence_id}"
                )
            draft_job_id: str | None = None
            if draft_payload is not None:
                draft_job_id = canonical_hash(["reminder_draft", occurrence_id])
                payload = dict(draft_payload)
                payload_json = canonical_json(payload)
                payload_hash = canonical_hash(payload)
                existing_job = db.execute(
                    """SELECT * FROM jobs
                       WHERE profile_id=? AND profile_generation=? AND job_id=?""",
                    (owner.profile_id, owner.profile_generation, draft_job_id),
                ).fetchone()
                if existing_job is None:
                    db.execute(
                        """INSERT INTO jobs(
                             profile_id,profile_generation,job_id,kind,dedupe_key,
                             payload_json,payload_hash,status,claim_epoch,attempt,
                             budget_reserved_tokens,budget_actual_tokens,
                             budget_reserved_ms,budget_actual_ms,reason_code,
                             schema_version,created_at,updated_at
                           ) VALUES (?,?,?,?,?,?,?,'queued',0,0,0,0,0,0,
                             'reminder_draft_requested',1,?,?)""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            draft_job_id,
                            "delegated_task",
                            f"reminder-draft:{occurrence_id}",
                            payload_json,
                            payload_hash,
                            now,
                            now,
                        ),
                    )
                    for scope in ("read", "draft", "reversible_local"):
                        db.execute(
                            """INSERT INTO delegated_task_grants(
                                 profile_id,profile_generation,grant_id,scope,target,
                                 expires_at,version,status,reason_code,schema_version,
                                 created_at,updated_at
                               ) VALUES (?,?,?,?,?,?,1,'active',
                                 'reminder_draft_grant',1,?,?)""",
                            (
                                owner.profile_id,
                                owner.profile_generation,
                                canonical_hash(
                                    ["reminder_draft_grant", occurrence_id, scope]
                                ),
                                scope,
                                f"reminder:{occurrence_id}",
                                grant_expires_at,
                                now,
                                now,
                            ),
                        )
                elif (
                    existing_job["kind"] != "delegated_task"
                    or existing_job["payload_hash"] != payload_hash
                ):
                    raise CompanionConflictError(
                        f"reminder_draft_job_conflict:{occurrence_id}"
                    )
            payload = dict(delivery_payload)
            if draft_job_id is not None:
                payload["draft_job_id"] = draft_job_id
            self._insert_outbox(
                db,
                owner,
                outbox_id=outbox_id,
                event_kind="reminder_occurrence_due",
                event_id=occurrence_id,
                sink_kind="companion_notification",
                payload=payload,
                now=now,
                reason_code="reminder_occurrence_delivery_ready",
            )
            return {
                "outbox_id": outbox_id,
                "draft_job_id": draft_job_id,
            }

    def settle_reminder_occurrence_delivery(
        self,
        owner: OwnerRef,
        *,
        occurrence_id: str,
        claim_owner: str,
        claim_epoch: int,
        outbox_id: str,
        outbox_claim_owner: str,
        outbox_claim_epoch: int,
        result_hash: str,
        draft_payload: Mapping[str, Any] | None = None,
        source_message_refs: Sequence[str] = (),
        next_occurrence_id: str | None = None,
        next_due_at: str | None = None,
        next_schedule: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        if bool(next_occurrence_id) != bool(next_due_at):
            raise ValueError("reminder_next_occurrence_identity_incomplete")
        now = self._now()
        with self._write() as db:
            self._require_owner(db, owner)
            occurrence = db.execute(
                """SELECT * FROM reminder_occurrences
                   WHERE profile_id=? AND profile_generation=? AND occurrence_id=?""",
                (owner.profile_id, owner.profile_generation, occurrence_id),
            ).fetchone()
            outbox = db.execute(
                """SELECT * FROM outbox
                   WHERE profile_id=? AND profile_generation=? AND outbox_id=?""",
                (owner.profile_id, owner.profile_generation, outbox_id),
            ).fetchone()
            if (
                occurrence is not None
                and occurrence["status"] == "delivered"
                and outbox is not None
                and outbox["status"] == "delivered"
            ):
                if outbox["result_hash"] != result_hash:
                    raise CompanionConflictError(
                        f"reminder_delivery_settle_conflict:{occurrence_id}"
                    )
                return dict(occurrence)
            if (
                occurrence is None
                or occurrence["status"] != "leased"
                or occurrence["claim_owner"] != claim_owner
                or int(occurrence["claim_epoch"]) != claim_epoch
            ):
                raise CompanionLeaseError(
                    f"reminder_occurrence_lease_stale:{occurrence_id}"
                )
            if (
                outbox is None
                or outbox["status"] != "claimed"
                or outbox["claim_owner"] != outbox_claim_owner
                or int(outbox["claim_epoch"]) != outbox_claim_epoch
            ):
                raise CompanionLeaseError(f"outbox_lease_stale:{outbox_id}")
            db.execute(
                """UPDATE reminder_occurrences
                   SET status='delivered',claim_owner=NULL,lease_expires_at=NULL,
                       reason_code='reminder_occurrence_delivered',updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND occurrence_id=?""",
                (
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    occurrence_id,
                ),
            )
            db.execute(
                """UPDATE outbox
                   SET status='delivered',result_hash=?,
                       reason_code='reminder_occurrence_delivered',delivered_at=?,
                       lease_expires_at=NULL,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND outbox_id=?""",
                (
                    result_hash,
                    now,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    outbox_id,
                ),
            )
            reminder_id = str(occurrence["reminder_id"])
            if next_occurrence_id is None:
                db.execute(
                    """UPDATE reminders SET status='completed',
                         reason_code='reminder_once_delivered',updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND reminder_id=?
                         AND status='active'""",
                    (
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        reminder_id,
                    ),
                )
            else:
                db.execute(
                    """INSERT OR IGNORE INTO reminder_occurrences(
                         profile_id,profile_generation,occurrence_id,reminder_id,due_at,
                         status,reason_code,schema_version,created_at,updated_at
                       ) VALUES (?,?,?,?,?,'pending','reminder_recurrence_scheduled',1,?,?)""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        next_occurrence_id,
                        reminder_id,
                        next_due_at,
                        now,
                        now,
                    ),
                )
                if next_schedule is not None:
                    db.execute(
                        """UPDATE reminders SET schedule_json=?,
                             reason_code='reminder_recurrence_scheduled',updated_at=?
                           WHERE profile_id=? AND profile_generation=? AND reminder_id=?
                             AND status='active'""",
                        (
                            canonical_json(dict(next_schedule)),
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            reminder_id,
                        ),
                    )
            delivery_payload = json.loads(str(outbox["payload_json"]))
            reminder_text = str(
                delivery_payload.get("text") or "提醒时间到了"
            )
            draft_text = (
                ""
                if draft_payload is None
                else str(draft_payload.get("text") or "").strip()
            )
            summary = (
                reminder_text
                if not draft_text
                else f"{reminder_text}\n\n草稿：\n{draft_text}"
            )
            notification_source_refs = tuple(
                str(item) for item in source_message_refs if str(item)
            ) or (occurrence_id,)
            self.create_notification(
                owner,
                notification_id=canonical_hash(
                    [
                        "reminder_due_notification",
                        owner.profile_id,
                        owner.profile_generation,
                        occurrence_id,
                    ]
                ),
                kind="reminder_due",
                source_refs=notification_source_refs,
                summary=summary,
                detail={
                    "overview": [
                        {
                            "id": occurrence_id,
                            "reminder_id": reminder_id,
                            "due_at": str(occurrence["due_at"]),
                            "status": "delivered",
                            "draft": draft_text or None,
                            "source_message_refs": list(
                                notification_source_refs
                            ),
                            "external_send_count": 0,
                        }
                    ]
                },
                available_actions=("view_detail",),
                reason_code="reminder_occurrence_delivered",
            )
            return dict(
                db.execute(
                    """SELECT * FROM reminder_occurrences
                       WHERE profile_id=? AND profile_generation=? AND occurrence_id=?""",
                    (owner.profile_id, owner.profile_generation, occurrence_id),
                ).fetchone()
            )

    # ------------------------------------------------------------------
    # Growth authority control plane

    def get_growth_authority_state(self) -> Mapping[str, Any]:
        with self.read() as db:
            row = db.execute(
                "SELECT * FROM growth_authority_state WHERE authority_key='growth'"
            ).fetchone()
            if row is None:
                raise CompanionStateError("growth_authority_state_missing")
            return dict(row)

    def list_growth_authority_cutover_journal(
        self,
        *,
        cutover_operation_id: str,
    ) -> list[Mapping[str, Any]]:
        """Return the ordered, hash-bearing receipts for one cutover."""

        with self.read() as db:
            rows = db.execute(
                """SELECT * FROM growth_authority_journal
                   WHERE authority_key='growth' AND cutover_operation_id=?
                   ORDER BY generation,event_seq""",
                (str(cutover_operation_id),),
            ).fetchall()
        result: list[Mapping[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["journal_payload"] = json.loads(
                str(row["journal_payload_json"])
            )
            result.append(item)
        return result

    def record_growth_authority_cutover_step(
        self,
        *,
        expected_phase: str,
        expected_generation: int,
        cutover_operation_id: str,
        substep_phase: str,
        substep_hash: str,
        marker_committed: bool,
        journal_payload: Mapping[str, Any],
        reason_code: str = "authority_cutover_step",
    ) -> Mapping[str, Any]:
        """Persist one idempotent cutover receipt without moving the pointer."""

        now = self._now()
        payload = dict(journal_payload)
        with self._write() as db:
            state = db.execute(
                "SELECT * FROM growth_authority_state WHERE authority_key='growth'"
            ).fetchone()
            if (
                state is None
                or str(state["phase"]) != str(expected_phase)
                or int(state["generation"]) != int(expected_generation)
            ):
                raise CompanionConflictError(
                    "growth_authority_cutover_step_state_stale"
                )
            if bool(state["roll_forward_required"]) != bool(marker_committed):
                raise CompanionConflictError(
                    "growth_authority_cutover_step_marker_stale"
                )
            if str(state["cutover_operation_id"] or "") != str(
                cutover_operation_id
            ):
                raise CompanionConflictError(
                    "growth_authority_cutover_operation_conflict"
                )
            existing = db.execute(
                """SELECT * FROM growth_authority_journal
                   WHERE authority_key='growth' AND cutover_operation_id=?
                     AND substep_phase=?
                   ORDER BY generation,event_seq LIMIT 1""",
                (cutover_operation_id, substep_phase),
            ).fetchone()
            expected = {
                "substep_hash": substep_hash,
                "marker_committed": int(marker_committed),
                "journal_payload_json": canonical_json(payload),
                "reason_code": reason_code,
            }
            if existing is not None:
                if not self._same(existing, expected):
                    raise CompanionConflictError(
                        "growth_authority_cutover_step_replay_conflict"
                    )
                return dict(existing)
            event_seq = (
                int(
                    db.execute(
                        """SELECT COALESCE(MAX(event_seq),0) AS seq
                           FROM growth_authority_journal
                           WHERE authority_key='growth' AND generation=?""",
                        (expected_generation,),
                    ).fetchone()["seq"]
                )
                + 1
            )
            db.execute(
                """INSERT INTO growth_authority_journal(
                     authority_key,generation,event_seq,from_phase,to_phase,
                     cutover_operation_id,marker_committed,marker_hash,
                     old_binding_generation,new_binding_generation,
                     old_owner_binding_set_stamp,new_owner_binding_set_stamp,
                     substep_phase,substep_hash,drain_state,journal_payload_json,
                     reason_code,schema_version,created_at
                   ) VALUES (
                     'growth',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?
                   )""",
                (
                    expected_generation,
                    event_seq,
                    expected_phase,
                    expected_phase,
                    cutover_operation_id,
                    int(marker_committed),
                    payload.get("marker_hash"),
                    payload.get("old_binding_generation"),
                    payload.get("new_binding_generation"),
                    payload.get("old_owner_binding_set_stamp"),
                    payload.get("new_owner_binding_set_stamp"),
                    substep_phase,
                    substep_hash,
                    payload.get("drain_state"),
                    canonical_json(payload),
                    reason_code,
                    now,
                ),
            )
            row = db.execute(
                """SELECT * FROM growth_authority_journal
                   WHERE authority_key='growth' AND generation=? AND event_seq=?""",
                (expected_generation, event_seq),
            ).fetchone()
            assert row is not None
            return dict(row)

    def transition_growth_authority_state(
        self,
        expected_phase: str,
        next_phase: str,
        *,
        migration_generation: int,
        marker_committed: bool = False,
        journal_payload: Mapping[str, Any] | None = None,
        reason_code: str = "authority_transition",
    ) -> Mapping[str, Any]:
        now = self._now()
        payload = dict(journal_payload or {})
        with self._write() as db:
            state = db.execute(
                "SELECT * FROM growth_authority_state WHERE authority_key='growth'"
            ).fetchone()
            if state is None:
                raise CompanionStateError("growth_authority_state_missing")
            if state["phase"] != expected_phase:
                if state["phase"] == next_phase and int(state["generation"]) == migration_generation:
                    replay = db.execute(
                        """SELECT * FROM growth_authority_journal
                           WHERE authority_key='growth' AND generation=?
                           ORDER BY event_seq DESC LIMIT 1""",
                        (migration_generation,),
                    ).fetchone()
                    if (
                        replay is None
                        or replay["from_phase"] != expected_phase
                        or replay["to_phase"] != next_phase
                        or int(replay["marker_committed"]) != int(marker_committed)
                        or replay["journal_payload_json"] != canonical_json(payload)
                        or replay["reason_code"] != reason_code
                    ):
                        raise CompanionConflictError(
                            "growth_authority_transition_replay_conflict"
                        )
                    return dict(state)
                raise CompanionConflictError("growth_authority_phase_conflict")
            existing_marker = bool(state["roll_forward_required"])
            current_generation = int(state["generation"])
            if migration_generation != current_generation + 1:
                raise CompanionStateError(
                    "growth_authority_generation_must_advance_once"
                )
            if existing_marker and not marker_committed:
                raise CompanionStateError(
                    "growth_authority_marker_cannot_be_cleared"
                )
            if expected_phase == "legacy":
                legal = next_phase == "preparing" and not marker_committed
            elif expected_phase == "preparing":
                if next_phase == "legacy":
                    legal = not existing_marker and not marker_committed
                elif next_phase == "preparing":
                    legal = True
                elif next_phase == "companion":
                    legal = existing_marker or marker_committed
                elif next_phase == "paused":
                    legal = True
                else:
                    legal = False
            elif expected_phase == "companion":
                legal = next_phase == "paused" and (
                    existing_marker or marker_committed
                )
            elif expected_phase == "paused":
                legal = (
                    next_phase == "companion"
                    and existing_marker
                    and marker_committed
                    and bool(payload.get("preflight_passed"))
                )
            else:
                legal = False
            if not legal:
                raise CompanionStateError(
                    f"growth_authority_transition_invalid:{expected_phase}:{next_phase}"
                )
            event_seq = (
                int(
                    db.execute(
                        """SELECT COALESCE(MAX(event_seq),0) AS seq
                           FROM growth_authority_journal
                           WHERE authority_key='growth' AND generation=?""",
                        (migration_generation,),
                    ).fetchone()["seq"]
                )
                + 1
            )
            cutover_operation_id = payload.get(
                "cutover_operation_id", state["cutover_operation_id"]
            )
            marker_hash = payload.get("marker_hash")
            db.execute(
                """INSERT INTO growth_authority_journal(
                     authority_key,generation,event_seq,from_phase,to_phase,
                     cutover_operation_id,marker_committed,marker_hash,
                     old_binding_generation,new_binding_generation,
                     old_owner_binding_set_stamp,new_owner_binding_set_stamp,
                     substep_phase,substep_hash,drain_state,journal_payload_json,
                     reason_code,schema_version,created_at
                   ) VALUES ('growth',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    migration_generation,
                    event_seq,
                    expected_phase,
                    next_phase,
                    cutover_operation_id,
                    int(marker_committed),
                    marker_hash,
                    payload.get("old_binding_generation"),
                    payload.get("new_binding_generation"),
                    payload.get("old_owner_binding_set_stamp"),
                    payload.get("new_owner_binding_set_stamp"),
                    payload.get("substep_phase"),
                    payload.get("substep_hash"),
                    payload.get("drain_state"),
                    canonical_json(payload),
                    reason_code,
                    now,
                ),
            )
            db.execute(
                """UPDATE growth_authority_state SET phase=?,generation=?,
                     migration_version=?,migration_hash=?,cutover_operation_id=?,
                     roll_forward_required=?,
                     drain_started_at=COALESCE(?,drain_started_at),
                     drain_completed_at=COALESCE(?,drain_completed_at),
                     prepared_at=CASE WHEN ?='preparing' THEN COALESCE(prepared_at,?) ELSE prepared_at END,
                     switched_at=CASE WHEN ?='companion' THEN COALESCE(switched_at,?) ELSE switched_at END,
                     last_error=?,reason_code=?,updated_at=?
                   WHERE authority_key='growth' AND phase=?""",
                (
                    next_phase,
                    migration_generation,
                    payload.get("migration_version"),
                    payload.get("migration_hash"),
                    cutover_operation_id,
                    int(existing_marker or marker_committed),
                    payload.get("drain_started_at"),
                    payload.get("drain_completed_at"),
                    next_phase,
                    now,
                    next_phase,
                    now,
                    payload.get("last_error"),
                    reason_code,
                    now,
                    expected_phase,
                ),
            )
            # Authority outbox is owner-scoped by design. Fan out a stable
            # control event to every active generation in the same transaction.
            for profile in db.execute(
                "SELECT profile_id,generation FROM profiles WHERE status='active'"
            ).fetchall():
                owner = OwnerRef(str(profile["profile_id"]), int(profile["generation"]))
                event_id = f"{migration_generation}:{event_seq}"
                self._insert_outbox(
                    db,
                    owner,
                    outbox_id=canonical_hash(
                        ["growth_authority_transition", owner.profile_id, owner.profile_generation, event_id]
                    ),
                    event_kind="growth_authority_transition",
                    event_id=event_id,
                    sink_kind="growth_router",
                    payload={
                        "schema_version": 1,
                        "from_phase": expected_phase,
                        "to_phase": next_phase,
                        "generation": migration_generation,
                        "marker_committed": marker_committed,
                    },
                    now=now,
                    reason_code=reason_code,
                )
            row = db.execute(
                "SELECT * FROM growth_authority_state WHERE authority_key='growth'"
            ).fetchone()
            assert row is not None
            return dict(row)
