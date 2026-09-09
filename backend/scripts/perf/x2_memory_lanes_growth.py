"""事件 X-2 离线复现：打开 memory 车道的多回合前台 runtime 内存增长测量（诊断脚本）。

产品回归用例在 ``backend/tests/execution/test_primary_runtime_memory_growth.py``；
本脚本是它的**独立进程**版本，用来拿不受 pytest conftest 夹具污染的 RSS / gc 普查 /
保留根数据（conftest 的 autouse 夹具会把每个 aiosqlite 连接钉到用例结束）。

用法（cwd=backend）::

    PYTHONPATH=. REPRO_TMP=/tmp/x2 REPRO_TURNS=14 REPRO_WARMUP=4 \
        .venv/bin/python scripts/perf/x2_memory_lanes_growth.py

环境变量：``REPRO_TURNS`` / ``REPRO_WARMUP`` / ``REPRO_FILLER_KIB`` /
``REPRO_ROUTE``（memory_standalone|direct_standalone）/ ``REPRO_EMBEDDER``（1=打开
2048 维稠密夹具 embedder，短程向量车道才会跑）/ ``REPRO_AGENT_MEMORY`` /
``REPRO_CENSUS``（1=做容器普查与保留根反查；它自身会保留约 30 万个元组，**必须**与
tracemalloc/RSS 测量分两次进程跑）。

不启动原生 app、不连真 provider。装配：
  - `tests.execution.test_primary_foreground_runtime.build(dynamic=True)` 的真实
    runtime + SDK stack + 真实 context_route / tool_search / context_page_in；
  - SDK agent memory：`MemoryManager.build_development`（context_provider + staging
    + embedding catch-up 车道）；
  - Host 认知记忆：`compose_human_memory_runtime`（真实 HumanMemoryV7 store、
    HostMemoryAnalysisExecutor、typed recall），analysis provider 是返回合法 v9
    提案的假 adapter；
  - 每回合结束后驱动 `MemoryAnalysisLane.tick()` 直到空闲（ingestion outbox →
    short index → analysis job runner）。
每回合采 tracemalloc 快照 + gc 类型普查 + RSS + 显式结构探针（SDK kernel 的
``_leases``/``_fences``/``_cancels``/``_heartbeats``、页引用存储、asyncio 任务分布）。
"""
from __future__ import annotations

import asyncio
import gc
import json
import os
import time
import tracemalloc
from collections import Counter
from pathlib import Path

TURNS = int(os.environ.get("REPRO_TURNS", "14"))
WARMUP = int(os.environ.get("REPRO_WARMUP", "4"))
FILLER_BYTES = int(os.environ.get("REPRO_FILLER_KIB", "128")) * 1024
PAGE_BODY = "EXACT_PAGE_BODY " * 128
ROUTE = os.environ.get("REPRO_ROUTE", "memory_standalone")
# 容器普查会保留 ~30 万个 (类型名, 长度) 元组，本身就是几十 MB 的
# "增长"；因此与 tracemalloc/RSS 测量分两个进程跑。
CENSUS = os.environ.get("REPRO_CENSUS", "0") == "1"
# 真 WeMM-Embedding-2B（2B 参数）超出本轮 3 GB 进程预算，这里用同维度
# (2048/l2) 的确定性稠密夹具 embedder，让短程向量车道真的跑起来。
DENSE_EMBEDDER = os.environ.get("REPRO_EMBEDDER", "0") == "1"
USE_AGENT_MEMORY = os.environ.get("REPRO_AGENT_MEMORY", "1") == "1"


def rss_mib() -> float:
    import subprocess
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())],
                         capture_output=True, text=True).stdout.strip()
    return int(out) / 1024.0


def container_census() -> dict[int, tuple[str, int]]:
    """id(container) → (类型名, 长度)。只看有 __len__ 的常见容器。"""
    out: dict[int, tuple[str, int]] = {}
    for obj in gc.get_objects():
        t = type(obj)
        if t in (dict, list, set, tuple, Counter) or t.__name__ in ("deque", "WeakSet", "OrderedDict"):
            try:
                out[id(obj)] = (t.__qualname__, len(obj))
            except Exception:
                pass
    return out


def describe(obj, depth: int = 0) -> str:
    t = type(obj).__qualname__
    if depth >= 3:
        return t
    parts = []
    for ref in gc.get_referrers(obj)[:4]:
        rt = type(ref)
        if rt.__name__ in ("frame", "list") and depth == 0:
            continue
        if rt is dict:
            owners = [type(o).__qualname__ for o in gc.get_referrers(ref)[:3]
                      if type(o).__name__ != "list"]
            parts.append(f"dict<-{owners}")
        else:
            parts.append(rt.__qualname__)
    return f"{t} <- {parts}"


def report_growth(before: dict[int, tuple[str, int]], turns: int, limit: int = 20) -> None:
    after = container_census()
    grown = []
    alive = {id(o): o for o in gc.get_objects()
             if type(o) in (dict, list, set) or type(o).__name__ in ("deque", "WeakSet")}
    for key, (name, size) in after.items():
        old = before.get(key)
        if old is None or old[0] != name:
            continue
        if size - old[1] >= turns * 0.8:
            grown.append((size - old[1], name, size, key))
    grown.sort(reverse=True)
    print(f"\n=== 增长中的容器（每回合 >= 0.8 条，共 {len(grown)} 个） ===")
    for delta, name, size, key in grown[:limit]:
        obj = alive.get(key)
        holder = "?" if obj is None else describe(obj)
        sample = ""
        if obj is not None:
            try:
                items = list(obj)[-1:]
                sample = repr(items)[:160]
            except Exception:
                pass
        print(f"  +{delta/turns:5.2f}/回合  len={size:<6} {name}  持有者 {holder}\n"
              f"        末项 {sample}")


def type_histogram() -> Counter:
    counts: Counter = Counter()
    for obj in gc.get_objects():
        try:
            counts[type(obj).__qualname__] += 1
        except Exception:
            pass
    return counts


async def main() -> None:
    import tests.execution.test_primary_foreground_runtime as fixture
    from simple_harness import CallId
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
    from simple_harness_memory import MemoryManager
    from deskpet.memory.human_memory_service import (
        HumanMemoryHostServiceFactory, QueueTurnRequest,
    )
    from deskpet.memory.human_memory_v7 import local_memory_principal
    from deskpet.memory.runtime_composition import compose_human_memory_runtime
    from deskpet.memory.memory_ingestion_outbox import (
        MemoryAnalysisLane, MemoryIngestionOutboxWorker, build_worker_config,
        OutboxRunOutcome,
    )
    from deskpet.memory.analysis_lineage import binding_model_config_hash
    from deskpet.sdk_adapters.context_route import local_owner_auth
    from deskpet.sdk_adapters.tool_authority import SdkRuntimeCapabilityBridgeAdapter
    from deskpet.tools.context_page_in_tools import ContextPageInStore
    from tests.sdk_adapters import s5b_memory_harness as mh
    import sqlite3

    tmp = Path(os.environ["REPRO_TMP"])
    tmp.mkdir(parents=True, exist_ok=True)
    state = tmp / "state.db"

    from deskpet.memory.schema import dispatch_startup_epoch
    startup = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    service = HumanMemoryHostServiceFactory(state, startup).bind(local_owner_auth())
    await service.open_primary()
    subject = local_owner_auth().subject
    pages = ContextPageInStore()

    # --- Host 认知记忆 + 分析车道 -------------------------------------------------
    holder = {"item": None, "quote": None, "turn": 0}

    class AnalysisAdapter:
        """返回合法 v9 提案的确定性 adapter（同 ProductProviderAdapter.invoke 形状）。"""
        target = type("T", (), dict(provider_id="fixture", model="model",
                                    endpoint_identity="e" * 64))()

        def __init__(self):
            self.calls = 0

        async def invoke(self, request, *, cancel):
            self.calls += 1
            ops = []
            if holder["item"] is not None:
                ops = [mh.semantic_op(holder["item"], holder["quote"],
                                      operation_id=f"op-{holder['turn']}",
                                      predicate=f"turn_fact_{holder['turn']}",
                                      object_value=f"回合 {holder['turn']} 的事实")]
            return mh.proposal_call(ops, outcome="mutate" if ops else "no_mutation",
                                    raw_id=f"raw-{self.calls}",
                                    provider_request_id=f"prov-{self.calls}")

    adapter = AnalysisAdapter()

    embedder = None
    if DENSE_EMBEDDER:
        import hashlib as _hashlib
        import math as _math
        from simple_harness_memory.embedders.base import Embedder, EmbeddingLineage

        class FixtureDenseEmbedder(Embedder):
            """与 WeMM 同形状（2048 维 / l2 归一）的确定性稠密向量。"""

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
                seed = _hashlib.sha256(text.encode("utf-8")).digest()
                values = []
                block = seed
                while len(values) < self.dim:
                    block = _hashlib.sha256(block).digest()
                    values.extend(b / 255.0 - 0.5 for b in block)
                values = values[: self.dim]
                norm = _math.sqrt(sum(v * v for v in values)) or 1.0
                return [v / norm for v in values]

        embedder = FixtureDenseEmbedder()

    memory_runtime = compose_human_memory_runtime(
        state, tmp / "human_memory_v7.db",
        adapter_factory=lambda record: adapter,
        embedder_getter=(lambda: embedder) if embedder is not None else None,
        principal=local_memory_principal(),
    )

    # --- SDK agent memory --------------------------------------------------------
    agent_memory = (await MemoryManager.build_development(tmp / "agent-memory.db")
                    if USE_AGENT_MEMORY else None)

    original_search = SdkRuntimeCapabilityBridgeAdapter.search

    def search(self, query):
        result = dict(original_search(self, query))
        context = self._context_getter()
        reference = pages.put(kind="tool_result", source="x2-large-result",
                              content=PAGE_BODY, session_id=context.session_id,
                              request_id=context.request_id, scope_id=context.scope_id)
        result["filler"] = f"{len(result)}-" + "R" * FILLER_BYTES
        result["page"] = {"reference_id": reference.reference_id,
                          "source_hash": reference.source_hash}
        holder["page"] = dict(result["page"])
        return result

    SdkRuntimeCapabilityBridgeAdapter.search = search

    class TurnProvider(fixture.Provider):
        def __init__(self):
            super().__init__()
            self.calls = 0

        async def invoke(self, request, *, cancel):
            self.calls += 1
            results = [json.loads(m.content) for m in request.messages
                       if m.role.value == "tool" and isinstance(m.content, str)]
            step = len(results)
            if step == 0:
                name, arguments = ("context_route", {
                    "route": "memory_standalone",
                    "query": f"turn fact {holder['turn']}",
                    "memory_types": ["semantic", "episode"],
                }) if ROUTE == "memory_standalone" else ("context_route", {"route": "direct_standalone"})
            elif step == 1:
                name, arguments = "tool_search", {"query": "write_file"}
            elif step == 2:
                # 结果被预算截断成 typed_tool_result_summary 时没有 value；页引用
                # 与 Host 发布给模型的那一份完全一致，直接用。
                last = results[-1]
                page = last.get("value", {}).get("page") if isinstance(last, dict) else None
                page = page or holder["page"]
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
    runtime, stack, queue = await fixture.build(
        tmp, state, provider, dynamic=True, page_in_store=pages,
        memory=agent_memory,
        visibility_memory=memory_runtime,
        recall_executor=memory_runtime.typed_recall,
    )
    registry = runtime.built_ports["ports"].tools._registry

    lane = None

    def _driver_idle():
        driver = runtime._driver
        return driver is None or driver.done()

    async def build_lane():
        with sqlite3.connect(state) as db:
            row = db.execute(
                "SELECT analysis_lineage_json FROM memory_ingestion_outbox ORDER BY created_at LIMIT 1"
            ).fetchone()
        lineage = json.loads(row[0])
        worker = MemoryIngestionOutboxWorker(state, memory_runtime.manager,
                                             owner_id="x2-outbox")
        config = build_worker_config(provider_id=lineage["provider_id"],
                                     model_id=lineage["model_id"],
                                     model_config_hash=lineage["model_config_hash"],
                                     deadline_ms=20_000)
        return MemoryAnalysisLane(worker=worker, runtime=memory_runtime,
                                  executor=memory_runtime.analysis_authority,
                                  config=config, worker_id="x2-analysis")

    async def drain_lane(limit: int = 24) -> list[str]:
        outcomes = []
        for _ in range(limit):
            outbox, job = await lane.tick()
            outcomes.append(f"{outbox}/{job}")
            busy = outbox is not OutboxRunOutcome.IDLE or (job is not None and str(job) != "idle")
            busy = busy or (lane.last_short_step is not None
                            and lane.last_short_step.scanned > 0
                            and not lane.last_short_step.wrapped)
            if not busy:
                break
        return outcomes

    rows = []
    baseline = None
    baseline_hist = None
    tracemalloc.start(8)
    try:
        for turn in range(TURNS):
            holder["turn"] = turn
            holder["item"] = f"turn-{turn}"
            text = f"Offline actual user turn {turn} about quartznebula scheduling"
            holder["quote"] = text
            await service.enqueue_turn(QueueTurnRequest(None, f"turn-{turn}", text))
            await runtime.after_enqueue(subject=subject)
            deadline = time.monotonic() + 180
            while provider.calls < 4 * (turn + 1) or not _driver_idle():
                if time.monotonic() > deadline:
                    raise SystemExit(f"turn {turn} 未收敛 provider_calls={provider.calls} "
                                     f"last_error={runtime.last_error!r}")
                await asyncio.sleep(0.02)
            if lane is None:
                lane = await build_lane()
            outcomes = await drain_lane()
            gc.collect()
            gc.collect()

            kernels = [o for o in gc.get_objects()
                       if type(o).__qualname__ == "Runtime" and hasattr(o, "_leases")]
            kprobe = {}
            for k in kernels:
                kprobe = {name: len(getattr(k, name)) for name in
                          ("_leases", "_fences", "_cancels", "_heartbeats",
                           "_workflow_spawn_ready_activations", "_workflow_start_dispatches",
                           "_workflow_recovery_work", "_child_signal_wait_handoffs")}
                live = getattr(k, "_live", None)
                for attr in ("_runs", "_drivers", "_tasks", "_index", "_entries"):
                    if live is not None and hasattr(live, attr):
                        kprobe["live." + attr] = len(getattr(live, attr))
                kprobe["hb_done"] = sum(1 for t in getattr(k, "_heartbeats").values() if t.done())
            tasks = Counter()
            for t in asyncio.all_tasks():
                tasks[(t.get_name().split(":")[0], t.done())] += 1
            hist = type_histogram()
            snap = tracemalloc.take_snapshot()
            row = dict(turn=turn, rss=rss_mib(), tool_calls=len(registry.calls),
                       page_records=len(pages._records),
                       page_active=len(getattr(pages, "_active", ())),
                       gc_objects=len(gc.get_objects()),
                       lane=outcomes[-3:], adapter_calls=adapter.calls,
                       kernel=kprobe, tasks=dict(tasks))
            rows.append(row)
            print(f"[turn {turn}] RSS={row['rss']:.1f} MiB gc_objects={row['gc_objects']} "
                  f"tool_calls={row['tool_calls']} pages={row['page_records']} "
                  f"analysis_calls={adapter.calls}\n         kernel={kprobe}\n         tasks={dict(tasks)}", flush=True)

            if turn == WARMUP:
                baseline, baseline_hist = snap, hist
                census = container_census() if CENSUS else None
            elif turn == TURNS - 1 and baseline is not None:
                measured = TURNS - 1 - WARMUP
                stats = snap.compare_to(baseline, "lineno")
                total = sum(s.size_diff for s in stats)
                print(f"\n=== tracemalloc：{measured} 回合窗口净保留 "
                      f"{total/1048576:.2f} MiB，{total/measured/1024:.1f} KiB/回合 ===")
                for s in stats[:25]:
                    print(f"  {s.size_diff/measured/1024:+9.1f} KiB/回合  {s.count_diff/measured:+7.1f} 个/回合  {s.traceback[0]}")
                print(f"\n=== gc 类型普查（turn{WARMUP} → turn{TURNS-1}，每回合差） ===")
                delta = Counter()
                for key in set(hist) | set(baseline_hist):
                    d = hist[key] - baseline_hist[key]
                    if d:
                        delta[key] = d
                for key, d in delta.most_common(30):
                    if abs(d) / measured >= 0.4:
                        print(f"  {d/measured:+7.2f} 个/回合  {key}  (末值 {hist[key]})")
                if census is not None:
                    report_growth(census, measured)
                print("\n=== 逐类型保留根（gc.get_referrers） ===")
                for name in ("ExecutionLease", "CancelToken", "Task", "Context",
                             "Event", "deque", "TimerHandle", "Future"):
                    samples = [o for o in gc.get_objects()
                               if type(o).__qualname__ == name][-2:]
                    for obj in samples:
                        print(f"  {name}: {describe(obj)}")
    finally:
        tracemalloc.stop()
        try:
            if lane is not None:
                await lane.close()
        except Exception as exc:
            print("lane close", exc)
        await runtime.close()
        await stack.close()
        await memory_runtime.close()
        if agent_memory is not None:
            await agent_memory.close()

    print("\n=== 每回合汇总 ===")
    for row in rows:
        print(row)


if __name__ == "__main__":
    asyncio.run(main())
