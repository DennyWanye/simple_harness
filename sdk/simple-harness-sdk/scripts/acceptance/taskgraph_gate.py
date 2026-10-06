# SPDX-License-Identifier: Apache-2.0
"""执行图部署验收门（TaskGraph 原计划 §0.3、§16；补齐第 1 批 V12）。

原计划要求"前置门禁通过才开生产开关"，完成关口有五道：TASKGRAPH_SQL_PASS、TASKGRAPH_CORE_PASS、
TASKGRAPH_REAL_MODEL、TASKGRAPH_HOST_SEAM、INDEPENDENT_REVIEW。这个脚本把能自动跑的四道一次跑完，
写一份门报告；``scripts/build/taskgraph_manifest.py validate --gate 报告`` 读到 PASS 的报告才把部署
清单的 ``taskgraph_acceptance`` 从 ``NOT_RUN`` 改成 ``VALIDATED``。Host 启动时只认 VALIDATED。

四道自动关口（任一失败整体 FAIL）：

- SQL：数据表守护用例 ``tests/orchestrator/product_world/test_table_guards.py``。
- CORE：执行图产品同形用例 ``tests/orchestrator/full_target/taskgraph_exec/`` + 随机动作序列
  ``tests/orchestrator/product_world/test_random_sequences.py``（默认规模）+ 12 条定点改坏 M01～M12
  全部 KILLED（用 ``scripts/acceptance/run_mutations.py`` 的判定）。
- HOST_SEAM：Host 执行图接缝用例 ``backend/tests/orchestration/test_*taskgraph*.py``。它们对着 Host
  **已安装**的 SDK 轮子跑，而门要验的是**这一份**源码；所以默认记 ``DEFERRED_TO_HOST_PIN``，由发版脚本在
  Host 钉上新轮子之后跑（``scripts/release_sdk_opt.sh`` 的钉版测试清单里有这五个文件）。手工排查可加
  ``--with-host`` 当场跑，跑了就参与判定。
- REAL_MODEL、INDEPENDENT_REVIEW：按原计划允许标 PENDING；报告里如实写 PENDING 并指向现有证据目录，
  不参与 PASS/FAIL 判定。

顺序：先跑改坏（它会反复重生成部署清单），再重生成一次清单取得 ``source_files_sha256``，再跑用例。
用例对着这一份源码跑，报告里带这个哈希，``validate`` 只认哈希相同的报告。

用法（在 ``sdk/simple-harness-sdk`` 下）::

    uv run --frozen python scripts/acceptance/taskgraph_gate.py [--upstream 上游证据.json] [--with-host]

报告写到 ``<仓库>/.local-test-evidence/<日期>/taskgraph-gate/gate-<时刻>.json``，最后一行打印 ``{"gate": 路径, "status": ...}``。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path

SDK = Path(__file__).resolve().parents[2]
REPO = SDK.parents[1]
BACKEND = REPO / "backend"
MANIFEST = SDK / "src/agent_orchestrator/orchestrator/taskgraph_deployment_manifest.json"
DEFAULT_UPSTREAM = REPO / ".local-test-evidence/2026-10-05/f2-upstream/evidence.json"
MUTATIONS = tuple(f"M{n:02d}" for n in range(1, 13))
SQL_TESTS = ("tests/orchestrator/product_world/test_table_guards.py",)
CORE_TESTS = ("tests/orchestrator/full_target/taskgraph_exec",
              "tests/orchestrator/product_world/test_random_sequences.py")
HOST_TESTS = ("tests/orchestration/test_taskgraph_bound_at_creation.py",
              "tests/orchestration/test_taskgraph_execution_reads.py",
              "tests/orchestration/test_taskgraph_operator_verbs.py",
              "tests/orchestration/test_ws_taskgraph_routing.py",
              "tests/orchestration/test_support_export_taskgraph_history.py")

sys.path.insert(0, str(SDK / "scripts/acceptance"))
sys.path.insert(0, str(SDK / "scripts/build"))
import run_mutations  # noqa: E402
import taskgraph_manifest  # noqa: E402


def _pytest(cwd: Path, command: list[str], targets: tuple[str, ...]) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="tg-gate-") as scratch:
        report = Path(scratch) / "junit.xml"
        done = subprocess.run([*command, "-q", "-p", "no:cacheprovider", f"--junitxml={report}", *targets],
                              cwd=cwd, capture_output=True, text=True)
        counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
        failed: list[str] = []
        if report.exists():
            root = ElementTree.parse(report).getroot()
            for suite in ([root] if root.tag == "testsuite" else root.iter("testsuite")):
                for key in counts:
                    counts[key] += int(suite.get(key) or 0)
            for case in root.iter("testcase"):
                if case.find("failure") is not None or case.find("error") is not None:
                    failed.append(f"{case.get('classname')}::{case.get('name')}")
        return {"targets": list(targets), "returncode": done.returncode, **counts, "failed": failed[:50],
                "tail": done.stdout[-600:] if done.returncode else ""}


def _passed(result: dict[str, object]) -> bool:
    return result["returncode"] == 0 and int(result["tests"]) > 0 and not result["failed"]


def run_gate(upstream: Path, *, with_host: bool) -> dict[str, object]:
    started = datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat()
    # 1 改坏（每条都会重生成清单、清字节码）
    mutation_rows = run_mutations.run(list(MUTATIONS), upstream)
    mutations = {"rows": mutation_rows,
                 "all_killed": all(row["verdict"] == "KILLED" for row in mutation_rows) and len(mutation_rows) == len(MUTATIONS)}
    # 2 清单回到 NOT_RUN，记下这份源码清单的哈希
    run_mutations._regenerate(upstream)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    source_hash = taskgraph_manifest.source_files_sha256(manifest)
    # 3 用例
    sdk_cmd = ["uv", "run", "--frozen", "--group", "dev", "--extra", "local-capacity", "pytest"]
    sql = _pytest(SDK, sdk_cmd, SQL_TESTS)
    core = _pytest(SDK, sdk_cmd, CORE_TESTS)
    host = (_pytest(BACKEND, ["uv", "run", "--frozen", "python", "-m", "pytest"], HOST_TESTS) if with_host
            else {"deferred": "Host 接缝用例对着已安装轮子跑，由发版脚本在钉上新轮子后执行", "targets": list(HOST_TESTS)})
    gates = {
        "TASKGRAPH_SQL_PASS": "PASS" if _passed(sql) else "FAIL",
        "TASKGRAPH_CORE_PASS": "PASS" if _passed(core) and mutations["all_killed"] else "FAIL",
        "TASKGRAPH_HOST_SEAM": ("PASS" if _passed(host) else "FAIL") if with_host else "DEFERRED_TO_HOST_PIN",
        "TASKGRAPH_REAL_MODEL": "PENDING",
        "INDEPENDENT_REVIEW": "PENDING",
    }
    status = "PASS" if "FAIL" not in gates.values() else "FAIL"
    return {
        "schema": "taskgraph-acceptance-gate-v1", "status": status, "run_at": started,
        "source_files_sha256": source_hash, "deployment_id_at_run": manifest["deployment_id"],
        "gates": gates, "sql": sql, "core": core, "host": host, "mutations": mutations,
        "pending": {"TASKGRAPH_REAL_MODEL": "真实模型局证据：.local-test-evidence/2026-10-05/f2-ui（资料换版本局通过）；"
                                            "'分支共用并换做法'一局待补（补齐清单 V28）",
                    "INDEPENDENT_REVIEW": "独立核验按六道关口重做待补（补齐清单 V29）"},
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM)
    parser.add_argument("--with-host", action="store_true", help="当场也跑 Host 接缝用例（对着已安装轮子）")
    args = parser.parse_args(argv)
    report = run_gate(args.upstream.resolve(), with_host=args.with_host)
    out = REPO / ".local-test-evidence" / datetime.date.today().isoformat() / "taskgraph-gate"
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"gate-{datetime.datetime.now().strftime('%H%M%S')}.json"  # 每次一份，不覆盖
    target.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"gate": str(target), "status": report["status"], "gates": report["gates"],
                      "gate_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
