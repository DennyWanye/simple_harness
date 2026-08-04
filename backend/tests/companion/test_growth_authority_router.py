from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from deskpet.companion.authority import (
    GrowthAuthorityGenerationMismatch,
    GrowthAuthorityPhase,
    GrowthAuthorityRouter,
    GrowthAuthorityState,
    GrowthIngressKind,
    GrowthIngressRequest,
    GrowthIngressUnavailable,
    GrowthWritesPaused,
    IllegalGrowthAuthorityTransition,
    RevocationBarrier,
    RevocationBarrierTimeout,
    get_process_revocation_barrier,
)
from deskpet.companion.legacy_authority import LegacyGrowthAuthorityAdapter


class FakeAuthorityStore:
    def __init__(self, state: GrowthAuthorityState) -> None:
        self.state = state
        self.transitions: list[dict[str, Any]] = []

    async def get_growth_authority_state(self) -> GrowthAuthorityState:
        return self.state

    async def transition_growth_authority_state(
        self,
        expected_phase: str,
        next_phase: str,
        *,
        migration_generation: int,
        marker_committed: bool = False,
        journal_payload: Any,
    ) -> GrowthAuthorityState:
        if self.state.phase.value != expected_phase:
            raise RuntimeError("stale phase")
        if migration_generation <= self.state.generation:
            raise RuntimeError("generation did not advance")
        self.transitions.append(
            {
                "expected_phase": expected_phase,
                "next_phase": next_phase,
                "generation": migration_generation,
                "marker": marker_committed,
                "journal": dict(journal_payload),
            }
        )
        self.state = replace(
            self.state,
            phase=GrowthAuthorityPhase(next_phase),
            generation=migration_generation,
            roll_forward_required=marker_committed,
        )
        return self.state


class RecordingAuthority:
    def __init__(self, name: str) -> None:
        self.name = name
        self.reads: list[GrowthIngressRequest] = []
        self.writes: list[GrowthIngressRequest] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def read(self, request: GrowthIngressRequest) -> object:
        self.reads.append(request)
        return f"{self.name}:read"

    async def write(self, request: GrowthIngressRequest) -> object:
        self.writes.append(request)
        self.entered.set()
        if self.block:
            await self.release.wait()
        return f"{self.name}:write"


def request(
    kind: GrowthIngressKind,
    *,
    authority_generation: int,
) -> GrowthIngressRequest:
    return GrowthIngressRequest(
        kind=kind,
        profile_id="profile-a",
        profile_generation=7,
        authority_generation=authority_generation,
        payload={"value": "x"},
    )


@pytest.mark.asyncio
async def test_legacy_is_the_only_writer_before_cutover() -> None:
    store = FakeAuthorityStore(
        GrowthAuthorityState(GrowthAuthorityPhase.LEGACY, generation=3)
    )
    legacy = RecordingAuthority("legacy")
    companion = RecordingAuthority("companion")
    router = GrowthAuthorityRouter(
        store=store, legacy=legacy, companion=companion
    )

    state = await router.start()
    result = await router.dispatch(
        request(GrowthIngressKind.PREFERENCE_WRITE, authority_generation=state.generation)
    )

    assert result == "legacy:write"
    assert len(legacy.writes) == 1
    assert companion.writes == []


@pytest.mark.asyncio
async def test_every_ingress_rejects_stale_authority_generation() -> None:
    store = FakeAuthorityStore(
        GrowthAuthorityState(GrowthAuthorityPhase.LEGACY, generation=9)
    )
    router = GrowthAuthorityRouter(store=store, legacy=RecordingAuthority("legacy"))
    await router.start()

    with pytest.raises(GrowthAuthorityGenerationMismatch) as exc:
        await router.dispatch(
            request(GrowthIngressKind.PREFERENCE_READ, authority_generation=8)
        )

    assert exc.value.expected == 8
    assert exc.value.actual == 9


@pytest.mark.asyncio
async def test_pre_marker_restart_rolls_back_to_legacy() -> None:
    store = FakeAuthorityStore(
        GrowthAuthorityState(
            GrowthAuthorityPhase.PREPARING,
            generation=4,
            roll_forward_required=False,
        )
    )
    router = GrowthAuthorityRouter(store=store, legacy=RecordingAuthority("legacy"))

    state = await router.start()

    assert state.phase is GrowthAuthorityPhase.LEGACY
    assert state.generation == 5
    assert store.transitions[0]["journal"]["reason"] == "startup_pre_marker_rollback"


@pytest.mark.asyncio
async def test_post_marker_restart_never_guesses_legacy() -> None:
    store = FakeAuthorityStore(
        GrowthAuthorityState(
            GrowthAuthorityPhase.PREPARING,
            generation=4,
            roll_forward_required=True,
        )
    )
    legacy = RecordingAuthority("legacy")
    router = GrowthAuthorityRouter(store=store, legacy=legacy)

    state = await router.start()

    assert state.phase is GrowthAuthorityPhase.PREPARING
    assert store.transitions == []
    with pytest.raises(GrowthIngressUnavailable):
        await router.dispatch(
            request(GrowthIngressKind.PREFERENCE_WRITE, authority_generation=4)
        )
    assert legacy.writes == []


@pytest.mark.asyncio
async def test_companion_integrity_failure_pauses_growth_without_legacy_fallback() -> None:
    store = FakeAuthorityStore(
        GrowthAuthorityState(
            GrowthAuthorityPhase.COMPANION,
            generation=8,
            roll_forward_required=True,
        )
    )
    legacy = RecordingAuthority("legacy")
    router = GrowthAuthorityRouter(store=store, legacy=legacy, companion=None)

    state = await router.start()

    assert state.phase is GrowthAuthorityPhase.PAUSED
    assert router.ordinary_chat_allowed
    assert legacy.writes == []
    assert store.transitions[0]["journal"]["error_code"] == (
        "companion_authority_adapter_missing"
    )


@pytest.mark.asyncio
async def test_marker_commit_forbids_legacy_fallback() -> None:
    store = FakeAuthorityStore(
        GrowthAuthorityState(GrowthAuthorityPhase.LEGACY, generation=1)
    )
    router = GrowthAuthorityRouter(store=store, legacy=RecordingAuthority("legacy"))
    await router.start()
    preparing = await router.transition(
        GrowthAuthorityPhase.PREPARING,
        expected_generation=1,
        marker_committed=False,
        reason="begin",
    )
    marked = await router.transition(
        GrowthAuthorityPhase.PREPARING,
        expected_generation=preparing.generation,
        marker_committed=True,
        reason="commit_marker",
    )

    with pytest.raises(IllegalGrowthAuthorityTransition):
        await router.transition(
            GrowthAuthorityPhase.LEGACY,
            expected_generation=marked.generation,
            marker_committed=True,
            reason="forbidden",
        )
    with pytest.raises(IllegalGrowthAuthorityTransition):
        await router.transition(
            GrowthAuthorityPhase.LEGACY,
            expected_generation=marked.generation,
            marker_committed=False,
            reason="cannot_clear_marker",
        )


@pytest.mark.asyncio
async def test_cannot_skip_durable_marker_state() -> None:
    store = FakeAuthorityStore(
        GrowthAuthorityState(GrowthAuthorityPhase.LEGACY, generation=1)
    )
    router = GrowthAuthorityRouter(store=store, legacy=RecordingAuthority("legacy"))
    await router.start()
    with pytest.raises(IllegalGrowthAuthorityTransition):
        await router.transition(
            GrowthAuthorityPhase.PREPARING,
            expected_generation=1,
            marker_committed=True,
            reason="cannot_mark_while_entering",
        )

    preparing = await router.transition(
        GrowthAuthorityPhase.PREPARING,
        expected_generation=1,
        marker_committed=False,
        reason="begin",
    )
    with pytest.raises(IllegalGrowthAuthorityTransition):
        await router.transition(
            GrowthAuthorityPhase.COMPANION,
            expected_generation=preparing.generation,
            marker_committed=True,
            reason="cannot_commit_marker_and_switch_together",
        )


@pytest.mark.asyncio
async def test_companion_switch_and_paused_recovery_require_preflight() -> None:
    store = FakeAuthorityStore(
        GrowthAuthorityState(
            GrowthAuthorityPhase.PREPARING,
            generation=11,
            roll_forward_required=True,
        )
    )
    legacy = RecordingAuthority("legacy")
    companion = RecordingAuthority("companion")
    router = GrowthAuthorityRouter(
        store=store, legacy=legacy, companion=companion
    )
    await router.start()
    active = await router.transition(
        GrowthAuthorityPhase.COMPANION,
        expected_generation=11,
        marker_committed=True,
        reason="cutover_complete",
    )
    assert await router.dispatch(
        request(GrowthIngressKind.CODIFY, authority_generation=active.generation)
    ) == "companion:write"
    assert legacy.writes == []

    paused = await router.transition(
        GrowthAuthorityPhase.PAUSED,
        expected_generation=active.generation,
        reason="integrity_failure",
    )
    assert router.ordinary_chat_allowed is True
    with pytest.raises(GrowthWritesPaused):
        await router.dispatch(
            request(
                GrowthIngressKind.REMINDER_WRITE,
                authority_generation=paused.generation,
            )
        )
    with pytest.raises(IllegalGrowthAuthorityTransition):
        await router.transition(
            GrowthAuthorityPhase.COMPANION,
            expected_generation=paused.generation,
            reason="unchecked_resume",
        )
    resumed = await router.transition(
        GrowthAuthorityPhase.COMPANION,
        expected_generation=paused.generation,
        preflight_passed=True,
        reason="repair_preflight_passed",
    )
    assert resumed.phase is GrowthAuthorityPhase.COMPANION


@pytest.mark.asyncio
async def test_cutover_exclusive_gate_drains_legacy_writer_before_switch() -> None:
    store = FakeAuthorityStore(
        GrowthAuthorityState(GrowthAuthorityPhase.LEGACY, generation=1)
    )
    legacy = RecordingAuthority("legacy")
    legacy.block = True
    router = GrowthAuthorityRouter(
        store=store, legacy=legacy, companion=RecordingAuthority("companion")
    )
    await router.start()
    write_task = asyncio.create_task(
        router.dispatch(
            request(GrowthIngressKind.PREFERENCE_WRITE, authority_generation=1)
        )
    )
    await asyncio.wait_for(legacy.entered.wait(), timeout=1)
    transition_task = asyncio.create_task(
        router.transition(
            GrowthAuthorityPhase.PREPARING,
            expected_generation=1,
            marker_committed=False,
            reason="drain",
        )
    )
    await asyncio.sleep(0)
    assert not transition_task.done()

    legacy.release.set()
    await asyncio.wait_for(write_task, timeout=1)
    state = await asyncio.wait_for(transition_task, timeout=1)
    assert state.phase is GrowthAuthorityPhase.PREPARING


@pytest.mark.asyncio
async def test_profile_delete_uses_same_exclusive_barrier_as_ingress() -> None:
    class DeletingStore(FakeAuthorityStore):
        def __init__(self, state: GrowthAuthorityState) -> None:
            super().__init__(state)
            self.deleted = asyncio.Event()

        async def delete_profile_generation(
            self, owner: object, *, reason_code: str
        ) -> tuple[object, str]:
            self.deleted.set()
            return owner, reason_code

    store = DeletingStore(
        GrowthAuthorityState(GrowthAuthorityPhase.LEGACY, generation=1)
    )
    legacy = RecordingAuthority("legacy")
    legacy.block = True
    router = GrowthAuthorityRouter(store=store, legacy=legacy)
    await router.start()

    ingress = asyncio.create_task(
        router.dispatch(
            request(
                GrowthIngressKind.PREFERENCE_WRITE,
                authority_generation=1,
            )
        )
    )
    await asyncio.wait_for(legacy.entered.wait(), timeout=1)
    deletion = asyncio.create_task(
        router.delete_profile_generation(
            ("profile-a", 7),
            reason_code="user_delete",
        )
    )
    await asyncio.sleep(0)
    assert not store.deleted.is_set()

    legacy.release.set()
    await asyncio.wait_for(ingress, timeout=1)
    assert await asyncio.wait_for(deletion, timeout=1) == (
        ("profile-a", 7),
        "user_delete",
    )


@pytest.mark.asyncio
async def test_revocation_barrier_is_writer_preferring_and_epoch_fenced() -> None:
    barrier = RevocationBarrier()
    first_reader_entered = asyncio.Event()
    release_first = asyncio.Event()
    order: list[str] = []

    async def first_reader() -> None:
        async with barrier.shared() as lease:
            order.append("reader-1")
            assert barrier.is_current(lease)
            first_reader_entered.set()
            await release_first.wait()

    async def writer() -> None:
        async with barrier.exclusive() as lease:
            order.append("writer")
            assert barrier.is_current(lease)

    async def late_reader() -> None:
        async with barrier.shared():
            order.append("reader-2")

    first = asyncio.create_task(first_reader())
    await first_reader_entered.wait()
    mutation = asyncio.create_task(writer())
    await asyncio.sleep(0)
    late = asyncio.create_task(late_reader())
    await asyncio.sleep(0)
    release_first.set()
    await asyncio.wait_for(asyncio.gather(first, mutation, late), timeout=1)

    assert order == ["reader-1", "writer", "reader-2"]
    assert barrier.epoch == 1


@pytest.mark.asyncio
async def test_revocation_barrier_timeout_does_not_leak_waiting_writer() -> None:
    barrier = RevocationBarrier()
    async with barrier.shared():
        with pytest.raises(RevocationBarrierTimeout):
            async with barrier.exclusive(timeout=0.001):
                pass

    async with barrier.shared(timeout=0.1):
        pass


def test_production_revocation_barrier_is_process_singleton() -> None:
    assert get_process_revocation_barrier() is get_process_revocation_barrier()


class FakePreferenceReader:
    async def read_preference(self, request: GrowthIngressRequest) -> object:
        return ("preference-read", request.profile_id)


class FakePreferenceWriter:
    async def write_preference(self, request: GrowthIngressRequest) -> object:
        return ("preference-write", request.payload["value"])


class FakeCodifier:
    async def codify(self, request: GrowthIngressRequest) -> object:
        return ("codify", request.profile_generation)


class FakeReminderReader:
    async def read_reminders(self, request: GrowthIngressRequest) -> object:
        return ("reminder-read", request.profile_id)


class FakeReminderWriter:
    async def write_reminder(self, request: GrowthIngressRequest) -> object:
        return ("reminder-write", request.payload["value"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (GrowthIngressKind.PREFERENCE_READ, ("preference-read", "profile-a")),
        (GrowthIngressKind.PREFERENCE_WRITE, ("preference-write", "x")),
        (GrowthIngressKind.CODIFY, ("codify", 7)),
        (GrowthIngressKind.REMINDER_READ, ("reminder-read", "profile-a")),
        (GrowthIngressKind.REMINDER_WRITE, ("reminder-write", "x")),
    ],
)
async def test_legacy_adapter_maps_typed_ports(
    kind: GrowthIngressKind, expected: object
) -> None:
    adapter = LegacyGrowthAuthorityAdapter(
        preference_reader=FakePreferenceReader(),
        preference_writer=FakePreferenceWriter(),
        codifier=FakeCodifier(),
        reminder_reader=FakeReminderReader(),
        reminder_writer=FakeReminderWriter(),
    )
    ingress = request(kind, authority_generation=1)

    if kind.is_write:
        actual = await adapter.write(ingress)
    else:
        actual = await adapter.read(ingress)

    assert actual == expected
