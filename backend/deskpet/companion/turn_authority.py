"""Typed, host-only authority for one Companion product turn.

The authority deliberately has two phases:

``prepare_turn``
    Freezes the current owner, preference resolution, assembled Skill scopes,
    and at most one Personal Workflow candidate.  This phase may read the
    current Hub/Store projection.

``finalize_after_catalog``
    Consumes only the prepared value plus the *single* catalog lease capture
    produced by ProductVenue.  It never re-reads a live binding.  The method
    freezes the generation-0 growth snapshot and issues the final
    ``deskpet.companion.selection.v1`` payload.

Keeping those phases explicit prevents ProductTurnPreparer from inventing a
second catalog authority while still allowing Task 13's composition root to
inject the production services without a module singleton.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from deskpet.capabilities.contracts import CapabilityScope
from deskpet.capabilities.manifest import load_and_validate_pack
from deskpet.capabilities.store import CapabilityStore
from deskpet.companion.contracts import GrowthDependency, GrowthEvent, OwnerRef
from deskpet.companion.identity_gate import FrozenOwnerIdentity, IdentityReadyGate
from deskpet.companion.personal_workflow import (
    PERSONAL_WORKFLOW_INTERPRETER_ID,
    PERSONAL_WORKFLOW_INTERPRETER_VERSION,
)
from deskpet.companion.preferences import (
    ModelPreferenceTurnInterpreter,
    PreferenceResolution,
    PreferenceResolver,
)
from deskpet.companion.store import canonical_hash
from deskpet.execution.contracts import fingerprint_json
from deskpet.execution.provider_workloads import workload_context
from deskpet.workflows.definitions.personal_workflow import (
    PersonalWorkflowSelectionV1,
    personal_workflow_query_hash,
)


def _closed_json(value: Mapping[str, Any]) -> Mapping[str, Any]:
    cloned = json.loads(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    return MappingProxyType(cloned)


async def _await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _call_with_workload_context(call: Callable[..., Any], /, **kwargs: Any) -> Any:
    """Pass the new context only to collaborators that declare the contract."""

    parameters = inspect.signature(call).parameters
    if "workload_context" not in parameters:
        kwargs.pop("workload_context", None)
    return await _await(call(**kwargs))


def _provider_workload_context(turn: Any):
    session_id = str(getattr(turn, "session_id", "") or "")
    root_run_id = str(getattr(turn, "root_run_id", "") or "")
    request_id = str(getattr(turn, "request_id", "") or "")
    if not session_id or not root_run_id or not request_id:
        raise RuntimeError("companion model workload requires frozen request/root identity")
    return workload_context(
        "companion.preference_interpret",
        request_id=request_id,
        session_id=session_id,
        root_run_id=root_run_id,
        task_scope_id=(
            str(getattr(turn, "task_scope_id", "") or "") or None
        ),
    )


@dataclass(frozen=True, slots=True)
class FrozenPersonalWorkflowCandidateV1:
    """One exact Manager version selected before the catalog lease capture."""

    candidate_id: str
    owner_key: str
    pack_id: str
    version: str
    manifest_hash: str
    binding_generation: int
    workflow_id: str
    graph: Mapping[str, Any]
    graph_content_hash: str
    display_name: str
    description: str

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "owner_key",
            "pack_id",
            "version",
            "workflow_id",
            "display_name",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        for name in ("manifest_hash", "graph_content_hash"):
            value = str(getattr(self, name))
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if self.binding_generation < 1:
            raise ValueError("binding_generation must be positive")
        object.__setattr__(self, "graph", _closed_json(self.graph))


@dataclass(frozen=True, slots=True)
class CompanionTurnPreparationRequestV1:
    turn: Any
    services: Mapping[str, Any]
    current_message_id: int | None = None
    ingress_owner: FrozenOwnerIdentity | None = None
    selected_instruction_refs: tuple[Mapping[str, Any], ...] = ()
    skill_invocation_scopes: tuple[Mapping[str, Any], ...] = ()
    active_skill_scope_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        refs = tuple(_closed_json(item) for item in self.selected_instruction_refs)
        scopes = tuple(_closed_json(item) for item in self.skill_invocation_scopes)
        active = tuple(dict.fromkeys(str(item) for item in self.active_skill_scope_ids))
        scope_ids = {str(item.get("scope_id") or "") for item in scopes}
        if not set(active).issubset(scope_ids):
            raise ValueError("active Skill scope is absent from prepared scopes")
        object.__setattr__(self, "services", MappingProxyType(dict(self.services)))
        object.__setattr__(self, "selected_instruction_refs", refs)
        object.__setattr__(self, "skill_invocation_scopes", scopes)
        object.__setattr__(self, "active_skill_scope_ids", active)


@dataclass(frozen=True, slots=True)
class PreparedCompanionTurnV1:
    """Host-only result retained until ProductVenue owns the single lease."""

    preparation_id: str
    owner: OwnerRef
    owner_key: str
    binding_epoch: int
    preference_resolution: PreferenceResolution
    preference_prompt: str
    base_dependencies: tuple[GrowthDependency, ...]
    owner_memory_read_scopes: tuple[Mapping[str, Any], ...]
    selected_instruction_refs: tuple[Mapping[str, Any], ...]
    skill_invocation_scopes: tuple[Mapping[str, Any], ...]
    active_skill_scope_ids: tuple[str, ...]
    personal_workflow_candidate: FrozenPersonalWorkflowCandidateV1 | None
    personal_workflow_query_hash: str | None
    route_prompt: str

    def __post_init__(self) -> None:
        if (
            not self.preparation_id
            or not self.owner_key
            or self.binding_epoch < 1
            or self.preference_resolution.owner != self.owner
        ):
            raise ValueError("prepared Companion turn identity is invalid")
        if (self.personal_workflow_candidate is None) != (
            self.personal_workflow_query_hash is None
        ):
            raise ValueError("Personal Workflow candidate and query hash must align")
        object.__setattr__(
            self,
            "owner_memory_read_scopes",
            tuple(_closed_json(item) for item in self.owner_memory_read_scopes),
        )
        object.__setattr__(
            self,
            "selected_instruction_refs",
            tuple(_closed_json(item) for item in self.selected_instruction_refs),
        )
        object.__setattr__(
            self,
            "skill_invocation_scopes",
            tuple(_closed_json(item) for item in self.skill_invocation_scopes),
        )


@dataclass(frozen=True, slots=True)
class CompanionCatalogLeaseCaptureV1:
    """Only catalog facts accepted by ``finalize_after_catalog``."""

    run_catalog_content_stamp: str
    process_catalog_stamp: str
    catalog_snapshot_ref: str
    capability_lease_intent_ref: str
    exact_tools: tuple[Mapping[str, Any], ...]
    lease_entries: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        for name in (
            "run_catalog_content_stamp",
            "process_catalog_stamp",
            "catalog_snapshot_ref",
            "capability_lease_intent_ref",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        object.__setattr__(
            self, "exact_tools", tuple(_closed_json(item) for item in self.exact_tools)
        )
        object.__setattr__(
            self, "lease_entries", tuple(_closed_json(item) for item in self.lease_entries)
        )


@dataclass(frozen=True, slots=True)
class FinalizedCompanionTurnV1:
    preparation_id: str
    owner_key: str
    profile_generation: int
    binding_epoch: int
    product_snapshot_ref: str
    product_snapshot_hash: str
    growth_dependencies: tuple[GrowthDependency, ...]
    selection_payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if (
            not self.preparation_id
            or not self.owner_key
            or self.profile_generation < 1
            or self.binding_epoch < 1
            or not self.product_snapshot_ref
        ):
            raise ValueError("finalized Companion turn identity is invalid")
        object.__setattr__(self, "selection_payload", _closed_json(self.selection_payload))


class CompanionTurnAuthorityPort(Protocol):
    async def prepare_turn(
        self, request: CompanionTurnPreparationRequestV1
    ) -> PreparedCompanionTurnV1: ...

    async def finalize_after_catalog(
        self,
        prepared: PreparedCompanionTurnV1,
        capture: CompanionCatalogLeaseCaptureV1,
        *,
        run_id: str,
    ) -> FinalizedCompanionTurnV1: ...


class PersonalWorkflowCandidateSourcePort(Protocol):
    async def frozen_candidates(
        self,
        request: CompanionTurnPreparationRequestV1,
        identity: FrozenOwnerIdentity,
    ) -> tuple[FrozenPersonalWorkflowCandidateV1, ...]: ...


class PersonalWorkflowMatcherPort(Protocol):
    async def select_candidate(
        self,
        *,
        query: str,
        candidates: tuple[FrozenPersonalWorkflowCandidateV1, ...],
        workload_context: Any | None = None,
    ) -> str | None: ...


ScopeResolver = Callable[
    [CompanionTurnPreparationRequestV1, FrozenOwnerIdentity],
    CapabilityScope | Awaitable[CapabilityScope],
]


class HubStorePersonalWorkflowCandidateSource:
    """Read the same owner-scoped Store projection consumed by CapabilityHub."""

    def __init__(
        self,
        store: CapabilityStore,
        *,
        scope_resolver: ScopeResolver,
    ) -> None:
        self._store = store
        self._scope_resolver = scope_resolver

    async def frozen_candidates(
        self,
        request: CompanionTurnPreparationRequestV1,
        identity: FrozenOwnerIdentity,
    ) -> tuple[FrozenPersonalWorkflowCandidateV1, ...]:
        scope = await _await(self._scope_resolver(request, identity))
        if not isinstance(scope, CapabilityScope):
            raise TypeError("personal workflow scope resolver returned an invalid scope")
        entries = await self._store.visible_entries(
            scope, owner_key=identity.owner_key
        )
        candidates: list[FrozenPersonalWorkflowCandidateV1] = []
        for entry in entries:
            bindings = tuple(
                item
                for item in entry.bindings
                if item.active and item.generation > 0
            )
            if not bindings:
                continue
            selected_binding = max(
                bindings,
                key=lambda item: (
                    item.owner_key == identity.owner_key,
                    item.scope == "user",
                    item.generation,
                    item.binding_id,
                ),
            )
            record = await self._store.get_version(
                entry.version.capability_id,
                entry.version.version,
                entry.version.manifest_hash,
            )
            if record is None:
                raise RuntimeError("personal_workflow_version_missing")
            validated = await asyncio.to_thread(
                load_and_validate_pack, record.install_path
            )
            manifest = validated.manifest
            if (
                manifest.id != entry.version.capability_id
                or manifest.version != entry.version.version
                or manifest.manifest_hash != entry.version.manifest_hash
            ):
                raise RuntimeError("personal_workflow_manifest_identity_stale")
            for workflow in manifest.workflows:
                if (
                    workflow.interpreter_id
                    != PERSONAL_WORKFLOW_INTERPRETER_ID
                    or workflow.interpreter_version
                    != PERSONAL_WORKFLOW_INTERPRETER_VERSION
                ):
                    continue
                graph_path = Path(validated.root, workflow.path)
                try:
                    graph = json.loads(graph_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError("personal_workflow_graph_invalid") from exc
                if not isinstance(graph, dict):
                    raise RuntimeError("personal_workflow_graph_invalid")
                graph_hash = str(validated.file_hashes.get(workflow.path) or "")
                if not graph_hash:
                    raise RuntimeError(
                        "personal_workflow_graph_content_hash_missing"
                    )
                identity_payload = {
                    "owner_key": identity.owner_key,
                    "pack_id": manifest.id,
                    "version": manifest.version,
                    "manifest_hash": manifest.manifest_hash,
                    "binding_generation": selected_binding.generation,
                    "workflow_id": workflow.id,
                    "graph_content_hash": graph_hash,
                }
                candidates.append(
                    FrozenPersonalWorkflowCandidateV1(
                        candidate_id=(
                            "personal-workflow-candidate:"
                            + fingerprint_json(identity_payload)
                        ),
                        owner_key=identity.owner_key,
                        pack_id=manifest.id,
                        version=manifest.version,
                        manifest_hash=manifest.manifest_hash,
                        binding_generation=selected_binding.generation,
                        workflow_id=workflow.id,
                        graph=graph,
                        graph_content_hash=graph_hash,
                        display_name=str(graph.get("name") or workflow.id),
                        description=str(graph.get("description") or ""),
                    )
                )
        return tuple(sorted(candidates, key=lambda item: item.candidate_id))


def _skill_dependency(
    scope: Mapping[str, Any],
    *,
    binding_generation: int,
    catalog_stamp: str,
) -> GrowthDependency:
    return GrowthDependency(
        dependency_kind="capability_pack",
        dependency_id=str(scope["scope_id"]),
        content_hash=str(scope["scope_hash"]),
        pack_id=str(scope["pack_id"]),
        version=str(scope["version"]),
        manifest_hash=str(scope["manifest_hash"]),
        binding_generation=int(binding_generation),
        catalog_content_stamp=catalog_stamp,
    )


def _entry_identity(raw: Mapping[str, Any]) -> tuple[str, str, str, int]:
    item = dict(raw)
    envelope = item.get("canonical_envelope")
    if isinstance(envelope, Mapping):
        item = dict(envelope)
    version = item.get("version")
    binding = item.get("binding")
    selected_binding = item.get("selected_binding")
    if isinstance(version, Mapping):
        item.setdefault("pack_id", version.get("capability_id") or version.get("pack_id"))
        item["version"] = version.get("version")
        item.setdefault("manifest_hash", version.get("manifest_hash"))
    if isinstance(binding, Mapping):
        item.setdefault("binding_generation", binding.get("generation"))
    if isinstance(selected_binding, Mapping):
        item.setdefault(
            "binding_generation", selected_binding.get("generation")
        )
    return (
        str(item.get("pack_id") or item.get("capability_id") or ""),
        str(item.get("version") or ""),
        str(item.get("manifest_hash") or ""),
        int(item.get("binding_generation") or 0),
    )


def _personal_tool_binding(fact: Mapping[str, Any]) -> Mapping[str, Any]:
    item = dict(fact)
    policy = item.get("effect_policy")
    if not isinstance(policy, Mapping):
        raise RuntimeError("personal_workflow_tool_effect_policy_missing")
    kind = str(policy.get("kind") or "")
    if kind not in {"read_only", "idempotent_read", "deterministic_reusable"}:
        raise RuntimeError("personal_workflow_tool_effect_not_safe")
    build = item.get("execution_build_identity")
    if not isinstance(build, Mapping):
        raise RuntimeError("personal_workflow_tool_build_identity_missing")
    return {
        "stable_handler_id": str(item.get("stable_handler_id") or ""),
        "tool_name": str(item.get("name") or ""),
        "spec_ref": str(item.get("tool_spec_fingerprint") or ""),
        "schema_hash": str(item.get("schema_hash") or ""),
        "execution_build_identity": fingerprint_json(dict(build)),
        "effect_policy_hash": fingerprint_json(dict(policy)),
        "effect": "idempotent_read" if kind != "read_only" else "read_only",
        "idempotent": True,
    }


class CompanionTurnAuthority:
    """Production-ready authority with all dependencies supplied explicitly."""

    def __init__(
        self,
        *,
        identity_gate: IdentityReadyGate,
        preference_resolver: PreferenceResolver,
        preference_interpreter: ModelPreferenceTurnInterpreter | None = None,
        personal_workflow_source: PersonalWorkflowCandidateSourcePort | None = None,
        personal_workflow_matcher: PersonalWorkflowMatcherPort | None = None,
        preference_overrides: Callable[[Any, Mapping[str, Any]], Sequence[Any]]
        | None = None,
        relevant_preference_keys: Callable[
            [Any, Mapping[str, Any]], Sequence[str] | None
        ]
        | None = None,
        owner_memory_read_scope: Callable[
            [Any, Mapping[str, Any], FrozenOwnerIdentity], Any
        ]
        | None = None,
    ) -> None:
        if (personal_workflow_source is None) != (
            personal_workflow_matcher is None
        ):
            raise ValueError(
                "personal workflow source and matcher must be configured together"
            )
        if preference_interpreter is not None and preference_overrides is not None:
            raise ValueError(
                "model preference interpreter and legacy preference overrides "
                "cannot both be configured"
            )
        self._identity_gate = identity_gate
        self._preferences = preference_resolver
        self._preference_interpreter = preference_interpreter
        self._workflow_source = personal_workflow_source
        self._workflow_matcher = personal_workflow_matcher
        self._preference_overrides = preference_overrides
        self._relevant_preference_keys = relevant_preference_keys
        self._owner_memory_read_scope = owner_memory_read_scope

    async def prepare_turn(
        self, request: CompanionTurnPreparationRequestV1
    ) -> PreparedCompanionTurnV1:
        identity = self._identity_gate.freeze()
        if (
            request.ingress_owner is not None
            and request.ingress_owner != identity
        ):
            raise RuntimeError("companion_ingress_owner_fence_changed")
        turn = request.turn
        request_id = str(getattr(turn, "request_id", "") or "")
        if not request_id:
            raise RuntimeError("companion_turn_request_id_missing")
        keys = (
            self._relevant_preference_keys(turn, request.services)
            if self._relevant_preference_keys is not None
            else None
        )
        current = self._preferences.resolve(
            identity.owner,
            request_id=f"{request_id}:preference-assessment",
            relevant_keys=keys,
        )
        overrides = (
            tuple(self._preference_overrides(turn, request.services))
            if self._preference_overrides is not None
            else ()
        )
        if self._preference_interpreter is not None and getattr(
            turn, "venue", "text"
        ) != "background":
            message_id = request.current_message_id
            session_id = str(getattr(turn, "session_id", "") or "")
            if (
                isinstance(message_id, bool)
                or message_id is None
                or int(message_id) < 1
                or not session_id
            ):
                raise RuntimeError(
                    "companion_preference_source_message_missing"
                )
            user_text = str(getattr(turn, "text", "") or "")
            message_ref = f"message:{session_id}:{int(message_id)}"
            source_message_hash = canonical_hash(
                {
                    "schema_version": 1,
                    "source_message_ref": message_ref,
                    "user_text": user_text,
                }
            )
            interpreted = self._preferences.load_turn_decision(
                identity.owner,
                source_message_ref=message_ref,
                source_message_hash=source_message_hash,
            )
            if interpreted is None:
                assessment = (
                    self._preference_interpreter.assessment_payload(
                        user_text=user_text,
                        current_preferences=current.items,
                    )
                )
                interpreted = await self._preference_interpreter.interpret(
                    user_text=user_text,
                    current_preferences=current.items,
                    workload_context=_provider_workload_context(turn),
                )
                # Provider/parse failures are intentionally not receipts.
                # The turn can proceed without a preference mutation and the
                # exact source message remains safe to retry after restart.
                if not interpreted.retryable_failure:
                    interpreted = self._preferences.settle_turn_decision(
                        identity.owner,
                        source_message_ref=message_ref,
                        source_message_hash=source_message_hash,
                        assessment_input_hash=canonical_hash(assessment),
                        decision=interpreted,
                    )
            overrides = interpreted.overrides()
            if interpreted.has_observation:
                task_scope_id = str(
                    getattr(turn, "task_scope_id", "")
                    or getattr(turn, "root_run_id", "")
                    or ""
                )
                if not task_scope_id:
                    raise RuntimeError(
                        "companion_preference_source_message_missing"
                    )
                source_ref = (
                    f"preference-observation:{message_ref}:"
                    f"{interpreted.preference_key}"
                )
                event_id = "preference:" + canonical_hash(
                    {
                        "schema_version": 1,
                        "owner": {
                            "profile_id": identity.owner.profile_id,
                            "profile_generation": (
                                identity.owner.profile_generation
                            ),
                        },
                        "source_ref": source_ref,
                        "preference_key": interpreted.preference_key,
                        "preference_value": interpreted.durable_value,
                        "signal_kind": interpreted.signal_kind,
                    }
                )
                self._preferences.record(
                    GrowthEvent(
                        owner=identity.owner,
                        event_id=event_id,
                        source_kind="preference_observation",
                        source_ref=source_ref,
                        context_key=f"task:{task_scope_id}",
                        root_run_id=str(
                            getattr(turn, "root_run_id", "")
                            or request_id
                        ),
                        reason_code="model_preference_observed",
                        payload={
                            "interpreter": "model_preference_turn_v1",
                            "decision_receipt_ref": message_ref,
                            "decision_receipt_hash": canonical_hash(
                                interpreted.to_dict()
                            ),
                            "decision_reason": interpreted.reason_code,
                        },
                    ),
                    preference_key=str(interpreted.preference_key),
                    value=interpreted.durable_value,
                    signal_kind=str(interpreted.signal_kind),
                )
        resolution = self._preferences.resolve(
            identity.owner,
            request_id=request_id,
            overrides=overrides,
            relevant_keys=keys,
        )
        # PreferenceResolver adds ``resolution.dependencies`` itself during
        # finalization.  This collection contains only the additional frozen
        # dependencies to avoid duplicating preference rows.
        dependencies: list[GrowthDependency] = []
        memory_scopes: tuple[Mapping[str, Any], ...] = ()
        if self._owner_memory_read_scope is not None:
            scope = await _await(
                self._owner_memory_read_scope(
                    turn,
                    request.services,
                    identity,
                )
            )
            # The callback is installed before Task 13 so the same Harness
            # composition can observe the fenced authority cutover without a
            # rebuild.  While legacy still owns growth reads it deliberately
            # returns None: capturing a Companion scope here would create an
            # owner binding before Companion becomes authoritative.
            if scope is not None:
                if (
                    scope.profile_id != identity.owner.profile_id
                    or scope.profile_generation
                    != identity.owner.profile_generation
                ):
                    raise RuntimeError("owner_memory_read_scope_owner_mismatch")
                payload = {**scope.to_dict(), "scope_ref": scope.scope_ref}
                memory_scopes = (payload,)
                dependencies.append(
                    GrowthDependency(
                        dependency_kind="memory_scope",
                        dependency_id=scope.scope_ref,
                        content_hash=scope.scope_hash,
                        version="v1",
                        catalog_content_stamp=scope.session_set_hash,
                    )
                )

        selected: FrozenPersonalWorkflowCandidateV1 | None = None
        query_hash: str | None = None
        if self._workflow_source is not None:
            candidates = await self._workflow_source.frozen_candidates(
                request, identity
            )
            assert self._workflow_matcher is not None
            selected_id = await _call_with_workload_context(
                self._workflow_matcher.select_candidate,
                query=str(getattr(turn, "text", "") or ""),
                candidates=candidates,
                workload_context=_provider_workload_context(turn),
            )
            if selected_id is not None:
                selected = next(
                    (
                        item
                        for item in candidates
                        if item.candidate_id == str(selected_id)
                    ),
                    None,
                )
                if selected is None:
                    raise RuntimeError("personal_workflow_matcher_selected_unknown")
                query_hash = personal_workflow_query_hash(
                    str(getattr(turn, "text", "") or "")
                )
        # 2026-09-09: the model-facing spawn tool was removed, so a matched
        # Personal Workflow no longer produces route text. Selection and the
        # query-hash alignment invariant below are kept unchanged (Slice 2b).
        route_prompt = ""
        preparation_payload = {
            "owner_key": identity.owner_key,
            "profile_generation": identity.owner.profile_generation,
            "binding_epoch": identity.binding_epoch,
            "request_id": request_id,
            "preference_snapshot_hash": resolution.snapshot_hash,
            "skill_scope_ids": list(request.active_skill_scope_ids),
            "personal_candidate_id": (
                None if selected is None else selected.candidate_id
            ),
        }
        return PreparedCompanionTurnV1(
            preparation_id=(
                "prepared-companion-turn:" + fingerprint_json(preparation_payload)
            ),
            owner=identity.owner,
            owner_key=identity.owner_key,
            binding_epoch=identity.binding_epoch,
            preference_resolution=resolution,
            preference_prompt=resolution.prompt_block(),
            base_dependencies=tuple(dependencies),
            owner_memory_read_scopes=memory_scopes,
            selected_instruction_refs=request.selected_instruction_refs,
            skill_invocation_scopes=request.skill_invocation_scopes,
            active_skill_scope_ids=request.active_skill_scope_ids,
            personal_workflow_candidate=selected,
            personal_workflow_query_hash=query_hash,
            route_prompt=route_prompt,
        )

    async def finalize_after_catalog(
        self,
        prepared: PreparedCompanionTurnV1,
        capture: CompanionCatalogLeaseCaptureV1,
        *,
        run_id: str,
    ) -> FinalizedCompanionTurnV1:
        """Finalize from frozen values only; never consult IdentityGate/Store bindings."""

        if not run_id.strip():
            raise ValueError("run_id is required")
        entries = {_entry_identity(item): item for item in capture.lease_entries}
        dependencies = list(prepared.base_dependencies)
        for scope in prepared.skill_invocation_scopes:
            identity_prefix = (
                str(scope.get("pack_id") or ""),
                str(scope.get("version") or ""),
                str(scope.get("manifest_hash") or ""),
            )
            matches = [
                (identity, entry)
                for identity, entry in entries.items()
                if identity[:3] == identity_prefix and identity[3] > 0
            ]
            if len(matches) != 1:
                raise RuntimeError("prepared_skill_catalog_binding_mismatch")
            dependencies.append(
                _skill_dependency(
                    scope,
                    binding_generation=matches[0][0][3],
                    catalog_stamp=capture.run_catalog_content_stamp,
                )
            )

        personal_payload = None
        candidate = prepared.personal_workflow_candidate
        if candidate is not None:
            candidate_identity = (
                candidate.pack_id,
                candidate.version,
                candidate.manifest_hash,
                candidate.binding_generation,
            )
            lease_entry = entries.get(candidate_identity)
            if lease_entry is None:
                raise RuntimeError("personal_workflow_catalog_binding_mismatch")
            exact_by_name = {
                str(item.get("name") or ""): item for item in capture.exact_tools
            }
            raw_nodes = candidate.graph.get("nodes")
            if not isinstance(raw_nodes, list):
                raise RuntimeError("personal_workflow_graph_invalid")
            tool_names = tuple(
                sorted(
                    {
                        str((node.get("config") or {}).get("tool_name") or "")
                        for node in raw_nodes
                        if isinstance(node, Mapping)
                        and node.get("type") == "tool_call"
                    }
                    - {""}
                )
            )
            tool_bindings = {
                name: _personal_tool_binding(exact_by_name[name])
                for name in tool_names
                if name in exact_by_name
            }
            if set(tool_bindings) != set(tool_names):
                raise RuntimeError("personal_workflow_tool_absent_from_catalog")
            effect_topology = {
                "schema_version": 1,
                "nodes": [
                    {
                        "node_id": str(node.get("id") or ""),
                        "tool_name": str(
                            (node.get("config") or {}).get("tool_name") or ""
                        ),
                        "effect_policy_hash": tool_bindings[
                            str((node.get("config") or {}).get("tool_name") or "")
                        ]["effect_policy_hash"],
                    }
                    for node in raw_nodes
                    if isinstance(node, Mapping)
                    and node.get("type") == "tool_call"
                ],
            }
            selection = PersonalWorkflowSelectionV1.issue(
                owner_key=prepared.owner_key,
                pack_id=candidate.pack_id,
                version=candidate.version,
                manifest_hash=candidate.manifest_hash,
                binding_generation=candidate.binding_generation,
                graph=copy.deepcopy(dict(candidate.graph)),
                query_hash=str(prepared.personal_workflow_query_hash),
                run_catalog_content_stamp=capture.run_catalog_content_stamp,
                lease_entries=(lease_entry,),
                effect_topology=effect_topology,
                tool_bindings=tool_bindings,
            )
            personal_payload = selection.to_child_payload()
            dependencies.append(
                GrowthDependency(
                    dependency_kind="personal_workflow",
                    dependency_id=selection.selection_id,
                    content_hash=selection.selection_fingerprint,
                    pack_id=candidate.pack_id,
                    version=candidate.version,
                    manifest_hash=candidate.manifest_hash,
                    binding_generation=candidate.binding_generation,
                    catalog_content_stamp=capture.run_catalog_content_stamp,
                    effect_hash=fingerprint_json(effect_topology),
                )
            )

        snapshot = self._preferences.freeze_run_snapshot(
            prepared.preference_resolution,
            run_id=run_id,
            additional_dependencies=tuple(dependencies),
            owner_memory_read_scopes=prepared.owner_memory_read_scopes,
        )
        selection_payload = {
            "companion_snapshot_ref": snapshot.snapshot_id,
            "owner_key": prepared.owner_key,
            "selected_instruction_refs": [
                copy.deepcopy(dict(item))
                for item in prepared.selected_instruction_refs
            ],
            "skill_invocation_scopes": [
                copy.deepcopy(dict(item))
                for item in prepared.skill_invocation_scopes
            ],
            "active_skill_scope_ids": list(prepared.active_skill_scope_ids),
            "personal_workflow_selection": personal_payload,
        }
        return FinalizedCompanionTurnV1(
            preparation_id=prepared.preparation_id,
            owner_key=prepared.owner_key,
            profile_generation=prepared.owner.profile_generation,
            binding_epoch=prepared.binding_epoch,
            product_snapshot_ref=snapshot.snapshot_id,
            product_snapshot_hash=snapshot.snapshot_hash,
            growth_dependencies=(
                *prepared.preference_resolution.dependencies,
                *dependencies,
            ),
            selection_payload=selection_payload,
        )


__all__ = [
    "CompanionCatalogLeaseCaptureV1",
    "CompanionTurnAuthority",
    "CompanionTurnAuthorityPort",
    "CompanionTurnPreparationRequestV1",
    "FinalizedCompanionTurnV1",
    "FrozenPersonalWorkflowCandidateV1",
    "HubStorePersonalWorkflowCandidateSource",
    "PersonalWorkflowCandidateSourcePort",
    "PersonalWorkflowMatcherPort",
    "PreparedCompanionTurnV1",
]
