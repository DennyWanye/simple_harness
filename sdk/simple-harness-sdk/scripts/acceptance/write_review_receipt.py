# SPDX-License-Identifier: Apache-2.0
"""写独立核验回执（原计划 §16 的 INDEPENDENT_REVIEW 关口；补齐第 1 批评估处置 V12）。

独立核验员（只读子代理）写完评估文件后，由它或主会话跑这个脚本：读当前部署清单，算去掉两个版本文件后的
源码清单哈希（``taskgraph_manifest.review_source_sha256``），连同评估文件路径与哈希、结论、核验员名、时间写成回执。
``taskgraph_gate.py --review 回执`` 只认哈希相同且结论为 PASS 的回执；评估文件之后源码再改，回执就作废。

结论只能是 PASS 或 FAIL：评估文件里"必须处理"清单为空才是 PASS；有一条就是 FAIL，门不过。

用法（在 ``sdk/simple-harness-sdk`` 下）::

    uv run --frozen python scripts/acceptance/write_review_receipt.py --report <评估文件> --verdict PASS|FAIL --reviewer <名字>
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

SDK = Path(__file__).resolve().parents[2]
REPO = SDK.parents[1]
MANIFEST = SDK / "src/agent_orchestrator/orchestrator/taskgraph_deployment_manifest.json"

sys.path.insert(0, str(SDK / "scripts/build"))
import taskgraph_manifest  # noqa: E402


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--verdict", choices=("PASS", "FAIL"), required=True)
    parser.add_argument("--reviewer", required=True)
    args = parser.parse_args(argv)
    report = args.report.resolve()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    receipt = {
        "schema": "taskgraph-independent-review-v1",
        "review_source_sha256": taskgraph_manifest.review_source_sha256(manifest),
        "deployment_id_at_review": manifest["deployment_id"],
        "verdict": args.verdict,
        "reviewer": args.reviewer,
        "report": str(report),
        "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
        "reviewed_at": datetime.datetime.now(datetime.UTC).replace(microsecond=0).isoformat(),
    }
    out = REPO / ".local-test-evidence" / datetime.date.today().isoformat() / "taskgraph-gate"
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"review-{datetime.datetime.now().strftime('%H%M%S')}.json"
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(target), **receipt}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
