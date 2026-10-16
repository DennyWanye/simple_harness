"""Growth events, preference decisions and growth targets."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from ..contracts import CompanionConflictError, CompanionStateError, GrowthEvent, OwnerRef
from ..growth import GrowthEvidenceV1
from .core import CompanionStoreCore
from .support import canonical_hash, canonical_json


class GrowthEventMixin(CompanionStoreCore):
    """Growth events, preference decisions and growth targets."""

    __slots__ = ()

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
            raise CompanionStateError("growth_evidence_missing_or_tombstoned")
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
                retry_of=(None if row["retry_of"] is None else str(row["retry_of"])),
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

        # 按需导入：循环依赖（store ↔ preferences）
        from ..preferences import apply_preference_event_db

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
                raise CompanionConflictError("preference_turn_source_message_conflict")
            payload = json.loads(str(row["decision_json"]))
            if not isinstance(payload, dict) or canonical_hash(payload) != str(
                row["decision_hash"]
            ):
                raise CompanionStateError("preference_turn_decision_receipt_corrupt")
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
                    raise CompanionConflictError("preference_turn_source_message_conflict")
                persisted = json.loads(str(row["decision_json"]))
                if not isinstance(persisted, dict) or canonical_hash(persisted) != str(
                    row["decision_hash"]
                ):
                    raise CompanionStateError("preference_turn_decision_receipt_corrupt")
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
