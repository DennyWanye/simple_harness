"""New Manual UI public API over the real routed factory, without UI/native claims."""
import sqlite3

import pytest

from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.primary_workspace_bindings import IDENTITY_FIELDS
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.execution.test_completed_scope_workspace_continuation import actual_world


@pytest.mark.asyncio
@pytest.mark.parametrize("control", ["exact", "mixed_identity", "allow_append_fault", "expired"])
async def test_actual_route_manual_public_ui_source(tmp_path, control):
    async def decide(*, factory, authority, state, root):
        service = factory.bind(local_owner_auth(), binding_append=authority)
        primary = (await service.open_primary())["primary_ref"]
        serial = 0

        async def call(operation, request):
            nonlocal serial
            serial += 1
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

        if control == "expired":
            clock = authority._clock_millis
            authority._clock_millis = lambda: item["expires_at_millis"] + 1
            try:
                assert (await pending())["state"] == "expired"
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
        clock = authority._clock_millis
        authority._clock_millis = lambda: item["expires_at_millis"] + 1
        try:
            assert (await pending())["state"] == "bound"
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
