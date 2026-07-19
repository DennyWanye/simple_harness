from __future__ import annotations

import copy

import pytest

from deskpet.workflows import WorkflowContext
from deskpet.workflows.contracts import NodeExecutionIdentity, canonical_json
from deskpet.workflows.definitions.deep_research_v6_assessment import (
    assess_requirements,
    decode_assessment_inputs,
)
from deskpet.workflows.definitions.deep_research_v6_compiler import compile_research_spec
from deskpet.workflows.definitions.deep_research_v6_contracts import format_blob_ref, parse_blob_ref
from deskpet.workflows.definitions.deep_research_v6_delivery import (
    TerminalDeliveryManifestV1,
    build_terminal_commit_request,
    project_v6_terminal_commit,
)
from deskpet.workflows.definitions.deep_research_v6_evidence import (
    AdmittedResearchFactV1,
    EvidenceFactBatchV1,
    GENESIS_EVIDENCE_HEAD,
)
from deskpet.workflows.definitions.deep_research_v6_terminal_nodes import (
    CLAIM_POLICY_V1,
    QUALITY_POLICY_V1,
    build_continuation_snapshot,
    integrity_handler,
    persist_manifest_handler,
    render_claims_handler,
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


async def _put_json(
    blobs: RegisteredBlobStore,
    identity: NodeExecutionIdentity,
    value,
) -> str:
    ref = await blobs.put(
        canonical_json(value).encode("utf-8"), identity, media_type="application/json"
    )
    return format_blob_ref(ref.sha256)


async def _fixture(
    tmp_path,
    *,
    extra_provenance: bool = False,
    answer_locale: str = "en-US",
    localized_exact: bool = False,
):
    run_id = "run-terminal-generic"
    blobs = RegisteredBlobStore(tmp_path / "blobs", tmp_path / "workflow.db")
    seed = _identity(run_id, "seed")
    spec = compile_research_spec(
        "What was China's total population in 2024?",
        answer_locale=answer_locale,
        as_of_date="2026-07-18",
    )
    spec_ref = await _put_json(blobs, seed, spec.to_json())

    policy_values = {
        "compiler": {"schema_version": 1, "policy_id": "compiler-test"},
        "route": {"schema_version": 1, "policy_id": "route-test"},
        "extraction": {"schema_version": 1, "policy_id": "extraction-test"},
        "llm_extract": {"schema_version": 1, "policy_id": "llm-extract-test"},
        "llm_repair": {"schema_version": 1, "policy_id": "llm-repair-test"},
        "llm_inference": {"schema_version": 1, "policy_id": "llm-inference-test"},
        "admission": {"schema_version": 1, "policy_id": "admission-test"},
        "inference": {"schema_version": 1, "policy_id": "inference-test"},
        "assessment": {"schema_version": 1, "policy_id": "assessment-test"},
        "claim": CLAIM_POLICY_V1,
        "quality": QUALITY_POLICY_V1,
    }
    policy_refs = {
        key: await _put_json(blobs, seed, value)
        for key, value in policy_values.items()
    }
    requirement_id = str(spec.requirements[0]["requirement_id"])
    fact = AdmittedResearchFactV1.create(
        run_id=run_id,
        spec_hash=spec.spec_hash,
        requirement_id=requirement_id,
        target_kind="scalar",
        item_or_cell_id=None,
        field_or_facet_key=None,
        candidate_id="ecd-test-candidate",
        page_id="page-test",
        span_id="span-test",
        binding_id="binding-test",
        source_family_id="official-test",
        source_tier="first_party",
        admission_policy_hash=parse_blob_ref(policy_refs["admission"]),
        status="admitted",
        semantic_payload=(
            {
                "value": 1408280000,
                "canonical_unit": "person",
                "time_scope": spec.requirements[0]["time_scope"],
                "scope": spec.requirements[0]["scope"],
                "definition": "year_end_total_population",
            }
            if localized_exact
            else {
                "value": 140828,
                "canonical_unit": "ten_thousand_persons",
                "time_scope": "2024",
                "scope": "China",
                "definition": "year-end national population",
            }
        ),
    )
    fact_ref = await _put_json(blobs, seed, fact.to_json())
    provenance = {fact_ref}
    if extra_provenance:
        provenance.add(await _put_json(blobs, seed, {"schema_version": 1, "orphan": True}))
    batch = EvidenceFactBatchV1.create(
        batch_kind="facts",
        run_id=run_id,
        spec_hash=spec.spec_hash,
        previous_head_hash=GENESIS_EVIDENCE_HEAD,
        ordinal=0,
        page_result_refs=(),
        candidate_slot_results=(),
        inference_slot_results=(),
        admitted_fact_refs=(fact_ref,),
        registered_inference_refs=(),
        rejected_candidate_ids=(),
        conflict_ids=(),
        provenance_refs=tuple(provenance),
        policy_refs=policy_refs,
    )
    batch_ref = await _put_json(blobs, seed, batch.to_json())
    registered = {fact_ref: fact.to_json()}
    facts, inferences = decode_assessment_inputs(
        (fact_ref,), (), spec, registered_objects=registered
    )
    assessment = assess_requirements(
        spec=spec,
        evidence_head_hash=batch.head_hash,
        facts=facts,
        inferences=inferences,
        ordered_fact_refs=(fact_ref,),
        ordered_inference_refs=(),
        policy_hash=parse_blob_ref(policy_refs["assessment"]),
    )
    assessment_ref = await _put_json(blobs, seed, assessment.to_json())
    refs = {
        spec_ref, batch_ref, fact_ref, assessment_ref, *policy_refs.values(), *provenance,
    }
    state = {
        "schema_version": 1,
        "workflow_name": "deep_research",
        "workflow_version": "v6",
        "thread_id": run_id,
        "run_id": run_id,
        "session_id": "session-terminal",
        "active_nodes": [],
        "active_step_id": None,
        "status": "running",
        "values": {
            "spec_ref": spec_ref,
            "spec_hash": spec.spec_hash,
            "fact_batch_refs": [batch_ref],
            "evidence_head_hash": batch.head_hash,
            "assessment_ref": assessment_ref,
            "assessment_hash": assessment.assessment_hash,
            "policy_refs": policy_refs,
            "stage": "answer_assessed",
        },
        "blob_refs": [
            {"id": parse_blob_ref(ref), "sha256": parse_blob_ref(ref)}
            for ref in sorted(refs)
        ],
        "artifact_refs": [],
        "receipt_refs": [],
        "loop_counters": {},
        "budgets": {},
        "errors": [],
    }
    return state, blobs, spec, assessment


@pytest.mark.asyncio
async def test_generic_terminal_handlers_persist_ref_only_exact_snapshot_and_manifest(tmp_path) -> None:
    state, blobs, spec, assessment = await _fixture(tmp_path)
    run_id = str(state["run_id"])
    for node_id, handler in (
        ("render_claims", render_claims_handler),
        ("integrity", integrity_handler),
        ("persist_manifest", persist_manifest_handler),
    ):
        patch = await handler(
            state,
            WorkflowContext(ports={"blob": blobs}, identity=_identity(run_id, node_id)),
        )
        state.update(patch.to_dict())

    values = state["values"]
    render = __import__("json").loads(
        (await blobs.get(parse_blob_ref(str(values["render_bundle_ref"])))).decode("utf-8")
    )
    final_text = (
        await blobs.get(parse_blob_ref(str(render["final_assistant_ref"])))
    ).decode("utf-8")
    assert final_text == (
        "## Answer\n\n"
        "- year-end national population: 140828 "
        "[unit=ten_thousand_persons; time=2024; scope=\"China\"; "
        "definition=year-end national population]"
    )
    forbidden_inline = {
        "claims", "final_assistant", "report_markdown", "research_spec",
        "answer_result", "integrity_assessment", "integrity_fact_batches",
    }
    assert forbidden_inline.isdisjoint(values)
    snapshot_ref = str(values["continuation_snapshot_ref"])
    snapshot = __import__("json").loads(
        (await blobs.get(parse_blob_ref(snapshot_ref))).decode("utf-8")
    )
    assert snapshot["assessment_input_hash"] == assessment.assessment_input_hash
    assert set(snapshot["closure_refs"]) == {
        snapshot["spec_ref"],
        *snapshot["fact_batch_refs"],
        snapshot["assessment_ref"],
        snapshot["claim_batch_ref"],
        *snapshot["provenance_refs"],
        *snapshot["policy_refs"].values(),
    }
    base = copy.deepcopy(snapshot)
    assert base.pop("snapshot_id") == "rcs_" + base["snapshot_hash"][:24]
    snapshot_hash = base.pop("snapshot_hash")
    from deskpet.workflows.definitions.deep_research_v6_contracts import sha256_json
    assert snapshot_hash == sha256_json(base)

    manifest_ref = str(values["terminal_manifest_ref"])
    manifest = TerminalDeliveryManifestV1.from_json(
        __import__("json").loads(
            (await blobs.get(parse_blob_ref(manifest_ref))).decode("utf-8")
        )
    )
    assert manifest.manifest_ref == manifest_ref
    assert manifest.value["continuation_snapshot_ref"] == snapshot_ref
    assert manifest.value["spec_hash"] == spec.spec_hash
    assert manifest.value["answer_status"] == "completed"
    request = build_terminal_commit_request(
        state=state,
        engine_status="completed",
        engine_error_code=None,
        recovery_action=None,
    )
    projection = await project_v6_terminal_commit(
        request.to_json(),
        WorkflowContext(
            ports={"blob": blobs}, identity=_identity(run_id, "terminal-projector")
        ),
    )
    assert projection["manifest_ref"] == manifest_ref
    assert projection["answer_status"] == "completed"
    assert set(projection["blob_refs"]) == {
        manifest_ref,
        snapshot_ref,
        *snapshot["closure_refs"],
        *[ref for ref in manifest.value["content_refs"].values() if ref is not None],
    }


@pytest.mark.asyncio
async def test_zh_exact_terminal_renders_user_facing_statistic_without_internal_fields(
    tmp_path,
) -> None:
    state, blobs, _, _ = await _fixture(
        tmp_path,
        answer_locale="zh-CN",
        localized_exact=True,
    )
    run_id = str(state["run_id"])
    patch = await render_claims_handler(
        state,
        WorkflowContext(
            ports={"blob": blobs}, identity=_identity(run_id, "render_claims")
        ),
    )
    state.update(patch.to_dict())
    render = __import__("json").loads(
        (
            await blobs.get(
                parse_blob_ref(str(state["values"]["render_bundle_ref"]))
            )
        ).decode("utf-8")
    )
    final_text = (
        await blobs.get(parse_blob_ref(str(render["final_assistant_ref"])))
    ).decode("utf-8")
    assert final_text == (
        "## 结论\n\n"
        "- 年末总人口：140828 万人（时间：2024年末；范围：全国）"
    )
    assert all(
        marker not in final_text
        for marker in (
            "1408280000",
            "unit=",
            "scope=",
            "definition=",
            "year_end_total_population",
        )
    )


@pytest.mark.asyncio
async def test_persist_manifest_rejects_arbitrary_same_run_blob_in_batch_provenance(tmp_path) -> None:
    state, blobs, _, _ = await _fixture(tmp_path, extra_provenance=True)
    run_id = str(state["run_id"])
    for node_id, handler in (
        ("render_claims", render_claims_handler),
        ("integrity", integrity_handler),
    ):
        patch = await handler(
            state,
            WorkflowContext(ports={"blob": blobs}, identity=_identity(run_id, node_id)),
        )
        state.update(patch.to_dict())
    with pytest.raises(ValueError, match="exact transitive closure"):
        await persist_manifest_handler(
            state,
            WorkflowContext(
                ports={"blob": blobs}, identity=_identity(run_id, "persist_manifest")
            ),
        )


def test_snapshot_builder_is_identity_stable_and_assessment_input_is_hash_bound() -> None:
    class Inputs:
        spec = type("Spec", (), {"spec_hash": "a" * 64})()
        assessment = type("Assessment", (), {
            "evidence_head_hash": "b" * 64,
            "assessment_hash": "c" * 64,
            "assessment_input_hash": "d" * 64,
        })()
        batch_refs = ("sha256:" + "1" * 64,)
        policy_refs = {
            key: "sha256:" + format(index + 2, "064x")
            for index, key in enumerate(sorted({
                "compiler", "route", "extraction", "llm_extract", "llm_repair",
                "llm_inference", "admission", "inference", "assessment", "claim", "quality",
            }))
        }

    kwargs = dict(
        run_id="run-one",
        spec_ref="sha256:" + "e" * 64,
        assessment_ref="sha256:" + "f" * 64,
        inputs=Inputs(),
        claim_batch_ref="sha256:" + "9" * 64,
        provenance_refs=("sha256:" + "8" * 64,),
    )
    first = build_continuation_snapshot(**kwargs)
    second = build_continuation_snapshot(**kwargs)
    assert first == second
    Inputs.assessment.assessment_input_hash = "7" * 64
    changed = build_continuation_snapshot(**kwargs)
    assert changed["snapshot_hash"] != first["snapshot_hash"]
