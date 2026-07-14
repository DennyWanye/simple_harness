# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Request-scoped tool capability contracts for Context OS V1.

The objects in this module deliberately contain no handlers, credentials or
raw tool arguments.  A prepared set is the provider-neutral, immutable truth
for one agent request; provider adapters may only translate its exact schemas.
"""
from __future__ import annotations

import asyncio
import contextvars
import copy
import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Optional


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ToolExposurePolicy:
    direct: tuple[str, ...] = ()
    discoverable: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolExposureIntent:
    direct_selectors: tuple[str, ...] = ()
    discoverable_selectors: tuple[str, ...] = ()
    deny_selectors: tuple[str, ...] = ()
    required_direct_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolEligibilityContext:
    session_id: str
    request_id: str
    task_type: str
    mode: str = "companion"


@dataclass(frozen=True)
class ToolPolicySnapshot:
    disabled_toolsets: frozenset[str] = frozenset()
    schema_only_toolsets: frozenset[str] = frozenset()
    dangerous_allowlist: frozenset[str] = frozenset()
    fingerprint: str = ""

    @classmethod
    def from_config(cls, config: Any) -> "ToolPolicySnapshot":
        disabled = frozenset(getattr(config, "disabled_toolsets", ()) or ())
        schema_only = frozenset(
            getattr(config, "disabled_toolsets_schema_only", ()) or ()
        )
        dangerous = frozenset(
            getattr(config, "dangerous_tools_allowlist", ()) or ()
        )
        payload = {
            "disabled_toolsets": sorted(disabled),
            "schema_only_toolsets": sorted(schema_only),
            "dangerous_allowlist": sorted(dangerous),
        }
        return cls(disabled, schema_only, dangerous, canonical_hash(payload))


@dataclass(frozen=True)
class ToolCapabilityRef:
    capability_id: str
    name: str
    toolset: str
    source: str
    description: str
    schema_hash: str
    spec_version: str = "v1"
    permission_policy_version: str = "v1"
    permission_category: str = "read_file"
    dangerous: bool = False


@dataclass(frozen=True)
class PreparedToolCapability:
    ref: ToolCapabilityRef
    canonical_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        schema = copy.deepcopy(dict(self.canonical_schema))
        schema_hashes = {canonical_hash(schema)}
        if isinstance(schema.get("function"), dict):
            schema_hashes.add(canonical_hash(schema["function"]))
        if self.ref.schema_hash not in schema_hashes:
            raise ValueError(f"schema hash mismatch for {self.ref.name!r}")
        object.__setattr__(self, "canonical_schema", schema)

    def schema_copy(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self.canonical_schema))


@dataclass(frozen=True)
class ToolSelectionDecision:
    name: str
    disposition: str
    reason: str


@dataclass(frozen=True)
class ResolvedToolDraft:
    registry_revision: int
    direct: tuple[PreparedToolCapability, ...] = ()
    deferred: tuple[ToolCapabilityRef, ...] = ()
    conditional_direct: tuple[PreparedToolCapability, ...] = ()
    denied_names: tuple[str, ...] = ()
    policy_fingerprint: str = ""
    decisions: tuple[ToolSelectionDecision, ...] = ()
    conditional_name_map: tuple[tuple[str, str], ...] = ()

    def finalize(
        self,
        *,
        required_conditional_names: tuple[str, ...] = (),
        scope_id: Optional[str] = None,
    ) -> "PreparedToolSet":
        required = set(required_conditional_names)
        known = {cap.ref.name for cap in self.conditional_direct}
        unknown = required - known
        if unknown:
            raise ValueError(f"unknown conditional tools: {sorted(unknown)!r}")
        selected = tuple(
            cap for cap in self.conditional_direct if cap.ref.name in required
        )
        direct = _dedupe_capabilities((*self.direct, *selected))
        decisions = (*self.decisions, *(
            ToolSelectionDecision(cap.ref.name, "direct", "conditional_required")
            for cap in selected
        ))
        return PreparedToolSet.create(
            scope_id=scope_id,
            revision=1,
            registry_revision=self.registry_revision,
            direct=direct,
            deferred=self.deferred,
            activated=(),
            denied_names=self.denied_names,
            policy_fingerprint=self.policy_fingerprint,
            decisions=tuple(decisions),
        )


@dataclass(frozen=True)
class PreparedToolSet:
    scope_id: str
    revision: int
    registry_revision: int
    direct: tuple[PreparedToolCapability, ...] = ()
    deferred: tuple[ToolCapabilityRef, ...] = ()
    activated: tuple[PreparedToolCapability, ...] = ()
    denied_names: tuple[str, ...] = ()
    policy_fingerprint: str = ""
    schema_fingerprint: str = ""
    decisions: tuple[ToolSelectionDecision, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        scope_id: Optional[str],
        revision: int,
        registry_revision: int,
        direct: Iterable[PreparedToolCapability],
        deferred: Iterable[ToolCapabilityRef],
        activated: Iterable[PreparedToolCapability],
        denied_names: Iterable[str],
        policy_fingerprint: str,
        decisions: Iterable[ToolSelectionDecision],
    ) -> "PreparedToolSet":
        direct_tuple = _dedupe_capabilities(tuple(direct))
        activated_tuple = _dedupe_capabilities(tuple(activated))
        schemas = [cap.schema_copy() for cap in (*direct_tuple, *activated_tuple)]
        return cls(
            scope_id=scope_id or uuid.uuid4().hex,
            revision=revision,
            registry_revision=registry_revision,
            direct=direct_tuple,
            deferred=_dedupe_refs(tuple(deferred)),
            activated=activated_tuple,
            denied_names=tuple(dict.fromkeys(denied_names)),
            policy_fingerprint=policy_fingerprint,
            schema_fingerprint=canonical_hash(schemas),
            decisions=tuple(decisions),
        )

    def logical_schemas(self) -> tuple[dict[str, Any], ...]:
        return tuple(cap.schema_copy() for cap in (*self.direct, *self.activated))

    def has_direct(self, name: str) -> bool:
        return any(cap.ref.name == name for cap in (*self.direct, *self.activated))

    def capability(self, name: str) -> Optional[PreparedToolCapability]:
        return next(
            (cap for cap in (*self.direct, *self.activated) if cap.ref.name == name),
            None,
        )

    def activate(self, capability: PreparedToolCapability) -> "PreparedToolSet":
        if capability.ref.name in self.denied_names:
            raise ValueError("cannot activate denied capability")
        deferred = tuple(
            ref for ref in self.deferred if ref.capability_id != capability.ref.capability_id
        )
        activated = _dedupe_capabilities((*self.activated, capability))
        return PreparedToolSet.create(
            scope_id=self.scope_id,
            revision=self.revision + 1,
            registry_revision=self.registry_revision,
            direct=self.direct,
            deferred=deferred,
            activated=activated,
            denied_names=self.denied_names,
            policy_fingerprint=self.policy_fingerprint,
            decisions=(*self.decisions, ToolSelectionDecision(
                capability.ref.name, "activated", "explicit_activation"
            )),
        )


@dataclass(frozen=True)
class PreparedToolPayload:
    logical_schema_fingerprint: str
    adapter_id: str
    adapter_version: str
    tools: Any
    wire_payload_hash: str
    wire_tokens: int
    estimate_method: str


@dataclass(frozen=True)
class ToolExecutionContext:
    scope_id: str
    session_id: str
    request_id: str
    origin: str = "agent"
    policy_snapshot: Optional[ToolPolicySnapshot] = None


@dataclass(frozen=True)
class ToolActivationProposal:
    prepared_capability: PreparedToolCapability
    base_scope_revision: int
    nonce: str


_CURRENT_EXECUTION_CONTEXT: contextvars.ContextVar[Optional[ToolExecutionContext]] = (
    contextvars.ContextVar("deskpet_tool_execution_context", default=None)
)


def current_tool_execution_context() -> Optional[ToolExecutionContext]:
    return _CURRENT_EXECUTION_CONTEXT.get()


def set_tool_execution_context(context: ToolExecutionContext) -> contextvars.Token:
    return _CURRENT_EXECUTION_CONTEXT.set(context)


def reset_tool_execution_context(token: contextvars.Token) -> None:
    _CURRENT_EXECUTION_CONTEXT.reset(token)


@dataclass(frozen=True)
class ToolCapabilityScopeRecord:
    prepared: PreparedToolSet
    eligibility: ToolEligibilityContext
    session_id: str
    request_id: str
    expires_at: float
    snapshot_handle: Any = None


class ToolCapabilityScopeStore:
    """Bounded in-memory authority for one live AgentLoop run."""

    def __init__(self, *, ttl_seconds: float = 300.0, max_scopes: int = 256) -> None:
        self._ttl = max(1.0, float(ttl_seconds))
        self._max = max(1, int(max_scopes))
        self._records: dict[str, ToolCapabilityScopeRecord] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [key for key, value in self._records.items() if value.expires_at <= now]
        for key in expired:
            self._records.pop(key, None)
            self._locks.pop(key, None)

    def open(
        self,
        prepared: PreparedToolSet,
        eligibility: ToolEligibilityContext,
        *,
        snapshot_handle: Any = None,
    ) -> ToolCapabilityScopeRecord:
        self._purge_expired()
        if prepared.scope_id in self._records:
            raise ValueError("scope already exists")
        if len(self._records) >= self._max:
            oldest = min(self._records, key=lambda key: self._records[key].expires_at)
            self._records.pop(oldest, None)
            self._locks.pop(oldest, None)
        record = ToolCapabilityScopeRecord(
            prepared=prepared,
            eligibility=eligibility,
            session_id=eligibility.session_id,
            request_id=eligibility.request_id,
            expires_at=time.monotonic() + self._ttl,
            snapshot_handle=snapshot_handle,
        )
        self._records[prepared.scope_id] = record
        self._locks[prepared.scope_id] = asyncio.Lock()
        return record

    def get(
        self, scope_id: str, *, session_id: str, request_id: str
    ) -> Optional[ToolCapabilityScopeRecord]:
        self._purge_expired()
        record = self._records.get(scope_id)
        if record is None:
            return None
        if record.session_id != session_id or record.request_id != request_id:
            return None
        return record

    def lock_for(self, scope_id: str) -> asyncio.Lock:
        lock = self._locks.get(scope_id)
        if lock is None:
            raise KeyError(scope_id)
        return lock

    def commit_prevalidated(
        self,
        candidate: PreparedToolSet,
        *,
        snapshot_handle: Any = None,
    ) -> ToolCapabilityScopeRecord:
        current = self._records[candidate.scope_id]
        record = replace(
            current,
            prepared=candidate,
            expires_at=time.monotonic() + self._ttl,
            snapshot_handle=(
                snapshot_handle if snapshot_handle is not None else current.snapshot_handle
            ),
        )
        self._records[candidate.scope_id] = record
        return record

    def advance_snapshot_handle_prevalidated(self, scope_id: str, handle: Any) -> None:
        current = self._records[scope_id]
        self._records[scope_id] = replace(current, snapshot_handle=handle)

    def purge(self, scope_id: str) -> None:
        self._records.pop(scope_id, None)
        self._locks.pop(scope_id, None)

    def purge_session(self, session_id: str) -> None:
        for scope_id in [
            key for key, record in self._records.items() if record.session_id == session_id
        ]:
            self.purge(scope_id)


class ToolCapabilityBridgeService:
    """Search/describe/activate only within the current authorized scope."""

    def __init__(self, registry: Any, scopes: ToolCapabilityScopeStore) -> None:
        self._registry = registry
        self._scopes = scopes
        self._nonces: dict[str, tuple[str, int, str, float]] = {}

    def _current_record(self) -> tuple[ToolExecutionContext, ToolCapabilityScopeRecord]:
        context = current_tool_execution_context()
        if context is None:
            raise RuntimeError("capability_denied")
        record = self._scopes.get(
            context.scope_id,
            session_id=context.session_id,
            request_id=context.request_id,
        )
        if record is None:
            raise RuntimeError("capability_denied")
        return context, record

    def search(self, query: str, *, limit: int = 10, cursor: int = 0) -> dict[str, Any]:
        _, record = self._current_record()
        tokens = [part for part in (query or "").lower().split() if part]
        if not tokens:
            raise ValueError("query must be non-empty")
        matches = []
        for ref in record.prepared.deferred:
            haystack = f"{ref.name} {ref.description} {ref.toolset} {ref.source}".lower()
            if all(token in haystack for token in tokens):
                matches.append({
                    "capability_id": ref.capability_id,
                    "name": ref.name,
                    "description": ref.description,
                    "toolset": ref.toolset,
                    "source": ref.source,
                    "permission_category": ref.permission_category,
                    "dangerous": ref.dangerous,
                    "schema_hash": ref.schema_hash,
                })
        matches.sort(key=lambda item: item["name"])
        start = max(0, int(cursor))
        bounded = matches[start : start + max(1, min(int(limit), 10))]
        next_cursor = start + len(bounded) if start + len(bounded) < len(matches) else None
        return {"matches": bounded, "count": len(matches), "next_cursor": next_cursor}

    def describe(self, capability_id: str, *, _observe: bool = True) -> dict[str, Any]:
        _, record = self._current_record()
        ref = next(
            (item for item in record.prepared.deferred if item.capability_id == capability_id),
            None,
        )
        if ref is None:
            raise RuntimeError("capability_denied")
        policy = self._registry.read_policy_snapshot(strict=True)
        if policy.fingerprint != record.prepared.policy_fingerprint:
            raise RuntimeError("capability_stale")
        spec = self._registry.get(ref.name)
        if (
            spec is None
            or spec.schema_hash != ref.schema_hash
            or spec.spec_version != ref.spec_version
            or spec.permission_policy_version != ref.permission_policy_version
            or not spec.env_satisfied()
            or not spec.is_visible(record.eligibility)
        ):
            raise RuntimeError("capability_stale")
        hooks = getattr(self._registry, "context_os_e2e_hooks", None)
        if _observe and hooks is not None and spec.fixture_spec_hash:
            execution, _ = self._current_record()
            hooks.observe_describe(
                session_id=execution.session_id,
                request_id=execution.request_id,
                tool=ref.name,
                schema_hash=ref.schema_hash,
            )
        nonce = uuid.uuid4().hex
        self._nonces[nonce] = (
            record.prepared.scope_id,
            record.prepared.revision,
            capability_id,
            time.monotonic() + 60.0,
        )
        return {
            "capability_id": capability_id,
            "schema": {"type": "function", "function": copy.deepcopy(spec.schema)},
            "schema_hash": spec.schema_hash,
            "describe_nonce": nonce,
        }

    def activate(
        self, capability_id: str, schema_hash: str, describe_nonce: str
    ) -> ToolActivationProposal:
        _, record = self._current_record()
        nonce = self._nonces.pop(describe_nonce, None)
        if nonce is None:
            raise RuntimeError("activation_nonce_invalid")
        scope_id, revision, described_id, expires_at = nonce
        if (
            scope_id != record.prepared.scope_id
            or revision != record.prepared.revision
            or described_id != capability_id
            or expires_at <= time.monotonic()
        ):
            raise RuntimeError("activation_nonce_stale")
        ref = next(
            item
            for item in record.prepared.deferred
            if item.capability_id == capability_id
        )
        try:
            described = self.describe(capability_id, _observe=False)
        except RuntimeError as exc:
            # MCP EOF and the explicit catalog probe race each other during
            # disconnect.  Once a remote capability was successfully
            # described, both paths mean the same public contract: the
            # catalog authority changed before activation.  Keep that error
            # stable regardless of which invalidation wins the race.
            if str(exc) == "capability_stale" and ref.source.startswith("mcp:"):
                self._registry.invalidate_mcp_catalog_sources((ref.source,))
                raise RuntimeError("tool_catalog_stale") from exc
            raise
        # ``describe`` created a fresh nonce during the strict recheck; discard
        # it because activation consumes the original one-shot grant.
        self._nonces.pop(str(described["describe_nonce"]), None)
        if described["schema_hash"] != schema_hash:
            raise RuntimeError("capability_stale")
        hooks = getattr(self._registry, "context_os_e2e_hooks", None)
        spec = self._registry.get(ref.name)
        if hooks is not None and spec is not None and spec.fixture_spec_hash:
            remote_name = spec.fixture_remote_name or spec.name
            try:
                meta = hooks.catalog([remote_name])
            except RuntimeError as exc:
                # A disconnected/timeout catalog is stale authority, not a
                # transient permission success.  Invalidate the exact spec so
                # the next request must re-list and resolve a fresh epoch.
                self._registry.invalidate_mcp_catalog_sources((spec.source,))
                raise RuntimeError("tool_catalog_stale") from exc
            item = (meta.get("tools") or {}).get(remote_name) if isinstance(meta, dict) else None
            if (
                not isinstance(item, dict)
                or int(item.get("fixture_epoch", -1)) != spec.fixture_epoch
                or str(item.get("fixture_spec_hash", "")) != spec.fixture_spec_hash
                or str(item.get("fixture_spec_version", "")) != spec.fixture_spec_version
            ):
                self._registry.invalidate_mcp_catalog_sources((spec.source,))
                raise RuntimeError("tool_catalog_stale")
        # Test-only pre-activation stale check.  In production hooks is None,
        # so this remains the exact legacy execution path with zero I/O.
        self._registry.validate_prepared_tool_set(
            record.prepared,
            eligibility=record.eligibility,
        )
        return ToolActivationProposal(
            PreparedToolCapability(ref, described["schema"]),
            record.prepared.revision,
            describe_nonce,
        )


def selector_matches(selector: str, *, name: str, toolset: str, source: str) -> bool:
    if selector == "*":
        return True
    if selector.startswith("toolset:"):
        return toolset == selector.partition(":")[2]
    if selector == "source:builtin":
        return source == "builtin"
    if selector == "source:mcp:*":
        return source.startswith("mcp:")
    if selector == "source:plugin:*":
        return source.startswith("plugin:")
    if ":" in selector:
        raise ValueError(f"unknown tool selector: {selector!r}")
    return name == selector


class ToolCapabilityResolver:
    """Freeze one provider-neutral tool draft from one catalog snapshot."""

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def resolve_draft(
        self,
        intent: ToolExposureIntent,
        *,
        eligibility: ToolEligibilityContext,
        conditional_direct_names: tuple[str, ...] = (),
    ) -> ResolvedToolDraft:
        catalog = self._registry.catalog_snapshot()
        policy = self._registry.read_policy_snapshot(strict=True)
        eligible = self._registry.eligible_specs(
            context=eligibility,
            policy_snapshot=policy,
            catalog=catalog,
        )
        by_name = {spec.name: spec for spec in eligible}
        remote_aliases: dict[str, list[str]] = {}
        for spec in eligible:
            remote_name = str(getattr(spec, "fixture_remote_name", "") or "")
            if remote_name and remote_name != spec.name:
                remote_aliases.setdefault(remote_name, []).append(spec.name)
        bridge_names = {"tool_search", "tool_describe", "tool_activate"}

        def canonical_tool_name(requested_name: str) -> Optional[str]:
            """Resolve exact remote MCP names only when the alias is unique.

            MCP tools are registered under a server-qualified canonical name,
            while users and upstream planners commonly refer to the remote
            name advertised by that server.  Exact canonical names always win;
            ambiguous remote aliases intentionally remain unresolved.
            """

            if requested_name in by_name:
                return requested_name
            candidates = remote_aliases.get(requested_name, ())
            return candidates[0] if len(candidates) == 1 else None

        def matches(spec: Any, selectors: tuple[str, ...]) -> bool:
            return any(
                selector_matches(
                    selector,
                    name=spec.name,
                    toolset=spec.toolset,
                    source=spec.source,
                )
                for selector in selectors
            )

        denied: set[str] = {
            spec.name for spec in eligible if matches(spec, intent.deny_selectors)
        }
        direct_names = {
            spec.name
            for spec in eligible
            if spec.name not in bridge_names and matches(spec, intent.direct_selectors)
        }
        direct_names.update(intent.required_direct_names)
        conditional_name_map = tuple(
            (requested_name, canonical_name)
            for requested_name in conditional_direct_names
            if (canonical_name := canonical_tool_name(requested_name)) is not None
            # Explicitly naming a remote MCP tool must not bypass the
            # progressive-disclosure protocol.  Its schema stays deferred
            # until tool_search -> tool_describe -> tool_activate succeeds.
            # Built-in tools may still use the conditional-direct fast path.
            if not str(by_name[canonical_name].source).startswith("mcp:")
        )
        conditional_names = {canonical for _, canonical in conditional_name_map}
        deferred_names = {
            spec.name
            for spec in eligible
            if spec.name not in bridge_names
            and matches(spec, intent.discoverable_selectors)
        }
        direct_names -= denied
        deferred_names -= denied | direct_names | conditional_names
        if deferred_names:
            if bridge_names.issubset(by_name) and not (bridge_names & denied):
                direct_names.update(bridge_names)
            else:
                # A half discovery surface is unsafe and unusable.
                deferred_names.clear()

        missing_required = {
            name for name in intent.required_direct_names if name not in by_name
        }
        if missing_required:
            raise RuntimeError(f"required_tool_unavailable:{sorted(missing_required)!r}")

        refs = {name: self._ref(spec) for name, spec in by_name.items()}
        direct = tuple(
            self._prepared(by_name[name], refs[name])
            for name in by_name
            if name in direct_names
        )
        deferred = tuple(refs[name] for name in by_name if name in deferred_names)
        conditional = tuple(
            self._prepared(by_name[name], refs[name])
            for name in by_name
            if name in conditional_names and name not in denied
        )
        decisions: list[ToolSelectionDecision] = []
        for name in by_name:
            if name in denied:
                decisions.append(ToolSelectionDecision(name, "denied", "policy_deny"))
            elif name in direct_names:
                decisions.append(ToolSelectionDecision(name, "direct", "direct_selector"))
            elif name in conditional_names:
                decisions.append(
                    ToolSelectionDecision(name, "conditional_direct", "planner_requirement")
                )
            elif name in deferred_names:
                decisions.append(
                    ToolSelectionDecision(name, "deferred", "discoverable_selector")
                )
        return ResolvedToolDraft(
            registry_revision=catalog.revision,
            direct=direct,
            deferred=deferred,
            conditional_direct=conditional,
            denied_names=tuple(sorted(denied)),
            policy_fingerprint=policy.fingerprint,
            decisions=tuple(decisions),
            conditional_name_map=conditional_name_map,
        )

    @staticmethod
    def _ref(spec: Any) -> ToolCapabilityRef:
        description = str(spec.schema.get("description", ""))[:512]
        return ToolCapabilityRef(
            capability_id=f"{spec.source}:{spec.name}",
            name=spec.name,
            toolset=spec.toolset,
            source=spec.source,
            description=description,
            schema_hash=spec.schema_hash,
            spec_version=spec.spec_version,
            permission_policy_version=spec.permission_policy_version,
            permission_category=spec.permission_category,
            dangerous=spec.dangerous,
        )

    @staticmethod
    def _prepared(spec: Any, ref: ToolCapabilityRef) -> PreparedToolCapability:
        return PreparedToolCapability(
            ref=ref,
            canonical_schema={"type": "function", "function": copy.deepcopy(spec.schema)},
        )


def _dedupe_capabilities(
    capabilities: Iterable[PreparedToolCapability],
) -> tuple[PreparedToolCapability, ...]:
    out: dict[str, PreparedToolCapability] = {}
    for capability in capabilities:
        existing = out.get(capability.ref.name)
        if existing is not None and existing.ref.schema_hash != capability.ref.schema_hash:
            raise ValueError(f"conflicting schemas for {capability.ref.name!r}")
        out[capability.ref.name] = capability
    return tuple(out.values())


def _dedupe_refs(refs: Iterable[ToolCapabilityRef]) -> tuple[ToolCapabilityRef, ...]:
    out: dict[str, ToolCapabilityRef] = {}
    for ref in refs:
        out[ref.capability_id] = ref
    return tuple(out.values())
