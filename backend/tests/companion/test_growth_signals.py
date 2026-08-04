from __future__ import annotations

import pytest

from deskpet.companion.contracts import CompanionConflictError, OwnerRef
from deskpet.companion.identity_gate import FrozenOwnerIdentity, IdentityReadyGate
from deskpet.companion.signals import (
    CompanionIngressOutboxDispatcher,
    GrowthDeliveryTarget,
    GrowthIngressDispatcher,
    GrowthSignalKind,
    GrowthSignalV1,
    GrowthTerminalDeliveryContributor,
    execution_outcome_signal,
)
from deskpet.companion.store import CompanionStore
from deskpet.harness.contracts import RunRequest
from deskpet.memory.companion_message_projection import TrustedCompanionOwner
from deskpet.memory.session_db import SessionDB


def _store(tmp_path):
    store = CompanionStore(tmp_path / "companion.db")
    owner = store.create_profile(
        profile_id="profile-a",
        generation=1,
        identity_namespace_hash="a" * 64,
    )
    return store, owner


@pytest.mark.parametrize(
    "kind",
    [
        GrowthSignalKind.MESSAGE_INGRESS,
        GrowthSignalKind.EXECUTION_OUTCOME,
        GrowthSignalKind.USER_DECISION,
    ],
)
def test_three_growth_sources_share_one_replay_safe_contract(tmp_path, kind):
    store, owner = _store(tmp_path)
    dispatcher = GrowthIngressDispatcher(store)
    signal = GrowthSignalV1(
        owner=owner,
        kind=kind,
        source_ref=f"{kind.value}:source-1",
        context_key="project:alpha",
        root_run_id="run-1",
        reason_code="test_signal",
        payload={"summary": "non-sensitive", "success": True},
    )

    first = dispatcher.deliver(signal)
    replay = dispatcher.deliver(signal)

    assert first["event_id"] == replay["event_id"] == signal.event_id
    assert first["event_hash"] == replay["event_hash"]


def test_same_source_with_different_payload_is_a_conflict(tmp_path):
    store, owner = _store(tmp_path)
    dispatcher = GrowthIngressDispatcher(store)
    base = dict(
        owner=owner,
        kind=GrowthSignalKind.MESSAGE_INGRESS,
        source_ref="message:session-1:7",
        context_key="session:session-1",
        root_run_id="run-1",
        reason_code="message_committed",
    )
    dispatcher.deliver(GrowthSignalV1(**base, payload={"intent": "brief"}))

    with pytest.raises(CompanionConflictError):
        dispatcher.deliver(GrowthSignalV1(**base, payload={"intent": "verbose"}))


def test_retry_requires_explicit_stable_request_and_run_links(tmp_path):
    _, owner = _store(tmp_path)
    with pytest.raises(ValueError, match="retry_links_incomplete"):
        GrowthSignalV1(
            owner=owner,
            kind=GrowthSignalKind.MESSAGE_INGRESS,
            source_ref="message:session-1:8",
            context_key="session:session-1",
            root_run_id="run-2",
            reason_code="message_retry",
            payload={},
            previous_request_id="request-1",
        )

    signal = GrowthSignalV1(
        owner=owner,
        kind=GrowthSignalKind.MESSAGE_INGRESS,
        source_ref="message:session-1:8",
        context_key="session:session-1",
        root_run_id="run-2",
        reason_code="message_retry",
        payload={},
        previous_request_id="request-1",
        previous_run_id="run-1",
        current_request_id="request-2",
        current_run_id="run-2",
    )
    assert signal.to_growth_event().payload["retry_links"]["previous_run_id"] == "run-1"


@pytest.mark.parametrize(
    "payload",
    [
        {"access_token": "tsk-secret"},
        {"nested": {"private_key": "nope"}},
        {"raw_args": {"path": "private"}},
    ],
)
def test_signal_payload_rejects_credentials_and_unnecessary_raw_args(tmp_path, payload):
    store, owner = _store(tmp_path)
    with pytest.raises(ValueError, match="sensitive_key"):
        GrowthSignalV1(
            owner=owner,
            kind=GrowthSignalKind.USER_DECISION,
            source_ref="decision:1",
            context_key="candidate:1",
            root_run_id="run-1",
            reason_code="decision_recorded",
            payload=payload,
        )
    with store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM growth_events").fetchone()[0] == 0


@pytest.mark.parametrize("purpose", ["reflection", "evaluation"])
def test_internal_reflection_and_evaluation_never_recurse(tmp_path, purpose):
    _, owner = _store(tmp_path)
    with pytest.raises(ValueError, match="recursive_internal_run"):
        GrowthSignalV1(
            owner=owner,
            kind=GrowthSignalKind.EXECUTION_OUTCOME,
            source_ref=f"internal:{purpose}:1",
            context_key=f"internal:{purpose}",
            root_run_id="run-internal",
            reason_code="internal_result",
            payload={},
            origin="companion",
            purpose=purpose,
        )


def test_delegated_task_only_captures_final_objective_outcome(tmp_path):
    _, owner = _store(tmp_path)
    with pytest.raises(ValueError, match="delegated_process_not_objective"):
        GrowthSignalV1(
            owner=owner,
            kind=GrowthSignalKind.EXECUTION_OUTCOME,
            source_ref="delegate:step-1",
            context_key="delegate:1",
            root_run_id="run-1",
            reason_code="delegate_step",
            payload={},
            origin="companion",
            purpose="delegated_task",
        )

    signal = GrowthSignalV1(
        owner=owner,
        kind=GrowthSignalKind.EXECUTION_OUTCOME,
        source_ref="delegate:terminal",
        context_key="delegate:1",
        root_run_id="run-1",
        reason_code="delegate_objective_outcome",
        payload={"status": "succeeded"},
        origin="companion",
        purpose="delegated_task",
        delegated_objective_outcome=True,
    )
    assert signal.event_id.startswith("growth_")


def test_terminal_delivery_is_frozen_to_exact_owner_generation(tmp_path):
    _, owner = _store(tmp_path)
    gate = IdentityReadyGate()
    gate.bind(FrozenOwnerIdentity(owner, "companion:profile-a:1", 1))
    contributor = GrowthTerminalDeliveryContributor(gate)

    deliveries = contributor.freeze_deliveries(object(), object())
    assert contributor.requires_durable(object(), object()) is True
    assert len(deliveries) == 1
    assert deliveries[0].target_id == "companion-profile:profile-a:1"
    assert GrowthDeliveryTarget.parse(deliveries[0].target_id).owner == owner

    owner_b = OwnerRef("profile-b", 1)
    gate.bind(FrozenOwnerIdentity(owner_b, "companion:profile-b:1", 2))
    assert deliveries[0].target_id == "companion-profile:profile-a:1"


@pytest.mark.parametrize("purpose", ["reflection", "evaluation"])
def test_terminal_delivery_skips_recursive_background_runs(tmp_path, purpose):
    _, owner = _store(tmp_path)
    gate = IdentityReadyGate()
    gate.bind(FrozenOwnerIdentity(owner, "companion:profile-a:1", 1))
    contributor = GrowthTerminalDeliveryContributor(gate)
    request = RunRequest(
        text="internal",
        request_id="request-1",
        turn_id="turn-1",
        venue="background",
        mode=purpose,
        payload={
            "companion_background": {
                "purpose": purpose,
                "capture_growth": False,
            }
        },
    )

    assert contributor.requires_durable(request, object()) is False
    assert contributor.freeze_deliveries(request, object()) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("growth_signal_kind", "expected_reason"),
    (
        ("explicit_correction", "explicit_user_correction"),
        (
            "explicit_capability_request",
            "explicit_user_capability_request",
        ),
    ),
)
async def test_committed_message_outbox_projects_event_and_reflection_job(
    tmp_path,
    growth_signal_kind,
    expected_reason,
):
    companion_store, owner = _store(tmp_path)
    session_db = SessionDB(tmp_path / "state.db")
    trusted = TrustedCompanionOwner("profile-a", 1, 1)
    await session_db.initialize()
    await session_db.ensure_session("session-1")
    await session_db.bind_session_owner_if_absent("session-1", trusted)
    message_id = await session_db.append_user_message_with_growth_outbox(
        "session-1",
        "以后请保持简洁",
        trusted_owner=trusted,
        request_id="request-1",
        turn_id="turn-1",
        run_id="run-1",
        priority="normal",
    )

    class _Runtime:
        wakes = 0

        def wake(self):
            self.wakes += 1

    runtime = _Runtime()
    dispatcher = CompanionIngressOutboxDispatcher(
        session_db=session_db,
        store=companion_store,
        runtime=runtime,
        claim_owner="test-ingress",
    )
    settled = await dispatcher.settle_semantic_intent(
        session_id="session-1",
        message_id=message_id,
        request_id="request-1",
        turn_id="turn-1",
        owner=trusted,
        growth_signal_kind=growth_signal_kind,
    )

    assert settled["priority"] == "blocking"
    assert (
        settled["event_envelope"]["semantic_growth_intent"]
        == growth_signal_kind
    )
    with companion_store.read() as db:
        event = db.execute(
            "SELECT * FROM growth_events WHERE source_ref=?",
            (f"message:session-1:{message_id}",),
        ).fetchone()
        job = db.execute(
            "SELECT * FROM jobs WHERE kind='reflection'"
        ).fetchone()
        assert event is not None
        assert event["reason_code"] == expected_reason
        assert job is not None
        assert job["status"] == "queued"
    assert runtime.wakes == 1
    assert (
        await dispatcher.drain_one(
            profile_id=owner.profile_id,
            profile_generation=owner.profile_generation,
        )
        is None
    )
    await session_db.close()


def test_effect_settlement_payload_is_reference_only(tmp_path):
    _, owner = _store(tmp_path)
    signal = execution_outcome_signal(
        owner=owner,
        source_ref="effect:run-1:call-1",
        context_key="run:run-1",
        root_run_id="run-1",
        status="succeeded",
        receipt_ref="receipt:1",
        evidence_verified=True,
        capability_audit_ref="audit:cap:1",
    )
    facts = signal.to_growth_event().payload["facts"]
    assert facts == {
        "status": "succeeded",
        "receipt_ref": "receipt:1",
        "evidence_verified": True,
        "capability_audit_ref": "audit:cap:1",
        "error_class": None,
    }
