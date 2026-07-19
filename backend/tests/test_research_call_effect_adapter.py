from __future__ import annotations

import asyncio
import json

import aiosqlite
import pytest

from deskpet.workflows.adapters.research_runtime import (
    BoundResearchEffectContext,
    DurableResearchCallEffectAdapter,
    DurableResearchLLMStagePort,
    DurableResearchReadEffectAdapter,
    DurableResearchReadStagePort,
    MonotonicResearchClock,
    ResearchEffectCancelled,
    ResearchEffectAdapterError,
    WorkflowControlSignalHub,
)
from deskpet.workflows.contracts import NodeExecutionIdentity
from deskpet.workflows.definitions.deep_research_v5_contracts import ResearchLLMResult
from deskpet.workflows.definitions.research_core import ResearchLLMPortV2
from deskpet.workflows.effects import EffectExecutionContext, EffectJournal, EffectStateConflict
from deskpet.workflows.store import RegisteredBlobStore, WorkflowRunStore


async def _runtime(tmp_path, *, clock=None):
    database = tmp_path / "workflow.db"
    store = WorkflowRunStore(database, **({"clock": clock} if clock else {}))
    run_id, _ = await store.create_run(
        request_key="research-effect",
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name="deep_research",
        workflow_version="v5",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="research-budget",
        capability_snapshot={
            "research_llm_budget": {
                "max_input_tokens": 10_000,
                "max_output_tokens": 10_000,
                "max_cost_micros": 100_000,
            },
            "research_io_budget": {
                "max_input_tokens": 100,
                "max_output_tokens": 0,
                "max_cost_micros": 0,
            },
        },
        state_schema_version=5,
    )
    fence = await store.claim(run_id, "runner")
    identity = NodeExecutionIdentity(
        workflow_name="deep_research",
        workflow_version="v5",
        thread_id=run_id,
        run_id=run_id,
        checkpoint_id="checkpoint-1",
        checkpoint_ns="root",
        task_id="task-quality",
        node_id="quality_audit",
        attempt=1,
    )
    journal = EffectJournal(database, **({"clock": clock} if clock else {}))
    context = EffectExecutionContext(
        journal=journal,
        fence=fence,
        node_execution_id="node-execution-quality",
        workflow_name=identity.workflow_name,
        workflow_version=identity.workflow_version,
        node_id=identity.node_id,
    )
    blobs = RegisteredBlobStore(
        tmp_path / "blobs",
        database,
        **({"clock": clock} if clock else {}),
    )
    binding = [BoundResearchEffectContext(identity, context)]

    async def resolve(requested):
        assert requested is identity
        return binding[0]

    return database, store, identity, journal, blobs, binding, resolve


async def _payload(blobs, identity, value="durable prompt") -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    ref = await blobs.put(raw, identity, media_type="application/json")
    return f"sha256:{ref.sha256}"


def _adapter(*, journal, blobs, llm_call, resolve, fault=None, signals=None):
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
        fault_injector=fault,
        control_signals=signals,
    )


def test_llm_and_read_effect_adapters_have_distinct_public_apis_and_safe_mro():
    assert DurableResearchCallEffectAdapter.__bases__ == (object,)
    assert DurableResearchReadEffectAdapter.__bases__ == (object,)
    assert callable(DurableResearchCallEffectAdapter.__call__)
    assert not hasattr(DurableResearchCallEffectAdapter, "execute")
    assert callable(DurableResearchReadEffectAdapter.execute)
    assert "__call__" not in DurableResearchReadEffectAdapter.__dict__


def test_monotonic_research_clock_never_double_counts_or_moves_backward() -> None:
    now = [100.0]
    clock = MonotonicResearchClock(monotonic=lambda: now[0])
    assert clock.active_seconds(operation_id="op", accumulated_active_seconds=0.0) == 0.0
    now[0] = 169.0
    assert clock.active_seconds(operation_id="op", accumulated_active_seconds=0.0) == 69.0
    now[0] = 170.0
    assert clock.active_seconds(operation_id="op", accumulated_active_seconds=69.0) == 70.0
    assert clock.active_seconds(operation_id="op", accumulated_active_seconds=70.0) == 70.0

    restored_now = [500.0]
    restored = MonotonicResearchClock(monotonic=lambda: restored_now[0])
    assert restored.active_seconds(operation_id="op", accumulated_active_seconds=69.0) == 69.0
    restored_now[0] = 501.0
    assert restored.active_seconds(operation_id="op", accumulated_active_seconds=69.0) == 70.0


@pytest.mark.asyncio
async def test_v5_stage_ports_bind_native_identity_and_only_return_json_objects(tmp_path):
    _, _, identity, journal, blobs, _, resolve = await _runtime(tmp_path)
    seen: dict[str, object] = {}

    async def complete(prompt, *, max_output_tokens, stable_call_id):
        seen["prompt"] = json.loads(prompt)
        seen["stable_call_id"] = stable_call_id
        return ResearchLLMResult(
            '{"route":"persist","quality_score":88}', "test-model", 12, 8, 0,
            "provider", "request-stage",
        )

    llm_stage = DurableResearchLLMStagePort(
        blobs=blobs,
        effect=_adapter(
            journal=journal, blobs=blobs, llm_call=complete, resolve=resolve
        ),
    )
    result = await llm_stage.execute(
        stage="quality_audit", payload={"safe": True}, identity=identity
    )
    assert result == {"route": "persist", "quality_score": 88}
    assert seen["prompt"]["role"] == "quality_audit"
    assert "at most one affected dimension" in seen["prompt"]["instruction"]
    assert str(seen["stable_call_id"]).startswith("v5-quality_audit-")


@pytest.mark.asyncio
async def test_v5_modeling_prompt_forbids_cross_stage_payloads(tmp_path):
    _, _, identity, journal, blobs, _, resolve = await _runtime(tmp_path)
    seen: dict[str, object] = {}

    async def complete(
        prompt, *, max_output_tokens, stable_call_id, response_format=None
    ):
        seen.update(json.loads(prompt))
        seen["response_format"] = response_format
        return ResearchLLMResult(
            '{"modeling_output":{}}', "test-model", 8, 4, 0,
            "provider", "request-modeling",
        )

    stage = DurableResearchLLMStagePort(
        blobs=blobs,
        effect=_adapter(
            journal=journal, blobs=blobs, llm_call=complete, resolve=resolve
        ),
    )
    assert await stage.execute(
        stage="modeling", payload={"topic": "education"}, identity=identity
    ) == {"modeling_output": {}}
    instruction = str(seen["instruction"])
    assert "importance (enum: core or supporting)" in instruction
    assert "dimensions (3 to 8 objects)" in instruction
    assert "Do not return queries, analyses, claims" in instruction
    assert "profile, locale, geography" in instruction
    response_format = seen["response_format"]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    properties = schema["properties"]["modeling_output"]["properties"]
    assert set(properties) == {"subjects", "expected_decision", "dimensions"}
    assert properties["dimensions"]["minItems"] == 3
    assert properties["dimensions"]["maxItems"] == 8
    assert properties["dimensions"]["items"]["properties"]["importance"]["enum"] == [
        "core",
        "supporting",
    ]

    async def transport(payload, requested_identity):
        assert requested_identity is identity
        return {"stage": payload["_stage"], "ok": True}

    read_stage = DurableResearchReadStagePort(
        effect=DurableResearchReadEffectAdapter(
            journal=journal,
            blobs=blobs,
            resolve_effect_context=resolve,
            transport=transport,
            control_signals=WorkflowControlSignalHub(),
        ),
        effect_name="search",
    )
    assert await read_stage.execute(
        stage="search", payload={"query_count": 1}, identity=identity
    ) == {"stage": "search", "ok": True}


@pytest.mark.asyncio
async def test_v5_report_synthesis_uses_strict_contract_schema(tmp_path):
    _, _, identity, journal, blobs, _, resolve = await _runtime(tmp_path)
    seen: dict[str, object] = {}

    async def complete(
        prompt, *, max_output_tokens, stable_call_id, response_format=None
    ):
        seen["prompt"] = json.loads(prompt)
        seen["response_format"] = response_format
        return ResearchLLMResult(
            '{"analyses":[],"claims":[]}',
            "test-model",
            8,
            4,
            0,
            "provider",
            stable_call_id,
        )

    stage = DurableResearchLLMStagePort(
        blobs=blobs,
        effect=_adapter(
            journal=journal,
            blobs=blobs,
            llm_call=complete,
            resolve=resolve,
        ),
    )

    assert await stage.execute(
        stage="report_synthesis",
        payload={"research_brief": {}, "evidence_candidates": []},
        identity=identity,
    ) == {"analyses": [], "claims": []}
    response_format = seen["response_format"]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    assert set(schema["properties"]) == {"analyses", "claims"}
    analysis = schema["properties"]["analyses"]["items"]
    claim = schema["properties"]["claims"]["items"]
    assert analysis["additionalProperties"] is False
    assert claim["additionalProperties"] is False
    assert "winning_evidence_ids" in analysis["required"]
    assert "supported_fact_refs" in claim["required"]
    assert seen["prompt"]["json_schema"] == schema
    assert "3 to 7 key_judgment" in seen["prompt"]["instruction"]
    assert "current_state" in seen["prompt"]["instruction"]
    assert "span_text vocabulary" in seen["prompt"]["instruction"]


@pytest.mark.asyncio
async def test_modeling_repair_uses_distinct_committed_effect_and_replays_without_resend(
    tmp_path,
):
    database, _, identity, journal, blobs, _, resolve = await _runtime(tmp_path)
    calls: list[str] = []

    async def complete(
        prompt, *, max_output_tokens, stable_call_id, response_format=None
    ):
        payload = json.loads(prompt)
        calls.append(stable_call_id)
        assert response_format["type"] == "json_schema"
        content = (
            '{"modeling_output":{"profile":"drifted","dimensions":[]}}'
            if payload["input"].get("repair_attempt") is None
            else '{"modeling_output":{"subjects":["AI"],"expected_decision":"rank",'
            '"dimensions":[]}}'
        )
        return ResearchLLMResult(
            content, "test-model", 20, 8, 0, "provider", stable_call_id
        )

    stage = DurableResearchLLMStagePort(
        blobs=blobs,
        effect=_adapter(
            journal=journal,
            blobs=blobs,
            llm_call=complete,
            resolve=resolve,
        ),
    )
    primary_payload = {"topic": "AI ranking", "operation_id": "op"}
    repair_payload = {
        **primary_payload,
        "repair_attempt": 1,
        "validation_error": "invalid modeling profile",
        "previous_modeling_output": {"profile": "drifted", "dimensions": []},
    }
    await stage.execute(stage="modeling", payload=primary_payload, identity=identity)
    await stage.execute(stage="modeling", payload=repair_payload, identity=identity)
    await stage.execute(stage="modeling", payload=primary_payload, identity=identity)
    await stage.execute(stage="modeling", payload=repair_payload, identity=identity)

    assert len(calls) == 2
    assert calls[0] != calls[1]
    async with aiosqlite.connect(database) as db:
        effects = await (
            await db.execute(
                "SELECT COUNT(*), COUNT(DISTINCT effect_id) FROM workflow_effects"
            )
        ).fetchone()
        reservations = await (
            await db.execute(
                "SELECT COUNT(*), SUM(status='committed') FROM workflow_effect_budget_reservations"
            )
        ).fetchone()
    assert effects == (2, 2)
    assert reservations == (2, 2)


@pytest.mark.asyncio
async def test_success_is_blob_backed_committed_and_reused_without_transport(tmp_path):
    database, _, identity, journal, blobs, binding, resolve = await _runtime(tmp_path)
    calls = 0

    async def complete(prompt, *, max_output_tokens, stable_call_id):
        nonlocal calls
        calls += 1
        assert prompt == "durable prompt"
        assert (max_output_tokens, stable_call_id) == (64, "audit-1")
        return ResearchLLMResult(
            "durable answer", "model-a", 12, 5, 2, "provider", "request-1"
        )

    adapter = _adapter(
        journal=journal, blobs=blobs, llm_call=complete, resolve=resolve
    )
    payload_ref = await _payload(blobs, identity)
    kwargs = {
        "role": "quality_audit",
        "payload_ref": payload_ref,
        "max_output_tokens": 64,
        "stable_call_id": "audit-1",
        "execution_identity": identity,
    }

    first = await adapter(**kwargs)
    replay = await adapter(**kwargs)

    assert first == replay
    assert calls == 1
    ledger = await journal.rebuild_budget_ledger(
        binding[0].effect_context.fence,
        ledger_kind="llm",
    )
    assert (ledger.charged_input_tokens, ledger.charged_output_tokens) == (12, 5)
    assert ledger.reservations[0].status == "committed"
    async with aiosqlite.connect(database) as db:
        row = await (
            await db.execute(
                "SELECT policy_json,outcome_json FROM workflow_effects"
            )
        ).fetchone()
    policy = json.loads(row[0])
    outcome = json.loads(row[1])["value"]
    assert policy["kind"] == "opaque_manual"
    assert policy["max_attempts"] == 1
    assert outcome["model"] == "model-a"
    assert outcome["usage"] == {
        "cache_tokens": 2,
        "input_tokens": 12,
        "output_tokens": 5,
    }
    assert ResearchLLMResult.from_json(
        json.loads((await blobs.get(outcome["result_ref"].split(":", 1)[1])).decode())
    ) == first


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        "modeling",
        "query_strategy",
        "dimension_analysis",
        "report_synthesis",
        "quality_audit",
        "targeted_repair",
    ],
)
async def test_all_six_llm_roles_share_the_same_pre_dispatch_cancel_fence(tmp_path, role):
    database, _, identity, journal, blobs, _, resolve = await _runtime(tmp_path)
    hub = WorkflowControlSignalHub()
    hub.notify_cancel(identity.run_id, command_id="cancel-all-llm")
    calls = 0

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("cancelled LLM transport must not run")

    adapter = _adapter(
        journal=journal,
        blobs=blobs,
        llm_call=complete,
        resolve=resolve,
        signals=hub,
    )
    payload_ref = await _payload(blobs, identity)
    with pytest.raises(ResearchEffectCancelled, match="before provider"):
        await adapter(
            role=role,
            payload_ref=payload_ref,
            max_output_tokens=64,
            stable_call_id=f"{role}-cancelled",
            execution_identity=identity,
        )
    async with aiosqlite.connect(database) as db:
        row = await (
            await db.execute(
                "SELECT status,dispatch_state FROM workflow_effect_budget_reservations"
            )
        ).fetchone()
    assert row == ("released", "not_started")
    assert calls == 0


@pytest.mark.asyncio
async def test_resolver_identity_or_fence_mismatch_hard_rejects_before_begin(tmp_path):
    database, _, identity, journal, blobs, binding, resolve = await _runtime(tmp_path)
    calls = 0

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    adapter = _adapter(
        journal=journal, blobs=blobs, llm_call=complete, resolve=resolve
    )
    payload_ref = await _payload(blobs, identity)
    other = NodeExecutionIdentity(
        **{**identity.__dict__, "checkpoint_id": "different-checkpoint"}
    )
    binding[0] = BoundResearchEffectContext(other, binding[0].effect_context)

    with pytest.raises(EffectStateConflict, match="checkpoint/task/attempt"):
        await adapter(
            role="quality_audit",
            payload_ref=payload_ref,
            max_output_tokens=64,
            stable_call_id="audit-mismatch",
            execution_identity=identity,
        )
    async with aiosqlite.connect(database) as db:
        count = await (await db.execute("SELECT COUNT(*) FROM workflow_effects")).fetchone()
    assert count == (0,)
    assert calls == 0


@pytest.mark.asyncio
async def test_invalid_payload_releases_reserved_budget_before_transport(tmp_path):
    database, _, identity, journal, blobs, _, resolve = await _runtime(tmp_path)
    calls = 0

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    adapter = _adapter(
        journal=journal, blobs=blobs, llm_call=complete, resolve=resolve
    )
    payload_ref = await _payload(blobs, identity, b'{"not_prompt":"value"}')
    with pytest.raises(ValueError, match="JSON with prompt"):
        await adapter(
            role="quality_audit",
            payload_ref=payload_ref,
            max_output_tokens=64,
            stable_call_id="invalid-payload",
            execution_identity=identity,
        )
    async with aiosqlite.connect(database) as db:
        row = await (
            await db.execute(
                """SELECT reservation.status,reservation.dispatch_state,effect.status
                FROM workflow_effect_budget_reservations reservation
                JOIN workflow_effects effect USING(effect_id)"""
            )
        ).fetchone()
    assert row == ("released", "not_started", "failed")
    assert calls == 0


@pytest.mark.asyncio
async def test_transport_without_usage_is_held_and_never_retried(tmp_path):
    database, _, identity, journal, blobs, _, resolve = await _runtime(tmp_path)
    calls = 0

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("ambiguous provider timeout")

    adapter = _adapter(
        journal=journal, blobs=blobs, llm_call=complete, resolve=resolve
    )
    payload_ref = await _payload(blobs, identity)
    kwargs = {
        "role": "quality_audit",
        "payload_ref": payload_ref,
        "max_output_tokens": 64,
        "stable_call_id": "timeout",
        "execution_identity": identity,
    }
    with pytest.raises(TimeoutError):
        await adapter(**kwargs)
    with pytest.raises(ResearchEffectAdapterError, match="will not be sent again"):
        await adapter(**kwargs)

    async with aiosqlite.connect(database) as db:
        row = await (
            await db.execute(
                """SELECT reservation.status,reservation.dispatch_state,effect.status
                FROM workflow_effect_budget_reservations reservation
                JOIN workflow_effects effect USING(effect_id)"""
            )
        ).fetchone()
    assert row == ("held_uncertain", "started", "uncertain")
    assert calls == 1


@pytest.mark.asyncio
async def test_crash_before_transport_is_released_after_fenced_takeover(tmp_path):
    now = [100.0]
    database, store, identity, journal, blobs, binding, resolve = await _runtime(
        tmp_path, clock=lambda: now[0]
    )
    calls = 0

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("transport must not run")

    class InjectedCrash(BaseException):
        pass

    def crash(stage):
        if stage == "research_llm.after_begin_before_upstream":
            raise InjectedCrash()

    payload_ref = await _payload(blobs, identity)
    kwargs = {
        "role": "quality_audit",
        "payload_ref": payload_ref,
        "max_output_tokens": 64,
        "stable_call_id": "crash-before-transport",
        "execution_identity": identity,
    }
    crashing = _adapter(
        journal=journal,
        blobs=blobs,
        llm_call=complete,
        resolve=resolve,
        fault=crash,
    )
    with pytest.raises(InjectedCrash):
        await crashing(**kwargs)

    now[0] = 200.0
    replacement = await store.claim(identity.run_id, "replacement")
    binding[0] = BoundResearchEffectContext(
        identity,
        EffectExecutionContext(
            journal=journal,
            fence=replacement,
            node_execution_id="node-execution-quality",
            workflow_name=identity.workflow_name,
            workflow_version=identity.workflow_version,
            node_id=identity.node_id,
        ),
    )
    recovered = _adapter(
        journal=journal, blobs=blobs, llm_call=complete, resolve=resolve
    )
    with pytest.raises(ResearchEffectAdapterError, match="will not be sent again"):
        await recovered(**kwargs)

    async with aiosqlite.connect(database) as db:
        row = await (
            await db.execute(
                "SELECT status,dispatch_state FROM workflow_effect_budget_reservations"
            )
        ).fetchone()
    assert row == ("released", "not_started")
    assert calls == 0


@pytest.mark.asyncio
async def test_usage_unknown_result_persists_outcome_but_remains_held(tmp_path):
    database, _, identity, journal, blobs, _, resolve = await _runtime(tmp_path)

    async def complete(*_args, **_kwargs):
        return ResearchLLMResult(
            "answer", "model-unknown", None, None, None, "usage_unknown", None
        )

    adapter = _adapter(
        journal=journal, blobs=blobs, llm_call=complete, resolve=resolve
    )
    payload_ref = await _payload(blobs, identity)
    with pytest.raises(ResearchEffectAdapterError, match="unknown usage"):
        await adapter(
            role="quality_audit",
            payload_ref=payload_ref,
            max_output_tokens=64,
            stable_call_id="unknown-usage",
            execution_identity=identity,
        )

    async with aiosqlite.connect(database) as db:
        row = await (
            await db.execute(
                """SELECT reservation.status,effect.status,effect.outcome_json
                FROM workflow_effect_budget_reservations reservation
                JOIN workflow_effects effect USING(effect_id)"""
            )
        ).fetchone()
    outcome = json.loads(row[2])["value"]
    assert row[:2] == ("held_uncertain", "uncertain")
    assert outcome["model"] == "model-unknown"
    assert outcome["usage_source"] == "usage_unknown"
    assert await blobs.get(outcome["result_ref"].split(":", 1)[1])


@pytest.mark.asyncio
async def test_crash_after_response_blob_does_not_resend_after_owner_takeover(tmp_path):
    now = [100.0]
    database, store, identity, journal, blobs, binding, resolve = await _runtime(
        tmp_path, clock=lambda: now[0]
    )
    calls = 0

    async def complete(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return ResearchLLMResult("answer", "model-a", 8, 3, 0, "provider", None)

    class InjectedCrash(BaseException):
        pass

    def crash(stage):
        if stage == "research_llm.after_result_blob_before_effect_commit":
            raise InjectedCrash()

    adapter = _adapter(
        journal=journal,
        blobs=blobs,
        llm_call=complete,
        resolve=resolve,
        fault=crash,
    )
    payload_ref = await _payload(blobs, identity)
    kwargs = {
        "role": "quality_audit",
        "payload_ref": payload_ref,
        "max_output_tokens": 64,
        "stable_call_id": "crash-after-response",
        "execution_identity": identity,
    }
    with pytest.raises(InjectedCrash):
        await adapter(**kwargs)
    with pytest.raises(ResearchEffectAdapterError, match="transport is blocked"):
        await adapter(**kwargs)

    now[0] = 200.0
    replacement = await store.claim(identity.run_id, "replacement")
    binding[0] = BoundResearchEffectContext(
        identity,
        EffectExecutionContext(
            journal=journal,
            fence=replacement,
            node_execution_id="node-execution-quality",
            workflow_name=identity.workflow_name,
            workflow_version=identity.workflow_version,
            node_id=identity.node_id,
        ),
    )
    recovered = _adapter(
        journal=journal, blobs=blobs, llm_call=complete, resolve=resolve
    )
    with pytest.raises(ResearchEffectAdapterError, match="will not be sent again"):
        await recovered(**kwargs)

    async with aiosqlite.connect(database) as db:
        row = await (
            await db.execute(
                "SELECT status,dispatch_state FROM workflow_effect_budget_reservations"
            )
        ).fetchone()
    assert row == ("held_uncertain", "started")
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("effect_name", ["search", "static_fetch", "playwright", "edge"])
async def test_read_effect_cancel_before_dispatch_releases_without_transport(
    tmp_path, effect_name
):
    database, _, identity, journal, blobs, _, resolve = await _runtime(tmp_path)
    hub = WorkflowControlSignalHub()
    hub.notify_cancel(identity.run_id, command_id="cancel-before")
    calls = 0

    async def transport(_payload, _identity):
        nonlocal calls
        calls += 1
        return {"body": "must not run"}

    adapter = DurableResearchReadEffectAdapter(
        journal=journal,
        blobs=blobs,
        resolve_effect_context=resolve,
        transport=transport,
        control_signals=hub,
    )
    with pytest.raises(ResearchEffectCancelled, match="before dispatch"):
        await adapter.execute(
            effect_name=effect_name,
            payload={"url": "https://example.test"},
            stable_call_id=f"{effect_name}-before",
            execution_identity=identity,
        )
    async with aiosqlite.connect(database) as db:
        row = await (
            await db.execute(
                "SELECT status,dispatch_state FROM workflow_effect_budget_reservations"
            )
        ).fetchone()
    assert row == ("released", "not_started")
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("effect_name", ["search", "static_fetch", "playwright", "edge"])
async def test_read_effect_cancel_after_dispatch_holds_rejects_late_and_never_restarts(
    tmp_path, effect_name
):
    database, _, identity, journal, blobs, _, resolve = await _runtime(tmp_path)
    hub = WorkflowControlSignalHub(poll_interval=0.005)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def transport(_payload, _identity):
        nonlocal calls
        calls += 1
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return {"body": "late outcome"}

    adapter = DurableResearchReadEffectAdapter(
        journal=journal,
        blobs=blobs,
        resolve_effect_context=resolve,
        transport=transport,
        control_signals=hub,
    )
    task = asyncio.create_task(
        adapter.execute(
            effect_name=effect_name,
            payload={"url": "https://example.test"},
            stable_call_id=f"{effect_name}-after",
            execution_identity=identity,
        )
    )
    await started.wait()
    hub.notify_cancel(identity.run_id, command_id="cancel-after")
    with pytest.raises(ResearchEffectCancelled, match="after dispatch"):
        await task
    release.set()
    await asyncio.sleep(0)
    with pytest.raises(ResearchEffectAdapterError, match="will not be restarted"):
        await adapter.execute(
            effect_name=effect_name,
            payload={"url": "https://example.test"},
            stable_call_id=f"{effect_name}-after",
            execution_identity=identity,
        )
    async with aiosqlite.connect(database) as db:
        row = await (
            await db.execute(
                """SELECT reservation.status,reservation.dispatch_state,effect.status,
                effect.outcome_json FROM workflow_effect_budget_reservations reservation
                JOIN workflow_effects effect USING(effect_id)"""
            )
        ).fetchone()
    assert row[:3] == ("held_uncertain", "started", "uncertain")
    assert "late outcome" not in row[3]
    assert calls == 1
