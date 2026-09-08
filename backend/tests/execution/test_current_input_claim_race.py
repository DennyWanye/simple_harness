"""Incident P: the current-input (``:input-v1:``) claim guard vs. CLAIMED->RUNNING.

Real Host FIFO/queue store, real signed control, real SDK runtime and ingress;
only the Provider is deterministic. The Host hands the physical request to the
SDK while the run head is still ``CLAIMED`` — ``SDK_START`` is admitted only in
that state, and ``CLAIMED->RUNNING`` may only be recorded after that start was
observed — so the guard must accept ``CLAIMED`` and must survive that one
transition landing underneath a single check.
"""
import asyncio
import hashlib
import sqlite3
from contextlib import asynccontextmanager

import pytest

from deskpet.execution.foreground_queue import ForegroundQueueError, ForegroundQueueStore, RunState
from deskpet.execution.primary_dependencies import (
    PrimaryHistoryDisclosureRejected, check_runtime_dependencies, rejection_reason,
)
from deskpet.memory.current_input_source import CurrentInputSourceError
from deskpet.memory.current_input_visibility import (
    CLAIM_KEYS, LIVE_CLAIM_STATES, claim_stamp, same_physical_claim,
)
from deskpet.memory.current_input_visibility import check_primary_input_visibility
from deskpet.memory.human_memory_service import QueueTurnRequest
from deskpet.memory.human_memory_v7 import local_memory_principal
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.memory.trusted_disclosure import resolve_current_disclosure
from deskpet.quality.corpus_c12 import disclosure_selection
from deskpet.quality.corpus_c12_control import FixtureSignedControl
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.execution.test_primary_create_new_runtime import fixture
from tests.execution.test_primary_foreground_runtime import Provider, build

TEXT = "受众：供应商；用途：送货邮件草稿；公开信息：送服务台。请整理本轮提供的公开工作材料。"
TERMINAL_STATES = ("COMPLETED", "FAILED", "STOPPED", "CANCELLED")


def _head(state_path):
    with sqlite3.connect(state_path) as db:
        db.row_factory = sqlite3.Row
        return db.execute("SELECT * FROM foreground_run_heads").fetchone()


def _synthetic_heads(path, states):
    """Only ``foreground_run_heads`` is read by ``claim_stamp``; the real store
    refuses forged transitions, so the whitelist itself is covered here."""
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE foreground_run_heads(host_run_id TEXT PRIMARY KEY,subject TEXT,turn_id TEXT,"
                   "primary_conversation_id TEXT,owner_id TEXT,generation INTEGER,current_state TEXT,sdk_run_id TEXT)")
        db.executemany("INSERT INTO foreground_run_heads VALUES (?,?,?,?,?,?,?,?)",
                       [(f"run-{value}", "user", "turn", "primary", "owner", 1, value, "sdk") for value in states])


async def _declared_turn(tmp_path, service):
    """A real signed-control turn whose resolved disclosure carries ``:input-v1:``."""
    control = await FixtureSignedControl(root=tmp_path / "control").start()
    config = await control.configure_disclosure(
        service, request_id="claim-race:configure",
        selection=disclosure_selection("C12-05", "供应商"))
    declaration = {"schema_version": 1, "kind": "current_user", "item_json_pointer": "/text",
                   "text_sha256": hashlib.sha256(TEXT.encode()).hexdigest()}
    with control.request_scope():
        queued = await service.enqueue_turn(QueueTurnRequest(
            None, "claim-race-turn", TEXT,
            disclosure_binding_ref=config["binding_ref"], input_declaration=declaration))
    return control, queued


class _GatedPolicy:
    """The real policy; ``after_check`` runs inside the guard's G1->G2 window."""

    def __init__(self, inner, after_check):
        self._inner, self._after_check = inner, after_check

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def check_dependencies(self, **kwargs):
        allowed = await self._inner.check_dependencies(**kwargs)
        await self._after_check()
        return allowed


@asynccontextmanager
async def drive(tmp_path, *, after_check=None, during_claimed=None, prepare=None):
    """One real foreground turn with the CLAIMED->RUNNING write held back.

    Holding ``record_sdk_started`` until the guard has run reproduces the
    production interleaving deterministically instead of racing for it
    (C12-19 lost that race by 7.5 ms).
    """
    state, _factory, service, configured, authority = await fixture(tmp_path)
    control, queued = await _declared_turn(tmp_path, service)
    provider = Provider()
    # The production visibility checker: an ordinary history read under this
    # non-self recipient is denied, so only the real current-input permit can
    # make the declared turn usable at all.
    memory = compose_human_memory_runtime(state, tmp_path / "visibility-memory.db",
                                          adapter_factory=lambda *_, **__: None,
                                          principal=local_memory_principal())
    async def checker(*, subject, disclosure_context, bindings):
        assert memory.principal().actor_id == subject
        return await check_primary_input_visibility(db_path=state, manager=await memory.manager(),
            principal=memory.principal(), disclosure_context=disclosure_context, bindings=bindings)
    runtime, stack, queue = await build(tmp_path, state, provider, binding_authority=authority,
                                        configured_root=configured, visibility_memory=memory,
                                        visibility_checker=checker)
    seen = {"states": [], "order": [], "state": state, "stack": stack, "turn_id": queued["turn_ref"],
            "in_guard": False}
    released = asyncio.Event()
    seen["released"] = released

    started = runtime._store.record_sdk_started
    async def gated_record_sdk_started(**kwargs):
        await asyncio.wait_for(released.wait(), 30)
        seen["order"].append("record_sdk_started")
        return await started(**kwargs)
    runtime._store.record_sdk_started = gated_record_sdk_started

    ingress_start = runtime._ingress.start
    async def watched_start(**kwargs):
        seen["order"].append("ingress.start")
        seen["state_at_handoff"] = _head(state)["current_state"]
        return await ingress_start(**kwargs)
    runtime._ingress.start = watched_start

    invoke = provider.invoke
    async def guarded_invoke(request, *, cancel):
        snapshot = await queue.current_snapshot(local_owner_auth().subject)
        seen["states"].append(snapshot.state.value)
        seen["sdk_run_id"], seen["host_run_id"] = snapshot.sdk_run_id, snapshot.host_run_id
        if during_claimed is not None:
            await during_claimed(seen)
        policy = runtime.history_policy if after_check is None else _GatedPolicy(
            runtime.history_policy, lambda: after_check(seen, released))
        seen["in_guard"] = True
        try:
            await check_runtime_dependencies(db_path=state, stack=stack, sdk_run_id=snapshot.sdk_run_id,
                                             request=request, policy_factory=lambda _: policy)
        except PrimaryHistoryDisclosureRejected as error:
            seen["rejection"] = error
        finally:
            seen["in_guard"] = False
            released.set()
        return await invoke(request, cancel=cancel)
    provider.invoke = guarded_invoke

    try:
        if prepare is not None:
            prepare(seen)
        assert await asyncio.wait_for(runtime._drive_once(), 60)
        yield seen
    finally:
        control.close()
        await runtime.close()
        await stack.close()
        await memory.close()


def _assert_passed(seen):
    rejection = seen.get("rejection")
    assert rejection is None, rejection_reason(rejection._private_cause)


@pytest.mark.asyncio
async def test_declared_input_guard_passes_while_the_head_is_still_claimed(tmp_path):
    """修前：G1 ``claim_stamp`` 白名单不含 CLAIMED → 交接期首个物理请求被拒。"""
    async with drive(tmp_path) as seen:
        assert seen["states"] == ["CLAIMED"], seen["states"]
        _assert_passed(seen)
        context = await resolve_current_disclosure(
            db_path=seen["state"], subject=local_owner_auth().subject, run_id=seen["sdk_run_id"],
            request_id=f"foreground-request-{seen['turn_id']}", turn_id=seen["turn_id"])
        assert ":input-v1:" in context.authority_ref
        with sqlite3.connect(seen["state"]) as db:
            assert db.execute(
                "SELECT terminal_state FROM foreground_terminal_receipts").fetchone()[0] == "COMPLETED"


@pytest.mark.asyncio
async def test_claimed_to_running_landing_inside_the_check_is_not_a_claim_change(tmp_path):
    """G1 读 CLAIMED、慢 Memory 检查期间真实落 RUNNING、G2 读 RUNNING：仍是同一次认领。"""
    async def after_check(seen, released):
        seen["at_g1"] = _head(seen["state"])["current_state"]
        released.set()
        for _ in range(3000):
            if _head(seen["state"])["current_state"] == "RUNNING":
                seen["at_g2"] = "RUNNING"
                return
            await asyncio.sleep(0.005)
        raise AssertionError("record_sdk_started never landed")

    async with drive(tmp_path, after_check=after_check) as seen:
        assert seen["states"] == ["CLAIMED"]
        assert (seen.get("at_g1"), seen.get("at_g2")) == ("CLAIMED", "RUNNING")
        _assert_passed(seen)


@pytest.mark.asyncio
async def test_running_cannot_be_recorded_before_the_sdk_start_is_observed(tmp_path):
    """顺序不变量：不能靠提前落 RUNNING 消除本竞态，只能承认 CLAIMED 就是交接态。

    ``SDK_START`` 只在 CLAIMED 准入，且 ``record_sdk_started`` 要求先有 start
    observation——两条硬约束合起来把 ``ingress.start`` 钉在 RUNNING 之前。
    """
    from deskpet.execution.foreground_queue import EffectBoundary, _EFFECT_BOUNDARY_ALLOWED_STATES

    assert _EFFECT_BOUNDARY_ALLOWED_STATES[EffectBoundary.SDK_START] == frozenset({"CLAIMED"})
    assert "CLAIMED" not in _EFFECT_BOUNDARY_ALLOWED_STATES[EffectBoundary.TOOL]
    async with drive(tmp_path) as seen:
        assert seen["state_at_handoff"] == "CLAIMED"
        with sqlite3.connect(seen["state"]) as db:
            db.row_factory = sqlite3.Row
            states = [row["to_state"] for row in db.execute(
                "SELECT to_state FROM foreground_run_transitions ORDER BY recorded_at,transition_id")]
            observations = [row["outcome"] for row in db.execute(
                "SELECT outcome FROM foreground_execution_start_observations "
                "ORDER BY recorded_at,observation_id")]
        assert "RUNNING" in states
        # RUNNING is only reachable behind an observed start; a hoisted
        # ``record_sdk_started`` would find no such row and fail closed.
        assert {"RETURNED", "QUERY_FOUND"} & set(observations)
        store = ForegroundQueueStore(seen["state"])
        with pytest.raises(ForegroundQueueError):
            await store.record_sdk_started(
                host_run_id=seen["host_run_id"], sdk_run_id=seen["sdk_run_id"], owner_id="foreign-owner",
                generation=1, sdk_event_id="hoisted", idempotency_key="hoisted-running")


@pytest.mark.asyncio
async def test_running_landing_inside_the_current_input_authority_is_not_a_claim_change(tmp_path, monkeypatch):
    """内层窗口：Host 当前输入 port 自己的慢读期间落 RUNNING，同样不是认领改变。

    ``resolve_current_input`` 的两次 head 读之间是本守卫里最长的一段真实 IO；修前
    它持有第三份状态字面量与第四份八元组全等比较，会把同一次交接判成认领被换掉。
    """
    from deskpet.memory import current_input_authority as authority_module

    original_reader = authority_module.read_current_input_source
    inner = {}

    async def slow_reader(**kwargs):
        result = await original_reader(**kwargs)
        seen = inner.get("seen")
        if seen is not None and seen["in_guard"] and "advanced" not in inner:
            inner["at_first_read"] = _head(seen["state"])["current_state"]
            seen["released"].set()
            for _ in range(3000):
                if _head(seen["state"])["current_state"] == "RUNNING":
                    inner["advanced"] = True
                    break
                await asyncio.sleep(0.005)
        return result

    monkeypatch.setattr(authority_module, "read_current_input_source", slow_reader)
    async with drive(tmp_path, prepare=lambda seen: inner.__setitem__("seen", seen)) as seen:
        assert seen["states"] == ["CLAIMED"]
        assert (inner.get("at_first_read"), inner.get("advanced")) == ("CLAIMED", True)
        _assert_passed(seen)


@pytest.mark.asyncio
async def test_guard_rejects_a_foreign_run_in_the_claimed_window_and_after_settlement(tmp_path):
    """负控：CLAIMED 不是放行任意请求；只有本次绑定的 SDK Run、且认领仍在世才成立。"""
    async def during_claimed(seen):
        for wrong in ((seen["host_run_id"], seen["sdk_run_id"] + "-other"),
                      (seen["host_run_id"] + "-other", seen["sdk_run_id"])):
            with pytest.raises(CurrentInputSourceError) as caught:
                await claim_stamp(seen["state"], *wrong)
            assert caught.value.code == "host_current_input_physical_claim_unavailable"
        seen["foreign_rejected"] = True

    async with drive(tmp_path, during_claimed=during_claimed) as seen:
        assert seen.get("foreign_rejected") and seen["states"] == ["CLAIMED"]
        _assert_passed(seen)
        # The run has settled: a replayed request finds no live claim at all.
        assert _head(seen["state"])["current_state"] not in LIVE_CLAIM_STATES
        with pytest.raises(CurrentInputSourceError) as caught:
            await claim_stamp(seen["state"], seen["host_run_id"], seen["sdk_run_id"])
        assert caught.value.code == "host_current_input_physical_claim_unavailable"


@pytest.mark.asyncio
async def test_claim_stamp_accepts_exactly_the_live_claim_states(tmp_path):
    """白名单本身：活着的认领六态放行，四个终态与未绑定的 SDK Run 一律拒绝。"""
    path = tmp_path / "heads.db"
    _synthetic_heads(path, [state.value for state in RunState])
    for state in RunState:
        run_id = f"run-{state.value}"
        if state.value in LIVE_CLAIM_STATES:
            stamp = await claim_stamp(path, run_id, "sdk")
            assert stamp["current_state"] == state.value
            assert set(stamp) == set(CLAIM_KEYS)
        else:
            with pytest.raises(CurrentInputSourceError):
                await claim_stamp(path, run_id, "sdk")
        with pytest.raises(CurrentInputSourceError) as caught:
            await claim_stamp(path, run_id, "other-sdk")
        assert caught.value.code == "host_current_input_physical_claim_unavailable"
    with pytest.raises(CurrentInputSourceError):
        await claim_stamp(path, "run-absent", "sdk")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE foreground_run_heads SET sdk_run_id=NULL WHERE host_run_id='run-CLAIMED'")
    with pytest.raises(CurrentInputSourceError) as caught:
        await claim_stamp(path, "run-CLAIMED", None)
    assert caught.value.code == "host_current_input_physical_claim_unavailable"
    assert set(LIVE_CLAIM_STATES) | set(TERMINAL_STATES) == {state.value for state in RunState}


def _stamp(**overrides):
    base = dict(host_run_id="run", subject="user", turn_id="turn", primary_conversation_id="primary",
                owner_id="owner", generation=1, current_state="CLAIMED", sdk_run_id="sdk")
    return {**base, **overrides}


def test_same_physical_claim_admits_only_the_start_advance():
    original = _stamp()
    assert same_physical_claim(original, _stamp())
    assert same_physical_claim(original, _stamp(current_state="RUNNING"))
    assert same_physical_claim(_stamp(current_state="RUNNING"), _stamp(current_state="RUNNING"))
    assert not same_physical_claim(original, None)
    assert not same_physical_claim(_stamp(current_state="RUNNING"), _stamp(current_state="CLAIMED"))
    for moved in ("PAUSE_REQUESTED", "PAUSED", "STOP_REQUESTED", "CANCEL_REQUESTED", *TERMINAL_STATES):
        assert not same_physical_claim(original, _stamp(current_state=moved)), moved
        assert not same_physical_claim(_stamp(current_state="RUNNING"), _stamp(current_state=moved)), moved
    for key in CLAIM_KEYS:
        if key == "current_state":
            continue
        assert not same_physical_claim(original, _stamp(**{key: 2 if key == "generation" else "x"})), key
    # The pre-``bind_sdk_run`` reader may see the binding land; nobody else may.
    unbound = _stamp(sdk_run_id=None)
    assert same_physical_claim(unbound, _stamp(), may_bind_sdk_run_id="sdk")
    assert same_physical_claim(unbound, _stamp(current_state="RUNNING"), may_bind_sdk_run_id="sdk")
    assert not same_physical_claim(unbound, _stamp())
    assert not same_physical_claim(unbound, _stamp(sdk_run_id="other"), may_bind_sdk_run_id="sdk")
    assert not same_physical_claim(original, _stamp(sdk_run_id="other"), may_bind_sdk_run_id="other")
    assert not same_physical_claim(original, _stamp(sdk_run_id=None), may_bind_sdk_run_id="sdk")


def test_redacted_rejection_reason_is_stable_and_payload_free():
    assert rejection_reason(CurrentInputSourceError("physical_claim_unavailable")) == (
        "host_current_input_physical_claim_unavailable")
    assert rejection_reason(ValueError("primary_input_claim_changed_during_check")) == (
        "primary_input_claim_changed_during_check")
    assert rejection_reason(ValueError("current_input_provider_request_mismatch")) == (
        "current_input_provider_request_mismatch")
    class _PayloadValueError(ValueError):
        pass

    for opaque in (ValueError("/Users/someone/state.db is locked"), KeyError("sk-secret"),
                   sqlite3.OperationalError("no such table: foreground_run_heads"),
                   RuntimeError("Bearer abc123"), ValueError("primary_dependencies_not_visible", "sk-secret"),
                   _PayloadValueError("primary_dependencies_not_visible"),
                   UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")):
        assert rejection_reason(opaque) == "primary_dependencies_rejected"
    error = PrimaryHistoryDisclosureRejected(
        public_message=rejection_reason(CurrentInputSourceError("claim_changed_during_check")),
        private_cause=ValueError("/Users/someone/state.db"))
    assert error.error_code == "primary_history_disclosure_rejected"
    assert str(error) == "host_current_input_claim_changed_during_check"
    assert "/Users" not in str(error.to_dict())
