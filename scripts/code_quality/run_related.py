#!/usr/bin/env python3
"""代码质量整治：按文件跑相关测试，每个文件配卡死看门狗（每片必跑的"相关测试"）。

用法：
    python3 scripts/code_quality/run_related.py backend tests/a.py tests/b.py ...
    python3 scripts/code_quality/run_related.py backend @清单文件     （一行一个，相对 backend/）
    python3 scripts/code_quality/run_related.py sdk tests/x.py ...
    CQ_SDK_DIR=<快照>/sdk/simple-harness-sdk python3 ... sdk @清单   （在快照副本里跑）
每个文件单独起进程；输出 N 秒不动判卡死并杀整棵进程树。最后打印失败与卡死清单，有则退出码 1。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from full_regression import BACKEND, SDK, junit_failures, run_watched  # noqa: E402


def main() -> int:
    tree, *args = sys.argv[1:]
    files: list[str] = []
    for a in args:
        files += Path(a[1:]).read_text().split() if a.startswith("@") else [a]
    # CQ_SDK_DIR：在冻结的快照副本里跑 SDK 测试（snapshot_tests.sh），虚拟环境仍用工作树的
    cwd = BACKEND if tree == "backend" else Path(os.environ.get("CQ_SDK_DIR") or SDK)
    py = str((BACKEND if tree == "backend" else SDK) / ".venv/bin/python")
    env = dict(os.environ)
    plugin: list[str] = []
    if tree == "sdk":
        env["PYTHONPATH"] = str(cwd / "src")  # 虚拟环境的可编辑安装可能指向别的检出
    else:
        venv_backend = (cwd / ".venv").resolve().parent
        if venv_backend != cwd.resolve():  # 工作树借用主检出的虚拟环境
            env["CQ_ORIGIN_REBASE"] = f"{venv_backend}/vendor|{cwd.resolve()}/vendor"
            env["PYTHONPATH"] = os.pathsep.join(
                p for p in (str(Path(__file__).resolve().parent), env.get("PYTHONPATH", "")) if p
            )
            plugin = ["-p", "origin_rebase_plugin"]
    stall = int(os.environ.get("CQ_STALL", "300"))
    bad: list[str] = []
    with tempfile.TemporaryDirectory(prefix="cq-related-") as tmp:
        for i, f in enumerate(files, 1):
            xml = Path(tmp) / f"{i}.xml"
            log = Path(tmp) / f"{i}.log"
            status = run_watched([py, "-m", "pytest", f, "-q", "-p", "no:cacheprovider", *plugin, f"--junitxml={xml}"],
                                 cwd, log, stall, env=env)
            counts, failed = junit_failures(xml)
            mark = "ok" if status in ("exit=0", "exit=5") else status
            print(f"[{i}/{len(files)}] {f}: {mark} tests={counts['tests']} failed={len(failed)}", flush=True)
            if status == "STALL":
                bad.append(f"{f}::<STALL>")
            bad += failed
            if status not in ("exit=0", "exit=5", "STALL") and not failed:
                bad.append(f"{f}::<{status}>  " + log.read_text()[-600:].replace("\n", " | "))
    # 全量回归里已经失败的用例（CQ_KNOWN 指向那次的 summary.json）单列为"已知失败"，不算本批新失败
    known: set[str] = set()
    if os.environ.get("CQ_KNOWN"):
        summary = json.loads(Path(os.environ["CQ_KNOWN"]).read_text(encoding="utf-8"))
        known = set(summary.get("host" if tree == "backend" else "sdk", {}).get("failed", []))
    for b in bad:
        print("已知失败（全量回归已有）" if b in known else "失败", b)
    new = [b for b in bad if b not in known]
    print(f"相关测试：{len(files)} 个文件，失败 {len(new)}" + (f"（另有已知失败 {len(bad) - len(new)}）" if len(new) < len(bad) else ""))
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
