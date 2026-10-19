#!/usr/bin/env python3
"""代码质量整治：控制通道特性化快照。

在隔离用户目录里跑真实启动流程，然后对每类控制消息：
开一个新的控制连接 → 发一条最小消息 → 再发 ping 作结束标记 → 记录到 pong 为止的回包。
每个回包只记"类型、载荷字段名、错误码"，不记会变的值（编号、时间、路径）。

用法：
  python3 scripts/code_quality/control_snapshot.py            # 与基线比对
  python3 scripts/code_quality/control_snapshot.py --write    # 重写基线

排除的消息类型（会调模型或访问网络）见 EXCLUDED，它们由各自的单元测试覆盖。
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if sys.version_info < (3, 12):
    _py = Path(__file__).resolve().parents[2] / "backend/.venv/bin/python"
    if _py.exists():
        os.execv(str(_py), [str(_py), *sys.argv])

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
BASELINE = REPO / "scripts/code_quality/control_baseline.json"

MESSAGE_TYPES = [
    "__debug_dump_session_activity", "__debug_force_error_pending",
    "__debug_inject_supervisor_alert", "budget_status", "capability_activate",
    "capability_install", "capability_list", "capability_operation_cancel",
    "capability_operation_retry", "capability_operations_list", "capability_repair",
    "capability_rollback", "capability_uninstall", "chat_turn_timeout_get",
    "chat_turn_timeout_set", "chat_v2_interrupt", "clarification_response",
    "companion_profile_bind", "companion_profile_unbind",
    "context_breakdown_request", "context_usage_request", "external_wait_response",
    "harness_inspector_cancel", "harness_inspector_details_request",
    "harness_inspector_snapshot", "harness_inspector_snapshot_request",
    "human_memory_request", "interrupt", "memory_archive_list", "memory_clear",
    "memory_delete", "memory_export", "memory_list", "memory_summarize_now",
    "memory_thumbs_up", "mission_list", "orchestration_publish_dir_get",
    "orchestration_publish_dir_set", "permission_auto_mode_get", "permission_auto_mode_set",
    "permission_response", "plugin_disable", "plugin_enable", "plugin_list",
    "project_catalog_page", "project_directory_response", "project_inspect",
    "project_preview_register", "project_register", "project_relocate",
    "project_sessions_page", "protected_path_decision", "session_create", "session_delete",
    "session_health_check", "session_messages_load", "session_provider_get",
    "session_rename", "session_run_status_query", "session_set_model",
    "session_set_provider", "sessions_list", "settings_providers_add",
    "settings_providers_list_request", "settings_providers_remove",
    "settings_providers_reorder", "settings_providers_update", "skill_install_confirm",
    "skill_install_status", "skill_list_installed", "skill_uninstall", "supervisor_toggle",
    "supervisor_user_choice", "task_projection_update", "task_projections_list",
    "taskgraph.execution_detail", "unknown_type_for_snapshot",
]
# 会调模型或访问网络：chat、chat_v2、slash_command、plan_confirm、models_list、
# provider_test_connection、settings_providers_probe_models、skill_install_from_url、
# skill_marketplace_list、agent_skill_request、agent_skill_evaluation_dispatch、
# agent_skill_evaluation_mission
EXCLUDED = [
    "chat", "chat_v2", "slash_command", "plan_confirm", "models_list",
    "provider_test_connection", "settings_providers_probe_models", "skill_install_from_url",
    "skill_marketplace_list", "agent_skill_request", "agent_skill_evaluation_dispatch",
    "agent_skill_evaluation_mission",
]

CHILD = r"""
import asyncio, json, re, sys, warnings
warnings.simplefilter("ignore")
import importlib.metadata as _md, os as _os
_rebase = _os.environ.get("CQ_ORIGIN_REBASE")
if _rebase:
    _src, _dst = _rebase.split("|")
    _orig = _md.distribution
    class _D:
        def __init__(self, d): self._d = d
        def __getattr__(self, k): return getattr(self._d, k)
        def read_text(self, n):
            t = self._d.read_text(n)
            return t.replace(_src, _dst) if (n == "direct_url.json" and t) else t
    _md.distribution = lambda name: _D(_orig(name)) if name == "simple-harness-sdk" else _orig(name)
from starlette.websockets import WebSocketDisconnect
import main

TYPES = json.loads(sys.argv[1])
# 按路径从应用路由表取控制通道端点，不依赖它定义在哪个模块
CONTROL_ENDPOINT = next(r.endpoint for r in main.app.routes if getattr(r, "path", "") == "/ws/control")

class FakeWS:
    def __init__(self, frames):
        self.query_params = {"session_id": "snapshot-session"}
        self.headers = {}
        self._incoming = list(frames)
        self.sent = []
        self.closed = None
    async def accept(self):
        pass
    async def close(self, code=1000, reason=""):
        self.closed = [code, reason]
    async def send_json(self, frame):
        self.sent.append(frame)
    async def receive_text(self):
        await asyncio.sleep(0)
        if not self._incoming:
            raise WebSocketDisconnect(1000)
        return self._incoming.pop(0)

def shape(frame):
    if not isinstance(frame, dict):
        return [type(frame).__name__]
    out = [str(frame.get("type"))]
    payload = frame.get("payload")
    if isinstance(payload, dict):
        out.append(sorted(payload))
        for key in ("code", "reason", "ok", "error_code"):
            value = payload.get(key)
            if isinstance(value, (str, bool, int)):
                if isinstance(value, str):
                    value = re.sub(r"[0-9a-f]{8,}|/[^ ]+", "<v>", value)[:120]
                out.append([key, value])
    elif payload is not None:
        out.append(type(payload).__name__)
    return out

async def one(msg_type):
    ws = FakeWS([
        json.dumps({"type": msg_type, "payload": {}, "request_id": "snap"}),
        json.dumps({"type": "ping"}),
    ])
    try:
        await asyncio.wait_for(CONTROL_ENDPOINT(ws), timeout=30)
    except asyncio.TimeoutError:
        return {"timeout": True, "frames": [shape(f) for f in ws.sent]}
    except Exception as exc:
        return {"exception": type(exc).__name__, "frames": [shape(f) for f in ws.sent]}
    frames = []
    for f in ws.sent:
        if isinstance(f, dict) and f.get("type") == "pong":
            break
        frames.append(shape(f))
    return {"frames": frames, "closed": ws.closed}

async def go():
    out = {}
    try:
        async with asyncio.timeout(900):
            async with main.app.router.lifespan_context(main.app):
                for t in TYPES:
                    out[t] = await one(t)
    except BaseException as exc:  # 关闭阶段的取消异常是已记录的旧缺陷，不影响已收集的结果
        if len(out) != len(TYPES):
            raise
    return out

res = asyncio.run(go())
sys.stdout.write("\n@@RESULT@@" + json.dumps(res, ensure_ascii=False, sort_keys=True))
sys.stdout.flush()
os._exit(0)
"""


def capture() -> dict:
    with tempfile.TemporaryDirectory(prefix="cq-control-") as raw:
        tmp = os.path.realpath(raw)
        os.makedirs(f"{tmp}/data/data", exist_ok=True)
        env = dict(os.environ)
        env.update({
            "DESKPET_USER_DATA_DIR": f"{tmp}/data", "DESKPET_USER_CACHE_DIR": f"{tmp}/cache",
            "DESKPET_USER_LOG_DIR": f"{tmp}/log", "DESKPET_DEEPRESEARCH_DIR": f"{tmp}/dr",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(BACKEND), "PYTHONHASHSEED": "0",
            "DESKPET_BACKEND_PORT": "18933", "DESKPET_DEV_MODE": "1",
        })
        venv_backend = (BACKEND / ".venv").resolve().parent
        if venv_backend != BACKEND.resolve():
            env["CQ_ORIGIN_REBASE"] = f"{venv_backend}/vendor|{BACKEND.resolve()}/vendor"
        p = subprocess.run(
            [str(BACKEND / ".venv/bin/python"), "-c", CHILD, json.dumps(MESSAGE_TYPES)],
            cwd=BACKEND, capture_output=True, text=True, env=env, timeout=1200,
        )
    if "@@RESULT@@" not in p.stdout:
        raise SystemExit(f"控制通道快照进程异常 rc={p.returncode}\n{p.stderr[-3000:]}")
    return json.loads(p.stdout.split("@@RESULT@@", 1)[1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    now = capture()
    text = json.dumps(now, ensure_ascii=False, indent=1, sort_keys=True)
    bad = [t for t, r in now.items() if r.get("timeout") or r.get("exception")]
    print(f"消息类型 {len(now)} 种；超时或异常 {len(bad)} 种：{', '.join(bad) if bad else '无'}")
    if a.write:
        BASELINE.write_text(text + "\n", encoding="utf-8")
        print("控制通道快照：已写基线")
        return 0
    base = BASELINE.read_text(encoding="utf-8").rstrip("\n")
    if base == text:
        print("控制通道快照：一致")
        return 0
    sys.stdout.writelines(
        difflib.unified_diff(base.splitlines(True), (text + "\n").splitlines(True), "基线", "现在", n=2)
    )
    print("\n控制通道快照：不一致")
    return 1


if __name__ == "__main__":
    sys.exit(main())
