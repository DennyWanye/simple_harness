"""内容审阅回了一段格式不对的回答 → 恰好一次有预算的格式修复 → 正式记录（产品同形世界）。

2026-10-03（HTN 补齐阶段 A′）迁到产品同形世界：任务经产品那一份部署组装建出、主循环真跑；
审阅员第一次回 ``{malformed``，原样保留原始回答、带着格式意见发第二次（FORMAT_REPAIR），第二次
正常回答被正式导入。诊断：内容审阅两次调用、一条正式记录、一条格式拒收回执、结束后审阅预留不再
挂着。不证明真实模型、Host 或界面。
"""
from _product_seam import StepReviewer, count, quick, reviewed_mission, source_sha256, write_report

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_orchestrator.assurance.codec import decode


async def main() -> None:
    quick()
    provider = StepReviewer({"TASK_CONTENT": ["{malformed"]})
    with TemporaryDirectory(prefix="assurance-format-repair-") as temp:
        async with reviewed_mission(Path(temp), provider) as case:
            mission = await case.settle()
            assert str(mission.status.value) == "COMPLETED", mission.final_report
            store = case.store
            rows = store.connection.execute(
                "SELECT ordinal, json_extract(invocation_json,'$.reason') AS reason, dispatch_intent_id "
                "FROM assurance_review_invocations WHERE review_key LIKE 'assurance-content:%' ORDER BY ordinal").fetchall()
            invocations = [(r["ordinal"], r["reason"]) for r in rows]
            assert invocations == [(1, "INITIAL"), (2, "FORMAT_REPAIR")], invocations
            second = store.get_intent(rows[1]["dispatch_intent_id"])
            feedback = decode(second.config["message"]["content"])["format_feedback"]
            assert feedback, "the repair call carries the format feedback"
            counts = {
                "provider_calls": provider.review_calls["TASK_CONTENT"],
                "official_records": count(store, "SELECT COUNT(*) FROM review_records WHERE mission_id=? AND official=1 "
                                                 "AND purpose='TASK_CONTENT'", case.mission_id),
                "format_rejected": count(store, "SELECT COUNT(*) FROM commit_receipts WHERE kind="
                                                "'AssuranceReviewFormatRejected' AND json_extract(receipt_json,"
                                                "'$.review_key') LIKE 'assurance-content:%'"),
                "held_review_reservations": count(store, "SELECT COUNT(*) FROM budget_reservations WHERE subject_id "
                                                         "LIKE '%:assurance:%' AND state='RESERVED'"),
            }
            assert counts["format_rejected"] == 1 and counts["held_review_reservations"] == 0, counts
            write_report("critic-format-repair-seam", {
                "status": "PASS",
                "scope": "product deployment, real main loop: malformed raw retained -> one funded format repair -> "
                         "official record; scripted model replies only; not a real model, Host or UI",
                "counts": counts, "invocations": invocations, "format_feedback": feedback[:400],
                "source_hashes": source_sha256(["orchestrator/assurance_review_runtime.py",
                                                "orchestrator/assurance_review_transport.py",
                                                "orchestrator/assurance_review_import.py"]),
            })


if __name__ == "__main__":
    asyncio.run(main())
