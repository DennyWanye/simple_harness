"""Translate public orchestration tools to durable Harness boundaries."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

from deskpet.execution.contracts import ProfileLaunchTicket, fingerprint_json
from deskpet.execution.uow_ports import DelegateFactoryUnitOfWork
from deskpet.harness.execution_profiles import WorkflowSpawnRequest
from deskpet.harness.profiles import ProfileRegistry
from deskpet.tools.code_tools.spawn_subagents_tool import normalize_delegation
from deskpet.tools.orchestration_controls import (
    CAPABILITY_BUILD,
    CAPABILITY_REPAIR,
    EXTERNAL_ACTION_WAIT,
    PROJECT_DIRECTORY_SELECT,
    WORKFLOW_SPAWN,
)
from deskpet.workflows.effects import PreparedToolCall
from deskpet.workflows.output_contract import TaskOutputContractV1
from deskpet.workflows.definitions.personal_workflow import (
    personal_workflow_query_hash,
    selection_from_capability_snapshot,
)


_TOOLS = frozenset({"agent", "agent_parallel", "spawn_team", "spawn_subagents"})
_FORBIDDEN = _TOOLS | {
    "await_subagents",
    WORKFLOW_SPAWN,
    CAPABILITY_BUILD,
    CAPABILITY_REPAIR,
    EXTERNAL_ACTION_WAIT,
    PROJECT_DIRECTORY_SELECT,
}
_READ_ONLY = {"read_file", "list_directory", "glob", "grep", "web_search"}
_CAPABILITY_LIFECYCLE_MUTATIONS = frozenset(
    {
        "capability_install",
        "capability_update",
        "capability_repair",
        "capability_uninstall",
        "capability_rollback",
        "capability_catalog_refresh",
    }
)
_PROJECT_CREATION_ACTION = re.compile(
    r"(?:\b(?:create|build|make|scaffold|generate)\b|创建|新建|生成|搭建|做一个)",
    re.IGNORECASE,
)
_LOCAL_PROJECT_KIND = re.compile(
    r"(?:\b(?:project|game|app|application|website|repository|repo|demo)\b|"
    r"项目|游戏|应用|网站|仓库|演示|Godot|Unity|Unreal)",
    re.IGNORECASE,
)
_CAPABILITY_BUILDER_PROTOCOL = (
    "You are the controlled Capability Builder. The host has already verified "
    "the stamped search receipt, frozen the parent goal and original arguments, "
    "and selected the only writable capability staging lineage. Work only in "
    "that staging lineage. Produce the fixed builder artifacts described in "
    "model_snapshot.capability_builder. Generated workers are compute-only "
    "deskpet-json-tool-v1 processes and every tool uses brokered-effect-v1. "
    "Never edit DeskPet core source and never install, update, repair, publish, "
    "or refresh capabilities yourself. The host permits one initial draft and "
    "at most three materially different repair drafts. Finish by returning the "
    "exactly one JSON object with schema_version, lineage_id, draft_index, and "
    "draft_path. The host validator will emit the typed manager install request "
    "and evidence for the parent."
)
_CAPABILITY_BUILDER_STEPS = (
    "Read the host-frozen search evidence, lineage, and fixed artifact contract",
    "Author draft-0 only inside the host-owned capability staging lineage",
    "Run manifest, schemas, worker, healthcheck, happy, and invalid tests",
    "Repair validation failures with at most three materially different drafts",
    "Return the final draft path and immutable lineage to the host validator",
)


def _allowed_tools(request: Any) -> set[str]:
    snapshot = request.capability_snapshot
    if "tools" in snapshot:
        raw = snapshot["tools"]
    elif "capabilities" in snapshot:
        raw = snapshot["capabilities"]
    else:
        raw_context = request.request_payload.get("context_os")
        prepared_ref = snapshot.get("prepared_tool_set_ref")
        if not isinstance(raw_context, Mapping) or not prepared_ref:
            return set()
        from deskpet.capabilities.refresh import context_os_snapshot_ref
        from deskpet.tools.prepared_snapshot import load_context_os_snapshot

        prepared, eligibility = load_context_os_snapshot(raw_context)
        if context_os_snapshot_ref(prepared, eligibility) != prepared_ref:
            raise ValueError(
                "Context OS snapshot differs from trusted catalog lease"
            )
        raw = tuple(
            capability.ref.name
            for capability in (*prepared.direct, *prepared.activated)
        )
    if not isinstance(raw, (list, tuple, set, frozenset)):
        raise ValueError("capability snapshot tools must be a sequence")
    return {str(item) for item in raw if str(item).strip()}


def _child_context_os(
    raw_context: object,
    *,
    parent_run_id: str,
    stable_call_id: str,
) -> object:
    """Clone a parent's frozen catalog into one child-owned live scope.

    The schemas and eligibility remain frozen, but runtime activations must not
    leak between sequential children.  A deterministic id keeps replay of the
    same provider call idempotent while distinct workflow_spawn calls receive
    independent activation revisions.
    """

    if not isinstance(raw_context, Mapping):
        return raw_context
    from deskpet.tools.prepared_snapshot import (
        dump_context_os_snapshot,
        load_context_os_snapshot,
    )

    prepared, eligibility = load_context_os_snapshot(raw_context)
    child_scope_id = "child-scope:" + hashlib.sha256(
        f"{parent_run_id}|{stable_call_id}".encode("utf-8")
    ).hexdigest()
    return dump_context_os_snapshot(
        replace(prepared, scope_id=child_scope_id), eligibility
    )


def _workspace_from(request: Any, requested: object) -> str | None:
    if requested:
        return str(Path(str(requested)).expanduser().resolve(strict=False))
    context = getattr(request, "run_context", None)
    raw = None if context is None else getattr(context, "workspace", None)
    if isinstance(raw, str) and raw:
        return str(Path(raw).expanduser().resolve(strict=False))
    if isinstance(raw, Mapping):
        selected = raw.get("workspace_root") or raw.get("root") or raw.get("path")
        if selected:
            return str(Path(str(selected)).expanduser().resolve(strict=False))
    return None


def _requires_selected_project_workspace(
    request: Any,
    objective: str,
    plan_steps: object,
) -> bool:
    """Recognize a concrete new local project before a child can write it.

    The model may shorten its child objective, so the trusted canonical user
    turn participates in the decision.  A path typed into chat is still only
    unstructured model input; the native picker remains the authority that
    binds a writable project root.
    """

    parts = [objective]
    if isinstance(plan_steps, (list, tuple)):
        parts.extend(str(step) for step in plan_steps)
    for message in reversed(tuple(request.canonical_messages or ())):
        if isinstance(message, Mapping) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                parts.append(content)
            break
    text = "\n".join(part for part in parts if str(part).strip())
    return bool(
        _PROJECT_CREATION_ACTION.search(text)
        and _LOCAL_PROJECT_KIND.search(text)
    )


def _complete_presentation_objective(
    objective: str,
    canonical_messages: object,
) -> str:
    """Restore constraints when a provider copies only the request prefix.

    A presentation tool call occasionally repeats only the first sentence of
    the user's request.  The prepared canonical history is the trusted source
    for that turn, so it is safe to restore the remaining suffix only when the
    tool objective appears there verbatim.
    """

    if (
        not objective.strip()
        or not isinstance(canonical_messages, (list, tuple))
    ):
        return objective
    for message in reversed(canonical_messages):
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            return objective
        start = content.find(objective)
        if start < 0:
            return objective
        if content.rfind(objective) != start:
            return objective
        complete = content[start:].strip()
        return complete if len(complete) > len(objective) else objective
    return objective


class ProductDelegateFactory:
    """Build child requests only from a prepared call and trusted parent state."""

    def __init__(
        self,
        profiles: ProfileRegistry,
        tool_registry: Any,
        uow: DelegateFactoryUnitOfWork,
        capability_builder_host: Any | None = None,
        provider_snapshot_resolver: (
            Callable[[str], tuple[Mapping[str, Any], Mapping[str, Any]]] | None
        ) = None,
    ) -> None:
        self._profiles = profiles
        self._tool_registry = tool_registry
        self._uow = uow
        self._capability_builder_host = capability_builder_host
        self._provider_snapshot_resolver = provider_snapshot_resolver

    @staticmethod
    def handles(tool_name: str) -> bool:
        return tool_name in (
            _TOOLS
            | {
                "ask_clarification",
                WORKFLOW_SPAWN,
                CAPABILITY_BUILD,
                CAPABILITY_REPAIR,
            }
        )

    @staticmethod
    def _child_run_id(parent_run_id: str, command_id: str) -> str:
        return "child-" + hashlib.sha256(
            f"execution-child-run|{parent_run_id}|{command_id}".encode()
        ).hexdigest()[:32]

    async def _workflow_spawn(self, request: Any, call: PreparedToolCall):
        from deskpet.harness.ports import AttachmentPolicy, DelegateRun, JoinPolicy

        args = call.arguments_json()
        generation = int(args.get("catalog_generation") or 0)
        mutation_control = call.tool_name in {
            CAPABILITY_BUILD,
            CAPABILITY_REPAIR,
        }
        profile_key = (
            "workflow.capability_build"
            if mutation_control
            else str(args.get("profile_key") or "")
        )
        objective = str(args.get("objective") or "").strip()
        # Profile capabilities describe host runtime lanes (for example
        # ``workflow``), not names in the parent's prepared tool set.  The
        # admitted workflow_spawn call and frozen profile catalog are the
        # launch authority; child tool delegation is narrowed separately.
        profile = self._profiles.resolve(
            profile_key,
            generation=generation,
            launch_policy=(
                "reserved_control"
                if mutation_control
                else "model_spawnable"
            ),
        )
        if profile.profile_key == "workflow.presentation":
            objective = _complete_presentation_objective(
                objective,
                request.canonical_messages,
            )
        personal_selection = None
        if profile.profile_key == "workflow.personal_v1":
            forbidden = {
                "owner",
                "owner_key",
                "version",
                "manifest_hash",
                "binding_generation",
                "graph",
                "persistence",
                "delivery",
                "host_extensions",
                "capability_snapshot_lease",
            }
            if forbidden.intersection(args):
                raise ValueError(
                    "workflow_spawn personal workflow accepts no authority fields"
                )
            personal_selection = selection_from_capability_snapshot(
                request.capability_snapshot
            )
            if personal_selection is None:
                raise ValueError(
                    "workflow.personal_v1 requires a frozen parent selection"
                )
            if personal_workflow_query_hash(objective) != (
                personal_selection.query_hash
            ):
                raise ValueError(
                    "workflow.personal_v1 objective differs from frozen selection"
                )
        if (
            profile.profile_key == "workflow.capability_build"
            and not mutation_control
        ):
            raise ValueError(
                "workflow.capability_build is reserved for capability_build "
                "and capability_repair"
            )
        context = request.run_context
        if context is None:
            raise ValueError("workflow_spawn requires a trusted parent context")
        workspace = _workspace_from(
            request,
            None if mutation_control else args.get("workspace_ref"),
        )
        task_scope_id = str(
            request.request_payload.get("task_scope_id")
            or request.request_payload.get("root_run_id")
            or context.root_run_id
        )
        current_attempt = dict(
            request.completion_state.get("current_attempt") or {}
        )
        if (
            not current_attempt.get("attempt_id")
            or not current_attempt.get("provider_turn_id")
        ):
            raise ValueError(
                "workflow_spawn requires a durable provider attempt"
            )
        requested_failure_set_id = (
            str(args["trigger_failure_set_id"])
            if args.get("trigger_failure_set_id")
            else None
        )
        attempt_failure_set_id = (
            str(current_attempt["trigger_failure_set_id"])
            if current_attempt.get("trigger_failure_set_id")
            else None
        )
        if (
            requested_failure_set_id is not None
            and attempt_failure_set_id is not None
            and requested_failure_set_id != attempt_failure_set_id
        ):
            raise ValueError(
                "workflow_spawn trigger differs from its durable attempt"
            )
        builder_launch = None
        if mutation_control:
            builder_host = self._capability_builder_host
            if builder_host is None:
                raise RuntimeError(
                    "capability builder host is unavailable"
                )
            repair_receipt_ref = None
            if call.tool_name == CAPABILITY_REPAIR:
                repair_receipt_ref = str(
                    args.get("failure_receipt_ref") or ""
                )
                receipt = await builder_host.store.get_failure_receipt(
                    repair_receipt_ref
                )
                if (
                    receipt is None
                    or receipt.root_run_id != context.root_run_id
                ):
                    raise ValueError(
                        "capability_repair requires a durable host-signed "
                        "failure receipt from this root run"
                    )
                goal = await self._uow.get_task_goal(context.root_run_id)
                objective_ref = (
                    goal.objective_ref if goal is not None else task_scope_id
                )
                objective = (
                    f"Repair managed capability {receipt.capability_id} "
                    f"after {receipt.tool_name} failed with "
                    f"{receipt.error_code}; preserve root objective "
                    f"{objective_ref}."
                )
                original_args: Mapping[str, Any] = dict(
                    receipt.canonical_args
                )
            else:
                original_args = args.get("original_args")
                if not isinstance(original_args, Mapping):
                    raise ValueError(
                        "capability builder original_args must be an object"
                    )
            builder_launch = await builder_host.admit(
                root_run_id=context.root_run_id,
                parent_run_id=request.run_id,
                parent_goal_ref=task_scope_id,
                objective=objective,
                original_args=dict(original_args),
                canonical_messages=request.canonical_messages,
                task_workspace=context.workspace,
                requested_workspace=None,
                requested_scope=args.get("scope"),
                repair_receipt_ref=repair_receipt_ref,
                repair_control_call_id=(
                    call.stable_call_id
                    if repair_receipt_ref is not None
                    else None
                ),
            )
            workspace = builder_launch.staging_root
        output_contract: TaskOutputContractV1 | None = None
        raw_output_refs = args.get("output_refs")
        raw_scratch_refs = args.get("scratch_refs", ())
        if profile.workflow_name == "durable_task" and not mutation_control:
            if workspace is None:
                raise ValueError(
                    "workflow.durable_task requires a committed workspace_ref; "
                    "call workspace_prepare first"
                )
            if not isinstance(raw_output_refs, list):
                raise ValueError(
                    "workflow.durable_task requires an explicit output_refs array"
                )
            if not isinstance(raw_scratch_refs, list):
                raise ValueError("workflow.durable_task scratch_refs must be an array")
            output_contract = TaskOutputContractV1.freeze(
                workspace,
                output_refs=raw_output_refs,
                scratch_refs=raw_scratch_refs,
            )
        spawn = WorkflowSpawnRequest(
            profile_key=profile_key,
            objective=objective,
            input_refs=tuple(str(item) for item in args.get("input_refs", ())),
            output_refs=(
                output_contract.output_refs if output_contract is not None else ()
            ),
            scratch_refs=(
                output_contract.scratch_refs if output_contract is not None else ()
            ),
            workspace_ref=workspace,
            parent_run_id=request.run_id,
            root_run_id=context.root_run_id,
            task_scope_id=task_scope_id,
            trigger_failure_set_id=(
                requested_failure_set_id or attempt_failure_set_id
            ),
            focused_failure_ref=(
                str(args["focused_failure_ref"])
                if args.get("focused_failure_ref")
                else None
            ),
            supersedes_run_id=(
                str(args["supersedes_run_id"])
                if args.get("supersedes_run_id")
                else None
            ),
            profile_catalog_generation=generation,
        )
        child_payload: dict[str, Any] = {
            "driver_kind": profile.driver_kind,
            "profile_key": profile.profile_key,
            "text": objective,
            "request": objective,
            "topic": objective,
            "objective": objective,
            "input_refs": list(spawn.input_refs),
            "output_refs": list(spawn.output_refs),
            "scratch_refs": list(spawn.scratch_refs),
            "workspace_ref": workspace,
            "task_scope_id": task_scope_id,
            "profile_catalog_generation": generation,
            "trigger_failure_set_id": spawn.trigger_failure_set_id,
            "focused_failure_ref": spawn.focused_failure_ref,
            "supersedes_run_id": spawn.supersedes_run_id,
        }
        if personal_selection is not None:
            child_payload["personal_workflow_selection"] = (
                personal_selection.to_child_payload()
            )
        if profile.workflow_name == "durable_task":
            if output_contract is None and not mutation_control:
                raise RuntimeError("durable task output contract is unavailable")
            if _requires_selected_project_workspace(
                request,
                objective,
                args.get("plan_steps"),
            ):
                work_context = await self._uow.get_task_work_context(
                    context.root_run_id
                )
                if (
                    work_context is not None
                    and work_context.workspace_source != "user_path"
                ):
                    raise ValueError(
                        "project_workspace_selection_required: call "
                        "project_directory_select and wait for the user to "
                        "confirm the native folder card before workflow_spawn"
                    )
            from deskpet.workflows.adapters.durable_task_runtime import (
                capability_snapshot,
                workflow_session_ref,
            )

            allowed_set = _allowed_tools(request) - _FORBIDDEN
            if profile.profile_key == "workflow.capability_build":
                allowed_set -= _CAPABILITY_LIFECYCLE_MUTATIONS
            allowed = tuple(sorted(allowed_set))
            provider_plan = context.provider_plan.get("providers")
            if (
                not isinstance(provider_plan, (list, tuple))
                or not provider_plan
                or not str(provider_plan[0]).strip()
            ):
                raise ValueError(
                    "workflow.durable_task requires a frozen provider plan"
                )
            provider_id = str(provider_plan[0])
            raw_bindings = context.provider_plan.get("bindings")
            exact_model_id = ""
            if isinstance(raw_bindings, (list, tuple)) and raw_bindings:
                first_binding = raw_bindings[0]
                if not isinstance(first_binding, Mapping):
                    raise ValueError(
                        "workflow.durable_task provider binding is invalid"
                    )
                binding_provider_id = str(
                    first_binding.get("provider_id") or ""
                ).strip()
                exact_model_id = str(
                    first_binding.get("model_id") or ""
                ).strip()
                if binding_provider_id != provider_id or not exact_model_id:
                    raise ValueError(
                        "workflow.durable_task provider binding differs from its "
                        "frozen provider plan"
                    )
            requested_plan_steps = args.get("plan_steps")
            durable_plan_steps = [
                str(step).strip()
                for step in (
                    requested_plan_steps
                    if isinstance(requested_plan_steps, list)
                    else []
                )
                if str(step).strip()
            ][:8]
            if len(durable_plan_steps) < 2:
                # Compatibility for older providers and replayed calls. The
                # UI can still recover observable substeps from checkpointed
                # Agent messages and tool effects.
                durable_plan_steps = [objective]
            if exact_model_id:
                provider_snapshot = {"provider_id": provider_id}
                model_snapshot = {"model": exact_model_id}
            else:
                # Compatibility for Runs frozen before exact provider/model
                # bindings became part of the trusted RunContext.
                if self._provider_snapshot_resolver is None:
                    raise ValueError(
                        "workflow.durable_task provider snapshot resolver is unavailable"
                    )
                provider_snapshot, model_snapshot = (
                    self._provider_snapshot_resolver(provider_id)
                )
            if (
                str(provider_snapshot.get("provider_id") or "") != provider_id
                or not str(model_snapshot.get("model") or "").strip()
            ):
                raise ValueError(
                    "workflow.durable_task provider/model snapshot is incomplete"
                )
            child_payload.update(
                {
                    "session_ref": workflow_session_ref(
                        session_id=context.session_id,
                        task_scope_id=task_scope_id,
                        delivery_session_id=context.session_id,
                        workspace_root=workspace,
                        session_epoch=context.auth_epoch,
                        task_epoch=context.auth_epoch,
                    ).to_dict(),
                    "capability_snapshot": [
                        item.to_dict()
                        for item in capability_snapshot(
                            self._tool_registry, allowed_tools=allowed
                        )
                    ],
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "The host froze task-output-contract-v1. Create or "
                                "modify only output_refs and scratch_refs. Every "
                                "output_ref must exist at completion; every "
                                "scratch_ref must be removed. Persistent changes "
                                "outside the contract fail the audit. Register only "
                                "declared output_refs as artifacts."
                            ),
                        },
                        {"role": "user", "content": objective},
                    ],
                    "plan_steps": durable_plan_steps,
                    "approval_required": False,
                    "started_at": time.time(),
                    "request_id": context.request_id,
                    "turn_id": context.turn_id,
                    "provider_snapshot": dict(provider_snapshot),
                    "model_snapshot": dict(model_snapshot),
                    "context_os": _child_context_os(
                        request.request_payload.get("context_os"),
                        parent_run_id=str(request.run_id),
                        stable_call_id=str(call.stable_call_id),
                    ),
                }
            )
            if output_contract is not None:
                child_payload["output_contract"] = output_contract.to_dict()
            if profile.profile_key == "workflow.capability_build":
                if builder_launch is None:  # pragma: no cover - guarded above
                    raise RuntimeError("capability builder launch is missing")
                child_payload.update(
                    {
                        "messages": [
                            {
                                "role": "system",
                                "content": _CAPABILITY_BUILDER_PROTOCOL,
                            },
                            {"role": "user", "content": objective},
                        ],
                        "plan_steps": list(_CAPABILITY_BUILDER_STEPS),
                        "approval_required": False,
                        "proposal_budget": 12,
                        "fix_budget": 3,
                        "capability_builder": builder_launch.to_dict(),
                    }
                )
        command_id = f"delegate:{call.tool_name}:" + hashlib.sha256(
            (
                f"{request.run_id}|{call.stable_call_id}|"
                f"{spawn.fingerprint()}"
            ).encode("utf-8")
        ).hexdigest()[:24]
        profile_launch_request = {
            **spawn.to_dict(),
            # Bind every host-derived profile field (including the durable
            # tool capability records) into the ticket.  ChildRunCoordinator
            # can then preserve those records without trusting arbitrary
            # model-supplied authority fields.
            "trusted_child_payload_hash": fingerprint_json(child_payload),
        }
        capability_snapshot_ref = str(
            request.tool_set_snapshot_ref
            or context.capability_hash
            or fingerprint_json(dict(request.capability_snapshot))
        )
        task_grant_ref = str(
            request.request_payload.get("task_grant_ref")
            or (
                "task-grant:compat:"
                + fingerprint_json(
                    {
                        "root_run_id": context.root_run_id,
                        "principal_id": context.principal_id,
                        "capability_snapshot_ref": capability_snapshot_ref,
                    }
                )
            )
        )
        ticket = ProfileLaunchTicket(
            ticket_ref="profile-launch:"
            + hashlib.sha256(
                f"{request.run_id}|{call.stable_call_id}".encode("utf-8")
            ).hexdigest(),
            parent_run_id=request.run_id,
            root_run_id=context.root_run_id,
            task_scope_id=task_scope_id,
            attempt_id=str(current_attempt["attempt_id"]),
            provider_turn_id=str(current_attempt["provider_turn_id"]),
            profile_key=profile.profile_key,
            driver_kind=profile.driver_kind,
            profile_catalog_generation=generation,
            capability_snapshot_ref=capability_snapshot_ref,
            task_grant_ref=task_grant_ref,
            spawn_call_id=call.stable_call_id,
            personal_selection_id=(
                None
                if personal_selection is None
                else personal_selection.selection_id
            ),
            personal_selection_fingerprint=(
                None
                if personal_selection is None
                else personal_selection.selection_fingerprint
            ),
            trigger_failure_set_id=spawn.trigger_failure_set_id,
            request_fingerprint=fingerprint_json(profile_launch_request),
        )
        issued = await self._uow.issue_profile_launch_ticket(ticket)
        if issued.child_command_id is not None:
            command_id = issued.child_command_id
            expected_child_run_id = self._child_run_id(request.run_id, command_id)
            if issued.child_run_id != expected_child_run_id:
                raise RuntimeError(
                    "profile launch ticket child identity is inconsistent"
                )
        delegated_capabilities = _allowed_tools(request) - _FORBIDDEN
        if profile.profile_key == "workflow.capability_build":
            delegated_capabilities -= _CAPABILITY_LIFECYCLE_MUTATIONS
        return DelegateRun(
            request.run_id,
            command_id,
            child_payload,
            profile.profile_key,
            tuple(sorted(delegated_capabilities)),
            AttachmentPolicy.ATTACHED,
            JoinPolicy.JOIN_BEFORE_FINAL,
            profile_launch_ticket_ref=issued.ticket_ref,
            profile_launch_request=profile_launch_request,
        )

    def __call__(self, request: Any, call: Any):
        name = str(getattr(call, "tool_name", getattr(call, "name", "")))
        if not self.handles(name):
            return None
        if name in {
            WORKFLOW_SPAWN,
            CAPABILITY_BUILD,
            CAPABILITY_REPAIR,
        }:
            if not isinstance(call, PreparedToolCall):
                raise TypeError(
                    f"{name} must be prepared before delegation"
                )
            return self._workflow_spawn(request, call)
        return build_product_delegate(request, call)


def build_product_delegate(request: Any, call: Any):
    """Compatibility mapping for legacy public subagent tools."""

    name = str(getattr(call, "tool_name", getattr(call, "name", "")))
    args = dict(
        getattr(call, "final_params", getattr(call, "arguments", {})) or {}
    )
    if name == "ask_clarification":
        from deskpet.harness.ports import OpenDecision

        question = str(args.get("question") or "").strip()
        if not question:
            raise ValueError("ask_clarification requires question")
        identity = hashlib.sha256(
            f"clarification|{request.run_id}|{getattr(call, 'stable_call_id', getattr(call, 'id', ''))}".encode()
        ).hexdigest()
        return OpenDecision(
            request.run_id,
            f"clarification:{getattr(call, 'stable_call_id', getattr(call, 'id', ''))}",
            identity,
            hashlib.sha256(f"nonce|{identity}".encode()).hexdigest(),
            "clarification",
            {"question": question, "options": args.get("options") or []},
        )
    if name not in _TOOLS:
        return None
    from deskpet.harness.ports import AttachmentPolicy, DelegateRun, JoinPolicy

    text, requested, detached = normalize_delegation(name, args)
    available = _allowed_tools(request) - _FORBIDDEN
    capabilities = tuple(
        sorted(available & (set(requested) if requested else _READ_ONLY))
    )
    frozen = json.dumps(
        args, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    call_id = str(
        getattr(call, "stable_call_id", getattr(call, "id", ""))
    )
    return DelegateRun(
        request.run_id,
        f"delegate:{name}:"
        + hashlib.sha256(
            f"{request.run_id}|{call_id}|{name}|{frozen}".encode()
        ).hexdigest()[:24],
        {
            "driver_kind": "react",
            "text": text,
            "delegation_tool": name,
            "delegation_args": args,
        },
        f"react.{name}",
        capabilities,
        AttachmentPolicy.DETACHED if detached else AttachmentPolicy.ATTACHED,
        JoinPolicy.DETACHED if detached else JoinPolicy.JOIN_BEFORE_FINAL,
    )


__all__ = ["ProductDelegateFactory", "build_product_delegate"]
