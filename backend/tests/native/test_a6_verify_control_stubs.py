# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""A6-3 / A6-2 接入同 Run 有界化回执字段(Incident E followup F-E1 / F-E2)。

覆盖三件事:

1. `Evidence.receipt_bound_stats()` 能从 `run_context_snapshot_receipts.
   source_revisions` 读出 `pages_forced` / `groups_trimmed_for_budget` /
   `budget_headroom` / `control_results_stubbed` / `control_result_tokens` /
   `control_stubs_forced`, 并且**字段缺失**(Host 构建早于 F-E2, 例如 attempt 8)
   时按缺失处理: 计数记 0、`budget_headroom` 记 None、不崩溃。
2. `Evidence.control_elision_stats()` 只认 `metadata.source ==
   primary_control_result_elided_v1`, 不认正文里出现同名字符串的回声。
3. A6-3 新增判据: native.log 里出现前台 Run 的 `sdk_context_budget_exceeded`
   即 FAIL —— 即使 input_tokens 增长比完全达标; 且说明里必须带 stub/force 计数。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

_VERIFY = Path(__file__).resolve().parents[3] / "scripts" / "native" / "a6_verify.py"


def _load():
    spec = importlib.util.spec_from_file_location("a6_verify_control_stubs_uut", _VERIFY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


a6 = _load()

_FG_RUN = "product-sdk-foreground"
_BG_RUN = "product-sdk-background"

# 与 attempt 8 同形状: window=32000 -> tier 8192 -> effective_input_budget=26752
_WINDOW = 32000
_EXCEEDED_LINE = (
    '{"event": "sdk_context_budget_exceeded planned=27015 effective=26752 protected=7771'
    ' tool_schemas=5898 groups=1 ratio=1.65 protected_messages=1873 open_group=19244'
    ' full_trim=True", "level": "warning",'
    ' "logger": "deskpet.sdk_adapters.context_authority"}'
)
_KERNEL_LINE = (
    '{"error_message": "sdk_context_budget_exceeded", "error_type": "ContextBudgetExceeded",'
    ' "event": "sdk_run_driver_failed", "logger": "simple_harness.runtime.kernel"}'
)


def _closure_line(sdk_run_id: str) -> str:
    return json.dumps(
        {
            "event": "foreground.runtime.closure_settled",
            "host_run_id": "h-1",
            "sdk_run_id": sdk_run_id,
            "status": "clean",
        }
    )


def _make_db(path: str, schema: list[str], inserts: list[tuple[str, tuple]]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        for stmt in schema:
            conn.execute(stmt)
        for sql, params in inserts:
            conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _evidence(
    tmp_path: Path,
    *,
    receipts: list[tuple[str, int, dict]],
    attempts: list[int] | None = None,
    requests: list[dict] | None = None,
    log_lines: list[str] | None = None,
    foreground: list[str] | None = None,
) -> "a6.Evidence":
    """搭一份最小证据目录(只含本组用例要读的表), 返回 `Evidence`。"""
    root = str(tmp_path)
    data = os.path.join(root, "userdata", "data")
    foreground = foreground if foreground is not None else [_FG_RUN]
    attempts = attempts or []
    requests = requests or []

    _make_db(
        os.path.join(data, "state.db"),
        [
            "create table foreground_run_heads(host_run_id text primary key,"
            " current_state text, sdk_run_id text, updated_at real)",
            "create table run_context_snapshot_receipts(snapshot_id text primary key,"
            " sdk_run_id text, provider_turn_ordinal integer,"
            " source_revisions_json text, payload_hash text,"
            " expected_request_fingerprint text, recorded_at real)",
            "create table sdk_provider_attempt_audit(invocation_id text primary key,"
            " snapshot_id text, model_id text, provider_id text, context_window integer,"
            " effective_ceiling integer, input_tokens integer, total_tokens integer,"
            " state text, settled_at real)",
        ],
        [
            (
                "insert into foreground_run_heads values (?,?,?,?)",
                (f"host-{n}", "COMPLETED", run, 100.0 + n),
            )
            for n, run in enumerate(foreground)
        ]
        + [
            (
                "insert into run_context_snapshot_receipts values (?,?,?,?,?,?,?)",
                (
                    f"ctx-snap:{run}:{ordinal}",
                    run,
                    ordinal,
                    json.dumps({"source_revisions": rev}),
                    "fp",
                    "fp",
                    100.0,
                ),
            )
            for run, ordinal, rev in receipts
        ]
        + [
            (
                "insert into sdk_provider_attempt_audit values (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"inv{n}",
                    "snap",
                    "deepseek-v4-flash",
                    "primary",
                    _WINDOW,
                    0,
                    tokens,
                    tokens + 10,
                    "succeeded",
                    100.0 + n,
                ),
            )
            for n, tokens in enumerate(attempts)
        ],
    )
    _make_db(
        os.path.join(data, "simple-harness-sdk", "execution-v6.sqlite3"),
        [
            "create table provider_invocations(invocation_id text primary key, run_id text,"
            " request_id text, request_fingerprint text, request_json text, state text,"
            " claimed_at real, response_json text, usage_json text)",
            "create table execution_effects(effect_id text primary key, run_id text,"
            " tool_name text, state text, result_json text, arguments_json text)",
        ],
        [
            (
                "insert into provider_invocations values (?,?,?,?,?,?,?,?,?)",
                (
                    f"inv{n}",
                    _FG_RUN,
                    f"req{n}",
                    f"fp{n}",
                    json.dumps(request, ensure_ascii=False),
                    "succeeded",
                    100.0 + n,
                    "{}",
                    "{}",
                ),
            )
            for n, request in enumerate(requests)
        ],
    )
    with open(os.path.join(root, "native.log"), "w", encoding="utf-8") as fh:
        fh.write(
            '{"event": "model_context_resolved model=deepseek-v4-flash'
            ' window=%d source=global"}\n' % _WINDOW
        )
        for line in log_lines or []:
            fh.write(line + "\n")
    return a6.Evidence(root)


def _tool_message(*, elided: bool, content: str = "{}") -> dict:
    return {
        "role": "tool",
        "name": "tool_search",
        "call_id": "call-1",
        "content": content,
        "metadata": {"source": a6.CONTROL_ELISION_MARKER} if elided else {},
    }


def _healthy_receipts(run: str, *, control: bool) -> list[tuple[str, int, dict]]:
    """一个「装得下」的 Run 的 8 条回执; `control=False` 模拟 F-E2 之前的 Host。"""
    out: list[tuple[str, int, dict]] = []
    for ordinal in range(1, 9):
        rev = {
            "budget_tier": 8192,
            "causal_groups": 2,
            "trimmed_groups": 1,
            "groups_trimmed_for_budget": 1 if ordinal > 4 else 0,
            "budget_headroom": 7000 - 300 * ordinal,
            "current_tool_pages": ordinal % 3,
            "pages_forced": 1 if ordinal in (5, 6) else 0,
        }
        if control:
            rev.update(
                control_results_stubbed=2 if ordinal >= 7 else 0,
                control_result_tokens=4301 if ordinal >= 7 else 8829,
                control_stubs_forced=1 if ordinal == 8 else 0,
            )
        out.append((run, ordinal, rev))
    return out


# --------------------------------------------------------------- 提取


def test_receipt_bound_stats_aggregates_per_run_and_tolerates_absent_fields(tmp_path):
    """两个 Run: 一个带 F-E2 字段, 一个不带 —— 聚合正确且缺字段不算 0 样本。"""
    receipts = _healthy_receipts(_FG_RUN, control=True) + _healthy_receipts(
        _BG_RUN, control=False
    )
    ev = _evidence(tmp_path, receipts=receipts)
    try:
        stats = ev.receipt_bound_stats()
    finally:
        ev.close()

    assert stats["receipt_runs"] == 2
    assert stats["receipts_with_source_revisions"] == 16
    # 只有一半回执带 control_* 字段 —— 这正是「Host 早于 F-E2」的可辨识信号。
    assert stats["receipts_with_control_fields"] == 8
    assert stats["receipts_with_pages_forced_field"] == 16
    # pages_forced: 每个 Run 峰值 1、合计 2, 两个 Run 合计 4。
    assert stats["pages_forced_max_per_run"] == 1
    assert stats["pages_forced_sum"] == 4
    assert stats["runs_with_pages_forced"] == 2
    assert stats["groups_trimmed_for_budget_max_per_run"] == 1
    assert stats["groups_trimmed_for_budget_sum"] == 8
    assert stats["budget_headroom_min"] == 7000 - 300 * 8
    assert stats["current_tool_pages_max_per_run"] == 2
    # control_* 只有一个 Run 有: 2 条 × 2 轮 = 4; tokens 取峰值; forced 合计 1。
    assert stats["control_results_stubbed_sum"] == 4
    assert stats["control_result_tokens_max"] == 8829
    assert stats["control_stubs_forced_sum"] == 1
    assert stats["runs_with_control_stubs"] == 1


def test_receipt_bound_stats_on_pre_f_e2_host_is_zero_not_a_crash(tmp_path):
    """attempt 8 形状: 回执里根本没有 control_* 三个键。"""
    ev = _evidence(tmp_path, receipts=_healthy_receipts(_FG_RUN, control=False))
    try:
        stats = ev.receipt_bound_stats()
    finally:
        ev.close()
    assert stats["receipts_with_control_fields"] == 0
    assert stats["control_results_stubbed_sum"] == 0
    assert stats["control_result_tokens_max"] == 0
    assert stats["control_stubs_forced_sum"] == 0
    # E 的字段仍然读得到 —— 缺的只是 F-E2 那三个。
    assert stats["pages_forced_sum"] == 2
    json.dumps(stats)  # 必须仍可进 JSON 报告


def test_receipt_bound_stats_without_any_receipt_returns_none_headroom(tmp_path):
    ev = _evidence(tmp_path, receipts=[])
    try:
        stats = ev.receipt_bound_stats()
        combined = ev.same_run_bound_numbers()
    finally:
        ev.close()
    assert stats["receipt_runs"] == 0
    assert stats["budget_headroom_min"] is None
    assert combined["requests_with_control_elision_notice"] == 0
    assert combined["sdk_context_budget_exceeded_total"] == 0


def test_control_elision_notices_come_from_metadata_not_from_echoed_text(tmp_path):
    """正文里出现同名字符串不算 —— 只认 `metadata.source`。"""
    real = {
        "messages": [
            {"role": "system", "content": "x"},
            _tool_message(elided=True),
            _tool_message(elided=True),
            _tool_message(elided=False),
        ]
    }
    echo = {
        "messages": [
            # 回声: 字符串出现在正文里, metadata 干净 -> 不计。
            _tool_message(
                elided=False,
                content=json.dumps({"kind": a6.CONTROL_ELISION_MARKER}),
            )
        ]
    }
    clean = {"messages": [{"role": "user", "content": "hi"}]}
    ev = _evidence(tmp_path, receipts=[], requests=[real, echo, clean])
    try:
        stats = ev.control_elision_stats()
    finally:
        ev.close()
    assert stats["requests_with_control_elision_notice"] == 1
    assert stats["control_elision_notices_in_requests"] == 2
    assert stats["runs_with_control_elision_notice"] == 1


def test_budget_exceeded_is_attributed_to_the_next_run_id_in_the_log(tmp_path):
    """溢出行本身不带 run id: 前台 / 其他泳道 / 归属不到, 三种都要分得开。"""
    ev = _evidence(
        tmp_path,
        receipts=[],
        log_lines=[
            _EXCEEDED_LINE,
            _KERNEL_LINE,  # 同一次溢出的第二条行文, 不得重复计数
            _closure_line(_FG_RUN),
            _EXCEEDED_LINE,
            _closure_line(_BG_RUN),
        ],
        foreground=[_FG_RUN],
    )
    try:
        stats = ev.budget_exceeded_events()
    finally:
        ev.close()
    assert stats["sdk_context_budget_exceeded_total"] == 2
    assert stats["sdk_context_budget_exceeded_foreground"] == 1
    assert stats["sdk_context_budget_exceeded_other_lane"] == 1
    assert stats["sdk_context_budget_exceeded_unattributed"] == 0
    assert stats["sdk_context_budget_exceeded_samples"][0]["planned"] == 27015


def test_budget_exceeded_without_any_run_id_counts_as_unattributed(tmp_path):
    ev = _evidence(tmp_path, receipts=[], log_lines=[_EXCEEDED_LINE, _KERNEL_LINE])
    try:
        stats = ev.budget_exceeded_events()
    finally:
        ev.close()
    assert stats["sdk_context_budget_exceeded_total"] == 1
    assert stats["sdk_context_budget_exceeded_unattributed"] == 1
    assert stats["sdk_context_budget_exceeded_foreground"] == 0


# --------------------------------------------------------------- A6-3 判据


def _a6_3(ev) -> "a6.Item":
    return a6.item_a6_3(ev, ev.budget_profile())


_HEALTHY_TOKENS = [7000 + 50 * n for n in range(20)]  # 后8/前8 峰值比 ≈ 1.13


def test_a6_3_passes_when_nothing_exceeded_and_note_flags_the_pre_f_e2_host(tmp_path):
    """基线: 旧判据该 PASS 就仍然 PASS, 说明里追加 stub/force 计数。"""
    ev = _evidence(
        tmp_path,
        receipts=_healthy_receipts(_FG_RUN, control=False),
        attempts=_HEALTHY_TOKENS,
        requests=[{"messages": [{"role": "user", "content": "hi"}]}],
    )
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert item.verdict == a6.PASS, item.reason
    assert "同 Run 有界化" in item.reason
    assert "pages_forced 合计 2" in item.reason
    assert "control_results_stubbed 合计 0" in item.reason
    assert "早于 F-E2" in item.reason
    assert item.numbers["pages_forced_sum"] == 2
    assert item.numbers["budget_headroom_min"] == 4600
    assert item.numbers["sdk_context_budget_exceeded_total"] == 0


def test_a6_3_fails_on_a_foreground_budget_exceeded_even_with_a_healthy_ratio(tmp_path):
    """新判据: 增长比达标、预算内, 但前台 Run 撑爆过 -> FAIL。"""
    ev = _evidence(
        tmp_path,
        receipts=_healthy_receipts(_FG_RUN, control=True),
        attempts=_HEALTHY_TOKENS,
        requests=[{"messages": [{"role": "user", "content": "hi"}]}],
        log_lines=[_EXCEEDED_LINE, _KERNEL_LINE, _closure_line(_FG_RUN)],
    )
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL, item.reason
    assert "sdk_context_budget_exceeded" in item.reason
    # 说明必须带 stub/force 计数(委派方的硬要求)。
    assert "control_stubs_forced 合计 1" in item.reason
    assert "control_results_stubbed 合计 4" in item.reason
    assert "pages_forced 合计 2" in item.reason
    assert item.numbers["sdk_context_budget_exceeded_foreground"] == 1


def test_a6_3_ignores_a_budget_exceeded_that_belongs_to_another_lane(tmp_path):
    """溢出归属到非前台 Run 时不改判 —— 否则后台泳道会污染 A6-3。"""
    ev = _evidence(
        tmp_path,
        receipts=_healthy_receipts(_FG_RUN, control=False),
        attempts=_HEALTHY_TOKENS,
        requests=[{"messages": [{"role": "user", "content": "hi"}]}],
        log_lines=[_EXCEEDED_LINE, _closure_line(_BG_RUN)],
        foreground=[_FG_RUN],
    )
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert item.verdict == a6.PASS, item.reason
    assert item.numbers["sdk_context_budget_exceeded_other_lane"] == 1


def test_a6_3_fails_before_the_inconclusive_branches(tmp_path):
    """样本不全(从未整组丢弃)本会记 INCONCLUSIVE, 但直接观测到的溢出优先。"""
    receipts = [
        (
            _FG_RUN,
            ordinal,
            {"budget_tier": 8192, "causal_groups": 1, "trimmed_groups": 0,
             "groups_trimmed_for_budget": 0, "budget_headroom": 100,
             "current_tool_pages": 0, "pages_forced": 0},
        )
        for ordinal in range(1, 4)
    ]
    ev = _evidence(
        tmp_path,
        receipts=receipts,
        attempts=[7000, 7100, 7200],
        requests=[{"messages": [{"role": "user", "content": "hi"}]}],
        log_lines=[_EXCEEDED_LINE, _closure_line(_FG_RUN)],
    )
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL, item.reason
    assert "有界性不成立" in item.reason


def test_a6_3_numbers_keep_the_old_keys_first_so_the_markdown_brief_is_unchanged(tmp_path):
    """Markdown 表的「依据数字」列只取前 4 个键 —— 新字段必须追加在末尾。"""
    ev = _evidence(
        tmp_path,
        receipts=_healthy_receipts(_FG_RUN, control=True),
        attempts=_HEALTHY_TOKENS,
        requests=[{"messages": [{"role": "user", "content": "hi"}]}],
    )
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert list(item.numbers)[:4] == [
        "attempt_rows",
        "max_input_tokens",
        "first8_max_input_tokens",
        "last8_max_input_tokens",
    ]
    brief = a6._numbers_brief(item.numbers)
    assert brief.startswith("attempt_rows=20")
    assert "control_" not in brief
    json.dumps(item.numbers, ensure_ascii=False)


def test_a6_2_carries_the_same_bound_numbers_without_changing_its_verdict(tmp_path):
    """A6-2 只补数字, 判据不动。"""
    ev = _evidence(
        tmp_path,
        receipts=_healthy_receipts(_FG_RUN, control=True),
        attempts=_HEALTHY_TOKENS,
        requests=[{"messages": [_tool_message(elided=True)]}],
    )
    try:
        item = a6.item_a6_2(ev)
    finally:
        ev.close()
    # 没有大 tool result、也没有 context_page_in, 轮次不足 -> 老判据的 INCONCLUSIVE。
    assert item.verdict == a6.INCONCLUSIVE
    assert item.numbers["control_results_stubbed_sum"] == 4
    assert item.numbers["requests_with_control_elision_notice"] == 1
    assert list(item.numbers)[0] == "distinct_page_reference_ids"


def test_selftest_still_runs_end_to_end():
    """脚本自检覆盖全部 18 项 + Markdown 渲染, 用它兜底不回归。"""
    assert a6.selftest() == 0
