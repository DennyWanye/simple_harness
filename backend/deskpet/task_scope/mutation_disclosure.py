"""Current disclosure of exact, durably applied model-produced scope fields.

Host indexes locate facts; SDK public effects or a new Host closure response S1
prove the actual producer. Neither a matching value nor a scope link suffices.
"""
from __future__ import annotations

import hashlib
import json

from simple_harness import (EvidenceRef, EvidenceSourceKind, EvidenceReasonCode,
    SanitizedEvidenceEnvelope, SanitizedEvidenceReceipt, thaw_json)
from simple_harness.execution.provider_invocations import provider_response_json, provider_response_from_json

from deskpet.execution.primary_dependencies import dependencies, read_run_dependencies, parse_dependencies
from deskpet.memory.primary_visibility import read_evidence_pair
from deskpet.task_scope.protocol import canonical_hash, canonical_json

RESULT_POLICY = "host-closure-result-source/v1"
FIELD_KINDS = {"resume": {"resume.update"}, "goal": {"goal.set", "goal.revise"}}


async def _rows(db, sql, args=()):
    cursor = await db.execute(sql, args)
    try:
        return await cursor.fetchall()
    finally:
        await cursor.close()


def _arguments(plan):
    return dict(outcome=plan["outcome"], base_revision=plan["base_revision"],
        closure_reason=plan.get("closure_reason"), idempotency_key=plan["idempotency_key"],
        evidence_refs=[r["evidence_id"] for r in plan["evidence_refs"]],
        operations=[dict(operation_id=o["operation_id"], kind=o["kind"], value=o["value"],
            reason_code=o["reason_code"], evidence_refs=[r["evidence_id"] for r in o["evidence_refs"]])
            for o in plan["operations"]])


def _matches(arguments, plan):
    from deskpet.sdk_adapters.task_scope_mutation import _validate_shape
    # The production handler strips only this declared presentation sidecar;
    # all actual mutation fields and every other unexpected key stay strict.
    value = _validate_shape({k: v for k, v in arguments.items() if k != "deskpet_public_progress"})
    value["closure_reason"] = value.get("closure_reason")
    value["evidence_refs"] = list(dict.fromkeys(value["evidence_refs"]))
    for op in value["operations"]:
        op["evidence_refs"] = list(dict.fromkeys(op["evidence_refs"]))
    return value == _arguments(plan)


async def record_closure_result_tx(db, *, db_path, attempt_id, plan_id, response):
    """New successful mutation only, within its existing commit extension.

    Separate S1 avoids changing the invoker's evidence-set response replay
    selection. Old decisions/replays never call this writer for backfill.
    """
    from deskpet.execution.closure_request_guard import input_id
    from deskpet.memory.human_memory_program import HumanMemoryProgramStore
    from deskpet.sdk_adapters.post_turn_invoker import _response_hash
    found = await _rows(db, "SELECT a.*,r.subject,r.primary_conversation_id FROM post_turn_invocation_attempts a "
        "JOIN foreground_runs r ON r.host_run_id=a.host_run_id WHERE a.attempt_id=?", (attempt_id,))
    decisions = await _rows(db, "SELECT plan_json FROM task_scope_mutation_decisions WHERE plan_id=? AND outcome='mutate'", (plan_id,))
    if not decisions:
        return  # no_mutation / rejected response has no mutable field producer
    if len(found) != 1 or len(decisions) != 1:
        raise ValueError("closure_result_source_missing")
    row = found[0]
    if row["purpose"] != "closure" or row["status"] != "succeeded" or row["plan_id"] != plan_id or row["result_hash"] != _response_hash(response):
        raise ValueError("closure_result_source_attempt_differs")
    source, _ = await read_evidence_pair(db=db, subject=row["subject"],
        primary_ref=row["primary_conversation_id"], evidence_id=input_id(attempt_id))
    body = dict(schema_version=1, attempt_id=attempt_id, plan_id=plan_id,
        input_evidence_id=source.evidence_id, input_envelope_hash=source.envelope_hash,
        response=json.loads(canonical_json(provider_response_json(response))))
    eid = "closure-result-source:" + attempt_id
    digest = canonical_hash(body)
    refs = (EvidenceRef(source.evidence_id, source.envelope_hash, 1),)
    envelope = SanitizedEvidenceEnvelope(evidence_id=eid, run_id=row["sdk_run_id"], subject=row["subject"],
        source_kind=EvidenceSourceKind.RUNTIME_EVENT, source_ref=eid, source_hash=digest,
        sanitized_payload=body, sanitized_hash=digest, filter_policy_version=RESULT_POLICY,
        removed_spans=(), disclosure_context=source.disclosure_context, evidence_refs=refs)
    receipt = SanitizedEvidenceReceipt(receipt_id="receipt:"+eid, run_id=envelope.run_id,
        subject=envelope.subject, evidence_id=eid, envelope_hash=envelope.envelope_hash,
        source_hash=digest, sanitized_hash=digest, filter_policy_version=RESULT_POLICY, accepted=True,
        reason_codes=(EvidenceReasonCode.SANITIZED_AND_ACCEPTED,), disclosure_context=envelope.disclosure_context,
        evidence_refs=refs, admitted_at=float(row["settled_at"]))
    await HumanMemoryProgramStore(db_path).append_evidence_tx(db, envelope, receipt,
        primary_conversation_id=row["primary_conversation_id"], committed_at=float(row["settled_at"]))


async def _producer(db, *, stack, decision, plan, receipt, subject, primary_ref):
    from deskpet.sdk_adapters.task_scope_mutation import derive_plan_id
    from deskpet.sdk_adapters.post_turn_invoker import _response_hash
    from deskpet.execution.semantic_closure import _closure_arguments
    if receipt.attempt_id is not None:
        rows = await _rows(db, "SELECT * FROM post_turn_invocation_attempts WHERE attempt_id=?", (receipt.attempt_id,))
        if len(rows) != 1:
            return None
        row = rows[0]
        present = await _rows(db, "SELECT 1 FROM human_memory_evidence WHERE evidence_id=?", ("closure-result-source:"+receipt.attempt_id,))
        if not present:
            return None  # preserved legacy output hash is not a full response
        result, _ = await read_evidence_pair(db=db, subject=subject, primary_ref=primary_ref,
            evidence_id="closure-result-source:"+receipt.attempt_id)
        value = thaw_json(result.sanitized_payload)
        if (result.filter_policy_version != RESULT_POLICY or result.run_id != plan["run_id"]
                or value["attempt_id"] != row["attempt_id"] or value["plan_id"] != decision["plan_id"]
                or row["sdk_run_id"] != plan["run_id"] or row["plan_id"] != decision["plan_id"]
                or row["purpose"] != "closure" or row["status"] != "succeeded"):
            raise ValueError("scope_field_response_binding_differs")
        source, _ = await read_evidence_pair(db=db, subject=subject, primary_ref=primary_ref,
            evidence_id=value["input_evidence_id"])
        expected_input_id = "closure-attempt-input:"+row["attempt_id"]
        if (source.evidence_id != expected_input_id or source.envelope_hash != value["input_envelope_hash"]
                or result.evidence_refs != (EvidenceRef(source.evidence_id, source.envelope_hash, 1),)):
            raise ValueError("scope_field_input_binding_differs")
        body = thaw_json(source.sanitized_payload)
        response = provider_response_from_json(value["response"])
        if (_response_hash(response) != row["result_hash"] or response.request_id.value !=
                f"post-turn-closure-{row['request_hash'][:24]}-{row['attempt_ordinal']}"
                or body["attempt_id"] != row["attempt_id"]
                or body["prepared"]["identity"]["sdk_run_id"] != plan["run_id"]
                or body["prepared"]["identity"]["subject"] != subject
                or body["prepared"]["identity"]["scope"] != plan["task_scope_id"]
                or source.run_id != plan["run_id"]
                or not _matches(_closure_arguments(response), plan)):
            raise ValueError("scope_field_response_differs")
        proof = parse_dependencies(body["prepared"]["dependencies"])
        proof = dependencies([*proof["evidence"],
            dict(evidence_id=result.evidence_id, envelope_hash=result.envelope_hash),
            dict(evidence_id=source.evidence_id, envelope_hash=source.envelope_hash)],
            proof["recall"], proof.get("short_horizon", ()),
            procedure_drafts=proof.get("procedure_drafts", ()))
        return dict(producer_attempt_id=row["attempt_id"], producer_receipt_hash=result.envelope_hash), proof
    if derive_plan_id(plan["idempotency_key"], plan["task_scope_id"]) != plan["plan_id"]:
        return None
    indexed = await _rows(db, "SELECT * FROM primary_effect_identities WHERE sdk_run_id=? "
        "AND tool_name='task_scope_update' ORDER BY sequence LIMIT 257", (plan["run_id"],))
    if len(indexed) > 256:
        raise ValueError("scope_field_producer_limit")
    candidates = []
    for row in indexed:
        identity = dict(host_run_id=row["host_run_id"], sdk_run_id=row["sdk_run_id"],
            effect_id=row["effect_id"], tool_name=row["tool_name"])
        if row["identity_hash"] != canonical_hash(identity) or row["identity_json"] != canonical_json(identity):
            raise ValueError("scope_field_effect_index_differs")
        _, (effect,) = stack.read_primary_dependency_facts(plan["run_id"], (row["effect_id"],))
        if effect is None or not effect.terminal or effect.result is None:
            continue
        value = thaw_json(effect.result.value)
        if not isinstance(value, dict) or value.get("closure_receipt") != receipt.to_json():
            continue
        if (effect.run_id.value != plan["run_id"] or effect.effect_id.value != row["effect_id"]
                or effect.tool_name != "task_scope_update" or not value.get("ok")
                or value.get("committed_revision") != decision["committed_revision"]
                or not _matches(thaw_json(effect.arguments), plan)):
            raise ValueError("scope_field_effect_differs")
        found = await read_run_dependencies(db=db, stack=stack, sdk_run_id=plan["run_id"], before_effect_id=row["effect_id"])
        if found is None or found[0]["subject"] != subject or found[0]["primary_conversation_id"] != primary_ref:
            raise ValueError("scope_field_origin_differs")
        candidates.append((dict(producer_effect_id=row["effect_id"], producer_receipt_hash=canonical_hash(receipt.to_json())), found[1]))
    return candidates[0] if len(candidates) == 1 else None


async def mutation_fields(*, db, stack, scope_id, state, revision, subject, policy, disclosure_context, selected):
    from deskpet.execution.semantic_closure import closure_receipt_for_plan_tx
    result = {}
    for field, kinds in FIELD_KINDS.items():
        if selected is not None and field not in selected:
            continue  # don't expand a retained old manifest
        writes = [op for op in state.get("operations", ()) if op["kind"] in kinds]
        if not writes:
            continue
        latest = writes[-1]
        records = await _rows(db, "SELECT * FROM task_scope_mutation_decisions WHERE task_scope_id=? AND plan_id=?", (scope_id, latest["plan_id"]))
        if len(records) != 1:
            continue
        decision = records[0]; plan = json.loads(decision["plan_json"])
        if (canonical_hash(plan) != decision["plan_hash"] or plan["subject"] != subject
                or plan["task_scope_id"] != scope_id or decision["committed_revision"] > revision):
            raise ValueError("scope_field_plan_differs")
        ops = [op for op in plan["operations"] if op["kind"] in kinds]
        if not ops or any(ops[-1][k] != latest[k] for k in ("operation_id", "kind", "value", "reason_code")) or state.get(field) != ops[-1]["value"]:
            raise ValueError("scope_field_value_differs")
        receipt = await closure_receipt_for_plan_tx(db, task_scope_id=scope_id, plan_id=plan["plan_id"])
        if receipt is None or receipt.sdk_run_id != plan["run_id"] or receipt.outcome != "mutate":
            continue
        runs = await _rows(db, "SELECT r.subject,r.primary_conversation_id FROM foreground_runs r JOIN foreground_run_sdk_bindings b "
            "ON b.host_run_id=r.host_run_id WHERE b.sdk_run_id=?", (plan["run_id"],))
        if len(runs) != 1 or runs[0]["subject"] != subject:
            continue
        primary = runs[0]["primary_conversation_id"]
        produced = await _producer(db, stack=stack, decision=decision, plan=plan, receipt=receipt, subject=subject, primary_ref=primary)
        if produced is None:
            continue
        origin, proof = produced
        refs = [*plan["evidence_refs"], *(ref for op in plan["operations"] for ref in op["evidence_refs"])]
        for ref in refs:
            envelope, _ = await read_evidence_pair(db=db, subject=subject, primary_ref=primary, evidence_id=ref["evidence_id"])
            if envelope.envelope_hash != ref["content_hash"]:
                raise ValueError("scope_field_evidence_differs")
        proof = dependencies([*proof["evidence"], *(dict(evidence_id=r["evidence_id"], envelope_hash=r["content_hash"]) for r in refs)],
            proof["recall"], proof.get("short_horizon", ()),
            procedure_drafts=proof.get("procedure_drafts", ()))
        if policy is not None and not await policy.check_dependencies(db=db, primary_ref=primary, dependencies=proof, disclosure_context=disclosure_context):
            continue  # never fall back to the older value
        full = str(state[field])
        text = full.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
        result[field] = dict(text=text, utf8_sha256=hashlib.sha256(text.encode()).hexdigest(),
            full_utf8_sha256=hashlib.sha256(full.encode()).hexdigest(),
            producer_run_id=plan["run_id"], producer_plan_id=plan["plan_id"],
            producer_operation_id=latest["operation_id"], dependencies=proof, **origin)
    return result
