# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 X-3：原生按站点内存探针（`observability/memory_probe.py`）。

四件必须成立的事：

1. **默认关闭 = 零成本**：环境变量没显式打开时 `install_memory_probe` 返回
   ``None``、**不注册**任何终态监听器、不启动 `tracemalloc`、不产出日志行；
2. **打开时监听器仍然是微秒级**（X3-F4 的核心回归）：哪怕堆里已经有几十万个
   被 tracemalloc 追踪的对象，`observe_terminal` 也必须在毫秒内返回，采样在
   **另一条线程**上做；采样在飞时多余的终态被丢弃并计数；
3. **每个终态一行廉价 ``memory.probe.rss``**，全量 ``memory.probe`` 每 N 个终态一行；
   字段齐全、第一条全量行是 baseline、`top_sites` / `type_census_delta` 只有
   `file:line`/类型名与数字（无载荷）；
4. **快照文件有界**：`<snapshot_dir>/turn-<n>.snap` 只保留最近 keep 份。
"""
from __future__ import annotations

import re
import threading
import time
import tracemalloc

import pytest

from observability.memory_probe import (
    DEFAULT_CENSUS,
    DEFAULT_EVERY,
    DEFAULT_FRAMES,
    ENV_CENSUS,
    ENV_ENABLED,
    ENV_EVERY,
    ENV_FRAMES,
    ENV_KEEP,
    ENV_TOP,
    LIGHT_EVERY,
    PROBE_EVENT,
    PROBE_RSS_EVENT,
    PROBE_SKIPPED_EVENT,
    MemoryProbe,
    build_memory_probe,
    install_memory_probe,
    shorten_location,
)

_SITE = re.compile(r"^[^\s]+:\d+ -?\d+\.\d+ -?\d+$")
_CENSUS = re.compile(r"^[^\s]+ -?\d+$")

_RSS_KEYS = {
    "probe_event",
    "terminal_seq",
    "every",
    "run_id",
    "rss_kb",
    "rss_delta_kb",
    "rss_max_kb",
    "tracemalloc_current_kb",
    "tracemalloc_peak_kb",
    "sampling",
    "skipped_total",
    "listener_us",
}

_FULL_KEYS = {
    "probe_event",
    "probe_seq",
    "terminal_seq",
    "every",
    "frames",
    "census",
    "baseline",
    "run_id",
    "rss_kb",
    "rss_delta_kb",
    "rss_max_kb",
    "tracemalloc_current_kb",
    "tracemalloc_peak_kb",
    "tracemalloc_delta_kb",
    "gc_objects",
    "gc_counts",
    "top_sites",
    "type_census_delta",
    "snapshot",
    "skipped_total",
    "queued_ms",
    "snapshot_ms",
    "statistics_ms",
    "dump_ms",
    "census_ms",
    "sample_ms",
    "thread",
}


class _FakeRegistry:
    def __init__(self) -> None:
        self.listeners: list[object] = []

    def add_terminal_listener(self, listener) -> None:  # type: ignore[no-untyped-def]
        self.listeners.append(listener)

    def fire(self, run_id: str) -> None:
        record = type("_Authority", (), {"run_id": run_id})()
        for listener in tuple(self.listeners):
            listener(record)


@pytest.fixture
def restore_tracemalloc():
    """探针会全局启停 tracemalloc；用例之间必须还原，否则会串。"""

    was_tracing = tracemalloc.is_tracing()
    yield
    if tracemalloc.is_tracing() and not was_tracing:
        tracemalloc.stop()
    elif was_tracing and not tracemalloc.is_tracing():
        tracemalloc.start()


@pytest.fixture
def emitted() -> list[dict]:
    return []


def _install(env, tmp_path, emitted, registry=None, emit=None):
    registry = registry if registry is not None else _FakeRegistry()
    probe = install_memory_probe(
        registry=registry,
        snapshot_dir=tmp_path / "memory-probe",
        emit=emit if emit is not None else (lambda fields: emitted.append(dict(fields))),
        env=env,
    )
    return registry, probe


def _of(emitted, kind):
    return [record for record in emitted if record.get("probe_event") == kind]


def _await(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.002)
    return False


# --------------------------------------------------------------------------
# 1. 关闭时零成本
# --------------------------------------------------------------------------
def test_probe_off_by_default(tmp_path, emitted, restore_tracemalloc):
    registry, probe = _install({}, tmp_path, emitted)
    assert probe is None
    assert registry.listeners == []
    assert emitted == []
    assert not (tmp_path / "memory-probe").exists()


@pytest.mark.parametrize("value", ["", "0", "false", "off", "no"])
def test_probe_explicitly_off(value, tmp_path, emitted, restore_tracemalloc):
    registry, probe = _install({ENV_ENABLED: value}, tmp_path, emitted)
    assert probe is None
    assert registry.listeners == []


def test_probe_off_does_not_start_tracemalloc(tmp_path, emitted):
    was_tracing = tracemalloc.is_tracing()
    assert build_memory_probe(
        snapshot_dir=tmp_path, emit=emitted.append, env={ENV_ENABLED: "0"}
    ) is None
    assert tracemalloc.is_tracing() is was_tracing


def test_probe_off_starts_no_sampling_thread(tmp_path, emitted, restore_tracemalloc):
    before = {thread.name for thread in threading.enumerate()}
    _install({ENV_ENABLED: "0"}, tmp_path, emitted)
    after = {thread.name for thread in threading.enumerate()}
    assert not {name for name in after - before if "memory-probe" in name}


# --------------------------------------------------------------------------
# 2. X3-F4：环境变量默认值（更便宜的一套）
# --------------------------------------------------------------------------
def test_env_defaults_are_the_cheap_ones(tmp_path, emitted):
    probe = build_memory_probe(
        snapshot_dir=tmp_path, emit=emitted.append, env={ENV_ENABLED: "1"}
    )
    assert probe is not None
    assert (DEFAULT_FRAMES, DEFAULT_EVERY, DEFAULT_CENSUS) == (5, 3, False)
    assert probe.frames == 5
    assert probe.every == 3
    assert probe.census_enabled is False
    assert probe.started is False  # 构造不启动


def test_env_overrides_are_parsed(tmp_path, emitted):
    probe = build_memory_probe(
        snapshot_dir=tmp_path,
        emit=emitted.append,
        env={
            ENV_ENABLED: "on",
            ENV_EVERY: str(LIGHT_EVERY),
            ENV_FRAMES: "9",
            ENV_KEEP: "4",
            ENV_TOP: "7",
            ENV_CENSUS: "1",
        },
    )
    assert probe is not None
    assert (probe.every, probe.frames, probe.census_enabled) == (6, 9, True)


@pytest.mark.parametrize("value", ["", "not-a-number", "0", "-3"])
def test_bad_numbers_fall_back_to_defaults(value, tmp_path, emitted):
    probe = build_memory_probe(
        snapshot_dir=tmp_path,
        emit=emitted.append,
        env={ENV_ENABLED: "1", ENV_EVERY: value, ENV_FRAMES: value},
    )
    assert probe is not None
    assert (probe.every, probe.frames) == (DEFAULT_EVERY, DEFAULT_FRAMES)


# --------------------------------------------------------------------------
# 3. X3-F4 核心回归：监听器不许阻塞事件循环
# --------------------------------------------------------------------------
def test_listener_returns_in_milliseconds_with_a_large_heap(
    tmp_path, emitted, restore_tracemalloc
):
    """X3-F4：第 12 次整跑就死在这里 —— 终态钩子在事件循环上做了全量采样。

    先在 tracemalloc 追踪下堆出 30 万个对象（旧实现在这个规模上 `filter_traces`
    + `statistics` 要好几秒），再量 6 次监听器调用的**墙钟**耗时。
    """

    registry, probe = _install(
        {ENV_ENABLED: "1", ENV_EVERY: "1", ENV_FRAMES: "5"}, tmp_path, emitted
    )
    assert probe is not None
    try:
        heap = [{"i": index, "pad": b"x" * 64} for index in range(300_000)]
        assert len(heap) == 300_000
        worst = 0.0
        for ordinal in range(6):
            began = time.perf_counter()
            registry.fire(f"run-{ordinal}")
            worst = max(worst, time.perf_counter() - began)
        del heap
    finally:
        probe.wait_idle(timeout=60)
        probe.stop()

    assert worst < 0.05, f"terminal listener blocked the event loop for {worst:.3f}s"
    rss_lines = _of(emitted, PROBE_RSS_EVENT)
    assert len(rss_lines) == 6
    assert max(record["listener_us"] for record in rss_lines) < 50_000


def test_sampling_runs_on_the_background_thread(tmp_path, emitted, restore_tracemalloc):
    threads: list[tuple[str, int]] = []

    def emit(fields):
        emitted.append(dict(fields))
        threads.append((str(fields.get("probe_event")), threading.get_ident()))

    registry, probe = _install(
        {ENV_ENABLED: "1", ENV_EVERY: "1", ENV_FRAMES: "5"},
        tmp_path,
        emitted,
        emit=emit,
    )
    assert probe is not None
    try:
        registry.fire("run-a")
        assert _await(lambda: _of(emitted, PROBE_EVENT))
    finally:
        probe.wait_idle(timeout=30)
        probe.stop()

    caller = threading.get_ident()
    rss_threads = [ident for kind, ident in threads if kind == PROBE_RSS_EVENT]
    full_threads = [ident for kind, ident in threads if kind == PROBE_EVENT]
    assert rss_threads and set(rss_threads) == {caller}
    assert full_threads and caller not in full_threads
    assert _of(emitted, PROBE_EVENT)[0]["thread"] == "memory-probe-sampler"


def test_requests_are_coalesced_and_skips_are_counted(
    tmp_path, emitted, restore_tracemalloc
):
    """采样在飞时来的终态被丢弃（不排队），并打 `memory.probe.skipped`。"""

    gate = threading.Event()

    def emit(fields):
        emitted.append(dict(fields))
        if fields.get("probe_event") == PROBE_EVENT:
            gate.wait(timeout=30)

    registry, probe = _install(
        {ENV_ENABLED: "1", ENV_EVERY: "1", ENV_FRAMES: "5"},
        tmp_path,
        emitted,
        emit=emit,
    )
    assert probe is not None
    try:
        registry.fire("run-0")
        assert _await(lambda: _of(emitted, PROBE_EVENT)), "sampler never started"
        for ordinal in range(1, 4):
            registry.fire(f"run-{ordinal}")
    finally:
        gate.set()
        probe.wait_idle(timeout=30)
        probe.stop()

    assert len(_of(emitted, PROBE_EVENT)) == 1, "more than one sample was in flight"
    assert probe.probes == 1 and probe.terminals == 4
    skipped = _of(emitted, PROBE_SKIPPED_EVENT)
    assert [record["terminal_seq"] for record in skipped] == [2, 3, 4]
    assert [record["skipped_total"] for record in skipped] == [1, 2, 3]
    assert {record["reason"] for record in skipped} <= {"sample_in_flight", "queued"}
    assert probe.skipped == 3
    rss_lines = _of(emitted, PROBE_RSS_EVENT)
    assert [record["sampling"] for record in rss_lines] == [
        "queued",
        "skipped",
        "skipped",
        "skipped",
    ]


# --------------------------------------------------------------------------
# 4. 行的形状：每终态 RSS 行 + 每 N 个终态一条全量行
# --------------------------------------------------------------------------
def test_rss_line_every_terminal_full_sample_every_n(
    tmp_path, emitted, restore_tracemalloc
):
    registry, probe = _install(
        {ENV_ENABLED: "1", ENV_EVERY: "3", ENV_FRAMES: "5"}, tmp_path, emitted
    )
    assert probe is not None and probe.started
    assert len(registry.listeners) == 1
    try:
        for ordinal in range(7):
            registry.fire(f"run-{ordinal}")
            # 采样是异步的，连着打会被合并掉；这条用例量的是"每 N 个终态投递
            # 一次"，所以逐条等采样落地（合并本身另有用例）。
            assert probe.wait_idle(timeout=30)
        assert _await(lambda: len(_of(emitted, PROBE_EVENT)) == 2)
    finally:
        probe.wait_idle(timeout=30)
        probe.stop()

    rss_lines = _of(emitted, PROBE_RSS_EVENT)
    assert [record["terminal_seq"] for record in rss_lines] == [1, 2, 3, 4, 5, 6, 7]
    assert [record["sampling"] for record in rss_lines] == [
        "idle",
        "idle",
        "queued",
        "idle",
        "idle",
        "queued",
        "idle",
    ]
    full = _of(emitted, PROBE_EVENT)
    assert [record["terminal_seq"] for record in full] == [3, 6]
    assert [record["probe_seq"] for record in full] == [1, 2]
    assert [record["run_id"] for record in full] == ["run-2", "run-5"]
    assert [record["baseline"] for record in full] == [True, False]
    assert {record["every"] for record in full} == {3}
    assert probe.terminals == 7 and probe.probes == 2


def test_rss_line_has_expected_keys(tmp_path, emitted, restore_tracemalloc):
    registry, probe = _install(
        {ENV_ENABLED: "1", ENV_EVERY: "100"}, tmp_path, emitted
    )
    assert probe is not None
    try:
        registry.fire("run-a")
        ballast = [bytearray(4096) for _ in range(256)]
        registry.fire("run-b")
        assert len(ballast) == 256
    finally:
        probe.stop()

    rss_lines = _of(emitted, PROBE_RSS_EVENT)
    assert len(rss_lines) == 2
    assert _of(emitted, PROBE_EVENT) == []  # every=100，没到，绝不采样
    for record in rss_lines:
        assert set(record) == _RSS_KEYS
        assert record["probe_event"] == "memory.probe.rss"
        assert record["sampling"] == "idle"
        assert isinstance(record["tracemalloc_current_kb"], int)
    assert rss_lines[0]["rss_delta_kb"] is None
    assert rss_lines[1]["rss_delta_kb"] is not None or rss_lines[1]["rss_kb"] is None


def test_full_line_has_expected_keys(tmp_path, emitted, restore_tracemalloc):
    registry, probe = _install({ENV_ENABLED: "yes", ENV_EVERY: "1"}, tmp_path, emitted)
    assert probe is not None
    try:
        registry.fire("run-a")
        assert _await(lambda: len(_of(emitted, PROBE_EVENT)) == 1)
        ballast = [bytearray(4096) for _ in range(256)]
        registry.fire("run-b")
        assert len(ballast) == 256
        assert _await(lambda: len(_of(emitted, PROBE_EVENT)) == 2)
    finally:
        probe.wait_idle(timeout=30)
        probe.stop()

    full = _of(emitted, PROBE_EVENT)
    for record in full:
        assert set(record) == _FULL_KEYS
        assert record["probe_event"] == "memory.probe"
        assert record["frames"] == 5 and record["census"] is False
        assert isinstance(record["tracemalloc_current_kb"], int)
        assert record["gc_objects"] is None  # 普查默认关
        assert record["census_ms"] is None
        assert len(record["top_sites"]) <= 15
        assert record["type_census_delta"] == []
        assert record["queued_ms"] >= 0 and record["sample_ms"] >= 0
    assert full[1]["tracemalloc_delta_kb"] is not None


def test_probe_lines_carry_no_payload(tmp_path, emitted, restore_tracemalloc):
    registry, probe = _install(
        {ENV_ENABLED: "1", ENV_EVERY: "1", ENV_CENSUS: "1"}, tmp_path, emitted
    )
    assert probe is not None
    try:
        registry.fire("run-a")
        assert _await(lambda: len(_of(emitted, PROBE_EVENT)) == 1)
        secret = ["S3CRET-PAYLOAD-" + str(index) for index in range(512)]
        registry.fire("run-b")
        assert len(secret) == 512
        assert _await(lambda: len(_of(emitted, PROBE_EVENT)) == 2)
    finally:
        probe.wait_idle(timeout=30)
        probe.stop()

    for record in emitted:
        for entry in record.get("top_sites") or ():
            assert _SITE.match(entry), entry
        for entry in record.get("type_census_delta") or ():
            assert _CENSUS.match(entry), entry
        assert "S3CRET-PAYLOAD" not in repr(record)


def test_census_can_be_enabled(tmp_path, emitted, restore_tracemalloc):
    registry, probe = _install(
        {ENV_ENABLED: "1", ENV_EVERY: "1", ENV_CENSUS: "1"}, tmp_path, emitted
    )
    assert probe is not None
    try:
        registry.fire("run-a")
        assert _await(lambda: len(_of(emitted, PROBE_EVENT)) == 1)
    finally:
        probe.wait_idle(timeout=30)
        probe.stop()
    record = _of(emitted, PROBE_EVENT)[0]
    assert record["census"] is True
    assert isinstance(record["gc_objects"], int) and record["gc_objects"] > 0
    assert record["census_ms"] is not None


def test_listener_never_raises_on_odd_record(tmp_path, emitted, restore_tracemalloc):
    _, probe = _install({ENV_ENABLED: "1", ENV_EVERY: "100"}, tmp_path, emitted)
    assert probe is not None
    try:
        probe.observe_terminal(object())  # 没有 run_id
        probe.observe_terminal(None)
    finally:
        probe.stop()
    assert [record["run_id"] for record in _of(emitted, PROBE_RSS_EVENT)] == [None, None]


def test_emit_failure_never_reaches_the_caller(tmp_path, restore_tracemalloc):
    def emit(fields):
        raise RuntimeError("sink is down")

    registry, probe = _install(
        {ENV_ENABLED: "1", ENV_EVERY: "1"}, tmp_path, [], emit=emit
    )
    assert probe is not None
    try:
        registry.fire("run-a")  # 不许抛
        probe.wait_idle(timeout=30)
    finally:
        probe.stop()
    assert probe.terminals == 1


def test_not_started_probe_ignores_terminals(tmp_path, emitted):
    probe = MemoryProbe(snapshot_dir=tmp_path, emit=emitted.append)
    assert probe.record_terminal("run-a") is None
    assert emitted == []
    assert probe.terminals == 0


# --------------------------------------------------------------------------
# 5. 快照文件有界
# --------------------------------------------------------------------------
def test_snapshot_files_are_bounded(tmp_path, emitted, restore_tracemalloc):
    registry, probe = _install(
        {ENV_ENABLED: "1", ENV_EVERY: "1", ENV_KEEP: "3", ENV_FRAMES: "3"},
        tmp_path,
        emitted,
    )
    assert probe is not None
    try:
        for ordinal in range(6):
            registry.fire(f"run-{ordinal}")
            # 采样被合并会少写快照，所以逐条等它落地。
            assert _await(lambda n=ordinal: len(_of(emitted, PROBE_EVENT)) == n + 1)
    finally:
        probe.wait_idle(timeout=30)
        probe.stop()

    names = sorted(path.name for path in (tmp_path / "memory-probe").iterdir())
    assert names == ["turn-4.snap", "turn-5.snap", "turn-6.snap"]
    assert [record["snapshot"] for record in _of(emitted, PROBE_EVENT)][-1] == "turn-6.snap"
    reloaded = tracemalloc.Snapshot.load(str(tmp_path / "memory-probe" / "turn-6.snap"))
    assert reloaded.statistics("lineno")


def test_stop_joins_the_sampling_thread(tmp_path, emitted, restore_tracemalloc):
    registry, probe = _install(
        {ENV_ENABLED: "1", ENV_EVERY: "1", ENV_FRAMES: "5"}, tmp_path, emitted
    )
    assert probe is not None
    registry.fire("run-a")
    probe.wait_idle(timeout=30)
    probe.stop()
    assert not [
        thread for thread in threading.enumerate() if thread.name == "memory-probe-sampler"
    ]
    assert probe.started is False


def test_shorten_location_is_stable_and_relative():
    assert shorten_location("/a/b/backend/deskpet/x.py", 12) == "deskpet/x.py:12"
    assert (
        shorten_location("/v/lib/python3.12/site-packages/pkg/y.py", 7) == "pkg/y.py:7"
    )
    assert shorten_location("/one/two/three/four/z.py", 3) == "three/four/z.py:3"
