# SPDX-License-Identifier: BUSL-1.1

"""Fail-closed structural validation for the frozen S1 TaskScope protocol.

The Host intentionally does not import or pin the S1 wheel in S4.  These
validators accept the frozen public object surface, recompute canonical
hashes, and reject structural drift or private/credential-bearing values.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Any


class TaskScopeProtocolError(ValueError):
    code = "task_scope_protocol_rejected"


_DISCLOSURE_KEYS = {
    "schema_version", "run_id", "subject", "recipient", "recipient_id",
    "intended_audience", "purpose", "source", "trust", "generation",
    "authority_ref", "reason_codes",
}
_REF_KEYS = {"evidence_id", "content_hash", "ordinal"}
_EXECUTION_KEYS = {
    "schema_version", "event_id", "run_id", "subject", "kind",
    "public_payload", "disclosure_context", "evidence_refs",
    "idempotency_key", "occurred_at",
}
_PLAN_KEYS = {
    "schema_version", "plan_id", "run_id", "subject", "task_scope_id",
    "base_revision", "outcome", "operations", "closure_reason",
    "source_turn_id", "disclosure_context", "evidence_refs", "idempotency_key",
}
_OPERATION_KEYS = {"operation_id", "kind", "value", "evidence_refs", "reason_code"}
_EXECUTION_KINDS = {
    "provider_invocation", "tool_invocation", "context_snapshot",
    "route_decision", "run_terminal",
}
_MUTATION_KINDS = {
    "goal.set", "goal.revise", "scope.include", "scope.exclude",
    "decision.record", "decision.supersede", "plan.step.add",
    "plan.step.revise", "plan.step.cancel", "plan.reorder", "task.pause",
    "task.block", "task.resume", "task.complete", "resume.update",
    "checkpoint.request", "relation.add",
}
_FORBIDDEN_KEYS = {
    "api_key", "access_token", "authorization", "authorization_header",
    "cookie", "password", "private_key", "reasoning", "reasoning_content",
    "thinking", "chain_of_thought",
}
_CREDENTIAL_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\b(?:tsk|key)_[A-Za-z0-9_-]{8,}\b"),
)


def canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise TaskScopeProtocolError("non_canonical_json_value") from exc


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def identifier(value: object, name: str, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise TaskScopeProtocolError(f"{name}_invalid")
    if len(value.encode("utf-8")) > maximum:
        raise TaskScopeProtocolError(f"{name}_too_large")
    return value


def digest(value: object, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise TaskScopeProtocolError(f"{name}_invalid")
    return value


def _schema_v1(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != 1:
        raise TaskScopeProtocolError(f"{name}_schema_rejected")


def _reject_private(value: object, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise TaskScopeProtocolError(f"{path}_key_invalid")
            if key.strip().lower().replace("-", "_") in _FORBIDDEN_KEYS:
                raise TaskScopeProtocolError(f"forbidden_durable_field:{path}.{key}")
            _reject_private(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_private(child, f"{path}[{index}]")
    elif isinstance(value, str) and any(pattern.search(value) for pattern in _CREDENTIAL_PATTERNS):
        raise TaskScopeProtocolError(f"credential_value_rejected:{path}")


def reject_private_payload(value: object, path: str = "payload") -> None:
    """Public Host-side defensive scan for values about to become durable."""

    _reject_private(value, path)


CREDENTIAL_REDACTION_PLACEHOLDER = "[redacted:credential]"


def redact_credential_shapes(text: str) -> tuple[str, bool]:
    """Deterministically replace credential-shaped fragments in *text*.

    S5b Task 2 review F-1: model-supplied public facts (file paths, targets)
    recorded *after* the SDK settled an effect must never make the Host raise;
    every fragment matching a ``_CREDENTIAL_PATTERNS`` shape becomes the stable
    placeholder and the caller records ``redacted=True``.  The result always
    passes :func:`reject_private_payload`.
    """

    redacted = False
    result = text
    for pattern in _CREDENTIAL_PATTERNS:
        result, count = pattern.subn(CREDENTIAL_REDACTION_PLACEHOLDER, result)
        redacted = redacted or count > 0
    return result, redacted


def _validate_disclosure(value: object, run_id: str, subject: str) -> None:
    if not isinstance(value, Mapping) or set(value) != _DISCLOSURE_KEYS:
        raise TaskScopeProtocolError("disclosure_context_fields_differ")
    _schema_v1(value.get("schema_version"), "disclosure_context")
    if value.get("run_id") != run_id or value.get("subject") != subject:
        raise TaskScopeProtocolError("disclosure_context_binding_mismatch")
    for name in ("recipient", "intended_audience", "purpose", "source", "trust", "generation"):
        identifier(value.get(name), f"disclosure_{name}", 256)
    for name in ("recipient_id", "authority_ref"):
        if value.get(name) is not None:
            identifier(value[name], f"disclosure_{name}", 256)
    reasons = value.get("reason_codes")
    if not isinstance(reasons, list) or not all(isinstance(item, str) and item for item in reasons) or len(reasons) != len(set(reasons)):
        raise TaskScopeProtocolError("disclosure_reason_codes_invalid")


def validate_refs(value: object, *, required: bool = False) -> list[dict[str, object]]:
    if not isinstance(value, list) or (required and not value):
        raise TaskScopeProtocolError("evidence_refs_invalid")
    result: list[dict[str, object]] = []
    for ordinal, item in enumerate(value, 1):
        if not isinstance(item, Mapping) or set(item) != _REF_KEYS:
            raise TaskScopeProtocolError("evidence_ref_fields_differ")
        if item.get("ordinal") != ordinal or isinstance(item.get("ordinal"), bool):
            raise TaskScopeProtocolError("evidence_ref_ordinal_invalid")
        result.append({
            "evidence_id": identifier(item.get("evidence_id"), "evidence_ref_id"),
            "content_hash": digest(item.get("content_hash"), "evidence_ref_content_hash"),
            "ordinal": ordinal,
        })
    if len({item["evidence_id"] for item in result}) != len(result):
        raise TaskScopeProtocolError("evidence_ref_duplicate")
    return result


def _object_json(value: object, expected: set[str], name: str) -> dict[str, Any]:
    to_json = getattr(value, "to_json", None)
    if not callable(to_json):
        raise TaskScopeProtocolError(f"{name}_required")
    raw = to_json()
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise TaskScopeProtocolError(f"{name}_fields_differ")
    return dict(raw)


def _surface(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _surface(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_surface(child) for child in value]
    to_json = getattr(value, "to_json", None)
    if callable(to_json):
        return _surface(to_json())
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    return value


def validate_execution_evidence(value: object) -> tuple[dict[str, Any], str]:
    raw = _object_json(value, _EXECUTION_KEYS, "execution_evidence")
    _schema_v1(raw.get("schema_version"), "execution_evidence")
    if getattr(value, "schema_version", None) != raw.get("schema_version"):
        raise TaskScopeProtocolError("execution_evidence_object_json_mismatch")
    for name in ("event_id", "run_id", "subject", "idempotency_key"):
        if getattr(value, name, None) != raw.get(name):
            raise TaskScopeProtocolError("execution_evidence_object_json_mismatch")
        identifier(raw.get(name), name, 512)
    kind = getattr(getattr(value, "kind", None), "value", getattr(value, "kind", None))
    if kind != raw.get("kind") or kind not in _EXECUTION_KINDS:
        raise TaskScopeProtocolError("execution_evidence_kind_rejected")
    payload = raw.get("public_payload")
    if not isinstance(payload, Mapping):
        raise TaskScopeProtocolError("execution_evidence_payload_invalid")
    if (
        _surface(getattr(value, "public_payload", None)) != payload
        or _surface(getattr(value, "disclosure_context", None)) != raw.get("disclosure_context")
        or _surface(getattr(value, "evidence_refs", None)) != raw.get("evidence_refs")
        or getattr(value, "occurred_at", None) != raw.get("occurred_at")
    ):
        raise TaskScopeProtocolError("execution_evidence_object_json_mismatch")
    _reject_private(payload)
    _validate_disclosure(raw.get("disclosure_context"), raw["run_id"], raw["subject"])
    validate_refs(raw.get("evidence_refs"))
    occurred = raw.get("occurred_at")
    if isinstance(occurred, bool) or not isinstance(occurred, (int, float)) or not math.isfinite(float(occurred)) or occurred < 0:
        raise TaskScopeProtocolError("execution_evidence_occurred_at_invalid")
    evidence_hash = canonical_hash(raw)
    if digest(getattr(value, "evidence_hash", None), "evidence_hash") != evidence_hash:
        raise TaskScopeProtocolError("execution_evidence_hash_mismatch")
    return raw, evidence_hash


def validate_mutation_plan(value: object) -> tuple[dict[str, Any], str]:
    raw = _object_json(value, _PLAN_KEYS, "task_scope_mutation_plan")
    _schema_v1(raw.get("schema_version"), "task_scope_mutation_plan")
    if getattr(value, "schema_version", None) != raw.get("schema_version"):
        raise TaskScopeProtocolError("mutation_plan_object_json_mismatch")
    for name in ("plan_id", "run_id", "subject", "task_scope_id", "source_turn_id", "idempotency_key"):
        if getattr(value, name, None) != raw.get(name):
            raise TaskScopeProtocolError("mutation_plan_object_json_mismatch")
        identifier(raw.get(name), name, 512)
    base_revision = raw.get("base_revision")
    if isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 1:
        raise TaskScopeProtocolError("base_revision_invalid")
    outcome = getattr(getattr(value, "outcome", None), "value", getattr(value, "outcome", None))
    if outcome != raw.get("outcome") or outcome not in {"mutate", "no_mutation"}:
        raise TaskScopeProtocolError("mutation_outcome_rejected")
    operations = raw.get("operations")
    if not isinstance(operations, list):
        raise TaskScopeProtocolError("mutation_operations_invalid")
    if (
        _surface(getattr(value, "operations", None)) != operations
        or _surface(getattr(value, "disclosure_context", None)) != raw.get("disclosure_context")
        or _surface(getattr(value, "evidence_refs", None)) != raw.get("evidence_refs")
        or getattr(value, "base_revision", None) != base_revision
        or getattr(value, "closure_reason", None) != raw.get("closure_reason")
    ):
        raise TaskScopeProtocolError("mutation_plan_object_json_mismatch")
    if (outcome == "mutate") != bool(operations):
        raise TaskScopeProtocolError("mutation_outcome_operations_mismatch")
    seen: set[str] = set()
    for operation in operations:
        if not isinstance(operation, Mapping) or set(operation) != _OPERATION_KEYS:
            raise TaskScopeProtocolError("mutation_operation_fields_differ")
        operation_id = identifier(operation.get("operation_id"), "operation_id", 512)
        if operation_id in seen:
            raise TaskScopeProtocolError("mutation_operation_duplicate")
        seen.add(operation_id)
        if operation.get("kind") not in _MUTATION_KINDS:
            raise TaskScopeProtocolError("mutation_kind_rejected")
        identifier(operation.get("value"), "operation_value", 32_768)
        identifier(operation.get("reason_code"), "reason_code", 512)
        validate_refs(operation.get("evidence_refs"), required=True)
    closure = raw.get("closure_reason")
    if outcome == "no_mutation" and (not isinstance(closure, str) or not closure):
        raise TaskScopeProtocolError("no_mutation_closure_reason_required")
    if closure is not None:
        identifier(closure, "closure_reason", 4096)
    _validate_disclosure(raw.get("disclosure_context"), raw["run_id"], raw["subject"])
    validate_refs(raw.get("evidence_refs"), required=True)
    _reject_private(raw)
    plan_hash = canonical_hash(raw)
    if digest(getattr(value, "plan_hash", None), "plan_hash") != plan_hash:
        raise TaskScopeProtocolError("mutation_plan_hash_mismatch")
    return raw, plan_hash
