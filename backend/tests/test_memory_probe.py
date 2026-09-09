# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 X-3：原生按站点内存探针（`observability/memory_probe.py`）。

三件必须成立的事：

1. **默认关闭 = 零成本**：环境变量没显式打开时 `install_memory_probe` 返回
   ``None``、**不注册**任何终态监听器、不启动 `tracemalloc`、不产出日志行；
2. **打开时按 N 个终态出一行**：字段齐全、第一条是 baseline、
   `top_sites` / `type_census_delta` 只有 `file:line`/类型名与数字（无载荷）；
3. **快照文件有界**：`<snapshot_dir>/turn-<n>.snap` 只保留最近 keep 份。
"""
from __future__ import annotations

import re
import tracemalloc

import pytest

from observability.memory_probe import (
    ENV_CENSUS,
    ENV_ENABLED,
    ENV_EVERY,
    ENV_FRAMES,
    ENV_KEEP,
    MemoryProbe,
    build_memory_probe,
    install_memory_probe,
    shorten_location,
)

_SITE = re.compile(r"^[^\s]+:\d+ -?\d+\.\d+ -?\d+$")
_CENSUS = re.compile(r"^[^\s]+ -?\d+$")


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


def _install(env, tmp_path, emitted, registry=None):
    registry = registry if registry is not None else _FakeRegistry()
    probe = install_memory_probe(
        registry=registry,
        snapshot_dir=tmp_path / "memory-probe",
        emit=lambda fields: emitted.append(dict(fields)),
        env=env,
    )
    return registry, probe


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


# --------------------------------------------------------------------------
# 2. 打开时每 N 个终态一行
# --------------------------------------------------------------------------
def test_probe_emits_every_n_terminals(tmp_path, emitted, restore_tracemalloc):
    registry, probe = _install(
        {ENV_ENABLED: "1", ENV_EVERY: "3", ENV_FRAMES: "5"}, tmp_path, emitted
    )
    assert probe is not None and probe.started
    assert len(registry.listeners) == 1
    try:
        for ordinal in range(7):
            registry.fire(f"run-{ordinal}")
    finally:
        probe.stop()

    assert [record["terminal_seq"] for record in emitted] == [3, 6]
    assert [record["probe_seq"] for record in emitted] == [1, 2]
    assert [record["run_id"] for record in emitted] == ["run-2", "run-5"]
    assert [record["baseline"] for record in emitted] == [True, False]
    assert {record["every"] for record in emitted} == {3}
    assert probe.terminals == 7 and probe.probes == 2


def test_probe_line_has_expected_keys(tmp_path, emitted, restore_tracemalloc):
    registry, probe = _install({ENV_ENABLED: "yes"}, tmp_path, emitted)
    assert probe is not None
    try:
        registry.fire("run-a")
        ballast = [bytearray(4096) for _ in range(256)]
        registry.fire("run-b")
        assert len(ballast) == 256
    finally:
        probe.stop()

    assert len(emitted) == 2
    for record in emitted:
        assert set(record) == {
            "probe_seq",
            "terminal_seq",
            "every",
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
            "sample_ms",
        }
        assert isinstance(record["tracemalloc_current_kb"], int)
        assert isinstance(record["gc_objects"], int) and record["gc_objects"] > 0
        assert len(record["top_sites"]) <= 15
        assert len(record["type_census_delta"]) <= 15
    second = emitted[1]
    assert second["tracemalloc_delta_kb"] is not None
    assert second["rss_delta_kb"] is not None or second["rss_kb"] is None


def test_probe_lines_carry_no_payload(tmp_path, emitted, restore_tracemalloc):
    registry, probe = _install({ENV_ENABLED: "1"}, tmp_path, emitted)
    assert probe is not None
    try:
        registry.fire("run-a")
        secret = ["S3CRET-PAYLOAD-" + str(index) for index in range(512)]
        registry.fire("run-b")
        assert len(secret) == 512
    finally:
        probe.stop()

    for record in emitted:
        for entry in record["top_sites"]:
            assert _SITE.match(entry), entry
        for entry in record["type_census_delta"]:
            assert _CENSUS.match(entry), entry
        assert "S3CRET-PAYLOAD" not in repr(record)


def test_census_can_be_disabled(tmp_path, emitted, restore_tracemalloc):
    registry, probe = _install({ENV_ENABLED: "1", ENV_CENSUS: "0"}, tmp_path, emitted)
    assert probe is not None
    try:
        registry.fire("run-a")
    finally:
        probe.stop()
    assert emitted[0]["gc_objects"] is None
    assert emitted[0]["type_census_delta"] == []


def test_listener_never_raises_on_odd_record(tmp_path, emitted, restore_tracemalloc):
    _, probe = _install({ENV_ENABLED: "1"}, tmp_path, emitted)
    assert probe is not None
    try:
        probe.observe_terminal(object())  # 没有 run_id
        probe.observe_terminal(None)
    finally:
        probe.stop()
    assert [record["run_id"] for record in emitted] == [None, None]


def test_not_started_probe_ignores_terminals(tmp_path, emitted):
    probe = MemoryProbe(snapshot_dir=tmp_path, emit=emitted.append)
    assert probe.record_terminal("run-a") is None
    assert emitted == []
    assert probe.terminals == 0


# --------------------------------------------------------------------------
# 3. 快照文件有界
# --------------------------------------------------------------------------
def test_snapshot_files_are_bounded(tmp_path, emitted, restore_tracemalloc):
    registry, probe = _install(
        {ENV_ENABLED: "1", ENV_KEEP: "3", ENV_FRAMES: "3"}, tmp_path, emitted
    )
    assert probe is not None
    try:
        for ordinal in range(6):
            registry.fire(f"run-{ordinal}")
    finally:
        probe.stop()

    names = sorted(path.name for path in (tmp_path / "memory-probe").iterdir())
    assert names == ["turn-4.snap", "turn-5.snap", "turn-6.snap"]
    assert [record["snapshot"] for record in emitted][-1] == "turn-6.snap"
    reloaded = tracemalloc.Snapshot.load(str(tmp_path / "memory-probe" / "turn-6.snap"))
    assert reloaded.statistics("lineno")


def test_shorten_location_is_stable_and_relative():
    assert shorten_location("/a/b/backend/deskpet/x.py", 12) == "deskpet/x.py:12"
    assert (
        shorten_location("/v/lib/python3.12/site-packages/pkg/y.py", 7) == "pkg/y.py:7"
    )
    assert shorten_location("/one/two/three/four/z.py", 3) == "three/four/z.py:3"
