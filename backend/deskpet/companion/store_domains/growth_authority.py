"""Growth authority state and cutover journal."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ..contracts import CompanionConflictError, CompanionStateError, OwnerRef
from .core import CompanionStoreCore
from .support import canonical_hash, canonical_json


class GrowthAuthorityMixin(CompanionStoreCore):
    """Growth authority state and cutover journal."""

    __slots__ = ()

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
            item["journal_payload"] = json.loads(str(row["journal_payload_json"]))
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
                raise CompanionConflictError("growth_authority_cutover_step_state_stale")
            if bool(state["roll_forward_required"]) != bool(marker_committed):
                raise CompanionConflictError("growth_authority_cutover_step_marker_stale")
            if str(state["cutover_operation_id"] or "") != str(cutover_operation_id):
                raise CompanionConflictError("growth_authority_cutover_operation_conflict")
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
                    raise CompanionConflictError("growth_authority_cutover_step_replay_conflict")
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
                if (
                    state["phase"] == next_phase
                    and int(state["generation"]) == migration_generation
                ):
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
                        raise CompanionConflictError("growth_authority_transition_replay_conflict")
                    return dict(state)
                raise CompanionConflictError("growth_authority_phase_conflict")
            existing_marker = bool(state["roll_forward_required"])
            current_generation = int(state["generation"])
            if migration_generation != current_generation + 1:
                raise CompanionStateError("growth_authority_generation_must_advance_once")
            if existing_marker and not marker_committed:
                raise CompanionStateError("growth_authority_marker_cannot_be_cleared")
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
                legal = next_phase == "paused" and (existing_marker or marker_committed)
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
                        [
                            "growth_authority_transition",
                            owner.profile_id,
                            owner.profile_generation,
                            event_id,
                        ]
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
