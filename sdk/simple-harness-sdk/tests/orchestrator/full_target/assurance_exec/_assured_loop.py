# SPDX-License-Identifier: Apache-2.0
"""A real ``Orchestrator`` loop on the assured lane, with a planning world and scripted models.

What a deployment supplies and this fixture stands in for:

* the Assurance assembly (``install_assurance``: native root, four consumers, the review
  runtime) — the production one, unchanged;
* the Host's root initialisation: the root goal's semantic binding and duty, the person's
  confirmation of the completion mapping (CONTENT_ONLY), and the lossless check policy for
  the "new method" review of the root goal;
* the Host's auto permission for every Planner request (``decision_loop.auto_grant``).

Only the model replies are scripted.  The root goal has **no** library method: the Planner
has to propose one, which is the path this fixture exists to exercise.
"""
from __future__ import annotations

import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

HERE = Path(__file__).resolve().parent
for extra in (HERE.parent, HERE.parent / "fixtures" / "htn"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from decision_loop import _envelope, auto_grant, decision_text  # noqa: E402
from htn_world import Env, method, param, step, task_binding  # noqa: E402

from agent_orchestrator.api.operation_completion import OperationCompletionApi  # noqa: E402
from agent_orchestrator.assurance.policy import AssurancePolicy  # noqa: E402
from agent_orchestrator.contracts import Budget  # noqa: E402
from agent_orchestrator.contracts.htn import TaskForm  # noqa: E402
from agent_orchestrator.contracts.obligations import Obligation  # noqa: E402
from agent_orchestrator.governance.permissions import Principal  # noqa: E402
from agent_orchestrator.orchestrator.assurance_assembly import (  # noqa: E402
    AssuranceDeploymentPorts,
    install_assurance,
)
from agent_orchestrator.orchestrator.assurance_check_policy import (  # noqa: E402
    lossless_planning_subject_mapping,
)
from agent_orchestrator.orchestrator.commit_service import MissionSpec  # noqa: E402
from agent_orchestrator.orchestrator.event_handler import Orchestrator  # noqa: E402
from agent_orchestrator.runtime.assembly import OrchestratorConfig  # noqa: E402
from agent_orchestrator.storage.htn_store import HtnStore  # noqa: E402
from agent_orchestrator.storage.obligation_store import ObligationStore  # noqa: E402
from agent_orchestrator.testing.fixtures import RoleScriptedProvider, package_of, role_of  # noqa: E402

TENANT = "tenant-assured-loop"
PRINCIPAL = Principal("assured-loop-user")
ROOT_TASK = "task-root"
ROOT_DUTY = "obl-root"
#: the assured factory names the Mission's success criteria ``c-user-<n>``
CRITERION = "c-user-1"
TOOLS = ("workspace_read_file", "workspace_write_file", "workspace_list")
#: the assured review instructions carry no ``[role:…]`` marker, so the scripted
#: provider files every assured reviewer under this name
REVIEWER = "unknown"


class HeldProvider(RoleScriptedProvider):
    """A scripted provider whose calls for the roles in ``held`` wait at ``release``."""

    def __init__(self, scripts: dict[str, Any], *, held: tuple[str, ...] = ()) -> None:
        super().__init__(scripts)
        import asyncio

        self.held = set(held)
        self.release = asyncio.Event()

    async def invoke(self, request: Any, *, cancel: Any) -> Any:
        if role_of(request) in self.held:
            await self.release.wait()
        return await super().invoke(request, cancel=cancel)


def _env(mission_id: str) -> Env:
    env = Env(mission=mission_id)
    env.register_type("plan.goal", form=TaskForm.COMPOUND, parameters=(("subject", "string"),),
                      criteria=(CRITERION,), domain="plan")
    env.register_type("plan.leaf", parameters=(("subject", "string"),),
                      outputs=(("result", "plan.result"),), capabilities=("plan.read",), domain="plan")
    return env


def proposed_method(method_id: str = "plan.proposed", *, version: int = 1) -> Any:
    """root (compound) → one leaf that carries the Mission's one criterion."""

    return method(method_id, "plan.goal", parameter_schema="plan.goal.params", version=version,
                  steps=(step("leaf", "plan.leaf", TaskForm.PRIMITIVE, {"subject": param("subject")},
                              capabilities=("plan.read",)),),
                  links=((CRITERION, "leaf", None),), finalizer="leaf")


def _open_subject(package: dict[str, Any]) -> tuple[dict[str, Any], str]:
    goal = package["plan"]["open_compound_goals"][0]
    subject = next(row["subject_key"] for row in package["planning_subjects"]
                   if row["occurrence_id"] == goal["occurrence_id"])
    return goal, subject


def propose_step(contract: Any):
    """A scripted Planner: PROPOSE_METHOD ``contract`` for the open goal."""

    def reply(request: Any) -> str:
        _, subject = _open_subject(package_of(request))
        return decision_text(_envelope(
            subject, "PROPOSE_METHOD",
            {"method_proposal": {"method": contract.to_json(), "author": "model",
                                 "rationale": "no registered method serves this goal"}},
            rationale="现有做法都不适用，提出一个新做法。"))

    return reply


def refine_with_step(contract: Any):
    """A scripted Planner: REFINE the open goal with exactly ``contract``."""

    def reply(request: Any) -> str:
        goal, subject = _open_subject(package_of(request))
        ref = contract.method_ref()
        return decision_text(_envelope(
            subject, "REFINE",
            {"method_ref": {"kind": "method", "id": ref.method_id, "semantic_revision": int(ref.version),
                            "content_hash": ref.content_hash},
             "bindings": dict(goal["typed_parameters"])},
            rationale="采用通过审阅的新做法。"))

    return reply


def review_reply(verdict: str = "ACCEPT", *, limitation: str = "") -> str:
    grade = "PASS" if verdict == "ACCEPT" else ("UNKNOWN" if verdict == "INCONCLUSIVE" else "FAIL")
    return json.dumps({"schema_version": 2, "verdict": verdict, "assessments": [
        {"criterion_id": CRITERION, "verdict": grade, "evidence_ids": [],
         "reason": limitation or "fixture method review",
         "limitations": [limitation] if limitation else []}], "findings": []})


@asynccontextmanager
async def assured_loop(root: Path, provider: Any, *, key: str = "assured-loop", approve_method_policy: bool = True,
                       library: tuple[Any, ...] = (), success_criteria: tuple[str, ...] = ("the report is written",),
                       env_factory: Any = None, root_type: str = "plan.goal", host_policies: bool = False,
                       **config: Any):
    """``library`` methods are registered the way a deployment registers its own: admitted,
    with no trial scope — the review gate is not about them.

    ``env_factory`` / ``root_type`` swap in another planning world (sub-goal types, more
    criteria).  ``host_policies`` makes :func:`run_until` stand in for the Host's policy
    projector: before every cycle it approves the "new method" review policy for each goal
    of the plan that has none yet, the way the desktop Host does after a plan commit."""
    cfg = OrchestratorConfig(evidence_root=Path(root) / "root", max_concurrency=3,
                             test_timeout_seconds=60, **config)

    def root_setup(orch: Any) -> None:
        orch.commit.install_assurance_root(principal=PRINCIPAL, tenant_id=TENANT, command_id="install")

    def assembly(orch: Any) -> None:
        install_assurance(orch, AssuranceDeploymentPorts(
            tenant_id=TENANT, principal=PRINCIPAL, select_profile=lambda _spec: AssurancePolicy(),
            notify_transport=lambda _message: None, host_fingerprint="cd" * 32))

    async with Orchestrator(cfg, provider, poll_interval=0.02, assurance_root_setup=root_setup,
                            startup_assembly=assembly) as loop:
        mission, _ = loop.commit.create_mission(MissionSpec(
            goal="交付一份报告", success_criteria=tuple(success_criteria), tenant_id=TENANT,
            idempotency_key=key, allowed_tools=TOOLS,
            budget=Budget(max_tokens=2_000_000, max_attempts=12),
            orchestration_semantics_version="hierarchical"))
        env = (env_factory or _env)(mission.id)
        binding = task_binding(env, root_type, task_id=ROOT_TASK, obligation=ROOT_DUTY,
                               parameters={"subject": "alpha"})
        htn = HtnStore(loop.store)
        ObligationStore(loop.store).register(
            Obligation(obligation_id=ROOT_DUTY, mission_id=mission.id,  # type: ignore[arg-type]
                       requirement_refs=("req-1",), goal_signature_id=root_type),
            recursion_fuel=8)
        loop.commit.admit_obligation_demand(
            mission.id, ROOT_DUTY, principal=PRINCIPAL.principal_id,  # type: ignore[arg-type]
            requester={"kind": "mission_root"}, evidence={"mission_id": mission.id})
        htn.put_task_semantics(mission.id, binding)
        for contract in library:
            receipt = env.admit(contract)
            assert receipt.admitted, receipt.problems
            htn.register_method(contract, env.registry.registration(contract.method_ref()))
        requirements = htn.latest_requirements_revision(mission.id)
        reference = {"id": str(requirements.revision_id), "revision": int(requirements.revision),
                     "content_hash": requirements.content_hash()}
        OperationCompletionApi(loop.commit, tenant_id=TENANT, principal=PRINCIPAL).approve({
            "mission_id": mission.id, "command_id": "approve-" + key,
            "expected_requirements_ref": {"kind": "requirements", **reference},
            "proposal": {"schema_version": 1, "mission_id": mission.id, "requirements_ref": reference,
                         "mode": "CONTENT_ONLY",
                         "content_criterion_ids": list(requirements.required_criterion_ids()),
                         "effects": []}})
        loop.commit.begin_planning(mission.id)
        env.semantics = htn
        loop.install_hierarchical(planning=env)
        auto_grant(loop)
        world = SimpleNamespace(loop=loop, store=loop.store, commit=loop.commit, mission=mission,
                                env=env, provider=provider, htn=htn, host_policies=host_policies,
                                approved_policies=set())
        if approve_method_policy:
            approve_method_policies(world)
        await loop._try_planner_intent(mission.id, ordinal=1)
        yield world


def approve_method_policies(world: Any) -> None:
    """What the Host's projector does: the lossless METHOD_PLAN policy for every goal Task."""

    from agent_orchestrator.assurance.codec import AssuranceError

    for binding in world.htn.list_task_semantics(world.mission.id, form="compound"):
        task_id = str(binding.task_id)
        if task_id in world.approved_policies:
            continue
        try:
            requirements_ref, subject_ref, mapping = lossless_planning_subject_mapping(
                world.commit, mission_id=world.mission.id, task_id=task_id)
        except AssuranceError:
            continue  # nothing to review a method for this goal against (yet)
        world.commit.approve_assurance_check_policy(
            tenant_id=TENANT, mission_id=world.mission.id,
            command_id="host-check-policy:method-plan:" + task_id, principal=PRINCIPAL,
            requirements_ref=requirements_ref, planning_subject=subject_ref, purpose="METHOD_PLAN",
            candidate_mapping=mapping, approval_source="HOST_LOSSLESS_AUTO")
        world.approved_policies.add(task_id)


def event_types(world: Any) -> list[str]:
    return [event.type for event in world.store.list_events(world.mission.id)]


async def run_until(world: Any, done, *, cycles: int = 400) -> bool:
    """Drive the loop one cycle at a time until ``done(world)``; False when it never was."""

    import asyncio

    for _ in range(cycles):
        if done(world):
            return True
        if getattr(world, "host_policies", False):
            approve_method_policies(world)
        await world.loop._cycle()
        await asyncio.sleep(0.01)
    return done(world)


def events_of(world: Any, kind: str) -> list[Any]:
    return [event for event in world.store.list_events(world.mission.id) if event.type == kind]
