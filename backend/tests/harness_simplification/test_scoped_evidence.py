from __future__ import annotations

import json
import hashlib
from dataclasses import replace
from datetime import datetime, timezone

import aiosqlite
import pytest

from agent.agent_loop import AgentLoop, ErrorEvent, FinalEvent
from deskpet.agent.self_check_gate import SelfCheckGate
from deskpet.agent.verify_gate import ClaimPattern, RegexExtractor, VerifyGate
from deskpet.execution.contracts import PersistenceLevel, RunContext, RunCreate, fingerprint_json
from deskpet.execution.evidence import EvidenceContext, EvidenceResolver, UNKNOWN_EVIDENCE
from deskpet.tools.receipt import make_receipt
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


CAPABILITY_HASH = fingerprint_json({"tools": ["ppt_create"]})


def _spec() -> RunCreate:
    return RunCreate(
        run_id="run-new",
        idempotency_key="root:session-new:request-new:turn-2",
        context=RunContext(
            session_id="session-new",
            root_run_id="run-new",
            parent_run_id=None,
            request_id="request-new",
            turn_id="turn-2",
            venue="text",
            workspace={},
            capability_hash=CAPABILITY_HASH,
            provider_plan={"model": "fixture"},
            trace_id="trace-new",
            principal_id="principal",
            auth_epoch=1,
        ),
        payload_fingerprint=fingerprint_json({"text": "create deck"}),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind="react",
        profile_key="react_short",
        persistence_level=PersistenceLevel.DURABLE,
    )


def _context() -> EvidenceContext:
    return EvidenceContext(
        run_id="run-new",
        turn_id="turn-2",
        call_id="call-7",
        effect_id="effect-9",
        artifact_ref="artifact://deck/1",
    )


def _delegated_context() -> EvidenceContext:
    call_id = "workflow_spawn_1"
    return EvidenceContext(
        run_id="run-new",
        turn_id="turn-2",
        call_id=call_id,
        effect_id=hashlib.sha256(
            f"effect|run-new|{call_id}".encode("utf-8")
        ).hexdigest(),
    )


async def _ledger(tmp_path) -> SqliteExecutionUnitOfWork:
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.initialize()
    await uow.create(_spec())
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT INTO execution_effects(
            effect_id,schema_version,run_id,effect_fingerprint,call_id,tool_name,
            args_hash,capability_hash,scope_hash,effect_type,status,
            handoff_state,completion_disposition,policy_json,
            prepared_json,outcome_json,receipt_ref,artifact_refs_json,
            effect_version,created_at,updated_at,ended_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "effect-9", 1, "run-new", "effect-fingerprint", "call-7",
                "ppt_create", fingerprint_json({}), CAPABILITY_HASH, None,
                "write", "succeeded", "reconciled", "normal",
                "{}", "{}", "{}", "receipt-1",
                json.dumps(["artifact://deck/1"]), 1, 1.0, 1.0, 1.0,
            ),
        )
        await db.commit()
    return uow


async def _delegated_ledger(tmp_path) -> SqliteExecutionUnitOfWork:
    uow = await _ledger(tmp_path)
    child = RunCreate(
        run_id="child-1",
        idempotency_key="delegate:run-new:workflow_spawn_1",
        context=RunContext(
            session_id="session-new",
            root_run_id="run-new",
            parent_run_id="run-new",
            request_id="request-child",
            turn_id="turn-child",
            venue="text",
            workspace={},
            capability_hash=CAPABILITY_HASH,
            provider_plan={"model": "fixture"},
            trace_id="trace-child",
            principal_id="principal",
            auth_epoch=1,
        ),
        payload_fingerprint=fingerprint_json({"text": "verify files"}),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind="workflow",
        profile_key="workflow.durable_task",
        persistence_level=PersistenceLevel.DURABLE,
    )
    await uow.create(child)
    terminal_payload = {
        "status": "completed",
        "value": {
            "run_id": "child-1",
            "status": "completed",
            "terminal_event_id": "child-terminal",
            "workflow_report": {"audit": {"passed": True}},
        },
    }
    async with aiosqlite.connect(tmp_path / "workflow.db") as db:
        await db.execute(
            """UPDATE execution_runs SET status='completed',
            terminal_event_id='child-terminal',ended_at=2.0,updated_at=2.0
            WHERE run_id='child-1'"""
        )
        await db.execute(
            """INSERT INTO execution_profile_launch_tickets(
            ticket_ref,schema_version,parent_run_id,root_run_id,task_scope_id,
            attempt_id,provider_turn_id,profile_key,driver_kind,
            profile_catalog_generation,capability_snapshot_ref,task_grant_ref,
            spawn_call_id,request_fingerprint,state,child_command_id,
            child_run_id,ticket_version,created_at,updated_at,consumed_at
            ) VALUES(?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "ticket-1", "run-new", "run-new", "scope-1", "attempt-1",
                "provider-turn-1", "workflow.durable_task", "workflow", 1,
                "snapshot-1", "grant-1", "workflow_spawn_1", "a" * 64,
                "consumed", "delegate-1", "child-1", 1, 1.0, 1.0, 1.0,
            ),
        )
        await db.execute(
            """INSERT INTO execution_child_commands(
            operation_id,schema_version,parent_run_id,command_id,child_run_id,
            profile_key,join_policy,capability_snapshot_ref,
            capability_subset_json,child_request_json,child_spec_json,
            intent_fingerprint,status,attempts,created_at,updated_at,ack_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "operation-1", 1, "run-new", "delegate-1", "child-1",
                "workflow.durable_task", "attached", "snapshot-1", "[]",
                "{}", "{}", "b" * 64, "acked", 1, 1.0, 1.0, 1.0,
            ),
        )
        await db.execute(
            """INSERT INTO execution_child_signal_inbox(
            signal_id,schema_version,operation_id,parent_run_id,command_id,
            child_run_id,kind,payload_json,attempts,created_at,updated_at,
            delivered_at) VALUES(?,1,?,?,?,?, 'terminal',?,1,?,?,?)""",
            (
                "signal-1", "operation-1", "run-new", "delegate-1",
                "child-1", json.dumps(terminal_payload), 1.0, 1.0, 1.0,
            ),
        )
        for index, tool_name in enumerate(("write_file", "run_shell", "register_artifacts")):
            await db.execute(
                """INSERT INTO workflow_effects(
                effect_id,run_id,node_execution_id,effect_fingerprint,
                effect_type,policy_json,args_hash,status,prepared_json,
                outcome_json,receipt_ref,artifact_refs_json,lease_epoch,
                started_at,updated_at,ended_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"child-effect-{index}", "child-1", f"node-{index}",
                    f"fingerprint-{index}", "idempotent_read", "{}",
                    f"args-{index}", "committed",
                    json.dumps({"tool_name": tool_name}), "{}",
                    f"receipt-{index}",
                    json.dumps(["artifact://result/1"] if tool_name == "register_artifacts" else []),
                    1, float(index + 1), float(index + 1), float(index + 1),
                ),
            )
        await db.commit()
    return uow


def _receipt():
    now = datetime(2026, 7, 21, tzinfo=timezone.utc)
    return make_receipt(
        tool_name="ppt_create",
        args={},
        started_at=now,
        ended_at=now,
        ok=True,
        session_id="old-session",
    )


def _gate() -> VerifyGate:
    pattern = ClaimPattern(
        id="created",
        regex=r"created (?P<title>\S+)",
        artifact_kind="file",
        tool_hint=["ppt_create"],
    )
    return VerifyGate(extractor=RegexExtractor([pattern]), mode="strict")


@pytest.mark.asyncio
async def test_uow_is_the_read_only_evidence_resolver_and_exact_context_passes(tmp_path):
    resolver = await _ledger(tmp_path)
    assert isinstance(resolver, EvidenceResolver)
    context = _context()
    selection = await resolver.lookup_completion_evidence(context)

    outcome = _gate().check(
        assistant_text="created deck.pptx",
        ledger=[_receipt()],
        evidence_context=context,
        scoped_evidence=selection,
    )
    assert selection.status == "matched"
    assert outcome.passed is True
    assert outcome.evidence_status == "matched"


@pytest.mark.asyncio
async def test_attached_completed_child_projects_its_committed_effect_receipts(tmp_path):
    resolver = await _delegated_ledger(tmp_path)
    context = _delegated_context()

    selection = await resolver.lookup_completion_evidence(context)

    assert selection.status == "matched"
    assert [record.tool_name for record in selection.records] == [
        "write_file",
        "run_shell",
        "register_artifacts",
    ]
    assert all(record.context == context for record in selection.records)
    gate = VerifyGate(
        extractor=RegexExtractor([
            ClaimPattern(
                id="tests-passed",
                regex=r"tests passed",
                artifact_kind="test",
                tool_hint=["run_shell"],
            )
        ]),
        mode="strict",
    )
    assert gate.check(
        assistant_text="tests passed",
        ledger=[],
        scoped_evidence=selection,
    ).passed is True


@pytest.mark.asyncio
async def test_attached_child_evidence_requires_explicit_passed_audit(tmp_path):
    resolver = await _delegated_ledger(tmp_path)
    async with aiosqlite.connect(tmp_path / "workflow.db") as db:
        row = await (
            await db.execute(
                "SELECT payload_json FROM execution_child_signal_inbox "
                "WHERE signal_id='signal-1'"
            )
        ).fetchone()
        payload = json.loads(str(row[0]))
        payload["value"]["workflow_report"]["audit"]["passed"] = False
        await db.execute(
            "UPDATE execution_child_signal_inbox SET payload_json=? "
            "WHERE signal_id='signal-1'",
            (json.dumps(payload),),
        )
        await db.commit()

    assert (
        await resolver.lookup_completion_evidence(_delegated_context())
    ).status == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "other"),
    [
        ("run_id", "run-old"),
        ("turn_id", "turn-old"),
        ("call_id", "call-old"),
        ("effect_id", "effect-old"),
        ("artifact_ref", "artifact://deck/old"),
    ],
)
async def test_cross_run_turn_call_effect_or_artifact_is_unknown(
    tmp_path, field: str, other: str
):
    resolver = await _ledger(tmp_path)
    selection = await resolver.lookup_completion_evidence(replace(_context(), **{field: other}))
    assert selection.status == "unknown"
    assert selection.records == ()


@pytest.mark.asyncio
async def test_unstored_target_digest_is_unknown_not_effect_fingerprint(tmp_path):
    resolver = await _ledger(tmp_path)
    context = replace(_context(), target_digest="effect-fingerprint")
    assert (await resolver.lookup_completion_evidence(context)).status == "unknown"


def test_raw_legacy_session_receipt_cannot_satisfy_scoped_query():
    outcome = _gate().check(
        assistant_text="created deck.pptx",
        ledger=[_receipt()],
        evidence_context=_context(),
    )
    assert outcome.passed is False
    assert outcome.evidence_status == "unknown"


def test_unknown_scope_without_context_clears_legacy_session_receipts():
    outcome = _gate().check(
        assistant_text="created deck.pptx",
        ledger=[_receipt()],
        scoped_evidence=UNKNOWN_EVIDENCE,
    )
    assert outcome.passed is False
    assert outcome.evidence_status == "unknown"


def test_missing_required_identity_is_rejected_before_lookup():
    with pytest.raises(ValueError, match="call_id is required"):
        replace(_context(), call_id="")


class _FinalLLM:
    async def chat_with_fallback(self, messages, tools=None, **kwargs):
        from llm.types import ChatResponse, ChatUsage
        return ChatResponse(
            content="created deck.pptx", tool_calls=[], stop_reason="end_turn",
            usage=ChatUsage(input_tokens=1, output_tokens=1), model="fixture",
        )


class _NoTools:
    def schemas(self, enabled_toolsets=None):
        return []


class _OldReceiptStore:
    def load_session(self, session_id):
        return [_receipt()]


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", [False, True])
async def test_new_react_unknown_scope_blocks_old_session_receipt_in_both_gates(pipeline):
    gate = _gate()
    loop = AgentLoop(
        _FinalLLM(), _NoTools(), verify_gate=gate,
        self_check_gate=SelfCheckGate(verify_gate=gate) if pipeline else None,
        pipeline_problem_type="creation" if pipeline else None,
        receipt_store=_OldReceiptStore(), max_verify_nudges=1,
        force_finish_via_tool_choice=False,
    )
    events = [event async for event in loop.run(
        [{"role": "user", "content": "make it"}], session_id="old-session",
        _scoped_evidence=UNKNOWN_EVIDENCE,
    )]
    assert any(isinstance(event, ErrorEvent) and event.reason == "verify_exhausted" for event in events)
    assert not any(isinstance(event, FinalEvent) for event in events)


@pytest.mark.asyncio
async def test_agent_loop_without_scoped_argument_keeps_legacy_receipt_compatibility():
    loop = AgentLoop(
        _FinalLLM(), _NoTools(), verify_gate=_gate(),
        receipt_store=_OldReceiptStore(), max_verify_nudges=1,
        force_finish_via_tool_choice=False,
    )
    events = [event async for event in loop.run(
        [{"role": "user", "content": "make it"}], session_id="old-session",
    )]
    assert any(isinstance(event, FinalEvent) for event in events)
