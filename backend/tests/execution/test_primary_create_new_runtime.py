"""Fresh active-None CREATE_NEW through production binding, tools and terminal."""
import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from simple_harness import CallId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
from deskpet.capabilities.store import CapabilityStore, initialize_capability_database
from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.task_scope.runtime_binding_authority import WorkspaceBindingRuntimeAuthority
from tests.execution.test_primary_foreground_runtime import Provider, build


class CreateProvider(Provider):
    def __init__(self):
        super().__init__()
        self.route_result = None

    async def invoke(self, request, *, cancel):
        n = len(self.requests)
        self.requests.append(request)
        values = [json.loads(m.content)["value"] for m in request.messages
                  if m.role.value == "tool" and isinstance(m.content, str) and isinstance(json.loads(m.content).get("value"), dict)]
        if n == 0:
            name, args = "context_route", {"route": "create_new", "title": "Fresh project", "goal": "Write actual output"}
        elif n == 1:
            self.route_result = values[-1] if values else {"error": "route rejected"}
            if "context_route_receipt" not in self.route_result:
                return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Binding needs attention"),
                                        model="model", usage=ProviderUsage(10, 10, 20))
            name, args = "tool_search", {"query": "write_file"}
        elif n == 2:
            name, args = "tool_describe", {"capability_id": values[-1]["matches"][0]["capability_id"]}
        elif n == 3:
            name, args = "tool_activate", {k: values[-1][k] for k in ("capability_id", "schema_hash", "describe_nonce")}
        elif n == 4:
            name, args = "write_file", {"path": "fresh.txt", "content": "created in exact task root"}
        elif n == 5:
            instruction = next(json.loads(m.content) for m in request.messages if m.role.value == "system"
                and isinstance(m.content, str) and '"task_scope_closure_required"' in m.content)
            name, args = "task_scope_update", {"outcome": "no_mutation", "base_revision": instruction["current_revision"],
                "closure_reason": "File written; metadata unchanged.", "evidence_refs": instruction["allowed_evidence_refs"],
                "idempotency_key": "create-close"}
        else:
            return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "Created and written"),
                                    model="model", usage=ProviderUsage(10, 10, 20))
        return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, name),
            tool_calls=(ProviderToolCall(CallId(f"create-{n}"), name, args),), model="model", usage=ProviderUsage(10, 10, 20))


async def fixture(tmp_path, mode="auto"):
    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    factory = HumanMemoryHostServiceFactory(state, startup)
    service = factory.bind(local_owner_auth())
    await service.open_primary()
    configured = tmp_path / "configured"
    configured.mkdir()
    policy_path = await initialize_capability_database(tmp_path / "policy.db")
    policy = CapabilityStore(policy_path)
    old = await policy.get_policy_state()
    if old.mode != mode:
        await policy.compare_and_set_policy_mode(mode, expected_generation=old.generation)
    authority = WorkspaceBindingRuntimeAuthority(state, subject=local_owner_auth().subject,
        foreground=ForegroundQueueStore(state), policy=policy, configured_workspace_root=configured)
    return state, factory, service, configured, authority


@pytest.mark.asyncio
async def test_active_primary_create_new_auto_real_binding_effect_terminal(tmp_path):
    state, factory, service, configured, authority = await fixture(tmp_path)
    await service.enqueue_turn(QueueTurnRequest(None, "fresh-project", "Create a new project and write its file"))
    provider = CreateProvider()
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
        binding_authority=authority, configured_root=configured)
    try:
        assert await asyncio.wait_for(runtime._drive_once(), 20)
        with sqlite3.connect(state) as db:
            db.row_factory = sqlite3.Row
            run = db.execute("SELECT * FROM foreground_runs").fetchone()
            assert run["task_scope_id"] is None
            receipts = db.execute("SELECT receipt_json FROM task_workspace_binding_revisions").fetchall()
            assert len(receipts) == 1, [r[0] for r in db.execute("SELECT detail_json FROM context_route_tool_invocations").fetchall()]
            binding = json.loads(receipts[0][0])
            proposal = json.loads(db.execute("SELECT proposal_json FROM task_workspace_binding_proposals").fetchone()[0])
            assert proposal["run_id"] == run["host_run_id"]  # no pre-admission/bootstrap Run
            request = json.loads(db.execute("SELECT request_json FROM task_workspace_run_mode_snapshots").fetchone()[0])
            assert request["run_id"] == run["host_run_id"]
            assert request["context_snapshot_id"] == run["context_snapshot_id"]
            assert request["context_snapshot_hash"] == run["context_snapshot_hash"]
            assert request["task_scope_id"] == binding["task_scope_id"]
            assert db.execute("SELECT COUNT(*) FROM task_workspace_manual_challenges").fetchone()[0] == 0
            root = Path(proposal["root"]["canonical_path"])
            assert root.parent == configured.resolve() and root != configured.resolve()
            assert root.name == f"task-{binding["task_scope_id"]}"
            assert (root / "fresh.txt").read_text() == "created in exact task root"
            assert db.execute("SELECT terminal_state FROM foreground_terminal_receipts").fetchone()[0] == "COMPLETED"
            assert db.execute("SELECT COUNT(*) FROM task_scopes").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM task_scope_terminal_gate_receipts").fetchone()[0] == 1
            assert provider.route_result["context_route_receipt"]["task_scope_id"] == binding["task_scope_id"]
        assert await queue.current_snapshot(local_owner_auth().subject) is None
        assert authority._primary_target.get() is None
        await runtime.close()
        await stack.close()
        await service.enqueue_turn(QueueTurnRequest(None, "fresh-project", "Create a new project and write its file"))
        runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
            binding_authority=authority, configured_root=configured)
        assert not await runtime._drive_once()
        assert len(provider.requests) == 7
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT COUNT(*) FROM task_workspace_binding_revisions").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM task_scopes").fetchone()[0] == 1
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
async def test_create_new_manual_returns_real_durable_proposal_challenge(tmp_path):
    from types import SimpleNamespace
    from simple_harness import RunId
    from deskpet.sdk_adapters.context_route import ContextRouteToolService
    from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
    from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore
    from deskpet.memory.human_memory_service import DecideManualBindingRequest
    state, factory, _, configured, authority = await fixture(tmp_path, "manual")
    # Manual binding itself does not require a running SDK Run. Use its real
    # Host service/authority/proposal/challenge/decision path; no UI claim.
    route = ContextRouteToolService(service_factory_getter=lambda: factory,
        binding_store_factory=lambda: WorkspaceBindingAuthorityStore(state, configured_workspace_root=configured),
        binding_append_getter=lambda: authority, ledger=ContextRouteLedgerStore(state),
        tool_context_getter=lambda: SimpleNamespace(run_id=RunId("manual-route"), effect_id=SimpleNamespace(value="manual-effect"),
            task_execution_envelope=SimpleNamespace(raw_call_id="manual-call", turn_ordinal=1)))
    result = await route.handle_context_route({"route": "create_new", "title": "Manual project", "goal": "Real proposal"})
    assert result["error"]["code"] == "context_route_binding_authorization_required", result
    challenge = result["error"]["binding_challenge"]
    assert challenge["status"] == "authorization_required"
    with sqlite3.connect(state) as db:
        row = db.execute("SELECT envelope_sha256,payload_json FROM human_memory_evidence WHERE evidence_id=?",
                         (challenge["evidence_ref"],)).fetchone()
        assert row is not None
        payload = json.loads(row[1])
        assert payload["action"] == "binding.append"
        assert payload["scope_ref"] == challenge["scope_ref"]
        assert Path(payload["root"]).parent == configured.resolve()
        assert db.execute("SELECT COUNT(*) FROM task_workspace_binding_revisions").fetchone()[0] == 0
        before = db.execute("SELECT evidence_id,envelope_sha256 FROM human_memory_evidence ORDER BY evidence_id").fetchall()
    service = factory.bind(local_owner_auth(), binding_append=authority)
    decided = await service.decide_manual_binding(DecideManualBindingRequest(challenge["challenge_ref"], "allow", "allow-manual"))
    assert decided["status"] == "bound" and decided["binding_set_revision"] == 1
    with sqlite3.connect(state) as db:
        after = dict(db.execute("SELECT evidence_id,envelope_sha256 FROM human_memory_evidence").fetchall())
        assert all(after[eid] == digest for eid, digest in before)
        assert db.execute("SELECT COUNT(*) FROM task_workspace_manual_decisions").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM task_workspace_binding_revisions").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_active_primary_create_auto_rechecks_lease_before_binding(tmp_path, monkeypatch):
    from deskpet.execution.foreground_queue import ForegroundQueueError
    state, _, service, configured, authority = await fixture(tmp_path)
    await service.enqueue_turn(QueueTurnRequest(None, "lease-lost", "Create a fresh project"))
    provider = CreateProvider()
    runtime, stack, _ = await build(tmp_path, state, provider, dynamic=True,
        binding_authority=authority, configured_root=configured)
    original = authority._store.append_binding
    async def revoke_then_append(proposal, grant, **kwargs):
        current = await authority._foreground.current_snapshot(local_owner_auth().subject)
        assert current.task_scope_id is None and proposal.run_id == current.host_run_id
        await authority._foreground.close_current_lease(host_run_id=current.host_run_id, owner_id=current.owner_id,
            generation=current.generation, idempotency_key="close-before-binding")
        return await original(proposal, grant, **kwargs)
    monkeypatch.setattr(authority._store, "append_binding", revoke_then_append)
    try:
        with pytest.raises(ForegroundQueueError, match="lease"):
            await asyncio.wait_for(runtime._drive_once(), 20)
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT COUNT(*) FROM task_workspace_binding_revisions").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM foreground_terminal_receipts").fetchone()[0] == 0
        assert not list(configured.glob("*/fresh.txt"))
        assert authority._primary_target.get() is None
    finally:
        await runtime.close()
        await stack.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,bad", [("interaction_evidence_id", "made-up-proposal"), ("interaction_evidence_hash", "0" * 64)])
async def test_active_primary_auto_rejects_fabricated_proposal(tmp_path, monkeypatch, field, bad):
    state, _, service, configured, authority = await fixture(tmp_path)
    await service.enqueue_turn(QueueTurnRequest(None, "forged-proposal", "Create a project"))
    original = authority.append_binding
    rejected = []
    async def corrupt_proof(**kwargs):
        from deskpet.task_scope.workspace_bindings import WorkspaceBindingError
        kwargs[field] = bad
        try:
            return await original(**kwargs)
        except WorkspaceBindingError as exc:
            rejected.append(exc.code)
            raise
    monkeypatch.setattr(authority, "append_binding", corrupt_proof)
    runtime, stack, _ = await build(tmp_path, state, CreateProvider(), dynamic=True,
        binding_authority=authority, configured_root=configured)
    try:
        assert await asyncio.wait_for(runtime._drive_once(), 20)
        assert rejected == ["workspace_binding_primary_target_evidence_not_durable"]
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT COUNT(*) FROM task_workspace_binding_revisions").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM task_workspace_binding_grants").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM context_route_decisions WHERE task_scope_id IS NOT NULL").fetchone()[0] == 0
        assert authority._primary_target.get() is None
    finally:
        await runtime.close()
        await stack.close()
