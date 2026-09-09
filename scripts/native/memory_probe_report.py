# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 X-3：把原生 `native.log` 里的 `memory.probe` 行读成每回合增长表。

用法::

    python scripts/native/memory_probe_report.py --log <evidence>/native.log
    python scripts/native/memory_probe_report.py --log native.log --top 25 \\
        --snapshot-old <userdata>/memory-probe/turn-4.snap \\
        --snapshot-new <userdata>/memory-probe/turn-18.snap

三张表：

1. **每回合增长**：RSS / tracemalloc current / gc 对象数，及其相对上一次探针的增量；
2. **累计增长最大的分配站点**：把每次探针的 `top_sites` 增量按 `file:line` 求和
   （第一条 `baseline` 探针不计入，它是绝对量不是增量）；
3. **累计增长最大的 gc 类型**。

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


def read_probe_lines(path: Path) -> list[dict[str, Any]]:
    """`native.log` 一行一条 JSON（structlog JSONRenderer）；非 JSON 行直接跳过。"""

    probes: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line or PROBE_EVENT not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("event") == PROBE_EVENT:
                probes.append(record)
    probes.sort(key=lambda record: _int(record.get("probe_seq")) or 0)
    return probes


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
) -> str:
    blocks: list[str] = []
    if not probes:
        return (
            "no memory.probe lines found — run the journey with "
            "scripts/native/launch_native_candidate.py --memory-probe"
        )
    every = probes[0].get("every")
    blocks.append(
        f"memory.probe records: {len(probes)}  "
        f"every={every}  terminals_covered={probes[-1].get('terminal_seq')}"
    )
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
    probes = read_probe_lines(args.log.resolve(strict=True))
    print(render(probes, top=max(1, args.top), snapshots=snapshots))
    return 0 if probes else 1


if __name__ == "__main__":
    sys.exit(main())
