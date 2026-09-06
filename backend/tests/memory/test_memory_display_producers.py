"""Actual Host lane → public SDK job apply → content-free UI producer.

Only provider transport is deterministic; no fake runner outcome or SDK SQL.
"""

import json

import pytest
from deskpet.memory.display_invalidation import MemoryDisplayInvalidation
from deskpet.memory.memory_ingestion_outbox import MemoryAnalysisLane
from tests.memory.test_semantic_correction import memory_env
from tests.sdk_adapters import s5b_closure_harness as ch
from tests.sdk_adapters import s5b_memory_harness as mh


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["revise", "no_mutation", "notification_failure"])
async def test_real_runner_completion_emits_refresh_and_reopen_is_idle(tmp_path, mode):
    env = await mh.bound_turn_run(
        tmp_path, "graph-original", text="My preferred drink is coffee."
    )
    await mh.finish_clean_run(env)
    adapter = ch.FakeAdapter(
        [
            mh.proposal_call(
                [
                    mh.semantic_op(
                        mh.item_id(env),
                        "My preferred drink is coffee.",
                        predicate="preferred_drink",
                        object_value="coffee",
                    )
                ]
            )
        ]
    )
    menv = memory_env(env, adapter)
    events = []

    async def broadcast(event):
        events.append(event)
        if mode == "notification_failure":
            raise RuntimeError("transport unavailable")

    changes = MemoryDisplayInvalidation(broadcast)

    def lane(m):
        return MemoryAnalysisLane(
            worker=m.worker,
            runtime=m.runtime,
            executor=m.executor,
            config=m.config,
            worker_id=m.worker_id,
            clock=m.clock,
            display_invalidation=changes,
        )

    try:
        assert str((await lane(menv).tick())[1]) == "applied"
        initial = await (await menv.runtime.manager()).get_twin_graph_view(
            principal=menv.runtime.principal()
        )
        assert len(initial.nodes) == 1 and initial.nodes[0].revision == 1
    finally:
        await mh.close(menv)
    current = await mh.next_turn_run(
        env,
        "graph-correction",
        text="Correct my preferred drink from coffee to tea.",
        delivery_key="graph-correct",
    )
    await mh.finish_clean_run(current)

    class Correct:
        calls = 0

        async def invoke(self, request, *, cancel):
            self.calls += 1
            if mode == "no_mutation":
                return mh.proposal_call([], outcome="no_mutation")
            body = json.loads(request.messages[-1].content.split("\n", 1)[1])
            (candidate,) = body["semantic_candidates"]
            operation = mh.semantic_op(
                mh.item_id(current),
                "Correct my preferred drink from coffee to tea.",
                predicate="preferred_drink",
                object_value="tea",
            )
            operation.update(
                action="revise_semantic", candidate_key=candidate["candidate_key"]
            )
            return mh.proposal_call(
                [operation], provider_request_id="graph-correction-response"
            )

    correct = Correct()
    menv = memory_env(current, correct)
    try:
        runner = lane(menv)
        assert str((await runner.tick())[1]) == "applied"
        view = await (await menv.runtime.manager()).get_twin_graph_view(
            principal=menv.runtime.principal()
        )
        assert len(view.nodes) == 1
        assert view.nodes[0].memory_id == initial.nodes[0].memory_id
        assert view.nodes[0].revision == (1 if mode == "no_mutation" else 2)
        assert events == [{"type": "human_memory_changed", "payload": {}}] * 2
        assert changes.generation == 2 and correct.calls == 1
        assert str((await runner.tick())[1]) == "idle"
        assert len(events) == 2
    finally:
        await mh.close(menv)
    menv = memory_env(current, correct)
    try:
        assert str((await lane(menv).tick())[1]) == "idle"
        assert len(events) == 2 and correct.calls == 1
    finally:
        await mh.close(menv)


@pytest.mark.asyncio
async def test_actual_main_lane_activation_consumes_shared_notification_instance(
    tmp_path, monkeypatch
):
    import main

    env = await mh.bound_turn_run(
        tmp_path, "graph-startup", text="My preferred drink is coffee."
    )
    menv = memory_env(env, ch.FakeAdapter([]))

    async def broadcast(_):
        pass

    changes = MemoryDisplayInvalidation(broadcast)
    monkeypatch.setattr(main, "_state_db_path", env.db_path)
    monkeypatch.setattr(main, "_memory_analysis_lane", None)
    monkeypatch.setattr(main, "_provider_registry", None)
    monkeypatch.setattr(MemoryAnalysisLane, "start", lambda self: None)
    slots = {
        name: main.service_context.get(name)
        for name in (
            "human_memory_v7_runtime",
            "sdk_provider_binding_resolver",
            "sdk_evidence_authority",
            "sdk_memory_analysis_executor",
            "sdk_memory_ingestion_outbox",
            "human_memory_host_service_factory",
        )
    }
    try:
        main.service_context.register("human_memory_v7_runtime", menv.runtime)
        main.service_context.register("sdk_provider_binding_resolver", object())
        from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory
        from deskpet.memory.schema import dispatch_startup_epoch

        startup = await dispatch_startup_epoch(env.db_path, approved_fresh_lane=True)
        factory = HumanMemoryHostServiceFactory(
            env.db_path, startup, display_invalidation=changes
        )
        main.service_context.register("human_memory_host_service_factory", factory)
        main._activate_memory_analysis_lane()
        lane = main.service_context.get("sdk_memory_ingestion_outbox")
        assert isinstance(lane, MemoryAnalysisLane)
        assert lane._display_invalidation is changes
        assert lane.short_indexer.runtime is menv.runtime
        assert lane.short_indexer.authority is menv.runtime.conversation_evidence_authority
    finally:
        for name, value in slots.items():
            main.service_context.register(name, value)
        await mh.close(menv)
