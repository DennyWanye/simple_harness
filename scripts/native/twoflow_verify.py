#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""两轮完整流程（含重启）证据后置校验器。

依据 plans/2026-09-09-two-flow-journey/00-PLAN.md 第 1 / 4 节, 对一次「流程一 → 重启 →
流程二」的原生真实模型跑做只读审计, 逐项给出 TF-1..TF-15 与 5 条负控的判定。

用法:
    python scripts/native/twoflow_verify.py --evidence <E1> \
        [--second-log <E2>/native.log] [--second-launch <E2>/launch.json] \
        [--installed-target <dir>] [--out <E1>/twoflow-verify.json] [--json-only]
    python scripts/native/twoflow_verify.py --selftest

<E1> 是**第一次启动**的 run 目录(它拥有 userdata), 需包含:
    userdata/data/state.db / sdk-product-state.db / human_memory_v7.db / operation-audit.db
    userdata/data/simple-harness-sdk/execution-v6.sqlite3
    native.log
    launch.json
    twoflow-progress.jsonl

设计原则(与 a6_verify.py 一致):
  * 只读打开 sqlite; 表/列缺失 -> 该项 INCONCLUSIVE, 不崩溃。
  * 「模型没做」与「Host 拦住了」必须区分; 前者 INCONCLUSIVE。
  * 重启后 UI 停摆 / provider 超时 -> BLOCKED, 不记 FAIL。
  * 逻辑遗忘只影响记忆, **不影响原始会话证据**(2026-09-07 用户产品决定第 2 条)。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import os
import re
import sqlite3
import sys
from typing import Any, Callable, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from a6_verify import (  # noqa: E402  (path shim above is intentional)
    BLOCKED,
    CREDENTIAL_PATTERNS,
    FAIL,
    INCONCLUSIVE,
    PASS,
    TERMINAL_RUN_STATES,
    Evidence as A6Evidence,
    Item,
    RoDb,
    SchemaMissing,
    _md_escape,
    _numbers_brief,
    as_text,
    build_replayer,
    maybe_json,
    sha256_file,
)

PROGRESS_NAME = "twoflow-progress.jsonl"
FLOW1_TURNS = tuple(range(1, 12))
FLOW2_TURNS = tuple(range(12, 23))
UI_TURNS = (18, 21)
SEND_TURNS = tuple(t for t in FLOW1_TURNS + FLOW2_TURNS if t not in UI_TURNS)
EXPECTED_TURNS = 22
RESTART_TURN = -1

TARGET_DIR_NEEDLE = "霜降素材"
PROCEDURE_NAME = "霜降清点"
HOST_AUDIT_SECTIONS = ("runs", "run_operations", "memory_calls")

NOTE_FORGOT = re.compile(r"(?i)forgot\s*=\s*1|已忘记|forget\s*=\s*1")
NOTE_GRANT = re.compile(r"(?i)grant\s*=\s*1|已授权")
NOTE_PRIMARY_SAME = re.compile(r"(?i)primary_id_unchanged\s*=\s*1|主对话.*不变")

MONOTONIC_COUNTERS = (
    "evidence_envelopes",
    "suppression_directives",
    "suppression_targets",
    "task_scope_events",
    "cognitive_memory_heads",
    "provider_invocations",
)


# ---------------------------------------------------------------- 证据载入


class Evidence(A6Evidence):
    """a6_verify.Evidence + twoflow 进度文件 + sdk-product-state.db + 第二段启动证据。"""

    def __init__(
        self,
        root: str,
        second_log: str | None = None,
        second_launch: str | None = None,
    ) -> None:
        super().__init__(root)
        data = os.path.join(self.root, "userdata", "data")
        self.paths.pop("a6-progress.jsonl", None)
        self.paths[PROGRESS_NAME] = os.path.join(self.root, PROGRESS_NAME)
        self.paths["sdk-product-state.db"] = os.path.join(data, "sdk-product-state.db")
        self.paths["launch.json"] = os.path.join(self.root, "launch.json")
        self.progress = self._read_progress(self.paths[PROGRESS_NAME])
        self.product = RoDb(self.paths["sdk-product-state.db"], "sdk-product-state.db")
        self.second_log_path = second_log
        self.second_launch_path = second_launch
        if second_log:
            self.paths["native.log(second)"] = second_log
        if second_launch:
            self.paths["launch.json(second)"] = second_launch
        self.second_log = self._read_text(second_log) if second_log else ""

    def close(self) -> None:
        super().close()
        self.product.close()

    # ---- launch 元数据 ----
    @staticmethod
    def _read_json(path: str | None) -> dict[str, Any]:
        if not path or not os.path.isfile(path):
            return {}
        with contextlib.suppress(Exception):
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                obj = json.load(fh)
            if isinstance(obj, dict):
                return obj
        return {}

    def launch_first(self) -> dict[str, Any]:
        return self._read_json(self.paths.get("launch.json"))

    def launch_second(self) -> dict[str, Any]:
        return self._read_json(self.second_launch_path)

    # ---- 逐轮计数 ----
    def counter_rows(self) -> dict[int, dict[str, Any]]:
        """turn -> row。T0 取第一条(基线), 其余取最后一条(支持 --start 续跑)。"""
        out: dict[int, dict[str, Any]] = {}
        for row in self.progress:
            turn = row.get("turn")
            if not isinstance(turn, int):
                continue
            if turn == 0 and 0 in out:
                continue
            out[turn] = row
        return out

    def restart_rows(self) -> list[dict[str, Any]]:
        return [
            r for r in self.progress
            if r.get("turn") == RESTART_TURN or as_text(r.get("outcome")) == "restart"
        ]

    def value_at(self, turn: int, key: str) -> int | None:
        row = self.counter_rows().get(turn)
        if row is None:
            return None
        value = row.get(key)
        return int(value) if isinstance(value, int) else None

    def prev_turn(self, turn: int) -> int | None:
        earlier = [t for t in self.counter_rows() if 0 <= t < turn]
        return max(earlier) if earlier else None

    def delta(self, turn: int, key: str) -> int | None:
        now = self.value_at(turn, key)
        prev_t = self.prev_turn(turn)
        if now is None or prev_t is None:
            return None
        before = self.value_at(prev_t, key)
        return None if before is None else now - before

    def note(self, turn: int) -> str:
        row = self.counter_rows().get(turn)
        return as_text(row.get("note")) if row else ""

    def outcome(self, turn: int) -> str:
        row = self.counter_rows().get(turn)
        return as_text(row.get("outcome")) if row else ""

    def phase(self, turn: int) -> str:
        row = self.counter_rows().get(turn)
        return as_text(row.get("phase")) if row else ""

    def time_window(self, first_turn: int, last_turn: int | None = None) -> tuple[float, float]:
        """[first_turn 的上一条记录 ts, last_turn 的 ts] —— 用于按时间归属 DB 行。"""
        last_turn = first_turn if last_turn is None else last_turn
        prev = self.prev_turn(first_turn)
        rows = self.counter_rows()
        start = float(rows[prev].get("_ts_epoch") or 0.0) if prev is not None else 0.0
        end_row = rows.get(last_turn)
        end = float(end_row.get("_ts_epoch") or 0.0) if end_row else 0.0
        if end <= 0.0:
            end = float("inf")
        return start, end

    def bad_turns(self, turns: Sequence[int]) -> list[int]:
        return [
            t for t in turns
            if t not in self.counter_rows() or self.outcome(t) in {"timeout", "send_failed"}
        ]

    def primary_ids(self) -> list[str]:
        seen: list[str] = []
        for turn in sorted(self.counter_rows()):
            pid = as_text(self.counter_rows()[turn].get("primary_conversation_id"))
            if pid and pid != "none" and pid not in seen:
                seen.append(pid)
        return seen

    def max_turn(self) -> int:
        turns = [t for t in self.counter_rows() if t > 0]
        return max(turns) if turns else 0

    # ---- DB 便捷读取 ----
    def route_rows(self) -> list[sqlite3.Row]:
        self.state.require("context_route_decisions", "route", "task_scope_id")
        return self.state.rows(
            "select route, origin, task_scope_id, recorded_at from context_route_decisions"
            " order by recorded_at asc"
        )

    def scope_titles(self) -> dict[str, str]:
        if not self.state.has("task_scopes", "task_scope_id", "title"):
            return {}
        with contextlib.suppress(SchemaMissing):
            return {
                as_text(r[0]): as_text(r[1])
                for r in self.state.rows("select task_scope_id, title from task_scopes")
            }
        return {}

    def suppressed_memory_refs(self) -> list[str]:
        refs: list[str] = []
        if self.hm.has("suppression_directives", "scope_kind", "scope_ref", "event_kind"):
            with contextlib.suppress(SchemaMissing):
                for r in self.hm.rows(
                    "select scope_ref from suppression_directives"
                    " where upper(scope_kind)='MEMORY' and event_kind='directive'"
                ):
                    if r[0]:
                        refs.append(as_text(r[0]))
        if self.hm.has("suppression_targets", "target_ref"):
            with contextlib.suppress(SchemaMissing):
                for r in self.hm.rows("select target_ref from suppression_targets"):
                    if r[0]:
                        refs.append(as_text(r[0]))
        return sorted(set(refs))

    def audit_sections(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in ("human_audit_host_streams", "human_audit_host_deliveries"):
            if not self.audit.has(table, "section"):
                continue
            with contextlib.suppress(SchemaMissing):
                for r in self.audit.rows(f"select section, count(*) from {table} group by section"):
                    key = as_text(r[0])
                    out[key] = out.get(key, 0) + int(r[1] or 0)
        return out

    def log_all(self) -> str:
        return self.native_log + "\n" + self.second_log


# ---------------------------------------------------------------- TF-1 .. TF-15


def item_tf_1(ev: Evidence) -> Item:
    it = Item("TF-1", "唯一永久主对话跨重启不变")
    ev.state.require("human_memory_primary_conversations", "primary_conversation_id", "writable")
    total = ev.state.count("human_memory_primary_conversations")
    writable = ev.state.count("human_memory_primary_conversations", "writable=1")
    ids = ev.primary_ids()
    l1, l2 = ev.launch_first(), ev.launch_second()
    ud1, ud2 = as_text(l1.get("userdata")), as_text(l2.get("userdata"))
    it.numbers = {
        "primary_conversations": total,
        "writable": writable,
        "distinct_ids_in_progress": ids,
        "launch1_userdata": ud1,
        "launch2_userdata": ud2,
    }
    if total == 0:
        it.verdict = INCONCLUSIVE
        it.reason = "主对话表为空(冷启动未完成?)。"
        return it
    if total > 1 or writable > 1:
        it.verdict = FAIL
        it.reason = f"主对话 {total} 行 / 可写 {writable} 行 —— 违反唯一主对话约束。"
        return it
    if len(ids) > 1:
        it.verdict = FAIL
        it.reason = f"progress 记录到多个主对话 ID: {ids} —— 重启后换了主对话。"
        return it
    if ud1 and ud2 and os.path.normpath(ud1) != os.path.normpath(ud2):
        it.verdict = FAIL
        it.reason = f"两次启动的 userdata 不同({ud1} vs {ud2}), 不是同 userdata 重启。"
        return it
    if not ud2:
        it.verdict = INCONCLUSIVE
        it.reason = (
            f"主对话恒为 1 行(id={ids[0] if ids else '未知'}), 但缺 --second-launch, "
            "无法证明两次启动共用 userdata。"
        )
        return it
    it.verdict = PASS
    it.reason = f"主对话恒为 1 行且可写 1 行, ID 全程为 {ids[0] if ids else '未知'}, 两次启动共用同一 userdata。"
    return it


def item_tf_2(ev: Evidence) -> Item:
    it = Item("TF-2", "≥20 turn 长上下文")
    ev.state.require("foreground_run_heads", "host_run_id", "current_state")
    runs = ev.state.rows("select host_run_id, current_state from foreground_run_heads")
    non_terminal = [
        as_text(r["host_run_id"]) for r in runs
        if as_text(r["current_state"]).upper() not in TERMINAL_RUN_STATES
    ]
    bad = ev.bad_turns(SEND_TURNS)
    it.numbers = {
        "turns_recorded": ev.max_turn(),
        "expected_turns": EXPECTED_TURNS,
        "send_turns": len(SEND_TURNS),
        "foreground_run_heads": len(runs),
        "non_terminal_runs": len(non_terminal),
        "non_terminal_sample": non_terminal[:5],
        "bad_turns": bad,
    }
    if ev.max_turn() < EXPECTED_TURNS:
        it.verdict = INCONCLUSIVE
        it.reason = f"progress 只记录到 T{ev.max_turn()}/{EXPECTED_TURNS}, 脚本未跑完。"
        return it
    if non_terminal:
        it.verdict = BLOCKED
        it.reason = f"{len(non_terminal)} 个 Run 非终态(疑似 F06 传输超时停摆), 按 plan 第 4 节记 BLOCKED。"
        return it
    if bad:
        it.verdict = INCONCLUSIVE
        it.reason = f"T{bad} timeout/send_failed, 长上下文完备性不可判。"
        return it
    if len(runs) < len(SEND_TURNS):
        it.verdict = INCONCLUSIVE
        it.reason = f"终态 Run 仅 {len(runs)} 个, 少于 {len(SEND_TURNS)} 个发送轮。"
        return it
    it.verdict = PASS
    it.reason = f"{len(SEND_TURNS)} 个发送轮全部完成, {len(runs)} 个 Run 全部终态。"
    return it


def item_tf_3(ev: Evidence) -> Item:
    it = Item("TF-3", "重启确实发生且是冷启动")
    restarts = ev.restart_rows()
    phases = {ev.phase(t) for t in ev.counter_rows() if t > 0}
    l1, l2 = ev.launch_first(), ev.launch_second()
    pid1, pid2 = l1.get("pid"), l2.get("pid")
    log2_bytes = len(ev.second_log)
    it.numbers = {
        "restart_markers": len(restarts),
        "restart_note": as_text(restarts[0].get("note")) if restarts else "",
        "phases_seen": sorted(p for p in phases if p),
        "launch1_pid": pid1,
        "launch2_pid": pid2,
        "second_native_log_bytes": log2_bytes,
        "second_log_path": ev.second_log_path,
    }
    if "flow1" not in phases or "flow2" not in phases:
        it.verdict = INCONCLUSIVE
        it.reason = f"progress 里只见到阶段 {sorted(phases)}, 两个流程未都跑到。"
        return it
    if not restarts:
        it.verdict = INCONCLUSIVE
        it.reason = "progress 没有 restart 标记行, 无法确认重启动作发生过。"
        return it
    if pid1 is None or pid2 is None:
        it.verdict = INCONCLUSIVE
        it.reason = "缺 launch.json(--second-launch), 无法用 pid 证明是两个进程。"
        return it
    if pid1 == pid2:
        it.verdict = FAIL
        it.reason = f"两次启动 pid 相同({pid1}) —— 并没有真正重启。"
        return it
    if log2_bytes == 0:
        it.verdict = INCONCLUSIVE
        it.reason = "第二段 native.log 为空或未提供, 冷启动过程无日志证据。"
        return it
    it.verdict = PASS
    it.reason = f"flow1/flow2 两阶段齐全, 两次启动 pid {pid1} → {pid2}, 第二段日志 {log2_bytes} 字节。"
    return it


def item_tf_4(ev: Evidence) -> Item:
    it = Item("TF-4", "两个 TaskScope")
    scopes = ev.scope_titles()
    routes = ev.route_rows()
    creates = [r for r in routes if as_text(r["route"]) == "create_new"]
    it.numbers = {
        "task_scopes": len(scopes),
        "titles": list(scopes.values())[:6],
        "route_create_new": len(creates),
        "create_scope_ids": sorted({as_text(r["task_scope_id"]) for r in creates}),
    }
    if len(scopes) < 2:
        it.verdict = INCONCLUSIVE
        it.reason = f"只建了 {len(scopes)} 个 TaskScope, 第二个任务未成立。"
        return it
    if len(creates) < 2:
        it.verdict = INCONCLUSIVE
        it.reason = f"create_new 路由只出现 {len(creates)} 次, 无法证明两次独立建档。"
        return it
    it.verdict = PASS
    it.reason = f"{len(scopes)} 个 TaskScope, {len(creates)} 次 create_new 路由。"
    return it


def item_tf_5(ev: Evidence) -> Item:
    it = Item("TF-5", "exact resume 不混任务")
    searches = opens = None
    if ev.state.has("task_scope_search_access_receipts", "operation"):
        searches = ev.state.count("task_scope_search_access_receipts", "operation='search'")
        opens = ev.state.count("task_scope_search_access_receipts", "operation='open'")
    routes = ev.route_rows()
    all_resumes = [r for r in routes if as_text(r["route"]) == "resume_existing"]
    # 只看 T13–T15 这个恢复窗口: T22 给任务 B 收口也会走 resume_existing, 不能算"混任务"。
    start, end = ev.time_window(13, 15)
    resumes = [
        r for r in all_resumes
        if start < float(r["recorded_at"] or 0.0) <= end
    ] or all_resumes[:1]
    scopes = ev.scope_titles()
    resumed_ids = sorted({as_text(r["task_scope_id"]) for r in resumes if r["task_scope_id"]})
    resumed_titles = [scopes.get(i, i) for i in resumed_ids]
    target = [t for t in resumed_titles if TARGET_DIR_NEEDLE in t]
    d_search = ev.delta(13, "task_scope_search_ops")
    d_open = ev.delta(14, "task_scope_open_ops")
    it.numbers = {
        "search_receipts": searches,
        "open_receipts": opens,
        "delta_search_T13": d_search,
        "delta_open_T14": d_open,
        "route_resume_existing_total": len(all_resumes),
        "route_resume_existing_in_T13_T15": len(resumes),
        "resume_window": [start, end],
        "resumed_titles": resumed_titles,
    }
    if searches is None:
        it.verdict = INCONCLUSIVE
        it.reason = "task_scope_search_access_receipts 缺表, 无法判定。"
        return it
    if 13 not in ev.counter_rows() or 14 not in ev.counter_rows():
        it.verdict = INCONCLUSIVE
        it.reason = "缺 T13/T14 进度记录, 无法把 resume 归属到恢复窗口。"
        return it
    if ev.bad_turns((13, 14)):
        it.verdict = INCONCLUSIVE
        it.reason = f"T{ev.bad_turns((13, 14))} timeout/send_failed, 任务发现与恢复未走完。"
        return it
    if searches == 0:
        it.verdict = INCONCLUSIVE
        it.reason = (
            "全程 0 次 task_scope_search —— T13 的模糊搜索没有发生(索引未落库或模型没搜), "
            "按 plan 第 4 节改用「列出最近任务后精确打开」重试一次。"
        )
        return it
    if not resumes:
        it.verdict = INCONCLUSIVE
        it.reason = f"有 {searches} 次搜索、{opens} 次打开, 但没有 resume_existing 路由, exact resume 未成立。"
        return it
    if len(resumed_titles) > 1:
        it.verdict = FAIL
        it.reason = f"T13–T15 恢复窗口内 resume 命中多个任务 {resumed_titles} —— 恢复混入了其他任务。"
        return it
    if not target:
        it.verdict = FAIL
        it.reason = f"resume 打开的是 {resumed_titles}, 不是「{TARGET_DIR_NEEDLE}…」那个任务 —— 搜错任务。"
        return it
    it.verdict = PASS
    it.reason = (
        f"{searches} 次搜索 → {opens} 次 exact open → 恢复窗口内 {len(resumes)} 次 resume_existing, "
        f"恢复的正是 {resumed_titles}。"
    )
    return it


def item_tf_6(ev: Evidence) -> Item:
    it = Item("TF-6", "跨重启类型化召回")
    d_req = ev.delta(12, "typed_recall_requests")
    d_res = ev.delta(12, "typed_recall_results")
    total_req = ev.value_at(22, "typed_recall_requests")
    bad = ev.bad_turns((12,))
    it.numbers = {
        "delta_typed_recall_requests_T12": d_req,
        "delta_typed_recall_results_T12": d_res,
        "typed_recall_requests_total": total_req,
        "phase_T12": ev.phase(12),
        "bad_turns": bad,
    }
    if d_req is None:
        it.verdict = INCONCLUSIVE
        it.reason = "T12 无计数记录, 无法判定。"
        return it
    if bad:
        it.verdict = INCONCLUSIVE
        it.reason = "T12 timeout/send_failed, 跨重启召回未走完。"
        return it
    if ev.phase(12) != "flow2":
        it.verdict = INCONCLUSIVE
        it.reason = f"T12 的 phase 记为 {ev.phase(12)!r}, 不在重启之后, 该项失去意义。"
        return it
    if d_req <= 0:
        it.verdict = INCONCLUSIVE
        it.reason = (
            "T12 窗口没有任何 typed recall 请求 —— 即使答对了也可能来自最近上下文而非召回, "
            "跨重启召回未被证明(plan 第 4 节)。"
        )
        return it
    it.verdict = PASS
    it.reason = f"重启后的 T12 新增 {d_req} 次 typed recall 请求 / {d_res} 条结果。"
    return it


def item_tf_7(ev: Evidence) -> Item:
    it = Item("TF-7", "Procedure 一等能力 + 跨重启使用")
    proc_records = ev.value_at(22, "procedure_records")
    proc_heads = ev.value_at(22, "heads_procedure")
    d_build = ev.delta(9, "procedure_records")
    d_use = ev.delta(16, "procedure_uses")
    uses_total = ev.value_at(22, "procedure_uses")
    names: list[str] = []
    if ev.hm.has("procedure_records", "name"):
        with contextlib.suppress(SchemaMissing):
            names = [as_text(r[0]) for r in ev.hm.rows("select name from procedure_records")]
    it.numbers = {
        "procedure_records": proc_records,
        "heads_procedure": proc_heads,
        "delta_procedure_records_T9": d_build,
        "delta_procedure_uses_T16": d_use,
        "procedure_uses_total": uses_total,
        "procedure_names": names[:5],
    }
    if proc_records is None:
        it.verdict = INCONCLUSIVE
        it.reason = "缺 procedure_records 计数, 无法判定。"
        return it
    if proc_records == 0:
        it.verdict = INCONCLUSIVE
        it.reason = "T9 没有建立任何 Procedure 记忆(分析通道未落库或模型未按程序保存)。"
        return it
    if not uses_total:
        it.verdict = INCONCLUSIVE
        it.reason = (
            f"已建 {proc_records} 条 Procedure, 但 procedure_uses 为 0 —— T16 没走程序路径; "
            f"按 plan 第 4 节改说「按我存过的名字叫「{PROCEDURE_NAME}」的那个流程做」重试一次。"
        )
        return it
    if (d_use or 0) <= 0:
        it.verdict = INCONCLUSIVE
        it.reason = f"procedure_uses 合计 {uses_total} 但不在 T16 窗口, 时间窗归属需人工核。"
        return it
    it.verdict = PASS
    it.reason = f"flow1 建立 {proc_records} 条 Procedure, flow2 的 T16 新增 {d_use} 次真实使用。"
    return it


def item_tf_8(ev: Evidence) -> Item:
    it = Item("TF-8", "Prospective 明确触发才 pending")
    d = ev.delta(10, "prospective_records")
    total = ev.value_at(22, "prospective_records")
    heads = ev.value_at(22, "heads_prospective")
    triggers: list[str] = []
    if ev.hm.has("prospective_records", "trigger_kind"):
        with contextlib.suppress(SchemaMissing):
            triggers = [
                as_text(r[0]) for r in ev.hm.rows("select trigger_kind from prospective_records")
            ]
    untriggered = [t for t in triggers if not t]
    it.numbers = {
        "delta_prospective_records_T10": d,
        "prospective_records_total": total,
        "heads_prospective": heads,
        "trigger_kinds": triggers[:5],
        "records_without_trigger": len(untriggered),
    }
    if total is None:
        it.verdict = INCONCLUSIVE
        it.reason = "缺 prospective_records 计数, 无法判定。"
        return it
    if total == 0:
        it.verdict = INCONCLUSIVE
        it.reason = "T10 的「收尾后提醒」没有产生任何 Prospective 记录。"
        return it
    if untriggered:
        it.verdict = FAIL
        it.reason = f"{len(untriggered)} 条 Prospective 没有 trigger_kind —— 缺触发却进了记录。"
        return it
    it.verdict = PASS
    it.reason = f"{total} 条 Prospective 全部带触发({sorted(set(triggers))}), T10 新增 {d} 条。"
    return it


def item_tf_9(ev: Evidence) -> Item:
    it = Item("TF-9", "逻辑遗忘落 append-only suppression")
    d_dir = ev.delta(18, "suppression_directives")
    d_tgt = ev.delta(18, "suppression_targets")
    note = ev.note(18)
    kinds: list[tuple[str, str, str]] = []
    if ev.hm.has("suppression_directives", "event_kind", "scope_kind", "reason_code"):
        with contextlib.suppress(SchemaMissing):
            kinds = [
                (as_text(r[0]), as_text(r[1]), as_text(r[2]))
                for r in ev.hm.rows(
                    "select event_kind, scope_kind, reason_code from suppression_directives"
                )
            ]
    user_forget = [k for k in kinds if k[2] == "user_forget" and k[0] == "directive"]
    it.numbers = {
        "delta_suppression_directives_T18": d_dir,
        "delta_suppression_targets_T18": d_tgt,
        "directives": kinds[:6],
        "user_forget_directives": len(user_forget),
        "note_T18": note,
        "note_says_forgot": bool(NOTE_FORGOT.search(note)),
    }
    if d_dir is None:
        it.verdict = INCONCLUSIVE
        it.reason = "T18 无计数记录, 无法判定。"
        return it
    if d_dir < 0 or (d_tgt or 0) < 0:
        it.verdict = FAIL
        it.reason = "suppression 行数减少 —— 违反 append-only / 物理删除禁令。"
        return it
    if d_dir == 0:
        if not NOTE_FORGOT.search(note):
            it.verdict = INCONCLUSIVE
            it.reason = "T18 既无 suppression 增量, note 也未记录点过忘记 —— UI 步骤可能没做。"
            return it
        it.verdict = BLOCKED
        it.reason = "note 记录点了「忘记这条记忆」, 但 suppression_directives 无增量 —— UI 与权威层脱节。"
        return it
    if not user_forget:
        it.verdict = FAIL
        it.reason = f"新增了 suppression 但没有 reason_code='user_forget' 的 directive: {kinds[:6]}。"
        return it
    it.verdict = PASS
    it.reason = (
        f"T18 新增 {d_dir} 条 directive / {d_tgt} 条 target, "
        f"其中 {len(user_forget)} 条为 MEMORY/user_forget。"
    )
    return it


def item_tf_10(ev: Evidence) -> Item:
    it = Item("TF-10", "遗忘后普通召回不可见")
    refs = ev.suppressed_memory_refs()
    d_items = ev.delta(19, "typed_recall_result_items")
    hits: list[str] = []
    scanned = 0
    if refs and ev.hm.has("typed_recall_result_items", "result_item_json", "result_id"):
        with contextlib.suppress(SchemaMissing):
            rows = ev.hm.rows(
                "select r.created_at, i.result_item_json from typed_recall_result_items i"
                " join typed_recall_results r on r.result_id = i.result_id"
                " order by r.created_at asc"
            )
            # 只看遗忘之后产生的结果: 用最后 N 条近似(遗忘发生在倒数第 2 个召回窗口之前)
            after = rows[-(d_items or 0):] if (d_items or 0) > 0 else []
            scanned = len(after)
            for row in after:
                text = as_text(row[1])
                for ref in refs:
                    if ref and ref in text:
                        hits.append(ref)
    it.numbers = {
        "suppressed_refs": refs[:5],
        "suppressed_ref_count": len(refs),
        "delta_recall_items_T19": d_items,
        "post_forget_items_scanned": scanned,
        "suppressed_refs_hit": sorted(set(hits)),
        "note_T19": ev.note(19),
    }
    if not refs:
        it.verdict = INCONCLUSIVE
        it.reason = "没有任何 MEMORY suppression, T18 未生效, 该项无对象。"
        return it
    if d_items is None:
        it.verdict = INCONCLUSIVE
        it.reason = "T19 无计数记录, 无法判定。"
        return it
    if hits:
        it.verdict = FAIL
        it.reason = f"遗忘后的召回结果仍命中被抑制的记忆: {sorted(set(hits))}。"
        return it
    if (d_items or 0) == 0:
        it.verdict = INCONCLUSIVE
        it.reason = (
            "T19 没有产生任何召回结果项 —— 可能是「无召回」而非「召回后被过滤」, "
            "需人工核对模型回答是否明确表示不再持有该记忆。"
        )
        return it
    it.verdict = PASS
    it.reason = f"T19 新增 {d_items} 条召回项, 对 {len(refs)} 个被抑制引用零命中。"
    return it


def item_tf_11(ev: Evidence) -> Item:
    it = Item("TF-11", "原始会话文本仍在(产品决定 #2)")
    before = ev.value_at(17, "evidence_envelopes")
    after = ev.value_at(18, "evidence_envelopes")
    final = ev.value_at(22, "evidence_envelopes")
    bad = ev.bad_turns((20,))
    it.numbers = {
        "evidence_envelopes_before_T18": before,
        "evidence_envelopes_after_T18": after,
        "evidence_envelopes_final": final,
        "note_T20": ev.note(20),
        "bad_turns": bad,
    }
    if before is None or after is None:
        it.verdict = INCONCLUSIVE
        it.reason = "缺 evidence_envelopes 计数, 无法判定。"
        return it
    if after < before or (final is not None and final < after):
        it.verdict = FAIL
        it.reason = (
            f"原始证据从 {before} 降到 {after}/{final} —— 遗忘物理删除了会话证据, "
            "违反物理删除禁令与 2026-09-07 产品决定第 2 条。"
        )
        return it
    if bad:
        it.verdict = INCONCLUSIVE
        it.reason = "T20 timeout/send_failed, 「原话仍可翻到」未取到模型侧证据。"
        return it
    it.verdict = PASS
    it.reason = f"遗忘前后原始证据 {before} → {after} → {final}, 只增不减; T20 已完成复核。"
    return it


def item_tf_12(ev: Evidence) -> Item:
    it = Item("TF-12", "受控审计面(含本机执行审计)")
    grants = ev.value_at(22, "human_audit_grants")
    deliveries = ev.value_at(22, "human_audit_deliveries")
    streams = ev.value_at(22, "human_audit_host_streams")
    host_del = ev.value_at(22, "human_audit_host_deliveries")
    sections = ev.audit_sections()
    missing = [s for s in HOST_AUDIT_SECTIONS if s not in sections]
    note = ev.note(21)
    it.numbers = {
        "human_audit_grants": grants,
        "human_audit_deliveries": deliveries,
        "human_audit_host_streams": streams,
        "human_audit_host_deliveries": host_del,
        "host_sections": sections,
        "missing_sections": missing,
        "note_T21": note,
    }
    if grants is None:
        it.verdict = INCONCLUSIVE
        it.reason = "缺 operation-audit.db 计数, 无法判定。"
        return it
    if grants == 0:
        if NOTE_GRANT.search(note):
            it.verdict = BLOCKED
            it.reason = "note 记录已取得审计授权, 但 human_audit_grants 为 0 —— 受控面未落库。"
            return it
        it.verdict = INCONCLUSIVE
        it.reason = "没有任何审计 grant, T21 的受控取证未执行。"
        return it
    if not deliveries:
        it.verdict = INCONCLUSIVE
        it.reason = f"有 {grants} 个 grant 但 0 条记忆页投递 —— 只授权没翻页。"
        return it
    if missing:
        it.verdict = INCONCLUSIVE
        it.reason = (
            f"「本机执行审计」缺分节 {missing}(已有 {sorted(sections)}) —— "
            "三个分节未全部真实读过。"
        )
        return it
    it.verdict = PASS
    it.reason = (
        f"grant {grants} / 记忆页投递 {deliveries} / host 流 {streams} / host 投递 {host_del}, "
        f"三个分节 {sorted(sections)} 全部被读过。"
    )
    return it


def item_tf_13(ev: Evidence) -> Item:
    it = Item("TF-13", "两个任务都正向收口")
    closures = ev.value_at(22, "task_scope_closure_receipts")
    d11 = ev.delta(11, "task_scope_closure_receipts")
    d22 = ev.delta(22, "task_scope_closure_receipts")
    completed: list[str] = []
    if ev.state.has("task_scope_canonical_revisions", "task_scope_id", "state_json", "revision"):
        with contextlib.suppress(SchemaMissing):
            latest: dict[str, tuple[int, str]] = {}
            for r in ev.state.rows(
                "select task_scope_id, revision, state_json from task_scope_canonical_revisions"
            ):
                sid, rev, js = as_text(r[0]), int(r[1] or 0), as_text(r[2])
                if sid not in latest or rev > latest[sid][0]:
                    latest[sid] = (rev, js)
            for sid, (_rev, js) in latest.items():
                obj = maybe_json(js) or {}
                status = as_text(obj.get("status")) if isinstance(obj, dict) else ""
                closure = as_text(obj.get("closure_reason")) if isinstance(obj, dict) else ""
                if status and status not in {"active", "paused", "blocked"}:
                    completed.append(f"{sid}:{status}")
                elif closure:
                    completed.append(f"{sid}:closure_reason")
    it.numbers = {
        "task_scope_closure_receipts": closures,
        "delta_closure_T11": d11,
        "delta_closure_T22": d22,
        "scopes_in_terminal_status": completed,
    }
    if closures is None:
        it.verdict = INCONCLUSIVE
        it.reason = "缺 task_scope_closure_receipts 计数, 无法判定。"
        return it
    if len(completed) >= 2:
        it.verdict = PASS
        it.reason = f"两个任务均进入完成态: {completed}; closure 回执 {closures} 条。"
        return it
    if closures == 0:
        it.verdict = INCONCLUSIVE
        it.reason = "没有任何 closure 回执, T11/T22 的收口未发生。"
        return it
    it.verdict = INCONCLUSIVE
    it.reason = (
        f"closure 回执 {closures} 条, 但只有 {len(completed)} 个任务的 canonical status 到达完成态: "
        f"{completed} —— 需人工核 state_json 的完成语义。"
    )
    return it


def item_tf_14(ev: Evidence) -> Item:
    it = Item("TF-14", "物理删除禁令(计数单调不减)")
    rows = sorted(t for t in ev.counter_rows() if t >= 0)
    drops: dict[str, list[str]] = {}
    for key in MONOTONIC_COUNTERS:
        seq = [(t, ev.value_at(t, key)) for t in rows]
        seq = [(t, v) for t, v in seq if v is not None]
        for (t0, v0), (t1, v1) in zip(seq, seq[1:]):
            if v1 < v0:
                drops.setdefault(key, []).append(f"T{t0}:{v0}->T{t1}:{v1}")
    it.numbers = {"tracked": list(MONOTONIC_COUNTERS), "drops": drops, "turns": len(rows)}
    if len(rows) < 2:
        it.verdict = INCONCLUSIVE
        it.reason = "进度记录不足两行, 无法判断单调性。"
        return it
    if drops:
        it.verdict = FAIL
        it.reason = f"计数出现回落(疑似物理删除): {drops}。"
        return it
    it.verdict = PASS
    it.reason = f"{len(MONOTONIC_COUNTERS)} 个计数在 {len(rows)} 条记录(含重启前后)中单调不减。"
    return it


def item_tf_15(
    ev: Evidence,
    replay: Callable[[str, str], str] | None,
    replay_info: dict[str, Any] | None = None,
) -> Item:
    it = Item("TF-15", "snapshot 重放指纹")
    invs = ev.invocations()
    info = replay_info or {}
    if replay is None:
        it.verdict = INCONCLUSIVE
        it.reason = (
            "无法重放指纹: " + (as_text(info.get("error")) or "未提供 --installed-target")
            + "(注意: 重放需要安装目标所用的 Python 3.12, 例如 backend/.venv/bin/python)。"
        )
        it.numbers = {"provider_invocations": len(invs), "replay": info}
        return it
    mismatch: list[str] = []
    checked = 0
    for inv in invs:
        try:
            # a6_verify.build_replayer 的签名是 replay(request_id, request_json_text)
            got = replay(inv.request_id, inv.request_json_text)
        except Exception as exc:  # noqa: BLE001
            it.verdict = INCONCLUSIVE
            it.reason = f"重放异常 {type(exc).__name__}: {exc}"
            it.numbers = {"provider_invocations": len(invs), "checked": checked}
            return it
        checked += 1
        if got != inv.request_fingerprint:
            mismatch.append(inv.invocation_id)
    it.numbers = {
        "provider_invocations": len(invs),
        "checked": checked,
        "mismatch": len(mismatch),
        "mismatch_sample": mismatch[:5],
    }
    if not invs:
        it.verdict = INCONCLUSIVE
        it.reason = "无 provider_invocations, 无从重放。"
        return it
    if mismatch:
        it.verdict = FAIL
        it.reason = f"{len(mismatch)}/{checked} 次请求的重放指纹与记录不符。"
        return it
    it.verdict = PASS
    it.reason = f"{checked}/{len(invs)} 次请求重放指纹全等。"
    return it


# ---------------------------------------------------------------- 负控 NC-T1..5


def item_nc_t1(ev: Evidence) -> Item:
    it = Item("NC-T1", "T6 简单改写不建/不切 TaskScope")
    d_create = ev.delta(6, "route_create_new")
    d_scope = ev.delta(6, "task_scopes")
    d_recall = ev.delta(6, "typed_recall_requests")
    d_norecall = ev.delta(6, "route_no_recall")
    it.numbers = {
        "delta_create_new_T6": d_create,
        "delta_task_scopes_T6": d_scope,
        "delta_typed_recall_requests_T6": d_recall,
        "delta_no_recall_origin_T6": d_norecall,
    }
    if d_create is None:
        it.verdict = INCONCLUSIVE
        it.reason = "T6 无计数记录, 无法判定。"
        return it
    if d_create > 0 or (d_scope or 0) > 0:
        it.verdict = FAIL
        it.reason = f"T6 简单改写建了 {d_scope} 个 TaskScope / {d_create} 次 create_new。"
        return it
    if (d_recall or 0) > 0 and (d_norecall or 0) == 0:
        it.verdict = INCONCLUSIVE
        it.reason = f"T6 未建档, 但触发了 {d_recall} 次长期召回且未记 no_recall —— 需人工核路由理由。"
        return it
    it.verdict = PASS
    it.reason = f"T6 未建/未切 TaskScope, 长期召回增量 {d_recall}, no_recall 增量 {d_norecall}。"
    return it


def item_nc_t2(ev: Evidence) -> Item:
    it = Item("NC-T2", "T17 模糊愿望不产生 pending Prospective")
    d_rec = ev.delta(17, "prospective_records")
    d_reg = ev.delta(17, "prospective_scheduler_registrations")
    d_sem = ev.delta(17, "heads_semantic")
    it.numbers = {
        "delta_prospective_records_T17": d_rec,
        "delta_scheduler_registrations_T17": d_reg,
        "delta_heads_semantic_T17": d_sem,
    }
    if d_rec is None:
        it.verdict = INCONCLUSIVE
        it.reason = "T17 无计数记录, 无法判定。"
        return it
    if d_rec > 0 or (d_reg or 0) > 0:
        it.verdict = FAIL
        it.reason = f"模糊愿望产生了 {d_rec} 条 Prospective / {d_reg} 个调度注册。"
        return it
    it.verdict = PASS
    it.reason = f"T17 未产生 Prospective 或调度注册(语义记忆增量 {d_sem})。"
    return it


def item_nc_t3(ev: Evidence) -> Item:
    it = Item("NC-T3", "纯 UI 轮不新增 provider 调用")
    deltas = {t: ev.delta(t, "provider_invocations") for t in UI_TURNS}
    positive = {t: d for t, d in deltas.items() if d and d > 0}
    it.numbers = {"delta_provider_invocations": deltas}
    if all(d is None for d in deltas.values()):
        it.verdict = INCONCLUSIVE
        it.reason = "缺 UI 轮计数, 无法判定。"
        return it
    if positive:
        it.verdict = FAIL
        it.reason = f"纯 UI 轮新增了 provider 调用: {positive}。"
        return it
    it.verdict = PASS
    it.reason = f"T{list(UI_TURNS)} 两轮 provider 调用增量均为 0。"
    return it


def item_nc_t4(ev: Evidence) -> Item:
    it = Item("NC-T4", "request_json 无凭据形状")
    invs = ev.invocations()
    hits: dict[str, int] = {}
    for inv in invs:
        for name, pattern in CREDENTIAL_PATTERNS:
            if pattern.search(inv.request_json_text):
                hits[name] = hits.get(name, 0) + 1
    it.numbers = {"provider_invocations": len(invs), "credential_hits": hits}
    if not invs:
        it.verdict = INCONCLUSIVE
        it.reason = "无 provider_invocations, 无从扫描。"
        return it
    if hits:
        it.verdict = FAIL
        it.reason = f"请求体命中凭据形状: {hits}。"
        return it
    it.verdict = PASS
    it.reason = f"{len(invs)} 次请求全部无凭据形状命中。"
    return it


def item_nc_t5(ev: Evidence) -> Item:
    it = Item("NC-T5", "重启不产生第二条可写主对话")
    ev.state.require("human_memory_primary_conversations", "writable")
    writable = ev.state.count("human_memory_primary_conversations", "writable=1")
    track = [
        (t, ev.value_at(t, "primary_conversations_writable"))
        for t in sorted(ev.counter_rows()) if t >= 0
    ]
    track = [(t, v) for t, v in track if v is not None]
    over = [f"T{t}:{v}" for t, v in track if v > 1]
    it.numbers = {"writable_now": writable, "writable_over_time": over or "全程 ≤1"}
    if writable > 1 or over:
        it.verdict = FAIL
        it.reason = f"出现过多于一条可写主对话: now={writable}, {over}。"
        return it
    if writable == 0:
        it.verdict = INCONCLUSIVE
        it.reason = "可写主对话为 0 行, 初始化异常。"
        return it
    it.verdict = PASS
    it.reason = "全程恰有 1 条可写主对话, 重启未产生第二条。"
    return it


# ---------------------------------------------------------------- 报告 / 主流程


ORDER = [
    "TF-1", "TF-2", "TF-3", "TF-4", "TF-5", "TF-6", "TF-7", "TF-8",
    "TF-9", "TF-10", "TF-11", "TF-12", "TF-13", "TF-14", "TF-15",
    "NC-T1", "NC-T2", "NC-T3", "NC-T4", "NC-T5",
]


def run_items(
    ev: Evidence,
    replay: Callable[[str, str], str] | None,
    replay_info: dict[str, Any] | None = None,
) -> list[Item]:
    specs: list[tuple[str, str, Callable[[], Item]]] = [
        ("TF-1", "唯一永久主对话跨重启不变", lambda: item_tf_1(ev)),
        ("TF-2", "≥20 turn 长上下文", lambda: item_tf_2(ev)),
        ("TF-3", "重启确实发生", lambda: item_tf_3(ev)),
        ("TF-4", "两个 TaskScope", lambda: item_tf_4(ev)),
        ("TF-5", "exact resume 不混任务", lambda: item_tf_5(ev)),
        ("TF-6", "跨重启类型化召回", lambda: item_tf_6(ev)),
        ("TF-7", "Procedure 跨重启使用", lambda: item_tf_7(ev)),
        ("TF-8", "Prospective 明确触发", lambda: item_tf_8(ev)),
        ("TF-9", "逻辑遗忘 append-only", lambda: item_tf_9(ev)),
        ("TF-10", "遗忘后召回不可见", lambda: item_tf_10(ev)),
        ("TF-11", "原始会话文本仍在", lambda: item_tf_11(ev)),
        ("TF-12", "受控审计面", lambda: item_tf_12(ev)),
        ("TF-13", "两个任务收口", lambda: item_tf_13(ev)),
        ("TF-14", "物理删除禁令", lambda: item_tf_14(ev)),
        ("TF-15", "snapshot 重放指纹", lambda: item_tf_15(ev, replay, replay_info)),
        ("NC-T1", "简单改写不建档", lambda: item_nc_t1(ev)),
        ("NC-T2", "模糊愿望不 pending", lambda: item_nc_t2(ev)),
        ("NC-T3", "UI 轮不调 provider", lambda: item_nc_t3(ev)),
        ("NC-T4", "无凭据形状", lambda: item_nc_t4(ev)),
        ("NC-T5", "不产生第二条主对话", lambda: item_nc_t5(ev)),
    ]
    out: list[Item] = []
    for key, title, fn in specs:
        try:
            item = fn()
        except SchemaMissing as exc:
            item = Item(key, title, INCONCLUSIVE, f"schema 缺失: {exc}", {})
        except Exception as exc:  # noqa: BLE001
            item = Item(key, title, INCONCLUSIVE, f"计算异常 {type(exc).__name__}: {exc}", {})
        item.key, item.title = key, title
        out.append(item)
    return out


def build_report(
    ev: Evidence, replay: Callable[[str, str], str] | None, replay_info: dict[str, Any]
) -> dict[str, Any]:
    items = run_items(ev, replay, replay_info)
    tally: dict[str, int] = {}
    for i in items:
        tally[i.verdict] = tally.get(i.verdict, 0) + 1
    notes: list[str] = []
    max_turn = ev.max_turn()
    if max_turn < EXPECTED_TURNS:
        notes.append(
            f"{PROGRESS_NAME} 只记录到 T{max_turn}/{EXPECTED_TURNS} —— 两个阶段未跑满, "
            "缺轮相关项已按 INCONCLUSIVE 处理。"
        )
    bad = [t for t in sorted(ev.counter_rows()) if t > 0 and ev.outcome(t) in {"timeout", "send_failed"}]
    if bad:
        notes.append(f"timeout/send_failed 轮: {bad}(plan 第 4 节: 该轮断言降级)。")
    if not ev.second_log_path:
        notes.append("未提供 --second-log: 第二段(重启后)native.log 缺席, TF-3 只能靠 progress 判定。")
    if not ev.second_launch_path:
        notes.append("未提供 --second-launch: 无法用 pid/userdata 交叉证明同 userdata 重启。")
    notes.append(
        "逻辑遗忘只针对记忆, 不隐藏会话记录(Host CLAUDE.md 2026-09-07 用户产品决定第 2 条); "
        "TF-10 与 TF-11 是一对断言, 只通过其一不算成立。"
    )
    for db in (ev.hm, ev.state, ev.audit, ev.exec, ev.product):
        if db.error:
            notes.append(db.error)
    return {
        "schema": "twoflow-verify-v1",
        "evidence_root": ev.root,
        "second_log": ev.second_log_path,
        "second_launch": ev.second_launch_path,
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "turns_recorded": max_turn,
        "expected_turns": EXPECTED_TURNS,
        "flow1_turns": list(FLOW1_TURNS),
        "flow2_turns": list(FLOW2_TURNS),
        "ui_turns": list(UI_TURNS),
        "timeout_turns": bad,
        "restart_markers": len(ev.restart_rows()),
        "primary_conversation_ids": ev.primary_ids(),
        "replay": replay_info,
        "sha256": ev.hashes(),
        "items": [i.to_json() for i in items],
        "tally": tally,
        "schema_notes": notes,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# 两轮完整流程（含重启）核对结果", ""]
    lines.append(f"证据目录(第一次启动): `{report['evidence_root']}`")
    lines.append(f"第二段日志: `{report['second_log'] or '未提供'}`")
    lines.append(
        f"轮次: 记录到 T{report['turns_recorded']}/{report['expected_turns']}; "
        f"重启标记={report['restart_markers']}; timeout 轮={report['timeout_turns'] or '无'}"
    )
    lines.append(f"主对话 ID: {report['primary_conversation_ids'] or '未记录'}")
    lines.append("")
    lines.append("| 项 | 判定 | 依据数字 | 说明 |")
    lines.append("|---|---|---|---|")
    for item in report["items"]:
        lines.append(
            f"| {item['item']} {_md_escape(item['title'])} | **{item['verdict']}** | "
            f"{_numbers_brief(item['numbers'])} | {_md_escape(item['reason'])} |"
        )
    lines.append("")
    lines.append("## 判定统计")
    t = report["tally"]
    lines.append("  ".join(f"{k}={t.get(k, 0)}" for k in (PASS, FAIL, BLOCKED, INCONCLUSIVE)))
    lines.append("")
    lines.append("## 证据文件 SHA-256")
    lines.append("")
    lines.append("| 文件 | SHA-256 |")
    lines.append("|---|---|")
    for name, digest in report["sha256"].items():
        lines.append(f"| {name} | `{digest or '缺失'}` |")
    if report.get("schema_notes"):
        lines.append("")
        lines.append("## Schema / 假设备注")
        for note in report["schema_notes"]:
            lines.append(f"- {_md_escape(note)}")
    return "\n".join(lines)


# ---------------------------------------------------------------- selftest


def selftest() -> int:
    """在临时 sqlite 上建最小表集, 确认每个 item 都能跑通并给出判定。"""
    import tempfile

    root = tempfile.mkdtemp(prefix="twoflow-selftest-")
    second = tempfile.mkdtemp(prefix="twoflow-selftest-e2-")
    data = os.path.join(root, "userdata", "data", "simple-harness-sdk")
    os.makedirs(data, exist_ok=True)
    parent = os.path.dirname(data)
    userdata = os.path.join(root, "userdata")

    def make(path: str, ddl: Sequence[str], rows: Sequence[tuple[str, Sequence[Any]]] = ()) -> None:
        conn = sqlite3.connect(path)
        for stmt in ddl:
            conn.execute(stmt)
        for sql, params in rows:
            conn.execute(sql, params)
        conn.commit()
        conn.close()

    req = {"messages": [{"role": "user", "content": "霜降素材整理"}]}
    make(
        os.path.join(data, "execution-v6.sqlite3"),
        [
            "create table provider_invocations(invocation_id text primary key, run_id text,"
            " request_id text, request_fingerprint text, request_json text, state text,"
            " claimed_at real, response_json text, usage_json text)",
            "create table execution_effects(effect_id text primary key, run_id text,"
            " tool_name text, state text, result_json text, arguments_json text)",
        ],
        [
            ("insert into provider_invocations values (?,?,?,?,?,?,?,?,?)",
             ("inv1", "run1", "req1", "fp1", json.dumps(req, ensure_ascii=False),
              "succeeded", 100.0, "{}", "{}")),
        ],
    )
    scope_a, scope_b = "scope-a", "scope-b"
    state_a = json.dumps({"status": "completed", "goal": "霜降素材整理",
                          "closure_reason": "用户标记完成"}, ensure_ascii=False)
    state_b = json.dumps({"status": "completed", "goal": "霜降字幕校对",
                          "closure_reason": "用户标记完成"}, ensure_ascii=False)
    make(
        os.path.join(parent, "state.db"),
        [
            "create table human_memory_primary_conversations(primary_conversation_id text primary key,"
            " subject text, writable integer, created_at real)",
            "create table foreground_run_heads(host_run_id text primary key, current_state text,"
            " sdk_run_id text, updated_at real)",
            "create table task_scopes(task_scope_id text primary key, subject text, title text,"
            " created_at real)",
            "create table context_route_decisions(decision_id text primary key, route text,"
            " origin text, task_scope_id text, recorded_at real)",
            "create table task_scope_search_access_receipts(receipt_id text primary key,"
            " operation text, created_at real)",
            "create table task_scope_canonical_revisions(task_scope_id text, revision integer,"
            " state_json text)",
            "create table task_scope_closure_receipts(receipt_id text primary key,"
            " task_scope_id text, outcome text, reason_code text)",
            "create table procedure_uses(use_id text primary key, task_scope_id text,"
            " memory_id text, target_revision integer)",
            "create table task_scope_events(event_id text primary key, task_scope_id text)",
            "create table task_scope_read_view_revisions(view_revision_id text primary key,"
            " task_scope_id text, view_kind text, content blob)",
            "create table task_workspace_binding_revisions(receipt_id text primary key,"
            " task_scope_id text, binding_set_revision integer)",
        ],
        [
            ("insert into human_memory_primary_conversations values (?,?,?,?)",
             ("primary-1", "owner", 1, 1.0)),
            ("insert into task_scopes values (?,?,?,?)", (scope_a, "o", "霜降素材整理", 1.0)),
            ("insert into task_scopes values (?,?,?,?)", (scope_b, "o", "霜降字幕校对", 2.0)),
            ("insert into context_route_decisions values (?,?,?,?,?)",
             ("d1", "create_new", "route", scope_a, 1.0)),
            ("insert into context_route_decisions values (?,?,?,?,?)",
             ("d2", "create_new", "route", scope_b, 2.0)),
            ("insert into context_route_decisions values (?,?,?,?,?)",
             ("d3", "direct_standalone", "no_recall", "", 3.0)),
            ("insert into context_route_decisions values (?,?,?,?,?)",
             ("d4", "resume_existing", "route", scope_a, 4.0)),
            ("insert into task_scope_search_access_receipts values (?,?,?)", ("s1", "search", 4.0)),
            ("insert into task_scope_search_access_receipts values (?,?,?)", ("s2", "open", 4.5)),
            ("insert into task_scope_canonical_revisions values (?,?,?)", (scope_a, 3, state_a)),
            ("insert into task_scope_canonical_revisions values (?,?,?)", (scope_b, 2, state_b)),
            ("insert into task_scope_closure_receipts values (?,?,?,?)",
             ("c1", scope_a, "closed", "user_complete")),
            ("insert into task_scope_closure_receipts values (?,?,?,?)",
             ("c2", scope_b, "closed", "user_complete")),
            ("insert into procedure_uses values (?,?,?,?)", ("u1", scope_a, "mem-proc", 1)),
        ],
    )
    for i in range(20):
        conn = sqlite3.connect(os.path.join(parent, "state.db"))
        conn.execute("insert into foreground_run_heads values (?,?,?,?)",
                     (f"h{i}", "COMPLETED", f"run{i}", float(i)))
        conn.commit()
        conn.close()
    make(
        os.path.join(parent, "human_memory_v7.db"),
        [
            "create table evidence_envelopes(evidence_id text primary key, sanitized_payload blob)",
            "create table cognitive_memory_heads(memory_id text primary key, memory_type text,"
            " current_revision integer)",
            "create table procedure_records(memory_id text, revision integer, name text,"
            " applicability_json text, steps_json text, risk_level text)",
            "create table prospective_records(memory_id text, revision integer, action_text text,"
            " trigger_kind text, trigger_json text, scheduler_registration_ref text, due_at real)",
            "create table prospective_scheduler_registrations(memory_id text)",
            "create table suppression_directives(directive_id text primary key, request_id text,"
            " principal_id text, event_kind text, scope_kind text, scope_ref text, purpose text,"
            " reason_code text)",
            "create table suppression_targets(directive_id text, ordinal integer,"
            " target_kind text, target_ref text)",
            "create table typed_recall_requests(request_id text primary key)",
            "create table typed_recall_results(result_id text primary key, request_id text,"
            " created_at real)",
            "create table typed_recall_result_items(result_id text, ordinal integer,"
            " item_id text, result_item_json text)",
            "create table analysis_batches(batch_id text primary key, state text)",
        ],
        [
            ("insert into cognitive_memory_heads values (?,?,?)", ("mem-sem", "semantic", 1)),
            ("insert into cognitive_memory_heads values (?,?,?)", ("mem-proc", "procedure", 1)),
            ("insert into cognitive_memory_heads values (?,?,?)", ("mem-pros", "prospective", 1)),
            ("insert into procedure_records values (?,?,?,?,?,?)",
             ("mem-proc", 1, "霜降清点", "{}", "[]", "low")),
            ("insert into prospective_records values (?,?,?,?,?,?,?)",
             ("mem-pros", 1, "写成片说明", "event", "{}", "reg-1", None)),
            ("insert into suppression_directives values (?,?,?,?,?,?,?,?)",
             ("dir1", "req1", "p1", "directive", "MEMORY", "mem-sem", "user", "user_forget")),
            ("insert into suppression_targets values (?,?,?,?)", ("dir1", 1, "MEMORY", "mem-sem")),
            ("insert into typed_recall_requests values (?)", ("tr1",)),
            ("insert into typed_recall_results values (?,?,?)", ("res1", "tr1", 10.0)),
            ("insert into typed_recall_result_items values (?,?,?,?)",
             ("res1", 1, "it1", json.dumps({"memory_id": "mem-other"}, ensure_ascii=False))),
        ],
    )
    make(
        os.path.join(parent, "sdk-product-state.db"),
        [
            "create table authorization_policy_state(singleton_id integer primary key, mode text,"
            " generation integer, updated_at real, provenance text, schema_generation integer,"
            " user_set_receipt_ref text)",
            "create table task_grants(task_grant_id text primary key, source text)",
        ],
        [("insert into authorization_policy_state values (?,?,?,?,?,?,?)",
          (1, "auto", 0, 1.0, "factory_default", 2, None))],
    )
    make(
        os.path.join(parent, "operation-audit.db"),
        [
            "create table audit_attempts(id text)",
            "create table human_audit_grants(audit_ref text primary key, status text, reads integer)",
            "create table human_audit_deliveries(audit_ref text, action_id text, status text)",
            "create table human_audit_host_streams(audit_ref text, stream_key text, section text)",
            "create table human_audit_host_deliveries(audit_ref text, action_id text, section text)",
        ],
        [
            ("insert into human_audit_grants values (?,?,?)", ("a1", "live", 2)),
            ("insert into human_audit_deliveries values (?,?,?)", ("a1", "p1", "delivered")),
        ]
        + [("insert into human_audit_host_streams values (?,?,?)", ("a1", f"k{i}", s))
           for i, s in enumerate(HOST_AUDIT_SECTIONS)]
        + [("insert into human_audit_host_deliveries values (?,?,?)", ("a1", f"h{i}", s))
           for i, s in enumerate(HOST_AUDIT_SECTIONS)],
    )
    with open(os.path.join(root, "native.log"), "w", encoding="utf-8") as fh:
        fh.write('{"event": "startup complete"}\n')
    with open(os.path.join(second, "native.log"), "w", encoding="utf-8") as fh:
        fh.write('{"event": "startup complete (restart)"}\n')
    with open(os.path.join(root, "launch.json"), "w", encoding="utf-8") as fh:
        json.dump({"pid": 1001, "userdata": userdata}, fh)
    with open(os.path.join(second, "launch.json"), "w", encoding="utf-8") as fh:
        json.dump({"pid": 2002, "userdata": userdata}, fh)

    base = {
        "primary_conversations": 1, "primary_conversations_writable": 1, "task_scopes": 0,
        "foreground_run_heads": 0, "context_route_decisions": 0, "route_create_new": 0,
        "route_resume_existing": 0, "route_no_recall": 0, "task_scope_search_ops": 0,
        "task_scope_open_ops": 0, "task_scope_events": 0, "task_scope_read_view_revisions": 0,
        "task_scope_closure_receipts": 0, "procedure_uses": 0, "binding_revisions": 0,
        "evidence_envelopes": 0, "cognitive_memory_heads": 0, "heads_semantic": 0,
        "heads_episode": 0, "heads_procedure": 0, "heads_prospective": 0,
        "procedure_records": 0, "prospective_records": 0,
        "prospective_scheduler_registrations": 0, "suppression_directives": 0,
        "suppression_targets": 0, "typed_recall_requests": 0, "typed_recall_results": 0,
        "typed_recall_result_items": 0, "human_audit_grants": 0, "human_audit_deliveries": 0,
        "human_audit_host_streams": 0, "human_audit_host_deliveries": 0,
        "provider_invocations": 0, "execution_effects": 0, "task_grants_user": 0,
    }
    script: list[tuple[int, str, str, str, dict[str, int]]] = [
        (0, "baseline", "flow1", "baseline", {}),
        (1, "send", "flow1", "settled", {"cognitive_memory_heads": 1, "heads_semantic": 1,
                                         "evidence_envelopes": 2, "foreground_run_heads": 1,
                                         "provider_invocations": 2}),
        (2, "send", "flow1", "settled", {"task_scopes": 1, "route_create_new": 1,
                                         "context_route_decisions": 1, "binding_revisions": 1,
                                         "foreground_run_heads": 2, "evidence_envelopes": 4,
                                         "provider_invocations": 4}),
        (3, "send", "flow1", "settled", {"execution_effects": 1, "foreground_run_heads": 3,
                                         "evidence_envelopes": 6, "provider_invocations": 6}),
        (4, "send", "flow1", "settled", {"execution_effects": 2, "foreground_run_heads": 4,
                                         "evidence_envelopes": 8, "provider_invocations": 8}),
        (5, "send", "flow1", "settled", {"task_scope_events": 1,
                                         "task_scope_read_view_revisions": 2,
                                         "foreground_run_heads": 5, "evidence_envelopes": 10,
                                         "provider_invocations": 10}),
        (6, "send", "flow1", "settled", {"context_route_decisions": 2, "route_no_recall": 1,
                                         "foreground_run_heads": 6, "evidence_envelopes": 12,
                                         "provider_invocations": 12}),
        (7, "send", "flow1", "settled", {"task_scopes": 2, "route_create_new": 2,
                                         "context_route_decisions": 3, "binding_revisions": 2,
                                         "foreground_run_heads": 7, "evidence_envelopes": 14,
                                         "provider_invocations": 14}),
        (8, "send", "flow1", "settled", {"execution_effects": 3, "foreground_run_heads": 8,
                                         "evidence_envelopes": 16, "provider_invocations": 16}),
        (9, "send", "flow1", "settled", {"procedure_records": 1, "cognitive_memory_heads": 2,
                                         "heads_procedure": 1, "foreground_run_heads": 9,
                                         "evidence_envelopes": 18, "provider_invocations": 18}),
        (10, "send", "flow1", "settled", {"prospective_records": 1, "cognitive_memory_heads": 3,
                                          "heads_prospective": 1, "foreground_run_heads": 10,
                                          "evidence_envelopes": 20, "provider_invocations": 20}),
        (11, "send", "flow1", "settled", {"task_scope_closure_receipts": 1,
                                          "foreground_run_heads": 11, "evidence_envelopes": 22,
                                          "provider_invocations": 22}),
        (RESTART_TURN, "restart", "restart", "restart", {}),
        (12, "send", "flow2", "settled", {"typed_recall_requests": 1, "typed_recall_results": 1,
                                          "typed_recall_result_items": 1,
                                          "foreground_run_heads": 12, "evidence_envelopes": 24,
                                          "provider_invocations": 24}),
        (13, "send", "flow2", "settled", {"task_scope_search_ops": 1, "foreground_run_heads": 13,
                                          "evidence_envelopes": 26, "provider_invocations": 26}),
        (14, "send", "flow2", "settled", {"task_scope_open_ops": 1, "route_resume_existing": 1,
                                          "context_route_decisions": 4,
                                          "foreground_run_heads": 14, "evidence_envelopes": 28,
                                          "provider_invocations": 28}),
        (15, "send", "flow2", "settled", {"execution_effects": 4, "foreground_run_heads": 15,
                                          "evidence_envelopes": 30, "provider_invocations": 30}),
        (16, "send", "flow2", "settled", {"procedure_uses": 1, "execution_effects": 6,
                                          "foreground_run_heads": 16, "evidence_envelopes": 32,
                                          "provider_invocations": 32}),
        (17, "send", "flow2", "settled", {"heads_semantic": 2, "cognitive_memory_heads": 4,
                                          "foreground_run_heads": 17, "evidence_envelopes": 34,
                                          "provider_invocations": 34}),
        (18, "ui", "flow2", "manual_ui", {"suppression_directives": 1, "suppression_targets": 1}),
        (19, "send", "flow2", "settled", {"typed_recall_requests": 2, "typed_recall_results": 2,
                                          "typed_recall_result_items": 2,
                                          "foreground_run_heads": 18, "evidence_envelopes": 36,
                                          "provider_invocations": 36}),
        (20, "send", "flow2", "settled", {"foreground_run_heads": 19, "evidence_envelopes": 38,
                                          "provider_invocations": 38}),
        (21, "ui", "flow2", "manual_ui", {"human_audit_grants": 1, "human_audit_deliveries": 1,
                                          "human_audit_host_streams": 3,
                                          "human_audit_host_deliveries": 3}),
        (22, "send", "flow2", "settled", {"task_scope_closure_receipts": 2,
                                          "foreground_run_heads": 20, "evidence_envelopes": 40,
                                          "provider_invocations": 40}),
    ]
    notes = {
        RESTART_TURN: "E2=/tmp/e2 pid=2002 primary_id_unchanged=1",
        18: "before=4 after=3 forgot=1",
        21: "grant=1 pages=2 sections=runs,run_operations,memory_calls",
    }
    running = dict(base)
    with open(os.path.join(root, PROGRESS_NAME), "w", encoding="utf-8") as fh:
        for idx, (turn, kind, phase, outcome, patch) in enumerate(script):
            running.update(patch)
            row = {
                "ts": "2026-09-09T%02d:%02d:00+0800" % (9 + idx // 6, (idx * 7) % 60),
                "turn": turn, "kind": kind, "phase": phase, "outcome": outcome,
                "elapsed_s": 1.0, "primary_conversation_id": "primary-1",
                "last_run_state": "COMPLETED", "note": notes.get(turn, ""),
            }
            row.update(running)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    ev = Evidence(
        root,
        second_log=os.path.join(second, "native.log"),
        second_launch=os.path.join(second, "launch.json"),
    )
    try:
        items = run_items(ev, None)
        assert len(items) == len(ORDER), f"item 数不符: {len(items)} != {len(ORDER)}"
        assert [i.key for i in items] == ORDER, "item 顺序与 ORDER 不一致"
        for item in items:
            assert item.verdict in {PASS, FAIL, BLOCKED, INCONCLUSIVE}, item
            assert item.reason, f"{item.key} 无 reason"
        report = build_report(ev, None, {"imported": False})
        md = render_markdown(report)
        assert "| 项 | 判定 | 依据数字 | 说明 |" in md
        json.dumps(report, ensure_ascii=False)
        verdicts = {i.key: i.verdict for i in items}
    finally:
        ev.close()
    assert sha256_file(os.path.join(root, PROGRESS_NAME)), "进度文件 hash 计算失败"
    print("selftest: OK —— %d 个判定项全部可执行, 报告可渲染 (临时库 %s)" % (len(ORDER), root))
    print("selftest verdicts: " + json.dumps(verdicts, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- CLI


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="两轮完整流程(含重启)后置校验器")
    ap.add_argument("--evidence", help="第一次启动的证据目录 <E1>(拥有 userdata)")
    ap.add_argument("--second-log", help="重启后 <E2>/native.log")
    ap.add_argument("--second-launch", help="重启后 <E2>/launch.json")
    ap.add_argument("--installed-target", help="含已安装 simple_harness 包的目录(指纹重放)")
    ap.add_argument("--out", help="JSON 输出路径, 默认 <E1>/twoflow-verify.json")
    ap.add_argument("--json-only", action="store_true", help="只打印 JSON, 不打印 Markdown 表")
    ap.add_argument("--selftest", action="store_true", help="临时 sqlite 自检")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.evidence:
        ap.error("--evidence 必填(或使用 --selftest)")

    replay, replay_info = build_replayer(args.installed_target)
    ev = Evidence(args.evidence, args.second_log, args.second_launch)
    try:
        report = build_report(ev, replay, replay_info)
    finally:
        ev.close()

    out_path = args.out or os.path.join(ev.root, "twoflow-verify.json")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        report["out_path"] = out_path
    except OSError as exc:
        print(f"warn: 无法写入 {out_path}: {exc}", file=sys.stderr)

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))
        print()
        print(f"JSON 已写入: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
