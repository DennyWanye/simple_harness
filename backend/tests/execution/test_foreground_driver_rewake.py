"""驱动的唤醒不得丢失 —— 直接驱动生产类 ``ForegroundRuntimeExecutionAuthority``。

实测故障（.local-test-evidence/real-ui-channel/20260904T120431）：模型全程做对了
——README 真被改、语义收口 ``outcome=mutate``、10 个 effect 全部结算、SDK Run
``completed``——但宿主侧回合永停 ``CLAIMED``、``foreground_terminal_receipts`` 为空、
Memory 摄入永不发生；``run.terminal`` 之后再等 5 分钟仍无提交。

根因：``_run_driver`` 「无进展即 return」，而 ``after_control`` 唤醒时若驱动**仍在
运行**，``after_enqueue`` 只看 ``driver.done()``，判假就什么都不做——它设的
``_control_wake`` 由 ``_pump_controls`` 消费，与「重新进入驱动」无关。决策在本轮
``_drive_once`` 期间落地即丢失唤醒。诊断依据：通过的轮次 ``foreground.runtime.bound``
出现 2 次，挂住的只有 1 次，而两者 SDK 事件序列完全相同。

**本文件的前身是同义反复**：它把修好的控制流抄进测试自建的类里，删掉生产代码后
测试照样全绿（独立审计实测）。现已改为构造真实对象、只把 ``_drive_once`` 换成
可控桩——被测的是生产的 ``_run_driver`` / ``after_enqueue`` / ``close`` 本身。
"""

from __future__ import annotations

import asyncio

import pytest

from deskpet.execution.foreground_runtime import ForegroundRuntimeExecutionAuthority

# 唤醒丢失的症状是「永远等不到下一轮」。用例必须快速失败而不是挂死。
_WAIT = 2.0


async def _expect(event: asyncio.Event, what: str) -> None:
    try:
        await asyncio.wait_for(event.wait(), timeout=_WAIT)
    except TimeoutError:  # pragma: no cover - 仅在契约被破坏时走到
        raise AssertionError(f"{what}：等了 {_WAIT}s 没等到 —— 唤醒被丢弃了") from None


class _Store:
    """close() 会读快照；返回 None 表示「无活跃 Run」，据此走完清理分支。"""

    async def current_snapshot(self, _subject):
        return None


def _authority(drive_results: list[bool]) -> ForegroundRuntimeExecutionAuthority:
    """真实对象 + 受控的 _drive_once。

    构造依赖全部传 object()：本用例只走 _run_driver / after_enqueue / close 的
    控制流，不进入任何端口。
    """
    rt = ForegroundRuntimeExecutionAuthority(
        store=_Store(),
        subject="s",
        owner_id="o",
        ingress=object(),
        context=object(),
        provider=object(),
        tools=object(),
        terminal_observer=object(),
    )
    rt.results = list(drive_results)
    rt.drive_calls = 0
    rt.entered = 0
    rt.in_drive = asyncio.Event()
    rt.release = asyncio.Event()

    original = rt._run_driver

    async def counting_driver() -> None:
        rt.entered += 1
        await original()

    async def drive_once() -> bool:
        rt.drive_calls += 1
        rt.in_drive.set()
        await rt.release.wait()          # 用例精确控制唤醒时机
        rt.release.clear()
        return rt.results.pop(0) if rt.results else False

    rt._run_driver = counting_driver     # type: ignore[method-assign]
    rt._drive_once = drive_once          # type: ignore[method-assign]
    return rt


@pytest.mark.asyncio
async def test_wake_arriving_during_a_drive_pass_is_not_lost() -> None:
    """唤醒发生在 _drive_once 执行期间 —— 最容易丢的时刻。"""
    rt = _authority([False, False])
    await rt.after_enqueue(subject="s")
    await _expect(rt.in_drive, "驱动首次进入")
    rt.in_drive.clear()

    await rt.after_control(subject="s")           # ← 唤醒到达：驱动仍在跑
    rt.release.set()                              # 本轮判「无进展」

    await _expect(rt.in_drive, "驱动应因留痕的唤醒重新跑一轮")
    assert rt.drive_calls == 2, "唤醒被丢弃了：驱动没有重新进入"

    rt.release.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_driver_clears_its_reference_so_the_next_wake_always_starts_one() -> None:
    """退出时置空 _driver：消除「标记设上但 done() 尚为假」的残余窗口。"""
    rt = _authority([False])
    await rt.after_enqueue(subject="s")
    await _expect(rt.in_drive, "驱动进入")
    rt.release.set()
    await asyncio.sleep(0.05)

    assert rt._driver is None, "驱动退出后必须置空引用，否则下次唤醒可能两头落空"

    rt.in_drive.clear()
    await rt.after_enqueue(subject="s")
    await _expect(rt.in_drive, "置空引用后下一次唤醒应新建驱动")
    assert rt.entered == 2
    rt.release.set()
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_no_wake_means_the_driver_simply_exits() -> None:
    """没有唤醒就该干净退出 —— 不能变成空转。"""
    rt = _authority([False])
    await rt.after_enqueue(subject="s")
    await _expect(rt.in_drive, "驱动进入")
    rt.release.set()
    await asyncio.sleep(0.05)

    assert rt.drive_calls == 1
    assert rt.entered == 1


@pytest.mark.asyncio
async def test_close_still_reaches_lease_cleanup_after_the_driver_exited() -> None:
    """置空 _driver 不得让 close() 跳过租约清理。

    修 P0-15 时差点引入的回归：close() 原本 `if task is None: return`，
    而驱动正常退出后正是 None —— 那样租约永不关闭。
    """
    rt = _authority([False])
    await rt.after_enqueue(subject="s")
    await _expect(rt.in_drive, "驱动进入")
    rt.release.set()
    await asyncio.sleep(0.05)
    assert rt._driver is None

    seen: list[str] = []

    async def snapshot(_subject):
        seen.append("snapshot")
        return None

    rt._store.current_snapshot = snapshot          # type: ignore[method-assign]
    await rt.close(timeout=1.0)
    assert seen == ["snapshot"], "驱动跑完后 close() 必须继续走租约清理，而不是提前返回"


@pytest.mark.asyncio
async def test_close_without_any_driver_skips_cleanup() -> None:
    """从未起过驱动 → 无租约可清，照旧提前返回（不得因上一条而误伤）。"""
    rt = _authority([])
    seen: list[str] = []

    async def snapshot(_subject):
        seen.append("snapshot")
        return None

    rt._store.current_snapshot = snapshot          # type: ignore[method-assign]
    await rt.close(timeout=1.0)
    assert seen == [], "从未起过驱动时不该去读快照"
