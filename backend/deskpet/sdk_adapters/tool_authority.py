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
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Protocol

from simple_harness import RunId, thaw_json
from simple_harness.tools import (
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationResult,
    CatalogRunToolExposure,
    ExecutableToolRecord,
    PreparedToolEffect,
    RuntimeCapabilityRecord,
    RuntimeToolCatalog,
    RuntimeToolCatalogError,
    ToolEffectClass,
    ToolExposureMode,
)
from simple_harness.tools.runtime_catalog import MAX_SEARCH_RESULTS

from deskpet.permissions.effect_policy import _CONFIRM_ONLY
from deskpet.permissions.runtime import (
    PreparedAuthorizationPlan,
    PreparedAuthorizationRuntime,
)
from deskpet.product_state.authorization_saga import AuthorizationSagaIdentity
from deskpet.sdk_adapters.run_route_state import ROUTED_TASK_STATE
from deskpet.tools.build_identity import EffectClass
from deskpet.tools.capabilities import (
    PreparedToolCapability,
    PreparedToolSet,
    ToolCapabilityBridgeService,
    ToolCapabilityRef,
    ToolCapabilityScopeStore,
    ToolEligibilityContext,
    ToolExecutionContext,
    ToolPolicySnapshot,
    ToolSelectionDecision,
    canonical_hash,
    reset_tool_execution_context,
    set_tool_execution_context,
)
from deskpet.types.task_grants import ResourceSelector, TaskGrant
from deskpet.types.task_work_context import PrimaryRunWorkContext, TaskWorkContext
from deskpet.workflows.effects import PreparedToolCall

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
    **{
        name: ("project_effect", "required", "required")
        for name in PROJECT_EFFECT_TOOL_NAMES
    },
    # S5b Task 3 (design-freeze §1/§7): semantic closure is always exposed as a
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
        # S5b Task 3: semantic closure must be reachable every provider turn
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
    # S5b Task 6 (AC-3⑤ / A5): the frozen EffectClass of the Tool (inventory
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
        from deskpet.tool_catalog.manifest import load_tool_manifest

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
    raw_dangerous = _field(inventory_item, "manifest_dangerous", None) if inventory_item is not None else None
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
            project_revision=int(
                self.workspace_resolution.get("project_revision") or 0
            ),
            project_identity=str(
                self.workspace_resolution.get("project_identity") or ""
            ),
            capability_hash=self.capability_hash,
            scope_hash=self.scope_hash,
            run_id=self.run_id,
            call_id=call_id,
            effect_id=effect_id,
            owner_key=self.principal_id,
            binding_epoch=self.task_work_context.binding_version,
            capability_snapshot_ref=self.catalog_fingerprint,
        )

        from deskpet.sdk_adapters.effect_gate import project_tool_execution_context
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
                    # S5b Task 6: the frozen EffectClass travels with the Run so a
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
        self._terminal_listeners: list[
            Callable[[SdkRunToolAuthorityV1], None]
        ] = []

    def bind_workspace_identity_validator(
        self, validator: Callable[[Mapping[str, Any]], None]
    ) -> None:
        if not callable(validator):
            raise TypeError("workspace identity validator must be callable")
        if (
            self._workspace_identity_validator is not None
            and self._workspace_identity_validator is not validator
        ):
            raise RuntimeError("workspace identity validator is already bound")
        self._workspace_identity_validator = validator

    def add_terminal_listener(
        self, listener: Callable[[SdkRunToolAuthorityV1], None]
    ) -> None:
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
        run_id = _required(run_id, "run_id")
        session_id = _required(session_id, "session_id")
        request_id = _required(request_id, "request_id")
        root_run_id = _required(root_run_id, "root_run_id")
        if task_scope_id is not None:
            task_scope_id = _required(task_scope_id, "task_scope_id")
        elif (workspace_root is not None or type(binding_version) is not int or binding_version != 0
              or not isinstance(workspace_resolution, Mapping)
              or workspace_resolution.get("kind") != "projectless"):
            raise ValueError("unscoped primary Tool authority must be projectless")
        principal_id = _required(principal_id, "principal_id")
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
            normalized_workspace_resolution["binding_version"] = binding_version
        catalog_fingerprint = _required(
            catalog.get("content_fingerprint"), "catalog_fingerprint"
        )
        generation = int(catalog.get("generation", -1))
        if generation < 0:
            raise ValueError("catalog_generation must be non-negative")

        raw_specs = catalog.get("specs")
        if not isinstance(raw_specs, list) or not raw_specs:
            raise ValueError("frozen SDK catalog must contain complete specs")
        inventory_by_name = {
            _required(_field(item, "name"), "inventory.name"): item
            for item in inventory
        }
        names = tuple(_required(item.get("name"), "spec.name") for item in raw_specs)
        if len(names) != len(set(names)) or not set(names).issubset(inventory_by_name):
            raise ValueError("SDK catalog and product inventory differ")
        unavailable_inventory = {
            name: item for name, item in inventory_by_name.items() if name not in names
        }
        if any(
            not str(_field(item, "availability_reason", "") or "").strip()
            for item in unavailable_inventory.values()
        ):
            raise ValueError("non-executable Tool descriptor lacks availability reason")

        deferred = frozenset(str(item) for item in deferred_names)
        unknown_deferred = deferred - set(names)
        if unknown_deferred:
            raise ValueError(
                f"unknown deferred SDK capabilities: {sorted(unknown_deferred)!r}"
            )
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
        if (
            disclosure_policy == SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY
            and not deferred
        ):
            raise ValueError("explicit-deferred disclosure requires deferred tools")
        capabilities: list[PreparedToolCapability] = []
        deferred_refs: list[ToolCapabilityRef] = []
        frozen_specs: dict[str, _FrozenCapabilitySpec] = {}
        decisions: list[ToolSelectionDecision] = []
        permissions: dict[str, str] = {}
        dispatch_kinds: dict[str, str] = {}
        catalog_schema_hashes = catalog.get("schema_fingerprints")
        if catalog_schema_hashes is not None and not isinstance(
            catalog_schema_hashes, Mapping
        ):
            raise TypeError("catalog schema_fingerprints must be an object")
        for raw in raw_specs:
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
            dispatch = _required(
                _field(inventory_item, "dispatch_kind"), f"{name}.dispatch_kind"
            )
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
                and not (
                    task_scope_id is None
                    and name in _PROJECTLESS_ROUTE_CAPABLE_TOOL_NAMES
                )
            ):
                raise RuntimeError("projectless_catalog_contains_project_tool")
            function_schema = {
                "name": name,
                "description": description,
                "parameters": dict(input_schema),
            }
            schema_hash = canonical_hash(dict(input_schema))
            if catalog_schema_hashes is not None and str(
                catalog_schema_hashes.get(name) or ""
            ) != schema_hash:
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
                decisions.append(
                    ToolSelectionDecision(name, "deferred", "frozen_run_scope")
                )
            else:
                capabilities.append(capability)
                decisions.append(
                    ToolSelectionDecision(name, "direct", "frozen_run_scope")
                )
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

        scope_seed = {
            "run_id": run_id,
            "session_id": session_id,
            "request_id": request_id,
            "root_run_id": root_run_id,
            "task_scope_id": task_scope_id,
            "catalog_generation": generation,
            "catalog_fingerprint": catalog_fingerprint,
        }
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
        work_context = (PrimaryRunWorkContext(session_id, root_run_id) if task_scope_id is None else TaskWorkContext(
            session_id=session_id,
            root_run_id=root_run_id,
            task_scope_id=task_scope_id,
            workspace_root=workspace_root,
            workspace_source="existing" if workspace_root else "none",
            binding_version=binding_version,
        ))
        authority_fingerprint = _canonical_sha256(
            {
                "disclosure_policy": disclosure_policy,
                "direct": sorted(set(names) - deferred),
                "deferred": sorted(deferred),
                "inventory": [
                    {
                        "name": name,
                        "dispatch_kind": dispatch_kinds[name],
                        "permission_category": permissions[name],
                        "source": frozen_specs[name].source,
                        "version": frozen_specs[name].spec_version,
                        "execution_identity": frozen_specs[name].execution_identity,
                        "permission_policy_version": (
                            frozen_specs[name].permission_policy_version
                        ),
                        "projectless_admission": frozen_specs[
                            name
                        ].projectless_admission,
                        "dangerous": frozen_specs[name].dangerous,
                    }
                    for name in names
                ],
                "principal_id": principal_id,
                "workspace_resolution": normalized_workspace_resolution,
            }
        )
        scope_hash = _canonical_sha256(
            {
                **scope_seed,
                "prepared_scope_id": prepared.scope_id,
                "prepared_schema_fingerprint": prepared.schema_fingerprint,
                "authority_fingerprint": authority_fingerprint,
                "workspace_root": workspace_root,
                "workspace_binding_version": binding_version,
                "workspace_resolution": normalized_workspace_resolution,
            }
        )
        record = SdkRunToolAuthorityV1(
            run_id=run_id,
            session_id=session_id,
            request_id=request_id,
            root_run_id=root_run_id,
            task_work_context=work_context,
            prepared_tool_set=prepared,
            catalog_generation=generation,
            catalog_fingerprint=catalog_fingerprint,
            capability_hash=prepared.schema_fingerprint,
            scope_hash=scope_hash,
            authority_fingerprint=authority_fingerprint,
            principal_id=principal_id,
            permission_categories=permissions,
            dispatch_kinds=dispatch_kinds,
            specs=frozen_specs,
            disclosure_policy=disclosure_policy,
            workspace_resolution=normalized_workspace_resolution,
            workspace_identity_validator=self._workspace_identity_validator,
        )
        current = self._records.get(run_id)
        if current is not None:
            if (
                current.capability_hash != record.capability_hash
                or current.scope_hash != record.scope_hash
                or current.catalog_fingerprint != record.catalog_fingerprint
            ):
                raise RuntimeError("sdk_tool_authority_conflict")
            return current
        eligibility = ToolEligibilityContext(session_id, request_id, "sdk-run")
        self.scope_store.open(prepared, eligibility)
        if self.scope_store.pin(
            prepared.scope_id, session_id=session_id, request_id=request_id
        ) is None:
            self.scope_store.purge(prepared.scope_id)
            raise RuntimeError("sdk_tool_scope_pin_failed")
        self._records[run_id] = record
        runtime_records = []
        for raw in raw_specs:
            name = str(raw["name"])
            frozen = frozen_specs[name]
            source_namespace = (
                frozen.source if frozen.source.startswith("mcp:") else "builtin"
            )
            runtime_records.append(
                ExecutableToolRecord(
                    capability_id=f"{source_namespace}:{name}",
                    namespace=source_namespace,
                    source=(
                        frozen.source
                        if frozen.source.startswith("mcp:")
                        else "simple_harness"
                    ),
                    source_revision=catalog_fingerprint,
                    exposure_mode=(
                        ToolExposureMode.DEFERRED
                        if name in deferred
                        else ToolExposureMode.DIRECT
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
        return record

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
                    "runtime_provenance_ref": str(
                        getattr(live_spec, "runtime_provenance_ref", "")
                    ),
                    "fixture_epoch": int(getattr(live_spec, "fixture_epoch", 0)),
                    "fixture_spec_hash": str(
                        getattr(live_spec, "fixture_spec_hash", "")
                    ),
                    "stable_handler_id": str(
                        getattr(live_spec, "stable_handler_id", "")
                    ),
                }
            )
            identity_matches = live_execution_identity == frozen.execution_identity
        else:
            identity_matches = (
                str(getattr(live_spec, "schema_hash", "")) == frozen.schema_hash
            )
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
        fingerprint = _required(
            _field(run_binding, "catalog_fingerprint"), "catalog_fingerprint"
        )
        if (
            int(run_start_record.get("catalog_generation", -1)) != generation
            or str(run_start_record.get("catalog_fingerprint") or "") != fingerprint
        ):
            raise RuntimeError("sdk_tool_authority_binding_mismatch")
        for identity_field in ("run_id", "session_id", "request_id"):
            if _required(
                run_start_record.get(identity_field), identity_field
            ) != _required(_field(run_binding, identity_field), identity_field):
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
                "description": _required(
                    _field(item, "description"), "snapshot.spec.description"
                ),
                "input_schema": thaw_json(_field(item, "parameters")),
            }
            for item in snapshot_specs
        ]
        raw_inventory = run_start_record.get("inventory")
        if not isinstance(raw_inventory, list) or not raw_inventory:
            raise ValueError("sdk_tool_authority_inventory_missing")
        inventory_names = {
            _required(_field(item, "name"), "inventory.name")
            for item in raw_inventory
        }
        specs = [item for item in specs if item["name"] in inventory_names]
        if {item["name"] for item in specs} != inventory_names:
            raise RuntimeError("sdk_tool_catalog_projection_unavailable")
        catalog = {
            "generation": generation,
            "content_fingerprint": fingerprint,
            "policy_fingerprint": str(
                run_start_record.get("policy_fingerprint") or ""
            ),
            "specs": specs,
            "schema_fingerprints": {
                item["name"]: canonical_hash(item["input_schema"])
                for item in specs
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
            root_run_id=_required(
                run_start_record.get("root_run_id"), "root_run_id"
            ),
            task_scope_id=(None if run_start_record["task_scope_id"] is None
                           else _required(run_start_record["task_scope_id"], "task_scope_id")),
            workspace_root=(
                str(run_start_record["workspace_root"])
                if run_start_record.get("workspace_root") is not None
                else None
            ),
            catalog=catalog,
            inventory=raw_inventory,
            principal_id=_required(
                run_start_record.get("principal_id"), "principal_id"
            ),
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
                    "binding_version": int(
                        run_start_record.get("binding_version", 0)
                    ),
                }
            ),
        )
        expected_capability_hash = _required(
            run_start_record.get("capability_hash"), "capability_hash"
        )
        expected_scope_hash = _required(
            run_start_record.get("scope_hash"), "scope_hash"
        )
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
        return (
            self.mark_waiting(restored.run_id)
            if normalized_lease == "waiting"
            else restored
        )

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
            raise ValueError(
                "terminal_state must be completed, failed, cancelled, or stopped"
            )
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
        # S5b Task 6 (review F-6): every terminal path — foreground observer or
        # not — releases the process-local whole-Run fault memo of this Run.
        release = getattr(self._run_fault_sink, "release", None)
        if callable(release):
            release(record.run_id)
        return record


class _FrozenCapabilityRegistry:
    """The exact SDK generation behind ``ToolCapabilityBridgeService``."""

    def __init__(
        self,
        authorities: SdkRunToolAuthorityRegistry,
        context_getter: Callable[[], ToolExecutionContext],
    ) -> None:
        self._authorities = authorities
        self._context_getter = context_getter

    def _authority(self) -> SdkRunToolAuthorityV1:
        return self._authorities.resolve(self._context_getter().run_id)

    def read_policy_snapshot(self, *, strict: bool = True) -> ToolPolicySnapshot:
        del strict
        authority = self._authority()
        return ToolPolicySnapshot(fingerprint=authority.prepared_tool_set.policy_fingerprint)

    def get(self, name: str) -> _FrozenCapabilitySpec | None:
        return self._authority().specs.get(str(name))

    def validate_prepared_tool_set(
        self,
        prepared: PreparedToolSet,
        *,
        eligibility: ToolEligibilityContext,
    ) -> None:
        authority = self._authority()
        if prepared.scope_id != authority.prepared_tool_set.scope_id:
            raise RuntimeError("capability_stale")
        if (
            eligibility.session_id != authority.session_id
            or eligibility.request_id != authority.request_id
        ):
            raise RuntimeError("capability_denied")
        for ref in (
            *(item.ref for item in (*prepared.direct, *prepared.activated)),
            *prepared.deferred,
        ):
            spec = authority.specs.get(ref.name)
            if spec is None or spec.schema_hash != ref.schema_hash:
                raise RuntimeError("capability_stale")


class SdkCapabilityBridgeAdapter:
    """Bind the real product capability service to the active SDK Run scope."""

    def __init__(
        self,
        authorities: SdkRunToolAuthorityRegistry,
        context_getter: Callable[[], ToolExecutionContext],
    ) -> None:
        self._authorities = authorities
        self._context_getter = context_getter
        self._service = ToolCapabilityBridgeService(
            _FrozenCapabilityRegistry(authorities, context_getter),
            authorities.scope_store,
        )

    def _invoke(self, method: str, *args: Any, **kwargs: Any) -> Any:
        context = self._context_getter()
        token = set_tool_execution_context(context)
        try:
            return getattr(self._service, method)(*args, **kwargs)
        finally:
            reset_tool_execution_context(token)

    def search(self, query: str, *, limit: int = 10, cursor: int = 0) -> dict[str, Any]:
        return self._invoke("search", query, limit=limit, cursor=cursor)

    def suggestions(self, capability_id: str, *, limit: int = 3) -> list[dict[str, Any]]:
        return self._invoke("suggestions", capability_id, limit=limit)

    def describe(self, capability_id: str) -> dict[str, Any]:
        return self._invoke("describe", capability_id)

    def activate(
        self, capability_id: str, schema_hash: str, describe_nonce: str
    ) -> Any:
        context = self._context_getter()
        proposal = self._invoke(
            "activate", capability_id, schema_hash, describe_nonce
        )
        store = self._authorities.scope_store
        current = store.get(
            context.scope_id,
            session_id=context.session_id,
            request_id=context.request_id,
        )
        if current is None or current.prepared.revision != proposal.base_scope_revision:
            raise RuntimeError("activation_scope_stale")
        self._authorities.commit_activation(
            context.run_id,
            current.prepared.activate(proposal.prepared_capability),
        )
        return proposal


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


@dataclass(frozen=True, slots=True)
class RunAvailabilityFacts:
    """The Run-start facts the ``workspace_unscoped`` guidance may depend on.

    Everything here is frozen at Run start, which is exactly why the guidance
    can promise what it promises: no in-Run action changes any of it.
    """

    routed: bool = False
    workspace_bound: bool = False
    exposed_tool_names: frozenset[str] = frozenset()

    def alternatives_for(self, capability_id: str) -> tuple[str, ...]:
        """Capabilities of the refused one's own class that this Run can activate.

        A read request must never be answered with ``edit_file``: naming a write
        tool as the substitute for ``read_file`` is the same class of wrong
        answer as naming ``read_file`` itself.
        """

        name = str(capability_id).rpartition(":")[2]
        if name in WORKSPACE_READ_TOOL_NAMES:
            family: tuple[str, ...] = WORKSPACE_READ_TOOL_NAMES
        elif name in WORKSPACE_WRITE_TOOL_NAMES:
            family = WORKSPACE_WRITE_TOOL_NAMES
        else:
            # A non-builtin (MCP) capability has no product class; every
            # workspace file tool this Run exposes is a candidate.
            family = (*WORKSPACE_READ_TOOL_NAMES, *WORKSPACE_WRITE_TOOL_NAMES)
        return tuple(
            f"builtin:{item}"
            for item in family
            if item != name and item in self.exposed_tool_names
        )


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


def unavailable_capability_next_action(
    reason: str,
    *,
    capability_id: str = "",
    facts: RunAvailabilityFacts | None = None,
) -> str:
    """One executable next step for a capability this Run cannot activate.

    Never names ``capability_id`` itself as the way forward (incident Z), and
    never names a capability this Run also reports unavailable.
    """

    reason = str(reason)
    if reason == WORKSPACE_UNSCOPED_REASON:
        if facts is None:
            return _UNSCOPED_UNREACHABLE_ACTION
        alternatives = facts.alternatives_for(capability_id)
        if alternatives:
            return _UNSCOPED_ALTERNATIVES_ACTION.format(names=", ".join(alternatives))
        if facts.routed and not facts.workspace_bound:
            return _UNSCOPED_PREPARE_ACTION
        if not facts.routed:
            return _UNSCOPED_UNROUTED_ACTION
        return _UNSCOPED_UNREACHABLE_ACTION
    return UNAVAILABLE_CAPABILITY_NEXT_ACTIONS.get(
        reason, _DEFAULT_UNAVAILABLE_NEXT_ACTION
    )


class SdkRuntimeCapabilityBridgeAdapter:
    """Delegate discovery and activation to the SDK-owned Runtime catalog."""

    def __init__(
        self,
        authorities: SdkRunToolAuthorityRegistry,
        context_getter: Callable[[], ToolExecutionContext],
        *,
        route_state_memo: Any = None,
    ) -> None:
        self._authorities = authorities
        self._context_getter = context_getter
        # HM-TO-A6 incident A: written by ``ProductRunContextAuthority`` for the
        # turn whose provider request produced this very tool call.  ``None``
        # (unwired, or no snapshot prepared yet) keeps the previous behaviour —
        # this memo shapes disclosure, never authority.
        self._route_state_memo = route_state_memo

    def _binding(self) -> tuple[RunId, CatalogRunToolExposure]:
        run_id = RunId(self._context_getter().run_id)
        return run_id, self._authorities.resolve_exposure(run_id)

    def _availability_facts(self, run_id: RunId) -> RunAvailabilityFacts:
        """Run-start facts behind the ``workspace_unscoped`` guidance.

        Incident Z: the guidance must describe *this* Run, so it is derived from
        the frozen authority rather than from a static table.  A Run without an
        authority record (restart, or a bridge wired without one) falls back to
        the "cannot be reached here" text, which is never wrong.
        """

        try:
            authority = self._authorities.resolve(run_id)
        except KeyError:
            return RunAvailabilityFacts()
        work = authority.task_work_context
        return RunAvailabilityFacts(
            routed=getattr(work, "task_scope_id", None) is not None,
            workspace_bound=bool(getattr(work, "workspace_root", None)),
            exposed_tool_names=frozenset(str(name) for name in authority.specs),
        )

    def unavailable_next_action(self, capability_id: str, reason: str) -> str:
        """Model-facing next step for a refusal, shaped by this Run's facts."""

        run_id = RunId(self._context_getter().run_id)
        return unavailable_capability_next_action(
            reason,
            capability_id=str(capability_id),
            facts=self._availability_facts(run_id),
        )

    def _route_blocked_capabilities(self, run_id: RunId) -> frozenset[str]:
        """PROJECT_EFFECT capability ids that this Run's route can never run."""

        if self._route_state_memo is None:
            return frozenset()
        state = self._route_state_memo.read(run_id)
        if state is None or str(state) == ROUTED_TASK_STATE:
            return frozenset()
        return self._authorities.project_effect_capabilities(run_id)

    def _unavailable_with_route(self, run_id: RunId) -> dict[str, str]:
        """Descriptor-only reasons plus this turn's route-blocked capabilities.

        A capability that already carries its own availability_reason keeps it:
        the durable Run-start reason is the more specific fact.
        """

        reasons = self._authorities.unavailable_capabilities(run_id)
        merged = dict(reasons)
        for capability_id in self._route_blocked_capabilities(run_id):
            merged.setdefault(capability_id, PROJECT_EFFECT_ROUTE_REASON)
        return merged

    def search(self, query: str, *, limit: int = 10, cursor: int = 0) -> dict[str, Any]:
        run_id, exposure = self._binding()
        unavailable = self._unavailable_with_route(run_id)
        if not unavailable:
            page = exposure.search(run_id, query, limit=limit, cursor=cursor)
            return {
                "matches": [item.to_json() for item in page.items],
                "count": len(page.items),
                "query": str(query),
                "next_cursor": page.next_cursor,
            }
        # Descriptor-only capabilities stay searchable so the model learns why
        # they are absent, but they must never crowd out activatable matches:
        # the SDK ranks by token hits, and a long MCP description outranked
        # every built-in file tool in UI-B.  Re-rank on the Host side (stable
        # partition, SDK order preserved within each group) and paginate the
        # re-ranked list so cursor semantics stay consistent.
        facts = self._availability_facts(run_id)
        activatable: list[dict[str, Any]] = []
        descriptor_only: list[dict[str, Any]] = []
        sdk_cursor = 0
        while True:
            page = exposure.search(run_id, query, limit=MAX_SDK_SEARCH_PAGE, cursor=sdk_cursor)
            for item in page.items:
                value = item.to_json()
                reason = unavailable.get(str(value.get("capability_id")))
                if reason is None:
                    activatable.append(value)
                else:
                    descriptor_only.append(
                        {
                            **value,
                            "activatable": False,
                            "availability_reason": reason,
                            # Incident Z: the model looped on tool_search because
                            # the per-match hint told it to activate the very
                            # capability the same page reported unavailable.
                            "next_action": unavailable_capability_next_action(
                                reason,
                                capability_id=str(value.get("capability_id") or ""),
                                facts=facts,
                            ),
                        }
                    )
            if page.next_cursor is None:
                break
            sdk_cursor = page.next_cursor
        ranked = [*activatable, *descriptor_only]
        start = max(0, int(cursor))
        stop = start + max(1, int(limit))
        matches = ranked[start:stop]
        result: dict[str, Any] = {
            "matches": matches,
            "count": len(matches),
            "query": str(query),
            "next_cursor": stop if stop < len(ranked) else None,
        }
        unavailable_count = sum(1 for item in matches if "availability_reason" in item)
        if unavailable_count:
            result["unavailable_count"] = unavailable_count
            reasons = sorted({str(item["availability_reason"]) for item in matches if "availability_reason" in item})
            first = next(
                str(item.get("capability_id") or "")
                for item in matches
                if str(item.get("availability_reason") or "") == reasons[0]
            )
            result["next_action"] = (
                f"{unavailable_count} match(es) carry availability_reason "
                f"({', '.join(reasons)}) and cannot be activated in this Run; "
                "prefer matches without availability_reason. "
                + unavailable_capability_next_action(
                    reasons[0], capability_id=first, facts=facts
                )
            )
        return result

    def suggestions(self, capability_id: str, *, limit: int = 3) -> list[dict[str, Any]]:
        query = str(capability_id).replace(":", " ")
        return list(self.search(query, limit=limit)["matches"])

    def describe(self, capability_id: str) -> dict[str, Any]:
        run_id, exposure = self._binding()
        value = exposure.describe(run_id, str(capability_id)).to_json()
        descriptor = value["descriptor"]
        if not isinstance(descriptor, dict):
            raise TypeError("catalog_descriptor_invalid")
        projection = value.get("projection")
        if not isinstance(projection, dict):
            raise TypeError("catalog_projection_invalid")
        # The SDK projection carries its own provider-input-schema hash while
        # activation is intentionally bound to the broader capability hash.
        # Exposing both values under the same ``schema_hash`` label made real
        # models occasionally copy projection.schema_hash into tool_activate,
        # which correctly failed closed but looked like an authorization hang.
        # The provider hash is not an activation input, so omit it from the
        # model-facing projection and expose exactly one schema_hash to copy.
        value["projection"] = {
            key: item for key, item in projection.items() if key != "schema_hash"
        }
        value["schema_hash"] = value["capability_hash"]
        value["describe_nonce"] = value["nonce"]
        value["capability_id"] = descriptor["capability_id"]
        reason = self._unavailable_with_route(run_id).get(
            str(descriptor["capability_id"])
        )
        if reason is not None:
            # Descriptor-only in this Run: say so before the model wastes a
            # tool_activate turn (UI-B looped three times on exactly this).
            value["activatable"] = False
            value["activation_required"] = False
            value["availability_reason"] = reason
            value["next_action"] = (
                "Do not call tool_activate for this capability_id. "
                + unavailable_capability_next_action(
                    reason,
                    capability_id=str(descriptor["capability_id"]),
                    facts=self._availability_facts(run_id),
                )
            )
            return value
        value["activatable"] = True
        value["activation_required"] = True
        value["next_action"] = (
            "Call tool_activate now, copying the top-level capability_id, "
            "schema_hash, and describe_nonce from this response exactly. "
            "Do not call the described target tool until tool_activate "
            "succeeds and the next model iteration exposes it."
        )
        return value

    def activate(
        self, capability_id: str, schema_hash: str, describe_nonce: str
    ) -> Any:
        run_id, exposure = self._binding()
        unavailable = self._authorities.unavailable_reason(run_id, capability_id)
        if unavailable is not None:
            raise RuntimeError(f"tool_unavailable:{unavailable}")
        if str(capability_id) in self._route_blocked_capabilities(run_id):
            # Deny is unchanged either way — a standalone route has no task
            # execution authority.  Refusing here makes it a model-visible
            # rejection it can act on, instead of a whole-Run fault later.
            raise RuntimeError(PROJECT_EFFECT_ROUTE_REASON)
        described = exposure.describe(run_id, str(capability_id))
        if str(schema_hash) != described.capability_hash:
            raise RuntimeError("activation_schema_hash_stale")
        return exposure.prepare_activation(
            run_id,
            str(capability_id),
            str(describe_nonce),
        )


@dataclass(frozen=True, slots=True)
class _PreparedAuthorizationFacts:
    authority: SdkRunToolAuthorityV1
    call: PreparedToolCall
    plan: PreparedAuthorizationPlan
    grant: TaskGrant


@dataclass(frozen=True, slots=True)
class SkillInstallPreflightReady:
    intent_id: str
    artifact_ref: str
    content_digest: str
    member_digest: str
    member_summary: tuple[Mapping[str, Any], ...]
    permission_summary: tuple[str, ...]
    expires_at: float


@dataclass(frozen=True, slots=True)
class SkillInstallPreflightRejected:
    failure_receipt_ref: str
    code: str
    public_message: str
    retryable: bool
    correlation_id: str
    attempt_generation: int = 1
    allowed_actions: tuple[str, ...] = ("change_source", "cancel")

    def sdk_public_message(self) -> str:
        return json.dumps(
            {
                "schema": "skill-install-preflight-rejection-v1",
                "code": self.code,
                "public_message": self.public_message,
                "retryable": self.retryable,
                "failure_receipt_ref": self.failure_receipt_ref,
                "attempt_generation": self.attempt_generation,
                "allowed_actions": list(self.allowed_actions),
                "correlation_id": self.correlation_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


SkillInstallPreflightOutcome = (
    SkillInstallPreflightReady | SkillInstallPreflightRejected
)


class ProjectSkillInstallPreflightPort(Protocol):
    async def stage_authorization_preflight(
        self,
        *,
        prepared: PreparedToolEffect,
        context: ToolExecutionContext,
        args_hash: str,
        principal_id: str,
    ) -> SkillInstallPreflightOutcome: ...


class SdkPreparedAuthorizationPolicy:
    """Map SDK effects onto the product prepared authorization runtime."""

    def __init__(
        self,
        runtime: PreparedAuthorizationRuntime,
        authorities: SdkRunToolAuthorityRegistry,
        *,
        clock: Callable[[], float] = time.time,
        initial_policy_generation: int | None = None,
        skill_install_preflight: ProjectSkillInstallPreflightPort | None = None,
    ) -> None:
        self._runtime = runtime
        self._authorities = authorities
        self._clock = clock
        self._facts: dict[tuple[str, str], _PreparedAuthorizationFacts] = {}
        self._auto_skill_approvals: dict[tuple[str, str], Mapping[str, Any]] = {}
        self._skill_install_preflight = skill_install_preflight
        if initial_policy_generation is not None and initial_policy_generation < 0:
            raise ValueError("initial_policy_generation must be non-negative")
        self._policy_generation = initial_policy_generation
        authorities.add_terminal_listener(self._release_run)

    def _release_run(self, authority: SdkRunToolAuthorityV1) -> None:
        self._facts = {
            identity: facts
            for identity, facts in self._facts.items()
            if facts.authority.run_id != authority.run_id
        }
        self._auto_skill_approvals = {
            identity: approval
            for identity, approval in self._auto_skill_approvals.items()
            if identity[0] != authority.run_id
        }

    def auto_skill_approval_for(
        self, prepared: PreparedToolEffect
    ) -> Mapping[str, Any] | None:
        return self._auto_skill_approvals.get(
            (prepared.run_id.value, prepared.effect_id.value)
        )

    def current_policy_generation(self) -> int:
        if self._policy_generation is None:
            raise RuntimeError("product authorization policy has not been evaluated")
        return self._policy_generation

    def update_policy_generation(self, generation: int) -> None:
        value = int(generation)
        if value < 0:
            raise ValueError("policy generation must be non-negative")
        if self._policy_generation is not None and value < self._policy_generation:
            raise RuntimeError("authorization policy generation regressed")
        self._policy_generation = value

    async def _stage_skill_preflight(
        self, *, prepared: PreparedToolEffect, context: ToolExecutionContext,
        call: PreparedToolCall, authority: SdkRunToolAuthorityV1,
    ) -> SkillInstallPreflightReady | AuthorizationResult | None:
        if call.tool_name != "skill_install":
            return None
        if self._skill_install_preflight is None:
            return AuthorizationResult(
                AuthorizationDecision.DENY,
                reason_code="skill_install_preflight_unavailable",
            )
        outcome = await self._skill_install_preflight.stage_authorization_preflight(
            prepared=prepared, context=context, args_hash=call.args_hash,
            principal_id=authority.principal_id,
        )
        if isinstance(outcome, SkillInstallPreflightRejected):
            return AuthorizationResult(
                AuthorizationDecision.DENY, reason_code=outcome.code,
                public_message=outcome.sdk_public_message(),
                receipt_ref=outcome.failure_receipt_ref,
            )
        if not isinstance(outcome, SkillInstallPreflightReady):
            raise TypeError("skill install preflight returned an invalid outcome")
        return outcome

    @staticmethod
    def _resource_selectors(
        prepared: PreparedToolEffect,
        *,
        args_hash: str,
    ) -> tuple[ResourceSelector, ...]:
        selectors: list[ResourceSelector] = []
        for resource in prepared.resources:
            namespace = str(resource.namespace).casefold()
            value = str(resource.resource_id)
            actions = tuple(str(item) for item in resource.actions)
            if namespace in {"filesystem", "file", "path"}:
                selectors.append(ResourceSelector.filesystem(value, *actions))
            elif namespace in {"network", "network_origin"}:
                selectors.append(ResourceSelector.network(value, *actions))
            elif namespace in {
                "process_executable",
                "package_source",
                "application",
                "desktop_target",
                "capability_managed_root",
                "system_change",
            }:
                selectors.append(ResourceSelector(namespace, value, actions))  # type: ignore[arg-type]
            else:
                raise ValueError(f"unsupported SDK Tool resource namespace: {namespace}")
        if selectors:
            return tuple(selectors)
        # A missing sidecar must not collapse to an unscoped allow. Bind the
        # exact argument hash into a symbolic resource so the resulting grant
        # cannot authorize another Tool or another argument payload.
        return (
            ResourceSelector(
                "system_change",
                f"sdk-tool:{prepared.call.name}:{args_hash}",
                ("invoke",),
            ),
        )

    def _prepared_call(
        self,
        prepared: PreparedToolEffect,
        authority: SdkRunToolAuthorityV1,
    ) -> PreparedToolCall:
        name = prepared.call.name
        spec = authority.specs.get(name)
        if spec is None or not self._authorities.is_tool_exposed(
            authority.run_id, name
        ):
            raise RuntimeError("capability_denied")
        arguments = thaw_json(prepared.call.arguments)
        if not isinstance(arguments, Mapping):
            raise TypeError("SDK Tool arguments must be an object")
        provisional = PreparedToolCall.prepare(
            tool_name=name,
            stable_call_id=prepared.call.call_id.value,
            final_params=dict(arguments),
            tool_spec_version=spec.spec_version,
            schema_hash=spec.schema_hash,
            permission_policy_version=spec.permission_policy_version,
            effect_type=authority.dispatch_kinds[name],
        )
        return replace(
            provisional,
            resource_selectors=self._resource_selectors(
                prepared, args_hash=provisional.args_hash
            ),
        )

    async def decide(
        self,
        prepared: PreparedToolEffect,
        *,
        request: AuthorizationRequest | None,
    ) -> AuthorizationResult:
        authority = self._authorities.resolve(prepared.run_id)
        authority.assert_workspace_current()
        call = self._prepared_call(prepared, authority)
        context = authority.execution_context(
            call_id=prepared.call.call_id.value,
            effect_id=prepared.effect_id.value,
        )
        # S5b Task 6 (AC-3⑤ / A5, design-freeze §4 step 7): ``explicit_only``
        # comes from the frozen EffectClass of the Tool.  A confirm-only class
        # (external_send / destructive / payment / credential / privacy /
        # unknown, or manifest-dangerous) is never covered by an existing grant
        # and never auto-granted: Auto mode returns REQUIRE_USER exactly like
        # Manual mode does, and the candidate grant's source is ``user``.
        explicit_only = authority.specs[call.tool_name].confirm_only
        explicit_fences: dict[str, str] = (
            {
                "decision_nonce": _canonical_sha256(
                    {"effect_id": prepared.effect_id.value, "confirm_only": True}
                ),
                "confirm_only_snapshot_ref": authority.prepared_tool_set.scope_id,
                "confirm_only_snapshot_hash": authority.authority_fingerprint,
            }
            if explicit_only
            else {}
        )
        plan = await self._runtime.plan_prepared_call(
            call=call,
            context=context,
            permission_category=authority.permission_categories[call.tool_name],
            task_grant_id=None,
            principal_id=authority.principal_id,
            explicit_only=explicit_only,
            confirmed=False,
            **explicit_fences,
        )
        self._policy_generation = plan.policy_state.generation
        if plan.action != "allow" and explicit_only and plan.policy_state.mode == "auto":
            # 2026-09-07 product decision (user): the product has only two modes,
            # manual and auto, and auto is the default in which NO prompt is ever
            # shown -- confirm-only effect classes are auto-granted exactly like any
            # other Tool. The frozen EffectClass still travels in the audit facts;
            # only the REQUIRE_USER round trip is removed for auto mode.
            plan = await self._runtime.plan_prepared_call(
                call=call,
                context=context,
                permission_category=authority.permission_categories[call.tool_name],
                task_grant_id=None,
                principal_id=authority.principal_id,
                explicit_only=False,
                confirmed=False,
            )
            self._policy_generation = plan.policy_state.generation
            explicit_only = False
        if plan.action != "allow" and (plan.policy_state.mode == "manual" or explicit_only):
            # SDK owns the final decision nonce.  Compute the exact user grant
            # candidate now, but return REQUIRE_USER so it remains prepared
            # until the SDK's durable decision bind activates it.
            plan = await self._runtime.plan_prepared_call(
                call=call,
                context=context,
                permission_category=authority.permission_categories[call.tool_name],
                task_grant_id=None,
                principal_id=authority.principal_id,
                confirmed=True,
                explicit_only=explicit_only,
                # Manual grants are immutable.  Bind their instance identity
                # to the stable SDK effect so replay is idempotent while two
                # different Tool effects in one Run cannot collide merely
                # because they request the same resource category.
                decision_id=prepared.effect_id.value,
                **explicit_fences,
            )
            self._policy_generation = plan.policy_state.generation
            if plan.action != "allow" or plan.committed_task_grant is None:
                return AuthorizationResult(
                    AuthorizationDecision.DENY,
                    reason_code=f"product_policy:{plan.action}",
                )
            grant = plan.committed_task_grant
            self._facts[(authority.run_id, prepared.effect_id.value)] = (
                _PreparedAuthorizationFacts(authority, call, plan, grant)
            )
            preflight_outcome = await self._stage_skill_preflight(
                prepared=prepared, context=context, call=call, authority=authority
            )
            if isinstance(preflight_outcome, AuthorizationResult):
                return preflight_outcome
            preflight = preflight_outcome
            nonce = request.nonce if request is not None else _canonical_sha256(
                {
                    "effect_id": prepared.effect_id.value,
                    "grant_fingerprint": grant.fingerprint,
                }
            )
            return AuthorizationResult(
                AuthorizationDecision.REQUIRE_USER,
                reason_code="product_policy_user_confirmation",
                public_message=f"Allow {call.tool_name} for this exact request?",
                request=AuthorizationRequest(
                    f"Allow {call.tool_name} for this exact request?",
                    nonce,
                    expires_at=float(self._clock()) + 300.0,
                    metadata={
                        "tool_name": call.tool_name,
                        "scope_hash": authority.scope_hash,
                        "grant_source": grant.source,
                        "policy_generation": grant.policy_generation,
                        **(
                            {}
                            if preflight is None
                            else {
                                "skill_install_artifact_ref": preflight.artifact_ref,
                                "skill_install_intent_id": preflight.intent_id,
                                "skill_install_content_digest": preflight.content_digest,
                                "skill_install_member_digest": preflight.member_digest,
                                "skill_install_member_summary": list(preflight.member_summary),
                                "skill_install_permission_summary": list(preflight.permission_summary),
                                "skill_install_expires_at": preflight.expires_at,
                            }
                        ),
                    },
                ),
            )
        if plan.action != "allow" or plan.committed_task_grant is None:
            decision = (
                AuthorizationDecision.DENY
                if plan.action == "deny"
                else AuthorizationDecision.REQUIRE_USER
            )
            if decision is AuthorizationDecision.REQUIRE_USER:
                return AuthorizationResult(
                    decision,
                    reason_code=f"product_policy:{plan.action}",
                    request=AuthorizationRequest(
                        f"Authorization required for {call.tool_name}.",
                        _canonical_sha256(
                            {"effect_id": prepared.effect_id.value, "action": plan.action}
                        ),
                    ),
                )
            return AuthorizationResult(decision, reason_code=f"product_policy:{plan.action}")
        grant = plan.committed_task_grant
        self._facts[(authority.run_id, prepared.effect_id.value)] = (
            _PreparedAuthorizationFacts(authority, call, plan, grant)
        )
        preflight_outcome = await self._stage_skill_preflight(
            prepared=prepared, context=context, call=call, authority=authority
        )
        if isinstance(preflight_outcome, AuthorizationResult):
            return preflight_outcome
        if isinstance(preflight_outcome, SkillInstallPreflightReady):
            preflight = preflight_outcome
            self._auto_skill_approvals[(authority.run_id, prepared.effect_id.value)] = {
                "skill_install_intent_id": preflight.intent_id,
                "skill_install_content_digest": preflight.content_digest,
                "skill_install_member_digest": preflight.member_digest,
                "skill_install_expires_at": preflight.expires_at,
            }
            return AuthorizationResult(
                AuthorizationDecision.ALLOW,
                receipt_ref=f"product-policy:auto:{grant.policy_generation}:{grant.fingerprint}",
            )
        return AuthorizationResult(
            AuthorizationDecision.ALLOW,
            receipt_ref=(
                f"product-policy:{grant.source}:{grant.policy_generation}:"
                f"{grant.fingerprint}"
            ),
        )

    def facts_for(self, prepared: PreparedToolEffect) -> _PreparedAuthorizationFacts:
        facts = self._facts.get((prepared.run_id.value, prepared.effect_id.value))
        if facts is None:
            raise RuntimeError("SDK prepared authorization facts are unavailable")
        if facts.authority.run_id != prepared.run_id.value:
            raise RuntimeError("SDK prepared authorization facts crossed Run scope")
        return facts

    def restore_facts(
        self,
        prepared: PreparedToolEffect,
        *,
        authority: SdkRunToolAuthorityV1,
        call: PreparedToolCall,
        plan: PreparedAuthorizationPlan,
        grant: TaskGrant,
    ) -> None:
        """Restore already-verified frozen authorization facts without policy IO."""

        if authority.run_id != prepared.run_id.value:
            raise RuntimeError("restored authority crossed Run scope")
        if call.stable_call_id != prepared.call.call_id.value:
            raise RuntimeError("restored call identity differs")
        if call.tool_name != prepared.call.name:
            raise RuntimeError("restored Tool name differs")
        arguments = thaw_json(prepared.call.arguments)
        if not isinstance(arguments, Mapping):
            raise TypeError("SDK Tool arguments must be an object")
        expected = self._prepared_call(prepared, authority)
        if expected != call:
            raise RuntimeError("restored prepared call differs from frozen authority")
        if plan.committed_task_grant != grant:
            raise RuntimeError("restored plan grant differs")
        if grant.principal_id != authority.principal_id:
            raise RuntimeError("restored grant principal differs")
        self._facts[(authority.run_id, prepared.effect_id.value)] = (
            _PreparedAuthorizationFacts(authority, call, plan, grant)
        )

    def grant_factory(
        self,
        prepared: PreparedToolEffect,
        _result: AuthorizationResult,
    ) -> TaskGrant:
        return self.facts_for(prepared).grant

    def identity_factory(
        self,
        prepared: PreparedToolEffect,
        request: AuthorizationRequest | None,
    ) -> AuthorizationSagaIdentity:
        facts = self.facts_for(prepared)
        authority = facts.authority
        grant = facts.grant
        identity_payload = {
            "run_id": authority.run_id,
            "effect_id": prepared.effect_id.value,
            "call_id": prepared.call.call_id.value,
            "args_hash": facts.call.args_hash,
            "capability_hash": authority.capability_hash,
            "schema_hash": facts.call.schema_hash,
            "scope_hash": authority.scope_hash,
            "grant_fingerprint": grant.fingerprint,
        }
        return AuthorizationSagaIdentity(
            authorization_id=f"sdk-auth:{_canonical_sha256(identity_payload)}",
            principal_id=authority.principal_id,
            session_id=authority.session_id,
            root_run_id=authority.root_run_id,
            run_id=authority.run_id,
            call_id=prepared.call.call_id.value,
            effect_id=prepared.effect_id.value,
            tool_name=prepared.call.name,
            arguments=dict(thaw_json(prepared.call.arguments)),
            capability_hash=authority.capability_hash,
            schema_hash=facts.call.schema_hash,
            scope_hash=authority.scope_hash,
            grant_id=grant.task_grant_id,
            grant_version=grant.version,
            grant_fingerprint=grant.fingerprint,
            policy_generation=grant.policy_generation,
            decision_nonce=request.nonce if request is not None else None,
            decision_version=0,
            run_lease_epoch=authority.task_work_context.binding_version,
            execution_lease_epoch=authority.catalog_generation,
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
    "ProjectSkillInstallPreflightPort",
    "SdkCapabilityBridgeAdapter",
    "SdkPreparedAuthorizationPolicy",
    "SdkRunToolAuthorityRegistry",
    "SdkRunToolAuthorityV1",
    "SdkRuntimeCapabilityBridgeAdapter",
    "SdkToolAuthorityMigrationUnavailable",
    "SkillInstallPreflightOutcome",
    "SkillInstallPreflightReady",
    "SkillInstallPreflightRejected",
    "confirm_only_tool_names",
    "frozen_tool_effect",
    "is_confirm_only_effect",
)
