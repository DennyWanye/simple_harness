from __future__ import annotations

import hashlib
import json

import pytest

from deskpet.workflows import WorkflowContext
from deskpet.workflows.adapters.deep_research_v6_semantic_runtime import (
    DeepResearchV6SemanticRuntime,
)
from deskpet.workflows.adapters.research_runtime import build_v6_research_llm_profiles
from deskpet.workflows.contracts import NodeExecutionIdentity, canonical_json
from deskpet.workflows.definitions.deep_research_v6_contracts import (
    ResearchSpecV1,
    build_route_decision_from_spec,
)
from deskpet.workflows.definitions.deep_research_v6_delivery import (
    PersistedTerminalBundle,
    TerminalCommitRequestV1,
    TerminalDeliveryManifestV1,
    build_terminal_commit_request,
    project_v6_terminal_commit,
)
from deskpet.workflows.definitions.deep_research_v6_evidence_contracts import (
    V6FetchedPageRefPayloadV1,
)
from deskpet.workflows.definitions.deep_research_v6_production_nodes import (
    admit_facts_handler,
    assess_answer_handler,
    compile_spec_handler,
    extract_candidate_bundles_handler,
    load_pages_handler,
    plan_route_handler,
    register_inferences_handler,
    synthesize_inferences_handler,
)
from deskpet.workflows.definitions.deep_research_v6_terminal_nodes import (
    integrity_handler,
    persist_manifest_handler,
    render_claims_handler,
)
from deskpet.workflows.definitions.v6.deep_research import DEEP_RESEARCH_V6, initial_state
from deskpet.workflows.errors import InvalidStatePatch, WorkflowNodeError
from deskpet.workflows.native import InMemoryNativeCheckpointStore
from deskpet.workflows.store import RegisteredBlobStore


QUESTION = "深入调研：2024年中国总人口和出生人口分别是多少？优先国家统计局。"
SOURCE_URL = "https://www.stats.gov.cn/runtime.html"


def _identity(run_id: str, node_id: str) -> NodeExecutionIdentity:
    return NodeExecutionIdentity(
        workflow_name="deep_research", workflow_version="v6", thread_id=run_id,
        run_id=run_id, checkpoint_id=f"checkpoint-{node_id}", checkpoint_ns="",
        task_id=f"task-{node_id}", node_id=node_id, attempt=1,
    )


async def _page(
    blobs: RegisteredBlobStore,
    run_id: str,
    body: str,
    *,
    final_url: str = SOURCE_URL,
) -> V6FetchedPageRefPayloadV1:
    identity = _identity(run_id, "seed")
    body_blob = await blobs.put(
        body.encode("utf-8"), identity, media_type="text/plain; charset=utf-8"
    )
    url_hash = hashlib.sha256(SOURCE_URL.encode("utf-8")).hexdigest()
    final_url_hash = hashlib.sha256(final_url.encode("utf-8")).hexdigest()
    locator_base = {
        "schema_version": 1, "canonical_url": SOURCE_URL, "final_url": final_url,
        "canonical_url_hash": url_hash, "final_url_hash": final_url_hash,
        "authority_id": "cn.nbs", "verification_status": "verified",
        "verification_policy_hash": "p" * 64,
        "redirect_chain_hashes": [] if final_url == SOURCE_URL else [final_url_hash],
    }
    locator = {
        **locator_base,
        "locator_id": "locator_" + hashlib.sha256(
            canonical_json(locator_base).encode("utf-8")
        ).hexdigest()[:24],
    }
    locator_blob = await blobs.put(
        canonical_json(locator).encode("utf-8"), identity,
        media_type="application/vnd.deskpet.source-locator.v1+json",
    )
    page_id = "page_" + hashlib.sha256(
        (final_url_hash + body_blob.sha256).encode("ascii")
    ).hexdigest()[:24]
    return V6FetchedPageRefPayloadV1(
        page_id=page_id, ordinal=0,
        source_locator_ref=f"sha256:{locator_blob.sha256}",
        body_ref=f"sha256:{body_blob.sha256}", body_hash=body_blob.sha256,
        canonical_url_hash=url_hash, final_url_hash=final_url_hash,
        authority_id="cn.nbs", title_hash=hashlib.sha256(b"title").hexdigest(),
        media_type="text/plain; charset=utf-8", admission_status="admitted",
        reason_codes=("official_domain_match",),
    )


async def _bundle(
    tmp_path,
    *,
    run_id: str,
    body: str | None,
    final_url: str = SOURCE_URL,
):
    blobs = RegisteredBlobStore(tmp_path / "blobs", tmp_path / "workflow.db")
    pages = [] if body is None else [
        await _page(blobs, run_id, body, final_url=final_url)
    ]
    ports = _ports(blobs, pages)
    state = initial_state(topic=QUESTION, run_id=run_id, thread_id=run_id)
    for node_id, handler in (
        ("compile_spec", compile_spec_handler),
        ("plan_route", plan_route_handler),
        ("load_pages", load_pages_handler),
        ("extract_candidate_bundles", extract_candidate_bundles_handler),
        ("admit_facts", admit_facts_handler),
        ("synthesize_inferences", synthesize_inferences_handler),
        ("register_inferences", register_inferences_handler),
        ("assess_answer", assess_answer_handler),
        ("render_claims", render_claims_handler),
        ("integrity", integrity_handler),
        ("persist_manifest", persist_manifest_handler),
    ):
        patch = await handler(
            state,
            WorkflowContext(
                ports=ports, identity=_identity(run_id, node_id)
            ),
        )
        state.update(patch.to_dict())
    bundle = PersistedTerminalBundle(
        manifest_ref=str(state["values"]["terminal_manifest_ref"]),
        manifest_hash=str(state["values"]["terminal_manifest_hash"]),
        blob_refs=tuple(
            f"sha256:{item['sha256']}" for item in state["blob_refs"]
        ),
    )
    return state, blobs, bundle


def _ports(blobs: RegisteredBlobStore, pages: list[V6FetchedPageRefPayloadV1]):
    class Retrieval:
        async def plan_route(self, *, spec, identity):
            parsed = ResearchSpecV1.from_json(spec)
            policy = {"schema_version": 1, "policy_id": "delivery-test-route-v1"}
            policy_blob = await blobs.put(
                canonical_json(policy).encode("utf-8"), identity,
                media_type="application/json",
            )
            policy_ref = f"sha256:{policy_blob.sha256}"
            decision = build_route_decision_from_spec(
                parsed,
                run_id=identity.run_id,
                policy_hash=policy_blob.sha256,
                capability_snapshot_hash="c" * 64,
                budget={"query": 1, "fetch": 8, "browser": 0, "llm": 0, "lane": 1},
            )
            decision_blob = await blobs.put(
                canonical_json(decision.to_json()).encode("utf-8"), identity,
                media_type="application/json",
            )
            return {
                "route_policy_ref": policy_ref,
                "route_policy_hash": policy_blob.sha256,
                "route_decision_ref": f"sha256:{decision_blob.sha256}",
                "route_id": decision.to_json()["route_id"],
                "blob_refs": [
                    {"id": policy_blob.sha256, "sha256": policy_blob.sha256},
                    {"id": decision_blob.sha256, "sha256": decision_blob.sha256},
                ],
            }

        async def load_pages(self, *, spec, identity, route_decision):
            ResearchSpecV1.from_json(spec)
            assert route_decision["run_id"] == identity.run_id
            result_refs = []
            refs = set()
            for page in pages:
                blob = await blobs.put(
                    canonical_json(page.to_json()).encode("utf-8"), identity,
                    media_type="application/json",
                )
                result_refs.append(f"sha256:{blob.sha256}")
                refs.update((blob.sha256, page.body_ref[7:], page.source_locator_ref[7:]))
            return {
                "page_result_refs": result_refs,
                "blob_refs": [{"id": ref, "sha256": ref} for ref in sorted(refs)],
            }

    profiles = build_v6_research_llm_profiles(
        {
            "evidence_candidate_extract": "a" * 64,
            "evidence_inference_synthesize": "b" * 64,
            "evidence_structured_repair": "c" * 64,
        }
    )

    class NeverLLM:
        def __init__(self, profile):
            self.profile = profile
            self.profile_ref = profile.profile_ref

        async def execute(self, **_kwargs):
            raise AssertionError("official exact path must not call an LLM")

    stages = {role: NeverLLM(profile) for role, profile in profiles.items()}
    semantic = DeepResearchV6SemanticRuntime(
        blobs=blobs,
        llm_extract=stages["evidence_candidate_extract"],
        llm_inference=stages["evidence_inference_synthesize"],
        llm_repair=stages["evidence_structured_repair"],
    )
    return {"blob": blobs, "retrieval": Retrieval(), "semantic": semantic}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,expected_status,expected_report,expected_artifact",
    [
        ("2024年末全国人口140828万人。2024年全年出生人口954万人。", "completed", 1, 1),
        ("2024年末全国人口140828万人。", "partial", 1, 1),
        (None, "insufficient_evidence", 0, 0),
    ],
)
async def test_manifest_persistence_freezes_status_cardinality_and_content_refs(
    tmp_path, body, expected_status, expected_report, expected_artifact
) -> None:
    _, blobs, bundle = await _bundle(tmp_path, run_id="run-status", body=body)
    raw = await blobs.get(bundle.manifest_hash)
    manifest = TerminalDeliveryManifestV1.from_json(json.loads(raw.decode("utf-8")))
    value = manifest.to_json()
    snapshot = json.loads(
        (await blobs.get(value["continuation_snapshot_ref"][7:])).decode("utf-8")
    )
    claim_batch = json.loads(
        (await blobs.get(snapshot["claim_batch_ref"][7:])).decode("utf-8")
    )
    quality_audit = json.loads(
        (await blobs.get(value["content_refs"]["quality_audit_ref"][7:])).decode("utf-8")
    )

    assert manifest.manifest_hash == bundle.manifest_hash
    assert manifest.manifest_ref == bundle.manifest_ref
    assert value["answer_status"] == expected_status
    assert value["cardinality"] == {
        "final_assistant": 1,
        "workflow_final_status": 1,
        "report": expected_report,
        "artifact": expected_artifact,
        "run_terminal": 1,
    }
    assert snapshot["assessment_hash"] == value["assessment_hash"]
    assert snapshot["evidence_head_hash"] != "0" * 64 or body is None
    # v6 always checkpoints the facts frontier and then the inference frontier,
    # including the empty-evidence terminal path.
    assert len(snapshot["fact_batch_refs"]) == 2
    assert bool(snapshot["provenance_refs"]) is (body is not None)
    assert claim_batch["status"] == "valid"
    assert quality_audit["hard_gate_status"] == "passed"
    assert quality_audit["hard_failure_codes"] == []
    assert quality_audit["answer_status"] == expected_status
    assert quality_audit["claim_batch_ref"] == snapshot["claim_batch_ref"]
    expected_closure = {
        snapshot["spec_ref"],
        *snapshot["fact_batch_refs"],
        snapshot["assessment_ref"],
        snapshot["claim_batch_ref"],
        *snapshot["provenance_refs"],
        *snapshot["policy_refs"].values(),
    }
    assert set(snapshot["closure_refs"]) == expected_closure
    if expected_status == "insufficient_evidence":
        assert value["content_refs"]["report_ref"] is None
        assert value["content_refs"]["safe_summary_ref"] == value["content_refs"]["final_assistant_ref"]
    else:
        assert value["content_refs"]["report_ref"].startswith("sha256:")
        assert value["content_refs"]["safe_summary_ref"] is None


@pytest.mark.asyncio
async def test_report_cites_successful_canonical_request_when_final_host_alias_differs(
    tmp_path,
) -> None:
    final_alias = "https://stats.gov.cn/runtime.html"
    _, blobs, bundle = await _bundle(
        tmp_path,
        run_id="run-citation-alias",
        body=(
            "2024\u5e74\u672b\u5168\u56fd\u4eba\u53e3140828\u4e07\u4eba\u3002"
            "2024\u5e74\u5168\u5e74\u51fa\u751f\u4eba\u53e3954\u4e07\u4eba\u3002"
        ),
        final_url=final_alias,
    )
    manifest = TerminalDeliveryManifestV1.from_json(json.loads(
        (await blobs.get(bundle.manifest_hash)).decode("utf-8")
    ))
    report_ref = str(manifest.to_json()["content_refs"]["report_ref"])
    report = (await blobs.get(report_ref[7:])).decode("utf-8")

    assert SOURCE_URL in report
    assert final_alias not in report


@pytest.mark.asyncio
async def test_async_projection_emits_identity_only_payloads_and_exact_final_status(tmp_path) -> None:
    state, blobs, bundle = await _bundle(
        tmp_path, run_id="run-project",
        body="2024年末全国人口140828万人。2024年全年出生人口954万人。",
    )
    request = build_terminal_commit_request(
        state=state, engine_status="completed", engine_error_code=None,
        recovery_action=None,
    )
    projection = await project_v6_terminal_commit(
        request.to_json(), WorkflowContext(ports={"blob": blobs})
    )

    assert projection["manifest_ref"] == bundle.manifest_ref
    assert projection["blob_refs"] == sorted(set(projection["blob_refs"]))
    assert [item["event_key"] for item in projection["intents"]] == [
        "answer:final", "artifact:report", "run:terminal",
    ]
    answer = projection["intents"][0]
    assert set(answer["payload"]) == {
        "schema_version", "manifest_ref", "intent_id", "content_ref"
    }
    final = projection["intents"][-1]["payload"]
    assert final["status"] == "completed"
    assert final["error"] is None and final["recovery_action"] is None
    serialized = json.dumps(projection, ensure_ascii=False)
    assert "140828" not in serialized
    assert "954万人" not in serialized
    assert "stats.gov.cn" not in serialized


@pytest.mark.asyncio
async def test_native_terminal_commit_materializes_exact_intents(tmp_path) -> None:
    run_id = "run-native-terminal"
    blobs = RegisteredBlobStore(tmp_path / "blobs", tmp_path / "workflow.db")
    page = await _page(
        blobs, run_id, "2024年末全国人口140828万人。2024年全年出生人口954万人。"
    )
    store = InMemoryNativeCheckpointStore()
    ports = _ports(blobs, [page])
    result = await DEEP_RESEARCH_V6.bind(checkpointer=store).ainvoke(
        initial_state(topic=QUESTION, run_id=run_id),
        WorkflowContext(ports=ports), thread_id=run_id, run_id=run_id,
    )
    assert result["values"]["answer_status"] == "completed"
    assert set(store.materialized_intents) == {
        "answer:final", "artifact:report", "run:terminal"
    }
    assert all(
        "140828" not in json.dumps(intent, ensure_ascii=False)
        for intent in store.materialized_intents.values()
    )


def test_commit_request_is_exact_and_requires_completed_engine_tuple() -> None:
    valid = {
        "schema_version": 1, "workflow_name": "deep_research", "workflow_version": "v6",
        "run_id": "run", "manifest_ref": "sha256:" + "a" * 64,
        "manifest_hash": "a" * 64, "engine_status": "completed",
        "engine_error_code": None, "recovery_action": None,
    }
    assert TerminalCommitRequestV1.from_json(valid).to_json() == valid
    with pytest.raises(InvalidStatePatch):
        TerminalCommitRequestV1.from_json(valid | {"compat": True})
    with pytest.raises(InvalidStatePatch):
        TerminalCommitRequestV1.from_json(valid | {"engine_status": "failed"})


@pytest.mark.asyncio
async def test_native_v6_missing_blob_port_fails_closed() -> None:
    state = initial_state(topic=QUESTION, run_id="run-missing")
    with pytest.raises(WorkflowNodeError):
        await DEEP_RESEARCH_V6.bind(
            checkpointer=InMemoryNativeCheckpointStore()
        ).ainvoke(
            state, WorkflowContext(), thread_id="run-missing", run_id="run-missing"
        )
