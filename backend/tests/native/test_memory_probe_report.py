# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 X-3：`scripts/native/memory_probe_report.py` 能把 native.log 读成增长表。

夹具日志刻意混入非 `memory.probe` 行、非 JSON 行与乱序的 `probe_seq`，
因为真实 native.log 就是这三样东西的混合体。
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tracemalloc
from pathlib import Path

import pytest

_REPORT = Path(__file__).resolve().parents[3] / "scripts" / "native" / "memory_probe_report.py"


def _load():
    spec = importlib.util.spec_from_file_location("memory_probe_report_uut", _REPORT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


report = _load()


def _probe(seq, *, terminal, rss, rss_delta, traced, traced_delta, sites, types, baseline=False):
    return {
        "event": "memory.probe",
        "probe_event": "memory.probe",
        "level": "info",
        "timestamp": f"2026-09-09T10:0{seq}:00Z",
        "probe_seq": seq,
        "terminal_seq": terminal,
        "every": 1,
        "baseline": baseline,
        "run_id": f"run-{seq}",
        "rss_kb": rss,
        "rss_delta_kb": rss_delta,
        "rss_max_kb": rss,
        "tracemalloc_current_kb": traced,
        "tracemalloc_peak_kb": traced + 100,
        "tracemalloc_delta_kb": traced_delta,
        "gc_objects": 1_000_000 + seq * 1000,
        "gc_counts": [7, 1, 0],
        "top_sites": sites,
        "type_census_delta": types,
        "snapshot": f"turn-{seq}.snap",
        "sample_ms": 12.5,
        "frames": 5,
        "census": True,
    }


def _rss(terminal, *, rss, rss_delta, sampling="idle", skipped_total=0):
    """X3-F4 起每个终态一条的廉价行；发射端把类别放在 `probe_event` 里。"""

    return {
        "event": "memory.probe",
        "probe_event": "memory.probe.rss",
        "level": "info",
        "terminal_seq": terminal,
        "every": 1,
        "run_id": f"run-{terminal}",
        "rss_kb": rss,
        "rss_delta_kb": rss_delta,
        "rss_max_kb": rss,
        "tracemalloc_current_kb": 1000 * terminal,
        "tracemalloc_peak_kb": 1000 * terminal + 50,
        "sampling": sampling,
        "skipped_total": skipped_total,
        "listener_us": 41.5,
    }


def _skipped(terminal, *, skipped_total):
    return {
        "event": "memory.probe",
        "probe_event": "memory.probe.skipped",
        "level": "info",
        "terminal_seq": terminal,
        "run_id": f"run-{terminal}",
        "skipped_total": skipped_total,
        "reason": "sample_in_flight",
    }


@pytest.fixture
def fixture_log(tmp_path) -> Path:
    rows = [
        '{"event": "foreground.runtime.bound", "level": "info"}',
        "INFO not-json at all { oops",
        json.dumps(
            _probe(2, terminal=2, rss=520000, rss_delta=260000, traced=90000,
                   traced_delta=60000,
                   sites=["deskpet/agent/turn.py:41 40960.0 12",
                          "pkg/short_horizon.py:705 8.0 1"],
                   types=["bytes 1200", "dict 300"])
        ),
        '{"event": "memory.probe.started", "level": "info", "every": 1}',
        json.dumps(
            _probe(1, terminal=1, rss=260000, rss_delta=None, traced=30000,
                   traced_delta=None,
                   sites=["deskpet/agent/turn.py:41 20000.0 8"],
                   types=["dict 900"], baseline=True)
        ),
        json.dumps(
            _probe(3, terminal=3, rss=790000, rss_delta=270000, traced=152000,
                   traced_delta=62000,
                   sites=["deskpet/agent/turn.py:41 41984.0 13",
                          "pkg/short_horizon.py:705 8.0 1"],
                   types=["bytes 1300"])
        ),
        '{"event": "sdk.run.terminal", "level": "info"}',
        json.dumps(_rss(2, rss=520000, rss_delta=260000, sampling="queued")),
        json.dumps(_rss(1, rss=260000, rss_delta=None, sampling="queued")),
        json.dumps(_rss(3, rss=790000, rss_delta=270000, sampling="skipped",
                        skipped_total=1)),
        json.dumps(_skipped(3, skipped_total=1)),
    ]
    path = tmp_path / "native.log"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_reads_and_orders_probe_lines(fixture_log):
    probes = report.read_probe_lines(fixture_log)
    assert [record["probe_seq"] for record in probes] == [1, 2, 3]
    assert probes[0]["baseline"] is True


def test_growth_table_shows_per_probe_deltas(fixture_log):
    rows = report.growth_table(report.read_probe_lines(fixture_log))
    assert rows[0].split() == [
        "seq", "term", "rss_kb", "Δrss_kb", "tm_cur_kb", "Δtm_kb",
        "tm_peak_kb", "gc_objects", "Δgc", "ms",
    ]
    assert rows[2].split()[:6] == ["1", "1", "260000", "-", "30000", "-"]
    assert rows[3].split()[:6] == ["2", "2", "520000", "260000", "90000", "60000"]
    assert rows[4].split()[8] == "1000"  # gc 对象数每探针 +1000


def test_summary_excludes_the_baseline_probe(fixture_log):
    lines = report.summary(report.read_probe_lines(fixture_log))
    assert "RSS: 2 deltas, total 530000 KiB" in lines[0]
    assert "mean 265000.0 KiB/probe" in lines[0]
    assert "tracemalloc: 2 deltas, total 122000 KiB" in lines[1]


def test_cumulative_sites_sum_non_baseline_deltas(fixture_log):
    rows = report.cumulative_sites(report.read_probe_lines(fixture_log), 20)
    assert "site" in rows[0]
    body = [row.split() for row in rows[2:]]
    assert body[0][-1] == "deskpet/agent/turn.py:41"
    assert body[0][0] == "82944.0"  # 40960 + 41984，baseline 的 20000 不计
    assert body[0][2] == "2"  # 出现在 2 次探针里
    assert body[0][3] == "25"  # 12 + 13 个对象
    assert body[1][-1] == "pkg/short_horizon.py:705"


def test_cumulative_types(fixture_log):
    rows = report.cumulative_types(report.read_probe_lines(fixture_log), 20)
    body = [row.split() for row in rows[2:]]
    assert body[0] == ["2500", "bytes"]
    assert body[1] == ["300", "dict"]


def test_reads_and_orders_rss_and_skipped_lines(fixture_log):
    buckets = report.read_records(fixture_log)
    assert [record["probe_seq"] for record in buckets[report.PROBE_EVENT]] == [1, 2, 3]
    rss_lines = buckets[report.PROBE_RSS_EVENT]
    assert [record["terminal_seq"] for record in rss_lines] == [1, 2, 3]
    assert [record["sampling"] for record in rss_lines] == ["queued", "queued", "skipped"]
    assert [record["terminal_seq"] for record in buckets[report.PROBE_SKIPPED_EVENT]] == [3]


def test_probe_kind_falls_back_to_event_for_pre_x3f4_logs():
    """X3-F4 之前的日志没有 `probe_event`，只有 `event`，必须还认得出来。"""

    assert report.probe_kind({"event": "memory.probe"}) == report.PROBE_EVENT
    assert report.probe_kind({"event": "memory.probe.started"}) is None
    assert (
        report.probe_kind({"event": "memory.probe", "probe_event": "memory.probe.rss"})
        == report.PROBE_RSS_EVENT
    )


def test_rss_table_shows_the_per_terminal_curve(fixture_log):
    rows = report.rss_table(report.read_records(fixture_log)[report.PROBE_RSS_EVENT])
    assert rows[0].split() == [
        "term", "rss_kb", "Δrss_kb", "rss_max_kb", "tm_cur_kb", "tm_peak_kb",
        "sampling", "skipped", "listener_us", "run_id",
    ]
    assert rows[2].split()[:4] == ["1", "260000", "-", "260000"]
    assert rows[3].split()[:4] == ["2", "520000", "260000", "520000"]
    assert rows[4].split()[6:8] == ["skipped", "1"]


def test_rss_summary_uses_every_terminal(fixture_log):
    lines = report.rss_summary(report.read_records(fixture_log)[report.PROBE_RSS_EVENT])
    assert "per-terminal RSS: 2 deltas, total 530000 KiB" in lines[0]
    assert "listener cost on the event loop: max 41.5 us" in lines[1]


def test_rss_table_is_explicit_when_absent():
    rows = report.rss_table([])
    assert "no memory.probe.rss lines" in rows[-1]


def test_main_accepts_a_log_with_only_rss_lines(tmp_path, capsys):
    """全量采样一次都没落地（被节流/被 KEEP 掐掉）时，每回合曲线仍然要出。"""

    path = tmp_path / "native.log"
    path.write_text(
        "\n".join(
            json.dumps(_rss(term, rss=100000 * term, rss_delta=None if term == 1 else 100000))
            for term in (1, 2, 3)
        )
        + "\n",
        encoding="utf-8",
    )
    assert report.main(["--log", str(path)]) == 0
    printed = capsys.readouterr().out
    assert "0 full, 3 rss, 0 skipped" in printed
    assert "## per-terminal RSS" in printed
    assert "(no non-baseline probes)" in printed


def test_render_and_main(fixture_log, capsys):
    assert report.main(["--log", str(fixture_log), "--top", "5"]) == 0
    printed = capsys.readouterr().out
    assert "memory.probe records: 3 full, 3 rss, 1 skipped" in printed
    assert "frames=5" in printed and "census=True" in printed
    assert "throttled: 1 sample requests dropped" in printed
    assert "## per-terminal RSS" in printed
    assert "## per-terminal summary" in printed
    assert "## per-probe growth" in printed
    assert "## summary" in printed
    assert "## top 5 cumulative growth sites" in printed
    assert "## top 5 cumulative gc type growth" in printed
    assert "deskpet/agent/turn.py:41" in printed


def test_main_reports_missing_probe_lines(tmp_path, capsys):
    path = tmp_path / "native.log"
    path.write_text('{"event": "foreground.runtime.bound"}\n', encoding="utf-8")
    assert report.main(["--log", str(path)]) == 1
    assert "no memory.probe lines found" in capsys.readouterr().out


def test_snapshot_diff_between_two_dumps(tmp_path):
    was_tracing = tracemalloc.is_tracing()
    if not was_tracing:
        tracemalloc.start(5)
    try:
        keep_small = [bytearray(64) for _ in range(8)]
        old = tmp_path / "turn-1.snap"
        tracemalloc.take_snapshot().dump(str(old))
        keep_big = [bytearray(9000) for _ in range(64)]
        new = tmp_path / "turn-2.snap"
        tracemalloc.take_snapshot().dump(str(new))
        rows = report.snapshot_diff(old, new, 10)
        assert len(keep_small) == 8 and len(keep_big) == 64
    finally:
        if not was_tracing:
            tracemalloc.stop()
    assert "Δkb" in rows[0]
    joined = "\n".join(rows)
    assert "test_memory_probe_report.py" in joined
    growth = [float(row.split()[1]) for row in rows[2:]]
    assert max(growth) > 400  # 64 × 9000B ≈ 560 KiB


def test_snapshot_flags_must_come_in_pairs(fixture_log):
    with pytest.raises(SystemExit):
        report.main(["--log", str(fixture_log), "--snapshot-old", str(fixture_log)])


def test_round_trip_real_probe_lines(tmp_path, capsys):
    """生产者（探针）与消费者（本脚本）用同一份格式：真跑一次，再解析回来。"""

    from observability.memory_probe import install_memory_probe

    lines: list[str] = []

    class _Registry:
        def __init__(self) -> None:
            self.listeners: list[object] = []

        def add_terminal_listener(self, listener) -> None:  # type: ignore[no-untyped-def]
            self.listeners.append(listener)

    registry = _Registry()
    was_tracing = tracemalloc.is_tracing()
    probe = install_memory_probe(
        registry=registry,
        snapshot_dir=tmp_path / "memory-probe",
        emit=lambda fields: lines.append(
            json.dumps({"event": "memory.probe", "level": "info", **dict(fields)})
        ),
        env={
            "SIMPLEHARNESS_MEMORY_PROBE": "1",
            "SIMPLEHARNESS_MEMORY_PROBE_EVERY": "1",
            "SIMPLEHARNESS_MEMORY_PROBE_FRAMES": "6",
            "SIMPLEHARNESS_MEMORY_PROBE_CENSUS": "1",
        },
    )
    assert probe is not None
    try:
        ballast: list[bytearray] = []
        for ordinal in range(3):
            ballast.extend(bytearray(20000) for _ in range(64))
            probe.record_terminal(f"run-{ordinal}")
            # 采样在后台线程上；逐条等它落地，否则会被合并成一次。
            assert probe.wait_idle(timeout=30)
        assert len(ballast) == 192
    finally:
        probe.stop()
        if was_tracing and not tracemalloc.is_tracing():
            tracemalloc.start()

    path = tmp_path / "native.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert report.main(["--log", str(path), "--top", "5"]) == 0
    printed = capsys.readouterr().out
    assert "memory.probe records: 3 full, 3 rss, 0 skipped" in printed
    assert "test_memory_probe_report.py:" in printed  # ballast 的分配站点被归因到本文件
    parsed = report.read_probe_lines(path)
    assert [record["probe_seq"] for record in parsed] == [1, 2, 3]
    assert all(report.parse_site(entry) for record in parsed for entry in record["top_sites"])
    assert all(
        report.parse_census(entry) for record in parsed for entry in record["type_census_delta"]
    )

    snaps = sorted((tmp_path / "memory-probe").iterdir(), key=lambda item: item.name)
    assert [item.name for item in snaps] == ["turn-1.snap", "turn-2.snap", "turn-3.snap"]
    diff = report.snapshot_diff(snaps[0], snaps[-1], 5)
    assert any("test_memory_probe_report.py" in row for row in diff[2:])
