from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
from pathlib import Path
import time

import aiosqlite
import pytest

from deskpet.workflows import WorkflowContext
from deskpet.workflows.adapters.deep_research_v6_semantic_runtime import (
    DeepResearchV6SemanticRuntime,
)
from deskpet.workflows.adapters.deep_research_v6_retrieval_runtime import (
    DurableV6DeadlinePort,
    DurableV6PageReadEffectAdapter,
)
from deskpet.workflows.adapters.research_runtime import (
    BoundResearchEffectContext,
    DurableResearchCallEffectAdapter,
    DurableV6ResearchLLMStagePort,
    V6_RESEARCH_RESPONSE_FORMATS,
    WorkflowControlSignalHub,
    build_v6_research_llm_profiles,
    research_response_format_hash,
)
from deskpet.workflows.contracts import canonical_json
from deskpet.workflows.definitions.deep_research_v5_contracts import ResearchLLMResult
from deskpet.workflows.definitions.deep_research_v6_contracts import (
    ResearchSpecV1,
    build_route_decision_from_spec,
)
from deskpet.workflows.definitions.deep_research_v6_assessment import (
    derive_collection_item_id,
    derive_matrix_cell_id,
)
from deskpet.workflows.definitions.deep_research_v6_evidence_contracts import (
    EvidenceCandidateBundleV1,
    EvidenceCandidateV1,
    InferenceProposalBundleV1,
    InferenceProposalV1,
    ResearchLLMEffectOutcomeV1,
    V6FetchedPageRefPayloadV1,
)
from deskpet.workflows.definitions.deep_research_v6_retrieval_contracts import (
    OfficialSearchResultV1,
    PageExtractionResultV1,
    SearchCandidateV1,
    SourceLocatorV1,
)
from deskpet.workflows.definitions.deep_research_v6_evidence import EvidenceFactBatchV1
from deskpet.workflows.definitions.research_core import ResearchLLMPortV2
from deskpet.workflows.definitions.v6 import register_v6_workflows
from deskpet.workflows.definitions.v6.deep_research import initial_state
from deskpet.workflows.effects import EffectExecutionContext, EffectJournal
from deskpet.workflows.native import NativeExecutionPolicy
from deskpet.workflows.runner import WorkflowRegistry, WorkflowRunner
from deskpet.workflows.store import (
    NativeCheckpointStore,
    RegisteredBlobStore,
    RunFence,
    WorkflowRunStore,
)


TOPICS = {
    "official_exact_fact": "What were China's year-end total population and annual births in 2024?",
    "comparison": "Compare Alpha versus Beta on cost, reliability, and ecosystem differences",
    "top_n": "List the Top 3 AI products most worth attention",
    "policy": "Research policy direction and potential impact",
    "open_research": "Research the current state, conclusions, limitations, and uncertainty of example technology",
}
BODY = (
    "2024年末全国人口140828万人。2024年全年出生人口954万人。"
    "This source also documents "
    "the comparison, ranking, policy impact, conclusion, limitation, and uncertainty."
)
URL = "https://example.test/deep-research-v6"


def _blob_items(refs):
    return [
        {"id": ref.removeprefix("sha256:"), "sha256": ref.removeprefix("sha256:")}
        for ref in sorted(set(refs))
    ]


async def _put_json(blobs, identity, value, media_type="application/json"):
    ref = await blobs.put(canonical_json(value).encode(), identity, media_type=media_type)
    return f"sha256:{ref.sha256}"


class _FailOnCallStage:
    def __init__(self, profile):
        self.profile = profile
        self.calls = 0

    async def execute(self, **_kwargs):
        self.calls += 1
        raise AssertionError("exact fact must not call an LLM stage")


class _StructuredProvider:
    def __init__(self, role, profile_ref):
        self.role = role
        self.profile_ref = profile_ref
        self.calls = 0

    async def __call__(self, prompt, *, max_output_tokens, stable_call_id, response_format=None):
        self.calls += 1
        inner = json.loads(prompt)
        payload = inner["input"]
        if self.role == "evidence_candidate_extract":
            group = payload["work_group"]
            requirement_id = group["requirement_ids"][0]
            requirement = next(
                item for item in payload["spec"]["requirements"]
                if item["requirement_id"] == requirement_id
            )
            body = payload["body"].encode("utf-8")
            if requirement["kind"] == "matrix":
                candidate_kind = "matrix_cell"
                member_lists = [
                    [member["member_id"] for member in axis["members"]]
                    for axis in requirement["axes"]
                ]
                candidate_payloads = [{
                    "cell_id": derive_matrix_cell_id(requirement_id, members),
                    "axis_member_ids": list(members),
                    "field_key": "value",
                    "value": f"documented comparison {index}",
                    "canonical_unit": None,
                    "time_scope": requirement["time_scope"],
                    "scope": requirement["scope"],
                    "definition": "documented comparison cell",
                } for index, members in enumerate(itertools.product(*member_lists))]
            elif requirement["kind"] == "collection":
                candidate_kind = "collection_field"
                candidate_payloads = []
                field_values = {
                    "product_name": lambda name, rank: name,
                    "attention_score": lambda name, rank: 100 - rank,
                    "advantages": lambda name, rank: [f"{name} advantage"],
                    "disadvantages": lambda name, rank: [f"{name} limitation"],
                }
                for rank in range(3):
                    name = f"Example Product {rank + 1}"
                    unique_values = [name]
                    for field in requirement["item_schema"]["fields"]:
                        candidate_payloads.append({
                            "item_id": derive_collection_item_id(requirement_id, unique_values),
                            "unique_key_values": unique_values,
                            "field_key": field["field_key"],
                            "value": field_values[field["field_key"]](name, rank),
                            "canonical_unit": None,
                            "as_of": requirement["selection"]["as_of"],
                            "rank_inputs": [{
                                "field_key": "attention_score",
                                "value": 100 - rank,
                                "missing": False,
                            }],
                        })
            else:
                candidate_kind = "claim_fact"
                fact_facets = [
                    facet for facet in requirement["topic_facets"]
                    if facet["facet_id"] != "impact"
                ]
                candidate_payloads = [{
                    "claim_instance_id": f"claim-{payload['spec']['intent_type']}-{facet['facet_id']}",
                    "facet_key": facet["facet_id"],
                    "value": "documented source fact",
                    "canonical_unit": None,
                    "time_scope": requirement["time_scope"],
                    "scope": requirement["scope"],
                    "definition": "documented source claim",
                } for facet in fact_facets]
            candidates = tuple(
                EvidenceCandidateV1.create(
                    body_bytes=body,
                    candidate_kind=candidate_kind,
                    work_group_id=group["work_group_id"],
                    logical_page_id=payload["logical_page_id"],
                    page_plan_ordinal=payload["page_plan_ordinal"],
                    candidate_ordinal=ordinal,
                    requirement_id=requirement_id,
                    span_start_byte=0,
                    span_end_byte=len(body),
                    normalized_proposition=f"{requirement['key']}: documented source fact {ordinal}",
                    payload=candidate_payload,
                )
                for ordinal, candidate_payload in enumerate(candidate_payloads)
            )
            value = EvidenceCandidateBundleV1.create(
                run_id=payload["route_decision"]["run_id"],
                spec_hash=payload["spec"]["spec_hash"],
                work_group_id=group["work_group_id"],
                logical_page_id=payload["logical_page_id"],
                page_plan_ordinal=payload["page_plan_ordinal"],
                page_result_ref=payload["page_result_ref"],
                route_decision_ref=payload["route_decision_ref"],
                route_policy_ref=payload["route_policy_ref"],
                extraction_policy_ref=payload["extraction_policy_ref"],
                repair_round=0,
                candidates=candidates,
                bundle_reason_codes=(),
            ).to_json()
        elif self.role == "evidence_inference_synthesize":
            group = payload["work_group"]
            facts = payload["admitted_facts"]
            assert facts
            inference_kinds = {
                "comparison": "comparison",
                "top_n": "preference",
                "policy": "impact",
            }
            intent_type = payload["spec"]["intent_type"]
            requested_kinds = (
                ("conclusion", "limitation", "counterevidence", "uncertainty")
                if intent_type == "open_research"
                else (inference_kinds[intent_type],)
            )
            proposals = []
            for inference_kind in requested_kinds:
                matching = next(
                    (
                        item for item in facts
                        if inference_kind
                        in item["fact"]["semantic_payload"].get("facet_ids", ())
                    ),
                    facts[0],
                )
                premise_ref = matching["fact_ref"]
                fact = matching["fact"]
                proposals.append(InferenceProposalV1.create(
                    requirement_id=fact["requirement_id"],
                    inference_kind=inference_kind,
                    item_or_cell_id=(
                        f"{fact['item_or_cell_id']}-{inference_kind}-inference"
                        if fact["target_kind"] == "claim_fact"
                        else fact["item_or_cell_id"]
                    ),
                    facet_ids=(
                        ("impact",)
                        if intent_type == "policy"
                        else (inference_kind,)
                    ),
                    normalized_proposition=f"registered {inference_kind} inference",
                    premise_fact_refs=(premise_ref,),
                    model_id="test-v6-model",
                    model_policy_ref=self.profile_ref,
                ))
            value = InferenceProposalBundleV1.create(
                run_id=payload["route_decision"]["run_id"],
                spec_hash=payload["spec"]["spec_hash"],
                work_group_id=group["work_group_id"],
                input_evidence_head_hash=payload["input_evidence_head_hash"],
                ordinal=payload["ordinal"],
                profile_ref=self.profile_ref,
                proposals=tuple(proposals),
            ).to_json()
        else:
            raise AssertionError("valid provider output must not invoke repair")
        return ResearchLLMResult(
            canonical_json(value), "test-v6-model", 20, 10, 0,
            "provider", stable_call_id,
        )


class _CrashCheckpointStore(NativeCheckpointStore):
    def __init__(self, path, *, task_node=None, frontier_stage=None):
        super().__init__(path)
        self.task_node = task_node
        self.frontier_stage = frontier_stage
        self.crashed = False

    async def commit_task_result(self, **kwargs):
        result = await super().commit_task_result(**kwargs)
        task = kwargs["task"]
        if not self.crashed and task.node_id == self.task_node:
            self.crashed = True
            raise asyncio.CancelledError()
        return result

    async def commit_frontier(self, **kwargs):
        result = await super().commit_frontier(**kwargs)
        values = kwargs["state"].get("values", {})
        if (
            not self.crashed
            and isinstance(values, dict)
            and values.get("stage") == self.frontier_stage
        ):
            self.crashed = True
            raise asyncio.CancelledError()
        return result


async def _runtime(
    tmp_path: Path,
    intent: str,
    *,
    run_id: str | None = None,
    providers=None,
    clock=time.time,
    owner: str | None = None,
    crash_task_node: str | None = None,
    crash_frontier_stage: str | None = None,
    crash_after_raw_result: bool = False,
    crash_llm_stage: str | None = None,
    force_llm_deadline: bool = False,
):
    database = tmp_path / "workflow.db"
    store = WorkflowRunStore(database, clock=clock)
    saver = _CrashCheckpointStore(
        database,
        task_node=crash_task_node,
        frontier_stage=crash_frontier_stage,
    )
    registry = WorkflowRegistry()
    register_v6_workflows(registry)
    runner = WorkflowRunner(
        store,
        saver,
        registry,
        owner=owner or f"runner-{intent}",
        heartbeat_interval=1.0,
        lease_ttl=10.0,
        clock=clock,
    )
    capability = {
        "research_io_budget": {
            "max_input_tokens": 100,
            "max_output_tokens": 0,
            "max_cost_micros": 0,
        },
        "research_llm_budget": {
            "max_input_tokens": 100_000,
            "max_output_tokens": 100_000,
            "max_cost_micros": 10_000_000,
        },
    }
    if run_id is None:
        run_id = await runner.start(
            session_id=f"session-{intent}", request_id=f"request-{intent}",
            turn_id=f"turn-{intent}", workflow_name="deep_research",
            workflow_version="v6", capability_snapshot=capability,
        )
        await store.bind_session_refs(
            run_id, (("delivery", f"session-{intent}", 0),)
        )
    blobs = RegisteredBlobStore(tmp_path / "blobs", database)
    journal = EffectJournal(database)
    signals = WorkflowControlSignalHub(poll_interval=0.005)

    async def resolve(identity):
        row = await store.get_run(identity.run_id)
        assert row is not None and row["status"] == "running"
        return BoundResearchEffectContext(
            identity,
            EffectExecutionContext(
                journal=journal,
                fence=RunFence(
                    identity.run_id, str(row["lease_owner"]), int(row["lease_epoch"]),
                    int(row["run_version"]),
                ),
                node_execution_id=f"test-{identity.checkpoint_id}-{identity.task_id}",
                workflow_name="deep_research", workflow_version="v6",
                node_id=identity.node_id,
            ),
        )

    route_policy = {"schema_version": 1, "policy_id": "runner-route-v1"}

    async def route_transport(payload, identity):
        spec = ResearchSpecV1.from_json(payload["spec"])
        policy_ref = await _put_json(
            blobs, identity, route_policy,
            "application/vnd.deskpet.deepresearch-v6-route-policy+json",
        )
        decision = build_route_decision_from_spec(
            spec, run_id=identity.run_id, policy_hash=policy_ref[7:],
            capability_snapshot_hash="c" * 64,
            budget={
                "query": 4,
                "fetch": 4,
                "browser": 0,
                "llm": 0 if spec.intent_type == "official_exact_fact" else 32,
                "lane": 8,
            },
        )
        decision_ref = await _put_json(blobs, identity, decision.to_json())
        await deadline_port.prepare_route(
            identity=identity,
            route_id=str(decision.to_json()["route_id"]),
            policy_hash=policy_ref[7:],
            budgets=dict(decision.to_json()["budget"]),
        )
        return {
            "route_policy_ref": policy_ref, "route_policy_hash": policy_ref[7:],
            "route_decision_ref": decision_ref,
            "route_id": decision.to_json()["route_id"],
            "blob_refs": _blob_items((policy_ref, decision_ref)),
        }

    async def pages_transport(_payload, identity):
        body = BODY.encode()
        body_blob = await blobs.put(body, identity, media_type="text/plain; charset=utf-8")
        url_hash = hashlib.sha256(URL.encode()).hexdigest()
        authority_id = "cn.nbs" if intent == "official_exact_fact" else "test.authority"
        locator_base = {
            "schema_version": 1, "canonical_url": URL, "final_url": URL,
            "canonical_url_hash": url_hash, "final_url_hash": url_hash,
            "authority_id": authority_id, "verification_status": "verified",
            "verification_policy_hash": "f" * 64, "redirect_chain_hashes": [],
        }
        locator = {
            **locator_base,
            "locator_id": "locator_" + hashlib.sha256(canonical_json(locator_base).encode()).hexdigest()[:24],
        }
        locator_ref = await _put_json(blobs, identity, locator)
        page_id = "page_" + hashlib.sha256((url_hash + body_blob.sha256).encode()).hexdigest()[:24]
        page = V6FetchedPageRefPayloadV1(
            page_id=page_id, ordinal=0, source_locator_ref=locator_ref,
            body_ref=f"sha256:{body_blob.sha256}", body_hash=body_blob.sha256,
            canonical_url_hash=url_hash, final_url_hash=url_hash,
            authority_id=authority_id, title_hash=hashlib.sha256(b"title").hexdigest(),
            media_type="text/plain; charset=utf-8", admission_status="admitted",
            reason_codes=("test_page",),
        )
        page_ref = await _put_json(blobs, identity, page.to_json())
        return {
            "page_result_refs": [page_ref],
            "blob_refs": _blob_items((page_ref, locator_ref, f"sha256:{body_blob.sha256}")),
        }

    deadline_port = DurableV6DeadlinePort(
        journal=journal,
        resolve_effect_context=resolve,
    )
    read_effects = DurableV6PageReadEffectAdapter(
        journal=journal,
        blobs=blobs,
        resolve_effect_context=resolve,
        deadline_port=deadline_port,
    )

    class Retrieval:
        async def plan_route(self, *, spec, identity):
            return await route_transport({"spec": spec}, identity)

        async def load_pages(self, *, spec, identity, route_decision):
            route_id = str(route_decision["route_id"])
            policy_hash = str(route_decision["policy_hash"])
            budget = route_decision["budget"]
            root, _ = await deadline_port.resume_root(
                identity=identity,
                route_id=route_id,
                policy_hash=policy_hash,
                budget_ms=120_000,
            )

            url_hash = hashlib.sha256(URL.encode()).hexdigest()
            authority_id = "cn.nbs" if intent == "official_exact_fact" else "test.authority"
            search_locator = SourceLocatorV1.create(
                canonical_url=URL, final_url=URL, canonical_url_hash=url_hash,
                final_url_hash=url_hash, authority_id=authority_id,
                verification_status="unverified", verification_policy_hash="f" * 64,
                redirect_chain_hashes=(),
            )
            search_locator_ref = await _put_json(
                blobs, identity, search_locator.to_json(),
                "application/vnd.deskpet.source-locator.v1+json",
            )
            search_candidate = SearchCandidateV1.create(
                ordinal=0, source_locator_ref=search_locator_ref,
                title_hash=hashlib.sha256(b"title").hexdigest(),
                snippet_hash=hashlib.sha256(b"").hexdigest(),
                authority_match="unverified", reason_codes=("candidate_unverified",),
            )

            async def search_transport(attempt):
                return OfficialSearchResultV1.create(
                    logical_effect_id=attempt.logical_effect_id,
                    attempt_no=attempt.attempt_no, request_id="runner-search-0",
                    query_hash=hashlib.sha256(b"runner query").hexdigest(),
                    target_id="runner-target-0", candidates=(search_candidate,),
                    outcome="succeeded", error_code=None, deadline_id=attempt.deadline_id,
                )

            await read_effects.execute(
                operation_kind="official_search",
                route_id=route_id,
                target_or_page_id="runner-target-0",
                ordinal=0,
                resource_kind="query",
                resource_hard_limit=int(budget["query"]),
                resource_policy_hash=policy_hash,
                deadline=root,
                identity=identity,
                transport=search_transport,
            )
            child, _ = await deadline_port.resume_child(
                identity=identity,
                parent=root,
                logical_key="runner-page-0",
                logical_scope="deep_research_v6.page_fetch",
                budget_ms=20_000,
                policy_hash=policy_hash,
            )

            page_output = None

            async def page_transport(attempt):
                nonlocal page_output
                page_output = await pages_transport(
                    {"spec": spec, "route_decision": route_decision}, identity
                )
                raw_page = await blobs.get(str(page_output["page_result_refs"][0])[7:])
                page = V6FetchedPageRefPayloadV1.from_json(json.loads(raw_page.decode()))
                return PageExtractionResultV1.create(
                    logical_page_id="runner-page-0",
                    logical_effect_id=attempt.logical_effect_id,
                    attempt_no=attempt.attempt_no,
                    source_locator_ref=page.source_locator_ref,
                    page_record={
                        "schema_version": 1, "page_id": page.page_id,
                        "source_locator_ref": page.source_locator_ref,
                        "canonical_url_hash": page.canonical_url_hash,
                        "final_url_hash": page.final_url_hash,
                        "authority_id": page.authority_id,
                        "source_family_id": page.authority_id,
                        "source_tier": "first_party" if intent == "official_exact_fact" else "general",
                        "body_ref": page.body_ref, "body_hash": page.body_hash,
                        "fetched_at": "2026-07-18T00:00:00Z",
                        "media_type": page.media_type, "admission_status": "admitted",
                        "reason_codes": list(page.reason_codes),
                    },
                    spans=(), bindings=(), outcome="succeeded", error_code=None,
                    deadline_id=attempt.deadline_id, control_command_id=None,
                )

            result = await read_effects.execute(
                operation_kind="page_fetch",
                route_id=route_id,
                target_or_page_id="runner-page-0",
                ordinal=0,
                resource_kind="fetch",
                resource_hard_limit=int(budget["fetch"]),
                resource_policy_hash=policy_hash,
                deadline=child,
                identity=identity,
                transport=page_transport,
                input_source_locator_ref=search_locator_ref,
            )
            assert PageExtractionResultV1.from_json(result).outcome == "succeeded"
            assert page_output is not None
            typed = PageExtractionResultV1.from_json(result)
            canonical_ref = "sha256:" + hashlib.sha256(
                canonical_json(typed.to_json()).encode()
            ).hexdigest()
            async with aiosqlite.connect(database) as db:
                effect_outcome = await (await db.execute(
                    """SELECT outcome_json FROM workflow_effects
                    WHERE effect_type='deep_research_v6_page_fetch' AND status='committed'
                    ORDER BY ended_at DESC LIMIT 1"""
                )).fetchone()
            assert effect_outcome is not None
            assert json.loads(effect_outcome[0])["canonical_result_ref"] == canonical_ref
            return {
                "page_result_refs": [canonical_ref],
                "blob_refs": _blob_items((
                    canonical_ref, search_locator_ref, *typed.dependency_refs()
                )),
            }

    profiles = build_v6_research_llm_profiles({
        role: research_response_format_hash(value)
        for role, value in V6_RESEARCH_RESPONSE_FORMATS.items()
    })
    providers = providers if providers is not None else {}
    stages = {}
    if intent == "official_exact_fact":
        stages = {role: _FailOnCallStage(profile) for role, profile in profiles.items()}
    else:
        raw_result_fault_fired = False

        async def raw_result_fault(stage_name):
            nonlocal raw_result_fault_fired
            if (
                stage_name == (
                    crash_llm_stage
                    or "research_llm.after_result_blob_before_effect_commit"
                )
                and not raw_result_fault_fired
            ):
                raw_result_fault_fired = True
                raise asyncio.CancelledError()

        for role, profile in profiles.items():
            provider = providers.get(role)
            if provider is None:
                provider = _StructuredProvider(role, profile.profile_ref)
                providers[role] = provider
            effect = DurableResearchCallEffectAdapter(
                journal=journal, blobs=blobs, llm=ResearchLLMPortV2(provider),
                resolve_effect_context=resolve,
                reserve_cost_micros=lambda _role, _input, output: output,
                actual_cost_micros=lambda result: (result.input_tokens or 0) + (result.output_tokens or 0),
                control_signals=signals, profile=profile,
                response_format=V6_RESEARCH_RESPONSE_FORMATS[role],
                deadline_observer=(
                    (lambda _identity: False)
                    if force_llm_deadline
                    else deadline_port.allow_dispatch
                ),
                route_resource_budget_kind="llm",
                fault_injector=(
                    raw_result_fault
                    if (crash_after_raw_result or crash_llm_stage is not None)
                    and role == "evidence_candidate_extract"
                    else None
                ),
            )
            stages[role] = DurableV6ResearchLLMStagePort(blobs=blobs, effect=effect)
    semantic = DeepResearchV6SemanticRuntime(
        blobs=blobs,
        llm_extract=stages["evidence_candidate_extract"],
        llm_inference=stages["evidence_inference_synthesize"],
        llm_repair=stages["evidence_structured_repair"],
    )
    context = WorkflowContext(
        ports={
            "blob": blobs, "retrieval": Retrieval(), "semantic": semantic,
            "llm_extract": stages["evidence_candidate_extract"],
            "llm_inference": stages["evidence_inference_synthesize"],
            "llm_repair": stages["evidence_structured_repair"],
            "deadline": deadline_port,
            "native_execution_policy": NativeExecutionPolicy(max_parallel_tasks=3),
        }
    )
    return database, store, runner, run_id, context, stages, providers, blobs


@pytest.mark.asyncio
async def test_real_v6_port_registers_semantic_deadline_outcome_without_provider_call(tmp_path):
    database, _, runner, run_id, context, _, providers, blobs = await _runtime(
        tmp_path, "comparison", force_llm_deadline=True
    )
    result = await runner.run(
        run_id,
        initial_state(
            topic=TOPICS["comparison"], run_id=run_id,
            session_id="session-deadline",
        ),
        context,
    )
    assert result.error is None, await runner.history(run_id)
    assert sum(provider.calls for provider in providers.values()) == 0
    async with aiosqlite.connect(database) as db:
        rows = await (await db.execute(
            """SELECT sha256 FROM workflow_blobs
            WHERE media_type='application/vnd.deskpet.deepresearch-v6-llm-effect-outcome+json'"""
        )).fetchall()
        effect_rows = await (await db.execute(
            "SELECT status,outcome_json FROM workflow_effects "
            "WHERE run_id=? AND effect_type='research_llm'", (run_id,),
        )).fetchall()
    assert rows and effect_rows
    semantic_outcomes = [
        ResearchLLMEffectOutcomeV1.from_json(
            json.loads((await blobs.get(row[0])).decode())
        )
        for row in rows
    ]
    assert all(outcome.status == "deadline" for outcome in semantic_outcomes)
    assert all(outcome.raw_result_ref is None for outcome in semantic_outcomes)
    assert all(status == "failed" for status, _ in effect_rows)
    assert all(json.loads(raw)["error"]["code"] == "deadline" for _, raw in effect_rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", list(TOPICS))
async def test_real_runner_five_intents_use_durable_effects_and_eleven_nodes(tmp_path, intent):
    database, store, runner, run_id, context, stages, providers, blobs = await _runtime(tmp_path, intent)
    state = initial_state(topic=TOPICS[intent], run_id=run_id, session_id=f"session-{intent}")
    result = await runner.run(run_id, state, context)
    assert result.error is None, await runner.history(run_id)
    output = result.output
    assert output["values"]["terminal_manifest_ref"].startswith("sha256:")
    async with aiosqlite.connect(database) as db:
        node_ids = [row[0] for row in await (await db.execute(
            "SELECT node_id FROM workflow_nodes WHERE run_id=? AND latest_status='succeeded'",
            (run_id,),
        )).fetchall()]
        checkpoint_count = (await (await db.execute(
            "SELECT COUNT(*) FROM workflow_checkpoints WHERE run_id=?", (run_id,)
        )).fetchone())[0]
    assert set((
        "compile_spec", "plan_route", "load_pages", "extract_candidate_bundles",
        "admit_facts", "synthesize_inferences", "register_inferences", "assess_answer",
        "render_claims", "integrity", "persist_manifest",
    )).issubset(set(node_ids))
    assert checkpoint_count >= 12
    async with aiosqlite.connect(database) as db:
        effects = (await (await db.execute(
            "SELECT COUNT(*) FROM workflow_effects WHERE run_id=?", (run_id,)
        )).fetchone())[0]
        llm_effects = (await (await db.execute(
            "SELECT COUNT(*) FROM workflow_effects WHERE run_id=? AND effect_type='research_llm'",
            (run_id,),
        )).fetchone())[0]
        llm_reservations = (await (await db.execute(
            "SELECT COUNT(*) FROM workflow_effect_budget_reservations WHERE run_id=? AND ledger_kind='research_llm_budget'",
            (run_id,),
        )).fetchone())[0]
        read_rows = await (await db.execute(
            """SELECT logical_effect_id,effect_id,attempt_no,status,prepared_json
            FROM workflow_effects WHERE run_id=? AND logical_effect_id IS NOT NULL""",
            (run_id,),
        )).fetchall()
        route_budgets = await (await db.execute(
            """SELECT resource_kind,hard_limit,reserved,consumed
            FROM workflow_research_resource_budgets
            WHERE run_id=? ORDER BY resource_kind""",
            (run_id,),
        )).fetchall()
    assert effects >= 2
    assert len(read_rows) == 2
    assert {json.loads(row[4])["final_params"]["operation_kind"] for row in read_rows} == {
        "official_search", "page_fetch"
    }
    for logical, effect_id, attempt_no, status, prepared_json in read_rows:
        prepared = json.loads(prepared_json)["final_params"]
        expected = hashlib.sha256(
            (
                f"{run_id}|{prepared['route_id']}|{prepared['operation_kind']}|"
                f"{prepared['target_or_page_id']}|{prepared['ordinal']}"
            ).encode()
        ).hexdigest()
        assert logical == expected
        assert effect_id == hashlib.sha256(f"{run_id}|{logical}|1".encode()).hexdigest()
        assert (attempt_no, status) == (1, "committed")
    assert {kind for kind, _, _, _ in route_budgets} == {
        "query", "fetch", "browser", "llm", "lane"
    }
    llm_route_budget = next(row for row in route_budgets if row[0] == "llm")

    spec = await _load_json_ref(blobs, output["values"]["spec_ref"])
    requirement_ids = {item["requirement_id"] for item in spec["requirements"]}
    fact_refs = []
    inference_refs = []
    for batch_ref in output["values"]["fact_batch_refs"]:
        batch = EvidenceFactBatchV1.from_json(await _load_json_ref(blobs, batch_ref))
        fact_refs.extend(batch.admitted_fact_refs)
        inference_refs.extend(batch.registered_inference_refs)
    assert fact_refs
    facts = [await _load_json_ref(blobs, ref) for ref in fact_refs]
    assert {fact["requirement_id"] for fact in facts}.issubset(requirement_ids)
    binding_ids = {fact["binding_id"] for fact in facts}
    assert binding_ids

    render = await _load_json_ref(blobs, output["values"]["render_bundle_ref"])
    assert render["claims"]
    assert {claim["requirement_id"] for claim in render["claims"]}.issubset(requirement_ids)
    assert any(set(claim["binding_ids"]) & binding_ids for claim in render["claims"])
    final_text = (
        await blobs.get(render["final_assistant_ref"].removeprefix("sha256:"))
    ).decode("utf-8")
    assert URL in final_text

    snapshot_ref = output["values"]["continuation_snapshot_ref"]
    snapshot = await _load_json_ref(blobs, snapshot_ref)
    snapshot_closure = set(snapshot["closure_refs"])
    assert set(fact_refs).issubset(snapshot_closure)
    assert set(inference_refs).issubset(snapshot_closure)
    assert output["values"]["assessment_ref"] in snapshot_closure
    assert output["values"]["claim_batch_ref"] in snapshot_closure
    if intent == "official_exact_fact":
        assert sum(stage.calls for stage in stages.values()) == 0
        assert not inference_refs
        assert llm_effects == 0
        assert llm_reservations == 0
        route_ref = output["values"]["route_decision_ref"]
        route = json.loads((await blobs.get(route_ref.removeprefix("sha256:"))).decode())
        assert route["budget"]["llm"] == 0
        assert llm_route_budget[1:] == (0, 0, 0)
        assert "- 年末总人口：140828 万人（时间：2024年末；范围：全国）" in final_text
        assert "- 全年出生人口：954 万人（时间：2024年全年；范围：全国）" in final_text
        assert f"[国家统计局]({URL})" in final_text
        assert all(
            marker not in final_text
            for marker in (
                "1408280000",
                "9540000",
                "unit=",
                "scope=",
                "definition=",
                "year_end_total_population",
                "births_during_period",
                "cn.nbs",
            )
        )
    else:
        assert inference_refs
        inferences = [await _load_json_ref(blobs, ref) for ref in inference_refs]
        assert all(item["status"] == "registered" for item in inferences)
        assert {item["requirement_id"] for item in inferences}.issubset(requirement_ids)
        assert all(item["premise_binding_ids"] for item in inferences)
        assert providers["evidence_candidate_extract"].calls >= 1
        assert providers["evidence_inference_synthesize"].calls >= 1
        assert providers["evidence_structured_repair"].calls == 0
        assert llm_route_budget[2] == 0
        assert llm_route_budget[3] == llm_effects
        if intent == "open_research":
            semantic_kinds = {
                "conclusion", "limitation", "counterevidence", "uncertainty"
            }
            by_kind = {
                kind: [
                    (ref, item) for ref, item in zip(inference_refs, inferences)
                    if item["inference_kind"] == kind
                ]
                for kind in semantic_kinds
            }
            assert all(by_kind.values())
            fact_by_ref = dict(zip(fact_refs, facts))
            assessment = await _load_json_ref(blobs, output["values"]["assessment_ref"])
            assessed_bindings = {
                binding
                for item in assessment["requirement_results"]
                for binding in item["binding_ids"]
            }
            render_claims = render["claims"]
            claim_batch = await _load_json_ref(
                blobs, output["values"]["claim_batch_ref"]
            )
            quality = await _load_json_ref(
                blobs, output["values"]["quality_audit_ref"]
            )
            assert quality["hard_gate_status"] == "passed"
            assert quality["claim_batch_ref"] == output["values"]["claim_batch_ref"]
            batch_claims = {
                claim["claim_id"]: claim for claim in claim_batch["claims"]
            }
            assert all(
                batch_claims[claim["claim_id"]] == claim for claim in render_claims
            )
            for kind, rows in by_kind.items():
                for inference_ref, inference in rows:
                    assert inference["status"] == "registered"
                    assert inference["facet_ids"] == [kind]
                    assert inference["premise_fact_refs"]
                    assert inference["proposed_premise_fact_refs"] == inference["premise_fact_refs"]
                    premises = [
                        fact_by_ref[premise_ref]
                        for premise_ref in inference["premise_fact_refs"]
                    ]
                    premise_bindings = {fact["binding_id"] for fact in premises}
                    assert premise_bindings == set(inference["premise_binding_ids"])
                    assert premise_bindings.issubset(assessed_bindings)
                    rendered_kind = "inference" if kind == "conclusion" else kind
                    matching_render = [
                        claim for claim in render_claims
                        if claim["claim_kind"] == rendered_kind
                        and claim["inference_ref"] == inference_ref
                    ]
                    assert matching_render
                    assert all(
                        set(claim["binding_ids"]) == premise_bindings
                        and claim["support_status"] == "supported"
                        and claim["visibility"] == "user"
                        for claim in matching_render
                    )
                    assert any(
                        claim["claim_id"] in claim_batch["visible_claim_ids"]
                        for claim in matching_render
                    )
                    assert inference_ref in snapshot_closure
                    assert set(inference["premise_fact_refs"]).issubset(snapshot_closure)
            for kind in semantic_kinds:
                marker = f"registered {kind} inference"
                marker_at = final_text.index(marker)
                assert URL in final_text[marker_at:marker_at + len(marker) + 300]
            assert URL in final_text


RECOVERY_SEAMS = {
    "llm_provider_return": {
        "crash_llm_stage": "research_llm.after_provider_return_before_result_blob"
    },
    "llm_raw_result": {
        "crash_llm_stage": "research_llm.after_result_blob_before_effect_commit"
    },
    "llm_effect_commit": {
        "crash_llm_stage": "research_llm.after_effect_commit_before_return"
    },
    "candidate_bundle": {"crash_task_node": "extract_candidate_bundles"},
    "fact_head": {"crash_frontier_stage": "facts_admitted"},
    "inference_bundle": {"crash_task_node": "synthesize_inferences"},
    "inference_head": {"crash_frontier_stage": "inferences_registered"},
}


async def _load_json_ref(blobs, ref):
    return json.loads((await blobs.get(ref.removeprefix("sha256:"))).decode("utf-8"))


@pytest.mark.asyncio
@pytest.mark.parametrize("seam", list(RECOVERY_SEAMS))
async def test_real_runner_recovers_seven_durable_llm_and_head_crash_seams_without_resend(
    tmp_path, seam
):
    now = [100.0]
    runtime = await _runtime(
        tmp_path,
        "comparison",
        clock=lambda: now[0],
        owner=f"before-crash-{seam}",
        **RECOVERY_SEAMS[seam],
    )
    database, store, runner, run_id, context, _, providers, _ = runtime
    state = initial_state(
        topic=TOPICS["comparison"],
        run_id=run_id,
        session_id="session-comparison",
    )

    with pytest.raises(asyncio.CancelledError):
        await runner.run(run_id, state, context)

    calls_at_crash = {
        role: provider.calls for role, provider in providers.items()
    }
    assert calls_at_crash["evidence_candidate_extract"] >= 1
    assert (calls_at_crash["evidence_inference_synthesize"] > 0) == (
        seam in {"inference_bundle", "inference_head"}
    )
    async with aiosqlite.connect(database) as db:
        effect_rows_at_crash = await (await db.execute(
                "SELECT effect_id,status,outcome_json FROM workflow_effects "
                "WHERE run_id=? AND effect_type='research_llm'",
                (run_id,),
            )).fetchall()
        effects_at_crash = {row[0] for row in effect_rows_at_crash}
        raw_rows_at_crash = await (await db.execute(
            "SELECT sha256,size_bytes FROM workflow_blobs "
            "WHERE media_type='application/vnd.deskpet.research-llm-result+json'",
        )).fetchall()
        extract_node_status_at_crash = await (await db.execute(
            "SELECT latest_status FROM workflow_nodes "
            "WHERE run_id=? AND node_id='extract_candidate_bundles'",
            (run_id,),
        )).fetchone()
    assert len(effects_at_crash) == sum(calls_at_crash.values())
    if seam == "llm_provider_return":
        assert not raw_rows_at_crash
    if seam in {"llm_raw_result", "llm_effect_commit"}:
        assert len(raw_rows_at_crash) == 1
    if seam == "llm_effect_commit":
        assert extract_node_status_at_crash is not None
        assert extract_node_status_at_crash[0] != "succeeded"
        committed_at_crash = [
            row for row in effect_rows_at_crash if row[1] == "committed"
        ]
        assert len(committed_at_crash) == 1
        committed_effect_id, _, committed_outcome_json = committed_at_crash[0]
        committed_outcome = json.loads(committed_outcome_json)
        committed_result_ref = committed_outcome["value"]["result_ref"]
        assert committed_result_ref == "sha256:" + raw_rows_at_crash[0][0]
        async with aiosqlite.connect(database) as db:
            committed_owner = await (await db.execute(
                "SELECT owner_kind,owner_id FROM workflow_blob_refs "
                "WHERE sha256=? AND owner_kind='effect'",
                (committed_result_ref.removeprefix("sha256:"),),
            )).fetchall()
        assert [tuple(row) for row in committed_owner] == [
            ("effect", committed_effect_id)
        ]
    crashed_row = await store.get_run(run_id)
    assert crashed_row is not None and crashed_row["status"] == "running"

    now[0] = 200.0
    recovered = await _runtime(
        tmp_path,
        "comparison",
        run_id=run_id,
        providers=providers,
        clock=lambda: now[0],
        owner=f"after-crash-{seam}",
    )
    _, recovered_store, recovered_runner, _, recovered_context, _, _, blobs = recovered
    records = await recovered_runner.recover_expired()
    assert any(record.run_id == run_id for record in records)
    recovered_row = await recovered_store.get_run(run_id)
    assert recovered_row is not None
    if seam in {"llm_provider_return", "llm_raw_result", "llm_effect_commit"}:
        # Lease recovery does not guess whether an already-dispatched call
        # completed.  The resumed adapter reconciles that running journal row
        # to canonical opaque_uncertain without sending the call again.
        assert any(record.reason == "lease_expired" for record in records)
        assert recovered_row["status"] == "retryable"
        assert providers["evidence_candidate_extract"].calls == calls_at_crash[
            "evidence_candidate_extract"
        ]
        assert providers["evidence_inference_synthesize"].calls == 0
        assert providers["evidence_structured_repair"].calls == 0
    if seam in {"candidate_bundle", "inference_bundle"}:
        assert any(record.reason == "succeeded_pending" for record in records)
    assert recovered_row["status"] == "retryable"

    result = await recovered_runner.run(run_id, None, recovered_context)
    assert result.error is None, await recovered_runner.history(run_id)
    assert result.status.value == "completed"
    output = result.output
    values = output["values"]

    # Every provider call that existed before the crash is reused.  A restart
    # may still send first calls for later work groups/stages, but no durable
    # call is ever sent twice.
    assert providers["evidence_candidate_extract"].calls >= 1
    assert providers["evidence_inference_synthesize"].calls >= 1
    assert providers["evidence_structured_repair"].calls == 0

    batch_refs = values["fact_batch_refs"]
    assert len(batch_refs) == 2
    assert len(batch_refs) == len(set(batch_refs))
    batches = [
        EvidenceFactBatchV1.from_json(await _load_json_ref(blobs, ref))
        for ref in batch_refs
    ]
    assert [batch.batch_kind for batch in batches] == ["facts", "inferences"]
    assert [batch.ordinal for batch in batches] == [0, 1]
    assert batches[1].previous_head_hash == batches[0].head_hash
    assert values["evidence_head_hash"] == batches[1].head_hash

    assessment_ref = values["assessment_ref"]
    assessment_bytes = await blobs.get(assessment_ref.removeprefix("sha256:"))
    assessment = json.loads(assessment_bytes.decode("utf-8"))
    assert hashlib.sha256(assessment_bytes).hexdigest() == assessment_ref[7:]
    assert assessment["assessment_hash"] == values["assessment_hash"]
    assert values["terminal_manifest_ref"].startswith("sha256:")

    snapshot = await _load_json_ref(blobs, values["continuation_snapshot_ref"])
    snapshot_closure = set(snapshot["closure_refs"])
    fact_refs = set(batches[0].admitted_fact_refs)
    inference_refs = set(batches[1].registered_inference_refs)
    assert set(batch_refs).issubset(snapshot_closure)
    assert fact_refs.issubset(snapshot_closure)
    assert inference_refs.issubset(snapshot_closure)
    assert assessment_ref in snapshot_closure
    assert values["claim_batch_ref"] in snapshot_closure

    # The terminal snapshot may only own readable registered blobs.  This also
    # catches a recovery path that reused a head but lost one of its closure
    # owners during the process boundary.
    for item in output["blob_refs"]:
        digest = item["sha256"] if isinstance(item, dict) else item
        assert await blobs.get(digest)

    async with aiosqlite.connect(database) as db:
        llm_effect_rows = await (await db.execute(
            "SELECT effect_id,status,prepared_json FROM workflow_effects "
            "WHERE run_id=? AND effect_type='research_llm'",
            (run_id,),
        )).fetchall()
        raw_rows = await (await db.execute(
            "SELECT sha256,size_bytes FROM workflow_blobs "
            "WHERE media_type='application/vnd.deskpet.research-llm-result+json'",
        )).fetchall()
        semantic_outcome_rows = await (await db.execute(
            "SELECT sha256 FROM workflow_blobs "
            "WHERE media_type="
            "'application/vnd.deskpet.deepresearch-v6-llm-effect-outcome+json'",
        )).fetchall()
        producer_outcome_rows = await (await db.execute(
            "SELECT sha256 FROM workflow_blobs "
            "WHERE media_type="
            "'application/vnd.deskpet.candidate-producer-outcome.v1+json'",
        )).fetchall()
    final_effect_ids = {row[0] for row in llm_effect_rows}
    assert effects_at_crash.issubset(final_effect_ids)
    assert len(llm_effect_rows) == sum(provider.calls for provider in providers.values())
    stable_call_ids = [json.loads(row[2])["stable_call_id"] for row in llm_effect_rows]
    assert len(stable_call_ids) == len(set(stable_call_ids))
    statuses = {row[1] for row in llm_effect_rows}
    assert statuses <= {"committed", "uncertain"}
    if seam in {"llm_provider_return", "llm_raw_result"}:
        uncertain_effect_ids = {
            row[0] for row in llm_effect_rows if row[1] == "uncertain"
        }
        assert len(uncertain_effect_ids) == 1
        assert uncertain_effect_ids == effects_at_crash

        semantic_outcomes = {
            row[0]: json.loads((await blobs.get(row[0])).decode("utf-8"))
            for row in semantic_outcome_rows
        }
        opaque_semantic_outcomes = [
            (digest, item)
            for digest, item in semantic_outcomes.items()
            if item["status"] == "opaque_uncertain"
        ]
        assert len(opaque_semantic_outcomes) == 1
        opaque_outcome_digest, opaque_outcome = opaque_semantic_outcomes[0]
        assert opaque_outcome["effect_id"] in uncertain_effect_ids
        assert opaque_outcome["raw_result_ref"] is None
        assert opaque_outcome["result_ref"] is None

        producer_outcomes = [
            json.loads((await blobs.get(row[0])).decode("utf-8"))
            for row in producer_outcome_rows
        ]
        opaque_producer_outcomes = [
            item for item in producer_outcomes if item["status"] == "opaque_uncertain"
        ]
        assert len(opaque_producer_outcomes) == 1
        assert opaque_producer_outcomes[0]["llm_effect_outcome_ref"] == (
            "sha256:" + opaque_outcome_digest
        )

        final_raw_digests = {row[0] for row in raw_rows}
        assert {row[0] for row in raw_rows_at_crash}.issubset(final_raw_digests)
        for digest, size_bytes in raw_rows_at_crash:
            raw_bytes = await blobs.get(digest)
            assert len(raw_bytes) == size_bytes
            assert hashlib.sha256(raw_bytes).hexdigest() == digest
    else:
        assert statuses == {"committed"}
        if seam == "llm_effect_commit":
            semantic_outcomes = [
                json.loads((await blobs.get(row[0])).decode("utf-8"))
                for row in semantic_outcome_rows
            ]
            recovered_outcomes = [
                item
                for item in semantic_outcomes
                if item["effect_id"] in effects_at_crash
            ]
            assert len(recovered_outcomes) == 1
            assert recovered_outcomes[0]["status"] == "validated"
            assert recovered_outcomes[0]["raw_result_ref"] == committed_result_ref
            assert recovered_outcomes[0]["result_ref"].startswith("sha256:")
