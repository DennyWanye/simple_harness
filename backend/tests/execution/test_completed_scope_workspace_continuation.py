"""New active Scope edits an existing bound root through real public ports.

Deterministic HTTP, not model/native quality. No direct SDK database or
pass-through disclosure authority. Host SQL below only inspects actual facts.
"""
import asyncio
import json
from pathlib import Path
import sqlite3

import httpx
import pytest

from deskpet.execution.primary_dependencies import check_runtime_dependencies
from deskpet.memory.human_memory_service import (
    AppendBindingRequest, CreateTaskScopeRequest, DecideManualBindingRequest,
    MutateTaskScopeRequest, QueueTurnRequest,
)
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from deskpet.task_scope.store import CanonicalTaskScopeStore
from deskpet.task_scope.workspace_bindings import WorkspaceBindingError, canonical_workspace_root
from tests.execution.test_primary_create_new_runtime import fixture
from tests.execution.test_primary_foreground_runtime import build
from tests.execution.test_completed_scope_guidance import tool_results
from tests.sdk_adapters.test_product_host_ports import Registry


async def archive(tmp_path, mode="auto"):
    state, factory, service, configured, authority = await fixture(tmp_path, mode)
    scope = (await service.create_task_scope(CreateTaskScopeRequest(
        "archived-workspace", "Archived work", "Accepted earlier work", "archive-root")))["scope_ref"]
    root = configured / "original-workspace"; root.mkdir()
    (root / "document.txt").write_text("original accepted bytes")
    binding_service = factory.bind(local_owner_auth(), binding_append=authority)
    result = await binding_service.append_binding(AppendBindingRequest(scope, str(root), "original-root"))
    if mode == "manual":
        result = await binding_service.decide_manual_binding(DecideManualBindingRequest(result["challenge_ref"], "allow", "allow-original"))
    assert result["status"] == "bound"
    await service.mutate_task_scope(MutateTaskScopeRequest(scope, "status", "complete", "archive-complete"))
    return state, factory, service, configured, authority, scope, root


def archive_facts(state, scope):
    with sqlite3.connect(state) as db:
        return tuple(db.execute(query, (scope,)).fetchall() for query in (
            "SELECT * FROM task_scope_canonical_revisions WHERE task_scope_id=? ORDER BY revision",
            "SELECT * FROM task_scope_events WHERE task_scope_id=? ORDER BY event_sequence",
            "SELECT receipt_json FROM task_workspace_binding_revisions WHERE task_scope_id=? ORDER BY binding_set_revision"))


def verify_added_bound_events(db, added, sdk_run_id, stack):
    """Exact new facts of real route ingress; no blanket same-Run allowance."""
    from deskpet.task_scope.protocol import canonical_hash
    db.row_factory = sqlite3.Row
    route = db.execute("SELECT * FROM context_route_decisions WHERE sdk_run_id=? AND task_scope_id IS NOT NULL", (sdk_run_id,)).fetchall()
    assert len(route) == 1 and route[0]["route"] == "resume_existing"
    route = route[0]
    invocations = db.execute("SELECT * FROM context_route_tool_invocations WHERE sdk_run_id=? ORDER BY rowid", (sdk_run_id,)).fetchall()
    assert len(invocations) == 2
    assert [r["verdict"] for r in invocations] == ["accepted", "rejected"]
    assert json.loads(invocations[-1]["detail_json"])["code"] == "context_route_workspace_reuse_requires_new_run"
    terminal = db.execute("SELECT * FROM foreground_terminal_receipts WHERE sdk_run_id=?", (sdk_run_id,)).fetchone()
    assert terminal is not None and terminal["terminal_state"] == "COMPLETED"
    sdk_terminal = stack.read_run_terminal_evidence(sdk_run_id)
    assert sdk_terminal is not None and sdk_terminal.run_id == sdk_run_id
    assert sdk_terminal.state == "completed" and sdk_terminal.event_id == terminal["sdk_event_id"]
    expected = {
        "route:" + route["decision_id"]: ("harness.route_decision", {
            "decision_id": route["decision_id"], "route": route["route"], "origin": route["origin"],
            "task_scope_id": route["task_scope_id"], "receipt_id": route["receipt_id"],
            "receipt_hash": route["receipt_hash"], "provider_turn_ordinal": route["provider_turn_ordinal"],
        }),
        f"{sdk_run_id}:terminal:completed": ("harness.run_terminal", {
            "generation": terminal["generation"], "terminal_state": terminal["terminal_state"],
            "sdk_terminal_event_hash": sdk_terminal.event_hash,
        }),
    }
    from simple_harness import thaw_json
    _, effects = stack.read_primary_dependency_facts(sdk_run_id, tuple(r["effect_id"] for r in invocations))
    for invocation, effect in zip(invocations, effects, strict=True):
        assert effect is not None and effect.terminal and effect.tool_name == "context_route"
        assert effect.run_id.value == sdk_run_id and effect.raw_call_id == invocation["raw_call_id"]
        assert effect.effect_id.value == invocation["effect_id"]
        assert canonical_hash(thaw_json(effect.arguments)) == invocation["proposal_hash"]
        expected["effect:" + invocation["effect_id"]] = ("harness.tool_invocation", {
            "tool_name": "context_route", "effect_id": invocation["effect_id"],
            "raw_call_id": invocation["raw_call_id"], "verdict": invocation["verdict"],
            "decision_id": invocation["decision_id"], "proposal_hash": invocation["proposal_hash"],
        })
    assert {row[5] for row in added} == {"execution:" + key for key in expected} and len(added) == len(expected)
    for row in added:
        payload = json.loads(row[7])
        kind, public = expected[payload["event_id"]]
        assert row[3] == kind and row[4] == "harness"
        assert payload["run_id"] == sdk_run_id and "execution:" + payload["event_id"] == row[5]
        assert payload["public_payload"] == public
        assert canonical_hash(payload) == row[6]
        if kind == "harness.run_terminal":
            # Host receipt binds the admitted ExecutionEvidence envelope; its
            # nested SDK event hash is independently read from the public proof.
            assert terminal["sdk_event_hash"] == row[6]
    db.row_factory = None


async def actual_world(tmp_path, *, mode="auto", invalid=None, manual_decider=None):
    state, factory, service, configured, authority, old, root = await archive(tmp_path, mode)
    prior = archive_facts(state, old)
    if invalid == "multi_root":
        another = configured / "another-root"; another.mkdir()
        await factory.bind(local_owner_auth(), binding_append=authority).append_binding(
            AppendBindingRequest(old, str(another), "second-original-root"))
        prior = archive_facts(state, old)
    sends, observed = [], {}
    # Capture the real public authority result for the external human channel.
    # Product ToolResult.failed intentionally omits raw challenge fields; this
    # does not claim that the native UI already presents the binding challenge.
    original_append = authority.append_binding
    async def capture_append(**kwargs):
        result = await original_append(**kwargs)
        observed["binding_outcome"] = result
        return result
    authority.append_binding = capture_append
    runtime = stack = queue = None
    async def physical(request):
        body = json.loads(request.content); sends.append(body)
        results = tool_results(body); n = len(sends) - 1
        call = None
        if n == 0:
            name, args = (("context_route", {"route": "resume_existing", "task_scope_id": old})
                          if invalid == "already_bound" else ("task_scope_search", {"query": "Archived work"}))
        elif n == 1 and invalid == "already_bound":
            original = results[-1]["value"]["resume_package"]
            assert original["status"] == "complete"
            name, args = "context_route", {"route": "create_new", "title": "Further edits", "goal": "Update the original document",
                "reuse_workspace_of": original["task_scope_id"], "expected_source_hash": original["source_hash"]}
        elif n == 1:
            hits = results[-1]["value"]["candidates"]
            assert len(hits) == 1
            candidate = hits[0]
            assert candidate["scope_disclosure"]["status"] == "complete"
            observed["old_package"] = candidate["scope_disclosure"]
            name, args = "context_route", {"route": "create_new", "title": "Further edits", "goal": "Update the original document",
                "reuse_workspace_of": candidate["task_scope_id"], "expected_source_hash": candidate["source_hash"]}
            if invalid == "stale_hash": args["expected_source_hash"] = "0" * 64
            if invalid == "foreign_scope": args["reuse_workspace_of"] = "foreign-unowned-scope"
        elif n == 2:
            outcome = results[-1]
            if invalid:
                expected = {"stale_hash": "task_scope_source_stale", "foreign_scope": "human_memory_permission_denied",
                            "multi_root": "context_route_workspace_source_requires_single_root",
                            "already_bound": "context_route_workspace_reuse_requires_new_run"}
                assert outcome["outcome"] == "failed" and outcome["error_code"] == expected[invalid], outcome
                if invalid == "already_bound":
                    assert "next Run" in outcome["public_message"]
                    assert "task_scope_search" in outcome["public_message"]
                observed["rejection"] = outcome
                assert (root / "document.txt").read_text() == "original accepted bytes"
                name = None
            elif mode == "manual":
                assert outcome["outcome"] == "failed"
                assert outcome["error_code"] == "context_route_binding_authorization_required", outcome
                challenge = observed["binding_outcome"]
                observed["new_scope"] = challenge["scope_ref"]
                with sqlite3.connect(state) as db:
                    assert db.execute("SELECT COUNT(*) FROM context_route_decisions WHERE task_scope_id=?", (observed["new_scope"],)).fetchone()[0] == 0
                    assert db.execute("SELECT COUNT(*) FROM task_workspace_binding_revisions WHERE task_scope_id=?", (observed["new_scope"],)).fetchone()[0] == 0
                # Actual authenticated public UI/control-channel operation;
                # the deterministic model cannot return a bool to authorize it.
                if manual_decider is None:
                    decided = await factory.bind(local_owner_auth(), binding_append=authority).decide_manual_binding(
                        DecideManualBindingRequest(challenge["challenge_ref"], "allow", "actual-binding-allow"))
                else:
                    decided = await manual_decider(factory=factory, authority=authority, state=state, root=root)
                assert decided["status"] == "bound"
                observed["manual"] = decided
                name, args = "context_route", {"route": "resume_existing", "task_scope_id": observed["new_scope"]}
            else:
                value = outcome["value"]
                observed["new_scope"] = value["created"]["scope_ref"]
                assert value["workspace_source"]["task_scope_id"] == old
                assert value["context_route_receipt"]["task_scope_id"] != old
                name, args = "tool_search", {"query": "write_file"}
        else:
            stage = n - (1 if mode == "manual" else 0)
            if stage == 2:  # manual grant -> real resume -> discover write
                assert results[-1]["value"]["resume_package"]["status"] == "active"
                name, args = "tool_search", {"query": "write_file"}
            elif stage == 3:
                name, args = "tool_describe", {"capability_id": results[-1]["value"]["matches"][0]["capability_id"]}
            elif stage == 4:
                name, args = "tool_activate", {k: results[-1]["value"][k] for k in ("capability_id", "schema_hash", "describe_nonce")}
            elif stage == 5:
                name, args = "write_file", {"path": "document.txt", "content": "new scope edited original document", "overwrite": True}
            elif stage == 6:
                assert (root / "document.txt").read_text() == "new scope edited original document"
                observed["write_result"] = results[-1]
                instruction = [json.loads(m["content"]) for m in body["messages"] if m["role"] == "system"
                    and isinstance(m.get("content"), str) and '"task_scope_closure_required"' in m["content"]][-1]
                name, args = "task_scope_update", dict(outcome="no_mutation", base_revision=instruction["current_revision"],
                    evidence_refs=instruction["allowed_evidence_refs"], closure_reason="This turn wrote the requested document; no task completion claim.",
                    idempotency_key="continue-close")
            else:
                name = None
        message = {"role": "assistant", "content": "New task used the original workspace." if not invalid else "The requested workspace was not bound."}
        if name:
            call = {"id": f"continue-{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            message["tool_calls"] = [call]
        return httpx.Response(200, json={"id": f"continue-response-{n}", "model": "model-a",
            "choices": [{"message": message, "finish_reason": "tool_calls" if call else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
    async def observed_physical(request):
        try:
            return await physical(request)
        except Exception:
            import traceback
            observed["callback_error"] = traceback.format_exc()
            raise
    client = httpx.AsyncClient(transport=httpx.MockTransport(observed_physical))
    async def guard(request):
        current = await queue.current_snapshot(local_owner_auth().subject)
        await check_runtime_dependencies(db_path=state, stack=stack, sdk_run_id=current.sdk_run_id,
            request=request, policy_factory=lambda _: runtime.history_policy)
    provider = ProductProviderAdapter(Registry("fixture-key"), provider_id="relay", client=client,
        price_resolver=lambda *_: (1, 1, "fixture-prices"), pre_invoke_guard=guard)
    try:
        from deskpet.tools.os_tools.registration import register_os_tools
        from deskpet.tools.registry import ToolRegistry
        from deskpet.tools.os_tools.write_file import write_file
        actual_tools = ToolRegistry()
        register_os_tools(actual_tools)
        write_spec = actual_tools.get("write_file")
        assert write_spec is not None and write_spec.handler is write_file
        assert write_spec.context_handler is not None
        assert write_spec.permission_category == "write_file"
        from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress
        from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
        runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True, binding_authority=authority, configured_root=configured,
            context_route_ledger_factory=lambda path: ContextRouteLedgerStore(path, evidence_ingress=ExecutionEvidenceIngress(path)),
            write_file_schema=write_spec.schema["parameters"])
        await service.enqueue_turn(QueueTurnRequest(None, "continue-original", "Continue editing the accepted project's original document in a new task"))
        await runtime.after_enqueue(subject=runtime.subject)
        await asyncio.wait_for(runtime.drain(), 25)
        assert runtime.last_error is None
        assert "callback_error" not in observed, observed.get("callback_error")
        after = archive_facts(state, old)
        assert after[0] == prior[0] and after[2] == prior[2]  # canonical + binding immutable
        assert after[1][:len(prior[1])] == prior[1]  # no old event rewritten
        if invalid == "already_bound":
            with sqlite3.connect(state) as db:
                sdk_run_id = db.execute("SELECT sdk_run_id FROM foreground_run_sdk_bindings").fetchone()[0]
                added = after[1][len(prior[1]):]
                verify_added_bound_events(db, added, sdk_run_id, stack)
                assert db.execute("SELECT COUNT(*) FROM context_route_decisions WHERE task_scope_id IS NOT NULL").fetchone()[0] == 1
        else:
            assert after[1] == prior[1]
        assert await CanonicalTaskScopeStore(state).read_head_status(old) == "complete"
        with sqlite3.connect(state) as db:
            if invalid:
                assert len(sends) == 3 and observed["rejection"]
                assert db.execute("SELECT COUNT(*) FROM task_scopes").fetchone()[0] == 1
                assert (root / "document.txt").read_text() == "original accepted bytes"
            else:
                new = observed["new_scope"]
                assert new != old and await CanonicalTaskScopeStore(state).read_head_status(new) == "active"
                newroot = json.loads(db.execute("SELECT root_json FROM task_workspace_binding_roots WHERE task_scope_id=?", (new,)).fetchone()[0])
                oldroot = json.loads(db.execute("SELECT root_json FROM task_workspace_binding_roots WHERE task_scope_id=?", (old,)).fetchone()[0])
                assert newroot["canonical_path"] == oldroot["canonical_path"] == str(root)
                assert newroot["filesystem_identity_hash"] == oldroot["filesystem_identity_hash"]
                assert newroot["root_id"] != oldroot["root_id"]
                assert observed["write_result"]
                evidence = db.execute("SELECT payload_json FROM human_memory_evidence WHERE source_ref LIKE 'host-binding-append:%'").fetchall()
                assert any(json.loads(row[0]).get("expected_filesystem_identity_hash") == oldroot["filesystem_identity_hash"] for row in evidence)
                assert db.execute("SELECT COUNT(*) FROM task_scopes").fetchone()[0] == 2
    finally:
        if runtime: await runtime.close()
        if stack: await stack.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_new_active_scope_physically_edits_original_root(tmp_path):
    await actual_world(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["stale_hash", "foreign_scope", "multi_root"])
async def test_invalid_source_cannot_create_editable_scope(tmp_path, invalid):
    await actual_world(tmp_path, invalid=invalid)


@pytest.mark.asyncio
async def test_manual_binding_requires_actual_public_decision_before_route(tmp_path):
    await actual_world(tmp_path, mode="manual")


@pytest.mark.asyncio
async def test_expected_inode_change_before_proposal_cannot_bind(tmp_path):
    state, factory, service, configured, authority, old, root = await archive(tmp_path)
    expected = canonical_workspace_root(root, root_id="original-source").filesystem_identity.identity_hash
    root.rename(configured / "retained-original")
    root.mkdir()
    new = (await service.create_task_scope(CreateTaskScopeRequest("inode-new", "Further work", "Continue original", "inode-new")))["scope_ref"]
    with pytest.raises(WorkspaceBindingError, match="workspace_binding_expected_root_identity_changed"):
        await factory.bind(local_owner_auth(), binding_append=authority).append_binding(
            AppendBindingRequest(new, str(root), "inode-bind", expected))
    with sqlite3.connect(state) as db:
        assert db.execute("SELECT COUNT(*) FROM task_workspace_binding_revisions WHERE task_scope_id=?", (new,)).fetchone()[0] == 0
    assert not (root / "document.txt").exists()
    assert (configured / "retained-original" / "document.txt").read_text() == "original accepted bytes"


@pytest.mark.asyncio
async def test_already_bound_run_rejects_before_new_scope_and_explains_next_run(tmp_path):
    await actual_world(tmp_path, invalid="already_bound")
