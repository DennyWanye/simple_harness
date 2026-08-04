from __future__ import annotations

import pytest

from deskpet.companion.authority import (
    GrowthAuthorityPhase,
    GrowthAuthorityRouter,
    GrowthIngressKind,
    GrowthIngressRequest,
    GrowthIngressUnavailable,
    GrowthWritesPaused,
)
from deskpet.companion.cutover import (
    CompanionGrowthAuthorityAdapter,
    DEFAULT_POST_MARKER_STEPS,
    DEFAULT_PRE_MARKER_STEPS,
    GrowthAuthorityCutoverCoordinator,
    GrowthCutoverPlan,
    GrowthCutoverPlanConflict,
)
from deskpet.companion.store import CompanionStore


class _Authority:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = []

    async def read(self, request):
        self.calls.append(("read", request))
        return f"{self.name}:read"

    async def write(self, request):
        self.calls.append(("write", request))
        return f"{self.name}:write"


class _Crash(BaseException):
    pass


class _Executor:
    def __init__(self, *, fail_at=None, crash_at=None) -> None:
        self.fail_at = fail_at
        self.crash_at = crash_at
        self.calls = []
        self.states = []
        self.router = None

    async def execute(self, authorization):
        self.calls.append(authorization.step)
        if self.router is not None:
            self.states.append(
                (
                    authorization.step,
                    self.router.current.phase,
                    self.router.current.roll_forward_required,
                )
            )
        if authorization.step == self.crash_at:
            raise _Crash(authorization.step)
        if authorization.step == self.fail_at:
            raise RuntimeError(authorization.step)
        return {
            "receipt_hash": authorization.expected_step_hash,
            "result_hash": f"result:{authorization.step}",
        }


class _ReceiptCrashStore:
    def __init__(self, store, *, crash_step) -> None:
        self._store = store
        self._crash_step = crash_step
        self.crashed = False

    def __getattr__(self, name):
        return getattr(self._store, name)

    def record_growth_authority_cutover_step(self, **kwargs):
        if (
            kwargs["substep_phase"] == self._crash_step
            and not self.crashed
        ):
            self.crashed = True
            raise _Crash(self._crash_step)
        return self._store.record_growth_authority_cutover_step(**kwargs)


class _LegacyReceiptStore:
    def __init__(self, store, *, corrupt_reason=False) -> None:
        self._store = store
        self._corrupt_reason = corrupt_reason

    def __getattr__(self, name):
        return getattr(self._store, name)

    def list_growth_authority_cutover_journal(self, **kwargs):
        rows = self._store.list_growth_authority_cutover_journal(**kwargs)
        result = []
        for original in rows:
            row = dict(original)
            payload = dict(row["journal_payload"])
            if row["substep_phase"] in (
                DEFAULT_PRE_MARKER_STEPS + DEFAULT_POST_MARKER_STEPS
            ):
                payload.pop("result_hash", None)
                if self._corrupt_reason:
                    row["reason_code"] = "authority_transition"
            row["journal_payload"] = payload
            result.append(row)
        return result


class _DurableEffectExecutor:
    def __init__(self, *, applied, physical_counts, invocation_counts) -> None:
        self._applied = applied
        self._physical_counts = physical_counts
        self._invocation_counts = invocation_counts

    async def execute(self, authorization):
        step = authorization.step
        self._invocation_counts[step] = self._invocation_counts.get(step, 0) + 1
        proof = self._applied.get(step)
        if proof is None:
            proof = {
                "receipt_hash": authorization.expected_step_hash,
                "result_hash": f"durable-result:{step}",
            }
            self._applied[step] = proof
            self._physical_counts[step] = (
                self._physical_counts.get(step, 0) + 1
            )
        elif proof["receipt_hash"] != authorization.expected_step_hash:
            raise RuntimeError(f"durable effect drifted:{step}")
        return proof


def _plan(
    operation: str = "cutover-1",
    *,
    old_binding_generation: int = 4,
    new_binding_generation: int = 5,
    old_owner_binding_set_stamp: str = "owner-set-old",
    new_owner_binding_set_stamp: str = "owner-set-new",
) -> GrowthCutoverPlan:
    steps = DEFAULT_PRE_MARKER_STEPS + DEFAULT_POST_MARKER_STEPS
    return GrowthCutoverPlan(
        cutover_operation_id=operation,
        migration_version=1,
        migration_hash="migration-hash-1",
        legacy_owner_profile_id="legacy_local_profile",
        legacy_owner_generation=1,
        old_binding_generation=old_binding_generation,
        new_binding_generation=new_binding_generation,
        old_owner_binding_set_stamp=old_owner_binding_set_stamp,
        new_owner_binding_set_stamp=new_owner_binding_set_stamp,
        step_hashes={step: f"hash:{step}" for step in steps},
    )


async def _roots(tmp_path, executor):
    store = CompanionStore(tmp_path / "companion.db")
    store.create_profile(
        profile_id="legacy_local_profile",
        generation=1,
        identity_namespace_hash="local-identity",
        reason_code="test",
    )
    legacy = _Authority("legacy")
    companion = _Authority("companion")
    router = GrowthAuthorityRouter(
        store=store,
        legacy=legacy,
        companion=companion,
    )
    await router.start()
    executor.router = router
    coordinator = GrowthAuthorityCutoverCoordinator(
        router=router,
        store=store,
        executor=executor,
    )
    return store, legacy, companion, router, coordinator


async def _roots_with_store(tmp_path, executor, store_port):
    store = CompanionStore(tmp_path / "companion.db")
    store.create_profile(
        profile_id="legacy_local_profile",
        generation=1,
        identity_namespace_hash="local-identity",
        reason_code="test",
    )
    legacy = _Authority("legacy")
    companion = _Authority("companion")
    wrapped = store_port(store)
    router = GrowthAuthorityRouter(
        store=wrapped,
        legacy=legacy,
        companion=companion,
    )
    await router.start()
    coordinator = GrowthAuthorityCutoverCoordinator(
        router=router,
        store=wrapped,
        executor=executor,
    )
    return store, legacy, companion, router, coordinator


def _request(kind, generation):
    return GrowthIngressRequest(
        kind=kind,
        profile_id="legacy_local_profile",
        profile_generation=1,
        authority_generation=generation,
        payload={"value": "x"},
    )


@pytest.mark.asyncio
async def test_cutover_keeps_pointer_preparing_until_every_receipt_matches(
    tmp_path,
) -> None:
    executor = _Executor()
    store, legacy, companion, router, coordinator = await _roots(
        tmp_path, executor
    )

    state = await coordinator.execute(_plan())

    assert state.phase is GrowthAuthorityPhase.COMPANION
    assert executor.calls == list(
        DEFAULT_PRE_MARKER_STEPS + DEFAULT_POST_MARKER_STEPS
    )
    assert all(
        phase is GrowthAuthorityPhase.PREPARING
        for _, phase, _ in executor.states
    )
    assert all(
        marker is False
        for step, _, marker in executor.states
        if step in DEFAULT_PRE_MARKER_STEPS
    )
    assert all(
        marker is True
        for step, _, marker in executor.states
        if step in DEFAULT_POST_MARKER_STEPS
    )
    result = await router.dispatch(
        _request(GrowthIngressKind.PREFERENCE_WRITE, state.generation)
    )
    assert result == "companion:write"
    assert legacy.calls == []
    rows = store.list_growth_authority_cutover_journal(
        cutover_operation_id="cutover-1"
    )
    step_rows = {
        row["substep_phase"]: row
        for row in rows
        if row["substep_phase"] in _plan().step_hashes
    }
    assert set(step_rows) == set(_plan().step_hashes)
    assert all(
        row["old_owner_binding_set_stamp"] == "owner-set-old"
        and row["new_owner_binding_set_stamp"] == "owner-set-new"
        for row in step_rows.values()
    )


@pytest.mark.asyncio
async def test_pre_marker_failure_returns_to_legacy_and_chat_stays_available(
    tmp_path,
) -> None:
    executor = _Executor(fail_at="inactive_stage")
    _, legacy, _, router, coordinator = await _roots(tmp_path, executor)

    with pytest.raises(RuntimeError, match="inactive_stage"):
        await coordinator.execute(_plan())

    assert router.current.phase is GrowthAuthorityPhase.LEGACY
    assert router.current.roll_forward_required is False
    assert router.ordinary_chat_allowed is True
    assert await router.dispatch(
        _request(
            GrowthIngressKind.REMINDER_WRITE,
            router.current.generation,
        )
    ) == "legacy:write"
    assert len(legacy.calls) == 1


@pytest.mark.asyncio
async def test_post_marker_failure_pauses_and_never_falls_back_to_legacy(
    tmp_path,
) -> None:
    executor = _Executor(fail_at="owner_projection_reconcile")
    _, legacy, _, router, coordinator = await _roots(tmp_path, executor)

    with pytest.raises(RuntimeError, match="owner_projection_reconcile"):
        await coordinator.execute(_plan())

    assert router.current.phase is GrowthAuthorityPhase.PAUSED
    assert router.current.roll_forward_required is True
    assert router.ordinary_chat_allowed is True
    with pytest.raises(GrowthWritesPaused):
        await router.dispatch(
            _request(
                GrowthIngressKind.CODIFY,
                router.current.generation,
            )
        )
    assert legacy.calls == []


@pytest.mark.parametrize("failed_step", DEFAULT_PRE_MARKER_STEPS)
@pytest.mark.asyncio
async def test_every_pre_marker_step_failure_has_one_legacy_writer_and_recovers(
    tmp_path,
    failed_step,
) -> None:
    executor = _Executor(fail_at=failed_step)
    store, legacy, companion, router, coordinator = await _roots(
        tmp_path, executor
    )

    assert await router.dispatch(
        _request(GrowthIngressKind.REMINDER_WRITE, router.current.generation)
    ) == "legacy:write"
    with pytest.raises(RuntimeError, match=failed_step):
        await coordinator.execute(_plan())

    assert router.current.phase is GrowthAuthorityPhase.LEGACY
    assert router.current.roll_forward_required is False
    assert await router.dispatch(
        _request(GrowthIngressKind.REMINDER_WRITE, router.current.generation)
    ) == "legacy:write"
    assert len(legacy.calls) == 2
    assert companion.calls == []

    recovery = _Executor()
    recovery.router = router
    final = await GrowthAuthorityCutoverCoordinator(
        router=router,
        store=store,
        executor=recovery,
    ).execute(_plan())

    failed_index = DEFAULT_PRE_MARKER_STEPS.index(failed_step)
    assert recovery.calls == list(
        DEFAULT_PRE_MARKER_STEPS[failed_index:] + DEFAULT_POST_MARKER_STEPS
    )
    assert final.phase is GrowthAuthorityPhase.COMPANION
    assert await router.dispatch(
        _request(GrowthIngressKind.REMINDER_WRITE, final.generation)
    ) == "companion:write"
    assert len(legacy.calls) == 2
    assert len(companion.calls) == 1


@pytest.mark.parametrize("failed_step", DEFAULT_POST_MARKER_STEPS)
@pytest.mark.asyncio
async def test_every_post_marker_step_failure_has_no_fallback_writer_and_recovers(
    tmp_path,
    failed_step,
) -> None:
    executor = _Executor(fail_at=failed_step)
    store, legacy, companion, router, coordinator = await _roots(
        tmp_path, executor
    )

    assert await router.dispatch(
        _request(GrowthIngressKind.CODIFY, router.current.generation)
    ) == "legacy:write"
    with pytest.raises(RuntimeError, match=failed_step):
        await coordinator.execute(_plan())

    assert router.current.phase is GrowthAuthorityPhase.PAUSED
    assert router.current.roll_forward_required is True
    before = (len(legacy.calls), len(companion.calls))
    with pytest.raises(GrowthWritesPaused):
        await router.dispatch(
            _request(GrowthIngressKind.CODIFY, router.current.generation)
        )
    assert (len(legacy.calls), len(companion.calls)) == before == (1, 0)

    recovery = _Executor()
    recovery.router = router
    final = await GrowthAuthorityCutoverCoordinator(
        router=router,
        store=store,
        executor=recovery,
    ).execute(_plan())

    failed_index = DEFAULT_POST_MARKER_STEPS.index(failed_step)
    assert recovery.calls == list(DEFAULT_POST_MARKER_STEPS[failed_index:])
    assert final.phase is GrowthAuthorityPhase.COMPANION
    assert await router.dispatch(
        _request(GrowthIngressKind.CODIFY, final.generation)
    ) == "companion:write"
    assert len(legacy.calls) == 1
    assert len(companion.calls) == 1


@pytest.mark.parametrize(
    "receipt_crash_step",
    DEFAULT_PRE_MARKER_STEPS + DEFAULT_POST_MARKER_STEPS,
)
@pytest.mark.asyncio
async def test_callback_applied_before_receipt_crash_replays_without_double_write(
    tmp_path,
    receipt_crash_step,
) -> None:
    applied = {}
    physical_counts = {}
    invocation_counts = {}
    crashing_executor = _DurableEffectExecutor(
        applied=applied,
        physical_counts=physical_counts,
        invocation_counts=invocation_counts,
    )
    crash_store = None

    def wrap(store):
        nonlocal crash_store
        crash_store = _ReceiptCrashStore(
            store,
            crash_step=receipt_crash_step,
        )
        return crash_store

    store, legacy, companion, router, coordinator = await _roots_with_store(
        tmp_path,
        crashing_executor,
        wrap,
    )
    assert await router.dispatch(
        _request(GrowthIngressKind.CODIFY, router.current.generation)
    ) == "legacy:write"

    with pytest.raises(_Crash):
        await coordinator.execute(_plan())

    assert crash_store is not None and crash_store.crashed
    assert receipt_crash_step in applied
    assert physical_counts[receipt_crash_step] == 1
    assert receipt_crash_step not in {
        row["substep_phase"]
        for row in store.list_growth_authority_cutover_journal(
            cutover_operation_id="cutover-1"
        )
    }

    recovered_router = GrowthAuthorityRouter(
        store=store,
        legacy=legacy,
        companion=companion,
    )
    recovered = await recovered_router.start()
    before = (len(legacy.calls), len(companion.calls))
    if receipt_crash_step in DEFAULT_PRE_MARKER_STEPS:
        assert recovered.phase is GrowthAuthorityPhase.LEGACY
        assert await recovered_router.dispatch(
            _request(GrowthIngressKind.CODIFY, recovered.generation)
        ) == "legacy:write"
        assert (len(legacy.calls), len(companion.calls)) == (before[0] + 1, 0)
    else:
        assert recovered.phase is GrowthAuthorityPhase.PREPARING
        assert recovered.roll_forward_required is True
        with pytest.raises(GrowthIngressUnavailable):
            await recovered_router.dispatch(
                _request(GrowthIngressKind.CODIFY, recovered.generation)
            )
        assert (len(legacy.calls), len(companion.calls)) == before

    recovery_executor = _DurableEffectExecutor(
        applied=applied,
        physical_counts=physical_counts,
        invocation_counts=invocation_counts,
    )
    final = await GrowthAuthorityCutoverCoordinator(
        router=recovered_router,
        store=store,
        executor=recovery_executor,
    ).execute(_plan())

    assert final.phase is GrowthAuthorityPhase.COMPANION
    assert invocation_counts[receipt_crash_step] == 2
    assert physical_counts[receipt_crash_step] == 1
    assert set(physical_counts) == set(
        DEFAULT_PRE_MARKER_STEPS + DEFAULT_POST_MARKER_STEPS
    )
    assert set(physical_counts.values()) == {1}
    assert await recovered_router.dispatch(
        _request(GrowthIngressKind.CODIFY, final.generation)
    ) == "companion:write"
    assert len(companion.calls) == 1

    step_rows = [
        row
        for row in store.list_growth_authority_cutover_journal(
            cutover_operation_id="cutover-1"
        )
        if row["substep_phase"] in _plan().step_hashes
    ]
    assert len(step_rows) == len(_plan().step_hashes)
    assert len({row["substep_phase"] for row in step_rows}) == len(step_rows)
    assert all(row["journal_payload"]["result_hash"] for row in step_rows)


@pytest.mark.asyncio
async def test_cold_restart_after_marker_only_rolls_forward(tmp_path) -> None:
    crashing = _Executor(crash_at="owner_projection_reconcile")
    store, legacy, companion, router, coordinator = await _roots(
        tmp_path, crashing
    )
    with pytest.raises(_Crash):
        await coordinator.execute(_plan())
    assert router.current.phase is GrowthAuthorityPhase.PREPARING
    assert router.current.roll_forward_required is True

    recovered_router = GrowthAuthorityRouter(
        store=store,
        legacy=legacy,
        companion=companion,
    )
    recovered = await recovered_router.start()
    assert recovered.phase is GrowthAuthorityPhase.PREPARING
    recovery = _Executor()
    recovery.router = recovered_router
    final = await GrowthAuthorityCutoverCoordinator(
        router=recovered_router,
        store=store,
        executor=recovery,
    ).execute(_plan())

    assert final.phase is GrowthAuthorityPhase.COMPANION
    assert recovery.calls == list(
        DEFAULT_POST_MARKER_STEPS[1:]
    )
    assert legacy.calls == []


@pytest.mark.asyncio
async def test_cold_restart_accepts_strict_legacy_committed_step_receipts(
    tmp_path,
) -> None:
    crashing = _Executor(crash_at="handler_switch")
    store, legacy, companion, router, coordinator = await _roots(
        tmp_path, crashing
    )
    with pytest.raises(_Crash):
        await coordinator.execute(_plan())

    legacy_store = _LegacyReceiptStore(store)
    recovered_router = GrowthAuthorityRouter(
        store=legacy_store,
        legacy=legacy,
        companion=companion,
    )
    recovered = await recovered_router.start()
    assert recovered.phase is GrowthAuthorityPhase.PREPARING
    recovery = _Executor()
    recovery.router = recovered_router

    recovered_plan = _plan(
        old_binding_generation=8,
        new_binding_generation=8,
        old_owner_binding_set_stamp="new-process-owner-set",
        new_owner_binding_set_stamp="new-process-owner-set",
    )
    assert recovered_plan.plan_hash != _plan().plan_hash
    final = await GrowthAuthorityCutoverCoordinator(
        router=recovered_router,
        store=legacy_store,
        executor=recovery,
    ).execute(recovered_plan)

    assert final.phase is GrowthAuthorityPhase.COMPANION
    assert recovery.calls == list(DEFAULT_POST_MARKER_STEPS[2:])


@pytest.mark.asyncio
async def test_legacy_cutover_receipt_without_exact_commit_reason_fails_closed(
    tmp_path,
) -> None:
    crashing = _Executor(crash_at="handler_switch")
    store, legacy, companion, _, coordinator = await _roots(
        tmp_path, crashing
    )
    with pytest.raises(_Crash):
        await coordinator.execute(_plan())

    corrupt_store = _LegacyReceiptStore(store, corrupt_reason=True)
    recovered_router = GrowthAuthorityRouter(
        store=corrupt_store,
        legacy=legacy,
        companion=companion,
    )
    await recovered_router.start()
    recovery = _Executor()
    recovery.router = recovered_router

    with pytest.raises(
        GrowthCutoverPlanConflict,
        match="cutover step proof incomplete",
    ):
        await GrowthAuthorityCutoverCoordinator(
            router=recovered_router,
            store=corrupt_store,
            executor=recovery,
        ).execute(_plan())


@pytest.mark.asyncio
async def test_companion_adapter_routes_each_typed_ingress_once() -> None:
    calls = []

    async def record(request):
        calls.append(request.kind)
        return request.kind.value

    adapter = CompanionGrowthAuthorityAdapter(
        readers={
            GrowthIngressKind.PREFERENCE_READ: record,
            GrowthIngressKind.REMINDER_READ: record,
        },
        writers={
            GrowthIngressKind.PREFERENCE_WRITE: record,
            GrowthIngressKind.CODIFY: record,
            GrowthIngressKind.REMINDER_WRITE: record,
        },
    )
    for kind in GrowthIngressKind:
        request = _request(kind, 1)
        result = (
            await adapter.write(request)
            if kind.is_write
            else await adapter.read(request)
        )
        assert result == kind.value
    assert calls == list(GrowthIngressKind)


def test_cutover_plan_rejects_relay_owned_legacy_import() -> None:
    with pytest.raises(ValueError, match="local-owner-only"):
        GrowthCutoverPlan(
            cutover_operation_id="cutover-1",
            migration_version=1,
            migration_hash="migration",
            legacy_owner_profile_id="relay-user",
            legacy_owner_generation=1,
            old_binding_generation=1,
            new_binding_generation=2,
            old_owner_binding_set_stamp="old",
            new_owner_binding_set_stamp="new",
            step_hashes={
                step: f"hash:{step}"
                for step in (
                    DEFAULT_PRE_MARKER_STEPS + DEFAULT_POST_MARKER_STEPS
                )
            },
            source_owner_policy="active_relay",
        )
