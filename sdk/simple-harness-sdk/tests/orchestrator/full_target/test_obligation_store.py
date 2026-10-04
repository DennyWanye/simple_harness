# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P1.2 red tests: the durable obligation ledger (§6.1, §6.4, ADR-08).

The arithmetic under test is the one thing re-planning must not be able to reset:
a duty's recursion fuel and demand belong to the ``obligation_id``, so renaming the
task, swapping the method or handing the work to another agent leave every counter
exactly where it was.  Failures and spend are not stored on the duty (stage D).  These tests drive the
persistent store, not the in-memory ledger, and additionally pin that a *refused*
call writes nothing at all.
"""

from __future__ import annotations

import pytest

from agent_orchestrator.contracts import (
    Budget,
    Mission,
    MissionStatus,
)
from agent_orchestrator.contracts.htn import ObligationId, Requiredness
from agent_orchestrator.contracts.obligations import (
    Obligation,
    ObligationLifecycle,
    ShapeChange,
)
from agent_orchestrator.storage.obligation_store import ObligationStore
from agent_orchestrator.storage.store import Store, StoreConflict

MISSION = "mission-1"


def _mission(mission_id: str = MISSION) -> Mission:
    return Mission(
        id=mission_id,
        goal="g",
        success_criteria=("ok",),
        stop_conditions=(),
        allowed_tools=(),
        risk_level="sandbox",
        budget=Budget(max_tokens=1000, max_attempts=2),
        tenant_id="t",
        status=MissionStatus.CREATED,
        created_at=1.0,
        version=1,
        idempotency_key=mission_id,
    )


def _duty(
    obligation: str = "obligation-1",
    *,
    mission_id: str = MISSION,
    parent: str | None = None,
    lifecycle: ObligationLifecycle = ObligationLifecycle.UNSATISFIED,
    resolution_ref: str | None = None,
    budget_lineage_ref: str | None = None,
) -> Obligation:
    return Obligation(
        obligation_id=ObligationId(obligation),
        mission_id=mission_id,
        requirement_refs=("c-complete",),
        goal_signature_id="compare-sources",
        parameters={"subject": "alpha"},
        scope="mission",
        requiredness=Requiredness.REQUIRED,
        budget_lineage_ref=budget_lineage_ref,
        lifecycle=lifecycle,
        resolution_ref=resolution_ref,
        parent_obligation_id=None if parent is None else ObligationId(parent),
    )


@pytest.fixture
def store(tmp_path) -> Store:
    opened = Store.open(tmp_path / "orchestrator.db")
    opened.insert_mission(_mission(), spec_hash="h")
    return opened


@pytest.fixture
def ledger(store: Store) -> ObligationStore:
    return ObligationStore(store)


# ------------------------------------------------------------------ registration
def test_register_round_trips_the_duty(ledger: ObligationStore) -> None:
    duty = _duty()
    ledger.register(duty, recursion_fuel=2)
    assert ledger.obligation(MISSION, duty.obligation_id) == duty
    assert ledger.obligation_ids(MISSION) == (duty.obligation_id,)
    assert ledger.list_obligations(MISSION) == (duty,)


def test_registering_the_same_id_twice_is_refused(ledger: ObligationStore) -> None:
    ledger.register(_duty())
    with pytest.raises(StoreConflict, match="already registered"):
        ledger.register(_duty())


def test_a_fresh_account_starts_at_zero(ledger: ObligationStore) -> None:
    ledger.register(_duty(), recursion_fuel=3)
    view = ledger.account(MISSION, ObligationId("obligation-1"))
    assert view.remaining_fuel == 3
    assert view.lifecycle is ObligationLifecycle.UNSATISFIED


def test_an_unknown_obligation_is_a_conflict(ledger: ObligationStore) -> None:
    with pytest.raises(StoreConflict, match="not registered"):
        ledger.account(MISSION, ObligationId("obligation-missing"))


# ------------------------------------------------------------------ lifecycle
def test_satisfied_without_a_resolution_ref_is_refused_and_changes_nothing(
    ledger: ObligationStore,
) -> None:
    target = ObligationId("obligation-1")
    ledger.register(_duty())
    with pytest.raises(StoreConflict, match="resolution_ref"):
        ledger.set_lifecycle(MISSION, target, ObligationLifecycle.SATISFIED)
    assert ledger.account(MISSION, target).lifecycle is ObligationLifecycle.UNSATISFIED
    assert ledger.obligation(MISSION, target).lifecycle is ObligationLifecycle.UNSATISFIED


def test_satisfied_stores_the_resolution_ref_in_the_row_and_the_document(
    ledger: ObligationStore,
) -> None:
    target = ObligationId("obligation-1")
    ledger.register(_duty())
    view = ledger.set_lifecycle(
        MISSION, target, ObligationLifecycle.SATISFIED, resolution_ref="resolution-1"
    )
    assert view.lifecycle is ObligationLifecycle.SATISFIED
    assert view.resolution_ref == "resolution-1"
    stored = ledger.obligation(MISSION, target)
    assert stored.lifecycle is ObligationLifecycle.SATISFIED
    assert stored.resolution_ref == "resolution-1"


# ------------------------------------------------------------------ recursion fuel
# ------------------------------------------------------------------ ledger bridge
def test_load_ledger_reproduces_every_counter(ledger: ObligationStore) -> None:
    target = ObligationId("obligation-1")
    ledger.register(_duty(), recursion_fuel=2)
    ledger.set_lifecycle(
        MISSION, target, ObligationLifecycle.SATISFIED, resolution_ref="resolution-1"
    )
    memory = ledger.load_ledger(MISSION)
    assert memory.account(target) == ledger.account(MISSION, target)
    assert memory.obligation(target) == ledger.obligation(MISSION, target)


def test_persist_writes_a_memory_ledger_back_unchanged(ledger: ObligationStore) -> None:
    target = ObligationId("obligation-1")
    ledger.register(_duty(), recursion_fuel=3)
    memory = ledger.load_ledger(MISSION)
    memory.set_lifecycle(target, ObligationLifecycle.SATISFIED, resolution_ref="resolution-1")
    assert ledger.persist(memory) == (target,)
    assert ledger.account(MISSION, target).lifecycle is ObligationLifecycle.SATISFIED
    # 按义务扣燃料与形状变化没有表（阶段 G 删）：带着它们的内存账本不许静默丢掉
    memory.note_shape_change(target, ShapeChange.PARAMETERS_REBOUND, detail="subject=beta")
    with pytest.raises(StoreConflict, match="not stored"):
        ledger.persist(memory)


def test_persisting_an_untouched_ledger_leaves_every_axis_alone(ledger: ObligationStore) -> None:
    target = ObligationId("obligation-1")
    ledger.register(_duty(), recursion_fuel=2)
    before = ledger.account(MISSION, target)
    ledger.persist(ledger.load_ledger(MISSION))
    assert ledger.account(MISSION, target) == before


def test_an_admitted_demand_round_trips_and_is_admitted_once(ledger: ObligationStore) -> None:
    target = ObligationId("obligation-1")
    ledger.register(_duty())
    assert ledger.account(MISSION, target).has_admitted_demand is False
    assert ledger.admit_demand(MISSION, target).has_admitted_demand is True
    with pytest.raises(StoreConflict, match="already has an admitted demand"):
        ledger.admit_demand(MISSION, target)
    assert ledger.load_ledger(MISSION).account(target).has_admitted_demand is True
    assert ledger.withdraw_demand(MISSION, target).has_admitted_demand is False
    with pytest.raises(StoreConflict, match="no admitted demand"):
        ledger.withdraw_demand(MISSION, target)


def test_a_duty_that_is_no_longer_open_may_not_keep_a_demand(ledger: ObligationStore) -> None:
    target = ObligationId("obligation-1")
    ledger.register(_duty())
    ledger.admit_demand(MISSION, target)
    with pytest.raises(StoreConflict):
        ledger.set_lifecycle(
            MISSION, target, ObligationLifecycle.SATISFIED, resolution_ref="resolution-1"
        )
    assert ledger.account(MISSION, target).lifecycle is ObligationLifecycle.UNSATISFIED
    ledger.withdraw_demand(MISSION, target)
    ledger.set_lifecycle(
        MISSION, target, ObligationLifecycle.SATISFIED, resolution_ref="resolution-1"
    )
    with pytest.raises(StoreConflict, match="open obligation"):
        ledger.admit_demand(MISSION, target)


def test_the_store_knows_which_duties_a_mission_carries(ledger: ObligationStore) -> None:
    ledger.register(_duty())
    assert ledger.exists(MISSION, ObligationId("obligation-1")) is True
    assert ledger.exists(MISSION, ObligationId("obligation-9")) is False
    assert ledger.exists("mission-other", ObligationId("obligation-1")) is False


def test_persist_inserts_duties_the_store_has_never_seen(ledger: ObligationStore) -> None:
    memory = ledger.load_ledger(MISSION)
    memory.register(_duty("obligation-9"), recursion_fuel=2)
    ledger.persist(memory)
    assert ledger.exists(MISSION, ObligationId("obligation-9")) is True
    assert ledger.remaining_fuel(MISSION, ObligationId("obligation-9")) == 2


# ------------------------------------------------------------------ concurrent writers
def _rival(tmp_path, monkeypatch: pytest.MonkeyPatch, store: Store, action) -> None:
    """Let ``action`` commit through a second connection just before ``store`` writes.

    The competing write lands *before* our BEGIN IMMEDIATE, so a store that reads its
    counters outside the transaction and writes an absolute value silently drops it,
    while one that accumulates in SQL sees it.
    """

    rival_store = Store.open(tmp_path / "orchestrator.db")
    rival = ObligationStore(rival_store)
    original = Store.transaction
    fired = {"done": False}

    def racing(self):
        if self is store and not fired["done"]:
            fired["done"] = True
            action(rival)
        return original(self)

    monkeypatch.setattr(Store, "transaction", racing)


def test_a_failed_transaction_rolls_back_every_obligation_write(
    store: Store, ledger: ObligationStore
) -> None:
    target = ObligationId("obligation-1")
    ledger.register(_duty(), recursion_fuel=2)
    with pytest.raises(RuntimeError, match="deliberate"):
        with store.transaction():
            ledger.admit_demand(MISSION, target)
            ledger.register(_duty("obligation-2"))
            raise RuntimeError("deliberate")
    assert ledger.account(MISSION, target).has_admitted_demand is False
    assert ledger.obligation_ids(MISSION) == (target,)
