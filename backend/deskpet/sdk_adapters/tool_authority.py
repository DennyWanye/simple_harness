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
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Iterable, Mapping, Sequence

from simple_harness import thaw_json
from simple_harness.tools import (
    AuthorizationDecision,
    AuthorizationRequest,
    AuthorizationResult,
    PreparedToolEffect,
)

from deskpet.permissions.runtime import (
    PreparedAuthorizationPlan,
    PreparedAuthorizationRuntime,
)
from deskpet.product_state.authorization_saga import AuthorizationSagaIdentity
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
from deskpet.types.task_work_context import TaskWorkContext
from deskpet.workflows.effects import PreparedToolCall


SDK_TOOL_AUTHORITY_RECORD_KIND = "deskpet.sdk-tool-authority"
SDK_TOOL_AUTHORITY_RECORD_VERSION = 1
SDK_FULL_CATALOG_DISCLOSURE_POLICY = "full-direct-v1"
SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY = "explicit-deferred-v1"
SDK_PERMISSION_POLICY_VERSION = "sdk-product-policy-v1"


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
    dangerous: bool = False

    def env_satisfied(self) -> bool:
        return True

    def is_visible(self, _eligibility: ToolEligibilityContext) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class SdkRunToolAuthorityV1:
    run_id: str
    session_id: str
    request_id: str
    root_run_id: str
    task_work_context: TaskWorkContext
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
        return ToolExecutionContext(
            scope_id=self.prepared_tool_set.scope_id,
            session_id=self.session_id,
            request_id=self.request_id,
            root_run_id=self.root_run_id,
            turn_id=turn_id,
            workspace=self.task_work_context.workspace_root,
            write_scope_root=self.task_work_context.workspace_root,
            capability_hash=self.capability_hash,
            scope_hash=self.scope_hash,
            run_id=self.run_id,
            call_id=call_id,
            effect_id=effect_id,
            owner_key=self.principal_id,
            binding_epoch=self.task_work_context.binding_version,
            capability_snapshot_ref=self.catalog_fingerprint,
        )

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
            "principal_id": self.principal_id,
            "catalog_generation": self.catalog_generation,
            "catalog_fingerprint": self.catalog_fingerprint,
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
                    "dangerous": spec.dangerous,
                }
                for name, spec in self.specs.items()
            ],
        }


class SdkRunToolAuthorityRegistry:
    """Immutable per-Run Tool authority with WAITING-safe scope leases."""

    def __init__(
        self,
        *,
        scope_store: ToolCapabilityScopeStore | None = None,
    ) -> None:
        self.scope_store = scope_store or ToolCapabilityScopeStore()
        self._records: dict[str, SdkRunToolAuthorityV1] = {}
        self._terminal_listeners: list[
            Callable[[SdkRunToolAuthorityV1], None]
        ] = []

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
        task_scope_id: str,
        workspace_root: str | None,
        catalog: Mapping[str, Any],
        inventory: Sequence[object],
        principal_id: str = "sdk-runtime",
        deferred_names: Iterable[str] = (),
        disclosure_policy: str | None = None,
        binding_version: int = 1,
    ) -> SdkRunToolAuthorityV1:
        run_id = _required(run_id, "run_id")
        session_id = _required(session_id, "session_id")
        request_id = _required(request_id, "request_id")
        root_run_id = _required(root_run_id, "root_run_id")
        task_scope_id = _required(task_scope_id, "task_scope_id")
        principal_id = _required(principal_id, "principal_id")
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
        if len(names) != len(set(names)) or set(names) != set(inventory_by_name):
            raise ValueError("SDK catalog and product inventory differ")

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
                dangerous=dangerous,
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
        work_context = TaskWorkContext(
            session_id=session_id,
            root_run_id=root_run_id,
            task_scope_id=task_scope_id,
            workspace_root=workspace_root,
            workspace_source="existing" if workspace_root else "none",
            binding_version=binding_version,
        )
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
                        "dangerous": frozen_specs[name].dangerous,
                    }
                    for name in names
                ],
                "principal_id": principal_id,
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
        return record

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
        if int(run_start_record.get("schema_version", -1)) != 1:
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
        catalog = {
            "generation": generation,
            "content_fingerprint": fingerprint,
            "specs": specs,
            "schema_fingerprints": {
                item["name"]: canonical_hash(item["input_schema"])
                for item in specs
            },
        }
        raw_inventory = run_start_record.get("inventory")
        if not isinstance(raw_inventory, list) or not raw_inventory:
            raise ValueError("sdk_tool_authority_inventory_missing")
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
            task_scope_id=_required(
                run_start_record.get("task_scope_id"), "task_scope_id"
            ),
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
        )
        if (
            restored.authority_fingerprint != expected_authority_fingerprint
            or restored.capability_hash
            != _required(run_start_record.get("capability_hash"), "capability_hash")
            or restored.scope_hash
            != _required(run_start_record.get("scope_hash"), "scope_hash")
        ):
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
        if str(terminal_state).lower() not in {"completed", "failed", "cancelled"}:
            raise ValueError("terminal_state must be completed, failed, or cancelled")
        record = self.resolve(run_id)
        self._records.pop(record.run_id, None)
        self.scope_store.unpin(
            record.prepared_tool_set.scope_id,
            session_id=record.session_id,
            request_id=record.request_id,
        )
        self.scope_store.purge(record.prepared_tool_set.scope_id)
        for listener in tuple(self._terminal_listeners):
            listener(record)
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


@dataclass(frozen=True, slots=True)
class _PreparedAuthorizationFacts:
    authority: SdkRunToolAuthorityV1
    call: PreparedToolCall
    plan: PreparedAuthorizationPlan
    grant: TaskGrant


class SdkPreparedAuthorizationPolicy:
    """Map SDK effects onto the product prepared authorization runtime."""

    def __init__(
        self,
        runtime: PreparedAuthorizationRuntime,
        authorities: SdkRunToolAuthorityRegistry,
        *,
        clock: Callable[[], float] = time.time,
        initial_policy_generation: int | None = None,
    ) -> None:
        self._runtime = runtime
        self._authorities = authorities
        self._clock = clock
        self._facts: dict[tuple[str, str], _PreparedAuthorizationFacts] = {}
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
        if spec is None or not authority.prepared_tool_set.has_direct(name):
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
        call = self._prepared_call(prepared, authority)
        context = authority.execution_context(
            call_id=prepared.call.call_id.value,
            effect_id=prepared.effect_id.value,
        )
        plan = await self._runtime.plan_prepared_call(
            call=call,
            context=context,
            permission_category=authority.permission_categories[call.tool_name],
            task_grant_id=None,
            principal_id=authority.principal_id,
        )
        self._policy_generation = plan.policy_state.generation
        if plan.action != "allow" and plan.policy_state.mode == "manual":
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
    "SDK_EXPLICIT_DEFERRED_DISCLOSURE_POLICY",
    "SDK_FULL_CATALOG_DISCLOSURE_POLICY",
    "SDK_PERMISSION_POLICY_VERSION",
    "SDK_TOOL_AUTHORITY_RECORD_KIND",
    "SDK_TOOL_AUTHORITY_RECORD_VERSION",
    "SdkCapabilityBridgeAdapter",
    "SdkPreparedAuthorizationPolicy",
    "SdkRunToolAuthorityRegistry",
    "SdkRunToolAuthorityV1",
)
