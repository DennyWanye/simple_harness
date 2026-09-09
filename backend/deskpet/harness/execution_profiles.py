"""Execution profile descriptors shared by the Kernel and Driver adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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


__all__ = [
    "ExecutionProfileDescriptor",
    "LaunchPolicy",
]
