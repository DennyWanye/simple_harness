# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 X-3：原生进程的按站点内存探针（默认关闭，关闭时零成本）。

事件 X / X-2 在**离线**复现里证明了每回合净保留只有 ~5 KiB，可原生 backend
（Tauri 拉起的 `backend/.venv/bin/python main.py`）仍然每个 provider 回合涨
250–300 MB（attempt 11：turn 15 RSS 5.1 GB）。X2-F2 的结论是：只有一次**原生**
测量能定这笔账。

本模块给原生跑提供这次测量所需的最小装置：

* 只有 `SIMPLEHARNESS_MEMORY_PROBE` 被显式打开时才启动 `tracemalloc`
  （25 帧）并挂终态钩子；关闭时 :func:`build_memory_probe` 直接返回 ``None``，
  调用方连监听器都不注册 —— 不 import `psutil`、不碰 `gc.get_objects()`、
  不启 `tracemalloc`。
* 打开时，每 N 个前台 Run 终态往 stdout（→ `native.log`）打**一行**结构化
  ``memory.probe``：RSS、tracemalloc current/peak、**相对上一次探针**的
  top 15 分配站点、gc 类型普查的 top 15 增量，以及一份写到
  ``<userdata>/memory-probe/turn-<n>.snap`` 的完整 tracemalloc 快照
  （只保留最近 30 份）。

**永远不记录载荷内容**：日志与快照里只有 `file:line`、字节数、对象计数与类型名。
"""
from __future__ import annotations

import gc
import os
import sys
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
]

PROBE_EVENT = "memory.probe"

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

DEFAULT_FRAMES = 25
DEFAULT_KEEP = 30
DEFAULT_TOP = 15

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


def _rss_kb() -> int | None:
    try:
        import psutil  # noqa: PLC0415 - 只在探针开启时才付出这次 import
    except Exception:
        return None
    try:
        return int(psutil.Process().memory_info().rss // 1024)
    except Exception:
        return None


def _max_rss_kb() -> int | None:
    try:
        import resource  # noqa: PLC0415 - Windows 上没有
    except Exception:
        return None
    try:
        raw = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None
    # macOS 的 ru_maxrss 是字节，Linux 是 KiB。
    return raw // 1024 if sys.platform == "darwin" else raw


class MemoryProbe:
    """按 Run 终态采样的进程内存探针。构造本身不做任何事，:meth:`start` 才启动。"""

    def __init__(
        self,
        *,
        snapshot_dir: Path,
        emit: Callable[[Mapping[str, Any]], None],
        every: int = 1,
        frames: int = DEFAULT_FRAMES,
        keep: int = DEFAULT_KEEP,
        top: int = DEFAULT_TOP,
        census: bool = True,
    ) -> None:
        self._snapshot_dir = Path(snapshot_dir)
        self._emit = emit
        self._every = max(1, int(every))
        self._frames = max(1, int(frames))
        self._keep = max(1, int(keep))
        self._top = max(1, int(top))
        self._census_enabled = bool(census)
        self._started = False
        self._terminals = 0
        self._probes = 0
        self._prev_sites: dict[str, tuple[int, int]] = {}
        self._prev_census: dict[str, int] = {}
        self._prev_rss_kb: int | None = None
        self._prev_traced_kb: int | None = None
        self._filters: tuple[tracemalloc.Filter, ...] = (
            tracemalloc.Filter(False, tracemalloc.__file__),
            tracemalloc.Filter(False, __file__),
            tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
            tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>"),
            tracemalloc.Filter(False, "<unknown>"),
        )

    # -- 只读探针 ---------------------------------------------------------
    @property
    def started(self) -> bool:
        return self._started

    @property
    def every(self) -> int:
        return self._every

    @property
    def terminals(self) -> int:
        return self._terminals

    @property
    def probes(self) -> int:
        return self._probes

    @property
    def snapshot_dir(self) -> Path:
        return self._snapshot_dir

    # -- 生命周期 ---------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        if not tracemalloc.is_tracing():
            tracemalloc.start(self._frames)
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        if tracemalloc.is_tracing():
            tracemalloc.stop()

    # -- 钩子 -------------------------------------------------------------
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
        if not self._started:
            return None
        self._terminals += 1
        if self._terminals % self._every:
            return None
        return self._sample(run_id)

    # -- 采样 -------------------------------------------------------------
    def _sample(self, run_id: str | None) -> Mapping[str, Any]:
        began = time.monotonic()
        self._probes += 1
        rss_kb = _rss_kb()
        current, peak = tracemalloc.get_traced_memory()
        traced_kb = current // 1024

        snapshot = tracemalloc.take_snapshot().filter_traces(self._filters)
        sites: dict[str, tuple[int, int]] = {}
        for stat in snapshot.statistics("lineno"):
            frame = stat.traceback[0]
            key = shorten_location(frame.filename, frame.lineno)
            size, count = sites.get(key, (0, 0))
            sites[key] = (size + stat.size, count + stat.count)

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

        snapshot_name = self._dump(snapshot)
        del snapshot

        gc_objects: int | None = None
        census_delta: list[str] = []
        if self._census_enabled:
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

        fields: dict[str, Any] = {
            "probe_seq": self._probes,
            "terminal_seq": self._terminals,
            "every": self._every,
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
            "sample_ms": round((time.monotonic() - began) * 1000, 1),
        }
        self._prev_sites = sites
        self._prev_rss_kb = rss_kb
        self._prev_traced_kb = traced_kb
        self._emit(fields)
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
        every=_positive_int(environment, ENV_EVERY, 1),
        frames=_positive_int(environment, ENV_FRAMES, DEFAULT_FRAMES),
        keep=_positive_int(environment, ENV_KEEP, DEFAULT_KEEP),
        top=_positive_int(environment, ENV_TOP, DEFAULT_TOP),
        census=_flag(environment, ENV_CENSUS, default=True),
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
