"""Independent public SDK observations, including failed and nonterminal attempts."""
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


def _read_providers(uow, run_id):
    from simple_harness.execution.audit import audit_hash
    from simple_harness.contracts import thaw_json

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
    return list(providers.values())


def read_public_trace(uow, run_id):
    """Independent observations: no terminal is not permission to erase attempts."""
    from simple_harness import RunId
    from simple_harness.execution.audit import audit_reference

    result = dict(sdk_run_id=run_id, terminal=None, terminal_status="UNAVAILABLE",
        providers=[], operation_audit=None, observation_errors={})
    try:
        terminal = uow.read_run_terminal_record(RunId(run_id))
        result.update(terminal=wire(terminal),
            terminal_status="NONTERMINAL" if terminal is None else "TERMINAL")
    except Exception as exc:
        result["observation_errors"]["terminal"] = type(exc).__name__
    try:
        result["providers"] = _read_providers(uow, run_id)
    except Exception as exc:
        result["observation_errors"]["providers"] = type(exc).__name__
    try:
        audit = uow.read_run_operation_audit(RunId(run_id), limit=4096)
        result["operation_audit"] = wire(audit)
        if audit.run_id != run_id or audit.truncated:
            raise ValueError("corpus_operation_trace_incomplete")
        expected = {op.provider_invocation_id for op in audit.operations
            if op.kind == "provider" and op.record_type == "head"}
        observed = {audit_reference("provider", item["invocation_id"]) for item in result["providers"]}
        if expected != observed:
            raise ValueError("corpus_provider_audit_projection_incomplete")
    except Exception as exc:
        result["observation_errors"]["operation_audit"] = type(exc).__name__
    result["trace_status"] = "PARTIAL" if result["observation_errors"] else "COMPLETE"
    return {**result, "trace_hash": digest(result)}


async def collect_bound_observations(*, stack, run_id, text, route_reader, queue_reader, persist):
    """Persist each successful observation before trying another read surface."""
    result = {"observation_errors": {}}
    try:
        trace = stack.read_corpus_scoring_trace(run_id)
        result["trace"] = trace
        persist("trace", trace)
        if trace["observation_errors"]:
            result["observation_errors"]["trace"] = "partial_public_trace"
    except Exception as exc:
        result["observation_errors"]["trace"] = type(exc).__name__
    try:
        result["transcript"] = wire(stack.read_primary_run_messages(run_id, current_text=text))
        persist("transcript", result["transcript"])
    except Exception as exc:
        result["observation_errors"]["transcript"] = type(exc).__name__
    try:
        result["route_audit"] = route_reader()
        persist("route_audit", result["route_audit"])
    except Exception as exc:
        result["observation_errors"]["route_audit"] = type(exc).__name__
    if "route_audit" in result:
        try:
            _, effects = stack.read_primary_dependency_facts(run_id,
                effect_ids=tuple(r["effect_id"] for r in result["route_audit"]))
            result["route_effects"] = wire(effects)
            persist("route_effects", result["route_effects"])
        except Exception as exc:
            result["observation_errors"]["route_effects"] = type(exc).__name__
    try:
        result["queue_snapshot"] = await queue_reader()
        persist("queue_snapshot", result["queue_snapshot"])
    except Exception as exc:
        result["observation_errors"]["queue_snapshot"] = type(exc).__name__
    return result
