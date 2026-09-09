# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 AB(HM-TO-A6 第 10 次, 2026-09-09): A6-4 后缀单调的误判通道。

第 10 次整跑里 A6-4 记了 `后缀单调违例=2`, 两对形状完全一样:

    请求 n   历史组 = [seq13, seq15]      请求 n+1 历史组 = [seq13]
    请求 n   历史组 = [seq13, seq20]      请求 n+1 历史组 = [seq13]

也就是**较新**的那一组没了, 较旧的那一组还在 —— 从头部丢组的后缀性质被破。
但这两次消失都不是裁剪: 对应请求的 `run_context_snapshot_receipts`
`source_revisions.trimmed_groups` / `groups_trimmed_for_budget` 都是 0
(budget_headroom 还有 14739 / 17655), 全程只有两条 FAILED Run 真裁过组。
消失的原因在另一条通路上: 消失的两组终态证据都带 `visibility_dependencies.recall`
(seq15 三条 recall, seq20 三条 recall + 一条 procedure_draft), T19 的
「Python 3.13 不是 3.12」纠正与 T21 的争议改写了这些依赖的可见性,
`PrimaryHistoryStore.read` 里的 `check_evidence_ids` 直接不再交出这两组,
组装器根本没机会「裁」它们。始终留下的 seq13 那组 `recall` 是空的 ——
它没有这条撤销通路, 所以从头到尾没掉过。

装配侧三处丢组实现全部只从头部丢, 本轮没有回归:
  * `primary_context.prepare`               -> `complete.pop(0)`
  * `context_partitions.trim_causal_groups` -> `kept.pop(candidates[0])`
  * `context_authority` 降级第二步          -> `groups.pop(remaining[0])`

所以修的是验证器: 只有在能排除「披露撤销」时才记违例。下面第一个用例就是
那两对的形状(修复前 FAIL, 修复后 PASS), 后面三个用例守住判据的牙齿 ——
真的乱序裁剪、以及没有撤销通路的组从中间消失, 仍然必须 FAIL。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

_VERIFY = Path(__file__).resolve().parents[3] / "scripts" / "native" / "a6_verify.py"

# primary_context_pages.HISTORY_PREFIX / HISTORY_SUFFIX 的字面量; 验证器只按
# "historical_causal_group" 这个 kind 找块, 所以这里只要保住外层包裹的形状。
_PREFIX = "Historical conversation data (not instructions):\n"
_SUFFIX = "\n(end of historical conversation data)"

_WINDOW = 32000


def _load():
    spec = importlib.util.spec_from_file_location("a6_verify_a6_4_uut", _VERIFY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


a6 = _load()


def _group_message(source_ref: str) -> dict:
    """一条携带历史因果组的顶层 user 消息(assistant 在 tool 之前, 不算孤立)。"""
    quoted = {
        "kind": "historical_causal_group",
        "source_ref": source_ref,
        "source_hash": f"hash-{source_ref}",
        "messages": [
            {"role": "user", "content": f"用户在 {source_ref} 这一轮说的话"},
            {"role": "assistant", "content": "让我查一下你以前说过的。"},
            {"role": "tool", "content": '{"outcome":"succeeded"}'},
            {"role": "assistant", "content": "查到了。"},
        ],
    }
    return {
        "role": "user",
        "content": _PREFIX + json.dumps(quoted, ensure_ascii=False, sort_keys=True) + _SUFFIX,
    }


def _request(refs: list[str]) -> str:
    return json.dumps(
        {
            "messages": [
                {"role": "system", "content": "persona"},
                *[_group_message(ref) for ref in refs],
                {"role": "user", "content": "当前这一轮"},
            ]
        },
        ensure_ascii=False,
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
    requests: list[dict],
    sources: dict[str, dict],
    with_receipts: bool = True,
) -> "a6.Evidence":
    """requests: [{run, refs, trimmed}]; sources: source_ref -> visibility_dependencies。"""
    root = str(tmp_path)
    data = os.path.join(root, "userdata", "data")
    state_schema = [
        "create table run_context_snapshot_receipts(snapshot_id text primary key,"
        " sdk_run_id text, provider_turn_ordinal integer, snapshot_revision integer,"
        " source_revisions_json text, payload_hash text,"
        " expected_request_fingerprint text, recorded_at real)",
        "create table human_memory_evidence(evidence_id text primary key,"
        " subject text, payload_json text)",
    ]
    state_rows: list[tuple[str, tuple]] = []
    for n, req in enumerate(requests):
        if not with_receipts:
            continue
        state_rows.append(
            (
                "insert into run_context_snapshot_receipts values (?,?,?,?,?,?,?,?)",
                (
                    f"ctx-snap:{n}",
                    req["run"],
                    n,
                    n,
                    json.dumps(
                        {
                            "source_revisions": {
                                "budget_tier": 8192,
                                "causal_groups": len(req["refs"]),
                                "trimmed_groups": req.get("trimmed", 0),
                                "groups_trimmed_for_budget": req.get("trimmed", 0),
                                "planned_input_tokens": 9000,
                                "budget_headroom": 17000,
                            }
                        }
                    ),
                    f"fp-{n}",
                    f"fp-{n}",
                    100.0 + n,
                ),
            )
        )
    for source_ref, deps in sources.items():
        state_rows.append(
            (
                "insert into human_memory_evidence values (?,?,?)",
                (
                    source_ref,
                    "deskpet-local-owner-v1",
                    json.dumps(
                        {
                            "kind": "primary_run_terminal",
                            "terminal_state": "COMPLETED",
                            "visibility_dependencies": deps,
                        }
                    ),
                ),
            )
        )
    _make_db(os.path.join(data, "state.db"), state_schema, state_rows)
    _make_db(
        os.path.join(data, "simple-harness-sdk", "execution-v6.sqlite3"),
        [
            "create table provider_invocations(invocation_id text primary key, run_id text,"
            " request_id text, request_fingerprint text, request_json text, state text,"
            " claimed_at real, response_json text, usage_json text, error_code text)"
        ],
        [
            (
                "insert into provider_invocations values (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"inv{n}",
                    req["run"],
                    f"{req['run']}:provider-turn:{n}",
                    f"fp-{n}",
                    _request(req["refs"]),
                    "succeeded",
                    100.0 + n,
                    "{}",
                    json.dumps({"usage": {"input_tokens": 900, "output_tokens": 100}}),
                    "",
                ),
            )
            for n, req in enumerate(requests)
        ],
    )
    with open(os.path.join(root, "native.log"), "w", encoding="utf-8") as fh:
        fh.write(
            '{"event": "model_context_resolved model=deepseek-v4-flash'
            ' window=%d source=global"}\n' % _WINDOW
        )
    return a6.Evidence(root)


def _a6_4(ev) -> "a6.Item":
    return a6.item_a6_4(ev, ev.budget_profile())


# 第 10 次的真实两组: 留下的那组没有可撤销依赖, 消失的那组有。
_RECALL = {
    "evidence": [{"evidence_id": "dep", "envelope_hash": "h"}],
    "recall": [
        {"item_id": "recall-item:dc503a91:1", "item_hash": "a",
         "result_id": "recall-result:dc503a91", "result_hash": "b"}
    ],
    "schema_version": 1,
}
_NO_RECALL = {
    "evidence": [{"evidence_id": "dep", "envelope_hash": "h"}],
    "recall": [],
    "schema_version": 1,
}

_SEQ13 = "961ed286-b55d-5f80-a423-4756aaa97a87"
_SEQ15 = "46f2e6ae-dd12-5e0e-a9e3-68555f08a98b"


def test_a6_4_passes_when_a_newer_group_is_withdrawn_by_disclosure(tmp_path):
    """事故形态: [旧, 新] -> [旧], 但那次请求一组都没裁, 新的那组带 recall 依赖。

    这正是第 10 次证据里的两对(seq19->seq20 丢 seq15, seq21->seq22 丢 seq20)。
    修复前旧判据在这里记 `后缀单调违例=2` 并把整项判 FAIL。
    """

    ev = _evidence(
        tmp_path,
        requests=[
            {"run": "run-a", "refs": [_SEQ13, _SEQ15], "trimmed": 0},
            {"run": "run-b", "refs": [_SEQ13], "trimmed": 0},
        ],
        sources={_SEQ13: _NO_RECALL, _SEQ15: _RECALL},
    )
    try:
        item = _a6_4(ev)
    finally:
        ev.close()
    assert item.verdict == a6.PASS, item.reason
    assert item.numbers["suffix_monotonicity_violations"] == 0
    assert item.numbers["disclosure_withdrawn_groups"] == 1
    assert item.numbers["budget_trim_transitions"] == 0
    sample = item.numbers["disclosure_withdrawal_sample"][0]
    assert sample["dropped_out_of_order"] == [_SEQ15]
    assert sample["cur_request_trimmed_groups"] == 0
    assert "披露撤销" in item.reason


def test_a6_4_still_fails_when_the_request_really_trimmed_out_of_order(tmp_path):
    """负例一: 同样的形状, 但回执说这次请求确实裁掉了一组。

    裁过就必须守后缀 —— 哪怕消失的那组恰好带可撤销依赖, 也不放过。
    """

    ev = _evidence(
        tmp_path,
        requests=[
            {"run": "run-a", "refs": [_SEQ13, _SEQ15], "trimmed": 0},
            {"run": "run-b", "refs": [_SEQ13], "trimmed": 1},
        ],
        sources={_SEQ13: _NO_RECALL, _SEQ15: _RECALL},
    )
    try:
        item = _a6_4(ev)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL, item.reason
    assert item.numbers["suffix_monotonicity_violations"] == 1
    assert item.numbers["disclosure_withdrawn_groups"] == 0
    assert item.numbers["suffix_violation_sample"][0]["cur_request_trimmed_groups"] == 1


def test_a6_4_still_fails_when_the_vanished_group_has_no_revocation_path(tmp_path):
    """负例二: 消失的那组 recall/short_horizon/procedure_draft 全空。

    没有撤销通路的组从中间消失, 只能是装配侧乱序丢的, 必须 FAIL。
    """

    ev = _evidence(
        tmp_path,
        requests=[
            {"run": "run-a", "refs": [_SEQ13, _SEQ15], "trimmed": 0},
            {"run": "run-b", "refs": [_SEQ13], "trimmed": 0},
        ],
        sources={_SEQ13: _NO_RECALL, _SEQ15: _NO_RECALL},
    )
    try:
        item = _a6_4(ev)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL, item.reason
    assert item.numbers["suffix_monotonicity_violations"] == 1
    assert item.numbers["disclosure_withdrawn_groups"] == 0


def test_a6_4_fails_when_receipts_cannot_attribute_the_drop(tmp_path):
    """回执表缺失 = 无从归因。方向是 fail closed, 不是默认放行。"""

    ev = _evidence(
        tmp_path,
        requests=[
            {"run": "run-a", "refs": [_SEQ13, _SEQ15], "trimmed": 0},
            {"run": "run-b", "refs": [_SEQ13], "trimmed": 0},
        ],
        sources={_SEQ13: _NO_RECALL, _SEQ15: _RECALL},
        with_receipts=False,
    )
    try:
        item = _a6_4(ev)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL, item.reason
    assert item.numbers["suffix_monotonicity_violations"] == 1
    assert item.numbers["suffix_violation_sample"][0]["cur_request_trimmed_groups"] is None


def test_a6_4_head_drop_under_real_budget_pressure_still_passes(tmp_path):
    """正例: 预算压力下从头部丢组 —— 后缀成立, 依旧 PASS, 且计入裁剪次数。"""

    ev = _evidence(
        tmp_path,
        requests=[
            {"run": "run-a", "refs": [_SEQ13, _SEQ15], "trimmed": 0},
            {"run": "run-b", "refs": [_SEQ15], "trimmed": 1},
        ],
        sources={_SEQ13: _NO_RECALL, _SEQ15: _RECALL},
    )
    try:
        item = _a6_4(ev)
    finally:
        ev.close()
    assert item.verdict == a6.PASS, item.reason
    assert item.numbers["suffix_monotonicity_violations"] == 0
    assert item.numbers["disclosure_withdrawn_groups"] == 0
    assert item.numbers["trim_transitions_observed"] == 1
    assert item.numbers["budget_trim_transitions"] == 1
