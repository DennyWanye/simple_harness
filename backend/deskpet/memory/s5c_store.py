# SPDX-License-Identifier: BUSL-1.1
"""S5c domain persistence and read-only authority ports, not a scheduler/issuer.

Only trusted Host composition may persist prepared registration commands. There
is no Tool/API route into these methods. The action journal records requests;
action grant issuance is deliberately left to T5's exact authorization path.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
from simple_harness.contracts import canonical_json
from simple_harness.runtime import (
    MemoryActionAuthority,
    MemoryActionAuthorityRef,
    MemoryActionIntent,
    ProspectiveSignalAuthority,
    ProspectiveSignalAuthorityRef,
)
from simple_harness_memory import MemoryPrincipal
from simple_harness_memory.core.lifecycle_results import ProspectiveSignalApplyResult
from simple_harness_memory.core.occurrence import OccurrenceInboxEntryV1, OutboxEntryV1

from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.s5c_timer_schema import validate_s5c_domain_state_db


class S5cConflict(ValueError):
    """Stable rejection; caller must not invent new signal/plan identities."""


def _json_containers(value):
    """Materialize public immutable JSON containers without changing their bytes."""
    if isinstance(value, Mapping):
        return {key: _json_containers(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_containers(item) for item in value]
    return value


def _hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _owner(principal: MemoryPrincipal) -> str:
    if type(principal) is not MemoryPrincipal:
        raise TypeError("MemoryPrincipal required")
    # Session is not the ownership boundary: later Runs must see the same inbox.
    return _hash([principal.deployment_id, principal.household_id, principal.actor_id])


def registration_signal_id(
    principal: MemoryPrincipal, outbox_id: str, kind: str
) -> str:
    return _hash(["host:prospective-signal/v1", _owner(principal), outbox_id, kind])


@dataclass(frozen=True)
class PreparedRegistration:
    entry: OutboxEntryV1
    authority: ProspectiveSignalAuthority
    result: ProspectiveSignalApplyResult | None = None

    @property
    def reference(self) -> ProspectiveSignalAuthorityRef:
        return ProspectiveSignalAuthorityRef.from_authority(self.authority)


class S5cStore:
    def __init__(
        self,
        db_path: str | Path,
        principal: MemoryPrincipal,
        *,
        fault_inject: Callable[[str], None] | None = None,
    ):
        self.path = Path(db_path)
        self.principal = principal
        self.owner = _owner(principal)
        self.fault = fault_inject
        validate_s5c_domain_state_db(self.path)
        with sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True) as db:
            self.cursor_table = (
                "prospective_outbox_cursor_v52" if db.execute("PRAGMA user_version").fetchone() in ((52,), (53,))
                else "prospective_outbox_cursor"
            )

    @asynccontextmanager
    async def _transaction(self):
        async with aiosqlite.connect(
            f"{self.path.resolve().as_uri()}?mode=rw", uri=True
        ) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            await db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def _cursor_row(self, db):
        cursor = await db.execute(
            f"SELECT * FROM {self.cursor_table} WHERE owner_key=? "
            "ORDER BY sequence DESC LIMIT 1",
            (self.owner,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def cursor(self) -> tuple[float, str] | None:
        async with aiosqlite.connect(
            f"{self.path.resolve().as_uri()}?mode=ro", uri=True
        ) as db:
            db.row_factory = aiosqlite.Row
            row = await self._cursor_row(db)
        return None if row is None else (row["after_time"], row["after_id"])

    def _terminal_record(self, entry, receipt, expected_source_hash):
        from simple_harness_memory import ProspectiveInvalidationNotRequiredReceipt
        if type(entry) is not OutboxEntryV1 or type(receipt) is not ProspectiveInvalidationNotRequiredReceipt:
            raise TypeError("typed invalidation entry and terminal receipt required")
        checked = ProspectiveInvalidationNotRequiredReceipt.from_json(receipt.to_json())
        payload = dict(entry.payload) if entry.payload is not None else {}
        if (checked.receipt_hash != receipt.receipt_hash
                or (receipt.deployment_id, receipt.household_id, receipt.subject) != (
                    self.principal.deployment_id, self.principal.household_id, self.principal.actor_id)
                or receipt.target_source_hash != expected_source_hash
                or (receipt.outbox_id, receipt.outbox_payload_hash, receipt.outbox_created_at) != (
                    entry.outbox_id, entry.payload_hash, entry.created_at)
                or entry.topic != "memory.prospective.invalidation.requested"
                or entry.idempotency_key != entry.outbox_id
                or set(payload) != {"schema_version", "command", "memory_id", "prospective_revision",
                                     "registration_revision", "trigger", "trigger_hash"}
                or payload.get("schema_version") != 1 or payload.get("command") != "invalidation"
                or payload.get("memory_id") != receipt.memory_id
                or payload.get("prospective_revision") != receipt.target_revision
                or payload.get("registration_revision") != receipt.registration_revision
                or payload.get("trigger_hash") != receipt.trigger_hash or _hash(payload) != entry.payload_hash):
            raise S5cConflict("s5c_terminal_source_differs")
        source = dict(outbox_id=entry.outbox_id, topic=entry.topic, payload=payload,
                      payload_hash=entry.payload_hash, created_at=entry.created_at,
                      idempotency_key=entry.idempotency_key)
        return (
            _hash(["s5c:invalidation-terminal", self.owner, entry.outbox_id]), self.owner,
            entry.outbox_id, "not_required", canonical_json(source), _hash(source),
            canonical_json(receipt.to_json()), receipt.receipt_hash,
            _hash(["s5c:invalidation-not-required/v1", self.owner, source, receipt.to_json(), receipt.receipt_hash]),
        )

    def _decode_terminal(self, row):
        from simple_harness_memory import ProspectiveInvalidationNotRequiredReceipt
        try:
            source = json.loads(row["source_json"])
            receipt = ProspectiveInvalidationNotRequiredReceipt.from_json(json.loads(row["receipt_json"]))
            entry = OutboxEntryV1(source["outbox_id"], source["topic"], source["idempotency_key"],
                "pending", source["payload_hash"], 0, source["created_at"], source["created_at"],
                source["created_at"], source["payload"])
            if tuple(row) != self._terminal_record(entry, receipt, receipt.target_source_hash):
                raise ValueError("terminal identity")
            return entry, receipt
        except (ValueError, TypeError, KeyError) as error:
            raise S5cConflict("s5c_terminal_corrupt") from error

    async def terminal(self, outbox_id):
        if self.cursor_table != "prospective_outbox_cursor_v52":
            return None
        async with aiosqlite.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            async with db.execute("SELECT * FROM prospective_invalidation_terminals "
                    "WHERE owner_key=? AND outbox_id=?", (self.owner, outbox_id)) as query:
                row = await query.fetchone()
            if row is None:
                return None
            entry, receipt = self._decode_terminal(row)
            async with db.execute("SELECT * FROM prospective_outbox_cursor_v52 WHERE terminal_record_id=?",
                                  (row["record_id"],)) as query:
                cursor = await query.fetchone()
            if (cursor is None or cursor["owner_key"] != self.owner
                    or cursor["registration_record_id"] is not None
                    or (cursor["after_time"], cursor["after_id"]) != (entry.created_at, entry.outbox_id)
                    or cursor["cursor_hash"] != _hash(["s5c:cursor/not-required/v1", self.owner,
                        cursor["sequence"], [entry.created_at, entry.outbox_id], row["record_id"], cursor["prior_hash"]])):
                raise S5cConflict("s5c_terminal_cursor_corrupt")
            return receipt

    async def commit_not_required(self, entry, receipt, *, expected_source_hash, expected_cursor):
        if self.cursor_table != "prospective_outbox_cursor_v52":
            raise S5cConflict("s5c_terminal_schema_required")
        record = self._terminal_record(entry, receipt, expected_source_hash)
        async with self._transaction() as db:
            async with db.execute("SELECT * FROM prospective_invalidation_terminals "
                    "WHERE owner_key=? AND outbox_id=?", (self.owner, entry.outbox_id)) as query:
                existing = await query.fetchone()
            if existing is not None:
                if tuple(existing) != record:
                    raise S5cConflict("s5c_terminal_replay_differs")
                # The first terminal and its cursor are committed atomically.
                # Reopening through terminal() verifies their exact join.
                if await self.terminal(entry.outbox_id) is None:
                    raise S5cConflict("s5c_terminal_cursor_corrupt")
                return receipt
            async with db.execute("SELECT 1 FROM prospective_scheduler_registrations "
                    "WHERE owner_key=? AND outbox_id=?", (self.owner, entry.outbox_id)) as query:
                if await query.fetchone() is not None:
                    raise S5cConflict("s5c_terminal_has_registration")
            row = await self._cursor_row(db)
            current = None if row is None else (row["after_time"], row["after_id"])
            after = (entry.created_at, entry.outbox_id)
            if current != expected_cursor or (current is not None and after <= current):
                raise S5cConflict("s5c_cursor_conflict")
            await db.execute("INSERT INTO prospective_invalidation_terminals VALUES (?,?,?,?,?,?,?,?,?)", record)
            sequence, prior = (1, "0"*64) if row is None else (row["sequence"]+1, row["cursor_hash"])
            digest = _hash(["s5c:cursor/not-required/v1", self.owner, sequence, list(after), record[0], prior])
            await db.execute("INSERT INTO prospective_outbox_cursor_v52 VALUES (?,?,?,?,?,?,?,?)",
                (self.owner, sequence, *after, None, prior, digest, record[0]))
            if self.fault:
                self.fault("s5c.terminal.before_commit")
        if self.fault:
            self.fault("s5c.terminal.after_commit")
        return receipt

    def _registration(self, entry, authority):
        if (
            type(entry) is not OutboxEntryV1
            or type(authority) is not ProspectiveSignalAuthority
        ):
            raise TypeError("typed registration and authority required")
        intent = authority.intent
        kind = intent.signal_kind.value
        command = {
            "registration_accepted": "registration",
            "registration_invalidated": "invalidation",
        }.get(kind)
        scope = intent.scope
        owner_id = (
            self.principal.actor_id
            if scope.kind.value == "personal"
            else self.principal.household_id
        )
        if (
            intent.subject != self.principal.actor_id
            or scope.owner_id != owner_id
            or authority.issuer_ref != "host:prospective-signal/v1"
            or command is None
        ):
            raise S5cConflict("s5c_registration_authority_scope_differs")
        expected = {
            "schema_version": 1,
            "command": command,
            "memory_id": intent.target_memory_id,
            "prospective_revision": intent.target_revision,
            "registration_revision": intent.registration_revision,
            "trigger": intent.trigger.to_json(),
            "trigger_hash": intent.trigger_hash,
        }
        if (
            entry.topic != f"memory.prospective.{command}.requested"
            or entry.payload is None
            or canonical_json(_json_containers(entry.payload)) != canonical_json(expected)
            or entry.payload_hash != _hash(expected)
            or intent.outbox_id != entry.outbox_id
            or intent.outbox_payload_hash != entry.payload_hash
            or intent.signal_id
            != registration_signal_id(self.principal, entry.outbox_id, kind)
        ):
            raise S5cConflict("s5c_registration_source_differs")
        # Only immutable outbox fields: retry counters/state are not source identity.
        source = {
            "outbox_id": entry.outbox_id,
            "topic": entry.topic,
            "payload": expected,
            "payload_hash": entry.payload_hash,
            "created_at": entry.created_at,
            "idempotency_key": entry.idempotency_key,
        }
        record_id = _hash(["s5c:registration", self.owner, entry.outbox_id])
        digest = _hash([self.owner, source, authority.to_json()])
        return source, record_id, digest

    async def commit_registration(
        self,
        entry: OutboxEntryV1,
        authority: ProspectiveSignalAuthority,
        *,
        expected_cursor: tuple[float, str] | None,
        advance_cursor: bool = True,
    ) -> ProspectiveSignalAuthorityRef:
        """One ordered outbox entry + fixed prepared signal + cursor, one transaction.

        The future consumer supplies contiguous entries from the principal's
        public read_outbox page. This store never reads/writes Memory's SQLite.
        """
        if type(advance_cursor) is not bool:
            raise TypeError("advance_cursor must be bool")
        source, record_id, digest = self._registration(entry, authority)
        async with self._transaction() as db:
            cursor = await db.execute(
                "SELECT record_hash FROM prospective_scheduler_registrations "
                "WHERE record_id=?",
                (record_id,),
            )
            existing = await cursor.fetchone()
            await cursor.close()
            if existing is not None:
                if existing["record_hash"] != digest:
                    raise S5cConflict("s5c_registration_replay_differs")
                async with db.execute(
                    f"SELECT 1 FROM {self.cursor_table} WHERE registration_record_id=?", (record_id,)
                ) as committed:
                    already_advanced = await committed.fetchone() is not None
                if not advance_cursor or already_advanced:
                    return ProspectiveSignalAuthorityRef.from_authority(authority)
            row = await self._cursor_row(db)
            current = None if row is None else (row["after_time"], row["after_id"])
            after = (entry.created_at, entry.outbox_id)
            if advance_cursor and (current != expected_cursor or (current is not None and after <= current)):
                raise S5cConflict("s5c_cursor_conflict")
            if existing is None:
                await db.execute(
                    "INSERT INTO prospective_scheduler_registrations "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        record_id,
                        self.owner,
                        entry.outbox_id,
                        "prepared",
                        canonical_json(source),
                        _hash(source),
                        authority.authority_id,
                        canonical_json(authority.to_json()),
                        authority.authority_hash,
                        digest,
                    ),
                )
            if not advance_cursor:
                if self.fault:
                    self.fault("s5c.registration.before_commit")
                return ProspectiveSignalAuthorityRef.from_authority(authority)
            seq, prior = (
                (1, "0" * 64)
                if row is None
                else (row["sequence"] + 1, row["cursor_hash"])
            )
            cursor_hash = _hash([self.owner, seq, list(after), record_id, prior])
            await db.execute(
                f"INSERT INTO {self.cursor_table} "
                "(owner_key,sequence,after_time,after_id,registration_record_id,prior_hash,cursor_hash) "
                "VALUES (?,?,?,?,?,?,?)",
                (self.owner, seq, *after, record_id, prior, cursor_hash),
            )
            if self.fault:
                self.fault("s5c.registration.before_commit")
        if self.fault:
            self.fault("s5c.registration.after_commit")
        return ProspectiveSignalAuthorityRef.from_authority(authority)

    def _decode_prepared(self, row) -> PreparedRegistration:
        try:
            source = json.loads(row["source_json"])
            authority = ProspectiveSignalAuthority.from_json(
                json.loads(row["authority_json"])
            )
            entry = OutboxEntryV1(
                source["outbox_id"],
                source["topic"],
                source["idempotency_key"],
                "pending",
                source["payload_hash"],
                0,
                source["created_at"],
                source["created_at"],
                source["created_at"],
                source["payload"],
            )
            expected, record_id, digest = self._registration(entry, authority)
            if (
                source != expected
                or row["owner_key"] != self.owner
                or row["phase"] != "prepared"
                or row["outbox_id"] != entry.outbox_id
                or row["record_id"] != record_id
                or row["record_hash"] != digest
                or row["source_hash"] != _hash(source)
                or row["authority_id"] != authority.authority_id
                or row["authority_hash"] != authority.authority_hash
            ):
                raise ValueError("prepared identity differs")
        except (KeyError, TypeError, ValueError) as exc:
            raise S5cConflict("s5c_registration_corrupt") from exc
        return PreparedRegistration(entry, authority)

    def _result_record(self, prepared, result):
        if type(result) is not ProspectiveSignalApplyResult:
            raise TypeError("ProspectiveSignalApplyResult required")
        i = prepared.authority.intent
        if (
            result.signal_id != i.signal_id
            or result.memory_id != i.target_memory_id
            or result.base_revision != i.target_revision
            or result.committed_revision != i.target_revision
            or result.lifecycle_state != i.transition_to
            or result.outcome.value != "acknowledged"
            or result.reason_code != "prospective_registration_acknowledged"
            or not math.isfinite(result.decided_at)
            or not max(i.observed_at, prepared.authority.issued_at)
            <= result.decided_at
            < prepared.authority.expires_at
        ):
            raise S5cConflict("s5c_registration_result_differs")
        _, prepared_id, prepared_hash = self._registration(
            prepared.entry, prepared.authority
        )
        # Phase-specific receipt envelope, linked to the immutable original;
        # no new schema column and no rewriting the prepared source or cursor.
        body = {
            "schema_version": 1,
            "prepared_record_id": prepared_id,
            "prepared_record_hash": prepared_hash,
            "result": result.to_json(),
            "result_hash": result.result_hash,
        }
        return (
            _hash(["s5c:registration-result", self.owner, prepared.entry.outbox_id]),
            self.owner,
            prepared.entry.outbox_id,
            "applied",
            canonical_json(body),
            _hash(body),
            prepared.authority.authority_id,
            canonical_json(prepared.authority.to_json()),
            prepared.authority.authority_hash,
            _hash(
                [
                    "s5c:registration-result/v1",
                    self.owner,
                    body,
                    prepared.reference.to_json(),
                ]
            ),
        )

    async def _read_registration_tx(self, db, row):
        prepared = self._decode_prepared(row)
        cursor = await db.execute(
            "SELECT * FROM prospective_scheduler_registrations "
            "WHERE owner_key=? AND outbox_id=? AND phase='applied'",
            (self.owner, prepared.entry.outbox_id),
        )
        applied = await cursor.fetchone()
        await cursor.close()
        if applied is None:
            return prepared
        try:
            result = ProspectiveSignalApplyResult.from_json(
                json.loads(applied["source_json"])["result"]
            )
            if tuple(applied) != self._result_record(prepared, result):
                raise ValueError("receipt differs")
        except (KeyError, TypeError, ValueError) as exc:
            raise S5cConflict("s5c_registration_result_corrupt") from exc
        return PreparedRegistration(prepared.entry, prepared.authority, result)

    async def registration(self, outbox_id: str) -> PreparedRegistration | None:
        async with aiosqlite.connect(
            f"{self.path.resolve().as_uri()}?mode=ro", uri=True
        ) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            cursor = await db.execute(
                "SELECT * FROM prospective_scheduler_registrations "
                "WHERE owner_key=? AND outbox_id=? AND phase='prepared'",
                (self.owner, outbox_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
            return None if row is None else await self._read_registration_tx(db, row)

    async def accepted_registration(self, *, memory_id: str, revision: int) -> PreparedRegistration | None:
        """Read the actual acknowledged Host registration for a target revision.

        This queries Host facts, never computes a Memory outbox identifier.
        Multiple bindings are an inconsistency rather than a newest-row choice.
        """
        if not isinstance(memory_id, str) or not memory_id or type(revision) is not int or revision < 1:
            raise ValueError("s5c_registration_target_invalid")
        async with aiosqlite.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            async with db.execute(
                "SELECT p.* FROM prospective_scheduler_registrations p "
                "WHERE p.owner_key=? AND p.phase='prepared' "
                "AND json_extract(p.source_json,'$.payload.command')='registration' "
                "AND json_extract(p.source_json,'$.payload.memory_id')=? "
                "AND json_extract(p.source_json,'$.payload.prospective_revision')=? LIMIT 2",
                (self.owner, memory_id, revision),
            ) as cursor:
                rows = await cursor.fetchall()
            if len(rows) > 1:
                raise S5cConflict("s5c_registration_target_ambiguous")
            if not rows:
                return None
            prepared = await self._read_registration_tx(db, rows[0])
            return prepared if prepared.result is not None else None

    async def page_accepted_registrations(self, *, after: int = 0,
                                          upper: int | None = None, limit: int = 100):
        """Bounded Host cursor window; cursor is scan progress, not delivery ACK."""
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("s5c_registration_page_invalid")
        if upper is not None and (type(upper) is not int or upper < after):
            raise ValueError("s5c_registration_page_upper_invalid")
        async with aiosqlite.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            if upper is None:
                row = await (await db.execute(f"SELECT COALESCE(MAX(sequence),0) FROM {self.cursor_table} WHERE owner_key=?", (self.owner,))).fetchone()
                upper = row[0]
            rows = await (await db.execute(
                f"SELECT c.sequence,p.* FROM {self.cursor_table} c "
                "JOIN prospective_scheduler_registrations p ON p.record_id=c.registration_record_id "
                "WHERE c.owner_key=? AND c.sequence>? AND c.sequence<=? "
                "ORDER BY c.sequence LIMIT ?", (self.owner, after, upper, limit))).fetchall()
            accepted = []
            for row in rows:
                prepared = await self._read_registration_tx(db, row)
                if prepared.result is not None and prepared.entry.payload['command'] == 'registration':
                    accepted.append(prepared)
            return tuple(accepted), (rows[-1]['sequence'] if rows else upper), upper

    async def accepted_invalidation(self, *, memory_id: str, revision: int,
                                    registration_revision: int, registration_ref: str):
        """Read exact ACKed invalidation; pending commands are not ACK facts."""
        async with aiosqlite.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            rows = await (await db.execute(
                "SELECT * FROM prospective_scheduler_registrations WHERE owner_key=? AND phase='prepared' "
                "AND json_extract(source_json,'$.payload.command')='invalidation' "
                "AND json_extract(source_json,'$.payload.memory_id')=? "
                "AND json_extract(source_json,'$.payload.prospective_revision')=? "
                "AND json_extract(source_json,'$.payload.registration_revision')=?",
                (self.owner, memory_id, revision, registration_revision))).fetchall()
            found = None
            for row in rows:
                prepared = await self._read_registration_tx(db, row)
                if prepared.authority.intent.scheduler_registration_ref == registration_ref and prepared.result is not None:
                    if found is not None:
                        raise S5cConflict('s5c_invalidation_ambiguous')
                    found = prepared
            return found

    async def pending_registrations(
        self, *, limit: int = 100
    ) -> tuple[PreparedRegistration, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("registration limit must be 1..1000")
        async with aiosqlite.connect(
            f"{self.path.resolve().as_uri()}?mode=ro", uri=True
        ) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            cursor = await db.execute(
                "SELECT p.* FROM prospective_scheduler_registrations p "
                f"LEFT JOIN {self.cursor_table} c "
                "ON c.registration_record_id=p.record_id "
                "WHERE p.owner_key=? AND p.phase='prepared' AND NOT EXISTS "
                "(SELECT 1 FROM prospective_scheduler_registrations a "
                "WHERE a.owner_key=p.owner_key "
                "AND a.outbox_id=p.outbox_id AND a.phase='applied') "
                "ORDER BY CASE WHEN c.sequence IS NULL THEN 0 ELSE 1 END,c.sequence,p.outbox_id LIMIT ?",
                (self.owner, limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            return tuple(self._decode_prepared(row) for row in rows)

    async def commit_registration_result(
        self, reference, result
    ) -> ProspectiveSignalApplyResult:
        if type(reference) is not ProspectiveSignalAuthorityRef:
            raise TypeError("ProspectiveSignalAuthorityRef required")
        async with self._transaction() as db:
            cursor = await db.execute(
                "SELECT * FROM prospective_scheduler_registrations "
                "WHERE owner_key=? AND authority_id=? AND phase='prepared'",
                (self.owner, reference.authority_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                raise S5cConflict("s5c_authority_not_found")
            prepared = await self._read_registration_tx(db, row)
            if prepared.reference != reference:
                raise S5cConflict("s5c_registration_result_ref_differs")
            values = self._result_record(prepared, result)
            if prepared.result is not None:
                if prepared.result != result:
                    raise S5cConflict("s5c_registration_result_replay_differs")
                return prepared.result
            await db.execute(
                "INSERT INTO prospective_scheduler_registrations "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                values,
            )
            if self.fault:
                self.fault("s5c.registration_result.before_commit")
        if self.fault:
            self.fault("s5c.registration_result.after_commit")
        return result

    async def claim_occurrence(self, entry: OccurrenceInboxEntryV1) -> str:
        """Persist an inbox claim; does not present/ack/settle or authorize content."""
        if type(entry) is not OccurrenceInboxEntryV1:
            raise TypeError("OccurrenceInboxEntryV1 required")
        if (
            entry.outcome != "matched"
            or entry.suppressed
            or entry.lifecycle_state.lower()
            not in {"triggered", "in_progress", "rescheduled"}
        ):
            raise S5cConflict("s5c_occurrence_not_live")
        body = entry.to_json()
        digest = _hash([self.owner, body])
        record_id = _hash(["s5c:claim", self.owner, entry.occurrence_key])
        async with self._transaction() as db:
            cursor = await db.execute(
                "SELECT record_hash FROM prospective_occurrences WHERE record_id=?",
                (record_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is not None:
                if row["record_hash"] != digest:
                    raise S5cConflict("s5c_occurrence_replay_differs")
                return record_id
            await db.execute(
                "INSERT INTO prospective_occurrences VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    record_id,
                    self.owner,
                    entry.occurrence_key,
                    "claimed",
                    None,
                    None,
                    None,
                    canonical_json(body),
                    digest,
                ),
            )
        return record_id

    async def record_action_request(
        self, action_id: str, intent: MemoryActionIntent
    ) -> str:
        """Journal a grounded request, NOT permission to mutate/suppress Memory.

        No authorized writer exists in this slice. T5 must bind an exact Host
        approval/plan before appending an authorized row for the read-only port.
        """
        if type(intent) is not MemoryActionIntent:
            raise TypeError("MemoryActionIntent required")
        if (
            not isinstance(action_id, str)
            or not action_id.strip()
            or "\x00" in action_id
        ):
            raise S5cConflict("s5c_action_id_invalid")
        if intent.subject != self.principal.actor_id:
            raise S5cConflict("s5c_action_subject_differs")
        source = HostEvidenceAuthority(self.path)
        for ref in intent.evidence_refs:
            envelope, _ = await source.read_admitted(ref.evidence_id)
            if (
                envelope.envelope_hash != ref.content_hash
                or envelope.subject != intent.subject
                or envelope.run_id != intent.run_id
            ):
                raise S5cConflict("s5c_action_evidence_differs")
        body = intent.to_json()
        digest = _hash([self.owner, action_id, "requested", body])
        async with self._transaction() as db:
            cursor = await db.execute(
                "SELECT record_hash FROM memory_action_events "
                "WHERE owner_key=? AND action_id=? AND phase='requested'",
                (self.owner, action_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
            if row is not None:
                if row["record_hash"] != digest:
                    raise S5cConflict("s5c_action_replay_differs")
                return digest
            await db.execute(
                "INSERT INTO memory_action_events VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    action_id,
                    "requested",
                    self.owner,
                    canonical_json(body),
                    intent.intent_hash,
                    None,
                    None,
                    None,
                    digest,
                ),
            )
        return digest


class HostProspectiveSignalAuthority:
    def __init__(self, db_path: str | Path, principal: MemoryPrincipal):
        self.store = S5cStore(db_path, principal)

    async def resolve_prospective_signal_authority(
        self, reference: ProspectiveSignalAuthorityRef
    ) -> ProspectiveSignalAuthority:
        if type(reference) is not ProspectiveSignalAuthorityRef:
            raise TypeError("ProspectiveSignalAuthorityRef required")
        async with aiosqlite.connect(
            f"{self.store.path.resolve().as_uri()}?mode=ro", uri=True
        ) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM prospective_scheduler_registrations "
                "WHERE owner_key=? AND authority_id=? AND phase='prepared'",
                (self.store.owner, reference.authority_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            raise S5cConflict("s5c_authority_not_found")
        try:
            authority = ProspectiveSignalAuthority.from_json(
                json.loads(row["authority_json"])
            )
            source = json.loads(row["source_json"])
            if (
                authority.authority_hash != row["authority_hash"]
                or ProspectiveSignalAuthorityRef.from_authority(authority) != reference
                or _hash(source) != row["source_hash"]
                or _hash([self.store.owner, source, authority.to_json()])
                != row["record_hash"]
            ):
                raise ValueError("hash differs")
            entry = OutboxEntryV1(
                source["outbox_id"],
                source["topic"],
                source["idempotency_key"],
                "pending",
                source["payload_hash"],
                0,
                source["created_at"],
                source["created_at"],
                source["created_at"],
                source["payload"],
            )
            _, record_id, _ = self.store._registration(entry, authority)
            if record_id != row["record_id"] or source["outbox_id"] != row["outbox_id"]:
                raise ValueError("identity differs")
        except (KeyError, TypeError, ValueError) as exc:
            raise S5cConflict("s5c_authority_corrupt_or_ref_differs") from exc
        # SDK owns expiry/consumption checks; an applied lost-ACK replay can use
        # its stored receipt even after this immutable authority has expired.
        return authority


class HostMemoryActionAuthority:
    """Read-only exact resolver. No grant issuance or Tool exposure in T2."""

    def __init__(self, db_path: str | Path, principal: MemoryPrincipal):
        self.store = S5cStore(db_path, principal)

    async def resolve_memory_action_authority(
        self, reference: MemoryActionAuthorityRef
    ) -> MemoryActionAuthority:
        if type(reference) is not MemoryActionAuthorityRef:
            raise TypeError("MemoryActionAuthorityRef required")
        async with aiosqlite.connect(
            f"{self.store.path.resolve().as_uri()}?mode=ro", uri=True
        ) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT a.*,r.request_json AS original_request,"
                "r.record_hash AS original_hash FROM memory_action_events a "
                "JOIN memory_action_events r ON r.owner_key=a.owner_key "
                "AND r.action_id=a.action_id AND r.phase='requested' "
                "WHERE a.owner_key=? AND a.authority_id=? AND a.phase='authorized'",
                (self.store.owner, reference.authority_id),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:
            raise S5cConflict("s5c_action_authority_not_found")
        try:
            authority = MemoryActionAuthority.from_json(
                json.loads(row["authority_json"])
            )
            body = authority.intent.to_json()
            if (
                authority.intent.subject != self.store.principal.actor_id
                or authority.issuer_ref != "host:memory-action/v1"
                or canonical_json(body) != row["original_request"]
                or canonical_json(body) != row["request_json"]
                or authority.intent.intent_hash != row["request_hash"]
                or authority.authority_hash != row["authority_hash"]
                or MemoryActionAuthorityRef.from_authority(authority) != reference
                or _hash([self.store.owner, row["action_id"], "requested", body])
                != row["original_hash"]
                or _hash(
                    [
                        self.store.owner,
                        row["action_id"],
                        "authorized",
                        authority.to_json(),
                    ]
                )
                != row["record_hash"]
            ):
                raise ValueError("binding differs")
        except (KeyError, TypeError, ValueError) as exc:
            raise S5cConflict("s5c_action_authority_corrupt_or_ref_differs") from exc
        return authority
