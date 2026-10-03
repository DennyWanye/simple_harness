# SPDX-License-Identifier: Apache-2.0
"""HTN 补齐 F1-5：按改坏清单逐条执行改坏检验。

每一条：先备份被改文件 → 把原文一段换成改坏后的写法 → 重生成部署清单、清字节码缓存 → 只跑绑定的
用例 → 判定 → 从备份恢复 → 核对文件哈希与改前一致 → 再重生成部署清单。**不用 git 恢复**（会冲掉
没提交的正式改动）。

判定只认行为断言：绑定用例里至少一条以 ``AssertionError`` 失败 = 抓到（KILLED）；全部通过 = 没抓到
（SURVIVED）；收集错误、导入错误、别的异常失败 = 无效（INVALID，原 TaskGraph 计划 §15 的规矩）。
没有绑定用例的条目记 SKIPPED（例如 TaskGraph 12 条，留联测补写）。

用法（在 ``sdk/simple-harness-sdk`` 下）::

    uv run --frozen python scripts/acceptance/run_mutations.py [编号 ...] [--upstream 上游证据.json]

结果写到 ``<仓库>/.local-test-evidence/<日期>/mutations/results.json``（不进仓库），并打印每条一行。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path

SDK = Path(__file__).resolve().parents[2]
REPO = SDK.parents[1]
CATALOGUE = SDK / "tests/orchestrator/acceptance_assets/mutations.json"
DEFAULT_UPSTREAM = Path("/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/"
                        "2026-09-27/batch2a-upstream/evidence.json")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean_bytecode() -> None:
    for cache in (SDK / "src").rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)


def _regenerate(upstream: Path) -> None:
    done = subprocess.run(["uv", "run", "--frozen", "python", "scripts/build/taskgraph_manifest.py", "generate",
                           "--upstream", str(upstream)], cwd=SDK, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit("deployment manifest could not be regenerated:\n" + done.stderr[-2000:])
    _clean_bytecode()


def _judge(tests: list[str]) -> tuple[str, list[str]]:
    with tempfile.TemporaryDirectory(prefix="mutation-") as scratch:
        report = Path(scratch) / "junit.xml"
        done = subprocess.run(["uv", "run", "--frozen", "pytest", "-q", "-p", "no:cacheprovider",
                               f"--junitxml={report}", *tests], cwd=SDK, capture_output=True, text=True)
        if done.returncode == 0:
            return "SURVIVED", []
        if not report.exists():
            return "INVALID", [done.stdout[-400:]]
        root = ElementTree.parse(report).getroot()
        failures, other = [], []
        for case in root.iter("testcase"):
            for kind in ("failure", "error"):
                for node in case.findall(kind):
                    name = f"{case.get('classname')}::{case.get('name')}"
                    text = (node.get("message") or "") + (node.text or "")[:200]
                    if kind == "failure" and "AssertionError" in ((node.get("type") or "") + text):
                        failures.append(name)
                    else:
                        other.append(f"{name}: {(node.get('type') or kind)}")
        if failures:
            return "KILLED", failures
        return "INVALID", other or [done.stdout[-400:]]


def run(ids: list[str], upstream: Path) -> list[dict[str, object]]:
    catalogue = json.loads(CATALOGUE.read_text())["mutations"]
    chosen = [row for row in catalogue if not ids or row["id"] in ids]
    results = []
    for row in chosen:
        if not row.get("file") or not row.get("tests"):
            results.append({"id": row["id"], "verdict": "SKIPPED", "detail": ["no bound test yet"]})
            print(f"{row['id']}: SKIPPED", flush=True)
            continue
        target = SDK / row["file"]
        original = target.read_text()
        if original.count(row["original"]) != 1:
            results.append({"id": row["id"], "verdict": "INVALID", "detail": ["anchor not found exactly once"]})
            print(f"{row['id']}: INVALID (anchor)", flush=True)
            continue
        before = _sha(target)
        backup = Path(tempfile.mkdtemp(prefix="mutation-backup-")) / target.name
        shutil.copy2(target, backup)
        try:
            target.write_text(original.replace(row["original"], row["replacement"]))
            _regenerate(upstream)
            verdict, detail = _judge(list(row["tests"]))
        finally:
            shutil.copy2(backup, target)
            _regenerate(upstream)
        restored = _sha(target) == before
        results.append({"id": row["id"], "verdict": verdict, "detail": detail, "restored": restored})
        print(f"{row['id']}: {verdict}{'' if restored else '  (RESTORE MISMATCH)'}", flush=True)
        if not restored:
            raise SystemExit(f"{row['file']} was not restored byte for byte; stopping")
    return results


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ids", nargs="*")
    parser.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM)
    args = parser.parse_args(argv)
    results = run(args.ids, args.upstream)
    out = REPO / ".local-test-evidence" / datetime.date.today().isoformat() / "mutations"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n")
    print(f"written: {out / 'results.json'}")
    return 0 if all(r["verdict"] in {"KILLED", "SKIPPED"} for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
