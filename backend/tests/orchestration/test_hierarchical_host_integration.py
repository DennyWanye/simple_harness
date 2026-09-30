"""Final Host integration: actual creation/recovery and transport identity."""
import pytest

from deskpet.orchestration.handlers import handle
from deskpet.orchestration.native_fixture import FixtureWordCounter
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings
from ._support import notes_provider, notes_request


@pytest.mark.asyncio
async def test_planning_request_id_survives_transport_dispatch():
    class Service:
        def status(self):
            return {"state": "available"}

        def planning_authorization(self, body):
            assert body == {"operation": "issue", "mission_id": "m1",
                            "request_id": "durable-request", "command_id": "grant-command"}
            return {"issued": True}

    result = await handle(Service(), "mission_planning_authorization",
        {"operation": "issue", "mission_id": "m1", "request_id": "durable-request",
         "command_id": "grant-command"}, request_id="transport-request")
    assert result["payload"] == {"request_id": "transport-request", "ok": True,
                                 "data": {"issued": True}}


@pytest.mark.asyncio
async def test_host_roots_are_atomic_isolated_and_recoverable(orchestration_root, principal, monkeypatch):
    from agent_orchestrator.storage.htn_store import HtnStore
    from deskpet.orchestration import hierarchical

    service = OrchestrationService(orchestration_root, OrchestrationSettings(),
        provider=notes_provider(), principal=principal, drive=False, native_test_counter=FixtureWordCounter())
    await service.start()
    try:
        first = service.create_mission(notes_request("htn-first"))
        second = service.create_mission(notes_request("htn-second", goal="Write OTHER.md"))
        loop = service._orchestrator
        worlds = [loop._dispatch_for(r["mission_id"]).require_planning_world() for r in (first, second)]
        assert worlds[0] is not worlds[1]
        assert worlds[0].mission_id == first["mission_id"]
        assert worlds[1].mission_id == second["mission_id"]
        requirements = HtnStore(loop.store).latest_requirements_revision(first["mission_id"])
        assert requirements.criteria[0].statement == "file:NOTES.md"
        await loop._start_planning(loop.store.get_mission(first["mission_id"]))
        assert not loop.store.list_intents("PENDING", "CLAIMED", "AGENT_CREATED")
        changes = loop.store.connection.total_changes
        assert service.create_mission(notes_request("htn-first"))["created"] is False
        assert loop.store.connection.total_changes == changes
        with monkeypatch.context() as scoped:
            def broken(*args):
                raise RuntimeError("root preparation failed")
            scoped.setattr(hierarchical, "initialize_root", broken)
            with pytest.raises(RuntimeError, match="root preparation failed"):
                service.create_mission(notes_request("htn-rollback"))
        assert loop.store.find_mission(service.tenant_id, "htn-rollback") is None
        await service._rebuild()
        recovered = service._orchestrator
        assert HtnStore(recovered.store).latest_requirements_revision(first["mission_id"]) == requirements
        assert recovered._dispatch_for(first["mission_id"]).require_planning_world().mission_id == first["mission_id"]
        assert len(recovered.store.list_missions()) == 2
    finally:
        await service.close()
