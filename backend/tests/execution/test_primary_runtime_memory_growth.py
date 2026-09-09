"""事件 X 回归：长生命周期前台 runtime 的每回合保留内存必须有界。

原生旅程里后端进程每个 provider 回合稳定涨 ~250MB（2026-09-09 Manual run3：
0.94→2.86 GB / 7 回合），`vmmap` 显示 MALLOC_SMALL 2.1 GB —— 是 Python 对象堆
积，不是模型权重。本用例不启动原生 app、不连真 provider：用与生产同一套装配
（`tests.execution.test_primary_foreground_runtime.build` 的 dynamic 装配，真实
context_route / tool_search / context_page_in 适配器）把**同一个** runtime + SDK
stack 连驱 N 个回合，每回合发若干工具调用，用 tracemalloc 量每回合的净保留增长。

在修复前，冻结 SDK 的 `ToolRegistry._calls` 每次工具调用都记一条、只有
`allow_confirmed_not_started` 才删，而产品侧 registry 是每个 runtime stack 一份的
长生命周期对象 —— 于是每条 `_CallRecord` 连同已完成的 asyncio.Task、拷贝的
contextvars Context 和整份 ToolResult 载荷被永久保留。
"""
import asyncio
import gc
import time
import json
import tracemalloc

import pytest

from deskpet.memory.human_memory_service import (
    HumanMemoryHostServiceFactory,
    QueueTurnRequest,
)
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.tools.context_page_in_tools import ContextPageInStore

# 一次工具结果的载荷量级。原生旅程里 tool_search / 分页结果 / 文件读取都在这个
# 量级甚至更大；这里只要足够让"每回合泄漏一份"在 tracemalloc 上显著。
_FILLER_BYTES = 128 * 1024
# 分页内容保持小：ContextPageInStore 是有界缓存（512 条 / 300s TTL），本用例
# 的窗口内不会淘汰，不该被算进"泄漏"预算。
_PAGE_BODY = "EXACT_PAGE_BODY " * 128

TURNS = 12
WARMUP_TURNS = 3
# 修复后每回合净保留只剩有界缓存的一条分页引用（实测 ~9 KiB/回合）。阈值取在
# 修复前后的中间：泄漏时每回合至少保留一份 _FILLER_BYTES 载荷（实测 ~150 KiB/回合）。
MAX_RETAINED_BYTES_PER_TURN = 48 * 1024


# 测量要排除的**测试脚手架**噪声，而不是产品分配点：`tests/conftest.py` 的
# autouse 夹具会把每个 aiosqlite 连接（连同它的工作线程）钉到用例结束，所以
# 「每回合 2 个已关闭的连接」会被算成增长。独立进程复现（无 conftest）里这些
# 分配点是负增长，说明产品自己按时关闭了它们。泄漏本身分配在
# `json/decoder.py`、`simple_harness/contracts/json.py`、
# `simple_harness/tools/registry.py`，一个都不在下面的排除表里。
_HARNESS_NOISE = (
    tracemalloc.Filter(False, "*/aiosqlite/*"),
    tracemalloc.Filter(False, "*/threading.py"),
    tracemalloc.Filter(False, "*/_weakrefset.py"),
    tracemalloc.Filter(False, "*/tests/conftest.py"),
    # 基线快照对象本身在两次快照之间分配，是一次性测量开销，不是每回合增长。
    tracemalloc.Filter(False, tracemalloc.__file__),
)


def _snapshot() -> tracemalloc.Snapshot:
    return tracemalloc.take_snapshot().filter_traces(_HARNESS_NOISE)


def _driver_idle(runtime) -> bool:
    driver = runtime._driver
    return driver is None or driver.done()


@pytest.mark.asyncio
async def test_multi_turn_foreground_runtime_retains_bounded_memory(tmp_path, monkeypatch):
    from simple_harness import CallId
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
    from deskpet.sdk_adapters.tool_authority import SdkRuntimeCapabilityBridgeAdapter
    import tests.execution.test_primary_foreground_runtime as fixture

    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    await service.open_primary()
    subject = local_owner_auth().subject
    pages = ContextPageInStore()

    original_search = SdkRuntimeCapabilityBridgeAdapter.search

    def search(self, query):
        """真实能力检索，外加一份 Host 发布的大结果 + 分页引用。"""
        result = dict(original_search(self, query))
        context = self._context_getter()
        reference = pages.put(kind="tool_result", source="regression-large-result",
                              content=_PAGE_BODY, session_id=context.session_id,
                              request_id=context.request_id, scope_id=context.scope_id)
        # 每次都造一份新字符串：复用同一个常量对象的话，被保留的 ToolResult 只
        # 是多一个引用，tracemalloc 量不到任何增长。
        result["filler"] = f"{len(result)}-" + "R" * _FILLER_BYTES
        result["page"] = {"reference_id": reference.reference_id,
                          "source_hash": reference.source_hash}
        return result

    monkeypatch.setattr(SdkRuntimeCapabilityBridgeAdapter, "search", search)

    class TurnProvider(fixture.Provider):
        """每回合：context_route → tool_search → context_page_in → 结束。"""

        def __init__(self):
            super().__init__()
            self.calls = 0

        async def invoke(self, request, *, cancel):
            # 生产 provider 不留存请求；这里也不留，否则夹具自己就是最大的"泄漏"。
            self.calls += 1
            results = [json.loads(m.content) for m in request.messages
                       if m.role.value == "tool" and isinstance(m.content, str)]
            # 步进由本 Run 已有的工具结果条数推出，而不是自增计数器：驱动偶尔会
            # 重试一个回合（`foreground_run_already_terminal`），计数器会错位。
            step = len(results)
            if step == 0:
                name, arguments = "context_route", {"route": "direct_standalone"}
            elif step == 1:
                name, arguments = "tool_search", {"query": "write_file"}
            elif step == 2:
                page = results[-1]["value"]["page"]
                name, arguments = "context_page_in", {
                    "reference_id": page["reference_id"],
                    "source_hash": page["source_hash"]}
            else:
                return ProviderResponse(
                    request.request_id,
                    Message(MessageRole.ASSISTANT, f"Turn finished {self.calls}"),
                    model="model", usage=ProviderUsage(10, 10, 20))
            return ProviderResponse(
                request.request_id, Message(MessageRole.ASSISTANT, "Executing " + name),
                tool_calls=(ProviderToolCall(CallId(f"call-{self.calls}"), name, arguments),),
                model="model", usage=ProviderUsage(10, 10, 20))

    provider = TurnProvider()
    runtime, stack, queue = await fixture.build(tmp_path, state, provider,
                                                dynamic=True, page_in_store=pages)
    registry = runtime.built_ports["ports"].tools._registry
    tool_calls_per_turn = []
    baseline = None
    growth = None
    top_sites: list[str] = []
    tracemalloc.start(6)
    try:
        for turn in range(TURNS):
            await service.enqueue_turn(
                QueueTurnRequest(None, f"turn-{turn}", f"Offline actual user turn {turn}"))
            # 与生产一致：唤醒常驻驱动，而不是从测试里再并发驱一次——后者会和
            # 租约守护自己的 after_enqueue 抢 _stop_lease_keeper。
            await runtime.after_enqueue(subject=subject)
            deadline = time.monotonic() + 120
            # 只观察驱动自身是否静默：并发读队列快照会撞上运行中的状态迁移
            # （foreground_snapshot_not_active）。
            while provider.calls < 4 * (turn + 1) or not _driver_idle(runtime):
                assert time.monotonic() < deadline, (
                    f"turn {turn} 未收敛：provider_calls={provider.calls} "
                    f"last_error={runtime.last_error!r}")
                await asyncio.sleep(0.02)
            gc.collect()
            # SDK 的本地 Tool 认领必须随 Run 终态归还，而不是随进程一路攒着。
            tool_calls_per_turn.append(len(registry.calls))
            if turn == WARMUP_TURNS:
                baseline = _snapshot()
            elif turn == TURNS - 1:
                stats = _snapshot().compare_to(baseline, "lineno")
                growth = sum(stat.size_diff for stat in stats)
                top_sites = [f"{stat.size_diff / 1024:+.1f} KiB {stat.traceback[0]}"
                             for stat in stats[:8]]
    finally:
        tracemalloc.stop()
        await runtime.close()
        await stack.close()

    # 用 >=：驱动偶尔重试一个回合，会多出 provider 调用；这里要证明的是每个回合
    # 都真的跑完了，不是调用数刚好相等。
    assert provider.calls >= 4 * TURNS
    measured = TURNS - 1 - WARMUP_TURNS
    per_turn = growth / measured
    print(f"[事件X回归] 每回合净保留 {per_turn / 1024:.1f} KiB；工具认领数 {tool_calls_per_turn}")
    assert per_turn < MAX_RETAINED_BYTES_PER_TURN, (
        f"每回合净保留 {per_turn / 1024:.0f} KiB（{measured} 回合共 "
        f"{growth / 1048576:.1f} MiB）；每回合工具认领数 {tool_calls_per_turn}；"
        "增长最大的分配点：\n  " + "\n  ".join(top_sites)
    )
    # 结构性断言：认领数不随回合线性增长（泄漏时是 3/回合，单调递增）。
    assert max(tool_calls_per_turn) <= 4, tool_calls_per_turn
