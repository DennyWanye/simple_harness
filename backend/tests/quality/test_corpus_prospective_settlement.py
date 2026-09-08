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


# -- RUN-RERUN-FLASH-01 review §1: the blocked path must keep its receipt ------
# The v55 cursor regression blocked 20 corpus cases; the scoring outcome kept
# only the reason string, so what was seeded, how many ticks ran and which
# SQLite constraint rejected the write had to be reconstructed by hand.


class _FailingLane(_SilentLane):
    """A lane whose registration consumer fails exactly like the v55 store did."""

    def __init__(self, error=None):
        super().__init__()
        self._error = error

    async def tick(self):
        self.ticks += 1
        self.last_registration_error = self._error or _cursor_sealed_error()
        return 0


def _cursor_sealed_error():
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE t(a);"
        "CREATE TRIGGER g BEFORE INSERT ON t "
        "BEGIN SELECT RAISE(ABORT,'s5c_cursor_successor_required'); END;")
    try:
        db.execute("INSERT INTO t VALUES(1)")
    except sqlite3.Error as error:
        return error
    raise AssertionError("trigger did not abort")


@pytest.mark.asyncio
async def test_blocked_settlement_carries_the_full_receipt_and_constraint(tmp_path):
    host, memory_path, manager, principal, lane, reminder = await _seeded_case(tmp_path)
    try:
        failing = _FailingLane()
        with pytest.raises(ProspectiveSetupNotReady) as failure:
            await settle_prospective_registrations(lane=failing, manager=manager,
                path=host.path, principal=principal, max_ticks=2)
        receipt = failure.value.receipt
        assert receipt is not None
        assert receipt["missing"] == [reminder.memory_id]
        assert receipt["registered"] == [] and receipt["ticks"] == 2
        assert receipt["seeded_reminders"] == [{"memory_id": reminder.memory_id,
            "revision": reminder.revision, "lifecycle_state": "pending"}]
        # The identity the corpus run needed and did not have.
        assert receipt["tick_errors"] == [
            "type=IntegrityError sqlite=SQLITE_CONSTRAINT_TRIGGER sqlite_code=1811 "
            "constraint=s5c_cursor_successor_required"] * 2
        assert failure.value.code == str(failure.value)
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_a_raising_lane_persists_its_receipt_and_failure_identity(tmp_path):
    host, memory_path, manager, principal, lane, reminder = await _seeded_case(tmp_path)

    class _Raising(_SilentLane):
        async def tick(self):
            self.ticks += 1
            raise _cursor_sealed_error()

    try:
        with pytest.raises(ProspectiveSetupNotReady) as failure:
            await settle_prospective_registrations(lane=_Raising(), manager=manager,
                path=host.path, principal=principal)
        receipt = failure.value.receipt
        assert receipt is not None and receipt["ticks"] == 0
        assert receipt["seeded_reminders"][0]["memory_id"] == reminder.memory_id
        assert "constraint=s5c_cursor_successor_required" in receipt["setup_error"]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_an_absent_lane_still_reports_what_was_seeded(tmp_path):
    host, memory_path, manager, principal, lane, reminder = await _seeded_case(tmp_path)
    try:
        with pytest.raises(ProspectiveSetupNotReady) as failure:
            await settle_prospective_registrations(lane=None, manager=manager,
                path=host.path, principal=principal)
        assert str(failure.value) == "corpus_prospective_runtime_lane_unavailable"
        assert failure.value.receipt["seeded_reminders"][0]["memory_id"] == (
            reminder.memory_id)
    finally:
        await manager.close()
