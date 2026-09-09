# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 X-3：把原生 `native.log` 里的 `memory.probe` 行读成每回合增长表。

用法::

    python scripts/native/memory_probe_report.py --log <evidence>/native.log
    python scripts/native/memory_probe_report.py --log native.log --top 25 \\
        --snapshot-old <userdata>/memory-probe/turn-4.snap \\
        --snapshot-new <userdata>/memory-probe/turn-18.snap

四张表：

0. **每终态 RSS 曲线**（X3-F4 起）：`memory.probe.rss` 行，每个 Run 终态一条，
   只有 RSS / tracemalloc 计数器，没有 tracemalloc 快照 —— 即使全量采样被节流，
   旅程也一定拿得到逐回合 RSS；
1. **每回合增长**：RSS / tracemalloc current / gc 对象数，及其相对上一次探针的增量；
2. **累计增长最大的分配站点**：把每次探针的 `top_sites` 增量按 `file:line` 求和
   （第一条 `baseline` 探针不计入，它是绝对量不是增量）；
3. **累计增长最大的 gc 类型**（`SIMPLEHARNESS_MEMORY_PROBE_CENSUS=1` 才有）。

`memory.probe.skipped` 行（采样在飞时被丢掉的请求）只在抬头汇总里出现。

`--snapshot-old/--snapshot-new` 给的是探针写下的 `tracemalloc` 快照，用官方
`Snapshot.compare_to` 做一次跨回合的完整比对（日志里的 top 15 之外的站点也能看见）。
"""
from __future__ import annotations

import argparse
import json
import sys
import tracemalloc
from collections import Counter
from pathlib import Path
from typing import Any

PROBE_EVENT = "memory.probe"
PROBE_RSS_EVENT = "memory.probe.rss"
PROBE_SKIPPED_EVENT = "memory.probe.skipped"


def probe_kind(record: dict[str, Any]) -> str | None:
    """探针行的类别。

    发射端 `main.py` 把每条探针记录当 structlog kwargs 打在固定的 `memory.probe`
    事件名下，所以真正的类别在 `probe_event` 字段里；X3-F4 之前的日志没有这个
    字段，退回看 `event`（那时只有全量行）。
    """

    kind = record.get("probe_event") or record.get("event")
    if kind in (PROBE_EVENT, PROBE_RSS_EVENT, PROBE_SKIPPED_EVENT):
        return str(kind)
    return None


def read_records(path: Path) -> dict[str, list[dict[str, Any]]]:
    """`native.log` 一行一条 JSON（structlog JSONRenderer）；非 JSON 行直接跳过。"""

    buckets: dict[str, list[dict[str, Any]]] = {
        PROBE_EVENT: [],
        PROBE_RSS_EVENT: [],
        PROBE_SKIPPED_EVENT: [],
    }
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line or PROBE_EVENT not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            kind = probe_kind(record)
            if kind is not None:
                buckets[kind].append(record)
    buckets[PROBE_EVENT].sort(key=lambda record: _int(record.get("probe_seq")) or 0)
    for kind in (PROBE_RSS_EVENT, PROBE_SKIPPED_EVENT):
        buckets[kind].sort(key=lambda record: _int(record.get("terminal_seq")) or 0)
    return buckets


def read_probe_lines(path: Path) -> list[dict[str, Any]]:
    """只要全量 `memory.probe` 行（老调用方与老日志的兼容入口）。"""

    return read_records(path)[PROBE_EVENT]


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_site(entry: str) -> tuple[str, float, int] | None:
    """`file:line size_kb count` -> (site, size_kb, count)."""

    parts = str(entry).rsplit(" ", 2)
    if len(parts) != 3:
        return None
    site, size_text, count_text = parts
    try:
        return site, float(size_text), int(count_text)
    except ValueError:
        return None


def parse_census(entry: str) -> tuple[str, int] | None:
    parts = str(entry).rsplit(" ", 1)
    if len(parts) != 2:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None


def _cell(value: Any, width: int) -> str:
    return ("-" if value is None else str(value)).rjust(width)


def rss_table(rss_lines: list[dict[str, Any]]) -> list[str]:
    """X3-F4 的廉价逐终态 RSS 行：全量采样被节流时唯一还在的每回合曲线。"""

    header = (
        f"{'term':>5} {'rss_kb':>10} {'Δrss_kb':>10} {'rss_max_kb':>11} "
        f"{'tm_cur_kb':>10} {'tm_peak_kb':>11} {'sampling':>9} {'skipped':>8} "
        f"{'listener_us':>12}  run_id"
    )
    rows = [header, "-" * len(header)]
    for record in rss_lines:
        rows.append(
            " ".join(
                (
                    _cell(_int(record.get("terminal_seq")), 5),
                    _cell(_int(record.get("rss_kb")), 10),
                    _cell(_int(record.get("rss_delta_kb")), 10),
                    _cell(_int(record.get("rss_max_kb")), 11),
                    _cell(_int(record.get("tracemalloc_current_kb")), 10),
                    _cell(_int(record.get("tracemalloc_peak_kb")), 11),
                    _cell(record.get("sampling"), 9),
                    _cell(_int(record.get("skipped_total")), 8),
                    _cell(record.get("listener_us"), 12),
                )
            )
            + "  "
            + str(record.get("run_id"))
        )
    if len(rows) == 2:
        rows.append(
            "(no memory.probe.rss lines - pre-X3-F4 log, or the probe was off)"
        )
    return rows


def rss_summary(rss_lines: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    deltas = [_int(record.get("rss_delta_kb")) for record in rss_lines]
    deltas = [value for value in deltas if value is not None]
    if deltas:
        total = sum(deltas)
        ordered = sorted(deltas)
        lines.append(
            f"per-terminal RSS: {len(deltas)} deltas, total {total} KiB "
            f"({total / 1024:.1f} MiB), mean {total / len(deltas):.1f} KiB/terminal, "
            f"median {ordered[len(ordered) // 2]} KiB, max {max(deltas)} KiB"
        )
    costs = [record.get("listener_us") for record in rss_lines]
    costs = [float(value) for value in costs if isinstance(value, (int, float))]
    if costs:
        lines.append(
            f"listener cost on the event loop: max {max(costs):.1f} us, "
            f"mean {sum(costs) / len(costs):.1f} us over {len(costs)} terminals"
        )
    return lines or ["per-terminal RSS: no samples"]


def growth_table(probes: list[dict[str, Any]]) -> list[str]:
    header = (
        f"{'seq':>4} {'term':>5} {'rss_kb':>10} {'Δrss_kb':>10} "
        f"{'tm_cur_kb':>10} {'Δtm_kb':>9} {'tm_peak_kb':>11} "
        f"{'gc_objects':>11} {'Δgc':>9} {'ms':>7}"
    )
    rows = [header, "-" * len(header)]
    prev_gc: int | None = None
    for record in probes:
        gc_objects = _int(record.get("gc_objects"))
        gc_delta = None if gc_objects is None or prev_gc is None else gc_objects - prev_gc
        prev_gc = gc_objects if gc_objects is not None else prev_gc
        rows.append(
            " ".join(
                (
                    _cell(_int(record.get("probe_seq")), 4),
                    _cell(_int(record.get("terminal_seq")), 5),
                    _cell(_int(record.get("rss_kb")), 10),
                    _cell(_int(record.get("rss_delta_kb")), 10),
                    _cell(_int(record.get("tracemalloc_current_kb")), 10),
                    _cell(_int(record.get("tracemalloc_delta_kb")), 9),
                    _cell(_int(record.get("tracemalloc_peak_kb")), 11),
                    _cell(gc_objects, 11),
                    _cell(gc_delta, 9),
                    _cell(record.get("sample_ms"), 7),
                )
            )
        )
    return rows


def summary(probes: list[dict[str, Any]]) -> list[str]:
    measured = [record for record in probes if not record.get("baseline")]
    lines: list[str] = []
    for label, key in (("RSS", "rss_delta_kb"), ("tracemalloc", "tracemalloc_delta_kb")):
        values = [_int(record.get(key)) for record in measured]
        values = [value for value in values if value is not None]
        if not values:
            lines.append(f"{label}: no delta samples")
            continue
        total = sum(values)
        ordered = sorted(values)
        median = ordered[len(ordered) // 2]
        lines.append(
            f"{label}: {len(values)} deltas, total {total} KiB "
            f"({total / 1024:.1f} MiB), mean {total / len(values):.1f} KiB/probe, "
            f"median {median} KiB, max {max(values)} KiB"
        )
    return lines


def cumulative_sites(probes: list[dict[str, Any]], top: int) -> list[str]:
    sizes: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    seen: Counter[str] = Counter()
    measured = 0
    for record in probes:
        if record.get("baseline"):
            continue
        measured += 1
        for entry in record.get("top_sites") or ():
            parsed = parse_site(entry)
            if parsed is None:
                continue
            site, size_kb, count = parsed
            sizes[site] += size_kb
            counts[site] += count
            seen[site] += 1
    header = f"{'total_kb':>12} {'kb/probe':>10} {'probes':>7} {'objects':>10}  site"
    rows = [header, "-" * len(header)]
    for site, total in sizes.most_common(top):
        rows.append(
            f"{total:12.1f} {total / max(1, measured):10.1f} "
            f"{seen[site]:7d} {counts[site]:10d}  {site}"
        )
    if len(rows) == 2:
        rows.append("(no non-baseline probes)")
    return rows


def cumulative_types(probes: list[dict[str, Any]], top: int) -> list[str]:
    totals: Counter[str] = Counter()
    for record in probes:
        if record.get("baseline"):
            continue
        for entry in record.get("type_census_delta") or ():
            parsed = parse_census(entry)
            if parsed is not None:
                totals[parsed[0]] += parsed[1]
    header = f"{'total':>12}  type"
    rows = [header, "-" * len(header)]
    for name, total in totals.most_common(top):
        rows.append(f"{total:12d}  {name}")
    if len(rows) == 2:
        rows.append("(no census deltas)")
    return rows


def snapshot_diff(old: Path, new: Path, top: int) -> list[str]:
    before = tracemalloc.Snapshot.load(str(old))
    after = tracemalloc.Snapshot.load(str(new))
    header = f"{'size_kb':>12} {'Δkb':>12} {'Δcount':>10}  site"
    rows = [header, "-" * len(header)]
    for stat in after.compare_to(before, "lineno")[:top]:
        rows.append(
            f"{stat.size / 1024:12.1f} {stat.size_diff / 1024:12.1f} "
            f"{stat.count_diff:10d}  {stat.traceback[0].filename}:{stat.traceback[0].lineno}"
        )
    return rows


def render(
    probes: list[dict[str, Any]],
    *,
    top: int,
    snapshots: tuple[Path, Path] | None = None,
    rss_lines: list[dict[str, Any]] | None = None,
    skipped: list[dict[str, Any]] | None = None,
) -> str:
    rss_lines = rss_lines or []
    skipped = skipped or []
    blocks: list[str] = []
    if not probes and not rss_lines:
        return (
            "no memory.probe lines found — run the journey with "
            "scripts/native/launch_native_candidate.py --memory-probe-light"
        )
    head = probes[0] if probes else rss_lines[0]
    every = head.get("every")
    terminals = (rss_lines or probes)[-1].get("terminal_seq")
    blocks.append(
        f"memory.probe records: {len(probes)} full, {len(rss_lines)} rss, "
        f"{len(skipped)} skipped  every={every}  "
        f"frames={head.get('frames')}  census={head.get('census')}  "
        f"terminals_covered={terminals}"
    )
    if skipped:
        last = skipped[-1]
        blocks.append(
            f"throttled: {len(skipped)} sample requests dropped while a sample was "
            f"in flight (skipped_total={last.get('skipped_total')}); "
            f"raise SIMPLEHARNESS_MEMORY_PROBE_EVERY if this is most terminals"
        )
    blocks.append("\n## per-terminal RSS\n" + "\n".join(rss_table(rss_lines)))
    blocks.append("\n## per-terminal summary\n" + "\n".join(rss_summary(rss_lines)))
    blocks.append("\n## per-probe growth\n" + "\n".join(growth_table(probes)))
    blocks.append("\n## summary\n" + "\n".join(summary(probes)))
    blocks.append(
        f"\n## top {top} cumulative growth sites (sum of per-probe deltas)\n"
        + "\n".join(cumulative_sites(probes, top))
    )
    blocks.append(
        f"\n## top {top} cumulative gc type growth\n"
        + "\n".join(cumulative_types(probes, top))
    )
    if snapshots is not None:
        blocks.append(
            f"\n## snapshot diff {snapshots[1].name} vs {snapshots[0].name}\n"
            + "\n".join(snapshot_diff(snapshots[0], snapshots[1], top))
        )
    return "\n".join(blocks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", type=Path, required=True, help="native.log from the journey")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--snapshot-old", type=Path, default=None)
    parser.add_argument("--snapshot-new", type=Path, default=None)
    args = parser.parse_args(argv)
    if (args.snapshot_old is None) != (args.snapshot_new is None):
        parser.error("--snapshot-old and --snapshot-new must be given together")
    snapshots = (
        None
        if args.snapshot_old is None
        else (args.snapshot_old.resolve(strict=True), args.snapshot_new.resolve(strict=True))
    )
    buckets = read_records(args.log.resolve(strict=True))
    probes = buckets[PROBE_EVENT]
    rss_lines = buckets[PROBE_RSS_EVENT]
    print(
        render(
            probes,
            top=max(1, args.top),
            snapshots=snapshots,
            rss_lines=rss_lines,
            skipped=buckets[PROBE_SKIPPED_EVENT],
        )
    )
    return 0 if (probes or rss_lines) else 1


if __name__ == "__main__":
    sys.exit(main())
