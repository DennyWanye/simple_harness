"""Scoring-runway prospective registration settlement; no Provider/model runs.

The SDK typed-recall type-authority gate withholds a pending reminder that has
no accepted `prospective_scheduler_registrations` row, so the corpus runway must
produce that row itself once MemoryAnalysisLane (which owns the production
registration consumer) has been closed for foreground isolation.
"""
import sqlite3

import pytest
import simple_harness_memory as m

from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.prospective_runtime import ProspectiveRuntimeLane
from deskpet.memory.s5c_store import HostProspectiveSignalAuthority, S5cStore
from deskpet.memory.s5c_terminal_schema import initialize_s5c_terminal_state_db
from deskpet.quality.corpus_c04 import SETUPS, SCENARIO_CLOCKS, compile_c04_setup
from deskpet.quality.corpus_c04_prepare import open_c04_fixture
from deskpet.quality.corpus_prospective import (
    ProspectiveSetupNotReady, seeded_reminders, settle_prospective_registrations,
)
from tests.memory.test_primary_read_api import setup, AUTH
from tests.memory.test_primary_visibility import classification_policy, FILTERS

CASE = "C04-01"


class _Runtime:
    """Only the two seams ProspectiveRuntimeLane actually uses."""
    def __init__(self, manager, principal):
        self._manager, self._principal = manager, principal

    async def manager(self):
        return self._manager

    def principal(self):
        return self._principal


class _SilentLane:
    """A closed lane: the exact runway state that produced run-01e's zero recall."""
    def __init__(self):
        self.last_registration_error = None
        self.ticks = 0

    async def tick(self):
        self.ticks += 1
        return 0


def _sdk_registrations(memory_path, memory_id):
    with sqlite3.connect(f"file:{memory_path}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(
            "SELECT state,trigger_hash,prospective_revision FROM "
            "prospective_scheduler_registrations WHERE memory_id=?", (memory_id,))]


async def _seeded_case(tmp_path):
    """Seed one pending reminder exactly the way the scoring runway does."""
    host = await setup(tmp_path / "host")
    await initialize_s5c_terminal_state_db(host.path)
    memory_path = tmp_path / "memory.db"
    batch = compile_c04_setup(CASE, SETUPS[CASE][0], scenario_clock=SCENARIO_CLOCKS[CASE])
    principal = m.MemoryPrincipal("host", "household", AUTH.subject, "corpus-fixture")
    async with open_c04_fixture(path=host.path, memory_path=memory_path, principal=principal,
            authority_ref=AUTH.authority_ref, batch=batch,
            classification_policy=classification_policy(),
            supported_filter_policies=FILTERS) as (_, actual):
        reminder = actual["labels"]["P"]
    # Reopen with the production prospective signal authority, as the scoring
    # session does when it activates the real product SDK runtime.
    manager = await m.build_human_memory_v7(memory_path,
        classification_policy=classification_policy(), supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(host.path),
        prospective_signal_authority=HostProspectiveSignalAuthority(host.path, principal),
        clock=lambda: batch.scenario_time)
    lane = ProspectiveRuntimeLane(path=host.path, runtime=_Runtime(manager, principal),
                                  clock=lambda: batch.scenario_time)
    return host, memory_path, manager, principal, lane, reminder


@pytest.mark.asyncio
async def test_seeded_reminder_has_no_registration_without_a_tick(tmp_path):
    host, memory_path, manager, principal, lane, reminder = await _seeded_case(tmp_path)
    try:
        reminders = await seeded_reminders(manager, principal)
        assert [node.memory_id for node in reminders] == [reminder.memory_id]
        assert str(reminders[0].lifecycle_state) == "pending"
        # The exact gate input is absent, so typed recall could only return zero.
        assert _sdk_registrations(memory_path, reminder.memory_id) == []
        assert await S5cStore(host.path, principal).accepted_registration(
            memory_id=reminder.memory_id, revision=reminder.revision) is None
        silent = _SilentLane()
        with pytest.raises(ProspectiveSetupNotReady) as failure:
            await settle_prospective_registrations(lane=silent, manager=manager,
                path=host.path, principal=principal, max_ticks=2)
        assert str(failure.value) == (
            "corpus_prospective_registration_missing:" + reminder.memory_id)
        assert silent.ticks == 2
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_explicit_lane_tick_produces_the_accepted_registration(tmp_path):
    host, memory_path, manager, principal, lane, reminder = await _seeded_case(tmp_path)
    try:
        receipt = await settle_prospective_registrations(lane=lane, manager=manager,
            path=host.path, principal=principal)
        assert receipt["registered"] == [reminder.memory_id]
        assert receipt["missing"] == [] and receipt["tick_errors"] == []
        assert receipt["ticks"] == 1
        assert receipt["seeded_reminders"] == [{"memory_id": reminder.memory_id,
            "revision": reminder.revision, "lifecycle_state": "pending"}]
        accepted = await S5cStore(host.path, principal).accepted_registration(
            memory_id=reminder.memory_id, revision=reminder.revision)
        assert accepted is not None and accepted.result is not None
        assert accepted.result.outcome.value == "acknowledged"
        assert accepted.result.reason_code == "prospective_registration_acknowledged"
        # The row the SDK type-authority gate reads, not just the Host ACK.
        rows = _sdk_registrations(memory_path, reminder.memory_id)
        assert [row["state"] for row in rows] == ["accepted"]
        assert rows[0]["prospective_revision"] == reminder.revision
        # Settling again is idempotent and never spends another scheduler tick.
        again = await settle_prospective_registrations(lane=lane, manager=manager,
            path=host.path, principal=principal)
        assert again["registered"] == [reminder.memory_id] and again["ticks"] == 0
        assert _sdk_registrations(memory_path, reminder.memory_id) == rows
    finally:
        await lane.close()
        await manager.close()


@pytest.mark.asyncio
async def test_case_without_a_reminder_never_wakes_the_closed_lane(tmp_path):
    host = await setup(tmp_path / "host")
    await initialize_s5c_terminal_state_db(host.path)
    principal = m.MemoryPrincipal("host", "household", AUTH.subject, "corpus-fixture")
    manager = await m.build_human_memory_v7(tmp_path / "memory.db",
        classification_policy=classification_policy(), supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(host.path))
    try:
        await manager.register_principal_owner(principal, m.MemoryScope.personal(principal.actor_id))
        silent = _SilentLane()
        receipt = await settle_prospective_registrations(lane=silent, manager=manager,
            path=host.path, principal=principal)
        assert receipt == {"seeded_reminders": [], "ticks": 0, "registered": [],
                           "missing": [], "tick_errors": []}
        assert silent.ticks == 0
    finally:
        await manager.close()
