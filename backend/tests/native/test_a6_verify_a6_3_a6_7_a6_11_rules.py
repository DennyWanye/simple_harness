# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HM-TO-A6 第 11 次暴露的三条验证器口径缺陷(2026-09-09)。

见 `plans/2026-09-08-hm-to-a6/DECISION-VERIFIER-RULES-2026-09-09.md`。

1. **A6-3 增长比**。plan 写的是「后 8 **轮**/前 8 **轮**」, 旧实现拿
   `sdk_provider_attempt_audit` 的**逐次尝试**行当轮次用 —— 一轮 ReAct 循环
   有几次尝试就算几「轮」。第 11 次因此比的是「开头几轮的浅循环」对
   「末尾几轮的深循环」, 给出 2.269 并判 FAIL; 按真正的轮(= 一条前台
   `sdk_run_id`)聚合是 0.999。同时把当轮自己那条 user 消息(第 11 次 T17 一次
   发进来 18 KB 逐字目标文本)与 reasoning 回传从「增长」里扣掉 —— 本项判的是
   history/装配 质量有没有膨胀, 不是用户这一轮敲了多长。

2. **A6-7 生命周期豁免**。Prospective 到点推进
   (`sqlite_v5.py::_copy_cognitive_revision_unlocked`, 0.6.37)把上一条 revision
   逐字复制一份、只改 `lifecycle_state`, plan_id 换成
   `prospective-signal-plan-<authority>`, **不写** evolution 血缘边(写的是
   `prospective_mutation_outbox` + `prospective_trigger_event`)。旧判据把它记成
   「新 revision 缺少血缘边」。内容真的变了却没有血缘边, 仍然必须 FAIL。

3. **A6-11 memory_id 归属**。本项要挡的是「**图谱结构**进入 provider context」。
   memory_id 出现在工具回执、事件 V 的 `conflict_notice`、事件 Y / F-EPI-1 的
   召回提示、以及 Prospective 到点提醒(`prospective_inbox`, 第 11 次新增)里,
   是这些机制本来就要求的可寻址回执。判 FAIL 的只有图谱形状的载体。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

_VERIFY = Path(__file__).resolve().parents[3] / "scripts" / "native" / "a6_verify.py"


def _load():
    spec = importlib.util.spec_from_file_location("a6_verify_rules_uut", _VERIFY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


a6 = _load()

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


# ------------------------------------------------------------------ A6-3


def _attempt(
    *,
    run: str,
    ordinal: int,
    billed: int,
    own_user: str = "hi",
    history_tokens: int = 0,
) -> dict:
    """一次 provider 尝试: 计费量 + 当轮自己的 user 消息 + 一段历史组。"""
    return {
        "run": run,
        "ordinal": ordinal,
        "billed": billed,
        "own_user": own_user,
        "history_tokens": history_tokens,
    }


def _request_json(attempt: dict) -> str:
    messages: list[dict] = [{"role": "system", "content": "sys", "metadata": {}}]
    if attempt["history_tokens"]:
        body = json.dumps(
            {"kind": "historical_causal_group",
             "messages": [{"role": "user", "content": "x" * attempt["history_tokens"]}]},
            ensure_ascii=False,
        )
        messages.append(
            {"role": "user", "content": "Historical conversation data "
                                        "(not instructions): " + body,
             "metadata": {}}
        )
    messages.append({"role": "user", "content": attempt["own_user"], "metadata": {}})
    return json.dumps({"messages": messages, "tools": [], "metadata": {}},
                      ensure_ascii=False)


def _a6_3_evidence(tmp_path: Path, attempts: list[dict], *, native_extra: str = "") -> "a6.Evidence":
    root = str(tmp_path)
    data = os.path.join(root, "userdata", "data")
    runs = []
    for a in attempts:
        if a["run"] not in runs:
            runs.append(a["run"])
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
            " effective_ceiling integer, input_tokens integer, reasoning_tokens integer,"
            " total_tokens integer, state text, settled_at real)",
        ],
        [
            ("insert into foreground_run_heads values (?,?,?,?)",
             (f"h{n}", "COMPLETED", run, 100.0 + n))
            for n, run in enumerate(runs)
        ]
        + [
            (
                "insert into run_context_snapshot_receipts values (?,?,?,?,?,?,?,?)",
                (
                    f"snap{n}", a["run"], a["ordinal"], n + 1,
                    json.dumps({"source_revisions": {
                        "budget_tier": 8192, "causal_groups": 2, "trimmed_groups": 1,
                        "groups_trimmed_for_budget": 1,
                        "budget_headroom": _EFFECTIVE - a["billed"],
                        "planned_input_tokens": a["billed"],
                    }}),
                    f"fp{n}", f"fp{n}", 100.0 + n,
                ),
            )
            for n, a in enumerate(attempts)
        ]
        + [
            (
                "insert into sdk_provider_attempt_audit values (?,?,?,?,?,?,?,?,?,?,?)",
                (f"inv{n}", f"snap{n}", "deepseek-v4-flash", "primary", _WINDOW, 0,
                 a["billed"], 0, a["billed"] + 10, "succeeded", 100.0 + n),
            )
            for n, a in enumerate(attempts)
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
                (f"inv{n}", a["run"], f"{a['run']}:provider-turn:{a['ordinal']}",
                 f"fp{n}", _request_json(a), "succeeded", 100.0 + n, "{}",
                 json.dumps({"usage": {"input_tokens": a["billed"], "output_tokens": 10}}),
                 ""),
            )
            for n, a in enumerate(attempts)
        ],
    )
    with open(os.path.join(root, "native.log"), "w", encoding="utf-8") as fh:
        fh.write('{"event": "model_context_resolved model=deepseek-v4-flash'
                 ' window=%d source=global"}\n' % _WINDOW)
        if native_extra:
            fh.write(native_extra)
    return a6.Evidence(root)


def _a6_3(ev) -> "a6.Item":
    return a6.item_a6_3(ev, ev.budget_profile())


def _run11_shape() -> list[dict]:
    """第 11 次的形态: 22 轮, 开头几轮浅循环、末尾几轮深循环。

    逐轮峰值几乎持平(0.999), 但逐次尝试的前 8 行全落在开头几轮的第 1~2 次
    尝试上、后 8 行全落在末尾几轮的深循环里 —— 旧口径给 2.269。
    """
    peaks = [5297, 6165, 6376, 7301, 12163, 17487, 16869, 15135,
             15695, 12161, 18846, 4933, 10553, 11321, 16687, 17058,
             16710, 6749, 10720, 17470, 9248, 12720]
    attempts: list[dict] = []
    for turn, peak in enumerate(peaks, start=1):
        run = f"product-sdk-turn{turn:02d}"
        # 开头 4 轮只有 2 次尝试, 后面的轮次逐渐加深到 8 次。
        depth = 2 if turn <= 4 else min(8, 2 + turn // 3)
        for step in range(depth):
            billed = peak if step == depth - 1 else max(4000, peak - 900 * (depth - 1 - step))
            attempts.append(_attempt(run=run, ordinal=step + 1, billed=billed))
    return attempts


def test_a6_3_judges_turns_not_attempts_on_the_run11_shape(tmp_path: Path) -> None:
    ev = _a6_3_evidence(tmp_path, _run11_shape())
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert item.verdict == a6.PASS, item.reason
    numbers = item.numbers
    assert numbers["turns_with_billing"] == 22
    assert numbers["growth_basis"] == (
        "per_turn_billed_minus_own_user_message_and_reasoning_relay"
    )
    # 旧口径必须仍然记下来 —— 这是「口径变了」的证据, 不是把问题藏起来。
    assert numbers["growth_ratio_legacy_per_attempt"] > 1.6
    assert numbers["growth_ratio_raw_per_turn"] <= 1.6
    assert numbers["growth_ratio_last8_over_first8"] <= 1.6


def test_a6_3_still_fails_when_the_turn_peaks_really_do_grow(tmp_path: Path) -> None:
    """负例: 逐轮峰值真的翻了倍, 口径换了也必须 FAIL。"""
    attempts: list[dict] = []
    for turn in range(1, 23):
        run = f"product-sdk-turn{turn:02d}"
        peak = 6000 + 400 * turn
        for step in range(2):
            attempts.append(_attempt(run=run, ordinal=step + 1,
                                     billed=peak - 500 * (1 - step)))
    ev = _a6_3_evidence(tmp_path, attempts)
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL, item.reason
    assert "后 8 轮调整后峰值" in item.reason
    assert item.numbers["growth_ratio_last8_over_first8"] > 1.6


def test_a6_3_does_not_count_the_users_own_18kb_message_as_growth(tmp_path: Path) -> None:
    """第 11 次 T17 一次发进来 18 KB 逐字目标文本 —— 那是用户的输入, 不是膨胀。"""
    big = "目" * 6000  # CJK: 1 token/字, 逐字目标文本的形状
    flat: list[dict] = []
    for turn in range(1, 23):
        run = f"product-sdk-turn{turn:02d}"
        # 后 8 轮把那条 user 消息带上, 计费量因此高出 6000。
        late = turn >= 15
        for step in range(2):
            flat.append(_attempt(run=run, ordinal=step + 1,
                                 billed=9000 + (6000 if late else 0),
                                 own_user=big if late else "hi"))
    ev = _a6_3_evidence(tmp_path, flat)
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert item.numbers["own_user_message_tokens_max"] >= 6000
    # 未调整的逐轮比越过 1.6 上限之下但明显上涨; 调整后回到 1.0。
    assert item.numbers["growth_ratio_raw_per_turn"] > item.numbers[
        "growth_ratio_last8_over_first8"
    ]
    assert item.numbers["growth_ratio_last8_over_first8"] == 1.0
    assert item.verdict == a6.PASS, item.reason


def test_a6_3_reasoning_relay_is_recorded_as_observed_zero(tmp_path: Path) -> None:
    """DECISION-Y: 回传逐字写在 wire payload 上, 落库的 request_json 没有它。

    观测不到就扣 0(fail closed), 但必须把「按什么口径扣的」写进 numbers。
    """
    ev = _a6_3_evidence(tmp_path, _run11_shape())
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert item.numbers["reasoning_relay_basis"] == "observed_zero"
    assert item.numbers["reasoning_relay_observed_max"] == 0
    assert item.numbers["reasoning_relay_tokens_subtracted_max"] == 0


def test_a6_3_reasoning_relay_is_subtracted_when_the_log_shows_one(tmp_path: Path) -> None:
    line = ('{"event": "sdk_provider_wire_input_budget_exceeded floor=31246'
            ' effective=26752 wire=31246 carry=0 reasoning_relay=4096",'
            ' "logger": "deskpet.sdk_adapters.wire_input_budget"}\n')
    ev = _a6_3_evidence(tmp_path, _run11_shape(), native_extra=line)
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert item.numbers["reasoning_relay_observed_max"] == 4096
    assert item.numbers["reasoning_relay_basis"] == "ledger_cumulative_prior_reasoning"


def test_a6_3_needs_sixteen_turns_not_sixteen_attempts(tmp_path: Path) -> None:
    """一条 Run 里跑了 20 次尝试也只是 1 轮 —— 不足以做前后 8 轮比对。"""
    attempts = [_attempt(run="product-sdk-only", ordinal=n, billed=9000 + 100 * n)
                for n in range(1, 21)]
    ev = _a6_3_evidence(tmp_path, attempts)
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert item.verdict == a6.INCONCLUSIVE
    assert "不足 16 轮" in item.reason
    assert item.numbers["turns_with_billing"] == 1
    assert item.numbers["growth_ratio_last8_over_first8"] is None


def test_a6_3_keeps_the_first_four_number_keys_for_the_markdown_brief(tmp_path: Path) -> None:
    ev = _a6_3_evidence(tmp_path, _run11_shape())
    try:
        item = _a6_3(ev)
    finally:
        ev.close()
    assert list(item.numbers)[:4] == [
        "attempt_rows", "max_input_tokens",
        "first8_max_input_tokens", "last8_max_input_tokens",
    ]
    json.dumps(item.numbers, ensure_ascii=False)


# ------------------------------------------------------------------ A6-7


def _hm_db(tmp_path: Path, heads, revisions, relations, *, with_lifecycle_columns=True):
    path = tmp_path / "human_memory_v7.db"
    conn = sqlite3.connect(path)
    if with_lifecycle_columns:
        conn.execute(
            "create table cognitive_memory_revisions(memory_id text, revision integer,"
            " lifecycle_state text, conflict_status text, content_hash text, plan_id text)"
        )
        insert = "insert into cognitive_memory_revisions values (?,?,?,?,?,?)"
    else:
        conn.execute(
            "create table cognitive_memory_revisions(memory_id text, revision integer,"
            " lifecycle_state text, conflict_status text)"
        )
        insert = "insert into cognitive_memory_revisions values (?,?,?,?)"
        revisions = [r[:4] for r in revisions]
    conn.execute("create table cognitive_memory_heads(memory_id text primary key,"
                 " current_revision integer)")
    conn.execute("create table cognitive_relations(relation_domain text, relation_kind text,"
                 " source_memory_id text, source_revision integer,"
                 " target_memory_id text, target_revision integer)")
    conn.executemany("insert into cognitive_memory_heads values (?,?)", heads)
    conn.executemany(insert, revisions)
    conn.executemany("insert into cognitive_relations values (?,?,?,?,?,?)", relations)
    conn.commit()
    conn.close()
    return a6.RoDb(str(path), "human_memory_v7.db")


def _a6_7(tmp_path: Path, heads, revisions, relations, **kw) -> "a6.Item":
    db = _hm_db(tmp_path, heads, revisions, relations, **kw)
    try:
        return a6.item_a6_7(SimpleNamespace(hm=db))
    finally:
        db.close()


# 第 11 次的真实形状: Prospective 到点, pending -> triggered, 内容哈希一字不变,
# plan_id 换成 prospective-signal-plan-<authority>, 没有 evolution 边。
_PROSPECTIVE = [
    ("m-prosp", 1, "pending", "uncontested", "c3682f0e", "host-analysis-plan-5ae9db75"),
    ("m-prosp", 2, "triggered", "uncontested", "c3682f0e",
     "prospective-signal-plan-045b09caec4be127"),
]


def test_a6_7_exempts_a_prospective_lifecycle_advance(tmp_path: Path) -> None:
    item = _a6_7(tmp_path, [("m-prosp", 2)], _PROSPECTIVE, [])
    assert item.verdict == a6.PASS, item.reason
    assert item.numbers["missing_evolution_lineage"] == []
    assert item.numbers["lifecycle_only_revisions_exempted"] == 1
    assert item.numbers["lifecycle_only_revision_samples"] == [
        ("m-prosp", 2, "pending->triggered")
    ]


def test_a6_7_still_fails_a_real_content_revision_without_lineage(tmp_path: Path) -> None:
    """负例: 内容真的改了(哈希变了)却没有血缘边 —— 仍然必须 FAIL。"""
    revisions = [
        ("m-fix", 1, "active", "uncontested", "aaaa1111", "host-analysis-plan-1"),
        ("m-fix", 2, "active", "uncontested", "bbbb2222", "host-analysis-plan-2"),
    ]
    item = _a6_7(tmp_path, [("m-fix", 2)], revisions, [])
    assert item.verdict == a6.FAIL
    assert item.numbers["missing_evolution_lineage"] == [("m-fix", 2)]
    assert item.numbers["lifecycle_only_revisions_exempted"] == 0


def test_a6_7_does_not_exempt_an_identical_rewrite_with_no_lifecycle_move(tmp_path: Path) -> None:
    """内容哈希相同、lifecycle 也没动、又不带调度器出身 —— 不给豁免。"""
    revisions = [
        ("m-dup", 1, "active", "uncontested", "same-hash", "host-analysis-plan-1"),
        ("m-dup", 2, "active", "uncontested", "same-hash", "host-analysis-plan-2"),
    ]
    item = _a6_7(tmp_path, [("m-dup", 2)], revisions, [])
    assert item.verdict == a6.FAIL
    assert item.numbers["missing_evolution_lineage"] == [("m-dup", 2)]


def test_a6_7_lifecycle_move_alone_is_not_enough_when_content_changed(tmp_path: Path) -> None:
    """调度器出身 + lifecycle 变, 但内容哈希也变了 —— 那是内容纠正, 必须 FAIL。"""
    revisions = [
        ("m-prosp", 1, "pending", "uncontested", "c3682f0e", "host-analysis-plan-1"),
        ("m-prosp", 2, "triggered", "uncontested", "DIFFERENT",
         "prospective-signal-plan-045b09caec4be127"),
    ]
    item = _a6_7(tmp_path, [("m-prosp", 2)], revisions, [])
    assert item.verdict == a6.FAIL
    assert item.numbers["missing_evolution_lineage"] == [("m-prosp", 2)]


def test_a6_7_without_a_content_hash_column_gives_no_exemption(tmp_path: Path) -> None:
    """旧证据目录没有 content_hash 列: 认不出生命周期推进就不豁免(fail closed)。"""
    item = _a6_7(tmp_path, [("m-prosp", 2)], _PROSPECTIVE, [],
                 with_lifecycle_columns=False)
    assert item.verdict == a6.FAIL
    assert item.numbers["lifecycle_only_revisions_exempted"] == 0


def test_a6_7_content_revision_with_lineage_and_lifecycle_advance_both_pass(
    tmp_path: Path,
) -> None:
    revisions = [
        ("m-fix", 1, "active", "uncontested", "aaaa1111", "host-analysis-plan-1"),
        ("m-fix", 2, "active", "uncontested", "bbbb2222", "host-analysis-plan-2"),
    ] + _PROSPECTIVE
    relations = [("evolution", "amends", "m-fix", 2, "m-fix", 1)]
    item = _a6_7(tmp_path, [("m-fix", 2), ("m-prosp", 2)], revisions, relations)
    assert item.verdict == a6.PASS, item.reason
    assert item.numbers["memories_with_multiple_revisions"] == 2
    assert item.numbers["lifecycle_only_revisions_exempted"] == 1


# ------------------------------------------------------------------ A6-11

_MEM_A = "cognitive-memory-" + "6" * 64
_MEM_B = "cognitive-memory-" + "9" * 64
_REL = "cognitive-relation-" + "a" * 64


def _a6_11_evidence(tmp_path: Path, requests: list[list[dict]]) -> "a6.Evidence":
    root = str(tmp_path)
    data = os.path.join(root, "userdata", "data")
    _make_db(
        os.path.join(data, "human_memory_v7.db"),
        [
            "create table cognitive_memory_heads(memory_id text primary key,"
            " current_revision integer)",
            "create table cognitive_relations(relation_id text, relation_hash text,"
            " relation_domain text, relation_kind text, source_memory_id text,"
            " source_revision integer, target_memory_id text, target_revision integer)",
        ],
        [
            ("insert into cognitive_memory_heads values (?,?)", (_MEM_A, 1)),
            ("insert into cognitive_memory_heads values (?,?)", (_MEM_B, 1)),
            ("insert into cognitive_relations values (?,?,?,?,?,?,?,?)",
             (_REL, "h" * 64, "ordinary", "applies_to", _MEM_A, 1, _MEM_B, 1)),
        ],
    )
    _make_db(
        os.path.join(data, "simple-harness-sdk", "execution-v6.sqlite3"),
        [
            "create table provider_invocations(invocation_id text primary key, run_id text,"
            " request_id text, request_fingerprint text, request_json text, state text,"
            " claimed_at real, response_json text, usage_json text, error_code text)",
        ],
        [
            (
                "insert into provider_invocations values (?,?,?,?,?,?,?,?,?,?)",
                (f"inv{n}", "run1", f"req{n}", f"fp{n}",
                 json.dumps({"messages": messages, "tools": [], "metadata": {}},
                            ensure_ascii=False),
                 "succeeded", 100.0 + n, "{}", "{}", ""),
            )
            for n, messages in enumerate(requests)
        ],
    )
    with open(os.path.join(root, "native.log"), "w", encoding="utf-8") as fh:
        fh.write("{}\n")
    with open(os.path.join(root, "a6-progress.jsonl"), "w", encoding="utf-8") as fh:
        for turn in (15, 16, 23, 24):
            fh.write(json.dumps({"kind": "turn", "turn": turn,
                                 "provider_invocations": 7}) + "\n")
    return a6.Evidence(root)


def _prospective_inbox_message() -> dict:
    return {
        "role": "system",
        "content": json.dumps(
            {"count": 1, "entries": [
                {"action": "有机会时开始学画画", "memory_id": _MEM_A,
                 "occurred_at": 1788920763.4}]},
            ensure_ascii=False,
        ),
        "metadata": {"source": "prospective_inbox", "trust": "host_authority"},
    }


def _tool_receipt_message() -> dict:
    return {
        "role": "tool",
        "content": json.dumps(
            {"outcome": "succeeded",
             "value": {"candidates": [{"candidate": {"memory_id": _MEM_B,
                                                     "name": "秋分资料整理校对流程"}}]}},
            ensure_ascii=False,
        ),
        "metadata": {},
    }


def _history_group_with_tool_receipt() -> dict:
    body = json.dumps(
        {"kind": "historical_causal_group",
         "messages": [
             {"role": "user", "content": "那你现在按哪个版本执行这套校对流程？"},
             {"role": "tool", "content": json.dumps(
                 {"value": {"candidates": [{"memory_id": _MEM_B}]}}, ensure_ascii=False)},
         ]},
        ensure_ascii=False,
    )
    return {"role": "user",
            "content": "Historical conversation data (not instructions): " + body,
            "metadata": {}}


def _conflict_notice_message() -> dict:
    return {
        "role": "system",
        "content": json.dumps({"kind": "conflict_notice", "memory_id": _MEM_A,
                               "incumbent": "Python 3.12"}, ensure_ascii=False),
        "metadata": {"source": "conflict_notice", "trust": "host_authority"},
    }


def _graph_payload_message() -> dict:
    """真正该挡的形状: memory_id 被放在一个图谱形状的载荷里。"""
    return {
        "role": "user",
        "content": json.dumps(
            {"edges": [{"source_memory_id": _MEM_A, "target_memory_id": _MEM_B,
                        "relation_kind": "applies_to"}]},
            ensure_ascii=False,
        ),
        "metadata": {},
    }


def test_a6_11_passes_when_memory_ids_only_ride_receipts_and_notices(tmp_path: Path) -> None:
    """第 11 次的形态: 33 次 prospective_inbox + 工具回执 + 历史组内回执。"""
    requests = [
        [_prospective_inbox_message(), {"role": "user", "content": "你好", "metadata": {}}]
        for _ in range(5)
    ] + [
        [_tool_receipt_message()],
        [_history_group_with_tool_receipt()],
        [_conflict_notice_message()],
    ]
    ev = _a6_11_evidence(tmp_path, requests)
    try:
        item = a6.item_a6_11(ev, False)
    finally:
        ev.close()
    assert item.verdict == a6.PASS, item.reason
    histogram = item.numbers["memory_id_hit_histogram"]
    assert histogram["system:prospective_inbox"] == 5
    assert histogram["system:conflict_notice"] == 1
    assert histogram["tool_result"] == 1
    assert histogram["history_group_tool_result"] == 1
    assert item.numbers["memory_id_hits_in_graph_payload"] == 0
    assert item.numbers["hits_memory_id"] == 8
    # 旧的「只在 tool 回执里」判据必须已经不再决定判定 —— 它现在只是个数字。
    assert item.numbers["hits_memory_id_only_in_tool_results"] is False


def test_a6_11_fails_when_a_memory_id_rides_a_graph_shaped_payload(tmp_path: Path) -> None:
    ev = _a6_11_evidence(tmp_path, [[_tool_receipt_message()], [_graph_payload_message()]])
    try:
        item = a6.item_a6_11(ev, False)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL
    assert item.numbers["memory_id_hits_in_graph_payload"] >= 1
    assert any(s["key"] in a6.GRAPH_SHAPED_PAYLOAD_KEYS
               for s in item.numbers["memory_id_graph_payload_samples"])


def test_a6_11_still_fails_on_a_relation_id(tmp_path: Path) -> None:
    message = {"role": "user", "content": f"relation {_REL}", "metadata": {}}
    ev = _a6_11_evidence(tmp_path, [[message]])
    try:
        item = a6.item_a6_11(ev, False)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL
    assert item.numbers["hits_relation_id"] == 1


def test_a6_11_strict_mode_still_fails_on_any_memory_id(tmp_path: Path) -> None:
    ev = _a6_11_evidence(tmp_path, [[_prospective_inbox_message()]])
    try:
        item = a6.item_a6_11(ev, True)
    finally:
        ev.close()
    assert item.verdict == a6.FAIL
    assert item.numbers["strict_mode"] is True


def test_a6_11_unparsable_request_is_counted_as_structural(tmp_path: Path) -> None:
    """请求体解析不出来就归类不了 —— 按结构性命中记(fail closed)。"""
    inv = SimpleNamespace(invocation_id="inv-broken", request=None,
                          request_json_text=_MEM_A)
    histogram, structural = a6._memory_id_carriers(inv, [_MEM_A])
    assert histogram == {"unparsable_request": 1}
    assert structural and structural[0]["carrier"] == "unparsable_request"


def test_selftest_still_runs_every_item() -> None:
    assert a6.selftest() == 0
