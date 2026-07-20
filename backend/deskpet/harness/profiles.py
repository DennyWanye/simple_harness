"""One immutable profile catalog shared by routing and Driver adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .ports import DriverStart
from .router import RouteProfile


@dataclass(frozen=True, slots=True)
class ProfileSpec:
    profile_key: str
    route_tag: str
    driver_kind: str
    capabilities: frozenset[str] = frozenset()
    workflow_key: str | None = None
    workflow_name: str | None = None
    workflow_version: str | None = None
    state_factory: Callable[..., Any] | None = None
    context_factory: Callable[..., Any] | None = None
    request_factory: Callable[[DriverStart], Mapping[str, Any]] | None = None
    logical_slot: str = "kernel:0"

    def __post_init__(self) -> None:
        for name in ("profile_key", "route_tag", "driver_kind", "logical_slot"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
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


class ProfileRegistry:
    """Frozen source for Router profiles and WorkflowDriver definitions."""

    def __init__(self, specs: tuple[ProfileSpec, ...]) -> None:
        if not specs:
            raise ValueError("at least one profile is required")
        by_key = {spec.profile_key: spec for spec in specs}
        by_route = {spec.route_tag: spec for spec in specs}
        workflows = {
            key: spec for key, spec in by_key.items() if spec.driver_kind == "workflow"
        }
        workflow_mapping = {
            key: str(spec.workflow_key) for key, spec in workflows.items()
        }
        if len(by_key) != len(specs) or len(by_route) != len(specs):
            raise ValueError("profile and route keys must be unique")
        if len(set(workflow_mapping.values())) != len(workflow_mapping):
            raise ValueError("workflow keys must be unique")
        self._by_key = MappingProxyType(by_key)
        self._by_route = MappingProxyType(by_route)
        self._workflows = MappingProxyType(workflows)

    @property
    def specs(self) -> Mapping[str, ProfileSpec]:
        return self._by_key

    @property
    def workflow_specs(self) -> Mapping[str, ProfileSpec]:
        return self._workflows

    def profile_for_route(self, route_tag: str) -> ProfileSpec:
        try:
            return self._by_route[route_tag]
        except KeyError as exc:
            raise ValueError(f"route tag is not registered: {route_tag}") from exc

    def route_profiles(self) -> tuple[RouteProfile, ...]:
        return tuple(
            RouteProfile(spec.profile_key, spec.driver_kind, spec.capabilities)
            for spec in self._by_key.values()
        )


__all__ = ["ProfileRegistry", "ProfileSpec"]
