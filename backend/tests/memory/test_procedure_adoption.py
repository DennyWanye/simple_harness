"""Host semantic classification mapping and exact sources; no real model calls.

The scripted classifications test the binding/compiler, not language accuracy.
Public installed Memory writes and durable response-only replay are exercised
separately from unit mutations; SQL below only reads resulting test evidence.
"""
from copy import deepcopy
from dataclasses import replace
import asyncio
import json
from types import SimpleNamespace

import pytest
from simple_harness import thaw_json
from simple_harness.runtime import AnalysisBudget, EvidenceRef, EvidenceSourceKind, MemoryAnalysisRequest
from simple_harness_memory import MemoryManager, MemoryPrincipal

from deskpet.memory import analysis_proposal as v3, analysis_proposal_v4 as v4
from deskpet.memory import analysis_proposal_v5 as v5
from deskpet.memory.analysis_executor import HostMemoryAnalysisExecutor, HostAnalysisExecutorError
from deskpet.memory.analysis_protocol import protocol_for_request
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_service import build_foreground_turn_evidence
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker, build_worker_config
from tests.sdk_adapters import s5b_memory_harness as mh, s5b_closure_harness as ch

ADOPTION = "以后整理文件就按这两步：先列清单，再复制到备份目录。"
REPORTED = "这次整理文件先列清单，再复制到备份目录，成功了一次。"
UNCERTAIN = "可以考虑先列清单，再复制到备份目录，但我还没决定采用。"
STEPS = ["先列清单", "再复制到备份目录"]


def operation(item_id, text, *, intent="adoption", risk="low"):
    return {
        "operation_id": "procedure-1", "memory_type": "procedure", "action": "create", "candidate_key": "",
        "evidence_item_id": item_id, "exact_quote": text, "reason_code": "model_cannot_grant_authority",
        "procedure": {"name": "文件整理步骤", "steps": list(STEPS), "risk_level": risk,
                      "intent_kind": intent, "adoption_quote": "以后整理文件就按这两步" if intent == "adoption" else ""},
    }


def compilation(text=ADOPTION, *, intent="adoption"):
    envelope, receipt = build_foreground_turn_evidence(subject="actor-1", authority_ref=mh.AUTHORITY_REF,
        delivery_key="procedure-source", text=text)
    item = v3.admitted_item(envelope, receipt)
    request = MemoryAnalysisRequest(job_id="procedure-analysis", run_id=envelope.run_id, subject=envelope.subject,
        ordered_evidence_refs=(EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),),
        prompt_version=v4.PROMPT_VERSION, result_schema_version=v4.RESULT_SCHEMA_VERSION,
        policy_version=v4.POLICY_VERSION, provider_id="provider-1", model_id="model-1", model_config_hash="a" * 64,
        attempt=1, budget=AnalysisBudget(2048, 1024, 5000, 1000),
        disclosure_context=envelope.disclosure_context, idempotency_key="procedure-analysis")
    return SimpleNamespace(items=[item], request=request,
        proposal={"outcome": "mutate", "operations": [operation(item.item_id, text, intent=intent)]})


def compile_case(case):
    return v4.compile_proposal(case.proposal, request=case.request, items=case.items,
        base_revision=1, plan_id="host-procedure-unit", now=1.0)


@pytest.mark.parametrize("text,intent,lifecycle", [
    (ADOPTION, "adoption", "active"), (REPORTED, "reported_steps", "draft"),
    (UNCERTAIN, "uncertain", "draft"),
])
def test_classification_maps_only_after_exact_user_steps(text, intent, lifecycle):
    case = compilation(text, intent=intent)
    compiled = compile_case(case)
    assert not compiled.rejected and compiled.operation_count == 1
    op = compiled.plan.operations[0]
    assert op.lifecycle_state.value == lifecycle and op.payload.steps == tuple(STEPS)
    assert op.epistemic_status.value == "explicit_user" and op.verification_state.value == "source_bound"
    assert op.reason_code == f"host_procedure_{intent}_source_bound"
    assert [s.exact_quote for s in op.evidence_spans] == [text, *STEPS, *(
        ["以后整理文件就按这两步"] if intent == "adoption" else [])]
    for span in op.evidence_spans:
        assert text.encode()[span.start_byte:span.end_byte] == span.exact_quote.encode()
        assert span.admission_receipt_hash == case.items[0].receipt.receipt_hash
        assert span.typed_observation is None


@pytest.mark.parametrize("change", ["missing_intent", "bool_intent", "empty_adoption", "unknown_grant",
    "truncated_context", "fabricated_step", "reversed_steps", "duplicate_step", "overlap_steps",
    "ambiguous_quote", "tuple_steps", "name_nul", "duplicate_item", "foreign_subject", "ref_hash",
    "receipt_hash", "assistant_source", "different_item_text", "body_other_type"])
def test_procedure_source_or_structure_mutations_never_create(change):
    case = compilation(ADOPTION + "以后整理文件就按这两步" if change == "ambiguous_quote" else ADOPTION)
    raw = case.proposal["operations"][0]
    body = raw["procedure"]
    if change == "missing_intent":
        body.pop("intent_kind")
    elif change == "bool_intent":
        body["intent_kind"] = True
    elif change == "empty_adoption":
        body["adoption_quote"] = ""
    elif change == "unknown_grant":
        body["authorized"] = True
    elif change == "truncated_context":
        raw["exact_quote"] = STEPS[0]
    elif change == "fabricated_step":
        body["steps"][1] = "删除目录"
    elif change == "reversed_steps":
        body["steps"].reverse()
    elif change == "duplicate_step":
        body["steps"] = [STEPS[0], STEPS[0]]
    elif change == "overlap_steps":
        body["steps"] = [STEPS[0], "清单"]
    elif change == "ambiguous_quote":
        assert case.items[0].text.count(body["adoption_quote"]) == 2
    elif change == "tuple_steps":
        body["steps"] = tuple(STEPS)
    elif change == "name_nul":
        body["name"] = "invalid\x00name"
    elif change == "duplicate_item":
        case.items *= 2
    elif change == "foreign_subject":
        case.request = replace(case.request, subject="other-actor")
    elif change == "ref_hash":
        case.request = replace(case.request, ordered_evidence_refs=(EvidenceRef(case.items[0].evidence_id, "0" * 64, 1),))
    elif change == "receipt_hash":
        item = case.items[0]
        case.items[0] = replace(item, receipt=replace(item.receipt, source_hash="0" * 64))
    elif change == "assistant_source":
        item = case.items[0]
        envelope = replace(item.envelope, source_kind=EvidenceSourceKind.ASSISTANT_MESSAGE)
        case.items[0] = replace(item, envelope=envelope,
            receipt=replace(item.receipt, envelope_hash=envelope.envelope_hash))
        case.request = replace(case.request,
            ordered_evidence_refs=(EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),))
    elif change == "different_item_text":
        case.items[0] = replace(case.items[0], text=ADOPTION + " fabricated")
    elif change == "body_other_type":
        raw["semantic"] = {"subject_entity": "user:self", "predicate": "x", "object_value": "y"}
    result = compile_case(case)
    assert result.plan is None and result.rejected and result.outcome == "no_mutation"


def test_high_risk_adoption_is_memory_state_and_model_semantics_remain_explicit_boundary():
    case = compilation()
    case.proposal["operations"][0]["procedure"]["risk_level"] = "irreversible"
    op = compile_case(case).plan.operations[0]
    assert op.lifecycle_state.value == "active" and op.payload.proposed_risk_level.value == "irreversible"
    assert all(span.typed_observation is None for span in op.evidence_spans)
    # Deliberately wrong semantic classification: exact quoting alone CANNOT
    # detect this error. Keep that limitation reviewable, never invent NLP gates.
    wrong = compilation(REPORTED, intent="reported_steps")
    wrong.proposal["operations"][0]["procedure"].update(intent_kind="adoption", adoption_quote="成功了一次")
    assert compile_case(wrong).plan.operations[0].lifecycle_state.value == "active"


def test_exact_version_choice_v3_unchanged_and_v4_has_no_missing_field_fallback():
    case = compilation(REPORTED, intent="reported_steps")
    raw = deepcopy(case.proposal)
    raw["operations"][0]["procedure"].pop("intent_kind")
    raw["operations"][0]["procedure"].pop("adoption_quote")
    old_request = replace(case.request, prompt_version=v3.PROMPT_VERSION,
        result_schema_version=v3.RESULT_SCHEMA_VERSION, policy_version=v3.POLICY_VERSION)
    old = protocol_for_request(old_request).compile_proposal(raw, request=old_request, items=case.items,
        base_revision=1, plan_id="v3-compat", now=1.0)
    assert old.plan.operations[0].lifecycle_state.value == "active"
    case.proposal = raw
    assert compile_case(case).plan is None
    with pytest.raises(v3.AnalysisProposalRejected, match="analysis_protocol_unsupported"):
        protocol_for_request(replace(case.request, policy_version=v3.POLICY_VERSION))
    assert "intent_kind" not in v3.PROPOSAL_TOOL_SCHEMA["properties"]["operations"]["items"]["properties"]["procedure"]["properties"]


def public_env(env, adapter, *, fault=None, version=4):
    executor = HostMemoryAnalysisExecutor(env.db_path, adapter_factory=lambda _: adapter,
        clock=env.clock, fault_inject=fault)

    async def builder(path, **kwargs):
        return await MemoryManager.build_human_memory_v7(path, **kwargs, allow_development_embedder=True)

    runtime = HumanMemoryV7Runtime(env.db_path.parent / "procedure-memory.db",
        evidence_authority=HostEvidenceAuthority(env.db_path), analysis_authority=executor,
        backend_factory=builder, clock=env.clock,
        principal=MemoryPrincipal("deskpet-local", "deskpet-local-household", mh.SUBJECT, "primary-conversation"))
    config = build_worker_config(provider_id=mh.BINDING["provider_id"], model_id=mh.BINDING["model_id"],
        model_config_hash=mh.expected_model_config_hash(), deadline_ms=5000)
    protocol = {3: v3, 4: v4, 5: v5}[version]
    config = replace(config, prompt_version=protocol.PROMPT_VERSION,
        result_schema_version=protocol.RESULT_SCHEMA_VERSION, policy_version=protocol.POLICY_VERSION)
    return mh.MemoryEnv(executor=executor, runtime=runtime, config=config, adapter=adapter,
        clock=env.clock, worker_id="procedure-worker", runner=None,
        worker=MemoryIngestionOutboxWorker(env.db_path, runtime.manager, owner_id="procedure-outbox", clock=env.clock))


@pytest.mark.asyncio
@pytest.mark.parametrize("version,recover", [(4, False), (4, True), (3, True)])
async def test_public_materialization_and_response_only_reopen_keep_persisted_protocol(tmp_path, monkeypatch, version, recover, recovery_version=4):
    env = await mh.bound_turn_run(tmp_path, "procedure-source-run", text=REPORTED)
    await mh.finish_clean_run(env)
    raw = operation(mh.item_id(env), REPORTED, intent="reported_steps")
    if version == 3:
        raw["procedure"].pop("intent_kind")
        raw["procedure"].pop("adoption_quote")
    adapter = ch.FakeAdapter([mh.proposal_call([raw])])
    def interrupt(point):
        if point == "analysis-before-derive":
            raise RuntimeError("new-control: response-only crash")
    menv = public_env(env, adapter, fault=interrupt if recover else None, version=version)
    try:
        assert await menv.worker.run_once() == "delivered"
        assert await mh.run_job(menv) == ("retry_scheduled" if recover else "applied")
        assert len(adapter.calls) == 1
        if recover:
            [(saved_request_json, saved_request_hash, saved_state)] = await mh.memory_rows(
                menv, "SELECT request_json,request_hash,state FROM analysis_batches")
            assert saved_state == "failed"  # Ordinary Exception, not expiry reclaim.
    finally:
        await mh.close(menv)
    env.clock.now += 60
    menv = public_env(env, adapter, version=recovery_version)
    try:
        if recover:
            assert await mh.run_job(menv) == "applied"
        assert len(adapter.calls) == 1 and menv.executor.provider_calls == 0
        # Read-only diagnostics of actual public application, no SQL seed edits.
        [(state,)] = await mh.memory_rows(menv, "SELECT lifecycle_state FROM cognitive_memory_revisions")
        assert state == ("active" if version == 3 else "draft")
        assert await mh.memory_rows(menv, "SELECT success_evidence_count,failure_evidence_count FROM procedure_records") == [(0, 0)]
        assert await mh.memory_rows(menv, "SELECT COUNT(*) FROM procedure_observation_authority_consumptions") == [(0,)]
        [(request_json,)] = await mh.memory_rows(menv, "SELECT request_json FROM analysis_batches WHERE state='applied'")
        request = MemoryAnalysisRequest.from_json(json.loads(request_json))
        if recover:
            attempt_fields = {"job_id", "attempt", "idempotency_key"}
            saved_input = json.loads(saved_request_json)
            assert {key: value for key, value in request.to_json().items() if key not in attempt_fields} == {
                key: value for key, value in saved_input.items() if key not in attempt_fields}
            assert request.request_hash != saved_request_hash
            assert await mh.memory_rows(menv,
                "SELECT request_json,request_hash FROM analysis_batches WHERE state='failed'") == [
                    (saved_request_json, saved_request_hash)]
        assert request.prompt_version == f"host-analysis-prompt/v{version}"
        durable, _ = await menv.executor._durable_envelope(request.request_hash)
        before = durable.to_json()
        protocol = protocol_for_request(request)
        with monkeypatch.context() as patch:
            patch.setattr(protocol, "compile_proposal", lambda *_args, **_kwargs: pytest.fail("saved plan must not recompile"))
            assert (await menv.executor.analyze_memory(request)).to_json() == before
        assert len(adapter.calls) == 1 and menv.executor.provider_calls == 0
        tools = adapter.calls[0].tools
        properties = thaw_json(tools[0].parameters)["properties"]["operations"]["items"]["properties"]["procedure"]["properties"]
        assert ("intent_kind" in properties) is (version == 4)
        for bad in (replace(request, policy_version="host-analysis-policy/unknown"),
                    replace(request, policy_version=v3.POLICY_VERSION if version == 4 else v4.POLICY_VERSION)):
            with pytest.raises(HostAnalysisExecutorError, match="analysis_protocol_unsupported"):
                await menv.executor.analyze_memory(bad)
        assert len(adapter.calls) == 1
        assert mh.rows(env.db_path, "SELECT COUNT(*) FROM host_pre_admission_audit WHERE reason_code=?",
            "analysis_protocol_unsupported") == [(2,)]
    finally:
        await mh.close(menv)


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3, 4])
async def test_cancelled_response_only_claim_reopens_original_version(tmp_path, version):
    # Cancellation is a distinct SDK recovery path from an executor Exception;
    # do not replace or weaken the failed-version-cutover counterexample above.
    env = await mh.bound_turn_run(tmp_path, "procedure-cancelled-source", text=REPORTED)
    await mh.finish_clean_run(env)
    raw = operation(mh.item_id(env), REPORTED, intent="reported_steps")
    if version == 3:
        raw["procedure"].pop("intent_kind")
        raw["procedure"].pop("adoption_quote")
    adapter = ch.FakeAdapter([mh.proposal_call([raw])])
    def cancel(point):
        if point == "analysis-before-derive":
            raise asyncio.CancelledError()
    menv = public_env(env, adapter, version=version, fault=cancel)
    try:
        assert await menv.worker.run_once() == "delivered"
        with pytest.raises(asyncio.CancelledError):
            await mh.run_job(menv)
        [(saved, saved_hash, state)] = await mh.memory_rows(menv,
            "SELECT request_json,request_hash,state FROM analysis_batches")
        assert state == "handed_off" and len(adapter.calls) == 1
    finally:
        await mh.close(menv)
    env.clock.now += 60
    menv = public_env(env, adapter)  # actual latest worker config, no v3 override
    try:
        assert await mh.run_job(menv) == "applied"
        assert await mh.memory_rows(menv, "SELECT request_json,request_hash FROM analysis_batches") == [(saved, saved_hash)]
        assert await mh.memory_rows(menv, "SELECT lifecycle_state FROM cognitive_memory_revisions") == [
            ("active" if version == 3 else "draft",)]
        assert len(adapter.calls) == 1 and menv.executor.provider_calls == 0
    finally:
        await mh.close(menv)
