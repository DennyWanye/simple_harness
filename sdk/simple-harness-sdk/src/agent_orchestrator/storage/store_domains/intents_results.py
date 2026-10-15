# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""Dispatch intents, results and verifications."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from simple_harness.contracts import canonical_json

from .core import StoreCore
from .records import (
    DispatchIntent,
    StoredResult,
    StoreError,
    _intent_from_row,
    _loads,
    _stored_result,
)


class StoreIntentResultMixin(StoreCore):
    """Dispatch intents, results and verifications."""

    __slots__ = ()

    # -------------------------------------------------------- dispatch intents
    def insert_intent(self, intent: DispatchIntent) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO dispatch_intents(intent_id,kind,subject_id,mission_id,state,version,"
                "creation_key,input_id,input_hash,config_json,expected_turn_id,agent_id,receipt_json,"
                "lease_owner,lease_expires_at,replays,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    intent.intent_id,
                    intent.kind,
                    intent.subject_id,
                    intent.mission_id,
                    intent.state,
                    intent.version,
                    intent.creation_key,
                    intent.input_id,
                    intent.input_hash,
                    canonical_json(dict(intent.config)),
                    intent.expected_turn_id,
                    intent.agent_id,
                    None if intent.receipt is None else canonical_json(dict(intent.receipt)),
                    intent.lease_owner,
                    intent.lease_expires_at,
                    intent.replays,
                    intent.created_at,
                    self.now,
                ),
            )

    def update_intent(self, intent: DispatchIntent, *, expected_version: int) -> None:
        self._cas(
            "dispatch_intents",
            "intent_id",
            intent.intent_id,
            expected_version,
            {
                "state": intent.state,
                "version": intent.version,
                # P2.3f: a re-hand-off of a service intent needs a *new* executor, and
                # the executor is created idempotently from this key.  Every other
                # writer leaves it as it was, so the column is written unchanged there.
                "creation_key": intent.creation_key,
                "expected_turn_id": intent.expected_turn_id,
                "agent_id": intent.agent_id,
                "receipt_json": None
                if intent.receipt is None
                else canonical_json(dict(intent.receipt)),
                "lease_owner": intent.lease_owner,
                "lease_expires_at": intent.lease_expires_at,
                "replays": intent.replays,
            },
        )

    def get_intent(self, intent_id: str) -> DispatchIntent | None:
        row = self._connection.execute(
            "SELECT * FROM dispatch_intents WHERE intent_id = ?", (intent_id,)
        ).fetchone()
        return None if row is None else _intent_from_row(row)

    def get_intent_for_subject(self, subject_id: str) -> DispatchIntent | None:
        row = self._connection.execute(
            "SELECT * FROM dispatch_intents WHERE subject_id = ?", (subject_id,)
        ).fetchone()
        return None if row is None else _intent_from_row(row)

    def list_intents(self, *states: str) -> list[DispatchIntent]:
        marks = ",".join("?" for _ in states)
        rows = self._connection.execute(
            f"SELECT * FROM dispatch_intents WHERE state IN ({marks}) ORDER BY created_at",
            tuple(states),
        ).fetchall()
        return [_intent_from_row(row) for row in rows]

    # ----------------------------------------------------------------- results
    def insert_result(self, stored: StoredResult) -> None:
        envelope = stored.envelope
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO results(result_id,attempt_id,task_id,mission_id,turn_id,result_hash,"
                "verification_state,verdict,json,received_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    envelope.id,
                    envelope.attempt_id,
                    envelope.task_id,
                    envelope.mission_id,
                    stored.turn_id,
                    envelope.result_hash,
                    stored.verification_state,
                    stored.verdict,
                    canonical_json(stored.to_json()),
                    stored.received_at,
                    self.now,
                ),
            )

    def set_result_verification(self, result_id: str, *, state: str, verdict: str | None) -> None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT json FROM results WHERE result_id = ?", (result_id,)
            ).fetchone()
            if row is None:
                raise StoreError(f"unknown result {result_id}")
            data = _loads(row[0])
            data["verification_state"] = state
            data["verdict"] = verdict
            connection.execute(
                "UPDATE results SET verification_state = ?, verdict = ?, json = ?, updated_at = ?"
                " WHERE result_id = ?",
                (state, verdict, canonical_json(data), self.now, result_id),
            )

    def get_result(self, result_id: str) -> StoredResult | None:
        row = self._connection.execute(
            "SELECT json FROM results WHERE result_id = ?", (result_id,)
        ).fetchone()
        return None if row is None else _stored_result(_loads(row[0]))

    def find_result_for_attempt(self, attempt_id: str) -> StoredResult | None:
        row = self._connection.execute(
            "SELECT json FROM results WHERE attempt_id = ? ORDER BY received_at DESC LIMIT 1",
            (attempt_id,),
        ).fetchone()
        return None if row is None else _stored_result(_loads(row[0]))

    def has_unverified_results(self, mission_id: str) -> bool:
        """这个任务有没有还在等审或正在审的结果（试用前第 5 步：积压只暂停它自己的规划轮）。"""

        return (
            self._connection.execute(
                "SELECT 1 FROM results WHERE mission_id=? AND verification_state IN ('PENDING','RUNNING') LIMIT 1",
                (mission_id,),
            ).fetchone()
            is not None
        )

    def list_results_by_verification(self, *states: str) -> list[StoredResult]:
        marks = ",".join("?" for _ in states)
        rows = self._connection.execute(
            f"SELECT json FROM results WHERE verification_state IN ({marks}) ORDER BY received_at",
            tuple(states),
        ).fetchall()
        return [_stored_result(_loads(row[0])) for row in rows]

    # ----------------------------------------------------------- verifications
    def upsert_verification(
        self,
        *,
        result_id: str,
        attempt_id: str,
        requirements_revision: int,
        layer: str,
        status: str,
        detail: Mapping[str, Any],
    ) -> None:
        """One verification layer of one result as judged under one requirements revision
        (TaskGraph 补全第四批: a result kept across an amendment is reviewed again under the
        new revision; both revisions' layers stand side by side)."""
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO verifications(verification_id,result_id,attempt_id,requirements_revision,layer,"
                "status,detail_json,created_at) VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(result_id, requirements_revision, layer) DO UPDATE SET"
                " status = excluded.status, detail_json = excluded.detail_json",
                (
                    f"{result_id}:r{int(requirements_revision)}:{layer}",
                    result_id,
                    attempt_id,
                    int(requirements_revision),
                    layer,
                    status,
                    canonical_json(dict(detail)),
                    self.now,
                ),
            )

    def list_verifications(
        self, result_id: str, *, requirements_revision: int | None
    ) -> list[dict[str, Any]]:
        """The layers recorded for one result under one requirements revision; ``None`` lists
        every revision's layers (display only — a judgement reads one revision)."""
        if requirements_revision is None:
            rows = self._connection.execute(
                "SELECT layer, status, detail_json, created_at, requirements_revision FROM verifications"
                " WHERE result_id = ? ORDER BY requirements_revision, created_at, layer",
                (result_id,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT layer, status, detail_json, created_at, requirements_revision FROM verifications"
                " WHERE result_id = ? AND requirements_revision = ? ORDER BY created_at, layer",
                (result_id, int(requirements_revision)),
            ).fetchall()
        return [
            {
                "layer": row[0],
                "status": row[1],
                "detail": _loads(row[2]),
                "created_at": row[3],
                "requirements_revision": row[4],
            }
            for row in rows
        ]
