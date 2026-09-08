#!/usr/bin/env python3
"""hm_benchmark —— HM-AC-8 质量 / 延迟 / Token 基准提取器（只读证据 → JSON + 中文 Markdown）。

用法::

    python scripts/benchmark/hm_benchmark.py \
        --evidence <dir> [--evidence <dir> ...] \
        [--review-verdicts <json> ...] \
        --out <report.json> [--md] [--md-out <report.md>]
    python scripts/benchmark/hm_benchmark.py --selftest

每个 ``--evidence`` 目录会被递归扫描：

* ``**/userdata/data/state.db`` 所在目录视为一个「运行时根」（原生 App 一次启动，或语料批次里一个用例）；
  同目录下读取 ``state.db``、``operation-audit.db``、``human_memory_v7.db``、
  ``simple-harness-sdk/execution-v6.sqlite3``。
* ``**/native.log``（没有时退回 ``**/logs/backend.log``）—— 按行内容去重后解析 ``product_provider_attempt_*``、``context.*``、
  ``model_context_resolved``、``foreground.runtime.failed``。
* ``**/*-progress.jsonl`` —— UI 驱动记录的「发送 → 终态」墙钟时间。
* ``**/batch-summary.jsonl`` —— 语料批次用例级摘要。

所有数据库先复制到临时目录（含 -wal/-shm）再打开，绝不写回证据。
分位数采用 nearest-rank（p = 第 ceil(p/100·n) 个有序样本），跨目录汇总用原始样本合并，而不是平均的平均。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Sequence

SCHEMA = "hm-benchmark-v1"

# ---------------------------------------------------------------------------
# 预算常量：优先从 Host 代码导入，失败时使用逐字一致的副本（与 context_partitions.py 对齐）
# ---------------------------------------------------------------------------

_GENERATION_RESERVE: dict[int, int] = {4096: 1024, 8192: 2048, 32768: 4096}
_SUPPORTED_WINDOWS: tuple[int, ...] = (4096, 8192, 32768)


def _fallback_text_tokens(value: object) -> int:
    text = str(value or "")
    cjk = sum(1 for char in text if "㐀" <= char <= "鿿")
    return cjk + max(0, len(text) - cjk + 3) // 4


def _fallback_budget_window(window_tokens: int) -> int:
    chosen = _SUPPORTED_WINDOWS[0]
    for tier in _SUPPORTED_WINDOWS:
        if window_tokens >= tier:
            chosen = tier
    return chosen


def _fallback_safety_margin(window_tokens: int) -> int:
    return max(256, window_tokens // 10)


def _fallback_effective_input_budget(window_tokens: int) -> int:
    tier = _fallback_budget_window(window_tokens)
    return window_tokens - _GENERATION_RESERVE[tier] - _fallback_safety_margin(window_tokens)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_host_helpers() -> dict[str, Any]:
    info: dict[str, Any] = {"host_import": False}
    text_tokens: Callable[[object], int] = _fallback_text_tokens
    budget_window = _fallback_budget_window
    effective_input_budget = _fallback_effective_input_budget
    try:  # pragma: no cover - 取决于 PYTHONPATH
        from deskpet.sdk_adapters import context_partitions as cp  # type: ignore

        text_tokens = cp.text_tokens
        budget_window = cp.budget_window
        effective_input_budget = cp.effective_input_budget
        info["host_import"] = True
    except Exception as exc:  # noqa: BLE001
        info["host_import_error"] = f"{type(exc).__name__}: {exc}"
    info["text_tokens"] = text_tokens
    info["budget_window"] = budget_window
    info["effective_input_budget"] = effective_input_budget
    return info


HELPERS = _load_host_helpers()
text_tokens: Callable[[object], int] = HELPERS["text_tokens"]

# ---------------------------------------------------------------------------
# 契约阈值（仅列出契约文本明确给出的；其余指标标 N-A）
# ---------------------------------------------------------------------------

CONTRACT: dict[str, Any] = {
    "recall_p95_ms": 500,  # acceptance.md HM-AC-8：本地记忆检索 p95 ≤ 500ms
    "recall_hard_deadline_ms": 2000,  # acceptance.md HM-AC-8：hard deadline ≤ 2s
    "provider_timeout_s_default": 240.0,  # native.log timeout_seconds（S5 provider timeout）
    "corpus_required_recall_min": 0.90,  # HM-AC-8：required-memory-type recall ≥ 90%
    "corpus_no_recall_accuracy_min": 0.90,  # HM-AC-8：no-recall 判断正确率 ≥ 90%
    "corpus_extra_type_rate_max": 0.15,  # HM-AC-8：额外类型率 ≤ 15%
    "corpus_privacy_accuracy": 1.0,  # HM-AC-8：隐私禁止项正确率 100%
}


# ---------------------------------------------------------------------------
# 统计工具
# ---------------------------------------------------------------------------


def percentile(values: Sequence[float], p: float) -> float | None:
    """nearest-rank 分位数：第 ceil(p/100·n) 个有序样本；空集返回 None。"""
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    rank = max(1, min(len(ordered), int(math.ceil(p / 100.0 * len(ordered)))))
    return ordered[rank - 1]


def stats(values: Sequence[float], digits: int = 1) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return {"n": 0, "p50": None, "p95": None, "max": None, "min": None, "mean": None, "sum": None}
    return {
        "n": len(vals),
        "p50": round(percentile(vals, 50) or 0.0, digits),
        "p95": round(percentile(vals, 95) or 0.0, digits),
        "max": round(max(vals), digits),
        "min": round(min(vals), digits),
        "mean": round(sum(vals) / len(vals), digits),
        "sum": round(sum(vals), digits),
    }


def ratio(num: float, den: float) -> float | None:
    if not den:
        return None
    return round(num / den, 4)


# ---------------------------------------------------------------------------
# 样本容器
# ---------------------------------------------------------------------------


@dataclass
class Samples:
    """一个证据目录（或全部汇总）的原始样本。所有列表可直接拼接。"""

    runtime_roots: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 前台 turn：enqueued → 最后一个 Run 的终态
    turns: list[dict[str, Any]] = field(default_factory=list)
    # Provider 调用（execution-v6.provider_invocations）
    provider_calls: list[dict[str, Any]] = field(default_factory=list)
    # Host 侧结算审计（state.db.sdk_provider_attempt_audit）
    attempt_audit: list[dict[str, Any]] = field(default_factory=list)
    # typed recall（operation-audit.db.memory_call_attempts）
    recall_calls: list[dict[str, Any]] = field(default_factory=list)
    # typed recall 终态（human_memory_v7.db.typed_recall_terminals）
    recall_terminals: list[dict[str, Any]] = field(default_factory=list)
    recall_deadlines_ms: list[int] = field(default_factory=list)
    # 分析通道
    analysis_invocations: list[dict[str, Any]] = field(default_factory=list)
    analysis_batches: list[dict[str, Any]] = field(default_factory=list)
    # 上下文组装回执
    snapshot_receipts: list[dict[str, Any]] = field(default_factory=list)
    page_in_effects: int = 0
    # Run 失败（sdk_run_id → reason）
    run_failures: list[dict[str, Any]] = field(default_factory=list)
    # native.log 解析
    log_provider_elapsed_ms: list[float] = field(default_factory=list)
    log_provider_timeouts_s: list[float] = field(default_factory=list)
    log_provider_started: int = 0
    log_provider_succeeded: int = 0
    log_provider_failed: int = 0
    log_context_events: Counter = field(default_factory=Counter)
    log_model_windows: dict[str, int] = field(default_factory=dict)
    log_runtime_failed: Counter = field(default_factory=Counter)
    log_lines_seen: int = 0
    # 驱动进度（UI 发送 → 终态）
    driver_turns: list[dict[str, Any]] = field(default_factory=list)
    # 语料批次用例级
    corpus_cases: list[dict[str, Any]] = field(default_factory=list)

    def extend(self, other: "Samples") -> None:
        for name in (
            "runtime_roots",
            "warnings",
            "turns",
            "provider_calls",
            "attempt_audit",
            "recall_calls",
            "recall_terminals",
            "recall_deadlines_ms",
            "analysis_invocations",
            "analysis_batches",
            "snapshot_receipts",
            "run_failures",
            "log_provider_elapsed_ms",
            "log_provider_timeouts_s",
            "driver_turns",
            "corpus_cases",
        ):
            getattr(self, name).extend(getattr(other, name))
        self.page_in_effects += other.page_in_effects
        self.log_provider_started += other.log_provider_started
        self.log_provider_succeeded += other.log_provider_succeeded
        self.log_provider_failed += other.log_provider_failed
        self.log_lines_seen += other.log_lines_seen
        self.log_context_events.update(other.log_context_events)
        self.log_runtime_failed.update(other.log_runtime_failed)
        for model, window in other.log_model_windows.items():
            self.log_model_windows[model] = window


# ---------------------------------------------------------------------------
# 只读数据库访问：复制后打开
# ---------------------------------------------------------------------------


class ReadOnlyDb:
    """把 db(+wal/shm) 复制到临时目录再打开；证据本体绝不被写。"""

    def __init__(self, path: str, scratch: str):
        self.path = path
        self.scratch = scratch
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> "ReadOnlyDb":
        if not os.path.exists(self.path):
            return self
        digest = hashlib.sha256(os.path.abspath(self.path).encode("utf-8")).hexdigest()[:16]
        target_dir = os.path.join(self.scratch, digest)
        os.makedirs(target_dir, exist_ok=True)
        base = os.path.basename(self.path)
        target = os.path.join(target_dir, base)
        shutil.copyfile(self.path, target)
        for suffix in ("-wal", "-shm"):
            side = self.path + suffix
            if os.path.exists(side):
                shutil.copyfile(side, target + suffix)
        self.conn = sqlite3.connect(target)
        self.conn.row_factory = sqlite3.Row
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        # 用完即删副本，避免大批量语料把临时目录撑满
        digest = hashlib.sha256(os.path.abspath(self.path).encode("utf-8")).hexdigest()[:16]
        shutil.rmtree(os.path.join(self.scratch, digest), ignore_errors=True)

    @property
    def available(self) -> bool:
        return self.conn is not None

    def has_table(self, name: str) -> bool:
        if self.conn is None:
            return False
        row = self.conn.execute(
            "select 1 from sqlite_master where type='table' and name=?", (name,)
        ).fetchone()
        return row is not None

    def rows(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        if self.conn is None:
            return []
        return list(self.conn.execute(sql, params).fetchall())


def _json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    try:
        return json.loads(value)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# 证据发现
# ---------------------------------------------------------------------------


def find_runtime_roots(evidence_dir: str) -> list[str]:
    roots: list[str] = []
    for dirpath, dirnames, filenames in os.walk(evidence_dir):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "node_modules", ".git")]
        if "state.db" in filenames and os.path.basename(dirpath) == "data":
            if os.path.basename(os.path.dirname(dirpath)) == "userdata":
                roots.append(dirpath)
    return sorted(roots)


def find_files(evidence_dir: str, predicate: Callable[[str], bool]) -> list[str]:
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(evidence_dir):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "node_modules", ".git")]
        for name in filenames:
            if predicate(name):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


# ---------------------------------------------------------------------------
# 单个运行时根的提取
# ---------------------------------------------------------------------------


def extract_root(root: str, scratch: str) -> Samples:
    s = Samples(runtime_roots=[root])
    state_path = os.path.join(root, "state.db")
    audit_path = os.path.join(root, "operation-audit.db")
    hm_path = os.path.join(root, "human_memory_v7.db")
    exec_path = os.path.join(root, "simple-harness-sdk", "execution-v6.sqlite3")

    def warn(msg: str) -> None:
        s.warnings.append(f"{root}: {msg}")

    run_reason: dict[str, str] = {}

    # ---- execution-v6：provider_invocations + run.failed 原因 ----
    with ReadOnlyDb(exec_path, scratch) as db:
        if not db.available:
            warn("缺少 execution-v6.sqlite3")
        else:
            if db.has_table("run_events"):
                try:
                    for row in db.rows(
                        "select run_id, payload_json from run_events where kind='run.failed'"
                    ):
                        payload = _json(row["payload_json"]) or {}
                        reason = payload.get("code")
                        if not reason:
                            raw = payload.get("raw_failures") or []
                            if raw and isinstance(raw, list):
                                reason = (raw[0] or {}).get("error_code")
                        run_reason[str(row["run_id"])] = str(reason or "unknown")
                except sqlite3.OperationalError as exc:
                    warn(f"run_events 读取失败: {exc}")
            if db.has_table("provider_invocations"):
                try:
                    for row in db.rows(
                        "select invocation_id, run_id, state, error_code, request_json, usage_json,"
                        " claimed_at, handed_off_at, settled_at from provider_invocations"
                    ):
                        usage = (_json(row["usage_json"]) or {}).get("usage") or {}
                        request = _json(row["request_json"]) or {}
                        messages = request.get("messages") or []
                        tools = request.get("tools") or []
                        est_msgs = text_tokens(_canonical_json(messages)) if messages else 0
                        est_tools = text_tokens(_canonical_json(tools)) if tools else 0
                        start = row["handed_off_at"] or row["claimed_at"]
                        latency_ms = None
                        if start is not None and row["settled_at"] is not None:
                            latency_ms = (float(row["settled_at"]) - float(start)) * 1000.0
                        s.provider_calls.append(
                            {
                                "invocation_id": row["invocation_id"],
                                "run_id": row["run_id"],
                                "state": row["state"],
                                "error_code": row["error_code"],
                                "latency_ms": latency_ms,
                                "input_tokens": usage.get("input_tokens"),
                                "output_tokens": usage.get("output_tokens"),
                                "cache_tokens": usage.get("cache_tokens"),
                                "est_tokens_messages": est_msgs,
                                "est_tokens_with_tools": est_msgs + est_tools,
                            }
                        )
                except sqlite3.OperationalError as exc:
                    warn(f"provider_invocations 读取失败: {exc}")
            if db.has_table("execution_effects"):
                try:
                    row = db.rows(
                        "select count(*) as c from execution_effects where tool_name='context_page_in'"
                    )
                    s.page_in_effects += int(row[0]["c"]) if row else 0
                except sqlite3.OperationalError as exc:
                    warn(f"execution_effects 读取失败: {exc}")

    # ---- state.db：turn / run 终态 / provider 结算审计 / 快照回执 ----
    with ReadOnlyDb(state_path, scratch) as db:
        if not db.available:
            warn("缺少 state.db")
        else:
            needed = ("foreground_turns", "foreground_runs", "foreground_terminal_receipts")
            if all(db.has_table(t) for t in needed):
                try:
                    turns = db.rows(
                        "select turn_id, enqueue_sequence, enqueued_at from foreground_turns"
                    )
                    runs = db.rows("select host_run_id, turn_id, admitted_at from foreground_runs")
                    terminals = db.rows(
                        "select host_run_id, sdk_run_id, terminal_state, recorded_at"
                        " from foreground_terminal_receipts"
                    )
                    term_by_run: dict[str, sqlite3.Row] = {}
                    for t in terminals:
                        prev = term_by_run.get(t["host_run_id"])
                        if prev is None or (t["recorded_at"] or 0) >= (prev["recorded_at"] or 0):
                            term_by_run[t["host_run_id"]] = t
                    runs_by_turn: dict[str, list[sqlite3.Row]] = defaultdict(list)
                    for r in runs:
                        runs_by_turn[r["turn_id"]].append(r)
                    for turn in turns:
                        turn_runs = sorted(
                            runs_by_turn.get(turn["turn_id"], []),
                            key=lambda r: r["admitted_at"] or 0,
                        )
                        last_terminal = None
                        for r in turn_runs:
                            t = term_by_run.get(r["host_run_id"])
                            if t is not None:
                                last_terminal = t
                        record: dict[str, Any] = {
                            "root": root,
                            "turn_id": turn["turn_id"],
                            "enqueue_sequence": turn["enqueue_sequence"],
                            "enqueued_at": turn["enqueued_at"],
                            "runs": len(turn_runs),
                            "terminal_state": None,
                            "wall_ms": None,
                            "host_run_id": None,
                            "sdk_run_id": None,
                            "reason": None,
                        }
                        if last_terminal is not None:
                            record["terminal_state"] = last_terminal["terminal_state"]
                            record["host_run_id"] = last_terminal["host_run_id"]
                            record["sdk_run_id"] = last_terminal["sdk_run_id"]
                            if turn["enqueued_at"] is not None and last_terminal["recorded_at"]:
                                record["wall_ms"] = (
                                    float(last_terminal["recorded_at"]) - float(turn["enqueued_at"])
                                ) * 1000.0
                        s.turns.append(record)
                    for t in terminals:
                        if str(t["terminal_state"]).upper() != "COMPLETED":
                            s.run_failures.append(
                                {
                                    "root": root,
                                    "host_run_id": t["host_run_id"],
                                    "sdk_run_id": t["sdk_run_id"],
                                    "terminal_state": t["terminal_state"],
                                    "reason": run_reason.get(str(t["sdk_run_id"]), "unknown"),
                                }
                            )
                    for rec in s.turns:
                        if rec["terminal_state"] and rec["terminal_state"] != "COMPLETED":
                            rec["reason"] = run_reason.get(str(rec["sdk_run_id"]), "unknown")
                except sqlite3.OperationalError as exc:
                    warn(f"foreground_* 读取失败: {exc}")
            else:
                warn("缺少 foreground_turns/foreground_runs/foreground_terminal_receipts")
            if db.has_table("sdk_provider_attempt_audit"):
                try:
                    for row in db.rows(
                        "select root_run_id, state, usage_available, input_tokens, output_tokens,"
                        " total_tokens, cache_tokens, settled_at, model_id, context_window"
                        " from sdk_provider_attempt_audit"
                    ):
                        s.attempt_audit.append(
                            {
                                "root": root,
                                "root_run_id": row["root_run_id"],
                                "state": row["state"],
                                "usage_available": row["usage_available"],
                                "input_tokens": row["input_tokens"],
                                "output_tokens": row["output_tokens"],
                                "total_tokens": row["total_tokens"],
                                "cache_tokens": row["cache_tokens"],
                                "settled_at": row["settled_at"],
                                "model_id": row["model_id"],
                                "context_window": row["context_window"],
                            }
                        )
                except sqlite3.OperationalError as exc:
                    warn(f"sdk_provider_attempt_audit 读取失败: {exc}")
            if db.has_table("run_context_snapshot_receipts"):
                try:
                    for row in db.rows(
                        "select sdk_run_id, provider_turn_ordinal, source_revisions_json"
                        " from run_context_snapshot_receipts"
                    ):
                        payload = _json(row["source_revisions_json"]) or {}
                        rev = payload.get("source_revisions") or {}
                        s.snapshot_receipts.append(
                            {
                                "root": root,
                                "sdk_run_id": row["sdk_run_id"],
                                "ordinal": row["provider_turn_ordinal"],
                                "budget_tier": rev.get("budget_tier"),
                                "causal_groups": rev.get("causal_groups"),
                                "trimmed_groups": rev.get("trimmed_groups"),
                                "current_tool_pages": rev.get("current_tool_pages"),
                                "current_tool_tokens": rev.get("current_tool_tokens"),
                            }
                        )
                except sqlite3.OperationalError as exc:
                    warn(f"run_context_snapshot_receipts 读取失败: {exc}")

    # ---- operation-audit.db：typed recall（Host 侧墙钟） ----
    with ReadOnlyDb(audit_path, scratch) as db:
        if not db.available:
            warn("缺少 operation-audit.db")
        elif db.has_table("memory_call_attempts"):
            try:
                for row in db.rows(
                    "select caller, state, observation_status, started_at, settled_at"
                    " from memory_call_attempts"
                ):
                    latency = None
                    if row["started_at"] is not None and row["settled_at"] is not None:
                        latency = (float(row["settled_at"]) - float(row["started_at"])) * 1000.0
                    s.recall_calls.append(
                        {
                            "root": root,
                            "caller": row["caller"],
                            "state": row["state"],
                            "latency_ms": latency,
                        }
                    )
            except sqlite3.OperationalError as exc:
                warn(f"memory_call_attempts 读取失败: {exc}")

    # ---- human_memory_v7.db：typed recall 终态 + 分析通道 ----
    with ReadOnlyDb(hm_path, scratch) as db:
        if not db.available:
            warn("缺少 human_memory_v7.db")
        else:
            if db.has_table("typed_recall_terminals"):
                try:
                    for row in db.rows(
                        "select terminal_kind, degradation_codes_json from typed_recall_terminals"
                    ):
                        s.recall_terminals.append(
                            {
                                "root": root,
                                "terminal_kind": row["terminal_kind"],
                                "degradation_codes": _json(row["degradation_codes_json"]) or [],
                            }
                        )
                except sqlite3.OperationalError as exc:
                    warn(f"typed_recall_terminals 读取失败: {exc}")
            if db.has_table("typed_recall_requests"):
                try:
                    for row in db.rows("select request_json from typed_recall_requests"):
                        req = _json(row["request_json"]) or {}
                        budget = (req.get("context") or {}).get("budget") or req.get("budget") or {}
                        if budget.get("deadline_ms") is not None:
                            s.recall_deadlines_ms.append(int(budget["deadline_ms"]))
                except sqlite3.OperationalError as exc:
                    warn(f"typed_recall_requests 读取失败: {exc}")
            if db.has_table("llm_invocations"):
                try:
                    for row in db.rows(
                        "select output_storage_status, output_reason_code, input_tokens,"
                        " output_tokens, cost_microunits, latency_ms, started_at, completed_at"
                        " from llm_invocations"
                    ):
                        s.analysis_invocations.append(
                            {
                                "root": root,
                                "status": row["output_storage_status"],
                                "reason_code": row["output_reason_code"],
                                "input_tokens": row["input_tokens"],
                                "output_tokens": row["output_tokens"],
                                "cost_microunits": row["cost_microunits"],
                                "latency_ms": row["latency_ms"],
                            }
                        )
                except sqlite3.OperationalError as exc:
                    warn(f"llm_invocations 读取失败: {exc}")
            if db.has_table("analysis_batches"):
                try:
                    reason_by_batch: dict[str, str] = {}
                    if db.has_table("job_attempts"):
                        for row in db.rows(
                            "select batch_id, reason_code from job_attempts where reason_code is not null"
                        ):
                            reason_by_batch[str(row["batch_id"])] = str(row["reason_code"])
                    for row in db.rows(
                        "select batch_id, state, attempt, request_json, created_at, updated_at"
                        " from analysis_batches"
                    ):
                        req = _json(row["request_json"]) or {}
                        budget = req.get("budget") or {}
                        s.analysis_batches.append(
                            {
                                "root": root,
                                "batch_id": row["batch_id"],
                                "state": row["state"],
                                "attempt": row["attempt"],
                                "reason_code": reason_by_batch.get(str(row["batch_id"])),
                                "deadline_ms": budget.get("deadline_ms"),
                                "max_input_tokens": budget.get("max_input_tokens"),
                                "max_output_tokens": budget.get("max_output_tokens"),
                                "wall_ms": (
                                    (float(row["updated_at"]) - float(row["created_at"])) * 1000.0
                                    if row["updated_at"] is not None and row["created_at"] is not None
                                    else None
                                ),
                            }
                        )
                except sqlite3.OperationalError as exc:
                    warn(f"analysis_batches 读取失败: {exc}")
    return s


# ---------------------------------------------------------------------------
# 日志 / 驱动 / 批次摘要
# ---------------------------------------------------------------------------

_RE_ELAPSED = re.compile(r"elapsed_ms=(\d+)")
_RE_TIMEOUT = re.compile(r"timeout_seconds=([0-9.]+)")
_RE_WINDOW = re.compile(r"model_context_resolved model=(\S+) window=(\d+)")


def extract_native_logs(paths: Iterable[str]) -> Samples:
    """按行内容去重：同一行在多个文件里出现（merged 日志 + 分段日志）只按单个文件内的最大重复次数计。"""
    s = Samples()
    multiplicity: dict[str, int] = {}
    text_by_key: dict[str, str] = {}
    for path in paths:
        local: Counter = Counter()
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    line = raw.strip()
                    idx = line.find("{")
                    if idx < 0:
                        continue
                    line = line[idx:]
                    key = hashlib.sha1(line.encode("utf-8")).hexdigest()
                    local[key] += 1
                    text_by_key.setdefault(key, line)
        except OSError as exc:
            s.warnings.append(f"{path}: 读取失败 {exc}")
            continue
        for key, count in local.items():
            multiplicity[key] = max(multiplicity.get(key, 0), count)
    for key, count in multiplicity.items():
        try:
            obj = json.loads(text_by_key[key])
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(obj, dict):
            continue
        s.log_lines_seen += count
        event = str(obj.get("event") or "")
        if event.startswith("product_provider_attempt_started"):
            s.log_provider_started += count
            m = _RE_TIMEOUT.search(event)
            if m:
                s.log_provider_timeouts_s.extend([float(m.group(1))] * count)
        elif event.startswith("product_provider_attempt_succeeded"):
            s.log_provider_succeeded += count
            m = _RE_ELAPSED.search(event)
            if m:
                s.log_provider_elapsed_ms.extend([float(m.group(1))] * count)
        elif event.startswith("product_provider_attempt_failed") or event.startswith(
            "product_provider_attempt_degraded"
        ):
            s.log_provider_failed += count
        elif event in ("context.preparing", "context.staged", "context.consumed"):
            s.log_context_events[event] += count
        elif event.startswith("model_context_resolved"):
            m = _RE_WINDOW.search(event)
            if m:
                s.log_model_windows[m.group(1)] = int(m.group(2))
        elif event == "foreground.runtime.failed":
            detail = obj.get("error_detail") or obj.get("error_code") or "unknown"
            s.log_runtime_failed[str(detail)] += count
    return s


def _dedup_jsonl_lines(paths: Iterable[str], warnings: list[str]) -> list[tuple[str, dict[str, Any], int]]:
    """读取多份 jsonl，按行内容去重（合并副本只按单文件内最大重复次数计）。返回 (file, obj, multiplicity)。"""
    multiplicity: dict[str, int] = {}
    first: dict[str, tuple[str, str]] = {}
    for path in paths:
        local: Counter = Counter()
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line:
                        continue
                    key = hashlib.sha1(line.encode("utf-8")).hexdigest()
                    local[key] += 1
                    first.setdefault(key, (path, line))
        except OSError as exc:
            warnings.append(f"{path}: 读取失败 {exc}")
            continue
        for key, count in local.items():
            multiplicity[key] = max(multiplicity.get(key, 0), count)
    out: list[tuple[str, dict[str, Any], int]] = []
    for key, count in multiplicity.items():
        path, line = first[key]
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(obj, dict):
            out.append((path, obj, count))
    return out


def extract_driver_progress(paths: Iterable[str]) -> Samples:
    s = Samples()
    for path, obj, count in _dedup_jsonl_lines(paths, s.warnings):
        turn = obj.get("turn")
        if not isinstance(turn, int) or turn <= 0:
            continue
        outcome = str(obj.get("outcome") or "")
        state = str(obj.get("last_run_state") or "")
        elapsed = obj.get("elapsed_s")
        for _ in range(count):
            s.driver_turns.append(
                {
                    "file": path,
                    "turn": turn,
                    "outcome": outcome,
                    "last_run_state": state,
                    "elapsed_s": float(elapsed) if elapsed is not None else None,
                }
            )
    return s


def extract_batch_summaries(paths: Iterable[str]) -> Samples:
    s = Samples()
    for path, obj, count in _dedup_jsonl_lines(paths, s.warnings):
        for _ in range(count):
            s.corpus_cases.append(
                {
                    "file": path,
                    "case_id": obj.get("case_id"),
                    "elapsed_seconds": obj.get("elapsed_seconds"),
                    "oracle_verdict": obj.get("oracle_verdict"),
                    "driver_returncode": obj.get("driver_returncode"),
                    "peak_rss_kib": obj.get("peak_rss_kib"),
                }
            )
    return s


def extract_evidence_dir(evidence_dir: str, scratch: str) -> Samples:
    pooled = Samples()
    for root in find_runtime_roots(evidence_dir):
        pooled.extend(extract_root(root, scratch))
    # native.log 已包含 backend.log 的内容但行格式不完全相同，二者同时解析会重复计数；
    # 只有目录里没有 native.log（语料批次）时才退回 backend.log。
    logs = find_files(evidence_dir, lambda n: n == "native.log")
    if not logs:
        logs = find_files(evidence_dir, lambda n: n == "backend.log")
    pooled.extend(extract_native_logs(logs))
    pooled.extend(
        extract_driver_progress(find_files(evidence_dir, lambda n: n.endswith("-progress.jsonl")))
    )
    pooled.extend(
        extract_batch_summaries(find_files(evidence_dir, lambda n: n == "batch-summary.jsonl"))
    )
    return pooled


# ---------------------------------------------------------------------------
# 语料复审判定（可选 --review-verdicts）
# ---------------------------------------------------------------------------


def load_review_verdicts(paths: Iterable[str]) -> dict[str, dict[str, Any]]:
    verdicts: dict[str, dict[str, Any]] = {}
    for path in paths:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        items = data if isinstance(data, list) else data.get("verdicts") or data.get("cases") or []
        for item in items:
            if not isinstance(item, dict) or not item.get("case_id"):
                continue
            verdicts[str(item["case_id"])] = item
    return verdicts


def _extra_count(value: Any) -> int | None:
    """extra_types 可能是整数、类型名列表或 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (list, tuple, set)):
        return len(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_no_recall_category(category: str) -> bool:
    c = category.lower().replace("_", "-")
    return "no-match" in c or "no-recall" in c


def corpus_quality(verdicts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """复审判定聚合。category 为自由文本，故只依赖字段本身：

    * required_recall_rate：required_hit 为布尔的已计分用例中 True 的比例；
    * extra_type_rate：extra_types 非空的已计分用例中 extra_types>0 的比例；
    * no_recall_accuracy：category 含 no-match / no-recall 的已计分用例中 verdict=PASS 的比例；
    * privacy_accuracy：1 − privacy_violation=True 的已计分用例比例；
    * hard_trigger_accuracy：hard_trigger_correct 为布尔的用例中 True 的比例。
    同一 case_id 出现在多个文件时，后传入的文件覆盖先传入的（按参数顺序）。
    """
    scored = [v for v in verdicts.values() if str(v.get("verdict") or "").upper() not in ("", "NOT_SCORED")]
    categories = Counter(str(v.get("category") or "unknown") for v in verdicts.values())
    verdict_counts = Counter(str(v.get("verdict") or "unknown") for v in verdicts.values())
    not_scored_reasons = Counter(
        str(v.get("not_scored_reason") or "unknown")
        for v in verdicts.values()
        if str(v.get("verdict") or "").upper() == "NOT_SCORED"
    )
    recall_cases = [v for v in scored if isinstance(v.get("required_hit"), bool)]
    required_hits = sum(1 for v in recall_cases if v.get("required_hit") is True)
    extra_cases = [v for v in scored if _extra_count(v.get("extra_types")) is not None]
    extra_hits = sum(1 for v in extra_cases if (_extra_count(v.get("extra_types")) or 0) > 0)
    no_recall = [v for v in scored if _is_no_recall_category(str(v.get("category") or ""))]
    no_recall_ok = sum(1 for v in no_recall if str(v.get("verdict") or "").upper() == "PASS")
    violations = sum(1 for v in scored if v.get("privacy_violation") is True)
    hard = [v for v in scored if isinstance(v.get("hard_trigger_correct"), bool)]
    hard_ok = sum(1 for v in hard if v.get("hard_trigger_correct") is True)
    return {
        "cases": len(verdicts),
        "scored": len(scored),
        "categories": dict(categories),
        "verdicts": dict(verdict_counts),
        "not_scored_reasons": dict(not_scored_reasons),
        "pass_rate": ratio(sum(1 for v in scored if str(v.get("verdict")).upper() == "PASS"), len(scored)),
        "required_recall_rate": ratio(required_hits, len(recall_cases)),
        "required_recall_n": len(recall_cases),
        "extra_type_rate": ratio(extra_hits, len(extra_cases)),
        "extra_type_n": len(extra_cases),
        "no_recall_accuracy": ratio(no_recall_ok, len(no_recall)),
        "no_recall_n": len(no_recall),
        "privacy_accuracy": ratio(len(scored) - violations, len(scored)),
        "privacy_violations": violations,
        "hard_trigger_accuracy": ratio(hard_ok, len(hard)),
        "hard_trigger_n": len(hard),
    }


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------


def aggregate(s: Samples) -> dict[str, Any]:
    m: dict[str, Any] = {"runtime_roots": len(s.runtime_roots), "warnings": list(s.warnings)}

    # --- turn 墙钟 ---
    settled = [t for t in s.turns if t["wall_ms"] is not None and t["wall_ms"] > 0]
    completed = [t for t in settled if t["terminal_state"] == "COMPLETED"]
    m["turn_wall_ms"] = {
        "all_settled": stats([t["wall_ms"] for t in settled]),
        "completed_only": stats([t["wall_ms"] for t in completed]),
        "turns_total": len(s.turns),
        "turns_unsettled": len(s.turns) - len(settled),
        "terminal_states": dict(Counter(str(t["terminal_state"]) for t in settled)),
    }
    m["driver_turn_wall_s"] = {
        "all": stats([t["elapsed_s"] for t in s.driver_turns if t["elapsed_s"] is not None]),
        "completed_only": stats(
            [
                t["elapsed_s"]
                for t in s.driver_turns
                if t["elapsed_s"] is not None
                and (t["last_run_state"] == "COMPLETED" or t["outcome"] == "COMPLETED")
            ]
        ),
        "outcomes": dict(Counter(f"{t['outcome']}/{t['last_run_state']}" for t in s.driver_turns)),
    }

    # --- provider 调用延迟 ---
    ok_calls = [c for c in s.provider_calls if c["state"] == "succeeded" and c["latency_ms"] is not None]
    # 语料批次的 SDK 在冻结时钟下运行（claimed_at == settled_at），0 ms 视为「未测量」而不是真实延迟
    measured = [c for c in ok_calls if c["latency_ms"] > 0]
    m["provider_latency_ms"] = {
        "db_succeeded": stats([c["latency_ms"] for c in measured]),
        "db_unmeasured_zero_duration": len(ok_calls) - len(measured),
        "native_log_elapsed": stats(s.log_provider_elapsed_ms),
        "states": dict(Counter(str(c["state"]) for c in s.provider_calls)),
        "error_codes": dict(Counter(str(c["error_code"]) for c in s.provider_calls if c["error_code"])),
        "log_started": s.log_provider_started,
        "log_succeeded": s.log_provider_succeeded,
        "log_failed_or_degraded": s.log_provider_failed,
        "timeout_seconds_seen": sorted(set(s.log_provider_timeouts_s)),
    }

    # --- 每 turn provider 调用数 / token ---
    audit_ok = [a for a in s.attempt_audit if a["usage_available"] and a["input_tokens"] is not None]
    # 语料批次里 host_run_id 由确定性种子派生、跨用例重复，必须按 (运行时根, run) 分组
    per_run_calls: Counter = Counter((a["root"], a["root_run_id"]) for a in s.attempt_audit)
    per_run_in: dict[tuple[str, Any], int] = defaultdict(int)
    per_run_out: dict[tuple[str, Any], int] = defaultdict(int)
    for a in audit_ok:
        per_run_in[(a["root"], a["root_run_id"])] += int(a["input_tokens"] or 0)
        per_run_out[(a["root"], a["root_run_id"])] += int(a["output_tokens"] or 0)
    m["provider_calls_per_turn"] = stats(list(per_run_calls.values()), digits=2)
    m["provider_calls_total"] = len(s.attempt_audit)
    m["tokens"] = {
        "input_per_turn": stats(list(per_run_in.values()), digits=0),
        "output_per_turn": stats(list(per_run_out.values()), digits=0),
        "input_per_call": stats([a["input_tokens"] for a in audit_ok], digits=0),
        "output_per_call": stats([a["output_tokens"] for a in audit_ok if a["output_tokens"] is not None], digits=0),
        "peak_input_tokens_single_call": max((int(a["input_tokens"]) for a in audit_ok), default=None),
        "peak_input_tokens_run_sum": max(per_run_in.values(), default=None),
        "cache_tokens_total": sum(int(a["cache_tokens"] or 0) for a in audit_ok),
        "input_tokens_total": sum(int(a["input_tokens"] or 0) for a in audit_ok),
        "output_tokens_total": sum(int(a["output_tokens"] or 0) for a in audit_ok),
        "attempt_states": dict(Counter(str(a["state"]) for a in s.attempt_audit)),
        "models": sorted({str(a["model_id"]) for a in s.attempt_audit if a["model_id"]}),
    }

    # --- Host 估算 vs Provider 实报 ---
    ratios_msgs: list[float] = []
    ratios_full: list[float] = []
    est_peak_msgs = 0
    est_peak_full = 0
    for c in s.provider_calls:
        prov = c.get("input_tokens")
        est_peak_msgs = max(est_peak_msgs, int(c["est_tokens_messages"] or 0))
        est_peak_full = max(est_peak_full, int(c["est_tokens_with_tools"] or 0))
        if prov and int(prov) > 0:
            ratios_msgs.append(c["est_tokens_messages"] / int(prov))
            ratios_full.append(c["est_tokens_with_tools"] / int(prov))
    m["host_estimate_vs_provider"] = {
        "ratio_messages_only": stats(ratios_msgs, digits=3),
        "ratio_with_tools": stats(ratios_full, digits=3),
        "peak_estimated_messages_tokens": est_peak_msgs,
        "peak_estimated_with_tools_tokens": est_peak_full,
        "estimator": "deskpet.sdk_adapters.context_partitions.text_tokens"
        if HELPERS.get("host_import")
        else "fallback(text_tokens verbatim copy)",
    }

    # --- typed recall ---
    by_caller: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in s.recall_calls:
        by_caller[str(r["caller"])].append(r)
    recall_block: dict[str, Any] = {}
    for caller, rows in sorted(by_caller.items()):
        lat = [r["latency_ms"] for r in rows if r["latency_ms"] is not None]
        raised = sum(1 for r in rows if r["state"] == "raised")
        recall_block[caller] = {
            "latency_ms": stats(lat),
            "states": dict(Counter(str(r["state"]) for r in rows)),
            "raised_rate": ratio(raised, len(rows)),
            "over_500ms": sum(1 for v in lat if v > CONTRACT["recall_p95_ms"]),
            "over_2000ms": sum(1 for v in lat if v > CONTRACT["recall_hard_deadline_ms"]),
        }
    all_lat = [r["latency_ms"] for r in s.recall_calls if r["latency_ms"] is not None]
    term_kinds = Counter(str(t["terminal_kind"]) for t in s.recall_terminals)
    degr = Counter()
    for t in s.recall_terminals:
        for code in t["degradation_codes"] or []:
            degr[str(code)] += 1
    deadline_exceeded = term_kinds.get("deadline_exceeded", 0)
    m["typed_recall"] = {
        "by_caller": recall_block,
        "all_callers_latency_ms": stats(all_lat),
        "sdk_terminal_kinds": dict(term_kinds),
        "sdk_degradation_codes": dict(degr),
        "sdk_timeout_rate": ratio(deadline_exceeded, sum(term_kinds.values())),
        "sdk_terminals_total": sum(term_kinds.values()),
        "request_deadline_ms_seen": sorted(set(s.recall_deadlines_ms)),
    }

    # --- 分析通道 ---
    inv_ok = [i for i in s.analysis_invocations if i["status"] == "public"]
    inv_all = s.analysis_invocations
    batches = s.analysis_batches
    batch_states = Counter(str(b["state"]) for b in batches)
    failed_batches = sum(v for k, v in batch_states.items() if k not in ("applied", "completed", "succeeded"))
    budgets = {
        "deadline_ms": sorted({b["deadline_ms"] for b in batches if b["deadline_ms"] is not None}),
        "max_input_tokens": sorted({b["max_input_tokens"] for b in batches if b["max_input_tokens"] is not None}),
        "max_output_tokens": sorted({b["max_output_tokens"] for b in batches if b["max_output_tokens"] is not None}),
    }
    m["analysis_lane"] = {
        "invocations_total": len(inv_all),
        "invocation_status": dict(Counter(f"{i['status']}/{i['reason_code']}" for i in inv_all)),
        "latency_ms_accepted": stats([i["latency_ms"] for i in inv_ok if i["latency_ms"]]),
        "latency_unmeasured": sum(1 for i in inv_ok if not i["latency_ms"]),
        "input_tokens_accepted": stats([i["input_tokens"] for i in inv_ok if i["input_tokens"]], digits=0),
        "output_tokens_accepted": stats([i["output_tokens"] for i in inv_ok if i["output_tokens"]], digits=0),
        "cost_microunits_total": sum(int(i["cost_microunits"] or 0) for i in inv_all),
        "invocation_failure_rate": ratio(len(inv_all) - len(inv_ok), len(inv_all)),
        "batches_total": len(batches),
        "batch_states": dict(batch_states),
        "batch_failure_rate": ratio(failed_batches, len(batches)),
        "batch_failure_reasons": dict(
            Counter(str(b["reason_code"]) for b in batches if b["reason_code"])
        ),
        "batch_wall_ms": stats([b["wall_ms"] for b in batches if b["wall_ms"] is not None]),
        "request_budget_seen": budgets,
    }

    # --- 上下文组装 ---
    receipts = s.snapshot_receipts
    m["context_assembly"] = {
        "receipts": len(receipts),
        "budget_tiers": dict(Counter(str(r["budget_tier"]) for r in receipts)),
        "causal_groups": stats([r["causal_groups"] for r in receipts if r["causal_groups"] is not None], digits=2),
        "trimmed_groups_total": sum(int(r["trimmed_groups"] or 0) for r in receipts),
        "receipts_with_trim": sum(1 for r in receipts if (r["trimmed_groups"] or 0) > 0),
        "current_tool_pages_total": sum(int(r["current_tool_pages"] or 0) for r in receipts),
        "current_tool_tokens_max": max((int(r["current_tool_tokens"] or 0) for r in receipts), default=None),
        "context_page_in_effects": s.page_in_effects,
        "log_context_events": dict(s.log_context_events),
        "log_context_triple_mismatch": (
            len(
                {
                    s.log_context_events.get("context.preparing", 0),
                    s.log_context_events.get("context.staged", 0),
                    s.log_context_events.get("context.consumed", 0),
                }
            )
            > 1
        ),
    }

    # --- Run 失败 ---
    terminal_total = len(settled)
    reasons = Counter(str(f["reason"]) for f in s.run_failures)
    m["run_failures"] = {
        "runs_with_terminal": terminal_total,
        "failed_runs": len(s.run_failures),
        "failure_rate": ratio(len(s.run_failures), terminal_total),
        "by_reason": dict(reasons),
        "driver_runtime_failed_by_detail": dict(s.log_runtime_failed),
    }

    # --- 预算推导（窗口来自 native.log 最后一次 model_context_resolved） ---
    window = None
    models_used = m["tokens"]["models"]
    for model in models_used:
        if model in s.log_model_windows:
            window = s.log_model_windows[model] if window is None else min(window, s.log_model_windows[model])
    if window is None and s.log_model_windows:
        window = min(s.log_model_windows.values())
    budget_info: dict[str, Any] = {"window_tokens": window, "model_windows": dict(s.log_model_windows)}
    if window:
        tier = HELPERS["budget_window"](window)
        budget_info["budget_tier"] = tier
        budget_info["effective_input_budget"] = HELPERS["effective_input_budget"](window)
    m["budget"] = budget_info

    # --- 语料批次用例级 ---
    cases = s.corpus_cases
    m["corpus_cases"] = {
        "cases": len(cases),
        "elapsed_seconds": stats([c["elapsed_seconds"] for c in cases if c["elapsed_seconds"] is not None]),
        "oracle_verdicts": dict(Counter(str(c["oracle_verdict"]) for c in cases)),
        "execution_failed_rate": ratio(
            sum(1 for c in cases if str(c["oracle_verdict"]) == "EXECUTION_FAILED"), len(cases)
        ),
        "peak_rss_mib_max": (
            round(max(int(c["peak_rss_kib"] or 0) for c in cases) / 1024.0, 0) if cases else None
        ),
    }
    return m


# ---------------------------------------------------------------------------
# 契约判定
# ---------------------------------------------------------------------------


def _verdict(cond: bool | None) -> str:
    if cond is None:
        return "N-A"
    return "PASS" if cond else "FAIL"


def build_verdicts(m: dict[str, Any], corpus: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add(key: str, label: str, threshold: str, observed: Any, cond: bool | None, source: str, note: str = "") -> None:
        out.append(
            {
                "key": key,
                "metric": label,
                "threshold": threshold,
                "observed": observed,
                "verdict": _verdict(cond),
                "source": source,
                "note": note,
            }
        )

    fg = (m["typed_recall"]["by_caller"] or {}).get("foreground_recall") or {}
    fg_lat = fg.get("latency_ms") or {"n": 0}
    add(
        "recall_p95",
        "typed recall（foreground_recall，Host 墙钟）p95",
        f"≤ {CONTRACT['recall_p95_ms']} ms",
        fg_lat.get("p95"),
        None if not fg_lat.get("n") else fg_lat["p95"] <= CONTRACT["recall_p95_ms"],
        "acceptance.md HM-AC-8",
        "样本来源 operation-audit.db.memory_call_attempts(caller=foreground_recall)",
    )
    add(
        "recall_hard_deadline",
        "typed recall（foreground_recall）最大值 / SDK deadline_exceeded",
        f"max ≤ {CONTRACT['recall_hard_deadline_ms']} ms 且 SDK 超时率 = 0",
        {"max_ms": fg_lat.get("max"), "sdk_timeout_rate": m["typed_recall"]["sdk_timeout_rate"]},
        None
        if not fg_lat.get("n")
        else (fg_lat["max"] <= CONTRACT["recall_hard_deadline_ms"] and (m["typed_recall"]["sdk_timeout_rate"] or 0) == 0),
        "acceptance.md HM-AC-8 / S3 hard deadline",
    )
    all_lat = m["typed_recall"]["all_callers_latency_ms"]
    add(
        "recall_all_p95",
        "typed recall（全部 caller）p95",
        f"≤ {CONTRACT['recall_p95_ms']} ms",
        all_lat.get("p95"),
        None if not all_lat.get("n") else all_lat["p95"] <= CONTRACT["recall_p95_ms"],
        "acceptance.md HM-AC-8（信息性：含 analysis_candidates 等后台 caller）",
    )

    timeouts = m["provider_latency_ms"]["timeout_seconds_seen"] or [CONTRACT["provider_timeout_s_default"]]
    timeout_ms = min(timeouts) * 1000.0
    prov = m["provider_latency_ms"]["db_succeeded"]
    prov_err = m["provider_latency_ms"]["error_codes"]
    timeout_errors = sum(v for k, v in prov_err.items() if "timeout" in k.lower())
    add(
        "provider_timeout",
        "Provider 调用最大延迟 / 超时错误",
        f"max < {timeout_ms / 1000:.0f} s 且无 timeout error_code",
        {"max_ms": prov.get("max"), "timeout_error_codes": timeout_errors, "log_failed_or_degraded": m["provider_latency_ms"]["log_failed_or_degraded"]},
        None if not prov.get("n") else (prov["max"] < timeout_ms and timeout_errors == 0),
        "S5 provider timeout（native.log timeout_seconds）",
    )

    budget = m["budget"]
    eib = budget.get("effective_input_budget")
    peak = m["tokens"]["peak_input_tokens_single_call"]
    add(
        "input_budget_provider",
        "Provider 实报单次 input_tokens 峰值",
        f"≤ effective_input_budget={eib}" if eib else "effective_input_budget 未知",
        peak,
        None if (eib is None or peak is None) else peak <= eib,
        "S5 context_partitions.effective_input_budget（窗口取 native.log model_context_resolved）",
    )
    est_peak = m["host_estimate_vs_provider"]["peak_estimated_messages_tokens"]
    add(
        "input_budget_estimated",
        "Host 估算（messages）单次峰值",
        f"≤ effective_input_budget={eib}" if eib else "effective_input_budget 未知",
        est_peak,
        None if (eib is None or not est_peak) else est_peak <= eib,
        "S5 effective_input_budget",
    )

    al = m["analysis_lane"]
    bud = al["request_budget_seen"]
    dl = max(bud["deadline_ms"]) if bud["deadline_ms"] else None
    lat = al["latency_ms_accepted"]
    add(
        "analysis_deadline",
        "分析通道 LLM 延迟最大值",
        f"≤ 请求预算 deadline_ms={dl}" if dl else "请求预算未知",
        lat.get("max"),
        None if (dl is None or not lat.get("n")) else lat["max"] <= dl,
        "analysis_batches.request_json.budget",
    )
    mi = max(bud["max_input_tokens"]) if bud["max_input_tokens"] else None
    it = al["input_tokens_accepted"]
    add(
        "analysis_input_budget",
        "分析通道输入 token 最大值",
        f"≤ 请求预算 max_input_tokens={mi}" if mi else "请求预算未知",
        it.get("max"),
        None if (mi is None or not it.get("n")) else it["max"] <= mi,
        "analysis_batches.request_json.budget",
    )
    mo = max(bud["max_output_tokens"]) if bud["max_output_tokens"] else None
    ot = al["output_tokens_accepted"]
    add(
        "analysis_output_budget",
        "分析通道输出 token 最大值",
        f"≤ 请求预算 max_output_tokens={mo}" if mo else "请求预算未知",
        ot.get("max"),
        None if (mo is None or not ot.get("n")) else ot["max"] <= mo,
        "analysis_batches.request_json.budget",
    )
    add(
        "context_triple",
        "native.log context.preparing/staged/consumed 三元组计数一致",
        "三者相等",
        m["context_assembly"]["log_context_events"],
        None if not m["context_assembly"]["log_context_events"] else not m["context_assembly"]["log_context_triple_mismatch"],
        "plans/2026-09-08-hm-to-a6/00-PLAN.md §0",
    )
    # 无契约阈值的指标
    for key, label, observed in (
        ("turn_wall", "每 turn 墙钟（enqueued→终态）p95", m["turn_wall_ms"]["all_settled"].get("p95")),
        ("calls_per_turn", "每 turn provider 调用数 p95", m["provider_calls_per_turn"].get("p95")),
        ("estimate_ratio", "Host 估算 / Provider 实报（messages）p50", m["host_estimate_vs_provider"]["ratio_messages_only"].get("p50")),
        ("run_failure_rate", "Run 失败率", m["run_failures"]["failure_rate"]),
        ("analysis_failure_rate", "分析通道 invocation 失败率", al["invocation_failure_rate"]),
    ):
        add(key, label, "契约未给出阈值", observed, None, "—")

    if corpus and corpus.get("scored"):
        rr = corpus["required_recall_rate"]
        add(
            "corpus_required_recall",
            "语料复审 required-type recall",
            f"≥ {CONTRACT['corpus_required_recall_min']:.0%}",
            {"rate": rr, "n": corpus["required_recall_n"]},
            None if rr is None else rr >= CONTRACT["corpus_required_recall_min"],
            "acceptance.md HM-AC-8（review-verdicts.json，已计分用例）",
        )
        er = corpus["extra_type_rate"]
        add(
            "corpus_extra_type",
            "语料复审额外类型率",
            f"≤ {CONTRACT['corpus_extra_type_rate_max']:.0%}",
            {"rate": er, "n": corpus["extra_type_n"]},
            None if er is None else er <= CONTRACT["corpus_extra_type_rate_max"],
            "acceptance.md HM-AC-8",
        )
        nr = corpus["no_recall_accuracy"]
        add(
            "corpus_no_recall",
            "语料复审 no-recall（no-match 类）判断正确率",
            f"≥ {CONTRACT['corpus_no_recall_accuracy_min']:.0%}",
            {"rate": nr, "n": corpus["no_recall_n"]},
            None if nr is None else nr >= CONTRACT["corpus_no_recall_accuracy_min"],
            "acceptance.md HM-AC-8",
        )
        pv = corpus["privacy_accuracy"]
        add(
            "corpus_privacy",
            "语料复审隐私禁止项正确率（已计分用例无 privacy_violation）",
            "= 100%",
            {"rate": pv, "violations": corpus["privacy_violations"]},
            None if pv is None else pv >= CONTRACT["corpus_privacy_accuracy"],
            "acceptance.md HM-AC-8",
        )
        ht = corpus["hard_trigger_accuracy"]
        add(
            "corpus_hard_trigger",
            "语料复审硬触发正确率",
            "= 100%",
            {"rate": ht, "n": corpus["hard_trigger_n"]},
            None if ht is None else ht >= 1.0,
            "acceptance.md HM-AC-8（冻结路由集硬触发召回率 100%）",
        )
    return out


# ---------------------------------------------------------------------------
# Markdown 渲染
# ---------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if value != value:  # NaN
            return "—"
        if value.is_integer():
            return f"{int(value):,}"
        if abs(value) < 10:
            return f"{value:,.3f}".rstrip("0").rstrip(".")
        return f"{value:,.1f}".rstrip("0").rstrip(".")
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, dict):
        return "; ".join(f"{k}={_fmt(v)}" for k, v in value.items()) or "—"
    if isinstance(value, list):
        return ", ".join(_fmt(v) for v in value) or "—"
    return str(value)


def _st(block: dict[str, Any], key: str = "p95") -> str:
    if not block or not block.get("n"):
        return "—"
    return _fmt(block.get(key))


def _row(label: str, getter: Callable[[dict[str, Any]], Any], per: dict[str, dict[str, Any]], pooled: dict[str, Any]) -> str:
    cells = [label]
    for name in per:
        try:
            cells.append(_fmt(getter(per[name])))
        except Exception:  # noqa: BLE001
            cells.append("—")
    try:
        cells.append(_fmt(getter(pooled)))
    except Exception:  # noqa: BLE001
        cells.append("—")
    return "| " + " | ".join(cells) + " |"


def render_markdown(report: dict[str, Any]) -> str:
    per: dict[str, dict[str, Any]] = report["per_evidence"]
    pooled: dict[str, Any] = report["pooled"]
    names = list(per)
    header = "| 指标 | " + " | ".join(names) + " | 汇总 |"
    sep = "|---|" + "---|" * (len(names) + 1)
    lines: list[str] = []
    lines.append(f"# HM 基准报告（{SCHEMA}）")
    lines.append("")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(f"- 估算器：{pooled['host_estimate_vs_provider']['estimator']}")
    lines.append("- 分位数：nearest-rank；汇总列为各目录原始样本合并后重新计算。")
    lines.append("")
    lines.append("## 1. 证据目录")
    lines.append("")
    lines.append("| 名称 | 路径 | 运行时根数 | 警告数 |")
    lines.append("|---|---|---|---|")
    for name in names:
        lines.append(
            f"| {name} | `{report['evidence_paths'][name]}` | {per[name]['runtime_roots']} | {len(per[name]['warnings'])} |"
        )
    lines.append("")

    def section(title: str, rows: list[tuple[str, Callable[[dict[str, Any]], Any]]]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        lines.append(header)
        lines.append(sep)
        for label, getter in rows:
            lines.append(_row(label, getter, per, pooled))
        lines.append("")

    section(
        "2. 每 turn 墙钟（发送 → 终态）",
        [
            ("turn 数（库内）", lambda m: m["turn_wall_ms"]["turns_total"]),
            ("终态分布", lambda m: m["turn_wall_ms"]["terminal_states"]),
            ("库内墙钟 p50 (ms)", lambda m: _st(m["turn_wall_ms"]["all_settled"], "p50")),
            ("库内墙钟 p95 (ms)", lambda m: _st(m["turn_wall_ms"]["all_settled"], "p95")),
            ("库内墙钟 max (ms)", lambda m: _st(m["turn_wall_ms"]["all_settled"], "max")),
            ("仅 COMPLETED p95 (ms)", lambda m: _st(m["turn_wall_ms"]["completed_only"], "p95")),
            ("UI 驱动 turn 数", lambda m: m["driver_turn_wall_s"]["all"]["n"]),
            ("UI 驱动 p50 (s)", lambda m: _st(m["driver_turn_wall_s"]["all"], "p50")),
            ("UI 驱动 p95 (s)", lambda m: _st(m["driver_turn_wall_s"]["all"], "p95")),
            ("UI 驱动 max (s)", lambda m: _st(m["driver_turn_wall_s"]["all"], "max")),
        ],
    )
    section(
        "3. Provider 调用",
        [
            ("调用数（结算审计）", lambda m: m["provider_calls_total"]),
            ("结算状态", lambda m: m["tokens"]["attempt_states"]),
            ("延迟 p50 (ms, 库)", lambda m: _st(m["provider_latency_ms"]["db_succeeded"], "p50")),
            ("延迟 p95 (ms, 库)", lambda m: _st(m["provider_latency_ms"]["db_succeeded"], "p95")),
            ("延迟 max (ms, 库)", lambda m: _st(m["provider_latency_ms"]["db_succeeded"], "max")),
            ("延迟未测量（冻结时钟）", lambda m: m["provider_latency_ms"]["db_unmeasured_zero_duration"]),
            ("延迟 p95 (ms, native.log)", lambda m: _st(m["provider_latency_ms"]["native_log_elapsed"], "p95")),
            ("native.log started/succeeded/failed", lambda m: f"{m['provider_latency_ms']['log_started']}/{m['provider_latency_ms']['log_succeeded']}/{m['provider_latency_ms']['log_failed_or_degraded']}"),
            ("timeout_seconds", lambda m: m["provider_latency_ms"]["timeout_seconds_seen"]),
            ("每 turn 调用数 p50", lambda m: _st(m["provider_calls_per_turn"], "p50")),
            ("每 turn 调用数 p95", lambda m: _st(m["provider_calls_per_turn"], "p95")),
            ("每 turn 调用数 max", lambda m: _st(m["provider_calls_per_turn"], "max")),
        ],
    )
    section(
        "4. Token",
        [
            ("模型", lambda m: m["tokens"]["models"]),
            ("输入 token/turn p50", lambda m: _st(m["tokens"]["input_per_turn"], "p50")),
            ("输入 token/turn p95", lambda m: _st(m["tokens"]["input_per_turn"], "p95")),
            ("输入 token/turn max", lambda m: _st(m["tokens"]["input_per_turn"], "max")),
            ("输出 token/turn p50", lambda m: _st(m["tokens"]["output_per_turn"], "p50")),
            ("输出 token/turn p95", lambda m: _st(m["tokens"]["output_per_turn"], "p95")),
            ("单次输入峰值", lambda m: m["tokens"]["peak_input_tokens_single_call"]),
            ("单次输入 p50", lambda m: _st(m["tokens"]["input_per_call"], "p50")),
            ("输入 token 合计", lambda m: m["tokens"]["input_tokens_total"]),
            ("输出 token 合计", lambda m: m["tokens"]["output_tokens_total"]),
            ("cache token 合计", lambda m: m["tokens"]["cache_tokens_total"]),
            ("窗口 / 档位 / effective_input_budget", lambda m: f"{_fmt(m['budget'].get('window_tokens'))} / {_fmt(m['budget'].get('budget_tier'))} / {_fmt(m['budget'].get('effective_input_budget'))}"),
            ("Host 估算/实报（messages）p50", lambda m: _st(m["host_estimate_vs_provider"]["ratio_messages_only"], "p50")),
            ("Host 估算/实报（messages）p95", lambda m: _st(m["host_estimate_vs_provider"]["ratio_messages_only"], "p95")),
            ("Host 估算/实报（含 tools）p50", lambda m: _st(m["host_estimate_vs_provider"]["ratio_with_tools"], "p50")),
            ("Host 估算峰值（messages）", lambda m: m["host_estimate_vs_provider"]["peak_estimated_messages_tokens"]),
        ],
    )
    section(
        "5. typed recall",
        [
            ("foreground_recall 次数", lambda m: m["typed_recall"]["by_caller"].get("foreground_recall", {}).get("latency_ms", {}).get("n", 0)),
            ("foreground_recall p50 (ms)", lambda m: _st(m["typed_recall"]["by_caller"].get("foreground_recall", {}).get("latency_ms", {}), "p50")),
            ("foreground_recall p95 (ms)", lambda m: _st(m["typed_recall"]["by_caller"].get("foreground_recall", {}).get("latency_ms", {}), "p95")),
            ("foreground_recall max (ms)", lambda m: _st(m["typed_recall"]["by_caller"].get("foreground_recall", {}).get("latency_ms", {}), "max")),
            ("foreground_recall 状态", lambda m: m["typed_recall"]["by_caller"].get("foreground_recall", {}).get("states")),
            ("analysis_candidates p95 (ms)", lambda m: _st(m["typed_recall"]["by_caller"].get("analysis_candidates", {}).get("latency_ms", {}), "p95")),
            ("全部 caller p95 (ms)", lambda m: _st(m["typed_recall"]["all_callers_latency_ms"], "p95")),
            ("SDK 终态", lambda m: m["typed_recall"]["sdk_terminal_kinds"]),
            ("SDK 超时率", lambda m: m["typed_recall"]["sdk_timeout_rate"]),
            ("SDK 降级码", lambda m: m["typed_recall"]["sdk_degradation_codes"]),
            ("请求 deadline_ms", lambda m: m["typed_recall"]["request_deadline_ms_seen"]),
        ],
    )
    section(
        "6. 分析通道",
        [
            ("invocation 数", lambda m: m["analysis_lane"]["invocations_total"]),
            ("invocation 状态", lambda m: m["analysis_lane"]["invocation_status"]),
            ("invocation 失败率", lambda m: m["analysis_lane"]["invocation_failure_rate"]),
            ("延迟 p50 (ms)", lambda m: _st(m["analysis_lane"]["latency_ms_accepted"], "p50")),
            ("延迟 p95 (ms)", lambda m: _st(m["analysis_lane"]["latency_ms_accepted"], "p95")),
            ("延迟 max (ms)", lambda m: _st(m["analysis_lane"]["latency_ms_accepted"], "max")),
            ("延迟未测量（冻结时钟）", lambda m: m["analysis_lane"]["latency_unmeasured"]),
            ("输入 token/批 p50", lambda m: _st(m["analysis_lane"]["input_tokens_accepted"], "p50")),
            ("输入 token/批 max", lambda m: _st(m["analysis_lane"]["input_tokens_accepted"], "max")),
            ("输出 token/批 p50", lambda m: _st(m["analysis_lane"]["output_tokens_accepted"], "p50")),
            ("输出 token/批 max", lambda m: _st(m["analysis_lane"]["output_tokens_accepted"], "max")),
            ("批次数 / 状态", lambda m: f"{m['analysis_lane']['batches_total']} / {_fmt(m['analysis_lane']['batch_states'])}"),
            ("批次失败率", lambda m: m["analysis_lane"]["batch_failure_rate"]),
            ("批次失败原因", lambda m: m["analysis_lane"]["batch_failure_reasons"]),
            ("批次墙钟 p95 (ms)", lambda m: _st(m["analysis_lane"]["batch_wall_ms"], "p95")),
            ("请求预算", lambda m: m["analysis_lane"]["request_budget_seen"]),
        ],
    )
    section(
        "7. 上下文组装",
        [
            ("快照回执数", lambda m: m["context_assembly"]["receipts"]),
            ("budget_tier", lambda m: m["context_assembly"]["budget_tiers"]),
            ("causal_groups p50", lambda m: _st(m["context_assembly"]["causal_groups"], "p50")),
            ("causal_groups max", lambda m: _st(m["context_assembly"]["causal_groups"], "max")),
            ("trimmed_groups 合计", lambda m: m["context_assembly"]["trimmed_groups_total"]),
            ("含裁剪的回执数", lambda m: m["context_assembly"]["receipts_with_trim"]),
            ("current_tool_pages 合计", lambda m: m["context_assembly"]["current_tool_pages_total"]),
            ("current_tool_tokens max", lambda m: m["context_assembly"]["current_tool_tokens_max"]),
            ("context_page_in 调用", lambda m: m["context_assembly"]["context_page_in_effects"]),
            ("native.log 三元组", lambda m: m["context_assembly"]["log_context_events"]),
        ],
    )
    section(
        "8. Run 失败",
        [
            ("有终态的 turn", lambda m: m["run_failures"]["runs_with_terminal"]),
            ("失败 Run 数", lambda m: m["run_failures"]["failed_runs"]),
            ("失败率", lambda m: m["run_failures"]["failure_rate"]),
            ("按 reason code", lambda m: m["run_failures"]["by_reason"]),
            ("驱动层 runtime.failed", lambda m: m["run_failures"]["driver_runtime_failed_by_detail"]),
        ],
    )
    section(
        "9. 语料批次（用例级）",
        [
            ("用例数", lambda m: m["corpus_cases"]["cases"]),
            ("用例耗时 p50 (s)", lambda m: _st(m["corpus_cases"]["elapsed_seconds"], "p50")),
            ("用例耗时 p95 (s)", lambda m: _st(m["corpus_cases"]["elapsed_seconds"], "p95")),
            ("oracle 判定", lambda m: m["corpus_cases"]["oracle_verdicts"]),
            ("EXECUTION_FAILED 率", lambda m: m["corpus_cases"]["execution_failed_rate"]),
            ("峰值 RSS max (MiB)", lambda m: m["corpus_cases"]["peak_rss_mib_max"]),
        ],
    )
    if report.get("corpus_review"):
        cr = report["corpus_review"]
        lines.append("## 10. 语料复审判定（--review-verdicts）")
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|---|---|")
        for k in (
            "cases",
            "scored",
            "categories",
            "verdicts",
            "pass_rate",
            "required_recall_rate",
            "required_recall_n",
            "extra_type_rate",
            "extra_type_n",
            "no_recall_accuracy",
            "no_recall_n",
            "privacy_accuracy",
            "privacy_violations",
            "hard_trigger_accuracy",
            "hard_trigger_n",
            "not_scored_reasons",
        ):
            lines.append(f"| {k} | {_fmt(cr.get(k))} |")
        lines.append("")
    lines.append("## 11. 契约阈值判定（汇总）")
    lines.append("")
    lines.append("| 指标 | 阈值 | 实测 | 判定 | 依据 |")
    lines.append("|---|---|---|---|---|")
    for v in report["verdicts"]:
        lines.append(f"| {v['metric']} | {v['threshold']} | {_fmt(v['observed'])} | **{v['verdict']}** | {v['source']} |")
    lines.append("")
    if report.get("per_evidence_verdicts"):
        lines.append("### 11.1 逐目录判定")
        lines.append("")
        keys = [v["key"] for v in report["verdicts"]]
        lines.append("| 指标 | " + " | ".join(names) + " |")
        lines.append("|---|" + "---|" * len(names))
        label_by_key = {v["key"]: v["metric"] for v in report["verdicts"]}
        for key in keys:
            cells = []
            for name in names:
                found = next((v for v in report["per_evidence_verdicts"][name] if v["key"] == key), None)
                cells.append(f"{found['verdict']}（{_fmt(found['observed'])}）" if found else "—")
            lines.append(f"| {label_by_key[key]} | " + " | ".join(cells) + " |")
        lines.append("")
    warnings = pooled.get("warnings") or []
    if warnings:
        lines.append("## 12. 提取警告")
        lines.append("")
        for w in warnings[:50]:
            lines.append(f"- {w}")
        if len(warnings) > 50:
            lines.append(f"- …另有 {len(warnings) - 50} 条")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 报告构建
# ---------------------------------------------------------------------------


def build_report(
    evidence_dirs: Sequence[str],
    review_verdict_paths: Sequence[str] = (),
    scratch: str | None = None,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    import datetime as _dt

    own_scratch = scratch is None
    scratch = scratch or tempfile.mkdtemp(prefix="hm-benchmark-")
    try:
        per: dict[str, dict[str, Any]] = {}
        per_verdicts: dict[str, list[dict[str, Any]]] = {}
        paths: dict[str, str] = {}
        pooled_samples = Samples()
        for d in evidence_dirs:
            name = (labels or {}).get(d) or _label_for(d, per)
            samples = extract_evidence_dir(d, scratch)
            pooled_samples.extend(samples)
            per[name] = aggregate(samples)
            per_verdicts[name] = build_verdicts(per[name], None)
            paths[name] = os.path.abspath(d)
        pooled = aggregate(pooled_samples)
        corpus = None
        if review_verdict_paths:
            corpus = corpus_quality(load_review_verdicts(review_verdict_paths))
        verdicts = build_verdicts(pooled, corpus)
        return {
            "schema": SCHEMA,
            "generated_at": _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
            "contract": {k: v for k, v in CONTRACT.items()},
            "evidence_paths": paths,
            "review_verdict_paths": [os.path.abspath(p) for p in review_verdict_paths],
            "per_evidence": per,
            "per_evidence_verdicts": per_verdicts,
            "pooled": pooled,
            "corpus_review": corpus,
            "verdicts": verdicts,
        }
    finally:
        if own_scratch:
            shutil.rmtree(scratch, ignore_errors=True)


def _label_for(path: str, existing: dict[str, Any]) -> str:
    parts = [p for p in os.path.normpath(path).split(os.sep) if p and p != "."]
    label = "/".join(parts[-2:]) if len(parts) >= 2 else (parts[-1] if parts else path)
    base = label
    i = 2
    while label in existing:
        label = f"{base}#{i}"
        i += 1
    return label


# ---------------------------------------------------------------------------
# 合成夹具 + 自检
# ---------------------------------------------------------------------------


def build_synthetic_fixture(base_dir: str) -> str:
    """在 base_dir 下生成一个最小证据目录；返回其路径。

    夹具内容（用于断言）：
    * 3 个 turn：T1 COMPLETED（10 s，2 次 provider 调用）、T2 FAILED（5 s，1 次调用，reason=react_max_turns_exceeded）、
      T3 COMPLETED（20 s，1 次调用）
    * provider 调用延迟 1000/2000/3000/4000 ms；input_tokens 1000/2000/3000/4000；output 100/200/300/400
    * foreground_recall 3 次：200/400/2500 ms（最后一次 state=raised）；analysis_candidates 1 次 50 ms
    * typed_recall_terminals：completed×3，deadline_exceeded×1
    * 分析通道：2 次 invocation（public 8000 ms 4000 tok；rejected_unsafe），2 个批次（applied / failed）
    * 快照回执 4 条：causal_groups 1/2/3/4，trimmed_groups 0/0/1/2，current_tool_pages 0/0/0/1
    * native.log：2 组 context 三元组、4 条 provider succeeded elapsed_ms=1000..4000、窗口 32000
    * driver progress：3 turn，elapsed 12/6/22 s
    """
    ev = os.path.join(base_dir, "synthetic-evidence")
    data = os.path.join(ev, "primary-ui-synth", "userdata", "data")
    os.makedirs(os.path.join(data, "simple-harness-sdk"), exist_ok=True)
    t0 = 1_700_000_000.0

    state = sqlite3.connect(os.path.join(data, "state.db"))
    state.executescript(
        """
        create table foreground_turns(turn_id text, enqueue_sequence integer, enqueued_at real);
        create table foreground_runs(host_run_id text, turn_id text, admitted_at real);
        create table foreground_terminal_receipts(host_run_id text, sdk_run_id text, terminal_state text, recorded_at real);
        create table sdk_provider_attempt_audit(root_run_id text, state text, usage_available integer,
            input_tokens integer, output_tokens integer, total_tokens integer, cache_tokens integer,
            settled_at real, model_id text, context_window integer);
        create table run_context_snapshot_receipts(sdk_run_id text, provider_turn_ordinal integer, source_revisions_json text);
        """
    )
    turns = [("t1", 1, t0, "r1", "sdk1", "COMPLETED", 10.0), ("t2", 2, t0 + 100, "r2", "sdk2", "FAILED", 5.0), ("t3", 3, t0 + 200, "r3", "sdk3", "COMPLETED", 20.0)]
    for tid, seq, at, hr, sr, st, dur in turns:
        state.execute("insert into foreground_turns values(?,?,?)", (tid, seq, at))
        state.execute("insert into foreground_runs values(?,?,?)", (hr, tid, at + 0.1))
        state.execute("insert into foreground_terminal_receipts values(?,?,?,?)", (hr, sr, st, at + dur))
    audit = [("r1", 1000, 100), ("r1", 2000, 200), ("r2", 3000, 300), ("r3", 4000, 400)]
    for i, (run, inp, out) in enumerate(audit):
        state.execute(
            "insert into sdk_provider_attempt_audit values(?,?,?,?,?,?,?,?,?,?)",
            (run, "succeeded", 1, inp, out, inp + out, 0, t0 + i, "synthetic-model", 0),
        )
    receipts = [(1, 0, 0), (2, 0, 0), (3, 1, 0), (4, 2, 1)]
    for i, (groups, trimmed, pages) in enumerate(receipts):
        state.execute(
            "insert into run_context_snapshot_receipts values(?,?,?)",
            (
                f"sdk{i // 2 + 1}",
                i % 2 + 1,
                json.dumps(
                    {
                        "source_revisions": {
                            "budget_tier": 8192,
                            "causal_groups": groups,
                            "trimmed_groups": trimmed,
                            "current_tool_pages": pages,
                            "current_tool_tokens": pages * 256,
                        }
                    }
                ),
            ),
        )
    state.commit()
    state.close()

    audit_db = sqlite3.connect(os.path.join(data, "operation-audit.db"))
    audit_db.executescript(
        "create table memory_call_attempts(caller text, state text, observation_status text, started_at real, settled_at real);"
    )
    for caller, st, ms in (("foreground_recall", "returned", 200), ("foreground_recall", "returned", 400), ("foreground_recall", "raised", 2500), ("analysis_candidates", "returned", 50)):
        audit_db.execute("insert into memory_call_attempts values(?,?,?,?,?)", (caller, st, "not_applicable", t0, t0 + ms / 1000.0))
    audit_db.commit()
    audit_db.close()

    hm = sqlite3.connect(os.path.join(data, "human_memory_v7.db"))
    hm.executescript(
        """
        create table typed_recall_terminals(terminal_kind text, degradation_codes_json text);
        create table typed_recall_requests(request_json text);
        create table llm_invocations(output_storage_status text, output_reason_code text, input_tokens integer,
            output_tokens integer, cost_microunits integer, latency_ms integer, started_at real, completed_at real);
        create table analysis_batches(batch_id text, state text, attempt integer, request_json text, created_at real, updated_at real);
        create table job_attempts(batch_id text, reason_code text);
        """
    )
    for kind, codes in (("completed", "[]"), ("completed", "[]"), ("completed", '["cognitive_vector_stale"]'), ("deadline_exceeded", "[]")):
        hm.execute("insert into typed_recall_terminals values(?,?)", (kind, codes))
        hm.execute("insert into typed_recall_requests values(?)", (json.dumps({"context": {"budget": {"deadline_ms": 1000}}}),))
    hm.execute("insert into llm_invocations values(?,?,?,?,?,?,?,?)", ("public", "analysis_validator_accepted", 4000, 600, 0, 8000, t0, t0 + 8))
    hm.execute("insert into llm_invocations values(?,?,?,?,?,?,?,?)", ("rejected_unsafe", "analysis_delivery_authority_rejected", None, None, 0, 0, t0, t0))
    budget = json.dumps({"budget": {"deadline_ms": 180000, "max_input_tokens": 16384, "max_output_tokens": 6144}})
    hm.execute("insert into analysis_batches values(?,?,?,?,?,?)", ("b1", "applied", 1, budget, t0, t0 + 9))
    hm.execute("insert into analysis_batches values(?,?,?,?,?,?)", ("b2", "failed", 1, budget, t0, t0 + 1))
    hm.execute("insert into job_attempts values(?,?)", ("b2", "analysis_delivery_authority_rejected"))
    hm.commit()
    hm.close()

    ex = sqlite3.connect(os.path.join(data, "simple-harness-sdk", "execution-v6.sqlite3"))
    ex.executescript(
        """
        create table provider_invocations(invocation_id text, run_id text, state text, error_code text,
            request_json text, usage_json text, claimed_at real, handed_off_at real, settled_at real);
        create table run_events(run_id text, kind text, payload_json text);
        create table execution_effects(tool_name text);
        """
    )
    for i, (run, inp, out) in enumerate(audit):
        sdk_run = {"r1": "sdk1", "r2": "sdk2", "r3": "sdk3"}[run]
        msgs = [{"role": "user", "content": "x" * (inp * 4)}]
        ex.execute(
            "insert into provider_invocations values(?,?,?,?,?,?,?,?,?)",
            (
                f"inv{i}",
                sdk_run,
                "succeeded",
                None,
                json.dumps({"messages": msgs, "tools": []}),
                json.dumps({"usage": {"input_tokens": inp, "output_tokens": out, "cache_tokens": 0}}),
                t0 + i,
                t0 + i,
                t0 + i + (i + 1),
            ),
        )
    ex.execute("insert into run_events values(?,?,?)", ("sdk2", "run.failed", json.dumps({"raw_failures": [{"error_code": "react_max_turns_exceeded"}]})))
    ex.execute("insert into execution_effects values(?)", ("context_page_in",))
    ex.commit()
    ex.close()

    log_lines = []
    for i in range(2):
        for ev_name in ("context.preparing", "context.staged", "context.consumed"):
            log_lines.append(json.dumps({"event": ev_name, "logger": "simple_harness.host_observability"}))
    log_lines.append(json.dumps({"event": "model_context_resolved model=synthetic-model window=32000 source=global"}))
    for i in range(4):
        log_lines.append(json.dumps({"event": f"product_provider_attempt_started request_ref=r{i} provider_id=primary model=synthetic-model timeout_seconds=240.0"}))
        log_lines.append(json.dumps({"event": f"product_provider_attempt_succeeded request_ref=r{i} elapsed_ms={(i + 1) * 1000} finish_reason=stop"}))
    log_lines.append(json.dumps({"event": "foreground.runtime.failed", "error_detail": "primary_history_transcript_mismatch"}))
    with open(os.path.join(ev, "primary-ui-synth", "native.log"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(log_lines) + "\n")
    # merged 副本：内容完全相同，应被去重
    os.makedirs(os.path.join(ev, "merged"), exist_ok=True)
    with open(os.path.join(ev, "merged", "native.log"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(log_lines) + "\n")
    with open(os.path.join(ev, "primary-ui-synth", "synth-progress.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"turn": 0, "outcome": "baseline", "elapsed_s": 0}) + "\n")
        for turn, el, st in ((1, 12.0, "COMPLETED"), (2, 6.0, "FAILED"), (3, 22.0, "COMPLETED")):
            fh.write(json.dumps({"turn": turn, "outcome": "settled", "elapsed_s": el, "last_run_state": st}) + "\n")
    with open(os.path.join(ev, "batch-summary.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"case_id": "C00-01", "elapsed_seconds": 60.0, "oracle_verdict": "PENDING_POST_TERMINAL_REVIEW", "peak_rss_kib": 1048576}) + "\n")
        fh.write(json.dumps({"case_id": "C00-02", "elapsed_seconds": 90.0, "oracle_verdict": "EXECUTION_FAILED", "peak_rss_kib": 524288}) + "\n")
    with open(os.path.join(ev, "review-verdicts.json"), "w", encoding="utf-8") as fh:
        json.dump(
            [
                {"case_id": "C00-01", "category": "exact", "verdict": "PASS", "required_hit": True, "extra_types": 1, "privacy_violation": False},
                {"case_id": "C00-02", "category": "no_recall", "verdict": "PASS", "required_hit": None, "extra_types": 0, "privacy_violation": False},
                {"case_id": "C00-03", "category": "exact", "verdict": "FAIL", "required_hit": False, "extra_types": 0, "privacy_violation": False},
                {"case_id": "C00-04", "category": "privacy", "verdict": "PASS", "required_hit": None, "extra_types": 0, "privacy_violation": False},
            ],
            fh,
        )
    return ev


def selftest_expectations(report: dict[str, Any]) -> list[str]:
    """返回不满足的断言列表（空表示自检通过）。"""
    m = report["pooled"]
    failures: list[str] = []

    def expect(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)

    expect(m["runtime_roots"] == 1, f"runtime_roots={m['runtime_roots']}")
    tw = m["turn_wall_ms"]["all_settled"]
    expect(tw["n"] == 3 and tw["p50"] == 10000.0 and tw["p95"] == 20000.0 and tw["max"] == 20000.0, f"turn_wall={tw}")
    expect(m["turn_wall_ms"]["terminal_states"] == {"COMPLETED": 2, "FAILED": 1}, f"terminal_states={m['turn_wall_ms']['terminal_states']}")
    dv = m["driver_turn_wall_s"]["all"]
    expect(dv["n"] == 3 and dv["p50"] == 12.0 and dv["max"] == 22.0, f"driver={dv}")
    pl = m["provider_latency_ms"]["db_succeeded"]
    expect(pl["n"] == 4 and pl["p50"] == 2000.0 and pl["p95"] == 4000.0, f"provider_latency={pl}")
    nl = m["provider_latency_ms"]["native_log_elapsed"]
    expect(nl["n"] == 4 and nl["max"] == 4000.0, f"native_log_elapsed(去重后应为 4)={nl}")
    expect(m["provider_latency_ms"]["timeout_seconds_seen"] == [240.0], "timeout_seconds")
    cpt = m["provider_calls_per_turn"]
    expect(cpt["n"] == 3 and cpt["max"] == 2.0 and cpt["p50"] == 1.0, f"calls_per_turn={cpt}")
    tk = m["tokens"]
    expect(tk["peak_input_tokens_single_call"] == 4000 and tk["input_tokens_total"] == 10000 and tk["output_tokens_total"] == 1000, f"tokens={tk}")
    expect(tk["input_per_turn"]["max"] == 4000 and tk["input_per_turn"]["p50"] == 3000, f"input_per_turn={tk['input_per_turn']}")
    ra = m["host_estimate_vs_provider"]["ratio_messages_only"]
    expect(ra["n"] == 4 and 1.0 <= ra["p50"] <= 1.1, f"ratio={ra}")
    fg = m["typed_recall"]["by_caller"]["foreground_recall"]
    expect(fg["latency_ms"]["n"] == 3 and fg["latency_ms"]["p95"] == 2500.0 and fg["latency_ms"]["p50"] == 400.0, f"fg={fg}")
    expect(fg["states"] == {"returned": 2, "raised": 1}, f"fg states={fg['states']}")
    expect(m["typed_recall"]["sdk_timeout_rate"] == 0.25, f"sdk_timeout_rate={m['typed_recall']['sdk_timeout_rate']}")
    expect(m["typed_recall"]["request_deadline_ms_seen"] == [1000], "deadline_ms")
    al = m["analysis_lane"]
    expect(al["invocations_total"] == 2 and al["invocation_failure_rate"] == 0.5, f"analysis inv={al}")
    expect(al["latency_ms_accepted"]["max"] == 8000.0 and al["input_tokens_accepted"]["max"] == 4000, "analysis latency/tokens")
    expect(al["batch_failure_rate"] == 0.5 and al["batch_failure_reasons"] == {"analysis_delivery_authority_rejected": 1}, f"batches={al}")
    ca = m["context_assembly"]
    expect(ca["receipts"] == 4 and ca["trimmed_groups_total"] == 3 and ca["receipts_with_trim"] == 2 and ca["current_tool_pages_total"] == 1, f"context={ca}")
    expect(ca["causal_groups"]["max"] == 4.0 and ca["context_page_in_effects"] == 1, f"context2={ca}")
    expect(ca["log_context_events"] == {"context.preparing": 2, "context.staged": 2, "context.consumed": 2}, f"log ctx={ca['log_context_events']}")
    rf = m["run_failures"]
    expect(rf["failed_runs"] == 1 and rf["by_reason"] == {"react_max_turns_exceeded": 1} and rf["failure_rate"] == round(1 / 3, 4), f"run_failures={rf}")
    expect(rf["driver_runtime_failed_by_detail"] == {"primary_history_transcript_mismatch": 1}, "runtime.failed")
    expect(m["budget"].get("window_tokens") == 32000 and m["budget"].get("budget_tier") == 8192 and m["budget"].get("effective_input_budget") == 26752, f"budget={m['budget']}")
    cc = m["corpus_cases"]
    expect(cc["cases"] == 2 and cc["execution_failed_rate"] == 0.5 and cc["peak_rss_mib_max"] == 1024, f"corpus={cc}")
    cr = report["corpus_review"]
    expect(cr and cr["required_recall_rate"] == 0.5 and cr["extra_type_rate"] == 0.25 and cr["no_recall_accuracy"] == 1.0 and cr["privacy_accuracy"] == 1.0 and cr["hard_trigger_accuracy"] is None, f"review={cr}")
    verdict = {v["key"]: v["verdict"] for v in report["verdicts"]}
    expect(verdict["recall_p95"] == "FAIL", f"recall_p95 verdict={verdict.get('recall_p95')}")
    expect(verdict["recall_hard_deadline"] == "FAIL", "recall_hard_deadline verdict")
    expect(verdict["provider_timeout"] == "PASS", "provider_timeout verdict")
    expect(verdict["input_budget_provider"] == "PASS", "input_budget_provider verdict")
    expect(verdict["analysis_deadline"] == "PASS" and verdict["analysis_input_budget"] == "PASS", "analysis verdicts")
    expect(verdict["context_triple"] == "PASS", "context_triple verdict")
    expect(verdict["turn_wall"] == "N-A", "turn_wall N-A")
    expect(verdict["corpus_required_recall"] == "FAIL" and verdict["corpus_privacy"] == "PASS" and verdict["corpus_hard_trigger"] == "N-A", "corpus verdicts")
    expect(not m["warnings"], f"warnings={m['warnings']}")
    return failures


def run_selftest(keep_dir: str | None = None) -> int:
    tmp = keep_dir or tempfile.mkdtemp(prefix="hm-benchmark-selftest-")
    try:
        ev = build_synthetic_fixture(tmp)
        report = build_report([ev], [os.path.join(ev, "review-verdicts.json")])
        failures = selftest_expectations(report)
        md = render_markdown(report)
        if "契约阈值判定" not in md or "typed recall" not in md:
            failures.append("markdown 渲染缺少关键章节")
        if failures:
            print("SELFTEST FAIL")
            for f in failures:
                print(f"  - {f}")
            return 1
        print(f"SELFTEST OK ({len(report['verdicts'])} verdicts, markdown {len(md)} chars)")
        return 0
    finally:
        if keep_dir is None:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--evidence", action="append", default=[], help="证据目录（可重复）")
    parser.add_argument("--label", action="append", default=[], help="name=dir 为证据目录指定显示名（可重复）")
    parser.add_argument("--review-verdicts", action="append", default=[], help="语料复审判定 JSON（可重复）")
    parser.add_argument("--out", help="输出 JSON 路径")
    parser.add_argument("--md", action="store_true", help="同时输出中文 Markdown（默认与 --out 同名 .md）")
    parser.add_argument("--md-out", help="Markdown 输出路径")
    parser.add_argument("--selftest", action="store_true", help="用合成 sqlite 夹具自检")
    args = parser.parse_args(argv)

    if args.selftest:
        return run_selftest()
    if not args.evidence:
        parser.error("至少一个 --evidence（或 --selftest）")
    labels: dict[str, str] = {}
    for item in args.label:
        if "=" not in item:
            parser.error(f"--label 需要 name=dir：{item}")
        name, path = item.split("=", 1)
        labels[path] = name
    for d in args.evidence:
        if not os.path.isdir(d):
            parser.error(f"证据目录不存在：{d}")
    report = build_report(args.evidence, args.review_verdicts, labels=labels)
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
    if args.md or args.md_out:
        md_path = args.md_out or (os.path.splitext(args.out)[0] + ".md" if args.out else None)
        md = render_markdown(report)
        if md_path:
            with open(md_path, "w", encoding="utf-8") as fh:
                fh.write(md + "\n")
        else:
            print(md)
    if not args.out and not (args.md or args.md_out):
        print(payload)
    summary = ", ".join(f"{v['key']}={v['verdict']}" for v in report["verdicts"] if v["verdict"] != "N-A")
    print(f"hm_benchmark: {len(report['per_evidence'])} 个证据目录，{report['pooled']['runtime_roots']} 个运行时根；判定：{summary}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
