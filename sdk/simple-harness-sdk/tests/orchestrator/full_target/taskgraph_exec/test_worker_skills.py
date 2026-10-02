# SPDX-License-Identifier: Apache-2.0
"""NEXT-TG-1.0 §11 / §12.1 E8: a Mission's Worker uses the deployment's Skills.

The three model-side Skill tools (discover / load / execute) are served by a native-plane
pool's own runtime, not the orchestrator's tool gateway.  A deployment that runs such
pools declares them (``DeploymentPolicy.skill_tools``); the current hierarchical Worker
role lists them; the original Mission ∩ Task ∩ Role ∩ Deployment intersection then freezes
them into the Worker's request — and only on a pool that serves them.  Every use asks the
catalogue again, so a Skill suspended between two calls of one turn is refused by name.

The dispatch-path cases run on the product deployment (``production_fixture``): its native
pools, its Skill catalogue owner and its real Assurance acceptance reader.  A Skill is
admitted only the product way — an evaluation Mission created under the evaluation's own
key, its dispatch recorded, run to its accepted root conclusion, and that certificate named
in the admission.  Only the model replies are scripted.
"""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

_TESTS = Path(__file__).resolve().parents[3]
for _extra in (_TESTS / "agents", _TESTS / "agents" / "arp"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

from production_fixture import ProductionWorld, enabled_world, root_task, scripted_worker  # noqa: E402
from skill_fixture import import_skill, lifecycle_command, md_bundle, trial_command  # noqa: E402

from agent_orchestrator.deployment.native_pools import catalogue_owner_profile_id  # noqa: E402
from agent_orchestrator.governance.policies import SKILL_TOOL_NAMES, DeploymentPolicy, effective_tools  # noqa: E402
from agent_orchestrator.runtime import role_templates as roles  # noqa: E402
from agent_orchestrator.runtime.tool_gateway import TOOL_NAMES  # noqa: E402
from agent_orchestrator.testing.fixtures import role_of  # noqa: E402
from agent_orchestrator.testing.product_world import DEFAULT_TOOLS  # noqa: E402
from simple_harness.agents.arp import store as arp_store  # noqa: E402
from simple_harness.agents.arp.assurance_acceptance import evaluation_mission_key  # noqa: E402
from simple_harness.agents.arp.pins import Pin  # noqa: E402
from simple_harness.agents.arp.skill_tools import SKILL_DISCOVER_TOOL_NAME, SKILL_EXECUTE_TOOL_NAME, SKILL_LOAD_TOOL_NAME  # noqa: E402

SKILLS = (SKILL_DISCOVER_TOOL_NAME, SKILL_LOAD_TOOL_NAME, SKILL_EXECUTE_TOOL_NAME)


# ---- the deployment declaration and the role ------------------------------------------------


def test_a_deployment_declares_the_skill_tools_its_pools_serve():
    assert SKILL_TOOL_NAMES == SKILLS
    # naming a Skill tool without declaring it is an unknown tool, as before
    with pytest.raises(ValueError):
        DeploymentPolicy(allowed_tools=(*TOOL_NAMES, SKILL_EXECUTE_TOOL_NAME))
    # only the three model-side Skill tools can be declared
    with pytest.raises(ValueError):
        DeploymentPolicy(skill_tools=("tool_discover",))
    declared = DeploymentPolicy(allowed_tools=(*TOOL_NAMES, *SKILLS), skill_tools=SKILLS)
    assert declared.to_json()["skill_tools"] == list(SKILLS)
    # an existing deployment keeps its exact document (no new key)
    assert "skill_tools" not in DeploymentPolicy().to_json()


def test_the_hierarchical_worker_lists_the_skill_tools():
    current = roles.WORKER_HIERARCHICAL
    assert current.prompt_version == "worker-hierarchical-v5"
    base = ("workspace_read_file", "workspace_write_file", "workspace_list", "run_tests")
    assert current.tool_names == (*base, *SKILLS)
    assert SKILL_DISCOVER_TOOL_NAME in current.instructions
    assert current.prompt_version in roles.HIERARCHICAL_WORKER_VERSIONS
    # the drone-sim Worker has its own tools and none of the Skill ones
    drone = roles.TEMPLATE_VERSIONS["worker"]["worker-drone-sim-hierarchical-v1"]
    assert drone.tool_names == (*base, "drone_sim_telemetry", "drone_sim_command")
    assert SKILL_DISCOVER_TOOL_NAME not in drone.instructions
    # the original intersection: offered by the deployment → the Worker gets them
    offered = DeploymentPolicy(allowed_tools=(*TOOL_NAMES, *SKILLS), skill_tools=SKILLS)
    tools = (*TOOL_NAMES, *SKILLS)
    assert set(SKILLS) <= set(effective_tools(mission_tools=tools, task_tools=tools, role_tools=current.tool_names, deployment=offered))
    assert not set(SKILLS) & set(effective_tools(mission_tools=TOOL_NAMES, task_tools=TOOL_NAMES,
                                                  role_tools=current.tool_names, deployment=DeploymentPolicy()))


# ---- the real dispatch path ------------------------------------------------------------------


#: The deployment offers the Skill tools; its pools serve them.
SKILL_DEPLOYMENT = {"deployment_policy": DeploymentPolicy(allowed_tools=(*TOOL_NAMES, *SKILLS), skill_tools=SKILLS),
                    "allowed_tools": (*DEFAULT_TOOLS, *SKILLS)}


def _owner_runtime(world: ProductionWorld):  # type: ignore[no-untyped-def]
    """The deployment's Skill catalogue authority: the catalogue owner pool's runtime."""
    return world.loop.assembled.pool(catalogue_owner_profile_id()).runtime


def _caller(world: ProductionWorld, command_id: str):  # type: ignore[no-untyped-def]
    """The deployment's authenticated control caller for one Skill command."""
    return world.product.native.control_caller({"command_id": command_id})


@asynccontextmanager
async def _skill_world(tmp_path: Path, *, key: str, worker):  # type: ignore[no-untyped-def]
    async with enabled_world(tmp_path, key=key, worker=worker, **SKILL_DEPLOYMENT) as world:
        yield world


def _worker_intent(world, mission_id=None):  # type: ignore[no-untyped-def]
    [intent_id] = [r[0] for r in world.store.connection.execute(
        "SELECT intent_id FROM dispatch_intents WHERE kind='attempt' AND mission_id=?", (mission_id or world.mission.id,))]
    return world.store.get_intent(intent_id)


def _session_of(runtime, intent):  # type: ignore[no-untyped-def]
    connection = runtime.uow.database.connection
    [session_id] = [r[0] for r in connection.execute("SELECT session_id FROM arp_agent_sessions WHERE agent_id=?", (intent.agent_id,))]
    return connection, session_id


def _last_worker_texts(world):  # type: ignore[no-untyped-def]
    """The messages of the last Worker request (reviewers run on the same provider)."""
    request = [r for r in world.provider.requests if role_of(r) == "worker"][-1]
    return [str(m.content) for m in request.messages]


def _pool_runtime(world, intent):  # type: ignore[no-untyped-def]
    return world.loop.assembled.pool(intent.config["runtime_profile_id"]).runtime


async def _complete(world, mission_id):  # type: ignore[no-untyped-def]
    await world.until(lambda: str(world.store.get_mission(mission_id).status.value) == "COMPLETED", timeout=60)


def _root_certificate(world, mission_id) -> Pin:  # type: ignore[no-untyped-def]
    [row] = world.store.connection.execute(
        "SELECT certificate_id, certificate_hash FROM assurance_use_certificates "
        "WHERE mission_id=? AND purpose='ACCEPT' AND consumer_kind='ROOT_RESOLUTION'", (mission_id,)).fetchall()
    return Pin("acceptance", str(row[0]), 0, str(row[1]))


def test_a_mission_worker_discovers_and_executes_an_admitted_skill_and_a_suspension_refuses_the_next_use(tmp_path):
    seen: dict = {}

    def discover(request):  # type: ignore[no-untyped-def]
        seen["offered"] = sorted(t.name for t in request.tools)
        return (SKILL_DISCOVER_TOOL_NAME, {"limit": 8})

    def execute(request):  # type: ignore[no-untyped-def]
        return (SKILL_EXECUTE_TOOL_NAME, {"skill_ref": seen["pin"], "arguments": {}})

    def suspend_then_execute(request):  # type: ignore[no-untyped-def]
        world, runtime = seen["world"], seen["runtime"]
        runtime.arp.lifecycle.transition(lifecycle_command(runtime, seen["revision"], "SUSPEND", acceptance=None),
                                         caller=_caller(world, "suspend-1"), command_id="suspend-1")
        return (SKILL_EXECUTE_TOOL_NAME, {"skill_ref": seen["pin"], "arguments": {}})

    # The first Mission is the Skill's evaluation (its Worker does the ordinary work); the
    # second one's Worker uses the admitted Skill.
    worker = scripted_worker()
    using = scripted_worker(discover, execute, suspend_then_execute)

    def route(request):  # type: ignore[no-untyped-def]
        return (using if seen.get("admitted") else worker)(request)

    async def case():  # type: ignore[no-untyped-def]
        async with _skill_world(tmp_path, key="tg-worker-skills-unused", worker=route) as world:
            seen["world"] = world
            runtime = _owner_runtime(world)
            revision = import_skill(runtime, md_bundle("repo-notes", "记录仓库事实时先列出文件。"), command="install-1").revision
            binding = runtime.arp.lifecycle.begin_trial(trial_command(runtime, revision), caller=_caller(world, "trial-1"),
                                                        command_id="trial-1")
            evaluation = Pin.from_json(binding["evaluation_ref"])
            # The Host creates the evaluation Mission under the evaluation's key and records its dispatch.
            created = world.product.create({"goal": world.mission.goal, "success_criteria": list(world.mission.success_criteria),
                                             "idempotency_key": evaluation_mission_key(evaluation)})
            trial_mission = created["mission_id"]
            runtime.arp.lifecycle.record_evaluation_dispatch(
                evaluation, mission_id=trial_mission, task_id=root_task(trial_mission),
                caller=_caller(world, "dispatch-1"), command_id="dispatch-1")
            await _complete(world, trial_mission)
            await _complete(world, world.mission.id)  # no Worker of an earlier Mission is still running
            runtime.arp.lifecycle.admit({
                "schema_version": 1, "skill_ref": revision.pin.to_json(),
                "evaluation_acceptance_ref": _root_certificate(world, trial_mission).to_json(),
                "evaluation_policy_ref": binding["policy_ref"], "scope_ref": binding["scope_ref"],
            }, caller=_caller(world, "admit-1"), command_id="admit-1")
            seen.update(runtime=runtime, revision=revision, pin=revision.pin.to_json(), admitted=True)

            user = world.product.create({"goal": world.mission.goal, "success_criteria": list(world.mission.success_criteria),
                                         "idempotency_key": "tg-worker-skills"})["mission_id"]
            await world.until(lambda: world.store.connection.execute(
                "SELECT COUNT(*) FROM results WHERE mission_id=? AND verification_state='DONE'", (user,)).fetchone()[0])

            intent = _worker_intent(world, user)
            assert set(SKILLS) <= set(intent.config["allowed_tools"])
            assert set(SKILLS) <= set(intent.config["agent_config"]["tool_names"])
            assert set(SKILLS) <= set(seen["offered"])
            connection, session_id = _session_of(_pool_runtime(world, intent), intent)
            uses = arp_store.read_skill_uses(connection, session_id)
            assert [u["mode"] for u in uses] == ["INSTRUCTIONS"]  # the suspended call bound no use
            texts = _last_worker_texts(world)
            assert any("SKILL_NOT_ADMITTED" in t for t in texts), texts[-3:]  # re-checked at the next use

    asyncio.run(case())


def test_a_skill_in_trial_is_used_by_the_worker_of_its_own_evaluation_mission(tmp_path):
    """NEXT-TG-1.0 §11 admission evaluation, real dispatch path in MISSION owner mode: before
    the trial is dispatched to this Mission the Worker's call is refused; once it is, the
    catalogue page says the Skill is usable here and the Worker executes it."""
    seen: dict = {}

    def refused_first(request):  # type: ignore[no-untyped-def]
        return (SKILL_EXECUTE_TOOL_NAME, {"skill_ref": seen["pin"], "arguments": {}})

    def dispatch_then_discover(request):  # type: ignore[no-untyped-def]
        world, runtime = seen["world"], seen["runtime"]
        runtime.arp.lifecycle.record_evaluation_dispatch(
            seen["evaluation"], mission_id=world.mission.id, task_id=root_task(world.mission.id),
            caller=_caller(world, "dispatch-1"), command_id="dispatch-1")
        return (SKILL_DISCOVER_TOOL_NAME, {"limit": 8})

    def execute(request):  # type: ignore[no-untyped-def]
        return (SKILL_EXECUTE_TOOL_NAME, {"skill_ref": seen["pin"], "arguments": {}})

    async def case():  # type: ignore[no-untyped-def]
        worker = scripted_worker(refused_first, dispatch_then_discover, execute)
        async with _skill_world(tmp_path, key="tg-worker-skills-trial", worker=worker) as world:
            runtime = _owner_runtime(world)
            revision = import_skill(runtime, md_bundle("trial-notes", "试用：先列出文件再记录。"), command="install-1").revision
            binding = runtime.arp.lifecycle.begin_trial(trial_command(runtime, revision), caller=_caller(world, "trial-1"),
                                                        command_id="trial-1")
            seen.update(world=world, runtime=runtime, pin=revision.pin.to_json(), evaluation=Pin.from_json(binding["evaluation_ref"]))
            await world.commit_seed()
            await world.run_worker()

            intent = _worker_intent(world)
            connection, session_id = _session_of(_pool_runtime(world, intent), intent)
            from simple_harness.agents.arp import mission_sources
            assert mission_sources.read_record(connection, session_id)["sources"]["mission_id"] == world.mission.id
            uses = arp_store.read_skill_uses(connection, session_id)
            assert [u["mode"] for u in uses] == ["INSTRUCTIONS"]  # only the call after the dispatch
            texts = _last_worker_texts(world)
            assert any("SKILL_NOT_ADMITTED" in t for t in texts)  # the call before the dispatch
            assert any('"name":"trial-notes"' in t and '"current_usable":true' in t and '"state":"TRIAL"' in t for t in texts), texts[-4:]
            assert any("试用：先列出文件再记录" in t for t in texts)

    asyncio.run(case())
