"""Independent public SDK observations, including failed and nonterminal attempts."""

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum

from simple_harness.contracts import thaw_json
from simple_harness.execution.audit import audit_hash, audit_reference

logger = logging.getLogger(__name__)


def wire(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): wire(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [wire(v) for v in value]
    if is_dataclass(value):
        return {f.name: wire(getattr(value, f.name)) for f in fields(value)}
    raise TypeError("unsupported_corpus_evidence_type:" + type(value).__name__)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            wire(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _provider_wire(invocation):
    return {
        key: wire(getattr(invocation, key))
        for key in (
            "invocation_id",
            "request_id",
            "state",
            "request_fingerprint",
            "request_json",
            "response_json",
            "usage_json",
            "error_code",
            "claimed_at",
            "handed_off_at",
            "settled_at",
            "version",
            "handoff_attempt",
            "rehandoff_count",
        )
    }


def _read_providers(uow, run_id, retained):

    providers = {}
    after = 0
    for _ in range(32):
        receipts = uow.list_provider_projection_receipts(after_sequence=after, limit=256)
        for receipt in receipts:
            if receipt.sequence <= after:
                raise ValueError("corpus_provider_projection_order")
            after = receipt.sequence
            if receipt.run_id != run_id:
                continue
            if audit_hash(thaw_json(receipt.payload)) != receipt.payload_hash:
                raise ValueError("corpus_provider_projection_hash")
            invocation = uow.read_provider_invocation(receipt.invocation_id)
            if invocation is None or invocation.run_id.value != run_id:
                raise ValueError("corpus_provider_projection_identity")
            if receipt.invocation_version == invocation.version:
                # Explicit allowlist excludes credential resolvers, HTTP headers,
                # exception strings and model-hidden reasoning continuations.
                providers[receipt.invocation_id] = _provider_wire(invocation)
                retained[:] = providers.values()
        if len(receipts) < 256:
            break
    else:
        raise ValueError("corpus_provider_projection_limit")
    # Projection receipts are settlement-oriented. The existing public reader
    # supplies genuine claimed/handed_off/unknown attempts before terminal.
    incomplete = uow.list_incomplete_provider_invocations()
    if len(incomplete) > 256:
        raise ValueError("corpus_incomplete_provider_limit")
    for invocation in incomplete:
        if invocation.run_id.value != run_id:
            continue
        current = uow.read_provider_invocation(invocation.invocation_id)
        if current != invocation:
            raise ValueError("corpus_incomplete_provider_changed_during_observation")
        providers[invocation.invocation_id] = _provider_wire(invocation)
        retained[:] = providers.values()
    return list(providers.values())


def read_public_trace(uow, run_id):
    """Independent observations: no terminal is not permission to erase attempts."""
    # 按需导入：测试替身（simple_harness）
    from simple_harness import RunId

    result = dict(
        sdk_run_id=run_id,
        terminal=None,
        terminal_status="UNAVAILABLE",
        providers=[],
        operation_audit=None,
        observation_errors={},
    )
    try:
        terminal = uow.read_run_terminal_record(RunId(run_id))
        result.update(
            terminal=wire(terminal),
            terminal_status="NONTERMINAL" if terminal is None else "TERMINAL",
        )
    except Exception as exc:
        logger.debug("read_public_trace_read_run_terminal_record_failed", exc_info=True)
        result["observation_errors"]["terminal"] = type(exc).__name__
    try:
        _read_providers(uow, run_id, result["providers"])
    except Exception as exc:
        logger.debug("read_public_trace_read_providers_failed", exc_info=True)
        result["observation_errors"]["providers"] = type(exc).__name__
    try:
        audit = uow.read_run_operation_audit(RunId(run_id), limit=4096)
        result["operation_audit"] = wire(audit)
        if audit.run_id != run_id or audit.truncated:
            raise ValueError("corpus_operation_trace_incomplete")
        if audit.coverage_gaps:
            raise ValueError("corpus_operation_audit_coverage_gap")
        expected = {
            op.provider_invocation_id
            for op in audit.operations
            if op.kind == "provider" and op.record_type == "head"
        }
        observed = {
            audit_reference("provider", item["invocation_id"]) for item in result["providers"]
        }
        if expected != observed:
            raise ValueError("corpus_provider_audit_projection_incomplete")
    except Exception as exc:
        logger.debug("read_public_trace_read_run_operation_audit_failed", exc_info=True)
        result["observation_errors"]["operation_audit"] = type(exc).__name__
    result["trace_status"] = "PARTIAL" if result["observation_errors"] else "COMPLETE"
    result["provider_observation_complete"] = not any(
        key in result["observation_errors"] for key in ("providers", "operation_audit")
    )
    return {**result, "trace_hash": digest(result)}
