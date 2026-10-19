#!/usr/bin/env python3
"""代码质量整治：启动烟雾 + 服务注册快照 + 路由集合（计划 0.4、0.6、4.1）。

在隔离的用户数据目录里真跑一遍 FastAPI 生命周期（全新安装路径），记录：
- 路由集合：方法、路径、名字。
- 服务注册顺序：ServiceContext.register 的调用序列（名字 + 值的类型名）。
- 启动后每个服务槽的值类型名（None 记 None）。
- 启动是否报错、关闭是否报错（地址抹掉后比对）。

已知（2026-10-12 录基线时发现，属已有缺陷，本轮只改形状不修）：关闭时 MCP 管理器在另一个
任务里退出 stdio 客户端，anyio 取消作用域漏出 CancelledError。基线里记着这条，比对时一致即可。

拆主入口、改生命周期步骤、收 AppState 时，这三样都不许变。

用法：
    python3 scripts/code_quality/startup_snapshot.py --write   # 写基线
    python3 scripts/code_quality/startup_snapshot.py           # 与基线比对
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

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
BASELINE = REPO / "scripts/code_quality/startup_baseline.json"

CHILD = r"""
import asyncio, json, re, sys, traceback, warnings
warnings.simplefilter("ignore")
import os as _os
_rebase = _os.environ.get("CQ_ORIGIN_REBASE")  # "主检出 backend 目录|本检出 backend 目录"
if _rebase:
    # 工作树借用主检出的虚拟环境：已安装包记录的来源是主检出的 wheel。
    # 只在快照子进程里把来源路径改写到本检出的同名 wheel（字节相同），生产代码不动。
    import importlib.metadata as _md
    _src, _dst = _rebase.split("|")
    _orig_dist = _md.distribution
    class _Dist:
        def __init__(self, d): self._d = d
        def __getattr__(self, k): return getattr(self._d, k)
        def read_text(self, name):
            t = self._d.read_text(name)
            return t.replace(_src, _dst) if (name == "direct_url.json" and t) else t
    def _distribution(name):
        d = _orig_dist(name)
        return _Dist(d) if name == "simple-harness-sdk" else d
    _md.distribution = _distribution
import context as _ctx
order = []
_orig = _ctx.ServiceContext.register
def _rec(self, name, provider):
    order.append([name, None if provider is None else type(provider).__name__])
    return _orig(self, name, provider)
_ctx.ServiceContext.register = _rec
import main
import host.runtime as _hr

# 关闭阶段的日志事件（生命周期用的共享记录器），只记事件名第一个词
_events = []
_capturing = [False]
for _lvl in ("debug", "info", "warning", "error", "exception"):
    def _wrap(event=None, *a, _o=getattr(_hr.logger, _lvl), _l=_lvl, **k):
        if _capturing[0]:
            _events.append([_l, str(event).split(" ")[0][:80]])
        return _o(event, *a, **k)
    setattr(_hr.logger, _lvl, _wrap)

def tname(v):
    return None if v is None else type(v).__name__

async def go():
    out = {"startup_error": None, "shutdown_error": None}
    routes = []
    for r in main.app.routes:
        methods = sorted(getattr(r, "methods", None) or [])
        routes.append([",".join(methods) or type(r).__name__, getattr(r, "path", ""), getattr(r, "name", "")])
    out["routes"] = sorted(routes)
    out["import_time_registrations"] = list(order)
    order.clear()
    phase = "startup"
    try:
        async with asyncio.timeout(400):
            async with main.app.router.lifespan_context(main.app):
                out["lifespan_registrations"] = list(order)
                sc = _hr.service_context
                out["services_after_startup"] = {
                    k: tname(getattr(sc, k, None)) for k in sorted(_ctx._VALID_SERVICES)}
                phase = "shutdown"
                order.clear()
                _capturing[0] = True
    except BaseException as e:
        msg = "".join(traceback.format_exception_only(type(e), e)).strip()
        msg = re.sub(r"<Task .*$", "<Task>", msg, flags=re.S)
        msg = re.sub(r"0x[0-9a-f]+|\b[0-9a-f]{9,}\b", "<addr>", msg)[:800]
        out[f"{phase}_error"] = msg
        if _os.environ.get("CQ_DEBUG"):
            sys.stderr.write("".join(traceback.format_exception(e)))
        out.setdefault("lifespan_registrations", list(order))
    if phase == "shutdown":
        _capturing[0] = False
        out["shutdown_registrations"] = list(order)
        out["shutdown_events"] = list(_events)
    return out

res = asyncio.run(go())
sys.stdout.write("\n@@RESULT@@" + json.dumps(res, ensure_ascii=False))
sys.stdout.flush()
import os; os._exit(0)
"""


def capture() -> dict:
    with tempfile.TemporaryDirectory(prefix="cq-startup-") as raw:
        tmp = os.path.realpath(raw)  # macOS 临时目录经过符号链接，状态库拒绝符号链接路径
        env = dict(os.environ)
        env.update({
            "DESKPET_USER_DATA_DIR": f"{tmp}/data", "DESKPET_USER_CACHE_DIR": f"{tmp}/cache",
            "DESKPET_USER_LOG_DIR": f"{tmp}/log", "DESKPET_DEEPRESEARCH_DIR": f"{tmp}/dr",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(BACKEND),
            "DESKPET_BACKEND_PORT": "18931",
        })
        venv_backend = (BACKEND / ".venv").resolve().parent
        if venv_backend != BACKEND.resolve():
            env["CQ_ORIGIN_REBASE"] = f"{venv_backend}/vendor|{BACKEND.resolve()}/vendor"
        p = subprocess.run([str(BACKEND / ".venv/bin/python"), "-c", CHILD], cwd=BACKEND,
                           capture_output=True, text=True, env=env, timeout=600)
    if "@@RESULT@@" not in p.stdout:
        raise SystemExit(f"启动快照进程异常 rc={p.returncode}\n{p.stderr[-3000:]}")
    return json.loads(p.stdout.split("@@RESULT@@", 1)[1])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--baseline", default=str(BASELINE))
    a = ap.parse_args()
    snap = capture()
    errs = [k for k in ("startup_error", "shutdown_error") if snap.get(k)]
    for k in errs:
        print(f"{k}: {snap[k]}")
    print(f"路由 {len(snap['routes'])} 条；导入期注册 {len(snap['import_time_registrations'])} 次；"
          f"生命周期注册 {len(snap.get('lifespan_registrations', []))} 次")
    if a.write:
        Path(a.baseline).write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已写基线 {a.baseline}")
        return 0
    base = json.loads(Path(a.baseline).read_text(encoding="utf-8"))
    diff = list(difflib.unified_diff(
        json.dumps(base, ensure_ascii=False, indent=1).splitlines(),
        json.dumps(snap, ensure_ascii=False, indent=1).splitlines(), "基线", "现在", lineterm="", n=1))
    for line in diff[:200]:
        print(line)
    ok = not diff
    print("启动快照：" + ("一致" if ok else "不一致"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
