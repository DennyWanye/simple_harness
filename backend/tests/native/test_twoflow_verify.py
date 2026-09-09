# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""两轮完整流程（含重启）旅程的核对器 / 驱动脚本回归（2026-09-09）。

本文件钉住三件今天才成立的事实，任何一件被改回去用例都会红：

1. **TF-17 授权策略权威只在 `workflow.db`**（MM-D1）。
   `sdk-product-state.db` 里的同名表是建库时 `INSERT OR IGNORE` 的 DDL 残留，
   恒为 `auto/0/factory_default`，**从来没有写路径会更新它**。Manual 旅程 run3
   就是因为驱动读了那张残留表，整趟把真实的 manual 记成 auto。
   因此两个夹具库故意给相反的值：`workflow.db=manual` 而残留行 `=auto`，
   核对器一旦回落读错库，TF-17 会从 FAIL 变 PASS——用例正是守这条。

2. **TF-16 读闸门绑定跨重启复用**（F-Z1b/F-Z1c）。读类工具现在要求目标目录已绑定；
   越界读走 S4 绑定提案通道，Auto 策略自动授予。所以 T4 读任务托管家目录**之外**
   的夹具时绑定根 +1，重启后 T15 读同一夹具**不得**再新增根/提案——那正是
   「绑定跨重启存活」的证据。T15 又冒出新根 = FAIL（绑定没活过重启）。

3. **两个驱动在发送前都要有界地等授权卡/停止键消失**。Manual 旅程今天出现过
   "no new Run head after two sends"：上一轮留下一张没应答的授权卡，或发送键
   还是「■ 停止」，`send.sh` 就点了一颗当时不生效的『发送』。

驱动侧另有 `bash -n` 与 `TWOFLOW_DRY_RUN=1` 两条冒烟（后者不碰 DB、不发消息）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "native"
_VERIFY = _SCRIPTS / "twoflow_verify.py"
_TWOFLOW_DRIVER = _SCRIPTS / "twoflow_driver.sh"
_MANUAL_DRIVER = _SCRIPTS / "manual_driver.sh"


def _load():
    spec = importlib.util.spec_from_file_location("twoflow_verify_uut", _VERIFY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tf = _load()


# ------------------------------------------------------------------ 夹具


def _make(path: Path, ddl, rows=()) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    for stmt in ddl:
        conn.execute(stmt)
    for sql, params in rows:
        conn.execute(sql, params)
    conn.commit()
    conn.close()


_POLICY_DDL = [
    "create table authorization_policy_state(singleton_id integer primary key, mode text,"
    " generation integer, updated_at real, provenance text, schema_generation integer,"
    " user_set_receipt_ref text)",
]

# 逐轮计数的最小骨架：只放 TF-16/TF-17 用得到的键，其余项缺键即 INCONCLUSIVE。
_BASE = {
    "binding_roots": 0,
    "binding_proposals": 0,
    "binding_revisions": 0,
    "execution_effects": 0,
    "foreground_run_heads": 0,
}


def _progress(root: Path, patches: dict[int, dict[str, int]], policy_mode: str = "auto") -> None:
    """写一份 T0..T22 的 twoflow-progress.jsonl；patches 是逐轮的绝对值覆盖。"""
    running = dict(_BASE)
    turns = [0] + list(range(1, 12)) + [tf.RESTART_TURN] + list(range(12, 23))
    with open(root / tf.PROGRESS_NAME, "w", encoding="utf-8") as fh:
        for idx, turn in enumerate(turns):
            running.update(patches.get(turn, {}))
            row = {
                "ts": "2026-09-09T%02d:%02d:00+0800" % (9 + idx // 6, (idx * 7) % 60),
                "turn": turn,
                "kind": "restart" if turn == tf.RESTART_TURN else ("ui" if turn in tf.UI_TURNS else "send"),
                "phase": "flow1" if 0 <= turn <= 11 else "flow2",
                "outcome": "settled",
                "elapsed_s": 1.0,
                "primary_conversation_id": "primary-1",
                "last_run_state": "COMPLETED",
                "policy_mode": policy_mode,
                "policy_provenance": "user_explicit",
                "note": "",
            }
            row.update(running)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


@pytest.fixture()
def evidence_root(tmp_path: Path):
    """一棵刚好够 TF-16 / TF-17 判定的证据树；返回 (root, data 目录)。"""
    root = tmp_path / "E1"
    data = root / "userdata" / "data"
    data.mkdir(parents=True)
    (root / "native.log").write_text(
        "startup complete\n"
        "read_workspace_binding_revised proposal=185fae71 root=/ws/twoflow-fixture\n",
        encoding="utf-8",
    )
    (root / "launch.json").write_text(json.dumps({"pid": 1, "userdata": str(root / "userdata")}))
    return root, data


def _default_state(data: Path) -> None:
    _make(
        data / "state.db",
        [
            "create table task_workspace_binding_roots(root_id text primary key,"
            " task_scope_id text, canonical_path text)",
            "create table task_workspace_binding_proposals(proposal_id text primary key,"
            " task_scope_id text, canonical_path text)",
        ],
    )


def _item(root: Path, key: str):
    ev = tf.Evidence(str(root))
    try:
        items = {i.key: i for i in tf.run_items(ev, None)}
    finally:
        ev.close()
    return items[key]


# ------------------------------------------------------------------ TF-17：策略权威


def test_tf17_reads_the_policy_from_workflow_db_not_the_product_state_residue(evidence_root):
    """MM-D1 的牙齿：两个库给相反的值，核对器必须以 workflow.db 为准。

    workflow.db = manual（真轨迹） / sdk-product-state.db = auto（DDL 残留种子行）。
    读对库 -> FAIL（本旅程要求 auto）；读错库 -> PASS。所以这条断言直接
    钉死了「绝不回落读残留表」。
    """
    root, data = evidence_root
    _default_state(data)
    _make(data / "workflow.db", _POLICY_DDL,
          [("insert into authorization_policy_state values (?,?,?,?,?,?,?)",
            (1, "manual", 1, 2.0, "user_explicit", 2, "policy-user-set:1"))])
    _make(data / "sdk-product-state.db", _POLICY_DDL,
          [("insert into authorization_policy_state values (?,?,?,?,?,?,?)",
            (1, "auto", 0, 1.0, "factory_default", 2, None))])
    _progress(root, {}, policy_mode="manual")

    item = _item(root, "TF-17")
    assert item.verdict == tf.FAIL, item.reason
    assert item.numbers["workflow_mode"] == "manual"
    # 残留行仍被读出来放进 numbers（供人核对），但不参与判定。
    assert item.numbers["product_state_residue_row"] == {
        "mode": "auto", "provenance": "factory_default",
    }


def test_tf17_passes_on_auto_and_records_the_residue_row_without_believing_it(evidence_root):
    root, data = evidence_root
    _default_state(data)
    _make(data / "workflow.db", _POLICY_DDL,
          [("insert into authorization_policy_state values (?,?,?,?,?,?,?)",
            (1, "auto", 1, 2.0, "user_explicit", 2, "policy-user-set:1"))])
    _make(data / "sdk-product-state.db", _POLICY_DDL,
          [("insert into authorization_policy_state values (?,?,?,?,?,?,?)",
            (1, "manual", 0, 1.0, "factory_default", 2, None))])
    _progress(root, {}, policy_mode="auto")

    item = _item(root, "TF-17")
    assert item.verdict == tf.PASS, item.reason
    assert item.numbers["workflow_provenance"] == "user_explicit"


def test_tf17_is_inconclusive_when_workflow_db_is_missing_and_never_falls_back(evidence_root):
    """缺 workflow.db 时只能 INCONCLUSIVE —— 回落读残留表会把 FAIL 洗成 PASS。"""
    root, data = evidence_root
    _default_state(data)
    _make(data / "sdk-product-state.db", _POLICY_DDL,
          [("insert into authorization_policy_state values (?,?,?,?,?,?,?)",
            (1, "auto", 0, 1.0, "factory_default", 2, None))])
    _progress(root, {}, policy_mode="auto")

    item = _item(root, "TF-17")
    assert item.verdict == tf.INCONCLUSIVE, item.reason


# ------------------------------------------------------------------ TF-16：读绑定跨重启


def _tf16_progress(root: Path, *, t4_root: int, t15_root: int, t15_effects: int) -> None:
    _progress(root, {
        2: {"binding_roots": 1, "binding_revisions": 1, "foreground_run_heads": 2},
        4: {"binding_roots": t4_root,
            "binding_proposals": 1 if t4_root > 1 else 0,
            "binding_revisions": 2 if t4_root > 1 else 1,
            "execution_effects": 2, "foreground_run_heads": 4},
        7: {"binding_roots": t4_root + 1, "foreground_run_heads": 7},
        15: {"binding_roots": t15_root, "execution_effects": t15_effects,
             "foreground_run_heads": 15},
    })


def test_tf16_passes_when_flow2_reuses_the_binding_made_by_the_read_gate_in_flow1(evidence_root):
    root, data = evidence_root
    _default_state(data)
    _progress(root, {})  # 占位，随后覆盖
    _tf16_progress(root, t4_root=2, t15_root=3, t15_effects=4)

    item = _item(root, "TF-16")
    assert item.verdict == tf.PASS, item.reason
    assert item.numbers["delta_binding_roots_T4"] == 1
    assert item.numbers["delta_binding_roots_T15"] == 0
    assert item.numbers["log_read_workspace_binding_revised"] == 1


def test_tf16_fails_when_the_restart_forces_a_second_binding_for_the_same_fixture(evidence_root):
    """绑定没活过重启：T15 又新增了根 —— 这正是本旅程要证伪的东西。"""
    root, data = evidence_root
    _default_state(data)
    _tf16_progress(root, t4_root=2, t15_root=4, t15_effects=4)

    item = _item(root, "TF-16")
    assert item.verdict == tf.FAIL, item.reason
    assert item.numbers["delta_binding_roots_T15"] == 1


def test_tf16_is_inconclusive_when_t4_never_triggered_the_read_gate(evidence_root):
    """T4 没新增根 = 模型压根没读那个夹具，读闸门链路未被触发，不能算 PASS。"""
    root, data = evidence_root
    _default_state(data)
    _tf16_progress(root, t4_root=1, t15_root=2, t15_effects=4)

    item = _item(root, "TF-16")
    assert item.verdict == tf.INCONCLUSIVE, item.reason


def test_tf16_is_inconclusive_when_t15_produced_no_tool_effect(evidence_root):
    """根没增只能证明「没重新绑」，还要有真实读效应才谈得上「复用旧绑定读到了」。"""
    root, data = evidence_root
    _default_state(data)
    _tf16_progress(root, t4_root=2, t15_root=3, t15_effects=2)

    item = _item(root, "TF-16")
    assert item.verdict == tf.INCONCLUSIVE, item.reason


def test_tf16_is_inconclusive_on_an_old_driver_without_the_binding_counters(evidence_root):
    root, data = evidence_root
    _default_state(data)
    running = {"execution_effects": 0, "foreground_run_heads": 0}
    with open(root / tf.PROGRESS_NAME, "w", encoding="utf-8") as fh:
        for turn in [0] + list(range(1, 23)):
            fh.write(json.dumps({
                "ts": "2026-09-09T09:00:00+0800", "turn": turn, "kind": "send",
                "phase": "flow1", "outcome": "settled", "elapsed_s": 1.0,
                "primary_conversation_id": "p", "last_run_state": "COMPLETED",
                "note": "", **running,
            }, ensure_ascii=False) + "\n")

    item = _item(root, "TF-16")
    assert item.verdict == tf.INCONCLUSIVE
    assert "旧版驱动" in item.reason


# ------------------------------------------------------------------ 核对器整体


def test_selftest_covers_every_item_in_order():
    assert tf.selftest() == 0
    assert tf.ORDER[-7:] == ["TF-16", "TF-17", "NC-T1", "NC-T2", "NC-T3", "NC-T4", "NC-T5"]
    assert len(tf.ORDER) == 22


def test_workflow_db_is_archived_in_the_sha256_manifest(evidence_root):
    """workflow.db 现在是判定依据，就必须进 SHA-256 归档清单。"""
    root, data = evidence_root
    _default_state(data)
    _make(data / "workflow.db", _POLICY_DDL)
    _progress(root, {})
    ev = tf.Evidence(str(root))
    try:
        assert "workflow.db" in ev.hashes()
        assert ev.hashes()["workflow.db"]
    finally:
        ev.close()


# ------------------------------------------------------------------ 驱动脚本


@pytest.mark.parametrize("script", [_TWOFLOW_DRIVER, _MANUAL_DRIVER])
def test_driver_scripts_parse(script: Path):
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0


@pytest.mark.parametrize("script", [_TWOFLOW_DRIVER, _MANUAL_DRIVER])
def test_both_drivers_wait_for_pending_authorization_cards_before_sending(script: Path):
    """今天的 "no new Run head after two sends" 只有这一个出口：发送前先等卡片消失。"""
    text = script.read_text(encoding="utf-8")
    assert "wait_ui_send_ready" in text, "缺少发送前的 UI 门"
    for label in ("允许一次", "允许本次绑定", "拒绝", "停止"):
        assert label in text, f"UI 门没有覆盖按钮 {label}"
    # 门必须在两次发送之前各调用一次（首发 + 重试），否则重试仍会撞同一张卡。
    body = text.split("send_confirmed()", 1)[1]
    assert body.count("wait_ui_send_ready") >= 2
    # 门永远不能把驱动卡死：有界次数由 UI_SETTLE 推出。
    assert "UI_SETTLE" in text
    # 结果带后缀后，主循环必须用前缀匹配，不能再用 `= send_failed` 全等。
    assert "send_failed*)" in text


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash 不可用")
def test_twoflow_dry_run_prints_all_22_turns_without_touching_anything(tmp_path: Path):
    userdata = tmp_path / "ud"
    evidence = tmp_path / "ev"
    out = subprocess.run(
        ["bash", str(_TWOFLOW_DRIVER), "com.example.absent", str(userdata), str(evidence), "all"],
        capture_output=True, text=True,
        env={**os.environ, "TWOFLOW_DRY_RUN": "1"},
    )
    assert out.returncode == 0, out.stderr
    for turn in range(1, 23):
        assert f"T{turn} " in out.stdout, f"T{turn} 未出现在计划里"
    # dry run 绝不落盘：不建证据目录、不建 progress、不建夹具。
    assert not evidence.exists()
    assert not userdata.exists()
    # 今天的三处对齐必须出现在计划抬头里。
    assert "workflow.db" in out.stdout
    assert "twoflow-fixture" in out.stdout


def test_the_read_fixture_lives_outside_the_task_managed_home():
    """T4/T15 读的必须是 workspace 根的严格后代、且不是任务托管家目录，

    否则读闸门的绑定提案（F-Z1b）根本不会被触发，TF-16 永远 INCONCLUSIVE。
    """
    text = _TWOFLOW_DRIVER.read_text(encoding="utf-8")
    assert 'FIXDIR="${TF_FIXTURES:-$HOME/SimpleHarnessWorkSpace/twoflow-fixture}"' in text
    assert text.count('TURNS[4]="请读一下 $FIXFILE') == 1
    assert "$FIXFILE 也再读一遍" in text, "T15 必须重读同一夹具才能证明绑定跨重启复用"


def test_twoflow_driver_reads_the_policy_from_workflow_db():
    text = _TWOFLOW_DRIVER.read_text(encoding="utf-8")
    assert 'WF="$DATA/workflow.db"' in text
    assert 'qs "$WF" "select mode from authorization_policy_state' in text
    assert 'qs "$PRODUCT" "select mode' not in text
