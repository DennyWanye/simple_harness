# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Stamped capability projection grounded in the live execution authorities."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import replace
from typing import Any, Awaitable, Callable, Mapping, Protocol

from deskpet.tools.registry import tool_spec_fingerprint

from .contracts import (
    SCOPE_PRECEDENCE,
    CapabilityBinding,
    CapabilityCatalogEntry,
    CapabilityCatalogSnapshot,
    CapabilityCollisionError,
    CapabilityDescriptor,
    CapabilityScope,
    CapabilityVersionDescriptor,
    CatalogStamp,
    CatalogUnstableError,
    PendingPublishError,
    RegistryCatalogSnapshot,
    RegistryToolDescriptor,
    RevisionedCatalogEntries,
    fingerprint_json,
)
from .catalog_gate import CapabilityCatalogGate, CatalogGateKey
from .store import CapabilityStore


class RegistryCatalogSource(Protocol):
    async def snapshot(self) -> RegistryCatalogSnapshot:
        ...


class CapabilityEntrySource(Protocol):
    async def snapshot(self) -> RevisionedCatalogEntries:
        ...


class CatalogRevisionSource(Protocol):
    async def revision(self) -> int:
        ...


class _AsyncLock(Protocol):
    async def __aenter__(self) -> Any:
        ...

    async def __aexit__(self, *args: Any) -> None:
        ...


class StaticCapabilityEntrySource:
    def __init__(
        self,
        entries: tuple[CapabilityCatalogEntry, ...] = (),
        *,
        revision: int = 0,
    ) -> None:
        self.entries = entries
        self.value = revision

    async def snapshot(self) -> RevisionedCatalogEntries:
        return RevisionedCatalogEntries(self.value, tuple(self.entries))


class StaticCatalogRevisionSource:
    def __init__(self, revision: int = 0) -> None:
        self.value = revision

    async def revision(self) -> int:
        return self.value


class ToolRegistryCatalogSource:
    """Read the existing ``ToolRegistry.catalog_snapshot`` without owning it."""

    def __init__(self, registry: Any) -> None:
        self._registry = registry
        self._exact_fingerprints: dict[str, str] = {}
        self._exact_revision = -1

    async def snapshot(self) -> RegistryCatalogSnapshot:
        raw = self._registry.catalog_snapshot()
        exact_fingerprints: dict[str, str] = {}
        tools: list[RegistryToolDescriptor] = []
        for spec in raw.specs:
            if not spec.env_satisfied():
                continue
            if (
                getattr(spec, "visibility_scope", "global") == "global"
                and not spec.is_visible()
            ):
                continue
            exact_fingerprints[str(spec.name)] = tool_spec_fingerprint(spec)
            schema_hash = str(getattr(spec, "schema_hash", "") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", schema_hash):
                schema_hash = fingerprint_json(getattr(spec, "schema", {}))
            effect = getattr(getattr(spec, "effect_policy", None), "kind", None)
            effect_kind = str(getattr(effect, "value", effect) or "opaque_manual")
            tools.append(
                RegistryToolDescriptor(
                    provider_name=str(spec.name),
                    source=str(getattr(spec, "source", "builtin") or "builtin"),
                    description=str(
                        getattr(spec, "description_for_llm", "") or spec.name
                    ),
                    schema_hash=schema_hash,
                    permission_category=str(
                        getattr(spec, "permission_category", "read_file")
                    ),
                    effect_kind=effect_kind,
                    spec_version=str(getattr(spec, "spec_version", "v1") or "v1"),
                    permission_policy_version=str(
                        getattr(spec, "permission_policy_version", "v1") or "v1"
                    ),
                    dangerous=bool(getattr(spec, "dangerous", False)),
                    effect_policy_version=str(
                        getattr(getattr(spec, "effect_policy", None), "version", "")
                        or ""
                    ),
                    dispatch_kind=(
                        "trusted_context_handler"
                        if getattr(spec, "context_handler", None) is not None
                        else "legacy_handler"
                    ),
                    concurrency_safe=bool(
                        getattr(spec, "concurrency_safe", True)
                    ),
                    completion_semantics=str(
                        getattr(spec, "completion_semantics", "sync") or "sync"
                    ),
                    outcome_parser_id=str(
                        getattr(spec, "outcome_parser_id", "") or ""
                    ),
                    outcome_parser_version=str(
                        getattr(spec, "outcome_parser_version", "") or ""
                    ),
                    outcome_parser_hash=str(
                        getattr(spec, "outcome_parser_hash", "") or ""
                    ),
                    resource_scope_resolver_id=str(
                        getattr(spec, "resource_scope_resolver_id", "") or ""
                    ),
                    resource_scope_resolver_version=str(
                        getattr(spec, "resource_scope_resolver_version", "") or ""
                    ),
                    runtime_provenance_ref=str(
                        getattr(spec, "runtime_provenance_ref", "") or ""
                    ),
                )
            )
        self._exact_fingerprints = exact_fingerprints
        self._exact_revision = int(raw.revision)
        return RegistryCatalogSnapshot(revision=int(raw.revision), tools=tuple(tools))

    def exact_fingerprints(self, revision: int) -> Mapping[str, str]:
        if int(revision) != self._exact_revision:
            raise RuntimeError("registry exact fingerprint revision changed")
        return dict(self._exact_fingerprints)

    async def lease_snapshot(
        self,
        *,
        snapshot_ref: str,
        run_id: str,
        entries: tuple[CapabilityCatalogEntry, ...],
    ) -> None:
        self._registry.lease_catalog_snapshot(
            snapshot_ref=snapshot_ref,
            run_id=run_id,
            tool_spec_fingerprints=(
                fingerprint
                for entry in entries
                for fingerprint in entry.expected_tool_fingerprints
            ),
        )

    async def lease_snapshot_fingerprints(
        self,
        *,
        snapshot_ref: str,
        run_id: str,
        tool_spec_fingerprints: tuple[str, ...],
    ) -> None:
        self._registry.lease_catalog_snapshot(
            snapshot_ref=snapshot_ref,
            run_id=run_id,
            tool_spec_fingerprints=tool_spec_fingerprints,
        )

    async def release_snapshot(self, *, snapshot_ref: str, run_id: str) -> bool:
        return bool(
            self._registry.release_catalog_snapshot(
                snapshot_ref=snapshot_ref,
                run_id=run_id,
            )
        )


class SkillLoaderCatalogSource:
    """Adapter for prompt-only ``SkillLoader`` facts."""

    def __init__(
        self,
        loader: Any,
        *,
        user_scope_key: str = "default",
        builtin_scope_key: str = "builtin",
    ) -> None:
        self._loader = loader
        self._user_scope_key = user_scope_key
        self._builtin_scope_key = builtin_scope_key
        self._lock = asyncio.Lock()
        self._content_fingerprint = ""
        self._revision = 0

    @staticmethod
    def _capability_id(name: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?", name):
            return name
        return "skill_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]

    async def snapshot(self) -> RevisionedCatalogEntries:
        metas = self._loader.list_metas()
        payload = []
        entries: list[CapabilityCatalogEntry] = []
        for meta in metas:
            raw = (
                meta.to_dict()
                if callable(getattr(meta, "to_dict", None))
                else dict(vars(meta))
            )
            payload.append(raw)
            name = str(getattr(meta, "name", raw.get("name", "")))
            scope_name = str(getattr(meta, "scope", raw.get("scope", "user")))
            scope = "builtin" if scope_name in {"builtin", "built-in", "bundled"} else "user"
            scope_key = (
                self._builtin_scope_key if scope == "builtin" else self._user_scope_key
            )
            manifest_hash = fingerprint_json(raw)
            descriptor = CapabilityVersionDescriptor(
                capability_id=self._capability_id(name),
                display_name=name,
                version=str(raw.get("version") or f"content-{manifest_hash[:12]}"),
                kind="instruction",
                source=f"skill:{scope}:{name}",
                description=str(raw.get("description") or name),
                aliases=(name,),
                logical_tool_ids=(),
                provider_tool_names=(),
                permission_categories=(),
                effect_kinds=(),
                schema_hash=fingerprint_json({}),
                manifest_hash=manifest_hash,
                health="healthy",
            )
            binding = CapabilityBinding(
                binding_id=fingerprint_json(
                    {
                        "scope": scope,
                        "scope_key": scope_key,
                        "capability_id": descriptor.capability_id,
                    }
                ),
                capability_id=descriptor.capability_id,
                version=descriptor.version,
                manifest_hash=descriptor.manifest_hash,
                scope=scope,  # type: ignore[arg-type]
                scope_key=scope_key,
                active=True,
                generation=1,
            )
            entries.append(CapabilityCatalogEntry(descriptor, (binding,), ()))
        content_fingerprint = fingerprint_json(payload)
        async with self._lock:
            if content_fingerprint != self._content_fingerprint:
                self._content_fingerprint = content_fingerprint
                self._revision += 1
            revision = self._revision
        return RevisionedCatalogEntries(revision, tuple(entries))


def _builtin_entries(
    registry: RegistryCatalogSnapshot,
) -> tuple[CapabilityCatalogEntry, ...]:
    entries: list[CapabilityCatalogEntry] = []
    for tool in registry.tools:
        # Capability-pack tools are represented by the store entry that owns
        # their version/binding.  Legacy plugin/MCP/builtin tools remain visible
        # as individually grounded entries.
        if tool.source.startswith("capability:"):
            continue
        raw_id = tool.provider_name
        capability_id = (
            raw_id
            if re.fullmatch(
                r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?", raw_id
            )
            else "tool_" + hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:24]
        )
        manifest_hash = fingerprint_json(
            {
                "provider_name": tool.provider_name,
                "source": tool.source,
                "fingerprint": tool.fingerprint,
            }
        )
        descriptor = CapabilityVersionDescriptor(
            capability_id=capability_id,
            display_name=tool.provider_name,
            version=tool.spec_version,
            kind=("mcp_tool" if tool.source.startswith("mcp:") else "function_tool"),
            source=tool.source,
            description=tool.description,
            aliases=(tool.provider_name,),
            logical_tool_ids=(tool.provider_name,),
            provider_tool_names=(tool.provider_name,),
            permission_categories=(tool.permission_category,),
            effect_kinds=(tool.effect_kind,),
            schema_hash=tool.schema_hash,
            manifest_hash=manifest_hash,
            health="healthy",
        )
        binding = CapabilityBinding(
            binding_id=fingerprint_json(
                {
                    "scope": "builtin",
                    "scope_key": "builtin",
                    "capability_id": capability_id,
                    "source": tool.source,
                }
            ),
            capability_id=capability_id,
            version=descriptor.version,
            manifest_hash=manifest_hash,
            scope="builtin",
            scope_key="builtin",
            active=True,
            generation=registry.revision,
        )
        entries.append(
            CapabilityCatalogEntry(
                descriptor,
                (binding,),
                (tool.fingerprint,),
            )
        )
    return tuple(entries)


def _merge_identical_entries(
    entries: tuple[CapabilityCatalogEntry, ...],
) -> tuple[CapabilityCatalogEntry, ...]:
    merged: dict[
        tuple[str, str, str],
        tuple[CapabilityVersionDescriptor, list[CapabilityBinding], set[str]],
    ] = {}
    for entry in entries:
        key = (
            entry.version.capability_id,
            entry.version.version,
            entry.version.manifest_hash,
        )
        current = merged.get(key)
        if current is None:
            merged[key] = (
                entry.version,
                list(entry.bindings),
                set(entry.expected_tool_fingerprints),
            )
            continue
        if current[0].fingerprint != entry.version.fingerprint:
            raise CapabilityCollisionError(
                "version_descriptor_collision",
                f"{entry.version.capability_id}@{entry.version.version} has conflicting facts",
            )
        current[1].extend(entry.bindings)
        current[2].update(entry.expected_tool_fingerprints)
    return tuple(
        CapabilityCatalogEntry(
            version=version,
            bindings=tuple(bindings),
            expected_tool_fingerprints=tuple(sorted(fingerprints)),
        )
        for version, bindings, fingerprints in merged.values()
    )


def _select_descriptor(
    capability_id: str,
    entries: tuple[CapabilityCatalogEntry, ...],
    *,
    scope: CapabilityScope,
    registry: RegistryCatalogSnapshot,
    stamp: CatalogStamp,
    exact_registry_fingerprints: Mapping[str, str] | None = None,
) -> tuple[CapabilityDescriptor, CapabilityCatalogEntry] | None:
    visible: list[tuple[CapabilityCatalogEntry, CapabilityBinding]] = []
    expected_keys = dict(scope.binding_keys())
    for entry in entries:
        for binding in entry.bindings:
            if (
                binding.active
                and expected_keys.get(binding.scope) == binding.scope_key
            ):
                visible.append((entry, binding))
    if not visible:
        discoverable = tuple(entry for entry in entries if not entry.bindings)
        if not discoverable:
            return None
        by_version: dict[str, list[CapabilityCatalogEntry]] = {}
        for entry in discoverable:
            by_version.setdefault(entry.version.version, []).append(entry)
        selected_version = sorted(by_version)[-1]
        at_version = by_version[selected_version]
        identities = {
            (
                entry.version.manifest_hash,
                entry.version.fingerprint,
            )
            for entry in at_version
        }
        if len(identities) != 1:
            raise CapabilityCollisionError(
                "configured_source_collision",
                f"{capability_id}@{selected_version} has conflicting sources",
            )
        entry = at_version[0]
        return (
            CapabilityDescriptor(
                version=entry.version,
                visible_bindings=(),
                executable=False,
                installed=False,
                tool_spec_fingerprints=(),
                stamp=stamp,
            ),
            entry,
        )

    selected_pair: tuple[CapabilityCatalogEntry, CapabilityBinding] | None = None
    for scope_kind in SCOPE_PRECEDENCE:
        at_level = [
            pair for pair in visible if pair[1].scope == scope_kind
        ]
        if not at_level:
            continue
        identities = {
            (
                pair[1].version,
                pair[1].manifest_hash,
                pair[1].binding_id,
            )
            for pair in at_level
        }
        if len(identities) != 1:
            raise CapabilityCollisionError(
                "active_binding_collision",
                f"{capability_id} has multiple active {scope_kind} bindings",
            )
        selected_pair = at_level[0]
        break
    if selected_pair is None:  # pragma: no cover - visible guarantees a scope
        return None
    selected_entry, selected_binding = selected_pair
    if (
        selected_entry.version.version != selected_binding.version
        or selected_entry.version.manifest_hash != selected_binding.manifest_hash
    ):
        raise CapabilityCollisionError(
            "binding_version_missing",
            f"{capability_id} binding does not resolve to a visible version",
        )

    registry_by_name = registry.by_name
    actual_tools = [
        registry_by_name[name]
        for name in selected_entry.version.provider_tool_names
        if name in registry_by_name
    ]
    actual_fingerprints = tuple(
        (
            exact_registry_fingerprints.get(tool.provider_name, tool.fingerprint)
            if exact_registry_fingerprints is not None
            else tool.fingerprint
        )
        for tool in actual_tools
    )
    expected_fingerprints = selected_entry.expected_tool_fingerprints
    executable = (
        selected_entry.version.kind != "instruction"
        and len(actual_tools) == len(selected_entry.version.provider_tool_names)
        and bool(selected_entry.version.provider_tool_names)
        and bool(expected_fingerprints)
        and set(actual_fingerprints) == set(expected_fingerprints)
    )
    version = selected_entry.version
    if version.kind != "instruction" and not executable:
        version = replace(version, health="degraded")
    bindings = tuple(
        sorted(
            (pair[1] for pair in visible),
            key=lambda item: (
                SCOPE_PRECEDENCE.index(item.scope),
                item.scope_key,
                item.binding_id,
            ),
        )
    )
    selected_entry = CapabilityCatalogEntry(
        version=version,
        bindings=selected_entry.bindings,
        expected_tool_fingerprints=selected_entry.expected_tool_fingerprints,
    )
    return (
        CapabilityDescriptor(
            version=version,
            visible_bindings=bindings,
            executable=executable,
            installed=bool(bindings),
            tool_spec_fingerprints=actual_fingerprints,
            stamp=stamp,
        ),
        selected_entry,
    )


class CapabilityHub:
    """Publish immutable, all-authority catalog snapshots."""

    def __init__(
        self,
        *,
        store: CapabilityStore,
        registry_source: RegistryCatalogSource,
        skill_source: CapabilityEntrySource | None = None,
        mcp_revision_source: CatalogRevisionSource | None = None,
        legacy_source: CapabilityEntrySource | None = None,
        publish_lock: _AsyncLock | None = None,
        catalog_gate: CapabilityCatalogGate | None = None,
        max_snapshot_retries: int = 4,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_snapshot_retries < 1:
            raise ValueError("max_snapshot_retries must be positive")
        self.store = store
        self.registry_source = registry_source
        self.skill_source = skill_source or StaticCapabilityEntrySource()
        self.mcp_revision_source = (
            mcp_revision_source or StaticCatalogRevisionSource()
        )
        self.legacy_source = legacy_source or StaticCapabilityEntrySource()
        self.publish_lock = publish_lock or asyncio.Lock()
        self.catalog_gate = catalog_gate or CapabilityCatalogGate()
        self.max_snapshot_retries = max_snapshot_retries
        self._clock = clock
        self._cache: dict[
            tuple[str, str],
            tuple[CapabilityCatalogSnapshot, tuple[CapabilityCatalogEntry, ...]],
        ] = {}

    async def _read_vector(
        self,
    ) -> tuple[
        Any,
        RegistryCatalogSnapshot,
        RevisionedCatalogEntries,
        int,
        RevisionedCatalogEntries,
    ]:
        store_state, registry, skills, mcp_revision, legacy = await asyncio.gather(
            self.store.state(),
            self.registry_source.snapshot(),
            self.skill_source.snapshot(),
            self.mcp_revision_source.revision(),
            self.legacy_source.snapshot(),
        )
        return store_state, registry, skills, int(mcp_revision), legacy

    @staticmethod
    def _vector_identity(vector: tuple[Any, ...]) -> tuple[int, ...]:
        store_state, registry, skills, mcp_revision, legacy = vector
        return (
            int(store_state.catalog_generation),
            int(store_state.binding_generation),
            int(store_state.pending_publish_count),
            int(registry.revision),
            int(skills.revision),
            int(mcp_revision),
            int(legacy.revision),
        )

    async def _snapshot_locked(
        self, scope: CapabilityScope
    ) -> tuple[CapabilityCatalogSnapshot, tuple[CapabilityCatalogEntry, ...]]:
        for _attempt in range(self.max_snapshot_retries):
            before = await self._read_vector()
            before_state, registry, skills, mcp_revision, legacy = before
            if before_state.pending_publish_count:
                raise PendingPublishError(
                    "capability publish intent must be reconciled before catalog read"
                )
            store_entries = await self.store.visible_entries(scope)
            after = await self._read_vector()
            if self._vector_identity(before) != self._vector_identity(after):
                continue
            catalog_generation = int(
                fingerprint_json(
                    {
                        "store": before_state.catalog_generation,
                        "legacy": legacy.revision,
                    }
                )[:15],
                16,
            )
            stamp = CatalogStamp(
                catalog_generation=catalog_generation,
                registry_revision=registry.revision,
                binding_generation=before_state.binding_generation,
                skill_revision=skills.revision,
                mcp_revision=mcp_revision,
            )
            cache_key = (stamp.fingerprint, scope.canonical)
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
            entries = _merge_identical_entries(
                (
                    *store_entries,
                    *skills.entries,
                    *legacy.entries,
                    *_builtin_entries(registry),
                )
            )
            by_capability: dict[str, list[CapabilityCatalogEntry]] = {}
            for entry in entries:
                by_capability.setdefault(
                    entry.version.capability_id, []
                ).append(entry)
            descriptors: list[CapabilityDescriptor] = []
            exact_fingerprints_reader = getattr(
                self.registry_source, "exact_fingerprints", None
            )
            exact_registry_fingerprints = (
                exact_fingerprints_reader(registry.revision)
                if callable(exact_fingerprints_reader)
                else None
            )
            store_version_keys = {
                (
                    entry.version.capability_id,
                    entry.version.version,
                    entry.version.manifest_hash,
                )
                for entry in store_entries
            }
            leased_entries: list[CapabilityCatalogEntry] = []
            for capability_id, candidates in sorted(by_capability.items()):
                selected = _select_descriptor(
                    capability_id,
                    tuple(candidates),
                    scope=scope,
                    registry=registry,
                    stamp=stamp,
                    exact_registry_fingerprints=exact_registry_fingerprints,
                )
                if selected is not None:
                    descriptor, entry = selected
                    descriptors.append(descriptor)
                    version_key = (
                        entry.version.capability_id,
                        entry.version.version,
                        entry.version.manifest_hash,
                    )
                    if version_key in store_version_keys:
                        # Built-in ToolRegistry rows and prompt-only skills do
                        # not have immutable rows in CapabilityStore and are
                        # therefore not GC targets.  Only store-owned versions
                        # may be persisted in the FK-backed snapshot lease table.
                        leased_entries.append(entry)
            snapshot = CapabilityCatalogSnapshot(
                stamp=stamp,
                scope=scope,
                descriptors=tuple(descriptors),
                created_at=self._clock(),
            )
            result = (snapshot, tuple(leased_entries))
            self._cache[cache_key] = result
            # Bound memory by generations; search caches use the same stamp.
            if len(self._cache) > 32:
                oldest = next(iter(self._cache))
                if oldest != cache_key:
                    self._cache.pop(oldest, None)
            return result
        raise CatalogUnstableError(
            "capability catalog revisions changed during every snapshot attempt"
        )

    @staticmethod
    def _gate_keys(
        scope: CapabilityScope, owner_key: str | None
    ) -> tuple[CatalogGateKey, ...]:
        owner = str(owner_key or scope.user_key).strip()
        if not owner:
            raise ValueError("catalog owner_key is required")
        return tuple(
            CatalogGateKey(owner, scope_kind, scope_key, "*")
            for scope_kind, scope_key in scope.binding_keys()
        )

    async def snapshot(
        self, scope: CapabilityScope, *, owner_key: str | None = None
    ) -> CapabilityCatalogSnapshot:
        async with self.publish_lock:
            async with self.catalog_gate.read(
                self._gate_keys(scope, owner_key)
            ):
                snapshot, _entries = await self._snapshot_locked(scope)
                return snapshot

    async def snapshot_for_atomic_lease(
        self, scope: CapabilityScope, *, owner_key: str | None = None
    ) -> tuple[CapabilityCatalogSnapshot, tuple[CapabilityCatalogEntry, ...]]:
        """Capture a snapshot while the caller holds ``publish_lock``.

        Refresh commits need the selected store rows so their new snapshot
        lease can share the caller's execution-UoW transaction.
        """

        async with self.catalog_gate.read(self._gate_keys(scope, owner_key)):
            return await self._snapshot_locked(scope)

    async def mirror_snapshot_lease(
        self,
        *,
        snapshot_ref: str,
        run_id: str,
        entries: tuple[CapabilityCatalogEntry, ...],
    ) -> None:
        lease_snapshot = getattr(self.registry_source, "lease_snapshot", None)
        if callable(lease_snapshot):
            await lease_snapshot(
                snapshot_ref=snapshot_ref,
                run_id=run_id,
                entries=entries,
            )

    async def mirror_snapshot_release(
        self, *, snapshot_ref: str, run_id: str
    ) -> None:
        release_snapshot = getattr(self.registry_source, "release_snapshot", None)
        if callable(release_snapshot):
            await release_snapshot(snapshot_ref=snapshot_ref, run_id=run_id)

    async def mirror_snapshot_fingerprints(
        self,
        *,
        snapshot_ref: str,
        run_id: str,
        tool_spec_fingerprints: tuple[str, ...],
    ) -> None:
        lease_fingerprints = getattr(
            self.registry_source, "lease_snapshot_fingerprints", None
        )
        if callable(lease_fingerprints):
            await lease_fingerprints(
                snapshot_ref=snapshot_ref,
                run_id=run_id,
                tool_spec_fingerprints=tool_spec_fingerprints,
            )

    async def snapshot_and_acquire_lease(
        self,
        *,
        run_id: str,
        root_run_id: str,
        scope: CapabilityScope,
        owner_key: str | None = None,
    ) -> CapabilityCatalogSnapshot:
        async with self.publish_lock:
            async with self.catalog_gate.read(
                self._gate_keys(scope, owner_key)
            ):
                snapshot, entries = await self._snapshot_locked(scope)
                await self.store.acquire_snapshot_lease(
                    snapshot_ref=snapshot.snapshot_ref,
                    run_id=run_id,
                    root_run_id=root_run_id,
                    entries=entries,
                )
                try:
                    await self.mirror_snapshot_lease(
                        snapshot_ref=snapshot.snapshot_ref,
                        run_id=run_id,
                        entries=entries,
                    )
                except BaseException:
                    await self.store.release_snapshot_lease(
                        snapshot.snapshot_ref, run_id
                    )
                    raise
                return snapshot

    async def release_lease(self, snapshot_ref: str, run_id: str) -> int:
        async with self.publish_lock:
            released = await self.store.release_snapshot_lease(snapshot_ref, run_id)
            await self.mirror_snapshot_release(
                snapshot_ref=snapshot_ref, run_id=run_id
            )
            return released

    async def clone_snapshot_lease_and_commit(
        self,
        *,
        snapshot_ref: str,
        source_run_id: str,
        target_run_id: str,
        root_run_id: str,
        commit: Callable[[Callable[..., Awaitable[Any]]], Awaitable[Any]],
    ) -> Any:
        """Clone a parent's immutable snapshot in the child-command CAS."""

        async with self.publish_lock:
            fingerprints = await self.store.snapshot_lease_fingerprints(
                snapshot_ref=snapshot_ref,
                run_id=source_run_id,
            )
            await self.mirror_snapshot_fingerprints(
                snapshot_ref=snapshot_ref,
                run_id=target_run_id,
                tool_spec_fingerprints=fingerprints,
            )

            async def clone_in_transaction(db: Any, _intent: Any) -> None:
                cloned = await self.store.bind(db).clone_snapshot_lease(
                    snapshot_ref=snapshot_ref,
                    source_run_id=source_run_id,
                    target_run_id=target_run_id,
                    root_run_id=root_run_id,
                )
                if cloned != fingerprints:
                    raise RuntimeError(
                        "child snapshot lease differs from its mirrored parent"
                    )

            try:
                return await commit(clone_in_transaction)
            except BaseException:
                await self.mirror_snapshot_release(
                    snapshot_ref=snapshot_ref,
                    run_id=target_run_id,
                )
                raise

    async def release_run_leases(
        self,
        run_id: str,
        *,
        known_snapshot_refs: tuple[str, ...] = (),
    ) -> int:
        """Release every active immutable snapshot owned by exactly one run."""

        async with self.publish_lock:
            snapshot_refs = tuple(
                sorted(
                    {
                        *known_snapshot_refs,
                        *await self.store.active_snapshot_refs_for_run(run_id),
                    }
                )
            )
            released = 0
            for snapshot_ref in snapshot_refs:
                released += await self.store.release_snapshot_lease(
                    snapshot_ref, run_id
                )
                await self.mirror_snapshot_release(
                    snapshot_ref=snapshot_ref,
                    run_id=run_id,
                )
            return released

    async def reconcile_terminal_run_leases(self) -> int:
        """Recover the small crash window after terminal commit, before release."""

        async with self.publish_lock:
            owners = await self.store.terminal_snapshot_lease_owners()
            released = 0
            for snapshot_ref, run_id in owners:
                released += await self.store.release_snapshot_lease(
                    snapshot_ref, run_id
                )
                await self.mirror_snapshot_release(
                    snapshot_ref=snapshot_ref,
                    run_id=run_id,
                )
            return released

    async def version_can_be_collected(
        self, *, pack_id: str, version: str, manifest_hash: str
    ) -> bool:
        async with self.publish_lock:
            return not await self.store.version_has_active_lease(
                pack_id, version, manifest_hash
            )


__all__ = [
    "CapabilityEntrySource",
    "CapabilityHub",
    "CatalogRevisionSource",
    "RegistryCatalogSource",
    "SkillLoaderCatalogSource",
    "StaticCapabilityEntrySource",
    "StaticCatalogRevisionSource",
    "ToolRegistryCatalogSource",
]
