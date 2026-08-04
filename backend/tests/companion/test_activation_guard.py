from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from deskpet.companion.activation_guard import (
    ActivationGuardMonitor,
    GuardBindingSnapshotV1,
    TrustedCapabilityFailureReceiptV1,
)
from deskpet.companion.authority import RevocationBarrier
from deskpet.companion.contracts import OwnerRef
from deskpet.companion.store import canonical_hash


def _receipt(**overrides) -> TrustedCapabilityFailureReceiptV1:
    facts = {
        "schema_version": 1,
        "source_authority": "runtime_health",
        "source_event_id": "runtime-event-1",
        "source_receipt_ref": "runtime-receipt-1",
        "source_receipt_hash": "runtime-receipt-hash-1",
        "guard_id": "guard-1",
        "binding_generation": 4,
        "pack_id": "personal.summarize-day",
        "version": "1.0.1",
        "manifest_hash": "manifest-1",
        "runtime_generation": 2,
        "failure_class": "runtime_contract",
        "severity": "critical",
        "failure_fingerprint": "health-schema-drift",
        "observed_at": "2026-07-25T02:00:00.000Z",
    }
    facts.update(overrides)
    return TrustedCapabilityFailureReceiptV1(
        **{key: value for key, value in facts.items() if key != "schema_version"},
        receipt_hash=canonical_hash(facts),
    )


class _Store:
    def __init__(self) -> None:
        self.incidents = []

    def record_capability_guard_incident(self, owner, incident):
        self.incidents.append((owner, incident))
        return {"status": "triggered"}


class _Fence:
    def __init__(self, snapshot, trace) -> None:
        self.snapshot = snapshot
        self.trace = trace

    def keep_closed_for_recovery(self, *, incident_id: str) -> None:
        self.trace.append(("keep_closed", incident_id))


class _Platform:
    def __init__(self, *, drift: bool = False) -> None:
        self.trace = []
        self.snapshot = GuardBindingSnapshotV1(
            target_owner_key="companion:alice:1",
            target_scope="user",
            target_scope_key="profile",
            pack_id="personal.summarize-day",
            version="1.0.2" if drift else "1.0.1",
            manifest_hash="manifest-1",
            binding_generation=4,
            owner_binding_set_stamp="owner-stamp-4",
        )

    @asynccontextmanager
    async def guard_incident_fence(self, **kwargs):
        self.trace.append(("enter_fence", kwargs["pack_id"]))
        yield _Fence(self.snapshot, self.trace)
        self.trace.append(("exit_fence", kwargs["pack_id"]))


@pytest.mark.asyncio
async def test_trusted_critical_incident_closes_gate_after_store_commit() -> None:
    store = _Store()
    platform = _Platform()
    outcome = await ActivationGuardMonitor(
        store=store,
        platform=platform,
        barrier=RevocationBarrier(),
    ).observe(
        OwnerRef("alice", 1),
        _receipt(),
        target_owner_key="companion:alice:1",
        target_scope="user",
        target_scope_key="profile",
    )

    assert outcome.status == "triggered"
    assert outcome.incident_id
    assert len(store.incidents) == 1
    assert platform.trace == [
        ("enter_fence", "personal.summarize-day"),
        ("keep_closed", outcome.incident_id),
        ("exit_fence", "personal.summarize-day"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "reason"),
    [
        (
            {"source_authority": "model_self_report"},
            "guard_source_authority_untrusted",
        ),
        ({"severity": "warning"}, "guard_severity_ineligible"),
        (
            {"failure_class": "provider_timeout"},
            "guard_failure_class_ineligible",
        ),
    ],
)
async def test_ineligible_incident_never_takes_platform_fence(
    override, reason
) -> None:
    store = _Store()
    platform = _Platform()
    outcome = await ActivationGuardMonitor(
        store=store,
        platform=platform,
        barrier=RevocationBarrier(),
    ).observe(
        OwnerRef("alice", 1),
        _receipt(**override),
        target_owner_key="companion:alice:1",
        target_scope="user",
        target_scope_key="profile",
    )

    assert outcome.status == "rejected"
    assert outcome.reason_code == reason
    assert store.incidents == []
    assert platform.trace == []


@pytest.mark.asyncio
async def test_binding_drift_rejects_without_creating_rollback() -> None:
    store = _Store()
    platform = _Platform(drift=True)
    outcome = await ActivationGuardMonitor(
        store=store,
        platform=platform,
        barrier=RevocationBarrier(),
    ).observe(
        OwnerRef("alice", 1),
        _receipt(),
        target_owner_key="companion:alice:1",
        target_scope="user",
        target_scope_key="profile",
    )

    assert outcome.status == "rejected"
    assert outcome.reason_code == "guard_binding_superseded"
    assert store.incidents == []
    assert platform.trace == [
        ("enter_fence", "personal.summarize-day"),
        ("exit_fence", "personal.summarize-day"),
    ]
