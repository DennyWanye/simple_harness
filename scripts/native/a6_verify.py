#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""HM-TO-A6 长对话 ContextSnapshot 审计后置校验器。

依据 plans/2026-09-08-hm-to-a6/00-PLAN.md 第 1 / 3 / 4 节, 对一次 A6 原生真实模型跑
产出的证据目录做只读审计, 逐项给出 A6-1..A6-12 与 6 条负控的判定。

用法:
    python scripts/native/a6_verify.py \
        --evidence <E> --installed-target <dir> [--out <E>/a6-verify.json] [--json-only]
    python scripts/native/a6_verify.py --selftest

<E> 需包含:
    userdata/data/human_memory_v7.db
    userdata/data/state.db
    userdata/data/operation-audit.db
    userdata/data/simple-harness-sdk/execution-v6.sqlite3
    native.log
    a6-progress.jsonl

设计原则(与 plan 一致):
  * 只读打开 sqlite (file:...?mode=ro), 不长事务, 允许对活动库取瞬时视图。
  * 所有列名都来自 pragma table_info; 表/列缺失 -> 该项记 INCONCLUSIVE, 不崩溃。
  * `sdk_context_public_snapshots` 不作为任何 PASS 依据(plan 第 0 节)。
  * 未观测到裁剪 -> A6-3/(b) 记 INCONCLUSIVE 而非 PASS。
  * 任一轮 timeout -> 相关项降级 INCONCLUSIVE。
  * 已知缺陷 F06(传输超时) / 模型拒调工具 -> BLOCKED, 不记 FAIL。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import hashlib
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Sequence

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
INCONCLUSIVE = "INCONCLUSIVE"

TERMINAL_RUN_STATES = {"COMPLETED", "FAILED", "STOPPED", "CANCELLED"}

# --- 冻结常量, 抄自 backend/deskpet/sdk_adapters/context_partitions.py (只读引用) ---
GENERATION_RESERVE: dict[int, int] = {4096: 1024, 8192: 2048, 32768: 4096}
SUPPORTED_WINDOWS: tuple[int, ...] = (4096, 8192, 32768)
PARTITION_CAPS_RECENT: dict[int, dict[str, int]] = {
    4096: {"groups_max": 10, "items_max": 64, "bytes_max": 131072},
    8192: {"groups_max": 10, "items_max": 80, "bytes_max": 196608},
    32768: {"groups_max": 10, "items_max": 120, "bytes_max": 393216},
}
DEFAULT_WINDOW_ASSUMPTION = 32000  # gpt-5.6-luna, plan 第 0 节

# --- 夹具锚点 (scripts/native/a6_driver.sh) ---
ANCHOR_ALPHA = "QF-2026-0908-ALPHA-7731"
ANCHOR_BETA = "QF-2026-0908-BETA-4419"

# --- 同 Run 有界化标记 (Incident E / followup F-E2) ---
# backend/deskpet/execution/current_tool_pages.py::CONTROL_MARKER 的逐字副本:
# 被省略的 CONTROL 工具结果以 role=tool + metadata.source == 该值的省略回执出场。
CONTROL_ELISION_MARKER = "primary_control_result_elided_v1"
# DECISION-F-E2-CONTROL-RESULT-BOUND.md §5.4 冻结的回执字段(全部 int, 可缺失)。
RECEIPT_BOUND_FIELDS = (
    "current_tool_pages",
    "pages_forced",
    "groups_trimmed_for_budget",
    "budget_headroom",
    "control_results_stubbed",
    "control_result_tokens",
    "control_stubs_forced",
)
RECEIPT_CONTROL_FIELDS = (
    "control_results_stubbed",
    "control_result_tokens",
    "control_stubs_forced",
)
# context_authority.py:1101 的 warning 形状; 同名的 kernel error 行没有 planned=,
# 用 planned= 把两者分开, 避免同一次溢出被数两遍。
BUDGET_EXCEEDED_RE = re.compile(
    r"sdk_context_budget_exceeded planned=(\d+) effective=(\d+)"
)
SDK_RUN_ID_RE = re.compile(r'"sdk_run_id":\s*"([^"]+)"|sdk_run_id=([^\s",\]]+)')
# 溢出行本身不带 run id, 向后找最近一条带 sdk_run_id 的记录做归属。
BUDGET_EXCEEDED_LOOKAHEAD = 40

HISTORY_PAGE_PREFIX = "primary-tool-page:v1:"
EFFECT_PAGE_PREFIX = "primary-effect-page:v1:"
HISTORY_SUMMARY_KIND = "primary_tool_result_summary_v1"
HISTORY_MESSAGE_SOURCE = "primary_tool_history_v1"
README_BOUNDED_TAIL = "[bounded; details are content-addressed in EVIDENCE]"

# plan 第 4 节负控 6: protocol.py::_CREDENTIAL_PATTERNS 形状
CREDENTIAL_PATTERNS = (
    ("bearer", re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}")),
    ("sk-", re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")),
    ("ghp_", re.compile(r"\bghp_[A-Za-z0-9]{16,}")),
    ("AKIA", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
)

GRAPH_STRUCTURAL_NEEDLES = ("twin_graph", "graph_edge", "relation_memory_id")

MANUAL_UI_TURNS = (16, 24)
EXPECTED_TURNS = 24


# ---------------------------------------------------------------- 基础工具


def sha256_file(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def text_tokens(value: object) -> int:
    """与 context_partitions.text_tokens 逐字一致的 CJK 感知估算。"""
    text = str(value or "")
    cjk = sum(1 for char in text if "㐀" <= char <= "鿿")
    return cjk + max(0, len(text) - cjk + 3) // 4


def _canonical_json_fallback(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


CANONICAL_JSON: Callable[[object], str] = _canonical_json_fallback


def budget_tier(window_tokens: int) -> int:
    chosen = SUPPORTED_WINDOWS[0]
    for tier in SUPPORTED_WINDOWS:
        if window_tokens >= tier:
            chosen = tier
    return chosen


def safety_margin(window_tokens: int) -> int:
    return max(256, window_tokens // 10)


def effective_input_budget(window_tokens: int) -> int:
    tier = budget_tier(window_tokens)
    return window_tokens - GENERATION_RESERVE[tier] - safety_margin(window_tokens)


def json_objects(text: str, start: int = 0) -> Iterator[Any]:
    """从 text 中依次 raw_decode 出多个顶层 JSON 对象(容忍分隔空白/换行)。"""
    decoder = json.JSONDecoder()
    i = start
    n = len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i >= n:
            return
        try:
            obj, i = decoder.raw_decode(text, i)
        except ValueError:
            return
        yield obj


def maybe_json(value: object) -> Any:
    if isinstance(value, (bytes, bytearray)):
        try:
            value = bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        return None


def as_text(value: object) -> str:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return "" if value is None else str(value)


def parse_progress_ts(raw: str) -> float | None:
    # driver 写的是 %Y-%m-%dT%H:%M:%S%z, 例如 2026-09-08T10:48:27+0800
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return _dt.datetime.strptime(raw, fmt).timestamp()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- sqlite 只读


class SchemaMissing(Exception):
    """plan 里点名的表/列在实际证据库中不存在。"""


class RoDb:
    """只读 sqlite 句柄; 每次查询即取即走, 不持有长事务。"""

    def __init__(self, path: str, label: str) -> None:
        self.path = path
        self.label = label
        self.available = os.path.isfile(path)
        self.error: str | None = None
        self._conn: sqlite3.Connection | None = None
        if not self.available:
            self.error = f"{label} 缺失: {path}"
            return
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("pragma busy_timeout=3000")
            conn.execute("pragma query_only=ON")
            self._conn = conn
        except sqlite3.Error as exc:  # pragma: no cover - 环境性失败
            self.available = False
            self.error = f"{label} 打开失败: {exc}"

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(sqlite3.Error):
                self._conn.close()
            self._conn = None

    # -- schema --
    def tables(self) -> set[str]:
        if self._conn is None:
            return set()
        try:
            rows = self._conn.execute(
                "select name from sqlite_master where type in ('table','view')"
            ).fetchall()
        except sqlite3.Error:
            return set()
        return {r[0] for r in rows}

    def columns(self, table: str) -> list[str]:
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(f"pragma table_info({table})").fetchall()
        except sqlite3.Error:
            return []
        return [r[1] for r in rows]

    def require(self, table: str, *cols: str) -> list[str]:
        """确认表存在且含全部列(列名一律来自 pragma table_info)。"""
        if not self.available:
            raise SchemaMissing(self.error or f"{self.label} 不可读")
        have = self.columns(table)
        if not have:
            raise SchemaMissing(f"{self.label} 缺表 {table}")
        missing = [c for c in cols if c not in have]
        if missing:
            raise SchemaMissing(f"{self.label}.{table} 缺列 {','.join(missing)}")
        return have

    def has(self, table: str, *cols: str) -> bool:
        try:
            self.require(table, *cols)
        except SchemaMissing:
            return False
        return True

    # -- query --
    def rows(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        if self._conn is None:
            raise SchemaMissing(self.error or f"{self.label} 不可读")
        try:
            return self._conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise SchemaMissing(f"{self.label} 查询失败: {exc}") from exc

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        rows = self.rows(sql, params)
        if not rows:
            return default
        value = rows[0][0]
        return default if value is None else value

    def count(self, table: str, where: str = "", params: Sequence[Any] = ()) -> int:
        clause = f" where {where}" if where else ""
        return int(self.scalar(f"select count(*) from {table}{clause}", params, 0) or 0)


# ---------------------------------------------------------------- 判定容器


@dataclass
class Item:
    key: str
    title: str
    verdict: str = INCONCLUSIVE
    reason: str = ""
    numbers: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "item": self.key,
            "title": self.title,
            "verdict": self.verdict,
            "reason": self.reason,
            "numbers": self.numbers,
        }


# ---------------------------------------------------------------- 证据载入


@dataclass
class Invocation:
    invocation_id: str
    run_id: str
    request_id: str
    request_fingerprint: str
    request_json_text: str
    request: dict[str, Any] | None
    state: str
    claimed_at: float
    response_text: str
    usage: dict[str, Any]


@dataclass
class HistoryGroup:
    source_ref: str
    source_hash: str
    messages: list[dict[str, Any]]


class Evidence:
    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)
        data = os.path.join(self.root, "userdata", "data")
        self.paths = {
            "human_memory_v7.db": os.path.join(data, "human_memory_v7.db"),
            "state.db": os.path.join(data, "state.db"),
            "operation-audit.db": os.path.join(data, "operation-audit.db"),
            "execution-v6.sqlite3": os.path.join(
                data, "simple-harness-sdk", "execution-v6.sqlite3"
            ),
            "native.log": os.path.join(self.root, "native.log"),
            "a6-progress.jsonl": os.path.join(self.root, "a6-progress.jsonl"),
        }
        self.hm = RoDb(self.paths["human_memory_v7.db"], "human_memory_v7.db")
        self.state = RoDb(self.paths["state.db"], "state.db")
        self.audit = RoDb(self.paths["operation-audit.db"], "operation-audit.db")
        self.exec = RoDb(self.paths["execution-v6.sqlite3"], "execution-v6.sqlite3")
        self.native_log = self._read_text(self.paths["native.log"])
        self.progress = self._read_progress(self.paths["a6-progress.jsonl"])
        self._invocations: list[Invocation] | None = None
        self.notes: list[str] = []

    def close(self) -> None:
        for db in (self.hm, self.state, self.audit, self.exec):
            db.close()

    @staticmethod
    def _read_text(path: str) -> str:
        if not os.path.isfile(path):
            return ""
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    @staticmethod
    def _read_progress(path: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not os.path.isfile(path):
            return rows
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    obj["_ts_epoch"] = parse_progress_ts(str(obj.get("ts", "")))
                    rows.append(obj)
        return rows

    def hashes(self) -> dict[str, str | None]:
        return {name: sha256_file(path) for name, path in self.paths.items()}

    # ---- provider invocations ----
    def invocations(self) -> list[Invocation]:
        if self._invocations is not None:
            return self._invocations
        self.exec.require(
            "provider_invocations",
            "invocation_id",
            "run_id",
            "request_id",
            "request_fingerprint",
            "request_json",
            "state",
            "claimed_at",
        )
        cols = self.exec.columns("provider_invocations")
        resp = "response_json" if "response_json" in cols else "NULL"
        usage = "usage_json" if "usage_json" in cols else "NULL"
        rows = self.exec.rows(
            "select invocation_id, run_id, request_id, request_fingerprint, request_json,"
            f" state, claimed_at, {resp} as response_json, {usage} as usage_json"
            " from provider_invocations order by claimed_at asc, invocation_id asc"
        )
        out: list[Invocation] = []
        for r in rows:
            raw = as_text(r["request_json"])
            parsed = maybe_json(raw)
            out.append(
                Invocation(
                    invocation_id=as_text(r["invocation_id"]),
                    run_id=as_text(r["run_id"]),
                    request_id=as_text(r["request_id"]),
                    request_fingerprint=as_text(r["request_fingerprint"]),
                    request_json_text=raw,
                    request=parsed if isinstance(parsed, dict) else None,
                    state=as_text(r["state"]),
                    claimed_at=float(r["claimed_at"] or 0.0),
                    response_text=as_text(r["response_json"]),
                    usage=maybe_json(as_text(r["usage_json"])) or {},
                )
            )
        self._invocations = out
        return out

    # ---- 轮次 -> run/invocation 归属 ----
    def turn_windows(self) -> list[dict[str, Any]]:
        """按 a6-progress.jsonl 的相邻 ts 划分时间窗, 归属 invocation/run。"""
        rows = [r for r in self.progress if isinstance(r.get("turn"), int)]
        rows.sort(key=lambda r: (r.get("_ts_epoch") or 0.0, r.get("turn", 0)))
        windows: list[dict[str, Any]] = []
        prev_ts = 0.0
        prev_counts: dict[str, int] = {}
        for row in rows:
            ts = row.get("_ts_epoch") or prev_ts
            windows.append(
                {
                    "turn": int(row["turn"]),
                    "outcome": str(row.get("outcome", "")),
                    "start": prev_ts,
                    "end": ts,
                    "row": row,
                    "prev_row_counts": dict(prev_counts),
                }
            )
            prev_ts = ts
            prev_counts = {k: v for k, v in row.items() if isinstance(v, int)}
        invs = self.invocations() if self.exec.available else []
        for w in windows:
            w["invocations"] = [
                i for i in invs if w["start"] < i.claimed_at <= w["end"]
            ]
            w["run_ids"] = sorted({i.run_id for i in w["invocations"]})
        return windows

    def window_for_turn(self, turn: int) -> dict[str, Any] | None:
        for w in self.turn_windows():
            if w["turn"] == turn:
                return w
        return None

    def progress_row(self, turn: int) -> dict[str, Any] | None:
        for row in self.progress:
            if row.get("turn") == turn:
                return row
        return None

    def timeout_turns(self) -> list[int]:
        return [
            int(r["turn"])
            for r in self.progress
            if r.get("outcome") == "timeout" and isinstance(r.get("turn"), int)
        ]

    def max_turn_recorded(self) -> int:
        turns = [int(r["turn"]) for r in self.progress if isinstance(r.get("turn"), int)]
        return max(turns) if turns else 0

    # ---- 窗口 / 预算 ----
    def resolved_windows(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in re.finditer(
            r"model_context_resolved model=([^\s\"]+) window=(\d+)", self.native_log
        ):
            out[m.group(1)] = int(m.group(2))
        return out

    def models_used(self) -> list[str]:
        if not self.state.has("sdk_provider_attempt_audit", "model_id"):
            return []
        try:
            rows = self.state.rows(
                "select distinct model_id from sdk_provider_attempt_audit"
            )
        except SchemaMissing:
            return []
        return [as_text(r[0]) for r in rows if r[0]]

    def budget_profile(self) -> dict[str, Any]:
        """优先用证据里记录的真实 window; 否则声明 plan 的 luna 32000 假设。"""
        source = "assumption"
        window = DEFAULT_WINDOW_ASSUMPTION
        models = self.models_used()
        resolved = self.resolved_windows()
        # 1) sdk_provider_attempt_audit.context_window (>0 才算记录到)
        db_window = 0
        if self.state.has("sdk_provider_attempt_audit", "context_window"):
            with contextlib.suppress(SchemaMissing):
                db_window = int(
                    self.state.scalar(
                        "select coalesce(max(context_window),0) from sdk_provider_attempt_audit",
                        default=0,
                    )
                    or 0
                )
        if db_window > 0:
            window, source = db_window, "state.db:sdk_provider_attempt_audit.context_window"
        else:
            # 2) native.log 的 model_context_resolved
            hit = None
            for m in models:
                if m in resolved:
                    hit = (m, resolved[m])
                    break
            if hit is None and len(resolved) == 1:
                hit = next(iter(resolved.items()))
            if hit is not None:
                window, source = hit[1], f"native.log:model_context_resolved({hit[0]})"
        # 3) receipts 里记录的 budget_tier(交叉验证)
        recorded_tiers = sorted(self._receipt_budget_tiers())
        tier = budget_tier(window)
        return {
            "window_tokens": window,
            "window_source": source,
            "window_assumed": source == "assumption",
            "models_used": models,
            "native_log_windows": resolved,
            "budget_tier": tier,
            "budget_tier_recorded": recorded_tiers,
            "generation_reserve": GENERATION_RESERVE[tier],
            "safety_margin": safety_margin(window),
            "effective_input_budget": effective_input_budget(window),
            "recent_causal_group_caps": PARTITION_CAPS_RECENT[tier],
        }

    def _receipt_budget_tiers(self) -> set[int]:
        tiers: set[int] = set()
        if not self.state.has("run_context_snapshot_receipts", "source_revisions_json"):
            return tiers
        with contextlib.suppress(SchemaMissing):
            for r in self.state.rows(
                "select source_revisions_json from run_context_snapshot_receipts"
            ):
                obj = maybe_json(as_text(r[0])) or {}
                rev = obj.get("source_revisions") if isinstance(obj, dict) else None
                if isinstance(rev, dict) and isinstance(rev.get("budget_tier"), int):
                    tiers.add(int(rev["budget_tier"]))
        return tiers

    def receipt_trim_stats(self) -> dict[str, int]:
        total = trimmed = groups_max = 0
        rows_seen = 0
        if not self.state.has("run_context_snapshot_receipts", "source_revisions_json"):
            return {"receipts": 0, "trimmed_groups_total": 0, "max_causal_groups": 0}
        with contextlib.suppress(SchemaMissing):
            for r in self.state.rows(
                "select source_revisions_json from run_context_snapshot_receipts"
            ):
                rows_seen += 1
                obj = maybe_json(as_text(r[0])) or {}
                rev = obj.get("source_revisions") if isinstance(obj, dict) else None
                if not isinstance(rev, dict):
                    continue
                total += 1
                trimmed += int(rev.get("trimmed_groups") or 0)
                groups_max = max(groups_max, int(rev.get("causal_groups") or 0))
        return {
            "receipts": rows_seen,
            "receipts_with_source_revisions": total,
            "trimmed_groups_total": trimmed,
            "max_causal_groups": groups_max,
        }

    # -------- 同 Run 有界化取证 (Incident E followup F-E1 / F-E2) --------

    def receipt_bound_stats(self) -> dict[str, Any]:
        """按 Run 汇总 `source_revisions` 里的同 Run 有界化字段。

        字段契约见 `DECISION-F-E2-CONTROL-RESULT-BOUND.md` §5.4 与 Incident E §4.3。
        早于 F-E2 的 Host 构建不写 `control_*` 三个字段 —— 缺字段一律**不计**
        (不是记 0), 并用 `receipts_with_control_fields` 把「Host 还没有这个能力」
        与「有能力但本轮没触发」区分开; `budget_headroom` 无样本时记 None。
        """
        per_run: dict[str, dict[str, list[int]]] = {}
        rows_seen = with_rev = with_control = with_forced = 0
        if self.state.has("run_context_snapshot_receipts", "source_revisions_json"):
            with contextlib.suppress(SchemaMissing):
                for r in self.state.rows(
                    "select sdk_run_id, source_revisions_json"
                    " from run_context_snapshot_receipts"
                ):
                    rows_seen += 1
                    obj = maybe_json(as_text(r[1])) or {}
                    rev = obj.get("source_revisions") if isinstance(obj, dict) else None
                    if not isinstance(rev, dict):
                        continue
                    with_rev += 1
                    if any(k in rev for k in RECEIPT_CONTROL_FIELDS):
                        with_control += 1
                    if "pages_forced" in rev:
                        with_forced += 1
                    acc = per_run.setdefault(
                        as_text(r[0]), {k: [] for k in RECEIPT_BOUND_FIELDS}
                    )
                    for key in RECEIPT_BOUND_FIELDS:
                        value = rev.get(key)
                        if isinstance(value, bool) or not isinstance(value, int):
                            continue
                        acc[key].append(value)

        def _samples(key: str) -> list[list[int]]:
            return [acc[key] for acc in per_run.values() if acc[key]]

        def _max(key: str) -> int:
            return max((max(s) for s in _samples(key)), default=0)

        def _sum(key: str) -> int:
            return sum(sum(s) for s in _samples(key))

        def _runs_hitting(key: str) -> int:
            return sum(1 for s in _samples(key) if max(s) > 0)

        headroom = _samples("budget_headroom")
        return {
            "receipt_runs": len(per_run),
            "receipts_with_source_revisions": with_rev,
            "receipts_with_pages_forced_field": with_forced,
            "receipts_with_control_fields": with_control,
            "current_tool_pages_max_per_run": _max("current_tool_pages"),
            "pages_forced_max_per_run": _max("pages_forced"),
            "pages_forced_sum": _sum("pages_forced"),
            "runs_with_pages_forced": _runs_hitting("pages_forced"),
            "groups_trimmed_for_budget_max_per_run": _max("groups_trimmed_for_budget"),
            "groups_trimmed_for_budget_sum": _sum("groups_trimmed_for_budget"),
            "budget_headroom_min": min((min(s) for s in headroom), default=None),
            "control_results_stubbed_sum": _sum("control_results_stubbed"),
            "control_result_tokens_max": _max("control_result_tokens"),
            "control_stubs_forced_sum": _sum("control_stubs_forced"),
            "runs_with_control_stubs": _runs_hitting("control_results_stubbed"),
        }

    def control_elision_stats(self) -> dict[str, int]:
        """真实发出的请求里有多少条 CONTROL 省略回执(按 metadata.source 判定)。"""
        requests = notices = 0
        runs: set[str] = set()
        for inv in self.invocations():
            if CONTROL_ELISION_MARKER not in inv.request_json_text:
                continue
            hit = 0
            for msg in (inv.request or {}).get("messages") or []:
                if not isinstance(msg, dict) or msg.get("role") != "tool":
                    continue
                meta = msg.get("metadata")
                if isinstance(meta, dict) and meta.get("source") == CONTROL_ELISION_MARKER:
                    hit += 1
            if hit:
                requests += 1
                notices += hit
                runs.add(inv.run_id)
        return {
            "requests_with_control_elision_notice": requests,
            "control_elision_notices_in_requests": notices,
            "runs_with_control_elision_notice": len(runs),
        }

    def budget_exceeded_events(self) -> dict[str, Any]:
        """native.log 里的 `sdk_context_budget_exceeded`, 并归属到前台/其他 Run。

        该行不带 run id, 因此向后最多 40 行找最近一条带 `sdk_run_id` 的记录
        (事故当场是 `run.fail` 之后的 `primary_terminal_observation_degraded` /
        `foreground.runtime.closure_settled`)。归属不到时**不**算作其他泳道:
        A6-3 的判据把「归属不到」与「归属到前台」一起记 FAIL(fail closed),
        两个计数分开给出, 读者能自己看到证据强度。
        """
        foreground: set[str] = set()
        if self.state.has("foreground_run_heads", "sdk_run_id"):
            with contextlib.suppress(SchemaMissing):
                for r in self.state.rows("select sdk_run_id from foreground_run_heads"):
                    sdk_run_id = as_text(r[0])
                    if sdk_run_id:
                        foreground.add(sdk_run_id)
        lines = self.native_log.splitlines()
        total = fg = other = unknown = 0
        samples: list[dict[str, Any]] = []
        for index, line in enumerate(lines):
            m = BUDGET_EXCEEDED_RE.search(line)
            if m is None:
                continue
            total += 1
            run_id: str | None = None
            for nxt in lines[index : index + BUDGET_EXCEEDED_LOOKAHEAD]:
                hit = SDK_RUN_ID_RE.search(nxt)
                if hit:
                    run_id = hit.group(1) or hit.group(2)
                    break
            if run_id is not None and run_id in foreground:
                fg += 1
                lane = "foreground"
            elif run_id is not None and foreground:
                other += 1
                lane = "other_lane"
            else:
                unknown += 1
                lane = "unattributed"
            if len(samples) < 3:
                samples.append(
                    {
                        "planned": int(m.group(1)),
                        "effective": int(m.group(2)),
                        "sdk_run_id": (run_id or "")[-8:],
                        "lane": lane,
                    }
                )
        return {
            "sdk_context_budget_exceeded_total": total,
            "sdk_context_budget_exceeded_foreground": fg,
            "sdk_context_budget_exceeded_other_lane": other,
            "sdk_context_budget_exceeded_unattributed": unknown,
            "sdk_context_budget_exceeded_samples": samples,
        }

    def same_run_bound_numbers(self) -> dict[str, Any]:
        """A6-2 / A6-3 共用的同 Run 有界化取证块(附加在既有 numbers 之后)。"""
        out: dict[str, Any] = dict(self.receipt_bound_stats())
        out.update(self.control_elision_stats())
        out.update(self.budget_exceeded_events())
        return out


def bound_note(bound: dict[str, Any]) -> str:
    """A6-3 说明里固定追加的一句: 这一轮到底强推了多少页 / 省略了多少控制结果。"""
    headroom = bound.get("budget_headroom_min")
    parts = [
        f"同 Run 有界化: pages_forced 合计 {bound.get('pages_forced_sum', 0)}"
        f"(单 Run 峰值 {bound.get('pages_forced_max_per_run', 0)})",
        f"control_results_stubbed 合计 {bound.get('control_results_stubbed_sum', 0)}",
        f"control_stubs_forced 合计 {bound.get('control_stubs_forced_sum', 0)}",
        f"control_result_tokens 峰值 {bound.get('control_result_tokens_max', 0)}",
        f"budget_headroom 最小 {headroom if headroom is not None else '无记录'}",
        f"请求内省略通知 {bound.get('requests_with_control_elision_notice', 0)} 次",
    ]
    tail = ""
    if not bound.get("receipts_with_control_fields"):
        tail = "(该 Host 构建早于 F-E2, 回执无 control_* 字段, 三个 control 计数按缺失记 0)"
    return "; ".join(parts) + "。" + tail


# ---------------------------------------------------------------- 请求解析


def history_groups(request: dict[str, Any]) -> list[HistoryGroup]:
    """从 request_json 顶层 user 消息里解出 historical_causal_group。"""
    out: list[HistoryGroup] = []
    for msg in request.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, str) or "historical_causal_group" not in content:
            continue
        start = content.find("{")
        if start < 0:
            continue
        for obj in json_objects(content, start):
            if not isinstance(obj, dict) or obj.get("kind") != "historical_causal_group":
                continue
            msgs = obj.get("messages")
            out.append(
                HistoryGroup(
                    source_ref=str(obj.get("source_ref") or ""),
                    source_hash=str(obj.get("source_hash") or ""),
                    messages=[m for m in (msgs or []) if isinstance(m, dict)],
                )
            )
    return out


def history_summaries(request: dict[str, Any]) -> list[dict[str, Any]]:
    """历史组内被 primary_tool_result_summary_v1 摘要的 tool 消息。"""
    out: list[dict[str, Any]] = []
    for group in history_groups(request):
        for ordinal, msg in enumerate(group.messages):
            content = msg.get("content")
            if not isinstance(content, str) or HISTORY_SUMMARY_KIND not in content:
                continue
            obj = maybe_json(content)
            if isinstance(obj, dict) and obj.get("kind") == HISTORY_SUMMARY_KIND:
                obj = dict(obj)
                obj["_group_source_ref"] = group.source_ref
                obj["_group_ordinal"] = ordinal
                out.append(obj)
    return out


def top_level_roles(request: dict[str, Any]) -> list[str]:
    return [
        str(m.get("role"))
        for m in (request.get("messages") or [])
        if isinstance(m, dict)
    ]


def history_message_sources(request: dict[str, Any]) -> int:
    n = 0
    for msg in request.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        meta = msg.get("metadata")
        if isinstance(meta, dict) and meta.get("source") == HISTORY_MESSAGE_SOURCE:
            n += 1
    return n


# ---------------------------------------------------------------- A6-1 .. A6-12


def item_a6_1(ev: Evidence) -> Item:
    it = Item("A6-1", "20+ turn 动态组装")
    ev.state.require("foreground_run_heads", "host_run_id", "current_state", "sdk_run_id")
    ev.state.require(
        "run_context_snapshot_receipts", "sdk_run_id", "provider_turn_ordinal"
    )
    runs = ev.state.rows(
        "select host_run_id, current_state, sdk_run_id, updated_at from foreground_run_heads"
    )
    total = len(runs)
    non_terminal = [
        as_text(r["host_run_id"]) for r in runs
        if as_text(r["current_state"]).upper() not in TERMINAL_RUN_STATES
    ]
    ordinals: dict[str, list[int]] = {}
    for r in ev.state.rows(
        "select sdk_run_id, provider_turn_ordinal from run_context_snapshot_receipts"
        " order by sdk_run_id, provider_turn_ordinal"
    ):
        ordinals.setdefault(as_text(r["sdk_run_id"]), []).append(int(r["provider_turn_ordinal"]))
    gapped = {
        run: seq for run, seq in ordinals.items() if seq != list(range(1, len(seq) + 1))
    }
    timeouts = ev.timeout_turns()
    max_turn = ev.max_turn_recorded()
    it.numbers = {
        "foreground_run_heads": total,
        "non_terminal_runs": len(non_terminal),
        "non_terminal_sample": non_terminal[:5],
        "receipt_runs": len(ordinals),
        "runs_with_ordinal_gap": len(gapped),
        "gap_sample": {k: v for k, v in list(gapped.items())[:3]},
        "turns_recorded": max_turn,
        "timeout_turns": timeouts,
        "required_runs": EXPECTED_TURNS,
    }
    if timeouts:
        it.verdict = INCONCLUSIVE
        it.reason = f"第 {timeouts} 轮记为 timeout(plan 第 4 节: 该轮断言降级), 轮次完备性不可判。"
        return it
    if max_turn < EXPECTED_TURNS:
        it.verdict = INCONCLUSIVE
        it.reason = f"a6-progress.jsonl 只记录到 T{max_turn}/{EXPECTED_TURNS}, 脚本未跑完, 不判 PASS/FAIL。"
        return it
    if gapped:
        it.verdict = FAIL
        it.reason = f"{len(gapped)} 个 Run 的 provider_turn_ordinal 不连续(有洞)。"
        return it
    if non_terminal:
        it.verdict = INCONCLUSIVE
        it.reason = f"{len(non_terminal)} 个 Run 非终态(疑似 F06 传输超时停摆), 按 plan 第 4 节记 BLOCKED 语义。"
        it.verdict = BLOCKED
        return it
    if total < EXPECTED_TURNS - len(MANUAL_UI_TURNS):
        it.verdict = INCONCLUSIVE
        it.reason = f"终态 Run 仅 {total} 个, 少于 22 个非 UI 轮, 无法证明 20+ turn 组装。"
        return it
    it.verdict = PASS
    it.reason = f"{total} 个 Run 全部终态, {len(ordinals)} 个 Run 的 receipt ordinal 从 1 连续无洞。"
    return it


def _page_in_effects(ev: Evidence) -> list[sqlite3.Row]:
    ev.exec.require("execution_effects", "tool_name", "state", "result_json", "run_id")
    return ev.exec.rows(
        "select effect_id, run_id, tool_name, state, result_json, arguments_json"
        " from execution_effects where tool_name='context_page_in'"
    )


def item_a6_2(ev: Evidence) -> Item:
    it = Item("A6-2", "大 tool result 分页")
    invs = ev.invocations()
    refs: set[str] = set()
    summaries = 0
    tagged_messages = 0
    for inv in invs:
        for m in re.finditer(re.escape(HISTORY_PAGE_PREFIX) + r"[0-9a-f]{64}:\d+", inv.request_json_text):
            refs.add(m.group(0))
        if inv.request:
            summaries += len(history_summaries(inv.request))
            tagged_messages += history_message_sources(inv.request)
    ref_ids = {r.rsplit(":", 1)[0] for r in refs}

    effects = _page_in_effects(ev)
    ok_effects = [r for r in effects if as_text(r["state"]).lower() in {"succeeded", "settled"}]
    ok_true = 0
    for r in ok_effects:
        obj = maybe_json(as_text(r["result_json"])) or {}
        value = obj.get("value") if isinstance(obj, dict) else None
        if obj.get("outcome") == "succeeded" and value is not None:
            ok_true += 1

    responses = "\n".join(inv.response_text for inv in invs)
    alpha_hit = ANCHOR_ALPHA in responses
    beta_hit = ANCHOR_BETA in responses

    # 大 tool result 是否真的产生过(>16 KiB 的 role=tool 消息)
    big_tool_msgs = 0
    for inv in invs:
        if not inv.request:
            continue
        for msg in inv.request.get("messages") or []:
            if isinstance(msg, dict) and msg.get("role") == "tool":
                if len(as_text(msg.get("content")).encode()) > 16384:
                    big_tool_msgs += 1

    it.numbers = {
        "distinct_page_reference_ids": len(ref_ids),
        "page_reference_samples": sorted(ref_ids)[:3],
        "history_summaries_in_requests": summaries,
        "messages_tagged_primary_tool_history_v1": tagged_messages,
        "context_page_in_effects": len(effects),
        "context_page_in_succeeded": ok_true,
        "anchor_alpha_in_response": alpha_hit,
        "anchor_beta_in_response": beta_hit,
        "tool_messages_over_16KiB": big_tool_msgs,
    }
    # 同 Run 有界化取证(F-E1 / F-E2)。判据不变, 只补数字; 追加在末尾, 因此
    # Markdown 表里 _numbers_brief 取的前 4 个键与旧报告逐字一致。
    it.numbers.update(ev.same_run_bound_numbers())
    if big_tool_msgs == 0 and len(effects) == 0:
        max_turn = ev.max_turn_recorded()
        if max_turn < 13:
            it.verdict = INCONCLUSIVE
            it.reason = (
                f"只跑到 T{max_turn}, 尚未走完 T6/T8(大文件读取)与 T11/T13(翻页), "
                "分页链路无从判定。"
            )
            return it
        it.verdict = BLOCKED
        it.reason = (
            "全程未出现 >16 KiB 的 tool 结果, 也无 context_page_in 调用 —— "
            "T5/T6/T8 未成功读文件, 按 plan 第 4 节记 BLOCKED(环境/路由)而非 FAIL。"
        )
        return it
    if len(ref_ids) >= 2 and ok_true >= 2 and alpha_hit and beta_hit:
        it.verdict = PASS
        it.reason = (
            f"{len(ref_ids)} 个不同 primary-tool-page:v1 reference_id 被摘要, "
            f"{ok_true} 次 context_page_in 成功, 两个夹具锚点均逐字出现在回复中。"
        )
        return it
    if len(ref_ids) == 0 and ok_true == 0:
        it.verdict = INCONCLUSIVE
        it.reason = "有大 tool result 但未观测到摘要/翻页, 无法判定分页链路(可能轮数不足)。"
        return it
    it.verdict = FAIL if (alpha_hit is False or beta_hit is False) and ok_true >= 2 else INCONCLUSIVE
    it.reason = (
        f"分页证据不完整: reference_id={len(ref_ids)}(需≥2), 成功翻页={ok_true}(需≥2), "
        f"ALPHA锚点={alpha_hit}, BETA锚点={beta_hit}。"
    )
    return it


def item_a6_3(ev: Evidence, budget: dict[str, Any]) -> Item:
    """A6-3 = `_item_a6_3_core` 的判定 + 固定追加的同 Run 有界化说明。"""
    bound = ev.same_run_bound_numbers()
    it = _item_a6_3_core(ev, budget, bound)
    it.reason = (it.reason.rstrip() + " " + bound_note(bound)).strip()
    return it


def _item_a6_3_core(ev: Evidence, budget: dict[str, Any], bound: dict[str, Any]) -> Item:
    it = Item("A6-3", "预算内有界")
    ev.state.require("sdk_provider_attempt_audit", "input_tokens", "settled_at")
    rows = ev.state.rows(
        "select input_tokens, settled_at from sdk_provider_attempt_audit"
        " where input_tokens is not null order by settled_at asc"
    )
    tokens = [int(r["input_tokens"]) for r in rows]
    trim = ev.receipt_trim_stats()
    budget_value = int(budget["effective_input_budget"])
    plan_ceiling = int(budget["window_tokens"]) - int(budget["generation_reserve"])

    est_max = 0
    est_over = 0
    for inv in ev.invocations():
        if not inv.request:
            continue
        est = text_tokens(CANONICAL_JSON(inv.request.get("messages") or []))
        est_max = max(est_max, est)
        if est > budget_value:
            est_over += 1

    first8 = tokens[:8]
    last8 = tokens[-8:] if len(tokens) >= 16 else []
    ratio = None
    if first8 and last8 and max(first8) > 0:
        ratio = round(max(last8) / max(first8), 3)

    it.numbers = {
        "attempt_rows": len(tokens),
        "max_input_tokens": max(tokens) if tokens else 0,
        "first8_max_input_tokens": max(first8) if first8 else 0,
        "last8_max_input_tokens": max(last8) if last8 else 0,
        "growth_ratio_last8_over_first8": ratio,
        "growth_ratio_limit": 1.6,
        "window_tokens": budget["window_tokens"],
        "window_source": budget["window_source"],
        "effective_input_budget": budget_value,
        "plan_ceiling_window_minus_reserve": plan_ceiling,
        "max_estimated_request_tokens": est_max,
        "requests_over_budget": est_over,
        "trimmed_groups_total": trim.get("trimmed_groups_total", 0),
        "max_causal_groups_recorded": trim.get("max_causal_groups", 0),
    }
    # 同 Run 有界化取证(F-E1 / F-E2), 追加在末尾: 既有键的顺序与取值不变。
    it.numbers.update(bound)
    # 新增判据: 只要前台 Run 真的把 context 预算撑爆过, 有界性就是伪的 ——
    # 这是**直接观测**, 不因样本不全(timeout / 未观测到整组丢弃)而降级,
    # 所以排在所有 INCONCLUSIVE 分支之前。归属不到 Run 的溢出一并记 FAIL。
    exceeded = int(bound.get("sdk_context_budget_exceeded_foreground") or 0) + int(
        bound.get("sdk_context_budget_exceeded_unattributed") or 0
    )
    if exceeded:
        it.verdict = FAIL
        it.reason = (
            f"native.log 记录了 {exceeded} 次前台 Run 的 sdk_context_budget_exceeded"
            f"(总计 {bound.get('sdk_context_budget_exceeded_total', 0)} 次, 其中归属不到 Run 的 "
            f"{bound.get('sdk_context_budget_exceeded_unattributed', 0)} 次也计入), "
            f"样本 {json.dumps(bound.get('sdk_context_budget_exceeded_samples') or [], ensure_ascii=False)} "
            "—— 请求装配越过 effective_input_budget 并抛 ContextBudgetExceeded, 有界性不成立。"
        )
        return it
    if ev.timeout_turns():
        it.verdict = INCONCLUSIVE
        it.reason = f"存在 timeout 轮 {ev.timeout_turns()}, 有界性样本不完整(plan 第 4 节)。"
        return it
    if not tokens:
        it.verdict = INCONCLUSIVE
        it.reason = "sdk_provider_attempt_audit 无 input_tokens 记录, 无法判定。"
        return it
    if est_over or (tokens and max(tokens) >= plan_ceiling):
        it.verdict = FAIL
        it.reason = (
            f"最大 input_tokens={max(tokens)} 或估算请求 token={est_max} 越过预算 "
            f"{budget_value}/{plan_ceiling}。"
        )
        return it
    if trim.get("trimmed_groups_total", 0) == 0:
        it.verdict = INCONCLUSIVE
        it.reason = (
            "全程 run_context_snapshot_receipts.source_revisions.trimmed_groups 恒为 0 —— "
            "从未发生整组丢弃, 按 plan 第 3(b) 节记 INCONCLUSIVE, 不记 PASS。"
        )
        return it
    if ratio is not None and ratio > 1.6:
        it.verdict = FAIL
        it.reason = f"后 8 轮 input_tokens 峰值是前 8 轮的 {ratio} 倍, 超过 1.6 倍上限。"
        return it
    if ratio is None:
        it.verdict = INCONCLUSIVE
        it.reason = (
            f"已观测到 {trim['trimmed_groups_total']} 次整组丢弃且 max_input_tokens="
            f"{max(tokens)} < {budget_value}, 但样本不足 16 次, 无法做前后 8 轮增长比对。"
        )
        return it
    it.verdict = PASS
    it.reason = (
        f"max_input_tokens={max(tokens)} < effective_input_budget={budget_value}; "
        f"后8/前8 峰值比 {ratio} ≤ 1.6; 观测到 {trim['trimmed_groups_total']} 次整组丢弃。"
    )
    return it


def item_a6_4(ev: Evidence, budget: dict[str, Any]) -> Item:
    it = Item("A6-4", "裁剪不破坏因果链")
    caps = budget["recent_causal_group_caps"]
    groups_max = int(caps["groups_max"])
    max_groups = 0
    over_group_requests = 0
    orphan_top_level = 0
    orphan_in_group = 0
    parsed = 0
    seq_by_run: dict[str, list[tuple[float, list[str]]]] = {}
    for inv in ev.invocations():
        if not inv.request:
            continue
        parsed += 1
        groups = history_groups(inv.request)
        max_groups = max(max_groups, len(groups))
        if len(groups) > groups_max:
            over_group_requests += 1
        seq_by_run.setdefault(inv.run_id, []).append(
            (inv.claimed_at, [g.source_ref for g in groups])
        )
        # 组内: tool 消息之前必须有同组的 assistant
        for g in groups:
            seen_assistant = False
            for m in g.messages:
                role = m.get("role")
                if role == "assistant":
                    seen_assistant = True
                elif role == "tool" and not seen_assistant:
                    orphan_in_group += 1
        # 顶层: 出现在任何 assistant 之前的裸 tool 消息才算孤立
        seen_assistant = False
        for role in top_level_roles(inv.request):
            if role == "assistant":
                seen_assistant = True
            elif role == "tool" and not seen_assistant:
                orphan_top_level += 1

    # 后缀单调: 相邻两次请求里, 存活的旧 source_ref 必须是上一次序列的连续后缀
    suffix_violations = 0
    trims_observed = 0
    ordered = sorted(
        (t for seqs in seq_by_run.values() for t in seqs), key=lambda x: x[0]
    )
    for (_, prev), (_, cur) in zip(ordered, ordered[1:]):
        if not prev:
            continue
        survivors = [r for r in prev if r in set(cur)]
        if len(survivors) < len(prev):
            trims_observed += 1
            if prev[len(prev) - len(survivors):] != survivors:
                suffix_violations += 1

    it.numbers = {
        "requests_parsed": parsed,
        "max_history_groups_in_one_request": max_groups,
        "groups_max_cap": groups_max,
        "requests_over_groups_max": over_group_requests,
        "orphan_tool_messages_top_level": orphan_top_level,
        "orphan_tool_messages_in_group": orphan_in_group,
        "trim_transitions_observed": trims_observed,
        "suffix_monotonicity_violations": suffix_violations,
    }
    if parsed == 0:
        it.verdict = INCONCLUSIVE
        it.reason = "无可解析的 request_json, 无法检查因果链。"
        return it
    if orphan_top_level or orphan_in_group or over_group_requests or suffix_violations:
        it.verdict = FAIL
        it.reason = (
            f"孤立 tool 消息 顶层={orphan_top_level}/组内={orphan_in_group}; "
            f"组数超 {groups_max} 的请求={over_group_requests}; 后缀单调违例={suffix_violations}。"
        )
        return it
    if trims_observed == 0:
        it.verdict = INCONCLUSIVE
        it.reason = (
            f"{parsed} 次请求均无孤立 tool 消息且组数 ≤{groups_max}(峰值 {max_groups}), "
            "但全程未观测到整组丢弃, 无法证明「裁剪不破坏因果链」, 按 plan 第 3(b) 记 INCONCLUSIVE。"
        )
        return it
    it.verdict = PASS
    it.reason = (
        f"{parsed} 次请求无孤立 tool 消息, 组数峰值 {max_groups} ≤ {groups_max}, "
        f"{trims_observed} 次裁剪全部满足从头部丢组的后缀单调。"
    )
    return it


def item_a6_5(ev: Evidence) -> Item:
    it = Item("A6-5", "README/STATUS 超限拆分")
    ev.state.require(
        "task_scope_read_view_revisions", "view_kind", "content", "task_scope_id"
    )
    rows = ev.state.rows(
        "select view_kind, content, task_scope_id, created_at"
        " from task_scope_read_view_revisions order by created_at asc"
    )
    readme_bounded = []
    status_bounded = []
    evidence_counts: list[int] = []
    for r in rows:
        kind = as_text(r["view_kind"]).upper()
        content = as_text(r["content"])
        if kind == "README" and README_BOUNDED_TAIL in content:
            readme_bounded.append(len(content.encode()))
        if kind == "STATUS" and '"bounded"' in content and "full_content_sha256" in content:
            status_bounded.append(len(content.encode()))
        if kind == "EVIDENCE":
            obj = maybe_json(content)
            if isinstance(obj, dict) and isinstance(obj.get("event_count"), int):
                evidence_counts.append(int(obj["event_count"]))

    scope_event_rows = 0
    if ev.state.has("task_scope_events", "task_scope_id"):
        with contextlib.suppress(SchemaMissing):
            scope_event_rows = ev.state.count("task_scope_events")

    it.numbers = {
        "view_revisions": len(rows),
        "readme_bounded_revisions": len(readme_bounded),
        "readme_bounded_max_bytes": max(readme_bounded) if readme_bounded else 0,
        "readme_cap_bytes": 16384,
        "status_bounded_revisions": len(status_bounded),
        "evidence_event_count_values": evidence_counts[-3:],
        "task_scope_events_rows": scope_event_rows,
    }
    if not rows:
        it.verdict = INCONCLUSIVE
        it.reason = "task_scope_read_view_revisions 无行 —— 未建 TaskScope 或未产生视图修订。"
        return it
    if not readme_bounded and not status_bounded:
        it.verdict = INCONCLUSIVE
        it.reason = (
            "README/STATUS 均未进入 bounded 形态 —— T17/T18 的超长 goal.set 未逐字落库, "
            "按 plan 第 4 节记 INCONCLUSIVE(可用多条 decision.record 顶 PLAN 作备用杠杆重试)。"
        )
        return it
    if readme_bounded and max(readme_bounded) > 16384:
        it.verdict = FAIL
        it.reason = f"README bounded 后仍有 {max(readme_bounded)} 字节 > 16384 上限。"
        return it
    if not evidence_counts:
        it.verdict = INCONCLUSIVE
        it.reason = (
            f"README bounded={len(readme_bounded)} / STATUS bounded={len(status_bounded)} 已成立, "
            "但 EVIDENCE 视图未记录 event_count, 无法核对 canonical 事件数不丢。"
        )
        return it
    if evidence_counts[-1] != scope_event_rows:
        it.verdict = FAIL
        it.reason = (
            f"EVIDENCE 视图 event_count={evidence_counts[-1]} 与 task_scope_events 实际"
            f" {scope_event_rows} 行不等, canonical 事实丢失。"
        )
        return it
    it.verdict = PASS
    it.reason = (
        f"README {len(readme_bounded)} 次 bounded(≤16384B), STATUS {len(status_bounded)} 次 bounded, "
        f"EVIDENCE event_count={evidence_counts[-1]} == task_scope_events {scope_event_rows} 行。"
    )
    return it


def item_a6_6(ev: Evidence) -> Item:
    it = Item("A6-6", "同一 plan 建节点 + relation memory")
    ev.hm.require(
        "cognitive_relations",
        "relation_id",
        "plan_id",
        "plan_hash",
        "relation_kind",
        "relation_memory_id",
        "source_memory_id",
        "source_revision",
        "target_memory_id",
        "target_revision",
        "created_at",
    )
    ev.hm.require("cognitive_memory_revisions", "memory_id", "revision", "plan_id", "plan_hash")
    ev.hm.require("cognitive_memory_heads", "memory_id")
    rels = ev.hm.rows(
        "select relation_id, plan_id, plan_hash, relation_kind, relation_memory_id,"
        " source_memory_id, source_revision, target_memory_id, target_revision"
        " from cognitive_relations order by created_at asc"
    )
    matches = []
    for r in rels:
        endpoints = [
            (as_text(r["source_memory_id"]), r["source_revision"]),
            (as_text(r["target_memory_id"]), r["target_revision"]),
        ]
        same_plan = True
        for mid, rev in endpoints:
            row = ev.hm.rows(
                "select plan_id, plan_hash from cognitive_memory_revisions"
                " where memory_id=? and revision=?",
                (mid, rev),
            )
            if not row:
                same_plan = False
                break
            if as_text(row[0]["plan_id"]) != as_text(r["plan_id"]) or as_text(
                row[0]["plan_hash"]
            ) != as_text(r["plan_hash"]):
                same_plan = False
                break
        rel_mid = as_text(r["relation_memory_id"])
        head_ok = bool(
            rel_mid
            and ev.hm.rows(
                "select 1 from cognitive_memory_heads where memory_id=?", (rel_mid,)
            )
        )
        if same_plan and head_ok:
            matches.append(
                {"relation_id": as_text(r["relation_id"]), "relation_kind": as_text(r["relation_kind"])}
            )
    kinds = sorted({as_text(r["relation_kind"]) for r in rels})
    it.numbers = {
        "cognitive_relations_rows": len(rels),
        "relations_same_plan_and_head_present": len(matches),
        "relation_kinds": kinds,
        "matched_sample": matches[:3],
    }
    if not rels:
        it.verdict = INCONCLUSIVE
        it.reason = "cognitive_relations 无行 —— T15 未生成 relation memory, 无法判定。"
        return it
    if not matches:
        it.verdict = FAIL
        it.reason = (
            f"{len(rels)} 条关系中没有一条满足「plan_id/plan_hash 与两端点 revision 相同 "
            "且 relation_memory_id 在 cognitive_memory_heads 中存在」。"
        )
        return it
    it.verdict = PASS
    it.reason = (
        f"{len(matches)}/{len(rels)} 条关系的 plan_id/plan_hash 与两端点 exact revision 一致, "
        f"relation_memory_id 在 heads 中存在; relation_kind={kinds}。"
    )
    return it


def item_a6_7(ev: Evidence) -> Item:
    """A6-7: 纠正后 head 前进到新 revision, 旧 revision 只作为不可变历史留存。

    2026-09-08 语义修正: 本项原先断言「旧 revision 的 lifecycle_state 必须退出
    active」, 这个判据与 S3 契约不符, 该断言恒为 FAIL。

    `cognitive_memory_revisions` 是 append-only 的每-revision 不可变快照:
    schema 上挂着 `cognitive_memory_revisions_immutable_update` /
    `_immutable_delete` 两个无条件 `RAISE(ABORT)` 触发器
    (`simple_harness_memory/backends/schema_v5.py:572-577`), 所以 SDK 在物理上
    不可能回头改写旧 revision 的 lifecycle_state; 全包 grep
    `UPDATE/DELETE cognitive_memory_revisions` 零命中。「哪个 revision 生效」
    只由 `cognitive_memory_heads.current_revision` 表达, 召回资格也一律按
    `r.revision = h.current_revision` 连接 (`sqlite_v5.py:2915-2924` 等), 契约
    `slices/S3-cognitive-systems-recall.md:152` 「所有长期普通候选先要求 exact
    principal、exact current head」。`superseded` 这个 lifecycle 确实在用, 但它
    是 SUPERSEDE 写入的**新** head revision 自身的状态
    (`core/mutations.py:423-425`), 不是给旧 revision 补盖的章。

    因此本项改判 head 前进 + head revision 的 lifecycle/conflict 合法 +
    evolution 血缘边存在。图谱只显示新 revision 一条 active edge 由 A6-9 判定。
    """
    it = Item("A6-7", "edge 更新/纠正(supersede)")
    ev.hm.require(
        "cognitive_memory_revisions", "memory_id", "revision", "lifecycle_state",
        "conflict_status",
    )
    ev.hm.require("cognitive_memory_heads", "memory_id", "current_revision")
    ev.hm.require(
        "cognitive_relations", "relation_domain", "relation_kind",
        "source_memory_id", "source_revision", "target_memory_id", "target_revision",
    )
    # head revision 自身的合法状态: revise 产出非终态(active 等),
    # supersede 产出 superseded(`core/mutations.py:423-425`)。
    _HEAD_LIFECYCLES = {"active", "amended", "reinforced", "superseded",
                        "pending", "triggered", "in_progress", "rescheduled"}
    heads = ev.hm.rows("select memory_id, current_revision from cognitive_memory_heads")
    multi: list[str] = []
    head_not_latest: list[tuple[str, int, int]] = []
    head_lifecycle_invalid: list[tuple[str, int, str]] = []
    missing_lineage: list[tuple[str, int]] = []
    for h in heads:
        mid = as_text(h["memory_id"])
        revs = ev.hm.rows(
            "select revision, lifecycle_state, conflict_status from"
            " cognitive_memory_revisions where memory_id=? order by revision asc",
            (mid,),
        )
        if len(revs) < 2:
            continue
        multi.append(mid)
        cur = int(h["current_revision"])
        latest = max(int(r["revision"]) for r in revs)
        if cur != latest:
            head_not_latest.append((mid, cur, latest))
        head_row = next((r for r in revs if int(r["revision"]) == cur), None)
        if head_row is None:
            head_lifecycle_invalid.append((mid, cur, "<head revision 行缺失>"))
        elif as_text(head_row["lifecycle_state"]).lower() not in _HEAD_LIFECYCLES:
            head_lifecycle_invalid.append(
                (mid, cur, as_text(head_row["lifecycle_state"]))
            )
        # 每一次 head 前进都必须留下一条 evolution 血缘边 rN -> rN-1。
        for r in revs:
            revision = int(r["revision"])
            if revision < 2:
                continue
            lineage = ev.hm.rows(
                "select 1 from cognitive_relations where relation_domain='evolution'"
                " and source_memory_id=? and source_revision=? and target_memory_id=?"
                " and target_revision=?",
                (mid, revision, mid, revision - 1),
            )
            if not lineage:
                missing_lineage.append((mid, revision))
    states = sorted(
        {
            as_text(r[0])
            for r in ev.hm.rows(
                "select distinct lifecycle_state from cognitive_memory_revisions"
            )
        }
    )
    kinds = sorted(
        {
            as_text(r[0])
            for r in ev.hm.rows(
                "select distinct relation_kind from cognitive_relations"
                " where relation_domain='evolution'"
            )
        }
    )
    it.numbers = {
        "heads": len(heads),
        "memories_with_multiple_revisions": len(multi),
        "head_not_latest_revision": head_not_latest[:3],
        "head_lifecycle_invalid": head_lifecycle_invalid[:3],
        "missing_evolution_lineage": missing_lineage[:3],
        "lifecycle_states_observed": states,
        "evolution_relation_kinds": kinds,
    }
    if not multi:
        it.verdict = INCONCLUSIVE
        it.reason = "没有任何记忆产生第 2 个 revision —— T20 的纠正未落成 supersede, 无法判定。"
        return it
    if head_not_latest:
        it.verdict = FAIL
        it.reason = f"{len(head_not_latest)} 条记忆的 head 未指向最新 revision: {head_not_latest[:3]}。"
        return it
    if head_lifecycle_invalid:
        it.verdict = FAIL
        it.reason = f"{len(head_lifecycle_invalid)} 条记忆的 head revision lifecycle 非法: {head_lifecycle_invalid[:3]}。"
        return it
    if missing_lineage:
        it.verdict = FAIL
        it.reason = f"{len(missing_lineage)} 个新 revision 缺少 evolution 血缘边: {missing_lineage[:3]}。"
        return it
    it.verdict = PASS
    it.reason = (
        f"{len(multi)} 条记忆出现新 revision, head 全部指向最新 revision 且其 lifecycle 合法"
        f"(取值 {states}), 每次前进都有 evolution 血缘边(kind={kinds});"
        f" 旧 revision 按 append-only 契约保持不可变快照, 不参与召回。"
    )
    return it


def item_a6_8(ev: Evidence) -> Item:
    it = Item("A6-8", "争议 contested")
    ev.hm.require(
        "cognitive_conflict_groups", "group_id", "memory_id", "incumbent_revision", "challenger_revision"
    )
    groups = ev.hm.rows(
        "select group_id, memory_id, incumbent_revision, challenger_revision"
        " from cognitive_conflict_groups"
    )
    contested = 0
    if ev.hm.has("cognitive_memory_revisions", "conflict_status"):
        contested = ev.hm.count(
            "cognitive_memory_revisions", "lower(conflict_status)='contested'"
        )
    # T22 的答复: 争议期必须要求确认
    w22 = ev.window_for_turn(22)
    t22_text = "\n".join(i.response_text for i in (w22 or {}).get("invocations", []))
    asks_confirm = any(k in t22_text for k in ("确认", "需要你", "请你确认", "无法确定"))
    uses_old = "3.12" in t22_text
    it.numbers = {
        "cognitive_conflict_groups_rows": len(groups),
        "revisions_contested": contested,
        "t22_invocations": len((w22 or {}).get("invocations", [])),
        "t22_asks_confirmation": asks_confirm,
        "t22_mentions_3_12": uses_old,
    }
    if not groups:
        it.verdict = INCONCLUSIVE
        it.reason = "cognitive_conflict_groups 无行 —— T21 未触发争议, 无法判定。"
        return it
    if contested == 0:
        it.verdict = FAIL
        it.reason = f"已有 {len(groups)} 个冲突组, 但没有任何 revision 的 conflict_status 变为 contested。"
        return it
    if w22 is None or not (w22 or {}).get("invocations"):
        it.verdict = INCONCLUSIVE
        it.reason = (
            f"冲突组 {len(groups)} 个、contested revision {contested} 条已成立, "
            "但 T22 未在 a6-progress.jsonl 中定位到 provider 调用, 后半段无法判定。"
        )
        return it
    if not asks_confirm:
        it.verdict = FAIL
        it.reason = "T22 的回复未出现「要求确认」语义, 争议期直接给出了执行结论。"
        return it
    it.verdict = PASS
    it.reason = (
        f"冲突组 {len(groups)} 个, contested revision {contested} 条; T22 回复要求用户确认。"
    )
    return it


# --- 普通(ordinary)展示投影口径 ------------------------------------------
#
# 契约来源(只读引用 memory-sdk plans/2026-08-29-human-memory-digital-twin):
#   * `slices/S3-cognitive-systems-recall.md` Task 6:
#     「twin_builder.py 从 canonical active/contested/inferred records 和
#      relation rows 生成 node/edge DTO … superseded/suppressed/expired 按普通
#      view policy 不展示」——contested record 是**展示素材**, 不是过滤对象;
#      被过滤的是 superseded / suppressed / expired。
#   * `acceptance.md` HM-S12 / HM-TO-A6:
#     「普通图谱显示一条可追溯 edge; 纠正后只显示新 active edge, relation/端点
#      遗忘、争议或 ordinary projection policy 判定不可展示后 edge 退出」——这一
#      句的主语是 HM-S12 场景里的 **knowledge 关系边**(`applies_to`, 由一条
#      relation memory 承载), 争议指的是它的端点进入争议。
#
# 因此普通投影分两层(与 SDK 0.6.31 实现一致):
#   node 层  `twin_builder._record_visible` + head/冲突组过滤:
#            展示 head revision; 未裁决冲突组额外展示 incumbent revision(两名
#            成员原子出现或原子消失); suppressed / expired / 非活跃 lifecycle /
#            relation memory 自身一律不展示; redacted 由 Host `graph` 丢弃。
#   edge 层  evolution 边(amends/supersedes/contests/supports/relates_to)只按
#            「两端 exact revision 都是可见 node」出现 —— 旧 revision 永不可见,
#            所以 amends/supersedes 血缘边在普通图谱里结构性不可见, 争议期唯一
#            能出现的 evolution 边就是 contests(它正是争议在图上的可读形式);
#            knowledge 边(`applies_to`)另加严格资格
#            `twin_builder.twin_graph_record_is_active_visible`: owner + 两端点
#            都必须 head + active + uncontested + 不在冲突组 + 未被抑制。
#
# 所以「争议态下普通图谱 edge 必须降到 0」不是契约; 契约要求的是「争议态下
# knowledge 边降到 0, 而 contests 边正常出现」。A6-9/A6-10 按上面这条口径判定。

_TWIN_ACTIVE_LIFECYCLES: dict[str, set[str]] = {
    "episode": {"active", "amended", "disputed"},
    "semantic": {"active"},
    "procedure": {"active", "reinforced"},
    "prospective": {"pending", "triggered", "in_progress", "rescheduled"},
}
_TWIN_INFERRED_LIFECYCLES = {"candidate", "draft"}
_TWIN_SENSITIVE_ATTRIBUTES = {
    "identity", "relationship", "family", "health", "location", "financial",
}
_TWIN_EVOLUTION_KINDS = {"amends", "supersedes", "contests", "supports", "relates_to"}


def _as_text_blob(value: Any) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return as_text(value)


def _manual_ui_observations(ev: Evidence) -> list[dict[str, Any]]:
    """从 a6-progress.jsonl 的 manual_ui note 里取回人工观测的 node/edge 计数。

    driver (`scripts/native/a6_driver.sh:201`) 把人工输入原样写进 `note`。两种
    已在用的书写形式都识别: `nodes=13 edges=1` 与 UI 原文「13条记忆，1条关系」。
    """
    out: list[dict[str, Any]] = []
    ascii_re = re.compile(r"nodes\s*=\s*(\d+)\D{0,12}?edges\s*=\s*(\d+)", re.I)
    cjk_re = re.compile(r"(\d+)\s*条记忆[^0-9]{0,8}?(\d+)\s*条关系")
    for row in ev.progress:
        if row.get("outcome") != "manual_ui":
            continue
        note = as_text(row.get("note") or "")
        m = ascii_re.search(note) or cjk_re.search(note)
        if m is None:
            continue
        out.append(
            {
                "turn": row.get("turn"),
                "nodes": int(m.group(1)),
                "edges": int(m.group(2)),
                "note": note[:200],
            }
        )
    return out


def _ordinary_graph_expectation(ev: Evidence) -> dict[str, Any]:
    """按上面的契约口径, 从 DB 复算普通图谱应有的 node/edge 集合。

    抛 SchemaMissing 交给调用方降级为 INCONCLUSIVE。
    """
    ev.hm.require(
        "cognitive_memory_heads", "memory_id", "current_revision", "memory_type"
    )
    ev.hm.require(
        "cognitive_memory_revisions", "memory_id", "revision", "lifecycle_state",
        "conflict_status", "epistemic_status", "effective_privacy_class",
        "information_attributes_json", "content_json", "valid_from", "valid_to",
        "created_at",
    )
    ev.hm.require(
        "cognitive_relations", "relation_domain", "relation_kind",
        "relation_memory_id", "relation_memory_revision",
        "source_memory_id", "source_revision", "target_memory_id", "target_revision",
    )
    ev.hm.require("cognitive_evidence_spans", "memory_id", "evidence_id", "source_kind")
    ev.hm.require(
        "suppression_directives", "directive_id", "event_kind",
        "supersedes_directive_id",
    )
    ev.hm.require("suppression_targets", "directive_id", "target_kind", "target_ref")

    notes: list[str] = []
    # 1) 生效的抑制指令(未被 revoke 覆盖)及其目标。
    active_directives = {
        as_text(r["directive_id"])
        for r in ev.hm.rows(
            "select directive_id from suppression_directives d"
            " where d.event_kind='directive' and not exists("
            "   select 1 from suppression_directives r where r.event_kind='revoke'"
            "   and r.supersedes_directive_id=d.directive_id)"
        )
    }
    direct_memories: set[str] = set()
    other_scopes: set[str] = set()
    if active_directives:
        for r in ev.hm.rows(
            "select directive_id, target_kind, target_ref from suppression_targets"
        ):
            if as_text(r["directive_id"]) not in active_directives:
                continue
            kind = as_text(r["target_kind"]).lower()
            if kind == "memory":
                direct_memories.add(as_text(r["target_ref"]))
            else:
                other_scopes.add(kind)
    if other_scopes:
        # subject/evidence/entity 作用域需要 SDK 的谱系推导, SQL 复算不到。
        notes.append(f"存在非 memory 作用域的抑制目标 {sorted(other_scopes)}, 复算口径不完整")
    # duplicate-source alias(SDK 2026-09-07 产品决定): 与被遗忘记忆共享同一条
    # USER 证据的记忆按「重新学到的同源副本」一并抑制, 但证据本身不被隐藏。
    alias_memories: set[str] = set()
    if direct_memories:
        marks = ",".join("?" for _ in direct_memories)
        alias_memories = {
            as_text(r[0])
            for r in ev.hm.rows(
                "select distinct b.memory_id from cognitive_evidence_spans a"
                " join cognitive_evidence_spans b on b.evidence_id=a.evidence_id"
                f" where a.memory_id in ({marks}) and a.source_kind='user_message'"
                " and b.source_kind='user_message'",
                tuple(sorted(direct_memories)),
            )
        } - direct_memories
    suppressed = direct_memories | alias_memories

    # 2) 每条 revision 的可展示性。
    heads = {
        as_text(r["memory_id"]): (int(r["current_revision"]), as_text(r["memory_type"]))
        for r in ev.hm.rows(
            "select memory_id, current_revision, memory_type from cognitive_memory_heads"
        )
    }
    revisions: dict[tuple[str, int], dict[str, Any]] = {}
    ref_now = 0.0
    for r in ev.hm.rows(
        "select memory_id, revision, lifecycle_state, conflict_status, epistemic_status,"
        " effective_privacy_class, cast(information_attributes_json as text) as attrs,"
        " cast(content_json as text) as content, valid_from, valid_to, created_at"
        " from cognitive_memory_revisions"
    ):
        created = float(r["created_at"] or 0.0)
        ref_now = max(ref_now, created)
        revisions[(as_text(r["memory_id"]), int(r["revision"]))] = {
            "lifecycle": as_text(r["lifecycle_state"]).lower(),
            "conflict": as_text(r["conflict_status"]).lower(),
            "epistemic": as_text(r["epistemic_status"]).lower(),
            "privacy": as_text(r["effective_privacy_class"]).lower(),
            "attrs": _as_text_blob(r["attrs"]),
            "content": _as_text_blob(r["content"]),
            "valid_from": None if r["valid_from"] is None else float(r["valid_from"]),
            "valid_to": None if r["valid_to"] is None else float(r["valid_to"]),
        }

    redacted: set[tuple[str, int]] = set()
    expired: set[tuple[str, int]] = set()
    relation_memories: set[str] = set()

    def displayable(mid: str, rev: int, *, ignore_suppression: bool = False) -> bool:
        row = revisions.get((mid, rev))
        head = heads.get(mid)
        if row is None or head is None:
            return False
        memory_type = head[1].lower()
        if memory_type == "semantic" and '"semantic_kind":"relation"' in row["content"].replace(" ", ""):
            # relation memory 只画成边, 从不作为节点(sqlite_v5.py get_twin_graph_view)。
            relation_memories.add(mid)
            return False
        if not ignore_suppression and mid in suppressed:
            return False
        if row["privacy"] == "restricted":
            return False
        if row["valid_to"] is not None and ref_now >= row["valid_to"]:
            expired.add((mid, rev))
            return False
        if row["valid_from"] is not None and ref_now < row["valid_from"]:
            expired.add((mid, rev))
            return False
        active = row["lifecycle"] in _TWIN_ACTIVE_LIFECYCLES.get(memory_type, set())
        inferred = (
            row["epistemic"] == "llm_inference"
            and row["lifecycle"] in _TWIN_INFERRED_LIFECYCLES
        )
        if not (active or inferred):
            return False
        try:
            attrs = json.loads(row["attrs"]) if row["attrs"] else []
        except (TypeError, ValueError):
            attrs = []
        is_redacted = row["privacy"] in {"sensitive", "restricted"} or bool(
            _TWIN_SENSITIVE_ATTRIBUTES.intersection(
                {as_text(a).lower() for a in attrs if isinstance(a, str)}
            )
        )
        if is_redacted:
            # SDK 会发一个 label 打码的节点, Host `PrimaryCognitiveControls.graph`
            # 再把 redacted 节点整条丢掉 —— 普通图谱里不含 redacted 项。
            redacted.add((mid, rev))
            return False
        return True

    # 3) node 集合: head revision + 未裁决冲突组的 incumbent revision(原子)。
    node_revisions: set[tuple[str, int]] = set()
    # 同一套口径再算一遍「假装没有任何抑制指令」的集合, 用来量化遗忘的实际效果。
    node_revisions_unsuppressed: set[tuple[str, int]] = set()
    for mid, (head_rev, _kind) in heads.items():
        if displayable(mid, head_rev):
            node_revisions.add((mid, head_rev))
        if displayable(mid, head_rev, ignore_suppression=True):
            node_revisions_unsuppressed.add((mid, head_rev))

    contested_pairs: list[tuple[str, int, int]] = []
    if ev.hm.has(
        "cognitive_conflict_groups", "group_id", "memory_id",
        "incumbent_revision", "challenger_revision",
    ):
        resolved: set[str] = set()
        if ev.hm.has("cognitive_conflict_resolutions", "group_id"):
            resolved = {
                as_text(r[0])
                for r in ev.hm.rows("select group_id from cognitive_conflict_resolutions")
            }
        for g in ev.hm.rows(
            "select group_id, memory_id, incumbent_revision, challenger_revision"
            " from cognitive_conflict_groups"
        ):
            if as_text(g["group_id"]) in resolved:
                continue
            mid = as_text(g["memory_id"])
            incumbent = int(g["incumbent_revision"])
            challenger = int(g["challenger_revision"])
            head = heads.get(mid)
            if head is None or head[0] != challenger:
                continue
            members = ((mid, incumbent), (mid, challenger))
            if all(displayable(m, r) for m, r in members):
                node_revisions.update(members)
                contested_pairs.append((mid, incumbent, challenger))
            else:
                # 冲突组是原子的: 一名成员不可见, 两名都不出现。
                node_revisions.difference_update(members)
            if all(displayable(m, r, ignore_suppression=True) for m, r in members):
                node_revisions_unsuppressed.update(members)
            else:
                node_revisions_unsuppressed.difference_update(members)

    # 4) edge 集合。
    visible_edges: list[dict[str, Any]] = []
    hidden_edges: list[dict[str, Any]] = []
    for r in ev.hm.rows(
        "select relation_id, relation_domain, relation_kind, relation_memory_id,"
        " relation_memory_revision, source_memory_id, source_revision,"
        " target_memory_id, target_revision from cognitive_relations"
    ):
        domain = as_text(r["relation_domain"]).lower()
        kind = as_text(r["relation_kind"]).lower()
        src = (as_text(r["source_memory_id"]), int(r["source_revision"]))
        tgt = (as_text(r["target_memory_id"]), int(r["target_revision"]))
        entry = {
            "relation_id": as_text(r["relation_id"])[:24],
            "domain": domain,
            "kind": kind,
            "source_revision": src[1],
            "target_revision": tgt[1],
        }
        if domain == "knowledge":
            owner = (
                as_text(r["relation_memory_id"] or ""),
                int(r["relation_memory_revision"] or 0),
            )

            def strict(mid: str, rev: int) -> bool:
                head = heads.get(mid)
                row = revisions.get((mid, rev))
                if head is None or row is None or head[0] != rev:
                    return False
                if mid in suppressed or row["privacy"] == "restricted":
                    return False
                if row["conflict"] == "contested":
                    return False
                if any(mid == m and rev in (i, c) for m, i, c in contested_pairs):
                    return False
                if row["valid_to"] is not None and ref_now >= row["valid_to"]:
                    return False
                return row["lifecycle"] in _TWIN_ACTIVE_LIFECYCLES.get(
                    head[1].lower(), set()
                )

            ok = all(strict(m, v) for m, v in (owner, src, tgt))
            entry["reason"] = "knowledge 边严格资格" + ("通过" if ok else "未通过")
        elif domain == "evolution" and kind in _TWIN_EVOLUTION_KINDS:
            ok = src in node_revisions and tgt in node_revisions
            entry["reason"] = (
                "两端 exact revision 都是可见 node" if ok
                else "至少一端 exact revision 不是可见 node(旧 revision/被抑制/已裁决)"
            )
        else:
            ok = False
            entry["reason"] = f"未知 relation_domain/kind({domain}/{kind})"
        (visible_edges if ok else hidden_edges).append(entry)

    return {
        "ref_now": ref_now,
        "heads": len(heads),
        "expected_nodes": len(node_revisions),
        "expected_edges": len(visible_edges),
        "expected_nodes_without_suppression": len(node_revisions_unsuppressed),
        "suppressed_direct": sorted(m[-12:] for m in direct_memories),
        "suppressed_alias_same_source": sorted(m[-12:] for m in alias_memories),
        "redacted_excluded": sorted((m[-12:], r) for m, r in redacted),
        "expired_excluded": sorted((m[-12:], r) for m, r in expired),
        "relation_memory_nodes_excluded": sorted(m[-12:] for m in relation_memories),
        "contested_pairs": [(m[-12:], i, c) for m, i, c in contested_pairs],
        "visible_edges": visible_edges,
        "hidden_edges": hidden_edges,
        "notes": notes,
    }


def item_a6_9(ev: Evidence) -> Item:
    """A6-9: 普通投影口径。

    2026-09-08 判据修正: 本项原先照抄 A6 计划里的「争议/遗忘态下普通图谱 edge
    数按预期降到 0」并因证据目录无边数记录而恒 INCONCLUSIVE。契约(见本文件
    `_ordinary_graph_expectation` 上方注释)要求的不是「edge 归零」, 而是:
    knowledge(`applies_to`)边在端点争议/遗忘后退出; evolution 边只在两端 exact
    revision 都可见时出现 —— 争议期 contests 边**应当**出现, 它就是争议在普通
    图谱上的可读形式。本项改为「从 DB 复算应有 node/edge 集合, 与 driver 记录
    的人工 UI 观测逐一对齐」。
    """
    it = Item("A6-9", "ordinary projection policy 过滤")
    observations = _manual_ui_observations(ev)
    try:
        exp = _ordinary_graph_expectation(ev)
    except SchemaMissing as exc:
        it.verdict = INCONCLUSIVE
        it.reason = f"复算普通投影所需的表/列缺失: {exc}"
        it.numbers = {"manual_ui_observations": observations}
        return it
    it.numbers = {
        "expected_nodes": exp["expected_nodes"],
        "expected_edges": exp["expected_edges"],
        "heads": exp["heads"],
        "suppressed_direct": exp["suppressed_direct"],
        "suppressed_alias_same_source": exp["suppressed_alias_same_source"],
        "redacted_excluded": exp["redacted_excluded"],
        "expired_excluded": exp["expired_excluded"],
        "relation_memory_nodes_excluded": exp["relation_memory_nodes_excluded"],
        "contested_pairs": exp["contested_pairs"],
        "visible_edges": exp["visible_edges"],
        "hidden_edges": exp["hidden_edges"],
        "manual_ui_observations": observations,
        "expectation_notes": exp["notes"],
    }
    if exp["notes"]:
        it.verdict = INCONCLUSIVE
        it.reason = "; ".join(exp["notes"]) + " —— 无法给出确定判定。"
        return it
    if not observations:
        it.verdict = INCONCLUSIVE
        it.reason = (
            f"DB 复算得普通图谱应为 nodes={exp['expected_nodes']} edges={exp['expected_edges']}, "
            "但 a6-progress.jsonl 的 manual_ui note 里没有可解析的 node/edge 观测"
            "(需写成 `nodes=N edges=M` 或 UI 原文「N条记忆，M条关系」)。"
        )
        return it
    last = observations[-1]
    if last["nodes"] != exp["expected_nodes"] or last["edges"] != exp["expected_edges"]:
        it.verdict = FAIL
        it.reason = (
            f"T{last['turn']} 人工观测 nodes={last['nodes']} edges={last['edges']}, "
            f"与按契约从 DB 复算的 nodes={exp['expected_nodes']} edges={exp['expected_edges']} 不符。"
        )
        return it
    kinds = sorted({e["kind"] for e in exp["visible_edges"]})
    hidden_kinds = sorted({e["kind"] for e in exp["hidden_edges"]})
    it.verdict = PASS
    it.reason = (
        f"T{last['turn']} 人工观测 nodes={last['nodes']} edges={last['edges']}, 与 DB 复算一致; "
        f"被抑制 {len(exp['suppressed_direct'])} 条(含同源副本 "
        f"{len(exp['suppressed_alias_same_source'])} 条)、redacted {len(exp['redacted_excluded'])} 条、"
        f"expired {len(exp['expired_excluded'])} 条、relation memory "
        f"{len(exp['relation_memory_nodes_excluded'])} 条都不在图上; "
        f"可见边 kind={kinds}(争议期 contests 边按契约应当出现), 被过滤边 kind={hidden_kinds}"
        "(旧 revision 血缘边/端点不可见)。"
    )
    return it


def item_a6_10(ev: Evidence) -> Item:
    """A6-10: relation/endpoint 遗忘 + close/reopen。

    2026-09-08 判据修正: 原实现只能证明「关系行 append-only」并停在
    INCONCLUSIVE。改为把三条契约要求都算出来: (a) 关系行不得物理减少;
    (b) 遗忘后普通图谱恰好等于按契约复算的 post-suppression 集合(被遗忘项及其
    同源副本离图); (c) 关表重开的观测与遗忘后的观测逐字相同(不复活)。
    """
    it = Item("A6-10", "relation/endpoint 遗忘 + close/reopen")
    ev.hm.require("cognitive_relations", "relation_id")
    final_rows = ev.hm.count("cognitive_relations")
    peak = 0
    for row in ev.progress:
        v = row.get("cognitive_relations")
        if isinstance(v, int):
            peak = max(peak, v)
    directives = 0
    if ev.hm.has("suppression_directives", "directive_id"):
        directives = ev.hm.count("suppression_directives")
    targets = 0
    if ev.hm.has("suppression_targets", "target_ref"):
        targets = ev.hm.count("suppression_targets")
    observations = _manual_ui_observations(ev)
    it.numbers = {
        "cognitive_relations_final": final_rows,
        "cognitive_relations_peak_in_progress": peak,
        "suppression_directives": directives,
        "suppression_targets": targets,
        "manual_ui_observations": observations,
    }
    if final_rows < peak:
        it.verdict = FAIL
        it.reason = f"cognitive_relations 从峰值 {peak} 行降到 {final_rows} 行 —— 关系被物理删除, 违反 append-only。"
        return it
    if final_rows == 0:
        it.verdict = INCONCLUSIVE
        it.reason = "从未建立关系行, T23 的遗忘无对象, 无法判定。"
        return it
    if directives == 0:
        it.verdict = INCONCLUSIVE
        it.reason = (
            f"关系行 {final_rows} 未减少(append-only 成立), 但 suppression_directives=0, "
            "T23 的逻辑遗忘未落库, 无法判定。"
        )
        return it
    try:
        exp = _ordinary_graph_expectation(ev)
    except SchemaMissing as exc:
        it.verdict = INCONCLUSIVE
        it.reason = f"关系行 append-only 成立, 但复算普通投影所需的表/列缺失: {exc}"
        return it
    knowledge_rows = 0
    if ev.hm.has("cognitive_relations", "relation_domain"):
        knowledge_rows = ev.hm.count(
            "cognitive_relations", "lower(relation_domain)='knowledge'"
        )
    removed = exp["expected_nodes_without_suppression"] - exp["expected_nodes"]
    it.numbers.update(
        {
            "expected_nodes": exp["expected_nodes"],
            "expected_edges": exp["expected_edges"],
            "nodes_removed_by_suppression": removed,
            "suppressed_direct": exp["suppressed_direct"],
            "suppressed_alias_same_source": exp["suppressed_alias_same_source"],
            "knowledge_relation_rows": knowledge_rows,
            "expectation_notes": exp["notes"],
        }
    )
    if exp["notes"]:
        it.verdict = INCONCLUSIVE
        it.reason = "关系行 append-only 成立, 但 " + "; ".join(exp["notes"]) + "。"
        return it
    after = [o for o in observations if o["turn"] is not None and int(o["turn"]) >= 23]
    if len(after) < 2:
        it.verdict = INCONCLUSIVE
        it.reason = (
            f"关系行 {final_rows} 未减少、suppression_directives={directives} 已写入, "
            "但 manual_ui note 里遗忘后+关表重开的可解析观测不足两条, 无法判定不复活。"
        )
        return it
    forget_obs, reopen_obs = after[-2], after[-1]
    if (forget_obs["nodes"], forget_obs["edges"]) != (
        exp["expected_nodes"], exp["expected_edges"]
    ):
        it.verdict = FAIL
        it.reason = (
            f"遗忘后 T{forget_obs['turn']} 观测 nodes={forget_obs['nodes']} "
            f"edges={forget_obs['edges']}, 与复算的 nodes={exp['expected_nodes']} "
            f"edges={exp['expected_edges']} 不符 —— 被遗忘项没有按 ordinary policy 离图。"
        )
        return it
    if removed <= 0:
        it.verdict = FAIL
        it.reason = (
            f"suppression_directives={directives} 已写入, 但复算显示没有任何节点因抑制离图"
            f"(removed={removed}) —— 遗忘对普通图谱无效。"
        )
        return it
    if (reopen_obs["nodes"], reopen_obs["edges"]) != (
        forget_obs["nodes"], forget_obs["edges"]
    ):
        it.verdict = FAIL
        it.reason = (
            f"关表重开 T{reopen_obs['turn']} 观测 nodes={reopen_obs['nodes']} "
            f"edges={reopen_obs['edges']}, 与遗忘后 T{forget_obs['turn']} 的 "
            f"nodes={forget_obs['nodes']} edges={forget_obs['edges']} 不同 —— 边/节点复活。"
        )
        return it
    scope = (
        "本次无 knowledge(applies_to)关系行, 「relation memory 自身被遗忘」子例由 A6-6 承载"
        if knowledge_rows == 0
        else f"含 knowledge 关系行 {knowledge_rows} 条"
    )
    it.verdict = PASS
    it.reason = (
        f"关系行 {final_rows} 未减少(峰值 {peak}, append-only 成立); 遗忘使 {removed} 个节点离图"
        f"(直接目标 {len(exp['suppressed_direct'])} 条 + 同源副本 "
        f"{len(exp['suppressed_alias_same_source'])} 条), T{forget_obs['turn']} 观测 "
        f"nodes={forget_obs['nodes']} edges={forget_obs['edges']} 与复算一致; "
        f"T{reopen_obs['turn']} 关表重开观测逐字相同, 未复活。{scope}。"
    )
    return it


def _graph_needles(ev: Evidence) -> dict[str, set[str]]:
    needles: dict[str, set[str]] = {
        "relation_id": set(),
        "relation_hash": set(),
        "memory_id": set(),
    }
    if ev.hm.has("cognitive_relations", "relation_id", "relation_hash"):
        with contextlib.suppress(SchemaMissing):
            for r in ev.hm.rows("select relation_id, relation_hash from cognitive_relations"):
                if r[0]:
                    needles["relation_id"].add(as_text(r[0]))
                if r[1]:
                    needles["relation_hash"].add(as_text(r[1]))
    if ev.hm.has("cognitive_memory_heads", "memory_id"):
        with contextlib.suppress(SchemaMissing):
            for r in ev.hm.rows("select memory_id from cognitive_memory_heads"):
                if r[0]:
                    needles["memory_id"].add(as_text(r[0]))
    return needles


def _memory_id_only_in_tool_results(inv: Invocation, values: Iterable[str]) -> bool:
    """判断 memory_id 命中是否只出现在 tool 回执内容里(模型自己调用工具的回声)。"""
    if inv.request is None:
        return False
    vals = [v for v in values if v]
    if not vals:
        return True
    outside = 0
    for msg in inv.request.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = as_text(msg.get("content"))
        if not any(v in content for v in vals):
            continue
        if msg.get("role") == "tool":
            continue
        # 历史组包裹的 user 消息: 只在组内 tool 消息里出现也算工具回声
        groups = []
        if "historical_causal_group" in content:
            start = content.find("{")
            if start >= 0:
                for obj in json_objects(content, start):
                    if isinstance(obj, dict) and obj.get("kind") == "historical_causal_group":
                        groups.append(obj)
        if groups:
            leaked = False
            for g in groups:
                for m in g.get("messages") or []:
                    if not isinstance(m, dict) or m.get("role") == "tool":
                        continue
                    if any(v in as_text(m.get("content")) for v in vals):
                        leaked = True
            if not leaked:
                continue
        outside += 1
    return outside == 0


def item_a6_11(ev: Evidence, strict: bool) -> Item:
    it = Item("A6-11", "图谱不进入 Provider Context")
    needles = _graph_needles(ev)
    invs = ev.invocations()
    hits = {"relation_id": 0, "relation_hash": 0, "memory_id": 0, "structural": 0}
    structural_samples: list[str] = []
    memory_hits_tool_only = True
    memory_hit_values: set[str] = set()
    for inv in invs:
        text = inv.request_json_text
        for key in ("relation_id", "relation_hash"):
            found = [v for v in needles[key] if v and v in text]
            hits[key] += len(found)
        mem_found = [v for v in needles["memory_id"] if v and v in text]
        if mem_found:
            hits["memory_id"] += len(mem_found)
            memory_hit_values.update(mem_found)
            if not _memory_id_only_in_tool_results(inv, mem_found):
                memory_hits_tool_only = False
        for key in GRAPH_STRUCTURAL_NEEDLES:
            if key in text:
                hits["structural"] += 1
                if len(structural_samples) < 3:
                    structural_samples.append(f"{inv.invocation_id[:12]}:{key}")

    # T16/T24 纯 UI 操作不得新增 provider_invocations
    ui_delta: dict[str, int | None] = {}
    for turn in MANUAL_UI_TURNS:
        row = ev.progress_row(turn)
        prev = ev.progress_row(turn - 1)
        if row is None or prev is None:
            ui_delta[f"T{turn}"] = None
            continue
        cur = row.get("provider_invocations")
        was = prev.get("provider_invocations")
        ui_delta[f"T{turn}"] = (
            int(cur) - int(was) if isinstance(cur, int) and isinstance(was, int) else None
        )

    it.numbers = {
        "invocations_scanned": len(invs),
        "needle_relation_ids": len(needles["relation_id"]),
        "needle_memory_ids": len(needles["memory_id"]),
        "hits_relation_id": hits["relation_id"],
        "hits_relation_hash": hits["relation_hash"],
        "hits_memory_id": hits["memory_id"],
        "hits_memory_id_only_in_tool_results": memory_hits_tool_only,
        "hits_graph_structural_keys": hits["structural"],
        "structural_samples": structural_samples,
        "ui_turn_invocation_delta": ui_delta,
        "strict_mode": strict,
    }
    if not invs:
        it.verdict = INCONCLUSIVE
        it.reason = "无 provider_invocations 行, 无法扫描。"
        return it
    bad_ui = [k for k, v in ui_delta.items() if isinstance(v, int) and v != 0]
    unknown_ui = [k for k, v in ui_delta.items() if v is None]
    hard = hits["relation_id"] + hits["relation_hash"] + hits["structural"]
    if strict:
        hard += hits["memory_id"]
    elif hits["memory_id"] and not memory_hits_tool_only:
        hard += hits["memory_id"]
    if hard:
        it.verdict = FAIL
        it.reason = (
            f"request_json 命中图谱标识: relation_id={hits['relation_id']}, "
            f"relation_hash={hits['relation_hash']}, 结构键={hits['structural']}, "
            f"memory_id={hits['memory_id']}(严格模式={strict})。"
        )
        return it
    note = ""
    if hits["memory_id"]:
        note = (
            f" 注: 有 {hits['memory_id']} 次 memory_id 命中, 但全部落在 tool 回执内容里"
            "(模型自己调用工具后的返回值回声), 非图谱投影; --strict-a6-11 下会判 FAIL。"
        )
    if bad_ui:
        it.verdict = FAIL
        it.reason = f"T{bad_ui} 的纯 UI 图谱操作新增了 provider_invocations 行。{note}"
        return it
    if unknown_ui:
        it.verdict = INCONCLUSIVE
        it.reason = (
            f"图谱标识扫描 0 命中, 但 {unknown_ui} 的 UI 轮在 a6-progress.jsonl 中缺少计数快照, "
            f"无法核对行数增量。{note}"
        )
        return it
    it.verdict = PASS
    it.reason = (
        f"{len(invs)} 次请求中 relation_id/relation_hash/twin_graph/graph_edge 命中数 = 0; "
        f"T16/T24 的 provider_invocations 增量均为 0。{note}"
    )
    return it


def item_a6_12(ev: Evidence, replay: Callable[[str, str], str] | None) -> Item:
    it = Item("A6-12", "snapshot 重放指纹")
    ev.state.require(
        "run_context_snapshot_receipts",
        "sdk_run_id",
        "provider_turn_ordinal",
        "expected_request_fingerprint",
        "payload_hash",
    )
    invs = ev.invocations()
    if replay is None:
        it.numbers = {"invocations": len(invs)}
        it.verdict = INCONCLUSIVE
        it.reason = (
            "无法从 --installed-target 导入 "
            "simple_harness.execution.provider_invocations.provider_request_from_json/"
            "provider_request_fingerprint, 重放不可执行。"
        )
        return it
    replay_ok = replay_bad = replay_err = 0
    bad_samples: list[str] = []
    for inv in invs:
        if not inv.request_json_text:
            replay_err += 1
            continue
        try:
            got = replay(inv.request_id, inv.request_json_text)
        except Exception as exc:  # noqa: BLE001 - 逐行容错
            replay_err += 1
            if len(bad_samples) < 3:
                bad_samples.append(f"{inv.invocation_id[:12]}:{type(exc).__name__}")
            continue
        if got == inv.request_fingerprint:
            replay_ok += 1
        else:
            replay_bad += 1
            if len(bad_samples) < 3:
                bad_samples.append(f"{inv.invocation_id[:12]}:fingerprint_mismatch")

    receipts: dict[tuple[str, int], sqlite3.Row] = {}
    for r in ev.state.rows(
        "select sdk_run_id, provider_turn_ordinal, expected_request_fingerprint, payload_hash"
        " from run_context_snapshot_receipts"
    ):
        receipts[(as_text(r["sdk_run_id"]), int(r["provider_turn_ordinal"]))] = r

    by_run: dict[str, list[Invocation]] = {}
    for inv in invs:
        by_run.setdefault(inv.run_id, []).append(inv)
    aligned = misaligned = unmatched = 0
    payload_self = payload_bad = 0
    count_mismatch: list[str] = []
    for run_id, items in by_run.items():
        items.sort(key=lambda i: (i.claimed_at, i.invocation_id))
        n_receipts = sum(1 for k in receipts if k[0] == run_id)
        if n_receipts != len(items):
            count_mismatch.append(f"{run_id[-8:]}:{n_receipts}!={len(items)}")
        for idx, inv in enumerate(items, start=1):
            rec = receipts.get((run_id, idx))
            if rec is None:
                unmatched += 1
                continue
            exp = as_text(rec["expected_request_fingerprint"])
            if exp == inv.request_fingerprint:
                aligned += 1
            else:
                misaligned += 1
            if as_text(rec["payload_hash"]) == exp:
                payload_self += 1
            else:
                payload_bad += 1

    it.numbers = {
        "invocations": len(invs),
        "replay_match": replay_ok,
        "replay_mismatch": replay_bad,
        "replay_error": replay_err,
        "replay_error_samples": bad_samples,
        "receipts": len(receipts),
        "fingerprint_aligned_with_receipt": aligned,
        "fingerprint_misaligned": misaligned,
        "invocations_without_receipt": unmatched,
        "receipt_payload_hash_equals_expected": payload_self,
        "receipt_payload_hash_mismatch": payload_bad,
        "per_run_count_mismatch": count_mismatch[:5],
    }
    if not invs:
        it.verdict = INCONCLUSIVE
        it.reason = "无 provider_invocations 行, 无法重放。"
        return it
    if replay_bad or misaligned or payload_bad:
        it.verdict = FAIL
        it.reason = (
            f"重放不等 {replay_bad} 条; 与 receipt.expected_request_fingerprint 不等 {misaligned} 条; "
            f"payload_hash 自洽失败 {payload_bad} 条。"
        )
        return it
    if replay_err:
        it.verdict = INCONCLUSIVE
        it.reason = f"{replay_err} 条 request_json 无法重放({bad_samples}), 其余 {replay_ok} 条相等。"
        return it
    if unmatched or count_mismatch:
        it.verdict = INCONCLUSIVE
        it.reason = (
            f"{replay_ok} 条重放全等, 但 {unmatched} 次调用无对应 receipt / "
            f"{len(count_mismatch)} 个 Run 的行数不匹配{count_mismatch[:3]}(疑似跑动中的活库快照)。"
        )
        return it
    it.verdict = PASS
    it.reason = (
        f"{replay_ok} 条 request_json 重放指纹 == request_fingerprint == "
        f"receipt.expected_request_fingerprint, 且 payload_hash 与之恒等({payload_self} 条)。"
    )
    return it


# ---------------------------------------------------------------- 负控 NC1..NC6


def item_nc1(ev: Evidence) -> Item:
    it = Item("NC-1", "T3 简单改写不查长期库(no_recall)")
    ev.state.require("context_route_decisions", "sdk_run_id", "route", "origin", "recorded_at")
    w = ev.window_for_turn(3)
    if w is None:
        it.numbers = {"t3_window": None}
        it.verdict = INCONCLUSIVE
        it.reason = "a6-progress.jsonl 无 T3 记录, 无法定位该轮。"
        return it
    rows = ev.state.rows(
        "select route, origin from context_route_decisions where recorded_at > ? and recorded_at <= ?",
        (w["start"], w["end"]),
    )
    origins = [as_text(r["origin"]) for r in rows]
    bad = [o for o in origins if o and o != "no_recall"]
    it.numbers = {
        "route_decisions_in_t3": len(rows),
        "origins": origins,
        "non_no_recall": bad,
    }
    if w.get("outcome") == "timeout":
        it.verdict = INCONCLUSIVE
        it.reason = "T3 记为 timeout, 断言降级。"
        return it
    if bad:
        it.verdict = FAIL
        it.reason = f"T3 出现非 no_recall 的路由来源 {bad}, 触发了长期库查询。"
        return it
    it.verdict = PASS
    it.reason = f"T3 的 context_route_decisions {len(rows)} 行全部为 no_recall(或无召回记录)。"
    return it


def item_nc2(ev: Evidence) -> Item:
    it = Item("NC-2", "T12 闲聊不建/切 TaskScope")
    ev.state.require("context_route_decisions", "route", "recorded_at")
    w = ev.window_for_turn(12)
    if w is None:
        it.verdict = INCONCLUSIVE
        it.reason = "a6-progress.jsonl 无 T12 记录, 无法定位该轮。"
        it.numbers = {"t12_window": None}
        return it
    rows = ev.state.rows(
        "select route from context_route_decisions where recorded_at > ? and recorded_at <= ?",
        (w["start"], w["end"]),
    )
    routes = [as_text(r["route"]) for r in rows]
    it.numbers = {"route_decisions_in_t12": len(rows), "routes": routes}
    if w.get("outcome") == "timeout":
        it.verdict = INCONCLUSIVE
        it.reason = "T12 记为 timeout, 断言降级。"
        return it
    if "create_new" in routes:
        it.verdict = FAIL
        it.reason = "T12 出现 route='create_new', active scope 发生漂移。"
        return it
    it.verdict = PASS
    it.reason = f"T12 未出现 create_new(该轮路由 {routes or '无'})。"
    return it


def item_nc3(ev: Evidence) -> Item:
    it = Item("NC-3", "T14 模糊愿望不产生 Prospective")
    n_records = n_regs = None
    if ev.hm.has("prospective_records", "memory_id"):
        n_records = ev.hm.count("prospective_records")
    if ev.hm.has("prospective_scheduler_registrations"):
        n_regs = ev.hm.count("prospective_scheduler_registrations")
    it.numbers = {
        "prospective_records": n_records,
        "prospective_scheduler_registrations": n_regs,
    }
    if n_records is None:
        it.verdict = INCONCLUSIVE
        it.reason = "human_memory_v7.db 无 prospective_records 表, 无法判定。"
        return it
    if ev.max_turn_recorded() < 14:
        it.verdict = INCONCLUSIVE
        it.reason = f"只跑到 T{ev.max_turn_recorded()}, 尚未到 T14。"
        return it
    if n_records > 0 or (n_regs or 0) > 0:
        it.verdict = FAIL
        it.reason = f"出现 prospective_records={n_records} / scheduler_registrations={n_regs}, 产生了待办调度。"
        return it
    it.verdict = PASS
    it.reason = "prospective_records 与 scheduler_registrations 均为 0, 未产生 pending Prospective。"
    return it


def item_nc4(ev: Evidence) -> Item:
    it = Item("NC-4", "T22 争议期不直接用旧值 3.12")
    w = ev.window_for_turn(22)
    invs = (w or {}).get("invocations", [])
    text = "\n".join(i.response_text for i in invs)
    asks = any(k in text for k in ("确认", "需要你", "请你确认", "无法确定"))
    mentions = "3.12" in text
    it.numbers = {
        "t22_invocations": len(invs),
        "asks_confirmation": asks,
        "mentions_3_12": mentions,
        "response_chars": len(text),
    }
    if w is None or not invs:
        it.verdict = INCONCLUSIVE
        it.reason = "未定位到 T22 的 provider 调用(未跑到该轮或计数快照缺失)。"
        return it
    if w.get("outcome") == "timeout":
        it.verdict = INCONCLUSIVE
        it.reason = "T22 记为 timeout, 断言降级。"
        return it
    if not asks:
        it.verdict = FAIL
        it.reason = "T22 回复未要求确认, 争议期直接下了执行结论。"
        return it
    it.verdict = PASS
    it.reason = f"T22 回复包含要求确认语义(提及 3.12={mentions}, 作为争议项陈述而非执行结论)。"
    return it


def item_nc5(ev: Evidence) -> Item:
    it = Item("NC-5", "T16/T24 图谱 UI 不新增 provider_invocations")
    deltas: dict[str, int | None] = {}
    for turn in MANUAL_UI_TURNS:
        row = ev.progress_row(turn)
        prev = ev.progress_row(turn - 1)
        if row is None or prev is None:
            deltas[f"T{turn}"] = None
            continue
        cur, was = row.get("provider_invocations"), prev.get("provider_invocations")
        deltas[f"T{turn}"] = (
            int(cur) - int(was) if isinstance(cur, int) and isinstance(was, int) else None
        )
    it.numbers = {"provider_invocation_delta": deltas}
    bad = [k for k, v in deltas.items() if isinstance(v, int) and v != 0]
    unknown = [k for k, v in deltas.items() if v is None]
    if bad:
        it.verdict = FAIL
        it.reason = f"{bad} 的图谱 UI 操作新增了 provider_invocations 行。"
        return it
    if unknown:
        it.verdict = INCONCLUSIVE
        it.reason = f"{unknown} 的计数快照缺失(未跑到该轮), 无法核对增量。"
        return it
    it.verdict = PASS
    it.reason = f"T16/T24 的 provider_invocations 增量均为 0({deltas})。"
    return it


def item_nc6(ev: Evidence) -> Item:
    it = Item("NC-6", "request_json 无凭据形状")
    invs = ev.invocations()
    hits: dict[str, int] = {}
    samples: list[str] = []
    for inv in invs:
        for name, pat in CREDENTIAL_PATTERNS:
            found = pat.findall(inv.request_json_text)
            if found:
                hits[name] = hits.get(name, 0) + len(found)
                if len(samples) < 3:
                    samples.append(f"{inv.invocation_id[:12]}:{name}")
    it.numbers = {
        "invocations_scanned": len(invs),
        "credential_hits": hits,
        "samples": samples,
    }
    if not invs:
        it.verdict = INCONCLUSIVE
        it.reason = "无 provider_invocations 行, 无法扫描。"
        return it
    if hits:
        it.verdict = FAIL
        it.reason = f"命中凭据形状 {hits}(样本 {samples})。"
        return it
    it.verdict = PASS
    it.reason = f"{len(invs)} 次请求全文扫描, sk-/Bearer/ghp_/AKIA/PRIVATE KEY 命中数均为 0。"
    return it


# ---------------------------------------------------------------- 组装 / 日志三元组


def context_triples(ev: Evidence) -> dict[str, Any]:
    counts = {
        k: len(re.findall(r'"event":\s*"context\.' + k + r'"', ev.native_log))
        for k in ("preparing", "staged", "consumed")
    }
    runs = 0
    if ev.state.has("foreground_run_heads", "host_run_id"):
        with contextlib.suppress(SchemaMissing):
            runs = ev.state.count("foreground_run_heads")
    counts["foreground_runs"] = runs
    counts["balanced"] = (
        counts["preparing"] == counts["staged"] == counts["consumed"]
    )
    counts["covers_all_runs"] = counts["balanced"] and counts["preparing"] >= runs
    return counts


# ---------------------------------------------------------------- 重放器


def build_replayer(installed_target: str | None) -> tuple[Callable[[str, str], str] | None, dict[str, Any]]:
    info: dict[str, Any] = {
        "installed_target": installed_target,
        "imported": False,
        "functions": [],
        "error": None,
    }
    if not installed_target:
        info["error"] = "未提供 --installed-target"
        return None, info
    target = os.path.abspath(installed_target)
    if not os.path.isdir(os.path.join(target, "simple_harness")):
        info["error"] = f"{target} 下没有 simple_harness 包"
        return None, info
    if target not in sys.path:
        sys.path.insert(0, target)
    try:
        from simple_harness.contracts import RequestId, canonical_json  # type: ignore
        from simple_harness.execution.provider_invocations import (  # type: ignore
            provider_request_fingerprint,
            provider_request_from_json,
        )
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"import 失败: {type(exc).__name__}: {exc}"
        return None, info

    global CANONICAL_JSON
    CANONICAL_JSON = canonical_json
    info["imported"] = True
    info["functions"] = [
        "simple_harness.execution.provider_invocations.provider_request_from_json"
        "(request_id: RequestId, value: object)",
        "simple_harness.execution.provider_invocations.provider_request_fingerprint(request)",
        "simple_harness.contracts.RequestId",
        "simple_harness.contracts.canonical_json",
    ]
    info["module_file"] = getattr(
        sys.modules.get("simple_harness.execution.provider_invocations"), "__file__", None
    )

    def replay(request_id: str, request_json_text: str) -> str:
        value = json.loads(request_json_text)
        return provider_request_fingerprint(provider_request_from_json(RequestId(request_id), value))

    return replay, info


# ---------------------------------------------------------------- 报告


ORDER = [
    "A6-1", "A6-2", "A6-3", "A6-4", "A6-5", "A6-6",
    "A6-7", "A6-8", "A6-9", "A6-10", "A6-11", "A6-12",
    "NC-1", "NC-2", "NC-3", "NC-4", "NC-5", "NC-6",
]


def _md_escape(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def _numbers_brief(numbers: dict[str, Any], limit: int = 4) -> str:
    parts = []
    for k, v in numbers.items():
        if v is None or v == [] or v == {}:
            continue
        if isinstance(v, (list, dict)):
            v = json.dumps(v, ensure_ascii=False)
            if len(v) > 60:
                v = v[:57] + "..."
        parts.append(f"{k}={v}")
        if len(parts) >= limit:
            break
    return _md_escape("; ".join(parts)) or "-"


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# HM-TO-A6 ContextSnapshot 审计结果")
    lines.append("")
    lines.append(f"证据目录: `{report['evidence_root']}`")
    b = report["budget"]
    lines.append(
        f"窗口/预算: window={b['window_tokens']} (来源: {b['window_source']}"
        + ("，**为 plan 假设值，未在证据中记录**" if b["window_assumed"] else "")
        + f"); tier={b['budget_tier']}; generation_reserve={b['generation_reserve']}; "
        f"safety_margin={b['safety_margin']}; effective_input_budget={b['effective_input_budget']}"
    )
    t = report["context_triples"]
    lines.append(
        f"native.log 三元组: preparing={t['preparing']} / staged={t['staged']} / "
        f"consumed={t['consumed']}; 前台 Run={t['foreground_runs']}; 均衡={t['balanced']}"
    )
    lines.append(
        f"轮次: 记录到 T{report['turns_recorded']}/{EXPECTED_TURNS}; timeout 轮={report['timeout_turns'] or '无'}"
    )
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
    tally = report["tally"]
    lines.append(
        "  ".join(f"{k}={tally.get(k, 0)}" for k in (PASS, FAIL, BLOCKED, INCONCLUSIVE))
    )
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


# ---------------------------------------------------------------- 主流程


def run_items(ev: Evidence, replay, strict_a6_11: bool) -> list[Item]:
    budget = ev.budget_profile()
    specs: list[tuple[str, str, Callable[[], Item]]] = [
        ("A6-1", "20+ turn 动态组装", lambda: item_a6_1(ev)),
        ("A6-2", "大 tool result 分页", lambda: item_a6_2(ev)),
        ("A6-3", "预算内有界", lambda: item_a6_3(ev, budget)),
        ("A6-4", "裁剪不破坏因果链", lambda: item_a6_4(ev, budget)),
        ("A6-5", "README/STATUS 超限拆分", lambda: item_a6_5(ev)),
        ("A6-6", "同一 plan 建节点 + relation memory", lambda: item_a6_6(ev)),
        ("A6-7", "edge 更新/纠正(supersede)", lambda: item_a6_7(ev)),
        ("A6-8", "争议 contested", lambda: item_a6_8(ev)),
        ("A6-9", "ordinary projection policy 过滤", lambda: item_a6_9(ev)),
        ("A6-10", "relation/endpoint 遗忘 + close/reopen", lambda: item_a6_10(ev)),
        ("A6-11", "图谱不进入 Provider Context", lambda: item_a6_11(ev, strict_a6_11)),
        ("A6-12", "snapshot 重放指纹", lambda: item_a6_12(ev, replay)),
        ("NC-1", "T3 简单改写不查长期库(no_recall)", lambda: item_nc1(ev)),
        ("NC-2", "T12 闲聊不建/切 TaskScope", lambda: item_nc2(ev)),
        ("NC-3", "T14 模糊愿望不产生 Prospective", lambda: item_nc3(ev)),
        ("NC-4", "T22 争议期不直接用旧值 3.12", lambda: item_nc4(ev)),
        ("NC-5", "T16/T24 图谱 UI 不新增 provider_invocations", lambda: item_nc5(ev)),
        ("NC-6", "request_json 无凭据形状", lambda: item_nc6(ev)),
    ]
    out: list[Item] = []
    for key, title, fn in specs:
        try:
            item = fn()
        except SchemaMissing as exc:
            item = Item(key, title, INCONCLUSIVE, f"schema 缺失: {exc}", {})
        except Exception as exc:  # noqa: BLE001 - 单项异常不拖垮整份报告
            item = Item(
                key, title, INCONCLUSIVE, f"计算异常 {type(exc).__name__}: {exc}", {}
            )
        item.key, item.title = key, title
        out.append(item)
    return out


def build_report(ev: Evidence, replay, replay_info: dict[str, Any], strict: bool) -> dict[str, Any]:
    budget = ev.budget_profile()
    items = run_items(ev, replay, strict)
    tally: dict[str, int] = {}
    for i in items:
        tally[i.verdict] = tally.get(i.verdict, 0) + 1
    notes: list[str] = []
    if budget["window_assumed"]:
        notes.append(
            f"未能从证据中读到真实 context window(sdk_provider_attempt_audit.context_window 恒为 0, "
            f"native.log 无 model_context_resolved), 按 plan 假设 luna window={DEFAULT_WINDOW_ASSUMPTION}。"
        )
    else:
        notes.append(
            f"context window={budget['window_tokens']} 读自 {budget['window_source']}; "
            f"receipts 记录的 budget_tier={budget['budget_tier_recorded'] or '无'}。"
        )
    if budget["window_tokens"] != DEFAULT_WINDOW_ASSUMPTION:
        notes.append(
            f"**与 plan 第 0 节的阈值前提不符**: plan 按 gpt-5.6-luna window={DEFAULT_WINDOW_ASSUMPTION}"
            f"(落 8192 档, effective_input_budget≈26752)推导阈值, 本次实测 model={budget['models_used']}"
            f" window={budget['window_tokens']} → 落 {budget['budget_tier']} 档, "
            f"effective_input_budget={budget['effective_input_budget']}, "
            f"recent_causal_groups={budget['recent_causal_group_caps']}。"
            "窗口这么大时 24 轮几乎不可能触发整组丢弃, A6-3/A6-4 会长期停在 INCONCLUSIVE; "
            "要证明有界性需把主模型换回 32000 窗口的模型再跑。"
        )
    if (
        budget["budget_tier_recorded"]
        and budget["budget_tier"] not in budget["budget_tier_recorded"]
    ):
        notes.append(
            f"budget_tier 交叉校验不一致: 由 window 推导 {budget['budget_tier']}, "
            f"receipts 记录 {budget['budget_tier_recorded']}。"
        )
    notes.append(
        "sdk_context_public_snapshots 按 plan 第 0 节未被用作任何判定依据(该表在当前构建为空)。"
    )
    if not strict:
        notes.append(
            "A6-11 默认区分「图谱结构性标识」与「memory_id 在 tool 回执中的回声」; "
            "加 --strict-a6-11 可回到 plan 字面的一刀切命中判定。"
        )
    for db in (ev.hm, ev.state, ev.audit, ev.exec):
        if db.error:
            notes.append(db.error)
    return {
        "schema": "a6-verify-v1",
        "evidence_root": ev.root,
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "budget": budget,
        "context_triples": context_triples(ev),
        "turns_recorded": ev.max_turn_recorded(),
        "timeout_turns": ev.timeout_turns(),
        "replay": replay_info,
        "sha256": ev.hashes(),
        "items": [i.to_json() for i in items],
        "tally": tally,
        "schema_notes": notes,
    }


# ---------------------------------------------------------------- selftest


def selftest() -> int:
    """在内存 sqlite 上建最小表集, 确认每个 item 函数都能跑通并给出判定。"""
    import tempfile

    root = tempfile.mkdtemp(prefix="a6-selftest-")
    data = os.path.join(root, "userdata", "data", "simple-harness-sdk")
    os.makedirs(data, exist_ok=True)
    parent = os.path.dirname(data)

    def make(path: str, ddl: Sequence[str], rows: Sequence[tuple[str, Sequence[Any]]] = ()) -> None:
        conn = sqlite3.connect(path)
        for stmt in ddl:
            conn.execute(stmt)
        for sql, params in rows:
            conn.execute(sql, params)
        conn.commit()
        conn.close()

    req = {"messages": [{"role": "system", "content": "x"}], "tools": [], "metadata": {}}
    req_text = json.dumps(req, ensure_ascii=False)
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
            (
                "insert into provider_invocations values (?,?,?,?,?,?,?,?,?)",
                ("inv1", "run1", "req1", "fp1", req_text, "succeeded", 100.0, "{}", "{}"),
            )
        ],
    )
    make(
        os.path.join(parent, "state.db"),
        [
            "create table foreground_run_heads(host_run_id text primary key, current_state text,"
            " sdk_run_id text, updated_at real)",
            "create table run_context_snapshot_receipts(snapshot_id text primary key,"
            " sdk_run_id text, provider_turn_ordinal integer, source_revisions_json text,"
            " payload_hash text, expected_request_fingerprint text, recorded_at real)",
            "create table sdk_provider_attempt_audit(invocation_id text primary key,"
            " snapshot_id text, model_id text, provider_id text, context_window integer,"
            " effective_ceiling integer, input_tokens integer, total_tokens integer,"
            " state text, settled_at real)",
            "create table context_route_decisions(decision_id text primary key,"
            " sdk_run_id text, route text, origin text, recorded_at real)",
            "create table task_scope_read_view_revisions(view_revision_id text primary key,"
            " task_scope_id text, view_kind text, content blob, created_at real)",
            "create table task_scope_events(event_id text primary key, task_scope_id text)",
        ],
        [
            (
                "insert into foreground_run_heads values (?,?,?,?)",
                ("h1", "COMPLETED", "run1", 100.0),
            ),
            (
                "insert into run_context_snapshot_receipts values (?,?,?,?,?,?,?)",
                (
                    "ctx-snap:run1:1",
                    "run1",
                    1,
                    json.dumps({"source_revisions": {"budget_tier": 8192, "causal_groups": 1, "trimmed_groups": 0}}),
                    "fp1",
                    "fp1",
                    100.0,
                ),
            ),
            (
                "insert into sdk_provider_attempt_audit values (?,?,?,?,?,?,?,?,?,?)",
                ("inv1", "sdk-context:x", "gpt-5.6-luna", "primary", 0, 0, 100, 120, "succeeded", 100.0),
            ),
        ],
    )
    make(
        os.path.join(parent, "human_memory_v7.db"),
        [
            "create table cognitive_relations(relation_id text primary key, plan_id text,"
            " plan_hash text, relation_kind text, relation_memory_id text,"
            " source_memory_id text, source_revision integer, target_memory_id text,"
            " target_revision integer, relation_hash text, created_at real)",
            "create table cognitive_memory_heads(memory_id text primary key, current_revision integer)",
            "create table cognitive_memory_revisions(memory_id text, revision integer,"
            " plan_id text, plan_hash text, lifecycle_state text, conflict_status text)",
            "create table cognitive_conflict_groups(group_id text primary key, memory_id text,"
            " incumbent_revision integer, challenger_revision integer)",
            "create table suppression_directives(directive_id text primary key)",
            "create table suppression_targets(directive_id text, ordinal integer,"
            " target_kind text, target_ref text)",
            "create table prospective_records(memory_id text, revision integer)",
            "create table prospective_scheduler_registrations(memory_id text)",
            "create table evidence_envelopes(evidence_id text primary key, sanitized_payload blob)",
        ],
    )
    make(os.path.join(parent, "operation-audit.db"), ["create table audit_attempts(id text)"])
    with open(os.path.join(root, "native.log"), "w", encoding="utf-8") as fh:
        fh.write('{"event": "model_context_resolved model=gpt-5.6-luna window=32000 source=builtin"}\n')
        for k in ("preparing", "staged", "consumed"):
            fh.write('{"event": "context.%s"}\n' % k)
    with open(os.path.join(root, "a6-progress.jsonl"), "w", encoding="utf-8") as fh:
        for turn in range(0, 3):
            fh.write(
                json.dumps(
                    {
                        "ts": "2026-09-08T10:0%d:00+0800" % turn,
                        "turn": turn,
                        "outcome": "settled" if turn else "baseline",
                        "provider_invocations": turn,
                        "cognitive_relations": 0,
                    }
                )
                + "\n"
            )

    ev = Evidence(root)
    try:
        items = run_items(ev, None, False)
        assert len(items) == len(ORDER), f"item 数不符: {len(items)}"
        for item in items:
            assert item.verdict in {PASS, FAIL, BLOCKED, INCONCLUSIVE}, item
            assert item.reason, f"{item.key} 无 reason"
        report = build_report(ev, None, {"imported": False}, False)
        md = render_markdown(report)
        assert "| 项 | 判定 | 依据数字 | 说明 |" in md
        json.dumps(report, ensure_ascii=False)
    finally:
        ev.close()
    print("selftest: OK —— %d 个判定项全部可执行, 报告可渲染 (临时库 %s)" % (len(ORDER), root))
    return 0


# ---------------------------------------------------------------- CLI


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HM-TO-A6 ContextSnapshot 后置校验器")
    ap.add_argument("--evidence", help="证据目录 <E>")
    ap.add_argument("--installed-target", help="含已安装 simple_harness 包的目录")
    ap.add_argument("--out", help="JSON 输出路径, 默认 <E>/a6-verify.json")
    ap.add_argument("--json-only", action="store_true", help="只打印 JSON, 不打印 Markdown 表")
    ap.add_argument(
        "--strict-a6-11",
        action="store_true",
        help="A6-11 按 plan 字面: 任何 memory_id 命中即 FAIL(默认区分 tool 回执回声)",
    )
    ap.add_argument("--selftest", action="store_true", help="内存 sqlite 自检")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.evidence:
        ap.error("--evidence 必填(或使用 --selftest)")

    replay, replay_info = build_replayer(args.installed_target)
    ev = Evidence(args.evidence)
    try:
        report = build_report(ev, replay, replay_info, args.strict_a6_11)
    finally:
        ev.close()

    out_path = args.out or os.path.join(ev.root, "a6-verify.json")
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
