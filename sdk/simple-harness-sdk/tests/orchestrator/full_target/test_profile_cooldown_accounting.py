"""Durable shared-pool wait origins survive retries, recovery and store reopen."""

from types import SimpleNamespace

import pytest

from agent_orchestrator.contracts import Budget, Event, Mission, MissionStatus
from agent_orchestrator.orchestrator.commit_service import CommitService
from agent_orchestrator.orchestrator.event_handler import Orchestrator
from agent_orchestrator.storage.store import Store


def test_inherited_cooldown_keeps_its_origin_across_reopen_and_renewal(tmp_path):
    now = 100.0
    path = tmp_path / "pool.db"
    store = Store.open(path, clock=lambda: now)
    try:
        commit = CommitService(store)
        commit.record_profile_failure(
            "shared", error=None, threshold=1, cooldown_seconds=1000.0, mission_ids=()
        )
        fake = SimpleNamespace(store=store, commit=commit)
        assert Orchestrator._profile_unavailable_since(fake, "new-mission", "shared") == 100.0
        now = 401.0
        store.close()
        store = Store.open(path, clock=lambda: now)
        commit = CommitService(store)
        fake = SimpleNamespace(store=store, commit=commit)
        assert Orchestrator._profile_unavailable_since(fake, "new-mission", "shared") == 100.0
        commit.record_profile_failure(
            "shared", error=None, threshold=1, cooldown_seconds=1000.0, mission_ids=()
        )
        assert Orchestrator._profile_unavailable_since(fake, "new-mission", "shared") == 100.0
        commit.record_profile_success("shared")
        commit.record_profile_failure(
            "shared", error=None, threshold=1, cooldown_seconds=1000.0, mission_ids=()
        )
        assert Orchestrator._profile_unavailable_since(fake, "new-mission", "shared") == 401.0
    finally:
        store.close()


@pytest.mark.parametrize("recovered", [False, True])
def test_legacy_shared_health_has_a_stable_origin(tmp_path, recovered):
    now = 100.0
    store = Store.open(tmp_path / "legacy-pool.db", clock=lambda: now)
    try:
        commit = CommitService(store)
        entry = {"failures": 0, "unavailable_until": 10000.0, "last_failure_at": 80.0}
        if recovered:
            entry["recovered_at"] = 70.0
        store.put_scheduler_state(commit.PROFILE_HEALTH_KEY, {"profiles": {"shared": entry}})
        fake = SimpleNamespace(store=store, commit=commit)
        assert Orchestrator._profile_unavailable_since(fake, "new-mission", "shared") == 80.0
        now = 401.0
        assert Orchestrator._profile_unavailable_since(fake, "new-mission", "shared") == 80.0
        commit.record_profile_failure(
            "shared", error=None, threshold=1, cooldown_seconds=10000.0, mission_ids=()
        )
        assert Orchestrator._profile_unavailable_since(fake, "new-mission", "shared") == 80.0
    finally:
        store.close()


@pytest.mark.parametrize("recovered", [False, True])
def test_legacy_inherited_cooldown_uses_other_missions_events(tmp_path, recovered):
    store = Store.open(tmp_path / "legacy-events.db", clock=lambda: 150.0)
    try:
        store.insert_mission(
            Mission(
                id="prior",
                goal="test",
                success_criteria=("ok",),
                stop_conditions=(),
                allowed_tools=(),
                risk_level="sandbox",
                budget=Budget(max_tokens=1000, max_attempts=2),
                tenant_id="test",
                status=MissionStatus.CREATED,
                created_at=1.0,
                version=1,
                idempotency_key="prior",
            ),
            spec_hash="test",
        )
        for created_at, until in ((100.0, 160.0), (140.0, 200.0)):
            store.append_event(
                Event(
                    id=f"unavailable-{created_at}",
                    type="RuntimeProfileUnavailable",
                    trace_id="test",
                    mission_id="prior",
                    task_id=None,
                    attempt_id=None,
                    actor_type="system",
                    actor_id="test",
                    payload={"profile_id": "shared", "until": until},
                    idempotency_key=f"unavailable-{created_at}",
                    created_at=created_at,
                )
            )
        commit = CommitService(store)
        entry = {"failures": 0, "unavailable_until": 200.0, "last_failure_at": 140.0}
        if recovered:
            entry["recovered_at"] = 120.0
        store.put_scheduler_state(commit.PROFILE_HEALTH_KEY, {"profiles": {"shared": entry}})
        fake = SimpleNamespace(store=store, commit=commit)
        expected = 140.0 if recovered else 100.0
        assert Orchestrator._profile_unavailable_since(fake, "new-mission", "shared") == expected
        commit.record_profile_failure(
            "shared", error=None, threshold=1, cooldown_seconds=60, mission_ids=()
        )
        assert Orchestrator._profile_unavailable_since(fake, "new-mission", "shared") == expected
    finally:
        store.close()
