# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Registration, catalog leases, catalog compare-and-swap and the authority phase."""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace

from ..build_identity import (
    ManifestValidationError,
    authority_accepts_handler,
    authority_handler_ids,
    core_authority_for_tool,
    validate_core_registry_handler_set,
)
from .core import ToolRegistryCore
from .support import ToolCatalogSnapshot, ToolSpec, tool_spec_fingerprint


class ToolCatalogRevisionConflict(RuntimeError):
    """The registry changed before an atomic catalog mutation acquired its lock."""


class ToolCatalogMutationError(RuntimeError):
    """An atomic catalog mutation is malformed or conflicts with live specs."""


class ToolCatalogSnapshotUnavailable(ToolCatalogMutationError):
    """A durable snapshot references ToolSpecs absent from this process."""

    code = "tool_catalog_stale"


class RegistrationMixin(ToolRegistryCore):
    """Registration, catalog leases, catalog compare-and-swap and the authority phase."""

    __slots__ = ()

    def has(self, name: str) -> bool:
        """Return True iff a tool with this name is currently registered.

        Stubs.py 守卫模式：``if not registry.has(name): register(...)``
        防止 late-loaded stub 覆盖 already-registered 真实现。
        """
        with self._lock:
            return name in self._tools

    def dispatch_kind(self, name: str) -> str:
        """Return the host-only dispatch boundary for a registered tool."""

        with self._lock:
            spec = self._tools.get(name)
        if spec is None:
            raise KeyError(f"unknown tool: {name}")
        return spec.dispatch_kind

    def unregister(self, name: str) -> bool:
        """Remove a tool by name. Returns True if removed, False if
        the name was absent. Used by MCPManager to drop a server's
        tools on disconnect (P4-S9 task 14.5 + 14.6).
        """
        with self._lock:
            removed = self._tools.pop(name, None)
            if removed is not None:
                self._retire_spec_if_leased_locked(removed)
                self._catalog_revision += 1
            return removed is not None

    def publish_source_batch(
        self,
        *,
        source: str,
        registrations: Sequence[Callable[[], None]],
    ) -> ToolCatalogSnapshot:
        """Replace one source's complete Tool set with atomic visibility.

        Registration callbacks must call this registry's normal ``register``
        method.  The re-entrant registry lock prevents readers from observing
        a partially published server catalog; any callback failure restores
        the exact pre-publication state and revision.
        """

        if not source.startswith("mcp:"):
            raise ToolCatalogMutationError("batch source must be an MCP source")
        actions = tuple(registrations)
        if any(not callable(action) for action in actions):
            raise ToolCatalogMutationError("registrations must be callable")
        with self._lock:
            before_tools = dict(self._tools)
            before_retired = dict(self._retired_specs)
            before_revision = self._catalog_revision
            try:
                for name, spec in tuple(self._tools.items()):
                    if spec.source == source:
                        self._tools.pop(name)
                        self._retire_spec_if_leased_locked(spec)
                for action in actions:
                    action()
                if any(
                    spec.source != source and before_tools.get(name) is not spec
                    for name, spec in self._tools.items()
                ):
                    raise ToolCatalogMutationError("batch registration published a foreign source")
            except Exception:
                self._tools = before_tools
                self._retired_specs = before_retired
                self._catalog_revision = before_revision
                raise
            changed = self._tools != before_tools
            self._catalog_revision = before_revision + int(changed)
            specs = tuple(
                replace(
                    spec,
                    schema=copy.deepcopy(spec.schema),
                    requires_env=list(spec.requires_env),
                )
                for spec in self._tools.values()
            )
            return ToolCatalogSnapshot(self._catalog_revision, specs)

    def _fingerprint_is_leased_locked(self, fingerprint: str) -> bool:
        return any(
            fingerprint in fingerprints for fingerprints in self._snapshot_spec_leases.values()
        )

    def _retire_spec_if_leased_locked(self, spec: ToolSpec) -> None:
        fingerprint = tool_spec_fingerprint(spec)
        if self._fingerprint_is_leased_locked(fingerprint):
            self._retired_specs[fingerprint] = spec

    def _collect_unleased_retired_specs_locked(self) -> None:
        for fingerprint in tuple(self._retired_specs):
            if not self._fingerprint_is_leased_locked(fingerprint):
                self._retired_specs.pop(fingerprint, None)

    def lease_catalog_snapshot(
        self,
        *,
        snapshot_ref: str,
        run_id: str,
        tool_spec_fingerprints: Iterable[str],
    ) -> None:
        """Pin exact active/retired specs for one run snapshot.

        The durable lease lives in ``CapabilityStore``. This process-local
        mirror only keeps immutable handler objects reachable until the
        durable owner releases the same ``(snapshot_ref, run_id)`` lease.
        """

        if len(snapshot_ref) != 64 or any(
            character not in "0123456789abcdef" for character in snapshot_ref
        ):
            raise ToolCatalogMutationError("snapshot_ref must be a SHA-256 digest")
        if not isinstance(run_id, str) or not run_id:
            raise ToolCatalogMutationError("run_id is required")
        fingerprints = frozenset(str(item) for item in tool_spec_fingerprints)
        if any(
            len(item) != 64 or any(character not in "0123456789abcdef" for character in item)
            for item in fingerprints
        ):
            raise ToolCatalogMutationError("tool_spec_fingerprints must be SHA-256 digests")
        key = (snapshot_ref, run_id)
        with self._lock:
            existing = self._snapshot_spec_leases.get(key)
            if existing is not None:
                if existing != fingerprints:
                    raise ToolCatalogMutationError("snapshot lease identity changed")
                return
            available = {tool_spec_fingerprint(spec) for spec in self._tools.values()} | set(
                self._retired_specs
            )
            missing = sorted(fingerprints - available)
            if missing:
                raise ToolCatalogSnapshotUnavailable(
                    "snapshot references unavailable ToolSpecs: " + ",".join(missing)
                )
            self._snapshot_spec_leases[key] = fingerprints

    def release_catalog_snapshot(self, *, snapshot_ref: str, run_id: str) -> bool:
        """Release one process-local snapshot pin and collect retired specs."""

        with self._lock:
            removed = self._snapshot_spec_leases.pop((snapshot_ref, run_id), None)
            self._collect_unleased_retired_specs_locked()
            return removed is not None

    def retired_spec_fingerprints(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._retired_specs)

    def compare_and_swap_catalog(
        self,
        *,
        expected_revision: int,
        expected_fingerprints: Mapping[str, str | None],
        replacements: Sequence[ToolSpec],
    ) -> ToolCatalogSnapshot:
        """Apply one all-or-nothing catalog mutation under the registry lock.

        ``expected_fingerprints[name] is None`` means the name must be absent.
        A non-null value means the current spec must have that exact immutable
        fingerprint. Names present in the expectation map but absent from
        ``replacements`` are removed. Every replacement name must be fenced by
        the expectation map, so this API cannot silently overwrite unrelated
        registrations.
        """

        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise ToolCatalogMutationError("expected_revision must be non-negative")
        expected = dict(expected_fingerprints)
        replacement_by_name: dict[str, ToolSpec] = {}
        for spec in replacements:
            if not isinstance(spec, ToolSpec):
                raise ToolCatalogMutationError("replacements must contain ToolSpec")
            if not spec.name or spec.name in replacement_by_name:
                raise ToolCatalogMutationError(
                    f"duplicate or empty replacement name: {spec.name!r}"
                )
            if spec.name not in expected:
                raise ToolCatalogMutationError(
                    f"replacement {spec.name!r} has no expected current state"
                )
            schema_name = spec.schema.get("name")
            if isinstance(schema_name, str) and schema_name != spec.name:
                raise ToolCatalogMutationError(f"replacement schema name differs for {spec.name!r}")
            replacement_by_name[spec.name] = replace(
                spec,
                schema=copy.deepcopy(spec.schema),
                requires_env=list(spec.requires_env),
            )
        for name, fingerprint in expected.items():
            if not isinstance(name, str) or not name:
                raise ToolCatalogMutationError("expected names must be non-empty")
            if fingerprint is None and name not in replacement_by_name:
                raise ToolCatalogMutationError(f"absent expectation {name!r} has no replacement")
            if fingerprint is not None and (
                len(fingerprint) != 64
                or any(character not in "0123456789abcdef" for character in fingerprint)
            ):
                raise ToolCatalogMutationError(f"expected fingerprint is invalid for {name!r}")

        with self._lock:
            if self._catalog_revision != expected_revision:
                raise ToolCatalogRevisionConflict(
                    f"registry revision is {self._catalog_revision}, expected {expected_revision}"
                )
            for name, fingerprint in expected.items():
                current = self._tools.get(name)
                if fingerprint is None:
                    if current is not None:
                        raise ToolCatalogMutationError(f"tool {name!r} was expected to be absent")
                elif current is None or tool_spec_fingerprint(current) != fingerprint:
                    raise ToolCatalogMutationError(f"tool {name!r} fingerprint changed")

            proposed = dict(self._tools)
            for name in expected:
                proposed.pop(name, None)
            proposed.update(replacement_by_name)
            if proposed != self._tools:
                for name in expected:
                    current = self._tools.get(name)
                    successor = proposed.get(name)
                    if current is not None and (
                        successor is None
                        or tool_spec_fingerprint(successor) != tool_spec_fingerprint(current)
                    ):
                        self._retire_spec_if_leased_locked(current)
                self._tools = proposed
                self._catalog_revision += 1
            specs = tuple(
                replace(
                    spec,
                    schema=copy.deepcopy(spec.schema),
                    requires_env=list(spec.requires_env),
                )
                for spec in self._tools.values()
            )
            return ToolCatalogSnapshot(self._catalog_revision, specs)

    @property
    def authority_phase(self) -> str:
        with self._lock:
            return self._authority_phase

    def _restore_durable_authority_phase(
        self,
        *,
        expected_revision: int,
        target_phase: str,
        replacements: Iterable[ToolSpec],
    ) -> ToolCatalogSnapshot:
        """Restore a process-local authority phase already durable on disk.

        A fresh process always starts its in-memory Registry in ``legacy``.
        Durable Companion state can already be ahead of that process-local
        default, and its handlers must be installed before Harness recovery
        leases a catalog snapshot.  This transition therefore validates the
        exact phase delta without requiring unrelated core tools to have been
        registered yet.  The normal full-manifest validation still runs once
        Harness catalog composition is complete.

        This is intentionally private. Product code must expose a narrow
        restore function that constructs its replacement specs internally;
        callers must never be allowed to submit arbitrary ToolSpecs here.
        """

        if target_phase != "companion":
            raise ToolCatalogMutationError("durable authority restore only supports companion")
        expected_ids = authority_handler_ids(
            phase="legacy",
            include_planned=False,
        )
        target_ids = authority_handler_ids(
            phase=target_phase,
            include_planned=False,
        )
        addition_ids = target_ids - expected_ids
        removal_ids = expected_ids - target_ids
        replacement_by_name: dict[str, ToolSpec] = {}
        replacement_ids: set[str] = set()
        for spec in replacements:
            if not isinstance(spec, ToolSpec):
                raise ToolCatalogMutationError("replacements must contain ToolSpec")
            if spec.name in replacement_by_name or not spec.name:
                raise ToolCatalogMutationError(
                    f"duplicate or empty replacement name: {spec.name!r}"
                )
            authority = core_authority_for_tool(spec.name)
            if (
                authority is None
                or spec.source != "builtin"
                or not authority_accepts_handler(authority, spec.handler)
                or (
                    spec.context_handler is not None
                    and not authority_accepts_handler(authority, spec.context_handler)
                )
                or authority.handler_id != spec.stable_handler_id
                or spec.effect_class is not authority.effect.effect_class
                or spec.idempotency is not authority.effect.idempotency
                or spec.target_normalizer_version != authority.effect.target_normalizer_version
                or spec.execution_build_identity != authority.build
            ):
                raise ToolCatalogMutationError(
                    f"durable authority replacement metadata mismatch: {spec.name}"
                )
            replacement_by_name[spec.name] = replace(
                spec,
                schema=copy.deepcopy(spec.schema),
                requires_env=list(spec.requires_env),
            )
            replacement_ids.add(spec.stable_handler_id)
        if replacement_ids != addition_ids:
            raise ToolCatalogMutationError(
                "durable authority replacement set mismatch: "
                f"missing={sorted(addition_ids - replacement_ids)!r} "
                f"unused={sorted(replacement_ids - addition_ids)!r}"
            )

        with self._lock:
            if self._catalog_revision != expected_revision:
                raise ToolCatalogRevisionConflict(
                    f"registry revision is {self._catalog_revision}, expected {expected_revision}"
                )
            if self._authority_phase == target_phase:
                return ToolCatalogSnapshot(
                    self._catalog_revision,
                    tuple(
                        replace(
                            spec,
                            schema=copy.deepcopy(spec.schema),
                            requires_env=list(spec.requires_env),
                        )
                        for spec in self._tools.values()
                    ),
                )
            if self._authority_phase != "legacy":
                raise ToolCatalogMutationError(
                    f"registry authority phase is {self._authority_phase}, expected legacy"
                )
            current_ids = {
                spec.stable_handler_id
                for spec in self._tools.values()
                if spec.source == "builtin" and spec.stable_handler_id
            }
            unexpected = current_ids - expected_ids
            if unexpected:
                raise ToolCatalogMutationError(
                    f"legacy registry contains unexpected handlers: {sorted(unexpected)!r}"
                )
            if not removal_ids.issubset(current_ids):
                raise ToolCatalogMutationError(
                    "legacy authority handlers missing before durable restore: "
                    f"{sorted(removal_ids - current_ids)!r}"
                )
            proposed = {
                name: spec
                for name, spec in self._tools.items()
                if spec.stable_handler_id not in removal_ids
            }
            collisions = sorted(set(proposed).intersection(replacement_by_name))
            if collisions:
                raise ToolCatalogMutationError(
                    f"durable authority target names already exist: {collisions!r}"
                )
            proposed.update(replacement_by_name)
            for spec in self._tools.values():
                if spec.stable_handler_id in removal_ids:
                    self._retire_spec_if_leased_locked(spec)
            self._tools = proposed
            self._authority_phase = target_phase
            self._catalog_revision += 1
            specs = tuple(
                replace(
                    spec,
                    schema=copy.deepcopy(spec.schema),
                    requires_env=list(spec.requires_env),
                )
                for spec in self._tools.values()
            )
            return ToolCatalogSnapshot(self._catalog_revision, specs)

    def compare_and_swap_authority_phase(
        self,
        *,
        expected_revision: int,
        expected_phase: str,
        target_phase: str,
        replacements: Sequence[ToolSpec],
    ) -> ToolCatalogSnapshot:
        """Atomically project one complete checked-manifest authority phase.

        The caller must hold the product ingress/cutover gate.  This method
        owns only the Registry mutation: it proves the current phase is
        complete, removes phase-exclusive legacy handlers, installs the exact
        target additions, proves the resulting phase is complete, and then
        publishes the new catalog and phase in one lock acquisition.
        """

        if expected_phase not in {"legacy", "companion"}:
            raise ToolCatalogMutationError("unsupported expected authority phase")
        if target_phase not in {"legacy", "companion"}:
            raise ToolCatalogMutationError("unsupported target authority phase")
        if expected_phase == target_phase:
            raise ToolCatalogMutationError("authority phase transition must advance")
        if expected_phase != "legacy" or target_phase != "companion":
            raise ToolCatalogMutationError(
                "authority phase cannot roll back after companion cutover"
            )
        if not isinstance(expected_revision, int) or expected_revision < 0:
            raise ToolCatalogMutationError("expected_revision must be non-negative")

        expected_ids = authority_handler_ids(
            phase=expected_phase,
            include_planned=False,
        )
        target_ids = authority_handler_ids(
            phase=target_phase,
            include_planned=False,
        )
        addition_ids = target_ids - expected_ids
        removal_ids = expected_ids - target_ids
        replacement_by_name: dict[str, ToolSpec] = {}
        replacement_ids: set[str] = set()
        for spec in replacements:
            if not isinstance(spec, ToolSpec):
                raise ToolCatalogMutationError("replacements must contain ToolSpec")
            if spec.name in replacement_by_name or not spec.name:
                raise ToolCatalogMutationError(
                    f"duplicate or empty replacement name: {spec.name!r}"
                )
            replacement_by_name[spec.name] = replace(
                spec,
                schema=copy.deepcopy(spec.schema),
                requires_env=list(spec.requires_env),
            )
            replacement_ids.add(spec.stable_handler_id)
        if replacement_ids != addition_ids:
            raise ToolCatalogMutationError(
                "authority phase replacement set mismatch: "
                f"missing={sorted(addition_ids - replacement_ids)!r} "
                f"unused={sorted(replacement_ids - addition_ids)!r}"
            )

        with self._lock:
            if self._catalog_revision != expected_revision:
                raise ToolCatalogRevisionConflict(
                    f"registry revision is {self._catalog_revision}, expected {expected_revision}"
                )
            if self._authority_phase != expected_phase:
                raise ToolCatalogMutationError(
                    f"registry authority phase is {self._authority_phase}, "
                    f"expected {expected_phase}"
                )
            current_specs = tuple(self._tools.values())
            try:
                validate_core_registry_handler_set(
                    current_specs,
                    phase=expected_phase,
                    include_planned=False,
                )
            except ManifestValidationError as exc:
                raise ToolCatalogMutationError(
                    f"current authority phase is incomplete: {exc}"
                ) from exc

            proposed = {
                name: spec
                for name, spec in self._tools.items()
                if spec.stable_handler_id not in removal_ids
            }
            collisions = sorted(set(proposed).intersection(replacement_by_name))
            if collisions:
                raise ToolCatalogMutationError(
                    f"authority phase target names already exist: {collisions!r}"
                )
            proposed.update(replacement_by_name)
            try:
                validate_core_registry_handler_set(
                    proposed.values(),
                    phase=target_phase,
                    include_planned=False,
                )
            except ManifestValidationError as exc:
                raise ToolCatalogMutationError(
                    f"target authority phase is incomplete: {exc}"
                ) from exc

            for spec in self._tools.values():
                if spec.stable_handler_id in removal_ids:
                    self._retire_spec_if_leased_locked(spec)
            self._tools = proposed
            self._authority_phase = target_phase
            self._catalog_revision += 1
            specs = tuple(
                replace(
                    spec,
                    schema=copy.deepcopy(spec.schema),
                    requires_env=list(spec.requires_env),
                )
                for spec in self._tools.values()
            )
            return ToolCatalogSnapshot(self._catalog_revision, specs)
