# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Run-local Tool capability and authorization authority for SDK execution.

The SDK catalog is already an immutable, durable generation.  This module
projects that generation into the product's typed ``PreparedToolSet`` and
``PreparedAuthorizationRuntime`` contracts without consulting the legacy V2
registry or inventing process-global authorization facts.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from simple_harness import RunId, thaw_json
from simple_harness.tools import (
    CatalogRunToolExposure,
    ExecutableToolRecord,
    RuntimeCapabilityRecord,
    RuntimeToolCatalog,
    RuntimeToolCatalogError,
    ToolEffectClass,
    ToolExposureMode,
)
from simple_harness.tools.runtime_catalog import MAX_SEARCH_RESULTS

from deskpet.permissions.effect_policy import _CONFIRM_ONLY
from deskpet.sdk_adapters.effect_gate import project_tool_execution_context
from deskpet.tool_catalog.manifest import load_tool_manifest
from deskpet.tools.build_identity import EffectClass
from deskpet.tools.capabilities import (
    PreparedToolCapability,
    PreparedToolSet,
    ToolCapabilityRef,
    ToolCapabilityScopeStore,
    ToolEligibilityContext,
    ToolExecutionContext,
    ToolSelectionDecision,
    canonical_hash,
)
from deskpet.types.task_work_context import PrimaryRunWorkContext, TaskWorkContext

# S5b design-freeze §1: built-in Tools that write into, or execute inside, the
# bound workspace root.  Every name is PROJECT_EFFECT with route and TaskScope
# REQUIRED, so the SDK react barrier + Host TaskExecutionEnvelope + EffectGate
# guard each physical effect.  Read-class Tools (read_file/file_read/glob/
# file_glob/grep/file_grep/list_directory/doc_read) keep the SDK default
# (NON_PROJECT_EFFECT, route/TaskScope OPTIONAL) so the frozen record contract
# and the describe->activate nonce/hash chain are unchanged; since F-Z1 the
# four Run-workspace read Tools are instead admitted per call by
# ``deskpet.sdk_adapters.read_gate.WorkspaceReadGate`` (durable context_route
# task decision -> verified S4 binding root -> path containment).
# ``task_scope_update`` joins as a direct NON_PROJECT_EFFECT kernel Tool in
# Task 3.
PROJECT_EFFECT_TOOL_NAMES: tuple[str, ...] = (
    "write_file",
    "file_write",
    "edit_file",
    "move_file",
    "file_organize",
    "run_shell",
    "process_start",
    "doc_create",
    "doc_edit",
    "excel_create",
    "ppt_create",
    "pdf_export",
    "download_file",
    "workspace_prepare",
)

# SDK execution-policy overrides for Host tools.  Everything absent keeps the
# SDK defaults (NON_PROJECT_EFFECT / OPTIONAL / OPTIONAL).  CONTEXT_CONTROL
# structurally forces route/task-scope FORBIDDEN in the SDK record contract;
# PROJECT_EFFECT structurally requires route/task-scope REQUIRED.
SDK_TOOL_EXECUTION_POLICY_OVERRIDES: dict[str, tuple[str, str, str]] = {
    "context_route": ("context_control", "forbidden", "forbidden"),
    **{name: ("project_effect", "required", "required") for name in PROJECT_EFFECT_TOOL_NAMES},
    # semantic closure is always exposed as a
    # direct kernel Tool; route/TaskScope REQUIRED makes an UNROUTED call a
    # model-visible ROUTE_BARRIER_NOT_OBSERVED rejection rather than a whole-Run
    # fault, and the Host handler gates the rest (scope_unbound / nothing_to_close).
    "task_scope_update": ("non_project_effect", "required", "required"),
    "procedure_use": ("non_project_effect", "required", "required"),
}


# S5b design-freeze §1 + F-Z1: the read-class built-ins that read *inside* the
# bound workspace root.  They keep the SDK default execution policy (their
# frozen record and describe->activate hash chain are unchanged), and are
# admitted per call by ``read_gate.WorkspaceReadGate``: a durable
# ``context_route`` task decision, that route's exact binding root, and path
# containment inside it.  Exactly the four whose target is a single ``path``
# argument and whose handlers consume the projected workspace root; every other
# read-class Tool (``file_read``/``file_glob``/``file_grep``/``doc_read``)
# stays ``requires_project`` and is dropped from a projectless projection.
PROJECT_READ_TOOL_NAMES: tuple[str, ...] = (
    "read_file",
    "glob",
    "grep",
    "list_directory",
)

# Names a ``primary_route_capable`` projectless Run may still expose: both
# families are exposed-then-gated-at-call-time, never unbound.
_PROJECTLESS_ROUTE_CAPABLE_TOOL_NAMES: frozenset[str] = frozenset(
    (*PROJECT_EFFECT_TOOL_NAMES, *PROJECT_READ_TOOL_NAMES)
)

SDK_TOOL_AUTHORITY_RECORD_KIND = "deskpet.sdk-tool-authority"

SDK_TOOL_AUTHORITY_RECORD_VERSION = 3
SDK_FULL_CATALOG_DISCLOSURE_POLICY = "full-direct-v1"
SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY = "explicit-deferred-v1"
SDK_PERMISSION_POLICY_VERSION = "sdk-product-policy-v1"
logger = logging.getLogger(__name__)
SDK_DIRECT_TOOL_KERNEL = frozenset(
    {
        "agent",
        "agent_parallel",
        "await_subagents",
        "context_page_in",
        # Five-route Context authority: the model must reach the route
        # barrier without a tool_search hop, and search candidates must be
        # cheap to request in the same conversation.
        "context_route",
        "task_scope_search",
        # semantic closure must be reachable every provider turn
        # (a hidden Tool call is a whole-Run fault in the frozen SDK).
        "task_scope_update",
        "skill_invoke",
        # Project Skill installation is a core product control surface, not a
        # generic deferred capability. Its one-field schema is cheap to expose
        # directly and the physical effect remains confirmation-only through
        # the durable install saga.
        "skill_install",
        "spawn_subagents",
        "spawn_team",
        "todo_complete",
        "todo_write",
        "tool_activate",
        "tool_describe",
        "tool_search",
    }
)


class SdkToolAuthorityMigrationUnavailable(RuntimeError):
    code = "sdk_tool_authority_migration_unavailable"

    def __init__(self, run_id: object, version: object) -> None:
        self.run_id = str(run_id or "")
        self.version = int(version)
        super().__init__(f"{self.code}:run={self.run_id}:version={self.version}")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _required(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _v2_record_hashes(record: Mapping[str, Any]) -> tuple[str, str]:
    """Recompute the pre-workspace-binding hashes for a durable v2 record."""

    inventory = []
    for raw in record.get("inventory", ()):
        inventory.append(
            {
                key: raw[key]
                for key in (
                    "name",
                    "dispatch_kind",
                    "permission_category",
                    "source",
                    "version",
                    "execution_identity",
                    "permission_policy_version",
                    "dangerous",
                )
            }
        )
    authority_fingerprint = _canonical_sha256(
        {
            "disclosure_policy": record["disclosure_policy"],
            "direct": sorted(record["direct_names"]),
            "deferred": sorted(record["deferred_names"]),
            "inventory": inventory,
            "principal_id": record["principal_id"],
        }
    )
    scope_seed = {
        key: record[key]
        for key in (
            "run_id",
            "session_id",
            "request_id",
            "root_run_id",
            "task_scope_id",
            "catalog_generation",
            "catalog_fingerprint",
        )
    }
    scope_hash = _canonical_sha256(
        {
            **scope_seed,
            "prepared_scope_id": f"sdk-tool-scope:{_canonical_sha256(scope_seed)}",
            "prepared_schema_fingerprint": record["capability_hash"],
            "authority_fingerprint": authority_fingerprint,
            "workspace_root": record.get("workspace_root"),
            "workspace_binding_version": int(record.get("binding_version", 0)),
        }
    )
    return authority_fingerprint, scope_hash


def _field(value: object, name: str, default: object = "") -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


@dataclass(frozen=True, slots=True)
class _FrozenCapabilitySpec:
    name: str
    schema: Mapping[str, Any]
    schema_hash: str
    spec_version: str
    permission_policy_version: str
    permission_category: str
    source: str
    toolset: str
    execution_identity: str
    projectless_admission: str = "requires_project"
    dangerous: bool = False
    # the frozen EffectClass of the Tool (inventory
    # attribute, else the real Tool manifest, else ``unknown``) and whether the
    # manifest marks it dangerous.  ``confirm_only`` (EffectClass ∈
    # ``_CONFIRM_ONLY`` or manifest-dangerous) is what ``decide`` feeds into
    # ``plan_prepared_call(explicit_only=…)``: never auto-granted.
    effect_class: str = "unknown"
    manifest_dangerous: bool = False

    @property
    def confirm_only(self) -> bool:
        return is_confirm_only_effect(self.effect_class, self.manifest_dangerous)

    def env_satisfied(self) -> bool:
        return True

    def is_visible(self, _eligibility: ToolEligibilityContext) -> bool:
        return True


def _manifest_effect_index() -> Mapping[str, tuple[str, bool]]:
    """``name → (effect_class, dangerous)`` of the real Tool manifest (loaded once)."""

    global _MANIFEST_EFFECT_INDEX
    if _MANIFEST_EFFECT_INDEX is None:
        index: dict[str, tuple[str, bool]] = {}
        for item in load_tool_manifest().tools:
            name = str(item.get("name") or "")
            if not name:
                continue
            index[name] = (
                str(item.get("effect_class") or EffectClass.UNKNOWN.value),
                bool(item.get("dangerous", False)),
            )
        _MANIFEST_EFFECT_INDEX = MappingProxyType(index)
    return _MANIFEST_EFFECT_INDEX


_MANIFEST_EFFECT_INDEX: Mapping[str, tuple[str, bool]] | None = None


def frozen_tool_effect(name: str, inventory_item: object = None) -> tuple[str, bool]:
    """Frozen ``(effect_class, manifest_dangerous)`` for one Tool name.

    Precedence: an explicit inventory ``effect_class`` (durable run-start
    records / MCP descriptors) → the real Tool manifest entry → ``unknown``
    (which is confirm-only by policy, so an unclassified Tool can never be
    auto-granted).
    """

    raw = _field(inventory_item, "effect_class", None) if inventory_item is not None else None
    manifest = _manifest_effect_index().get(str(name))
    if raw is not None and str(raw).strip():
        try:
            effect = EffectClass(str(raw)).value
        except ValueError:
            effect = EffectClass.UNKNOWN.value
    elif manifest is not None:
        effect = manifest[0]
    else:
        effect = EffectClass.UNKNOWN.value
    dangerous = bool(manifest[1]) if manifest is not None else False
    raw_dangerous = (
        _field(inventory_item, "manifest_dangerous", None) if inventory_item is not None else None
    )
    if isinstance(raw_dangerous, bool):
        dangerous = dangerous or raw_dangerous
    return effect, dangerous


def is_confirm_only_effect(effect_class: str, manifest_dangerous: bool = False) -> bool:
    try:
        effect = EffectClass(str(effect_class))
    except ValueError:
        effect = EffectClass.UNKNOWN
    return bool(manifest_dangerous) or effect in _CONFIRM_ONLY


def confirm_only_tool_names(authority: SdkRunToolAuthorityV1) -> tuple[str, ...]:
    """Sorted names of the Run's Tools that can never be auto-granted."""

    return tuple(sorted(name for name, spec in authority.specs.items() if spec.confirm_only))


@dataclass(frozen=True, slots=True)
class SdkRunToolAuthorityV1:
    run_id: str
    session_id: str
    request_id: str
    root_run_id: str
    task_work_context: TaskWorkContext | PrimaryRunWorkContext
    prepared_tool_set: PreparedToolSet
    catalog_generation: int
    catalog_fingerprint: str
    capability_hash: str
    scope_hash: str
    authority_fingerprint: str
    principal_id: str
    permission_categories: Mapping[str, str]
    dispatch_kinds: Mapping[str, str]
    specs: Mapping[str, _FrozenCapabilitySpec]
    disclosure_policy: str
    workspace_resolution: Mapping[str, Any]
    workspace_identity_validator: Callable[[Mapping[str, Any]], None] | None = None
    lease_state: str = "active"

    def __post_init__(self) -> None:
        if self.lease_state not in {"active", "waiting"}:
            raise ValueError("Tool authority lease must be active or waiting")

    def execution_context(
        self,
        *,
        call_id: str = "",
        effect_id: str = "",
        turn_id: str = "",
    ) -> ToolExecutionContext:
        context = ToolExecutionContext(
            scope_id=self.prepared_tool_set.scope_id,
            session_id=self.session_id,
            request_id=self.request_id,
            root_run_id=self.root_run_id,
            turn_id=turn_id,
            workspace=self.task_work_context.workspace_root,
            write_scope_root=self.task_work_context.workspace_root,
            project_id=str(self.workspace_resolution.get("project_id") or ""),
            project_revision=int(self.workspace_resolution.get("project_revision") or 0),
            project_identity=str(self.workspace_resolution.get("project_identity") or ""),
            capability_hash=self.capability_hash,
            scope_hash=self.scope_hash,
            run_id=self.run_id,
            call_id=call_id,
            effect_id=effect_id,
            owner_key=self.principal_id,
            binding_epoch=self.task_work_context.binding_version,
            capability_snapshot_ref=self.catalog_fingerprint,
        )

        return project_tool_execution_context(context)

    def assert_workspace_current(self) -> None:
        kind = str(self.workspace_resolution.get("kind") or "")
        if kind in {"legacy", "projectless"}:
            return
        if kind == "missing":
            raise RuntimeError("workspace_unavailable")
        if kind != "project_bound":
            raise RuntimeError("workspace_resolution_invalid")
        if self.workspace_identity_validator is None:
            raise RuntimeError("workspace_identity_validator_unavailable")
        self.workspace_identity_validator(self.workspace_resolution)

    def run_start_record(self) -> dict[str, Any]:
        """Return the private durable metadata needed for exact restart.

        Provider catalog snapshots intentionally contain only provider-facing
        schemas.  Product permission, dispatch, source, and version facts are
        persisted beside the immutable RunStart so a WAITING Run never borrows
        those facts from the process's current catalog after restart.
        """

        direct_names = sorted(item.ref.name for item in self.prepared_tool_set.direct)
        deferred_names = sorted(item.name for item in self.prepared_tool_set.deferred)
        return {
            "kind": SDK_TOOL_AUTHORITY_RECORD_KIND,
            "schema_version": SDK_TOOL_AUTHORITY_RECORD_VERSION,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "root_run_id": self.root_run_id,
            "task_scope_id": self.task_work_context.task_scope_id,
            "workspace_root": self.task_work_context.workspace_root,
            "binding_version": self.task_work_context.binding_version,
            "workspace_resolution": dict(self.workspace_resolution),
            "principal_id": self.principal_id,
            "catalog_generation": self.catalog_generation,
            "catalog_fingerprint": self.catalog_fingerprint,
            "policy_fingerprint": self.prepared_tool_set.policy_fingerprint,
            "capability_hash": self.capability_hash,
            "scope_hash": self.scope_hash,
            "authority_fingerprint": self.authority_fingerprint,
            "disclosure_policy": self.disclosure_policy,
            "direct_names": direct_names,
            "deferred_names": deferred_names,
            "inventory": [
                {
                    "name": name,
                    "dispatch_kind": self.dispatch_kinds[name],
                    "permission_category": self.permission_categories[name],
                    "source": spec.source,
                    "version": spec.spec_version,
                    "execution_identity": spec.execution_identity,
                    "permission_policy_version": spec.permission_policy_version,
                    "projectless_admission": spec.projectless_admission,
                    "dangerous": spec.dangerous,
                    # the frozen EffectClass travels with the Run so a
                    # WAITING restart never re-classifies from a newer manifest.
                    "effect_class": spec.effect_class,
                    "manifest_dangerous": spec.manifest_dangerous,
                }
                for name, spec in self.specs.items()
            ],
        }


class FaultRecordingRunToolExposure(CatalogRunToolExposure):
    """Run Tool exposure that labels ``catalog_execution_policy_unavailable``.

    The frozen SDK react loop reads the execution policy of every model call
    through this port; a hidden/absent Tool raises ``RuntimeToolCatalogError``
    which escapes the loop as a whole-Run fault whose SDK-public code is only
    ``driver_failed``.  Recording the stable code here lets the Host terminal
    observer carry it into the ``run_terminal`` evidence (S5b design-freeze §4).
    """

    def __init__(
        self,
        catalog: RuntimeToolCatalog,
        *,
        run_fault_sink: Any = None,
    ) -> None:
        super().__init__(catalog)
        self._run_fault_sink = run_fault_sink

    def execution_policy(self, run_id: RunId, provider_name: str):  # type: ignore[override]
        try:
            return super().execution_policy(run_id, provider_name)
        except RuntimeToolCatalogError as exc:
            record = getattr(self._run_fault_sink, "record", None)
            if callable(record) and exc.code == "catalog_execution_policy_unavailable":
                record(run_id, exc.code)
            raise


@dataclass
class _PrepareRunState:
    """SdkRunToolAuthorityRegistry.prepare_run 的运行状态：原函数的局部变量与参数（切段用）。"""

    result: Any = None
    flow: Any = None
    authority_fingerprint: Any = None
    binding_version: Any = None
    capabilities: Any = None
    catalog: Any = None
    catalog_fingerprint: Any = None
    current: Any = None
    decisions: Any = None
    deferred: Any = None
    deferred_names: Any = None
    deferred_refs: Any = None
    disclosure_policy: Any = None
    dispatch_kinds: Any = None
    eligibility: Any = None
    frozen_specs: Any = None
    generation: Any = None
    inventory: Any = None
    inventory_by_name: Any = None
    names: Any = None
    normalized_workspace_resolution: Any = None
    permissions: Any = None
    prepared: Any = None
    principal_id: Any = None
    raw_specs: Any = None
    record: Any = None
    request_id: Any = None
    resource_records: Any = None
    root_run_id: Any = None
    run_id: Any = None
    scope_hash: Any = None
    scope_seed: Any = None
    session_id: Any = None
    task_scope_id: Any = None
    unavailable_inventory: Any = None
    work_context: Any = None
    workspace_resolution: Any = None
    workspace_root: Any = None


class SdkRunToolAuthorityRegistry:
    """Immutable per-Run Tool authority with WAITING-safe scope leases."""

    def __init__(
        self,
        *,
        scope_store: ToolCapabilityScopeStore | None = None,
        resource_records: Sequence[RuntimeCapabilityRecord] = (),
        workspace_identity_validator: Callable[[Mapping[str, Any]], None] | None = None,
        run_fault_sink: Any = None,
    ) -> None:
        self.scope_store = scope_store or ToolCapabilityScopeStore()
        self._records: dict[str, SdkRunToolAuthorityV1] = {}
        self._runtime_exposures: dict[str, CatalogRunToolExposure] = {}
        self._unavailable_capabilities: dict[str, dict[str, str]] = {}
        # HM-TO-A6 incident A: capability ids of this Run's PROJECT_EFFECT
        # Tools, read from the very records that carry the SDK execution
        # policy.  The capability bridge needs the classification for
        # capabilities that are still *deferred* — ``execution_policy`` only
        # answers for visible ones.
        self._project_effect_capabilities: dict[str, frozenset[str]] = {}
        self._resource_records = tuple(resource_records)
        self._workspace_identity_validator = workspace_identity_validator
        self._run_fault_sink = run_fault_sink
        self._terminal_listeners: list[Callable[[SdkRunToolAuthorityV1], None]] = []

    def add_terminal_listener(self, listener: Callable[[SdkRunToolAuthorityV1], None]) -> None:
        self._terminal_listeners.append(listener)

    def prepare_run(
        self,
        *,
        run_id: str,
        session_id: str,
        request_id: str,
        root_run_id: str,
        task_scope_id: str | None,
        workspace_root: str | None,
        catalog: Mapping[str, Any],
        inventory: Sequence[object],
        principal_id: str = "sdk-runtime",
        deferred_names: Iterable[str] = (),
        disclosure_policy: str | None = None,
        binding_version: int = 1,
        workspace_resolution: Mapping[str, Any] | None = None,
        resource_records: Sequence[RuntimeCapabilityRecord] | None = None,
    ) -> SdkRunToolAuthorityV1:
        state = _PrepareRunState(
            run_id=run_id,
            session_id=session_id,
            request_id=request_id,
            root_run_id=root_run_id,
            task_scope_id=task_scope_id,
            workspace_root=workspace_root,
            catalog=catalog,
            inventory=inventory,
            principal_id=principal_id,
            deferred_names=deferred_names,
            disclosure_policy=disclosure_policy,
            binding_version=binding_version,
            workspace_resolution=workspace_resolution,
            resource_records=resource_records,
        )
        state.run_id = _required(state.run_id, "run_id")
        state.session_id = _required(state.session_id, "session_id")
        state.request_id = _required(state.request_id, "request_id")
        state.root_run_id = _required(state.root_run_id, "root_run_id")
        state.task_scope_id = self._normalized_task_scope_id(
            state.binding_version,
            state.task_scope_id,
            state.workspace_resolution,
            state.workspace_root,
        )
        state.principal_id = _required(state.principal_id, "principal_id")
        state.normalized_workspace_resolution = self._normalized_workspace_resolution(
            state.binding_version, state.workspace_resolution, state.workspace_root
        )
        state.catalog_fingerprint = _required(
            state.catalog.get("content_fingerprint"), "catalog_fingerprint"
        )
        state.generation = int(state.catalog.get("generation", -1))
        if state.generation < 0:
            raise ValueError("catalog_generation must be non-negative")

        state.raw_specs = state.catalog.get("specs")
        state.inventory_by_name, state.names, state.unavailable_inventory = self._catalog_inventory(
            state.inventory, state.raw_specs
        )
        state.deferred, state.disclosure_policy = self._deferred_disclosure_policy(
            state.deferred_names, state.disclosure_policy, state.names, state.unavailable_inventory
        )
        state.capabilities: list[PreparedToolCapability] = []
        state.deferred_refs: list[ToolCapabilityRef] = []
        state.frozen_specs: dict[str, _FrozenCapabilitySpec] = {}
        state.decisions: list[ToolSelectionDecision] = []
        state.permissions: dict[str, str] = {}
        state.dispatch_kinds: dict[str, str] = {}
        self._require_catalog_fingerprints(
            state.capabilities,
            catalog=state.catalog,
            decisions=state.decisions,
            deferred=state.deferred,
            deferred_refs=state.deferred_refs,
            dispatch_kinds=state.dispatch_kinds,
            frozen_specs=state.frozen_specs,
            inventory_by_name=state.inventory_by_name,
            normalized_workspace_resolution=state.normalized_workspace_resolution,
            permissions=state.permissions,
            raw_specs=state.raw_specs,
            task_scope_id=state.task_scope_id,
        )

        state.scope_seed = {
            "run_id": state.run_id,
            "session_id": state.session_id,
            "request_id": state.request_id,
            "root_run_id": state.root_run_id,
            "task_scope_id": state.task_scope_id,
            "catalog_generation": state.generation,
            "catalog_fingerprint": state.catalog_fingerprint,
        }
        state.prepared = self._prepared_tool_scope(
            state.capabilities,
            catalog=state.catalog,
            catalog_fingerprint=state.catalog_fingerprint,
            decisions=state.decisions,
            deferred=state.deferred,
            deferred_refs=state.deferred_refs,
            generation=state.generation,
            names=state.names,
            scope_seed=state.scope_seed,
        )
        state.work_context = self._run_work_context(
            state.binding_version,
            root_run_id=state.root_run_id,
            session_id=state.session_id,
            task_scope_id=state.task_scope_id,
            workspace_root=state.workspace_root,
        )
        state.flow = None
        self._record_prepared_authority(state)
        if state.flow == "stop":
            return state.result
        return self._store_prepared_run(
            state.catalog,
            catalog_fingerprint=state.catalog_fingerprint,
            deferred=state.deferred,
            frozen_specs=state.frozen_specs,
            generation=state.generation,
            raw_specs=state.raw_specs,
            record=state.record,
            resource_records=state.resource_records,
            run_id=state.run_id,
            unavailable_inventory=state.unavailable_inventory,
        )

    def _record_prepared_authority(self, state: _PrepareRunState) -> None:
        """算授权指纹与作用域哈希，生成授权记录；已有同一运行的记录时核对一致，再打开并钉住作用域。"""

        state.authority_fingerprint = _canonical_sha256(
            {
                "disclosure_policy": state.disclosure_policy,
                "direct": sorted(set(state.names) - state.deferred),
                "deferred": sorted(state.deferred),
                "inventory": [
                    {
                        "name": name,
                        "dispatch_kind": state.dispatch_kinds[name],
                        "permission_category": state.permissions[name],
                        "source": state.frozen_specs[name].source,
                        "version": state.frozen_specs[name].spec_version,
                        "execution_identity": state.frozen_specs[name].execution_identity,
                        "permission_policy_version": (
                            state.frozen_specs[name].permission_policy_version
                        ),
                        "projectless_admission": state.frozen_specs[name].projectless_admission,
                        "dangerous": state.frozen_specs[name].dangerous,
                    }
                    for name in state.names
                ],
                "principal_id": state.principal_id,
                "workspace_resolution": state.normalized_workspace_resolution,
            }
        )
        state.scope_hash = _canonical_sha256(
            {
                **state.scope_seed,
                "prepared_scope_id": state.prepared.scope_id,
                "prepared_schema_fingerprint": state.prepared.schema_fingerprint,
                "authority_fingerprint": state.authority_fingerprint,
                "workspace_root": state.workspace_root,
                "workspace_binding_version": state.binding_version,
                "workspace_resolution": state.normalized_workspace_resolution,
            }
        )
        state.record = SdkRunToolAuthorityV1(
            run_id=state.run_id,
            session_id=state.session_id,
            request_id=state.request_id,
            root_run_id=state.root_run_id,
            task_work_context=state.work_context,
            prepared_tool_set=state.prepared,
            catalog_generation=state.generation,
            catalog_fingerprint=state.catalog_fingerprint,
            capability_hash=state.prepared.schema_fingerprint,
            scope_hash=state.scope_hash,
            authority_fingerprint=state.authority_fingerprint,
            principal_id=state.principal_id,
            permission_categories=state.permissions,
            dispatch_kinds=state.dispatch_kinds,
            specs=state.frozen_specs,
            disclosure_policy=state.disclosure_policy,
            workspace_resolution=state.normalized_workspace_resolution,
            workspace_identity_validator=self._workspace_identity_validator,
        )
        state.current = self._records.get(state.run_id)
        if state.current is not None:
            if (
                state.current.capability_hash != state.record.capability_hash
                or state.current.scope_hash != state.record.scope_hash
                or state.current.catalog_fingerprint != state.record.catalog_fingerprint
            ):
                raise RuntimeError("sdk_tool_authority_conflict")
            state.result = state.current
            state.flow = "stop"
            return
        state.eligibility = ToolEligibilityContext(state.session_id, state.request_id, "sdk-run")
        self.scope_store.open(state.prepared, state.eligibility)
        if (
            self.scope_store.pin(
                state.prepared.scope_id, session_id=state.session_id, request_id=state.request_id
            )
            is None
        ):
            self.scope_store.purge(state.prepared.scope_id)
            raise RuntimeError("sdk_tool_scope_pin_failed")

    def _normalized_task_scope_id(
        self,
        binding_version: int,
        task_scope_id: str | None,
        workspace_resolution: Mapping[str, Any] | None,
        workspace_root: str | None,
    ) -> str | None:
        if task_scope_id is not None:
            task_scope_id = _required(task_scope_id, "task_scope_id")
        elif (
            workspace_root is not None
            or type(binding_version) is not int
            or binding_version != 0
            or not isinstance(workspace_resolution, Mapping)
            or workspace_resolution.get("kind") != "projectless"
        ):
            raise ValueError("unscoped primary Tool authority must be projectless")
        return task_scope_id

    def _normalized_workspace_resolution(
        self,
        binding_version: int,
        workspace_resolution: Mapping[str, Any] | None,
        workspace_root: str | None,
    ) -> dict[str, Any]:
        if workspace_resolution is None:
            normalized_workspace_resolution: dict[str, Any] = {
                "kind": "legacy",
                "effective_root": workspace_root,
                "binding_version": binding_version,
            }
        else:
            normalized_workspace_resolution = {
                str(key): value for key, value in workspace_resolution.items()
            }
            self._require_workspace_resolution(normalized_workspace_resolution, workspace_root)
            normalized_workspace_resolution["binding_version"] = binding_version
        return normalized_workspace_resolution

    def _catalog_inventory(self, inventory: Sequence[object], raw_specs):
        if not isinstance(raw_specs, list) or not raw_specs:
            raise ValueError("frozen SDK catalog must contain complete specs")
        inventory_by_name = {
            _required(_field(item, "name"), "inventory.name"): item for item in inventory
        }
        names = tuple(_required(item.get("name"), "spec.name") for item in raw_specs)
        if len(names) != len(set(names)) or not set(names).issubset(inventory_by_name):
            raise ValueError("SDK catalog and product inventory differ")
        unavailable_inventory = {
            name: item for name, item in inventory_by_name.items() if name not in names
        }
        return inventory_by_name, names, unavailable_inventory

    def _require_catalog_fingerprints(
        self,
        capabilities: list[PreparedToolCapability],
        *,
        catalog: Mapping[str, Any],
        decisions: list[ToolSelectionDecision],
        deferred,
        deferred_refs: list[ToolCapabilityRef],
        dispatch_kinds: dict[str, str],
        frozen_specs: dict[str, _FrozenCapabilitySpec],
        inventory_by_name,
        normalized_workspace_resolution: dict[str, Any],
        permissions: dict[str, str],
        raw_specs,
        task_scope_id: str | None,
    ) -> None:
        catalog_schema_hashes = catalog.get("schema_fingerprints")
        if catalog_schema_hashes is not None and not isinstance(catalog_schema_hashes, Mapping):
            raise TypeError("catalog schema_fingerprints must be an object")
        for raw in raw_specs:
            inventory_item, name, source = self._prepared_inventory_item(
                capabilities,
                catalog_schema_hashes=catalog_schema_hashes,
                decisions=decisions,
                deferred=deferred,
                deferred_refs=deferred_refs,
                dispatch_kinds=dispatch_kinds,
                frozen_specs=frozen_specs,
                inventory_by_name=inventory_by_name,
                normalized_workspace_resolution=normalized_workspace_resolution,
                permissions=permissions,
                raw=raw,
                task_scope_id=task_scope_id,
            )

    def _run_work_context(
        self,
        binding_version: int,
        *,
        root_run_id: str,
        session_id: str,
        task_scope_id: str | None,
        workspace_root: str | None,
    ):
        work_context = (
            PrimaryRunWorkContext(session_id, root_run_id)
            if task_scope_id is None
            else TaskWorkContext(
                session_id=session_id,
                root_run_id=root_run_id,
                task_scope_id=task_scope_id,
                workspace_root=workspace_root,
                workspace_source="existing" if workspace_root else "none",
                binding_version=binding_version,
            )
        )
        return work_context

    def _store_prepared_run(
        self,
        catalog: Mapping[str, Any],
        *,
        catalog_fingerprint,
        deferred,
        frozen_specs: dict[str, _FrozenCapabilitySpec],
        generation,
        raw_specs,
        record,
        resource_records: Sequence[RuntimeCapabilityRecord] | None,
        run_id: str,
        unavailable_inventory,
    ) -> SdkRunToolAuthorityV1:
        self._records[run_id] = record
        self._runtime_tool_records(
            catalog,
            catalog_fingerprint=catalog_fingerprint,
            deferred=deferred,
            frozen_specs=frozen_specs,
            generation=generation,
            raw_specs=raw_specs,
            resource_records=resource_records,
            run_id=run_id,
            unavailable_inventory=unavailable_inventory,
        )
        return record

    def _require_workspace_resolution(
        self, normalized_workspace_resolution: dict[str, Any], workspace_root: str | None
    ) -> None:
        kind = _required(
            normalized_workspace_resolution.get("kind"),
            "workspace_resolution.kind",
        )
        if kind not in {"project_bound", "projectless", "missing", "legacy"}:
            raise ValueError("unsupported workspace resolution kind")
        effective_root = normalized_workspace_resolution.get("effective_root")
        if effective_root != workspace_root:
            raise ValueError("workspace resolution differs from effective root")
        if kind == "projectless" and workspace_root is not None:
            raise ValueError("projectless workspace cannot carry a root")
        if kind == "missing":
            raise RuntimeError("workspace_unavailable")
        if kind == "project_bound":
            for field in (
                "project_id",
                "execution_kind",
                "project_identity",
                "execution_identity",
            ):
                _required(
                    normalized_workspace_resolution.get(field),
                    f"workspace_resolution.{field}",
                )
            if int(normalized_workspace_resolution.get("project_revision", 0)) < 1:
                raise ValueError("workspace project revision must be positive")
            if self._workspace_identity_validator is None:
                raise RuntimeError("workspace_identity_validator_unavailable")

    def _deferred_disclosure_policy(
        self,
        deferred_names: Iterable[str],
        disclosure_policy: str | None,
        names,
        unavailable_inventory,
    ):
        if any(
            not str(_field(item, "availability_reason", "") or "").strip()
            for item in unavailable_inventory.values()
        ):
            raise ValueError("non-executable Tool descriptor lacks availability reason")

        deferred = frozenset(str(item) for item in deferred_names)
        unknown_deferred = deferred - set(names)
        if unknown_deferred:
            raise ValueError(f"unknown deferred SDK capabilities: {sorted(unknown_deferred)!r}")
        if disclosure_policy is None:
            disclosure_policy = (
                SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY
                if deferred
                else SDK_FULL_CATALOG_DISCLOSURE_POLICY
            )
        if disclosure_policy not in {
            SDK_FULL_CATALOG_DISCLOSURE_POLICY,
            SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY,
        }:
            raise ValueError("unsupported SDK catalog disclosure policy")
        if disclosure_policy == SDK_FULL_CATALOG_DISCLOSURE_POLICY and deferred:
            raise ValueError("full-direct disclosure cannot contain deferred tools")
        if disclosure_policy == SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY and not deferred:
            raise ValueError("explicit-deferred disclosure requires deferred tools")
        return deferred, disclosure_policy

    def _prepared_inventory_item(
        self,
        capabilities: list[PreparedToolCapability],
        *,
        catalog_schema_hashes,
        decisions: list[ToolSelectionDecision],
        deferred,
        deferred_refs: list[ToolCapabilityRef],
        dispatch_kinds: dict[str, str],
        frozen_specs: dict[str, _FrozenCapabilitySpec],
        inventory_by_name,
        normalized_workspace_resolution: dict[str, Any],
        permissions: dict[str, str],
        raw,
        task_scope_id: str | None,
    ):
        name = str(raw["name"])
        description = _required(raw.get("description"), f"{name}.description")
        input_schema = raw.get("input_schema")
        if not isinstance(input_schema, Mapping):
            raise TypeError(f"{name}.input_schema must be an object")
        inventory_item = inventory_by_name[name]
        permission = _required(
            _field(inventory_item, "permission_category"),
            f"{name}.permission_category",
        )
        dispatch = _required(_field(inventory_item, "dispatch_kind"), f"{name}.dispatch_kind")
        source = _required(_field(inventory_item, "source"), f"{name}.source")
        version = _required(_field(inventory_item, "version"), f"{name}.version")
        execution_identity = _required(
            _field(inventory_item, "execution_identity"),
            f"{name}.execution_identity",
        )
        permission_policy_version = _required(
            _field(
                inventory_item,
                "permission_policy_version",
                SDK_PERMISSION_POLICY_VERSION,
            ),
            f"{name}.permission_policy_version",
        )
        dangerous = _field(
            inventory_item,
            "dangerous",
            dispatch in {"staged", "control"},
        )
        if not isinstance(dangerous, bool):
            raise TypeError(f"{name}.dangerous must be a boolean")
        projectless_admission = str(
            _field(inventory_item, "projectless_admission", "requires_project")
        )
        if projectless_admission not in {"safe", "requires_project"}:
            raise ValueError(f"{name}.projectless_admission is invalid")
        effect_class, manifest_dangerous = frozen_tool_effect(name, inventory_item)
        # ``task_scope_id is None`` is the frozen ``primary_route_capable``
        # fact.  Such a Run may carry the two call-gated families and
        # nothing else: PROJECT_EFFECT (react barrier + envelope +
        # EffectGate) and the F-Z1 read family (WorkspaceReadGate).
        if (
            normalized_workspace_resolution["kind"] == "projectless"
            and projectless_admission != "safe"
            and not (task_scope_id is None and name in _PROJECTLESS_ROUTE_CAPABLE_TOOL_NAMES)
        ):
            raise RuntimeError("projectless_catalog_contains_project_tool")
        function_schema = {
            "name": name,
            "description": description,
            "parameters": dict(input_schema),
        }
        schema_hash = canonical_hash(dict(input_schema))
        if (
            catalog_schema_hashes is not None
            and str(catalog_schema_hashes.get(name) or "") != schema_hash
        ):
            raise ValueError(f"{name} schema fingerprint differs from catalog")
        ref = ToolCapabilityRef(
            capability_id=f"{source}:{name}:{schema_hash[:16]}",
            name=name,
            toolset=dispatch,
            source=source,
            description=description,
            schema_hash=schema_hash,
            spec_version=version,
            permission_policy_version=permission_policy_version,
            permission_category=permission,
            dangerous=dangerous,
        )
        capability = PreparedToolCapability(
            ref,
            {"type": "function", "function": function_schema},
        )
        if name in deferred:
            deferred_refs.append(ref)
            decisions.append(ToolSelectionDecision(name, "deferred", "frozen_run_scope"))
        else:
            capabilities.append(capability)
            decisions.append(ToolSelectionDecision(name, "direct", "frozen_run_scope"))
        frozen_specs[name] = _FrozenCapabilitySpec(
            name=name,
            schema=function_schema,
            schema_hash=schema_hash,
            spec_version=version,
            permission_policy_version=permission_policy_version,
            permission_category=permission,
            source=source,
            toolset=dispatch,
            execution_identity=execution_identity,
            projectless_admission=projectless_admission,
            dangerous=dangerous,
            effect_class=effect_class,
            manifest_dangerous=manifest_dangerous,
        )
        permissions[name] = permission
        dispatch_kinds[name] = dispatch
        return inventory_item, name, source

    def _prepared_tool_scope(
        self,
        capabilities: list[PreparedToolCapability],
        *,
        catalog: Mapping[str, Any],
        catalog_fingerprint,
        decisions: list[ToolSelectionDecision],
        deferred,
        deferred_refs: list[ToolCapabilityRef],
        generation,
        names,
        scope_seed,
    ):
        scope_id = f"sdk-tool-scope:{_canonical_sha256(scope_seed)}"
        policy_fingerprint = str(catalog.get("policy_fingerprint") or "").strip()
        if not policy_fingerprint:
            # Compatibility for historical durable records and isolated unit
            # fixtures created before the physical policy fingerprint became
            # part of RunStart authority.
            policy_fingerprint = _canonical_sha256(
                {
                    "kind": "sdk-frozen-product-catalog",
                    "catalog_fingerprint": catalog_fingerprint,
                    "direct": sorted(set(names) - deferred),
                    "deferred": sorted(deferred),
                }
            )
        prepared = PreparedToolSet.create(
            scope_id=scope_id,
            revision=1,
            registry_revision=generation,
            direct=capabilities,
            deferred=deferred_refs,
            activated=(),
            denied_names=(),
            policy_fingerprint=policy_fingerprint,
            decisions=decisions,
        )
        return prepared

    def _runtime_tool_records(
        self,
        catalog: Mapping[str, Any],
        *,
        catalog_fingerprint,
        deferred,
        frozen_specs: dict[str, _FrozenCapabilitySpec],
        generation,
        raw_specs,
        resource_records: Sequence[RuntimeCapabilityRecord] | None,
        run_id: str,
        unavailable_inventory,
    ) -> None:
        runtime_records = []
        for raw in raw_specs:
            name = str(raw["name"])
            frozen = frozen_specs[name]
            source_namespace = frozen.source if frozen.source.startswith("mcp:") else "builtin"
            runtime_records.append(
                ExecutableToolRecord(
                    capability_id=f"{source_namespace}:{name}",
                    namespace=source_namespace,
                    source=(
                        frozen.source if frozen.source.startswith("mcp:") else "simple_harness"
                    ),
                    source_revision=catalog_fingerprint,
                    exposure_mode=(
                        ToolExposureMode.DEFERRED if name in deferred else ToolExposureMode.DIRECT
                    ),
                    provider_name=name,
                    description=str(raw["description"]),
                    input_schema=dict(raw["input_schema"]),
                    search_terms=(
                        frozen.permission_category,
                        frozen.toolset,
                        frozen.source,
                    ),
                    **dict(
                        zip(
                            (
                                "effect_class",
                                "route_requirement",
                                "task_scope_requirement",
                            ),
                            SDK_TOOL_EXECUTION_POLICY_OVERRIDES.get(name, ()),
                        )
                    ),
                )
            )
        descriptor_specs = catalog.get("descriptor_specs", raw_specs)
        if not isinstance(descriptor_specs, list):
            raise TypeError("catalog descriptor_specs must be a list")
        descriptor_by_name = {
            _required(item.get("name"), "descriptor.name"): item
            for item in descriptor_specs
            if isinstance(item, Mapping)
        }
        unavailable_capabilities: dict[str, str] = {}
        for name, inventory_item in sorted(unavailable_inventory.items()):
            raw = descriptor_by_name.get(name)
            if raw is None or not isinstance(raw.get("input_schema"), Mapping):
                raise ValueError(f"{name} descriptor projection is missing")
            source = _required(_field(inventory_item, "source"), f"{name}.source")
            reason = _required(
                _field(inventory_item, "availability_reason"),
                f"{name}.availability_reason",
            )
            source_namespace = source if source.startswith("mcp:") else "builtin"
            capability_id = f"{source_namespace}:{name}"
            runtime_records.append(
                ExecutableToolRecord(
                    capability_id=capability_id,
                    namespace=source_namespace,
                    source=source if source.startswith("mcp:") else "simple_harness",
                    source_revision=catalog_fingerprint,
                    exposure_mode=ToolExposureMode.DEFERRED,
                    provider_name=name,
                    description=_required(raw.get("description"), f"{name}.description"),
                    input_schema=dict(raw["input_schema"]),
                    search_terms=(f"unavailable:{reason}", source, reason),
                )
            )
            unavailable_capabilities[capability_id] = reason
        self._unavailable_capabilities[run_id] = unavailable_capabilities
        self._project_effect_capabilities[run_id] = frozenset(
            record.capability_id
            for record in runtime_records
            if ToolEffectClass(record.effect_class) is ToolEffectClass.PROJECT_EFFECT
        )
        self._runtime_exposures[run_id] = FaultRecordingRunToolExposure(
            RuntimeToolCatalog(
                (
                    *runtime_records,
                    *self._resource_records,
                    *(() if resource_records is None else tuple(resource_records)),
                ),
                generation=generation or 1,
            ),
            run_fault_sink=self._run_fault_sink,
        )

    def resolve_exposure(self, run_id: object) -> CatalogRunToolExposure:
        key = run_id.value if isinstance(run_id, RunId) else str(run_id)
        try:
            return self._runtime_exposures[key]
        except KeyError as exc:
            raise RuntimeError("sdk_runtime_tool_exposure_unavailable") from exc

    def unavailable_reason(self, run_id: object, capability_id: str) -> str | None:
        key = run_id.value if isinstance(run_id, RunId) else str(run_id)
        return self._unavailable_capabilities.get(key, {}).get(str(capability_id))

    def unavailable_capabilities(self, run_id: object) -> Mapping[str, str]:
        """capability_id -> availability_reason for descriptor-only records.

        These capabilities are searchable/describable (the model may learn why
        they are absent) but can never be activated in this Run.
        """

        key = run_id.value if isinstance(run_id, RunId) else str(run_id)
        return dict(self._unavailable_capabilities.get(key, {}))

    def project_effect_capabilities(self, run_id: object) -> frozenset[str]:
        """capability_ids whose frozen SDK EffectClass is PROJECT_EFFECT.

        Frozen at Run start together with the runtime catalog records, so the
        classification never re-reads a newer manifest and stays valid for
        deferred capabilities that ``execution_policy`` refuses to answer for.
        """

        key = run_id.value if isinstance(run_id, RunId) else str(run_id)
        return self._project_effect_capabilities.get(key, frozenset())

    def is_tool_exposed(self, run_id: object, tool_name: str) -> bool:
        """Return whether the SDK Run may currently project ``tool_name``.

        The Runtime exposure checkpoint is the authority for dynamic catalog
        activation.  The legacy ``PreparedToolSet`` remains the immutable
        Run-start snapshot and therefore cannot by itself recognize Tools
        activated after an Effect receipt settles.
        """

        key = run_id.value if isinstance(run_id, RunId) else str(run_id)
        authority = self.resolve(key)
        if authority.prepared_tool_set.has_direct(tool_name):
            return True
        exposure = self.resolve_exposure(key)
        try:
            specs = exposure.provider_specs(RunId(key))
        except RuntimeToolCatalogError as exc:
            if exc.code == "catalog_run_not_restored":
                return False
            raise
        return any(spec.name == tool_name for spec in specs)

    def validate_runtime_tool_admission(
        self,
        tool_name: str,
        context: ToolExecutionContext,
        live_spec: object,
    ) -> bool:
        """Validate an activated Tool at the legacy physical dispatch seam."""

        authority = self.resolve(context.run_id)
        if (
            context.scope_id != authority.prepared_tool_set.scope_id
            or context.session_id != authority.session_id
            or context.request_id != authority.request_id
        ):
            logger.warning(
                "sdk_dynamic_admission_denied tool=%s reason=context_identity_mismatch",
                tool_name,
            )
            return False
        frozen = authority.specs.get(str(tool_name))
        if frozen is None:
            logger.warning(
                "sdk_dynamic_admission_denied tool=%s reason=missing_frozen_spec",
                tool_name,
            )
            return False
        if not self.is_tool_exposed(authority.run_id, tool_name):
            logger.warning(
                "sdk_dynamic_admission_denied tool=%s reason=not_exposed",
                tool_name,
            )
            return False
        source = str(getattr(live_spec, "source", ""))
        if source.startswith("mcp:"):
            # The SDK projection may remove provider-irrelevant JSON Schema
            # annotations that its strict validator cannot represent.  Bind
            # physical execution to the original MCP schema and incarnation
            # instead of comparing that intentionally normalized schema hash.
            live_execution_identity = _canonical_sha256(
                {
                    "name": str(getattr(live_spec, "name", "")),
                    "source": source,
                    "spec_version": str(getattr(live_spec, "spec_version", "v1")),
                    "schema": dict(getattr(live_spec, "schema", {}) or {}),
                    "runtime_provenance_ref": str(getattr(live_spec, "runtime_provenance_ref", "")),
                    "fixture_epoch": int(getattr(live_spec, "fixture_epoch", 0)),
                    "fixture_spec_hash": str(getattr(live_spec, "fixture_spec_hash", "")),
                    "stable_handler_id": str(getattr(live_spec, "stable_handler_id", "")),
                }
            )
            identity_matches = live_execution_identity == frozen.execution_identity
        else:
            identity_matches = str(getattr(live_spec, "schema_hash", "")) == frozen.schema_hash
        admitted = identity_matches and (
            str(getattr(live_spec, "spec_version", "")) == frozen.spec_version
        )
        if not source.startswith("mcp:"):
            admitted = admitted and (
                str(getattr(live_spec, "permission_policy_version", ""))
                == frozen.permission_policy_version
            )
        if not admitted:
            logger.warning(
                "sdk_dynamic_admission_denied tool=%s reason=physical_identity_mismatch",
                tool_name,
            )
        return admitted

    def restore_run(
        self,
        *,
        run_start_record: Mapping[str, Any],
        run_binding: object,
        catalog_resolver: object,
        lease_state: str,
    ) -> SdkRunToolAuthorityV1:
        """Restore a recoverable Run from its immutable historical authority.

        There is deliberately no current-catalog fallback.  The resolver must
        return the exact generation and content fingerprint captured at
        RunStart, otherwise restart fails closed.
        """

        normalized_lease = str(lease_state).strip().lower()
        if normalized_lease not in {"active", "waiting"}:
            raise ValueError("restored SDK tool lease must be active or waiting")
        if run_start_record.get("kind") != SDK_TOOL_AUTHORITY_RECORD_KIND:
            raise ValueError("sdk_tool_authority_record_kind_invalid")
        record_version = int(run_start_record.get("schema_version", -1))
        if record_version == 1:
            # v1 predates the persisted per-tool executable identity.  It is
            # impossible to prove that a restarted handler is compatible, so
            # this Run is isolated rather than inventing an identity.
            raise SdkToolAuthorityMigrationUnavailable(
                run_start_record.get("run_id"), record_version
            )
        if record_version not in {2, SDK_TOOL_AUTHORITY_RECORD_VERSION}:
            raise ValueError("sdk_tool_authority_record_version_unsupported")
        generation = int(_field(run_binding, "catalog_generation", -1))
        fingerprint = _required(_field(run_binding, "catalog_fingerprint"), "catalog_fingerprint")
        if (
            int(run_start_record.get("catalog_generation", -1)) != generation
            or str(run_start_record.get("catalog_fingerprint") or "") != fingerprint
        ):
            raise RuntimeError("sdk_tool_authority_binding_mismatch")
        for identity_field in ("run_id", "session_id", "request_id"):
            if _required(run_start_record.get(identity_field), identity_field) != _required(
                _field(run_binding, identity_field), identity_field
            ):
                raise RuntimeError("sdk_tool_authority_binding_identity_mismatch")
        resolve = getattr(catalog_resolver, "resolve", None)
        if not callable(resolve):
            raise TypeError("catalog_resolver must expose resolve")
        snapshot = resolve(generation, fingerprint)
        if snapshot is None:
            raise RuntimeError("sdk_tool_catalog_snapshot_unavailable")
        if (
            int(getattr(snapshot, "generation", -1)) != generation
            or str(getattr(snapshot, "content_fingerprint", "")) != fingerprint
        ):
            raise RuntimeError("sdk_tool_catalog_snapshot_mismatch")
        snapshot_specs = tuple(getattr(snapshot, "specs", ()))
        if not snapshot_specs:
            raise RuntimeError("sdk_tool_catalog_snapshot_empty")
        specs = [
            {
                "name": _required(_field(item, "name"), "snapshot.spec.name"),
                "description": _required(_field(item, "description"), "snapshot.spec.description"),
                "input_schema": thaw_json(_field(item, "parameters")),
            }
            for item in snapshot_specs
        ]
        raw_inventory = run_start_record.get("inventory")
        if not isinstance(raw_inventory, list) or not raw_inventory:
            raise ValueError("sdk_tool_authority_inventory_missing")
        inventory_names = {
            _required(_field(item, "name"), "inventory.name") for item in raw_inventory
        }
        specs = [item for item in specs if item["name"] in inventory_names]
        if {item["name"] for item in specs} != inventory_names:
            raise RuntimeError("sdk_tool_catalog_projection_unavailable")
        catalog = {
            "generation": generation,
            "content_fingerprint": fingerprint,
            "policy_fingerprint": str(run_start_record.get("policy_fingerprint") or ""),
            "specs": specs,
            "schema_fingerprints": {
                item["name"]: canonical_hash(item["input_schema"]) for item in specs
            },
        }
        direct_names = run_start_record.get("direct_names")
        deferred_names = run_start_record.get("deferred_names")
        if not isinstance(direct_names, list) or not isinstance(deferred_names, list):
            raise ValueError("sdk_tool_authority_disclosure_missing")
        catalog_names = {item["name"] for item in specs}
        direct = {_required(item, "direct_name") for item in direct_names}
        deferred = {_required(item, "deferred_name") for item in deferred_names}
        if direct & deferred or direct | deferred != catalog_names:
            raise ValueError("sdk_tool_authority_disclosure_differs_from_catalog")
        expected_authority_fingerprint = _required(
            run_start_record.get("authority_fingerprint"),
            "authority_fingerprint",
        )
        restored = self.prepare_run(
            run_id=_required(run_start_record.get("run_id"), "run_id"),
            session_id=_required(run_start_record.get("session_id"), "session_id"),
            request_id=_required(run_start_record.get("request_id"), "request_id"),
            root_run_id=_required(run_start_record.get("root_run_id"), "root_run_id"),
            task_scope_id=(
                None
                if run_start_record["task_scope_id"] is None
                else _required(run_start_record["task_scope_id"], "task_scope_id")
            ),
            workspace_root=(
                str(run_start_record["workspace_root"])
                if run_start_record.get("workspace_root") is not None
                else None
            ),
            catalog=catalog,
            inventory=raw_inventory,
            principal_id=_required(run_start_record.get("principal_id"), "principal_id"),
            deferred_names=deferred,
            disclosure_policy=_required(
                run_start_record.get("disclosure_policy"), "disclosure_policy"
            ),
            binding_version=int(run_start_record.get("binding_version", 0)),
            workspace_resolution=(
                run_start_record.get("workspace_resolution")
                if record_version >= 3
                else {
                    "kind": "legacy",
                    "effective_root": run_start_record.get("workspace_root"),
                    "binding_version": int(run_start_record.get("binding_version", 0)),
                }
            ),
        )
        expected_capability_hash = _required(
            run_start_record.get("capability_hash"), "capability_hash"
        )
        expected_scope_hash = _required(run_start_record.get("scope_hash"), "scope_hash")
        hashes_match = (
            restored.authority_fingerprint == expected_authority_fingerprint
            and restored.capability_hash == expected_capability_hash
            and restored.scope_hash == expected_scope_hash
        )
        if record_version == 2:
            v2_authority_hash, v2_scope_hash = _v2_record_hashes(run_start_record)
            hashes_match = (
                expected_authority_fingerprint == v2_authority_hash
                and expected_scope_hash == v2_scope_hash
                and restored.capability_hash == expected_capability_hash
            )
        if not hashes_match:
            self.mark_terminal(restored.run_id, "failed")
            raise RuntimeError("sdk_tool_authority_hash_mismatch")
        return self.mark_waiting(restored.run_id) if normalized_lease == "waiting" else restored

    def restore_waiting_run(
        self,
        *,
        run_start_record: Mapping[str, Any],
        run_binding: object,
        catalog_resolver: object,
    ) -> SdkRunToolAuthorityV1:
        return self.restore_run(
            run_start_record=run_start_record,
            run_binding=run_binding,
            catalog_resolver=catalog_resolver,
            lease_state="waiting",
        )

    def resolve(self, run_id: object) -> SdkRunToolAuthorityV1:
        value = str(getattr(run_id, "value", run_id))
        record = self._records.get(value)
        if record is None:
            raise KeyError(value)
        return record

    def mark_waiting(self, run_id: object) -> SdkRunToolAuthorityV1:
        record = self.resolve(run_id)
        waiting = replace(record, lease_state="waiting")
        self._records[record.run_id] = waiting
        return waiting

    def commit_activation(
        self,
        run_id: object,
        candidate: PreparedToolSet,
    ) -> SdkRunToolAuthorityV1:
        record = self.resolve(run_id)
        current = self.scope_store.get(
            record.prepared_tool_set.scope_id,
            session_id=record.session_id,
            request_id=record.request_id,
        )
        if current is None or candidate.revision != current.prepared.revision + 1:
            raise RuntimeError("activation_scope_stale")
        if candidate.scope_id != record.prepared_tool_set.scope_id:
            raise RuntimeError("activation_scope_crossed_run")
        self.scope_store.commit_prevalidated(candidate)
        updated = replace(
            record,
            prepared_tool_set=candidate,
            capability_hash=candidate.schema_fingerprint,
            scope_hash=_canonical_sha256(
                {
                    "prior_scope_hash": record.scope_hash,
                    "scope_id": candidate.scope_id,
                    "scope_revision": candidate.revision,
                    "capability_hash": candidate.schema_fingerprint,
                }
            ),
        )
        self._records[record.run_id] = updated
        return updated

    def mark_terminal(self, run_id: object, terminal_state: str) -> SdkRunToolAuthorityV1:
        if str(terminal_state).lower() not in {
            "completed",
            "failed",
            "cancelled",
            "stopped",
        }:
            raise ValueError("terminal_state must be completed, failed, cancelled, or stopped")
        record = self.resolve(run_id)
        self._records.pop(record.run_id, None)
        self._runtime_exposures.pop(record.run_id, None)
        self._unavailable_capabilities.pop(record.run_id, None)
        self._project_effect_capabilities.pop(record.run_id, None)
        self.scope_store.unpin(
            record.prepared_tool_set.scope_id,
            session_id=record.session_id,
            request_id=record.request_id,
        )
        self.scope_store.purge(record.prepared_tool_set.scope_id)
        for listener in tuple(self._terminal_listeners):
            listener(record)
        # every terminal path — foreground observer or
        # not — releases the process-local whole-Run fault memo of this Run.
        release = getattr(self._run_fault_sink, "release", None)
        if callable(release):
            release(record.run_id)
        return record


# Model-facing guidance for capabilities that are searchable/describable in a
# Run but can never be activated there (``ProductToolInventoryEntry.
# availability_reason``).  UI-B (S5b phase-4) showed a real model looping on
# ``tool_activate`` of a ``workspace_unscoped`` filesystem MCP tool because the
# disclosure carried no reason and the refusal was an opaque ``tool_failed``.
# The text names the product path that does work in that Run; it is not a
# system-prompt change and contains no private data.
#
# HM-TO-A6 incident A (2026-09-08 native run product-sdk-a551104a…, turn 8) adds
# a second, *route-scoped* reason to the same surface: a real model activated
# ``builtin:run_shell`` while the Run was UNROUTED, then committed
# ``direct_standalone`` and called it from the schema ``tool_describe`` had put
# in its own history.  A PROJECT_EFFECT Tool has no task execution authority
# under a standalone route, so the Host envelope authority refuses — and the
# frozen SDK turns that refusal into a whole-Run ``driver_failed`` before any
# Tool result exists.  The refusal is correct and stays; the *disclosure* was
# what was wrong, so the discovery surface now says so while the model can
# still act on it.  Unlike ``workspace_unscoped`` this one is retriable: after
# context_route commits a task route the capability activates normally.
PROJECT_EFFECT_ROUTE_REASON = "project_effect_requires_task_route"
PROJECT_EFFECT_ROUTE_NEXT_ACTION = (
    "This Tool writes into or executes inside a task workspace, so it needs a "
    "bound TaskScope. Call context_route first with route=continue_active "
    "(the Run's current active task), resume_existing (an exact task_scope_id) "
    "or create_new (a new task with title and goal). Then call tool_describe "
    "and tool_activate for this capability again. Under direct_standalone or "
    "memory_standalone it can never run; do not call it by name either."
)
# HM-TO-A6 incident Z (2026-09-09 native run product-sdk-6ad6a40a…, turn 6) is
# the third shape on this same surface, and the first where the guidance itself
# was the defect.  A projectless Run that could still route (``task_scope_id``
# None at Run start) exposes the PROJECT_EFFECT file tools but *not* the
# read-class ones — ``read_file``/``glob``/``grep``/``list_directory`` are
# ``requires_project`` and the Run-start projection drops them.  The model asked
# for ``builtin:read_file``, was refused ``workspace_unscoped``, and the static
# text told it to "use the built-in workspace file tools instead (for example
# builtin:read_file …)" — i.e. to activate the exact capability that had just
# been refused, while naming no step that this Run could actually take.  It then
# issued 16 more ``tool_search`` calls until the assembly budget closed the Run.
#
# The projection is frozen at Run start and no in-Run action can widen it, so
# the honest next step depends on the Run's own frozen facts:
#   * some capability of the same class IS activatable here -> name those, and
#     only those (never the refused id);
#   * the Run is routed but its workspace root was never prepared -> prepare it
#     once, then activate;
#   * the Run is unrouted -> context_route now so the *next* Run is scoped, and
#     stop searching in this one;
#   * nothing of the kind can ever be reached here -> say so and stop.
#
# F-Z1 (2026-09-09) closed the asymmetry the incident-Z memo recorded as a
# followup: ``read_file``/``glob``/``grep``/``list_directory`` are now exposed
# in a ``primary_route_capable`` projectless Run behind a call-time gate
# (``read_gate.WorkspaceReadGate``), exactly as the write family is exposed
# behind the react barrier + TaskExecutionEnvelope + EffectGate.  So the
# "unrouted" branch below no longer says "from the next Run": routing inside
# this Run is what unlocks both families here.  The branch still exists for
# every read/write capability the projection genuinely left out (``doc_read``,
# ``file_read``, MCP filesystem tools, …).
WORKSPACE_READ_TOOL_NAMES: tuple[str, ...] = (
    "read_file",
    "file_read",
    "list_directory",
    "glob",
    "file_glob",
    "grep",
    "file_grep",
    "doc_read",
)
WORKSPACE_WRITE_TOOL_NAMES: tuple[str, ...] = (
    "write_file",
    "file_write",
    "edit_file",
    "move_file",
    "file_organize",
    "workspace_prepare",
)
WORKSPACE_PREPARE_CAPABILITY = "builtin:workspace_prepare"


_UNSCOPED_ALTERNATIVES_ACTION = (
    "This capability is not bound to the Run workspace and cannot be activated "
    "in this Run. Do not retry tool_activate for it. Workspace file tools that "
    "ARE activatable in this Run: {names} — reach them via tool_describe -> "
    "tool_activate; both reading and writing a task workspace require calling "
    "context_route first (create_new / resume_existing / continue_active) so "
    "the task scope and workspace root are bound, and both work in this same "
    "Run once that route is committed."
)
_UNSCOPED_PREPARE_ACTION = (
    "This Run is bound to a task whose workspace root is not prepared, so no "
    "workspace file capability is scoped to it. Do not retry tool_activate for "
    "this capability. Call tool_describe and then tool_activate for "
    f"{WORKSPACE_PREPARE_CAPABILITY}, call it once with no arguments, and then "
    "activate the workspace file tool you need. If workspace_prepare is refused "
    "too, stop calling tools and tell the user this task has no bound project "
    "directory."
)
_UNSCOPED_UNROUTED_ACTION = (
    "This Run is not bound to a task, and this particular capability was left "
    "out of the projection frozen at Run start, so no action inside this Run "
    "can activate it. Do not retry tool_activate for it and do not call "
    "tool_search for it again — the result will not change. Call context_route "
    "once (create_new / resume_existing / continue_active) to bind the task, "
    "then use the built-in workspace file tools this Run already exposes: they "
    "are gated per call on the bound task workspace, so they start working in "
    "this same Run as soon as the route is committed. If none of them fits, "
    "answer the user with what you already have and say which part needs the "
    "bound task workspace."
)
_UNSCOPED_UNREACHABLE_ACTION = (
    "No workspace file capability of this kind can be activated in this Run, "
    "and the projection was frozen at Run start, so no action inside this Run "
    "can widen it. Do not retry tool_activate and do not call tool_search for "
    "file tools again. Answer the user with what you already have and say that "
    "reading or writing local files needs a task workspace bound to this "
    "conversation first."
)
UNAVAILABLE_CAPABILITY_NEXT_ACTIONS: Mapping[str, str] = {
    PROJECT_EFFECT_ROUTE_REASON: PROJECT_EFFECT_ROUTE_NEXT_ACTION,
}
WORKSPACE_UNSCOPED_REASON = "workspace_unscoped"
# One SDK search page when the Host re-ranks descriptor-only matches.
MAX_SDK_SEARCH_PAGE = int(MAX_SEARCH_RESULTS)
_DEFAULT_UNAVAILABLE_NEXT_ACTION = (
    "This capability cannot be activated in this Run. Do not retry "
    "tool_activate for it; choose a match without availability_reason or "
    "call tool_search again with a different query."
)


__all__ = (
    "PROJECT_EFFECT_ROUTE_NEXT_ACTION",
    "PROJECT_EFFECT_ROUTE_REASON",
    "PROJECT_EFFECT_TOOL_NAMES",
    "SDK_DIRECT_TOOL_KERNEL",
    "SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY",
    "SDK_FULL_CATALOG_DISCLOSURE_POLICY",
    "SDK_PERMISSION_POLICY_VERSION",
    "SDK_TOOL_AUTHORITY_RECORD_KIND",
    "SDK_TOOL_AUTHORITY_RECORD_VERSION",
    "FaultRecordingRunToolExposure",
    "SdkRunToolAuthorityRegistry",
    "SdkRunToolAuthorityV1",
    "SdkToolAuthorityMigrationUnavailable",
    "confirm_only_tool_names",
    "frozen_tool_effect",
    "is_confirm_only_effect",
)
