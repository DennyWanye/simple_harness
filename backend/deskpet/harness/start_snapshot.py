"""Canonical immutable start snapshots for durable executions."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from typing import Any

from deskpet.execution.contracts import (
    DeliveryPolicy,
    DeliverySpec,
    JsonValue,
    RunCreate,
    RunStartSnapshotRecord,
    canonical_json,
    fingerprint_json,
)
from deskpet.security.redaction import TraceRedactor

from .contracts import (
    HostContext,
    HostExtensionRefV1,
    PreparedRunContextV1,
    RunRequest,
)


def _delivery_payload(deliveries: Sequence[DeliverySpec]) -> list[dict[str, JsonValue]]:
    return [
        {
            "policy": item.policy.value,
            "sink_instance": item.sink_instance,
            "sink_kind": item.sink_kind,
            "target_id": item.target_id,
        }
        for item in sorted(
            deliveries,
            key=lambda value: (
                value.sink_kind,
                value.sink_instance,
                value.target_id,
                value.policy.value,
            ),
        )
    ]


def _prepared_refs(prepared: PreparedRunContextV1) -> dict[str, JsonValue]:
    return {
        "host_extensions": {
            key: value.to_dict()
            for key, value in sorted(prepared.host_extensions.items())
        },
        "prepared_fingerprint": prepared.prepared_fingerprint,
        "prepared_tool_hash": prepared.prepared_tool_hash,
        "prepared_tool_ref": prepared.prepared_tool_ref,
        "product_snapshot_hash": prepared.product_snapshot_hash,
        "product_snapshot_ref": prepared.product_snapshot_ref,
        "schema_version": prepared.schema_version,
    }


def _provider_policy(
    request: RunRequest, host: HostContext
) -> Mapping[str, JsonValue]:
    if request.provider_launch_snapshot is not None:
        return {
            "admission_provider": request.provider_launch_snapshot.to_dict(),
            "provider_plan": list(host.provider_plan),
        }
    return {"provider_plan": list(host.provider_plan)}


def _trusted_context_os(
    request_payload: Mapping[str, Any],
    prepared: PreparedRunContextV1,
) -> dict[str, Any] | None:
    """Return the exact host-frozen ToolSet only when its reference matches."""

    raw = request_payload.get("context_os")
    if not isinstance(raw, Mapping):
        return None
    if (
        not prepared.prepared_tool_ref
        or prepared.prepared_tool_hash != prepared.prepared_tool_ref
    ):
        return None

    from deskpet.capabilities.refresh import context_os_snapshot_ref
    from deskpet.tools.prepared_snapshot import load_context_os_snapshot

    tool_set, eligibility = load_context_os_snapshot(raw)
    if (
        context_os_snapshot_ref(tool_set, eligibility)
        != prepared.prepared_tool_ref
    ):
        raise ValueError(
            "request Context OS snapshot differs from prepared ToolSet"
        )
    return copy.deepcopy(dict(raw))


def build_run_start_snapshot(
    *,
    spec: RunCreate,
    request: RunRequest,
    host: HostContext,
    prepared: PreparedRunContextV1,
    created_at: float,
    redactor: TraceRedactor | None = None,
) -> RunStartSnapshotRecord:
    """Freeze all launch inputs before a durable Run can perform an action."""

    scrubber = redactor or TraceRedactor()
    canonical_messages = list(
        request.canonical_messages
        or ({"role": "user", "content": request.text},)
    )
    session_cursor = {
        "session_projection_cursor": int(
            request.payload.get("session_projection_cursor", 0)
        )
    }
    request_payload = dict(request.payload)
    trusted_context_os = _trusted_context_os(request_payload, prepared)
    sanitized_request = scrubber.redact(
        {"payload": request_payload, "text": request.text}
    )
    if trusted_context_os is not None:
        sanitized_payload = sanitized_request.get("payload")
        if not isinstance(sanitized_payload, dict):
            raise ValueError("sanitized request payload is malformed")
        # Context OS is host-produced, content-addressed execution metadata.
        # Redacting prose inside a frozen schema changes its hash and makes a
        # durable Skill Run impossible to replay.  User text and all other
        # request fields remain under the normal secret redactor.
        sanitized_payload["context_os"] = trusted_context_os
    deliveries = _delivery_payload(prepared.frozen_terminal_deliveries)
    capability_snapshot = (
        dict(prepared.capability_snapshot)
        if prepared.capability_snapshot
        else {
            "capabilities": sorted(host.available_capabilities),
            "capability_hash": host.capability_hash,
        }
    )
    if capability_snapshot.get("capability_hash") != host.capability_hash:
        raise ValueError(
            "prepared capability snapshot differs from HostContext"
        )
    snapshot_payload: dict[str, Any] = {
        "snapshot_schema_version": 1,
        "canonical_messages": canonical_messages,
        "session_cursor": session_cursor,
        "prepared_refs": _prepared_refs(prepared),
        "sanitized_request": sanitized_request,
        "run_context": spec.context.to_dict(),
        "run_spec": spec.to_dict(),
        "capability_snapshot": capability_snapshot,
        "provider_launch_policy": _provider_policy(request, host),
        "terminal_deliveries": deliveries,
        "capability_lease_intent_ref": prepared.capability_lease_intent_ref,
        "capability_lease_intent_hash": prepared.capability_lease_intent_hash,
    }
    return RunStartSnapshotRecord(
        run_id=spec.run_id,
        snapshot_schema_version=1,
        start_fingerprint=fingerprint_json(snapshot_payload),
        canonical_messages_json=canonical_json(canonical_messages),
        session_cursor_json=canonical_json(session_cursor),
        prepared_refs_json=canonical_json(_prepared_refs(prepared)),
        sanitized_request_json=canonical_json(sanitized_request),
        run_context_json=canonical_json(spec.context.to_dict()),
        run_spec_json=canonical_json(spec.to_dict()),
        capability_snapshot_json=canonical_json(capability_snapshot),
        capability_snapshot_hash=fingerprint_json(capability_snapshot),
        provider_launch_policy_json=canonical_json(_provider_policy(request, host)),
        terminal_deliveries_json=canonical_json(deliveries),
        terminal_deliveries_hash=fingerprint_json(deliveries),
        capability_lease_intent_ref=prepared.capability_lease_intent_ref,
        capability_lease_intent_hash=prepared.capability_lease_intent_hash,
        created_at=float(created_at),
    )


def start_extension_receipts(
    snapshot: RunStartSnapshotRecord,
) -> tuple[HostExtensionRefV1, ...]:
    raw = json.loads(snapshot.start_extension_receipts_json)
    if not isinstance(raw, list):
        raise ValueError("start extension receipts must be a list")
    return tuple(HostExtensionRefV1(**item) for item in raw)


def terminal_deliveries_from_snapshot(
    snapshot: RunStartSnapshotRecord,
) -> tuple[DeliverySpec, ...]:
    raw = json.loads(snapshot.terminal_deliveries_json)
    if not isinstance(raw, list):
        raise ValueError("frozen terminal deliveries must be a list")
    return tuple(
        DeliverySpec(
            sink_kind=str(item["sink_kind"]),
            sink_instance=str(item["sink_instance"]),
            target_id=str(item["target_id"]),
            policy=DeliveryPolicy(str(item["policy"])),
        )
        for item in raw
    )


async def activate_after_start_commit(
    *,
    record: Any,
    snapshot: RunStartSnapshotRecord,
    prepared: PreparedRunContextV1,
) -> None:
    """Run host handshakes only after the immutable start is committed."""

    receipts = start_extension_receipts(snapshot)
    for handshake in prepared.after_start_commit_handshakes:
        accepted = await handshake.activate_after_start(
            record,
            start_snapshot=snapshot,
            extension_receipts=receipts,
        )
        if accepted is not True:
            raise RuntimeError(
                f"after_start_commit_handshake_rejected:{handshake.descriptor.kind}"
            )
