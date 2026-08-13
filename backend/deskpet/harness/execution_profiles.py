"""Model-visible execution profile and host-only launch contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Mapping

from deskpet.execution.contracts import JsonValue


LaunchPolicy = Literal["model_spawnable", "reserved_control", "legacy_recovery"]


@dataclass(frozen=True, slots=True)
class ExecutionProfileDescriptor:
    profile_key: str
    display_name: str
    description: str
    use_when: tuple[str, ...]
    avoid_when: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    input_schema_ref: str
    launch_policy: LaunchPolicy
    catalog_generation: int

    def __post_init__(self) -> None:
        if not self.profile_key or not self.description or not self.input_schema_ref:
            raise ValueError("profile descriptor identity and description are required")
        if self.catalog_generation < 1:
            raise ValueError("profile catalog generation must be positive")

    def compact(self) -> dict[str, JsonValue]:
        return {
            "profile_key": self.profile_key,
            "display_name": self.display_name,
            "description": self.description,
            "use_when": list(self.use_when),
            "avoid_when": list(self.avoid_when),
            "required_capabilities": list(self.required_capabilities),
            "input_schema_ref": self.input_schema_ref,
            "launch_policy": self.launch_policy,
            "catalog_generation": self.catalog_generation,
        }


@dataclass(frozen=True, slots=True)
class WorkflowSpawnRequest:
    profile_key: str
    objective: str
    input_refs: tuple[str, ...]
    output_refs: tuple[str, ...]
    scratch_refs: tuple[str, ...]
    workspace_ref: str | None
    parent_run_id: str
    root_run_id: str
    task_scope_id: str
    trigger_failure_set_id: str | None
    focused_failure_ref: str | None
    supersedes_run_id: str | None
    profile_catalog_generation: int

    def __post_init__(self) -> None:
        if not self.profile_key or not self.objective:
            raise ValueError("workflow spawn requires a profile and objective")
        if not self.parent_run_id or not self.root_run_id or not self.task_scope_id:
            raise ValueError("workflow spawn host identity is required")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "profile_key": self.profile_key,
            "objective": self.objective,
            "input_refs": list(self.input_refs),
            "output_refs": list(self.output_refs),
            "scratch_refs": list(self.scratch_refs),
            "workspace_ref": self.workspace_ref,
            "parent_run_id": self.parent_run_id,
            "root_run_id": self.root_run_id,
            "task_scope_id": self.task_scope_id,
            "trigger_failure_set_id": self.trigger_failure_set_id,
            "focused_failure_ref": self.focused_failure_ref,
            "supersedes_run_id": self.supersedes_run_id,
            "profile_catalog_generation": self.profile_catalog_generation,
        }

    def fingerprint(self) -> str:
        value: Mapping[str, JsonValue] = self.to_dict()
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProfileLaunchTicket:
    ticket_ref: str
    parent_run_id: str
    root_run_id: str
    task_scope_id: str
    attempt_id: str
    provider_turn_id: str
    profile_key: str
    driver_kind: str
    profile_catalog_generation: int
    capability_snapshot_ref: str
    task_grant_ref: str
    spawn_call_id: str
    trigger_failure_set_id: str | None
    request_fingerprint: str


__all__ = [
    "ExecutionProfileDescriptor",
    "LaunchPolicy",
    "ProfileLaunchTicket",
    "WorkflowSpawnRequest",
]
