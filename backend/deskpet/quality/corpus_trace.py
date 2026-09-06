"""Post-terminal trace reads using public SDK APIs; no SDK SQL or inference."""
from dataclasses import fields, is_dataclass
from enum import Enum
from collections.abc import Mapping
import hashlib
import json


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
    return hashlib.sha256(json.dumps(wire(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def read_public_trace(uow, run_id):
    from simple_harness import RunId
    from simple_harness.execution.audit import audit_hash, audit_reference
    from simple_harness.contracts import thaw_json

    terminal = uow.read_run_terminal_record(RunId(run_id))
    if terminal is None:
        raise ValueError("corpus_trace_requires_actual_terminal")
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
                providers[receipt.invocation_id] = {
                    key: wire(getattr(invocation, key)) for key in (
                        "invocation_id", "request_id", "state", "request_fingerprint",
                        "request_json", "response_json", "usage_json", "error_code",
                        "claimed_at", "handed_off_at", "settled_at", "version",
                        "handoff_attempt", "rehandoff_count",
                    )}
        if len(receipts) < 256:
            break
    else:
        raise ValueError("corpus_provider_projection_limit")
    audit = uow.read_run_operation_audit(RunId(run_id), limit=4096)
    if audit.run_id != run_id or audit.truncated:
        raise ValueError("corpus_operation_trace_incomplete")
    expected = {op.provider_invocation_id for op in audit.operations
        if op.kind == "provider" and op.record_type == "head"}
    observed = {audit_reference("provider", identity) for identity in providers}
    if expected != observed:
        raise ValueError("corpus_provider_audit_projection_incomplete")
    result = dict(sdk_run_id=run_id, terminal=wire(terminal),
        providers=list(providers.values()), operation_audit=wire(audit))
    return {**result, "trace_hash": digest(result)}
