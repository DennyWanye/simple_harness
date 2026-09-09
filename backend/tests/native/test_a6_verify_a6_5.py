# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 AC(HM-TO-A6 第 11 次, 2026-09-09): A6-5 canonical 事实核对的两处错口径。

第 11 次头一回把 ≥16 KiB 的逐字目标顶进了 bounded 视图
(`readme_bounded_revisions=1`, `readme_bounded_max_bytes=16383`, 24 条视图修订),
A6-5 却 FAIL 在 `EVIDENCE 视图 event_count=151 与 task_scope_events 实际 176 行不等`。

证据库 `.local-test-evidence/2026-09-09/native-a6-run11/primary-ui-9izlp1ao/
userdata/data/state.db` 里的真实行:

    task_scope_events 按 scope: 870a401b 151 行(seq 1..151), 3a5016d0 25 行(seq 1..25) = 整表 176
    EVIDENCE 视图 4 条(按 created_at):
      870a401b / d2716bdd  event_count=117  source.event_watermark=117
      870a401b / 3de42c24  event_count=125  source.event_watermark=125
      3a5016d0 / 2f8c70c3  event_count= 20  source.event_watermark= 20
      870a401b / 52c9d836  event_count=151  source.event_watermark=151

每条视图的 event_count 都**正好等于自己 source 的 event_watermark**, 一条 canonical
事实都没丢。旧判据错了两次:

  * 跨 scope: 拿最后一条(870a401b 的 151)去和整表 176(两个 scope 之和)比;
  * 跨水位: 视图按读取时物化, 3a5016d0 的视图停在水位 20, 而 seq 21..25
    (`tool_invocation` @1788921011.06 / `tool_invocation` / `context_snapshot` /
    `provider_invocation` / `run_terminal` @1788921031.66)是这次读取当时及之后才落库的
    —— 触发物化的那次读取本身就在生成后续事件。

修的是判据: 逐条视图在**自己的水位**上比同一 `task_scope_id` 的行数。
第一个用例是第 11 次的原形(修复前 FAIL, 修复后 PASS), 后面几个用例守住牙齿:
真丢事实、水位推进而计数倒退, 仍然必须 FAIL。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

_VERIFY = Path(__file__).resolve().parents[3] / "scripts" / "native" / "a6_verify.py"

_BOUNDED_TAIL = "\n…[bounded; details are content-addressed in EVIDENCE]\n"

_SCOPE_LONG = "870a401b-e019-5423-a1ec-8058eab7571b"
_SCOPE_SHORT = "3a5016d0-c365-5cb7-a3ef-62ce8e69bd69"


def _load():
    spec = importlib.util.spec_from_file_location("a6_verify_a6_5_uut", _VERIFY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


a6 = _load()


def _readme_bounded(byte_length: int = 16383) -> str:
    """README 的 bounded 形态: 尾巴是那句固定后缀, 总长压在 16384 以内。"""

    body = "目" * ((byte_length - len(_BOUNDED_TAIL.encode())) // 3)
    text = body + _BOUNDED_TAIL
    pad = byte_length - len(text.encode())
    return ("x" * pad) + text if pad >= 0 else text


def _status_bounded() -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "bounded": True,
            "view_kind": "STATUS",
            "full_content_sha256": "0" * 64,
            "full_byte_length": 20000,
            "details_view": "EVIDENCE",
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _evidence_manifest(
    scope: str, source_id: str, event_count: int, *, watermark: int | None
) -> str:
    payload = {
        "schema_version": 1,
        "task_scope_id": scope,
        "source_id": source_id,
        "source_hash": f"hash-{source_id}",
        "event_count": event_count,
        "logical_group_size": 500,
        "logical_group_count": 1,
        "canonical_archive_block_id": f"archive-{source_id}",
        "logical_groups_root_block_id": f"groups-{source_id}",
        "root_block_id": f"root-{source_id}",
    }
    if watermark is not None:
        payload["event_watermark"] = watermark
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _evidence(
    tmp_path: Path,
    *,
    events: dict[str, int],
    views: list[dict],
    self_describing: bool = True,
    with_sources: bool = True,
    with_event_sequence: bool = True,
) -> "a6.Evidence":
    """events: scope -> 事件行数; views: [{scope, source, count, watermark}] 按顺序物化。"""

    root = str(tmp_path)
    data = os.path.join(root, "userdata", "data")
    os.makedirs(data, exist_ok=True)
    conn = sqlite3.connect(os.path.join(data, "state.db"))
    try:
        if with_event_sequence:
            conn.execute(
                "create table task_scope_events(event_id text primary key, task_scope_id text,"
                " event_sequence integer, event_kind text, payload_json text, occurred_at real)"
            )
        else:
            conn.execute(
                "create table task_scope_events(event_id text primary key, task_scope_id text,"
                " event_kind text, payload_json text, occurred_at real)"
            )
        conn.execute(
            "create table task_scope_read_view_revisions(view_revision_id text primary key,"
            " source_id text, task_scope_id text, view_kind text, content_sha256 text,"
            " content blob, root_block_id text, block_count integer, receipt_hash text,"
            " receipt_json text, created_at real)"
        )
        if with_sources:
            conn.execute(
                "create table task_scope_projection_sources(source_id text primary key,"
                " task_scope_id text, source_sequence integer, event_watermark integer,"
                " renderer_contract_version text, source_hash text, created_at real)"
            )
        for scope, count in events.items():
            for seq in range(1, count + 1):
                if with_event_sequence:
                    conn.execute(
                        "insert into task_scope_events values (?,?,?,?,?,?)",
                        (f"{scope}:{seq}", scope, seq, "harness.tool_invocation", "{}", 100.0 + seq),
                    )
                else:
                    conn.execute(
                        "insert into task_scope_events values (?,?,?,?,?)",
                        (f"{scope}:{seq}", scope, "harness.tool_invocation", "{}", 100.0 + seq),
                    )
        created = 1000.0
        for n, view in enumerate(views):
            if with_sources:
                conn.execute(
                    "insert into task_scope_projection_sources values (?,?,?,?,?,?,?)",
                    (
                        view["source"],
                        view["scope"],
                        n + 1,
                        view["watermark"],
                        "task-scope-views/v2",
                        f"hash-{view['source']}",
                        created,
                    ),
                )
            for kind, content in (
                ("README", view.get("readme") or _readme_bounded()),
                ("STATUS", _status_bounded()),
                (
                    "EVIDENCE",
                    _evidence_manifest(
                        view["scope"],
                        view["source"],
                        view["count"],
                        watermark=view["watermark"] if self_describing else None,
                    ),
                ),
            ):
                created += 1.0
                conn.execute(
                    "insert into task_scope_read_view_revisions values"
                    " (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"{view['source']}:{kind}",
                        view["source"],
                        view["scope"],
                        kind,
                        "0" * 64,
                        content.encode(),
                        None,
                        0,
                        "0" * 64,
                        "{}",
                        created,
                    ),
                )
        conn.commit()
    finally:
        conn.close()
    with open(os.path.join(root, "native.log"), "w", encoding="utf-8") as fh:
        fh.write('{"event": "model_context_resolved window=32000"}\n')
    return a6.Evidence(root)


_RUN11_VIEWS = [
    {"scope": _SCOPE_LONG, "source": "d2716bdd", "count": 117, "watermark": 117},
    {"scope": _SCOPE_LONG, "source": "3de42c24", "count": 125, "watermark": 125},
    {"scope": _SCOPE_SHORT, "source": "2f8c70c3", "count": 20, "watermark": 20},
    {"scope": _SCOPE_LONG, "source": "52c9d836", "count": 151, "watermark": 151},
]
_RUN11_EVENTS = {_SCOPE_LONG: 151, _SCOPE_SHORT: 25}


def test_a6_5_passes_on_the_run11_two_scope_shape(tmp_path):
    """第 11 次原形: 整表 176 行 / 最后一条视图 151, 每条视图在自己水位上都对得上。"""

    ev = _evidence(tmp_path, events=_RUN11_EVENTS, views=_RUN11_VIEWS)
    try:
        item = a6.item_a6_5(ev)
    finally:
        ev.close()
    assert item.verdict == a6.PASS, item.reason
    assert item.numbers["task_scope_events_rows"] == 176
    assert item.numbers["task_scope_events_rows_by_scope"] == {"3a5016d0": 25, "870a401b": 151}
    assert item.numbers["evidence_event_count_values"] == [125, 20, 151]
    assert item.numbers["readme_bounded_revisions"] == 4
    assert item.numbers["readme_bounded_max_bytes"] <= 16384
    checks = item.numbers["evidence_watermark_checks"]
    assert [c["event_watermark"] for c in checks] == [117, 125, 20, 151]
    assert all(c["event_count"] == c["events_at_watermark"] for c in checks)
    # 3a5016d0 的视图停在水位 20, scope 已经有 25 行 —— 这是「读取时物化」的正常滞后。
    lagging = next(c for c in checks if c["task_scope_id"] == "3a5016d0")
    assert (lagging["event_watermark"], lagging["scope_event_rows"]) == (20, 25)


def test_a6_5_still_fails_when_a_view_really_loses_events(tmp_path):
    """负例一: 视图水位 151, 却只报 149 —— 水位下真丢了两条 canonical 事实。"""

    views = [dict(v) for v in _RUN11_VIEWS]
    views[-1]["count"] = 149
    ev = _evidence(tmp_path, events=_RUN11_EVENTS, views=views)
    try:
        item = a6.item_a6_5(ev)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL, item.reason
    assert "canonical 事实丢失" in item.reason
    assert "149" in item.reason and "151" in item.reason


def test_a6_5_fails_when_count_goes_backwards_as_the_watermark_advances(tmp_path):
    """负例二: 同一 scope 水位从 125 推进到 151, event_count 反而降到 120。

    这正是 plan 第 4 节点名的 FAIL 条件——超限拆分后 canonical 事件数变少。
    这里的库没有 `event_sequence`(逐水位比对无从做起), 剩下的这条倒退判据必须还有牙:
    水位只进不退时 event_count 绝不许降。
    """

    views = [dict(v) for v in _RUN11_VIEWS]
    views[-1]["count"] = 120
    ev = _evidence(
        tmp_path,
        events=_RUN11_EVENTS,
        views=views,
        with_event_sequence=False,
    )
    try:
        item = a6.item_a6_5(ev)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL, item.reason
    assert "变少" in item.reason
    assert "125" in item.reason and "151" in item.reason


def test_a6_5_falls_back_to_projection_source_watermark(tmp_path):
    """老库(视图清单没有 event_watermark)回落到 projection source 的水位, 仍能判定。"""

    ev = _evidence(
        tmp_path, events=_RUN11_EVENTS, views=_RUN11_VIEWS, self_describing=False
    )
    try:
        item = a6.item_a6_5(ev)
    finally:
        ev.close()
    assert item.verdict == a6.PASS, item.reason
    assert all(
        c["watermark_from"] == "projection_source"
        for c in item.numbers["evidence_watermark_checks"]
    )


def test_a6_5_is_inconclusive_when_no_watermark_can_be_located(tmp_path):
    """既不自述水位、也没有 projection source: 不许拿整表行数硬比, 记 INCONCLUSIVE。"""

    ev = _evidence(
        tmp_path,
        events=_RUN11_EVENTS,
        views=_RUN11_VIEWS,
        self_describing=False,
        with_sources=False,
    )
    try:
        item = a6.item_a6_5(ev)
    finally:
        ev.close()
    assert item.verdict == a6.INCONCLUSIVE, item.reason
    assert "水位" in item.reason
