from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from deskpet.memory.human_memory_program import (
    HumanMemoryProgramConflict,
    HumanMemoryProgramProtocolError,
    HumanMemoryProgramStore,
    PrimaryConversationMismatch,
)
from deskpet.memory.schema import (
    HumanMemoryProgramEpochError,
    InitializeError,
    initialize_state_db,
)
from deskpet.memory.session_db import SessionDB


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _disclosure(run_id: str, subject: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "subject": subject,
        "recipient": "user_self",
        "recipient_id": subject,
        "intended_audience": "user_self",
        "purpose": "personalization",
        "source": "authenticated_host",
        "trust": "trusted_authority",
        "generation": "current",
        "authority_ref": "host-decision-1",
        "reason_codes": ["disclosure_minimum_necessary"],
    }


@dataclass
class FakeS1Envelope:
    evidence_id: str
    run_id: str
    subject: str
    source_kind: str
    source_ref: str
    sanitized_payload: dict[str, Any]
    source_hash: str = "a" * 64
    filter_policy_version: str = "credential-filter/v1"
    sanitized_hash: str = field(init=False)
    envelope_hash: str = field(init=False)

    def __post_init__(self) -> None:
        self.sanitized_hash = _hash(self.sanitized_payload)
        self.envelope_hash = _hash(self.to_json())

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "evidence_id": self.evidence_id,
            "run_id": self.run_id,
            "subject": self.subject,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "source_hash": self.source_hash,
            "sanitized_payload": self.sanitized_payload,
            "sanitized_hash": self.sanitized_hash,
            "filter_policy_version": self.filter_policy_version,
            "removed_spans": [],
            "disclosure_context": _disclosure(self.run_id, self.subject),
            "evidence_refs": [],
        }


@dataclass
class FakeS1Receipt:
    envelope: FakeS1Envelope
    accepted: bool = True
    receipt_id: str = field(init=False)
    run_id: str = field(init=False)
    subject: str = field(init=False)
    evidence_id: str = field(init=False)
    envelope_hash: str = field(init=False)
    source_hash: str = field(init=False)
    sanitized_hash: str = field(init=False)
    filter_policy_version: str = field(init=False)
    admitted_at: float = 10.0
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        self.receipt_id = f"receipt-{self.envelope.evidence_id}"
        self.run_id = self.envelope.run_id
        self.subject = self.envelope.subject
        self.evidence_id = self.envelope.evidence_id
        self.envelope_hash = self.envelope.envelope_hash
        self.source_hash = self.envelope.source_hash
        self.sanitized_hash = self.envelope.sanitized_hash
        self.filter_policy_version = self.envelope.filter_policy_version
        self.receipt_hash = _hash(self.to_json())

    def verify(self, envelope: FakeS1Envelope) -> None:
        if not self.accepted or envelope.envelope_hash != self.envelope_hash:
            raise ValueError("fake_s1_receipt_rejected")

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "receipt_id": self.receipt_id,
            "run_id": self.run_id,
            "subject": self.subject,
            "evidence_id": self.evidence_id,
            "envelope_hash": self.envelope_hash,
            "source_hash": self.source_hash,
            "sanitized_hash": self.sanitized_hash,
            "filter_policy_version": self.filter_policy_version,
            "accepted": self.accepted,
            "reason_codes": [
                "evidence_sanitized_and_accepted"
                if self.accepted
                else "evidence_credential_canary_rejected"
            ],
            "disclosure_context": _disclosure(self.run_id, self.subject),
            "evidence_refs": [],
            "admitted_at": self.admitted_at,
        }


def _evidence(
    index: int,
    kind: str,
    payload: dict[str, Any],
    *,
    subject: str = "actor-1",
) -> tuple[FakeS1Envelope, FakeS1Receipt]:
    envelope = FakeS1Envelope(
        evidence_id=f"evidence-{index}",
        run_id="run-1",
        subject=subject,
        source_kind=kind,
        source_ref=f"source/{index}",
        sanitized_payload=payload,
    )
    return envelope, FakeS1Receipt(envelope)


@pytest.mark.asyncio
async def test_concurrent_cold_init_creates_one_writable_primary_and_receipt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    receipts = await asyncio.gather(
        *(HumanMemoryProgramStore(db_path).initialize_subject("actor-1") for _ in range(24))
    )
    assert len({item.primary_conversation_id for item in receipts}) == 1
    assert len({item.receipt_id for item in receipts}) == 1

    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 38
        assert db.execute(
            "SELECT COUNT(*) FROM human_memory_primary_conversations "
            "WHERE subject='actor-1' AND writable=1"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM human_memory_init_receipts WHERE subject='actor-1'"
        ).fetchone()[0] == 1
        ddl = "\n".join(
            row[0]
            for row in db.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL "
                "AND name LIKE 'human_memory_%'"
            )
        )
    assert "reasoning_content" not in ddl


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault_stage",
    ["before_human_memory_program_commit", "after_human_memory_program_commit"],
)
async def test_v35_commit_fault_restarts_without_split_primary(
    tmp_path: Path, fault_stage: str
) -> None:
    db_path = tmp_path / "state.db"

    def crash(stage: str) -> None:
        if stage == fault_stage:
            raise RuntimeError(f"crash:{stage}")

    from deskpet.memory.schema import initialize_human_memory_program_state_db

    with pytest.raises(InitializeError, match="human memory program initialization failed"):
        await initialize_human_memory_program_state_db(db_path, fault_inject=crash)
    first = await HumanMemoryProgramStore(db_path).initialize_subject("actor-1")
    second = await HumanMemoryProgramStore(db_path).initialize_subject("actor-1")
    assert first == second


@pytest.mark.asyncio
async def test_existing_v34_is_refused_without_migration_or_deletion(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    await initialize_state_db(db_path)
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO sessions(id,created_at,metadata) "
            "VALUES ('legacy',1,'{\"legacy\":true}')"
        )
        db.commit()
        before = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        before_markers = db.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0]
    before_bytes = db_path.read_bytes()

    with pytest.raises(
        HumanMemoryProgramEpochError,
        match=HumanMemoryProgramEpochError.code,
    ):
        await HumanMemoryProgramStore(db_path).initialize_subject("actor-1")

    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 34
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == before
        assert (
            db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
            == before_markers
        )
        assert db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'human_memory_%'"
        ).fetchone()[0] == 0
    assert db_path.read_bytes() == before_bytes


@pytest.mark.asyncio
async def test_append_all_task1_evidence_kinds_after_receipt(tmp_path: Path) -> None:
    store = HumanMemoryProgramStore(tmp_path / "state.db")
    cases = [
        ("user_message", {"public_text": "hello"}),
        ("assistant_message", {"public_text": "hi"}),
        ("tool_result", {"tool_name": "read_file", "result": "public"}),
        (
            "provider_record",
            {
                "provider_id": "relay-1",
                "provider_type": "openai-compatible",
                "model_id": "model-1",
                "request_hash": "b" * 64,
                "response_hash": "c" * 64,
                "public_content": "answer",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 4,
                    "total_tokens": 14,
                    "reasoning_tokens": 2,
                },
                "opaque_continuation_ref": "provider-item-1",
            },
        ),
        ("runtime_event", {"event": "run_terminal", "status": "completed"}),
    ]
    for index, (kind, payload) in enumerate(cases, 1):
        envelope, receipt = _evidence(index, kind, payload)
        committed = await store.append_evidence(envelope, receipt)
        assert committed.source_kind == kind

    rows = await store.list_evidence("actor-1")
    assert [row.source_kind for row in rows] == [kind for kind, _ in cases]
    replay_envelope, replay_receipt = _evidence(1, *cases[0])
    assert (
        await store.append_evidence(replay_envelope, replay_receipt)
    ).evidence_id == "evidence-1"


@pytest.mark.asyncio
async def test_protocol_and_provider_allowlist_fail_closed_before_insert(
    tmp_path: Path,
) -> None:
    store = HumanMemoryProgramStore(tmp_path / "state.db")
    private_envelope, private_receipt = _evidence(
        1,
        "provider_record",
        {
            "provider_id": "relay-1",
            "provider_type": "relay",
            "request_hash": "b" * 64,
            "response_hash": "c" * 64,
            "reasoning_content": "private",
        },
    )
    with pytest.raises(HumanMemoryProgramProtocolError, match="forbidden_durable_field"):
        await store.append_evidence(private_envelope, private_receipt)

    credential_envelope, credential_receipt = _evidence(
        3,
        "tool_result",
        {"result": {"diagnostic": "Bearer secret-token-value-12345"}},
    )
    with pytest.raises(HumanMemoryProgramProtocolError, match="credential_value_rejected"):
        await store.append_evidence(credential_envelope, credential_receipt)

    bool_schema_envelope, _ = _evidence(
        4, "user_message", {"public_text": "schema canary"}
    )
    original_to_json = bool_schema_envelope.to_json

    def bool_schema_json() -> dict[str, Any]:
        value = original_to_json()
        value["disclosure_context"]["schema_version"] = True
        return value

    bool_schema_envelope.to_json = bool_schema_json  # type: ignore[method-assign]
    bool_schema_envelope.envelope_hash = _hash(bool_schema_envelope.to_json())
    bool_schema_receipt = FakeS1Receipt(bool_schema_envelope)
    with pytest.raises(
        HumanMemoryProgramProtocolError,
        match="disclosure_context_schema_rejected",
    ):
        await store.append_evidence(bool_schema_envelope, bool_schema_receipt)

    rejected_envelope, _ = _evidence(2, "user_message", {"public_text": "no"})
    rejected_receipt = FakeS1Receipt(rejected_envelope, accepted=False)
    with pytest.raises((HumanMemoryProgramProtocolError, ValueError)):
        await store.append_evidence(rejected_envelope, rejected_receipt)

    initialized = await store.initialize_subject("actor-1")
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute("SELECT COUNT(*) FROM human_memory_evidence").fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM human_memory_sanitization_receipts"
        ).fetchone()[0] == 0
    assert initialized.primary_conversation_id


@pytest.mark.asyncio
async def test_raw_evidence_cannot_be_updated_deleted_or_opened_as_legacy(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.db"
    store = HumanMemoryProgramStore(db_path)
    envelope, receipt = _evidence(1, "user_message", {"public_text": "immutable"})
    committed = await store.append_evidence(envelope, receipt)
    with sqlite3.connect(db_path) as db:
        protected_tables = (
            "human_memory_program_bootstrap",
            "human_memory_program_marker",
            "human_memory_primary_conversations",
            "human_memory_init_receipts",
            "human_memory_sanitization_receipts",
            "human_memory_evidence",
        )
        for table in protected_tables:
            with pytest.raises(
                sqlite3.IntegrityError, match="human_memory_append_only"
            ):
                db.execute(f"UPDATE {table} SET rowid=rowid WHERE 1=1")
            db.rollback()
            with pytest.raises(
                sqlite3.IntegrityError, match="human_memory_append_only"
            ):
                db.execute(f"DELETE FROM {table} WHERE 1=1")
            db.rollback()

    with pytest.raises(PrimaryConversationMismatch, match=PrimaryConversationMismatch.code):
        await store.open_primary_conversation(
            "actor-1", requested_conversation_id="legacy-session-id"
        )
    assert await SessionDB(db_path).delete_turn(999999) is False
    rows = await store.list_evidence("actor-1")
    assert [row.evidence_id for row in rows] == [committed.evidence_id]
    # WAL/header activity may change file bytes after reopening; the durable
    # business hash itself must stay exact.
    with sqlite3.connect(db_path) as db:
        stored = db.execute(
            "SELECT envelope_sha256,payload_json FROM human_memory_evidence"
        ).fetchone()
    assert stored == (envelope.envelope_hash, _canonical(envelope.sanitized_payload))


@pytest.mark.asyncio
async def test_same_evidence_id_with_different_payload_conflicts(tmp_path: Path) -> None:
    store = HumanMemoryProgramStore(tmp_path / "state.db")
    envelope, receipt = _evidence(1, "user_message", {"public_text": "one"})
    await store.append_evidence(envelope, receipt)
    changed, changed_receipt = _evidence(
        1, "user_message", {"public_text": "different"}
    )
    with pytest.raises(HumanMemoryProgramConflict):
        await store.append_evidence(changed, changed_receipt)

    changed_metadata = FakeS1Receipt(envelope, admitted_at=11.0)
    with pytest.raises(HumanMemoryProgramConflict):
        await store.append_evidence(envelope, changed_metadata)
