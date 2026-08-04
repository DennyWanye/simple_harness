"""Declarative pack overlays for the fixed production workflow adapters."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .personal_workflow import PersonalWorkflowV1, parse_personal_workflow_v1


class WorkflowPackAdapterError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorkflowPackOverlay:
    workflow_id: str
    settings: Mapping[str, Any]
    disclosure: str
    personal_workflow: PersonalWorkflowV1 | None = None


class WorkflowPackAdapterRegistry:
    """Validate overlays without replacing any host Python workflow graph."""

    _IDS = frozenset(
        {
            "workflow.deep_research",
            "workflow.presentation",
            "workflow.durable_task",
            "workflow.personal_v1",
        }
    )

    def parse(
        self,
        workflow_id: str,
        value: Mapping[str, Any],
        *,
        tool_resolver=None,
    ) -> WorkflowPackOverlay:
        if workflow_id not in self._IDS:
            raise WorkflowPackAdapterError(f"unknown workflow adapter: {workflow_id}")
        if not isinstance(value, Mapping):
            raise WorkflowPackAdapterError("workflow overlay must be an object")
        raw = dict(value)
        disclosure = raw.pop("disclosure", "")
        if not isinstance(disclosure, str):
            raise WorkflowPackAdapterError("disclosure must be a string")

        personal = None
        if workflow_id == "workflow.deep_research":
            allowed = {"mode_default", "max_sub_questions"}
            self._reject_extra(raw, allowed)
            if raw.get("mode_default", "standard") not in {
                "light",
                "standard",
                "deep",
            }:
                raise WorkflowPackAdapterError("invalid deep research mode")
            count = raw.get("max_sub_questions", 4)
            if isinstance(count, bool) or not isinstance(count, int) or not 2 <= count <= 6:
                raise WorkflowPackAdapterError("max_sub_questions must be 2..6")
        elif workflow_id == "workflow.presentation":
            allowed = {
                "pages_default",
                "depth_default",
                "theme",
                "image_mode_default",
            }
            self._reject_extra(raw, allowed)
            pages = raw.get("pages_default", 8)
            if isinstance(pages, bool) or not isinstance(pages, int) or not 1 <= pages <= 30:
                raise WorkflowPackAdapterError("pages_default must be 1..30")
            if raw.get("depth_default", "standard") not in {
                "light",
                "standard",
                "deep",
            }:
                raise WorkflowPackAdapterError("invalid presentation depth")
            if raw.get("theme", "minimal") not in {
                "minimal",
                "dark",
                "playful",
                "corporate",
            }:
                raise WorkflowPackAdapterError("theme is not built in")
            if not isinstance(raw.get("image_mode_default", True), bool):
                raise WorkflowPackAdapterError("image_mode_default must be boolean")
        elif workflow_id == "workflow.durable_task":
            self._reject_extra(raw, set())
        else:
            self._reject_extra(raw, {"graph"})
            if "graph" not in raw:
                raise WorkflowPackAdapterError("personal workflow graph is required")
            graph = raw.pop("graph")
            personal = parse_personal_workflow_v1(
                graph, tool_resolver=tool_resolver
            )
        return WorkflowPackOverlay(
            workflow_id=workflow_id,
            settings=MappingProxyType(raw),
            disclosure=disclosure,
            personal_workflow=personal,
        )

    @staticmethod
    def _reject_extra(raw: Mapping[str, Any], allowed: set[str]) -> None:
        extra = set(raw) - allowed
        if extra:
            raise WorkflowPackAdapterError(
                f"unsupported workflow settings: {sorted(extra)}"
            )


__all__ = [
    "WorkflowPackAdapterError",
    "WorkflowPackAdapterRegistry",
    "WorkflowPackOverlay",
]
