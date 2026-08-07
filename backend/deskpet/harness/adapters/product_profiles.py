"""DeskPet product profile catalog outside the product-neutral Kernel."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.ports import DriverStart
from deskpet.workflows.definitions.personal_workflow import (
    selection_from_capability_snapshot,
)


class RuntimeAdapterRegistry(Protocol):
    def get(self, workflow_name: str, workflow_version: str) -> Any: ...


def _text(request: DriverStart) -> str:
    return str(request.request_payload.get("text") or "").strip()


def _workflow_payload(kind: str, blob_root: str):
    def build(request: DriverStart) -> Mapping[str, Any]:
        payload = dict(request.request_payload)
        topic = str(payload.get("topic") or _text(request)).strip()
        if not topic:
            raise ValueError(f"{kind} profile requires a topic")
        if kind == "research":
            return {
                "topic": topic, "mode": str(payload.get("mode") or "standard"),
                "research_config": dict(payload.get("research_config") or {}),
                "blob_root": str(payload.get("blob_root") or blob_root),
            }
        full_page_images = bool(payload.get("full_page_images", False))
        editable_required = bool(
            payload.get("editable_required", not full_page_images)
        )
        return {
            "topic": topic, "pages": int(payload.get("pages") or 8),
            "depth": str(payload.get("depth") or "deep"),
            "theme": str(payload.get("theme") or "minimal"),
            "image_mode": bool(payload.get("image_mode", True)),
            # A normal PowerPoint is expected to remain editable. Flattening
            # every slide into one picture is an explicit opt-in only.
            "editable_required": editable_required,
            "full_page_images": (
                False if editable_required else full_page_images
            ),
            "title": str(payload.get("title") or topic), "author": str(payload.get("author") or "Simple Harness"),
            "output_path": payload.get("output_path"),
            "blob_root": str(payload.get("blob_root") or blob_root),
        }
    return build


_DURABLE_REQUIRED = frozenset({"session_ref", "capability_snapshot", "messages",
    "provider_snapshot", "model_snapshot", "started_at", "request_id", "turn_id"})


def _durable_task_payload(request: DriverStart) -> Mapping[str, Any]:
    payload = dict(request.request_payload)
    missing = sorted(name for name in _DURABLE_REQUIRED if payload.get(name) is None)
    if missing:
        raise ValueError(f"durable task profile payload is incomplete: {','.join(missing)}")
    return {
        "request": str(payload.get("request") or _text(request)), "session_ref": dict(payload["session_ref"]),
        "capability_snapshot": list(payload["capability_snapshot"]),
        "messages": [dict(message) for message in payload["messages"]],
        "plan_steps": list(payload.get("plan_steps") or [_text(request)]),
        "approval_required": bool(payload.get("approval_required", True)), "started_at": float(payload["started_at"]),
        "request_id": str(payload["request_id"]), "turn_id": str(payload["turn_id"]),
        "provider_snapshot": dict(payload["provider_snapshot"]),
        "model_snapshot": dict(payload["model_snapshot"]),
        "proposal_budget": int(payload.get("proposal_budget") or 40),
        "fix_budget": int(payload.get("fix_budget") or 3),
        "context_os": dict(payload["context_os"]) if isinstance(payload.get("context_os"), Mapping) else None,
        "workspace_ref": payload.get("workspace_ref"),
    }


def _personal_workflow_payload(request: DriverStart) -> Mapping[str, Any]:
    selection = selection_from_capability_snapshot(request.capability_snapshot)
    if selection is None:
        raise ValueError(
            "workflow.personal_v1 requires a frozen parent selection"
        )
    payload = dict(request.request_payload)
    objective = str(payload.get("objective") or _text(request)).strip()
    input_refs = payload.get("input_refs", ())
    if not isinstance(input_refs, (list, tuple)) or any(
        not isinstance(item, str) for item in input_refs
    ):
        raise ValueError("personal workflow input_refs must be a string list")
    # Owner/version/graph fields in request_payload are intentionally ignored.
    return {
        "personal_workflow_selection": selection.to_child_payload(),
        "inputs": {
            "objective": objective,
            "input_refs": list(input_refs),
        },
    }


def build_product_profile_registry(
    registry: RuntimeAdapterRegistry, *, blob_root: str | Path,
) -> ProfileRegistry:
    specs = (
        (
            "workflow.personal_v1",
            "workflow",
            "personal.workflow.v1",
            "personal_workflow",
            "v1",
            _personal_workflow_payload,
            "Personal Workflow",
            (
                "Execute the one frozen personal workflow selected for this "
                "Turn from the current owner's capability snapshot."
            ),
            (
                "The host snapshot contains an exact matching Personal "
                "Workflow selection.",
            ),
            (
                "No trusted selection exists or a different workflow is "
                "requested.",
            ),
        ),
        (
            "workflow.durable_task",
            "workflow",
            "durable.task.v1",
            "durable_task",
            "v1",
            _durable_task_payload,
            "Durable Task",
            "Execute a multi-step task with checkpoints, bounded repair rounds, and tool receipts.",
            ("The task needs several dependent actions or restart-safe progress.",),
            ("A direct tool call or short conversational answer is sufficient.",),
        ),
        (
            "workflow.capability_build",
            "capability_build",
            "capability.build.v1",
            "durable_task",
            "v1",
            _durable_task_payload,
            "Capability Builder",
            (
                "Build, test, install, and activate a process-isolated capability "
                "pack when the stamped catalog and configured sources have no "
                "adequate reusable capability."
            ),
            (
                "A current-stamp capability search has no adequate executable "
                "match and a reusable local adapter is required.",
            ),
            (
                "An installed capability or ordinary composition of existing "
                "tools can complete the task.",
            ),
        ),
        (
            "workflow.deep_research",
            "deep_research",
            "research.deep.v7",
            "deep_research",
            "v7",
            _workflow_payload("research", str(blob_root)),
            "Deep Research",
            "Run evidence-backed, multi-direction research and produce a sourced report.",
            ("The answer requires fresh sources, comparison, or a research report.",),
            ("The user only needs a quick explanation from current context.",),
        ),
        (
            "workflow.presentation",
            "ppt_workflow",
            "ppt.create.v1",
            "ppt_pro",
            "v1",
            _workflow_payload("ppt", str(blob_root)),
            "Presentation",
            "Create and validate a durable presentation artifact.",
            ("The requested deliverable is a slide deck or presentation.",),
            ("The user only asks for an outline or prose.",),
        ),
    )
    profiles = [
        ProfileSpec(
            "agent.general",
            None,
            "react",
            display_name="General Agent",
            description=(
                "The single root agent. It can answer directly, compose tools, "
                "search capabilities, or explicitly spawn a durable workflow."
            ),
            use_when=("Every new top-level user task starts here.",),
            avoid_when=(),
            input_schema_ref="profile-input://agent-general/v1",
            launch_policy="reserved_control",
        )
    ]
    for (
        profile_key,
        capability,
        workflow_key,
        workflow_name,
        version,
        payload_factory,
        display_name,
        description,
        use_when,
        avoid_when,
    ) in specs:
        adapter = registry.get(workflow_name, version)
        if adapter is None:
            raise RuntimeError(f"required workflow adapter is unavailable: {workflow_name}@{version}")
        profiles.append(
            ProfileSpec(
                profile_key=profile_key, route_tag=None, driver_kind="workflow",
                capabilities=frozenset({"workflow", capability}),
                workflow_key=workflow_key,
                workflow_name=workflow_name, workflow_version=version,
                state_factory=adapter.state_factory, context_factory=adapter.context_factory,
                request_factory=payload_factory,
                display_name=display_name,
                description=description,
                use_when=use_when,
                avoid_when=avoid_when,
                input_schema_ref=f"profile-input://{workflow_name}/{version}",
                launch_policy=(
                    "reserved_control"
                    if profile_key == "workflow.capability_build"
                    else "model_spawnable"
                ),
            )
        )
    return ProfileRegistry(tuple(profiles))


__all__ = ["build_product_profile_registry"]
