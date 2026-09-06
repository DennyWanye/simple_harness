"""r19 guidance through current real Scope source/SDK/provider/closure paths.

Deterministic transport only; no all-visible reader, old gate harness migration,
model-quality claim or source/terminal fabrication.
"""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import httpx
import pytest
from simple_harness.providers import ProviderToolCall

from deskpet.execution.primary_dependencies import check_runtime_dependencies
from deskpet.memory.human_memory_service import CreateTaskScopeRequest, MutateTaskScopeRequest, QueueTurnRequest
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.sdk_adapters.effect_gate import effect_gate_public_message
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from deskpet.task_scope.store import CanonicalTaskScopeStore
from tests.execution.test_primary_create_new_runtime import fixture
from tests.execution.test_primary_foreground_runtime import build
from tests.sdk_adapters.s5b_effect_gate_harness import bind_scope_root
from tests.sdk_adapters.test_product_host_ports import Registry
from tests.execution.test_closure_request_guard import world, attempt_rows
from tests.execution.test_closure_resume_sources import run, mutation


def tool_results(body):
    return [json.loads(m["content"]) for m in body["messages"]
        if m["role"] == "tool" and isinstance(m.get("content"), str)]


@pytest.mark.asyncio
async def test_completed_scope_actual_resume_rejection_reaches_next_physical_request(tmp_path):
    state, _, service, configured, authority = await fixture(tmp_path)
    created = await service.create_task_scope(CreateTaskScopeRequest(
        "accepted-archive", "Accepted archive", "Previously accepted task", "archive-create"))
    scope = created["scope_ref"]
    root = configured / "accepted-root"; root.mkdir()
    # This helper only calls the real Host binding API. The runtime supplies
    # its actual ScopeDisclosureReader and producer_dependencies reader.
    await bind_scope_root(state, scope, root)
    await service.mutate_task_scope(MutateTaskScopeRequest(scope, "status", "complete", "archive-accepted"))
    scope_store = CanonicalTaskScopeStore(state)
    assert await scope_store.read_head_status(scope) == "complete"
    sends, refusals, routes = [], [], []
    async def physical(request):
        body = json.loads(request.content); sends.append(body)
        n = len(sends) - 1; results = tool_results(body)
        call = None
        if n == 0:
            name, args = "context_route", {"route": "resume_existing", "task_scope_id": scope}
        elif n == 1:
            result = results[-1]["value"]
            assert result["resume_package"]["status"] == "complete"
            assert result["resume_package"]["disclosure_manifest"]
            routes.append(result)
            name, args = "tool_search", {"query": "write_file"}
        elif n == 2:
            name, args = "tool_describe", {"capability_id": results[-1]["value"]["matches"][0]["capability_id"]}
        elif n == 3:
            name, args = "tool_activate", {k: results[-1]["value"][k] for k in ("capability_id", "schema_hash", "describe_nonce")}
        elif n == 4:
            name, args = "write_file", {"path": "blocked.txt", "content": "must never be written"}
        elif n in (5, 6):
            refusal = results[-1]
            code = "effect_gate_task_scope_not_active" if n == 5 else "effect_gate_route_receipt_rejected"
            assert refusal["error_code"] == code
            assert refusal["public_message"] == effect_gate_public_message(code)
            refusals.append(refusal)
            # Receipt correlation and a real second physical request prove the
            # refusal reaches the consumer; equality to a prompt alone cannot.
            assert not (root / "blocked.txt").exists()
            assert await scope_store.read_head_status(scope) == "complete"
            if n == 5:
                name, args = "write_file", {"path": "blocked.txt", "content": "same rejected route"}
            else:
                instructions = [json.loads(m["content"]) for m in body["messages"] if m["role"] == "system"
                    and isinstance(m.get("content"), str) and '"task_scope_closure_required"' in m["content"]]
                if instructions:
                    instruction = instructions[-1]
                    name, args = "task_scope_update", dict(outcome="no_mutation",
                        base_revision=instruction["current_revision"], evidence_refs=instruction["allowed_evidence_refs"],
                        closure_reason="Write was rejected; archived task remains complete.", idempotency_key="archive-refusal-close")
                else:
                    name = None
        else:
            name = None
        if name:
            call = {"id": f"archive-{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        message = {"role": "assistant", "content": "Further edits were not performed."}
        if call: message["tool_calls"] = [call]
        return httpx.Response(200, json={"id": f"archive-response-{n}", "model": "model-a",
            "choices": [{"message": message, "finish_reason": "tool_calls" if call else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
    runtime = stack = queue = None
    async def guard(request):
        current = await queue.current_snapshot(local_owner_auth().subject)
        await check_runtime_dependencies(db_path=state, stack=stack, sdk_run_id=current.sdk_run_id,
            request=request, policy_factory=lambda _: runtime.history_policy)
    provider = ProductProviderAdapter(Registry("fixture-key"), provider_id="relay", client=client,
        price_resolver=lambda *_: (1, 1, "fixture-prices"), pre_invoke_guard=guard)
    try:
        runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
            binding_authority=authority, configured_root=configured)
        await service.enqueue_turn(QueueTurnRequest(None, "archive-edit", "Resume the accepted task and edit its file"))
        await runtime.after_enqueue(subject=runtime.subject)
        await asyncio.wait_for(runtime.drain(), 25)
        assert runtime.last_error is None
        assert len(routes) == 1 and len(refusals) == 2 and len(sends) in (7, 8)
        assert not (root / "blocked.txt").exists()
        assert await scope_store.read_head_status(scope) == "complete"
        with sqlite3.connect(state) as db:
            sdk_run_id = db.execute("SELECT sdk_run_id FROM foreground_run_sdk_bindings").fetchone()[0]
        # Rejected effects have no successful primary source projection. Read
        # actual effect identities from the public SDK audit, not a Host table
        # whose purpose is to expose completed tool-result sources.
        from simple_harness import RunId
        api = stack.require_ready().client
        page = await api.open_run_operation_audit(RunId(sdk_run_id), page_size=256)
        operations = list(page.operations)
        while page.next_cursor is not None:
            page = await api.read_run_operation_audit_page(RunId(sdk_run_id), cursor=page.next_cursor)
            operations.extend(page.operations)
        heads = [o for o in operations if o.kind == "effect" and o.record_type == "head"
            and o.operation_name == "write_file"]
        assert len(heads) == 2
        assert {o.raw_call_id for o in heads} == {"archive-4", "archive-5"}
        _, effects = stack.read_primary_dependency_facts(sdk_run_id, tuple(o.effect_id for o in heads))
        assert len(effects) == 2 and all(e.terminal and e.result.error_code in {
            "effect_gate_task_scope_not_active", "effect_gate_route_receipt_rejected"} for e in effects)
    finally:
        if runtime is not None: await runtime.close()
        if stack is not None: await stack.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_real_closure_input_keeps_unperformed_readback_and_noncomplete_goal(tmp_path, monkeypatch):
    goal = "Write fresh.txt, then read the saved file and verify its exact content."
    remaining = "File creation is evidenced; reading the saved file and verification remain pending."
    def reply(observation, _ordinal):
        assert observation["task_scope"]["goal"] == goal
        return mutation({**observation["task_scope"], "allowed_evidence_refs": observation["allowed_evidence_refs"]}, value=remaining)
    w = await world(tmp_path, monkeypatch, "allow", closure_reply=reply, user_text=goal)
    original = w.provider.invoke
    async def actual_goal(request, *, cancel):
        first = not w.provider.requests
        response = await original(request, cancel=cancel)
        if first:
            call, = response.tool_calls
            assert call.name == "context_route" and call.arguments["route"] == "create_new"
            response = replace(response, tool_calls=(ProviderToolCall(call.call_id, call.name, {**call.arguments, "goal": goal}),))
        return response
    monkeypatch.setattr(w.provider, "invoke", actual_goal)
    try:
        await run(w)
        assert len(w.sent) == 1 and w.outcomes[-1].status == "mutate"
        actual = w.sent[0]
        observation = json.loads(actual["messages"][1]["content"].split("\n", 1)[1])
        assert observation["task_scope"]["goal"] == goal
        assert observation["staged_final_answer"] == "Created and written."
        assert observation["material_events"] and observation["allowed_evidence_refs"]
        assert [t["function"]["name"] for t in actual["tools"]] == ["task_scope_update"]
        # An independent prohibited presupposition from the original failure,
        # in addition to the real source/state oracle below. No keyword-based
        # product rule or claim that this scripted model judged completion.
        assert "你已经替用户完成了工作" not in actual["messages"][0]["content"]
        with sqlite3.connect(w.state) as db:
            scope, state_json = db.execute("SELECT h.task_scope_id,r.state_json FROM task_scope_heads h "
                "JOIN task_scope_canonical_revisions r ON r.task_scope_id=h.task_scope_id AND r.revision=h.current_revision").fetchone()
            state = json.loads(state_json)
            proposal = json.loads(db.execute("SELECT proposal_json FROM task_workspace_binding_proposals").fetchone()[0])
            tools = [r[0] for r in db.execute("SELECT tool_name FROM primary_effect_identities ORDER BY sequence")]
            sdk_run_id = db.execute("SELECT sdk_run_id FROM foreground_run_sdk_bindings").fetchone()[0]
        assert state["goal"] == goal and state["resume"] == remaining
        assert await CanonicalTaskScopeStore(w.state).read_head_status(scope) in {"active", "open"}
        assert tools.count("write_file") == 1 and not set(tools) & {"read_file", "doc_read", "doc_readback"}
        assert (Path(proposal["root"]["canonical_path"]) / "fresh.txt").read_text() == "created in exact task root"
        stack_terminal = w.stack.read_run_terminal_evidence(sdk_run_id)
        assert stack_terminal.state == "completed"  # actual Run, not whole Scope
        assert attempt_rows(w)[0]["status"] == "succeeded"
        assert w.prepared[0]["host"]["sources"]
    finally:
        await w.runtime.close(); await w.stack.close(); await w.client.aclose()
