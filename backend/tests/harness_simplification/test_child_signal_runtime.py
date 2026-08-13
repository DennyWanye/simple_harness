import asyncio
import logging
from types import SimpleNamespace

import pytest

from deskpet.harness.child_signal_runtime import ChildSignalRuntime


@pytest.mark.asyncio
async def test_failed_background_owner_is_logged_and_wakes_recovery(caplog) -> None:
    wakeups: list[str] = []
    runtime = ChildSignalRuntime(
        live=SimpleNamespace(lock=asyncio.Lock()),
        runtime=object(),
        continuations=object(),
        heartbeat_interval=0.01,
        reconcile_trigger=lambda: wakeups.append("wake"),
    )

    async def fail_after_durable_commit() -> None:
        raise RuntimeError("injected post-commit delivery failure")

    task = asyncio.create_task(
        fail_after_durable_commit(),
        name="deskpet-child-signal:test-failure",
    )
    await asyncio.gather(task, return_exceptions=True)

    with caplog.at_level(
        logging.ERROR,
        logger="deskpet.harness.child_signal_runtime",
    ):
        runtime._task_done(task)

    assert wakeups == ["wake"]
    assert "child_signal_owner_failed" in caplog.text
    assert "injected post-commit delivery failure" in caplog.text


@pytest.mark.asyncio
async def test_successful_background_owner_still_wakes_next_reconcile() -> None:
    wakeups: list[str] = []
    runtime = ChildSignalRuntime(
        live=SimpleNamespace(lock=asyncio.Lock()),
        runtime=object(),
        continuations=object(),
        heartbeat_interval=0.01,
        reconcile_trigger=lambda: wakeups.append("wake"),
    )
    task = asyncio.create_task(
        asyncio.sleep(0),
        name="deskpet-child-signal:test-success",
    )
    await task

    runtime._task_done(task)

    assert wakeups == ["wake"]


@pytest.mark.asyncio
async def test_reconcile_wakeup_failure_is_observed_without_leaking(caplog) -> None:
    def fail_wakeup() -> None:
        raise RuntimeError("injected wakeup failure")

    runtime = ChildSignalRuntime(
        live=SimpleNamespace(lock=asyncio.Lock()),
        runtime=object(),
        continuations=object(),
        heartbeat_interval=0.01,
        reconcile_trigger=fail_wakeup,
    )
    task = asyncio.create_task(
        asyncio.sleep(0),
        name="deskpet-child-signal:test-wakeup-failure",
    )
    await task

    with caplog.at_level(
        logging.ERROR,
        logger="deskpet.harness.child_signal_runtime",
    ):
        runtime._task_done(task)

    assert "child_signal_reconcile_wakeup_failed" in caplog.text
    assert "injected wakeup failure" in caplog.text
