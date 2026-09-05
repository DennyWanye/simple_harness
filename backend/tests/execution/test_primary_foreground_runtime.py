"""Real SDK ReAct/SQLite + Host FIFO/Context/Tool authority; deterministic provider only."""
import asyncio
import json
import sqlite3
from dataclasses import replace
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlparse
from types import SimpleNamespace

import pytest
from simple_harness import RuntimePorts, RuntimeProfile, SqliteContextPort
from simple_harness.contracts.messages import ContentBlock, Message, MessageRole
from simple_harness.execution.budget import BudgetPolicy, FrozenPriceEstimator
from simple_harness.execution.context_authority import DurableToolCatalogResolver
from simple_harness.execution.delivery import DeliveryDispatcher
from simple_harness.execution.dispatch import ProviderBinding, ProviderInvocationCoordinator
from simple_harness.providers import ProviderResponse, ProviderTarget, ProviderToolSpec, ProviderUsage
from simple_harness.providers.base import ProviderContinuationCapability, ProviderContinuationMode
from simple_harness.runtime import EffectBatchExecutor
from simple_harness.runtime.drivers import ReActDriver
from simple_harness.tools import EffectExecutor, FunctionTool, ToolRegistry, ToolSpec, ToolResult
from simple_harness.tools.authorization import AuthorizationDecision, AuthorizationResult
from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.execution.foreground_runtime import (
    BoundProviderAuthority, ForegroundRuntimeExecutionAuthority, FrozenProviderAuthority, SqliteSdkTerminalObserver,
)
from deskpet.execution.foreground_runtime_ports import ProductForegroundToolPort
from deskpet.execution.primary_context import PrimaryForegroundContextPort
from deskpet.execution.primary_history import PrimaryHistoryStore
from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.composition import ProductSdkRuntimeStack, SdkRuntimeBuildInputs
from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore, ProductRuntimeDecisionSink, ProductRunContextAuthority
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.sdk_adapters.ingress import SdkRuntimeIngress
from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
from deskpet.sdk_adapters.runtime_paths import ProductRuntimePathsAdapter
from deskpet.sdk_adapters.sdk_candidate import build_candidate_identity
from deskpet.sdk_adapters.tool_authority import SdkRunToolAuthorityRegistry
from deskpet.task_scope.protocol import canonical_hash
from deskpet.sdk_adapters.tools import ProductToolInventoryEntry


class Provider:
    target = ProviderTarget("fixture", "model", "model", "local", "fixture")
    def __init__(self):
        self.requests = []
    async def invoke(self, request, *, cancel):
        self.requests.append(request)
        return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, (
            ContentBlock("output_text", {"text": "Actual response " + str(len(self.requests)), "provider_private": "PRIVATE_CANARY"}),
            ContentBlock("reasoning", {"text": "HIDDEN_CANARY"}),
        )), model="model", usage=ProviderUsage(10, 10, 20), opaque_continuation_ref="fixture-opaque")


class ProviderPort:
    def __init__(self, binding):
        self.binding = binding
    async def freeze(self, *, claimed, sdk_run_id, **_):
        return FrozenProviderAuthority(claimed.host_run_id, sdk_run_id, claimed.owner_id, claimed.generation,
                                       "provider-fixture", "a" * 64, "fixture", "fixture-1", 1, 1, "model", {}, 32768)
    async def bind(self, *, frozen, context, tools, execution_session_id, request_id, sdk_run_id):
        record = SdkRunBindingV1.build(run_id=sdk_run_id, session_id=execution_session_id, request_id=request_id,
                                     snapshot_id=context.snapshot_id, provider_id="fixture", provider_incarnation_id="fixture-1",
                                     provider_config_revision=1, binding_epoch=1, model_id="model", model_params={},
                                     context_window=32768, catalog_generation=tools.catalog["generation"],
                                     catalog_fingerprint=tools.catalog["content_fingerprint"],
                                     budget_fingerprint=self.binding.budget_fingerprint)
        return BoundProviderAuthority(frozen.host_run_id, sdk_run_id, frozen.owner_id, frozen.generation,
                                      "provider-binding", record.binding_fingerprint, record)
    def mark_terminal(self, *_):
        pass


class Noop:
    async def reconcile(self):
        pass
    async def authorize(self, effect):
        return AuthorizationResult(AuthorizationDecision.ALLOW, receipt_ref="test-allow")
    async def bind_effect_handoff(self, prepared, authorization_receipt_ref, sdk_receipt):
        from simple_harness.tools import AuthorizationReceipt
        return AuthorizationReceipt("fixture-handoff", sdk_receipt.receipt_hash, sdk_receipt.receipt_hash)



async def build(tmp_path, state_path, provider, *, fault=None, memory=None, state_changed=None, legacy_observer=False, dynamic=False):
    from deskpet.execution.primary_context import ForegroundConversationEntrypoint
    from deskpet.memory.identity import ValidatedLocalMemoryIdentityAuthority
    from deskpet.memory.session_db import SessionDB
    from deskpet.sdk_adapters.context_provider import ProductConversationContextProvider
    from deskpet.sdk_adapters.context_source import ProductContextSourceRepository
    from simple_harness.execution.context_staging import ContextStagingRepository

    dynamic_factory = HumanMemoryHostServiceFactory(state_path, await dispatch_startup_epoch(state_path, approved_fresh_lane=True)) if dynamic else None
    sources = ProductContextSourceRepository(state_path)
    conversation = None if memory is None else ForegroundConversationEntrypoint(
        session_store=SessionDB(state_path),
        identity_authority=ValidatedLocalMemoryIdentityAuthority(state_path, user_data_dir=tmp_path),
        context_sources=sources,
    )
    binding = ProviderBinding(provider, FrozenPriceEstimator("fixture-prices", "model", 1, 1), BudgetPolicy(), ProviderContinuationCapability(ProviderContinuationMode.OPAQUE_REFERENCE))
    registry = SdkRunToolAuthorityRegistry()
    specs = [dict(name=name, description=name, input_schema={"type": "object", "properties": {}})
             for name in ("tool_search", "tool_describe", "tool_activate")]
    if dynamic:
        from deskpet.sdk_adapters.context_route import CONTEXT_ROUTE_SCHEMA
        from deskpet.sdk_adapters.task_scope_mutation import TASK_SCOPE_UPDATE_SCHEMA
        from tests.sdk_adapters.s5b_effect_gate_harness import WRITE_FILE_SCHEMA
        specs.extend(dict(name=name, description=name, input_schema=schema) for name, schema in (
            ("context_route", CONTEXT_ROUTE_SCHEMA), ("task_scope_update", TASK_SCOPE_UPDATE_SCHEMA),
            ("write_file", WRITE_FILE_SCHEMA)))
        for spec in specs:
            if spec["name"].startswith("tool_"):
                spec["input_schema"] = {"type": "object", "properties": {key: {"type": "string"} for key in ("query", "capability_id", "schema_hash", "describe_nonce")}}
    inventory = tuple(ProductToolInventoryEntry(name=s["name"], dispatch_kind="control", permission_category="read_file",
                        source="fixture", version="v1", execution_identity="fixture",
                        projectless_admission="requires_project" if s["name"] == "write_file" else "safe") for s in specs)
    catalog = {"specs": specs, "schema_fingerprints": {s["name"]: canonical_hash(s["input_schema"]) for s in specs}}
    ledger = ContextRouteLedgerStore(state_path)
    class AuthorityCheckingAuthorization(Noop):
        async def authorize(self, prepared):
            if prepared.call.name == "write_file":
                authority = registry.resolve(prepared.run_id)
                assert authority.task_work_context.task_scope_id is None
                assert authority.task_work_context.workspace_root is None
                context = authority.execution_context(call_id=prepared.call.call_id.value, effect_id=prepared.effect_id.value)
                assert context.write_scope_root is not None
                assert context.binding_epoch == 1
                assert context.workspace == context.write_scope_root
            return await super().authorize(prepared)
    noop = AuthorityCheckingAuthorization() if dynamic else Noop()
    def ports(database, uow):
        tools = ToolRegistry()
        async def handler(_args, _ctx):
            return ToolResult.succeeded(_ctx.call_id, {"ok": True})
        for spec in specs:
            tools.register(FunctionTool(ToolSpec(spec["name"], spec["description"], spec["input_schema"]), handler))
        if dynamic:
            tools = dynamic_tools(state_path, tools, registry, inventory, dynamic_factory)
        published = uow.put_tool_catalog_snapshot(tuple(ProviderToolSpec(s["name"], s["description"], s["input_schema"]) for s in specs))
        catalog.update(generation=published.generation, content_fingerprint=published.content_fingerprint)
        result = RuntimePorts(provider=ProviderInvocationCoordinator(uow=uow, resolver=SimpleNamespace(resolve=lambda _: binding)),
                            tools=EffectExecutor(uow=uow, registry=tools, authorization=noop, reconciliation=noop),
                            authorization=noop, context=SqliteContextPort(database), delivery=DeliveryDispatcher(uow, {}),
                            tool_reconciliation=noop, reconciliation=noop, provider_reconciliation=noop,
                            react_checkpoint=uow, tool_catalog=DurableToolCatalogResolver(uow),
                            runtime_decision_sink=ProductRuntimeDecisionSink(ledger=ledger),
                            agent_memory=memory,
                            context_provider=None if memory is None else ProductConversationContextProvider(sources),
                            context_staging=None if memory is None else ContextStagingRepository(database))
        if dynamic:
            from deskpet.sdk_adapters.task_execution import ProductTaskExecutionAuthority, BindingRootResolver
            from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore
            from deskpet.task_scope.store import CanonicalTaskScopeStore
            from deskpet.sdk_adapters.effect_gate import EffectGate
            from deskpet.sdk_adapters.tools import ProductEffectExecutor
            from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress
            bindings = WorkspaceBindingAuthorityStore(state_path)
            gate = EffectGate(binding_store=bindings, route_ledger=ledger, scope_store=CanonicalTaskScopeStore(state_path),
                              authority_resolver=registry.resolve, exposure_resolver=registry.resolve_exposure)
            result = replace(result, tools=ProductEffectExecutor(uow=uow, registry=tools, authorization=noop, reconciliation=noop,
                              effect_gate=gate, evidence_ingress=ExecutionEvidenceIngress(state_path)),
                              task_execution_authority=ProductTaskExecutionAuthority(root_resolver=BindingRootResolver(bindings)))
        from deskpet.execution.semantic_closure import closure_instruction_for_run
        return replace(result, run_context_authority=ProductRunContextAuthority(
            ports_resolver=lambda: result, exposure_resolver=registry.resolve_exposure, ledger=ledger,
            closure_reader=(lambda run: closure_instruction_for_run(state_path, run.value)) if dynamic else None,
        ))
    expected = build_candidate_identity()
    origin = json.loads(metadata.distribution("simple-harness-sdk").read_text("direct_url.json"))["url"]
    # Reuse the exact installed 0.7.2 wheel path; normal production verifier
    # still checks version/hash/origin. No SDK import or verifier is mocked.
    identity = replace(expected, wheel_path=Path(unquote(urlparse(origin).path)).resolve())
    stack = ProductSdkRuntimeStack(paths=ProductRuntimePathsAdapter(tmp_path / "sdk"),
                                  candidate_identity=identity,
                                  dependency_loader=lambda: SdkRuntimeBuildInputs(
                                      profiles={"agent.general": RuntimeProfile("agent.general", "react")},
                                      drivers={"react": ReActDriver(effects=EffectBatchExecutor(), tool_exposure_resolver=registry.resolve_exposure)},
                                      ports_factory=ports, workflow_catalog_digest="fixture"))
    await stack.start()
    ingress = SdkRuntimeIngress(stack)
    ingress.open()
    queue = ForegroundQueueStore(state_path, fault_hook=fault)
    tools = ProductForegroundToolPort(state_path, catalog=catalog,
                                     inventory=inventory,
                                     registry=registry)
    observer_stack = stack
    if legacy_observer:
        class LegacyScopedStack:
            # This is the old observer capability contract. Every exposed read
            # still delegates to the public SDK-backed production adapter.
            read_run_terminal_evidence = stack.read_run_terminal_evidence
            read_reserved_fact = stack.read_reserved_fact
        observer_stack = LegacyScopedStack()
    runtime = ForegroundRuntimeExecutionAuthority(
        store=queue, subject=local_owner_auth().subject, owner_id="primary-worker", ingress=ingress,
        context=PrimaryForegroundContextPort(state_path, subject=local_owner_auth().subject, route_ledger=ledger),
        provider=ProviderPort(binding), tools=tools, terminal_observer=SqliteSdkTerminalObserver(str(state_path), ingress, observer_stack),
        run_binding_reader=stack.read_closure_run_facts, conversation_entrypoint=conversation,
        state_changed=state_changed,
    )
    return runtime, stack, queue


@pytest.mark.asyncio
async def test_primary_real_runtime_terminal_outbox_history_reopen(tmp_path):
    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    provider = Provider()
    runtime, stack, queue = await build(tmp_path, state, provider)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "one", "First actual user turn"))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        assert runtime.last_error is None
        assert await queue.current_snapshot(local_owner_auth().subject) is None
        assert len(provider.requests) == 1
        history = await PrimaryHistoryStore(state).read(subject=local_owner_auth().subject, primary_ref=primary, before_sequence=2)
        assert [m["content"] for m in history[0]["messages"]] == ["First actual user turn", "Actual response 1"]
        assert "PRIVATE_CANARY" not in json.dumps(history)
        assert "HIDDEN_CANARY" not in json.dumps(history)
        await assert_exact_sdk_terminal_identity(state, stack, primary)
    finally:
        await runtime.close()
        await stack.close()
    runtime, stack, queue = await build(tmp_path, state, provider)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "one", "First actual user turn"))
        assert not await runtime._drive_once()  # delivery replay cannot rerun provider
        await service.enqueue_turn(QueueTurnRequest(None, "two", "Second actual user turn"))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        sent = [(m.role.value, m.content) for m in provider.requests[-1].messages]
        assert ("user", "First actual user turn") in sent
        assert ("assistant", "Actual response 1") in sent
        assert sent.count(("user", "Second actual user turn")) == 1
        assert len(provider.requests) == 2
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT COUNT(*) FROM task_scopes").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM foreground_terminal_receipts").fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM memory_ingestion_outbox WHERE state='pending'").fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM context_route_decisions WHERE origin='no_recall'").fetchone()[0] == 2
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ["terminal.after_outbox", "terminal.after_commit"])
async def test_primary_terminal_crash_reopen_does_not_resend_or_publish_partial(tmp_path, point):
    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    provider = Provider()

    def crash(actual):
        if actual == point:
            raise RuntimeError("injected-terminal-crash")

    runtime, stack, queue = await build(tmp_path, state, provider, fault=crash)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "crash", "Durable actual turn"))
        with pytest.raises(RuntimeError, match="injected-terminal-crash"):
            await asyncio.wait_for(runtime._drive_once(), 15)
        history = await PrimaryHistoryStore(state).read(
            subject=local_owner_auth().subject, primary_ref=primary, before_sequence=2,
        )
        committed = int(point == "terminal.after_commit")
        assert len(history) == committed
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT COUNT(*) FROM foreground_terminal_receipts").fetchone()[0] == committed
            assert db.execute("SELECT COUNT(*) FROM memory_ingestion_outbox").fetchone()[0] == committed
            observation = db.execute("SELECT evidence_id,envelope_sha256 FROM human_memory_evidence WHERE source_ref LIKE 'primary-runtime:%'").fetchall()
            assert len(observation) == 1
        assert len(provider.requests) == 1
    finally:
        await runtime.close()
        await stack.close()
    runtime, stack, queue = await build(tmp_path, state, provider)
    try:
        await asyncio.wait_for(runtime._drive_once(), 15)
        assert len(provider.requests) == 1
        assert await queue.current_snapshot(local_owner_auth().subject) is None
        history = await PrimaryHistoryStore(state).read(
            subject=local_owner_auth().subject, primary_ref=primary, before_sequence=2,
        )
        assert [m["content"] for m in history[0]["messages"]] == ["Durable actual turn", "Actual response 1"]
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT COUNT(*) FROM foreground_terminal_receipts").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM memory_ingestion_outbox").fetchone()[0] == 1
            assert db.execute("SELECT evidence_id,envelope_sha256 FROM human_memory_evidence WHERE source_ref LIKE 'primary-runtime:%'").fetchall() == observation
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
async def test_primary_real_runtime_with_agent_memory_and_validated_identity(tmp_path):
    from simple_harness_memory import MemoryManager

    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    await service.open_primary()
    # The installed public manager with real SQLite and explicitly development
    # embeddings. No production embedding / real Provider claim is made.
    memory = await MemoryManager.build_development(tmp_path / "agent-memory.db")
    provider = Provider()
    runtime, stack, queue = await build(tmp_path, state, provider, memory=memory)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "memory", "Actual memory-enabled turn"))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        assert len(provider.requests) == 1
        assert await queue.current_snapshot(local_owner_auth().subject) is None
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT COUNT(*) FROM task_scopes").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM foreground_terminal_receipts WHERE terminal_state='COMPLETED'").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM memory_session_identities").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM sdk_context_sources").fetchone()[0] == 1
    finally:
        await runtime.close()
        await stack.close()
        await memory.close()


@pytest.mark.asyncio
async def test_primary_failed_provider_settles_without_inventing_an_answer(tmp_path):
    class FailedProvider(Provider):
        async def invoke(self, request, *, cancel):
            self.requests.append(request)
            from simple_harness.providers.errors import ProviderAuthenticationError
            raise ProviderAuthenticationError()

    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    provider = FailedProvider()
    runtime, stack, queue = await build(tmp_path, state, provider)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "failed", "Actual failed turn"))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        assert await queue.current_snapshot(local_owner_auth().subject) is None
        history = await PrimaryHistoryStore(state).read(subject=local_owner_auth().subject, primary_ref=primary, before_sequence=2)
        assert history[0]["terminal_state"] == "FAILED"
        assert history[0]["messages"] == [{"role": "user", "content": "Actual failed turn"}]
        assert len(provider.requests) == 1
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
async def test_primary_recent_history_is_bounded_and_subject_partitioned(tmp_path):
    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    provider = Provider()
    runtime, stack, queue = await build(tmp_path, state, provider)
    try:
        for index in range(12):
            await service.enqueue_turn(QueueTurnRequest(None, f"bounded-{index}", f"Actual bounded turn {index}"))
            assert await asyncio.wait_for(runtime._drive_once(), 15)
        history_store = PrimaryHistoryStore(state)
        history = await history_store.read(subject=local_owner_auth().subject, primary_ref=primary, before_sequence=13)
        assert len(history) == 10
        assert [g["messages"][0]["content"] for g in history] == [f"Actual bounded turn {i}" for i in range(2, 12)]
        sent = [(m.role.value, m.content) for m in provider.requests[-1].messages]
        assert ("user", "Actual bounded turn 0") not in sent
        assert ("user", "Actual bounded turn 1") in sent
        assert sent.count(("user", "Actual bounded turn 11")) == 1
        assert await history_store.read(subject="different-owner", primary_ref=primary, before_sequence=13) == ()
        assert await history_store.read(subject=local_owner_auth().subject, primary_ref="different-primary", before_sequence=13) == ()
        with pytest.raises(ValueError, match="primary_history_limit_invalid"):
            await history_store.read(subject=local_owner_auth().subject, primary_ref=primary, before_sequence=13, limit=11)
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
async def test_primary_tool_history_uses_actual_group_not_fabricated_tool_calls(tmp_path):
    from simple_harness import CallId
    from simple_harness.providers import ProviderToolCall

    class ToolProvider(Provider):
        async def invoke(self, request, *, cancel):
            if not self.requests:
                self.requests.append(request)
                return ProviderResponse(
                    request.request_id, Message(MessageRole.ASSISTANT, "Searching actual tools"),
                    tool_calls=(ProviderToolCall(CallId("actual-search"), "tool_search", {}),),
                    model="model", usage=ProviderUsage(10, 10, 20), opaque_continuation_ref="fixture-opaque",
                )
            return await super().invoke(request, cancel=cancel)

    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    provider = ToolProvider()
    runtime, stack, queue = await build(tmp_path, state, provider)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "tool", "Actual tool turn"))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        history = await PrimaryHistoryStore(state).read(subject=local_owner_auth().subject, primary_ref=primary, before_sequence=2)
        messages = history[0]["messages"]
        assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"]
        assert messages[2]["name"] == "tool_search"
        assert messages[2]["call_id"]
        assert json.loads(messages[2]["content"])["value"] == {"ok": True}
        assert json.loads(messages[2]["content"])["outcome"] == "succeeded"
        await service.enqueue_turn(QueueTurnRequest(None, "after-tool", "Continue after actual tool"))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        sent = provider.requests[-1].messages
        assert not any(m.role.value == "tool" for m in sent)  # no orphan native tool results
        quoted = next(m.content.split("\n", 1)[1] for m in sent if isinstance(m.content, str) and "historical_causal_group" in m.content)
        assert json.loads(quoted)["messages"] == messages
        assert len(provider.requests) == 3
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
async def test_unscoped_head_completes_then_real_scoped_fifo_runs(tmp_path):
    from deskpet.memory.human_memory_service import CreateTaskScopeRequest

    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    await service.open_primary()
    scope = await service.create_task_scope(CreateTaskScopeRequest("fifo-scope", "Task", "Real scoped work", "create-fifo-scope"))
    from tests.sdk_adapters.s5b_effect_gate_harness import bind_scope_root
    root = tmp_path / "scoped-root"
    root.mkdir()
    await bind_scope_root(state, scope["scope_ref"], root)
    first = await service.enqueue_turn(QueueTurnRequest(None, "fifo-first", "First standalone"))
    second = await service.enqueue_turn(QueueTurnRequest(scope["scope_ref"], "fifo-second", "Then scoped work"))
    provider = Provider()
    runtime, stack, queue = await build(tmp_path, state, provider)
    try:
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        next_candidate = await queue.read_next_preparation_candidate(local_owner_auth().subject)
        assert next_candidate.turn_id == second["turn_ref"]
        assert next_candidate.turn_id != first["turn_ref"]
        assert next_candidate.task_scope_id == scope["scope_ref"]
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        assert await queue.current_snapshot(local_owner_auth().subject) is None
        assert len(provider.requests) == 2
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT COUNT(*) FROM foreground_terminal_receipts WHERE terminal_state='COMPLETED'").fetchone()[0] == 2
            assert db.execute("SELECT COUNT(*) FROM context_route_decisions WHERE origin='host_initial'").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM task_scopes").fetchone()[0] == 1
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["observe", "raise", "hang"])
async def test_primary_state_notification_cannot_block_terminal(tmp_path, mode):
    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    await service.open_primary()
    observed = []
    cancelled = asyncio.Event()

    async def changed():
        # A separate real connection must see committed facts, never a dirty row.
        with sqlite3.connect(state) as db:
            observed.append((
                db.execute("SELECT COUNT(*) FROM foreground_runs").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM foreground_run_sdk_bindings").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM foreground_terminal_receipts").fetchone()[0],
            ))
        if mode == "raise":
            raise RuntimeError("private callback failure")
        if mode == "hang":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    runtime, stack, queue = await build(tmp_path, state, Provider(), state_changed=changed)
    try:
        await service.enqueue_turn(QueueTurnRequest(None, "notification", "Actual user turn"))
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        assert await queue.current_snapshot(local_owner_auth().subject) is None
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT COUNT(*) FROM foreground_terminal_receipts").fetchone()[0] == 1
        await asyncio.wait_for(asyncio.shield(runtime._notification_task), 2)
        assert observed[0][0] == 1
        assert observed[-1] == (1, 1, 1)
        if mode == "hang":
            assert cancelled.is_set()
        assert runtime.last_error is None
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
async def test_primary_driver_error_invalidates_without_publishing_exception(tmp_path):
    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    await service.open_primary()
    calls = []

    async def changed():
        calls.append(())  # The callback has no identity, transcript, or error arguments.

    runtime, stack, queue = await build(tmp_path, state, Provider(), state_changed=changed)
    async def fail():
        raise RuntimeError("PRIVATE_DRIVER_ERROR")
    runtime._drive_once = fail
    try:
        await runtime.after_enqueue(subject=local_owner_auth().subject)
        await runtime.drain()
        await asyncio.wait_for(asyncio.shield(runtime._notification_task), 1)
        assert calls == [()]
        assert str(runtime.last_error) == "PRIVATE_DRIVER_ERROR"
        assert await queue.current_snapshot(local_owner_auth().subject) is None
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
async def test_primary_pre_observation_history_rebuild_reads_actual_sdk(tmp_path):
    from deskpet.memory.human_memory_service import CreateTaskScopeRequest
    from tests.sdk_adapters.s5b_effect_gate_harness import bind_scope_root
    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    primary = (await service.open_primary())["primary_ref"]
    scope = await service.create_task_scope(CreateTaskScopeRequest("legacy", "Task", "Legacy scoped work", "legacy-create"))
    root = tmp_path / "legacy-root"
    root.mkdir()
    await bind_scope_root(state, scope["scope_ref"], root)
    await service.enqueue_turn(QueueTurnRequest(scope["scope_ref"], "legacy-turn", "Actual old scoped user"))
    runtime, stack, _ = await build(tmp_path, state, Provider(), legacy_observer=True)
    try:
        assert await asyncio.wait_for(runtime._drive_once(), 15)
        with sqlite3.connect(state) as db:
            before = db.execute("SELECT evidence_id,envelope_sha256 FROM human_memory_evidence ORDER BY evidence_id").fetchall()
            assert db.execute("SELECT COUNT(*) FROM human_memory_evidence WHERE source_ref LIKE 'primary-runtime:%'").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM task_scope_terminal_gate_receipts").fetchone()[0] == 1
            receipt = json.loads(db.execute("SELECT receipt_json FROM foreground_terminal_receipts").fetchone()[0])
            assert receipt["terminal_authority_kind"] == "task_scope_watermarks"
            assert "primary_observation_ref" not in receipt
        for _ in range(2):
            history = await PrimaryHistoryStore(state, settled_run_reader=stack.read_settled_primary_run).read(
                subject=local_owner_auth().subject, primary_ref=primary, before_sequence=2)
            assert [m["content"] for m in history[0]["messages"]] == ["Actual old scoped user", "Actual response 1"]
            assert history[0]["source_ref"].startswith("primary-terminal:")
        for field, bad in (("event_id", "other-sdk-event"), ("event_hash", "0" * 64)):
            def mismatched_reader(run_id, **kwargs):
                terminal, messages = stack.read_settled_primary_run(run_id, **kwargs)
                return replace(terminal, **{field: bad}), messages
            with pytest.raises(RuntimeError, match="primary_history_terminal_mismatch"):
                await PrimaryHistoryStore(state, settled_run_reader=mismatched_reader).read(
                    subject=local_owner_auth().subject, primary_ref=primary, before_sequence=2)
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT evidence_id,envelope_sha256 FROM human_memory_evidence ORDER BY evidence_id").fetchall() == before
    finally:
        await runtime.close()
        await stack.close()


def dynamic_tools(state_path, source_tools, authorities, inventory, factory):
    from deskpet.sdk_adapters.tools import ProductToolsAdapter, active_product_tool_context
    from deskpet.sdk_adapters.effect_gate import project_tool_execution_context
    from deskpet.sdk_adapters.context_route import ContextRouteToolService
    from deskpet.sdk_adapters.task_scope_mutation import TaskScopeUpdateService
    from deskpet.sdk_adapters.tool_authority import SdkRuntimeCapabilityBridgeAdapter
    from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore
    from deskpet.tools.os_tools.write_file import write_file
    ledger = ContextRouteLedgerStore(state_path)
    def execution_context():
        context = active_product_tool_context()
        return project_tool_execution_context(authorities.resolve(context.run_id).execution_context(
            call_id=context.call_id.value, effect_id=context.effect_id.value), context)
    bridge = SdkRuntimeCapabilityBridgeAdapter(authorities, execution_context)
    route = ContextRouteToolService(
        service_factory_getter=lambda: factory,
        binding_store_factory=lambda: WorkspaceBindingAuthorityStore(state_path),
        binding_append_getter=lambda: None, ledger=ledger, tool_context_getter=active_product_tool_context)
    closure = TaskScopeUpdateService(state_path, tool_context_getter=active_product_tool_context, route_ledger=ledger)
    tools = ProductToolsAdapter(execution_identities={i.name: "fixture" for i in inventory})
    tools.bind_run_authorities(authorities)
    for item in inventory:
        async def invoke(args, context, name=item.name):
            if name == "context_route":
                result = await route.handle_context_route(args)
            elif name == "task_scope_update":
                return await closure.handle_task_scope_update(args)
            elif name == "tool_search":
                result = bridge.search(args["query"])
            elif name == "tool_describe":
                result = bridge.describe(args["capability_id"])
            elif name == "tool_activate":
                result = bridge.activate(args["capability_id"], args["schema_hash"], args["describe_nonce"]).to_json()
            elif name == "write_file":
                result = json.loads(write_file(dict(args), execution_context=execution_context()))
            else:
                raise AssertionError(name)
            return ToolResult.succeeded(context.call_id, result)
        tools.register(FunctionTool(source_tools.get(item.name).spec, invoke))
    return tools


@pytest.mark.asyncio
@pytest.mark.parametrize("supersede", [False, True])
async def test_primary_none_routes_to_exact_task_and_writes_real_file(tmp_path, supersede):
    from simple_harness import CallId
    from simple_harness.providers import ProviderToolCall
    from deskpet.memory.human_memory_service import CreateTaskScopeRequest
    from tests.sdk_adapters.s5b_effect_gate_harness import bind_scope_root
    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    factory = HumanMemoryHostServiceFactory(state, startup)
    service = factory.bind(local_owner_auth())
    await service.open_primary()
    scope = await service.create_task_scope(CreateTaskScopeRequest("dynamic", "Task", "Write actual file", "dynamic-create"))
    root = tmp_path / "exact-root"
    root.mkdir()
    await bind_scope_root(state, scope["scope_ref"], root)

    class RoutedProvider(Provider):
        async def invoke(self, request, *, cancel):
            n = len(self.requests)
            self.requests.append(request)
            results = [json.loads(m.content) for m in request.messages if m.role.value == "tool" and isinstance(m.content, str)]
            if n == 0:
                name, args = "context_route", {"route": "resume_existing", "task_scope_id": scope["scope_ref"]}
            elif n == 1:
                name, args = "tool_search", {"query": "write_file"}
            elif n == 2:
                value = results[-1]["value"]
                name, args = "tool_describe", {"capability_id": value["matches"][0]["capability_id"]}
            elif n == 3:
                value = results[-1]["value"]
                name, args = "tool_activate", {k: value[k] for k in ("capability_id", "schema_hash", "describe_nonce")}
            elif n == 4:
                if supersede:
                    other_root = tmp_path / "later-root"
                    other_root.mkdir()
                    await bind_scope_root(state, scope["scope_ref"], other_root, base_revision=1, tag="later")
                name, args = "write_file", {"path": "real.txt", "content": "exact routed output"}
            elif n == 5 and not supersede:
                instruction = next(json.loads(m.content) for m in request.messages if m.role.value == "system" and isinstance(m.content, str) and '"task_scope_closure_required"' in m.content)
                name, args = "task_scope_update", {"outcome": "no_mutation", "base_revision": instruction["current_revision"],
                    "closure_reason": "The requested output file is written; task metadata unchanged.",
                    "evidence_refs": instruction["allowed_evidence_refs"], "idempotency_key": "close-dynamic"}
            else:
                return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "File written"), model="model", usage=ProviderUsage(10, 10, 20))
            return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Executing " + name),
                tool_calls=(ProviderToolCall(CallId(f"dynamic-{n}"), name, args),), model="model", usage=ProviderUsage(10, 10, 20))

    provider = RoutedProvider()
    await service.enqueue_turn(QueueTurnRequest(None, "dynamic-turn", "Please write the file in the exact task"))
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True)
    try:
        assert await asyncio.wait_for(runtime._drive_once(), 20), [(m.role.value, m.content) for m in provider.requests[-1].messages]
        if supersede:
            assert not (root / "real.txt").exists()
            assert not (tmp_path / "later-root" / "real.txt").exists()
            with sqlite3.connect(state) as db:
                assert db.execute("SELECT reason_code FROM effect_gate_rejections").fetchone()[0] == "workspace_binding_receipt_superseded"
        else:
            assert (root / "real.txt").read_text() == "exact routed output"
        assert await queue.current_snapshot(local_owner_auth().subject) is None
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT task_scope_id FROM foreground_runs").fetchone()[0] is None
            assert db.execute("SELECT COUNT(*) FROM context_route_decisions WHERE origin='host_initial'").fetchone()[0] == 0
            assert db.execute("SELECT terminal_state FROM foreground_terminal_receipts").fetchone()[0] == "COMPLETED"
            assert db.execute("SELECT COUNT(*) FROM task_scope_terminal_gate_receipts").fetchone()[0] == 1
        primary_ref = (await service.open_primary())["primary_ref"]
        await assert_exact_sdk_terminal_identity(state, stack, primary_ref)
        assert len(provider.requests) == (6 if supersede else 7)
        # A second drive and SDK reopen must not re-issue the route or effect.
        assert not await runtime._drive_once()
        await runtime.close()
        await stack.close()
        runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True)
        assert not await runtime._drive_once()
        assert len(provider.requests) == (6 if supersede else 7)
    finally:
        await runtime.close()
        await stack.close()


def test_primary_public_artifact_projection_keeps_only_allowlisted_fields():
    from deskpet.sdk_adapters.composition import project_primary_transcript
    # Artifact-bearing public Context messages may be Host-authored. The SDK
    # provider port does not accept artifact blocks; this is a projection test.
    messages = (Message(MessageRole.USER, "Make artifact"), Message(MessageRole.ASSISTANT, (
        ContentBlock("text", {"text": "Created artifact", "private": "PRIVATE_CANARY"}),
        ContentBlock("artifact", {"artifact_ref": "artifact-real", "name": "report.txt",
            "mime_type": "text/plain", "uri": "artifact://report", "private": "PRIVATE_CANARY"}),
        ContentBlock("reasoning", {"text": "HIDDEN_CANARY"}),
    )))
    projected = project_primary_transcript(messages, current_text="Make artifact")
    assert projected[-1]["content"] == [{"type": "text", "data": {"text": "Created artifact"}},
        {"type": "artifact", "data": {"artifact_ref": "artifact-real", "name": "report.txt", "mime_type": "text/plain", "uri": "artifact://report"}}]
    assert "PRIVATE_CANARY" not in json.dumps(projected)
    assert "HIDDEN_CANARY" not in json.dumps(projected)


async def assert_exact_sdk_terminal_identity(state, stack, primary_ref):
    import aiosqlite
    from deskpet.execution.terminal_identity import read_primary_terminal_identity_tx
    async with aiosqlite.connect(state) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute("SELECT host_run_id,sdk_run_id FROM foreground_terminal_receipts")
        for row in await rows.fetchall():
            identity = await read_primary_terminal_identity_tx(db, subject=local_owner_auth().subject,
                primary_ref=primary_ref, host_run_id=row["host_run_id"], sdk_run_id=row["sdk_run_id"])
            actual = stack.read_run_terminal_evidence(row["sdk_run_id"])
            identity.verify_sdk_terminal(actual)
            for field, bad in (("event_id", "other-sdk-event"), ("event_hash", "0" * 64)):
                with pytest.raises(RuntimeError, match="primary_history_terminal_mismatch"):
                    identity.verify_sdk_terminal(replace(actual, **{field: bad}))
