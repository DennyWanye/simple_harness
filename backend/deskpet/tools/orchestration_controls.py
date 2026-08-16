"""Host-only orchestration control ToolSpecs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deskpet.types.task_grants import ResourceSelector

if TYPE_CHECKING:
    from deskpet.harness.profiles import ProfileRegistry


WORKFLOW_SPAWN = "workflow_spawn"
WORKSPACE_PREPARE = "workspace_prepare"
CAPABILITY_BUILD = "capability_build"
CAPABILITY_REPAIR = "capability_repair"
EXTERNAL_ACTION_WAIT = "external_action_wait"
PROJECT_DIRECTORY_SELECT = "project_directory_select"
CORE_CONTROL_NAMES = frozenset(
    {
        WORKFLOW_SPAWN,
        WORKSPACE_PREPARE,
        CAPABILITY_BUILD,
        CAPABILITY_REPAIR,
        EXTERNAL_ACTION_WAIT,
        PROJECT_DIRECTORY_SELECT,
    }
)


def _fail_closed(_args: dict[str, Any], _task_id: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": "delegate_control_must_be_dispatched_by_harness",
        },
        ensure_ascii=False,
    )


def _external_wait_fail_closed(_args: dict[str, Any], _task_id: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": "external_wait_must_be_staged_by_harness",
        },
        ensure_ascii=False,
    )


def _bound_workspace_paths(context: Any) -> tuple[Path, Path]:
    raw_workspace = str(getattr(context, "workspace", "") or "").strip()
    raw_scope = str(getattr(context, "write_scope_root", "") or "").strip()
    if not raw_workspace or not raw_scope:
        raise ValueError("task_workspace_binding_unavailable")
    workspace = Path(raw_workspace).expanduser().resolve(strict=False)
    scope = Path(raw_scope).expanduser().resolve(strict=False)
    try:
        workspace.relative_to(scope)
    except ValueError as exc:
        raise ValueError("task_workspace_outside_write_scope") from exc
    return workspace, scope


def _prepare_workspace(_args: dict[str, Any], context: Any) -> str:
    try:
        workspace, _scope = _bound_workspace_paths(context)
    except ValueError as exc:
        return json.dumps(
            {"ok": False, "error": str(exc)},
            ensure_ascii=False,
        )
    workspace.mkdir(parents=True, exist_ok=True)
    return json.dumps(
        {"ok": True, "result": {"workspace_root": str(workspace)}},
        ensure_ascii=False,
    )


def _workspace_prepare_resources(
    _args: dict[str, Any], context: Any
) -> tuple[ResourceSelector, ...]:
    workspace, _scope = _bound_workspace_paths(context)
    return (ResourceSelector.filesystem(workspace, "write"),)


def workflow_spawn_schema(profiles: ProfileRegistry) -> dict[str, Any]:
    keys = tuple(sorted(profiles.model_spawnable))
    if not keys:
        raise ValueError("workflow_spawn requires at least one model-spawnable profile")
    descriptions = [
        f"- {key}: {profiles.model_spawnable[key].description}" for key in keys
    ]
    return {
        "name": WORKFLOW_SPAWN,
        "description": (
            "Start one durable workflow selected from the current profile catalog. "
            "Choose by the profile descriptions, not by keywords.\n"
            + "\n".join(descriptions)
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "profile_key": {
                    "type": "string",
                    "enum": list(keys),
                    "description": "A model-spawnable key from this exact catalog generation.",
                },
                "objective": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The concrete objective for the child workflow.",
                },
                "plan_steps": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 120,
                    },
                    "minItems": 2,
                    "maxItems": 8,
                    "description": (
                        "For workflow.durable_task, provide 2-8 concise, "
                        "user-readable execution steps in order. Each step "
                        "must describe one observable outcome that can be "
                        "completed by one coherent tool batch."
                    ),
                },
                "input_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": [],
                },
                "output_refs": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "default": [],
                    "description": (
                        "Exact workspace-relative deliverable files the child "
                        "may create or modify. Use [] for a read-only/text-only "
                        "child. Every durable child must declare this boundary."
                    ),
                },
                "scratch_refs": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "default": [],
                    "description": (
                        "Optional workspace-relative temporary files or directory "
                        "prefixes (end directories with /). They may be used while "
                        "running but must be removed before the child completes."
                    ),
                },
                "workspace_ref": {
                    "anyOf": [{"type": "string"}, {"type": "null"}]
                },
                "catalog_generation": {
                    "type": "integer",
                    "const": profiles.generation,
                },
            },
            "required": [
                "profile_key",
                "objective",
                "output_refs",
                "catalog_generation",
            ],
            "additionalProperties": False,
        },
    }


def _workflow_spawn_resources(profiles: ProfileRegistry):
    """Bind delegation authority to this exact host catalog and parent scope."""

    generation = int(profiles.generation)
    profile_keys = frozenset(str(key) for key in profiles.model_spawnable)

    def resolve(
        args: dict[str, Any], context: Any
    ) -> tuple[ResourceSelector, ...]:
        profile_key = str(args.get("profile_key") or "").strip()
        requested_generation = args.get("catalog_generation")
        if (
            profile_key not in profile_keys
            or isinstance(requested_generation, bool)
            or requested_generation != generation
        ):
            raise ValueError("workflow_spawn_catalog_binding_mismatch")
        root_run_id = str(getattr(context, "root_run_id", "") or "").strip()
        if not root_run_id:
            raise ValueError("workflow_spawn_parent_binding_missing")
        selectors = [
            ResourceSelector(
                "system_change",
                f"workflow_spawn:{root_run_id}:{generation}:{profile_key}",
                ("delegate",),
            )
        ]
        raw_workspace = str(getattr(context, "workspace", "") or "").strip()
        raw_scope = str(
            getattr(context, "write_scope_root", "") or ""
        ).strip()
        if raw_workspace or raw_scope:
            workspace, _scope = _bound_workspace_paths(context)
            selectors.append(
                ResourceSelector.filesystem(workspace, "read", "write")
            )
        return tuple(selectors)

    return resolve


def _capability_mutation_schema(
    profiles: ProfileRegistry, *, repair: bool
) -> dict[str, Any]:
    if repair:
        return {
            "name": CAPABILITY_REPAIR,
            "description": (
                "Repair a managed capability from one host-signed failure "
                "receipt. The host derives the objective, immutable parent "
                "version, canonical failed arguments, and binding scope."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "failure_receipt_ref": {
                        "type": "string",
                        "pattern": "^[0-9a-f]{64}$",
                        "description": (
                            "The exact receipt returned by a failed capability "
                            "tool call. Free-form error text is not accepted."
                        ),
                    },
                    "catalog_generation": {
                        "type": "integer",
                        "const": profiles.generation,
                    },
                },
                "required": [
                    "failure_receipt_ref",
                    "catalog_generation",
                ],
                "additionalProperties": False,
            },
        }
    properties: dict[str, Any] = {
        "objective": {
            "type": "string",
            "minLength": 1,
            "description": (
                "The unchanged parent objective that requires a reusable "
                "deterministic adapter."
            ),
        },
        "original_args": {
            "type": "object",
            "description": (
                "Canonical arguments the generated adapter must support. "
                "This is frozen into builder lineage."
            ),
            "additionalProperties": True,
        },
        "scope": {
            "type": "string",
            "enum": ["run", "project", "user"],
            "default": "run",
            "description": (
                "Use run for one task, project for project-shaped reuse, and "
                "user only for explicitly cross-project reuse."
            ),
        },
        "catalog_generation": {
            "type": "integer",
            "const": profiles.generation,
        },
    }
    required = ["objective", "original_args", "catalog_generation"]
    return {
        "name": CAPABILITY_BUILD,
        "description": (
            "Build a process-isolated reusable capability only after a "
            "current stamped capability_search proves no adequate "
            "executable match. Existing generic tools should be preferred "
            "for one-off actions."
        ),
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def _capability_mutation_resources(
    args: dict[str, Any], context: Any
) -> tuple[ResourceSelector, ...]:
    root_run_id = str(getattr(context, "root_run_id", "") or "")
    receipt_ref = str(args.get("failure_receipt_ref") or "")
    scope = str(args.get("scope") or "run")
    resource = (
        f"capability:repair:{root_run_id}:{receipt_ref}"
        if receipt_ref
        else f"capability:generated:{scope}:{root_run_id}"
    )
    selectors = [
        ResourceSelector(
            "system_change",
            resource,
            ("repair" if receipt_ref else "install",),
        )
    ]
    workspace = str(getattr(context, "workspace", "") or "").strip()
    if workspace:
        selectors.append(ResourceSelector.filesystem(workspace, "read"))
    return tuple(selectors)


def register_orchestration_controls(registry: Any, profiles: ProfileRegistry) -> None:
    """Register the core exclusive control surface once at composition time."""

    from deskpet.workflows.contracts import EffectKind, EffectPolicy

    if registry.has(WORKFLOW_SPAWN):
        if registry.dispatch_kind(WORKFLOW_SPAWN) != "delegate_control":
            raise RuntimeError("workflow_spawn is registered by a non-control provider")
    else:
        registry.register(
            WORKFLOW_SPAWN,
            "orchestration",
            workflow_spawn_schema(profiles),
            _fail_closed,
            permission_category="shell",
            source="builtin",
            dangerous=False,
            concurrency_safe=False,
            spec_version=f"profiles-{profiles.generation}",
            effect_policy=EffectPolicy(
                policy_id="deskpet:workflow_spawn:orchestration_delegate",
                version="v1",
                kind=EffectKind.OPAQUE_MANUAL,
                max_attempts=1,
            ),
            resource_scope_resolver=_workflow_spawn_resources(profiles),
            resource_scope_resolver_id="workflow-spawn-parent-scope",
            resource_scope_resolver_version="v1",
            dispatch_kind="delegate_control",
        )
    if not registry.has(WORKSPACE_PREPARE):
        registry.register(
            WORKSPACE_PREPARE,
            "orchestration",
            {
                "name": WORKSPACE_PREPARE,
                "description": (
                    "Create the already host-bound, task-local workspace. "
                    "The path is chosen by the host and cannot be supplied by the model."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
            _fail_closed,
            context_handler=_prepare_workspace,
            resource_scope_resolver=_workspace_prepare_resources,
            resource_scope_resolver_id="host-bound-task-workspace",
            resource_scope_resolver_version="v1",
            outcome_parser_id="json_error_envelope_v1",
            permission_category="write_file",
            source="builtin",
            concurrency_safe=False,
            spec_version="task-workspace-v1",
            dispatch_kind="handler",
        )
    if not registry.has(EXTERNAL_ACTION_WAIT):
        registry.register(
            EXTERNAL_ACTION_WAIT,
            "orchestration",
            {
                "name": EXTERNAL_ACTION_WAIT,
                "description": (
                    "Pause the current action when progress requires an external "
                    "user step that Simple Harness cannot perform, such as accepting a "
                    "Windows UAC prompt, signing in, or supplying content in a "
                    "third-party application. The host resumes this exact call and "
                    "Attempt after the user reports the step was handled. Treat "
                    "that signal only as permission to continue: re-probe the "
                    "real external state before claiming success. The wait is not "
                    "a tool failure and does not consume a retry."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "wait_kind": {
                            "type": "string",
                            "enum": [
                                "uac",
                                "credential",
                                "user_content",
                                "third_party",
                            ],
                        },
                        "required_action": {
                            "type": "string",
                            "minLength": 1,
                            "description": (
                                "A concrete, safe instruction describing the one "
                                "external action the user must finish."
                            ),
                        },
                        "evidence_refs": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "default": [],
                            "description": (
                                "Optional references proving why the external "
                                "step is required."
                            ),
                        },
                    },
                    "required": ["wait_kind", "required_action"],
                    "additionalProperties": False,
                },
            },
            _external_wait_fail_closed,
            permission_category="read_file",
            source="builtin",
            dangerous=False,
            concurrency_safe=False,
            spec_version="task-external-wait-v1",
            effect_policy=EffectPolicy(
                policy_id="deskpet:external_action_wait:host_suspend",
                version="v1",
                kind=EffectKind.IDEMPOTENT_READ,
                max_attempts=1,
            ),
            dispatch_kind="handler",
        )
    if not registry.has(PROJECT_DIRECTORY_SELECT):
        registry.register(
            PROJECT_DIRECTORY_SELECT,
            "orchestration",
            {
                "name": PROJECT_DIRECTORY_SELECT,
                "description": (
                    "Ask the user to confirm the local directory for a multi-file "
                    "project. Call this before reading/writing project files or "
                    "spawning a child workflow when (a) the user asks to create a "
                    "new game, app, website, repository, or similar project, or "
                    "(b) the user refers to an existing project such as 'this "
                    "project/这个项目' but this task has no explicit confirmed "
                    "project directory. Use directory_mode=use_existing for (b). "
                    "A directory typed in chat is only a suggestion and must still "
                    "be confirmed through the native folder card. A previously "
                    "selected Session workspace does not remove this requirement "
                    "when the user asks for a new project. "
                    "The host shows a "
                    "native folder picker, binds the selected project directory "
                    "to this task and Session, and resumes this exact tool call. Do not invent "
                    "an absolute path and do not create project files first."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_name": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 80,
                            "description": "Short user-facing project name.",
                        },
                        "folder_name": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 80,
                            "description": (
                                "Safe suggested child folder name only, without "
                                "slashes or an absolute parent path."
                            ),
                        },
                        "project_kind": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 40,
                            "description": (
                                "Short project type such as Godot game or web app."
                            ),
                        },
                        "directory_mode": {
                            "type": "string",
                            "enum": ["create_new", "use_existing"],
                            "default": "create_new",
                            "description": (
                                "Use create_new for a new empty folder. Use "
                                "use_existing when the user explicitly asks to "
                                "continue/check/fix a project already present at "
                                "that path, including deictic 'this project' requests."
                            ),
                        },
                    },
                    "required": [
                        "project_name",
                        "folder_name",
                        "project_kind",
                        "directory_mode",
                    ],
                    "additionalProperties": False,
                },
            },
            _external_wait_fail_closed,
            permission_category="read_file",
            source="builtin",
            dangerous=False,
            concurrency_safe=False,
            spec_version="project-directory-select-v1",
            effect_policy=EffectPolicy(
                policy_id="deskpet:project_directory_select:host_suspend",
                version="v1",
                kind=EffectKind.IDEMPOTENT_READ,
                max_attempts=1,
            ),
            dispatch_kind="handler",
        )
    for name, repair in (
        (CAPABILITY_BUILD, False),
        (CAPABILITY_REPAIR, True),
    ):
        if registry.has(name):
            if registry.dispatch_kind(name) != "delegate_control":
                raise RuntimeError(
                    f"{name} is registered by a non-control provider"
                )
            continue
        registry.register(
            name,
            "orchestration",
            _capability_mutation_schema(profiles, repair=repair),
            _fail_closed,
            resource_scope_resolver=_capability_mutation_resources,
            resource_scope_resolver_id="capability-builder-host-scope",
            resource_scope_resolver_version="v1",
            permission_category="skill_install",
            source="builtin",
            dangerous=False,
            concurrency_safe=False,
            spec_version=f"capability-builder-{profiles.generation}",
            effect_policy=EffectPolicy(
                policy_id=f"deskpet:{name}:capability_mutation",
                version="v1",
                kind=EffectKind.OPAQUE_MANUAL,
                max_attempts=1,
            ),
            dispatch_kind="delegate_control",
        )


__all__ = [
    "CAPABILITY_BUILD",
    "CAPABILITY_REPAIR",
    "CORE_CONTROL_NAMES",
    "EXTERNAL_ACTION_WAIT",
    "PROJECT_DIRECTORY_SELECT",
    "WORKFLOW_SPAWN",
    "WORKSPACE_PREPARE",
    "register_orchestration_controls",
    "workflow_spawn_schema",
]
