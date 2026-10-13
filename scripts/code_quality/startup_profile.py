#!/usr/bin/env python3
"""代码质量整治阶段 3：启动耗时与启动后已加载模块（导入主入口 + 跑完启动流程）。

上提函数内导入会把加载时间从启动流程挪到导入阶段，单看导入耗时会误判，所以看总耗时；
同时列出启动完成后多加载的模块，重型或可选包（只该按需加载的）出现即不通过。
用法：
  python3 scripts/code_quality/startup_profile.py --write   # 记基线
  python3 scripts/code_quality/startup_profile.py           # 与基线比
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
BASELINE = REPO / "scripts/code_quality/startup_profile_baseline.json"
HEAVY = ("tkinter", "_tkinter", "torch", "torchaudio", "PIL", "pptx", "playwright", "browser_use",
         "faster_whisper", "silero_vad", "rapidocr_onnxruntime", "onnxruntime", "transformers",
         "tokenizers", "mss", "pyautogui", "sqlite_vec", "trafilatura", "selectolax", "anthropic")

CHILD = r"""
import asyncio, json, os, sys, time, warnings
warnings.simplefilter("ignore")
_rebase = os.environ.get("CQ_ORIGIN_REBASE")
if _rebase:
    sys.path.insert(0, os.environ["CQ_PLUGIN_DIR"])
    import origin_rebase_plugin  # noqa: F401
t0 = time.perf_counter()
import main
t1 = time.perf_counter()
async def go():
    try:
        async with asyncio.timeout(400):
            async with main.app.router.lifespan_context(main.app):
                t2 = time.perf_counter()
                mods = sorted(m for m in sys.modules if m != "origin_rebase_plugin")
                sys.stdout.write("@@" + json.dumps({"import_s": t1 - t0, "lifespan_s": t2 - t1, "modules": mods}))
                sys.stdout.flush()
                os._exit(0)
    except BaseException as e:
        sys.stderr.write("startup failed: %r" % (e,))
        os._exit(2)
asyncio.run(go())
"""


def capture() -> dict:
    runs = []
    for _ in range(3):
        with tempfile.TemporaryDirectory(prefix="cq-profile-") as raw:
            tmp = os.path.realpath(raw)
            os.makedirs(f"{tmp}/data/data", exist_ok=True)
            env = dict(os.environ)
            env.update({"DESKPET_USER_DATA_DIR": f"{tmp}/data", "DESKPET_USER_CACHE_DIR": f"{tmp}/cache",
                        "DESKPET_USER_LOG_DIR": f"{tmp}/log", "DESKPET_DEEPRESEARCH_DIR": f"{tmp}/dr",
                        "PYTHONPATH": str(BACKEND), "DESKPET_BACKEND_PORT": "18934",
                        "CQ_PLUGIN_DIR": str(Path(__file__).resolve().parent)})
            venv_backend = (BACKEND / ".venv").resolve().parent
            if venv_backend != BACKEND.resolve():
                env["CQ_ORIGIN_REBASE"] = f"{venv_backend}/vendor|{BACKEND.resolve()}/vendor"
            p = subprocess.run([str(BACKEND / ".venv/bin/python"), "-c", CHILD], cwd=BACKEND,
                               capture_output=True, text=True, env=env, timeout=900)
        if "@@" not in p.stdout:
            raise SystemExit(f"启动失败 rc={p.returncode}\n{p.stderr[-2000:]}")
        runs.append(json.loads(p.stdout.split("@@", 1)[1]))
    runs.sort(key=lambda r: r["import_s"] + r["lifespan_s"])
    mid = runs[1]
    return {"total_s": round(mid["import_s"] + mid["lifespan_s"], 3), "import_s": round(mid["import_s"], 3),
            "modules": mid["modules"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    now = capture()
    if a.write:
        BASELINE.write_text(json.dumps(now, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
        print(f"启动耗时基线：合计 {now['total_s']} 秒（导入 {now['import_s']}），模块 {len(now['modules'])} 个")
        return 0
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    extra = sorted(set(now["modules"]) - set(base["modules"]))
    heavy = [m for m in extra if m.split(".")[0] in HEAVY]
    print(f"启动耗时：基线合计 {base['total_s']} 秒，现在 {now['total_s']} 秒（导入 {base['import_s']}→{now['import_s']}）")
    print(f"启动完成后多加载的模块 {len(extra)} 个：{', '.join(extra[:60])}")
    ok = now["total_s"] <= base["total_s"] * 1.10 and not heavy
    if heavy:
        print("多加载了重型或可选包：" + ", ".join(heavy))
    print("启动耗时检查：" + ("通过" if ok else "不通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
