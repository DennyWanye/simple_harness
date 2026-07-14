from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio

from deskpet.agent.verify_gate import ClaimPattern, RegexExtractor, VerifyGate
from deskpet.memory.session_db import SessionDB
from deskpet.tools.receipt_store import ReceiptStore
from deskpet.workflows.adapters.product_delivery import (
    ProductDeliveryAdapter,
    ProductDeliveryError,
    build_product_delivery_handlers,
    stable_workflow_receipt_id,
)
from deskpet.workflows.outbox import WorkflowOutbox
from deskpet.workflows.service import WorkflowService
from deskpet.workflows.store import WorkflowRunStore


KEY = b"p" * 32


def _event(event_id: str, *, run_id: str, kind: str, payload: dict) -> dict:
    return {
        "event_id": event_id,
        "event_type": f"workflow.{kind}",
        "run_id": run_id,
        "created_at": 1_788_883_200.0,
        "payload": {
            "intent_id": f"{run_id}:{kind}",
            "kind": kind,
            "channel": "artifact" if kind == "artifact_card" else "receipt",
            "payload": payload,
        },
    }


def _delivery(event_id: str, channel: str, target_id: str = "session-1") -> dict:
    return {"event_id": event_id, "channel": channel, "target_id": target_id}


def _completion_gate(tool_name: str) -> VerifyGate:
    pattern = ClaimPattern(
        id="graph_completed",
        regex=r"已生成 (?P<title>[^\s，。]+\.pptx)",
        artifact_kind="file",
        tool_hint=[tool_name],
    )
    return VerifyGate(extractor=RegexExtractor([pattern]), mode="strict")


@pytest_asyncio.fixture
async def stores(tmp_path: Path):
    session_db = SessionDB(tmp_path / "state.db")
    await session_db.initialize()
    receipt_store = ReceiptStore(tmp_path / "product", key=KEY)
    return session_db, receipt_store


@pytest.mark.asyncio
async def test_artifact_handler_persists_toolartifact_once_by_event_id(
    stores, tmp_path: Path
) -> None:
    session_db, receipt_store = stores
    adapter = ProductDeliveryAdapter(session_db=session_db, receipt_store=receipt_store)
    deck = tmp_path / "deck.pptx"
    deck.write_bytes(b"real ppt bytes")
    deck_sha = hashlib.sha256(deck.read_bytes()).hexdigest()
    event = _event(
        "artifact-event-1",
        run_id="run-1",
        kind="artifact_card",
        payload={
            "tool": "ppt_pro",
            "path": str(deck),
            "title": "Durable Graph deck",
        },
    )
    delivery = _delivery(event["event_id"], "artifact")

    first = await adapter.deliver_artifact(event, delivery)
    second = await adapter.deliver_artifact(event, delivery)

    assert second["message_id"] == first["message_id"]
    messages = await session_db.get_messages("session-1")
    assert len(messages) == 1
    envelope = json.loads(messages[0]["content"])
    assert envelope["tool"] == "ppt_pro"
    assert envelope["workflow"] == {"event_id": "artifact-event-1", "run_id": "run-1"}
    assert envelope["artifacts"][0]["path"] == str(deck)
    assert envelope["artifacts"][0]["sha256"] == deck_sha
    assert envelope["artifacts"][0]["size_bytes"] == len(b"real ppt bytes")
    assert envelope["artifacts"][0]["mime"].endswith("presentationml.presentation")

    with sqlite3.connect(tmp_path / "state.db") as db:
        rows = db.execute(
            "SELECT workflow_event_id, role FROM messages WHERE workflow_event_id IS NOT NULL"
        ).fetchall()
    assert rows == [("artifact-event-1", "tool")]


@pytest.mark.asyncio
async def test_artifact_handler_publishes_persisted_tool_result_live(
    stores, tmp_path: Path
) -> None:
    session_db, receipt_store = stores
    published: list[dict] = []

    async def publish(payload: dict) -> None:
        published.append(payload)

    adapter = ProductDeliveryAdapter(
        session_db=session_db,
        receipt_store=receipt_store,
        artifact_publisher=publish,
    )
    report = tmp_path / "report.md"
    report.write_text("# Report\n", encoding="utf-8")

    result = await adapter.deliver_artifact(
        _event(
            "artifact-live-1",
            run_id="research-live",
            kind="artifact_card",
            payload={"tool": "artifact_create", "path": str(report)},
        ),
        _delivery("artifact-live-1", "artifact"),
    )

    assert len(published) == 1
    assert published[0]["message_id"] == result["message_id"]
    assert published[0]["workflow_event_id"] == "artifact-live-1"
    assert published[0]["session_id"] == "session-1"
    assert published[0]["artifacts"][0]["path"] == str(report)


@pytest.mark.asyncio
async def test_artifact_handler_discards_after_delivery_session_tombstone(stores, tmp_path: Path) -> None:
    session_db, receipt_store = stores
    workflow_store = WorkflowRunStore(tmp_path / "workflow.db")
    run_id, _ = await workflow_store.create_run(
        request_key="request-key",
        session_id="session-1",
        request_id="request",
        turn_id="turn",
        workflow_name="ppt_pro",
        workflow_version="v1",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="capability",
        capability_snapshot={},
        state_schema_version=1,
        run_id="run-tombstone",
    )
    await workflow_store.bind_session_refs(run_id, (("base", "session-1", 0), ("delivery", "session-1", 0)))
    adapter = ProductDeliveryAdapter(
        session_db=session_db,
        receipt_store=receipt_store,
        workflow_store=workflow_store,
    )
    deck = tmp_path / "late.pptx"
    deck.write_bytes(b"late")
    await session_db.tombstone_session("session-1")

    result = await adapter.deliver_artifact(
        _event("late-artifact", run_id=run_id, kind="artifact_card", payload={"path": str(deck)}),
        _delivery("late-artifact", "artifact"),
    )

    assert result["discarded"] is True
    assert await session_db.get_messages("session-1") == []


@pytest.mark.asyncio
async def test_research_report_intent_persists_a_text_artifact(stores) -> None:
    session_db, receipt_store = stores
    adapter = ProductDeliveryAdapter(session_db=session_db, receipt_store=receipt_store)
    event = _event(
        "artifact-event-report",
        run_id="research-run",
        kind="artifact_card",
        payload={
            "artifact_type": "research_report",
            "report": {
                "schema_version": 1,
                "status": "completed",
                "report": {"summary": "Evidence-backed summary", "report_md": "# Report\nBody"},
            },
        },
    )

    await adapter.dispatch(event, _delivery(event["event_id"], "artifact_message"))

    envelope = json.loads((await session_db.get_messages("session-1"))[0]["content"])
    artifact = envelope["artifacts"][0]
    assert artifact["kind"] == "text"
    assert artifact["title"] == "research_report"
    assert artifact["preview"] == "# Report\nBody"
    assert len(artifact["sha256"]) == 64


@pytest.mark.asyncio
async def test_research_report_explicit_preview_hides_blob_reference(stores) -> None:
    session_db, receipt_store = stores
    adapter = ProductDeliveryAdapter(session_db=session_db, receipt_store=receipt_store)
    event = _event(
        "artifact-event-blob-preview",
        run_id="research-run",
        kind="artifact_card",
        payload={
            "artifact_type": "research_report",
            "report": {
                "schema_version": 1,
                "status": "completed",
                "report": {
                    "$blob_ref": {
                        "sha256": "a" * 64,
                        "size": 20000,
                        "media_type": "application/json",
                    }
                },
            },
            "preview": "Readable research summary",
        },
    )

    await adapter.dispatch(event, _delivery(event["event_id"], "artifact_message"))

    envelope = json.loads((await session_db.get_messages("session-1"))[0]["content"])
    preview = envelope["artifacts"][0]["preview"]
    assert preview == "Readable research summary"
    assert "$blob_ref" not in preview


@pytest.mark.asyncio
async def test_artifact_handler_rejects_a_stale_declared_hash(stores, tmp_path: Path) -> None:
    session_db, receipt_store = stores
    adapter = ProductDeliveryAdapter(session_db=session_db, receipt_store=receipt_store)
    deck = tmp_path / "changed.pptx"
    deck.write_bytes(b"new bytes")
    event = _event(
        "artifact-event-stale",
        run_id="run-stale",
        kind="artifact_card",
        payload={"path": str(deck), "sha256": "0" * 64},
    )

    with pytest.raises(ProductDeliveryError, match="sha256 mismatch"):
        await adapter.deliver_artifact(
            event, _delivery(event["event_id"], "artifact")
        )

    assert await session_db.get_messages("session-1") == []


@pytest.mark.asyncio
async def test_handlers_wire_through_real_workflow_service_and_outbox(
    stores, tmp_path: Path
) -> None:
    session_db, receipt_store = stores
    adapter = ProductDeliveryAdapter(session_db=session_db, receipt_store=receipt_store)
    run_store = WorkflowRunStore(tmp_path / "workflow.db")
    run_id, created = await run_store.create_run(
        request_key="request:product-delivery",
        session_id="session-1",
        request_id="request-1",
        turn_id="turn-1",
        workflow_name="ppt_pro",
        workflow_version="1",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="capability",
        capability_snapshot={},
        state_schema_version=1,
        run_id="service-run",
    )
    assert created is True
    deck = tmp_path / "service-deck.pptx"
    deck.write_bytes(b"service artifact")
    outbox = WorkflowOutbox(run_store)
    event = await outbox.ensure_event(
        run_id=run_id,
        event_key="terminal:artifact",
        event_type="workflow.artifact",
        payload={
            "kind": "artifact_card",
            "payload": {"tool": "ppt_pro", "path": str(deck)},
        },
        deliveries=(("artifact", "session-1"),),
    )
    service = WorkflowService(
        run_store,
        runner=object(),
        delivery_handlers=adapter.handlers(),
    )

    first = await service.deliver_event_once(event["event_id"])
    second = await service.deliver_event_once(event["event_id"])

    assert first["deliveries"][0]["status"] == "delivered"
    assert second["deliveries"][0]["status"] == "delivered"
    assert first["deliveries"][0]["attempts"] == 1
    messages = await session_db.get_messages("session-1")
    assert len(messages) == 1
    envelope = json.loads(messages[0]["content"])
    assert envelope["workflow"]["event_id"] == event["event_id"]


@pytest.mark.asyncio
async def test_success_receipt_is_append_once_and_is_real_verifygate_evidence(stores) -> None:
    session_db, receipt_store = stores
    adapter = ProductDeliveryAdapter(session_db=session_db, receipt_store=receipt_store)
    event = _event(
        "receipt-event-success",
        run_id="ppt-run",
        kind="receipt",
        payload={
            "tool": "ppt_pro",
            "outcome": "success",
            "run_id": "ppt-run",
            "sha256": "b" * 64,
        },
    )
    delivery = _delivery(event["event_id"], "receipt_jsonl")

    first = await adapter.deliver_receipt(event, delivery)
    second = await adapter.deliver_receipt(event, delivery)

    assert second == first
    receipts = receipt_store.load_session("session-1")
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.receipt_id == stable_workflow_receipt_id(event["event_id"], "delivered")
    assert (receipt.sig_version, receipt.phase, receipt.outcome, receipt.ok) == (
        2,
        "delivered",
        "success",
        True,
    )
    assert receipt.effect_id == event["event_id"]
    assert receipt.artifacts == ["b" * 64]
    assert adapter.load_verify_evidence("session-1") == [receipt]

    outcome = _completion_gate("ppt_pro").check(
        assistant_text="已生成 deck.pptx",
        ledger=adapter.load_verify_evidence("session-1"),
    )
    assert outcome.passed is True
    assert outcome.claims_extracted == 1

    jsonl = receipt_store.receipts_dir / "session-1.jsonl"
    assert len(jsonl.read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_outcome", "error_class"),
    [("failed", "workflow_failed"), ("cancelled", "workflow_cancelled")],
)
async def test_failure_and_cancel_close_accepted_ledger_without_completion_evidence(
    stores, terminal_outcome: str, error_class: str
) -> None:
    session_db, receipt_store = stores
    adapter = ProductDeliveryAdapter(session_db=session_db, receipt_store=receipt_store)
    accepted = _event(
        f"accepted-{terminal_outcome}",
        run_id=f"run-{terminal_outcome}",
        kind="accepted",
        payload={"tool": "ppt_pro", "outcome": "pending", "phase": "accepted"},
    )
    terminal = _event(
        f"terminal-{terminal_outcome}",
        run_id=f"run-{terminal_outcome}",
        kind="receipt",
        payload={
            "tool": "ppt_pro",
            "outcome": terminal_outcome,
            "error": f"workflow {terminal_outcome}",
        },
    )

    accepted_result = await adapter.deliver_receipt(
        accepted, _delivery(accepted["event_id"], "receipt")
    )
    terminal_result = await adapter.deliver_receipt(
        terminal, _delivery(terminal["event_id"], "receipt")
    )

    assert accepted_result["completion_evidence"] is False
    assert terminal_result["completion_evidence"] is False
    receipts = receipt_store.load_session("session-1")
    assert [(item.phase, item.outcome, item.ok) for item in receipts] == [
        ("accepted", "pending", False),
        ("delivered", "failed", False),
    ]
    assert receipts[-1].error_class == error_class
    assert adapter.load_verify_evidence("session-1") == []

    outcome = _completion_gate("ppt_pro").check(
        assistant_text="已生成 deck.pptx",
        ledger=receipt_store.load_session("session-1"),
    )
    assert outcome.passed is False
    assert outcome.unmatched_claims[0].reason == "no_receipt"


@pytest.mark.asyncio
async def test_nonterminal_receipt_is_rejected_without_writing_jsonl(stores) -> None:
    session_db, receipt_store = stores
    adapter = ProductDeliveryAdapter(session_db=session_db, receipt_store=receipt_store)
    event = _event(
        "receipt-running",
        run_id="run-running",
        kind="receipt",
        payload={"tool": "ppt_pro", "outcome": "running"},
    )

    with pytest.raises(ProductDeliveryError, match="must be terminal"):
        await adapter.deliver_receipt(event, _delivery(event["event_id"], "receipt"))

    assert receipt_store.load_session("session-1") == []
    assert not (receipt_store.receipts_dir / "session-1.jsonl").exists()


def test_main_wiring_factory_returns_current_and_normative_channel_aliases(tmp_path: Path) -> None:
    handlers = build_product_delivery_handlers(
        session_db=SessionDB(tmp_path / "state.db"),
        receipt_store=ReceiptStore(tmp_path / "product", key=KEY),
    )

    assert set(handlers) == {"artifact", "artifact_message", "receipt", "receipt_jsonl"}
    assert all(callable(handler) for handler in handlers.values())
