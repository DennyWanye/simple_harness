# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""Commit, acceptance and delivery receipts and acceptance outputs."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from simple_harness.contracts import canonical_json

from ...contracts.htn import SemanticReadSet
from ...contracts.resolution import DeliveryReceipt
from ...contracts.semantic_base import content_hash_of, identifier, index
from ..store import StoreConflict
from .core import HtnStoreCore
from .records import ACCEPTANCE_RECEIPT_KINDS, AcceptanceCommitReceipt, PlanCommitReceipt


class HtnReceiptMixin(HtnStoreCore):
    """Commit, acceptance and delivery receipts and acceptance outputs."""

    __slots__ = ()

    # ================================================================== commit receipts
    def record_commit_receipt(
        self,
        mission_id: str,
        *,
        command_id: str,
        delta_id: str,
        base_plan_revision: int,
        new_plan_revision: int,
        intent_hash: str,
        read_set: SemanticReadSet,
        output_identity: Mapping[str, Any],
        detail: Mapping[str, Any] | None = None,
    ) -> PlanCommitReceipt:
        """Record what one commit command did.  Replaying the command is idempotent.

        The same ``command_id`` with the same intent and read-set returns the stored
        receipt (§17.4: a replayed command yields the same receipt, not a second
        commit).  A different intent under the same id is a conflict.
        """

        if not isinstance(read_set, SemanticReadSet):
            raise StoreConflict("record_commit_receipt expects a SemanticReadSet")
        mission = identifier(mission_id, "mission_id")
        command = identifier(command_id, "command_id")
        read_document = read_set.to_json()
        read_hash = content_hash_of(read_document)
        receipt = PlanCommitReceipt(
            command_id=command,
            mission_id=mission,
            delta_id=identifier(delta_id, "delta_id"),
            base_plan_revision=index(base_plan_revision, "base_plan_revision"),
            new_plan_revision=index(new_plan_revision, "new_plan_revision"),
            intent_hash=str(intent_hash),
            read_set_hash=read_hash,
            read_set=read_set,
            output_identity=dict(output_identity),
            detail=dict(detail or {}),
        )
        stored = self._store.connection.execute(
            "SELECT * FROM plan_commit_receipts WHERE command_id = ?", (command,)
        ).fetchone()
        if stored is not None:
            if stored["intent_hash"] != receipt.intent_hash:
                raise StoreConflict(
                    f"command {command} was already applied with intent {stored['intent_hash']},"
                    f" not {receipt.intent_hash}"
                )
            if stored["read_set_hash"] != read_hash:
                raise StoreConflict(
                    f"command {command} was already applied against a different read-set"
                )
            return self._commit_receipt(stored)
        self._insert(
            "INSERT INTO plan_commit_receipts(command_id,mission_id,delta_id,base_plan_revision,"
            "new_plan_revision,intent_hash,read_set_hash,read_set_json,output_identity_json,"
            "receipt_json,applied_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                command,
                mission,
                receipt.delta_id,
                receipt.base_plan_revision,
                receipt.new_plan_revision,
                receipt.intent_hash,
                read_hash,
                canonical_json(read_document),
                canonical_json(dict(receipt.output_identity)),
                canonical_json(dict(receipt.detail)),
                self._store.now,
            ),
            f"commit receipt for {mission}@{receipt.new_plan_revision} conflicts with a stored one",
        )
        return receipt

    def get_commit_receipt(self, command_id: str) -> PlanCommitReceipt:
        row = self._one(
            "SELECT * FROM plan_commit_receipts WHERE command_id = ?",
            (identifier(command_id, "command_id"),),
            f"no commit receipt for command {command_id}",
        )
        return self._commit_receipt(row)

    def list_commit_receipts(self, mission_id: str) -> tuple[PlanCommitReceipt, ...]:
        rows = self._store.connection.execute(
            "SELECT * FROM plan_commit_receipts WHERE mission_id = ? ORDER BY new_plan_revision",
            (identifier(mission_id, "mission_id"),),
        ).fetchall()
        return tuple(self._commit_receipt(row) for row in rows)

    # ============================================ migration 17: the accept-side receipts
    def record_acceptance_receipt(
        self,
        mission_id: str,
        *,
        command_id: str,
        kind: str,
        subject_id: str,
        intent_hash: str,
        read_set_hash: str,
        event_id: str,
        output_identity: Mapping[str, Any],
        detail: Mapping[str, Any] | None = None,
    ) -> AcceptanceCommitReceipt:
        """§17.4 for the accept side: one command, one commit, one receipt.

        The unique key is ``(mission_id, command_id)``, so a second delivery of the
        same command with the same intent returns the stored receipt and a *different*
        intent under the same id is a conflict rather than a silent second write.
        Migration 17 exists precisely so this is a keyed read: P2.3c part 1 had to
        project the receipt out of the Mission's event log and page through it.
        """

        mission = identifier(mission_id, "mission_id")
        command = identifier(command_id, "command_id")
        receipt = AcceptanceCommitReceipt(
            mission_id=mission,
            command_id=command,
            kind=self._state(kind, ACCEPTANCE_RECEIPT_KINDS, "receipt kind"),
            subject_id=identifier(subject_id, "subject_id"),
            intent_hash=str(intent_hash),
            read_set_hash=str(read_set_hash),
            event_id=str(event_id),
            output_identity=dict(output_identity),
            detail=dict(detail or {}),
        )
        stored = self._store.connection.execute(
            "SELECT * FROM acceptance_commit_receipts WHERE mission_id = ? AND command_id = ?",
            (mission, command),
        ).fetchone()
        if stored is not None:
            if stored["intent_hash"] != receipt.intent_hash:
                raise StoreConflict(
                    f"command {command} was already applied with intent {stored['intent_hash']},"
                    f" not {receipt.intent_hash}"
                )
            return self._acceptance_receipt(stored)
        self._insert(
            "INSERT INTO acceptance_commit_receipts(mission_id,command_id,kind,subject_id,"
            "intent_hash,read_set_hash,event_id,output_identity_json,detail_json,applied_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                mission,
                command,
                receipt.kind,
                receipt.subject_id,
                receipt.intent_hash,
                receipt.read_set_hash,
                receipt.event_id,
                canonical_json(dict(receipt.output_identity)),
                canonical_json(dict(receipt.detail)),
                self._store.now,
            ),
            f"acceptance receipt for {mission}/{command} conflicts with a stored one",
        )
        return receipt

    def find_acceptance_receipt(
        self, mission_id: str, command_id: str
    ) -> AcceptanceCommitReceipt | None:
        """The receipt of one accept-side command, or None.  One indexed read."""

        row = self._store.connection.execute(
            "SELECT * FROM acceptance_commit_receipts WHERE mission_id = ? AND command_id = ?",
            (identifier(mission_id, "mission_id"), identifier(command_id, "command_id")),
        ).fetchone()
        return None if row is None else self._acceptance_receipt(row)

    def list_acceptance_receipts(
        self, mission_id: str, *, kind: str | None = None
    ) -> tuple[AcceptanceCommitReceipt, ...]:
        sql = "SELECT * FROM acceptance_commit_receipts WHERE mission_id = ?"
        values: tuple[Any, ...] = (identifier(mission_id, "mission_id"),)
        if kind is not None:
            sql += " AND kind = ?"
            values += (self._state(kind, ACCEPTANCE_RECEIPT_KINDS, "receipt kind"),)
        rows = self._store.connection.execute(sql + " ORDER BY applied_at, command_id", values)
        return tuple(self._acceptance_receipt(row) for row in rows)

    def record_delivery_receipt(
        self, mission_id: str, receipt: DeliveryReceipt, *, command_id: str, intent_hash: str
    ) -> DeliveryReceipt:
        """Record how far one accepted output travelled (AER §6.1).

        These rows are the *only* source a Mission-root resolution may quote.  A
        receipt that arrives on a command and was never recorded here is a claim
        about the world, and "wrongly declared complete = 0" is exactly the invariant
        that a claim may not stand in for a record.
        """

        if not isinstance(receipt, DeliveryReceipt):
            raise StoreConflict("record_delivery_receipt expects a DeliveryReceipt")
        mission = identifier(mission_id, "mission_id")
        if receipt.mission_id != mission:
            raise StoreConflict(
                f"delivery receipt {receipt.receipt_id} belongs to mission "
                f"{receipt.mission_id!r}, not {mission!r}"
            )
        command = identifier(command_id, "command_id")
        document = receipt.to_json()
        stored = self._store.connection.execute(
            "SELECT * FROM delivery_receipts WHERE mission_id = ? AND command_id = ?",
            (mission, command),
        ).fetchone()
        if stored is not None:
            if stored["intent_hash"] != str(intent_hash):
                raise StoreConflict(
                    f"command {command} already recorded a delivery receipt with intent "
                    f"{stored['intent_hash']}, not {intent_hash}"
                )
            return DeliveryReceipt.from_json(json.loads(stored["receipt_json"]))
        self._insert(
            "INSERT INTO delivery_receipts(mission_id,command_id,receipt_id,acceptance_id,stage,"
            "observed_at_ms,operation_id,intent_hash,receipt_json,recorded_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                mission,
                command,
                receipt.receipt_id,
                str(receipt.acceptance_id),
                str(receipt.stage),
                int(receipt.observed_at_ms),
                None if receipt.operation_id is None else str(receipt.operation_id),
                str(intent_hash),
                canonical_json(document),
                self._store.now,
            ),
            f"delivery receipt {receipt.receipt_id} conflicts with a stored one",
        )
        return receipt

    def find_delivery_receipt(self, mission_id: str, receipt_id: str) -> DeliveryReceipt | None:
        row = self._store.connection.execute(
            "SELECT receipt_json FROM delivery_receipts WHERE mission_id = ? AND receipt_id = ?",
            (identifier(mission_id, "mission_id"), str(receipt_id)),
        ).fetchone()
        return None if row is None else DeliveryReceipt.from_json(json.loads(row["receipt_json"]))

    def list_delivery_receipts(
        self, mission_id: str, *, acceptance_id: str | None = None
    ) -> tuple[DeliveryReceipt, ...]:
        sql = "SELECT receipt_json FROM delivery_receipts WHERE mission_id = ?"
        values: tuple[Any, ...] = (identifier(mission_id, "mission_id"),)
        if acceptance_id is not None:
            sql += " AND acceptance_id = ?"
            values += (str(acceptance_id),)
        rows = self._store.connection.execute(sql + " ORDER BY receipt_id", values)
        return tuple(DeliveryReceipt.from_json(json.loads(row["receipt_json"])) for row in rows)

    def insert_acceptance_output(
        self,
        mission_id: str,
        *,
        acceptance_id: str,
        output_port: str,
        artifact_id: str,
        producer_occurrence: str,
        producer_task_ref: str,
        producer_result_id: str,
        support_revision: int,
        content_hash: str,
        source_revision: str,
        document: Mapping[str, Any],
    ) -> str:
        """Record "this Acceptance accepted that artifact at this output port".

        The index P2.3b needed and the schema did not hold.  Re-recording the same
        ``(acceptance, port, artifact)`` with the same content is a no-op — a crash
        between the result and the acceptance must not be able to double-write it —
        and with *different* content it is a conflict, because two answers to "which
        bytes were accepted at this port" is not something a later read can settle.
        """

        mission = identifier(mission_id, "mission_id")
        acceptance = identifier(acceptance_id, "acceptance_id")
        port = identifier(output_port, "output_port")
        artifact = identifier(artifact_id, "artifact_id")
        payload = canonical_json(dict(document))
        stored = self._store.connection.execute(
            "SELECT output_json FROM acceptance_outputs"
            " WHERE acceptance_id = ? AND output_port = ? AND artifact_id = ?",
            (acceptance, port, artifact),
        ).fetchone()
        if stored is not None:
            if stored["output_json"] != payload:
                raise StoreConflict(
                    f"acceptance {acceptance} already records {artifact} at port {port!r} with"
                    " different content"
                )
            return content_hash_of(dict(document))
        self._insert(
            "INSERT INTO acceptance_outputs(mission_id,acceptance_id,output_port,artifact_id,"
            "producer_occurrence,producer_task_ref,producer_result_id,support_revision,"
            "content_hash,source_revision,output_json,recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                mission,
                acceptance,
                port,
                artifact,
                identifier(producer_occurrence, "producer_occurrence"),
                identifier(producer_task_ref, "producer_task_ref"),
                str(producer_result_id),
                index(support_revision, "support_revision"),
                str(content_hash),
                str(source_revision),
                payload,
                self._store.now,
            ),
            f"acceptance output {acceptance}.{port} could not be stored",
        )
        return content_hash_of(dict(document))

    def list_acceptance_outputs(
        self, mission_id: str, *, producer_occurrence: str | None = None
    ) -> tuple[dict[str, Any], ...]:
        """The recorded accepted outputs of one Mission, in a deterministic order.

        **Joined to ``acceptances`` and filtered on validity** (P2.3c part 2c, review
        F3).  ``acceptance_outputs`` has no validity column of its own — an accepted
        output is a fact about an ``Acceptance``, and duplicating the Acceptance's
        state here would be a second answer that can disagree with the first.  So the
        row is offered only while the Acceptance that recorded it is still CURRENT: a
        superseded or revoked Acceptance is history, and letting the resolver bind a
        consumer to its output would feed downstream work on the strength of an
        acceptance nobody holds any more.  ``root_contributions`` already reads the
        ``acceptances`` rows for exactly this reason; this is the same rule on the
        other lane.

        An output whose Acceptance row is missing altogether is likewise not offered:
        the index entry is a claim about an Acceptance, and an Acceptance the library
        does not hold cannot support it.
        """

        placeholders = ",".join("?" for _ in self.USABLE_ACCEPTANCE_VALIDITY)
        sql = (
            "SELECT o.output_json AS output_json FROM acceptance_outputs AS o"
            " JOIN acceptances AS a ON a.acceptance_id = o.acceptance_id"
            f" WHERE o.mission_id = ? AND a.validity IN ({placeholders})"
        )
        values: tuple[Any, ...] = (
            identifier(mission_id, "mission_id"),
            *self.USABLE_ACCEPTANCE_VALIDITY,
        )
        if producer_occurrence is not None:
            sql += " AND o.producer_occurrence = ?"
            values += (str(producer_occurrence),)
        rows = self._store.connection.execute(
            sql
            + " ORDER BY o.producer_occurrence, o.output_port, o.source_revision, o.artifact_id",
            values,
        )
        return tuple(dict(json.loads(row["output_json"])) for row in rows)

    @staticmethod
    def _acceptance_receipt(row: sqlite3.Row) -> AcceptanceCommitReceipt:
        return AcceptanceCommitReceipt(
            mission_id=row["mission_id"],
            command_id=row["command_id"],
            kind=row["kind"],
            subject_id=row["subject_id"],
            intent_hash=row["intent_hash"],
            read_set_hash=row["read_set_hash"],
            event_id=row["event_id"],
            output_identity=dict(json.loads(row["output_identity_json"])),
            detail=dict(json.loads(row["detail_json"])),
        )

    @staticmethod
    def _commit_receipt(row: sqlite3.Row) -> PlanCommitReceipt:
        return PlanCommitReceipt(
            command_id=str(row["command_id"]),
            mission_id=str(row["mission_id"]),
            delta_id=str(row["delta_id"]),
            base_plan_revision=int(row["base_plan_revision"]),
            new_plan_revision=int(row["new_plan_revision"]),
            intent_hash=str(row["intent_hash"]),
            read_set_hash=str(row["read_set_hash"]),
            read_set=SemanticReadSet.from_json(json.loads(row["read_set_json"])),
            output_identity=json.loads(row["output_identity_json"]),
            detail=json.loads(row["receipt_json"]),
        )
