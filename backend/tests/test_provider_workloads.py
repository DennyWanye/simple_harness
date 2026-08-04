from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from deskpet.execution.provider_workload_audit import ProviderWorkloadAuditStore
from deskpet.execution.provider_workloads import (
    BackgroundModelPolicy,
    BreakerStateName,
    ProviderCircuitOpen,
    ProviderFailurePolicyV1,
    ProviderProbeCancelled,
    ProviderWorkloadBreaker,
    ProviderWorkloadRouter,
    PROVIDER_WORKLOAD_CALLSITES,
    ResolvedProviderWorkloadTarget,
    SessionAwareLLMCall,
    WorkloadClass,
    workload_context,
)
from deskpet.memory.migrator import run_migrations


class _Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _ProviderError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        *,
        error_class: str = "",
        retry_after: float | None = None,
        model_quota: bool = False,
    ) -> None:
        super().__init__(error_class or f"http {status_code}")
        self.status_code = status_code
        self.error_class = error_class
        self.retry_after = retry_after
        self.model_quota = model_quota


def _target(*, model: str = "kimi-k2.5", revision: int = 7):
    return ResolvedProviderWorkloadTarget(
        provider=object(),
        provider_id="kimi",
        model=model,
        incarnation_id="inc-a",
        config_revision=revision,
        endpoint="https://relay.invalid/v1",
        account_ref="acct-a",
    )


def _context(callsite: str = "agent.goal_check", request: str = "req"):
    return workload_context(
        callsite,
        request_id=request,
        session_id="session-a",
        root_run_id="root-a",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402])
async def test_cold_failure_is_single_flight_across_twenty_callers(status: int):
    breaker = ProviderWorkloadBreaker()
    calls = 0
    entered = asyncio.Event()
    release = asyncio.Event()

    async def operation():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        raise _ProviderError(status)

    tasks = [
        asyncio.create_task(
            breaker.execute(
                target=_target(model="kimi-k2.5" if i % 2 else "kimi-k2"),
                context=_context(
                    "agent.goal_check" if i % 2 else "memory.query_rewrite",
                    request=f"req-{i}",
                ),
                operation=operation,
            )
        )
        for i in range(20)
    ]
    await entered.wait()
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert calls == 1
    assert isinstance(results[0], _ProviderError)
    assert all(
        isinstance(item, (_ProviderError, ProviderCircuitOpen)) for item in results
    )


@pytest.mark.asyncio
async def test_healthy_cold_probe_does_not_share_semantic_results():
    breaker = ProviderWorkloadBreaker()
    calls = 0
    gate = asyncio.Event()

    async def operation():
        nonlocal calls
        calls += 1
        if calls == 1:
            await gate.wait()
        return calls

    tasks = [
        asyncio.create_task(
            breaker.execute(
                target=_target(),
                context=_context(request=f"req-{i}"),
                operation=operation,
            )
        )
        for i in range(20)
    ]
    await asyncio.sleep(0)
    assert calls == 1
    gate.set()
    results = await asyncio.gather(*tasks)
    assert calls == 20
    assert len({value for value, _transition in results}) == 20


@pytest.mark.asyncio
async def test_quota_cooldown_half_open_and_user_reset_use_test_clock():
    clock = _Clock()
    breaker = ProviderWorkloadBreaker(clock=clock)
    calls = 0

    async def fail():
        nonlocal calls
        calls += 1
        raise _ProviderError(402, retry_after=45)

    with pytest.raises(_ProviderError):
        await breaker.execute(target=_target(), context=_context(), operation=fail)
    with pytest.raises(ProviderCircuitOpen) as blocked:
        await breaker.execute(target=_target(), context=_context(), operation=fail)
    assert blocked.value.open_until == pytest.approx(1_045.0)
    assert calls == 1

    clock.now = 1_045.0

    async def healthy():
        nonlocal calls
        calls += 1
        return "ok"

    result, transition = await breaker.execute(
        target=_target(), context=_context(), operation=healthy
    )
    assert (result, transition) == ("ok", "half_open_closed")
    snapshot = await breaker.state_snapshot()
    quota_key = next(key for key in snapshot if key[0] == "account_quota")
    generation = await breaker.reset(quota_key, expected_provider_id="kimi")
    assert generation == 1
    snapshot = await breaker.state_snapshot()
    assert snapshot[quota_key]["state"] == BreakerStateName.UNPROVEN.value


@pytest.mark.asyncio
async def test_cancelled_probe_wakes_followers_and_next_call_can_probe():
    breaker = ProviderWorkloadBreaker()
    entered = asyncio.Event()

    async def blocked():
        entered.set()
        await asyncio.Event().wait()

    leader = asyncio.create_task(
        breaker.execute(target=_target(), context=_context(), operation=blocked)
    )
    await entered.wait()
    follower = asyncio.create_task(
        breaker.execute(target=_target(), context=_context(), operation=blocked)
    )
    await asyncio.sleep(0)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    with pytest.raises(ProviderProbeCancelled):
        await follower

    async def healthy():
        return "recovered"

    assert (
        await breaker.execute(target=_target(), context=_context(), operation=healthy)
    )[0] == "recovered"


@pytest.mark.asyncio
async def test_model_failure_does_not_open_whole_provider_revision():
    breaker = ProviderWorkloadBreaker()

    async def missing():
        raise _ProviderError(404, error_class="model_not_found")

    with pytest.raises(_ProviderError):
        await breaker.execute(
            target=_target(model="missing-model"),
            context=_context(),
            operation=missing,
        )

    async def healthy():
        return "other-model-ok"

    result, _transition = await breaker.execute(
        target=_target(model="healthy-model"),
        context=_context(request="other"),
        operation=healthy,
    )
    assert result == "other-model-ok"


@pytest.mark.asyncio
async def test_adapter_declared_model_quota_only_blocks_that_model():
    breaker = ProviderWorkloadBreaker()

    async def exhausted():
        raise _ProviderError(402, model_quota=True)

    with pytest.raises(_ProviderError):
        await breaker.execute(
            target=_target(model="quota-model"),
            context=_context(),
            operation=exhausted,
        )
    with pytest.raises(ProviderCircuitOpen):
        await breaker.execute(
            target=_target(model="quota-model"),
            context=_context(),
            operation=exhausted,
        )

    async def healthy():
        return "other-model-ok"

    result, _transition = await breaker.execute(
        target=_target(model="other-model"),
        context=_context(request="other-model"),
        operation=healthy,
    )
    assert result == "other-model-ok"


@pytest.mark.asyncio
async def test_rate_limit_does_not_block_other_workload_class():
    breaker = ProviderWorkloadBreaker()

    async def limited():
        raise _ProviderError(429, retry_after=30)

    with pytest.raises(_ProviderError):
        await breaker.execute(
            target=_target(), context=_context(), operation=limited
        )

    maintenance = BackgroundModelPolicy.context(
        "memory.reflection", request_id="maintenance-rate-independent"
    )

    async def healthy():
        return "maintenance-ok"

    result, _transition = await breaker.execute(
        target=_target(), context=maintenance, operation=healthy
    )
    assert result == "maintenance-ok"


@pytest.mark.asyncio
async def test_half_open_probe_can_reclassify_without_stranding_old_scope():
    clock = _Clock()
    breaker = ProviderWorkloadBreaker(clock=clock, transient_cooldown_s=5)

    async def endpoint_down():
        raise _ProviderError(503)

    with pytest.raises(_ProviderError):
        await breaker.execute(
            target=_target(), context=_context(), operation=endpoint_down
        )
    clock.now += 5

    async def credential_rejected():
        raise _ProviderError(401)

    with pytest.raises(_ProviderError):
        await breaker.execute(
            target=_target(), context=_context(), operation=credential_rejected
        )
    snapshot = await breaker.state_snapshot()
    assert snapshot[("endpoint", "https://relay.invalid/v1")]["state"] == "closed"
    credential_key = next(key for key in snapshot if key[0] == "credential")
    assert snapshot[credential_key]["state"] == "open"
    with pytest.raises(ProviderCircuitOpen):
        await breaker.execute(
            target=_target(), context=_context(), operation=credential_rejected
        )


@pytest.mark.asyncio
async def test_registry_reconcile_evicts_old_revision_and_readd_incarnation():
    breaker = ProviderWorkloadBreaker()

    async def rejected():
        raise _ProviderError(401)

    with pytest.raises(_ProviderError):
        await breaker.execute(
            target=_target(revision=7), context=_context(), operation=rejected
        )
    revised = _target(revision=8)
    assert await breaker.reconcile_provider_revision(revised) >= 1
    snapshot = await breaker.state_snapshot()
    assert not any("7" in key for key in snapshot)

    readded = ResolvedProviderWorkloadTarget(
        provider=object(),
        provider_id="kimi",
        model="kimi-k2.5",
        incarnation_id="inc-b",
        config_revision=1,
        endpoint="https://relay.invalid/v1",
        account_ref="acct-a",
    )
    assert await breaker.reconcile_provider_revision(readded) >= 0

    async def healthy():
        return "new-incarnation"

    result, _transition = await breaker.execute(
        target=readded, context=_context(), operation=healthy
    )
    assert result == "new-incarnation"


def test_failure_policy_scopes_match_failure_authority():
    policy = ProviderFailurePolicyV1()
    context = _context()
    credential = policy.classify(
        _ProviderError(401), target=_target(model="a"), context=context
    )
    quota = policy.classify(
        _ProviderError(402), target=_target(model="b"), context=context
    )
    model = policy.classify(
        _ProviderError(404, error_class="model_not_found"),
        target=_target(model="c"),
        context=context,
    )
    limited = policy.classify(
        _ProviderError(429, retry_after=9), target=_target(), context=context
    )
    endpoint = policy.classify(
        _ProviderError(503), target=_target(), context=context
    )
    assert credential.scope_key[:2] == ("credential", "kimi")
    assert "a" not in credential.scope_key
    assert quota.scope_key[:2] == ("account_quota", "kimi")
    assert "b" not in quota.scope_key
    assert model.scope_key[-1] == "c"
    assert limited.scope_key[-1] == "session-auxiliary"
    assert limited.retry_after_s == 9
    assert endpoint.scope_key == ("endpoint", "https://relay.invalid/v1")


def test_background_policy_never_claims_session_identity():
    context = BackgroundModelPolicy.context(
        "memory.reflection", request_id="maintenance-1"
    )
    assert context.detached is True
    assert context.session_id is None
    assert context.root_run_id is None


@pytest.mark.asyncio
async def test_background_policy_resolves_registry_chain_dict_to_full_entry():
    class Entry:
        id = "relay-cloud"
        model = "kimi-k3"
        incarnation_id = "inc-relay"
        config_revision = 7
        base_url = "https://relay.invalid/v1"
        account_ref = "account-a"

    class Registry:
        def get_chain(self):
            return [{"id": "relay-cloud", "model": "kimi-k3"}]

        def get_entry(self, provider_id):
            return Entry() if provider_id == "relay-cloud" else None

    provider = object()
    policy = BackgroundModelPolicy(
        registry=Registry(),
        provider_factory=lambda entry, model: provider,
    )

    target = await policy.resolve(
        BackgroundModelPolicy.context(
            "provider.maintenance_probe",
            request_id="maintenance-chain-dict",
        )
    )

    assert target.provider is provider
    assert target.provider_id == "relay-cloud"
    assert target.model == "kimi-k3"
    assert target.endpoint == "https://relay.invalid/v1"
    assert target.account_ref == "account-a"


def test_inventory_taxonomy_contains_all_provider_workload_classes():
    assert {item.value for item in WorkloadClass} == {
        "main",
        "session-auxiliary",
        "system-maintenance",
        "explicit-independent",
    }
    assert {
        item.workload_class for item in PROVIDER_WORKLOAD_CALLSITES.values()
    } == set(WorkloadClass)


def test_reviewed_inventory_covers_every_session_auxiliary_model_callsite():
    assert {
        "memory.fact_extract",
        "memory.fact_merge",
        "memory.cross_key_merge",
        "memory.query_rewrite",
        "memory.entity_extract",
        "agent.goal_check",
        "agent.problem_preanalysis",
        "agent.context_compaction",
        "agent.verify_ephemeral",
        "agent.external_evaluator",
        "memory.curation",
        "memory.forget_confirm",
        "companion.preference_interpret",
        "companion.personal_workflow_match",
    }.issubset(PROVIDER_WORKLOAD_CALLSITES)


@pytest.mark.asyncio
async def test_every_session_auxiliary_uses_the_resolver_selected_provider():
    class Provider:
        def __init__(self, name: str) -> None:
            self.name = name
            self.calls = 0

        async def chat_with_tools(self, **_kwargs):
            self.calls += 1
            return {"content": self.name}

    kimi = Provider("kimi")
    glm = Provider("glm")

    async def resolve(_context):
        target = _target()
        return ResolvedProviderWorkloadTarget(
            provider=kimi,
            provider_id=target.provider_id,
            model=target.model,
            incarnation_id=target.incarnation_id,
            config_revision=target.config_revision,
            endpoint=target.endpoint,
            account_ref=target.account_ref,
        )

    llm = SessionAwareLLMCall(ProviderWorkloadRouter(resolve))
    session_callsites = [
        item.callsite_id
        for item in PROVIDER_WORKLOAD_CALLSITES.values()
        if item.workload_class is WorkloadClass.SESSION_AUXILIARY
    ]
    for callsite_id in session_callsites:
        assert await llm("prompt", workload_context=_context(callsite_id)) == "kimi"
    assert kimi.calls == len(session_callsites)
    assert glm.calls == 0


@pytest.mark.asyncio
async def test_each_occurrence_has_distinct_stable_audit_identity():
    class Audit:
        def __init__(self):
            self.events = []

        async def record(self, event):
            self.events.append(dict(event))

    class Provider:
        async def chat_with_tools(self, **_kwargs):
            return {"content": "ok"}

    async def resolve(_context):
        target = _target()
        return ResolvedProviderWorkloadTarget(
            provider=Provider(),
            provider_id=target.provider_id,
            model=target.model,
            incarnation_id=target.incarnation_id,
            config_revision=target.config_revision,
            endpoint=target.endpoint,
            account_ref=target.account_ref,
        )

    audit = Audit()
    router = ProviderWorkloadRouter(resolve, audit=audit)
    llm = SessionAwareLLMCall(router)
    first = _context(request="same-request")
    second = _context(request="same-request")
    await llm("one", workload_context=first)
    await llm("two", workload_context=second)
    assert len(audit.events) == 2
    assert audit.events[0]["stable_call_id"] != audit.events[1]["stable_call_id"]


@pytest.mark.asyncio
async def test_audit_failure_never_masks_provider_success_or_error():
    class BrokenAudit:
        async def record(self, _event):
            raise RuntimeError("database is busy")

    class Provider:
        def __init__(self, error=None):
            self.error = error

        async def chat_with_tools(self, **_kwargs):
            if self.error:
                raise self.error
            return type("Response", (), {"content": "object response"})()

    provider = Provider()

    async def resolve(_context):
        target = _target()
        return ResolvedProviderWorkloadTarget(
            provider=provider,
            provider_id=target.provider_id,
            model=target.model,
            incarnation_id=target.incarnation_id,
            config_revision=target.config_revision,
            endpoint=target.endpoint,
            account_ref=target.account_ref,
        )

    router = ProviderWorkloadRouter(resolve, audit=BrokenAudit())
    assert await router.invoke("ok", workload_context=_context()) == "object response"
    provider.error = ValueError("original provider error")
    with pytest.raises(ValueError, match="original provider error"):
        await router.invoke("bad", workload_context=_context(request="req-2"))


@pytest.mark.asyncio
async def test_schedule_registers_root_task_before_return():
    entered = asyncio.Event()

    class Provider:
        async def chat_with_tools(self, **_kwargs):
            entered.set()
            await asyncio.Event().wait()

    async def resolve(_context):
        target = _target()
        return ResolvedProviderWorkloadTarget(
            provider=Provider(),
            provider_id=target.provider_id,
            model=target.model,
            incarnation_id=target.incarnation_id,
            config_revision=target.config_revision,
            endpoint=target.endpoint,
            account_ref=target.account_ref,
        )

    router = ProviderWorkloadRouter(resolve)
    task = await router.schedule("wait", workload_context=_context())
    await entered.wait()
    assert await router.cancel_root("root-a") == 1
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_audit_migration_record_and_bounded_retention(tmp_path: Path):
    db_path = tmp_path / "state.db"
    await run_migrations(db_path)
    clock = _Clock(4_000_000.0)
    store = ProviderWorkloadAuditStore(db_path, clock=clock)
    base = {
        "workload_class": "session-auxiliary",
        "callsite_id": "agent.goal_check",
        "purpose": "supervisor",
        "provider_id": "kimi",
        "provider_incarnation_id": "inc-a",
        "model": "kimi-k2.5",
        "config_revision": 7,
        "session_id": "session-a",
        "root_run_id": "root-a",
        "detached": False,
        "owner_policy": "in-turn",
        "status": "completed",
        "duration_ms": 12,
        "error_class": None,
        "breaker_transition": "probe_closed",
        "injection_ref": None,
    }
    for i in range(5):
        clock.now = 1_000.0 + i
        await store.record({**base, "stable_call_id": f"call-{i}"})
    clock.now = 4_000_000.0
    removed = await store.enforce_retention(batch_size=3)
    assert removed == 3
    async with aiosqlite.connect(db_path) as db:
        rows = await (
            await db.execute(
                "SELECT stable_call_id FROM provider_workload_audit ORDER BY stable_call_id"
            )
        ).fetchall()
    assert rows == [("call-3",), ("call-4",)]
