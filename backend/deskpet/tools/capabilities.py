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
import re
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


def canonical_deferred_capability_id(
    capability_id: str,
    deferred: Iterable[ToolCapabilityRef],
) -> str:
    """Resolve an exact capability ID or one unambiguous provider tool name.

    Unqualified names are accepted by the Context OS bridge so models may copy
    the provider-visible tool name.  Qualified IDs remain exact-match only.
    Keeping this normalization shared lets durable replay validate the same
    identity that activation validated, without weakening nonce/schema checks.
    """

    requested = str(capability_id or "").strip()
    if not requested:
        raise RuntimeError("capability_denied")
    candidates = tuple(deferred)
    if ":" in requested:
        if any(item.capability_id == requested for item in candidates):
            return requested
        raise RuntimeError("capability_denied")
    matches = tuple(
        item.capability_id
        for item in candidates
        if item.name == requested
    )
    if len(matches) != 1:
        raise RuntimeError("capability_denied")
    return matches[0]


@dataclass(frozen=True)
class PreparedToolCapability:
    ref: ToolCapabilityRef
    canonical_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        schema = copy.deepcopy(dict(self.canonical_schema))
        schema_hashes = {canonical_hash(schema)}
        if isinstance(schema.get("function"), dict):
            schema_hashes.add(canonical_hash(schema["function"]))
            if isinstance(schema["function"].get("parameters"), dict):
                # SDK ProviderToolSpec fingerprints the exact input schema,
                # while the capability bridge wraps it in the provider-neutral
                # function envelope. Both representations refer to the same
                # frozen schema and must preserve that canonical hash.
                schema_hashes.add(canonical_hash(schema["function"]["parameters"]))
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
class ToolEffectClassification:
    """Hash-covered host classification for one frozen tool definition."""

    name: str
    schema_hash: str
    stable_handler_id: str
    effect_class: str
    idempotency: str
    target_normalizer_version: str
    disposition: str

    def __post_init__(self) -> None:
        if not self.name or not self.effect_class or not self.idempotency:
            raise ValueError("effect classification identity is incomplete")
        if not self.target_normalizer_version:
            raise ValueError("target_normalizer_version is required")
        if self.disposition not in {"visible", "confirm_only", "excluded"}:
            raise ValueError("invalid effect classification disposition")

    def fingerprint_payload(self) -> dict[str, str]:
        return {
            "name": self.name,
            "schema_hash": self.schema_hash,
            "stable_handler_id": self.stable_handler_id,
            "effect_class": self.effect_class,
            "idempotency": self.idempotency,
            "target_normalizer_version": self.target_normalizer_version,
            "disposition": self.disposition,
        }


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
    # Host-owned action policy.  Empty defaults preserve v1 snapshots.
    confirm_only_names: tuple[str, ...] = ()
    effect_policy_version: str = ""
    effect_policy_hash: str = ""
    effect_classifications: tuple[ToolEffectClassification, ...] = ()

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
        confirm_only_names: Iterable[str] = (),
        effect_policy_version: str = "",
        effect_policy_hash: str = "",
        effect_classifications: Iterable[ToolEffectClassification] = (),
    ) -> "PreparedToolSet":
        direct_tuple = _dedupe_capabilities(tuple(direct))
        activated_tuple = _dedupe_capabilities(tuple(activated))
        schemas = [cap.schema_copy() for cap in (*direct_tuple, *activated_tuple)]
        visible_names = {
            cap.ref.name for cap in (*direct_tuple, *activated_tuple)
        }
        confirm_only = tuple(sorted(dict.fromkeys(confirm_only_names)))
        unknown_confirm_only = set(confirm_only) - visible_names
        if unknown_confirm_only:
            raise ValueError(
                f"unknown confirm-only tools: {sorted(unknown_confirm_only)!r}"
            )
        classifications = tuple(effect_classifications)
        classification_names = [item.name for item in classifications]
        if len(set(classification_names)) != len(classification_names):
            raise ValueError("effect classifications cannot contain duplicate tools")
        if bool(effect_policy_version) != bool(effect_policy_hash):
            raise ValueError("effect policy version and hash must be set together")
        extended_fingerprint = bool(
            confirm_only
            or effect_policy_version
            or effect_policy_hash
            or classifications
        )
        schema_fingerprint = (
            canonical_hash(
                {
                    "schemas": schemas,
                    "confirm_only_names": list(confirm_only),
                    "effect_policy_version": effect_policy_version,
                    "effect_policy_hash": effect_policy_hash,
                    "effect_classifications": [
                        item.fingerprint_payload() for item in classifications
                    ],
                }
            )
            if extended_fingerprint
            else canonical_hash(schemas)
        )
        return cls(
            scope_id=scope_id or uuid.uuid4().hex,
            revision=revision,
            registry_revision=registry_revision,
            direct=direct_tuple,
            deferred=_dedupe_refs(tuple(deferred)),
            activated=activated_tuple,
            denied_names=tuple(dict.fromkeys(denied_names)),
            policy_fingerprint=policy_fingerprint,
            schema_fingerprint=schema_fingerprint,
            decisions=tuple(decisions),
            confirm_only_names=confirm_only,
            effect_policy_version=effect_policy_version,
            effect_policy_hash=effect_policy_hash,
            effect_classifications=classifications,
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
        if self.effect_policy_hash and not any(
            item.name == capability.ref.name
            for item in self.effect_classifications
        ):
            raise ValueError("effect_policy_reclassification_required")
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
            confirm_only_names=self.confirm_only_names,
            effect_policy_version=self.effect_policy_version,
            effect_policy_hash=self.effect_policy_hash,
            effect_classifications=self.effect_classifications,
        )

    def with_effect_policy(
        self,
        *,
        visible_names: Iterable[str],
        confirm_only_names: Iterable[str],
        effect_policy_version: str,
        effect_policy_hash: str,
        effect_classifications: Iterable[ToolEffectClassification],
    ) -> "PreparedToolSet":
        """Return the policy-filtered immutable set.

        The policy may remove tools (reflection/evaluation) but cannot add a
        name that was absent from the captured set.
        """

        visible = frozenset(str(item) for item in visible_names)
        current = {
            cap.ref.name for cap in (*self.direct, *self.activated)
        }
        unknown = visible - current
        if unknown:
            raise ValueError(f"effect policy added unknown tools: {sorted(unknown)!r}")
        return PreparedToolSet.create(
            scope_id=self.scope_id,
            revision=self.revision,
            registry_revision=self.registry_revision,
            direct=tuple(cap for cap in self.direct if cap.ref.name in visible),
            deferred=tuple(ref for ref in self.deferred if ref.name in visible),
            activated=tuple(
                cap for cap in self.activated if cap.ref.name in visible
            ),
            denied_names=self.denied_names,
            policy_fingerprint=self.policy_fingerprint,
            decisions=self.decisions,
            confirm_only_names=confirm_only_names,
            effect_policy_version=effect_policy_version,
            effect_policy_hash=effect_policy_hash,
            effect_classifications=effect_classifications,
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
    """Trusted host values passed beside, never inside, model arguments.

    The first five fields retain the Context OS v1 constructor contract.  The
    remaining fields are populated by the harness ``HostContextFactory`` for
    the prepared-only execution path.  They deliberately contain identifiers
    and immutable plans only -- never live services or credentials.
    """

    scope_id: str
    session_id: str
    request_id: str
    origin: str = "agent"
    policy_snapshot: Optional[ToolPolicySnapshot] = None
    root_run_id: str = ""
    parent_run_id: Optional[str] = None
    turn_id: str = ""
    venue: str = "text"
    workspace: Optional[str] = None
    write_scope_root: Optional[str] = None
    capability_hash: str = ""
    scope_hash: str = ""
    provider_plan: tuple[str, ...] = ()
    run_id: str = ""
    command_id: str = ""
    call_id: str = ""
    effect_id: str = ""
    trace_id: str = ""
    owner_key: str = ""
    profile_generation: int = 0
    binding_epoch: int = 0
    capability_snapshot_ref: str = ""
    active_skill_scope_ids: tuple[str, ...] = ()
    effective_skill_tool_ref_hashes: tuple[str, ...] = ()
    effective_skill_tool_refs_hash: str = ""


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
        self._pins: dict[str, int] = {}

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, value in self._records.items()
            if value.expires_at <= now and self._pins.get(key, 0) <= 0
        ]
        for key in expired:
            self._records.pop(key, None)
            self._locks.pop(key, None)
            self._pins.pop(key, None)

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
            evictable = [
                key for key in self._records if self._pins.get(key, 0) <= 0
            ]
            if not evictable:
                raise RuntimeError("capability_scope_capacity_exhausted")
            oldest = min(
                evictable, key=lambda key: self._records[key].expires_at
            )
            self._records.pop(oldest, None)
            self._locks.pop(oldest, None)
            self._pins.pop(oldest, None)
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
        self._pins.pop(prepared.scope_id, None)
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

    def pin(
        self, scope_id: str, *, session_id: str, request_id: str
    ) -> Optional[ToolCapabilityScopeRecord]:
        """Keep a valid scope alive while one authorized effect is in flight."""
        self._purge_expired()
        record = self._records.get(scope_id)
        if record is None:
            return None
        if record.session_id != session_id or record.request_id != request_id:
            return None
        self._pins[scope_id] = self._pins.get(scope_id, 0) + 1
        return record

    def unpin(self, scope_id: str, *, session_id: str, request_id: str) -> bool:
        """Release one in-flight effect lease and restart the orphan TTL."""
        record = self._records.get(scope_id)
        if record is None:
            self._pins.pop(scope_id, None)
            return False
        if record.session_id != session_id or record.request_id != request_id:
            return False
        count = self._pins.get(scope_id, 0)
        if count <= 1:
            self._pins.pop(scope_id, None)
            self._records[scope_id] = replace(
                record, expires_at=time.monotonic() + self._ttl
            )
        else:
            self._pins[scope_id] = count - 1
        return True

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
        self._pins.pop(scope_id, None)

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
        self._nonces: dict[str, tuple[str, int, str]] = {}

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
        normalized_query = re.sub(
            r"[_:./\\-]+", " ", str(query or "").strip().lower()
        )
        tokens = tuple(
            dict.fromkeys(
                re.findall(r"[a-z0-9]+|[\u3400-\u9fff]+", normalized_query)
            )
        )
        if not tokens:
            raise ValueError("query must be non-empty")
        matches: list[tuple[int, float, str, dict[str, Any]]] = []
        for ref in record.prepared.deferred:
            normalized_name = re.sub(
                r"[_:./\\-]+", " ", str(ref.name).lower()
            )
            haystack = " ".join(
                (
                    normalized_name,
                    str(ref.description).lower(),
                    str(ref.toolset).lower(),
                    str(ref.source).lower(),
                )
            )
            matched = sum(token in haystack for token in tokens)
            if matched:
                exact_name = int(normalized_query == normalized_name)
                payload = {
                    "capability_id": ref.capability_id,
                    "name": ref.name,
                    "description": ref.description,
                    "toolset": ref.toolset,
                    "source": ref.source,
                    "permission_category": ref.permission_category,
                    "dangerous": ref.dangerous,
                    "schema_hash": ref.schema_hash,
                }
                matches.append(
                    (
                        exact_name,
                        matched / len(tokens),
                        ref.name,
                        payload,
                    )
                )
        matches.sort(key=lambda item: (-item[0], -item[1], item[2]))
        start = max(0, int(cursor))
        bounded = matches[start : start + max(1, min(int(limit), 10))]
        next_cursor = start + len(bounded) if start + len(bounded) < len(matches) else None
        return {
            "matches": [item[3] for item in bounded],
            "count": len(matches),
            "next_cursor": next_cursor,
        }

    def suggestions(self, capability_id: str, *, limit: int = 3) -> list[dict[str, Any]]:
        """Return request-scoped alternatives for a guessed capability id."""

        requested = str(capability_id or "").strip()
        suffix = requested.rsplit(":", 1)[-1]
        query = re.sub(r"[_./\\-]+", " ", suffix).strip()
        if not query:
            return []
        return list(self.search(query, limit=limit)["matches"])

    @staticmethod
    def _canonical_deferred_id(
        capability_id: str, record: ToolCapabilityScopeRecord
    ) -> str:
        return canonical_deferred_capability_id(
            capability_id,
            record.prepared.deferred,
        )

    def describe(self, capability_id: str, *, _observe: bool = True) -> dict[str, Any]:
        _, record = self._current_record()
        capability_id = self._canonical_deferred_id(capability_id, record)
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
        )
        # Nonces are one-shot and bound to an exact request scope, revision,
        # and capability.  Their validity follows that live scope instead of
        # imposing a separate model-thinking deadline.  Keep the in-memory
        # cache bounded for descriptions that are never activated.
        while len(self._nonces) > 1024:
            self._nonces.pop(next(iter(self._nonces)))
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
        capability_id = self._canonical_deferred_id(capability_id, record)
        nonce = self._nonces.pop(describe_nonce, None)
        if nonce is None:
            raise RuntimeError("activation_nonce_invalid")
        scope_id, revision, described_id = nonce
        if (
            scope_id != record.prepared.scope_id
            or revision != record.prepared.revision
            or described_id != capability_id
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
    if selector == "source:capability:*":
        return source.startswith("capability:")
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
            # A literal tool name in user text is only a planner hint, never
            # an authority override.  Keep denied names out of the map as
            # well as conditional_direct; otherwise the planner asks
            # finalize() to require a capability that policy already removed
            # and the whole request fails instead of continuing with the
            # visible workflow/profile surface.
            if canonical_name not in denied
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
