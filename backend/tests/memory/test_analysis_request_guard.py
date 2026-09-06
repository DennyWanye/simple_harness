"""Production resolver/physical HTTP adapter, real Host attempts and public Memory.

Local MockTransport is the physical-send oracle; the prior foreground terminal
is the existing deterministic Host terminal fixture, not a new native run.
"""
import asyncio
from dataclasses import replace
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from simple_harness import RequestId, thaw_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import CancelToken, ProviderRequest, ProviderRequestRejectedError
from simple_harness_memory import MemoryManager, MemoryPrincipal, SuppressionRequest, SuppressionScopeKind

from deskpet.memory.analysis_request_guard import AnalysisPhysicalRequestGuard
from deskpet.memory.control_binding import HumanMemoryControlBinding
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.memory.trusted_disclosure import TrustedDisclosureStore, current_record_tx
from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
from tests.companion.test_window_control_credentials import _ingress
from tests.memory.test_primary_control_binding import _bind
from tests.sdk_adapters import s5b_memory_harness as mh, s5b_closure_harness as ch
from tests.execution import test_foreground_queue as fq
from tests.sdk_adapters.test_product_host_ports import Registry


@pytest_asyncio.fixture
async def world(tmp_path, monkeypatch):
    import main
    private, _, _, ingress = _ingress(tmp_path)
    control = HumanMemoryControlBinding()
    challenge = await _bind(private, ingress, control)
    auth = control.authenticate(ingress, challenge)
    for module in (mh, ch, fq):
        monkeypatch.setattr(module, "SUBJECT", auth.subject)
    original_draft = fq._draft
    async def draft_for_authenticated_subject(store, **kwargs):
        return await original_draft(store, subject=auth.subject, **kwargs)
    monkeypatch.setattr(fq, "_draft", draft_for_authenticated_subject)
    env = await mh.bound_turn_run(tmp_path, "analysis-origin", text="My preferred drink is coffee.")
    registry = Registry("test-not-a-real-key")
    registry.entry.id = "provider-1"
    registry.entry.incarnation_id = "inc-1"
    registry.entry.config_revision = 1
    registry.entry.model = "model-1"
    registry.entry.models = ("model-1",)
    monkeypatch.setattr(main, "_sdk_price_snapshot", lambda *_: (1, 1, "price-v1"))
    w = SimpleNamespace(env=env, sent=[], captured=[], transform=None, runtime=None,
        control=control, challenge=challenge, ingress=ingress, auth=auth)
    async def transport(request):
        w.sent.append(json.loads(request.content))
        op = mh.semantic_op(mh.item_id(w.env), w.env.envelope.sanitized_payload["text"],
                            predicate="preferred_drink", object_value="coffee")
        return httpx.Response(200, json={"id": "physical-analysis", "model": "model-1",
            "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
                {"id": "proposal-1", "type": "function", "function": {"name": mh.PROPOSAL_TOOL_NAME,
                    "arguments": json.dumps({"outcome": "mutate", "operations": [op]})}}]},
                "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    resolver = main._ProductSdkProviderBindingResolver(registry, client)
    w.resolver = resolver
    record = {**mh.BINDING, "run_id": env.run_id}
    binding = SdkRunBindingV1.from_record(record)
    endpoint = resolver.build_authority(binding).provider.target.endpoint_identity
    facts = ch.FakeRunFacts(env.run_id, binding=record)
    observed = await ch.observe_terminal(env, facts)
    await mh.record_terminal(env, observed, binding=record, endpoint=endpoint)

    async def public_builder(path, **kwargs):
        return await MemoryManager.build_human_memory_v7(path, **kwargs, allow_development_embedder=True)

    def factory(record):
        bound = SdkRunBindingV1.from_record(record)
        guard = AnalysisPhysicalRequestGuard(env.db_path, binding=bound, runtime_getter=lambda: w.runtime)
        provider = resolver.build_authority(bound, request_guard=guard).provider
        class Boundary:
            def __getattr__(self, name):
                return getattr(provider, name)
            async def invoke(self, request, *, cancel):
                w.captured.append(request)
                if w.transform:
                    request = await w.transform(request, guard)
                return await provider.invoke(request, cancel=cancel)
        return Boundary()

    def reopen():
        w.runtime = compose_human_memory_runtime(env.db_path, env.db_path.parent / "guard-memory.db",
            adapter_factory=factory, clock=env.clock, backend_factory=public_builder,
            principal=MemoryPrincipal("deskpet-local", "deskpet-local-household", auth.subject, "primary-conversation"))
        w.menv = mh.MemoryEnv(executor=w.runtime.analysis_authority, runtime=w.runtime,
            worker=MemoryIngestionOutboxWorker(env.db_path, w.runtime.manager, owner_id="guard-worker",
                                             clock=env.clock, retry_delays=(1.0, 2.0)),
            config=mh.build_worker_config(provider_id=binding.provider_id, model_id=binding.model_id,
                model_config_hash=mh.expected_model_config_hash(record, endpoint=endpoint)),
            adapter=None, clock=env.clock, worker_id="guard-analysis", runner=None)
    w.reopen = reopen
    reopen()
    try:
        assert await w.menv.worker.run_once() == "delivered"
        yield w
    finally:
        await w.runtime.close()
        await client.aclose()


def attempt_rows(w):
    with sqlite3.connect(w.env.db_path) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute("SELECT * FROM post_turn_invocation_attempts WHERE purpose='analysis'")]


@pytest.mark.asyncio
async def test_real_resolver_send_materializes_and_durable_reopen_never_resends(world):
    w = world
    assert await mh.run_job(w.menv) == "applied"
    assert len(w.sent) == 1
    rows = attempt_rows(w)
    assert len(rows) == 1 and rows[0]["status"] == "succeeded"
    from deskpet.memory.analysis_request_guard import read_observation_tx
    from deskpet.memory.analysis_proposal import stable_id
    import aiosqlite
    async with aiosqlite.connect(w.env.db_path) as db:
        db.row_factory = aiosqlite.Row
        _, carrier = await read_observation_tx(db, subject=w.auth.subject,
            identity=stable_id("analysis-attempt-input", rows[0]["attempt_id"]))
    from simple_harness import MemoryAnalysisRequest
    request = MemoryAnalysisRequest.from_json(carrier["analysis_request"])
    authority = w.runtime._memory_action_authority
    # Same durable succeeded response/reopen requires no new physical call.
    first = await w.runtime.analysis_authority.analyze_memory(request)
    await w.runtime.close()
    w.reopen()
    assert await w.runtime.analysis_authority.analyze_memory(request) == first
    assert len(w.sent) == 1 and attempt_rows(w) == rows
    found = await w.runtime.typed_recall(query="coffee", run_id="verify-materialization",
                                      turn_ordinal=1, now=float(w.env.clock()))
    assert any(i.public_payload["object_value"] == "coffee" for i in found.execution.result.items)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["text", "temperature", "metadata", "tool", "request_id", "run", "snapshot"])
async def test_exact_physical_input_or_origin_mismatch_denies_zero_send(world, change):
    w = world
    if change == "snapshot":
        original_bind = w.runtime._memory_action_authority.bind_attempt
        async def mixed(analysis_request, row, snapshot, provider_request, *, db):
            # Self-consistent incoming hashes cannot replace the actual saved
            # candidate snapshot and the real attempt's evidence-set identity.
            await original_bind(analysis_request, row,
                {**snapshot, "evidence_set_key": "0" * 64}, provider_request, db=db)
        w.runtime._memory_action_authority.bind_attempt = mixed
    async def tamper(request, guard):
        if change == "text":
            return replace(request, messages=(*request.messages[:-1], Message(MessageRole.USER, "different bytes")))
        if change == "temperature":
            return replace(request, temperature=0.5)
        if change == "metadata":
            return replace(request, metadata={"foreign": True})
        if change == "tool":
            return replace(request, tools=(replace(request.tools[0], description="different schema contract",
                parameters=thaw_json(request.tools[0].parameters)),))
        if change == "request_id":
            return replace(request, request_id=RequestId("foreign-request"))
        if change == "run":
            guard.binding = guard.binding.replace(run_id="foreign-run")
        return request
    w.transform = tamper
    assert await mh.run_job(w.menv) == "retry_scheduled"
    assert w.sent == []
    row = attempt_rows(w)[0]
    assert row["status"] == "failed" and row["reason_code"] == "provider_analysis_request_disclosure_rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["disclosure", "source_forget", "candidate_forget"])
async def test_slow_visibility_then_current_change_denies_before_transport(world, change):
    w = world
    candidate = None
    if change == "candidate_forget":
        assert await mh.run_job(w.menv) == "applied"
        recalled = await w.runtime.typed_recall(query="coffee", run_id="candidate-setup", turn_ordinal=1,
                                               now=float(w.env.clock()))
        candidate = recalled.execution.result.items[0].selected_item.source_ref
        w.env = await mh.next_turn_run(w.env, "second-analysis", text="My preferred drink is coffee.",
                                      delivery_key="second-source")
        record = {**mh.BINDING, "run_id": w.env.run_id}
        binding = SdkRunBindingV1.from_record(record)
        endpoint = w.resolver.build_authority(binding).provider.target.endpoint_identity
        facts = ch.FakeRunFacts(w.env.run_id, binding=record)
        await mh.record_terminal(w.env, await ch.observe_terminal(w.env, facts), binding=record, endpoint=endpoint)
        assert await w.menv.worker.run_once() == "delivered"
    baseline = len(w.sent)
    authority = w.runtime._memory_action_authority
    original, guard_entered, changed = authority.check, False, False
    async def arm(request, guard):
        nonlocal guard_entered
        guard_entered = True
        return request
    w.transform = arm
    async def slow(request, snapshot):
        nonlocal changed
        await original(request, snapshot)
        if not guard_entered or changed:
            return
        if change == "disclosure":
            import aiosqlite
            async with aiosqlite.connect(w.env.db_path) as db:
                db.row_factory = aiosqlite.Row
                head = await current_record_tx(db, w.auth.subject)
            with w.control.request_scope(w.ingress, w.challenge):
                updated = await TrustedDisclosureStore(w.env.db_path).configure(auth=w.auth, request_id="changed-policy",
                    expected_ref=None if head is None else head["binding_ref"], selection={"recipient": "user_self",
                    "recipient_id": w.auth.subject, "intended_audience": "user_self", "purpose": "task_execution"})
            assert updated["binding_ref"] != (None if head is None else head["binding_ref"])
            async with aiosqlite.connect(w.env.db_path) as db:
                db.row_factory = aiosqlite.Row
                committed = await current_record_tx(db, w.auth.subject)
            assert committed["binding_ref"] == updated["binding_ref"]
        else:
            manager = await w.runtime.manager()
            decision = await manager.suppress(principal=w.runtime.principal(), request=SuppressionRequest(
                "late-guard-forget", w.auth.subject,
                SuppressionScopeKind.MEMORY if candidate else SuppressionScopeKind.EVIDENCE,
                candidate or w.env.evidence_id, "user_forget", float(w.env.clock())))
            assert decision.request_id == "late-guard-forget"
            assert decision.subject == w.auth.subject
            assert decision.scope_ref == (candidate or w.env.evidence_id)
            assert decision.decision_hash
        # Only a successfully committed policy/suppression change satisfies this control.
        changed = True
    authority.check = slow
    assert await mh.run_job(w.menv) == "retry_scheduled"
    assert changed and len(w.sent) == baseline
    assert attempt_rows(w)[-1]["reason_code"] == "provider_analysis_request_disclosure_rejected"


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["before_commit", "after_commit"])
async def test_attempt_and_full_input_are_atomic_and_ack_loss_does_not_send(world, point):
    w = world
    authority = w.runtime._memory_action_authority
    if point == "before_commit":
        original = authority._save
        async def fail(identity, *args, **kwargs):
            await original(identity, *args, **kwargs)
            if identity.startswith("analysis-attempt-input"):
                raise OSError("controlled-before-commit")
        authority._save = fail
    else:
        def fail(point):
            if point == "attempt-reserved":
                raise OSError("controlled-after-commit")
        w.runtime.analysis_authority._fault_inject = fail
    assert await mh.run_job(w.menv) == "retry_scheduled"
    assert w.sent == []
    with sqlite3.connect(w.env.db_path) as db:
        attempts = db.execute("SELECT count(*) FROM post_turn_invocation_attempts").fetchone()[0]
        inputs = db.execute("SELECT count(*) FROM human_memory_evidence "
                            "WHERE evidence_id LIKE 'analysis-attempt-input%'").fetchone()[0]
    assert attempts == inputs == (0 if point == "before_commit" else 1)


@pytest.mark.asyncio
async def test_resolver_default_guard_and_explicit_invalid_guard_remain_closed(world):
    w = world
    binding = SdkRunBindingV1.from_record({**mh.BINDING, "run_id": w.env.run_id})
    for invalid in (None, False, "skip"):
        with pytest.raises(TypeError):
            w.resolver.build_authority(binding, request_guard=invalid)
    default = w.resolver.build_authority(binding).provider
    with pytest.raises(ProviderRequestRejectedError):
        await default.invoke(ProviderRequest(RequestId("not-a-foreground-request"),
            (Message(MessageRole.USER, "unchanged foreground gate"),)), cancel=CancelToken())
    assert w.sent == []
