"""Real primary routing, physical file effects, whole groups and public SDK.

Only provider choices and the explicit task workspace selection are fixtures.
No fabricated terminal, success count, observation grant or SDK SQL seed.
"""
import asyncio
import json
import sqlite3
import time
from dataclasses import replace

import pytest
import simple_harness as h
from simple_harness_memory import MemoryManager
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
from simple_harness.contracts.messages import Message, MessageRole
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.memory.human_memory_service import (
    HumanMemoryHostServiceFactory, CreateTaskScopeRequest, QueueTurnRequest, build_foreground_turn_evidence,
)
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.memory.procedure_schema import initialize_procedure_state_db
from deskpet.memory.analysis_proposal import admitted_item, derive_span, compile_operation
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from deskpet.memory.short_indexing import PrimaryShortIndexingService
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.sdk_adapters.procedure_use import procedure_use_registration
from tests.execution.test_primary_foreground_runtime import Provider, build
from tests.sdk_adapters.s5b_effect_gate_harness import bind_scope_root

STEPS = ("先写入一份记录", "再写入一份备份")


async def create_draft(runtime, state):
    text = "我这次先写入一份记录，再写入一份备份。"
    envelope, receipt = build_foreground_turn_evidence(subject=runtime.principal().actor_id,
        authority_ref="host:procedure-test-owner", delivery_key="procedure-source", text=text)
    await HumanMemoryProgramStore(state).append_evidence(envelope, receipt)
    item = admitted_item(envelope, receipt)
    span = derive_span(item, text, span_id="procedure-test-source")
    # This fixture exercises observation after public DRAFT creation, not the
    # language classifier. Source text does not assert adoption or success.
    operation = compile_operation({"operation_id": "procedure-create", "memory_type": "procedure",
        "procedure": {"name": "记录及备份", "steps": list(STEPS), "risk_level": "low"}},
        span, item=item, now=time.time())
    operation = replace(operation, lifecycle_state=h.ProcedureLifecycleState.DRAFT)
    manager = await runtime.manager()
    await manager.ingest_committed_evidence(envelope, receipt)
    result = await manager.apply_memory_mutation_plan(principal=runtime.principal(), scope=runtime.scope(),
        plan=h.MemoryMutationPlan(plan_id="procedure-fixture-create", run_id=envelope.run_id,
            turn_id="procedure-fixture-turn", subject=envelope.subject, base_revision=1, outcome="mutate",
            operations=(operation,), disclosure_context=envelope.disclosure_context,
            evidence_refs=(h.EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),),
            idempotency_key="procedure-fixture-create"))
    view = await manager.get_memory_mutation_receipt_view(principal=runtime.principal(), receipt_ref=result.receipt_ref)
    return view.operations[0].memory_id, view.operations[0].revision


class UseProvider(Provider):
    def configure(self, scope_id, memory_id, revision, index):
        self.scope_id, self.memory_id, self.revision, self.index = scope_id, memory_id, revision, index
        self.stage = 0

    async def invoke(self, request, *, cancel):
        n = self.stage
        self.stage += 1
        self.requests.append(request)
        results = [json.loads(m.content) for m in request.messages if m.role.value == "tool" and isinstance(m.content, str)]
        arguments = [{"path": f"record-{self.index}.txt", "content": "actual record"},
                     {"path": f"backup-{self.index}.txt", "content": "actual record"}]
        if n == 0:
            name, args = "context_route", {"route": "resume_existing", "task_scope_id": self.scope_id}
        elif n == 1:
            name, args = "tool_search", {"query": "write_file"}
        elif n == 2:
            name, args = "tool_describe", {"capability_id": results[-1]["value"]["matches"][0]["capability_id"]}
        elif n == 3:
            name, args = "tool_activate", {key: results[-1]["value"][key] for key in ("capability_id", "schema_hash", "describe_nonce")}
        elif n == 4:
            name, args = "procedure_use", {"memory_id": self.memory_id, "revision": self.revision,
                "steps": [dict(text=text, tool="write_file", arguments=value)
                          for text, value in zip(STEPS, arguments, strict=True)]}
        elif n in (5, 6):
            name, args = "write_file", arguments[n - 5]
        elif n == 7:
            instruction = next(json.loads(m.content) for m in request.messages if m.role.value == "system"
                and isinstance(m.content, str) and '"task_scope_closure_required"' in m.content)
            name, args = "task_scope_update", {"outcome": "no_mutation", "base_revision": instruction["current_revision"],
                "closure_reason": "Both actual files were written; task metadata unchanged.",
                "evidence_refs": instruction["allowed_evidence_refs"], "idempotency_key": f"procedure-close-{self.index}"}
        else:
            return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "两份文件已写入。"),
                model="model", usage=ProviderUsage(10, 10, 20))
        return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "执行 " + name),
            tool_calls=(ProviderToolCall(h.CallId(f"procedure-{self.index}-{n}"), name, args),),
            model="model", usage=ProviderUsage(10, 10, 20))


@pytest.mark.asyncio
async def test_three_real_scopes_whole_groups_qualify_and_replay_does_not_increment(tmp_path):
    state = tmp_path / "state.db"
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    await service.open_primary()
    await initialize_procedure_state_db(state)
    async def builder(path, **kwargs):
        return await MemoryManager.build_human_memory_v7(path, **kwargs, allow_development_embedder=True)
    memory_runtime = compose_human_memory_runtime(state, tmp_path / "memory.db",
        adapter_factory=lambda _: None, backend_factory=builder)
    provider = UseProvider()
    runtime = stack = None
    try:
        memory_id, revision = await create_draft(memory_runtime, state)
        manager = await memory_runtime.manager()
        runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
            visibility_memory=memory_runtime, procedure_runtime=memory_runtime.procedure_runtime,
            extra_registrations=(procedure_use_registration(memory_runtime.procedure_runtime),))
        root = tmp_path / "actual-workspace"
        root.mkdir()
        worker = MemoryIngestionOutboxWorker(state, memory_runtime.manager, owner_id="procedure-test-outbox")
        authority = memory_runtime.conversation_evidence_authority
        indexing = PrimaryShortIndexingService(authority, manager=manager, principal=memory_runtime.principal())
        seen = set()
        for index, expected in enumerate(("draft", "eligible_for_activation", "active"), 1):
            scope = await service.create_task_scope(CreateTaskScopeRequest(
                f"procedure-task-{index}", "写记录和备份", "Write both files", f"create-procedure-task-{index}"))
            await bind_scope_root(state, scope["scope_ref"], root, tag=f"procedure-root-{index}")
            provider.configure(scope["scope_ref"], memory_id, revision, index)
            await service.enqueue_turn(QueueTurnRequest(None, f"procedure-turn-{index}", "执行记录和备份的两步任务。"))
            assert await asyncio.wait_for(runtime._drive_once(), 30)
            assert runtime.last_error is None
            assert (root / f"record-{index}.txt").read_text() == "actual record"
            assert (root / f"backup-{index}.txt").read_text() == "actual record"
            assert await worker.run_once() == "delivered"
            candidates = set(await authority.completed_run_ids()) - seen
            assert len(candidates) == 1
            seen |= candidates
            group = await authority.registrations_for_run(candidates.pop())
            await indexing.register_group(group)
            await memory_runtime.procedure_runtime.observe_group(group, manager)
            use = await memory_runtime.procedure_runtime.store.use_for_run(group.terminal_source[0].run_id)
            result = (await memory_runtime.procedure_runtime.store.journal(use["use_id"], "applied"))["result"]
            assert result["independent_successes"] == index
            assert result["lifecycle_state"] == expected
            revision = result["committed_revision"]
            # Same completed group is replayed by the normal cyclic worker.
            await memory_runtime.procedure_runtime.observe_group(group, manager)
            target = await manager.read_procedure_use_target(principal=memory_runtime.principal(),
                scope=memory_runtime.scope(), memory_id=memory_id, revision=revision)
            assert target.lifecycle_state == expected
        with sqlite3.connect(tmp_path / "operation-audit.db") as db:
            rows = db.execute("SELECT caller,state,observation_status FROM memory_call_attempts").fetchall()
        assert {row[0] for row in rows} == {"read_procedure_use_target", "prepare_procedure_observation", "record_procedure_observation"}
        assert all(row[1:] == ("returned", "captured_bound") for row in rows)
    finally:
        if runtime is not None:
            await runtime.close()
        if stack is not None:
            await stack.close()
        await memory_runtime.close()
