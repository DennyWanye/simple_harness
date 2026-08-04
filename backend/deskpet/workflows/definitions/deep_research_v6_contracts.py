"""Frozen semantic contracts for the DeepResearch v6 compiler.

This module intentionally contains no workflow/runtime concerns.  It owns the
strict JSON boundary and the semantic identities used before retrieval starts.
"""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass
from typing import Any, ClassVar, Iterable, Mapping, TypeAlias

from ..contracts import JsonValue, canonical_json, validate_json_value


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ResearchSpecValidationError(ValueError):
    """A v6 semantic contract failed closed with a stable machine code."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code} at {path}: {message}")


def _fail(code: str, path: str, message: str) -> "NoReturn":
    raise ResearchSpecValidationError(code, path, message)


def _object(value: object, keys: set[str], path: str, *, versioned: bool = True) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("spec_keys_differ", path, "must be an object")
    raw = dict(value)
    if set(raw) != keys:
        _fail(
            "spec_keys_differ",
            path,
            f"missing={sorted(keys-set(raw))}, unknown={sorted(set(raw)-keys)}",
        )
    if versioned and raw.get("schema_version") != 1:
        _fail("spec_schema_unsupported", f"{path}.schema_version", "must equal 1")
    try:
        validate_json_value(raw, path=path)
    except Exception as exc:
        _fail("spec_keys_differ", path, str(exc))
    return raw


def _text(value: object, path: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        _fail("spec_keys_differ", path, "must be a non-empty string")
    return value


def _integer(value: object, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail("bounds_empty", path, f"must be an integer >= {minimum}")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        _fail("spec_keys_differ", path, "must be boolean")
    return value


def _enum(value: object, allowed: set[str], path: str) -> str:
    if value not in allowed:
        _fail("spec_keys_differ", path, f"must be one of {sorted(allowed)}")
    return str(value)


def _strings(value: object, path: str, *, set_like: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        _fail("spec_keys_differ", path, "must be an array of non-empty strings")
    if len(set(value)) != len(value):
        _fail("spec_keys_differ", path, "contains duplicates")
    if set_like and value != sorted(value):
        _fail("spec_keys_differ", path, "set-like array must be canonically sorted")
    return tuple(value)


def _sequence(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        _fail("spec_keys_differ", path, "must be an array")
    return value


def _digest(value: object, path: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        _fail("policy_ref_hash_mismatch", path, "must be lowercase sha256 hex")
    return value


def format_blob_ref(digest: str) -> str:
    return "sha256:" + _digest(digest, "$.digest")


def parse_blob_ref(wire: str) -> str:
    if not isinstance(wire, str) or not wire.startswith("sha256:"):
        _fail("reference_missing", "$.ref", "must use sha256:<lowercase-64hex>")
    digest = wire[7:]
    if _HEX64.fullmatch(digest) is None:
        _fail("reference_missing", "$.ref", "must use sha256:<lowercase-64hex>")
    return digest


def sha256_json(value: JsonValue) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def derive_requirement_id(value: Mapping[str, JsonValue]) -> str:
    payload = copy.deepcopy(dict(value))
    payload.pop("requirement_id", None)
    payload.pop("ordinal", None)
    return "req_" + sha256_json(payload)[:24]


def derive_spec_hash(value: Mapping[str, JsonValue]) -> str:
    payload = copy.deepcopy(dict(value))
    payload.pop("spec_id", None)
    payload.pop("spec_hash", None)
    return sha256_json(payload)


def derive_spec_id(spec_hash: str) -> str:
    return "rs_" + _digest(spec_hash, "$.spec_hash")[:24]


def validate_continuation_spec_hash(expected: str, actual: str) -> None:
    """Fail closed when a continuation attempts to change semantic identity."""

    expected_digest = _digest(expected, "$.expected_spec_hash")
    actual_digest = _digest(actual, "$.actual_spec_hash")
    if actual_digest != expected_digest:
        _fail(
            "continuation_spec_mismatch",
            "$.spec_hash",
            "continuation must reuse the persisted ResearchSpecV1",
        )


class RouteDecisionV1:
    """Frozen, replayable routing decision derived from a ResearchSpecV1.

    The decision records whether work is eligible for conditional fan-out; it
    never changes the immutable completion semantics owned by the spec.
    """

    KEYS = {
        "schema_version", "route_id", "run_id", "spec_hash", "policy_ref",
        "policy_hash", "capability_snapshot_hash", "source_health", "budget",
        "work_groups", "initial_route", "allowed_escalations", "reason_codes",
    }

    def __init__(self, value: Mapping[str, JsonValue]) -> None:
        self._value = self._validate(value)

    @classmethod
    def _validate(cls, value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
        raw = _object(value, cls.KEYS, "$")
        _text(raw["run_id"], "$.run_id")
        _digest(raw["spec_hash"], "$.spec_hash")
        policy_digest = parse_blob_ref(str(raw["policy_ref"]))
        if policy_digest != _digest(raw["policy_hash"], "$.policy_hash"):
            _fail("policy_ref_hash_mismatch", "$.policy_ref", "ref digest differs from policy hash")
        _digest(raw["capability_snapshot_hash"], "$.capability_snapshot_hash")
        _text(raw["initial_route"], "$.initial_route")
        _strings(raw["reason_codes"], "$.reason_codes", set_like=True)

        health = _sequence(raw["source_health"], "$.source_health")
        health_keys: list[str] = []
        for index, item in enumerate(health):
            entry = _object(
                item,
                {"authority_id", "status", "reason_code"},
                f"$.source_health[{index}]",
                versioned=False,
            )
            authority = str(_text(entry["authority_id"], f"$.source_health[{index}].authority_id"))
            _enum(entry["status"], {"healthy", "degraded", "unavailable"}, f"$.source_health[{index}].status")
            _text(entry["reason_code"], f"$.source_health[{index}].reason_code")
            health_keys.append(authority)
        if health_keys != sorted(set(health_keys)):
            _fail("spec_keys_differ", "$.source_health", "must be sorted by unique authority_id")

        budget = _object(
            raw["budget"], {"query", "fetch", "browser", "llm", "lane"},
            "$.budget", versioned=False,
        )
        for key in sorted(budget):
            _integer(budget[key], f"$.budget.{key}")

        groups = _sequence(raw["work_groups"], "$.work_groups")
        group_ids: list[str] = []
        for index, item in enumerate(groups):
            group = _object(
                item, {"work_group_id", "requirement_ids", "merge_key"},
                f"$.work_groups[{index}]", versioned=False,
            )
            group_id = str(_text(group["work_group_id"], f"$.work_groups[{index}].work_group_id"))
            requirement_ids = _strings(
                group["requirement_ids"], f"$.work_groups[{index}].requirement_ids", set_like=True
            )
            if not requirement_ids:
                _fail("reference_missing", f"$.work_groups[{index}].requirement_ids", "must not be empty")
            _text(group["merge_key"], f"$.work_groups[{index}].merge_key")
            group_ids.append(group_id)
        if group_ids != sorted(set(group_ids)):
            _fail("spec_keys_differ", "$.work_groups", "must be sorted by unique work_group_id")

        escalations = _sequence(raw["allowed_escalations"], "$.allowed_escalations")
        escalation_keys: list[tuple[str, str, str]] = []
        for index, item in enumerate(escalations):
            edge = _object(
                item, {"from_route", "to_route", "condition_code"},
                f"$.allowed_escalations[{index}]", versioned=False,
            )
            key = tuple(
                str(_text(edge[name], f"$.allowed_escalations[{index}].{name}"))
                for name in ("from_route", "to_route", "condition_code")
            )
            escalation_keys.append(key)  # type: ignore[arg-type]
        if escalation_keys != sorted(set(escalation_keys)):
            _fail("spec_keys_differ", "$.allowed_escalations", "must be canonically sorted and unique")

        base = copy.deepcopy(raw)
        declared_id = str(base.pop("route_id"))
        expected_id = "route_" + sha256_json(base)[:24]
        if declared_id != expected_id:
            _fail("requirement_id_mismatch", "$.route_id", "route identity mismatch")
        return copy.deepcopy(raw)

    @classmethod
    def create(cls, **values: JsonValue) -> "RouteDecisionV1":
        payload = copy.deepcopy(values)
        payload["schema_version"] = 1
        payload.pop("route_id", None)
        payload["source_health"] = sorted(
            list(payload.get("source_health", [])), key=lambda item: str(item["authority_id"])
        )
        payload["work_groups"] = sorted(
            list(payload.get("work_groups", [])), key=lambda item: str(item["work_group_id"])
        )
        payload["allowed_escalations"] = sorted(
            list(payload.get("allowed_escalations", [])),
            key=lambda item: (str(item["from_route"]), str(item["to_route"]), str(item["condition_code"])),
        )
        payload["reason_codes"] = sorted(set(payload.get("reason_codes", [])))
        payload["route_id"] = "route_" + sha256_json(payload)[:24]
        return cls(payload)

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> "RouteDecisionV1":
        return cls(value)

    def to_json(self) -> dict[str, JsonValue]:
        return copy.deepcopy(self._value)


def conditional_fanout_reason(
    decision: RouteDecisionV1,
    *,
    intent_type: str,
) -> str:
    """Return the stable reason for enabling or disabling fan-out."""

    value = decision.to_json()
    groups = list(value["work_groups"])
    if intent_type == "official_exact_fact":
        return "exact_fact_fanout_forbidden"
    if len(groups) < 3:
        return "independent_work_groups_below_minimum"
    if any(not str(group["merge_key"]).strip() for group in groups):
        return "merge_key_missing"
    if int(value["budget"]["lane"]) < len(groups):
        return "lane_budget_insufficient"
    return "conditional_fanout_enabled"


def conditional_fanout_enabled(
    decision: RouteDecisionV1,
    *,
    intent_type: str,
) -> bool:
    return conditional_fanout_reason(decision, intent_type=intent_type) == "conditional_fanout_enabled"


@dataclass(frozen=True, slots=True)
class StrictContract:
    _value: dict[str, JsonValue]
    KEYS: ClassVar[set[str]] = set()
    VERSIONED: ClassVar[bool] = True

    def __post_init__(self) -> None:
        raw = copy.deepcopy(dict(self._value))
        self._validate(_object(raw, self.KEYS, "$", versioned=self.VERSIONED), "$")
        object.__setattr__(self, "_value", raw)

    @classmethod
    def from_json(cls, value: Mapping[str, Any]):
        return cls(copy.deepcopy(dict(value)))

    @classmethod
    def create(cls, **values: JsonValue):
        if cls.VERSIONED:
            values.setdefault("schema_version", 1)
        return cls.from_json(values)

    def to_json(self) -> dict[str, JsonValue]:
        return copy.deepcopy(self._value)

    def __getattr__(self, name: str) -> Any:
        try:
            return copy.deepcopy(self._value[name])
        except KeyError as exc:
            raise AttributeError(name) from exc

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        pass


class SubjectV1(StrictContract):
    VERSIONED = False
    KEYS = {"subject_id", "label", "aliases", "entity_type"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _text(raw["subject_id"], f"{path}.subject_id")
        _text(raw["label"], f"{path}.label")
        _strings(raw["aliases"], f"{path}.aliases", set_like=True)
        _text(raw["entity_type"], f"{path}.entity_type")


class UserConstraintsV1(StrictContract):
    KEYS = {"schema_version", "as_of_date", "jurisdictions", "preferred_authority_ids", "explicit_exclusions"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _text(raw["as_of_date"], f"{path}.as_of_date", nullable=True)
        for key in ("jurisdictions", "preferred_authority_ids", "explicit_exclusions"):
            _strings(raw[key], f"{path}.{key}", set_like=True)


class ScopeV1(StrictContract):
    KEYS = {"schema_version", "jurisdiction", "geography_ids", "population_definition", "qualifiers"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _text(raw["jurisdiction"], f"{path}.jurisdiction", nullable=True)
        _strings(raw["geography_ids"], f"{path}.geography_ids", set_like=True)
        _text(raw["population_definition"], f"{path}.population_definition", nullable=True)
        _strings(raw["qualifiers"], f"{path}.qualifiers", set_like=True)


class TimeScopeV1(StrictContract):
    KEYS = {"schema_version", "kind", "start", "end", "as_of", "label"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        kind = _enum(raw["kind"], {"instant", "period", "latest", "timeless"}, f"{path}.kind")
        start = _text(raw["start"], f"{path}.start", nullable=True)
        end = _text(raw["end"], f"{path}.end", nullable=True)
        as_of = _text(raw["as_of"], f"{path}.as_of", nullable=True)
        _text(raw["label"], f"{path}.label", nullable=True)
        valid = (
            (kind == "instant" and as_of is not None and start is None and end is None)
            or (kind == "period" and start is not None and end is not None and as_of is None and start <= end)
            or (kind == "latest" and as_of is not None and start is None and end is None)
            or (kind == "timeless" and start is None and end is None and as_of is None)
        )
        if not valid:
            _fail("time_scope_invalid", path, f"dates do not match kind={kind}")


class SourceConstraintV1(StrictContract):
    KEYS = {"schema_version", "first_party", "authority_roles", "preferred_authority_ids", "eligible_source_types", "minimum_source_families", "secondary_evidence"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _enum(raw["first_party"], {"required", "preferred", "not_required"}, f"{path}.first_party")
        for key in ("authority_roles", "preferred_authority_ids", "eligible_source_types"):
            _strings(raw[key], f"{path}.{key}", set_like=True)
        _integer(raw["minimum_source_families"], f"{path}.minimum_source_families")
        _enum(raw["secondary_evidence"], {"context_only", "support_allowed"}, f"{path}.secondary_evidence")


class UnitV1(StrictContract):
    VERSIONED = False
    KEYS = {"unit_id", "symbol", "to_canonical_numerator", "to_canonical_denominator"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _text(raw["unit_id"], f"{path}.unit_id")
        _text(raw["symbol"], f"{path}.symbol")
        _integer(raw["to_canonical_numerator"], f"{path}.to_canonical_numerator")
        _integer(raw["to_canonical_denominator"], f"{path}.to_canonical_denominator", minimum=1)


class ToleranceV1(StrictContract):
    VERSIONED = False
    KEYS = {"mode", "numerator", "denominator"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        mode = _enum(raw["mode"], {"exact", "absolute", "relative_ppm"}, f"{path}.mode")
        numerator = _integer(raw["numerator"], f"{path}.numerator")
        _integer(raw["denominator"], f"{path}.denominator", minimum=1)
        if mode == "exact" and numerator != 0:
            _fail("unit_invalid", path, "exact tolerance numerator must be zero")


class ValueSchemaV1(StrictContract):
    KEYS = {"schema_version", "value_type", "quantity_kind", "canonical_unit", "accepted_units", "definition", "tolerance"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _text(raw["value_type"], f"{path}.value_type")
        _text(raw["quantity_kind"], f"{path}.quantity_kind", nullable=True)
        canonical = UnitV1.from_json(raw["canonical_unit"])
        accepted = [UnitV1.from_json(v) for v in _sequence(raw["accepted_units"], f"{path}.accepted_units")]
        if not accepted or canonical.unit_id not in {u.unit_id for u in accepted}:
            _fail("unit_invalid", f"{path}.accepted_units", "must include canonical unit")
        if len({u.unit_id for u in accepted}) != len(accepted):
            _fail("unit_invalid", f"{path}.accepted_units", "duplicate unit_id")
        _text(raw["definition"], f"{path}.definition")
        ToleranceV1.from_json(raw["tolerance"])


class CardinalityV1(StrictContract):
    VERSIONED = False
    KEYS = {"minimum", "maximum"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        minimum = _integer(raw["minimum"], f"{path}.minimum")
        maximum = _integer(raw["maximum"], f"{path}.maximum")
        if maximum < minimum:
            _fail("bounds_empty", path, "maximum is below minimum")


_COMMON_REQUIREMENT_KEYS = {
    "schema_version", "requirement_id", "ordinal", "kind", "key", "label",
    "importance", "subject_ids", "scope", "time_scope", "source_constraint",
}


def _validate_common_requirement(raw: dict[str, Any], path: str, kind: str) -> None:
    _text(raw["requirement_id"], f"{path}.requirement_id")
    _integer(raw["ordinal"], f"{path}.ordinal")
    if raw["kind"] != kind:
        _fail("requirement_kind_unsupported", f"{path}.kind", f"expected {kind}")
    _text(raw["key"], f"{path}.key")
    _text(raw["label"], f"{path}.label")
    _enum(raw["importance"], {"required", "optional"}, f"{path}.importance")
    _strings(raw["subject_ids"], f"{path}.subject_ids", set_like=True)
    ScopeV1.from_json(raw["scope"])
    TimeScopeV1.from_json(raw["time_scope"])
    SourceConstraintV1.from_json(raw["source_constraint"])
    expected = derive_requirement_id(raw)
    if raw["requirement_id"] != expected:
        _fail("requirement_id_mismatch", f"{path}.requirement_id", f"expected {expected}")


class ScalarRequirement(StrictContract):
    KEYS = _COMMON_REQUIREMENT_KEYS | {"value_schema", "cardinality"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _validate_common_requirement(raw, path, "scalar")
        ValueSchemaV1.from_json(raw["value_schema"])
        cardinality = CardinalityV1.from_json(raw["cardinality"])
        if (cardinality.minimum, cardinality.maximum) != (1, 1):
            _fail("bounds_empty", f"{path}.cardinality", "scalar cardinality must be 1/1")


class AxisMemberV1(StrictContract):
    VERSIONED = False
    KEYS = {"member_id", "label", "subject_id", "value_schema"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _text(raw["member_id"], f"{path}.member_id")
        _text(raw["label"], f"{path}.label")
        _text(raw["subject_id"], f"{path}.subject_id", nullable=True)
        if raw["value_schema"] is not None:
            ValueSchemaV1.from_json(raw["value_schema"])


class AxisV1(StrictContract):
    VERSIONED = False
    KEYS = {"axis_id", "role", "label", "members"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _text(raw["axis_id"], f"{path}.axis_id")
        _enum(raw["role"], {"subject", "criterion", "time", "custom"}, f"{path}.role")
        _text(raw["label"], f"{path}.label")
        members = [AxisMemberV1.from_json(v) for v in _sequence(raw["members"], f"{path}.members")]
        if not members or len({m.member_id for m in members}) != len(members):
            _fail("bounds_empty", f"{path}.members", "must be non-empty with unique ids")


class MatrixCellPolicyV1(StrictContract):
    VERSIONED = False
    KEYS = {"minimum_admitted_bindings", "allow_inference"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _integer(raw["minimum_admitted_bindings"], f"{path}.minimum_admitted_bindings")
        _boolean(raw["allow_inference"], f"{path}.allow_inference")


class RequiredCellsV1(StrictContract):
    VERSIONED = False
    KEYS = {"mode", "excluded"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _enum(raw["mode"], {"cartesian_product"}, f"{path}.mode")
        excluded = _sequence(raw["excluded"], f"{path}.excluded")
        tuples = []
        for index, item in enumerate(excluded):
            tuples.append(_strings(item, f"{path}.excluded[{index}]"))
        if tuples != sorted(tuples) or len(set(tuples)) != len(tuples):
            _fail("spec_keys_differ", f"{path}.excluded", "must be unique and sorted")


class CoverageV1(StrictContract):
    VERSIONED = False
    KEYS = {"minimum_ratio_ppm"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        value = _integer(raw["minimum_ratio_ppm"], f"{path}.minimum_ratio_ppm")
        if value > 1_000_000:
            _fail("bounds_empty", f"{path}.minimum_ratio_ppm", "must be <= 1000000")


class MatrixRequirement(StrictContract):
    KEYS = _COMMON_REQUIREMENT_KEYS | {"axes", "cell_policy", "required_cells", "coverage"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _validate_common_requirement(raw, path, "matrix")
        axes = [AxisV1.from_json(v) for v in _sequence(raw["axes"], f"{path}.axes")]
        if not axes or len({a.axis_id for a in axes}) != len(axes):
            _fail("bounds_empty", f"{path}.axes", "must be non-empty with unique ids")
        MatrixCellPolicyV1.from_json(raw["cell_policy"])
        RequiredCellsV1.from_json(raw["required_cells"])
        CoverageV1.from_json(raw["coverage"])


class CollectionFieldV1(StrictContract):
    VERSIONED = False
    KEYS = {"field_key", "label", "value_type", "required", "unit", "minimum_admitted_bindings"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _text(raw["field_key"], f"{path}.field_key")
        _text(raw["label"], f"{path}.label")
        _text(raw["value_type"], f"{path}.value_type")
        _boolean(raw["required"], f"{path}.required")
        if raw["unit"] is not None:
            UnitV1.from_json(raw["unit"])
        _integer(raw["minimum_admitted_bindings"], f"{path}.minimum_admitted_bindings")


class CollectionItemSchemaV1(StrictContract):
    VERSIONED = False
    KEYS = {"entity_type", "unique_key", "fields"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _text(raw["entity_type"], f"{path}.entity_type")
        unique = _strings(raw["unique_key"], f"{path}.unique_key")
        fields = [CollectionFieldV1.from_json(v) for v in _sequence(raw["fields"], f"{path}.fields")]
        keys = [f.field_key for f in fields]
        if not fields or len(set(keys)) != len(keys) or not set(unique).issubset(keys):
            _fail("bounds_empty", path, "invalid fields/unique_key")


class RankingRuleV1(StrictContract):
    VERSIONED = False
    KEYS = {"metric_key", "direction", "tie_breakers", "missing_metric"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _text(raw["metric_key"], f"{path}.metric_key")
        _enum(raw["direction"], {"ascending", "descending"}, f"{path}.direction")
        _strings(raw["tie_breakers"], f"{path}.tie_breakers")
        _enum(raw["missing_metric"], {"ineligible", "last"}, f"{path}.missing_metric")


class CollectionSelectionV1(StrictContract):
    VERSIONED = False
    KEYS = {"mode", "minimum_items", "maximum_items", "as_of", "ranking_rule"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _enum(raw["mode"], {"top_n", "bounded_set"}, f"{path}.mode")
        minimum = _integer(raw["minimum_items"], f"{path}.minimum_items")
        maximum = _integer(raw["maximum_items"], f"{path}.maximum_items")
        if maximum < minimum:
            _fail("bounds_empty", path, "maximum_items is below minimum_items")
        _text(raw["as_of"], f"{path}.as_of", nullable=True)
        if raw["ranking_rule"] is not None:
            RankingRuleV1.from_json(raw["ranking_rule"])


class CollectionDedupeV1(StrictContract):
    VERSIONED = False
    KEYS = {"normalizer", "collision_policy"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        if raw["normalizer"] != "nfkc_casefold_v1" or raw["collision_policy"] != "merge_equal_identity_else_conflict":
            _fail("spec_keys_differ", path, "unsupported v1 dedupe policy")


class CollectionRequirement(StrictContract):
    KEYS = _COMMON_REQUIREMENT_KEYS | {"item_schema", "selection", "dedupe"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _validate_common_requirement(raw, path, "collection")
        CollectionItemSchemaV1.from_json(raw["item_schema"])
        CollectionSelectionV1.from_json(raw["selection"])
        CollectionDedupeV1.from_json(raw["dedupe"])


class ClaimKindRuleV1(StrictContract):
    VERSIONED = False
    KEYS = {"claim_kind", "minimum_claims", "maximum_claims", "minimum_admitted_bindings", "support_rule"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _enum(raw["claim_kind"], {"conclusion", "limitation", "counterevidence", "uncertainty", "policy_fact", "impact_inference"}, f"{path}.claim_kind")
        minimum = _integer(raw["minimum_claims"], f"{path}.minimum_claims")
        maximum = _integer(raw["maximum_claims"], f"{path}.maximum_claims")
        if maximum < minimum:
            _fail("bounds_empty", path, "maximum_claims is below minimum_claims")
        _integer(raw["minimum_admitted_bindings"], f"{path}.minimum_admitted_bindings")
        _enum(raw["support_rule"], {"admitted_binding", "admitted_binding_or_registered_inference"}, f"{path}.support_rule")


class TopicFacetV1(StrictContract):
    VERSIONED = False
    KEYS = {"facet_id", "label", "minimum_claims"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _text(raw["facet_id"], f"{path}.facet_id")
        _text(raw["label"], f"{path}.label")
        _integer(raw["minimum_claims"], f"{path}.minimum_claims")


class ClaimSetRequirement(StrictContract):
    KEYS = _COMMON_REQUIREMENT_KEYS | {"claim_kinds", "topic_facets", "coverage_mode", "contradiction_policy"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _validate_common_requirement(raw, path, "claim_set")
        kinds = [ClaimKindRuleV1.from_json(v) for v in _sequence(raw["claim_kinds"], f"{path}.claim_kinds")]
        facets = [TopicFacetV1.from_json(v) for v in _sequence(raw["topic_facets"], f"{path}.topic_facets")]
        if not kinds or len({v.claim_kind for v in kinds}) != len(kinds):
            _fail("bounds_empty", f"{path}.claim_kinds", "must be non-empty and unique")
        if len({v.facet_id for v in facets}) != len(facets):
            _fail("bounds_empty", f"{path}.topic_facets", "facet ids must be unique")
        _enum(raw["coverage_mode"], {"all_minima"}, f"{path}.coverage_mode")
        _enum(raw["contradiction_policy"], {"surface", "block_completed"}, f"{path}.contradiction_policy")


RequirementV1: TypeAlias = ScalarRequirement | MatrixRequirement | CollectionRequirement | ClaimSetRequirement


def requirement_from_json(value: Mapping[str, Any]) -> RequirementV1:
    kind = value.get("kind") if isinstance(value, Mapping) else None
    cls = {
        "scalar": ScalarRequirement,
        "matrix": MatrixRequirement,
        "collection": CollectionRequirement,
        "claim_set": ClaimSetRequirement,
    }.get(kind)
    if cls is None:
        _fail("requirement_kind_unsupported", "$.kind", f"unsupported kind {kind!r}")
    return cls.from_json(value)


def build_requirement(payload: Mapping[str, JsonValue], ordinal: int) -> RequirementV1:
    value = copy.deepcopy(dict(payload))
    value["schema_version"] = 1
    value["ordinal"] = ordinal
    value["requirement_id"] = derive_requirement_id(value)
    return requirement_from_json(value)


class WorkDimensionV1(StrictContract):
    VERSIONED = False
    KEYS = {"dimension_id", "ordinal", "requirement_ids", "search_concepts", "time_scope", "source_constraint"}

    @classmethod
    def _validate(cls, raw: dict[str, Any], path: str) -> None:
        _text(raw["dimension_id"], f"{path}.dimension_id")
        _integer(raw["ordinal"], f"{path}.ordinal")
        if not _strings(raw["requirement_ids"], f"{path}.requirement_ids"):
            _fail("reference_missing", f"{path}.requirement_ids", "must not be empty")
        _strings(raw["search_concepts"], f"{path}.search_concepts", set_like=True)
        TimeScopeV1.from_json(raw["time_scope"])
        SourceConstraintV1.from_json(raw["source_constraint"])


@dataclass(frozen=True, slots=True)
class ResearchSpecV1:
    _value: dict[str, JsonValue]
    KEYS: ClassVar[set[str]] = {
        "schema_version", "spec_id", "spec_hash", "normalized_question", "intent_type",
        "answer_locale", "subjects", "user_constraints", "work_dimensions", "requirements",
        "completion_policy_ref", "completion_policy_hash", "render_profile_ref",
        "render_profile_hash", "compiler_policy_hash",
    }

    def __post_init__(self) -> None:
        raw = _object(self._value, self.KEYS, "$ResearchSpecV1")
        _text(raw["normalized_question"], "$.normalized_question")
        _enum(raw["intent_type"], {"official_exact_fact", "comparison", "top_n", "policy", "open_research"}, "$.intent_type")
        _text(raw["answer_locale"], "$.answer_locale")
        subjects = [SubjectV1.from_json(v) for v in _sequence(raw["subjects"], "$.subjects")]
        if not subjects or len({s.subject_id for s in subjects}) != len(subjects):
            _fail("duplicate_requirement_id", "$.subjects", "subject ids must be non-empty and unique")
        UserConstraintsV1.from_json(raw["user_constraints"])
        requirements = [requirement_from_json(v) for v in _sequence(raw["requirements"], "$.requirements")]
        if not requirements:
            _fail("bounds_empty", "$.requirements", "must not be empty")
        if [r.ordinal for r in requirements] != list(range(len(requirements))):
            _fail("ordinal_invalid", "$.requirements", "ordinals must be contiguous from zero")
        keys = [r.key for r in requirements]
        ids = [r.requirement_id for r in requirements]
        if len(set(keys)) != len(keys):
            _fail("duplicate_requirement_key", "$.requirements", "duplicate key")
        if len(set(ids)) != len(ids):
            _fail("duplicate_requirement_id", "$.requirements", "duplicate id")
        subject_ids = {s.subject_id for s in subjects}
        for index, requirement in enumerate(requirements):
            if not set(requirement.subject_ids).issubset(subject_ids):
                _fail("reference_missing", f"$.requirements[{index}].subject_ids", "unknown subject")
        dimensions = [WorkDimensionV1.from_json(v) for v in _sequence(raw["work_dimensions"], "$.work_dimensions")]
        if [d.ordinal for d in dimensions] != list(range(len(dimensions))):
            _fail("ordinal_invalid", "$.work_dimensions", "ordinals must be contiguous from zero")
        if len({d.dimension_id for d in dimensions}) != len(dimensions):
            _fail("duplicate_requirement_id", "$.work_dimensions", "duplicate dimension id")
        requirement_ids = set(ids)
        for index, dimension in enumerate(dimensions):
            if not set(dimension.requirement_ids).issubset(requirement_ids):
                _fail("reference_missing", f"$.work_dimensions[{index}].requirement_ids", "unknown requirement")
        for prefix in ("completion_policy", "render_profile"):
            digest = _digest(raw[f"{prefix}_hash"], f"$.{prefix}_hash")
            if parse_blob_ref(raw[f"{prefix}_ref"]) != digest:
                _fail("policy_ref_hash_mismatch", f"$.{prefix}_ref", "ref digest differs from hash")
        _digest(raw["compiler_policy_hash"], "$.compiler_policy_hash")
        expected_hash = derive_spec_hash(raw)
        if raw["spec_hash"] != expected_hash or raw["spec_id"] != "rs_" + expected_hash[:24]:
            _fail("spec_hash_mismatch", "$.spec_hash", f"expected {expected_hash}")
        object.__setattr__(self, "_value", copy.deepcopy(raw))

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "ResearchSpecV1":
        return cls(copy.deepcopy(dict(value)))

    @classmethod
    def create(cls, **values: JsonValue) -> "ResearchSpecV1":
        payload = copy.deepcopy(values)
        payload["schema_version"] = 1
        payload.pop("spec_id", None)
        payload.pop("spec_hash", None)
        spec_hash = sha256_json(payload)
        payload["spec_hash"] = spec_hash
        payload["spec_id"] = "rs_" + spec_hash[:24]
        return cls.from_json(payload)

    def to_json(self) -> dict[str, JsonValue]:
        return copy.deepcopy(self._value)

    def __getattr__(self, name: str) -> Any:
        try:
            return copy.deepcopy(self._value[name])
        except KeyError as exc:
            raise AttributeError(name) from exc


def canonical_set(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


def build_route_decision_from_spec(
    spec: ResearchSpecV1,
    *,
    run_id: str,
    policy_hash: str,
    capability_snapshot_hash: str,
    budget: Mapping[str, int],
    source_health: Iterable[Mapping[str, JsonValue]] = (),
) -> RouteDecisionV1:
    """Derive stable work groups without changing the frozen spec.

    Each work dimension may execute independently, while its merge key points
    back to the sole main-graph assessment input. Lanes may propose evidence;
    they never own an assessment or a mutable ledger.
    """

    raw = spec.to_json()
    work_groups: list[dict[str, JsonValue]] = []
    for dimension in raw["work_dimensions"]:
        requirement_ids = sorted(str(value) for value in dimension["requirement_ids"])
        merge_key = "assessment:" + "+".join(requirement_ids) + ":" + str(dimension["dimension_id"])
        identity: dict[str, JsonValue] = {
            "spec_hash": spec.spec_hash,
            "dimension_id": str(dimension["dimension_id"]),
            "requirement_ids": requirement_ids,
            "merge_key": merge_key,
        }
        work_groups.append(
            {
                "work_group_id": "wg_" + sha256_json(identity)[:24],
                "requirement_ids": requirement_ids,
                "merge_key": merge_key,
            }
        )
    work_groups.sort(key=lambda item: str(item["work_group_id"]))
    normalized_budget = {
        key: int(budget.get(key, 0))
        for key in ("query", "fetch", "browser", "llm", "lane")
    }
    eligible = (
        spec.intent_type != "official_exact_fact"
        and len(work_groups) >= 3
        and normalized_budget["lane"] >= len(work_groups)
    )
    if spec.intent_type == "official_exact_fact":
        reason_codes = ["exact_fact_serial_route"]
        initial_route = "official_source_search"
    elif eligible:
        reason_codes = ["conditional_fanout_eligible"]
        initial_route = "conditional_fanout"
    else:
        reason_codes = ["serial_route_selected"]
        initial_route = "general_search"
    return RouteDecisionV1.create(
        run_id=run_id,
        spec_hash=spec.spec_hash,
        policy_ref=format_blob_ref(policy_hash),
        policy_hash=policy_hash,
        capability_snapshot_hash=capability_snapshot_hash,
        source_health=[copy.deepcopy(dict(item)) for item in source_health],
        budget=normalized_budget,
        work_groups=work_groups,
        initial_route=initial_route,
        allowed_escalations=[
            {
                "from_route": "general_search",
                "to_route": "conditional_fanout",
                "condition_code": "independent_work_groups_available",
            }
        ],
        reason_codes=reason_codes,
    )


__all__ = [
    "AxisMemberV1", "AxisV1", "CardinalityV1", "ClaimKindRuleV1",
    "ClaimSetRequirement", "CollectionDedupeV1", "CollectionFieldV1",
    "CollectionItemSchemaV1", "CollectionRequirement", "CollectionSelectionV1",
    "CoverageV1", "MatrixCellPolicyV1", "MatrixRequirement", "RankingRuleV1",
    "RequiredCellsV1", "RequirementV1", "ResearchSpecV1", "ResearchSpecValidationError",
    "RouteDecisionV1",
    "ScalarRequirement", "ScopeV1", "SourceConstraintV1", "SubjectV1",
    "TimeScopeV1", "ToleranceV1", "TopicFacetV1", "UnitV1",
    "UserConstraintsV1", "ValueSchemaV1", "WorkDimensionV1", "build_requirement",
    "build_route_decision_from_spec", "canonical_set", "derive_requirement_id", "derive_spec_hash", "derive_spec_id",
    "format_blob_ref", "parse_blob_ref", "requirement_from_json", "sha256_json",
    "conditional_fanout_enabled", "conditional_fanout_reason",
    "validate_continuation_spec_hash",
]
