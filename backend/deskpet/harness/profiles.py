"""One immutable profile catalog shared by the model and Driver adapters.

``driver_kind`` and workflow bindings are host-only.  The provider receives
only :class:`ExecutionProfileDescriptor` values and chooses a model-spawnable
profile through ``workflow_spawn``; top-level Kernel routing never derives a
Driver from user text.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .execution_profiles import ExecutionProfileDescriptor, LaunchPolicy
from .ports import DriverStart


@dataclass(frozen=True, slots=True)
class ProfileSpec:
    profile_key: str
    route_tag: str | None
    driver_kind: str
    capabilities: frozenset[str] = frozenset()
    workflow_key: str | None = None
    workflow_name: str | None = None
    workflow_version: str | None = None
    state_factory: Callable[..., Any] | None = None
    context_factory: Callable[..., Any] | None = None
    request_factory: Callable[[DriverStart], Mapping[str, Any]] | None = None
    logical_slot: str = "kernel:0"
    display_name: str = ""
    description: str = ""
    use_when: tuple[str, ...] = ()
    avoid_when: tuple[str, ...] = ()
    input_schema_ref: str = "profile-input://generic/v1"
    launch_policy: LaunchPolicy = "model_spawnable"

    def __post_init__(self) -> None:
        for name in ("profile_key", "driver_kind", "logical_slot"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        for name in ("use_when", "avoid_when"):
            value = getattr(self, name)
            if isinstance(value, (str, bytes)) or not isinstance(value, tuple):
                raise ValueError(f"{name} must be a tuple of strings")
            if any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError(f"{name} must contain non-empty strings")
        if self.route_tag is not None and not str(self.route_tag).strip():
            raise ValueError("route_tag cannot be blank")
        workflow_fields = (
            self.workflow_key,
            self.workflow_name,
            self.workflow_version,
            self.state_factory,
            self.context_factory,
        )
        if self.driver_kind == "workflow" and any(value is None for value in workflow_fields):
            raise ValueError(f"workflow profile is incomplete: {self.profile_key}")
        if self.driver_kind != "workflow" and any(value is not None for value in workflow_fields):
            raise ValueError("non-workflow profiles cannot own workflow metadata")
        if self.launch_policy == "legacy_recovery" and self.route_tag is not None:
            raise ValueError("legacy recovery profiles cannot be routed")
    def descriptor(self, *, generation: int) -> ExecutionProfileDescriptor:
        if self.launch_policy == "legacy_recovery":
            raise ValueError("legacy recovery profiles are not model-visible")
        return ExecutionProfileDescriptor(
            profile_key=self.profile_key,
            display_name=self.display_name or self.profile_key,
            description=self.description or f"Execute the {self.profile_key} profile.",
            use_when=tuple(self.use_when),
            avoid_when=tuple(self.avoid_when),
            required_capabilities=tuple(sorted(self.capabilities)),
            input_schema_ref=self.input_schema_ref,
            launch_policy=self.launch_policy,
            catalog_generation=generation,
        )


class ProfileRegistry:
    """Frozen source for model discovery and WorkflowDriver definitions."""

    def __init__(self, specs: tuple[ProfileSpec, ...], *, generation: int = 1) -> None:
        if not specs:
            raise ValueError("at least one profile is required")
        if generation < 1:
            raise ValueError("profile catalog generation must be positive")
        by_key = {spec.profile_key: spec for spec in specs}
        routed = tuple(spec for spec in specs if spec.route_tag is not None)
        by_route = {str(spec.route_tag): spec for spec in routed}
        workflows = {
            key: spec
            for key, spec in by_key.items()
            if spec.driver_kind == "workflow"
            and spec.launch_policy != "legacy_recovery"
        }
        workflow_mapping = {
            key: str(spec.workflow_key) for key, spec in workflows.items()
        }
        if len(by_key) != len(specs) or len(by_route) != len(routed):
            raise ValueError("profile and route keys must be unique")
        if len(set(workflow_mapping.values())) != len(workflow_mapping):
            raise ValueError("workflow keys must be unique")
        descriptors = {
            key: spec.descriptor(generation=generation)
            for key, spec in by_key.items()
            if spec.launch_policy != "legacy_recovery"
        }
        self._by_key = MappingProxyType(by_key)
        self._by_route = MappingProxyType(by_route)
        self._workflows = MappingProxyType(workflows)
        self._descriptors = MappingProxyType(descriptors)
        self._generation = generation

    @property
    def specs(self) -> Mapping[str, ProfileSpec]:
        return self._by_key

    @property
    def workflow_specs(self) -> Mapping[str, ProfileSpec]:
        return self._workflows

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def descriptors(self) -> Mapping[str, ExecutionProfileDescriptor]:
        return self._descriptors

    @property
    def model_spawnable(self) -> Mapping[str, ExecutionProfileDescriptor]:
        return MappingProxyType(
            {
                key: descriptor
                for key, descriptor in self._descriptors.items()
                if descriptor.launch_policy == "model_spawnable"
            }
        )

    def resolve(
        self,
        profile_key: str,
        *,
        generation: int | None = None,
        launch_policy: LaunchPolicy | None = None,
        available_capabilities: frozenset[str] | None = None,
    ) -> ProfileSpec:
        if generation is not None and generation != self._generation:
            raise ValueError("profile catalog generation is stale")
        try:
            profile = self._by_key[profile_key]
        except KeyError as exc:
            raise ValueError(f"profile is not registered: {profile_key}") from exc
        if launch_policy is not None and profile.launch_policy != launch_policy:
            raise ValueError(
                f"profile {profile_key} cannot launch through {launch_policy}"
            )
        if available_capabilities is not None:
            missing = profile.capabilities - available_capabilities
            if missing:
                raise ValueError(
                    f"profile {profile_key} is unavailable: {','.join(sorted(missing))}"
                )
        return profile

    def profile_for_route(self, route_tag: str) -> ProfileSpec:
        try:
            return self._by_route[route_tag]
        except KeyError as exc:
            raise ValueError(f"route tag is not registered: {route_tag}") from exc
__all__ = ["ProfileRegistry", "ProfileSpec"]
