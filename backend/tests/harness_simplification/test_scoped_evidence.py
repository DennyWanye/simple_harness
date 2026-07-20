from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

import aiosqlite
import pytest

from agent.agent_loop import AgentLoop, ErrorEvent, FinalEvent
from deskpet.agent.self_check_gate import SelfCheckGate
from deskpet.agent.verify_gate import ClaimPattern, RegexExtractor, VerifyGate
from deskpet.execution import PersistenceLevel, RunContext, RunCreate, fingerprint_json
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


async def _ledger(tmp_path) -> SqliteExecutionUnitOfWork:
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path)
    await uow.initialize()
    await uow.create(_spec())
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """INSERT INTO execution_effects(
            effect_id,schema_version,run_id,effect_fingerprint,call_id,tool_name,
            args_hash,capability_hash,scope_hash,effect_type,status,policy_json,
            prepared_json,outcome_json,receipt_ref,artifact_refs_json,
            effect_version,created_at,updated_at,ended_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "effect-9", 1, "run-new", "effect-fingerprint", "call-7",
                "ppt_create", fingerprint_json({}), CAPABILITY_HASH, None,
                "write", "succeeded", "{}", "{}", "{}", "receipt-1",
                json.dumps(["artifact://deck/1"]), 1, 1.0, 1.0, 1.0,
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
