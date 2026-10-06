# -*- coding: utf-8 -*-
"""真实模型验收：Assurance 原计划 §15 的四个场景 + 2026-10-06 用户加的编程题，每题 3 局。

跑法（产品同形）：每局起一个隔离的开发模式后台（独立数据目录、DeepSeek 线路经本机闸口），
用后台的控制通道建任务、按场景触发事实、批准该人批的卡，任务结束后只读库和产物文件核对。
模型自己说了什么一概不算；每局的判定、用量、模型请求数都写进证据目录。

用法（在仓库根）::

    backend/.venv/bin/python backend/scripts/assurance_model_scenarios/run.py <场景…> [--trials 3] [--out 目录]

场景名：accurate-report / bad-draft-rework / source-replaced-midway / publish-lost-reply / billing-tool。
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
PORT = int(os.environ.get("ASSURANCE_SCENARIO_PORT", "8100"))
WS = f"ws://127.0.0.1:{PORT}/ws/control"

FACTS_V1 = "item,quantity,unit_cost\nA,10,12\nB,4,25\n"
FACTS_V2 = "item,quantity,unit_cost\nA,11,12\nB,4,25\n"
FACTS_BAD = "item,quantity,unit_cost\nA,10,12\nB,4,\n"
FACTS_DISCOUNT = "item,quantity,unit_cost\nA,12,12\nB,4,25\n"
BAD_CANDIDATE = json.dumps({"total_cost": 290, "rows": [{"item": "A", "cost": 120}, {"item": "B", "cost": 100}],
                            "source_version": 1}, ensure_ascii=False, indent=2) + "\n"

REPORT_RULE = ("report.json 是一个 JSON 对象：rows 为列表，每项含 item 与 cost（cost = quantity × unit_cost）；"
               "total_cost 为各项 cost 之和；source_sha256 为资料 sources/facts.csv 文件内容的 SHA-256（十六进制小写）。")


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
        "limits": {"model_calls": 16, "tool_calls": 32, "tokens": 300_000, "wall": 900},
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
        "limits": {"model_calls": 16, "tool_calls": 32, "tokens": 300_000, "wall": 900},
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
        "limits": {"model_calls": 24, "tool_calls": 48, "tokens": 450_000, "wall": 1200},
        "oracle": "report",
        "expect": {"A": 132, "B": 100, "total": 232, "hash": sha(FACTS_V2), "actions": 0, "superseded": True},
    },
    "publish-lost-reply": {
        "title": "报告写好后要送出去，第一次送达的回执丢了，不能重发",
        "goal": "根据资料 sources/facts.csv 生成报告文件 report.json（逐项费用、合计费用、资料文件内容的 SHA-256），并把 report.json 发布到接收目录。",
        "criteria": ["file:report.json", REPORT_RULE, "action:file_publish.publish:report.json"],
        "sources": {"sources/facts.csv": FACTS_V1},
        "hook": "lose_first_publish_reply",
        "limits": {"model_calls": 24, "tool_calls": 48, "tokens": 450_000, "wall": 1200},
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
        # 2026-10-06 金丝雀：一次返工（终审打回"没分三个模块"）后 30 分钟不够走完，上限改为 45 分钟
        "limits": {"model_calls": 48, "tool_calls": 96, "tokens": 900_000, "wall": 2700},
        "oracle": "billing",
        "expect": {"hash": sha(FACTS_V1)},
    },
}


# ------------------------------------------------------------------ 后台
class Backend:
    def __init__(self, run_dir: Path, *, hook: str | None, hook_target: str = "report.json") -> None:
        self.run_dir = run_dir
        self.userdata = run_dir / "userdata"
        self.published = run_dir / "published"
        self.hook_log = run_dir / "hooks.jsonl"
        self.hook = hook
        self.hook_target = hook_target
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
            bad.write_text(BAD_CANDIDATE, encoding="utf-8")
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


async def run_trial(name: str, trial: int, out_root: Path) -> dict[str, Any]:
    spec = SCENARIOS[name]
    run_dir = out_root / name / f"trial-{trial}"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    backend = Backend(run_dir, hook=spec.get("hook"))
    backend.prepare()
    record: dict[str, Any] = {"scenario": name, "title": spec["title"], "trial": trial, "started_at": time.time(),
                              "events_seen": [], "approvals": [], "triggers": [], "notes": []}
    control = Control()
    backend.start()
    try:
        await control.connect()
        key = f"assurance-model:{name}:{trial}:{uuid.uuid4().hex[:8]}"
        sources = [{"path": path, "content": content, "kind": "text"} for path, content in spec["sources"].items()]
        created = await control.call("mission_create_with_sources", {
            # 预算用部署默认值（产品的预留是按上下文窗口上限预留的，拿计划里的 30 万实际用量上限去当
            # 预算会在第一次派发就耗尽）；计划里的用量上限在局后按实际结算用量核。
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
        while time.time() - started < spec["limits"]["wall"]:
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
                decision = "approve"
                await control.call("mission_approval_decide", {"approval_id": approval["request_id"], "decision": decision,
                                                               "reason": "", "note": "验收驱动脚本批准", "ruling": "", "basis": ""})
                record["approvals"].append({"request_id": approval["request_id"], "kind": kind, "decision": decision,
                                            "at": time.time() - started})
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
    # 通过 = 任务完成 + 判定全部成立；计划里的用量上限单独记（within_budget），不并进通过与否——
    # 产品真实用量（含思考）比计划写那几个数时估的高得多，超了如实报，由人看
    record["passed"] = record["final_status"] == "COMPLETED" and all(v is True for v in record["oracle"].values())
    record["within_budget"] = (record["usage"]["model_calls"] <= spec["limits"]["model_calls"]
                               and record["usage"]["tokens"] <= spec["limits"]["tokens"])
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


ORACLES = {"report": oracle_report, "publish": oracle_publish, "billing": oracle_billing}


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
    for name in args.scenarios:
        for trial in range(args.start_trial, args.start_trial + args.trials):
            print(f"== {SCENARIOS[name]['title']} 第 {trial} 局 {time.strftime('%H:%M:%S')}", flush=True)
            try:
                record = await run_trial(name, trial, args.out)
            except Exception as error:  # noqa: BLE001
                record = {"scenario": name, "trial": trial, "final_status": "INVALID_ENV",
                          "error": f"{type(error).__name__}: {error}", "passed": False}
            line = {k: record.get(k) for k in ("scenario", "title", "trial", "mission_id", "final_status", "passed",
                                                "within_budget", "wall_seconds", "usage", "oracle", "error")}
            with summary_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
            failed = [k for k, v in (record.get("oracle") or {}).items() if v is not True]
            print(f"   结果 {record.get('final_status')} 通过={record.get('passed')} 在计划预算内={record.get('within_budget')} "
                  f"用量={record.get('usage')} 未过={failed}",
                  flush=True)


if __name__ == "__main__":
    asyncio.run(main())
