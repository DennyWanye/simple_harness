"""Actual Host admission and public Memory denial; no SDK execution is invented."""

import json
import sqlite3
from dataclasses import replace

import aiosqlite
import pytest
import pytest_asyncio
from deskpet.execution.foreground_queue import ContextLineage, ForegroundQueueError
from deskpet.execution.preparation_rejection import PreparationRejection
from deskpet.execution.primary_dependencies import current_disclosure
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_service import (
    QueueTurnRequest,
    build_foreground_turn_evidence,
)
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
from deskpet.memory.primary_visibility import (
    PrimaryHistoryPolicy,
    PrimaryVisibilityError,
)
from deskpet.task_scope.protocol import canonical_hash, canonical_json
from tests.memory.test_primary_cognitive_evidence import fixture
from tests.memory.test_primary_visibility import materialize


@pytest_asyncio.fixture
async def admitted_denial(tmp_path):
    from simple_harness_memory import SuppressionRequest, SuppressionScopeKind

    path, auth, service, _ = await fixture(tmp_path)
    await service.enqueue_turn(QueueTurnRequest(None, "original", "Source to forget"))
    queue = service._foreground
    candidate = await queue.read_next_preparation_candidate(auth.subject)
    draft = await queue.prepare_candidate(
        subject=auth.subject, expected_candidate_hash=candidate.candidate_hash,
        context=ContextLineage("test-context", 1, candidate.candidate_hash), idempotency_key="draft",
    )
    admission = await queue.claim_next(
        subject=auth.subject, owner_id="worker", claim_idempotency_key="claim",
        preparation_draft_id=draft.draft_id, preparation_draft_hash=draft.draft_hash, lease_seconds=3600,
    )
    runtime = HumanMemoryV7Runtime(tmp_path / "memory.db", evidence_authority=HostEvidenceAuthority(path))
    manager = await runtime.manager()
    envelope, receipt = build_foreground_turn_evidence(
        subject=auth.subject, authority_ref=auth.authority_ref, delivery_key="original", text="Source to forget",
    )
    await manager.ingest_committed_evidence(envelope, receipt)
    memory_id = await materialize(manager, runtime.principal(), envelope, receipt)
    await manager.suppress(principal=runtime.principal(), request=SuppressionRequest(
        "forget-source", auth.subject, SuppressionScopeKind.MEMORY, memory_id, "user_forget", 10.0,
    ))

    async def checker(*, subject, disclosure_context, bindings):
        assert subject == auth.subject
        return await manager.check_history_visibility(
            principal=runtime.principal(), disclosure_context=disclosure_context, bindings=bindings,
        )

    policy = PrimaryHistoryPolicy(path, auth.subject, checker)
    context = current_disclosure(run_id=envelope.run_id, subject=auth.subject, request_id=admission.host_run_id)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        binding, snapshot, reason = await policy.current_user_denial(
            db=db, primary_ref=candidate.primary_conversation_id, evidence_id=candidate.evidence_id,
            evidence_hash=candidate.evidence_hash, disclosure_context=context,
        )
    rejection = PreparationRejection(
        admission.host_run_id, auth.subject, candidate.turn_id, candidate.turn_hash,
        candidate.evidence_id, candidate.evidence_hash, candidate.candidate_hash, binding,
        snapshot.snapshot_hash, canonical_json(snapshot.to_json()), reason,
    )
    try:
        yield path, queue, admission, rejection, policy, candidate, context, service, auth
    finally:
        await runtime.close()


def state(path):
    with sqlite3.connect(path) as db:
        return (
            db.execute("SELECT h.current_state,t.current_state,h.sdk_run_id FROM foreground_run_heads h JOIN foreground_turn_heads t ON t.turn_id=h.turn_id").fetchone(),
            db.execute("SELECT COUNT(*) FROM foreground_run_transitions WHERE idempotency_key='preparation-rejected:v1'").fetchone()[0],
            db.execute("SELECT COUNT(*) FROM foreground_terminal_receipts").fetchone()[0],
            db.execute("SELECT COUNT(*) FROM memory_ingestion_outbox").fetchone()[0],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["before_commit", "after_commit"])
async def test_rejection_commit_fault_and_exact_retry(admitted_denial, point):
    path, queue, admission, rejection, *_ = admitted_denial

    def fault(actual):
        if actual == "preparation_rejection." + point:
            raise RuntimeError("injected-rejection-crash")

    queue._fault_hook = fault
    with pytest.raises(RuntimeError, match="injected-rejection-crash"):
        await queue.settle_preparation_rejection(rejection=rejection, owner_id="worker", generation=admission.generation)
    expected = (("CLAIMED", "CLAIMED", None), 0, 0, 0) if point == "before_commit" else (("FAILED", "SETTLED", None), 1, 0, 0)
    assert state(path) == expected
    queue._fault_hook = None
    first = await queue.settle_preparation_rejection(rejection=rejection, owner_id="worker", generation=admission.generation)
    assert await queue.settle_preparation_rejection(rejection=rejection, owner_id="worker", generation=admission.generation) == first
    assert state(path) == (("FAILED", "SETTLED", None), 1, 0, 0)
    assert first["sdk_event_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["owner", "generation", "candidate", "binding", "start_intent"])
async def test_rejection_cannot_override_wrong_lease_binding_or_possible_start(admitted_denial, bad):
    path, queue, admission, rejection, *_ = admitted_denial
    owner, generation = "worker", admission.generation
    if bad == "owner":
        owner = "other-worker"
    elif bad == "generation":
        generation += 1
    elif bad == "candidate":
        rejection = replace(rejection, candidate_hash="f" * 64)
    elif bad == "binding":
        snapshot = json.loads(rejection.snapshot_json)
        for item in snapshot["items"]:
            if item["binding_hash"] == rejection.binding_hash:
                item["binding_hash"] = "b" * 64
        rejection = replace(
            rejection, binding_hash="b" * 64, snapshot_json=canonical_json(snapshot),
            snapshot_hash=canonical_hash({"domain": "memory.history.visibility.snapshot.v1", "payload": snapshot}),
        )
    else:
        # A real persisted Host start intent is sufficient to make SDK start
        # possible. No SDK observation, binding, Run or terminal is fabricated.
        await queue.record_execution_preparation(
            host_run_id=admission.host_run_id, owner_id=owner, generation=generation,
            context_ref="fixture-context", context_hash="c" * 64, provider_ref="fixture-provider",
            provider_hash="d" * 64, tool_ref="fixture-tools", tool_hash="e" * 64,
            execution_request_hash="f" * 64, idempotency_key="prepared",
        )
        await queue.record_start_intent(
            host_run_id=admission.host_run_id, sdk_run_id="prospective-sdk-run", owner_id=owner,
            generation=generation, start_request_hash="a" * 64, idempotency_key="start-intent",
        )
    before = state(path)
    with pytest.raises((ForegroundQueueError, ValueError), match={
        "owner": "foreground_generation_stale", "generation": "foreground_generation_stale",
        "candidate": "foreground_preparation_rejection_binding_mismatch",
        "binding": "preparation_rejection_source_binding_mismatch",
        "start_intent": "foreground_preparation_rejection_start_possible",
    }[bad]):
        await queue.settle_preparation_rejection(rejection=rejection, owner_id=owner, generation=generation)
    assert state(path) == before


@pytest.mark.asyncio
async def test_policy_infrastructure_failure_never_issues_permanent_rejection(admitted_denial):
    path, _, _, _, policy, candidate, context, *_ = admitted_denial

    async def unavailable(**_):
        raise RuntimeError("temporary-test-unavailability")

    policy.checker = unavailable
    before = state(path)
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        with pytest.raises(PrimaryVisibilityError, match="primary_read_policy_unavailable"):
            await policy.current_user_denial(
                db=db, primary_ref=candidate.primary_conversation_id, evidence_id=candidate.evidence_id,
                evidence_hash=candidate.evidence_hash, disclosure_context=context,
            )
    assert state(path) == before


@pytest.mark.asyncio
async def test_real_old_source_denial_cannot_settle_unrelated_new_source(admitted_denial):
    path, queue, admission, rejection, _, _, _, service, auth = admitted_denial
    original = await queue.settle_preparation_rejection(
        rejection=rejection, owner_id="worker", generation=admission.generation,
    )
    await service.enqueue_turn(QueueTurnRequest(None, "unrelated-new", "Independent new message"))
    candidate = await queue.read_next_preparation_candidate(auth.subject)
    draft = await queue.prepare_candidate(
        subject=auth.subject, expected_candidate_hash=candidate.candidate_hash,
        context=ContextLineage("new-context", 1, candidate.candidate_hash), idempotency_key="new-draft",
    )
    new_admission = await queue.claim_next(
        subject=auth.subject, owner_id="worker", claim_idempotency_key="new-claim",
        preparation_draft_id=draft.draft_id, preparation_draft_hash=draft.draft_hash, lease_seconds=3600,
    )
    mixed = replace(
        rejection, host_run_id=new_admission.host_run_id, turn_id=candidate.turn_id,
        turn_hash=candidate.turn_hash, evidence_id=candidate.evidence_id,
        evidence_hash=candidate.evidence_hash, candidate_hash=candidate.candidate_hash,
    )
    def all_rows():
        with sqlite3.connect(path) as db:
            return tuple(tuple(db.execute("SELECT * FROM " + table)) for table in (
                "foreground_run_heads", "foreground_turn_heads", "foreground_run_transitions",
                "foreground_terminal_receipts", "memory_ingestion_outbox",
            ))
    before = all_rows()
    with pytest.raises(ValueError, match="preparation_rejection_source_binding_mismatch"):
        await queue.settle_preparation_rejection(
            rejection=mixed, owner_id="worker", generation=new_admission.generation,
        )
    assert all_rows() == before
    assert await queue.settle_preparation_rejection(
        rejection=rejection, owner_id="worker", generation=admission.generation,
    ) == original
