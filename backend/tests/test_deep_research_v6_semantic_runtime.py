from __future__ import annotations

import copy
import hashlib
import json

import pytest

from deskpet.workflows.adapters.deep_research_v6_semantic_runtime import (
    DeepResearchV6SemanticRuntime,
    SemanticRuntimeConfigurationError,
)
from deskpet.workflows.contracts import NodeExecutionIdentity, canonical_json
from deskpet.workflows.definitions.deep_research_v6_compiler import compile_research_spec
from deskpet.workflows.definitions.deep_research_v6_contracts import (
    build_route_decision_from_spec,
    format_blob_ref,
)
from deskpet.workflows.definitions.deep_research_v6_evidence import (
    AdmittedResearchFactV1,
)
from deskpet.workflows.definitions.deep_research_v6_evidence_contracts import (
    CandidateProducerOutcomeV1,
    EvidenceRepairRequestV1,
    EvidenceCandidateBundleV1,
    EvidenceCandidateV1,
    InferenceProposalBundleV1,
    InferenceProposalV1,
    ResearchLLMEffectOutcomeV1,
    V6FetchedPageRefPayloadV1,
)
from deskpet.workflows.definitions.deep_research_v5_contracts import ResearchLLMResult
from deskpet.workflows.store import RegisteredBlobStore


def _identity(run_id: str = "run-semantic") -> NodeExecutionIdentity:
    return NodeExecutionIdentity(
        workflow_name="deep_research",
        workflow_version="v6",
        thread_id=run_id,
        run_id=run_id,
        checkpoint_id="checkpoint-semantic",
        checkpoint_ns="root",
        task_id="task-semantic",
        node_id="semantic",
        attempt=1,
    )


async def _put(
    blobs: RegisteredBlobStore,
    identity: NodeExecutionIdentity,
    value: object,
    *,
    media_type: str = "application/json",
) -> str:
    data = (
        canonical_json(value).encode("utf-8")
        if isinstance(value, (dict, list))
        else str(value).encode("utf-8")
    )
    ref = await blobs.put(data, identity, media_type=media_type)
    return format_blob_ref(ref.sha256)


class _StagePort:
    def __init__(self, profile_ref: str, responder) -> None:
        self.profile_ref = profile_ref
        self.responder = responder
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, *, stage, payload, identity):
        self.calls.append((stage, copy.deepcopy(dict(payload))))
        return await self.responder(stage, payload, identity, self.profile_ref)


class _FailPort(_StagePort):
    async def execute(self, **_kwargs):
        raise AssertionError("exact path must not call an LLM stage")


async def _envelope(
    blobs: RegisteredBlobStore,
    identity: NodeExecutionIdentity,
    profile_ref: str,
    *,
    content,
    status: str = "validated",
    model_id: str = "gpt-test",
):
    result_ref = None
    result = None
    if status in {"validated", "malformed"}:
        result = ResearchLLMResult(
            canonical_json(content) if isinstance(content, (dict, list)) else str(content),
            model_id,
            10,
            10,
            0,
            "provider",
            "request-test",
        )
        result_ref = await _put(
            blobs, identity, result.to_json(),
            media_type="application/vnd.deskpet.research-llm-result+json",
        )
    prompt_ref = await _put(
        blobs,
        identity,
        {"prompt": "semantic-test", "profile_ref": profile_ref},
        media_type="application/vnd.deskpet.research-llm-prompt+json",
    )
    return {
        "status": status,
        "effect_id": hashlib.sha256(
            f"{profile_ref}|{prompt_ref}|{result_ref}".encode()
        ).hexdigest(),
        "result_ref": result_ref,
        "prompt_ref": prompt_ref,
        "profile_ref": profile_ref,
        "result": result,
    }


async def _fixture(tmp_path, question: str, body_text: str):
    identity = _identity()
    blobs = RegisteredBlobStore(tmp_path / "blobs", tmp_path / "workflow.db")
    spec = compile_research_spec(question, as_of_date="2026-07-18")
    route_policy_ref = await _put(
        blobs, identity, {"policy_id": "route-test-v1", "schema_version": 1}
    )
    route_policy_hash = route_policy_ref[7:]
    route = build_route_decision_from_spec(
        spec,
        run_id=identity.run_id,
        policy_hash=route_policy_hash,
        capability_snapshot_hash="c" * 64,
        budget={"query": 6, "fetch": 8, "browser": 0, "llm": 8, "lane": 6},
    )
    route_ref = await _put(blobs, identity, route.to_json())
    body = body_text.encode("utf-8")
    body_ref = await _put(blobs, identity, body_text, media_type="text/plain")
    locator_ref = await _put(
        blobs, identity, {"schema_version": 1, "url": "https://example.test/source"}
    )
    final_hash = hashlib.sha256(b"https://example.test/source").hexdigest()
    page_id = "page_" + hashlib.sha256(
        (final_hash + hashlib.sha256(body).hexdigest()).encode("ascii")
    ).hexdigest()[:24]
    page = V6FetchedPageRefPayloadV1(
        page_id=page_id,
        ordinal=0,
        source_locator_ref=locator_ref,
        body_ref=body_ref,
        body_hash=hashlib.sha256(body).hexdigest(),
        canonical_url_hash=final_hash,
        final_url_hash=final_hash,
        authority_id="example.test",
        title_hash=hashlib.sha256(b"Example").hexdigest(),
        media_type="text/plain",
        admission_status="admitted",
        reason_codes=("verified",),
    )
    page_result_ref = await _put(
        blobs,
        identity,
        {"schema_version": 1, "page_id": page.page_id, "page_ref": page.to_json()},
    )
    profile_refs = {
        name: await _put(blobs, identity, {"profile": name, "schema_version": 1})
        for name in ("extract", "inference", "repair")
    }
    page_slots = [{
        "page_result_ref": page_result_ref,
        "route_decision_ref": route_ref,
        "route_policy_ref": route_policy_ref,
    }]
    return identity, blobs, spec, route, page, page_slots, profile_refs


@pytest.mark.asyncio
async def test_exact_candidate_producer_is_deterministic_and_never_calls_llm(tmp_path) -> None:
    identity, blobs, spec, route, _, page_slots, refs = await _fixture(
        tmp_path,
        "请给出2024年中国总人口，来源国家统计局",
        "国家统计局公报：2024年年末全国人口140828万人。",
    )
    runtime = DeepResearchV6SemanticRuntime(
        blobs=blobs,
        llm_extract=_FailPort(refs["extract"], None),
        llm_inference=_FailPort(refs["inference"], None),
        llm_repair=_FailPort(refs["repair"], None),
    )

    result = await runtime.produce_candidate_slots(
        spec.to_json(), route.to_json(), page_slots, identity
    )

    assert len(result["candidate_slots"]) == 1
    slot = result["candidate_slots"][0]
    producer = CandidateProducerOutcomeV1.from_json(
        json.loads((await blobs.get(slot["producer_outcome_ref"][7:])).decode())
    )
    assert producer.origin == "deterministic"
    assert producer.status == "validated"
    assert producer.llm_effect_outcome_ref is None
    bundle_json = json.loads((await blobs.get(slot["bundle_ref"][7:])).decode())
    body_ref = bundle_json["candidates"][0]
    assert body_ref["payload"]["value"] == 1_408_280_000
    assert body_ref["span_start_byte"] < body_ref["span_end_byte"]
    assert set(result["policy_refs"]) == {
        "extraction", "llm_extract", "llm_repair", "llm_inference"
    }

    inference = await runtime.synthesize_inference_slots(
        spec.to_json(), route.to_json(), "a" * 64, (), identity
    )
    assert inference["inference_slots"] == []


def _candidate_bundle_from_prompt(payload, profile_ref, *, repair_round: int):
    body = payload["body"].encode("utf-8")
    excerpt = "Test AI"
    start = body.index(excerpt.encode())
    group = payload["work_group"]
    requirement_id = group["requirement_ids"][0]
    candidate = EvidenceCandidateV1.create(
        body_bytes=body,
        candidate_kind="collection_field",
        work_group_id=group["work_group_id"],
        logical_page_id=payload["logical_page_id"],
        page_plan_ordinal=payload["page_plan_ordinal"],
        candidate_ordinal=0,
        requirement_id=requirement_id,
        span_start_byte=start,
        span_end_byte=start + len(excerpt),
        normalized_proposition="Test AI is in the ranked collection.",
        payload={
            "item_id": "item-test-ai",
            "unique_key_values": ["Test AI"],
            "field_key": "product_name",
            "value": "Test AI",
            "canonical_unit": None,
            "as_of": "2026-07-18",
            "rank_inputs": [
                {"field_key": "attention_score", "value": 10, "missing": False},
                {"field_key": "product_name", "value": "Test AI", "missing": False},
            ],
        },
    )
    return EvidenceCandidateBundleV1.create(
        run_id=payload["route_decision"]["run_id"],
        spec_hash=payload["spec"]["spec_hash"],
        work_group_id=group["work_group_id"],
        logical_page_id=payload["logical_page_id"],
        page_plan_ordinal=payload["page_plan_ordinal"],
        page_result_ref=payload["page_result_ref"],
        route_decision_ref=payload["route_decision_ref"],
        route_policy_ref=payload["route_policy_ref"],
        extraction_policy_ref=payload["extraction_policy_ref"],
        repair_round=repair_round,
        candidates=(candidate,),
        bundle_reason_codes=(),
    )


@pytest.mark.asyncio
async def test_generic_extract_success_is_strict_and_replay_shaped(tmp_path) -> None:
    identity, blobs, spec, route, _, page_slots, refs = await _fixture(
        tmp_path, "列出Top 3最值得关注的AI产品排名", "Test AI has strong adoption."
    )

    async def extract(_stage, payload, native, profile_ref):
        bundle = _candidate_bundle_from_prompt(payload, profile_ref, repair_round=0)
        return await _envelope(blobs, native, profile_ref, content=bundle.to_json())

    async def fail(*_args):
        raise AssertionError("repair must not run for valid output")

    extract_port = _StagePort(refs["extract"], extract)
    runtime = DeepResearchV6SemanticRuntime(
        blobs=blobs,
        llm_extract=extract_port,
        llm_inference=_FailPort(refs["inference"], None),
        llm_repair=_StagePort(refs["repair"], fail),
    )
    first = await runtime.produce_candidate_slots(
        spec.to_json(), route.to_json(), page_slots, identity
    )
    replay = await runtime.produce_candidate_slots(
        spec.to_json(), route.to_json(), page_slots, identity
    )
    assert first == replay
    assert len(extract_port.calls) == 2  # durable stage wrapper replays the same outcome
    slot = first["candidate_slots"][0]
    assert slot["bundle_ref"] is not None
    producer = CandidateProducerOutcomeV1.from_json(
        json.loads((await blobs.get(slot["producer_outcome_ref"][7:])).decode())
    )
    outcome = ResearchLLMEffectOutcomeV1.from_json(
        json.loads((await blobs.get(producer.llm_effect_outcome_ref[7:])).decode())
    )
    assert outcome.status == "validated"
    assert outcome.result_ref == slot["bundle_ref"]
    assert outcome.repair_round == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["opaque_uncertain", "deadline", "budget_denied"])
async def test_generic_extract_persists_canonical_no_result_terminal_outcome(
    tmp_path, terminal_status
) -> None:
    identity, blobs, spec, route, _, page_slots, refs = await _fixture(
        tmp_path,
        "列出Top 3最值得关注的AI产品排名",
        "Test AI has strong adoption.",
    )

    async def terminal(_stage, _payload, native, profile_ref):
        return await _envelope(
            blobs,
            native,
            profile_ref,
            content=None,
            status=terminal_status,
        )

    runtime = DeepResearchV6SemanticRuntime(
        blobs=blobs,
        llm_extract=_StagePort(refs["extract"], terminal),
        llm_inference=_FailPort(refs["inference"], None),
        llm_repair=_FailPort(refs["repair"], None),
    )
    result = await runtime.produce_candidate_slots(
        spec.to_json(), route.to_json(), page_slots, identity
    )
    slot = result["candidate_slots"][0]
    assert slot["bundle_ref"] is None
    producer = CandidateProducerOutcomeV1.from_json(
        json.loads((await blobs.get(slot["producer_outcome_ref"][7:])).decode())
    )
    assert producer.status == terminal_status
    outcome = ResearchLLMEffectOutcomeV1.from_json(
        json.loads((await blobs.get(producer.llm_effect_outcome_ref[7:])).decode())
    )
    assert outcome.status == terminal_status
    assert outcome.raw_result_ref is None and outcome.result_ref is None
    assert outcome.dependency_refs == tuple(
        sorted((outcome.profile_ref, outcome.prompt_ref))
    )


@pytest.mark.asyncio
async def test_generic_extract_uses_one_repair_and_second_malformed_is_terminal(tmp_path) -> None:
    identity, blobs, spec, route, _, page_slots, refs = await _fixture(
        tmp_path, "列出Top 3最值得关注的AI产品排名", "Test AI has strong adoption."
    )

    async def malformed(_stage, _payload, native, profile_ref):
        return await _envelope(blobs, native, profile_ref, content="not-json")

    repair_calls = 0

    async def repair(_stage, payload, native, profile_ref):
        nonlocal repair_calls
        repair_calls += 1
        bundle = _candidate_bundle_from_prompt(payload, profile_ref, repair_round=1)
        return await _envelope(
            blobs, native, profile_ref,
            content={
                "result_kind": "candidate_bundle",
                "candidate_bundle": bundle.to_json(),
                "inference_bundle": None,
            },
        )

    runtime = DeepResearchV6SemanticRuntime(
        blobs=blobs,
        llm_extract=_StagePort(refs["extract"], malformed),
        llm_inference=_FailPort(refs["inference"], None),
        llm_repair=_StagePort(refs["repair"], repair),
    )
    repaired = await runtime.produce_candidate_slots(
        spec.to_json(), route.to_json(), page_slots, identity
    )
    assert repaired["candidate_slots"][0]["bundle_ref"] is not None
    assert repair_calls == 1
    repaired_slot = repaired["candidate_slots"][0]
    repaired_producer = CandidateProducerOutcomeV1.from_json(
        json.loads((await blobs.get(repaired_slot["producer_outcome_ref"][7:])).decode())
    )
    repaired_outcome = ResearchLLMEffectOutcomeV1.from_json(
        json.loads((await blobs.get(repaired_producer.llm_effect_outcome_ref[7:])).decode())
    )
    assert repaired_outcome.repair_round == 1
    assert repaired_outcome.prior_outcome_ref is not None
    repair_request = EvidenceRepairRequestV1.from_json(
        json.loads((await blobs.get(repaired_outcome.repair_request_ref[7:])).decode())
    )
    assert repair_request.prior_outcome_ref == repaired_outcome.prior_outcome_ref

    runtime = DeepResearchV6SemanticRuntime(
        blobs=blobs,
        llm_extract=_StagePort(refs["extract"], malformed),
        llm_inference=_FailPort(refs["inference"], None),
        llm_repair=_StagePort(refs["repair"], malformed),
    )
    terminal = await runtime.produce_candidate_slots(
        spec.to_json(), route.to_json(), page_slots, identity
    )
    slot = terminal["candidate_slots"][0]
    assert slot["bundle_ref"] is None
    producer = CandidateProducerOutcomeV1.from_json(
        json.loads((await blobs.get(slot["producer_outcome_ref"][7:])).decode())
    )
    assert producer.status == "malformed"


@pytest.mark.asyncio
async def test_inference_runs_only_from_registered_fact_refs_and_repairs_once(tmp_path) -> None:
    identity, blobs, spec, route, _, _, refs = await _fixture(
        tmp_path, "列出Top 3最值得关注的AI产品排名", "Test AI has strong adoption."
    )
    requirement_id = spec.to_json()["requirements"][0]["requirement_id"]
    fact = AdmittedResearchFactV1.create(
        run_id=identity.run_id,
        spec_hash=spec.spec_hash,
        requirement_id=requirement_id,
        target_kind="scalar",
        item_or_cell_id=None,
        field_or_facet_key=None,
        candidate_id="candidate-one",
        page_id="page-one",
        span_id="span-one",
        binding_id="binding-one",
        source_family_id="example.test",
        source_tier="secondary",
        admission_policy_hash="d" * 64,
        status="admitted",
        semantic_payload={
            "value": 10,
            "canonical_unit": None,
            "time_scope": "2026",
            "scope": "global",
            "definition": "attention score",
        },
    )
    fact_ref = await _put(blobs, identity, fact.to_json())

    async def malformed(_stage, _payload, native, profile_ref):
        return await _envelope(blobs, native, profile_ref, content="not-json")

    async def repair(_stage, payload, native, profile_ref):
        group = payload["work_group"]
        proposal = InferenceProposalV1.create(
            requirement_id=requirement_id,
            inference_kind="conclusion",
            item_or_cell_id="item-test-ai",
            facet_ids=("ranking",),
            normalized_proposition="Test AI ranks highly on the admitted score.",
            premise_fact_refs=(fact_ref,),
            model_id="gpt-test",
            model_policy_ref=profile_ref,
        )
        bundle = InferenceProposalBundleV1.create(
            run_id=identity.run_id,
            spec_hash=spec.spec_hash,
            work_group_id=group["work_group_id"],
            input_evidence_head_hash=payload["input_evidence_head_hash"],
            ordinal=payload["ordinal"],
            profile_ref=profile_ref,
            proposals=(proposal,),
        )
        return await _envelope(
            blobs, native, profile_ref,
            content={
                "result_kind": "inference_bundle",
                "candidate_bundle": None,
                "inference_bundle": bundle.to_json(),
            },
        )

    runtime = DeepResearchV6SemanticRuntime(
        blobs=blobs,
        llm_extract=_FailPort(refs["extract"], None),
        llm_inference=_StagePort(refs["inference"], malformed),
        llm_repair=_StagePort(refs["repair"], repair),
    )
    result = await runtime.synthesize_inference_slots(
        spec.to_json(), route.to_json(), "e" * 64, (fact_ref,), identity
    )
    assert len(result["inference_slots"]) == 1
    inference_slot = result["inference_slots"][0]
    assert inference_slot["proposal_bundle_ref"] is not None
    inference_outcome = ResearchLLMEffectOutcomeV1.from_json(
        json.loads((await blobs.get(inference_slot["effect_outcome_ref"][7:])).decode())
    )
    assert inference_outcome.result_ref == inference_slot["proposal_bundle_ref"]
    assert inference_outcome.repair_round == 1
    assert result["policy_refs"] == {
        "llm_inference": refs["inference"],
        "inference": result["policy_refs"]["inference"],
    }


@pytest.mark.asyncio
async def test_stage_without_registered_outcome_metadata_fails_closed(tmp_path) -> None:
    identity, blobs, spec, route, _, page_slots, refs = await _fixture(
        tmp_path, "列出Top 3最值得关注的AI产品排名", "Test AI has strong adoption."
    )

    async def legacy(_stage, _payload, _identity, _profile_ref):
        return {"content": "{}"}

    runtime = DeepResearchV6SemanticRuntime(
        blobs=blobs,
        llm_extract=_StagePort(refs["extract"], legacy),
        llm_inference=_FailPort(refs["inference"], None),
        llm_repair=_FailPort(refs["repair"], None),
    )
    with pytest.raises(SemanticRuntimeConfigurationError, match="effect_id/result_ref"):
        await runtime.produce_candidate_slots(
            spec.to_json(), route.to_json(), page_slots, identity
        )
