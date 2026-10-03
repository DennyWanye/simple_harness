"""OC2: a real accepted upstream leaf is a DATA source under the new protocol.

The Mission runs on the product deployment (``taskgraph_exec.production_fixture``) with a
two-step chain: ``write`` delivers on its ``delivery`` port, ``continue`` consumes it.  The
upstream leaf really runs (scripted Worker, independent review, original acceptance) — its
acceptance is the durable pin the downstream input freezes.  The completion Spec is
CONTENT_ONLY (the product's auto mode); MIXED preparation waiting is covered elsewhere.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

_FULL_TARGET = Path(__file__).resolve().parents[1]
for _extra in (_FULL_TARGET, _FULL_TARGET / "taskgraph_exec"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from production_fixture import (  # noqa: E402
    CHAIN_CRITERIA,
    chain_planner,
    enabled_world,
    product_loop,
    result_envelope,
    scripted_worker,
)

from agent_orchestrator.artifacts.input_bindings import TargetRules  # noqa: E402
from agent_orchestrator.artifacts.versioning import manifest_upstream_inputs  # noqa: E402
from agent_orchestrator.contracts.htn import TaskForm  # noqa: E402
from agent_orchestrator.contracts.models import sha256_hex  # noqa: E402
from agent_orchestrator.contracts.state_machines import TaskStatus  # noqa: E402
from agent_orchestrator.orchestrator.commit_service import Reservation  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.testing.fixtures import RoleScriptedProvider, package_of  # noqa: E402

#: The extra workspace file the upstream Worker writes and delivers, per overlay case.  The
#: upstream step writes (it is not read-only), so a new ``tests/`` file of it is laid on the
#: consumer's baseline; the read-only half of that rule has its own unit case
#: (``test_criteria_driven_write_step``) — this deployment's world has no read-only leaf.
EXTRA = {"none": None, "seed": "target.py", "new-test": "tests/test_extra.py"}


def _upstream_worker(extra: str | None):  # type: ignore[no-untyped-def]
    """The upstream Worker: its declared output, plus (per case) one more accepted file."""

    def write_extra(request):  # type: ignore[no-untyped-def]
        return ("workspace_write_file", {"path": extra, "content": "# accepted upstream workspace file\n"})

    def submit(request):  # type: ignore[no-untyped-def]
        body = json.loads(result_envelope(request)[len("<result_envelope>"):-len("</result_envelope>")])
        body["artifacts"] = [*body["artifacts"], extra]
        return "<result_envelope>" + json.dumps(body, ensure_ascii=False) + "</result_envelope>"

    if extra is None:
        return scripted_worker()

    def declared(request):  # type: ignore[no-untyped-def]
        [output] = package_of(request)["task_contract"]["outputs"]
        return ("workspace_write_file", {"path": output, "content": '{"failing_test":"tests/test_kv.py"}\n'})

    return scripted_worker(write_extra, declared, submit)


def _forged_attempt(loop, task_id, inputs):  # type: ignore[no-untyped-def]
    return loop.commit.create_attempt(
        task_id, role="worker", model="fixture-worker", prompt_version="fixture-worker-v1",
        context_version="oc2-data-consumer-v1", reservation=Reservation(tokens=1_000),
        intent_config={"message": "consume the delivery"}, input_hash=sha256_hex("oc2-data-consumer"),
        inputs=inputs)


@pytest.mark.parametrize("overlay", ["none", "seed", "new-test"])
def test_oc2_data_dispatch_freezes_nonempty_accepted_input_and_never_uses_order_only(
    tmp_path: Path, overlay: str, monkeypatch,
) -> None:
    """OC2: DATA becomes readable from a real accepted source; an unbound edge stays open."""

    async def case() -> None:
        request = {"workspace_seed": {"target.py": "# seed"}} if overlay == "seed" else None
        async with enabled_world(tmp_path, key="oc2-data-dispatch", planner=chain_planner, criteria=CHAIN_CRITERIA,
                                 worker=_upstream_worker(EXTRA[overlay]), request=request) as world:
            loop, mission = world.loop, world.mission
            await world.commit_seed()
            dispatch = world.dispatch
            network = dispatch.network(mission.id)
            [data_edge] = network.data_requirements
            producer_occurrence = network.occurrence(data_edge.producer_occurrence)
            consumer_occurrence = network.occurrence(data_edge.consumer_occurrence)
            assert producer_occurrence.form is TaskForm.PRIMITIVE
            assert consumer_occurrence.form is TaskForm.PRIMITIVE
            assert data_edge.output_port == "delivery"

            # The adopted plan's relation alone cannot materialise a source.  This
            # is the pre-preparation oracle for the pure ORDER / no-artifact case.
            before = dispatch.resolved_inputs(mission.id, network, consumer_occurrence)
            assert before.manifest is not None and not before.manifest.is_frozen
            assert before.manifest.bindings == ()
            assert before.manifest.pending

            producer_task = str(producer_occurrence.task_id)
            consumer_task = str(consumer_occurrence.task_id)
            # The upstream leaf runs for real: Worker, independent review, acceptance.
            await world.until(lambda: loop.store.get_task(producer_task).status is TaskStatus.COMPLETED)
            producer = loop.store.get_task(producer_task)
            [acceptance] = [e.payload for e in loop.store.list_events(mission.id)
                            if e.type == "AcceptanceCommitted" and e.task_id == producer_task]
            assert loop.store.list_attempts(consumer_task) == []  # not dispatched yet

            dispatch.issue_input_witnesses(mission.id, network, now_ms=int(loop.store.now * 1000))
            resolved = dispatch.resolved_inputs(mission.id, network, consumer_occurrence)
            assert resolved.manifest is not None and resolved.manifest.is_frozen
            assert len(resolved.manifest.bindings) == 1
            bound = resolved.manifest.bindings[0]
            assert bound.artifact_id in producer.accepted_artifacts
            assert bound.acceptance_id == acceptance["acceptance_id"]  # pinned to the real acceptance
            artifact = loop.store.get_artifact(bound.artifact_id)

            rules = dispatch.target_rules or TargetRules(namespace=f"workspace:{consumer_task}")
            upstream = manifest_upstream_inputs(resolved.manifest, rules, network=network)
            assert len(upstream) == 1
            assert upstream[0].artifact_id == artifact.id
            assert upstream[0].content_hash == artifact.content_hash

            upstream = dispatch.overlay_attempt_inputs(mission.id, upstream)
            assert len(upstream) == (1 if overlay == "none" else 2)
            if overlay != "none":
                overlaid = next(item for item in upstream if item.path == EXTRA[overlay])
                # the accepted upstream file, not a seed copy
                assert overlaid.artifact_id in producer.accepted_artifacts

            # A caller cannot use the reviewed artifact at a mount the adopted
            # DATA manifest did not select: the TaskGraph dispatch binding refuses the input
            # set (the Mission is bound at creation). The refusal leaves no new Attempt.
            forged = [{**upstream[0].to_json(), "path": "forged/report.json"}]
            with pytest.raises(Exception, match="TASKGRAPH_DISPATCH_INPUT_SET_MISMATCH"):
                _forged_attempt(loop, consumer_task, forged)
            assert loop.store.list_attempts(consumer_task) == []

            if overlay == "seed":
                original_get = loop.store.get_artifact
                overlay_id = overlaid.artifact_id

                def wrong_owner(artifact_id):  # type: ignore[no-untyped-def]
                    found = original_get(artifact_id)
                    return replace(found, mission_id="foreign-mission") if artifact_id == overlay_id else found

                with monkeypatch.context() as patch:
                    patch.setattr(loop.store, "get_artifact", wrong_owner)
                    with pytest.raises(Exception, match="artifact ownership"):
                        dispatch.overlay_attempt_inputs(mission.id, upstream)
                    with pytest.raises(Exception, match="artifact ownership"):
                        _forged_attempt(loop, consumer_task, [item.to_json() for item in upstream])
                assert loop.store.list_attempts(consumer_task) == []

            # The loop dispatches the consumer itself, with the frozen DATA pin.
            await world.until(lambda: loop.store.list_attempts(consumer_task))
            [consumer_attempt] = loop.store.list_attempts(consumer_task)
            consumer_intent = loop.store.get_intent_for_subject(consumer_attempt.id)
            frozen = consumer_intent.config["completion_inputs"]
            assert frozen["manifest_hash"]
            assert frozen["manifest"]["bindings"]
            assert frozen["manifest"]["bindings"][0]["bound_input"]["acceptance_id"] == bound.acceptance_id
            assert HtnStore(loop.store).get_input_manifest(frozen["manifest_hash"]) == frozen["manifest"]
            assert [item["artifact_id"] for item in consumer_intent.config["inputs"]] == [
                item.artifact_id for item in upstream]

            seen.update(mission=mission.id, consumer=consumer_occurrence.occurrence_id,
                        manifest=resolved.manifest.to_json())

    async def cold() -> None:
        # A restarted process (the product's own startup assembly) sees precisely the same
        # frozen source pins; no in-memory acceptance cache is part of the downstream claim.
        async with product_loop(tmp_path, RoleScriptedProvider({})) as product:
            dispatch = product.loop._dispatch_for(seen["mission"])
            network = dispatch.network(seen["mission"])
            consumer = network.occurrence(seen["consumer"])
            again = dispatch.resolved_inputs(seen["mission"], network, consumer)
            assert again.manifest is not None and again.manifest.to_json() == seen["manifest"]

    seen: dict = {}
    asyncio.run(case())
    asyncio.run(cold())
