"""Production composition for model-authored Companion growth candidates.

The model supplies semantics, instruction bytes, and an independent comparison.
All owner identity, binding fences, immutable package identities, evaluation
permits, activation decisions, and lifecycle calls remain host-owned.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from deskpet.capabilities.contracts import OwnerScopeKey
from deskpet.harness.contracts import HostExtensionRefV1, PreparedRunContextV1
from deskpet.tools.registry import tool_spec_fingerprint

from .activation import ActivationDecisionCoordinator
from .build_admission import (
    CandidateDraftReceiptExpectationV1,
    CandidateDraftReceiptV1,
)
from .candidate_builder import (
    CandidateBuildProductV1,
    CandidateFileInputV1,
    CandidateSeedInputV1,
    DeterministicCandidateBuilder,
)
from .candidate_composition import (
    CandidateCompositionRequestV1,
    CandidateCompositionService,
)
from .candidate_receipts import (
    SqliteCandidateDraftMaterialQuery,
    SqliteCandidateDraftReceiptQuery,
)
from .contracts import (
    CandidateMode,
    EvaluationCaseLaunch,
    MutationAction,
    MutationRequest,
    OwnerRef,
)
from .evaluation import (
    EvaluationCaseComparisonV1,
    EvaluationIdentityV1,
    EvaluationPermitIssuer,
    ImmutableEvaluationGate,
    IndependentEvaluator,
)
from .growth import (
    CandidateBindingFenceV1,
    GrowthTargetIdentityV1,
    StructuredGrowthProposalV1,
)
from .reflection_postprocessor import (
    LiveGrowthTargetFactsV1,
    LiveReflectionEvidenceV1,
    ReflectionResultPostprocessor,
)
from .risk import EffectTopologyDiffV1
from .risk_facts import (
    CandidateRiskFactsError,
    parse_skill_frontmatter_bytes,
)
from .run_adapter import (
    BackgroundRunCanonicalResultV1,
    BackgroundRunDurableResultV1,
    CompanionPreparedRunFacts,
)
from .store import canonical_hash, canonical_json


def _json_object(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(code)
    cloned = json.loads(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )
    if not isinstance(cloned, dict):
        raise ValueError(code)
    return cloned


def _model_json_object(text: str, code: str) -> dict[str, Any]:
    """Decode the model's JSON object while tolerating one Markdown fence.

    The semantic contract remains strict after decoding.  This only accepts
    the common transport wrapper produced by chat models; it does not infer,
    repair, or merge candidate content.
    """

    candidate = text.strip()
    if candidate.startswith("```"):
        first_line_end = candidate.find("\n")
        if first_line_end < 0 or not candidate.endswith("```"):
            raise ValueError(code)
        opening = candidate[3:first_line_end].strip().lower()
        if opening not in {"", "json"}:
            raise ValueError(code)
        candidate = candidate[first_line_end + 1 : -3].strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError(code) from exc
    return _json_object(value, code)


def _semantic_replan_attempt(value: object, *, maximum: int) -> int:
    if type(value) is not int or value < 0 or value > maximum:
        raise ValueError("semantic_replan_attempt_invalid")
    return value


def _job_payload(store: Any, owner: OwnerRef, job_id: str) -> dict[str, Any]:
    row = store.get_job(owner, job_id=job_id)
    payload = json.loads(str(row["payload_json"]))
    if not isinstance(payload, dict):
        raise ValueError("companion_growth_job_payload_invalid")
    return payload


def _owner_key(owner: OwnerRef) -> str:
    return f"companion:{owner.profile_id}:{owner.profile_generation}"


def _reminder_draft_request(
    request_payload: Mapping[str, Any],
) -> tuple[str, str, str, tuple[str, ...]]:
    """Validate the frozen, host-authored reminder draft request."""

    if (
        request_payload.get("companion_stage") != "reminder_draft"
        or request_payload.get("operation") != "prepare_reminder_draft"
        or request_payload.get("external_send_allowed") is not False
    ):
        raise ValueError("reminder_draft_request_invalid")
    reminder_id = str(request_payload.get("reminder_id") or "").strip()
    occurrence_id = str(request_payload.get("occurrence_id") or "").strip()
    reminder_text = str(request_payload.get("reminder_text") or "").strip()
    raw_refs = request_payload.get("source_message_refs")
    raw_scopes = request_payload.get("allowed_grant_scopes")
    if (
        not reminder_id
        or not occurrence_id
        or not reminder_text
        or isinstance(raw_refs, (str, bytes))
        or not isinstance(raw_refs, Sequence)
        or isinstance(raw_scopes, (str, bytes))
        or not isinstance(raw_scopes, Sequence)
    ):
        raise ValueError("reminder_draft_request_invalid")
    source_refs = tuple(str(item).strip() for item in raw_refs)
    if (
        not source_refs
        or any(not item for item in source_refs)
        or len(set(source_refs)) != len(source_refs)
        or tuple(str(item) for item in raw_scopes)
        != ("read", "draft", "reversible_local")
    ):
        raise ValueError("reminder_draft_request_invalid")
    return reminder_id, occurrence_id, reminder_text, source_refs


def _evaluation_baseline_fields(
    proposal: StructuredGrowthProposalV1,
) -> dict[str, object]:
    """Project the proposal fence into Store's frozen baseline contract."""

    source = proposal.source_fence
    if source is None:
        if CandidateMode(proposal.candidate_mode) is not CandidateMode.GENESIS:
            raise ValueError("evaluation_source_baseline_missing")
        absent_hash = canonical_hash(None)
        return {
            "baseline_kind": "capability_absent_v1",
            "old_snapshot_hash": absent_hash,
            "absent_baseline_ref": "capability-absent-v1",
            "absent_baseline_hash": absent_hash,
        }
    if source.expected_absent:
        raise ValueError("evaluation_source_baseline_absent")
    return {
        "baseline_kind": "source_pack",
        "old_snapshot_hash": canonical_hash(source.to_dict()),
        "source_owner_key": source.owner_key,
        "source_scope": source.scope,
        "source_scope_key": source.scope_key,
        "source_pack_id": source.pack_id,
        "source_version": source.version,
        "source_manifest_hash": source.manifest_hash,
        "source_binding_generation": source.binding_generation,
    }


@dataclass(frozen=True, slots=True)
class FirstPartySkillTemplateV1:
    target: GrowthTargetIdentityV1
    candidate_mode: CandidateMode
    source_fence: CandidateBindingFenceV1 | None
    target_fence: CandidateBindingFenceV1
    relative_path: str
    source_markdown: str
    manifest_template: Mapping[str, Any]
    allowed_tools: tuple[str, ...]


def _source_risk_refs(
    template: FirstPartySkillTemplateV1,
    tool_manifest: Mapping[str, Mapping[str, object]],
    *,
    effect_topology: Mapping[str, object] | None = None,
) -> tuple[str, ...]:
    """Freeze source ToolSpec and package-effect refs for non-expansion checks."""

    effects = (
        template.manifest_template.get("effects", ())
        if effect_topology is None
        else effect_topology.get("effects", ())
    )
    if not isinstance(effects, (list, tuple)) or any(
        not isinstance(item, str) or not item for item in effects
    ):
        raise ValueError("source_effect_topology_invalid")
    refs = {
        str(item["spec_ref"])
        for item in tool_manifest.values()
    }
    refs.update(
        f"package-effect:{ordinal}:{effect}"
        for ordinal, effect in enumerate(effects)
    )
    return tuple(sorted(refs))


_PACK_EFFECT_BY_TOOL_EFFECT = {
    "read_only": "read_only",
    "draft_only": "staged_file",
    "reversible_local": "staged_file",
    "external_send": "opaque_manual",
    "destructive": "opaque_manual",
    "payment": "opaque_manual",
    "credential": "opaque_manual",
    "privacy": "opaque_manual",
    "unknown": "opaque_manual",
}


def _canonical_string_list(value: object, code: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(code)
    return tuple(sorted(item.strip() for item in value))


def _manifest_allowed_tools(
    manifest: Mapping[str, Any],
    *,
    relative_path: str,
) -> tuple[str, ...]:
    entries = manifest.get("entries")
    skills = entries.get("skills") if isinstance(entries, Mapping) else None
    if not isinstance(skills, list):
        raise ValueError("candidate_manifest_skill_entry_invalid")
    rows = [
        row
        for row in skills
        if isinstance(row, Mapping) and row.get("path") == relative_path
    ]
    if len(rows) != 1:
        raise ValueError("candidate_manifest_skill_entry_invalid")
    return _canonical_string_list(
        rows[0].get("allowed_tools", []),
        "candidate_manifest_allowed_tools_invalid",
    )


def _tool_effect_topology(
    manifest: Mapping[str, Any],
    *,
    tool_registry: Any,
    allowed_tools: Sequence[str],
) -> tuple[dict[str, Any], tuple[str, ...], tuple[str, ...]]:
    """Derive manifest permissions/effects and exact runtime topology."""

    raw_permissions = manifest.get("permissions", [])
    raw_effects = manifest.get("effects", [])
    base_permissions = _canonical_string_list(
        raw_permissions, "candidate_manifest_permissions_invalid"
    )
    base_effects = _canonical_string_list(
        raw_effects, "candidate_manifest_effects_invalid"
    )
    permissions = set(base_permissions)
    tool_effects: set[str] = set()
    for name in allowed_tools:
        spec = tool_registry.get(name) if tool_registry is not None else None
        if spec is None:
            raise ValueError(f"candidate_tool_missing:{name}")
        permissions.add(str(spec.permission_category))
        tool_effects.add(str(spec.effect_class.value))
    pack_effects = {
        _PACK_EFFECT_BY_TOOL_EFFECT.get(effect, "opaque_manual")
        for effect in tool_effects
    }
    pack_effects.update(base_effects)
    topology_effects = set(tool_effects)
    implied_pack_effects = {
        _PACK_EFFECT_BY_TOOL_EFFECT.get(effect, "opaque_manual")
        for effect in tool_effects
    }
    topology_effects.update(
        effect for effect in base_effects if effect not in implied_pack_effects
    )
    return (
        {"effects": sorted(topology_effects)},
        tuple(sorted(permissions)),
        tuple(sorted(pack_effects)),
    )


def _candidate_manifest_template(
    template: FirstPartySkillTemplateV1,
    *,
    allowed_tools: tuple[str, ...],
    tool_registry: Any,
) -> tuple[dict[str, Any], tuple[str, ...], dict[str, Any]]:
    manifest = _json_object(
        template.manifest_template,
        "candidate_manifest_template_invalid",
    )
    entries = manifest.get("entries")
    skills = entries.get("skills") if isinstance(entries, dict) else None
    if not isinstance(skills, list):
        raise ValueError("candidate_manifest_skill_entry_invalid")
    matched = 0
    for row in skills:
        if isinstance(row, dict) and row.get("path") == template.relative_path:
            row["allowed_tools"] = list(allowed_tools)
            matched += 1
    if matched != 1:
        raise ValueError("candidate_manifest_skill_entry_invalid")
    topology, permissions, pack_effects = _tool_effect_topology(
        manifest,
        tool_registry=tool_registry,
        allowed_tools=allowed_tools,
    )
    manifest["permissions"] = list(permissions)
    manifest["effects"] = list(pack_effects)
    return manifest, permissions, topology


def _candidate_manifest_and_skill_tools(
    package: Any,
    *,
    relative_path: str,
) -> tuple[dict[str, Any], tuple[str, ...], str]:
    manifest_blobs = [
        blob for blob in package.blobs if blob.blob_kind == "manifest"
    ]
    file_blobs = {
        item.relative_path: next(
            (
                blob.payload
                for blob in package.blobs
                if blob.blob_id == item.blob_id
            ),
            None,
        )
        for item in package.files
    }
    if len(manifest_blobs) != 1 or file_blobs.get(relative_path) is None:
        raise ValueError("candidate_immutable_skill_missing")
    try:
        manifest = json.loads(manifest_blobs[0].payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("candidate_manifest_invalid") from exc
    if not isinstance(manifest, dict):
        raise ValueError("candidate_manifest_invalid")
    manifest_tools = _manifest_allowed_tools(
        manifest,
        relative_path=relative_path,
    )
    skill_bytes = file_blobs[relative_path]
    assert isinstance(skill_bytes, bytes)
    try:
        frontmatter_tools, _body = parse_skill_frontmatter_bytes(
            skill_bytes, relative_path
        )
        markdown = skill_bytes.decode("utf-8")
    except (CandidateRiskFactsError, UnicodeDecodeError) as exc:
        raise ValueError("candidate_skill_invalid") from exc
    if frontmatter_tools != manifest_tools:
        raise ValueError("candidate_allowed_tools_mismatch")
    return manifest, manifest_tools, markdown


class FirstPartyGrowthTargetResolver:
    """Resolve model semantic names against the frozen Platform inventory."""

    def __init__(self, *, projection: Any, capability_platform: Any) -> None:
        self._projection = projection
        self._platform = capability_platform

    @property
    def catalog(self) -> tuple[dict[str, str], ...]:
        return tuple(
            {
                "target_kind": "skill",
                "stable_name": item.skill_id,
                "pack_id": item.pack_id,
            }
            for item in self._projection.inventory
        )

    async def __call__(
        self,
        owner: OwnerRef,
        target_or_kind: GrowthTargetIdentityV1 | str,
        name_or_mode: str | CandidateMode,
    ) -> LiveGrowthTargetFactsV1:
        if isinstance(target_or_kind, GrowthTargetIdentityV1):
            kind = target_or_kind.kind.value
            stable_name = target_or_kind.stable_name
            expected_target = target_or_kind
            expected_mode = CandidateMode(name_or_mode)
        else:
            kind = str(target_or_kind)
            stable_name = str(name_or_mode)
            expected_target = None
            expected_mode = None
        if kind != "skill" or not self._projection.contains(stable_name):
            raise ValueError("growth_target_not_in_frozen_catalog")
        item = next(
            current
            for current in self._projection.inventory
            if current.skill_id == stable_name
        )
        target = GrowthTargetIdentityV1(
            kind="skill",
            target_id=item.skill_id,
            stable_name=item.skill_id,
            pack_id=item.pack_id,
        )
        if expected_target is not None and expected_target != target:
            raise ValueError("growth_target_identity_stale")
        user_key = OwnerScopeKey(
            _owner_key(owner),
            "user",
            owner.profile_id,
        )
        builtin_key = OwnerScopeKey("builtin", "builtin", "builtin")
        snapshot = await self._platform.read_detail_snapshot(
            (user_key, builtin_key)
        )
        user_binding = next(
            (
                row
                for row in snapshot.bindings
                if row.active
                and row.capability_id == item.pack_id
                and row.owner_key == user_key.owner_key
                and row.scope == user_key.scope
                and row.scope_key == user_key.scope_key
            ),
            None,
        )
        builtin_binding = next(
            (
                row
                for row in snapshot.bindings
                if row.active
                and row.capability_id == item.pack_id
                and row.owner_key == builtin_key.owner_key
                and row.scope == builtin_key.scope
                and row.scope_key == builtin_key.scope_key
            ),
            None,
        )
        if user_binding is not None:
            mode = CandidateMode.UPDATE
            source = CandidateBindingFenceV1(
                owner_key=user_binding.owner_key,
                scope=user_binding.scope,
                scope_key=user_binding.scope_key,
                pack_id=item.pack_id,
                expected_absent=False,
                binding_generation=user_binding.generation,
                version=user_binding.version,
                manifest_hash=user_binding.manifest_hash,
            )
            target_fence = source
        elif builtin_binding is not None:
            mode = CandidateMode.BUILTIN_OVERRIDE
            source = CandidateBindingFenceV1(
                owner_key=builtin_binding.owner_key,
                scope=builtin_binding.scope,
                scope_key=builtin_binding.scope_key,
                pack_id=item.pack_id,
                expected_absent=False,
                binding_generation=builtin_binding.generation,
                version=builtin_binding.version,
                manifest_hash=builtin_binding.manifest_hash,
            )
            target_fence = CandidateBindingFenceV1(
                owner_key=_owner_key(owner),
                scope="user",
                scope_key=owner.profile_id,
                pack_id=item.pack_id,
                expected_absent=True,
                binding_generation=0,
            )
        else:
            raise ValueError("growth_target_has_no_active_source_binding")
        if expected_mode is not None and expected_mode is not mode:
            raise ValueError("growth_target_candidate_mode_stale")
        return LiveGrowthTargetFactsV1(
            target=target,
            candidate_mode=mode,
            source_fence=source,
            target_fence=target_fence,
        )

    async def template(
        self,
        owner: OwnerRef,
        proposal: StructuredGrowthProposalV1,
    ) -> FirstPartySkillTemplateV1:
        if proposal.target is None:
            raise ValueError("candidate_target_required")
        facts = await self(
            owner,
            proposal.target,
            CandidateMode(proposal.candidate_mode),
        )
        item = next(
            current
            for current in self._projection.inventory
            if current.skill_id == proposal.target.stable_name
        )
        manifest = json.loads(
            (item.pack_root / "deskpet-pack.json").read_text(encoding="utf-8")
        )
        if not isinstance(manifest, dict):
            raise ValueError("frozen_skill_manifest_invalid")
        for derived in ("version", "source", "files"):
            manifest.pop(derived, None)
        relative_path = item.skill_path.relative_to(item.pack_root).as_posix()
        return FirstPartySkillTemplateV1(
            target=facts.target,
            candidate_mode=CandidateMode(facts.candidate_mode),
            source_fence=facts.source_fence,
            target_fence=facts.target_fence,
            relative_path=relative_path,
            source_markdown=item.skill_path.read_text(encoding="utf-8"),
            manifest_template=manifest,
            allowed_tools=tuple(item.allowed_tools),
        )


@dataclass(frozen=True, slots=True)
class CandidateBuildTerminalExtension:
    """Build and persist immutable candidate bytes in the Run terminal UoW."""

    execution_run_id: str
    permit: Any
    proposal: StructuredGrowthProposalV1
    template: FirstPartySkillTemplateV1
    created_at: str
    tool_registry: Any | None = None

    @property
    def descriptor(self) -> HostExtensionRefV1:
        facts = {
            "schema": "companion-candidate-terminal-v1",
            "execution_run_id": self.execution_run_id,
            "permit_id": self.permit.permit_id,
            "permit_hash": self.permit.permit_hash,
            "proposal_hash": self.proposal.proposal_hash,
            "target_fence": self.template.target_fence.to_dict(),
        }
        return HostExtensionRefV1(
            kind="deskpet.companion.candidate_terminal.v1",
            ref=f"candidate-terminal:{self.permit.build_id}",
            content_hash=canonical_hash(facts),
        )

    def product_and_receipt(
        self,
        structured: Mapping[str, Any],
    ) -> tuple[CandidateBuildProductV1, CandidateDraftReceiptV1]:
        required = {
            "schema_version",
            "skill_markdown",
            "allowed_tools",
            "rationale",
            "self_checks",
        }
        if set(structured) != required or structured["schema_version"] != 1:
            raise ValueError("candidate_model_result_schema_invalid")
        markdown = structured["skill_markdown"]
        allowed_tools = _canonical_string_list(
            structured["allowed_tools"],
            "candidate_model_allowed_tools_invalid",
        )
        checks = structured["self_checks"]
        if (
            not isinstance(markdown, str)
            or not markdown.strip()
            or not isinstance(structured["rationale"], str)
            or not str(structured["rationale"]).strip()
            or not isinstance(checks, list)
            or not checks
            or not all(isinstance(item, str) and item.strip() for item in checks)
        ):
            raise ValueError("candidate_model_result_invalid")
        try:
            frontmatter_tools, _body = parse_skill_frontmatter_bytes(
                markdown.encode("utf-8"),
                self.template.relative_path,
            )
        except CandidateRiskFactsError as exc:
            raise ValueError(exc.code) from exc
        if frontmatter_tools != allowed_tools:
            raise ValueError("candidate_allowed_tools_mismatch")
        manifest, permissions, effect_topology = _candidate_manifest_template(
            self.template,
            allowed_tools=allowed_tools,
            tool_registry=self.tool_registry,
        )
        product = DeterministicCandidateBuilder().build(
            CandidateSeedInputV1(
                owner_key=self.permit.owner_key,
                target=self.template.target,
                candidate_mode=self.template.candidate_mode,
                source_fence=self.template.source_fence,
                target_fence=self.template.target_fence,
                files=(
                    CandidateFileInputV1(
                        self.template.relative_path,
                        markdown.encode("utf-8"),
                    ),
                ),
                manifest_template=manifest,
                permissions=permissions,
                effect_topology=effect_topology,
            )
        )
        receipt = CandidateDraftReceiptV1.issue_from_host(
            builder_launch_id=self.permit.builder_launch_id
            if hasattr(self.permit, "builder_launch_id")
            else "",
            child_run_id=self.permit.child_run_id
            if hasattr(self.permit, "child_run_id")
            else "",
            child_start_hash=self.permit.child_start_hash
            if hasattr(self.permit, "child_start_hash")
            else "",
            proposal_ref=self.permit.proposal_ref,
            proposal_hash=self.permit.proposal_hash,
            evidence_set_hash=self.permit.evidence_set_hash,
            target_fence_hash=self.permit.target_fence_hash,
            validated_draft_hash=product.package.candidate_content_hash,
            manifest_hash=product.package.candidate_manifest_hash,
            archive_hash=product.package.archive_hash,
            file_set_hash=product.file_set_hash,
            effect_topology_hash=product.package.effect_topology_hash,
        )
        return product, receipt

    @staticmethod
    def _structured(terminal_event: Any) -> Mapping[str, Any]:
        payload = getattr(terminal_event, "payload", None)
        if not isinstance(payload, Mapping):
            raise ValueError("candidate_terminal_payload_invalid")
        value: object = payload.get("structured_result")
        if not isinstance(value, Mapping):
            text = payload.get("text", payload.get("content"))
            if not isinstance(text, str):
                raise ValueError("candidate_terminal_text_missing")
            value = _model_json_object(
                text,
                "candidate_terminal_json_invalid",
            )
        return _json_object(value, "candidate_terminal_result_invalid")

    async def apply_terminal_commit(
        self,
        transaction: Any,
        *,
        record: Any,
        terminal_event: Any,
    ) -> HostExtensionRefV1:
        if (
            str(getattr(record, "run_id", "")) != self.execution_run_id
            or str(getattr(terminal_event, "kind", "")) != "run.final"
        ):
            raise ValueError("candidate_terminal_run_fence_mismatch")
        terminal_status = getattr(terminal_event, "status", None)
        terminal_status_value = getattr(
            terminal_status,
            "value",
            terminal_status,
        )
        if (
            terminal_status is not None
            and str(terminal_status_value) != "succeeded"
        ):
            # A successful model terminal that fails candidate validation is
            # converted by DriverRuntime into a durable failed terminal.  The
            # failed terminal must not re-run the same candidate parser: it
            # exists so BackgroundRunAdapter can persist failure_context and
            # schedule a fresh model attempt.
            return self.descriptor
        product, receipt = self.product_and_receipt(
            self._structured(terminal_event)
        )
        await transaction.insert_candidate_draft_receipt(
            {
                name: str(getattr(receipt, name))
                for name in (
                    "receipt_id",
                    "builder_launch_id",
                    "child_run_id",
                    "child_start_hash",
                    "proposal_ref",
                    "proposal_hash",
                    "evidence_set_hash",
                    "target_fence_hash",
                    "validated_draft_hash",
                    "manifest_hash",
                    "archive_hash",
                    "file_set_hash",
                    "effect_topology_hash",
                    "receipt_hash",
                )
            },
            created_at=self.created_at,
        )
        await transaction.insert_candidate_draft_material(
            receipt_id=receipt.receipt_id,
            archive_bytes=product.archive_bytes,
            validated_draft_hash=receipt.validated_draft_hash,
            manifest_hash=receipt.manifest_hash,
            archive_hash=receipt.archive_hash,
            file_set_hash=receipt.file_set_hash,
            effect_topology_hash=receipt.effect_topology_hash,
            created_at=self.created_at,
        )
        return HostExtensionRefV1(
            "deskpet.candidate-draft.v1",
            receipt.receipt_id,
            receipt.receipt_hash,
        )


@dataclass(frozen=True, slots=True)
class _CandidatePermitView:
    """Add Store-derived launch identities to an immutable permit."""

    permit: Any
    builder_launch_id: str
    child_run_id: str
    child_start_hash: str

    def __getattr__(self, name: str) -> Any:
        return getattr(self.permit, name)


class GrowthProductionPipeline:
    """Stage router used by the single Companion BackgroundRunAdapter."""

    _MAX_SEMANTIC_REPLAN_ATTEMPTS = 2

    def __init__(
        self,
        *,
        store: Any,
        target_resolver: FirstPartyGrowthTargetResolver,
        execution_database_path: str | Path,
        tool_registry: Any,
        revocation_barrier: Any,
        activation_dispatcher: Any | None = None,
        reminder_source_loader: Callable[
            [OwnerRef, tuple[str, ...]],
            Mapping[str, str] | Awaitable[Mapping[str, str]],
        ]
        | None = None,
        clock: Any | None = None,
        task_workspace: str | Path | None = None,
    ) -> None:
        self._store = store
        self._targets = target_resolver
        self._execution_database_path = Path(execution_database_path)
        self._tool_registry = tool_registry
        self._barrier = revocation_barrier
        self._activation_dispatcher = activation_dispatcher
        self._reminder_source_loader = reminder_source_loader
        self._clock = clock
        self._workspace = str(
            Path(task_workspace or self._execution_database_path.parent)
            .resolve(strict=False)
        )
        self._reflection = ReflectionResultPostprocessor(
            store=store,
            evidence_loader=self._load_evidence,
            target_facts_resolver=target_resolver,
        )

    def target_catalog(self) -> tuple[dict[str, str], ...]:
        return self._targets.catalog

    def _candidate_tool_catalog(self) -> tuple[dict[str, str], ...]:
        specs = self._tool_registry.all_specs()
        return tuple(
            {
                "name": str(spec.name),
                "permission": str(spec.permission_category),
                "effect": str(spec.effect_class.value),
            }
            for spec in sorted(specs, key=lambda item: str(item.name))
            if spec.execution_build_identity is not None
            and bool(spec.stable_handler_id)
        )

    def _now(self) -> str:
        if self._clock is not None:
            value = self._clock.now_utc()
            return value.isoformat()
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat()

    def _load_evidence(
        self,
        owner: OwnerRef,
        event_ids: tuple[str, ...],
    ) -> LiveReflectionEvidenceV1:
        return LiveReflectionEvidenceV1(
            evidence=self._store.load_live_growth_evidence(
                owner,
                event_ids=event_ids,
            ),
            explicit_user_signal=True,
        )

    async def prompt(
        self,
        owner: OwnerRef,
        claim: Any,
        facts: CompanionPreparedRunFacts,
        base_text: str,
    ) -> str:
        """Ground reminder drafts from trusted SessionDB message references."""

        payload = _job_payload(self._store, owner, claim.item_id)
        request_payload = _json_object(
            payload.get("request_payload", {}),
            "growth_request_payload_invalid",
        )
        if str(request_payload.get("companion_stage") or "reflection") != (
            "reminder_draft"
        ):
            return base_text
        _reminder_id, _occurrence_id, reminder_text, source_refs = (
            _reminder_draft_request(request_payload)
        )
        if (
            self._reminder_source_loader is None
            or facts.evidence_ids != source_refs
        ):
            raise ValueError("reminder_draft_source_loader_unavailable")
        loaded = self._reminder_source_loader(owner, source_refs)
        if inspect.isawaitable(loaded):
            loaded = await loaded
        if not isinstance(loaded, Mapping) or set(loaded) != set(source_refs):
            raise ValueError("reminder_draft_source_set_incomplete")
        grounded = []
        for ref in source_refs:
            content = str(loaded.get(ref) or "").strip()
            if not content:
                raise ValueError("reminder_draft_source_content_missing")
            grounded.append({"source_ref": ref, "content": content})
        return (
            "请依据下面三条由 host 冻结的真实消息生成一份非空游戏开发周报草稿。"
            "逐项覆盖已完成、待办和风险，并在每项旁保留对应 source_ref。"
            "不要调用工具，不要发送、发布或提交任何内容；只输出草稿正文。\n"
            f"提醒：{reminder_text}\n"
            "来源："
            + json.dumps(
                grounded,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )

    async def prepared_context(
        self,
        owner: OwnerRef,
        claim: Any,
        facts: CompanionPreparedRunFacts,
        base: PreparedRunContextV1,
        execution_run_id: str,
    ) -> PreparedRunContextV1:
        payload = _job_payload(self._store, owner, claim.item_id)
        request_payload = _json_object(
            payload.get("request_payload", {}),
            "growth_request_payload_invalid",
        )
        stage = str(request_payload.get("companion_stage") or "reflection")
        if stage == "reflection":
            return base
        if stage == "reminder_draft":
            _reminder_id, occurrence_id, _text, source_refs = (
                _reminder_draft_request(request_payload)
            )
            expected_target = f"reminder:{occurrence_id}"
            scopes = tuple(item.scope for item in facts.delegated_grants)
            if (
                facts.purpose != "delegated_task"
                or facts.owner_key != _owner_key(owner)
                or facts.evidence_ids != source_refs
                or scopes != ("read", "draft", "reversible_local")
                or any(
                    item.target != expected_target
                    for item in facts.delegated_grants
                )
            ):
                raise ValueError("reminder_draft_prepared_facts_invalid")
            return base
        if stage == "candidate_build":
            extension = await self._prepare_candidate_extension(
                owner,
                claim=claim,
                request_payload=request_payload,
                execution_run_id=execution_run_id,
            )
            if extension is None:
                return base
            return replace(
                base,
                terminal_commit_extensions=(
                    *base.terminal_commit_extensions,
                    extension,
                ),
            )
        if stage == "evaluation":
            await self._prepare_evaluation_cases(
                owner,
                claim=claim,
                request_payload=request_payload,
            )
            return base
        raise ValueError("companion_growth_stage_invalid")

    async def postprocess(
        self,
        result: BackgroundRunCanonicalResultV1,
    ) -> BackgroundRunDurableResultV1 | None:
        payload = _job_payload(self._store, result.owner, result.job_id)
        request_payload = _json_object(
            payload.get("request_payload", {}),
            "growth_request_payload_invalid",
        )
        stage = str(request_payload.get("companion_stage") or "reflection")
        if stage == "reflection":
            admitted = await self._reflection(result)
            await self._after_reflection(
                result,
                admitted,
                request_payload=request_payload,
            )
            return None
        if stage == "reminder_draft":
            return self._after_reminder_draft(result, request_payload)
        if stage == "candidate_build":
            await self._after_candidate(result, request_payload)
            return None
        if stage == "evaluation":
            await self._after_evaluation(result, request_payload)
            return None
        raise ValueError("companion_growth_stage_invalid")

    def _after_reminder_draft(
        self,
        result: BackgroundRunCanonicalResultV1,
        request_payload: Mapping[str, Any],
    ) -> BackgroundRunDurableResultV1:
        reminder_id, occurrence_id, reminder_text, source_refs = (
            _reminder_draft_request(request_payload)
        )
        text = result.text.strip()
        if not text or len(text) > 64_000:
            raise ValueError("reminder_draft_text_invalid")
        if result.evidence_ids != source_refs:
            raise ValueError("reminder_draft_evidence_mismatch")
        body = {
            "schema_version": 1,
            "kind": "reminder_draft",
            "occurrence_id": occurrence_id,
            "reminder_id": reminder_id,
            "reminder_text": reminder_text,
            "source_message_refs": list(source_refs),
            "text": text,
            "external_send_allowed": False,
            "external_send_count": 0,
        }
        return BackgroundRunDurableResultV1(
            result_ref="json:" + canonical_json(body),
            result_hash=canonical_hash(body),
            reason_code="reminder_draft_committed",
        )

    async def _after_reflection(
        self,
        result: BackgroundRunCanonicalResultV1,
        admitted: Mapping[str, Any],
        *,
        request_payload: Mapping[str, Any] | None = None,
    ) -> None:
        build = admitted.get("candidate_build")
        if not isinstance(build, Mapping):
            return
        proposal = StructuredGrowthProposalV1.from_mapping(
            json.loads(str(build["proposal_json"]))
        )
        template = await self._targets.template(result.owner, proposal)
        job_id = "candidate-job:" + canonical_hash(
            {
                "build_id": build["build_id"],
                "proposal_hash": proposal.proposal_hash,
            }
        )
        semantic_replan_attempt = _semantic_replan_attempt(
            (request_payload or {}).get("semantic_replan_attempt", 0),
            maximum=self._MAX_SEMANTIC_REPLAN_ATTEMPTS,
        )
        prompt = (
            "你正在为一个已通过 host fence 的 Companion 成长候选编写完整 Skill。"
            "不要调用工具，只输出严格 JSON："
            '{"schema_version":1,"skill_markdown":"完整 SKILL.md 文本",'
            '"allowed_tools":["由你按任务语义选择的精确工具名"],'
            '"rationale":"修改理由","self_checks":["自检项"]}。'
            "必须保留合法 YAML frontmatter 与 Skill 名称；"
            "你需要根据任务语义自主决定 allowed_tools，不能依赖关键词或正则路由。"
            "JSON 的 allowed_tools 必须与 YAML allowed-tools 完全一致，"
            "且只能从 host 提供的工具目录中精确选择；host 会从注册表自行推导"
            "权限和副作用，模型不得声明这些事实。不要添加脚本或 hook。\n"
            f"Host 工具目录={json.dumps(self._candidate_tool_catalog(), ensure_ascii=False)}\n"
            f"目标 Skill：{proposal.target.stable_name if proposal.target else ''}\n"
            f"用户要求：{dict(proposal.structured_diff).get('requested_change', '')}\n"
            f"预期改进：{proposal.expected_improvement}\n"
            "评测计划中的每一条 assertion 都是必须实现并在 self_checks 中逐项核对的"
            "验收条件；不能只实现其中一部分，也不能只复述要求。\n"
            f"评测计划：{json.dumps([dict(item) for item in proposal.evaluation_plan], ensure_ascii=False)}\n"
            f"原始 SKILL.md：\n{template.source_markdown}"
        )
        self._store.enqueue_job(
            result.owner,
            job_id=job_id,
            kind="candidate_build",
            dedupe_key=f"candidate-build:{build['build_id']}",
            payload={
                "purpose": "delegated_task",
                "owner_key": _owner_key(result.owner),
                "evidence_ids": list(result.evidence_ids),
                "capture_growth": False,
                "requires_idle": True,
                "text": prompt,
                "request_payload": {
                    "companion_stage": "candidate_build",
                    "build_id": str(build["build_id"]),
                    "proposal": proposal.to_dict(),
                    "semantic_replan_attempt": semantic_replan_attempt,
                },
            },
            budget_reserved_tokens=8_000,
            budget_reserved_ms=180_000,
            reason_code="candidate_authoring_queued",
        )

    async def _prepare_candidate_extension(
        self,
        owner: OwnerRef,
        *,
        claim: Any,
        request_payload: Mapping[str, Any],
        execution_run_id: str,
    ) -> CandidateBuildTerminalExtension | None:
        build_id = str(request_payload.get("build_id") or "")
        proposal = StructuredGrowthProposalV1.from_mapping(
            _json_object(
                request_payload.get("proposal"),
                "candidate_proposal_payload_invalid",
            )
        )
        existing = self._store.get_candidate_build_recovery(
            owner,
            build_id=build_id,
        )
        if existing["status"] == "built":
            if not existing["candidate_ref"]:
                raise ValueError("candidate_build_terminal_ref_missing")
            # The candidate commit and evaluation admission are separate
            # durable boundaries.  A crash between them must not try to
            # reclaim or rebuild the already committed candidate.  The model
            # run may still be replayed by the generic background adapter, but
            # its result is ignored by _after_candidate's built-state path.
            return None
        claim_owner = f"growth-builder:{build_id}"
        permit = self._store.claim_next_candidate_build(
            owner,
            claim_owner=claim_owner,
            lease_seconds=900,
            build_id=build_id,
        )
        if permit is None or permit.build_id != build_id:
            raise ValueError("candidate_build_scheduler_order_conflict")
        state = self._store.prepare_launch_pending(
            permit,
            task_workspace=self._workspace,
        )
        if state.status.value == "launch_pending":
            state = self._store.ack_child_precreated(
                permit,
                builder_launch_id=state.builder_launch_id,
                child_run_id=state.child_run_id,
                child_start_hash=state.expected_child_start_hash,
            )
        if state.status.value == "child_precreated":
            state = self._store.ack_running(
                permit,
                builder_launch_id=state.builder_launch_id,
                child_run_id=state.child_run_id,
                child_start_hash=state.expected_child_start_hash,
            )
        if state.status.value not in {"running", "handoff_pending"}:
            raise ValueError("candidate_build_not_running")
        template = await self._targets.template(owner, proposal)
        return CandidateBuildTerminalExtension(
            execution_run_id=execution_run_id,
            permit=_CandidatePermitView(
                permit=permit,
                builder_launch_id=state.builder_launch_id,
                child_run_id=state.child_run_id,
                child_start_hash=state.expected_child_start_hash,
            ),
            proposal=proposal,
            template=template,
            created_at=self._now(),
            tool_registry=self._tool_registry,
        )

    async def _after_candidate(
        self,
        result: BackgroundRunCanonicalResultV1,
        request_payload: Mapping[str, Any],
    ) -> None:
        build_id = str(request_payload["build_id"])
        proposal = StructuredGrowthProposalV1.from_mapping(
            _json_object(
                request_payload["proposal"],
                "candidate_proposal_payload_invalid",
            )
        )
        existing = self._store.get_candidate_build_recovery(
            result.owner,
            build_id=build_id,
        )
        if existing["status"] == "built":
            candidate_ref = existing["candidate_ref"]
            if not candidate_ref:
                raise ValueError("candidate_build_terminal_ref_missing")
            await self._admit_and_enqueue_evaluation(
                result.owner,
                proposal=proposal,
                candidate_id=candidate_ref,
                evidence_ids=result.evidence_ids,
                semantic_replan_attempt=_semantic_replan_attempt(
                    request_payload.get("semantic_replan_attempt", 0),
                    maximum=self._MAX_SEMANTIC_REPLAN_ATTEMPTS,
                ),
            )
            return
        if result.structured_result is None:
            raise ValueError("candidate_structured_result_required")
        claim_owner = f"growth-builder:{build_id}"
        permit = self._store.claim_next_candidate_build(
            result.owner,
            claim_owner=claim_owner,
            lease_seconds=900,
            build_id=build_id,
        )
        if permit is None or permit.build_id != build_id:
            raise ValueError("candidate_build_permit_missing")
        state = self._store.prepare_launch_pending(
            permit,
            task_workspace=self._workspace,
        )
        view = _CandidatePermitView(
            permit=permit,
            builder_launch_id=state.builder_launch_id,
            child_run_id=state.child_run_id,
            child_start_hash=state.expected_child_start_hash,
        )
        template = await self._targets.template(result.owner, proposal)
        extension = CandidateBuildTerminalExtension(
            execution_run_id=result.execution_run_id,
            permit=view,
            proposal=proposal,
            template=template,
            created_at=self._now(),
            tool_registry=self._tool_registry,
        )
        _product, receipt = extension.product_and_receipt(
            result.structured_result
        )
        expectation = CandidateDraftReceiptExpectationV1(
            **{
                name: getattr(receipt, name)
                for name in CandidateDraftReceiptExpectationV1.__dataclass_fields__
            }
        )
        if state.status.value == "running":
            state = self._store.ack_handoff_pending(
                permit,
                expectation=expectation,
            )
        service = CandidateCompositionService(
            receipt_query=SqliteCandidateDraftReceiptQuery(
                self._execution_database_path
            ),
            material_query=SqliteCandidateDraftMaterialQuery(
                self._execution_database_path
            ),
            store=self._store,
        )
        committed = service.create_candidate_from_builder_receipt(
            CandidateCompositionRequestV1(
                build_id=build_id,
                receipt_expectation=expectation,
            )
        )
        self._store.ack_built(
            permit,
            expectation=expectation,
            handoff={
                "build_id": build_id,
                "candidate_ref": committed.candidate_id,
                "candidate_hash": (
                    self._store.get_exact_candidate_bundle(
                        result.owner,
                        candidate_id=committed.candidate_id,
                    ).package.candidate_package_hash
                ),
            },
        )
        await self._admit_and_enqueue_evaluation(
            result.owner,
            proposal=proposal,
            candidate_id=committed.candidate_id,
            evidence_ids=result.evidence_ids,
            semantic_replan_attempt=_semantic_replan_attempt(
                request_payload.get("semantic_replan_attempt", 0),
                maximum=self._MAX_SEMANTIC_REPLAN_ATTEMPTS,
            ),
        )

    def _tool_manifest(
        self,
        names: Sequence[str],
    ) -> dict[str, Mapping[str, object]]:
        result: dict[str, Mapping[str, object]] = {}
        for name in sorted(set(names)):
            spec = self._tool_registry.get(name)
            if spec is None:
                raise ValueError(f"candidate_tool_missing:{name}")
            build = spec.execution_build_identity
            if build is None or not spec.stable_handler_id:
                raise ValueError(f"candidate_tool_identity_missing:{name}")
            effect_policy = spec.effect_policy
            policy_payload = (
                {"policy": "none"}
                if effect_policy is None
                else {
                    "policy_id": str(effect_policy.policy_id),
                    "version": str(effect_policy.version),
                    "kind": str(effect_policy.kind),
                    "max_attempts": int(effect_policy.max_attempts),
                    "reusable_across_branches": bool(
                        effect_policy.reusable_across_branches
                    ),
                }
            )
            ref = tool_spec_fingerprint(spec)
            result[name] = {
                "stable_handler_id": str(spec.stable_handler_id),
                "tool_name": name,
                "spec_ref": ref,
                "schema_hash": str(spec.schema_hash),
                "execution_build_identity": canonical_hash(
                    build.fingerprint_payload()
                ),
                "effect_policy_hash": canonical_hash(policy_payload),
                "effect": spec.effect_class.value,
                "idempotent": spec.idempotency.value == "idempotent",
            }
        return result

    async def _admit_and_enqueue_evaluation(
        self,
        owner: OwnerRef,
        *,
        proposal: StructuredGrowthProposalV1,
        candidate_id: str,
        evidence_ids: tuple[str, ...],
        semantic_replan_attempt: int = 0,
    ) -> None:
        semantic_replan_attempt = _semantic_replan_attempt(
            semantic_replan_attempt,
            maximum=self._MAX_SEMANTIC_REPLAN_ATTEMPTS,
        )
        bundle = self._store.get_exact_candidate_bundle(
            owner,
            candidate_id=candidate_id,
        )
        plan = [dict(item) for item in proposal.evaluation_plan]
        suite_hash = canonical_hash(
            {
                "schema": "companion-pairwise-suite-v1",
                "candidate_id": candidate_id,
                "plan": plan,
            }
        )
        evaluation_id = "evaluation:" + canonical_hash(
            [candidate_id, bundle.package.candidate_package_hash, suite_hash]
        )
        case_id = "case:" + canonical_hash(plan)[:32]
        input_id = "input:" + canonical_hash(
            [evaluation_id, case_id, list(evidence_ids)]
        )
        runner_policy_hash = canonical_hash(
            {
                "schema": "background-pairwise-runner-v1",
                "zero_tools": True,
                "blind_labels": True,
            }
        )
        baseline_fields = _evaluation_baseline_fields(proposal)
        self._store.admit_evaluation_experiment(
            owner,
            evaluation={
                "evaluation_id": evaluation_id,
                "candidate_id": candidate_id,
                "candidate_mode": CandidateMode(
                    proposal.candidate_mode
                ).value,
                "candidate_package_hash": (
                    bundle.package.candidate_package_hash
                ),
                "candidate_manifest_hash": (
                    bundle.package.candidate_manifest_hash
                ),
                "candidate_archive_hash": bundle.package.archive_hash,
                "suite_hash": suite_hash,
                "attempt_key": f"evaluation-attempt:{evaluation_id}",
                "candidate_snapshot_hash": (
                    bundle.package.candidate_package_hash
                ),
                "runner_id": "background-pairwise-v1",
                "runner_policy_hash": runner_policy_hash,
                "provider_id": "product-background-provider",
                "model_id": "product-background-model",
                **baseline_fields,
            },
            case_inputs=(
                {
                    "input_id": input_id,
                    "case_id": case_id,
                    "source_kind": "historical_replay",
                    "resource_ref": (
                        f"growth-evidence:{canonical_hash(list(evidence_ids))}"
                    ),
                    "resource_hash": canonical_hash(list(evidence_ids)),
                    "source_event_refs": evidence_ids,
                    "input_envelope": {
                        "evaluation_plan": plan,
                        "expected_improvement": proposal.expected_improvement,
                    },
                    "adapter_id": "background-pairwise-v1",
                    "adapter_version": "1",
                    "adapter_build_fingerprint": runner_policy_hash,
                    "assertion_ref": f"assertion:{case_id}",
                    "assertion_hash": canonical_hash(plan),
                    "read_tool_fixture": {"records": []},
                    "evaluation_tool_adapter_map": {},
                },
            ),
            cases=tuple(
                {
                    "case_id": case_id,
                    "variant": variant,
                    "input_id": input_id,
                    "manifest_case_version": "1",
                    "blind_label": f"blind-{canonical_hash([case_id, variant])[:16]}",
                    "expected_kind": "instruction_contract",
                }
                for variant in ("old", "candidate")
            ),
        )
        template = await self._targets.template(owner, proposal)
        candidate_manifest, candidate_tools, candidate_markdown = (
            _candidate_manifest_and_skill_tools(
                bundle.package,
                relative_path=template.relative_path,
            )
        )
        tools = self._tool_manifest(candidate_tools)
        source_tools = self._tool_manifest(template.allowed_tools)
        candidate_topology, candidate_permissions, _candidate_pack_effects = (
            _tool_effect_topology(
                candidate_manifest,
                tool_registry=self._tool_registry,
                allowed_tools=candidate_tools,
            )
        )
        source_topology, source_permissions, _source_pack_effects = (
            _tool_effect_topology(
                template.manifest_template,
                tool_registry=self._tool_registry,
                allowed_tools=template.allowed_tools,
            )
        )
        candidate_effects = set(candidate_topology["effects"])
        source_effects = set(source_topology["effects"])
        topology_expanded = not set(candidate_tools).issubset(
            template.allowed_tools
        )
        topology_diff = EffectTopologyDiffV1(
            permissions_added=tuple(
                sorted(set(candidate_permissions) - set(source_permissions))
            ),
            effects_added=tuple(
                sorted(candidate_effects - source_effects)
            ),
            topology_expanded=topology_expanded,
        )
        gate = ImmutableEvaluationGate(
            EvaluationPermitIssuer(self._store)
        ).issue(
            owner,
            identity=EvaluationIdentityV1(
                evaluation_id=evaluation_id,
                candidate_id=candidate_id,
                package_hash=bundle.package.candidate_package_hash,
                manifest_hash=bundle.package.candidate_manifest_hash,
                archive_hash=bundle.package.archive_hash,
                suite_hash=suite_hash,
                runner_policy_hash=runner_policy_hash,
                issued_revocation_epoch=self._barrier.epoch,
                risk_ref=f"risk:{evaluation_id}",
            ),
            package=bundle.package,
            receipt=bundle.receipt,
            effect_topology=candidate_topology,
            tool_manifest=tools,
            topology_diff=topology_diff,
            source_tool_refs=_source_risk_refs(
                template,
                source_tools,
                effect_topology=source_topology,
            ),
            zero_tools=True,
        )
        if gate.status != "issued" or gate.permit is None:
            return
        prompt = (
            "你是独立的零工具 pairwise 评测者。比较 A/B 两份 Skill 指令，"
            "只根据冻结的用户要求与评测计划判断，不调用工具。只输出严格 JSON："
            '{"schema_version":1,"case_id":"原样返回","old_status":"passed|failed",'
            '"candidate_status":"passed|failed","improved":true,'
            '"baseline_regression":false,"explanation":"理由"}。\n'
            f"case_id={case_id}\n评测计划={json.dumps(plan, ensure_ascii=False)}\n"
            f"预期改进={proposal.expected_improvement}\n"
            f"A(old):\n{template.source_markdown}\n"
            f"B(candidate):\n{candidate_markdown}"
        )
        job_id = "evaluation-job:" + canonical_hash(
            [evaluation_id, gate.permit.permit_hash]
        )
        self._store.enqueue_job(
            owner,
            job_id=job_id,
            kind="evaluation",
            dedupe_key=f"evaluation:{evaluation_id}",
            payload={
                "purpose": "evaluation",
                "owner_key": _owner_key(owner),
                "evidence_ids": list(evidence_ids),
                "capture_growth": False,
                "requires_idle": True,
                "text": prompt,
                "request_payload": {
                    "companion_stage": "evaluation",
                    "evaluation_id": evaluation_id,
                    "candidate_id": candidate_id,
                    "case_id": case_id,
                    "suite_hash": suite_hash,
                    "runner_policy_hash": runner_policy_hash,
                    "permit": {
                        name: getattr(gate.permit, name)
                        for name in gate.permit.__dataclass_fields__
                    },
                    "proposal": proposal.to_dict(),
                    "semantic_replan_attempt": semantic_replan_attempt,
                },
            },
            budget_reserved_tokens=4_000,
            # Pairwise evaluation includes both frozen Skill bodies and can
            # legitimately take as long as candidate authoring on the relay.
            # Keep the hard bound, but align it with the authoring stage so a
            # healthy long response is not converted into a synthetic retry.
            budget_reserved_ms=180_000,
            reason_code="candidate_evaluation_queued",
        )

    async def _prepare_evaluation_cases(
        self,
        owner: OwnerRef,
        *,
        claim: Any,
        request_payload: Mapping[str, Any],
    ) -> None:
        evaluation_id = str(request_payload["evaluation_id"])
        case_id = str(request_payload["case_id"])
        permit = _json_object(
            request_payload["permit"],
            "evaluation_permit_payload_invalid",
        )
        bundle = self._store.get_exact_candidate_bundle(
            owner,
            candidate_id=str(request_payload["candidate_id"]),
        )
        adapter_fingerprint = str(
            request_payload["runner_policy_hash"]
        )
        for variant in ("old", "candidate"):
            worker = f"growth-evaluator:{evaluation_id}:{variant}"
            fence = self._store.get_evaluation_execution_fence(
                owner,
                evaluation_id=evaluation_id,
                case_id=case_id,
                variant=variant,
            )
            if str(fence["case_status"]) == "queued":
                case = self._store.claim_evaluation_case(
                    owner,
                    evaluation_id=evaluation_id,
                    case_id=case_id,
                    variant=variant,
                    claim_owner=worker,
                    lease_seconds=900,
                )
            else:
                case = fence
                if (
                    str(case["case_status"]) != "leased"
                    or str(case["claim_owner"]) != worker
                ):
                    raise ValueError("evaluation_case_not_recoverable")
            launch_facts = {
                "schema_version": 1,
                "launch_id": (
                    f"evaluation-launch:{canonical_hash([evaluation_id, case_id, variant])}"
                ),
                "evaluation_id": evaluation_id,
                "case_id": case_id,
                "variant": variant,
                "attempt_ordinal": int(
                    case.get("attempt", case.get("attempt_ordinal", 1))
                ),
                "candidate_package_hash": (
                    bundle.package.candidate_package_hash
                ),
                "candidate_manifest_hash": (
                    bundle.package.candidate_manifest_hash
                ),
                "candidate_archive_hash": bundle.package.archive_hash,
                "suite_hash": str(request_payload["suite_hash"]),
                "permit_mode": str(permit["mode"]),
                "permit_id": str(permit["permit_id"]),
                "permit_hash": str(permit["permit_hash"]),
                "case_lease_epoch": int(
                    case.get("claim_epoch", case.get("case_lease_epoch"))
                ),
                "revocation_epoch": int(
                    permit["issued_revocation_epoch"]
                ),
                "adapter_id": "background-pairwise-v1",
                "adapter_version": "1",
                "adapter_fingerprint": adapter_fingerprint,
            }
            launch = EvaluationCaseLaunch(
                **launch_facts,
                launch_fingerprint=canonical_hash(launch_facts),
                reason_code="evaluation_case_launch_claimed",
            )
            row = self._store.create_evaluation_case_launch(
                owner,
                launch,
                claim_owner=worker,
            )
            if str(row["status"]) == "claimed":
                self._store.settle_evaluation_case_launch(
                    owner,
                    launch_id=launch.launch_id,
                    expected_status="claimed",
                    status="started",
                    reason_code="background_evaluation_start_ack",
                )

    async def _after_evaluation(
        self,
        result: BackgroundRunCanonicalResultV1,
        request_payload: Mapping[str, Any],
    ) -> None:
        owner = result.owner
        evaluation_id = str(request_payload["evaluation_id"])
        report_id = f"evaluation-report:{evaluation_id}"
        recovery_reader = getattr(
            self._store,
            "get_evaluation_postprocess_recovery",
            None,
        )
        recovery = (
            recovery_reader(owner, evaluation_id=evaluation_id)
            if callable(recovery_reader)
            else {"report": None, "results": ()}
        )
        if recovery.get("report") is not None:
            # The report and every failed-evaluation replan side effect share
            # one Store transaction.  A visible report therefore means this
            # postprocess boundary is fully settled.
            return
        recovered_results = {
            (str(item["case_id"]), str(item["variant"])): dict(item)
            for item in recovery.get("results", ())
        }
        recovered_comparison = next(
            (
                dict(item["assertions"]["comparison"])
                for item in recovered_results.values()
                if isinstance(item.get("assertions"), Mapping)
                and isinstance(
                    item["assertions"].get("comparison"),
                    Mapping,
                )
            ),
            None,
        )
        if recovered_comparison is not None:
            raw = recovered_comparison
        else:
            if result.structured_result is None:
                raise ValueError("evaluation_structured_result_required")
            raw = _json_object(
                result.structured_result,
                "evaluation_result_invalid",
            )
        required = {
            "schema_version",
            "case_id",
            "old_status",
            "candidate_status",
            "improved",
            "baseline_regression",
            "explanation",
        }
        if (
            set(raw) != required
            or raw["schema_version"] != 1
            or str(raw["case_id"]) != str(request_payload["case_id"])
            or raw["old_status"] not in {"passed", "failed"}
            or raw["candidate_status"] not in {"passed", "failed"}
            or not isinstance(raw["improved"], bool)
            or not isinstance(raw["baseline_regression"], bool)
            or not isinstance(raw["explanation"], str)
            or not str(raw["explanation"]).strip()
            or len(str(raw["explanation"])) > 8_000
        ):
            raise ValueError("evaluation_result_schema_invalid")
        case_id = str(request_payload["case_id"])
        result_items: list[dict[str, str]] = []
        for variant in ("candidate", "old"):
            recovered = recovered_results.get((case_id, variant))
            if recovered is not None:
                result_items.append(
                    {
                        "case_id": case_id,
                        "variant": variant,
                        "result_hash": str(recovered["result_hash"]),
                    }
                )
                continue
            worker = f"growth-evaluator:{evaluation_id}:{variant}"
            fence = self._store.get_evaluation_execution_fence(
                owner,
                evaluation_id=evaluation_id,
                case_id=case_id,
                variant=variant,
            )
            launch_id = (
                f"evaluation-launch:{canonical_hash([evaluation_id, case_id, variant])}"
            )
            outcome = {
                "assertions": {
                    "status": raw[f"{variant}_status"],
                    "explanation": raw["explanation"],
                    "comparison": dict(raw),
                },
                "judge_result": {
                    "improved": bool(raw["improved"]),
                    "baseline_regression": bool(
                        raw["baseline_regression"]
                    ),
                },
                "usage": {"background_run_id": result.execution_run_id},
            }
            outcome_hash = canonical_hash(outcome)
            try:
                self._store.settle_evaluation_case_launch(
                    owner,
                    launch_id=launch_id,
                    expected_status="started",
                    status="completed",
                    outcome_ref=f"{result.result_ref}:{variant}",
                    outcome_hash=outcome_hash,
                    reason_code="background_evaluation_completed",
                )
            except Exception:
                # Exact replay is verified by record_evaluation_result below.
                pass
            recorded = self._store.record_evaluation_result(
                owner,
                evaluation_id=evaluation_id,
                case_id=case_id,
                variant=variant,
                claim_owner=worker,
                claim_epoch=int(fence["claim_epoch"]),
                assertions=outcome["assertions"],
                judge_result=outcome["judge_result"],
                usage=outcome["usage"],
                result_hash=outcome_hash,
                reason_code="evaluation_result_committed",
            )
            result_items.append(
                {
                    "case_id": case_id,
                    "variant": variant,
                    "result_hash": str(recorded["result_hash"]),
                }
            )
        verdict = IndependentEvaluator().evaluate(
            required_case_ids=(case_id,),
            comparisons=(
                EvaluationCaseComparisonV1(
                    case_id=case_id,
                    old_status=str(raw["old_status"]),
                    candidate_status=str(raw["candidate_status"]),
                    improved=bool(raw["improved"]),
                    baseline_regression=bool(raw["baseline_regression"]),
                ),
            ),
        )
        candidate_id = str(request_payload["candidate_id"])
        bundle = self._store.get_exact_candidate_bundle(
            owner,
            candidate_id=candidate_id,
        )
        proposal = StructuredGrowthProposalV1.from_mapping(
            _json_object(
                request_payload["proposal"],
                "evaluation_proposal_payload_invalid",
            )
        )
        failed_replan = (
            self._semantic_evaluation_replan_descriptor(
                result,
                request_payload=request_payload,
                bundle=bundle,
                report_id=report_id,
                verdict=verdict,
                explanation=str(raw["explanation"]).strip(),
                proposal=proposal,
            )
            if verdict.verdict != "passed"
            else None
        )
        self._store.create_evaluation_report(
            owner,
            report_id=report_id,
            evaluation_id=evaluation_id,
            candidate_package_hash=(
                bundle.package.candidate_package_hash
            ),
            dataset_hash=canonical_hash(
                {
                    "case_id": case_id,
                    "evidence_ids": list(result.evidence_ids),
                }
            ),
            suite_hash=str(request_payload["suite_hash"]),
            required_cases=((case_id, "old"), (case_id, "candidate")),
            results_root_hash=canonical_hash(
                sorted(
                    result_items,
                    key=lambda item: (
                        item["case_id"],
                        item["variant"],
                    ),
                )
            ),
            verdict=verdict.verdict,
            reason_code=verdict.reason_code,
            failed_replan=failed_replan,
        )
        if verdict.verdict != "passed":
            return
        if self._activation_dispatcher is None:
            return
        permit_payload = _json_object(
            request_payload.get("permit"),
            "evaluation_permit_payload_invalid",
        )
        if permit_payload.get("mode") != "safe_auto":
            return
        await self._activate_candidate(
            owner,
            proposal=proposal,
            bundle=bundle,
            report_id=report_id,
            risk_id=f"risk:{evaluation_id}",
        )

    def _semantic_evaluation_replan_descriptor(
        self,
        result: BackgroundRunCanonicalResultV1,
        *,
        request_payload: Mapping[str, Any],
        bundle: Any,
        report_id: str,
        verdict: Any,
        explanation: str,
        proposal: StructuredGrowthProposalV1,
    ) -> Mapping[str, Any]:
        """Freeze one atomic, bounded replan descriptor for CompanionStore.

        The failed immutable candidate is never rewritten.  Instead, its
        reservation is released, the evaluator's exact explanation becomes a
        new host-authored evidence event, and a new reflection Run plans a new
        candidate.  Store commits report, invalidation, event and job in one
        transaction, so no replay recomputes this payload from live facts.
        """

        attempt = _semantic_replan_attempt(
            request_payload.get("semantic_replan_attempt", 0),
            maximum=self._MAX_SEMANTIC_REPLAN_ATTEMPTS,
        )
        exhausted = attempt >= self._MAX_SEMANTIC_REPLAN_ATTEMPTS
        if exhausted:
            return {
                "candidate_id": bundle.candidate_id,
                "exhausted": True,
            }
        next_attempt = attempt + 1
        event_id = "evaluation-replan:" + canonical_hash(
            {
                "schema": "companion-evaluation-replan-v1",
                "report_id": report_id,
                "candidate_id": bundle.candidate_id,
                "attempt": next_attempt,
            }
        )
        requested_change = str(
            dict(proposal.structured_diff).get("requested_change") or ""
        )
        event = {
            "event_id": event_id,
            "source_kind": "execution_outcome",
            "source_ref": report_id,
            "context_key": f"evaluation:{bundle.candidate_id}",
            "root_run_id": result.execution_run_id,
            "reason_code": "independent_evaluation_failed_replan",
            "payload": {
                "intent": requested_change,
                "capability_id": (
                    proposal.target.pack_id if proposal.target else "unscoped"
                ),
                "evaluation_report_id": report_id,
                "candidate_id": bundle.candidate_id,
                "evaluation_reason_code": str(verdict.reason_code),
                "evaluation_explanation": explanation,
                "replan_attempt": next_attempt,
            },
        }
        event_hash = canonical_hash(
            {
                "schema_version": 1,
                **{
                    key: event[key]
                    for key in (
                        "event_id",
                        "source_kind",
                        "source_ref",
                        "context_key",
                        "root_run_id",
                    )
                },
                "retry_of": None,
                "payload": event["payload"],
            }
        )
        job_id = f"reflection:{event_id}"
        target_catalog = self.target_catalog()
        prompt = (
            "你正在根据独立评测失败原因重新规划 Companion 成长候选。"
            "旧候选是不可变历史，不能辩解、覆盖或声称已经生效；"
            "请吸取失败说明，提出一个新的完整修订计划。"
            "只能从 host 提供的冻结目标目录中选择 exact stable_name。"
            "只输出 JSON，不调用工具；字段必须是："
            '{"decision":"propose|abstain","target_kind":"skill|workflow|none",'
            '"stable_name":"string|null","hypothesis":"string|null",'
            '"requested_change":"string|null","risk_hints":["string"],'
            '"expected_improvement":"string|null",'
            '"evaluation_plan":[{"case_id":"string","assertion":"string"}]}。'
            "如果继续 propose，新的 requested_change 和 evaluation_plan 必须逐项覆盖"
            "原评测指出的所有缺口，并保留原用户目标。\n"
            f"冻结目标目录：{json.dumps(target_catalog, ensure_ascii=False)}\n"
            f"原用户目标：{requested_change}\n"
            f"原评测计划：{json.dumps([dict(item) for item in proposal.evaluation_plan], ensure_ascii=False)}\n"
            f"独立评测失败原因：{explanation}\n"
            f"这是第 {next_attempt} 次有界重规划，最多 "
            f"{self._MAX_SEMANTIC_REPLAN_ATTEMPTS} 次。"
        )
        job_payload = {
                "purpose": "reflection",
                "owner_key": _owner_key(result.owner),
                "evidence_ids": [event_id],
                "capture_growth": False,
                "requires_idle": True,
                "text": prompt,
                "request_payload": {
                    "companion_stage": "reflection",
                    "growth_signal_ref": event_id,
                    "growth_signal_hash": event_hash,
                    "semantic_replan_attempt": next_attempt,
                    "source_evaluation_report_id": report_id,
                },
        }
        return {
            "candidate_id": bundle.candidate_id,
            "exhausted": False,
            "event": event,
            "job": {
                "job_id": job_id,
                "kind": "reflection",
                "dedupe_key": f"evaluation-replan:{report_id}",
                "payload": job_payload,
                "budget_reserved_tokens": 4_000,
                "budget_reserved_ms": 120_000,
                "reason_code": (
                    "independent_evaluation_failed_replan_queued"
                ),
            },
        }

    async def _activate_candidate(
        self,
        owner: OwnerRef,
        *,
        proposal: StructuredGrowthProposalV1,
        bundle: Any,
        report_id: str,
        risk_id: str,
    ) -> None:
        target = proposal.target_fence
        if target is None:
            raise ValueError("activation_target_fence_missing")
        action = (
            MutationAction.INSTALL
            if target.expected_absent
            else MutationAction.UPDATE
        )
        request_id = "activation-request:" + canonical_hash(
            [bundle.candidate_id, report_id, target.to_dict()]
        )
        request_fingerprint = canonical_hash(
            {
                "schema": "companion-activation-request-v1",
                "request_id": request_id,
                "candidate_id": bundle.candidate_id,
                "package_hash": bundle.package.candidate_package_hash,
                "target_fence": target.to_dict(),
                "source_fence": (
                    None
                    if proposal.source_fence is None
                    else proposal.source_fence.to_dict()
                ),
                "action": action.value,
            }
        )
        mutation = MutationRequest(
            request_id=request_id,
            request_fingerprint=request_fingerprint,
            action=action,
            candidate_id=bundle.candidate_id,
            candidate_mode=CandidateMode(proposal.candidate_mode),
            target_owner_key=target.owner_key,
            target_scope=target.scope,
            target_scope_key=target.scope_key,
            pack_id=bundle.package.pack_id,
            target_version=bundle.package.version,
            target_manifest_hash=(
                bundle.package.candidate_manifest_hash
            ),
            target_package_hash=bundle.package.candidate_package_hash,
            target_archive_hash=bundle.package.archive_hash,
            source_fence=(
                None
                if proposal.source_fence is None
                else proposal.source_fence.to_dict()
            ),
            target_expected_absent=target.expected_absent,
            target_expected_binding_generation=target.binding_generation,
            reason_code="low_risk_auto_activation",
        )
        decision_id = f"activation-decision:{request_id}"
        ActivationDecisionCoordinator(self._store).decide_and_enqueue(
            owner,
            decision_id=decision_id,
            nonce=f"activation-nonce:{canonical_hash([request_id, report_id])}",
            candidate_id=bundle.candidate_id,
            report_id=report_id,
            risk_id=risk_id,
            actor="system",
            activation_package_hash=(
                bundle.package.candidate_package_hash
            ),
            activation_code_digest=None,
            activation_risk_ack="none",
            reason_code="low_risk_auto_activation",
            mutation=mutation,
        )
        await self._activation_dispatcher.execute(
            owner,
            request_id=request_id,
            claim_owner=f"growth-activation:{request_id}",
        )


__all__ = [
    "CandidateBuildTerminalExtension",
    "FirstPartyGrowthTargetResolver",
    "FirstPartySkillTemplateV1",
    "GrowthProductionPipeline",
]
