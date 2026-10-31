#!/usr/bin/env python3
"""代码质量整治：三套测试全量跑一遍，产出可比对的失败名单。

用法：
    python3 scripts/code_quality/full_regression.py --out <目录> [--only host,frontend,sdk]

做的事：
- Host：backend 下整套 pytest，一次跑完，出 junit xml。
- 前端：tauri-app 下 vitest run。
- SDK：逐文件跑（整套单进程会在约 89% 处卡死，见 2026-10-08 SDK 组记录）。
- 每个子进程配卡死看门狗：输出 N 秒不动就杀整棵进程树，记为 STALL。
- 结果写 <out>/summary.json：每套的通过/失败数与失败用例名单。

比对：python3 scripts/code_quality/compare_regression.py <基线 summary.json> <新 summary.json>
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
SDK = REPO / "sdk" / "simple-harness-sdk"
FRONTEND = REPO / "tauri-app"


def run_watched(cmd: list[str], cwd: Path, log: Path, stall_s: int, env: dict | None = None) -> str:
    """跑命令，输出写 log。返回 'exit=<n>' 或 'STALL'。"""
    log.parent.mkdir(parents=True, exist_ok=True)
    last = [time.monotonic()]
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True, env=env,
        )

        def pump() -> None:
            # 按字节块读，不按行：pytest -q 一个文件只打点不换行，按行读会让一个跑十几分钟
            # 的大文件在看门狗眼里"没输出"而被误杀。
            assert proc.stdout is not None
            fd = proc.stdout.fileno()
            while True:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                fh.write(chunk.decode("utf-8", errors="replace"))
                fh.flush()
                last[0] = time.monotonic()

        t = threading.Thread(target=pump, daemon=True)
        t.start()
        while proc.poll() is None:
            time.sleep(5)
            if proc.poll() is None and time.monotonic() - last[0] > stall_s:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                t.join(timeout=5)
                fh.write(f"\nSTALL: no output for {stall_s}s, killed process group\n")
                return "STALL"
        t.join(timeout=10)
        fh.write(f"\nexit={proc.returncode}\n")
        return f"exit={proc.returncode}"


def junit_failures(xml_path: Path) -> tuple[dict, list[str]]:
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    failed: list[str] = []
    if not xml_path.exists():
        return counts, failed
    root = ET.parse(xml_path).getroot()
    for case in root.iter("testcase"):
        counts["tests"] += 1
        name = f"{case.get('classname')}::{case.get('name')}"
        if case.find("failure") is not None:
            counts["failures"] += 1
            failed.append(name)
        elif case.find("error") is not None:
            counts["errors"] += 1
            failed.append(name)
        elif case.find("skipped") is not None:
            counts["skipped"] += 1
    return counts, sorted(failed)


def run_host(out: Path, stall: int) -> dict:
    xml = out / "host.xml"
    env = dict(os.environ)
    plugin: list[str] = []
    venv_backend = (BACKEND / ".venv").resolve().parent
    if venv_backend != BACKEND.resolve():  # 工作树借用主检出的虚拟环境：改写已安装 SDK 的来源路径
        env["CQ_ORIGIN_REBASE"] = f"{venv_backend}/vendor|{BACKEND.resolve()}/vendor"
        env["PYTHONPATH"] = os.pathsep.join(
            x for x in (str(Path(__file__).resolve().parent), env.get("PYTHONPATH", "")) if x
        )
        plugin = ["-p", "origin_rebase_plugin"]
    status = run_watched(
        [str(BACKEND / ".venv/bin/python"), "-m", "pytest", "-q", "-p", "no:cacheprovider",
         *plugin, f"--junitxml={xml}"],
        BACKEND, out / "host.log", stall, env=env,
    )
    counts, failed = junit_failures(xml)
    return {"status": status, **counts, "failed": failed}


def run_frontend(out: Path, stall: int) -> dict:
    xml = out / "frontend.xml"
    status = run_watched(
        ["npx", "vitest", "run", "--reporter=default", "--reporter=junit", f"--outputFile.junit={xml}"],
        FRONTEND, out / "frontend.log", stall,
    )
    counts, failed = junit_failures(xml)
    return {"status": status, **counts, "failed": failed}


def run_sdk(out: Path, stall: int) -> dict:
    py = str(SDK / ".venv/bin/python")
    listing = subprocess.run(
        [py, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=SDK, capture_output=True, text=True,
    ).stdout.splitlines()
    files = sorted({line.split("::")[0] for line in listing if "::" in line})
    per = out / "sdk-files"
    total = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    failed: list[str] = []
    stalls: list[str] = []
    file_status: dict[str, str] = {}
    for i, f in enumerate(files, 1):
        tag = f.replace("/", "__")
        xml = per / f"{tag}.xml"
        status = run_watched(
            [py, "-m", "pytest", f, "-q", "-p", "no:cacheprovider", f"--junitxml={xml}"],
            SDK, per / f"{tag}.log", stall,
        )
        file_status[f] = status
        counts, fl = junit_failures(xml)
        for k in total:
            total[k] += counts[k]
        failed += fl
        if status == "STALL":
            stalls.append(f)
        if status not in ("exit=0", "exit=5") and not fl and status != "STALL":
            failed.append(f"{f}::<file-level {status}>")
        (out / "sdk-progress.txt").write_text(f"{i}/{len(files)} {f} {status}\n", encoding="utf-8")
    return {"status": "per-file", "files": len(files), **total, "failed": sorted(failed),
            "stalls": stalls, "file_status": file_status}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="host,frontend,sdk")
    ap.add_argument("--stall", type=int, default=600, help="无输出多少秒算卡死")
    a = ap.parse_args()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    summary["head"] = head
    for part in a.only.split(","):
        t0 = time.time()
        res = {"host": run_host, "frontend": run_frontend, "sdk": run_sdk}[part](out, a.stall)
        res["seconds"] = round(time.time() - t0)
        summary[part] = res
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{part}: {res['status']} tests={res['tests']} failed={len(res['failed'])} {res['seconds']}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
