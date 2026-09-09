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
from types import SimpleNamespace

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


# --------------------------------------------------------------------------
# 事件 X-2：打开 memory 车道的多回合保留
# --------------------------------------------------------------------------
# 事件 X 的离线复现跑在 ``memory=None`` 上（followup X-F5），所以
# ingestion outbox / analysis 出站箱 / HumanMemoryV7 认知库 / typed recall /
# 短程向量索引 / SDK agent memory 这几条车道**一条都没被覆盖**。本用例把它们
# 全部打开，仍然不启动原生 app、不连真 provider：
#
#   * SDK agent memory = ``MemoryManager.build_development``（context_provider
#     + context_staging + embedding catch-up）；
#   * Host 认知记忆 = 生产的 ``compose_human_memory_runtime``（真实
#     HumanMemoryV7 store、HostMemoryAnalysisExecutor、typed recall、
#     语义纠正与 prospective 车道），analysis provider 是返回**合法 v9 提案**的
#     确定性 adapter；
#   * 每回合终态后驱动生产的 ``MemoryAnalysisLane.tick()`` 直到空闲
#     （ingestion outbox → 短程索引 → analysis job runner → 认知写入）。
#
# 真 WeMM-Embedding-2B（2B 参数）超出本轮进程预算，短程向量车道用同维度
# （2048 / l2）的确定性稠密夹具 embedder 打开——被测的是**每回合保留**，不是
# 向量质量；hash/mock embedder 会被 ``HumanMemoryV7Runtime.build_kwargs`` 按
# 生产口径丢弃，短程车道就整条不跑了。
MEMORY_LANE_TURNS = 12
MEMORY_LANE_WARMUP = 4
# 每回合发布一份"已准备但未被 page-in"的大页引用——这正是生产
# ``_bind_page_in_candidate`` 对每个 trim_policy=page_in 的召回片段做的事：
# 模型最多 page-in 其中一个，其余整份留在进程内的 ContextPageInStore 里。
_LARGE_PAGE_BYTES = 3 * 1024 * 1024
# 修复前 ContextPageInStore 只按条数（512）设界，于是每回合净留一份 3 MiB；
# 修复后按字节预算（8 MiB）淘汰最旧的，常驻量不再随回合线性增长。
MAX_PAGE_STORE_BYTES = 16 * 1024 * 1024
# 主干（页存只按条数设界）每回合净留一份 3 MiB 大页 ≈ 3072 KiB；修复后实测
# 118 KiB/回合（短程向量精确扫描缓存每回合 +1 行 2048 float32 ≈ 8 KiB，
# 其余是夹具自身的 128 KiB 工具结果填充在窗口边界上的抖动）。阈值取在两者中间。
MAX_LANE_RETAINED_BYTES_PER_TURN = 512 * 1024


def _page_store_bytes(store) -> int:
    return sum(len(record.content.encode("utf-8")) for record in store._records.values())


def _fixture_dense_embedder():
    """与 WeMM 同形状（2048 维 / l2 归一）的确定性稠密 embedder。"""
    import hashlib
    import math
    from simple_harness_memory.embedders.base import Embedder, EmbeddingLineage

    class FixtureDenseEmbedder(Embedder):
        @property
        def kind(self):
            return "fixture-dense"

        @property
        def dim(self):
            return 2048

        @property
        def lineage(self):
            return EmbeddingLineage(kind=self.kind, provider="local",
                                    model="fixture-dense-2048", revision="x2",
                                    dimension=self.dim, normalization="l2",
                                    format_fingerprint="fixture-dense:x2:2048")

        async def embed(self, text):
            block = hashlib.sha256(text.encode("utf-8")).digest()
            values: list[float] = []
            while len(values) < self.dim:
                block = hashlib.sha256(block).digest()
                values.extend(byte / 255.0 - 0.5 for byte in block)
            values = values[: self.dim]
            norm = math.sqrt(sum(value * value for value in values)) or 1.0
            return [value / norm for value in values]

    return FixtureDenseEmbedder()


@pytest.mark.asyncio
async def test_multi_turn_memory_lanes_retain_bounded_memory(tmp_path, monkeypatch):
    import sqlite3

    from simple_harness import CallId
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
    from simple_harness_memory import MemoryManager

    from deskpet.memory.human_memory_v7 import local_memory_principal
    from deskpet.memory.memory_ingestion_outbox import (
        MemoryAnalysisLane, MemoryIngestionOutboxWorker, OutboxRunOutcome,
        build_worker_config,
    )
    from deskpet.memory.runtime_composition import compose_human_memory_runtime
    from deskpet.sdk_adapters.tool_authority import SdkRuntimeCapabilityBridgeAdapter
    import tests.execution.test_primary_foreground_runtime as fixture
    from tests.sdk_adapters import s5b_memory_harness as harness

    state = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    await service.open_primary()
    subject = local_owner_auth().subject
    pages = ContextPageInStore()
    turn_state = {"turn": 0, "item": "turn-0", "quote": "", "page": None}

    class AnalysisAdapter:
        """确定性 analysis provider：每回合一条合法 v9 semantic 提案。"""

        target = SimpleNamespace(provider_id="fixture", model="model",
                                 endpoint_identity="e" * 64)

        def __init__(self):
            self.calls = 0

        async def invoke(self, request, *, cancel):
            self.calls += 1
            operations = [harness.semantic_op(
                turn_state["item"], turn_state["quote"],
                operation_id=f"op-{turn_state['turn']}",
                predicate=f"turn_fact_{turn_state['turn']}",
                object_value=f"回合 {turn_state['turn']} 的事实")]
            return harness.proposal_call(operations, outcome="mutate",
                                         raw_id=f"raw-{self.calls}",
                                         provider_request_id=f"prov-{self.calls}")

    adapter = AnalysisAdapter()
    embedder = _fixture_dense_embedder()
    memory_runtime = compose_human_memory_runtime(
        state, tmp_path / "human_memory_v7.db",
        adapter_factory=lambda record: adapter,
        embedder_getter=lambda: embedder,
        principal=local_memory_principal(),
    )
    agent_memory = await MemoryManager.build_development(tmp_path / "agent-memory.db")

    original_search = SdkRuntimeCapabilityBridgeAdapter.search

    def search(self, query):
        """真实能力检索 + 一份 Host 发布的大结果 + 两份页引用。"""
        result = dict(original_search(self, query))
        context = self._context_getter()

        def publish(kind, content):
            return pages.put(kind=kind, source="x2-lane-result", content=content,
                             session_id=context.session_id, request_id=context.request_id,
                             scope_id=context.scope_id)

        # 每回合都新建字符串：复用同一个常量对象的话，被保留的页只是多一个引用，
        # tracemalloc 量不到任何增长。
        publish("memory_l3", f"{turn_state['turn']}-" + "L" * _LARGE_PAGE_BYTES)
        reference = publish("tool_result", _PAGE_BODY + str(turn_state["turn"]))
        result["filler"] = f"{len(result)}-" + "R" * _FILLER_BYTES
        result["page"] = {"reference_id": reference.reference_id,
                          "source_hash": reference.source_hash}
        turn_state["page"] = dict(result["page"])
        return result

    monkeypatch.setattr(SdkRuntimeCapabilityBridgeAdapter, "search", search)

    class LaneProvider(fixture.Provider):
        """每回合：context_route(memory_standalone 召回) → tool_search →
        context_page_in → 收尾文本。"""

        def __init__(self):
            super().__init__()
            self.calls = 0

        async def invoke(self, request, *, cancel):
            self.calls += 1
            results = [json.loads(m.content) for m in request.messages
                       if m.role.value == "tool" and isinstance(m.content, str)]
            step = len(results)
            if step == 0:
                name, arguments = "context_route", {
                    "route": "memory_standalone",
                    "query": f"turn fact {turn_state['turn']}",
                    "memory_types": ["semantic", "episode"]}
            elif step == 1:
                name, arguments = "tool_search", {"query": "write_file"}
            elif step == 2:
                # 打开 memory 车道后系统提示更长，工具结果会被预算截断成
                # typed_tool_result_summary（没有 value 字段）；页引用与 Host
                # 发布给模型的那一份完全一致，直接用。
                last = results[-1]
                page = last.get("value", {}).get("page") if isinstance(last, dict) else None
                page = page or turn_state["page"]
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

    provider = LaneProvider()
    runtime, stack, queue = await fixture.build(
        tmp_path, state, provider, dynamic=True, page_in_store=pages,
        memory=agent_memory, visibility_memory=memory_runtime,
        recall_executor=memory_runtime.typed_recall)
    registry = runtime.built_ports["ports"].tools._registry

    lane = None

    async def build_lane():
        with sqlite3.connect(state) as db:
            row = db.execute("SELECT analysis_lineage_json FROM memory_ingestion_outbox "
                             "ORDER BY created_at LIMIT 1").fetchone()
        lineage = json.loads(row[0])
        worker = MemoryIngestionOutboxWorker(state, memory_runtime.manager,
                                             owner_id="x2-outbox")
        return MemoryAnalysisLane(
            worker=worker, runtime=memory_runtime,
            executor=memory_runtime.analysis_authority,
            config=build_worker_config(provider_id=lineage["provider_id"],
                                       model_id=lineage["model_id"],
                                       model_config_hash=lineage["model_config_hash"],
                                       deadline_ms=20_000),
            worker_id="x2-analysis")

    async def drain_lane():
        for _ in range(24):
            outbox, job = await lane.tick()
            busy = outbox is not OutboxRunOutcome.IDLE or (
                job is not None and str(job) != "idle")
            busy = busy or (lane.last_short_step is not None
                            and lane.last_short_step.scanned > 0
                            and not lane.last_short_step.wrapped)
            if not busy:
                return

    page_bytes_per_turn = []
    tool_calls_per_turn = []
    baseline = None
    growth = None
    top_sites: list[str] = []
    tracemalloc.start(6)
    try:
        for turn in range(MEMORY_LANE_TURNS):
            turn_state.update(turn=turn, item=f"turn-{turn}")
            text = f"Offline actual user turn {turn} about quartznebula scheduling"
            turn_state["quote"] = text
            await service.enqueue_turn(QueueTurnRequest(None, f"turn-{turn}", text))
            await runtime.after_enqueue(subject=subject)
            deadline = time.monotonic() + 180
            while provider.calls < 4 * (turn + 1) or not _driver_idle(runtime):
                assert time.monotonic() < deadline, (
                    f"turn {turn} 未收敛：provider_calls={provider.calls} "
                    f"last_error={runtime.last_error!r}")
                await asyncio.sleep(0.02)
            if lane is None:
                lane = await build_lane()
            await drain_lane()
            gc.collect()
            page_bytes_per_turn.append(_page_store_bytes(pages))
            tool_calls_per_turn.append(len(registry.calls))
            if turn == MEMORY_LANE_WARMUP:
                baseline = _snapshot()
            elif turn == MEMORY_LANE_TURNS - 1:
                stats = _snapshot().compare_to(baseline, "lineno")
                growth = sum(stat.size_diff for stat in stats)
                top_sites = [f"{stat.size_diff / 1024:+.1f} KiB {stat.traceback[0]}"
                             for stat in stats[:8]]
    finally:
        tracemalloc.stop()
        if lane is not None:
            await lane.close()
        await runtime.close()
        await stack.close()
        await memory_runtime.close()
        await agent_memory.close()

    # 车道真的跑了：每回合一次终态、一次 ingestion 投递、一次分析提案落地。
    assert provider.calls >= 4 * MEMORY_LANE_TURNS
    assert adapter.calls >= MEMORY_LANE_TURNS, adapter.calls
    with sqlite3.connect(state) as db:
        terminals = dict(db.execute("SELECT terminal_state,COUNT(*) FROM "
                                    "foreground_terminal_receipts GROUP BY 1").fetchall())
        delivered = db.execute("SELECT COUNT(*) FROM memory_ingestion_outbox "
                               "WHERE state='delivered'").fetchone()[0]
    assert terminals == {"COMPLETED": MEMORY_LANE_TURNS}, terminals
    assert delivered == MEMORY_LANE_TURNS, delivered

    measured = MEMORY_LANE_TURNS - 1 - MEMORY_LANE_WARMUP
    per_turn = growth / measured
    print(f"[事件X2回归] 每回合净保留 {per_turn / 1024:.1f} KiB；"
          f"页存常驻 {[round(v / 1048576, 1) for v in page_bytes_per_turn]} MiB；"
          "增长最大的分配点：\n  " + "\n  ".join(top_sites))
    # 进程内页引用缓存必须按**字节**有界（事件 X 的 X-F3）：只按 512 条设界时，
    # 每回合净留一份大页，12 回合就是 36 MiB 且没有上界。
    assert max(page_bytes_per_turn) <= MAX_PAGE_STORE_BYTES, (
        f"ContextPageInStore 常驻字节未设界：{[round(v / 1048576, 1) for v in page_bytes_per_turn]} MiB")
    assert per_turn < MAX_LANE_RETAINED_BYTES_PER_TURN, (
        f"每回合净保留 {per_turn / 1024:.0f} KiB（{measured} 回合共 "
        f"{growth / 1048576:.1f} MiB）；页存常驻 "
        f"{[round(v / 1048576, 1) for v in page_bytes_per_turn]} MiB；"
        "增长最大的分配点：\n  " + "\n  ".join(top_sites))
    # 事件 X 的结构性不变量在 memory 车道打开后仍然成立。
    assert max(tool_calls_per_turn) <= 4, tool_calls_per_turn
