from __future__ import annotations

import json

from deskpet.execution.contracts import (
    DeliveryPolicy,
    DeliverySpec,
    PersistenceLevel,
    RunContext,
    RunCreate,
    fingerprint_json,
)
from deskpet.harness.contracts import HostContext, PreparedRunContextV1, RunRequest
from deskpet.harness.start_snapshot import (
    build_run_start_snapshot,
    terminal_deliveries_from_snapshot,
)


def _fixture():
    host = HostContext(
        session_id="session-1",
        principal_id="principal-1",
        auth_epoch=2,
        capability_hash=fingerprint_json({"caps": ["read"]}),
        available_capabilities=frozenset({"read"}),
        provider_plan=("openai",),
        trace_id="trace-1",
    )
    context = RunContext(
        session_id=host.session_id,
        root_run_id="run-1",
        parent_run_id=None,
        request_id="request-1",
        turn_id="turn-1",
        venue="text",
        workspace={},
        capability_hash=host.capability_hash,
        provider_plan={"providers": ["openai"]},
        trace_id=host.trace_id,
        principal_id=host.principal_id,
        auth_epoch=host.auth_epoch,
    )
    spec = RunCreate(
        run_id="run-1",
        idempotency_key="root:run-1",
        context=context,
        payload_fingerprint=fingerprint_json({"text": "hello"}),
        capability_fingerprint=host.capability_hash,
        driver_kind="react",
        profile_key="general",
        persistence_level=PersistenceLevel.DURABLE,
    )
    prepared = PreparedRunContextV1(
        persistence_required=True,
        product_snapshot_ref="snapshot-1",
        product_snapshot_hash=fingerprint_json({"snapshot": 1}),
        capability_lease_intent_ref="lease-1",
        capability_lease_intent_hash=fingerprint_json({"lease": 1}),
        frozen_terminal_deliveries=(
            DeliverySpec(
                sink_kind="session_message",
                sink_instance="main",
                target_id="session-1",
                policy=DeliveryPolicy.DURABLE_REQUIRED,
            ),
        ),
    )
    return host, spec, prepared


def test_snapshot_is_stable_across_created_at_and_redacts_credentials():
    host, spec, prepared = _fixture()
    request = RunRequest(
        text="use sk-abcdefghijklmnop",
        request_id="request-1",
        turn_id="turn-1",
        payload={"api_key": "secret-value", "session_projection_cursor": 4},
    )

    first = build_run_start_snapshot(
        spec=spec, request=request, host=host, prepared=prepared, created_at=1.0
    )
    replay = build_run_start_snapshot(
        spec=spec, request=request, host=host, prepared=prepared, created_at=2.0
    )

    assert first.start_fingerprint == replay.start_fingerprint
    assert first.created_at != replay.created_at
    sanitized = json.loads(first.sanitized_request_json)
    assert sanitized["payload"]["api_key"] == "[REDACTED]"
    assert "sk-abcdefghijklmnop" not in first.sanitized_request_json
    assert first.capability_lease_intent_ref == "lease-1"


def test_delivery_order_does_not_change_start_fingerprint():
    host, spec, prepared = _fixture()
    second = DeliverySpec(
        sink_kind="receipt",
        sink_instance="main",
        target_id="session-1",
        policy=DeliveryPolicy.DURABLE_REQUIRED,
    )
    left = PreparedRunContextV1(
        persistence_required=True,
        frozen_terminal_deliveries=(
            *prepared.frozen_terminal_deliveries,
            second,
        ),
    )
    right = PreparedRunContextV1(
        persistence_required=True,
        frozen_terminal_deliveries=(
            second,
            *prepared.frozen_terminal_deliveries,
        ),
    )
    request = RunRequest(
        text="hello", request_id="request-1", turn_id="turn-1"
    )

    left_snapshot = build_run_start_snapshot(
        spec=spec, request=request, host=host, prepared=left, created_at=1.0
    )
    right_snapshot = build_run_start_snapshot(
        spec=spec, request=request, host=host, prepared=right, created_at=1.0
    )

    assert (
        left_snapshot.terminal_deliveries_hash
        == right_snapshot.terminal_deliveries_hash
    )
    assert left_snapshot.start_fingerprint == right_snapshot.start_fingerprint
    assert terminal_deliveries_from_snapshot(left_snapshot) == (
        second,
        *prepared.frozen_terminal_deliveries,
    )
