# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 W(HM-TO-A6 第 9 次, 2026-09-09): 验证器的两个漏判通道。

1. **A6-3 判据顺序**。第 9 次里 `max_input_tokens=76708` —— 窗口 32000 的 2.40
   倍 —— 只作为 numbers 记了下来, 从未参与判定: 「真实计费超预算」这条判据
   排在 `timeout 轮 -> INCONCLUSIVE` **之后**, 而第 17 轮正好记了 timeout。
   计费侧是**直接观测**, 必须与装配期溢出同级, 排在所有降级分支之前。
   同时 A6-3 现在把 receipt 的 `planned_input_tokens` 与
   `usage.input_tokens` 逐条对照, 低估条数与最差比值直接进 numbers ——
   这一类缺陷以后不可能再无声。

2. **A6-12 不变量**。旧判据按**位次**配对再要求逐 Run 行数相等, 于是把一种
   合法形态判成异常: receipt 在请求组装完成时就提交, 而 Run 可能在那之后、
   provider 调用被 claim 之前就死掉(第 9 次的 ef57d663 / 765be600 都死于
   receipt 之后的 `recall_context_use_authority_stale`)。正确的不变量是
   **方向性**的: 每条调用恰好一条同 Run 同指纹 receipt; 没有调用的 receipt
   只允许是该 Run 的末条, 且每 Run 至多一条; 中段孤儿一律 FAIL。
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
    spec = importlib.util.spec_from_file_location("a6_verify_budget_bypass_uut", _VERIFY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


a6 = _load()

_RUN = "product-sdk-foreground"
_WINDOW = 32000
_EFFECTIVE = 26752


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
    receipts: list[dict],
    invocations: list[dict],
    progress: list[dict] | None = None,
) -> "a6.Evidence":
    root = str(tmp_path)
    data = os.path.join(root, "userdata", "data")
    _make_db(
        os.path.join(data, "state.db"),
        [
            "create table foreground_run_heads(host_run_id text primary key,"
            " current_state text, sdk_run_id text, updated_at real)",
            "create table run_context_snapshot_receipts(snapshot_id text primary key,"
            " sdk_run_id text, provider_turn_ordinal integer, snapshot_revision integer,"
            " source_revisions_json text, payload_hash text,"
            " expected_request_fingerprint text, recorded_at real)",
            "create table sdk_provider_attempt_audit(invocation_id text primary key,"
            " snapshot_id text, model_id text, provider_id text, context_window integer,"
            " effective_ceiling integer, input_tokens integer, total_tokens integer,"
            " state text, settled_at real)",
        ],
        [("insert into foreground_run_heads values (?,?,?,?)", ("h1", "COMPLETED", _RUN, 100.0))]
        + [
            (
                "insert into run_context_snapshot_receipts values (?,?,?,?,?,?,?,?)",
                (
                    f"ctx-snap:{r['run']}:{r['revision']}",
                    r["run"],
                    r["ordinal"],
                    r["revision"],
                    json.dumps(
                        {
                            "source_revisions": {
                                "budget_tier": 8192,
                                "causal_groups": 2,
                                "trimmed_groups": 1,
                                "groups_trimmed_for_budget": 1,
                                "budget_headroom": _EFFECTIVE - r.get("planned", 10_000),
                                "planned_input_tokens": r.get("planned", 10_000),
                            }
                        }
                    ),
                    r["fingerprint"],
                    r["fingerprint"],
                    100.0 + r["revision"],
                ),
            )
            for r in receipts
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
                    inv["input_tokens"],
                    None if inv["input_tokens"] is None else inv["input_tokens"] + 10,
                    "succeeded",
                    100.0 + n,
                ),
            )
            for n, inv in enumerate(invocations)
        ],
    )
    _make_db(
        os.path.join(data, "simple-harness-sdk", "execution-v6.sqlite3"),
        [
            "create table provider_invocations(invocation_id text primary key, run_id text,"
            " request_id text, request_fingerprint text, request_json text, state text,"
            " claimed_at real, response_json text, usage_json text, error_code text)",
            "create table execution_effects(effect_id text primary key, run_id text,"
            " tool_name text, state text, result_json text, arguments_json text)",
        ],
        [
            (
                "insert into provider_invocations values (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"inv{n}",
                    inv["run"],
                    f"{inv['run']}:provider-turn:{inv['ordinal']}",
                    inv["fingerprint"],
                    json.dumps({"messages": [{"role": "user", "content": "hi"}]}),
                    inv.get("state", "succeeded"),
                    100.0 + n,
                    "{}",
                    # 被闸门拦下的那次请求从未发出, 所以没有 usage 可落。
                    None
                    if inv["input_tokens"] is None
                    else json.dumps({"usage": {"input_tokens": inv["input_tokens"],
                                               "output_tokens": 100}}),
                    inv.get("error_code", ""),
                ),
            )
            for n, inv in enumerate(invocations)
        ],
    )
    with open(os.path.join(root, "native.log"), "w", encoding="utf-8") as fh:
        fh.write(
            '{"event": "model_context_resolved model=deepseek-v4-flash'
            ' window=%d source=global"}\n' % _WINDOW
        )
    if progress is not None:
        with open(os.path.join(root, "a6-progress.jsonl"), "w", encoding="utf-8") as fh:
            for row in progress:
                fh.write(json.dumps(row) + "\n")
    return a6.Evidence(root)


def _turn(ordinal: int, *, planned: int, billed: int, run: str = _RUN) -> tuple[dict, dict]:
    fingerprint = f"fp-{run[-8:]}-{ordinal}"
    receipt = {"run": run, "ordinal": ordinal, "revision": ordinal,
               "fingerprint": fingerprint, "planned": planned}
    invocation = {"run": run, "ordinal": ordinal, "fingerprint": fingerprint,
                  "input_tokens": billed}
    return receipt, invocation


def _split(pairs) -> tuple[list[dict], list[dict]]:
    return [p[0] for p in pairs], [p[1] for p in pairs]


# --------------------------------------------------------------- A6-3


def _a6_3(ev) -> "a6.Item":
    return a6.item_a6_3(ev, ev.budget_profile())


_TIMEOUT_PROGRESS = [
    {"kind": "turn", "turn": 17, "status": "timeout", "ts": "2026-09-08T21:30:00Z"}
]


def test_a6_3_fails_on_a_billed_prompt_over_budget_even_with_a_timeout_turn(tmp_path):
    """事故形态: 装配期一次没溢出, 但真实计费到了窗口的 2.4 倍。

    第 17 轮记了 timeout —— 旧顺序会在这里直接降级成 INCONCLUSIVE, 76708
    永远走不到自己的判据。
    """

    pairs = [_turn(n, planned=20_000 + 300 * n, billed=13_000 + 4_500 * n) for n in range(1, 15)]
    receipts, invocations = _split(pairs)
    ev = _evidence(tmp_path, receipts=receipts, invocations=invocations,
                   progress=_TIMEOUT_PROGRESS)
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL, item.reason
    assert "usage.input_tokens" in item.reason
    assert item.numbers["max_billed_input_tokens"] == 76_000
    assert item.numbers["attempts_over_effective_budget"] > 0
    assert item.numbers["attempts_over_window"] > 0
    # 估算侧的低估必须同时可见, 否则读者看不出「为什么闸门没响」。
    assert item.numbers["planned_under_counts_billed"] > 0
    assert item.numbers["worst_planned_over_billed"] < 0.5
    assert item.numbers["worst_planned_over_billed_sample"]["input_tokens"] == 76_000


def test_a6_3_pairs_every_receipt_planned_with_the_billed_prompt(tmp_path):
    """planned 与真实计费必须逐条配上 —— 没配上就等于没有判据。"""

    pairs = [_turn(n, planned=9_000, billed=8_000) for n in range(1, 6)]
    receipts, invocations = _split(pairs)
    ev = _evidence(tmp_path, receipts=receipts, invocations=invocations)
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert item.numbers["planned_billed_pairs"] == 5
    assert item.numbers["planned_under_counts_billed"] == 0
    assert item.numbers["attempts_over_effective_budget"] == 0
    assert item.numbers["billed_attempts"] == 5


def test_a6_3_still_reports_the_billed_side_when_the_assembly_gate_did_fire(tmp_path):
    """装配期溢出仍然优先, 但说明里必须同时给出计费侧的量。"""

    pairs = [_turn(n, planned=20_000, billed=30_000) for n in range(1, 4)]
    receipts, invocations = _split(pairs)
    ev = _evidence(tmp_path, receipts=receipts, invocations=invocations)
    root = ev.root
    ev.close()
    with open(os.path.join(root, "native.log"), "a", encoding="utf-8") as fh:
        fh.write(
            '{"event": "sdk_context_budget_exceeded planned=27015 effective=26752",'
            ' "logger": "deskpet.sdk_adapters.context_authority"}\n'
        )
        fh.write(json.dumps({"event": "closure", "sdk_run_id": _RUN}) + "\n")
    ev = a6.Evidence(root)
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL
    assert "sdk_context_budget_exceeded" in item.reason
    assert "计费侧同期" in item.reason
    assert "3/3" in item.reason


def test_a6_3_counts_the_new_measured_gate(tmp_path):
    """事件 W 的实测闸门在 native.log 里可数 —— 它是修好之后的可见证据。"""

    pairs = [_turn(n, planned=9_000, billed=8_000) for n in range(1, 4)]
    receipts, invocations = _split(pairs)
    ev = _evidence(tmp_path, receipts=receipts, invocations=invocations)
    root = ev.root
    ev.close()
    with open(os.path.join(root, "native.log"), "a", encoding="utf-8") as fh:
        fh.write(
            '{"event": "sdk_provider_wire_input_budget_exceeded floor=33119'
            ' effective=26752 wire=12000 carry=21119", "logger":'
            ' "deskpet.sdk_adapters.wire_input_budget"}\n'
        )
    ev = a6.Evidence(root)
    try:
        stats = ev.wire_input_budget_events()
    finally:
        ev.close()
    assert stats["sdk_provider_wire_input_budget_exceeded_total"] == 1


def test_a6_3_counts_the_measured_gate_from_the_durable_ledger(tmp_path):
    """闸门自己的 error_code 落在 provider_invocations 上 —— 日志之外的耐久证据。

    被拦下的请求一个字节都没发出, 所以它**不会**出现在计费统计里; 只有这个
    码能证明「这一轮闸门真的拦过」, 也只有它带 Run 归属。
    """

    pairs = [_turn(n, planned=9_000, billed=8_000) for n in range(1, 4)]
    receipts, invocations = _split(pairs)
    invocations.append(
        {
            "run": _RUN,
            "ordinal": 4,
            "fingerprint": "fp-blocked",
            "input_tokens": None,
            "state": "failed",
            "error_code": "sdk_provider_wire_input_budget_exceeded",
        }
    )
    receipts.append(
        {"run": _RUN, "ordinal": 4, "revision": 4, "fingerprint": "fp-blocked", "planned": 9_000}
    )
    ev = _evidence(tmp_path, receipts=receipts, invocations=invocations)
    try:
        stats = ev.wire_input_budget_events()
        item = _a6_3(ev)
    finally:
        ev.close()
    assert stats["sdk_provider_wire_input_budget_settled_invocations"] == 1
    assert stats["sdk_provider_wire_input_budget_settled_foreground"] == 1
    assert item.numbers["sdk_provider_wire_input_budget_settled_invocations"] == 1
    # 拦下的那一次没有 usage, 所以计费统计一个数都不会动 —— 这正是它必须
    # 单独取证的原因。判定说明里那一句由 wire_gate_note 渲染。
    assert item.numbers["billed_attempts"] == 3
    assert "实测闸门" in a6.wire_gate_note(item.numbers)
    assert a6.wire_gate_note({}) == ""


def test_a6_3_judges_the_foreground_lane_and_reports_the_others_separately(tmp_path):
    """其它泳道(工作流/后台/探针)另有窗口与预算, 混进来既误判也漏判。

    这里前台全部装得下、另一条泳道有一次 40k 的计费: A6-3 不能因为它 FAIL,
    但那次调用必须在 numbers 与说明里可见。
    """

    # 17 轮前台全部装得下 —— 足够走到 PASS, 于是「另一条泳道」是这一项判定
    # 唯一的变量。
    pairs = [_turn(n, planned=9_000, billed=8_000) for n in range(1, 18)]
    receipts, invocations = _split(pairs)
    # 40 000: 既超前台的 effective_input_budget(26752), 也超 plan_ceiling ——
    # 不过滤泳道的话, A6-3 会拿工作流的 prompt 判前台链 FAIL。
    other, other_inv = _turn(1, planned=9_000, billed=40_000, run="product-sdk-workflow")
    receipts.append(other)
    invocations.append(other_inv)
    ev = _evidence(tmp_path, receipts=receipts, invocations=invocations)
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert item.numbers["billed_lane"] == "foreground"
    assert item.numbers["billed_attempts"] == 17
    assert item.numbers["attempts_over_effective_budget"] == 0
    assert item.numbers["max_billed_input_tokens"] == 8_000
    assert item.numbers["other_lane_billed_attempts"] == 1
    assert item.numbers["other_lane_runs"] == 1
    assert item.numbers["other_lane_max_billed_input_tokens"] == 40_000
    assert item.numbers["other_lane_attempts_over_effective_budget"] == 1
    # 审计行同样按泳道过滤 —— 否则 max_input_tokens 会是工作流那条 40 000。
    assert item.numbers["max_input_tokens"] == 8_000
    assert item.numbers["other_lane_attempt_rows"] == 1
    assert item.numbers["other_lane_max_input_tokens"] == 40_000
    assert item.verdict == a6.PASS, item.reason
    assert "其它泳道" in item.reason


# --------------------------------------------------------------- A6-12


def _a6_12(ev) -> "a6.Item":
    fingerprints = {inv.request_id: inv.request_fingerprint for inv in ev.invocations()}
    return a6.item_a6_12(ev, lambda request_id, _text: fingerprints[request_id])


def test_a6_12_accepts_one_unsent_terminal_receipt_per_run(tmp_path):
    """合法形态: 组装完了, Run 在 provider 调用被 claim 之前就死了。"""

    pairs = [_turn(n, planned=9_000, billed=8_000) for n in range(1, 5)]
    receipts, invocations = _split(pairs)
    receipts.append(
        {"run": _RUN, "ordinal": 5, "revision": 5, "fingerprint": "fp-unsent", "planned": 9_000}
    )
    ev = _evidence(tmp_path, receipts=receipts, invocations=invocations)
    try:
        item = _a6_12(ev)
    finally:
        ev.close()
    assert item.verdict == a6.PASS, item.reason
    assert item.numbers["invocations"] == 4
    assert item.numbers["receipts"] == 5
    assert item.numbers["unsent_terminal_receipts"] == 1
    assert item.numbers["orphan_receipts_without_invocation"] == 0
    assert item.numbers["invocations_without_receipt"] == 0
    # 行数差本身不再是判据, 但它必须仍然如实报出来(而不是把 unsent 样本
    # 抄一遍): 这一条 Run 确实是 5 条回执对 4 次调用。
    assert item.numbers["per_run_count_mismatch"] == [f"{_RUN[-8:]}:5!=4"]
    assert "组装完但 Run 就此终止" in item.reason


def test_a6_12_fails_on_a_mid_run_orphan_receipt(tmp_path):
    """中段孤儿: 账本声称组装过一个既没发出、也没终止 Run 的请求。"""

    pairs = [_turn(n, planned=9_000, billed=8_000) for n in range(1, 5)]
    receipts, invocations = _split(pairs)
    # 第 2 号 receipt 的指纹与任何调用都对不上, 而它后面还有调用。
    receipts[1] = dict(receipts[1], fingerprint="fp-drifted")
    ev = _evidence(tmp_path, receipts=receipts, invocations=invocations)
    try:
        item = _a6_12(ev)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL, item.reason
    assert item.numbers["orphan_receipts_without_invocation"] == 1
    assert item.numbers["invocations_without_receipt"] == 1
    assert item.numbers["unsent_terminal_receipts"] == 0


def test_a6_12_fails_when_a_dispatched_request_has_no_receipt(tmp_path):
    """反方向: 发出去的请求没有重放凭证 —— 重放证明直接不成立。"""

    pairs = [_turn(n, planned=9_000, billed=8_000) for n in range(1, 4)]
    receipts, invocations = _split(pairs)
    receipts.pop()  # 最后一次调用没有 receipt
    ev = _evidence(tmp_path, receipts=receipts, invocations=invocations)
    try:
        item = _a6_12(ev)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL, item.reason
    assert item.numbers["invocations_without_receipt"] == 1


def test_a6_12_fails_on_two_unsent_receipts_in_one_run(tmp_path):
    """每 Run 至多一条: 两条就说明「组装了但没发」发生了不止一次。"""

    pairs = [_turn(n, planned=9_000, billed=8_000) for n in range(1, 4)]
    receipts, invocations = _split(pairs)
    receipts += [
        {"run": _RUN, "ordinal": 4, "revision": 4, "fingerprint": "fp-unsent-a", "planned": 9_000},
        {"run": _RUN, "ordinal": 5, "revision": 5, "fingerprint": "fp-unsent-b", "planned": 9_000},
    ]
    ev = _evidence(tmp_path, receipts=receipts, invocations=invocations)
    try:
        item = _a6_12(ev)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL, item.reason
    assert item.numbers["orphan_receipts_without_invocation"] == 2
    assert item.numbers["unsent_terminal_receipts"] == 0


def test_a6_12_passes_a_clean_run_with_no_leftovers(tmp_path):
    pairs = [_turn(n, planned=9_000, billed=8_000) for n in range(1, 6)]
    receipts, invocations = _split(pairs)
    ev = _evidence(tmp_path, receipts=receipts, invocations=invocations)
    try:
        item = _a6_12(ev)
    finally:
        ev.close()
    assert item.verdict == a6.PASS, item.reason
    assert item.numbers["unsent_terminal_receipts"] == 0
    assert item.numbers["fingerprint_aligned_with_receipt"] == 5


def test_a6_12_matches_repeated_fingerprints_one_receipt_per_invocation(tmp_path):
    """指纹不含 request_id, 同 Run 两次逐字节相同的装配会撞同一个指纹。

    配对必须是消耗式的: 两条调用要两条 receipt。共用一条就等于重放证明说不清
    「是哪一次」, 记 FAIL。
    """

    pairs = [_turn(n, planned=9_000, billed=8_000) for n in range(1, 4)]
    receipts, invocations = _split(pairs)
    # 两条调用共享同一个指纹, 但只有两条 receipt 里的一条能对上。
    invocations[1] = dict(invocations[1], fingerprint=invocations[0]["fingerprint"])
    ev = _evidence(tmp_path, receipts=receipts, invocations=invocations)
    try:
        item = _a6_12(ev)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL, item.reason
    assert item.numbers["duplicate_receipts_for_one_invocation"] == 1
    assert item.numbers["invocations_without_receipt"] == 1

    # 反面: 两次装配真的逐字节相同时, 两条 receipt 也带同一个指纹, 两条调用
    # 各认领一条 -> PASS。
    receipts[1] = dict(receipts[1], fingerprint=invocations[0]["fingerprint"])
    ev = _evidence(tmp_path / "twin", receipts=receipts, invocations=invocations)
    try:
        item = _a6_12(ev)
    finally:
        ev.close()
    assert item.verdict == a6.PASS, item.reason
    assert item.numbers["duplicate_receipts_for_one_invocation"] == 0
    assert item.numbers["fingerprint_aligned_with_receipt"] == 3
