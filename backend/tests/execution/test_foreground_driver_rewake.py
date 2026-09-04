"""驱动的唤醒不得丢失。

实测故障（.local-test-evidence/real-ui-channel/20260904T120431）：
模型全程做对了——README 真被改、语义收口 `outcome=mutate`、10 个 effect 全部结算、
SDK Run `completed`——但宿主侧回合永停 `CLAIMED`、`foreground_terminal_receipts`
为空、Memory 摄入永不发生。

根因是唤醒丢失：`after_control` 唤醒时若驱动**仍在运行**，`after_enqueue` 只看
`driver.done()`，判假就什么都不做；而驱动可能正处在本轮 `_drive_once` 的末尾、
马上要因「无进展」退出。退出后没有任何东西再去观察 SDK 终态。
通过的轮次 `foreground.runtime.bound` 出现 2 次（驱动被重新进入），
挂住的只有 1 次——差别就在这里。

同一条事件序列（created → activated → decision.open → decision.allowed →
completed）既能通过也能挂住，说明是竞态而非顺序问题。
"""

from __future__ import annotations

import asyncio

import pytest

# 唤醒丢失的症状就是「永远等不到下一轮」。用例必须快速失败而不是挂死，
# 否则 CI 上只会看到超时，看不出是哪条契约破了。
_WAIT = 2.0


async def _expect(event: asyncio.Event, what: str) -> None:
    try:
        await asyncio.wait_for(event.wait(), timeout=_WAIT)
    except TimeoutError:  # pragma: no cover - 只在契约被破坏时走到
        raise AssertionError(f"{what}：等了 {_WAIT}s 没等到——唤醒被丢弃了") from None


class _Runtime:
    """只保留唤醒/退出这一段控制流的最小复刻。

    不引真实 store：本用例要钉的是「驱动在退出前有没有复查唤醒」，
    与队列语义无关。
    """

    def __init__(self, drive_results: list[bool]) -> None:
        self._driver: asyncio.Task[None] | None = None
        self._driver_lock = asyncio.Lock()
        self._rewake_pending = False
        self._closed = False
        self._results = list(drive_results)
        self.drive_calls = 0
        self.entered = 0
        self._in_drive = asyncio.Event()
        self._release_drive = asyncio.Event()

    async def _drive_once(self) -> bool:
        self.drive_calls += 1
        self._in_drive.set()
        await self._release_drive.wait()      # 让用例精确控制唤醒时机
        self._release_drive.clear()
        return self._results.pop(0) if self._results else False

    async def _run_driver(self) -> None:
        self.entered += 1
        while not self._closed:
            progressed = await self._drive_once()
            if progressed:
                continue
            async with self._driver_lock:
                if self._rewake_pending:
                    self._rewake_pending = False
                    continue
                self._driver = None
                return

    async def after_enqueue(self) -> None:
        async with self._driver_lock:
            if self._driver is None or self._driver.done():
                self._rewake_pending = False
                self._driver = asyncio.create_task(self._run_driver())
            else:
                self._rewake_pending = True


@pytest.mark.asyncio
async def test_wake_during_a_drive_pass_is_not_lost() -> None:
    """唤醒发生在 _drive_once 执行期间——最容易丢的时刻。"""
    rt = _Runtime([False, False])
    await rt.after_enqueue()
    await _expect(rt._in_drive, "驱动首次进入")
    rt._in_drive.clear()

    await rt.after_enqueue()                  # ← 唤醒到达：驱动仍在跑
    assert rt._rewake_pending is True, "驱动在跑时的唤醒必须留痕"

    rt._release_drive.set()                   # 本轮判「无进展」
    await _expect(rt._in_drive, "驱动应因留痕的唤醒重新跑一轮")
    assert rt.drive_calls == 2, "唤醒被丢弃了：驱动没有重新进入"

    rt._release_drive.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_driver_clears_its_reference_so_next_wake_always_starts_one() -> None:
    """退出时置空 _driver：消除「标记设上但 done() 尚为假」的残余窗口。"""
    rt = _Runtime([False])
    await rt.after_enqueue()
    await _expect(rt._in_drive, "驱动进入")
    rt._release_drive.set()
    await asyncio.sleep(0.05)

    assert rt._driver is None, "驱动退出后必须置空引用，否则下次唤醒可能两头落空"

    rt._in_drive.clear()
    await rt.after_enqueue()                  # 下一次唤醒必定新建
    await _expect(rt._in_drive, "置空引用后下一次唤醒应新建驱动")
    assert rt.entered == 2
    rt._release_drive.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_no_wake_means_the_driver_simply_exits() -> None:
    """没有唤醒就该干净退出——不能变成空转。"""
    rt = _Runtime([False])
    await rt.after_enqueue()
    await _expect(rt._in_drive, "驱动进入")
    rt._release_drive.set()
    await asyncio.sleep(0.05)

    assert rt.drive_calls == 1
    assert rt.entered == 1
