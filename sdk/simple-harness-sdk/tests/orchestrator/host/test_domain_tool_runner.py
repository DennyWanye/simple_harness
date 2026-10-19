# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""领域工具执行器的语义（2026-10-20 裁决）：同步/异步都能跑；超时和被取消记"不确定"，锁等物理
调用真正结束才放；异常记"失败"且不回显文字；结果必须是 JSON 对象；名字与 schema 在注册时校验。"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from agent_orchestrator.runtime import domain_tool_runner as runner
from agent_orchestrator.runtime.domain_tools import DomainTool, ToolCallContext

CONTEXT = ToolCallContext(mission_id="m", attempt_id="a", call_id="run:c1")
SCHEMA = {
    "type": "object",
    "description": "测试用",
    "properties": {},
    "additionalProperties": False,
}


def _tool(invoke, *, timeout=60.0, name="probe"):
    return DomainTool(name=name, schema=SCHEMA, invoke=invoke, timeout_seconds=timeout)


def _run(tool, lock=None):
    lock = lock or asyncio.Lock()
    return asyncio.run(runner.run_domain_tool(tool, {}, CONTEXT, lock=lock)), lock


def test_sync_and_async_tools_both_return_their_result_and_release_the_lock() -> None:
    def sync(arguments, context):
        return {"kind": "sync", "mission": context.mission_id}

    async def coro(arguments, context):
        await asyncio.sleep(0)
        return {"kind": "async", "call": context.call_id}

    outcome, lock = _run(_tool(sync))
    assert outcome.status == "succeeded" and outcome.value == {"kind": "sync", "mission": "m"}
    assert not lock.locked()
    outcome, lock = _run(_tool(coro))
    assert outcome.status == "succeeded" and outcome.value == {"kind": "async", "call": "run:c1"}
    assert not lock.locked()


def test_an_exception_is_a_failure_without_its_text() -> None:
    def boom(arguments, context):
        raise KeyError("secret-token-123")

    outcome, lock = _run(_tool(boom))
    assert outcome.status == "failed" and outcome.error_code == "tool_failed"
    assert outcome.error_type == "KeyError"
    assert "secret-token" not in outcome.public_message
    assert not lock.locked()


def test_a_non_object_result_is_refused() -> None:
    outcome, _ = _run(_tool(lambda a, c: ["not", "an", "object"]))
    assert outcome.status == "failed" and outcome.error_code == "tool_result_invalid"

    async def returns_coroutine_object(a, c):
        return asyncio.sleep(0)  # 把协程当结果交出来

    outcome, _ = _run(_tool(returns_coroutine_object))
    assert outcome.status == "failed" and outcome.error_code == "tool_result_invalid"


def test_a_slow_sync_tool_times_out_as_unknown_and_the_lock_waits_for_the_thread(
    monkeypatch,
) -> None:
    monkeypatch.setattr(runner, "CANCEL_GRACE_SECONDS", 0.05)
    finished = threading.Event()

    def slow(arguments, context):
        time.sleep(0.4)
        finished.set()
        return {"late": True}

    async def case():
        lock = asyncio.Lock()
        outcome = await runner.run_domain_tool(_tool(slow, timeout=0.05), {}, CONTEXT, lock=lock)
        assert outcome.status == "unknown" and outcome.error_code == "tool_timeout"
        assert outcome.still_running is True
        assert lock.locked()  # 线程还在跑，锁还在
        for _ in range(100):
            if not lock.locked():
                break
            await asyncio.sleep(0.02)
        assert finished.is_set() and not lock.locked()

    asyncio.run(case())


def test_a_slow_async_tool_is_cancelled_on_timeout(monkeypatch) -> None:
    monkeypatch.setattr(runner, "CANCEL_GRACE_SECONDS", 0.5)
    seen: list[str] = []

    async def slow(arguments, context):
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            seen.append("cancelled")
            raise
        return {}

    async def case():
        lock = asyncio.Lock()
        outcome = await runner.run_domain_tool(_tool(slow, timeout=0.05), {}, CONTEXT, lock=lock)
        assert outcome.status == "unknown" and outcome.error_code == "tool_timeout"
        assert seen == ["cancelled"] and not lock.locked() and outcome.still_running is False

    asyncio.run(case())


def test_a_coroutine_that_swallows_cancellation_hands_the_lock_to_the_background(
    monkeypatch,
) -> None:
    monkeypatch.setattr(runner, "CANCEL_GRACE_SECONDS", 0.05)
    released = asyncio.Event

    async def stubborn(arguments, context):
        while True:
            try:
                await asyncio.sleep(0.1)
                return {"done": True}
            except asyncio.CancelledError:
                continue  # 吞掉取消，再跑一轮才退出

    async def case():
        lock = asyncio.Lock()
        outcome = await runner.run_domain_tool(
            _tool(stubborn, timeout=0.02), {}, CONTEXT, lock=lock
        )
        assert outcome.status == "unknown" and outcome.still_running is True
        assert lock.locked()
        for _ in range(100):
            if not lock.locked():
                break
            await asyncio.sleep(0.02)
        assert not lock.locked()

    del released
    asyncio.run(case())


def test_external_cancellation_keeps_the_outcome_unknown_and_frees_the_lock(monkeypatch) -> None:
    monkeypatch.setattr(runner, "CANCEL_GRACE_SECONDS", 0.5)

    async def slow(arguments, context):
        await asyncio.sleep(10)
        return {}

    async def case():
        lock = asyncio.Lock()
        task = asyncio.create_task(runner.run_domain_tool(_tool(slow), {}, CONTEXT, lock=lock))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not lock.locked()

    asyncio.run(case())


def test_calls_of_one_mission_run_one_at_a_time() -> None:
    running = {"now": 0, "peak": 0}

    async def busy(arguments, context):
        running["now"] += 1
        running["peak"] = max(running["peak"], running["now"])
        await asyncio.sleep(0.02)
        running["now"] -= 1
        return {}

    async def case():
        lock = asyncio.Lock()
        tool = _tool(busy)
        await asyncio.gather(
            *(runner.run_domain_tool(tool, {}, CONTEXT, lock=lock) for _ in range(4))
        )
        assert running["peak"] == 1

    asyncio.run(case())


@pytest.mark.parametrize(
    ("name", "schema", "timeout", "message"),
    [
        ("Bad-Name", SCHEMA, 60, "tool name"),
        ("workspace_x", {"type": "object"}, 60, "description"),
        ("ok_name", {"type": "array", "description": "x"}, 60, "JSON Schema object"),
        ("ok_name", SCHEMA, 0, "timeout_seconds"),
    ],
)
def test_registration_validates_name_schema_and_timeout(name, schema, timeout, message) -> None:
    with pytest.raises(ValueError, match=message):
        DomainTool(name=name, schema=schema, invoke=lambda a, c: {}, timeout_seconds=timeout)


@pytest.mark.parametrize(
    "value",
    [{"items": (1, 2)}, {"x": float("nan")}, {1: "int key"}],
    ids=["tuple", "nan", "int-key"],
)
def test_results_the_event_log_cannot_store_are_refused(value) -> None:
    """与事件、账本同一套 JSON 规则：执行器说成功的，事件一定写得进去。"""

    outcome, lock = _run(_tool(lambda a, c: value))
    assert outcome.status == "failed" and outcome.error_code == "tool_result_invalid"
    assert not lock.locked()


def test_a_call_waiting_behind_a_stuck_thread_is_busy_not_hung(monkeypatch) -> None:
    """上一次超时的线程还占着锁：这次在 timeout_seconds 内判 tool_busy、不发起调用；
    线程结束后再调成功。"""

    monkeypatch.setattr(runner, "CANCEL_GRACE_SECONDS", 0.05)
    release = threading.Event()
    calls: list[int] = []

    def stuck_once(arguments, context):
        calls.append(1)
        if len(calls) == 1:
            release.wait(5)
        return {"n": len(calls)}

    async def case():
        lock = asyncio.Lock()
        tool = _tool(stuck_once, timeout=0.1)
        first = await runner.run_domain_tool(tool, {}, CONTEXT, lock=lock)
        assert first.status == "unknown" and first.error_code == "tool_timeout"
        started = time.monotonic()
        second = await runner.run_domain_tool(tool, {}, CONTEXT, lock=lock)
        assert second.status == "failed" and second.error_code == "tool_busy"
        assert time.monotonic() - started < 1.0 and len(calls) == 1
        release.set()
        for _ in range(100):
            if not lock.locked():
                break
            await asyncio.sleep(0.02)
        third = await runner.run_domain_tool(tool, {}, CONTEXT, lock=lock)
        assert third.status == "succeeded" and third.value == {"n": 2}

    asyncio.run(case())


def test_a_call_cancelled_while_waiting_for_the_lock_never_starts() -> None:
    starts: list[str] = []

    async def case():
        lock = asyncio.Lock()
        await lock.acquire()  # 别人占着
        task = asyncio.create_task(
            runner.run_domain_tool(
                _tool(lambda a, c: {}), {}, CONTEXT, lock=lock, on_start=lambda: starts.append("s")
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert starts == [] and lock.locked()  # 锁还是原主人的
        lock.release()

    asyncio.run(case())
