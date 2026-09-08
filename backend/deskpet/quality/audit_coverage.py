"""HM-AC-7 全操作审计覆盖核对（只读证据副本）。

对一次真实运行的证据目录，把执行账本里观察到的每个 Host 操作（tool effect、
provider attempt、分析 apply、typed recall、TaskScope 变更、遗忘/suppression、
route 决策、context page-in、procedure 调用/绑定、prospective/current-input
读取、准备期拒绝、终态 Run、turn ingestion）与"应当存在"的审计记录逐条交叉，
按操作种类给出 observed / audited / missing（含 id），并标注：

* 该种类经哪个受控审计读取面可达（S6 Task 4 ``primary.audit.page`` 的 OA1
  family、Host ``audit_pages``、Host ``memory_call_attempts`` journal、仅日志）；
* 审计标识是否泄漏进普通业务库（普通对话面与审计面分权）。

本模块不判定 PASS/FAIL 的业务语义，只产出覆盖事实；证据 DB 先复制再打开，
从不写原始证据。id 只截断展示，不输出任何 payload 正文。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

MISSING_LIMIT = 24
ID_WIDTH = 28

# S6 Task 4 受控审计面（primary.audit.open/page/close）分页枚举的 OA1 family → SDK 表。
# 与 simple_harness_memory.backends.operation_audit._SPECS 顺序一致（测试守护漂移）。
OA1_FAMILY_TABLES: tuple[tuple[str, str], ...] = (
    ("mutation_commit", "memory_mutation_receipts"),
    ("mutation_rejection", "memory_mutation_rejection_audits"),
    ("typed_request", "typed_recall_requests"),
    ("typed_attempt", "typed_recall_attempts"),
    ("typed_terminal", "typed_recall_terminals"),
    ("recall_context_use", "recall_context_use_receipts"),
    ("short_recall", "short_horizon_audit"),
    ("suppression", "suppression_directives"),
    ("job_transition", "job_attempt_events"),
)

# Host 普通对话面操作（human_memory_api）；与 HUMAN_AUDIT_OPERATIONS 必须不相交。
ORDINARY_FACE_OPERATIONS = frozenset(
    {
        "primary.open", "primary.state", "primary.append", "primary.messages.page",
        "primary.messages.detail", "primary.memory.list", "primary.memory.graph",
        "primary.memory.forget", "primary.decisions.list", "primary.decisions.respond",
        "primary.bindings.pending", "primary.bindings.status", "primary.bindings.decide",
        "queue.enqueue", "queue.control", "task_scope.list", "task_scope.view",
        "task_scope.search", "task_scope.open_exact", "task_scope.create",
        "task_scope.mutate", "task_scope.evidence_page", "task_scope.evidence_groups",
        "disclosure.current", "disclosure.configure", "binding.append",
        "binding.manual.propose", "binding.manual.decide", "recovery.manifest",
        "recovery.emergency_export", "audit.refs",
    }
)

PROCEDURE_CALLERS = (
    "discover_procedure_drafts", "prepare_procedure_observation",
    "read_procedure_use_target", "record_procedure_observation",
)
PROSPECTIVE_CALLERS = (
    "prospective_source_read", "prospective_source_read_v2",
    "prospective_invalidation_settlement",
)
CURRENT_INPUT_CALLER = "current_input_visibility"

SURFACE_OA1 = "受控面 primary.audit.page（OA1 family: {families}）"
SURFACE_PAGES = "Host audit_pages（终态 Run 审计页；仅进程内 AuditStore.inspect，无 primary.audit.* 入口）"
SURFACE_JOURNAL = "Host memory_call_attempts（仅进程内 journal.page()，无 primary.audit.* 入口）"
SURFACE_HOST_LEDGER = "Host 业务账本自身（无独立审计读取面）"
SURFACE_LOG = "仅 native.log（_AuditSink → logger.info，非持久）"


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def audit_hash(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def audit_reference(kind: str, value: str | None) -> str | None:
    """SDK ``simple_harness.execution.audit.audit_reference`` 的独立实现。"""
    return None if value is None else kind + ":" + audit_hash([kind, value])


def short(value: object) -> str:
    text = str(value)
    return text if len(text) <= ID_WIDTH else text[:ID_WIDTH] + "…"


class EvidenceError(RuntimeError):
    pass


class Evidence:
    """证据目录的只读副本；缺表/缺库不抛错，记录到 ``missing_tables``。"""

    DATABASES = {
        "state": "state.db",
        "memory": "human_memory_v7.db",
        "audit": "operation-audit.db",
        "execution": "simple-harness-sdk/execution-v6.sqlite3",
    }

    def __init__(self, root: Path, workdir: Path | None = None) -> None:
        self.root = Path(root)
        self.data_dir = self._resolve(self.root)
        self.workdir = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="audit-coverage-"))
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.missing_tables: list[str] = []
        self.missing_databases: list[str] = []
        self._db: dict[str, sqlite3.Connection] = {}
        for name, relative in self.DATABASES.items():
            source = self.data_dir / relative
            if not source.is_file():
                self.missing_databases.append(relative)
                continue
            target = self.workdir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            for suffix in ("", "-wal", "-shm"):
                if (source.parent / (source.name + suffix)).is_file():
                    shutil.copy2(source.parent / (source.name + suffix), target.parent / (target.name + suffix))
            db = sqlite3.connect(target)
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.DatabaseError:
                pass
            db.execute("PRAGMA query_only=ON")
            self._db[name] = db
        candidates = [self.root, self.data_dir.parent.parent]
        self.native_log = next((c / "native.log" for c in candidates if (c / "native.log").is_file()), None)

    @staticmethod
    def _resolve(root: Path) -> Path:
        for candidate in (root, root / "userdata" / "data", root / "data"):
            if (candidate / "state.db").is_file():
                return candidate
        raise EvidenceError(f"evidence dir has no state.db: {root}")

    def close(self) -> None:
        for db in self._db.values():
            db.close()
        self._db.clear()

    def has_table(self, database: str, table: str) -> bool:
        db = self._db.get(database)
        if db is None:
            return False
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

    def rows(self, database: str, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        db = self._db.get(database)
        if db is None:
            return []
        try:
            return [dict(r) for r in db.execute(sql, params)]
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                table = str(exc).split(":", 1)[-1].strip()
                key = f"{database}:{table}"
                if key not in self.missing_tables:
                    self.missing_tables.append(key)
                return []
            raise

    def count(self, database: str, table: str) -> int | None:
        if not self.has_table(database, table):
            return None
        return self.rows(database, f'SELECT count(*) AS n FROM "{table}"')[0]["n"]

    def tables(self, database: str) -> list[str]:
        return [r["name"] for r in self.rows(database, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


@dataclass
class KindResult:
    kind: str
    label: str
    contract: str
    ledger: str
    audit: str
    surface: str
    observed: int = 0
    audited: int = 0
    missing: list[str] = field(default_factory=list)
    missing_total: int = 0
    notes: list[str] = field(default_factory=list)
    partial: bool = False
    informational: bool = False

    def miss(self, identity: object, reason: str) -> None:
        self.missing_total += 1
        if len(self.missing) < MISSING_LIMIT:
            self.missing.append(f"{short(identity)} ({reason})")

    @property
    def status(self) -> str:
        if self.observed == 0:
            return "not_observed"
        if self.missing_total:
            return "gap"
        if self.informational:
            return "informational"
        return "partial" if self.partial else "covered"

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status
        return data


class PageIndex:
    """终态 Run 审计页（Host audit_pages）的按 Run 索引。"""

    def __init__(self, ev: Evidence) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.ops: dict[str, dict[str, dict[str, Any]]] = {}
        self.names: dict[str, Counter] = {}
        for job in ev.rows("audit", "SELECT job_id,sdk_run_id,terminal_ref,terminal_hash,status,last_code,total_operations,processed_operations FROM audit_jobs"):
            self.jobs[job["sdk_run_id"]] = job
            ops = self.ops.setdefault(job["sdk_run_id"], {})
            names = self.names.setdefault(job["sdk_run_id"], Counter())
            for page in ev.rows("audit", "SELECT payload_json FROM audit_pages WHERE job_id=? ORDER BY page_index", (job["job_id"],)):
                try:
                    operations = json.loads(page["payload_json"])["operations"]
                except (ValueError, KeyError, TypeError):
                    continue
                for op in operations:
                    names[(op.get("kind"), op.get("operation_name"), op.get("state"))] += 1
                    key = op.get("operation_id")
                    if op.get("record_type") == "head" or key not in ops:
                        ops[key] = op

    def enumerated(self, run_id: str) -> bool:
        job = self.jobs.get(run_id)
        return job is not None and job["status"] == "enumerated"

    def head(self, run_id: str, operation_id: str | None) -> dict[str, Any] | None:
        op = self.ops.get(run_id, {}).get(operation_id)
        return op if op is not None and op.get("record_type") == "head" else None

    def op(self, run_id: str, operation_id: str | None) -> dict[str, Any] | None:
        return self.ops.get(run_id, {}).get(operation_id)

    def boundary(self, run_id: str, operation_name: str, state: str = "completed") -> int:
        return self.names.get(run_id, Counter()).get(("runtime", operation_name, state), 0)

    def page_reason(self, run_id: str) -> str:
        job = self.jobs.get(run_id)
        if job is None:
            return "run 无 audit_job"
        if job["status"] != "enumerated":
            return f"audit_job 未枚举完成 status={job['status']} code={job['last_code']}"
        return "审计页无对应 head"


CheckFn = Callable[[Evidence, PageIndex], KindResult]
REGISTRY: list[tuple[str, CheckFn]] = []


def register(kind: str) -> Callable[[CheckFn], CheckFn]:
    def wrap(fn: CheckFn) -> CheckFn:
        REGISTRY.append((kind, fn))
        return fn
    return wrap


def _oa1(*families: str) -> str:
    return SURFACE_OA1.format(families=",".join(families))


@register("run_terminal")
def check_run_terminal(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("run_terminal", "终态 Run（每次前台执行）", "HM-AC-7 全链路；S6 Task 4",
                   "state.foreground_terminal_receipts", "operation-audit.audit_jobs(enumerated)+audit_pages；task_scope_events harness.run_terminal（有 scope 时）",
                   SURFACE_PAGES)
    jobs = {(j["terminal_ref"], j["terminal_hash"]): j for j in ev.rows("audit", "SELECT terminal_ref,terminal_hash,status,last_code FROM audit_jobs")}
    scoped = {x["sdk_run_id"] for x in ev.rows("state", "SELECT t.sdk_run_id FROM foreground_terminal_receipts t JOIN foreground_runs r ON r.host_run_id=t.host_run_id WHERE r.task_scope_id IS NOT NULL")}
    terminal_events = {x["source_event_id"] for x in ev.rows("state", "SELECT source_event_id FROM task_scope_events WHERE event_kind='harness.run_terminal'")}
    states: Counter = Counter()
    for t in ev.rows("state", "SELECT terminal_receipt_id,sdk_run_id,terminal_state,receipt_hash FROM foreground_terminal_receipts"):
        r.observed += 1
        states[t["terminal_state"]] += 1
        job = jobs.get((t["terminal_receipt_id"], t["receipt_hash"]))
        reasons = []
        if job is None:
            reasons.append("无 audit_job")
        elif job["status"] != "enumerated":
            reasons.append(f"status={job['status']} code={job['last_code']}")
        if t["sdk_run_id"] in scoped and f"execution:{t['sdk_run_id']}" not in terminal_events:
            reasons.append("scope 内无 harness.run_terminal 事件")
        if reasons:
            r.miss(t["terminal_receipt_id"], "; ".join(reasons))
        else:
            r.audited += 1
    r.notes.append("终态分布 " + ", ".join(f"{k}={v}" for k, v in sorted(states.items())))
    rejections = ev.count("audit", "audit_source_rejections")
    if rejections:
        r.notes.append(f"audit_source_rejections={rejections}（来源绑定无效的终态被拒绝进入审计）")
    return r


def _effects(ev: Evidence, where: str = "", params: tuple = ()) -> list[dict[str, Any]]:
    return ev.rows("execution", "SELECT effect_id,run_id,tool_name,state FROM execution_effects " + where, params)


def _effect_reasons(pages: PageIndex, e: dict[str, Any], identities: set[str] | None = None) -> list[str]:
    reasons = []
    head = pages.head(e["run_id"], audit_reference("effect", e["effect_id"]))
    if head is None:
        reasons.append(pages.page_reason(e["run_id"]))
    else:
        if head.get("state") != e["state"]:
            reasons.append(f"审计头 state={head.get('state')}≠账本 {e['state']}")
        if e["state"] in {"failed", "rejected"} and not (head.get("error_code") or head.get("error_code_hash")):
            reasons.append("失败 effect 审计头无 error_code")
    if identities is not None and e["effect_id"] not in identities:
        reasons.append("primary_effect_identities 缺")
    return reasons


@register("tool_effect")
def check_tool_effect(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("tool_effect", "工具 effect（每次 tool 调用）", "HM-AC-7 tool/TaskScope 链路",
                   "execution.execution_effects", "audit_pages effect head（operation_id=audit_reference）+ state.primary_effect_identities",
                   SURFACE_PAGES)
    identities = {x["effect_id"] for x in ev.rows("state", "SELECT effect_id FROM primary_effect_identities")}
    per_tool: dict[str, list[int]] = {}
    for e in _effects(ev):
        r.observed += 1
        stat = per_tool.setdefault(e["tool_name"], [0, 0])
        stat[0] += 1
        reasons = _effect_reasons(pages, e, identities)
        if reasons:
            r.miss(f"{e['effect_id']}/{e['tool_name']}", "; ".join(reasons))
        else:
            r.audited += 1
            stat[1] += 1
    r.notes.append("按工具 observed/audited: " + ", ".join(f"{k}={v[0]}/{v[1]}" for k, v in sorted(per_tool.items())))
    return r


@register("provider_attempt")
def check_provider_attempt(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("provider_attempt", "Provider 调用（每次真实发送）", "HM-AC-7 model/Token/费用/延迟",
                   "execution.provider_invocations", "audit_pages provider head(usage) + state.sdk_provider_attempt_audit（Host 结算 token）",
                   SURFACE_PAGES)
    host = {x["invocation_id"]: x for x in ev.rows("state", "SELECT invocation_id,state,usage_available,total_tokens FROM sdk_provider_attempt_audit")}
    states: Counter = Counter()
    for inv in ev.rows("execution", "SELECT invocation_id,run_id,state FROM provider_invocations"):
        r.observed += 1
        states[inv["state"]] += 1
        reasons = []
        head = pages.head(inv["run_id"], audit_reference("provider", inv["invocation_id"]))
        if head is None:
            reasons.append(pages.page_reason(inv["run_id"]))
        elif inv["state"] == "succeeded" and not head.get("usage"):
            reasons.append("审计头无 usage")
        settlement = host.get(inv["invocation_id"])
        if settlement is None:
            reasons.append("Host sdk_provider_attempt_audit 缺")
        elif inv["state"] == "succeeded" and (not settlement["usage_available"] or settlement["total_tokens"] is None):
            reasons.append("Host 结算无 token")
        if reasons:
            r.miss(inv["invocation_id"], "; ".join(reasons))
        else:
            r.audited += 1
    r.notes.append("状态分布 " + ", ".join(f"{k}={v}" for k, v in sorted(states.items())))
    return r


@register("context_snapshot")
def check_context_snapshot(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("context_snapshot", "ContextSnapshot 冻结（每次发送的上下文）", "HM-AC-6 可审计 ContextSnapshot；HM-AC-7 输入 hash",
                   "execution.provider_invocations（request_fingerprint）", "state.run_context_snapshot_receipts（expected_request_fingerprint 同 run 匹配）",
                   SURFACE_HOST_LEDGER)
    receipts = {(x["sdk_run_id"], x["expected_request_fingerprint"]) for x in ev.rows("state", "SELECT sdk_run_id,expected_request_fingerprint FROM run_context_snapshot_receipts")}
    matched = set()
    for inv in ev.rows("execution", "SELECT invocation_id,run_id,request_fingerprint FROM provider_invocations"):
        r.observed += 1
        key = (inv["run_id"], inv["request_fingerprint"])
        if key in receipts:
            r.audited += 1
            matched.add(key)
        else:
            r.miss(inv["invocation_id"], "无匹配 fingerprint 的 snapshot 回执")
    unsent = len(receipts - matched)
    if unsent:
        r.notes.append(f"{unsent} 个 snapshot 回执没有对应的真实发送（冻结后未发送，非缺口）")
    scoped = ev.count("state", "task_scope_events")
    if scoped is not None:
        n = ev.rows("state", "SELECT count(*) AS n FROM task_scope_events WHERE event_kind='harness.context_snapshot'")[0]["n"]
        r.notes.append(f"TaskScope 账本内 harness.context_snapshot 事件 {n} 条（scope 绑定的发送）")
    return r


@register("route_decision")
def check_route_decision(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("route_decision", "Context route 决策（Recall need / TaskScope route）", "HM-AC-7 Recall need、TaskScope route",
                   "state.context_route_decisions", "context_tool→context_route_tool_invocations + effect 审计头；no_recall→audit_pages runtime context.no_recall；有 scope→task_scope_events harness.route_decision",
                   SURFACE_PAGES + "；scope 事件经 task_scope.evidence_page（普通执行面，非审计面）")
    invocations = {x["decision_id"]: x for x in ev.rows("state", "SELECT decision_id,effect_id,verdict FROM context_route_tool_invocations")}
    scope_events = {x["source_event_id"] for x in ev.rows("state", "SELECT source_event_id FROM task_scope_events WHERE event_kind='harness.route_decision'")}
    routes: Counter = Counter()
    for d in ev.rows("state", "SELECT decision_id,sdk_run_id,route,origin,task_scope_id,effect_id FROM context_route_decisions"):
        r.observed += 1
        routes[(d["route"], d["origin"])] += 1
        reasons = []
        if d["origin"] == "context_tool":
            if d["decision_id"] not in invocations:
                reasons.append("无 context_route_tool_invocations")
            if pages.head(d["sdk_run_id"], audit_reference("effect", d["effect_id"])) is None:
                reasons.append("route effect " + pages.page_reason(d["sdk_run_id"]))
        elif not pages.boundary(d["sdk_run_id"], "context.no_recall"):
            reasons.append("run 审计页无 context.no_recall 边界" if pages.enumerated(d["sdk_run_id"]) else pages.page_reason(d["sdk_run_id"]))
        if d["task_scope_id"] and f"execution:route:{d['decision_id']}" not in scope_events:
            reasons.append("scope 内无 harness.route_decision 事件")
        if reasons:
            r.miss(d["decision_id"], "; ".join(reasons))
        else:
            r.audited += 1
    r.notes.append("route/origin 分布 " + ", ".join(f"{k[0]}/{k[1]}={v}" for k, v in sorted(routes.items())))
    return r


def _tool_kind(ev: Evidence, pages: PageIndex, r: KindResult, like: str) -> None:
    for e in _effects(ev, "WHERE tool_name LIKE ?", (like,)):
        r.observed += 1
        reasons = _effect_reasons(pages, e)
        if reasons:
            r.miss(f"{e['effect_id']}/{e['tool_name']}/{e['state']}", "; ".join(reasons))
        else:
            r.audited += 1


@register("context_page_in")
def check_context_page_in(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("context_page_in", "Context page-in（受控引用换入 TaskScope 内容）", "HM-AC-6 受控引用 page-in；HM-AC-7",
                   "execution.execution_effects(tool_name=context_page_in)", "audit_pages effect head（参数/结果 hash）；Host 无持久 page-in 回执（ContextPageInStore 为进程内 TTL 存储）",
                   SURFACE_PAGES)
    _tool_kind(ev, pages, r, "context_page_in")
    if r.observed:
        r.partial = True
        r.notes.append("仅 effect 级审计：Host 侧 page-in 引用发放/消费无持久回执（followup）")
    return r


@register("task_scope_search_open")
def check_task_scope_search_open(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("task_scope_search_open", "TaskScope search/open（tool 路径）", "HM-AC-1 普通 search/open 遵守 suppression；HM-AC-7",
                   "execution.execution_effects(tool_name LIKE task_scope_search/open)", "audit_pages effect head + state.task_scope_search_access_receipts（按 operation 计数，回执不含 effect/run 引用）",
                   SURFACE_PAGES)
    _tool_kind(ev, pages, r, "task_scope_search")
    _tool_kind(ev, pages, r, "task_scope_open%")
    receipts = Counter(x["operation"] for x in ev.rows("state", "SELECT operation FROM task_scope_search_access_receipts"))
    if receipts:
        r.notes.append("Host 访问回执 " + ", ".join(f"{k}={v}" for k, v in sorted(receipts.items())) + "（无法逐条关联到 effect：receipt_json 无 effect_id/sdk_run_id，followup）")
        r.partial = r.observed > 0
    return r


@register("task_scope_mutation")
def check_task_scope_mutation(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("task_scope_mutation", "TaskScope 变更（mutation plan）", "HM-AC-7 TaskScope append-only ledger/canonical state",
                   "state.task_scope_mutation_attempts", "task_scope_mutation_decisions(plan_id) + task_scope_events mutation.plan（source_event_id=mutation-plan:<plan_id>）",
                   SURFACE_HOST_LEDGER + "；task_scope.evidence_page 可读事件")
    decisions = {x["plan_id"]: x["outcome"] for x in ev.rows("state", "SELECT plan_id,outcome FROM task_scope_mutation_decisions")}
    events = {x["source_event_id"] for x in ev.rows("state", "SELECT source_event_id FROM task_scope_events WHERE event_kind='mutation.plan'")}
    for a in ev.rows("state", "SELECT attempt_id,plan_id,result FROM task_scope_mutation_attempts"):
        r.observed += 1
        reasons = []
        if a["plan_id"] not in decisions:
            reasons.append("无 mutation decision")
        if f"mutation-plan:{a['plan_id']}" not in events:
            reasons.append("无 mutation.plan 事件")
        if reasons:
            r.miss(a["attempt_id"], "; ".join(reasons))
        else:
            r.audited += 1
    effects = Counter(e["state"] for e in _effects(ev, "WHERE tool_name='task_scope_update'"))
    if effects:
        pre = sum(v for k, v in effects.items() if k != "succeeded")
        r.notes.append("task_scope_update effect 状态 " + ", ".join(f"{k}={v}" for k, v in sorted(effects.items())) + (f"；{pre} 个未进入 attempt（前置拒绝，仅 effect 审计）" if pre else ""))
    return r


@register("taskscope_event_ledger")
def check_taskscope_event_ledger(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("taskscope_event_ledger", "TaskScope 事件账本（harness 事实 ingest）", "HM-AC-7 raw evidence + append-only event ledger",
                   "state.task_scope_execution_ingest_receipts", "task_scope_events（event_id 存在）",
                   SURFACE_HOST_LEDGER + "；task_scope.evidence_page")
    events = {x["event_id"] for x in ev.rows("state", "SELECT event_id FROM task_scope_events")}
    for x in ev.rows("state", "SELECT receipt_id,event_id,evidence_kind FROM task_scope_execution_ingest_receipts"):
        r.observed += 1
        if x["event_id"] in events:
            r.audited += 1
        else:
            r.miss(x["receipt_id"], f"回执 event_id 不在 task_scope_events（{x['evidence_kind']}）")
    kinds = Counter(x["event_kind"] for x in ev.rows("state", "SELECT event_kind FROM task_scope_events"))
    if kinds:
        r.notes.append("事件种类 " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    return r


@register("memory_analysis_apply")
def check_memory_analysis_apply(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("memory_analysis_apply", "记忆分析 LLM 调用与 apply（turn 后异步）", "HM-AC-2/7 invocation evidence + decision record；S2 Task 4/5 phase 链",
                   "state.post_turn_invocation_attempts(purpose=analysis)", "memory.llm_invocations(request_hash) + analysis_batches + job_attempt_events(provider_handoff…applied/dead_letter) + memory_mutation_receipts/decision_records（plan 已 apply 时）",
                   _oa1("job_transition", "mutation_commit", "mutation_rejection"))
    invocations = {x["request_hash"]: x for x in ev.rows("memory", "SELECT invocation_id,request_hash,output_reason_code,input_tokens,output_tokens,latency_ms,public_output_json FROM llm_invocations")}
    batches = {x["request_hash"]: x for x in ev.rows("memory", "SELECT batch_id,request_hash,state FROM analysis_batches")}
    events: dict[str, set[str]] = {}
    for x in ev.rows("memory", "SELECT batch_id,event_kind FROM job_attempt_events"):
        events.setdefault(x["batch_id"], set()).add(x["event_kind"])
    receipts = {x["plan_id"] for x in ev.rows("memory", "SELECT plan_id FROM memory_mutation_receipts")}
    rejections = {x["plan_id"] for x in ev.rows("memory", "SELECT plan_id FROM memory_mutation_rejection_audits")}
    decisions = Counter(x["invocation_id"] for x in ev.rows("memory", "SELECT invocation_id FROM decision_records"))
    statuses: Counter = Counter()
    no_mutation_without_record = 0
    for a in ev.rows("state", "SELECT attempt_id,request_hash,status,plan_id,result_hash FROM post_turn_invocation_attempts WHERE purpose='analysis'"):
        r.observed += 1
        statuses[a["status"]] += 1
        reasons = []
        inv = invocations.get(a["request_hash"])
        batch = batches.get(a["request_hash"])
        outcome = None
        if inv is None:
            reasons.append("无 llm_invocations")
        else:
            if inv["input_tokens"] is None or inv["latency_ms"] is None:
                reasons.append("invocation 无 token/延迟")
            try:
                outcome = json.loads(inv["public_output_json"] or "{}").get("outcome")
            except (ValueError, AttributeError):
                outcome = None
        if batch is None:
            reasons.append("无 analysis_batches")
        else:
            kinds = events.get(batch["batch_id"], set())
            if "provider_handoff" not in kinds:
                reasons.append("job 事件无 provider_handoff")
            if batch["state"] == "applied":
                if "applied" not in kinds:
                    reasons.append("batch applied 但无 applied 事件")
                has_record = a["plan_id"] in receipts or a["plan_id"] in rejections
                if has_record and inv is not None and not decisions.get(inv["invocation_id"]):
                    reasons.append("已 apply 但无 decision_records")
                if not has_record and outcome == "no_mutation":
                    no_mutation_without_record += 1
                elif not has_record:
                    reasons.append(f"outcome={outcome} 但无 mutation receipt/rejection")
            elif not kinds & {"dead_letter", "application_rejected", "retry_scheduled", "authority_retry_scheduled", "result_divergent"}:
                reasons.append(f"batch {batch['state']} 无终态 job 事件")
        if reasons:
            r.miss(a["attempt_id"], "; ".join(reasons))
        else:
            r.audited += 1
    r.notes.append("Host attempt 状态 " + ", ".join(f"{k}={v}" for k, v in sorted(statuses.items())))
    r.notes.append(f"mutation receipts={len(receipts)} rejection audits={len(rejections)} decision_records={sum(decisions.values())}")
    if no_mutation_without_record:
        r.partial = True
        r.notes.append(f"{no_mutation_without_record} 次 outcome=no_mutation：有 llm_invocation + job applied，但无 decision_records / no_mutation receipt（结构化决策记录缺失，followup）")
    return r


def _typed_recall(ev: Evidence, pages: PageIndex, r: KindResult, caller: str, analysis: bool) -> KindResult:
    condition = "idempotency_key LIKE 'analysis-candidates-%'" if analysis else "idempotency_key NOT LIKE 'analysis-candidates-%'"
    requests = ev.rows("memory", f"SELECT request_id,request_json FROM typed_recall_requests WHERE {condition} ORDER BY created_at")
    terminals = {x["request_id"]: x for x in ev.rows("memory", "SELECT request_id,terminal_kind,result_hash,decision_hash FROM typed_recall_terminals")}
    journal = ev.rows("audit", "SELECT attempt_ref,state,observation_status,result_hash,settled_at,context_run_ref_hash FROM memory_call_attempts WHERE caller=? ORDER BY started_at", (caller,))
    findings = {x["operation_ref"] for x in ev.rows("audit", "SELECT operation_ref FROM memory_call_findings")}
    by_result = {j["result_hash"]: j for j in journal if j["result_hash"]}
    matched: set[str] = set()
    by_run_fallback = 0
    for req in requests:
        r.observed += 1
        t = terminals.get(req["request_id"])
        if t is None:
            r.miss(req["request_id"], "SDK 无 typed_recall_terminals")
            continue
        if t["result_hash"] in by_result:
            r.audited += 1
            matched.add(by_result[t["result_hash"]]["attempt_ref"])
            continue
        # Host 在 SDK 结算前自行 raise（超时等）时 result_hash 为空：退回按同 run 的
        # 未匹配、非 returned 的 journal 行一对一关联（journal 只存 run ref 的 digest）。
        try:
            run_ref = json.loads(req["request_json"])["context"].get("run_id")
        except (ValueError, KeyError, TypeError, AttributeError):
            run_ref = None
        run_hash = audit_hash(["host.memory.context.run.v1", run_ref]) if isinstance(run_ref, str) else None
        fallback = next((j for j in journal if j["context_run_ref_hash"] == run_hash and j["attempt_ref"] not in matched and j["state"] != "returned" and run_hash), None)
        if fallback is None:
            r.miss(req["request_id"], f"Host journal 无匹配 result_hash/run ref（terminal={t['terminal_kind']}）")
            continue
        matched.add(fallback["attempt_ref"])
        by_run_fallback += 1
        if fallback["settled_at"] is not None and (fallback["attempt_ref"] in findings or fallback["observation_status"] == "captured_bound"):
            r.audited += 1
        else:
            r.miss(req["request_id"], f"run ref 关联到 Host {fallback['state']} 但无 finding（terminal={t['terminal_kind']}）")
    if by_run_fallback:
        r.partial = True
        r.notes.append(f"{by_run_fallback} 条 SDK terminal 只能按 run ref 关联 Host journal（Host 先于 SDK 结算 raise，result_hash 为空；SDK 未持久 host attempt ref → 无法精确到 attempt，followup）")
    host_only = 0
    for j in journal:
        if j["attempt_ref"] in matched:
            continue
        r.observed += 1
        host_only += 1
        if j["state"] == "returned":
            r.miss(j["attempt_ref"], "Host returned 但 SDK 无对应 terminal")
        elif j["settled_at"] is None:
            r.miss(j["attempt_ref"], f"未结算 state={j['state']}")
        elif j["attempt_ref"] in findings:
            r.audited += 1
        else:
            r.miss(j["attempt_ref"], f"state={j['state']} 无 memory_call_findings")
    statuses = Counter((j["state"], j["observation_status"]) for j in journal)
    r.notes.append("Host journal state/observation " + ", ".join(f"{k[0]}/{k[1]}={v}" for k, v in sorted(statuses.items())))
    if host_only:
        r.notes.append(f"{host_only} 条 Host 侧 attempt 无 SDK terminal 可关联（raised/超时等，按 finding 判定）")
    return r


@register("typed_recall_foreground")
def check_typed_recall_foreground(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("typed_recall_foreground", "前台 typed recall（Recall need 后的召回）", "HM-AC-7 Recall；S2 Task 4",
                   "memory.typed_recall_requests（非 analysis-candidates）+ audit.memory_call_attempts(caller=foreground_recall)",
                   "typed_recall_terminals ↔ Host journal result_hash；raised 时 memory_call_findings",
                   _oa1("typed_request", "typed_attempt", "typed_terminal") + "；Host journal 仅进程内")
    return _typed_recall(ev, pages, r, "foreground_recall", analysis=False)


@register("typed_recall_analysis")
def check_typed_recall_analysis(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("typed_recall_analysis", "分析候选 typed recall（语义纠正候选查询）", "HM-AC-7 冲突/更新；S2 Task 4",
                   "memory.typed_recall_requests(analysis-candidates) + audit.memory_call_attempts(caller=analysis_candidates)",
                   "typed_recall_terminals ↔ Host journal result_hash；raised 时 memory_call_findings",
                   _oa1("typed_request", "typed_attempt", "typed_terminal") + "；Host journal 仅进程内")
    return _typed_recall(ev, pages, r, "analysis_candidates", analysis=True)


@register("recall_context_use")
def check_recall_context_use(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("recall_context_use", "召回结果进入 Context 的使用授权", "HM-AC-6/7 Context disclosure 可审计",
                   "execution.provider_context_use_receipt_bindings", "memory.recall_context_use_receipts(receipt_hash) + provider 审计头",
                   _oa1("recall_context_use"))
    receipts = {x["receipt_hash"] for x in ev.rows("memory", "SELECT receipt_hash FROM recall_context_use_receipts")}
    runs = {x["invocation_id"]: x["run_id"] for x in ev.rows("execution", "SELECT invocation_id,run_id FROM provider_invocations")}
    for b in ev.rows("execution", "SELECT receipt_id,receipt_hash,invocation_id FROM provider_context_use_receipt_bindings"):
        r.observed += 1
        reasons = []
        if b["receipt_hash"] not in receipts:
            reasons.append("SDK 无 recall_context_use_receipts")
        run = runs.get(b["invocation_id"])
        if run is None or pages.head(run, audit_reference("provider", b["invocation_id"])) is None:
            reasons.append("绑定的 provider 调用无审计头")
        if reasons:
            r.miss(b["receipt_id"], "; ".join(reasons))
        else:
            r.audited += 1
    return r


@register("short_horizon_recall")
def check_short_horizon_recall(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("short_horizon_recall", "短时域召回（SDK 内 lane）", "HM-AC-7 Recall；S2 Task 4",
                   "memory.short_horizon_audit(recall_started)", "同 query_hash 的 recall/recall_terminal 事件",
                   _oa1("short_recall"))
    r.informational = True
    rows = ev.rows("memory", "SELECT audit_id,event_kind,query_hash,created_at FROM short_horizon_audit ORDER BY created_at")
    terminals = {(x["query_hash"]) for x in rows if x["event_kind"] in {"recall", "recall_terminal"}}
    for x in rows:
        if x["event_kind"] != "recall_started":
            continue
        r.observed += 1
        if x["query_hash"] in terminals:
            r.audited += 1
        else:
            r.miss(x["audit_id"], "无 recall 终态事件")
    others = Counter(x["event_kind"] for x in rows if x["event_kind"] not in {"recall_started", "recall", "recall_terminal"})
    if others:
        r.notes.append("其他事件 " + ", ".join(f"{k}={v}" for k, v in sorted(others.items())))
    return r


@register("forget_suppression")
def check_forget_suppression(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("forget_suppression", "遗忘 / suppression", "HM-AC-1 逻辑遗忘；S2 Task 3；HM-S7",
                   "state.memory_action_events(suppression_request_id) + execution_effects(tool_name=memory_forget)",
                   "memory.suppression_directives(request_id) ；memory_forget effect 审计头（工具按 DECISION-MEMORY-FORGET-TOOL 关闭，预期 failed）",
                   _oa1("suppression"))
    directives = {x["request_id"] for x in ev.rows("memory", "SELECT request_id FROM suppression_directives")}
    for a in ev.rows("state", "SELECT action_id,phase,request_json FROM memory_action_events"):
        try:
            payload = json.loads(a["request_json"] or "{}")
        except ValueError:
            payload = {}
        sid = payload.get("suppression_request_id") if isinstance(payload, dict) else None
        if sid is None and isinstance(payload, dict):
            nested = payload.get("action") or payload.get("request") or {}
            sid = nested.get("suppression_request_id") if isinstance(nested, dict) else None
        if sid is None:
            continue
        r.observed += 1
        if sid in directives:
            r.audited += 1
        else:
            r.miss(a["action_id"], f"phase={a['phase']} 无 suppression_directives")
    _tool_kind(ev, pages, r, "memory_forget")
    r.notes.append(f"suppression_directives={len(directives)}")
    return r


def _journal_kind(ev: Evidence, r: KindResult, callers: tuple[str, ...]) -> None:
    findings = {x["operation_ref"] for x in ev.rows("audit", "SELECT operation_ref FROM memory_call_findings")}
    statuses: Counter = Counter()
    marks = ",".join("?" * len(callers))
    for j in ev.rows("audit", f"SELECT attempt_ref,caller,state,observation_status,settled_at FROM memory_call_attempts WHERE caller IN ({marks})", callers):
        r.observed += 1
        statuses[(j["caller"], j["state"], j["observation_status"])] += 1
        if j["settled_at"] is None:
            r.miss(j["attempt_ref"], f"{j['caller']} 未结算 state={j['state']}")
        elif j["state"] == "returned" and j["observation_status"] == "captured_bound":
            r.audited += 1
        elif j["state"] != "returned" and (j["observation_status"] in {"captured_bound", "not_invoked"} or j["attempt_ref"] in findings):
            r.audited += 1
        else:
            r.miss(j["attempt_ref"], f"{j['caller']} state={j['state']} observation={j['observation_status']}")
    if statuses:
        r.notes.append("journal caller/state/observation " + ", ".join(f"{k[0]}/{k[1]}/{k[2]}={v}" for k, v in sorted(statuses.items())))


@register("procedure_operation")
def check_procedure_operation(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("procedure_operation", "Procedure SDK 调用（discover/prepare/read/record）", "HM-AC-5/7 Procedure",
                   "audit.memory_call_attempts(caller∈procedure ops) + execution_effects(tool_name LIKE procedure_%)",
                   "journal 结算且 observation captured_bound（SDK ProcedureOperationObservationV1）；effect 审计头",
                   SURFACE_JOURNAL + "；OA1 无 procedure family（SDK followup）")
    _journal_kind(ev, r, PROCEDURE_CALLERS)
    before = r.observed
    _tool_kind(ev, pages, r, "procedure_%")
    effects = r.observed - before
    if effects:
        succeeded = sum(1 for e in _effects(ev, "WHERE tool_name LIKE 'procedure_%' AND state='succeeded'"))
        r.notes.append(f"procedure effect {effects} 个（succeeded={succeeded}）；journal 调用 {before} 条；差额为前置拒绝的 effect（仅 effect 审计）")
    if r.observed:
        r.partial = True
    return r


@register("procedure_bind_step")
def check_procedure_bind_step(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("procedure_bind_step", "Procedure 绑定与步骤执行", "HM-AC-5 适用性检查；HM-AC-7",
                   "state.procedure_uses + procedure_use_effects", "procedure_observation_journal(use_id) + 步骤 effect 审计头 + memory.procedure_observations",
                   SURFACE_JOURNAL + "；OA1 无 procedure family")
    phases: dict[str, set[str]] = {}
    for x in ev.rows("state", "SELECT use_id,phase FROM procedure_observation_journal"):
        phases.setdefault(x["use_id"], set()).add(x["phase"])
    steps: dict[str, list[dict[str, Any]]] = {}
    for x in ev.rows("state", "SELECT use_id,step_ordinal,effect_id FROM procedure_use_effects"):
        steps.setdefault(x["use_id"], []).append(x)
    for u in ev.rows("state", "SELECT use_id,sdk_run_id FROM procedure_uses"):
        r.observed += 1
        reasons = []
        if u["use_id"] not in phases:
            reasons.append("无 observation journal")
        for s in steps.get(u["use_id"], []):
            if pages.head(u["sdk_run_id"], audit_reference("effect", s["effect_id"])) is None:
                reasons.append(f"step{s['step_ordinal']} effect 无审计头")
        if reasons:
            r.miss(u["use_id"], "; ".join(reasons))
        else:
            r.audited += 1
    observations = ev.count("memory", "procedure_observations")
    if observations:
        r.notes.append(f"SDK procedure_observations={observations}")
    return r


@register("prospective_source_read")
def check_prospective_source_read(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("prospective_source_read", "Prospective outbox 源读取/失效结算", "HM-AC-5/7 Prospective 状态审计",
                   "audit.memory_call_attempts(caller∈prospective ops)", "journal 结算 + 捕获 SDK observation",
                   SURFACE_JOURNAL + "；OA1 无 prospective family")
    _journal_kind(ev, r, PROSPECTIVE_CALLERS)
    return r


@register("current_input_visibility")
def check_current_input_visibility(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("current_input_visibility", "当前输入可见性检查", "HM-AC-1 suppression 同步生效；HM-AC-7",
                   "audit.memory_call_attempts(caller=current_input_visibility)", "journal 结算 + 捕获 SDK observation",
                   SURFACE_JOURNAL)
    _journal_kind(ev, r, (CURRENT_INPUT_CALLER,))
    return r


@register("preparation_rejection")
def check_preparation_rejection(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("preparation_rejection", "准备期（pre-SDK）拒绝", "HM-AC-7 全链路（SDK 之前的失败也留痕）",
                   "state.foreground_run_transitions(idempotency_key=preparation-rejected:v1)", "audit.preparation_audit_sources(source_ref=transition_id:hash) + preparation_audit_findings",
                   "Host preparation_audit_sources（仅进程内读取，无 primary.audit.* 入口）")
    sources = {x["source_ref"]: x["source_status"] for x in ev.rows("audit", "SELECT source_ref,source_status FROM preparation_audit_sources")}
    for t in ev.rows("state", "SELECT transition_id,transition_hash FROM foreground_run_transitions WHERE idempotency_key='preparation-rejected:v1'"):
        r.observed += 1
        status = sources.get(f"{t['transition_id']}:{t['transition_hash']}")
        if status == "verified":
            r.audited += 1
        else:
            r.miss(t["transition_id"], "无 preparation_audit_sources" if status is None else f"source_status={status}")
    return r


@register("turn_ingestion")
def check_turn_ingestion(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("turn_ingestion", "committed turn 进入记忆（memory port / ingestion）", "HM-AC-1 永久证据；HM-AC-7",
                   "state.memory_ingestion_outbox（每 turn）", "memory.ingestion_receipts(evidence_id) + audit_pages memory_port op（identity=audit_hash([intent,run,payload_hash,created_at])）",
                   SURFACE_PAGES)
    receipts = {x["evidence_id"] for x in ev.rows("memory", "SELECT evidence_id FROM ingestion_receipts")}
    port: dict[str, dict[str, Any]] = {}
    for x in ev.rows("execution", "SELECT intent_id,run_id,payload_hash,created_at,state FROM memory_outbox"):
        port[x["run_id"]] = x
    terminal = {x["sdk_run_id"]: x["terminal_state"] for x in ev.rows("state", "SELECT sdk_run_id,terminal_state FROM foreground_terminal_receipts")}
    no_port: Counter = Counter()
    states: Counter = Counter()
    for o in ev.rows("state", "SELECT outbox_id,sdk_run_id,evidence_ids_json,state FROM memory_ingestion_outbox"):
        r.observed += 1
        states[o["state"]] += 1
        reasons = []
        try:
            evidence_ids = json.loads(o["evidence_ids_json"] or "[]")
        except ValueError:
            evidence_ids = []
        if o["state"] == "delivered":
            lacking = [e for e in evidence_ids if e not in receipts]
            if lacking:
                reasons.append(f"{len(lacking)} 个 evidence 无 ingestion_receipts")
        else:
            reasons.append(f"Host outbox state={o['state']}")
        intent = port.get(o["sdk_run_id"])
        if intent is not None:
            identity = audit_hash([intent["intent_id"], intent["run_id"], intent["payload_hash"], intent["created_at"]])
            if pages.op(o["sdk_run_id"], audit_reference("memory_port", identity)) is None:
                reasons.append("审计页无 memory_port 记录" if pages.enumerated(o["sdk_run_id"]) else pages.page_reason(o["sdk_run_id"]))
        else:
            no_port[terminal.get(o["sdk_run_id"], "UNKNOWN")] += 1
        if reasons:
            r.miss(o["outbox_id"], "; ".join(reasons))
        else:
            r.audited += 1
    r.notes.append("Host outbox 状态 " + ", ".join(f"{k}={v}" for k, v in sorted(states.items())))
    if no_port:
        r.notes.append("SDK memory_outbox 无对应 run 的 Host outbox（按 Run 终态）" + ", ".join(f"{k}={v}" for k, v in sorted(no_port.items())) + "；FAILED Run 不提交 memory port，Host 仍投递原始证据（符合 HM-AC-1）")
    return r


@register("runtime_log_events")
def check_runtime_log_events(ev: Evidence, pages: PageIndex) -> KindResult:
    r = KindResult("runtime_log_events", "前台运行时事件（_record_audit）", "HM-AC-7（记录性，非合同要求持久）",
                   "native.log foreground.runtime.*", "仅日志（ForegroundRuntimeAuditSink=logger.info）", SURFACE_LOG)
    r.informational = True
    if ev.native_log is None:
        r.notes.append("证据目录无 native.log")
        return r
    counts: Counter = Counter()
    pattern = re.compile(r"foreground\.runtime\.[a-z_]+")
    with ev.native_log.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            for m in pattern.findall(line):
                counts[m] += 1
    r.observed = sum(counts.values())
    r.audited = r.observed
    r.notes.append(", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "无 foreground.runtime.* 行")
    r.notes.append("这些事件只进日志，不进任何持久审计表；持久审计由 foreground_run_transitions/audit_pages 承担")
    return r


@dataclass
class SeparationResult:
    scanned_tables: int = 0
    hits: list[str] = field(default_factory=list)
    identifiers: int = 0

    @property
    def status(self) -> str:
        return "leak" if self.hits else "separated"


def check_separation(ev: Evidence) -> SeparationResult:
    """审计标识不得出现在普通业务库（state/memory/execution）任何文本列。"""
    result = SeparationResult()
    literals: set[str] = set()
    for sql in (
        "SELECT job_id AS v FROM audit_jobs", "SELECT snapshot_hash AS v FROM audit_pages",
        "SELECT finding_id AS v FROM audit_findings", "SELECT audit_ref AS v FROM human_audit_grants",
        "SELECT read_ref AS v FROM memory_audit_reads",
    ):
        literals.update(x["v"] for x in ev.rows("audit", sql) if x["v"])
    result.identifiers = len(literals)
    parts = [re.escape("memory-attempt:"), re.escape("memory-request:")] + [re.escape(v) for v in sorted(literals)]
    pattern = re.compile("|".join(parts))
    for database in ("state", "memory", "execution"):
        for table in ev.tables(database):
            if table.startswith("sqlite_") or "_fts" in table or "_vec" in table or table.endswith("_vec"):
                continue
            try:
                rows = ev.rows(database, f'SELECT * FROM "{table}"')
            except sqlite3.OperationalError:
                continue
            result.scanned_tables += 1
            for row in rows:
                for column, value in row.items():
                    if isinstance(value, bytes):
                        value = value.decode("utf-8", errors="replace")
                    if isinstance(value, str) and pattern.search(value):
                        hit = f"{database}.{table}.{column}"
                        if hit not in result.hits:
                            result.hits.append(hit)
                        break
    return result


@dataclass
class Report:
    evidence: str
    kinds: list[KindResult]
    separation: SeparationResult
    surfaces: dict[str, Any]
    missing_tables: list[str]
    missing_databases: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "evidence": self.evidence,
            "summary": self.summary(),
            "kinds": [k.to_json() for k in self.kinds],
            "separation": {**asdict(self.separation), "status": self.separation.status},
            "surfaces": self.surfaces,
            "missing_tables": self.missing_tables,
            "missing_databases": self.missing_databases,
        }

    def summary(self) -> dict[str, Any]:
        counts = Counter(k.status for k in self.kinds)
        return {
            "kinds": len(self.kinds),
            "observed": sum(k.observed for k in self.kinds),
            "audited": sum(k.audited for k in self.kinds),
            "missing": sum(k.missing_total for k in self.kinds),
            "by_status": dict(sorted(counts.items())),
            "gap_kinds": [k.kind for k in self.kinds if k.status == "gap"],
            "partial_kinds": [k.kind for k in self.kinds if k.status == "partial"],
            "not_observed_kinds": [k.kind for k in self.kinds if k.status == "not_observed"],
            "separation": self.separation.status,
        }


def surface_facts(ev: Evidence) -> dict[str, Any]:
    families = {family: ev.count("memory", table) for family, table in OA1_FAMILY_TABLES}
    return {
        "primary.audit.page(OA1)": {
            "family_rows": families,
            "grants": ev.count("audit", "human_audit_grants"),
            "deliveries": ev.count("audit", "human_audit_deliveries"),
            "sdk_sealed_access_events": ev.count("memory", "sealed_audit_access_events"),
            "sdk_trace_access_events": ev.count("memory", "audit_trace_access_events"),
            "sdk_access_authority_events": ev.count("memory", "audit_access_authority_events"),
            "exercised": bool(ev.count("audit", "human_audit_deliveries")),
        },
        "host.audit_pages": {
            "jobs_by_status": {k: v for k, v in Counter(x["status"] for x in ev.rows("audit", "SELECT status FROM audit_jobs")).items()},
            "pages": ev.count("audit", "audit_pages"),
            "findings_by_rule": {k: v for k, v in Counter(x["rule_id"] for x in ev.rows("audit", "SELECT rule_id FROM audit_findings")).items()},
            "ui_operation": None,
        },
        "host.memory_call_attempts": {
            "by_caller": {k: v for k, v in Counter(x["caller"] for x in ev.rows("audit", "SELECT caller FROM memory_call_attempts")).items()},
            "findings": ev.count("audit", "memory_call_findings"),
            "ui_operation": None,
        },
        "host.preparation_audit_sources": {"rows": ev.count("audit", "preparation_audit_sources"), "ui_operation": None},
    }


def run(evidence_dir: Path, workdir: Path | None = None) -> Report:
    ev = Evidence(evidence_dir, workdir)
    try:
        pages = PageIndex(ev)
        kinds = [fn(ev, pages) for _, fn in REGISTRY]
        return Report(str(ev.data_dir), kinds, check_separation(ev), surface_facts(ev), list(ev.missing_tables), list(ev.missing_databases))
    finally:
        ev.close()


STATUS_ZH = {"covered": "✅ 覆盖", "partial": "◐ 部分", "gap": "❌ 缺口", "not_observed": "— 未观测", "informational": "ⓘ 记录性"}


def render_markdown(report: Report) -> str:
    s = report.summary()
    lines = [
        f"证据：`{report.evidence}`", "",
        f"种类 {s['kinds']}；observed {s['observed']}；audited {s['audited']}；missing {s['missing']}；状态分布 {s['by_status']}；分权扫描：{'泄漏 ' + ', '.join(report.separation.hits) if report.separation.hits else '未发现审计标识进入业务库'}（扫描 {report.separation.scanned_tables} 表 / {report.separation.identifiers} 个标识）",
        "",
        "| 操作种类 | 状态 | observed | audited | missing | 账本 | 应有审计记录 | 受控读取面 |",
        "|---|---|---:|---:|---:|---|---|---|",
    ]
    for k in report.kinds:
        lines.append(f"| {k.label} (`{k.kind}`) | {STATUS_ZH[k.status]} | {k.observed} | {k.audited} | {k.missing_total} | {k.ledger} | {k.audit} | {k.surface} |")
    lines.append("")
    for k in report.kinds:
        if not (k.missing or k.notes):
            continue
        lines.append(f"### `{k.kind}` — {k.label}")
        for n in k.notes:
            lines.append(f"- {n}")
        if k.missing:
            lines.append(f"- 缺失（前 {len(k.missing)} / {k.missing_total}）：")
            lines.extend(f"  - `{m}`" for m in k.missing)
        lines.append("")
    lines.append("### 受控读取面事实")
    lines.append("```json")
    lines.append(json.dumps(report.surfaces, ensure_ascii=False, indent=1))
    lines.append("```")
    if report.missing_tables or report.missing_databases:
        lines.append(f"缺表：{report.missing_tables}；缺库：{report.missing_databases}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--evidence", required=True, type=Path, help="证据目录（primary-ui-* 或其 userdata/data）")
    parser.add_argument("--json", type=Path, help="写 JSON 报告")
    parser.add_argument("--markdown", type=Path, help="写 Markdown 报告")
    parser.add_argument("--workdir", type=Path, help="DB 副本目录（默认临时目录）")
    parser.add_argument("--fail-on-gap", action="store_true", help="存在 gap 或分权泄漏时返回 1")
    args = parser.parse_args(argv)
    report = run(args.evidence, args.workdir)
    text = render_markdown(report)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(text + "\n", encoding="utf-8")
    print(text)
    summary = report.summary()
    if args.fail_on_gap and (summary["gap_kinds"] or report.separation.hits):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
