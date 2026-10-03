"""执行者检查接缝（产品同形世界，2026-10-03 迁离 ``_assured_fixture``）。

用户建一个带 ``pytest:tests`` 要求的任务；产品主循环真跑：执行者写出 report.json 和一份测试，
验证层经部署的进程执行器真起一个 pytest 子进程（code_test），受信的记录器把这一次运行的回执导入成
"执行已导入"事件与一条检查绑定，验收时只用这份记录、不重跑。替身只有脚本化的模型回复；测试源码
由执行者（脚本化）写进工作区，是外界会发生的事。

四种运行：
* 通过：绑定 SUCCEEDED / PASS，用例编号逐个记下，观察范围绑在这份结果与产物哈希上；任务完成；
* 失败：绑定 SUCCEEDED / FAIL，失败用例编号记下；任务不会完成；
* 测试改了输入文件：绑定不得为 PASS（UNKNOWN）；
* 超时（部署的测试超时设成 1 秒）：绑定 CANCELLED / UNKNOWN。

每次运行恰好导入一次（同一尝试一条"执行已导入"、一条绑定）。
"""
from _product_seam import EVIDENCE, ReviewScript, count, quick, reviewed_mission, source_sha256, write_report  # noqa: F401

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agent_orchestrator.assurance.codec import decode
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.scripted_replies import worker_reply

PASSING = "def test_report_ok():\n    assert True\n\ndef test_second():\n    assert 1 + 1 == 2\n"
FAILING = "def test_report_bad():\n    assert False, 'fixture failure'\n"
MUTATING = ("from pathlib import Path\n\ndef test_touches_input():\n"
            "    Path('report.json').write_text('changed by the test')\n    assert True\n")
SLOW = "import time\n\ndef test_slow():\n    time.sleep(5)\n"
CRITERIA = ("file:report.json", "pytest:tests")
GOAL = "写 report.json，并在 tests/ 下配一份测试。"


class TestWriter(ReviewScript):
    """执行者先把这份测试写进工作区，再照常写出声明的产出、交结果。"""

    def __init__(self, source: str) -> None:
        super().__init__()

        def worker(request: Any) -> Any:
            written = sum(1 for m in request.messages if "tool" in str(m.role).lower())
            if written == 0:
                return ("workspace_write_file", {"path": "tests/test_fixture.py", "content": source})
            outputs = list(package_of(request).get("task_contract", {}).get("outputs") or [])
            if written - 1 < len(outputs):
                return ("workspace_write_file", {"path": outputs[written - 1], "content": "{\"ok\": true}\n"})
            return worker_reply(request)

        self._answer["worker"] = worker


def code_test_bindings(store: Any, mission_id: str) -> list[dict[str, Any]]:
    rows = store.connection.execute(
        "SELECT binding_json FROM assurance_check_bindings WHERE mission_id=?", (mission_id,)).fetchall()
    return [b for b in (decode(r[0]) for r in rows) if str(b.get("assertion_key", "")).startswith("code_test:")]


def evidence_document(root: Path, content_hash: str) -> dict[str, Any]:
    [blob] = [p for p in root.rglob(content_hash) if p.is_file()] or [
        p for p in root.rglob(content_hash[2:]) if p.is_file() and p.parent.name == content_hash[:2]]
    return json.loads(blob.read_bytes())


async def one_run(root: Path, name: str, source: str, *, settle: bool, **config: Any) -> dict[str, Any]:
    provider = TestWriter(source)
    async with reviewed_mission(root / name, provider, criteria=CRITERIA, goal=GOAL, key=name, **config) as case:
        store = case.store
        if settle:
            await case.settle(timeout=120)
        else:
            await case.run_until(lambda: bool(code_test_bindings(store, case.mission_id)), timeout=120)
        [bound] = code_test_bindings(store, case.mission_id)
        [imported] = case.events("AssuranceExecutionImported")
        output = imported.payload["outputs"][0]["pin"]["content_hash"]
        document = evidence_document(root / name, output)
        [result] = case.events("ResultSubmitted")
        return {"status": case.status(), "binding": bound, "imported": imported.payload, "document": document,
                "result_id": result.payload["result_id"],
                "imports": count(store, "SELECT count(*) FROM events WHERE mission_id=? AND type="
                                 "'AssuranceExecutionImported'", case.mission_id),
                "code_test_bindings": len(code_test_bindings(store, case.mission_id))}


async def main() -> None:
    quick()
    report: dict[str, Any] = {
        "status": "PASS",
        "scope": "product deployment, real main loop: a pytest:tests criterion -> code_test runs a real pytest "
                 "child process through the deployed executor -> trusted recorder imports the receipts once "
                 "(AssuranceExecutionImported + CheckBinding) -> acceptance uses the record, never re-runs. "
                 "Scripted model replies only; not a real model, Host or UI."}
    with TemporaryDirectory(prefix="assurance-executor-check-") as temp:
        root = Path(temp).resolve()
        run = await one_run(root, "passing", PASSING, settle=True)
        bound, document = run["binding"], run["document"]
        assert run["status"] == "COMPLETED", run["status"]
        assert bound["execution_state"] == "SUCCEEDED" and bound["verdict"] == "PASS", bound
        assert bound["execution_ref"]["kind"] == "execution_receipt"
        run0 = document["runs"][0]
        assert run0["receipt"]["execution_id"] and run0["receipt"]["kind"] == "process_only"
        assert run0["receipt"]["exit_code"] == 0
        assert sorted(run0["nodeids"]["PASSED"]) == [
            "tests/test_fixture.py::test_report_ok", "tests/test_fixture.py::test_second"], run0["nodeids"]
        assert document["observation_scope"]["result_id"] == run["result_id"]
        assert set(document["observation_scope"]["artifact_hashes"]) >= {"report.json"}
        assert run["imports"] == 1 == run["code_test_bindings"]
        report["passing"] = {"binding_verdict": bound["verdict"], "state": bound["execution_state"],
                             "mission": run["status"], "nodeids": run0["nodeids"]["PASSED"],
                             "execution_id": run0["receipt"]["execution_id"], "bound_to_result": True,
                             "imported_once": True}

        run = await one_run(root, "failing", FAILING, settle=False)
        bound, document = run["binding"], run["document"]
        assert bound["execution_state"] == "SUCCEEDED" and bound["verdict"] == "FAIL", bound
        assert document["runs"][0]["nodeids"]["FAILED"] == ["tests/test_fixture.py::test_report_bad"]
        assert run["status"] != "COMPLETED"
        report["failing"] = {"binding_verdict": bound["verdict"], "mission": run["status"],
                             "failed_nodeids": document["runs"][0]["nodeids"]["FAILED"]}

        run = await one_run(root, "mutating", MUTATING, settle=False)
        bound = run["binding"]
        assert bound["execution_state"] == "SUCCEEDED" and bound["verdict"] == "UNKNOWN", bound
        report["workspace_mutated"] = {"binding_verdict": bound["verdict"], "reason": run["document"].get("reason")}

        run = await one_run(root, "slow", SLOW, settle=False, test_timeout_seconds=1.0)
        bound = run["binding"]
        assert run["document"]["runs"][0]["timed_out"]
        assert bound["execution_state"] == "CANCELLED" and bound["verdict"] == "UNKNOWN", bound
        report["timeout"] = {"state": bound["execution_state"], "binding_verdict": bound["verdict"]}
    report["source_sha256"] = source_sha256([
        "assurance/check_specs.py", "assurance/executor_checks.py", "assurance/local_checks.py",
        "verification/assurance_local.py", "verification/verifier_router.py", "verification/deterministic_checks.py",
        "orchestrator/assurance_local_checks.py", "orchestrator/assurance_check_import.py"])
    write_report("executor-check-seam", report)


if __name__ == "__main__":
    asyncio.run(main())
