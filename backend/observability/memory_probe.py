# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 X-3：原生进程的按站点内存探针（默认关闭，关闭时零成本）。

事件 X / X-2 在**离线**复现里证明了每回合净保留只有 ~5 KiB，可原生 backend
（Tauri 拉起的 `backend/.venv/bin/python main.py`）仍然每个 provider 回合涨
250–300 MB（attempt 11：turn 15 RSS 5.1 GB）。X2-F2 的结论是：只有一次**原生**
测量能定这笔账。

X3-F4（第 12 次整跑中止）之后本模块被重写为**零干扰**形态：

* **采样绝不跑在事件循环上**。终态监听器只做两件事：打一行极便宜的
  ``memory.probe.rss``（只有 `resource.getrusage` + `tracemalloc.get_traced_memory`，
  微秒级），以及往一个单槽队列里**投递**一次采样请求，然后立刻返回。
  真正的 `take_snapshot()` / 快照落盘 / 可选的 gc 普查都在一条专用后台线程上跑。
* **请求合并**：同时最多一次采样在飞。采样期间来的终态被丢弃（不排队），
  并打一行 ``memory.probe.skipped`` 带累计计数 —— 旅程永远不会因为探针而变慢。
* **默认更便宜**：栈深默认 5 帧（`SIMPLEHARNESS_MEMORY_PROBE_FRAMES`），
  gc 类型普查**默认关**（`..._CENSUS=1` 才开），采样频率默认每 3 个终态一次
  （`..._EVERY`）。
* **不再 `filter_traces()`**。它在 1 GiB 堆上要 66 s（见下表），而它做的事
  —— 排掉 tracemalloc / 本模块 / importlib 自身的分配 —— 在 `statistics("lineno")`
  之后按文件名筛一遍等价，且几乎免费（结果只有几十行）。

1 GiB 堆上的离线实测（`backend/.venv/bin/python`，Python 3.12.14，见备忘
`DECISION-X3-MEMORY-PROBE.md` §X3-F4）：

===========================  ==========  =========================================
步骤                          耗时        备注
===========================  ==========  =========================================
``filter_traces()``          66.1 s      **已删除**
``statistics("lineno")``     17.6 s      后台线程，纯 Python，会让出 GIL
``take_snapshot()``          0.72 s      后台线程，C 层持 GIL
``dump()``                   1.27 s      72 MB/份
gc 普查（tracemalloc 开）      6.57 s      默认关
``getrusage`` 取 RSS          0.3 µs      每个终态都打
===========================  ==========  =========================================

**永远不记录载荷内容**：日志与快照里只有 `file:line`、字节数、对象计数与类型名。
"""
from __future__ import annotations

import gc
import os
import sys
import threading
import time
import tracemalloc
from collections import Counter
from collections.abc import Callable, Mapping, MutableMapping
from pathlib import Path
from typing import Any

__all__ = [
    "MemoryProbe",
    "build_memory_probe",
    "install_memory_probe",
    "PROBE_EVENT",
    "PROBE_RSS_EVENT",
    "PROBE_SKIPPED_EVENT",
]

#: 完整采样行（后台线程写）。
PROBE_EVENT = "memory.probe"
#: 每个终态一行的廉价 RSS 行（事件循环上写，微秒级）。
PROBE_RSS_EVENT = "memory.probe.rss"
#: 采样在飞时被丢掉的采样请求。
PROBE_SKIPPED_EVENT = "memory.probe.skipped"

ENV_ENABLED = "SIMPLEHARNESS_MEMORY_PROBE"
ENV_EVERY = "SIMPLEHARNESS_MEMORY_PROBE_EVERY"
ENV_FRAMES = "SIMPLEHARNESS_MEMORY_PROBE_FRAMES"
ENV_KEEP = "SIMPLEHARNESS_MEMORY_PROBE_KEEP"
ENV_TOP = "SIMPLEHARNESS_MEMORY_PROBE_TOP"
ENV_CENSUS = "SIMPLEHARNESS_MEMORY_PROBE_CENSUS"

PROBE_ENV_NAMES = (
    ENV_ENABLED,
    ENV_EVERY,
    ENV_FRAMES,
    ENV_KEEP,
    ENV_TOP,
    ENV_CENSUS,
)

#: X3-F4：25 帧让分配路径慢 13x（离线实测），5 帧只慢 4.3x。
DEFAULT_FRAMES = 5
#: X3-F4：每个终态都做全量采样会把旅程拖垮，默认 3 个终态一次。
DEFAULT_EVERY = 3
DEFAULT_KEEP = 30
DEFAULT_TOP = 15
#: X3-F4：gc 普查在 1 GiB 堆上要 6.5 s，默认关，`..._CENSUS=1` 才开。
DEFAULT_CENSUS = False
#: `--memory-probe-light` 用的采样间隔（发射端也硬编码同一个值）。
LIGHT_EVERY = 6

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"", "0", "false", "no", "off"}


def _flag(env: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = str(env.get(name, "")).strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return default if raw == "" else False
    return default


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = str(env.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def shorten_location(filename: str, lineno: int) -> str:
    """把绝对路径压成稳定、可读、跨进程可比的 `pkg/mod.py:123`。"""

    text = str(filename).replace(os.sep, "/")
    for marker in ("/site-packages/", "/backend/", "/simple_harness/"):
        index = text.rfind(marker)
        if index >= 0:
            text = text[index + len(marker) :]
            break
    else:
        parts = text.split("/")
        text = "/".join(parts[-3:]) if len(parts) > 3 else text
    return f"{text}:{int(lineno)}"


def _max_rss_kb() -> int | None:
    """进程峰值 RSS。`resource` 是 stdlib、一次 `getrusage` 只要 ~0.3 µs。"""

    try:
        import resource  # noqa: PLC0415 - Windows 上没有

        raw = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None
    # macOS 的 ru_maxrss 是字节，Linux 是 KiB。
    return raw // 1024 if sys.platform == "darwin" else raw


def _psutil_process() -> Any | None:
    """只在 :meth:`MemoryProbe.start` 里解析一次；之后 `memory_info()` 只要 ~1.2 µs。"""

    try:
        import psutil  # noqa: PLC0415 - 只在探针开启时才付出这次 import

        return psutil.Process()
    except Exception:
        return None


class MemoryProbe:
    """按 Run 终态采样的进程内存探针。构造本身不做任何事，:meth:`start` 才启动。

    线程模型（X3-F4）：

    * :meth:`observe_terminal` / :meth:`record_terminal` 只在**事件循环**上跑，
      永远是微秒级：打一行 RSS，投递或丢弃一次采样请求，返回。
    * :meth:`_sample` 只在**专用后台线程**上跑。
    * 跨线程共享的只有 ``_pending`` / ``_in_flight`` / ``_skipped``，都在
      ``_lock`` 下；`_prev_sites` 等趋势状态只被采样线程碰。
    """

    def __init__(
        self,
        *,
        snapshot_dir: Path,
        emit: Callable[[Mapping[str, Any]], None],
        every: int = DEFAULT_EVERY,
        frames: int = DEFAULT_FRAMES,
        keep: int = DEFAULT_KEEP,
        top: int = DEFAULT_TOP,
        census: bool = DEFAULT_CENSUS,
        join_timeout: float = 5.0,
    ) -> None:
        self._snapshot_dir = Path(snapshot_dir)
        self._emit = emit
        self._every = max(1, int(every))
        self._frames = max(1, int(frames))
        self._keep = max(1, int(keep))
        self._top = max(1, int(top))
        self._census_enabled = bool(census)
        self._join_timeout = float(join_timeout)
        self._started = False
        self._terminals = 0
        self._probes = 0
        self._skipped = 0
        # 事件循环侧
        self._prev_rss_line_kb: int | None = None
        # 采样线程侧
        self._prev_sites: dict[str, tuple[int, int]] = {}
        self._prev_census: dict[str, int] = {}
        self._prev_rss_kb: int | None = None
        self._prev_traced_kb: int | None = None
        # 跨线程
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = False
        self._thread: threading.Thread | None = None
        self._pending: tuple[int, str | None, float] | None = None
        self._in_flight = False
        self._process: Any | None = None
        # X3-F4：不再 `filter_traces()`（1 GiB 堆上 66 s），改成 `statistics()`
        # 之后按顶帧文件名筛，几十行的筛选几乎免费。
        self._excluded_files = frozenset(
            {
                tracemalloc.__file__,
                __file__,
                "<frozen importlib._bootstrap>",
                "<frozen importlib._bootstrap_external>",
                "<unknown>",
            }
        )

    # -- 只读探针 ---------------------------------------------------------
    @property
    def started(self) -> bool:
        return self._started

    @property
    def every(self) -> int:
        return self._every

    @property
    def frames(self) -> int:
        return self._frames

    @property
    def census_enabled(self) -> bool:
        return self._census_enabled

    @property
    def terminals(self) -> int:
        return self._terminals

    @property
    def probes(self) -> int:
        return self._probes

    @property
    def skipped(self) -> int:
        return self._skipped

    @property
    def snapshot_dir(self) -> Path:
        return self._snapshot_dir

    # -- 生命周期 ---------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        if not tracemalloc.is_tracing():
            tracemalloc.start(self._frames)
        self._process = _psutil_process()
        self._stopping = False
        self._wake.clear()
        thread = threading.Thread(
            target=self._run, name="memory-probe-sampler", daemon=True
        )
        self._thread = thread
        self._started = True
        thread.start()

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        self._stopping = True
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=self._join_timeout)
        if tracemalloc.is_tracing():
            tracemalloc.stop()

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """等到既没有排队请求也没有在飞采样。测试与收工落盘用。"""

        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                if self._pending is None and not self._in_flight:
                    return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.002)

    # -- 钩子（事件循环上，必须是微秒级）----------------------------------
    def observe_terminal(self, authority: Any = None) -> None:
        """`SdkRunToolAuthorityRegistry.add_terminal_listener` 的监听器形状。

        探针**永不**因为自身失败影响业务终态，所以整条路径吞异常。
        """

        run_id = getattr(authority, "run_id", None)
        try:
            self.record_terminal(str(run_id) if run_id is not None else None)
        except Exception:  # pragma: no cover - 探针不允许影响 Run 终态
            pass

    def record_terminal(self, run_id: str | None = None) -> Mapping[str, Any] | None:
        """打一行廉价 RSS 并（按 `every`）投递一次后台采样；**从不**在这里采样。"""

        if not self._started:
            return None
        began = time.perf_counter()
        self._terminals += 1
        terminal_seq = self._terminals

        status = "idle"
        skipped_fields: dict[str, Any] | None = None
        if terminal_seq % self._every == 0:
            with self._lock:
                if self._pending is not None or self._in_flight:
                    self._skipped += 1
                    status = "skipped"
                    skipped_fields = {
                        "probe_event": PROBE_SKIPPED_EVENT,
                        "terminal_seq": terminal_seq,
                        "run_id": run_id,
                        "skipped_total": self._skipped,
                        "reason": "sample_in_flight" if self._in_flight else "queued",
                    }
                else:
                    self._pending = (terminal_seq, run_id, time.monotonic())
                    status = "queued"
            if status == "queued":
                self._wake.set()

        rss_kb = self._rss_kb()
        current, peak = (
            tracemalloc.get_traced_memory() if tracemalloc.is_tracing() else (0, 0)
        )
        fields: dict[str, Any] = {
            "probe_event": PROBE_RSS_EVENT,
            "terminal_seq": terminal_seq,
            "every": self._every,
            "run_id": run_id,
            "rss_kb": rss_kb,
            "rss_delta_kb": (
                None
                if rss_kb is None or self._prev_rss_line_kb is None
                else rss_kb - self._prev_rss_line_kb
            ),
            "rss_max_kb": _max_rss_kb(),
            "tracemalloc_current_kb": current // 1024,
            "tracemalloc_peak_kb": peak // 1024,
            "sampling": status,
            "skipped_total": self._skipped,
            "listener_us": round((time.perf_counter() - began) * 1_000_000, 1),
        }
        self._prev_rss_line_kb = rss_kb
        self._emit_safe(fields)
        if skipped_fields is not None:
            self._emit_safe(skipped_fields)
        return fields

    def _rss_kb(self) -> int | None:
        process = self._process
        if process is not None:
            try:
                return int(process.memory_info().rss // 1024)
            except Exception:
                return _max_rss_kb()
        return _max_rss_kb()

    def _emit_safe(self, fields: Mapping[str, Any]) -> None:
        try:
            self._emit(fields)
        except Exception:  # pragma: no cover - 探针不允许影响 Run 终态
            pass

    # -- 采样线程 ---------------------------------------------------------
    def _run(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            if self._stopping:
                return
            while True:
                with self._lock:
                    request = self._pending
                    self._pending = None
                    self._in_flight = request is not None
                if request is None:
                    break
                try:
                    self._sample(*request)
                except Exception:  # pragma: no cover - 采样失败不许拖垮线程
                    pass
                finally:
                    with self._lock:
                        self._in_flight = False
                if self._stopping:
                    return

    def _sample(
        self, terminal_seq: int, run_id: str | None, queued_at: float
    ) -> Mapping[str, Any]:
        began = time.monotonic()
        queued_ms = round((began - queued_at) * 1000, 1)
        self._probes += 1
        rss_kb = self._rss_kb()
        current, peak = tracemalloc.get_traced_memory()
        traced_kb = current // 1024

        mark = time.monotonic()
        snapshot = tracemalloc.take_snapshot()
        snapshot_ms = round((time.monotonic() - mark) * 1000, 1)

        mark = time.monotonic()
        sites: dict[str, tuple[int, int]] = {}
        for stat in snapshot.statistics("lineno"):
            frame = stat.traceback[0]
            if frame.filename in self._excluded_files:
                continue
            key = shorten_location(frame.filename, frame.lineno)
            size, count = sites.get(key, (0, 0))
            sites[key] = (size + stat.size, count + stat.count)
        statistics_ms = round((time.monotonic() - mark) * 1000, 1)

        baseline = not self._prev_sites
        deltas: list[tuple[int, str, int]] = []
        for key, (size, count) in sites.items():
            prev_size, prev_count = self._prev_sites.get(key, (0, 0))
            deltas.append((size - prev_size, key, count - prev_count))
        deltas.sort(key=lambda row: row[0], reverse=True)
        top_sites = [
            f"{key} {size_delta / 1024:.1f} {count_delta}"
            for size_delta, key, count_delta in deltas[: self._top]
            if size_delta > 0
        ]

        mark = time.monotonic()
        snapshot_name = self._dump(snapshot)
        dump_ms = round((time.monotonic() - mark) * 1000, 1)
        del snapshot

        gc_objects: int | None = None
        census_delta: list[str] = []
        census_ms: float | None = None
        if self._census_enabled:
            mark = time.monotonic()
            census = self._census()
            gc_objects = sum(census.values())
            rows = [
                (count - self._prev_census.get(name, 0), name)
                for name, count in census.items()
            ]
            rows.sort(key=lambda row: row[0], reverse=True)
            census_delta = [
                f"{name} {delta}" for delta, name in rows[: self._top] if delta > 0
            ]
            self._prev_census = census
            census_ms = round((time.monotonic() - mark) * 1000, 1)

        fields: dict[str, Any] = {
            "probe_event": PROBE_EVENT,
            "probe_seq": self._probes,
            "terminal_seq": terminal_seq,
            "every": self._every,
            "frames": self._frames,
            "census": self._census_enabled,
            "baseline": baseline,
            "run_id": run_id,
            "rss_kb": rss_kb,
            "rss_delta_kb": (
                None
                if rss_kb is None or self._prev_rss_kb is None
                else rss_kb - self._prev_rss_kb
            ),
            "rss_max_kb": _max_rss_kb(),
            "tracemalloc_current_kb": traced_kb,
            "tracemalloc_peak_kb": peak // 1024,
            "tracemalloc_delta_kb": (
                None if self._prev_traced_kb is None else traced_kb - self._prev_traced_kb
            ),
            "gc_objects": gc_objects,
            "gc_counts": list(gc.get_count()),
            "top_sites": top_sites,
            "type_census_delta": census_delta,
            "snapshot": snapshot_name,
            "skipped_total": self._skipped,
            "queued_ms": queued_ms,
            "snapshot_ms": snapshot_ms,
            "statistics_ms": statistics_ms,
            "dump_ms": dump_ms,
            "census_ms": census_ms,
            "sample_ms": round((time.monotonic() - began) * 1000, 1),
            "thread": threading.current_thread().name,
        }
        self._prev_sites = sites
        self._prev_rss_kb = rss_kb
        self._prev_traced_kb = traced_kb
        self._emit_safe(fields)
        return fields

    def _census(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        objects = gc.get_objects()
        try:
            for obj in objects:
                counts[type(obj).__name__] += 1
        finally:
            del objects
        # 普查自己的 Counter 也会被下一轮普查数到，但它是**同一个**对象被替换，
        # 不随回合增长，所以不污染趋势。
        return dict(counts)

    def _dump(self, snapshot: tracemalloc.Snapshot) -> str | None:
        try:
            self._snapshot_dir.mkdir(parents=True, exist_ok=True)
            name = f"turn-{self._probes}.snap"
            snapshot.dump(str(self._snapshot_dir / name))
        except Exception:
            return None
        self._prune()
        return name

    def _prune(self) -> None:
        try:
            existing = sorted(
                self._snapshot_dir.glob("turn-*.snap"),
                key=lambda path: _snapshot_ordinal(path.name),
            )
        except Exception:
            return
        for stale in existing[: max(0, len(existing) - self._keep)]:
            try:
                stale.unlink()
            except OSError:
                pass


def _snapshot_ordinal(name: str) -> int:
    stem = name[len("turn-") : -len(".snap")] if name.endswith(".snap") else name
    try:
        return int(stem)
    except ValueError:
        return -1


def build_memory_probe(
    *,
    snapshot_dir: Path,
    emit: Callable[[Mapping[str, Any]], None],
    env: Mapping[str, str] | None = None,
) -> MemoryProbe | None:
    """按环境变量构造探针；**未显式打开时返回 ``None``**（零成本）。"""

    environment: Mapping[str, str] = os.environ if env is None else env
    if not _flag(environment, ENV_ENABLED, default=False):
        return None
    return MemoryProbe(
        snapshot_dir=Path(snapshot_dir),
        emit=emit,
        every=_positive_int(environment, ENV_EVERY, DEFAULT_EVERY),
        frames=_positive_int(environment, ENV_FRAMES, DEFAULT_FRAMES),
        keep=_positive_int(environment, ENV_KEEP, DEFAULT_KEEP),
        top=_positive_int(environment, ENV_TOP, DEFAULT_TOP),
        census=_flag(environment, ENV_CENSUS, default=DEFAULT_CENSUS),
    )


def install_memory_probe(
    *,
    registry: Any,
    snapshot_dir: Path,
    emit: Callable[[Mapping[str, Any]], None],
    env: Mapping[str, str] | None = None,
) -> MemoryProbe | None:
    """构造 + 启动 + 挂到 Run 终态监听器。关闭时**不碰** ``registry``。"""

    probe = build_memory_probe(snapshot_dir=snapshot_dir, emit=emit, env=env)
    if probe is None:
        return None
    probe.start()
    registry.add_terminal_listener(probe.observe_terminal)
    return probe


def clear_probe_env(env: MutableMapping[str, str]) -> None:
    """把继承来的探针环境变量清干净（发射端用，保证跑与跑之间确定）。"""

    for name in PROBE_ENV_NAMES:
        env.pop(name, None)
