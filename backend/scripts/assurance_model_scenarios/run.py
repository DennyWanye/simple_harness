# -*- coding: utf-8 -*-
"""真实模型验收：Assurance 原计划 §15 的四个场景 + 2026-10-06 用户加的编程题 + 补齐清单 V28
"两条线共用一步、其中一条换做法"，每题 3 局。

跑法（产品同形）：每局起一个隔离的开发模式后台（独立数据目录、DeepSeek 线路经本机闸口），
用后台的控制通道建任务、按场景触发事实、批准该人批的卡，任务结束后只读库和产物文件核对。
模型自己说了什么一概不算；每局的判定、用量、模型请求数都写进证据目录。

口径（原计划 §15 与 ``implementation/model-scenarios.json``，脚本不另写数字）：

* 每局预算只从 ``plans/Assurance/specs/1.1/implementation/model-scenarios.json`` 的 ``limits`` 读
  （token 总量、模型调用数、工具调用数、墙钟秒数），五个场景同一份预算。
* 通过 = 任务 COMPLETED + 判定全部成立 + 没超预算。超预算的局判 FAIL，``fail_reasons`` 写明超了哪项、
  实际多少、上限多少。环境故障记 INVALID_ENV，不算通过。
* 不补抽。同一局号再跑写成新的 ``trial-N/attempt-K/``，旧 attempt 目录原样保留；``summary.jsonl``
  只追加，每次尝试一行，跑完按局号把历次尝试都列出来。

用法（在仓库根）::

    backend/.venv/bin/python backend/scripts/assurance_model_scenarios/run.py <场景…> [--trials 3] [--out 目录]

场景名：accurate-report / bad-draft-rework / source-replaced-midway / publish-lost-reply / billing-tool /
shared-step-switch-method。

V28 那一局另有"观察项"（``observed``：点名共用、换做法），只记不判，单列在 record 与 summary 里，
供如实报告"观察到 / 没观察到"；通过与否只看任务完成、判定、预算。
证据：``<out>/<场景>/trial-N/attempt-K/record.json`` 与 ``<out>/summary.jsonl``。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import websockets

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
HOOKS = Path(__file__).resolve().parent / "hooks"
TEMPLATE = REPO / ".local-test-evidence/2026-10-05/f2-ui/userdata"
PLAN_SCENARIOS = REPO / "plans/Assurance/specs/1.1/implementation/model-scenarios.json"
PORT = int(os.environ.get("ASSURANCE_SCENARIO_PORT", "8100"))
WS = f"ws://127.0.0.1:{PORT}/ws/control"


def plan_limits(path: Path = PLAN_SCENARIOS) -> dict[str, int]:
    """每局预算，只从原计划的 model-scenarios.json 读；脚本里不写数字。"""
    body = json.loads(path.read_text(encoding="utf-8"))["limits"]
    return {"tokens": int(body["total_model_tokens"]), "model_calls": int(body["model_calls"]),
            "tool_calls": int(body["tool_calls"]), "wall": int(body["wall_time_seconds"])}


LIMITS = plan_limits()

FACTS_V1 = "item,quantity,unit_cost\nA,10,12\nB,4,25\n"
FACTS_V2 = "item,quantity,unit_cost\nA,11,12\nB,4,25\n"
FACTS_BAD = "item,quantity,unit_cost\nA,10,12\nB,4,\n"
FACTS_DISCOUNT = "item,quantity,unit_cost\nA,12,12\nB,4,25\n"
BAD_CANDIDATE = json.dumps({"total_cost": 290, "rows": [{"item": "A", "cost": 120}, {"item": "B", "cost": 100}],
                            "source_version": 1}, ensure_ascii=False, indent=2) + "\n"

BAD_SUMMARY = "| item | cost |\n| --- | --- |\n| A | 120 |\n| B | 100 |\n\n合计：290 元\n"

REPORT_RULE = ("report.json 是一个 JSON 对象：rows 为列表，每项含 item 与 cost（cost = quantity × unit_cost）；"
               "total_cost 为各项 cost 之和；source_sha256 为资料 sources/facts.csv 文件内容的 SHA-256（十六进制小写）。")
COSTS_RULE = REPORT_RULE.replace("report.json", "costs.json")


def sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


# ------------------------------------------------------------------ 场景定义
SCENARIOS: dict[str, dict[str, Any]] = {
    "accurate-report": {
        "title": "按事实表出一份准确的报告",
        "goal": "根据资料 sources/facts.csv 生成报告文件 report.json：逐项费用、合计费用、以及资料文件内容的 SHA-256。只出报告，不发送。",
        "criteria": ["file:report.json", REPORT_RULE,
                     "report.json 里每一项的 cost、total_cost 都与 sources/facts.csv 的数据一致，source_sha256 与资料文件一致"],
        "sources": {"sources/facts.csv": FACTS_V1},
        "oracle": "report",
        "expect": {"A": 120, "B": 100, "total": 220, "hash": sha(FACTS_V1), "actions": 0},
    },
    "bad-draft-rework": {
        "title": "错误的草稿被独立审阅员挡下来，返工后才能完成",
        "goal": "根据资料 sources/facts.csv 生成报告文件 report.json：逐项费用、合计费用、以及资料文件内容的 SHA-256。只出报告文件，不要写测试代码，不发送。",
        "criteria": ["file:report.json", REPORT_RULE,
                     "report.json 里每一项的 cost、total_cost 都与 sources/facts.csv 的数据一致，source_sha256 与资料文件一致"],
        "sources": {"sources/facts.csv": FACTS_V1},
        "hook": "swap_first_report",
        "oracle": "report",
        "expect": {"A": 120, "B": 100, "total": 220, "hash": sha(FACTS_V1), "actions": 0, "bad_draft": True},
    },
    "source-replaced-midway": {
        "title": "做到一半换了事实表，旧版本的结论不能拿来通过",
        "goal": "按当前获准的那一版资料 sources/facts.csv 计算报告文件 report.json：逐项费用、合计费用、以及资料文件内容的 SHA-256；过期版本的资料不能用于当前的验收。",
        "criteria": ["file:report.json", REPORT_RULE,
                     "report.json 里每一项的 cost、total_cost 都与现行版本 sources/facts.csv 的数据一致，source_sha256 与现行版本资料文件一致"],
        "sources": {"sources/facts.csv": FACTS_V1},
        # 执行者交回结果（ResultSubmitted）之后、内容审阅的调用已发出（InputSubmitted：审阅材料已冻结、
        # 审阅员正在读）时换资料。证据披露事件是审阅回复导入时才记的，那时验收紧随其后，赶不上。
        "supersede_on": ("ResultSubmitted", "InputSubmitted"),
        "oracle": "report",
        "expect": {"A": 132, "B": 100, "total": 232, "hash": sha(FACTS_V2), "actions": 0, "superseded": True},
    },
    "publish-lost-reply": {
        "title": "报告写好后要送出去，第一次送达的回执丢了，不能重发",
        "goal": "根据资料 sources/facts.csv 生成报告文件 report.json（逐项费用、合计费用、资料文件内容的 SHA-256），并把 report.json 发布到接收目录。",
        "criteria": ["file:report.json", REPORT_RULE, "action:file_publish.publish:report.json"],
        "sources": {"sources/facts.csv": FACTS_V1},
        "hook": "lose_first_publish_reply",
        "oracle": "publish",
        "expect": {"A": 120, "B": 100, "total": 220, "hash": sha(FACTS_V1)},
    },
    "billing-tool": {
        "title": "写一个带命令行和测试的小计费工具",
        "goal": ("写一个 Python 包 billing，实现命令行工具 python -m billing <事实表.csv>：读取事实表（列：item、quantity、unit_cost），"
                 "输出 JSON：每项的费用（rows: [{item, cost}]）、合计 total_cost、以及事实表文件的 SHA-256（source_sha256）。"
                 "规则：某一项数量 ≥ 10 时该项打九折；数量或单价缺失、为负、不是数字时，不要崩溃，退出码为 2，"
                 "并在 stderr 给出含行号的错误信息，stdout 不输出任何内容。附带 pytest 测试（放在 tests/ 目录），至少覆盖："
                 "正常表、打折项、缺失单价、负数数量。代码分成读取、计算、命令行三个模块。资料里的三份 csv 可以当测试数据。"),
        "criteria": ["file:billing/__main__.py", "file:tests/test_billing.py", "pytest:tests",
                     "billing 包分成读取、计算、命令行至少三个模块；命令行按题目规则输出 JSON，数量 ≥ 10 的项打九折；"
                     "坏数据时退出码 2、stdout 为空、stderr 含行号"],
        "sources": {"sources/facts.csv": FACTS_V1, "sources/facts-discount.csv": FACTS_DISCOUNT, "sources/facts-bad.csv": FACTS_BAD},
        # 预算同样只用 model-scenarios.json 的 limits；这题不在计划 JSON 里，要另给预算就改 JSON，不在这里写数
        "oracle": "billing",
        "expect": {"hash": sha(FACTS_V1)},
    },
    # 补齐清单 V28：两条线共用一步、其中一条换做法。题目只给出结构上的共用机会（一份两条线都要依据的
    # 明细文件 + 两份彼此独立的交付），不提"共用"二字；换做法的理由是客户线第一次交的 summary.md 被
    # 钩子换成合计写错的草稿（同 bad-draft-rework 的造法），审阅打回后由规划器自己判：重做、换做法还是别的。
    "shared-step-switch-method": {
        "title": "两份交付依据同一份明细，其中一份第一次交错了",
        "goal": ("根据资料 sources/facts.csv 先算出费用明细文件 costs.json（rows 为列表，每项含 item 与 cost，"
                 "cost = quantity × unit_cost；total_cost 为各项 cost 之和；source_sha256 为资料文件内容的 SHA-256）。"
                 "再以 costs.json 为依据出两份彼此独立的交付：给财务的 finance.csv（表头 item,cost，每项一行，"
                 "最后一行 TOTAL,<合计>），给客户的 summary.md（Markdown 表格列出每一项的费用，表格后一行写"
                 "“合计：<合计> 元”）。两份交付里的数字都必须与 costs.json 一致。只出文件，不要写测试代码，不发送。"),
        "criteria": ["file:costs.json", "file:finance.csv", "file:summary.md", COSTS_RULE,
                     "finance.csv 表头为 item,cost，每项一行费用，最后一行为 TOTAL,<合计>，数字与 costs.json 一致",
                     "summary.md 用 Markdown 表格列出每一项的费用，表格后一行为“合计：<合计> 元”，数字与 costs.json 一致"],
        "sources": {"sources/facts.csv": FACTS_V1},
        "hook": "swap_first_report",
        "hook_target": "summary.md",
        "bad_content": BAD_SUMMARY,
        "oracle": "shared",
        "shared_path": "costs.json",
        "expect": {"A": 120, "B": 100, "total": 220, "hash": sha(FACTS_V1)},
    },
}


# ------------------------------------------------------------------ 后台
class Backend:
    def __init__(self, run_dir: Path, *, hook: str | None, hook_target: str = "report.json",
                 bad_content: str = BAD_CANDIDATE) -> None:
        self.run_dir = run_dir
        self.userdata = run_dir / "userdata"
        self.published = run_dir / "published"
        self.hook_log = run_dir / "hooks.jsonl"
        self.hook = hook
        self.hook_target = hook_target
        self.bad_content = bad_content
        self.process: subprocess.Popen[bytes] | None = None

    def prepare(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.published.mkdir(exist_ok=True)
        if not self.userdata.exists():
            self.userdata.mkdir()
            for name in ("config.toml", "llm_runtime.json", "model_overrides.toml", "onboarding_done.json",
                         "companion-local-identity.json"):
                if (TEMPLATE / name).exists():
                    shutil.copy(TEMPLATE / name, self.userdata / name)
            for name in ("secrets", "capabilities", "skills", "plugins"):
                if (TEMPLATE / name).exists():
                    shutil.copytree(TEMPLATE / name, self.userdata / name)
            text = (self.userdata / "config.toml").read_text(encoding="utf-8")
            text = re.sub(r'^publish_dir = ".*"$', f'publish_dir = "{self.published}"', text, flags=re.M)
            (self.userdata / "config.toml").write_text(text, encoding="utf-8")

    def _api_key(self) -> str:
        for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
            match = re.match(r'^\s*(?:export\s+)?DEEPSEEKER_APIKEY\s*=\s*["\']?([^"\']*)["\']?\s*$', line)
            if match:
                return match.group(1)
        raise SystemExit("找不到 DEEPSEEKER_APIKEY")

    def start(self) -> None:
        env = dict(os.environ)
        env.update({
            "DESKPET_USER_DATA_DIR": str(self.userdata), "DESKPET_CONFIG": str(self.userdata / "config.toml"),
            "DESKPET_USER_LOG_DIR": str(self.run_dir / "logs"), "DESKPET_USER_LOG": str(self.run_dir / "logs/backend.log"),
            "DESKPET_USER_CACHE_DIR": str(self.run_dir / "cache"), "DESKPET_DEV_MODE": "1",
            "DESKPET_BACKEND_PORT": str(PORT), "DESKPET_CLOUD_API_KEY": self._api_key(),
            "PYTHONPATH": str(HOOKS) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
            "ASSURANCE_HOOK_LOG": str(self.hook_log), "ASSURANCE_HOOK_TARGET": self.hook_target,
        })
        if self.hook:
            env["ASSURANCE_SCENARIO_HOOK"] = self.hook
            bad = self.run_dir / "bad-candidate.json"
            bad.write_text(self.bad_content, encoding="utf-8")
            env["ASSURANCE_HOOK_BAD_FILE"] = str(bad)
        (self.run_dir / "logs").mkdir(exist_ok=True)
        out = open(self.run_dir / "backend.out", "ab")
        self.process = subprocess.Popen([str(BACKEND / ".venv/bin/python"), "main.py"], cwd=BACKEND, env=env,
                                        stdout=out, stderr=subprocess.STDOUT, start_new_session=True)

    def stop(self) -> None:
        if self.process is None:
            return
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=30)
        except Exception:  # noqa: BLE001
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=10)
        self.process = None


class Control:
    def __init__(self) -> None:
        self.ws: Any = None
        self.pending: dict[str, asyncio.Future] = {}
        self.unsolicited: list[dict[str, Any]] = []
        self.reader: asyncio.Task | None = None

    async def connect(self, timeout: float = 240.0) -> None:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                self.ws = await websockets.connect(WS, max_size=64 * 1024 * 1024, open_timeout=5)
                break
            except Exception as error:  # noqa: BLE001
                last = error
                await asyncio.sleep(1)
        else:
            raise RuntimeError(f"后台没有起来：{last}")
        self.reader = asyncio.create_task(self._read())
        while time.time() < deadline:
            status = await self.call("orchestration_status", {})
            if status.get("available"):
                return
            await asyncio.sleep(2)
        raise RuntimeError(f"编排服务没有就绪：{status}")

    async def _read(self) -> None:
        async for raw in self.ws:
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            payload = message.get("payload") or {}
            request_id = payload.get("request_id") if isinstance(payload, dict) else None
            future = self.pending.pop(str(request_id), None) if request_id else None
            if future is not None and not future.done():
                future.set_result(message)
            else:
                self.unsolicited.append(message)

    async def call(self, msg_type: str, payload: dict[str, Any], *, timeout: float = 120.0) -> Any:
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        await self.ws.send(json.dumps({"type": msg_type, "request_id": request_id,
                                       "payload": {**payload, "request_id": request_id}}, ensure_ascii=False))
        message = await asyncio.wait_for(future, timeout)
        body = message.get("payload") or {}
        if not body.get("ok", False):
            raise RuntimeError(f"{msg_type}: {body.get('error_code')} {body.get('error')}")
        return body.get("data")

    async def close(self) -> None:
        if self.reader:
            self.reader.cancel()
        if self.ws:
            await self.ws.close()


# ------------------------------------------------------------------ 一局
def _confirm_proposal(mission_id: str, workspace: dict[str, Any], command_id: str) -> dict[str, Any]:
    """确认页做的事：内容要求照单确认，action: 要求作为必须完成的效果挂在根义务上，完成标准"内容哈希一致"。"""
    actions = [c["id"] for c in workspace["criteria"] if str(c["statement"]).startswith("action:")]
    content = [c["id"] for c in workspace["criteria"] if c.get("required") and c["id"] not in actions]
    [obligation] = workspace["obligations"]
    milestone = next(m for m in workspace["milestones"] if m["id"] == "CONTENT_HASH_VERIFIED")
    ref = workspace["requirements_ref"]
    effects = [{
        "effect_key": "publish-1", "source_slot_key": "publish-1", "obligation_id": obligation["id"],
        "criterion_ids": actions, "required_milestone": milestone["id"],
        "milestone_policy_ref": milestone["milestone_policy_ref"], "evidence_policy_ref": milestone["evidence_policy_ref"],
    }] if actions else []
    return {"mission_id": mission_id, "command_id": command_id, "expected_requirements_ref": ref,
            "proposal": {"schema_version": 1, "mission_id": mission_id,
                         "requirements_ref": {"id": ref["id"], "revision": ref["revision"], "content_hash": ref["content_hash"]},
                         "mode": "REQUIRED_EFFECTS" if effects else "CONTENT_ONLY",
                         "content_criterion_ids": content, "effects": effects}}


def _after(events: list[Any], first: str, second: str) -> bool:
    """事件流里 ``second`` 出现在某个 ``first`` 之后。"""
    seen_first = None
    for seq, kind in events:
        if kind == first and seen_first is None:
            seen_first = seq
        elif kind == second and seen_first is not None and seq > seen_first:
            return True
    return False


def new_attempt_dir(out_root: Path, name: str, trial: int) -> tuple[Path, int]:
    """同一局号再跑开一个新的 attempt 目录；旧 attempt 原样保留（计划 §15：新尝试留历史，不覆盖）。"""
    trial_dir = out_root / name / f"trial-{trial}"
    existing = [int(m.group(1)) for p in trial_dir.glob("attempt-*")
                if (m := re.fullmatch(r"attempt-(\d+)", p.name)) and p.is_dir()]
    attempt = max(existing, default=0) + 1
    run_dir = trial_dir / f"attempt-{attempt}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir, attempt


def verdict(final_status: str, oracle: dict[str, Any], usage: dict[str, Any], wall_seconds: float,
            limits: dict[str, int]) -> dict[str, Any]:
    """一局通过与否（计划 §15）：任务 COMPLETED、判定全部成立、没超预算，三者缺一判 FAIL。

    超预算的项写进 ``fail_reasons``（实际值 / 上限）；``within_budget`` 单独给出便于统计。
    工具调用数后台库里没有现成计数，这里不核（和改前一样），记录里如实不写。"""
    reasons: list[str] = []
    if final_status != "COMPLETED":
        reasons.append(f"任务终态是 {final_status or '未知'}，不是 COMPLETED")
    reasons.extend(f"判定 {key} 未成立：{value!r}" for key, value in oracle.items() if value is not True)
    over: list[str] = []
    checks = (("tokens", "token 总量", usage.get("tokens")), ("model_calls", "模型调用数", usage.get("model_calls")),
              ("wall", "墙钟秒数", wall_seconds))
    for key, label, actual in checks:
        if actual is None:
            over.append(f"{label}无法核对（没有用量数据），按超预算处理")
        elif actual > limits[key]:
            over.append(f"{label} {actual:.0f} > 上限 {limits[key]}")
    reasons.extend(f"超预算：{item}" for item in over)
    return {"passed": not reasons, "within_budget": not over, "fail_reasons": reasons}


async def run_trial(name: str, trial: int, attempt: int, run_dir: Path) -> dict[str, Any]:
    spec = SCENARIOS[name]
    backend = Backend(run_dir, hook=spec.get("hook"), hook_target=spec.get("hook_target", "report.json"),
                      bad_content=spec.get("bad_content", BAD_CANDIDATE))
    backend.prepare()
    record: dict[str, Any] = {"scenario": name, "title": spec["title"], "trial": trial, "attempt": attempt,
                              "limits": dict(LIMITS), "started_at": time.time(),
                              "events_seen": [], "approvals": [], "questions": [], "triggers": [], "notes": []}
    control = Control()
    backend.start()
    try:
        await control.connect()
        key = f"assurance-model:{name}:{trial}:{uuid.uuid4().hex[:8]}"
        sources = [{"path": path, "content": content, "kind": "text"} for path, content in spec["sources"].items()]
        created = await control.call("mission_create_with_sources", {
            # 任务预算用部署默认值（产品的预留是按上下文窗口上限预留的，拿计划里的 30 万实际用量上限去当
            # 预算会在第一次派发就耗尽）；计划里的用量上限在局后按实际结算用量核，超了判 FAIL。
            "mission": {"goal": spec["goal"], "success_criteria": list(spec["criteria"]), "idempotency_key": key},
            "sources": sources})
        mission_id = created["mission_id"]
        record["mission_id"] = mission_id
        record["source_versions"] = {s["path"]: sha(s["content"]) for s in sources}
        started = time.time()
        seen_seq = 0
        superseded = False
        confirmed: set[str] = set()
        status = ""
        while time.time() - started < LIMITS["wall"]:
            await asyncio.sleep(1)
            # 事件（只记类型，用来触发资料换版本与记录过程）
            batch = await control.call("mission_events", {"mission_id": mission_id, "after_seq": seen_seq, "limit": 200})
            for event in batch.get("events") or ():
                seen_seq = max(seen_seq, int(event.get("seq") or 0))
                record["events_seen"].append((event.get("seq"), event.get("type")))
            # 场景三：审阅材料冻结后、验收写下前换资料
            pattern = spec.get("supersede_on")
            if pattern and not superseded and _after(record["events_seen"], *pattern):
                superseded = True
                proposal = await control.call("mission_source_supersede", {
                    "mission_id": mission_id, "path": "sources/facts.csv", "content": FACTS_V2, "kind": "text",
                    "idempotency_key": key + ":supersede", "expected_version_hash": sha(FACTS_V1)})
                record["triggers"].append({"at": time.time() - started, "supersede": proposal, "after_seq": seen_seq})
            detail = await control.call("mission_get", {"mission_id": mission_id})
            status = str((detail.get("mission") or {}).get("status") or "")
            workspace = detail.get("operation_workspace") or {}
            if workspace.get("state") == "CONFIRMATION_REQUIRED" and workspace.get("editable"):
                command_id = f"confirm-{len(confirmed) + 1}"
                await control.call("mission_operation_completion_approve", _confirm_proposal(mission_id, workspace, command_id))
                confirmed.add(command_id)
                record["triggers"].append({"at": time.time() - started, "confirmed": command_id})
            for approval in (await control.call("mission_approval_list", {"mission_id": mission_id})).get("approvals") or ():
                if approval.get("state") != "PENDING" or approval["request_id"] in {a["request_id"] for a in record["approvals"]}:
                    continue
                kind = approval.get("kind")
                # 审阅员判不下来升级给人的结果审阅（kind=review）只收 review_pass/review_fail；
                # 2026-10-09 NL2Repo retrying 局按普通审批答，被拒、脚本中断。驱动脚本代人判"通过"，
                # 记进 approvals（kind=review 即代答），最终质量看系统外判定。
                decision = "review_pass" if kind == "review" else "approve"
                if kind == "arbitration":
                    # 仲裁要裁决内容与依据，脚本不替人裁：记下来，留给系统外判定，不中断这一局
                    record["approvals"].append({"request_id": approval["request_id"], "kind": kind,
                                                "decision": "left_pending", "at": time.time() - started})
                    continue
                note = "验收驱动脚本代判通过" if kind == "review" else "验收驱动脚本批准"
                await control.call("mission_approval_decide", {"approval_id": approval["request_id"], "decision": decision,
                                                               "reason": "", "note": note, "ruling": "", "basis": ""})
                record["approvals"].append({"request_id": approval["request_id"], "kind": kind, "decision": decision,
                                            "at": time.time() - started})
            # 2026-10-10：规划器向人提问时（10-09 parse 局：连续写满 + 无进展上限后请人决定），无人值守的
            # 验收脚本代答"按你的判断继续"并记进 record；真人使用时由人回答，这里不替系统做任何判断。
            for question in detail.get("planning_questions") or ():
                if question.get("state") != "PENDING" or question.get("decision_id") in {q["decision_id"] for q in record["questions"]}:
                    continue
                answer = "按你的判断继续：你自己决定做法，不用再问我。"
                try:
                    await control.call("mission_planning_answer", {
                        "decision_id": question["decision_id"], "answer": answer,
                        "expected_version": int(question.get("version") or 0),
                        "nonce": f"{key}:answer:{question['decision_id']}"})
                    outcome = "answered"
                except Exception as error:  # noqa: BLE001 - 记下来，不中断这一局
                    outcome = f"answer_failed: {type(error).__name__}: {error}"
                record["questions"].append({"decision_id": question["decision_id"], "question": question.get("question"),
                                            "answer": answer, "outcome": outcome, "at": time.time() - started})
            if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
        record["final_status"] = status
        record["wall_seconds"] = time.time() - started
        record["detail"] = detail
        record["hooks"] = [json.loads(line) for line in backend.hook_log.read_text(encoding="utf-8").splitlines()] \
            if backend.hook_log.exists() else []
    finally:
        await control.close()
        backend.stop()
    try:
        record["oracle"] = ORACLES[spec["oracle"]](backend, record, spec)
    except Exception as error:  # noqa: BLE001 - 判定本身出错也要留下记录，不冒充环境故障
        record["oracle"] = {"oracle_error": f"{type(error).__name__}: {error}"}
    record["usage"] = usage(backend, record.get("mission_id"))
    # 通过 = 任务完成 + 判定全部成立 + 没超计划预算（计划 §15"任一超预算/跑不完保留 FAIL"）
    record.update(verdict(record["final_status"], record["oracle"], record["usage"], record["wall_seconds"], LIMITS))
    (run_dir / "record.json").write_text(json.dumps(record, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return record


# ------------------------------------------------------------------ 判定（只读库与文件）
def db(backend: Backend) -> sqlite3.Connection:
    path = backend.userdata / "data/agent-orchestrator/orchestrator.db"
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def artifact_bytes(backend: Backend, content_hash: str) -> bytes:
    return (backend.userdata / "data/agent-orchestrator/artifacts/sha256" / content_hash).read_bytes()


def accepted_artifacts(connection: sqlite3.Connection, mission_id: str) -> dict[str, str]:
    """现行验收指向的产物：path → content_hash。"""
    out: dict[str, str] = {}
    for row in connection.execute("SELECT acceptance_json FROM acceptances WHERE mission_id=? AND validity='CURRENT'",
                                  (mission_id,)):
        body = json.loads(row[0])
        for ref in body.get("artifact_refs") or ():
            art = connection.execute("SELECT path, content_hash FROM artifacts WHERE artifact_id=?", (ref["id"],)).fetchone()
            if art is not None:
                out[art["path"]] = art["content_hash"]
    return out


def usage(backend: Backend, mission_id: str | None) -> dict[str, Any]:
    if not mission_id:
        return {"model_calls": 0, "tokens": 0}
    connection = db(backend)
    calls = connection.execute("SELECT count(*) FROM dispatch_intents WHERE mission_id=?", (mission_id,)).fetchone()[0]
    columns = [c[1] for c in connection.execute("PRAGMA table_info(budget_reservations)")]
    token_column = next((c for c in ("settled_tokens", "counted_tokens", "reserved_tokens") if c in columns), None)
    tokens = connection.execute(f"SELECT coalesce(sum({token_column}),0) FROM budget_reservations WHERE mission_id=?",
                                (mission_id,)).fetchone()[0] if token_column else None
    report = json.loads(connection.execute("SELECT json FROM missions WHERE mission_id=?", (mission_id,)).fetchone()[0])
    final = report.get("final_report") or {}
    return {"model_calls": int(calls), "tokens": int(tokens or 0), "token_column": token_column,
            "usage_fully_known": final.get("usage_fully_known"), "budget_conserved": final.get("budget_conserved")}


def _common(connection: sqlite3.Connection, mission_id: str) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["root_resolution_accepted"] = connection.execute(
        "SELECT count(*) FROM goal_resolutions WHERE mission_id=? AND verdict='ACCEPT' AND validity='CURRENT'",
        (mission_id,)).fetchone()[0] >= 1
    row = connection.execute("SELECT state FROM assurance_closeouts WHERE mission_id=?", (mission_id,)).fetchone()
    checks["closeout_finalized"] = row is not None and row["state"] == "FINALIZED"
    checks["official_reviews_present"] = connection.execute(
        "SELECT count(*) FROM review_records WHERE mission_id=? AND official=1", (mission_id,)).fetchone()[0] >= 2
    return checks


def oracle_report(backend: Backend, record: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    expect = spec["expect"]
    connection = db(backend)
    mission_id = record.get("mission_id")
    checks = _common(connection, mission_id)
    accepted = accepted_artifacts(connection, mission_id)
    checks["report_accepted"] = "report.json" in accepted
    if checks["report_accepted"]:
        try:
            body = json.loads(artifact_bytes(backend, accepted["report.json"]).decode("utf-8"))
            rows = {str(r.get("item")): r.get("cost") for r in body.get("rows") or ()}
            checks["cost_A"] = float(rows.get("A", -1)) == expect["A"]
            checks["cost_B"] = float(rows.get("B", -1)) == expect["B"]
            checks["total"] = float(body.get("total_cost", -1)) == expect["total"]
            checks["source_hash"] = str(body.get("source_sha256", "")).lower() == expect["hash"]
            record["report"] = body
        except Exception as error:  # noqa: BLE001
            checks["report_readable"] = f"{type(error).__name__}: {error}"
    checks["no_operations"] = connection.execute("SELECT count(*) FROM actions WHERE mission_id=?",
                                                 (mission_id,)).fetchone()[0] == expect.get("actions", 0)
    if expect.get("bad_draft"):
        hooks = [h for h in record.get("hooks") or () if h.get("path")]
        checks["bad_draft_was_written"] = bool(hooks)
        bad_hash = sha(BAD_CANDIDATE)
        checks["bad_draft_stored_as_artifact"] = connection.execute(
            "SELECT count(*) FROM artifacts WHERE mission_id=? AND content_hash=?", (mission_id, bad_hash)).fetchone()[0] >= 1
        checks["bad_draft_never_accepted"] = accepted.get("report.json") != bad_hash
        rejected = connection.execute(
            "SELECT record_json FROM review_records WHERE mission_id=? AND official=1 AND verdict IN ('REJECTED','REWORK')",
            (mission_id,)).fetchall()
        checks["official_rejection_exists"] = len(rejected) >= 1
        # 计划原话是"点名 total=290 的错"；真实审阅员写的是"total_cost 应为各项之和（220）"，同样是点名
        # 合计错了——按"打回意见点名合计这一项且给出 290 或 220"判
        checks["rejection_names_total_error"] = any(
            "total_cost" in r[0] and ("290" in r[0] or "220" in r[0]) for r in rejected)
    if expect.get("superseded"):
        checks["supersede_triggered"] = any("supersede" in t for t in record["triggers"])
        versions = connection.execute("SELECT version_hash, revoked, superseded_by FROM sources WHERE mission_id=? AND path='sources/facts.csv'",
                                      (mission_id,)).fetchall()
        checks["v1_retained_as_superseded"] = any(v["version_hash"] == sha(FACTS_V1) and v["superseded_by"] for v in versions)
        checks["v2_active"] = any(v["version_hash"] == sha(FACTS_V2) and not v["revoked"] and not v["superseded_by"]
                                  for v in versions)
        old = connection.execute("SELECT count(*) FROM artifacts a WHERE a.mission_id=? AND a.path='report.json'",
                                 (mission_id,)).fetchone()[0]
        checks["old_result_retained"] = old >= 1
        checks["planner_was_asked_about_source_change"] = any(
            t == "PlanningRepairRequested" for _, t in record["events_seen"])
        # 换资料落在哪个窗口（信息，不作通过条件）：步骤验收之前 = 计划规定的切点；之后 = 终审前的变体
        swapped_at = next((t["after_seq"] for t in record["triggers"] if "supersede" in t), None)
        first_acceptance = next((s for s, t in record["events_seen"] if t == "AcceptanceCommitted"), None)
        record["swap_window"] = ("before_step_acceptance" if swapped_at is not None and first_acceptance is not None
                                 and swapped_at < first_acceptance else "after_step_acceptance_before_final")
    return checks


def oracle_publish(backend: Backend, record: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    checks = oracle_report(backend, record, {**spec, "expect": {**spec["expect"], "actions": None}})
    checks.pop("no_operations", None)
    connection = db(backend)
    mission_id = record.get("mission_id")
    files = [p for p in backend.published.rglob("*") if p.is_file()]
    checks["published_exactly_once"] = len(files) == 1
    if files:
        checks["published_bytes_match_accepted"] = sha(files[0].read_bytes()) == accepted_artifacts(connection, mission_id).get("report.json")
    actions = [json.loads(r[0]) for r in connection.execute("SELECT json FROM actions WHERE mission_id=?", (mission_id,))]
    live = [a for a in actions if a.get("state") != "REFUSED"]
    checks["one_action_one_handoff"] = len(live) == 1 and int(live[0].get("handoffs") or 0) == 1 and live[0].get("state") == "SUCCEEDED"
    checks["reply_was_lost_by_hook"] = any(h.get("event") == "reply-lost" for h in record.get("hooks") or ())
    receipts = [json.loads(r[0]) for r in connection.execute("SELECT receipt_json FROM delivery_receipts WHERE mission_id=?",
                                                              (mission_id,))]
    checks["delivery_receipt_recorded"] = len(receipts) >= 1 and all(r.get("operation_id") for r in receipts)
    checks["effect_acceptance_recorded"] = connection.execute(
        "SELECT count(*) FROM events WHERE mission_id=? AND type='OperationOutcomeAccepted'", (mission_id,)).fetchone()[0] >= 1
    checks["reconciled_not_resent"] = (
        connection.execute("SELECT count(*) FROM events WHERE mission_id=? AND type='ActionOutcomeUnknown'",
                           (mission_id,)).fetchone()[0] >= 1
        and connection.execute("SELECT count(*) FROM events WHERE mission_id=? AND type='ActionReconciled'",
                               (mission_id,)).fetchone()[0] >= 1
        and connection.execute("SELECT count(*) FROM events WHERE mission_id=? AND type='ActionHandedOff'",
                               (mission_id,)).fetchone()[0] == 1)
    checks["no_human_ruling_needed"] = not any(t == "HumanOverride" for _, t in record["events_seen"])
    return checks


def oracle_billing(backend: Backend, record: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    connection = db(backend)
    mission_id = record.get("mission_id")
    checks = _common(connection, mission_id)
    accepted = accepted_artifacts(connection, mission_id)
    modules = [p for p in accepted if p.startswith("billing/") and p.endswith(".py")]
    checks["three_modules"] = len([p for p in modules if p not in ("billing/__init__.py", "billing/__main__.py")]) >= 2 \
        and "billing/__main__.py" in accepted
    checks["tests_present"] = any(p.startswith("tests/") and p.endswith(".py") for p in accepted)
    checks["pytest_check_consumed"] = connection.execute(
        "SELECT count(*) FROM events WHERE mission_id=? AND type='AssuranceLocalCheckFinished'", (mission_id,)).fetchone()[0] >= 1
    checks["no_operations"] = connection.execute("SELECT count(*) FROM actions WHERE mission_id=?", (mission_id,)).fetchone()[0] == 0
    # 把验收过的代码取出来，自己跑
    work = backend.run_dir / "oracle-workspace"
    if work.exists():
        shutil.rmtree(work)
    for path, content_hash in accepted.items():
        target = work / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(artifact_bytes(backend, content_hash))
    for name, content in spec["sources"].items():
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_text(content, encoding="utf-8")
    python = str(BACKEND / ".venv/bin/python")

    def run(args: list[str], path: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([python, "-m", "billing", path], cwd=work, capture_output=True, text=True, timeout=60,
                              env={**os.environ, "PYTHONPATH": str(work)})

    try:
        normal = run([], "sources/facts.csv")
        body = json.loads(normal.stdout)
        rows = {str(r.get("item")): float(r.get("cost")) for r in body.get("rows") or ()}
        checks["normal_A_108"] = abs(rows.get("A", -1) - 108) < 1e-6
        checks["normal_B_100"] = abs(rows.get("B", -1) - 100) < 1e-6
        checks["normal_total_208"] = abs(float(body.get("total_cost", -1)) - 208) < 1e-6
        checks["normal_hash"] = str(body.get("source_sha256", "")).lower() == spec["expect"]["hash"]
        discount = run([], "sources/facts-discount.csv")
        body = json.loads(discount.stdout)
        rows = {str(r.get("item")): float(r.get("cost")) for r in body.get("rows") or ()}
        checks["discount_A_129_6"] = abs(rows.get("A", -1) - 129.6) < 1e-6
        checks["discount_total_229_6"] = abs(float(body.get("total_cost", -1)) - 229.6) < 1e-6
        bad = run([], "sources/facts-bad.csv")
        checks["bad_exit_2"] = bad.returncode == 2
        checks["bad_stdout_empty"] = bad.stdout == ""
        checks["bad_stderr_names_line_3"] = "3" in bad.stderr
        tests = subprocess.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], cwd=work,
                               capture_output=True, text=True, timeout=300, env={**os.environ, "PYTHONPATH": str(work)})
        checks["pytest_passes_clean"] = tests.returncode == 0
        match = re.search(r"(\d+) passed", tests.stdout)
        checks["at_least_4_tests"] = bool(match) and int(match.group(1)) >= 4
        record["oracle_runs"] = {"normal": normal.stdout[:500], "bad_stderr": bad.stderr[:500], "pytest": tests.stdout[-800:]}
    except Exception as error:  # noqa: BLE001
        checks["program_runs"] = f"{type(error).__name__}: {error}"
    return checks


def live_tasks(connection: sqlite3.Connection, mission_id: str) -> set[str] | None:
    """现行计划版本（最新的 ACTIVE 版本）里被采用的普通步骤的 task_id；库里没有计划版本时返回 None（不过滤）。

    换做法 / 接替之后，旧步骤的验收可能仍是 CURRENT，但它已不在现行计划里——"只有一份被验收的结果"
    只数现行计划里的步骤，旧步骤是历史，不算重复执行。"""
    row = connection.execute("SELECT max(revision) FROM plan_revisions WHERE mission_id=? AND state='ACTIVE'",
                             (mission_id,)).fetchone()
    if row is None or row[0] is None:
        return None
    return {str(r[0]) for r in connection.execute(
        "SELECT DISTINCT task_id FROM plan_memberships WHERE mission_id=? AND revision=? AND adopted=1"
        " AND form='primitive'", (mission_id, row[0]))}


def live_accepted(connection: sqlite3.Connection, mission_id: str) -> dict[str, list[tuple[str, str]]]:
    """现行验收（CURRENT）里、现行计划步骤交的产物：path → [(task_id, content_hash)…]，一条路径可能有几个步骤。"""
    live = live_tasks(connection, mission_id)
    out: dict[str, list[tuple[str, str]]] = {}
    for row in connection.execute("SELECT task_id, acceptance_json FROM acceptances WHERE mission_id=? AND validity='CURRENT'",
                                  (mission_id,)):
        if live is not None and str(row[0]) not in live:
            continue
        for ref in json.loads(row[1]).get("artifact_refs") or ():
            art = connection.execute("SELECT path, content_hash FROM artifacts WHERE artifact_id=?", (ref["id"],)).fetchone()
            if art is not None and (str(row[0]), art["content_hash"]) not in out.get(art["path"], []):
                out.setdefault(art["path"], []).append((str(row[0]), art["content_hash"]))
    return out


def sharing_observations(connection: sqlite3.Connection, mission_id: str) -> tuple[dict[str, bool], dict[str, Any]]:
    """V28 的两个观察项，只读产品库里的计划事实（不读模型说了什么）：

    * ``named_sharing``：某个步骤被**两个不同目标**的做法实例同时当作子步骤持有，且至少一方是点名共用
      （``method_child_occurrences.reuse_policy`` 为 ``share_active`` / ``reuse_accepted``）。同一目标换做法时
      沿用旧步骤（新旧实例属于同一个目标）不算两条线共用。
    * ``method_switched``：某个分支目标（它本身是别的做法的子步骤，不是根）有一个已退休（RETIRED）的做法
      实例，且之后的计划版本里同一目标换上了不同的做法（做法 id / 版本 / 内容哈希有一项不同）。

    返回 (观察项, 细节)；细节里列出每个点名共用的步骤、每次换做法（含根目标的，``is_branch`` 标明）。"""
    holders: dict[str, list[dict[str, Any]]] = {}
    for row in connection.execute(
            "SELECT c.occurrence_id, c.reuse_policy, i.instance_id, i.goal_occurrence_id, i.state"
            " FROM method_child_occurrences c JOIN method_instances i"
            " ON i.mission_id=c.mission_id AND i.instance_id=c.instance_id WHERE c.mission_id=?", (mission_id,)):
        holders.setdefault(str(row[0]), []).append({"policy": str(row[1]), "instance": str(row[2]),
                                                    "goal": str(row[3]), "state": str(row[4])})
    tasks = {str(r[0]): str(r[1]) for r in connection.execute(
        "SELECT DISTINCT occurrence_id, task_id FROM plan_memberships WHERE mission_id=?", (mission_id,))}
    shared = []
    for occurrence, rows in sorted(holders.items()):
        goals = sorted({r["goal"] for r in rows})
        named = sorted({r["policy"] for r in rows if r["policy"] in ("share_active", "reuse_accepted")})
        if named and len(goals) >= 2:
            shared.append({"occurrence_id": occurrence, "task_id": tasks.get(occurrence), "held_by_goals": goals,
                           "reuse_policy": named,
                           "adopted_holders": len({r["instance"] for r in rows if r["state"] == "ADOPTED"})})
    children = set(holders)
    instances = [dict(r) for r in connection.execute(
        "SELECT instance_id, goal_occurrence_id, method_id, method_version, method_content_hash, plan_revision, state"
        " FROM method_instances WHERE mission_id=? ORDER BY plan_revision, created_at", (mission_id,))]
    shared_ids = {item["occurrence_id"] for item in shared}
    switches = []
    for old in instances:
        if old["state"] != "RETIRED":
            continue
        ref = (old["method_id"], old["method_version"], old["method_content_hash"])
        for new in instances:
            if (new["goal_occurrence_id"] == old["goal_occurrence_id"] and new["plan_revision"] > old["plan_revision"]
                    and (new["method_id"], new["method_version"], new["method_content_hash"]) != ref):
                keeps = sorted(o for o, rows in holders.items() if o in shared_ids
                               and any(r["instance"] == new["instance_id"] for r in rows))
                switches.append({"goal_occurrence_id": old["goal_occurrence_id"],
                                 "is_branch": old["goal_occurrence_id"] in children,
                                 "retired": f"{old['method_id']}@{old['method_version']}",
                                 "replacement": f"{new['method_id']}@{new['method_version']}",
                                 "at_plan_revision": new["plan_revision"], "keeps_shared_steps": keeps})
                break
    observed = {"named_sharing": bool(shared), "method_switched": any(item["is_branch"] for item in switches)}
    return observed, {"named_shared_steps": shared, "method_switches": switches}


def _summary_ok(text: str, expect: dict[str, Any]) -> bool:
    """summary.md：有 Markdown 表格，每一项一行带它的费用，另有一行"合计"写对合计。"""
    def num(value: Any) -> str:
        return rf"(?<![0-9.]){value}(?:\.0+)?(?![0-9.])"

    lines = text.splitlines()
    table = [line for line in lines if line.strip().startswith("|")]
    items = all(any(re.search(rf"(?<![A-Za-z0-9]){item}(?![A-Za-z0-9])", line) and re.search(num(expect[item]), line)
                    for line in table) for item in ("A", "B"))
    total = any("合计" in line and re.search(num(expect["total"]), line) for line in lines)
    return bool(table) and items and total


def _finance_ok(text: str, expect: dict[str, Any]) -> bool:
    import csv
    import io

    rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text)) if any(c.strip() for c in row)]
    if not rows or [c.lower() for c in rows[0]] != ["item", "cost"] or any(len(r) != 2 for r in rows[1:]):
        return False
    try:
        body = {r[0]: float(r[1]) for r in rows[1:]}
    except ValueError:
        return False
    return (rows[-1][0] == "TOTAL" and body == {"A": expect["A"], "B": expect["B"], "TOTAL": expect["total"]})


def oracle_shared(backend: Backend, record: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """V28 一局的判定。通过条件（进 checks）：根结论 / 收尾 / 两次正式审阅、三个文件都被验收且内容对、
    没有任何操作、钩子确实造了错稿且错稿没被验收、**共用的明细文件在现行计划里只有一份被验收的结果**
    （两条线各算一遍 costs.json 判 FAIL）。观察项（点名共用 / 换做法）写进 ``record["observed"]``，不进 checks。"""
    expect = spec["expect"]
    connection = db(backend)
    mission_id = record.get("mission_id")
    checks = _common(connection, mission_id)
    accepted = live_accepted(connection, mission_id)
    shared_path = spec.get("shared_path", "costs.json")
    record["shared_output_acceptances"] = accepted.get(shared_path, [])
    checks["shared_step_accepted_once"] = len({task for task, _ in accepted.get(shared_path, [])}) == 1

    def body(path: str) -> list[str]:
        return [artifact_bytes(backend, h).decode("utf-8") for _, h in accepted.get(path, [])]

    for path in ("costs.json", "finance.csv", "summary.md"):
        checks[f"{path}_accepted"] = path in accepted
    try:
        costs = [json.loads(text) for text in body("costs.json")]
        ok = bool(costs)
        for item in costs:
            rows = {str(r.get("item")): r.get("cost") for r in item.get("rows") or ()}
            ok = ok and float(rows.get("A", -1)) == expect["A"] and float(rows.get("B", -1)) == expect["B"] \
                and float(item.get("total_cost", -1)) == expect["total"] \
                and str(item.get("source_sha256", "")).lower() == expect["hash"]
        checks["costs_content"] = ok
        finance = body("finance.csv")
        checks["finance_content"] = bool(finance) and all(_finance_ok(text, expect) for text in finance)
        summary = body("summary.md")
        checks["summary_content"] = bool(summary) and all(_summary_ok(text, expect) for text in summary)
        record["deliverables"] = {"costs.json": costs, "finance.csv": finance, "summary.md": summary}
    except Exception as error:  # noqa: BLE001
        checks["deliverables_readable"] = f"{type(error).__name__}: {error}"
    checks["no_operations"] = connection.execute("SELECT count(*) FROM actions WHERE mission_id=?",
                                                 (mission_id,)).fetchone()[0] == 0
    checks["bad_draft_was_written"] = any(h.get("path") for h in record.get("hooks") or ())
    bad_hash = sha(spec.get("bad_content", BAD_SUMMARY))
    checks["bad_draft_never_accepted"] = all(h != bad_hash for _, h in accepted.get("summary.md", []))
    observed, detail = sharing_observations(connection, mission_id)
    record["observed"] = observed
    record["observed_detail"] = detail
    return checks


ORACLES = {"report": oracle_report, "publish": oracle_publish, "billing": oracle_billing, "shared": oracle_shared}


# ------------------------------------------------------------------ 入口
async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenarios", nargs="+", choices=sorted(SCENARIOS))
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--start-trial", type=int, default=1)
    parser.add_argument("--out", type=Path, default=REPO / ".local-test-evidence/2026-10-06/assurance-model")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    summary_path = args.out / "summary.jsonl"
    print(f"每局预算（{PLAN_SCENARIOS.relative_to(REPO)}）：{LIMITS}", flush=True)
    for name in args.scenarios:
        for trial in range(args.start_trial, args.start_trial + args.trials):
            run_dir, attempt = new_attempt_dir(args.out, name, trial)
            print(f"== {SCENARIOS[name]['title']} 第 {trial} 局 第 {attempt} 次尝试 {time.strftime('%H:%M:%S')}", flush=True)
            try:
                record = await run_trial(name, trial, attempt, run_dir)
            except Exception as error:  # noqa: BLE001
                record = {"scenario": name, "title": SCENARIOS[name]["title"], "trial": trial, "attempt": attempt,
                          "final_status": "INVALID_ENV", "error": f"{type(error).__name__}: {error}",
                          "passed": False, "within_budget": None,
                          "fail_reasons": [f"环境故障（INVALID_ENV）：{type(error).__name__}: {error}"]}
                (run_dir / "record.json").write_text(json.dumps(record, ensure_ascii=False, indent=1, default=str),
                                                     encoding="utf-8")
            line = summary_line(record, run_dir)
            with summary_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
            print(f"   结果 {record.get('final_status')} 通过={record.get('passed')} 在计划预算内={record.get('within_budget')} "
                  f"用量={record.get('usage')} 未过原因={record.get('fail_reasons')}"
                  + (f" 观察项={observed_text(record['observed'])}" if record.get("observed") is not None else ""),
                  flush=True)
    print_history(summary_path, args.scenarios)


def summary_line(record: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """summary.jsonl 的一行。有观察项的场景（V28）把 ``observed`` 单列，不混进通过与否。"""
    line = {k: record.get(k) for k in ("scenario", "title", "trial", "attempt", "mission_id", "final_status",
                                       "passed", "within_budget", "fail_reasons", "wall_seconds", "usage",
                                       "oracle", "error")}
    if "observed" in record or SCENARIOS.get(str(record.get("scenario")), {}).get("oracle") == "shared":
        line["observed"] = record.get("observed")  # 环境故障 / 判定出错时是 None：如实写"没有记录"
    line["run_dir"] = str(run_dir)
    return line


def observed_text(observed: dict[str, bool] | None) -> str:
    """观察项的中文写法："点名共用：观察到 / 换做法：没观察到"。"""
    names = {"named_sharing": "点名共用", "method_switched": "换做法"}
    if observed is None:
        return "没有记录（本局没跑到判定）"
    return " / ".join(f"{names.get(k, k)}：{'观察到' if v else '没观察到'}" for k, v in observed.items())


def print_history(summary_path: Path, scenarios: list[str]) -> None:
    """按局号列出历次尝试（含以前跑过的），不只给最后一次。"""
    rows = [json.loads(line) for line in summary_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print("\n== 历次尝试（同一局号多次尝试全部列出）", flush=True)
    for name in scenarios:
        for row in sorted((r for r in rows if r.get("scenario") == name),
                          key=lambda r: (int(r.get("trial") or 0), int(r.get("attempt") or 0))):
            print(f"   {name} 第 {row.get('trial')} 局 第 {row.get('attempt') or '?'} 次尝试：{row.get('final_status')} "
                  f"通过={row.get('passed')} 预算内={row.get('within_budget')} 未过原因={row.get('fail_reasons')}"
                  + (f" 观察项={observed_text(row.get('observed'))}" if "observed" in row else ""), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
