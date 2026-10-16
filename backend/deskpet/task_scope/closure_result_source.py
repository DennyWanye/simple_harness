"""收口结果来源证据的写入（阶段 6E 从 mutation_disclosure 下沉，语义收口因此不再导入可变字段披露）。"""

from __future__ import annotations

import json

from simple_harness import (
    EvidenceReasonCode,
    EvidenceRef,
    EvidenceSourceKind,
    SanitizedEvidenceEnvelope,
    SanitizedEvidenceReceipt,
)
from simple_harness.execution.provider_invocations import provider_response_json

from deskpet.execution.closure_evidence_ids import _response_hash, input_id
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.primary_visibility import read_evidence_pair
from deskpet.task_scope.protocol import canonical_hash, canonical_json

RESULT_POLICY = "host-closure-result-source/v1"


async def _rows(db, sql, args=()):
    cursor = await db.execute(sql, args)
    try:
        return await cursor.fetchall()
    finally:
        await cursor.close()


async def record_closure_result_tx(db, *, db_path, attempt_id, plan_id, response):
    """New successful mutation only, within its existing commit extension.

    Separate S1 avoids changing the invoker's evidence-set response replay
    selection. Old decisions/replays never call this writer for backfill.
    """

    found = await _rows(
        db,
        "SELECT a.*,r.subject,r.primary_conversation_id FROM post_turn_invocation_attempts a "
        "JOIN foreground_runs r ON r.host_run_id=a.host_run_id WHERE a.attempt_id=?",
        (attempt_id,),
    )
    decisions = await _rows(
        db,
        "SELECT plan_json FROM task_scope_mutation_decisions WHERE plan_id=? AND outcome='mutate'",
        (plan_id,),
    )
    if not decisions:
        return  # no_mutation / rejected response has no mutable field producer
    if len(found) != 1 or len(decisions) != 1:
        raise ValueError("closure_result_source_missing")
    row = found[0]
    if (
        row["purpose"] != "closure"
        or row["status"] != "succeeded"
        or row["plan_id"] != plan_id
        or row["result_hash"] != _response_hash(response)
    ):
        raise ValueError("closure_result_source_attempt_differs")
    source, _ = await read_evidence_pair(
        db=db,
        subject=row["subject"],
        primary_ref=row["primary_conversation_id"],
        evidence_id=input_id(attempt_id),
    )
    body = dict(
        schema_version=1,
        attempt_id=attempt_id,
        plan_id=plan_id,
        input_evidence_id=source.evidence_id,
        input_envelope_hash=source.envelope_hash,
        response=json.loads(canonical_json(provider_response_json(response))),
    )
    eid = "closure-result-source:" + attempt_id
    digest = canonical_hash(body)
    refs = (EvidenceRef(source.evidence_id, source.envelope_hash, 1),)
    envelope = SanitizedEvidenceEnvelope(
        evidence_id=eid,
        run_id=row["sdk_run_id"],
        subject=row["subject"],
        source_kind=EvidenceSourceKind.RUNTIME_EVENT,
        source_ref=eid,
        source_hash=digest,
        sanitized_payload=body,
        sanitized_hash=digest,
        filter_policy_version=RESULT_POLICY,
        removed_spans=(),
        disclosure_context=source.disclosure_context,
        evidence_refs=refs,
    )
    receipt = SanitizedEvidenceReceipt(
        receipt_id="receipt:" + eid,
        run_id=envelope.run_id,
        subject=envelope.subject,
        evidence_id=eid,
        envelope_hash=envelope.envelope_hash,
        source_hash=digest,
        sanitized_hash=digest,
        filter_policy_version=RESULT_POLICY,
        accepted=True,
        reason_codes=(EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
        disclosure_context=envelope.disclosure_context,
        evidence_refs=refs,
        admitted_at=float(row["settled_at"]),
    )
    await HumanMemoryProgramStore(db_path).append_evidence_tx(
        db,
        envelope,
        receipt,
        primary_conversation_id=row["primary_conversation_id"],
        committed_at=float(row["settled_at"]),
    )
