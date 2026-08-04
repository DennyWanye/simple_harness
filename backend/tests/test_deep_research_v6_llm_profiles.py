from __future__ import annotations

import hashlib
import json

import aiosqlite
import pytest

from deskpet.workflows.adapters.research_runtime import (
    BoundResearchEffectContext,
    DurableResearchCallEffectAdapter,
    DurableV6ResearchLLMStagePort,
    ResearchEffectAdapterError,
    V6_RESEARCH_RESPONSE_FORMATS,
    build_v6_research_llm_profiles,
    research_response_format_hash,
)
from deskpet.workflows.contracts import NodeExecutionIdentity, canonical_json
from deskpet.workflows.definitions.deep_research_v5_contracts import ResearchLLMResult
from deskpet.workflows.definitions.research_core import (
    RESEARCH_LLM_ROLES,
    ResearchLLMPortV2,
)
from deskpet.workflows.effects import EffectExecutionContext, EffectJournal
from deskpet.workflows.store import RegisteredBlobStore, WorkflowRunStore


EXTRACT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "deskpet_deep_research_v6_candidate_bundle",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["bundle_id"],
            "properties": {"bundle_id": {"type": "string", "minLength": 1}},
        },
    },
}


def _profile_hashes() -> dict[str, str]:
    return {
        "evidence_candidate_extract": research_response_format_hash(
            EXTRACT_RESPONSE_FORMAT
        ),
        "evidence_inference_synthesize": "b" * 64,
        "evidence_structured_repair": "c" * 64,
    }


async def _runtime(tmp_path):
    database = tmp_path / "workflow.db"
    store = WorkflowRunStore(database)
    run_id, _ = await store.create_run(
        request_key="research-v6-effect",
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name="deep_research",
        workflow_version="v6",
        manifest_hash="manifest-v6",
        implementation_hash="implementation-v6",
        capability_hash="research-budget-v6",
        capability_snapshot={
            "research_llm_budget": {
                "max_input_tokens": 10_000,
                "max_output_tokens": 10_000,
                "max_cost_micros": 100_000,
            }
        },
        state_schema_version=6,
    )
    fence = await store.claim(run_id, "runner")
    identity = NodeExecutionIdentity(
        workflow_name="deep_research",
        workflow_version="v6",
        thread_id=run_id,
        run_id=run_id,
        checkpoint_id="checkpoint-1",
        checkpoint_ns="root",
        task_id="task-extract",
        node_id="extract_candidate_bundles",
        attempt=1,
    )
    journal = EffectJournal(database)
    context = EffectExecutionContext(
        journal=journal,
        fence=fence,
        node_execution_id="node-execution-extract",
        workflow_name=identity.workflow_name,
        workflow_version=identity.workflow_version,
        node_id=identity.node_id,
    )
    blobs = RegisteredBlobStore(tmp_path / "blobs", database)

    async def resolve(requested):
        assert requested is identity
        return BoundResearchEffectContext(identity, context)

    return database, identity, journal, blobs, resolve


async def _payload(blobs, identity) -> str:
    row = await blobs.put(
        b'{"prompt":"extract candidate facts"}',
        identity,
        media_type="application/json",
    )
    return f"sha256:{row.sha256}"


def _adapter(*, journal, blobs, resolve, llm_call, **adapter_options):
    profile = build_v6_research_llm_profiles(_profile_hashes())[
        "evidence_candidate_extract"
    ]
    return DurableResearchCallEffectAdapter(
        journal=journal,
        blobs=blobs,
        llm=ResearchLLMPortV2(llm_call),
        resolve_effect_context=resolve,
        reserve_cost_micros=lambda _role, input_tokens, output_tokens: (
            input_tokens + output_tokens
        ),
        actual_cost_micros=lambda result: (
            (result.input_tokens or 0) + (result.output_tokens or 0)
        ),
        profile=profile,
        response_format=EXTRACT_RESPONSE_FORMAT,
        **adapter_options,
    )


def test_v5_prepared_call_default_is_byte_identical_golden() -> None:
    prepared = DurableResearchCallEffectAdapter._prepared(
        role="quality_audit",
        payload_ref=f"sha256:{'a' * 64}",
        max_output_tokens=64,
        stable_call_id="audit-1",
        response_format=None,
    )
    assert canonical_json(prepared.to_dict()) == (
        '{"args_hash":"b764b1617c08c554eaed7b342d59e4ccf82deab96244d45ce7622a7d7f87abf6",'
        '"effect_type":"research_llm","final_params":{"max_output_tokens":64,'
        '"payload_ref":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"response_format_hash":null,"role":"quality_audit"},"input_blob_hashes":[],'
        '"permission_policy_version":"research-readonly-v1","prepared_targets":[],'
        '"schema_hash":"research-llm-result-v1","stable_call_id":"audit-1",'
        '"tool_name":"research_llm_v2","tool_spec_version":"deep-research-v5-llm-v1"}'
    )


def test_v6_profiles_have_frozen_distinct_identities_and_roles() -> None:
    profiles = build_v6_research_llm_profiles(
        {
            "evidence_candidate_extract": "a" * 64,
            "evidence_inference_synthesize": "b" * 64,
            "evidence_structured_repair": "c" * 64,
        }
    )
    assert set(profiles) <= RESEARCH_LLM_ROLES
    assert {
        role: (profile.profile_id, profile.profile_hash)
        for role, profile in profiles.items()
    } == {
        "evidence_candidate_extract": (
            "rlp_923c405d206b784c495246c3",
            "923c405d206b784c495246c3f9d4d3a15a1772182e265aa4aa4d0e73ec9d9830",
        ),
        "evidence_inference_synthesize": (
            "rlp_6eb08ca321deef771146a2f4",
            "6eb08ca321deef771146a2f419cd81c2f39728e58b4371bcb3ce2a1c5702ed17",
        ),
        "evidence_structured_repair": (
            "rlp_39d217e0c200fd119db96260",
            "39d217e0c200fd119db9626039299f8e6d08fb89f223a92443c837a138a8c0aa",
        ),
    }
    assert profiles["evidence_candidate_extract"].max_repair_rounds == 1
    assert profiles["evidence_inference_synthesize"].max_repair_rounds == 1
    assert profiles["evidence_structured_repair"].max_repair_rounds == 0


def test_v6_three_prepared_calls_are_byte_frozen_goldens() -> None:
    profiles = build_v6_research_llm_profiles({
        role: research_response_format_hash(value)
        for role, value in V6_RESEARCH_RESPONSE_FORMATS.items()
    })
    fixtures = {
        "evidence_candidate_extract": (
            "a", "1",
            '{"args_hash":"7daa7216846e4560c4f7797c1ce0fa60bb0e6014a91ec6dc9dd0554209acf813","effect_type":"research_llm","final_params":{"max_output_tokens":4096,"payload_ref":"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","response_format_hash":"dc89783d978b70d7f16446e47f92e090d8f4f83ecc93aa373454e52f9ea4978c","role":"evidence_candidate_extract"},"input_blob_hashes":[],"permission_policy_version":"research-readonly-v1","prepared_targets":[],"schema_hash":"evidence-candidate-bundle-v1","stable_call_id":"1111111111111111111111111111111111111111111111111111111111111111","tool_name":"research_llm_v2","tool_spec_version":"deep-research-v6-evidence-candidate-v1"}',
        ),
        "evidence_inference_synthesize": (
            "b", "2",
            '{"args_hash":"1ac8557bf3ca0675d172d126b486e6fd4d59c559d25094848f3ba699862141b3","effect_type":"research_llm","final_params":{"max_output_tokens":4096,"payload_ref":"sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","response_format_hash":"06829396ef6c1ba0ba44c8e700bda5ed0e04eedb71f371f4ae35f3f5444316c9","role":"evidence_inference_synthesize"},"input_blob_hashes":[],"permission_policy_version":"research-readonly-v1","prepared_targets":[],"schema_hash":"inference-proposal-bundle-v1","stable_call_id":"2222222222222222222222222222222222222222222222222222222222222222","tool_name":"research_llm_v2","tool_spec_version":"deep-research-v6-inference-proposal-v1"}',
        ),
        "evidence_structured_repair": (
            "c", "3",
            '{"args_hash":"67cc8cb21b479ebd3940937995f1ba9d6183d79229772e0c60cbf9cb7ee127c0","effect_type":"research_llm","final_params":{"max_output_tokens":4096,"payload_ref":"sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","response_format_hash":"d26c755f2e6c156be5ad82681caac4797da10f76329fda19e3e8bd6c5cc4ec59","role":"evidence_structured_repair"},"input_blob_hashes":[],"permission_policy_version":"research-readonly-v1","prepared_targets":[],"schema_hash":"structured-repair-result-union-v1","stable_call_id":"3333333333333333333333333333333333333333333333333333333333333333","tool_name":"research_llm_v2","tool_spec_version":"deep-research-v6-structured-repair-v1"}',
        ),
    }
    for role, (digest_char, stable_char, expected) in fixtures.items():
        prepared = DurableResearchCallEffectAdapter._prepared(
            role=role,
            payload_ref=f"sha256:{digest_char * 64}",
            max_output_tokens=4096,
            stable_call_id=stable_char * 64,
            response_format=V6_RESEARCH_RESPONSE_FORMATS[role],
            profile=profiles[role],
        )
        assert canonical_json(prepared.to_dict()) == expected


def test_v6_provider_formats_are_frozen_strict_role_contracts() -> None:
    extract = V6_RESEARCH_RESPONSE_FORMATS["evidence_candidate_extract"]["json_schema"]
    inference = V6_RESEARCH_RESPONSE_FORMATS["evidence_inference_synthesize"]["json_schema"]
    repair = V6_RESEARCH_RESPONSE_FORMATS["evidence_structured_repair"]["json_schema"]
    assert extract["strict"] is True and inference["strict"] is True
    assert repair["strict"] is True
    assert set(extract["schema"]["properties"]) == {
        "schema_version", "bundle_id", "run_id", "spec_hash", "work_group_id",
        "logical_page_id", "page_plan_ordinal", "page_result_ref",
        "route_decision_ref", "route_policy_ref", "extraction_policy_ref",
        "repair_round", "candidates", "bundle_reason_codes",
    }
    assert set(inference["schema"]["properties"]) == {
        "schema_version", "bundle_id", "run_id", "spec_hash", "work_group_id",
        "input_evidence_head_hash", "ordinal", "profile_ref",
        "premise_fact_refs", "proposals",
    }
    assert repair["schema"]["additionalProperties"] is False
    assert set(repair["schema"]["properties"]) == {
        "result_kind", "candidate_bundle", "inference_bundle"
    }
    assert repair["schema"]["properties"]["result_kind"]["enum"] == [
        "candidate_bundle", "inference_bundle"
    ]


def test_v6_logical_effect_id_includes_group_page_round_and_is_prompt_independent() -> None:
    identity = NodeExecutionIdentity(
        workflow_name="deep_research", workflow_version="v6", thread_id="thread",
        run_id="run", checkpoint_id="checkpoint", checkpoint_ns="root",
        task_id="task", node_id="extract_candidate_bundles", attempt=1,
    )
    payload = {
        "work_group": {"work_group_id": "wg_1"},
        "logical_page_id": "page_1",
        "repair_round": 0,
        "body": "content is intentionally outside the identity",
    }
    expected = "v6-extract:root:checkpoint:task:wg_1:page_1:r0"
    assert DurableV6ResearchLLMStagePort.logical_effect_id(
        stage="evidence_candidate_extract", payload=payload, identity=identity
    ) == expected
    payload["body"] = "changed prompt bytes"
    assert DurableV6ResearchLLMStagePort.logical_effect_id(
        stage="evidence_candidate_extract", payload=payload, identity=identity
    ) == expected


def test_v6_profile_rejects_schema_drift_before_effect_begin() -> None:
    profile = build_v6_research_llm_profiles(_profile_hashes())[
        "evidence_candidate_extract"
    ]
    with pytest.raises(ValueError, match="does not match the profile hash"):
        DurableResearchCallEffectAdapter(
            journal=None,  # type: ignore[arg-type]
            blobs=None,  # type: ignore[arg-type]
            llm=None,  # type: ignore[arg-type]
            resolve_effect_context=lambda _identity: None,  # type: ignore[arg-type]
            reserve_cost_micros=lambda *_args: 0,
            actual_cost_micros=lambda _result: 0,
            profile=profile,
            response_format={"type": "drifted"},
        )


@pytest.mark.asyncio
async def test_v6_extract_profile_commits_and_replays_without_resend(tmp_path) -> None:
    database, identity, journal, blobs, resolve = await _runtime(tmp_path)
    calls = 0

    async def complete(
        prompt, *, max_output_tokens, stable_call_id, response_format=None
    ):
        nonlocal calls
        calls += 1
        assert prompt == "extract candidate facts"
        assert response_format == EXTRACT_RESPONSE_FORMAT
        return ResearchLLMResult(
            '{"bundle_id":"ecb_test"}',
            "test-model",
            12,
            5,
            0,
            "provider",
            stable_call_id,
        )

    adapter = _adapter(
        journal=journal, blobs=blobs, resolve=resolve, llm_call=complete
    )
    profile_payload = adapter.profile.to_json()
    profile_payload.pop("profile_id")
    profile_payload.pop("profile_hash")
    registered_profile = await blobs.put(
        canonical_json(profile_payload).encode("utf-8"),
        identity,
        media_type="application/vnd.deskpet.research-llm-profile.v1+json",
    )
    assert f"sha256:{registered_profile.sha256}" == adapter.profile.profile_ref
    payload_ref = await _payload(blobs, identity)
    logical_effect_id = (
        "v6-extract:root:checkpoint-1:task-extract:wg_test:page_test:r0"
    )
    kwargs = {
        "role": "evidence_candidate_extract",
        "payload_ref": payload_ref,
        "max_output_tokens": 256,
        "stable_call_id": hashlib.sha256(logical_effect_id.encode()).hexdigest(),
        "execution_identity": identity,
        "logical_effect_id": logical_effect_id,
    }
    first = await adapter.complete_with_effect(**kwargs)
    assert first == await adapter.complete_with_effect(**kwargs)
    assert calls == 1

    async with aiosqlite.connect(database) as db:
        row = await (
            await db.execute(
                "SELECT policy_json,prepared_json FROM workflow_effects"
            )
        ).fetchone()
    policy = json.loads(row[0])
    prepared = json.loads(row[1])
    assert policy == {
        "kind": "opaque_manual",
        "max_attempts": 1,
        "policy_id": "deep-research-v6-extraction-at-most-once",
        "reusable_across_branches": False,
        "version": "v1",
    }
    assert prepared["tool_spec_version"] == "deep-research-v6-evidence-candidate-v1"
    assert prepared["schema_hash"] == "evidence-candidate-bundle-v1"
    assert prepared["final_params"]["response_format_hash"] == (
        research_response_format_hash(EXTRACT_RESPONSE_FORMAT)
    )
    async with aiosqlite.connect(database) as db:
        owner_rows = await (
            await db.execute(
                """SELECT sha256 FROM workflow_blob_refs
                WHERE owner_kind='effect' ORDER BY sha256"""
            )
        ).fetchall()
    assert {row[0] for row in owner_rows} == {
        payload_ref.removeprefix("sha256:"),
        adapter.profile.profile_hash,
        first.result_ref.removeprefix("sha256:"),
    }


@pytest.mark.asyncio
async def test_v6_opaque_fault_is_held_and_replay_never_resends(tmp_path) -> None:
    _, identity, journal, blobs, resolve = await _runtime(tmp_path)
    calls = 0

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("ambiguous provider timeout")

    adapter = _adapter(
        journal=journal, blobs=blobs, resolve=resolve, llm_call=complete
    )
    profile_payload = adapter.profile.to_json()
    profile_payload.pop("profile_id")
    profile_payload.pop("profile_hash")
    await blobs.put(
        canonical_json(profile_payload).encode("utf-8"),
        identity,
        media_type="application/vnd.deskpet.research-llm-profile.v1+json",
    )
    payload_ref = await _payload(blobs, identity)
    logical_effect_id = (
        "v6-extract:root:checkpoint-1:task-extract:wg_test:page_test:r0"
    )
    kwargs = {
        "role": "evidence_candidate_extract",
        "payload_ref": payload_ref,
        "max_output_tokens": 256,
        "stable_call_id": hashlib.sha256(logical_effect_id.encode()).hexdigest(),
        "execution_identity": identity,
        "logical_effect_id": logical_effect_id,
    }
    with pytest.raises(ResearchEffectAdapterError, match="opaque_uncertain"):
        await adapter(**kwargs)
    with pytest.raises(ResearchEffectAdapterError, match="opaque_uncertain"):
        await adapter(**kwargs)
    assert calls == 1


@pytest.mark.asyncio
async def test_v6_budget_denial_is_a_canonical_no_call_effect(tmp_path) -> None:
    database, identity, journal, blobs, resolve = await _runtime(tmp_path)
    bound = await resolve(identity)
    await journal.ensure_v6_resource_budgets(
        bound.effect_context.fence,
        policy_hash="zero-llm-route",
        budgets={"query": 0, "fetch": 0, "browser": 0, "llm": 0, "lane": 1},
    )
    calls = 0

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("budget-denied effects must not reach the provider")

    adapter = _adapter(
        journal=journal, blobs=blobs, resolve=resolve, llm_call=complete,
        route_resource_budget_kind="llm",
    )
    profile_payload = adapter.profile.to_json()
    profile_payload.pop("profile_id")
    profile_payload.pop("profile_hash")
    await blobs.put(
        canonical_json(profile_payload).encode("utf-8"), identity,
        media_type="application/vnd.deskpet.research-llm-profile.v1+json",
    )
    payload_ref = await _payload(blobs, identity)
    logical_effect_id = (
        "v6-extract:root:checkpoint-1:task-extract:wg_test:page_test:r0"
    )
    envelope = await adapter.complete_with_effect(
        role="evidence_candidate_extract", payload_ref=payload_ref,
        max_output_tokens=256,
        stable_call_id=hashlib.sha256(logical_effect_id.encode()).hexdigest(),
        execution_identity=identity,
        logical_effect_id=logical_effect_id,
    )
    assert envelope.status == "budget_denied"
    assert envelope.result is None and envelope.result_ref is None
    assert calls == 0
    async with aiosqlite.connect(database) as db:
        effect = await (
            await db.execute(
                "SELECT status,outcome_json FROM workflow_effects WHERE effect_id=?",
                (envelope.effect_id,),
            )
        ).fetchone()
        reservation = await (
            await db.execute(
                "SELECT status,dispatch_state FROM workflow_effect_budget_reservations WHERE effect_id=?",
                (envelope.effect_id,),
            )
        ).fetchone()
    assert effect[0] == "failed"
    assert json.loads(effect[1])["error"]["code"] == "budget_denied"
    assert reservation == ("released", "not_started")


@pytest.mark.asyncio
async def test_v6_route_llm_round_budget_consumes_once_and_replay_does_not_repeat(tmp_path) -> None:
    database, identity, journal, blobs, resolve = await _runtime(tmp_path)
    bound = await resolve(identity)
    await journal.ensure_v6_resource_budgets(
        bound.effect_context.fence,
        policy_hash="route-policy",
        budgets={"query": 0, "fetch": 0, "browser": 0, "llm": 1, "lane": 1},
    )
    calls = 0

    async def complete(_prompt, **kwargs):
        nonlocal calls
        calls += 1
        return ResearchLLMResult(
            '{"bundle_id":"ecb_test"}', "test-model", 3, 2, 0,
            "provider", kwargs["stable_call_id"],
        )

    adapter = _adapter(
        journal=journal, blobs=blobs, resolve=resolve, llm_call=complete,
        route_resource_budget_kind="llm",
    )
    profile_payload = adapter.profile.to_json()
    profile_payload.pop("profile_id"); profile_payload.pop("profile_hash")
    await blobs.put(canonical_json(profile_payload).encode(), identity)
    payload_ref = await _payload(blobs, identity)

    async def invoke(logical: str):
        return await adapter.complete_with_effect(
            role="evidence_candidate_extract", payload_ref=payload_ref,
            max_output_tokens=256,
            stable_call_id=hashlib.sha256(logical.encode()).hexdigest(),
            execution_identity=identity, logical_effect_id=logical,
        )

    first_logical = "v6-extract:root:checkpoint-1:task-extract:wg:page:r0"
    assert (await invoke(first_logical)).status == "validated"
    assert (await invoke(first_logical)).status == "validated"
    denied = await invoke("v6-extract:root:checkpoint-1:task-extract:wg:page-2:r0")
    assert denied.status == "budget_denied"
    assert calls == 1
    async with aiosqlite.connect(database) as db:
        budget = await (await db.execute(
            "SELECT reserved,consumed FROM workflow_research_resource_budgets "
            "WHERE run_id=? AND resource_kind='llm'", (identity.run_id,),
        )).fetchone()
    assert budget == (0, 1)


@pytest.mark.asyncio
async def test_v6_deadline_denial_is_durable_no_result_and_never_calls_provider(tmp_path) -> None:
    database, identity, journal, blobs, resolve = await _runtime(tmp_path)
    calls = 0

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("expired deadline must fence provider transport")

    adapter = _adapter(
        journal=journal, blobs=blobs, resolve=resolve, llm_call=complete,
        deadline_observer=lambda _identity: False,
    )
    profile_payload = adapter.profile.to_json()
    profile_payload.pop("profile_id"); profile_payload.pop("profile_hash")
    await blobs.put(canonical_json(profile_payload).encode(), identity)
    payload_ref = await _payload(blobs, identity)
    logical = "v6-extract:root:checkpoint-1:task-extract:wg:page:r0"
    kwargs = dict(
        role="evidence_candidate_extract", payload_ref=payload_ref,
        max_output_tokens=256,
        stable_call_id=hashlib.sha256(logical.encode()).hexdigest(),
        execution_identity=identity, logical_effect_id=logical,
    )
    first = await adapter.complete_with_effect(**kwargs)
    replay = await adapter.complete_with_effect(**kwargs)
    assert first.status == replay.status == "deadline"
    assert first.result is replay.result is None
    assert first.result_ref is replay.result_ref is None
    assert calls == 0
    async with aiosqlite.connect(database) as db:
        row = await (await db.execute(
            "SELECT status,outcome_json FROM workflow_effects WHERE effect_id=?",
            (first.effect_id,),
        )).fetchone()
    assert row[0] == "failed"
    assert json.loads(row[1])["error"]["code"] == "deadline"
