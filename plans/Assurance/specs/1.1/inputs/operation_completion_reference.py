"""Executable reference for NEW completion documents; Python standard library only.

Not an SDK substitute. Parsing never proves caller authority, official Review,
Validity, or authenticity/semantics of a connector receipt. The production readers
specified in the amendment must establish these facts before applying coverage.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

MAX_BYTES = 262144
MAX_DEPTH = 16
HASH = re.compile(r"[0-9a-f]{64}\Z")


class ContractError(ValueError):
    pass


def obj(value: object, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContractError(f"{path}: exact object keys required")
    return value


def string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 8192:
        raise ContractError(f"{path}: nonblank bounded string required")
    return value


def integer(value: object, path: str) -> int:
    if type(value) is not int or value < 0:
        raise ContractError(f"{path}: nonnegative integer required, not bool")
    return value


def digest(value: object, path: str) -> str:
    if not isinstance(value, str) or HASH.fullmatch(value) is None:
        raise ContractError(f"{path}: lowercase sha256 required")
    return value


def strings(value: object, path: str, minimum: int = 0) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= 256:
        raise ContractError(f"{path}: bounded array required")
    result = [string(v, path) for v in value]
    if len(set(result)) != len(result):
        raise ContractError(f"{path}: duplicate values")
    return result


def pin(value: object, path: str) -> None:
    row = obj(value, {"id", "revision", "content_hash"}, path)
    string(row["id"], path + ".id")
    integer(row["revision"], path + ".revision")
    digest(row["content_hash"], path + ".content_hash")


def pins(value: object, path: str, minimum: int = 0) -> None:
    if not isinstance(value, list) or not minimum <= len(value) <= 256:
        raise ContractError(f"{path}: bounded pins required")
    identities = []
    for index, item in enumerate(value):
        pin(item, f"{path}[{index}]")
        identities.append((item["id"], item["revision"], item["content_hash"]))
    if len(set(identities)) != len(identities):
        raise ContractError(f"{path}: duplicate pins")


def header(row: dict[str, Any]) -> None:
    if type(row["schema_version"]) is not int or row["schema_version"] != 1:
        raise ContractError("schema_version: exactly integer 1 required")
    string(row["mission_id"], "mission_id")


SPEC_KEYS = {"schema_version", "mission_id", "requirements_ref", "mode", "content_criterion_ids", "effects"}
EFFECT_KEYS = {"effect_key", "obligation_id", "criterion_ids", "required_milestone", "milestone_policy_ref", "evidence_policy_ref", "source_slot_key"}
SCOPE_KEYS = {"schema_version", "mission_id", "requirements_ref", "spec_hash", "plan_ref", "occurrence_id", "task_ref", "obligation_id", "role", "content_criterion_ids", "required_effect_keys", "owned_effect_keys"}
CONTRIBUTION_KEYS = {"schema_version", "mission_id", "acceptance_id", "completion_scope_id", "spec_hash", "kind", "content_criterion_ids", "effect_keys", "output_artifact_refs", "outcome_binding_id", "delivery_receipt_ref"}
OUTCOME_STRINGS = {"effect_key", "completion_scope_id", "intent_id", "operation_id", "operation_occurrence_id", "action_key", "observed_milestone"}
OUTCOME_HASHES = {"spec_hash", "request_hash", "candidate_file_hash", "parameters_content_hash", "params_hash", "effect_contract_hash", "link_hash", "connector_profile_hash", "namespace_hash", "target_identity_hash"}
OUTCOME_KEYS = {"schema_version", "mission_id", "action_version", "milestone_policy_ref", "covered_handoff_ids", "source_receipt_refs"} | OUTCOME_STRINGS | OUTCOME_HASHES


def validate_spec(value: object) -> None:
    row = obj(value, SPEC_KEYS, "spec")
    header(row)
    pin(row["requirements_ref"], "requirements_ref")
    contents = strings(row["content_criterion_ids"], "content_criterion_ids")
    effects = row["effects"]
    if not isinstance(effects, list) or len(effects) > 64:
        raise ContractError("effects: bounded list required")
    if not isinstance(row["mode"], str) or row["mode"] not in {"CONTENT_ONLY", "REQUIRED_EFFECTS"}:
        raise ContractError("mode: invalid")
    if (row["mode"] == "CONTENT_ONLY") != (len(effects) == 0):
        raise ContractError("mode/effects mismatch")
    if not effects and not contents:
        raise ContractError("empty success requirement")
    keys, slots = [], []
    for index, item in enumerate(effects):
        effect = obj(item, EFFECT_KEYS, f"effects[{index}]")
        for key in ("effect_key", "obligation_id", "required_milestone", "source_slot_key"):
            string(effect[key], key)
        criteria = strings(effect["criterion_ids"], "criterion_ids", 1)
        if set(criteria) & set(contents):
            raise ContractError("effect criteria cannot be claimed as content")
        pin(effect["milestone_policy_ref"], "milestone_policy_ref")
        pin(effect["evidence_policy_ref"], "evidence_policy_ref")
        keys.append(effect["effect_key"])
        slots.append(effect["source_slot_key"])
    if len(set(keys)) != len(keys) or len(set(slots)) != len(slots):
        raise ContractError("duplicate effect/source slot")


def validate_scope(value: object) -> None:
    row = obj(value, SCOPE_KEYS, "scope")
    header(row)
    pin(row["requirements_ref"], "requirements_ref")
    pin(row["task_ref"], "task_ref")
    digest(row["spec_hash"], "spec_hash")
    plan = obj(row["plan_ref"], {"revision", "snapshot_hash"}, "plan_ref")
    integer(plan["revision"], "plan_ref.revision")
    digest(plan["snapshot_hash"], "plan_ref.snapshot_hash")
    for key in ("occurrence_id", "obligation_id"):
        string(row[key], key)
    contents = strings(row["content_criterion_ids"], "content_criterion_ids")
    effects = strings(row["required_effect_keys"], "required_effect_keys")
    owned = strings(row["owned_effect_keys"], "owned_effect_keys")
    if not isinstance(row["role"], str) or row["role"] not in {"CONTENT", "MIXED", "AGGREGATE"}:
        raise ContractError("invalid scope role")
    if row["role"] == "CONTENT" and (effects or owned):
        raise ContractError("CONTENT scope cannot own effect obligations")
    if row["role"] == "MIXED" and not effects:
        raise ContractError("MIXED requires effect slots")
    if not set(owned) <= set(effects):
        raise ContractError("owned effects outside required effects")
    if not effects and not contents and row["role"] != "AGGREGATE":
        raise ContractError("empty primitive scope")


def validate_contribution(value: object) -> None:
    row = obj(value, CONTRIBUTION_KEYS, "contribution")
    header(row)
    for key in ("acceptance_id", "completion_scope_id"):
        string(row[key], key)
    digest(row["spec_hash"], "spec_hash")
    contents = strings(row["content_criterion_ids"], "content_criterion_ids")
    effects = strings(row["effect_keys"], "effect_keys")
    pins(row["output_artifact_refs"], "output_artifact_refs")
    if not isinstance(row["kind"], str):
        raise ContractError("invalid contribution kind")
    if row["kind"] in {"CONTENT", "PREPARATION"}:
        if effects or row["outcome_binding_id"] is not None or row["delivery_receipt_ref"] is not None:
            raise ContractError("content/preparation cannot satisfy an effect")
    elif row["kind"] == "OPERATION_EFFECT":
        if len(effects) != 1 or contents or row["output_artifact_refs"]:
            raise ContractError("one effect, no content reclassification")
        string(row["outcome_binding_id"], "outcome_binding_id")
        if row["delivery_receipt_ref"] is not None:
            pin(row["delivery_receipt_ref"], "delivery_receipt_ref")
    else:
        raise ContractError("invalid contribution kind")


def validate_outcome(value: object) -> None:
    row = obj(value, OUTCOME_KEYS, "outcome")
    header(row)
    integer(row["action_version"], "action_version")
    for key in OUTCOME_STRINGS:
        string(row[key], key)
    for key in OUTCOME_HASHES:
        digest(row[key], key)
    pin(row["milestone_policy_ref"], "milestone_policy_ref")
    strings(row["covered_handoff_ids"], "covered_handoff_ids", 1)
    pins(row["source_receipt_refs"], "source_receipt_refs", 1)


VALIDATORS = {"requirements": validate_spec, "scope": validate_scope, "contribution": validate_contribution, "outcome": validate_outcome}


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out = {}
    for key, value in pairs:
        if key in out:
            raise ContractError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _constant(value: str) -> None:
    raise ContractError(f"non-finite JSON: {value}")


def _depth(value: object, level: int = 0) -> None:
    if level > MAX_DEPTH:
        raise ContractError("nesting bound exceeded")
    if isinstance(value, dict):
        for child in value.values():
            _depth(child, level + 1)
    elif isinstance(value, list):
        for child in value:
            _depth(child, level + 1)


@dataclass(frozen=True, slots=True)
class FrozenDocument:
    kind: str
    canonical: bytes

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical).hexdigest()

    def value(self) -> dict[str, Any]:
        # Returns a new object; mutation does not modify the frozen document.
        return json.loads(self.canonical)


def parse_contract(kind: str, raw: bytes) -> FrozenDocument:
    if kind not in VALIDATORS or not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise ContractError("invalid document kind/size")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError("malformed JSON") from exc
    _depth(value)
    VALIDATORS[kind](value)
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return FrozenDocument(kind=kind, canonical=canonical)


def check_required_criterion_coverage(spec: dict[str, Any], required_ids: set[str]) -> None:
    validate_spec(spec)
    covered = set(spec["content_criterion_ids"])
    for effect in spec["effects"]:
        covered.update(effect["criterion_ids"])
    if covered != required_ids:
        raise ContractError("OP_REQUIREMENT_MAPPING_AMBIGUOUS")


def check_scope_against_spec(spec: dict[str, Any], scope: dict[str, Any], *, spec_hash: str, root_occurrence: str) -> None:
    validate_spec(spec)
    validate_scope(scope)
    if scope["mission_id"] != spec["mission_id"] or scope["requirements_ref"] != spec["requirements_ref"] or scope["spec_hash"] != spec_hash:
        raise ContractError("OP_EFFECT_SCOPE_STALE")
    allowed = {effect["effect_key"] for effect in spec["effects"]}
    if not set(scope["required_effect_keys"]) <= allowed:
        raise ContractError("OP_COMPLETION_SCOPE_UNRESOLVED")
    if scope["occurrence_id"] == root_occurrence and set(scope["required_effect_keys"]) != allowed:
        raise ContractError("root dropped a required effect")


def check_outcome_identity(outcome: dict[str, Any], expected: dict[str, Any]) -> None:
    """expected is a COMPLETE fixture of an already verified source chain.

    Production MUST obtain and recheck each field from real Store/receipt adapters;
    this helper does not issue authenticity or official-review certification.
    """
    validate_outcome(outcome)
    validate_outcome(expected)
    if outcome != expected:
        differing = sorted(key for key in OUTCOME_KEYS if outcome[key] != expected[key])
        raise ContractError("OP_OUTCOME_BINDING_MISMATCH:" + ",".join(differing))


@dataclass(frozen=True, slots=True)
class CoverageResult:
    ready_for_review: bool
    missing_content: tuple[str, ...]
    pending_effects: tuple[str, ...]


def evaluate_coverage(
    spec: dict[str, Any],
    *,
    spec_hash: str,
    required_content: set[str],
    required_effects: set[str],
    contributions: list[dict[str, Any]],
    applicable_acceptance_ids: set[str],
    verified_outcomes: Mapping[str, dict[str, Any]],
) -> CoverageResult:
    """Reference coverage only, NOT GoalResolution or authorization.

    applicable_acceptance_ids and verified_outcomes represent fixture results of
    the production readers. They have no trusted default and are not wire fields.
    """
    validate_spec(spec)
    slots = {effect["effect_key"]: effect for effect in spec["effects"]}
    if not required_effects <= set(slots):
        raise ContractError("OP_COMPLETION_SCOPE_UNRESOLVED")
    content, effects = set(), set()
    observed_binding_ids = set()
    for item in contributions:
        validate_contribution(item)
        if item["mission_id"] != spec["mission_id"] or item["spec_hash"] != spec_hash:
            raise ContractError("OP_CONTRIBUTION_SCOPE_MISMATCH")
        if item["acceptance_id"] not in applicable_acceptance_ids:
            continue
        if item["kind"] != "OPERATION_EFFECT":
            content.update(item["content_criterion_ids"])
            continue
        key = item["effect_keys"][0]
        if key not in slots:
            raise ContractError("effect outside approved specification")
        binding_id = item["outcome_binding_id"]
        if binding_id in observed_binding_ids:
            raise ContractError("one outcome binding was claimed twice")
        observed_binding_ids.add(binding_id)
        proof = verified_outcomes.get(binding_id)
        if proof is None:
            continue
        validate_outcome(proof)
        if proof["mission_id"] != spec["mission_id"] or proof["spec_hash"] != spec_hash or proof["effect_key"] != key or proof["completion_scope_id"] != item["completion_scope_id"]:
            raise ContractError("OP_OUTCOME_BINDING_MISMATCH")
        if proof["observed_milestone"] != slots[key]["required_milestone"] or proof["milestone_policy_ref"] != slots[key]["milestone_policy_ref"]:
            # No global stage ordering. Any supported implication is checked by
            # the production policy and must produce the requested proposition.
            continue
        effects.add(key)
    missing = tuple(sorted(required_content - content))
    pending = tuple(sorted(required_effects - effects))
    return CoverageResult(not missing and not pending, missing, pending)


def evaluate_root_coverage(
    spec: dict[str, Any], *, spec_hash: str,
    contributions: list[dict[str, Any]],
    applicable_acceptance_ids: set[str],
    verified_outcomes: Mapping[str, dict[str, Any]],
) -> CoverageResult:
    """Root required-effect set is derived, never provided by a caller/model."""
    validate_spec(spec)
    return evaluate_coverage(
        spec, spec_hash=spec_hash,
        required_content=set(spec["content_criterion_ids"]),
        required_effects={row["effect_key"] for row in spec["effects"]},
        contributions=contributions,
        applicable_acceptance_ids=applicable_acceptance_ids,
        verified_outcomes=verified_outcomes,
    )
