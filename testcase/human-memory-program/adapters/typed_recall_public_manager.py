"""Public calls for the bridge's first executable path, not a 391-cell oracle.

Only validation-side helpers and SDK package roots are imported. Inputs contain
no expected outcome, source hash or oracle scorer. Unimplemented cells retain
BLOCKED; actual returned objects are recorded verbatim, never made to fit gold.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import time
import inspect
from datetime import datetime
from pathlib import Path

import simple_harness as harness
import simple_harness_memory as memory


def _helpers(filename="semantic_relation_public_manager.py"):
    spec = importlib.util.spec_from_file_location(
        filename.removesuffix(".py"), Path(__file__).with_name(filename)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def baseline_cases(request, workspace):
    if request["layer"] != "public":
        raise ValueError("public adapter cannot execute source/fault cells")
    cell_id = "eligibility/valid-until-null-unbounded"
    if cell_id not in request["cell_ids"]:
        raise ValueError("public dispatch cell is absent from the frozen partition")
    helpers = _helpers()
    public_cases = _helpers("typed_recall_public_cases.py")
    raw, validity = request["inputs"]["claim"], request["inputs"]["validity"]
    # Recall uses the frozen explicit now. Audit authority uses current time;
    # paging lacks an internal clock input, so expiry remains an explicit blocker.
    actual_now = time.time()
    now = datetime.fromisoformat(validity["now"].replace("Z", "+00:00")).timestamp()
    clock_kwargs = {"clock":lambda:now} if "clock" in inspect.signature(memory.MemoryManager.build_human_memory_v7).parameters else {}
    if clock_kwargs:
        actual_now = now
    valid_from = datetime.fromisoformat(validity["valid_from"].replace("Z", "+00:00")).timestamp()
    subject = "principal-1"
    principal = memory.MemoryPrincipal("bridge-deployment", "bridge-household", subject, "bridge-session")
    envelope, admission, span = helpers._evidence(subject)
    text = f"My {raw['predicate']} is {raw['object_value']}."
    source_hash = hashlib.sha256(text.encode()).hexdigest()
    evidence_payload = {"item_id": span.item_id, "public_text": text}
    envelope = dataclasses.replace(envelope, source_hash=source_hash,
        sanitized_payload=evidence_payload, sanitized_hash=harness.fingerprint_json(evidence_payload))
    admission = dataclasses.replace(admission, envelope_hash=envelope.envelope_hash,
        source_hash=source_hash, sanitized_hash=envelope.sanitized_hash)
    span = dataclasses.replace(span, envelope_hash=envelope.envelope_hash,
        sanitized_hash=envelope.sanitized_hash, admission_receipt_hash=admission.receipt_hash,
        source_hash=source_hash, end_byte=len(text.encode()), exact_quote=text, quote_hash=source_hash)
    authority = helpers._Authority(harness.AdmittedEvidenceAuthority(envelope, admission, helpers._item_authority(span)))
    policy = memory.InformationClassificationPolicy(policy_id="bridge-policy", policy_version="1",
        authority_ref="bridge-policy-authority", required_privacy_class=harness.PrivacyClass.PERSONAL,
        required_information_attributes=())
    # The approved source vector uses the complete Semantic wire and array type.
    if raw["qualifiers"] != []:
        raise ValueError("claim vector requires the approved empty qualifier array")
    payload = harness.SemanticMemoryPayload(raw["subject_entity"], raw["predicate"], raw["object_value"], ())
    audit_authority, audit_ref = public_cases.audit_authority(principal, actual_now)
    manager = await memory.build_human_memory_v6(workspace / "public.sqlite",
        audit_access_authority=audit_authority,
        evidence_authority=authority, memory_action_authority=authority,
        classification_policy=policy, **clock_kwargs)
    calls = ["build_human_memory_v6"]
    extra_cells = []
    try:
        async def snapshot():
            value = await manager.export_canonical_state_manifest(requester=principal,
                target_principal=principal, access_receipt=audit_receipt)
            return {"manifest": value.manifest.to_json(), "payload_hash": value.manifest.payload_hash,
                    "access_event_hash": value.access_event_hash}
        await manager.ingest_committed_evidence(envelope, admission)
        calls.append("ingest_committed_evidence")
        operation = helpers._shared_operation(span, operation_id="bridge-create",
            kind=harness.MemoryMutationKind.CREATE, memory_type=harness.LongTermMemoryType.SEMANTIC,
            payload=payload, lifecycle_state=harness.SemanticLifecycleState.ACTIVE,
            proposed_information_attributes=(), valid_time_interval=harness.ValidTimeInterval(valid_from, validity["valid_until"]))
        mutation = harness.MemoryMutationPlan(plan_id="bridge-mutation", run_id=envelope.run_id,
            turn_id="bridge-turn", subject=subject, base_revision=1,
            outcome=harness.MemoryMutationPlanOutcome.MUTATE, operations=(operation,),
            disclosure_context=envelope.disclosure_context,
            evidence_refs=(harness.EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),),
            idempotency_key="bridge-mutation")
        applied = await manager.apply_memory_mutation_plan(principal=principal,
            scope=memory.MemoryScope.personal(subject), plan=mutation)
        calls.append("apply_memory_mutation_plan")
        if applied.outcome is not harness.MemoryMutationApplyOutcome.COMMITTED or applied.receipt_ref is None:
            raise AssertionError("public seed mutation did not commit")
        receipt = await manager.get_memory_mutation_receipt_view(principal=principal, receipt_ref=applied.receipt_ref)
        calls.append("get_memory_mutation_receipt_view")
        audit_receipt = await manager.authorize_audit_access(principal=principal, authority_ref=audit_ref)
        calls.append("authorize_audit_access")
        budget = harness.RecallBudget(8, 16384, 2048, 2000)
        context = harness.RecallContext(run_id=envelope.run_id, subject=subject, turn_id="bridge-turn",
            context_revision=1, expires_at=now + 300, query=str(raw["object_value"]), active_task_scope_id=None,
            available_memory_types=(harness.LongTermMemoryType.SEMANTIC,), short_horizon_allowed=False,
            allowed_selector_domains=(harness.RecallSelectorDomain.MEMORY_TYPE,),
            allowed_retrieval_modes=(harness.RecallRetrievalMode.FULL_TEXT,), allowed_task_scope_ids=(),
            allowed_entity_constraints=(), earliest_occurred_at=None, latest_occurred_at=None,
            event_constraint_refs=(), environment_constraint_refs=(), task_phase_authority_refs=(),
            procedure_applicability_fingerprints=(), disclosure_context=envelope.disclosure_context,
            evidence_refs=mutation.evidence_refs, budget=budget)
        plan = harness.RecallPlan(plan_id="bridge-recall", run_id=context.run_id, subject=subject,
            context_hash=context.context_hash, context_revision=context.context_revision, query=context.query,
            requested_memory_types=context.available_memory_types, include_short_horizon=False,
            selector_domains=context.allowed_selector_domains, retrieval_modes=context.allowed_retrieval_modes,
            task_scope_ids=(), entity_constraints=(), earliest_occurred_at=None, latest_occurred_at=None,
            event_constraint_refs=(), environment_constraint_refs=(), task_phase_authority_refs=(),
            disclosure_context=context.disclosure_context, evidence_refs=context.evidence_refs, budget=budget,
            idempotency_key="bridge-recall", reason_codes=(harness.RecallReasonCode.USER_FACT_DEPENDENCY,))
        execution = await manager.execute_typed_recall(principal=principal, context=context, plan=plan, now=now)
        calls.append("execute_typed_recall")
        if not execution.result.items:
            raise AssertionError("the valid unbounded Semantic source was not recalled")
        replay = await manager.execute_typed_recall(principal=principal, context=context, plan=plan, now=now)
        calls.append("execute_typed_recall:replay")
        if replay.result.to_json() != execution.result.to_json():
            raise AssertionError("durable result replay changed")
        calls.append("page_typed_recall_result")
        try:
            page = await manager.page_typed_recall_result(principal=principal,
                request=harness.RecallResultPageRequestV1(execution.result.result_id,
                    execution.result.result_hash, 1, 0, 8, 16384, now))
            page_wire = page.to_json()
        except memory.MemoryValidationError as exc:
            page_wire = {"exception_type": type(exc).__name__, "exception_reason": str(exc)}
        observations = {"calls": calls, "mutation_receipt": receipt.to_json(),
            "mutation_plan": mutation.to_json(), "apply_result": applied.to_json(), "apply_result_hash": applied.result_hash,
            "execution": public_cases.execution_wire(execution), "replay": public_cases.execution_wire(replay),
            "recall_context": context.to_json(), "recall_plan": plan.to_json(),
            "decision": execution.decision.to_json(), "result": execution.result.to_json(),
            "result_hash": execution.result.result_hash,
            "result_item_hashes": [item.result_item_hash for item in execution.result.items],
            "page": page_wire, "candidate_query_count": execution.candidate_query_count,
            "replay_candidate_query_count": replay.candidate_query_count,
            "source_input": raw, "recall_now": now, "audit_now": actual_now,
            "state_manifest": await snapshot()}
        extra_cells = await public_cases.replay_cases(manager, principal, context, plan,
            request["inputs"], now, snapshot)
    finally:
        await manager.close()
        calls.append("close")
    return [{"cell_id":cell_id,"status":"OBSERVED","reason":"","observations":observations}] + extra_cells


async def run(request, workspace):
    inputs=request['inputs'];selected=set(request.get('selected_cells',request['cell_ids']))
    cells=[]
    if any(name=='eligibility/valid-until-null-unbounded' or name.startswith('unsupported-replay/') for name in selected):
        cells += await baseline_cases(request,workspace)
    cells += await _helpers('typed_recall_normal_cases.py').run_cases([r for r in inputs['normal'] if r['cell_id'] in selected],workspace)
    conflict={**inputs['conflict'],'cases':[r for r in inputs['conflict']['cases'] if 'conflict-state/'+r['id'] in selected],
        'state_cells':[name for name in inputs['conflict']['state_cells'] if name in selected]}
    cells += await _helpers('typed_recall_conflict_cases.py').run_cases(conflict,workspace)
    cells += await _helpers('typed_recall_conflict_cases.py').run_state_cases(conflict,workspace)
    returns={**inputs['returns'],'cases':[r for r in inputs['returns']['cases'] if 'protocol/'+r['id'] in selected]}
    cells += await _helpers('typed_recall_return_cases.py').run_cases(returns,workspace)
    short={**inputs['short'],'cases':[r for r in inputs['short']['cases'] if r['cell_id'] in selected]}
    cells += await _helpers('typed_recall_short_cases.py').run_cases(short,workspace)
    context_use=_helpers('typed_recall_context_use_cases.py')
    context_cells=sorted(selected.intersection(context_use.CELLS))
    if context_cells:
        cells += await context_use.run_cases({'cells':context_cells,'recipe':inputs['context_use']},workspace)
    authority=_helpers('typed_recall_authority_event_cases.py')
    authority_cells=sorted(selected.intersection(authority.CELLS))
    if authority_cells:
        cells += await authority.run_cases({'cells':authority_cells,'recipe':inputs['context_use'],
            'events':inputs['authority_events']['events']},workspace)
    selection=_helpers('typed_recall_selection_cases.py')
    selection_cells=sorted(selected.intersection(selection.CELLS))
    if selection_cells:
        cells += await selection.run_cases({'cells':selection_cells,'selection':inputs['selection']['cases'],
            'version':inputs['selection']['version']},workspace)
    done={row['cell_id'] for row in cells}
    cells += [{'cell_id':name,'status':'BLOCKED',
        'reason':'CELL_EXECUTOR_NOT_IMPLEMENTED' if name in selected else 'CELL_NOT_SELECTED_THIS_BATCH',
        'observations':{}} for name in request['cell_ids'] if name not in done]
    return cells
