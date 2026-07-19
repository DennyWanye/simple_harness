from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from deskpet.workflows import WorkflowContext
from deskpet.workflows.contracts import NodeExecutionIdentity, canonical_json
from deskpet.workflows.definitions.deep_research_v6_contracts import (
    ResearchSpecV1,
    build_route_decision_from_spec,
)
from deskpet.workflows.definitions.deep_research_v6_production_nodes import (
    ADMISSION_POLICY_V1,
    ASSESSMENT_POLICY_V1,
    INFERENCE_POLICY_V1,
    admit_facts_handler,
    assess_answer_handler,
    compile_spec_handler,
    extract_candidate_bundles_handler,
    load_pages_handler,
    plan_route_handler,
    register_inferences_handler,
    synthesize_inferences_handler,
    validate_candidate_admission,
)
from deskpet.workflows.definitions.deep_research_v6_compiler import compile_research_spec
from deskpet.workflows.definitions.deep_research_v6_evidence_contracts import EvidenceCandidateV1
from deskpet.workflows.definitions.v6.deep_research import (
    DEEP_RESEARCH_V6_DEFINITION,
    NODE_IDS,
    initial_state,
)
from deskpet.workflows.store import RegisteredBlobStore


def _identity(run_id: str, node_id: str) -> NodeExecutionIdentity:
    return NodeExecutionIdentity(
        workflow_name="deep_research",
        workflow_version="v6",
        thread_id=run_id,
        run_id=run_id,
        checkpoint_id=f"checkpoint-{node_id}",
        checkpoint_ns="",
        task_id=f"task-{node_id}",
        node_id=node_id,
        attempt=1,
    )


async def _put_policy(blobs, identity, value):
    ref = await blobs.put(
        canonical_json(value).encode("utf-8"), identity, media_type="application/json"
    )
    return f"sha256:{ref.sha256}", {"id": ref.sha256, "sha256": ref.sha256}


class _Retrieval:
    def __init__(self, blobs: RegisteredBlobStore):
        self.blobs = blobs
        self.plan_calls = 0
        self.load_calls = 0

    async def plan_route(self, *, spec, identity):
        self.plan_calls += 1
        parsed = ResearchSpecV1.from_json(spec)
        policy = {"schema_version": 1, "policy_id": "test-route-v1"}
        policy_ref, policy_item = await _put_policy(self.blobs, identity, policy)
        decision = build_route_decision_from_spec(
            parsed,
            run_id=identity.run_id,
            policy_hash=policy_ref[7:],
            capability_snapshot_hash="c" * 64,
            budget={"query": 1, "fetch": 1, "browser": 0, "llm": 0, "lane": 1},
        )
        decision_blob = await self.blobs.put(
            canonical_json(decision.to_json()).encode("utf-8"),
            identity,
            media_type="application/json",
        )
        decision_ref = f"sha256:{decision_blob.sha256}"
        return {
            "route_policy_ref": policy_ref,
            "route_policy_hash": policy_ref[7:],
            "route_decision_ref": decision_ref,
            "route_id": decision.to_json()["route_id"],
            "blob_refs": [policy_item, {"id": decision_blob.sha256, "sha256": decision_blob.sha256}],
        }

    async def load_pages(self, *, spec, identity, route_decision):
        self.load_calls += 1
        ResearchSpecV1.from_json(spec)
        assert route_decision["run_id"] == identity.run_id
        return {"page_result_refs": [], "blob_refs": []}


class _Semantic:
    def __init__(self, blobs: RegisteredBlobStore):
        self.blobs = blobs
        self.extract_calls = 0
        self.inference_calls = 0
        self.refs = {}
        self.items = []

    async def _ensure(self, identity):
        if self.refs:
            return
        policies = {
            "extraction": {"schema_version": 1, "policy_id": "test-extraction-v1"},
            "llm_extract": {"schema_version": 1, "policy_id": "test-llm-extract-v1"},
            "llm_repair": {"schema_version": 1, "policy_id": "test-llm-repair-v1"},
            "llm_inference": {"schema_version": 1, "policy_id": "test-llm-inference-v1"},
            "inference": INFERENCE_POLICY_V1,
        }
        for key, policy in policies.items():
            if key == "inference":
                import hashlib
                digest = hashlib.sha256(canonical_json(policy).encode("utf-8")).hexdigest()
                self.refs[key] = f"sha256:{digest}"
                continue
            ref, item = await _put_policy(self.blobs, identity, policy)
            self.refs[key] = ref
            self.items.append(item)

    async def produce_candidate_slots(
        self, *, spec, route_decision, page_slots, identity
    ):
        self.extract_calls += 1
        await self._ensure(identity)
        assert page_slots == []
        return {
            "candidate_slots": [],
            "policy_refs": {
                key: self.refs[key]
                for key in ("extraction", "llm_extract", "llm_repair", "llm_inference")
            },
            "blob_refs": list(self.items),
        }

    async def synthesize_inference_slots(
        self, *, spec, route_decision, evidence_head_hash, admitted_fact_refs, identity
    ):
        self.inference_calls += 1
        await self._ensure(identity)
        assert admitted_fact_refs == []
        return {
            "inference_slots": [],
            "policy_refs": {
                "llm_inference": self.refs["llm_inference"],
                "inference": self.refs["inference"],
            },
            "blob_refs": list(self.items),
        }


def test_v6_definition_is_exact_eleven_node_graph() -> None:
    expected = (
        "compile_spec", "plan_route", "load_pages", "extract_candidate_bundles",
        "admit_facts", "synthesize_inferences", "register_inferences",
        "assess_answer", "render_claims", "integrity", "persist_manifest",
    )
    assert NODE_IDS == expected
    assert tuple(node.node_id for node in DEEP_RESEARCH_V6_DEFINITION.nodes) == expected
    assert [(edge.sources[0], edge.target) for edge in DEEP_RESEARCH_V6_DEFINITION.edges] == [
        *list(zip(expected, expected[1:])),
        ("persist_manifest", "__end__"),
    ]
    assert DEEP_RESEARCH_V6_DEFINITION.prompt_manifest["intent_types"] == [
        "official_exact_fact", "comparison", "top_n", "policy", "open_research",
    ]
    assert DEEP_RESEARCH_V6_DEFINITION.prompt_manifest["llm_roles"] == [
        "evidence_candidate_extract", "evidence_inference_synthesize",
        "evidence_structured_repair",
    ]


def test_v6_initial_state_has_no_prefetched_or_compiler_candidate_parameters() -> None:
    signature = inspect.signature(initial_state)
    assert "fetched_pages" not in signature.parameters
    assert "compiler_candidates" not in signature.parameters
    with pytest.raises(TypeError):
        initial_state(topic="test", run_id="run", fetched_pages=[])  # type: ignore[call-arg]
    state = initial_state(topic="test", run_id="run")
    assert "fetched_page_refs" not in state["values"]
    assert "compiler_candidates" not in state["values"]


def test_admission_validator_preserves_frozen_first_failure_order() -> None:
    import copy
    import hashlib

    body = "2024年末全国人口140828万人。".encode("utf-8")
    spec = compile_research_spec(
        "What was China's year-end total population in 2024?"
    )
    requirement = spec.requirements[0]
    group_id = "wg-test"
    page_id = "page-test"

    def candidate() -> EvidenceCandidateV1:
        return EvidenceCandidateV1.create(
            body_bytes=body,
            candidate_kind="scalar",
            work_group_id=group_id,
            logical_page_id=page_id,
            page_plan_ordinal=0,
            candidate_ordinal=0,
            requirement_id=requirement["requirement_id"],
            span_start_byte=0,
            span_end_byte=len(body),
            normalized_proposition="population.total.year_end=1408280000 person",
            payload={
                "item_or_cell_id": None,
                "value": 1_408_280_000,
                "canonical_unit": requirement["value_schema"]["canonical_unit"]["unit_id"],
                "time_scope": copy.deepcopy(requirement["time_scope"]),
                "scope": copy.deepcopy(requirement["scope"]),
                "definition": requirement["value_schema"]["definition"],
            },
        )

    def rehash(item: EvidenceCandidateV1) -> None:
        raw = item.to_json()
        raw.pop("candidate_id")
        object.__setattr__(
            item,
            "candidate_id",
            "ecd_" + hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest()[:24],
        )

    def validate(item: object, **overrides):
        values = {
            "requirement": requirement,
            "body_bytes": body,
            "work_group_id": group_id,
            "logical_page_id": page_id,
            "page_plan_ordinal": 0,
            "source_family_id": "cn.nbs",
            "source_tier": "first_party",
        }
        values.update(overrides)
        return validate_candidate_admission(item, **values)

    assert validate(candidate()) is None
    assert validate({}) == "candidate_shape_invalid"

    item = candidate()
    object.__setattr__(item, "candidate_id", "ecd_wrong")
    assert validate(item, requirement=None) == "candidate_id_mismatch"
    assert validate(candidate(), requirement=None) == "requirement_missing"
    assert validate(candidate(), requirement={**requirement, "kind": "matrix"}) == "requirement_kind_mismatch"

    item = candidate()
    object.__setattr__(item, "span_end_byte", len(body) + 1)
    rehash(item)
    assert validate(item, source_tier="general") == "span_out_of_bounds"

    item = candidate()
    object.__setattr__(item, "excerpt_hash", "0" * 64)
    rehash(item)
    assert validate(item, source_tier="general") == "excerpt_hash_mismatch"
    required_source = copy.deepcopy(requirement)
    required_source["source_constraint"]["first_party"] = "required"
    assert validate(
        candidate(), requirement=required_source, source_tier="general"
    ) == "source_policy_rejected"

    item = candidate()
    item.payload["item_or_cell_id"] = "not-null"
    item.payload["canonical_unit"] = "wrong"
    rehash(item)
    assert validate(item) == "payload_semantics_invalid"

    item = candidate()
    item.payload["canonical_unit"] = "wrong"
    item.payload["time_scope"] = None
    item.payload["definition"] = "wrong"
    rehash(item)
    assert validate(item) == "unit_mismatch"

    item = candidate()
    item.payload["time_scope"] = None
    item.payload["definition"] = "wrong"
    rehash(item)
    assert validate(item) == "time_scope_mismatch"

    item = candidate()
    item.payload["definition"] = "wrong"
    rehash(item)
    assert validate(item) == "definition_mismatch"
    assert validate(candidate(), prior_semantic_payload=candidate().payload) == "duplicate_candidate"
    assert validate(candidate(), prior_semantic_payload={"different": True}) == "conflicting_candidate"


@pytest.mark.asyncio
async def test_compile_rejects_injected_upstream_outputs_before_any_port_call(
    tmp_path: Path,
) -> None:
    blobs = RegisteredBlobStore(tmp_path / "blobs", tmp_path / "workflow.db")
    for key in (
        "fetched_page_refs", "page_result_refs", "compiler_candidates",
        "candidate_slot_results", "fact_batch_refs", "inference_slot_results",
    ):
        state = initial_state(topic="研究测试主题", run_id=f"run-{key}")
        state["values"][key] = ["injected"]
        with pytest.raises(ValueError, match=key):
            await compile_spec_handler(
                state,
                WorkflowContext(
                    ports={"blob": blobs},
                    identity=_identity(state["run_id"], "compile_spec"),
                ),
            )


@pytest.mark.asyncio
async def test_nodes_one_to_eight_use_refs_and_two_distinct_evidence_heads(
    tmp_path: Path,
) -> None:
    run_id = "run-production-empty-evidence"
    blobs = RegisteredBlobStore(tmp_path / "blobs", tmp_path / "workflow.db")
    retrieval = _Retrieval(blobs)
    semantic = _Semantic(blobs)
    state = initial_state(topic="研究一个没有测试页面的开放问题", run_id=run_id)
    handlers = (
        ("compile_spec", compile_spec_handler),
        ("plan_route", plan_route_handler),
        ("load_pages", load_pages_handler),
        ("extract_candidate_bundles", extract_candidate_bundles_handler),
        ("admit_facts", admit_facts_handler),
        ("synthesize_inferences", synthesize_inferences_handler),
        ("register_inferences", register_inferences_handler),
        ("assess_answer", assess_answer_handler),
    )
    facts_head = None
    for node_id, handler in handlers:
        patch = await handler(
            state,
            WorkflowContext(
                ports={"blob": blobs, "retrieval": retrieval, "semantic": semantic},
                identity=_identity(run_id, node_id),
            ),
        )
        state.update(patch.to_dict())
        if node_id == "admit_facts":
            facts_head = state["values"]["evidence_head_hash"]
            assert len(state["values"]["fact_batch_refs"]) == 1
        if node_id == "register_inferences":
            assert len(state["values"]["fact_batch_refs"]) == 2
            assert state["values"]["evidence_head_hash"] != facts_head
    assert retrieval.plan_calls == retrieval.load_calls == 1
    assert semantic.extract_calls == semantic.inference_calls == 1
    assert state["values"]["assessment_ref"].startswith("sha256:")
    assert state["values"]["assessment_input_hash"]
    assert state["values"]["stage"] == "answer_assessed"
    assert set(state["values"]["policy_refs"]) == {
        "compiler", "route", "extraction", "llm_extract", "llm_repair",
        "llm_inference", "admission", "inference", "assessment", "claim", "quality",
    }
    assert ADMISSION_POLICY_V1["policy_id"] == "deep-research-v6-admission-v1"
    assert ASSESSMENT_POLICY_V1["policy_id"] == "deep-research-v6-assessment-v1"
