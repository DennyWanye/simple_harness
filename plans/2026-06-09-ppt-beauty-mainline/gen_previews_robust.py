# -*- coding: utf-8 -*-
"""鲁棒预览图生成: 每文件用带超时子进程渲染 —— WPS COM 对个别文件 Open
会挂死(非异常),超时即杀子进程+杀 WPS 残留,跳过续跑,绝不卡死整批。
用法: python gen_previews_robust.py <模板文件夹> [每文件超时秒,默认45]"""
import subprocess
import sys
import time
from pathlib import Path

D = Path(sys.argv[1] if len(sys.argv) > 1 else r"G:\projects\deskpet\resources\PPT_Template\02 高级简约")
TIMEOUT = int(sys.argv[2]) if len(sys.argv) > 2 else 45
OUT = D / "预览图"
OUT.mkdir(parents=True, exist_ok=True)
PY = r"G:\projects\deskpet\backend\.venv\Scripts\python.exe"
ONE = r"G:\projects\deskpet\plans\2026-06-09-ppt-beauty-mainline\preview_one.py"

def kill_wps():
    for name in ("wpp.exe", "wps.exe", "wpscloudsvr.exe", "et.exe"):
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True)

files = sorted(D.glob("*.pptx"))
print(f"{len(files)} templates in {D.name}  timeout={TIMEOUT}s", flush=True)
ok = fail = skip = 0
for f in files:
    png = OUT / (f.stem + ".png")
    if png.is_file() and png.stat().st_size > 1024:
        skip += 1
        continue
    t0 = time.time()
    try:
        r = subprocess.run([PY, ONE, str(f), str(png)],
                           capture_output=True, timeout=TIMEOUT)
        if png.is_file() and png.stat().st_size > 1024:
            ok += 1
            print(f"[ok {time.time()-t0:.0f}s] {f.name}", flush=True)
        else:
            fail += 1
            print(f"[FAIL] {f.name}: {r.stderr.decode('gbk','ignore')[:80]}", flush=True)
    except subprocess.TimeoutExpired:
        fail += 1
        print(f"[TIMEOUT {TIMEOUT}s] {f.name} — 杀 WPS 跳过", flush=True)
        kill_wps()
        time.sleep(2)
print(f"DONE ok={ok} skip={skip} fail={fail} -> {OUT}", flush=True)
