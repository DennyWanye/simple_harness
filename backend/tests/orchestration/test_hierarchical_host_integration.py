"""Final Host integration: actual creation/recovery and transport identity."""
import pytest

from deskpet.orchestration.handlers import handle
from agent_orchestrator.testing.word_counter import FixtureWordCounter
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
            def broken(*args, **kwargs):
                raise RuntimeError("root preparation failed")
            import agent_orchestrator.deployment.assembly as deployment_assembly
            scoped.setattr(deployment_assembly, "initialize_root", broken)
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


@pytest.mark.asyncio
async def test_the_desktop_registers_sub_goal_types_by_level(orchestration_root, principal):
    """HTN 精简 片 B：中间目标按层注册两个类型。类型不声明判据（子目标按上级做法交给它的要求
    审），层级决定能往下放什么——根目标 0 层，第一层子目标里还能再放第二层，第二层只能放步骤；
    层数上限就是注册了几层，由 SDK 的注册检查保证。"""
    service = OrchestrationService(orchestration_root, OrchestrationSettings(),
        provider=notes_provider(), principal=principal, drive=False, native_test_counter=FixtureWordCounter())
    await service.start()
    try:
        created = service.create_mission(notes_request("htn-levels"))
        world = service._orchestrator._dispatch_for(created["mission_id"]).require_planning_world()
        compound = {spec.task_type_ref.id: spec for spec in world.catalog.task_types()
                    if str(spec.form) == "compound"}
        assert {name: spec.refinement_level for name, spec in compound.items()} == {
            "desktop.user-goal": 0, "desktop.sub-goal-1": 1, "desktop.sub-goal-2": 2}
        for name in ("desktop.sub-goal-1", "desktop.sub-goal-2"):
            assert compound[name].goal_signature.coverage_criteria == ()
            assert compound[name].operator_ref is None
            assert world.schemas.resolve(compound[name].parameter_schema_ref) is not None
            # a later step takes what the sub-goal produced through this port (its finalizer's delivery)
            assert [port.port_key for port in compound[name].output_ports] == ["delivery"]
        # the root goal still declares the user's content requirements
        assert compound["desktop.user-goal"].goal_signature.coverage_criteria
    finally:
        await service.close()
