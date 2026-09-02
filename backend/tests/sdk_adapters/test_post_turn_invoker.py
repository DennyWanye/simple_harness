# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b Task 3：``RunBoundInvoker`` 五态 attempt 账本与 unknown 三分类（design-freeze §6）。

真实 ``ForegroundQueueStore``（lease）+ 真实 v46 ``post_turn_invocation_attempts/_members`` + 确定性
adapter 替身：三键查重（(request_hash, ordinal) 命中 succeeded → 同行 0 调用；成员 open handed_off /
sent_unknown → 拒绝 0 调用；否则新 attempt）；reserved 行与 lease 校验同事务（非 owner 零行零调用）；
reserved→handed_off→succeeded|failed|unknown；``not_sent`` 可 attempt+1、``sent_unknown`` 永不重发、
``sent_confirmed`` 仅经注入 observer 返回同一行；返回后再验 lease 才允许 apply。
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from simple_harness import RequestId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderRequest
from simple_harness.providers.errors import (
    ProviderCancelledError,
    ProviderRequestRejectedError,
    ProviderTimeoutError,
)

from deskpet.execution.foreground_queue import EffectBoundary, ForegroundQueueError
from deskpet.sdk_adapters.post_turn_invoker import (
    ForegroundLeaseFence,
    RunBoundInvoker,
    binding_model_config_hash,
)
from tests.sdk_adapters import s5b_closure_harness as ch

REQUEST_HASH = hashlib.sha256(b"closure-request").hexdigest()
SET_KEY = hashlib.sha256(b"evidence-set").hexdigest()
MEMBERS = ((ch.SUBJECT, "sdk-run-inv", "evidence-1"),)


def _request(row) -> ProviderRequest:  # type: ignore[no-untyped-def]
    return ProviderRequest(
        RequestId(f"post-turn-{row.attempt_ordinal}"),
        (Message(role=MessageRole.USER, content="observe"),),
    )


async def _invoker(tmp_path: Path, adapter: ch.FakeAdapter, *, fault=None, owner_id: str | None = None,
                   generation: int | None = None):  # type: ignore[no-untyped-def]
    env = await ch.bound_run(tmp_path, "sdk-run-inv")
    fence = ForegroundLeaseFence(
        env.store, host_run_id=env.admission.host_run_id, sdk_run_id=env.run_id,
        owner_id=owner_id or env.admission.owner_id, generation=generation or env.admission.generation,
    )
    return env, RunBoundInvoker(env.db_path, fence=fence, adapter_factory=lambda record: adapter, clock=env.clock, fault_inject=fault)


async def _invoke(env, invoker, *, request_hash: str = REQUEST_HASH, plan_id: str | None = "p" * 64, binding=None):  # type: ignore[no-untyped-def]
    return await invoker.invoke(
        purpose="closure", host_run_id=env.admission.host_run_id, sdk_run_id=env.run_id,
        generation=env.admission.generation, task_scope_id=ch.SCOPE, closure_watermark=3,
        request_hash=request_hash, evidence_set_key=SET_KEY, members=MEMBERS,
        binding_record={**ch.BINDING_RECORD, "run_id": env.run_id} if binding is None else binding,
        build_request=_request, plan_id=plan_id, deadline_seconds=5.0,
    )


@pytest.mark.asyncio
async def test_success_settles_in_apply_tx_and_replay_reuses_same_row(tmp_path: Path) -> None:
    adapter = ch.FakeAdapter([ch.plain_answer("first"), ch.plain_answer("second")])
    env, invoker = await _invoker(tmp_path, adapter)
    outcome = await _invoke(env, invoker)
    assert outcome.status == "succeeded" and outcome.response is not None and outcome.provider_calls == 1
    assert outcome.attempt_ordinal == 1
    # 五态：reserved → handed_off；成功结果由调用方在 apply 事务内 settle（同事务）。
    assert [(o, s) for o, s, *_ in ch.attempts(env.db_path)] == [(1, "handed_off")]
    assert ch.rows(env.db_path, "SELECT subject,run_id,evidence_id FROM post_turn_invocation_members") == [MEMBERS[0]]
    async with invoker.transaction() as db:
        await invoker.settle_succeeded_tx(db, outcome.attempt_id, response=outcome.response, plan_id="p" * 64)
    assert [(o, s, u, r, p, g) for o, s, u, r, p, g in ch.attempts(env.db_path)] == [(1, "succeeded", None, None, "p" * 64, 1)]
    [(result_hash, model_config_hash, provider_id, model_id)] = ch.rows(
        env.db_path,
        "SELECT result_hash,model_config_hash,provider_id,model_id FROM post_turn_invocation_attempts",
    )
    assert result_hash is not None and len(result_hash) == 64 and provider_id == "provider-1" and model_id == "model-1"
    assert model_config_hash == binding_model_config_hash({**ch.BINDING_RECORD, "run_id": env.run_id}, endpoint_identity="e" * 64)
    # 三键查重命中 succeeded → 同行、0 调用。
    again = await _invoke(env, invoker)
    assert again.status == "reused" and again.attempt_id == outcome.attempt_id and again.provider_calls == 0
    assert len(adapter.calls) == 1
    # 另一 request_hash 但同成员：成员上没有 open attempt → 允许新 attempt。
    other = await _invoke(env, invoker, request_hash=hashlib.sha256(b"other").hexdigest())
    assert other.status == "succeeded" and len(adapter.calls) == 2


@pytest.mark.asyncio
async def test_unknown_taxonomy_not_sent_retries_sent_unknown_never_resends(tmp_path: Path) -> None:
    # not_sent：连接失败（请求未进入传输）→ failed(not_sent) → 下一次 attempt+1。
    adapter = ch.FakeAdapter([httpx.ConnectError("refused"), ch.plain_answer("ok")])
    env, invoker = await _invoker(tmp_path, adapter)
    first = await _invoke(env, invoker)
    assert (first.status, first.unknown_class, first.reason_code) == ("failed", "not_sent", "provider_not_sent:ConnectError")
    assert [(o, s, u) for o, s, u, *_ in ch.attempts(env.db_path)] == [(1, "failed", "not_sent")]
    second = await _invoke(env, invoker)
    assert second.status == "succeeded" and second.attempt_ordinal == 2 and len(adapter.calls) == 2

    # sent_unknown：已发出、结果不可得（Provider cancel / 读超时）→ unknown(sent_unknown) → 以后同 request 0 调用。
    # （Task 6：asyncio.CancelledError 不再被吞——见 test_invoker_propagates_cancelled_error。）
    adapter2 = ch.FakeAdapter([ProviderCancelledError(), ch.plain_answer("never")])
    env2, invoker2 = await _invoker(tmp_path / "two", adapter2)
    unknown = await _invoke(env2, invoker2)
    assert (unknown.status, unknown.unknown_class) == ("unknown", "sent_unknown")
    blocked = await _invoke(env2, invoker2)
    assert blocked.status == "blocked" and blocked.reason_code == "closure_attempt_unknown" and blocked.provider_calls == 0
    assert len(adapter2.calls) == 1
    # 同成员、不同 request_hash 也被拒（成员上有 open sent_unknown）。
    blocked2 = await _invoke(env2, invoker2, request_hash=hashlib.sha256(b"other").hexdigest())
    assert blocked2.status == "blocked" and len(adapter2.calls) == 1
    # 读超时同样是 sent_unknown（响应可能已在路上）。
    adapter3 = ch.FakeAdapter([ProviderTimeoutError(public_message="timeout")])
    env3, invoker3 = await _invoker(tmp_path / "three", adapter3)
    timeout = await _invoke(env3, invoker3)
    assert (timeout.status, timeout.unknown_class, timeout.reason_code) == ("unknown", "sent_unknown", "closure_timeout")

    # 确定性失败（Provider 明确拒绝）→ failed（非 not_sent）→ 不再重发（terminal）。
    adapter4 = ch.FakeAdapter([ProviderRequestRejectedError(public_message="bad request"), ch.plain_answer("never")])
    env4, invoker4 = await _invoker(tmp_path / "four", adapter4)
    failed = await _invoke(env4, invoker4)
    assert failed.status == "failed" and failed.unknown_class is None
    again = await _invoke(env4, invoker4)
    assert again.status == "blocked" and again.reason_code == "closure_attempt_failed" and len(adapter4.calls) == 1


@pytest.mark.asyncio
async def test_sent_confirmed_only_via_injected_observer_returns_same_row(tmp_path: Path) -> None:
    """handed_off 行（crash 于 handoff 之后、settle 之前）只能由注入的 reconciliation observer 确认为
    ``sent_confirmed`` 并返回同一行、零调用；无 observer 时同一行 → blocked。"""
    adapter = ch.FakeAdapter([ch.plain_answer("never sent")])
    env, invoker = await _invoker(tmp_path, adapter, fault=ch.OneShot("attempt-handed-off"))
    with pytest.raises(RuntimeError, match="injected:attempt-handed-off"):
        await _invoke(env, invoker)
    assert [(o, s, u) for o, s, u, *_ in ch.attempts(env.db_path)] == [(1, "handed_off", None)]
    assert len(adapter.calls) == 0
    blocked = await _invoke(env, invoker)
    assert blocked.status == "blocked" and blocked.reason_code == "closure_attempt_unknown" and blocked.provider_calls == 0
    confirmed = ch.plain_answer("confirmed by observer")
    seen: list[str] = []

    async def observer(row):  # type: ignore[no-untyped-def]
        seen.append(row.attempt_id)
        return confirmed

    invoker.reconciliation_observer = observer
    outcome = await _invoke(env, invoker)
    assert outcome.status == "succeeded" and outcome.attempt_id == blocked.attempt_id and outcome.response is confirmed
    assert outcome.unknown_class == "sent_confirmed" and outcome.provider_calls == 0
    assert seen == [blocked.attempt_id] and len(adapter.calls) == 0
    assert [(o, s, u) for o, s, u, *_ in ch.attempts(env.db_path)] == [(1, "unknown", "sent_confirmed")]
    # 已 settle 的 unknown(sent_unknown) 行不可再改（v46 单调守卫）：observer 不再介入，仍 blocked。
    adapter2 = ch.FakeAdapter([ProviderCancelledError()])
    env2, invoker2 = await _invoker(tmp_path / "two", adapter2)
    unknown = await _invoke(env2, invoker2)
    assert (unknown.status, unknown.unknown_class) == ("unknown", "sent_unknown")
    invoker2.reconciliation_observer = observer
    assert (await _invoke(env2, invoker2)).status == "blocked"


@pytest.mark.asyncio
async def test_reserved_row_requires_current_lease_owner_same_tx(tmp_path: Path) -> None:
    adapter = ch.FakeAdapter([ch.plain_answer("never")])
    env, invoker = await _invoker(tmp_path, adapter, owner_id="owner-stale")
    with pytest.raises(ForegroundQueueError) as denied:
        await _invoke(env, invoker)
    assert denied.value.code == "foreground_generation_stale"
    assert ch.attempts(env.db_path) == [] and len(adapter.calls) == 0
    # CLOSURE 边界与 TOOL 同一允许集：RUNNING 下放行；终态后拒绝。
    ok_env, ok_invoker = await _invoker(tmp_path / "ok", ch.FakeAdapter([ch.plain_answer("ok")]))
    await ok_env.store.authorize_effect(
        host_run_id=ok_env.admission.host_run_id, sdk_run_id=ok_env.run_id, owner_id=ok_env.admission.owner_id,
        generation=ok_env.admission.generation, boundary=EffectBoundary.CLOSURE,
    )
    assert (await _invoke(ok_env, ok_invoker)).status == "succeeded"


@pytest.mark.asyncio
async def test_lease_lost_after_response_blocks_apply(tmp_path: Path) -> None:
    gate = asyncio.Event()

    async def slow(request):  # type: ignore[no-untyped-def]
        await gate.wait()
        return ch.plain_answer("late")

    adapter = ch.FakeAdapter([slow])
    env, invoker = await _invoker(tmp_path, adapter)
    task = asyncio.create_task(_invoke(env, invoker))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if [(o, s) for o, s, *_ in ch.attempts(env.db_path)] == [(1, "handed_off")]:
            break
    env.clock.now += 1000
    await env.store.reclaim_expired(
        host_run_id=env.admission.host_run_id, new_owner_id="owner-2",
        expected_generation=env.admission.generation, lease_seconds=10, idempotency_key="reclaim-2",
    )
    gate.set()
    outcome = await task
    assert outcome.status == "lease_lost" and outcome.response is None and outcome.provider_calls == 1
    # 结果已收到：行 settle 为 succeeded（Provider 侧事实），但 apply 被拒；新 owner 见 succeeded 无 receipt。
    assert [(o, s) for o, s, *_ in ch.attempts(env.db_path)] == [(1, "succeeded")]


@pytest.mark.asyncio
async def test_binding_unrebuildable_is_not_sent_without_provider_call(tmp_path: Path) -> None:
    adapter = ch.FakeAdapter([])
    env, invoker = await _invoker(tmp_path, adapter)

    def broken(record):  # type: ignore[no-untyped-def]
        raise RuntimeError("SDK bound Provider incarnation changed")

    invoker.adapter_factory = broken
    outcome = await _invoke(env, invoker)
    assert (outcome.status, outcome.unknown_class) == ("failed", "not_sent")
    assert outcome.reason_code.startswith("binding_unrebuildable:")
    assert [(o, s, u) for o, s, u, *_ in ch.attempts(env.db_path)] == [(1, "failed", "not_sent")]
    assert len(adapter.calls) == 0
    # 无 binding 记录：连行都不插（NOT NULL provider/model），零调用。
    none = await _invoke(env, invoker, binding={})
    assert none.status == "failed" and none.reason_code == "binding_unrebuildable:missing" and none.attempt_id is None


# ---- S5b Task 6：Task 3 审查 F-3 / F-4 / F-6 ---------------------------------------


@pytest.mark.asyncio
async def test_invoker_not_sent_classification_with_real_adapter_error(tmp_path: Path) -> None:
    """F-3：真实 SDK ``OpenAICompatibleProvider`` 指向关闭端口 → ``ProviderTransportError(private_cause=…) from None``
    （``__cause__`` 被切断，redactor 把私有原因变成 ``RuntimeError(str(ConnectError))``，类型也被抹掉）→ 仍按
    连接阶段失败判定 ``not_sent``（可 attempt+1），不再落成死路径 ``sent_unknown``。"""

    from simple_harness.providers import OpenAICompatibleProvider
    from simple_harness.providers.errors import ProviderTransportError

    try:
        from simple_harness.providers import Secret
    except ImportError:  # pragma: no cover - SDK layout guard
        from simple_harness.providers.secrets import Secret  # type: ignore[no-redef]

    client = httpx.AsyncClient()
    provider = OpenAICompatibleProvider(client, "http://127.0.0.1:1", "model-x", Secret("not-a-real-key"), timeout=2.0)
    try:
        # 先证明真实 adapter 的异常形状：__cause__ 为 None、私有原因是 httpx 连接错误。
        from simple_harness import RequestId as _RequestId
        from simple_harness.providers import CancelToken

        with pytest.raises(ProviderTransportError) as transport:
            await provider.invoke(_request(SimpleNamespace(attempt_ordinal=0)), cancel=CancelToken())
        assert transport.value.__cause__ is None
        private = getattr(transport.value, "_private_cause", None)
        assert isinstance(private, RuntimeError) and not isinstance(private, httpx.ConnectError)
        assert "connection" in str(private).lower()
        env, invoker = await _invoker(tmp_path, provider)
        first = await _invoke(env, invoker)
        assert (first.status, first.unknown_class, first.reason_code) == ("failed", "not_sent", "provider_not_sent:ConnectError")
        assert [(o, s, u) for o, s, u, *_ in ch.attempts(env.db_path)] == [(1, "failed", "not_sent")]
        # not_sent → 下一次 attempt+1（同 request_hash）。
        second = await _invoke(env, invoker)
        assert (second.status, second.unknown_class, second.attempt_ordinal) == ("failed", "not_sent", 2)
        assert _RequestId("x").value == "x"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_invoker_handoff_requires_rowcount_one(tmp_path: Path) -> None:
    """F-4：reserved 行在 handoff 前已被新 owner reconcile（reserved→failed(not_sent)）→ 旧 owner 的
    ``reserved→handed_off`` UPDATE 影响 0 行 → ``lease_lost``、**零 Provider 调用**（不产生账本外调用）。"""

    import sqlite3 as _sqlite3

    state: dict[str, Path] = {}

    def reconciled_by_new_owner(point: str) -> None:
        if point == "attempt-reserved":
            with _sqlite3.connect(state["db"]) as db:
                db.execute(
                    "UPDATE post_turn_invocation_attempts SET status='failed',unknown_class='not_sent',"
                    "reason_code='reserved_abandoned',settled_at=1.0 WHERE status='reserved'"
                )
                db.commit()

    adapter = ch.FakeAdapter([ch.plain_answer("must not be called")])
    env, invoker = await _invoker(tmp_path, adapter, fault=reconciled_by_new_owner)
    state["db"] = env.db_path
    outcome = await _invoke(env, invoker)
    assert outcome.status == "lease_lost" and outcome.reason_code == "attempt_reserved_row_lost"
    assert outcome.provider_calls == 0 and len(adapter.calls) == 0
    assert [(o, s, u) for o, s, u, *_ in ch.attempts(env.db_path)] == [(1, "failed", "not_sent")]


@pytest.mark.asyncio
async def test_invoker_propagates_cancelled_error(tmp_path: Path) -> None:
    """F-6：任务取消（asyncio.CancelledError）→ 账本 settle ``unknown(sent_unknown)`` 后 **重新抛出**（协作取消
    不被吞掉，调用方不得继续 durable 写入）；后续同 request 0 调用。"""

    adapter = ch.FakeAdapter([asyncio.CancelledError(), ch.plain_answer("never")])
    env, invoker = await _invoker(tmp_path, adapter)
    with pytest.raises(asyncio.CancelledError):
        await _invoke(env, invoker)
    assert [(o, s, u) for o, s, u, *_ in ch.attempts(env.db_path)] == [(1, "unknown", "sent_unknown")]
    blocked = await _invoke(env, invoker)
    assert blocked.status == "blocked" and blocked.provider_calls == 0 and len(adapter.calls) == 1
