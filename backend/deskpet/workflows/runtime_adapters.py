"""Versioned runtime adapters used by workflow launch and recovery.

The compiled :class:`WorkflowRegistry` owns graph definitions.  This module
owns the runtime-only factories needed to start or recover those definitions.
Keeping one registry for every workflow family avoids the historical split
where DeepResearch, Code, and PPT recovery each maintained private maps.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from .contracts import JsonValue, WorkflowContext


RuntimeIdentity = tuple[str, str]
StateFactory = Callable[..., object]
ContextFactory = Callable[..., WorkflowContext | Awaitable[WorkflowContext]]
CapabilitySnapshotFactory = Callable[..., Mapping[str, JsonValue]]


class WorkflowRuntimeAdapterError(RuntimeError):
    """Stable fail-closed error for runtime registry misuse."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)


def _identity(workflow_name: str, workflow_version: str) -> RuntimeIdentity:
    name = str(workflow_name).strip()
    version = str(workflow_version).strip()
    if not name or not version:
        raise WorkflowRuntimeAdapterError(
            "invalid_runtime_identity", "workflow runtime identity must not be empty"
        )
    return name, version


@dataclass(frozen=True, slots=True)
class DeepResearchContinuationAdapter:
    """Version-owned continuation codec and child payload builder."""

    decode_snapshot: Callable[[object], object]
    build_child_payload: Callable[..., Mapping[str, JsonValue]]

    def __post_init__(self) -> None:
        if not callable(self.decode_snapshot) or not callable(self.build_child_payload):
            raise WorkflowRuntimeAdapterError(
                "invalid_deep_research_continuation",
                "continuation adapter callables are required",
            )


@dataclass(frozen=True, slots=True)
class DeepResearchRuntimeExtension:
    """Typed DeepResearch capabilities stored on a generic runtime adapter."""

    capability_snapshot_factory: CapabilitySnapshotFactory | None = None
    # Fail closed.  Legacy adapters explicitly opt stable versions in; an
    # incomplete/new adapter must never become a new-run ingress by omission.
    new_runs_enabled: bool = False
    retry_from_start: bool = False
    action_ids: tuple[str, ...] = ()
    continuation: DeepResearchContinuationAdapter | None = None
    terminal_commit_capability: str | None = None

    def __post_init__(self) -> None:
        if self.capability_snapshot_factory is not None and not callable(
            self.capability_snapshot_factory
        ):
            raise WorkflowRuntimeAdapterError(
                "invalid_capability_snapshot_factory",
                "capability_snapshot_factory must be callable",
            )
        normalized_actions = tuple(sorted({str(item).strip() for item in self.action_ids}))
        if any(not item for item in normalized_actions):
            raise WorkflowRuntimeAdapterError(
                "invalid_deep_research_action", "action ids must not be empty"
            )
        object.__setattr__(self, "action_ids", normalized_actions)
        if self.terminal_commit_capability is not None:
            capability = str(self.terminal_commit_capability).strip()
            if not capability:
                raise WorkflowRuntimeAdapterError(
                    "invalid_terminal_commit_capability",
                    "terminal commit capability must not be empty",
                )
            object.__setattr__(self, "terminal_commit_capability", capability)


DEEP_RESEARCH_EXTENSION = "deep_research"


@dataclass(frozen=True, slots=True)
class WorkflowRuntimeAdapter:
    """Runtime factories and typed extensions for one compiled identity."""

    workflow_name: str
    workflow_version: str
    state_factory: StateFactory
    context_factory: ContextFactory
    extensions: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name, version = _identity(self.workflow_name, self.workflow_version)
        if not callable(self.state_factory) or not callable(self.context_factory):
            raise WorkflowRuntimeAdapterError(
                "invalid_runtime_factory", "state and context factories must be callable"
            )
        normalized: dict[str, object] = {}
        for raw_key, value in dict(self.extensions).items():
            key = str(raw_key).strip()
            if not key or key in normalized:
                raise WorkflowRuntimeAdapterError(
                    "invalid_runtime_extension", "runtime extension keys must be unique"
                )
            normalized[key] = value
        deep = normalized.get(DEEP_RESEARCH_EXTENSION)
        if deep is not None and not isinstance(deep, DeepResearchRuntimeExtension):
            raise WorkflowRuntimeAdapterError(
                "invalid_deep_research_extension",
                "deep_research extension has an unsupported type",
            )
        if name != "deep_research" and deep is not None:
            raise WorkflowRuntimeAdapterError(
                "misplaced_deep_research_extension",
                "deep_research extension may only be registered on deep_research",
            )
        object.__setattr__(self, "workflow_name", name)
        object.__setattr__(self, "workflow_version", version)
        object.__setattr__(self, "extensions", MappingProxyType(normalized))

    @property
    def identity(self) -> RuntimeIdentity:
        return self.workflow_name, self.workflow_version

    @property
    def deep_research(self) -> DeepResearchRuntimeExtension | None:
        value = self.extensions.get(DEEP_RESEARCH_EXTENSION)
        return value if isinstance(value, DeepResearchRuntimeExtension) else None


class WorkflowRuntimeAdapterRegistry:
    """The single register/seal/activate owner for runtime adapters."""

    def __init__(self) -> None:
        self._entries: dict[RuntimeIdentity, WorkflowRuntimeAdapter] = {}
        self._sealed = False
        self._active = False

    @property
    def sealed(self) -> bool:
        return self._sealed

    @property
    def active(self) -> bool:
        return self._active

    def register(self, adapter: WorkflowRuntimeAdapter) -> None:
        if self._sealed:
            raise WorkflowRuntimeAdapterError(
                "runtime_registry_sealed", "runtime adapters cannot be registered after seal"
            )
        if not isinstance(adapter, WorkflowRuntimeAdapter):
            raise WorkflowRuntimeAdapterError(
                "invalid_runtime_adapter", "expected WorkflowRuntimeAdapter"
            )
        if adapter.identity in self._entries:
            raise WorkflowRuntimeAdapterError(
                "duplicate_runtime_adapter",
                f"runtime adapter is already registered: {adapter.identity!r}",
            )
        self._entries[adapter.identity] = adapter

    def get(
        self, workflow_name: str, workflow_version: str
    ) -> WorkflowRuntimeAdapter | None:
        return self._entries.get(_identity(workflow_name, workflow_version))

    def require(
        self, workflow_name: str, workflow_version: str
    ) -> WorkflowRuntimeAdapter:
        identity = _identity(workflow_name, workflow_version)
        adapter = self._entries.get(identity)
        if adapter is None:
            raise WorkflowRuntimeAdapterError(
                "runtime_adapter_unavailable",
                f"workflow runtime adapter is unavailable: {identity!r}",
            )
        return adapter

    def identities(self) -> tuple[RuntimeIdentity, ...]:
        return tuple(sorted(self._entries))

    def seal(self, required_identities: Sequence[RuntimeIdentity] = ()) -> None:
        if self._sealed:
            raise WorkflowRuntimeAdapterError(
                "runtime_registry_already_sealed", "runtime adapter registry is already sealed"
            )
        missing = sorted(
            identity
            for raw_name, raw_version in required_identities
            if (identity := _identity(raw_name, raw_version)) not in self._entries
        )
        if missing:
            raise WorkflowRuntimeAdapterError(
                "required_runtime_adapter_missing",
                f"required runtime adapters are missing: {missing!r}",
            )
        self._sealed = True

    def activate(self) -> None:
        if not self._sealed:
            raise WorkflowRuntimeAdapterError(
                "runtime_registry_not_sealed", "runtime registry must be sealed before activation"
            )
        if self._active:
            raise WorkflowRuntimeAdapterError(
                "runtime_registry_already_active", "runtime registry is already active"
            )
        self._active = True

    def require_active(self) -> None:
        if not self._active:
            raise WorkflowRuntimeAdapterError(
                "runtime_registry_not_active", "workflow runtime is not activated"
            )

    @property
    def deep_research(self) -> "DeepResearchRuntimeView":
        return DeepResearchRuntimeView(self)


class DeepResearchRuntimeView:
    """Read-only typed view over generic registry entries; owns no second map."""

    __slots__ = ("_registry",)

    def __init__(self, registry: WorkflowRuntimeAdapterRegistry) -> None:
        self._registry = registry

    def versions(self) -> tuple[str, ...]:
        return tuple(
            version
            for name, version in self._registry.identities()
            if name == "deep_research"
        )

    def get(self, workflow_version: str) -> DeepResearchRuntimeExtension | None:
        adapter = self._registry.get("deep_research", workflow_version)
        return adapter.deep_research if adapter is not None else None

    def require(self, workflow_version: str) -> DeepResearchRuntimeExtension:
        adapter = self._registry.require("deep_research", workflow_version)
        extension = adapter.deep_research
        if extension is None:
            raise WorkflowRuntimeAdapterError(
                "deep_research_extension_unavailable",
                f"deep_research runtime extension is unavailable: {workflow_version!r}",
            )
        return extension

    def resolve_default(
        self, configured_version: str, *, require_new_runs_enabled: bool = True
    ) -> str:
        version = str(configured_version).strip()
        extension = self.require(version)
        if require_new_runs_enabled and not extension.new_runs_enabled:
            raise WorkflowRuntimeAdapterError(
                "deep_research_new_runs_disabled",
                f"deep_research new runs are disabled for {version!r}",
            )
        return version


__all__ = [
    "ContextFactory",
    "DEEP_RESEARCH_EXTENSION",
    "DeepResearchContinuationAdapter",
    "DeepResearchRuntimeExtension",
    "DeepResearchRuntimeView",
    "RuntimeIdentity",
    "StateFactory",
    "WorkflowRuntimeAdapter",
    "WorkflowRuntimeAdapterError",
    "WorkflowRuntimeAdapterRegistry",
]
