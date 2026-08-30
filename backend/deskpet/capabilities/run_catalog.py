# SPDX-License-Identifier: BUSL-1.1
"""Durable Run-catalog capture and Harness start/terminal integration."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from deskpet.execution.contracts import fingerprint_json
from deskpet.harness.contracts import HostExtensionRefV1
from deskpet.tools.capabilities import PreparedToolSet
from deskpet.tools.prepared_snapshot import (
    dump_prepared_tool_set,
    intersect_prepared_skill_tools,
    load_prepared_tool_set,
)
from deskpet.tools.registry import tool_spec_fingerprint

from .contracts import (
    CapabilityBinding,
    CapabilityCatalogEntry,
    CapabilityCatalogSnapshot,
    ProcessCatalogStamp,
    RunCatalogContentStamp,
    RunCatalogEntryIdentity,
)


class _FrozenCaptureDict(dict[str, Any]):
    """JSON-serializable dict whose captured values cannot be rewritten."""

    @staticmethod
    def _immutable(*_args: Any, **_kwargs: Any) -> None:
        raise TypeError("prepared ToolSet capture is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable

    def __deepcopy__(self, _memo: dict[int, Any]) -> "_FrozenCaptureDict":
        return self


class _FrozenCaptureList(list[Any]):
    """JSON-serializable list whose captured ordering cannot be rewritten."""

    @staticmethod
    def _immutable(*_args: Any, **_kwargs: Any) -> None:
        raise TypeError("prepared ToolSet capture is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __iadd__ = _immutable
    __imul__ = _immutable
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable

    def __deepcopy__(self, _memo: dict[int, Any]) -> "_FrozenCaptureList":
        return self


def _freeze_capture(value: Any) -> Any:
    """Defensively freeze the one publish-lock capture shared by the Run."""

    if isinstance(value, Mapping):
        return _FrozenCaptureDict(
            {
                str(key): _freeze_capture(item)
                for key, item in copy.deepcopy(dict(value)).items()
            }
        )
    if isinstance(value, (list, tuple)):
        return _FrozenCaptureList(
            _freeze_capture(item) for item in copy.deepcopy(value)
        )
    return copy.deepcopy(value)


def _selected_binding(
    snapshot: CapabilityCatalogSnapshot,
    bindings: Sequence[CapabilityBinding],
) -> CapabilityBinding:
    by_key = {key: index for index, key in enumerate(snapshot.scope.binding_keys())}
    candidates = [
        item
        for item in bindings
        if (item.scope, item.scope_key) in by_key and item.active
    ]
    if not candidates:
        raise RuntimeError("run_catalog_selected_binding_missing")
    return min(
        candidates,
        key=lambda item: (
            by_key[(item.scope, item.scope_key)],
            -item.generation,
            item.binding_id,
        ),
    )


def _result_field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


@dataclass(frozen=True, slots=True)
class PreparedToolSetCapture:
    external_ref: str
    envelope: Mapping[str, Any]
    fingerprint: str
    tool_spec_fingerprints: tuple[str, ...]


def _capture_prepared_tool_set(
    prepared: PreparedToolSet,
    *,
    registry: Any,
    expected_external_ref: str,
    expected_registry_revision: int,
) -> PreparedToolSetCapture:
    """Revalidate every prepared tool against one exact Registry generation."""

    catalog = registry.catalog_snapshot()
    if (
        int(catalog.revision) != int(expected_registry_revision)
        or int(prepared.registry_revision) != int(expected_registry_revision)
    ):
        raise RuntimeError("prepared_tool_set_registry_revision_stale")
    specs_by_name = {str(spec.name): spec for spec in catalog.specs}
    refs = tuple(
        item.ref
        for item in (*prepared.direct, *prepared.activated)
    ) + tuple(prepared.deferred)
    # Report the complete frozen-selection gap in one failure.  Production
    # composition can otherwise expose several dynamically rebound builtin
    # tools and force operators to discover missing build identities one turn
    # at a time.
    missing_build_identities: list[str] = []
    preflight_seen: set[str] = set()
    for ref in refs:
        if ref.name in preflight_seen:
            continue
        preflight_seen.add(ref.name)
        spec = specs_by_name.get(ref.name)
        if spec is None:
            continue
        actual = (
            str(spec.name),
            str(spec.source),
            str(spec.toolset),
            str(spec.schema_hash),
            str(spec.spec_version),
            str(spec.permission_policy_version),
            str(spec.permission_category),
            bool(spec.dangerous),
        )
        expected = (
            ref.name,
            ref.source,
            ref.toolset,
            ref.schema_hash,
            ref.spec_version,
            ref.permission_policy_version,
            ref.permission_category,
            ref.dangerous,
        )
        if (
            actual == expected
            and getattr(spec, "execution_build_identity", None) is None
        ):
            missing_build_identities.append(ref.name)
    if missing_build_identities:
        raise RuntimeError(
            "prepared_tool_build_identity_missing:"
            + ",".join(sorted(missing_build_identities))
        )
    exact: list[dict[str, Any]] = []
    fingerprints: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        if ref.name in seen:
            continue
        seen.add(ref.name)
        # Keep the exact ToolSpec and Registry revision from one immutable
        # catalog snapshot. A hot Registry/MCP replacement between
        # ``catalog_snapshot()`` and a separate live ``get()`` must not create
        # a mixed-generation Run capture.
        spec = specs_by_name.get(ref.name)
        if spec is None:
            raise RuntimeError(f"prepared_tool_missing:{ref.name}")
        actual = (
            str(spec.name),
            str(spec.source),
            str(spec.toolset),
            str(spec.schema_hash),
            str(spec.spec_version),
            str(spec.permission_policy_version),
            str(spec.permission_category),
            bool(spec.dangerous),
        )
        expected = (
            ref.name,
            ref.source,
            ref.toolset,
            ref.schema_hash,
            ref.spec_version,
            ref.permission_policy_version,
            ref.permission_category,
            ref.dangerous,
        )
        if actual != expected:
            raise RuntimeError(f"prepared_tool_identity_stale:{ref.name}")
        build = getattr(spec, "execution_build_identity", None)
        if build is None:
            raise RuntimeError(f"prepared_tool_build_identity_missing:{ref.name}")
        spec_fingerprint = tool_spec_fingerprint(spec)
        fingerprints.append(spec_fingerprint)
        exact.append(
            {
                "name": ref.name,
                "stable_handler_id": str(spec.stable_handler_id),
                "tool_spec_fingerprint": spec_fingerprint,
                "schema_hash": str(spec.schema_hash),
                "execution_build_identity": build.fingerprint_payload(),
                "dispatch_adapter_id": str(spec.dispatch_adapter_id),
                "dispatch_adapter_version": str(spec.dispatch_adapter_version),
                "dispatch_adapter_fingerprint": str(
                    spec.dispatch_adapter_fingerprint
                ),
                "effect_policy": (
                    None
                    if spec.effect_policy is None
                    else {
                        "policy_id": str(spec.effect_policy.policy_id),
                        "version": str(spec.effect_policy.version),
                        "kind": str(spec.effect_policy.kind),
                        "max_attempts": int(spec.effect_policy.max_attempts),
                        "reusable_across_branches": bool(
                            spec.effect_policy.reusable_across_branches
                        ),
                    }
                ),
                "idempotency": str(spec.idempotency),
            }
        )
    envelope = {
        "schema_version": 1,
        "external_ref": expected_external_ref,
        "prepared": dump_prepared_tool_set(prepared),
        "exact_tools": sorted(exact, key=lambda item: item["name"]),
    }
    return PreparedToolSetCapture(
        external_ref=expected_external_ref,
        envelope=envelope,
        fingerprint=fingerprint_json(envelope),
        tool_spec_fingerprints=tuple(sorted(fingerprints)),
    )


class SnapshotProjectionState(Protocol):
    intent_id: str
    intent_hash: str
    intent_status: str
    start_fingerprint: str | None
    run_catalog_content_stamp: str
    process_catalog_stamp: str
    projection_status: str
    projection_receipt_hash: str


class SnapshotLeaseReadyGate:
    """Process-local gate backed by a durable snapshot projection receipt."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pending: dict[str, PendingProcessPinToken] = {}
        self._ready: dict[str, tuple[str, str, str]] = {}
        self._run_to_intent: dict[str, str] = {}

    async def register_pending(
        self,
        *,
        intent_id: str,
        run_id: str,
        pin: "PendingProcessPinToken",
    ) -> None:
        async with self._lock:
            current = self._pending.get(intent_id)
            if current is not None and current.token_hash != pin.token_hash:
                raise RuntimeError("snapshot_ready_gate_pending_conflict")
            self._pending[intent_id] = pin
            self._run_to_intent[run_id] = intent_id

    async def unregister_pending(self, intent_id: str, run_id: str) -> None:
        async with self._lock:
            self._pending.pop(intent_id, None)
            self._ready.pop(intent_id, None)
            if self._run_to_intent.get(run_id) == intent_id:
                self._run_to_intent.pop(run_id, None)

    async def activate(
        self,
        *,
        intent_id: str,
        intent_hash: str,
        process_catalog_stamp: str,
        projection_receipt_hash: str,
    ) -> None:
        async with self._lock:
            pin = self._pending.get(intent_id)
            if pin is None or not pin.installed or not pin.committed:
                raise RuntimeError("snapshot_process_pin_not_ready")
            self._ready[intent_id] = (
                intent_hash,
                process_catalog_stamp,
                projection_receipt_hash,
            )

    async def require_ready(
        self,
        *,
        intent_id: str,
        intent_hash: str | None = None,
        process_catalog_stamp: str | None = None,
    ) -> None:
        async with self._lock:
            ready = self._ready.get(intent_id)
            if ready is None:
                raise RuntimeError("snapshot_lease_not_ready")
            if intent_hash is not None and ready[0] != intent_hash:
                raise RuntimeError("snapshot_lease_ready_identity_stale")
            if (
                process_catalog_stamp is not None
                and ready[1] != process_catalog_stamp
            ):
                raise RuntimeError("snapshot_lease_process_stamp_stale")

    async def require_run_ready(self, run_id: str) -> None:
        async with self._lock:
            intent_id = self._run_to_intent.get(run_id)
            if intent_id is None or intent_id not in self._ready:
                raise RuntimeError("snapshot_lease_not_ready")

    async def retire(self, *, intent_id: str, run_id: str) -> None:
        await self.unregister_pending(intent_id, run_id)


@dataclass(slots=True)
class PendingProcessPinToken:
    hub: Any
    snapshot_ref: str
    run_id: str
    lease_intent_id: str
    tool_spec_fingerprints: tuple[str, ...]
    installed: bool = False
    committed: bool = False

    @property
    def token_hash(self) -> str:
        return fingerprint_json(
            {
                "domain": "pending-snapshot-pin-v1",
                "snapshot_ref": self.snapshot_ref,
                "run_id": self.run_id,
                "lease_intent_id": self.lease_intent_id,
                "tool_spec_fingerprints": list(self.tool_spec_fingerprints),
            }
        )

    async def install(self) -> None:
        if self.installed:
            return
        await self.hub.mirror_snapshot_fingerprints(
            snapshot_ref=self.snapshot_ref,
            run_id=self.run_id,
            tool_spec_fingerprints=self.tool_spec_fingerprints,
        )
        self.installed = True

    def commit(self) -> None:
        if not self.installed:
            raise RuntimeError("cannot commit an uninstalled snapshot pin")
        self.committed = True

    async def rollback(self) -> None:
        if self.installed:
            await self.hub.mirror_snapshot_release(
                snapshot_ref=self.snapshot_ref,
                run_id=self.run_id,
            )
        self.installed = False
        self.committed = False


@dataclass(frozen=True, slots=True)
class PreparedRunCatalogLease:
    snapshot_ref: str
    run_catalog_content_stamp: str
    process_catalog_stamp: str
    lease_intent_id: str
    lease_intent_hash: str
    prepared_tool_set_fingerprint: str
    run_id: str
    root_run_id: str
    entry_set_hash: str
    expected_entry_count: int
    prepared_tool_set_capture_hash: str
    prepared_tool_set_envelope: Mapping[str, Any]
    lease_entries: tuple[Mapping[str, Any], ...]
    projection_receipt_id: str
    projection_receipt_hash: str
    process_instance_id: str
    _store: Any
    _hub: Any
    _ready_gate: SnapshotLeaseReadyGate
    _pin: PendingProcessPinToken

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "prepared_tool_set_envelope",
            _freeze_capture(self.prepared_tool_set_envelope),
        )
        object.__setattr__(
            self,
            "lease_entries",
            tuple(_freeze_capture(item) for item in self.lease_entries),
        )

    @property
    def descriptor(self) -> HostExtensionRefV1:
        return HostExtensionRefV1(
            kind="deskpet.capabilities.run_catalog_lease.v1",
            ref=self.lease_intent_id,
            content_hash=fingerprint_json(
                {
                    "snapshot_ref": self.snapshot_ref,
                    "run_catalog_content_stamp": self.run_catalog_content_stamp,
                    "process_catalog_stamp": self.process_catalog_stamp,
                    "lease_intent_id": self.lease_intent_id,
                    "lease_intent_hash": self.lease_intent_hash,
                    "prepared_tool_set_fingerprint": (
                        self.prepared_tool_set_fingerprint
                    ),
                    "run_id": self.run_id,
                    "root_run_id": self.root_run_id,
                    "entry_set_hash": self.entry_set_hash,
                    "expected_entry_count": self.expected_entry_count,
                    "prepared_tool_set_capture_hash": (
                        self.prepared_tool_set_capture_hash
                    ),
                    "projection_receipt_id": self.projection_receipt_id,
                    "projection_receipt_hash": self.projection_receipt_hash,
                    "process_instance_id": self.process_instance_id,
                }
            ),
        )

    @property
    def start_commit_extension(self) -> "_BindRunCatalogLease":
        return _BindRunCatalogLease(self)

    @property
    def after_start_handshake(self) -> "_ReadyRunCatalogLease":
        return _ReadyRunCatalogLease(self)

    @property
    def terminal_commit_extension(self) -> "_ReleaseRunCatalogLease":
        return _ReleaseRunCatalogLease(self)

    @property
    def after_terminal_cleanup(self) -> "_UnpinRunCatalogLease":
        return _UnpinRunCatalogLease(self)

    async def release_prepared(self) -> None:
        """Release a known-not-started capture and its process-local pin."""

        owner_ref = f"prepared-run-release:{self.run_id}"
        owner_hash = fingerprint_json(
            {
                "owner_ref": owner_ref,
                "lease_intent_id": self.lease_intent_id,
                "lease_intent_hash": self.lease_intent_hash,
                "reason": "known_start_not_committed",
            }
        )
        async with self._store.write_transaction() as db:
            tx = self._store.bind(db)
            receipt = await tx.release_snapshot_lease_intent_in_tx(
                self.lease_intent_id,
                intent_hash=self.lease_intent_hash,
                release_reason="prepared_orphan",
                owner_terminal_or_transition_ref=owner_ref,
                owner_terminal_or_transition_hash=owner_hash,
            )
            if not str(_result_field(receipt, "release_receipt_hash") or ""):
                raise RuntimeError("run_catalog_release_receipt_missing")
        await self._pin.rollback()
        await self._ready_gate.retire(
            intent_id=self.lease_intent_id,
            run_id=self.run_id,
        )

    async def require_ready(self) -> None:
        """Fence consumers to this exact bound process projection."""

        await self._ready_gate.require_ready(
            intent_id=self.lease_intent_id,
            intent_hash=self.lease_intent_hash,
            process_catalog_stamp=self.process_catalog_stamp,
        )

    def project_pack_entries(
        self,
        *,
        owner_key: str,
        project_scope_key: str | None,
    ) -> tuple[Mapping[str, Any], ...]:
        """Return only this frozen Run's exact Project instruction packs.

        The lease, rather than a fresh Store query, is the visibility
        authority.  This prevents a publish racing Run preparation from
        producing SDK discovery records from a different binding generation.
        Projectless Runs have no Project projection by construction.
        """

        expected_owner = str(owner_key).strip()
        if not expected_owner:
            raise ValueError("project Skill owner_key is required")
        expected_scope = str(project_scope_key or "").strip()
        if not expected_scope:
            return ()
        result: list[Mapping[str, Any]] = []
        for entry in self.lease_entries:
            if str(entry.get("entry_kind") or "") != "pack":
                continue
            selected = entry.get("selected_binding")
            if not isinstance(selected, Mapping):
                raise RuntimeError("run_catalog_project_binding_missing")
            if str(selected.get("scope") or "") != "project":
                continue
            if (
                str(selected.get("owner_key") or "") != expected_owner
                or str(selected.get("scope_key") or "") != expected_scope
            ):
                continue
            result.append(entry)
        return tuple(
            sorted(
                result,
                key=lambda item: (
                    str(item.get("pack_id") or ""),
                    str(item.get("version") or ""),
                    str(item.get("manifest_hash") or ""),
                ),
            )
        )

    def user_global_pack_entries(
        self,
        *,
        owner_key: str,
        user_scope_key: str,
    ) -> tuple[Mapping[str, Any], ...]:
        """Return user-global packs captured by this immutable Run lease.

        ``owner_key`` and ``user_scope_key`` are deliberately independent.
        The former is the validated binding owner; the latter is the
        domain-separated global Capability scope.  No default/profile guess is
        accepted here.
        """

        expected_owner = str(owner_key).strip()
        expected_scope = str(user_scope_key).strip()
        if not expected_owner or not expected_scope.startswith("user:v2:"):
            raise ValueError("validated user-global Skill authority is required")
        result: list[Mapping[str, Any]] = []
        for entry in self.lease_entries:
            if str(entry.get("entry_kind") or "") != "pack":
                continue
            selected = entry.get("selected_binding")
            if not isinstance(selected, Mapping):
                raise RuntimeError("run_catalog_global_binding_missing")
            if str(selected.get("scope") or "") != "user":
                continue
            if (
                str(selected.get("owner_key") or "") == expected_owner
                and str(selected.get("scope_key") or "") == expected_scope
            ):
                result.append(entry)
        return tuple(
            sorted(
                result,
                key=lambda item: (
                    str(item.get("pack_id") or ""),
                    str(item.get("version") or ""),
                    str(item.get("manifest_hash") or ""),
                ),
            )
        )


def _tx_db(transaction: Any) -> Any:
    db = getattr(transaction, "_db", None)
    if db is None:
        raise TypeError("run catalog extension requires a bound SQLite transaction")
    return db


@dataclass(frozen=True, slots=True)
class _BindRunCatalogLease:
    lease: PreparedRunCatalogLease

    @property
    def descriptor(self) -> HostExtensionRefV1:
        return HostExtensionRefV1(
            kind="deskpet.capabilities.snapshot_lease_bind.v1",
            ref=self.lease.lease_intent_id,
            content_hash=self.lease.lease_intent_hash,
        )

    async def apply_start_commit(
        self,
        transaction: Any,
        *,
        spec: Any,
        start_snapshot: Any,
    ) -> HostExtensionRefV1:
        if (
            spec.run_id != self.lease.run_id
            or start_snapshot.run_id != self.lease.run_id
            or start_snapshot.capability_lease_intent_ref
            != self.lease.lease_intent_id
            or start_snapshot.capability_lease_intent_hash
            != self.lease.lease_intent_hash
        ):
            raise RuntimeError("run_catalog_start_fence_mismatch")
        owner_ref = f"execution-run-start:{self.lease.run_id}"
        db = _tx_db(transaction)
        receipt = await self.lease._store.bind(
            db
        ).adopt_snapshot_lease_intent_in_tx(
            self.lease.lease_intent_id,
            intent_hash=self.lease.lease_intent_hash,
            owner_record_ref=owner_ref,
            owner_record_hash=start_snapshot.start_fingerprint,
            start_fingerprint=start_snapshot.start_fingerprint,
        )
        if (
            str(
                _result_field(receipt, "lease_intent_id")
                or _result_field(receipt, "intent_id")
                or ""
            )
            != self.lease.lease_intent_id
            or str(
                _result_field(receipt, "lease_intent_hash")
                or _result_field(receipt, "intent_hash")
                or ""
            )
            != self.lease.lease_intent_hash
            or str(_result_field(receipt, "start_fingerprint") or "")
            != start_snapshot.start_fingerprint
        ):
            raise RuntimeError("run_catalog_lease_bind_receipt_mismatch")
        return self.descriptor


@dataclass(frozen=True, slots=True)
class _ReadyRunCatalogLease:
    lease: PreparedRunCatalogLease

    @property
    def descriptor(self) -> HostExtensionRefV1:
        return HostExtensionRefV1(
            kind="deskpet.capabilities.snapshot_lease_ready.v1",
            ref=self.lease.lease_intent_id,
            content_hash=self.lease.process_catalog_stamp,
        )

    async def activate_after_start(
        self,
        record: Any,
        *,
        start_snapshot: Any,
        extension_receipts: Sequence[HostExtensionRefV1],
    ) -> bool:
        if str(
            getattr(
                record,
                "run_id",
                getattr(getattr(record, "ref", None), "run_id", ""),
            )
        ) != self.lease.run_id:
            return False
        if not any(
            item.kind == "deskpet.capabilities.snapshot_lease_bind.v1"
            and item.ref == self.lease.lease_intent_id
            and item.content_hash == self.lease.lease_intent_hash
            for item in extension_receipts
        ):
            return False
        projection = await self.lease._store.activate_snapshot_projection_ready(
            self.lease.lease_intent_id,
            intent_hash=self.lease.lease_intent_hash,
            start_fingerprint=start_snapshot.start_fingerprint,
            process_instance_id=self.lease.process_instance_id,
            process_catalog_stamp=self.lease.process_catalog_stamp,
            pin_token_hash=self.lease._pin.token_hash,
        )
        projection_hash = str(
            _result_field(projection, "projection_receipt_hash")
            or _result_field(projection, "receipt_hash")
            or ""
        )
        if not projection_hash:
            raise RuntimeError("snapshot_projection_receipt_missing")
        await self.lease._ready_gate.activate(
            intent_id=self.lease.lease_intent_id,
            intent_hash=self.lease.lease_intent_hash,
            process_catalog_stamp=self.lease.process_catalog_stamp,
            projection_receipt_hash=projection_hash,
        )
        return True


@dataclass(frozen=True, slots=True)
class _ReleaseRunCatalogLease:
    lease: PreparedRunCatalogLease

    @property
    def descriptor(self) -> HostExtensionRefV1:
        return HostExtensionRefV1(
            kind="deskpet.capabilities.snapshot_lease_release.v1",
            ref=self.lease.lease_intent_id,
            content_hash=self.lease.lease_intent_hash,
        )

    async def apply_terminal_commit(
        self,
        transaction: Any,
        *,
        record: Any,
        terminal_event: Any,
    ) -> HostExtensionRefV1:
        if str(
            getattr(
                record,
                "run_id",
                getattr(getattr(record, "ref", None), "run_id", ""),
            )
        ) != self.lease.run_id:
            raise RuntimeError("run_catalog_terminal_fence_mismatch")
        db = _tx_db(transaction)
        owner_ref = f"execution-run-terminal:{self.lease.run_id}"
        owner_hash = fingerprint_json(
            {
                "owner_ref": owner_ref,
                "record_version": int(getattr(record, "version", 0)),
                "terminal_event": (
                    terminal_event.to_dict()
                    if hasattr(terminal_event, "to_dict")
                    else str(terminal_event)
                ),
            }
        )
        receipt = await self.lease._store.bind(
            db
        ).release_snapshot_lease_intent_in_tx(
            self.lease.lease_intent_id,
            intent_hash=self.lease.lease_intent_hash,
            release_reason="terminal",
            owner_terminal_or_transition_ref=owner_ref,
            owner_terminal_or_transition_hash=owner_hash,
        )
        receipt_hash = str(
            _result_field(receipt, "release_receipt_hash")
            or _result_field(receipt, "receipt_hash")
            or ""
        )
        receipt_id = str(
            _result_field(receipt, "release_receipt_id")
            or _result_field(receipt, "receipt_id")
            or self.lease.lease_intent_id
        )
        if not receipt_hash:
            raise RuntimeError("run_catalog_release_receipt_missing")
        return HostExtensionRefV1(
            kind="deskpet.capabilities.snapshot_lease_release.v1",
            ref=receipt_id,
            content_hash=receipt_hash,
        )


@dataclass(frozen=True, slots=True)
class _UnpinRunCatalogLease:
    lease: PreparedRunCatalogLease

    @property
    def descriptor(self) -> HostExtensionRefV1:
        return HostExtensionRefV1(
            kind="deskpet.capabilities.snapshot_lease_unpin.v1",
            ref=self.lease.snapshot_ref,
            content_hash=self.lease.run_catalog_content_stamp,
        )

    async def cleanup_after_terminal(
        self,
        record: Any,
        event: Any,
        *,
        extension_receipts: Sequence[HostExtensionRefV1],
    ) -> None:
        del event
        if str(
            getattr(
                record,
                "run_id",
                getattr(getattr(record, "ref", None), "run_id", ""),
            )
        ) != self.lease.run_id:
            raise RuntimeError("run_catalog_terminal_fence_mismatch")
        if not any(
            item.kind == "deskpet.capabilities.snapshot_lease_release.v1"
            for item in extension_receipts
        ):
            raise RuntimeError("run_catalog_release_receipt_missing")
        await self.lease._pin.rollback()
        await self.lease._ready_gate.retire(
            intent_id=self.lease.lease_intent_id,
            run_id=self.lease.run_id,
        )


class SqliteRunCatalogLeasePreparer:
    """Convert one locked Hub snapshot into the durable Run-catalog envelope."""

    def __init__(
        self,
        *,
        store: Any,
        registry: Any,
        hub: Any,
        process_instance_id: str | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._hub = hub
        self._process_instance_id = process_instance_id or uuid.uuid4().hex

    def _host_build_identity(self, provider_names: Sequence[str]) -> Mapping[str, Any]:
        identities: dict[str, Any] = {}
        for name in provider_names:
            spec = self._registry.get(name)
            if spec is None:
                raise RuntimeError(f"run_catalog_tool_missing:{name}")
            identity = getattr(spec, "execution_build_identity", None)
            if identity is None:
                raise RuntimeError(f"run_catalog_build_identity_missing:{name}")
            identities[name] = identity.fingerprint_payload()
        return {
            "algorithm": "execution-build-set-v1",
            "members": identities,
            "fingerprint": fingerprint_json(identities),
        }

    def _entries(
        self,
        snapshot: CapabilityCatalogSnapshot,
        selected_entries: Sequence[CapabilityCatalogEntry],
    ) -> tuple[RunCatalogEntryIdentity, ...]:
        store_keys = {
            (
                item.version.capability_id,
                item.version.version,
                item.version.manifest_hash,
            )
            for item in selected_entries
        }
        identities: list[RunCatalogEntryIdentity] = []
        for descriptor in snapshot.descriptors:
            version = descriptor.version
            selected = _selected_binding(snapshot, descriptor.visible_bindings)
            visible = [
                item.to_dict()
                for item in sorted(
                    descriptor.visible_bindings,
                    key=lambda value: (
                        value.owner_key,
                        value.scope,
                        value.scope_key,
                        value.binding_id,
                    ),
                )
            ]
            base: dict[str, Any] = {
                "selected_binding": selected.to_dict(),
                "visible_bindings": visible,
                "descriptor": version.to_dict(),
                "tool_spec_fingerprints": list(
                    descriptor.tool_spec_fingerprints
                ),
                "instruction_refs_hash": fingerprint_json([]),
                "workflow_refs_hash": fingerprint_json([]),
                "runtime_descriptor_hash": fingerprint_json([]),
            }
            version_key = (
                version.capability_id,
                version.version,
                version.manifest_hash,
            )
            if version_key in store_keys:
                kind = "pack"
                base.update(
                    {
                        "pack_id": version.capability_id,
                        "version": version.version,
                        "manifest_hash": version.manifest_hash,
                    }
                )
            elif version.provider_tool_names:
                kind = "host_tool"
                build_identity = self._host_build_identity(
                    version.provider_tool_names
                )
                base.update(
                    {
                        "selected_binding": None,
                        "stable_host_binding_id": fingerprint_json(
                            {
                                "source": version.source,
                                "provider_names": list(
                                    version.provider_tool_names
                                ),
                                "schema_hash": version.schema_hash,
                                "build_identity": build_identity,
                            }
                        ),
                        "host_provider_name": ",".join(
                            version.provider_tool_names
                        ),
                        "host_source": version.source,
                        "host_spec_version": version.version,
                        "host_schema_hash": version.schema_hash,
                        "host_content_hash": version.manifest_hash,
                        "host_build_identity": build_identity,
                    }
                )
            else:
                kind = "host_instruction"
                base.update(
                    {
                        "selected_binding": None,
                        "stable_host_binding_id": fingerprint_json(
                            {
                                "source": version.source,
                                "manifest_hash": version.manifest_hash,
                            }
                        ),
                        "host_provider_name": version.capability_id,
                        "host_source": version.source,
                        "host_spec_version": version.version,
                        "host_schema_hash": version.schema_hash,
                        "host_content_hash": version.manifest_hash,
                        "host_build_identity": {
                            "algorithm": "instruction-content-v1",
                            "fingerprint": version.manifest_hash,
                        },
                    }
                )
            descriptor_fingerprint = fingerprint_json(
                {"entry_kind": kind, "envelope": base}
            )
            identities.append(
                RunCatalogEntryIdentity(
                    entry_kind=kind,
                    descriptor_fingerprint=descriptor_fingerprint,
                    canonical_envelope=base,
                )
            )
        return tuple(identities)

    async def prepare_captured_run_catalog(
        self,
        *,
        snapshot: CapabilityCatalogSnapshot,
        selected_entries: Sequence[CapabilityCatalogEntry],
        owner_key: str,
        prepared_tool_set: PreparedToolSet,
        prepared_tool_set_fingerprint: str,
        ready_gate: SnapshotLeaseReadyGate,
        run_id: str,
        root_run_id: str,
        request_id: str,
        turn_id: str,
        owner_operation_id: str,
    ) -> PreparedRunCatalogLease:
        prepared_capture = _capture_prepared_tool_set(
            prepared_tool_set,
            registry=self._registry,
            expected_external_ref=prepared_tool_set_fingerprint,
            expected_registry_revision=snapshot.stamp.registry_revision,
        )
        entries = self._entries(snapshot, selected_entries)
        content = RunCatalogContentStamp(snapshot.scope, entries)
        process = ProcessCatalogStamp(
            process_instance_id=self._process_instance_id,
            run_catalog_content_stamp=content.fingerprint,
            catalog_generation=snapshot.stamp.catalog_generation,
            registry_revision=snapshot.stamp.registry_revision,
            skill_revision=snapshot.stamp.skill_revision,
            mcp_revision=snapshot.stamp.mcp_revision,
        )
        snapshot_ref = content.snapshot_ref
        tool_fingerprints = tuple(
            sorted(
                {
                    value
                    for descriptor in snapshot.descriptors
                    for value in descriptor.tool_spec_fingerprints
                }.union(prepared_capture.tool_spec_fingerprints)
            )
        )
        pin: PendingProcessPinToken | None = None
        try:
            async with self._store.write_transaction() as db:
                projection = await self._store.prepare_run_catalog_projection_in_tx(
                    db,
                    content=content,
                    process_stamp=process,
                    snapshot_ref=snapshot_ref,
                    request_owner_key=owner_key,
                    catalog_generation_vector={
                        "catalog": snapshot.stamp.catalog_generation,
                        "binding": snapshot.stamp.binding_generation,
                        "registry": snapshot.stamp.registry_revision,
                        "skill": snapshot.stamp.skill_revision,
                        "mcp": snapshot.stamp.mcp_revision,
                    },
                    run_id=run_id,
                    root_run_id=root_run_id,
                    request_id=request_id,
                    turn_id=turn_id,
                    owner_operation_id=owner_operation_id,
                    lease_owner_kind="run_start",
                    prepared_tool_set_envelope=prepared_capture.envelope,
                    prepared_tool_set_hash=prepared_capture.fingerprint,
                )
                intent = _result_field(projection, "intent", projection)
                intent_id = str(
                    _result_field(intent, "lease_intent_id")
                    or _result_field(intent, "intent_id")
                    or ""
                )
                intent_hash = str(
                    _result_field(intent, "lease_intent_hash")
                    or _result_field(intent, "intent_hash")
                    or ""
                )
                projection_receipt = _result_field(
                    projection, "projection_receipt", projection
                )
                projection_receipt_id = str(
                    _result_field(
                        projection_receipt, "projection_receipt_id"
                    )
                    or _result_field(projection_receipt, "receipt_id")
                    or ""
                )
                projection_receipt_hash = str(
                    _result_field(
                        projection_receipt, "projection_receipt_hash"
                    )
                    or _result_field(projection_receipt, "receipt_hash")
                    or ""
                )
                if not all(
                    (
                        intent_id,
                        intent_hash,
                        projection_receipt_id,
                        projection_receipt_hash,
                    )
                ):
                    raise RuntimeError("run_catalog_projection_receipt_incomplete")
                pin = PendingProcessPinToken(
                    hub=self._hub,
                    snapshot_ref=snapshot_ref,
                    run_id=run_id,
                    lease_intent_id=intent_id,
                    tool_spec_fingerprints=tool_fingerprints,
                )
                await pin.install()
                await ready_gate.register_pending(
                    intent_id=intent_id,
                    run_id=run_id,
                    pin=pin,
                )
            pin.commit()
        except BaseException:
            if pin is not None:
                await pin.rollback()
                await ready_gate.unregister_pending(
                    pin.lease_intent_id,
                    run_id,
                )
            raise
        return PreparedRunCatalogLease(
            snapshot_ref=snapshot_ref,
            run_catalog_content_stamp=content.fingerprint,
            process_catalog_stamp=process.fingerprint,
            lease_intent_id=intent_id,
            lease_intent_hash=intent_hash,
            prepared_tool_set_fingerprint=prepared_tool_set_fingerprint,
            run_id=run_id,
            root_run_id=root_run_id,
            entry_set_hash=content.entry_set_hash,
            expected_entry_count=len(content.entries),
            prepared_tool_set_capture_hash=prepared_capture.fingerprint,
            prepared_tool_set_envelope=prepared_capture.envelope,
            lease_entries=tuple(
                {
                    **dict(entry.canonical_envelope),
                    "entry_kind": entry.entry_kind,
                    "descriptor_fingerprint": (
                        entry.descriptor_fingerprint
                    ),
                }
                for entry in content.entries
            ),
            projection_receipt_id=projection_receipt_id,
            projection_receipt_hash=projection_receipt_hash,
            process_instance_id=self._process_instance_id,
            _store=self._store,
            _hub=self._hub,
            _ready_gate=ready_gate,
            _pin=pin,
        )


class FirstPartyFrozenSkillResolver:
    """Resolve first-party or managed Skills from one exact Run catalog.

    The historical class name is retained for compatibility.  Selection is
    authority-neutral: the frozen binding owner and immutable pack version in
    the Run lease decide which Skill bytes may be paged in.
    """

    def __init__(self, *, store: Any, inventory: Sequence[Any]) -> None:
        from deskpet.skills.loader import SkillPackSnapshotResolver

        self._store = store
        # Kept for constructor compatibility; selection authority is the Run
        # catalog, not the process's current first-party inventory.
        self._inventory = tuple(inventory)
        self._snapshot_resolver = SkillPackSnapshotResolver(
            version_store=store
        )

    async def resolve_frozen_instruction(
        self,
        *,
        run_id: str,
        skill_name: str,
        arguments: tuple[str, ...],
    ) -> Mapping[str, Any]:
        async with self._store.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT i.snapshot_ref,i.run_catalog_content_stamp,
                              e.selected_owner_key,e.pack_id,e.version,
                              e.manifest_hash,
                              r.prepared_tool_set_envelope_json
                       FROM capability_snapshot_lease_intents AS i
                       JOIN capability_run_catalog_snapshot_entries AS e
                         ON e.run_catalog_content_stamp=
                            i.run_catalog_content_stamp
                       JOIN capability_runtime_projection_receipts AS r
                         ON r.lease_intent_id=i.lease_intent_id
                        AND r.purpose='snapshot_pin' AND r.status='ready'
                       WHERE i.run_id=? AND i.status='bound'
                         AND e.entry_kind='pack'
                       ORDER BY e.ordinal""",
                    (run_id,),
                )
            ).fetchall()
        if not rows:
            raise RuntimeError("frozen_skill_not_in_run_catalog")
        row = None
        scope = None
        for candidate in rows:
            try:
                candidate_scope = await self._snapshot_resolver.resolve_scope(
                    owner_key=str(
                        candidate["selected_owner_key"] or "builtin"
                    ),
                    pack_id=str(candidate["pack_id"]),
                    skill_id=str(skill_name),
                    version=str(candidate["version"]),
                    manifest_hash=str(candidate["manifest_hash"]),
                )
            except RuntimeError as exc:
                if str(exc) == "frozen_skill_manifest_entry_missing":
                    continue
                raise
            row = candidate
            scope = candidate_scope
            break
        if row is None or scope is None:
            raise RuntimeError("frozen_skill_not_in_run_catalog")
        resolved = await self._snapshot_resolver.resolve_instruction(
            scope,
            arguments,
        )
        envelope = json.loads(
            str(row["prepared_tool_set_envelope_json"])
        )
        prepared_raw = envelope.get("prepared")
        if not isinstance(prepared_raw, Mapping):
            raise RuntimeError("frozen_skill_prepared_tool_set_missing")
        prepared = load_prepared_tool_set(prepared_raw)
        exact_tools = envelope.get("exact_tools")
        if (
            isinstance(exact_tools, (str, bytes))
            or not isinstance(exact_tools, (list, tuple))
            or any(not isinstance(item, Mapping) for item in exact_tools)
        ):
            raise RuntimeError("frozen_skill_exact_tool_facts_missing")
        intersection = intersect_prepared_skill_tools(
            prepared,
            (scope,),
            exact_tool_facts=tuple(exact_tools),
        )
        return {
            **resolved.to_dict(),
            "capability_snapshot_ref": str(row["snapshot_ref"]),
            "run_catalog_content_stamp": str(
                row["run_catalog_content_stamp"]
            ),
            "allowed_tool_refs": [
                dict(item)
                for item in intersection.effective_tool_fact_payloads
            ],
            "effective_tool_ref_hashes": list(
                intersection.effective_tool_ref_hashes
            ),
            "effective_tool_refs_hash": (
                intersection.effective_tool_refs_hash
            ),
        }

    async def resolve_frozen_resource(
        self,
        *,
        run_id: str,
        skill_name: str,
        relative_path: str,
    ) -> Mapping[str, Any]:
        async with self._store.read_connection() as db:
            rows = await (
                await db.execute(
                    """SELECT i.snapshot_ref,e.selected_owner_key,e.pack_id,
                              e.version,e.manifest_hash
                       FROM capability_snapshot_lease_intents AS i
                       JOIN capability_run_catalog_snapshot_entries AS e
                         ON e.run_catalog_content_stamp=i.run_catalog_content_stamp
                       JOIN capability_runtime_projection_receipts AS r
                         ON r.lease_intent_id=i.lease_intent_id
                        AND r.purpose='snapshot_pin' AND r.status='ready'
                       WHERE i.run_id=? AND i.status='bound'
                         AND e.entry_kind='pack'
                       ORDER BY e.ordinal""",
                    (run_id,),
                )
            ).fetchall()
        for row in rows:
            try:
                scope = await self._snapshot_resolver.resolve_scope(
                    owner_key=str(row["selected_owner_key"] or "builtin"),
                    pack_id=str(row["pack_id"]),
                    skill_id=skill_name,
                    version=str(row["version"]),
                    manifest_hash=str(row["manifest_hash"]),
                )
            except RuntimeError as exc:
                if str(exc) == "frozen_skill_manifest_entry_missing":
                    continue
                raise
            resolved_path, raw = await self._snapshot_resolver.resolve_skill_resource(
                scope, relative_path
            )
            if len(raw) > 256 * 1024:
                raise RuntimeError("frozen_skill_resource_too_large")
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RuntimeError("frozen_skill_resource_not_text") from exc
            return {
                "skill_id": skill_name,
                "resource_path": relative_path,
                "resolved_path": resolved_path,
                "content": content,
                "content_hash": hashlib.sha256(raw).hexdigest(),
                "capability_snapshot_ref": str(row["snapshot_ref"]),
            }
        raise RuntimeError("frozen_skill_not_in_run_catalog")


__all__ = [
    "FirstPartyFrozenSkillResolver",
    "PreparedRunCatalogLease",
    "SnapshotLeaseReadyGate",
    "SqliteRunCatalogLeasePreparer",
]
