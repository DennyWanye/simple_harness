"""New Manual UI public API over the real routed factory, without UI/native claims."""
import sqlite3
import json
from contextlib import nullcontext

import pytest

from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.primary_workspace_bindings import IDENTITY_FIELDS
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.execution.test_completed_scope_workspace_continuation import actual_world


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["exact", "mixed_identity", "allow_append_fault", "expired", "rebind"])
async def test_actual_route_manual_public_ui_source(tmp_path, control):
    async def decide(*, factory, authority, state, root):
        service = factory.bind(local_owner_auth(), binding_append=authority)
        primary = (await service.open_primary())["primary_ref"]
        serial = 0
        connection = ingress = challenge = None
        if control == "rebind":
            from deskpet.memory.control_binding import HumanMemoryControlBinding
            from tests.companion.test_window_control_credentials import _ingress
            from tests.memory.test_primary_control_binding import _bind
            private, _, _, ingress = _ingress(tmp_path / "signed-control")
            connection = HumanMemoryControlBinding()
            challenge = await _bind(private, ingress, connection)

        async def call(operation, request):
            nonlocal serial
            serial += 1
            with connection.request_scope(ingress, challenge) if connection else nullcontext():
                response = await handle_human_memory_command(
                    {"type": "human_memory_request", "request_id": f"manual-ui:{serial}",
                     "operation": operation, "request": request},
                    factory=factory, auth=local_owner_auth(), binding_append=authority)
            return response["payload"]

        async def pending():
            response = await call("primary.bindings.pending", {"primary_ref": primary})
            assert response["ok"], response
            items = response["result"]["items"]
            assert len(items) == 1
            return items[0]

        async def status(challenge_ref):
            response = await call("primary.bindings.status", {"primary_ref": primary, "challenge_ref": challenge_ref})
            assert response["ok"], response
            assert len(response["result"]["items"]) == 1
            return response["result"]["items"][0]

        item = await pending()
        assert item["state"] == "pending" and item["can_decide"]
        assert item["root_path"] == str(root)
        target = {key: item[key] for key in IDENTITY_FIELDS}
        with sqlite3.connect(state) as db:
            proposal_run = db.execute("SELECT run_id FROM task_workspace_binding_proposals WHERE proposal_hash=?",
                (item["proposal_hash"],)).fetchone()[0]
            assert proposal_run not in {item["run_ref"], item["sdk_run_ref"]}

        if control == "mixed_identity":
            for key, value in (("challenge_hash", "0" * 64), ("effect_ref", "foreign-effect"),
                               ("primary_ref", "foreign-primary"), ("run_ref", "foreign-run")):
                rejected = await call("primary.bindings.decide", {**target, key: value, "decision": "allow"})
                assert not rejected["ok"]
            with sqlite3.connect(state) as db:
                assert db.execute("SELECT COUNT(*) FROM task_workspace_manual_decisions WHERE challenge_id=?",
                    (item["challenge_ref"],)).fetchone()[0] == 0

        if control == "rebind":
            read = authority.manual_binding_read_context
            for operation in ("primary.bindings.pending", "primary.bindings.decide"):
                async def slow_read(**kwargs):
                    result = await read(**kwargs)
                    # Match the readiness loss that lifecycle code exposes
                    # BEFORE waiting for the exclusive revocation lease. A
                    # complete signed bind cannot be awaited inside our own
                    # shared lease; it is performed below after rejection.
                    ingress.identity_gate.unbind(expected_binding_epoch=challenge.binding_epoch)
                    assert not ingress.identity_gate.ready
                    return result
                authority.manual_binding_read_context = slow_read
                try:
                    rejected = await call(operation, {"primary_ref": primary} if operation.endswith("pending") else
                                          {**target, "decision": "allow"})
                    assert not rejected["ok"] and rejected["error"]["code"] == "human_memory_connection_stale"
                finally:
                    authority.manual_binding_read_context = read
                with sqlite3.connect(state) as db:
                    assert db.execute("SELECT COUNT(*) FROM task_workspace_manual_decisions WHERE challenge_id=?",
                        (item["challenge_ref"],)).fetchone()[0] == 0
                connection = HumanMemoryControlBinding()
                challenge = await _bind(private, ingress, connection)
            assert (await pending())["challenge_ref"] == item["challenge_ref"]

        if control == "expired":
            clock = authority._clock_millis
            authority._clock_millis = lambda: item["expires_at_millis"] + 1
            try:
                assert (await status(item["challenge_ref"]))["state"] == "expired"
                assert (await call("primary.bindings.pending", {"primary_ref": primary}))["result"]["items"] == []
                refused = await call("primary.bindings.decide", {**target, "decision": "allow"})
                assert not refused["ok"] and refused["error"]["code"] == "primary_binding_not_actionable"
            finally:
                authority._clock_millis = clock

        if control == "allow_append_fault":
            # Only the Host append is interrupted. The actual preceding Manual
            # decision stays committed; recovery must reuse it, not issue another.
            append = authority._store.append_binding
            async def fault(*args, **kwargs):
                raise RuntimeError("controlled_host_binding_append_failure")
            authority._store.append_binding = fault
            try:
                failed = await call("primary.bindings.decide", {**target, "decision": "allow"})
                assert not failed["ok"]
            finally:
                authority._store.append_binding = append
            recovered_item = await pending()
            assert recovered_item["state"] == "allow_recorded" and recovered_item["can_decide"]
            denied = await call("primary.bindings.decide", {**target, "decision": "deny"})
            assert not denied["ok"] and denied["error"]["code"] == "primary_binding_decision_conflict"
            with sqlite3.connect(state) as db:
                decision_before = db.execute("SELECT decision_json FROM task_workspace_manual_decisions WHERE challenge_id=?",
                    (item["challenge_ref"],)).fetchone()[0]

        allowed = await call("primary.bindings.decide", {**target, "decision": "allow"})
        assert allowed["ok"], allowed
        result = allowed["result"]
        assert result["state"] == "bound" and result["binding_receipt_ref"]
        if control == "exact":
            assert_mismatched_ack_base_rejected(state, service, authority, item)
        clock = authority._clock_millis
        authority._clock_millis = lambda: item["expires_at_millis"] + 1
        try:
            assert (await status(item["challenge_ref"]))["state"] == "bound"
            assert (await call("primary.bindings.pending", {"primary_ref": primary}))["result"]["items"] == []
            replay = await call("primary.bindings.decide", {**target, "decision": "allow"})
            assert replay["ok"] and replay["result"] == result
        finally:
            authority._clock_millis = clock
        if control == "allow_append_fault":
            with sqlite3.connect(state) as db:
                rows = db.execute("SELECT decision_json FROM task_workspace_manual_decisions WHERE challenge_id=?",
                    (item["challenge_ref"],)).fetchall()
                assert rows == [(decision_before,)]
        # Resume/edit is done by the actual subsequent model tool path, not by
        # this UI decision hook; actual_world verifies the old/new Scope/files.
        return {"status": result["state"], "receipt_ref": result["binding_receipt_ref"]}

    await actual_world(tmp_path, mode="manual", manual_decider=decide)


async def reopen_host(tmp_path):
    """New authority/store objects; callers never borrow the former runtime's cache."""
    from deskpet.capabilities.store import CapabilityStore
    from deskpet.execution.foreground_queue import ForegroundQueueStore
    from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory
    from deskpet.memory.schema import dispatch_startup_epoch
    from deskpet.task_scope.runtime_binding_authority import WorkspaceBindingRuntimeAuthority
    state = tmp_path / "state.db"
    authority = WorkspaceBindingRuntimeAuthority(state, subject=local_owner_auth().subject,
        foreground=ForegroundQueueStore(state), policy=CapabilityStore(tmp_path / "policy.db"),
        configured_workspace_root=tmp_path / "configured")
    factory = HumanMemoryHostServiceFactory(state, await dispatch_startup_epoch(state, approved_fresh_lane=False))
    return state, factory, authority, factory.bind(local_owner_auth(), binding_append=authority)


@pytest.mark.asyncio
async def test_cold_process_reads_original_bound_and_replays_without_second_decision(tmp_path):
    import asyncio
    import subprocess
    import sys
    # All runtime/SDK/client objects are closed by this real execution fixture.
    saved = {}
    async def decide(**world):
        service = world["factory"].bind(local_owner_auth(), binding_append=world["authority"])
        primary = (await service.open_primary())["primary_ref"]
        item = (await service.list_primary_bindings(primary_ref=primary))["items"][0]
        saved["target"] = {key: item[key] for key in IDENTITY_FIELDS}
        result = await service.respond_primary_binding(**saved["target"], decision="allow")
        saved["result"] = result
        return {"status": result["state"], "receipt_ref": result["binding_receipt_ref"]}
    await actual_world(tmp_path, mode="manual", manual_decider=decide)
    with sqlite3.connect(tmp_path / "state.db") as db:
        before = db.execute("SELECT decision_json FROM task_workspace_manual_decisions ORDER BY receipt_id").fetchall()
    code = '''
import asyncio, json, sys
from pathlib import Path
sys.path[:0] = json.loads(sys.argv[1])
from tests.execution.test_primary_workspace_binding_ui import reopen_host
async def main():
    _, _, _, service = await reopen_host(Path(sys.argv[2]))
    target = json.loads(sys.argv[3])
    status = await service.read_primary_binding(primary_ref=target["primary_ref"], challenge_ref=target["challenge_ref"])
    replay = await service.respond_primary_binding(**target, decision="allow")
    print(json.dumps({"status": status, "replay": replay}))
asyncio.run(main())
'''
    child = await asyncio.to_thread(subprocess.run,
        [sys.executable, "-I", "-B", "-c", code, json.dumps(sys.path), str(tmp_path), json.dumps(saved["target"])],
        capture_output=True, text=True, timeout=20)
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert result["replay"] == saved["result"]
    assert result["status"]["items"][0]["binding_receipt_ref"] == saved["result"]["binding_receipt_ref"]
    assert result["status"]["items"][0]["state"] == "bound"
    with sqlite3.connect(tmp_path / "state.db") as db:
        assert db.execute("SELECT decision_json FROM task_workspace_manual_decisions ORDER BY receipt_id").fetchall() == before


@pytest.mark.asyncio
async def test_pending_pages_exclude_resolved_history_and_preserve_same_time_cursor(tmp_path):
    from deskpet.memory.human_memory_service import AppendBindingRequest, CreateTaskScopeRequest, DecideManualBindingRequest
    from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
    # One real executed foreground chain supplies its authentic Run binding.
    # The following extra records are explicit Host-journal projection fixtures,
    # not assertions that a model physically executed 67 additional tools.
    await actual_world(tmp_path, mode="manual")
    state, factory, authority, service = await reopen_host(tmp_path)
    primary = (await service.open_primary())["primary_ref"]
    with sqlite3.connect(state) as db:
        sdk_run = db.execute("SELECT sdk_run_id FROM foreground_run_sdk_bindings").fetchone()[0]
    ledger = ContextRouteLedgerStore(state, clock=lambda: 1900000000.0)  # all ties are deliberate
    original_clock = authority._clock_millis
    now = original_clock()
    expected = set()
    history = set()
    try:
        for kind, count in (("pending", 33), ("bound", 17), ("expired", 17)):
            for ordinal in range(count):
                key = f"page-{kind}-{ordinal:02}"
                effect = f"fixture-{key}"
                authority._clock_millis = lambda: now - (600_000 if kind == "expired" else 0)
                scope = (await service.create_task_scope(CreateTaskScopeRequest(key, key, "Projection fixture", key)))["scope_ref"]
                root = tmp_path / "configured" / key
                root.mkdir()
                outcome = await service.append_binding(AppendBindingRequest(scope, str(root), f"context-route:{sdk_run}:{effect}"))
                await ledger.record_tool_invocation(sdk_run_id=sdk_run, raw_call_id=key, effect_id=effect,
                    proposal={"route": "create_new", "title": key}, verdict="rejected", decision_id=None,
                    detail={"code": "context_route_binding_authorization_required", "task_scope_id": scope,
                            "binding_challenge": dict(outcome)})
                if kind == "bound":
                    bound = await service.decide_manual_binding(DecideManualBindingRequest(outcome["challenge_ref"], "allow", key))
                    assert bound["status"] == "bound"
                (expected if kind == "pending" else history).add(outcome["challenge_ref"])
    finally:
        authority._clock_millis = original_clock
    first = await service.list_primary_bindings(primary_ref=primary)
    assert len(first["items"]) == 32 and first["next_cursor"] and not first["truncated"]
    seen = {i["challenge_ref"] for i in first["items"]}
    assert seen <= expected and seen.isdisjoint(history)
    # Resolve an item in page one; immutable cursor remains usable, not offset-based.
    one = first["items"][0]
    denied = await service.respond_primary_binding(**{k: one[k] for k in IDENTITY_FIELDS}, decision="deny")
    assert denied["state"] == "denied"
    status = await service.read_primary_binding(primary_ref=primary, challenge_ref=one["challenge_ref"])
    assert status["items"][0]["state"] == "denied" and not status["items"][0]["can_decide"]
    with pytest.raises(Exception, match="primary_binding_decision_conflict"):
        await service.respond_primary_binding(**{k: one[k] for k in IDENTITY_FIELDS}, decision="allow")
    second = await service.list_primary_bindings(primary_ref=primary, cursor=first["next_cursor"])
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert seen.isdisjoint({i["challenge_ref"] for i in second["items"]})
    assert seen | {i["challenge_ref"] for i in second["items"]} == expected
    with pytest.raises(Exception, match="primary_binding_cursor_invalid"):
        await service.list_primary_bindings(primary_ref=primary, cursor="unknown-invocation")


def assert_mismatched_ack_base_rejected(state, service, authority, item):
    """A rehashed malicious read record is not a real grant transition.

    Only the Host read response is substituted; the durable fixture is untouched.
    This was accepted by the former four-field hand comparison.
    """
    from dataclasses import replace
    from simple_harness import ManualWorkspaceBindingChallenge, WorkspaceBindingProposal, WorkspaceBindingSetReceipt
    with sqlite3.connect(state) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM task_workspace_binding_revisions WHERE task_scope_id=?",
                         (item["scope_ref"],)).fetchone()
        ack = WorkspaceBindingSetReceipt.from_json(json.loads(row["receipt_json"]))
        forged = replace(ack, base_binding_set_revision=1, binding_set_revision=2,
                         parent_receipt_id=ack.receipt_id, parent_receipt_hash=ack.receipt_hash,
                         previous_root_set_digest=ack.root_set_digest)
        challenge = ManualWorkspaceBindingChallenge.from_json(json.loads(db.execute(
            "SELECT challenge_json FROM task_workspace_manual_challenges WHERE challenge_id=?",
            (item["challenge_ref"],)).fetchone()[0]))
        proposal = WorkspaceBindingProposal.from_json(json.loads(db.execute(
            "SELECT proposal_json FROM task_workspace_binding_proposals WHERE proposal_id=?",
            (challenge.proposal_id,)).fetchone()[0]))
        class ReadSubstitution:
            def execute(self, query, args):
                if query == "SELECT * FROM task_workspace_binding_revisions WHERE grant_id=?":
                    assert args == (ack.grant_id,)
                    class Result:
                        def fetchone(self):
                            return {**dict(row), "receipt_json": json.dumps(forged.to_json()), "receipt_hash": forged.receipt_hash}
                    return Result()
                return db.execute(query, args)
        with pytest.raises(ValueError, match="differs from grant"):
            service._primary_workspace_bindings()._decision_state(ReadSubstitution(), challenge, proposal,
                {"mode": "manual", "now_millis": authority._clock_millis()})
