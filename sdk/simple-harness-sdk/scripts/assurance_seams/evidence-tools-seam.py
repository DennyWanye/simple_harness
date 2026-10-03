"""审阅员的两个只读取证工具 → 实际发给模型的输入 → 按标签记下的披露批次（产品同形世界）。

2026-10-03（HTN 补齐阶段 A′）迁到产品同形世界：任务经产品那一份部署组装建出，用户给任务附了两份
资料（真实的"附资料"写入口）；主循环真跑，审阅员（脚本化回复）在审查中调用
``assurance_find_evidence`` / ``assurance_read_evidence``：

* 内容审阅：列出资料，整段读两份（两条工具结果消息），引用这两个标签 → 披露批次 1 正好是这两份、
  消息号按集合记下，正式记录通过；之后再跑几轮也不再多出批次；
* 内容审阅：只读了一页（不完整），却引用它 → 第一次按 UNEXPOSED_EVIDENCE 打回、带意见重答一次，
  第二次仍引用它 → 最终拒收，不披露任何东西；
* 最终审查：没有任何执行尝试，同样经这两个工具整段读一份并引用，正式记录通过。

（原 ``test_disclosure_batch_message_order`` 的"多条工具结果消息的批次按集合记、重放不拒"并入第一段。）
不证明真实模型、Host 或界面。
"""
from _product_seam import StepReviewer, cite, count, quick, reviewed_mission, source_sha256, tool_values, write_report

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_orchestrator.assurance.codec import decode

EXTRA = ("sources/notes/extra.md", "# extra evidence\nnote the user attached to the task\n")
SECOND = ("sources/notes/second.md", "# second note\nanother attached note\n")


def find_label(request, path):  # type: ignore[no-untyped-def]
    for listing in tool_values(request, "assurance_find_evidence"):
        for entry in listing["entries"]:
            if entry["id"] == path:
                return entry["label"]
    raise AssertionError(f"{path} not listed")


def read_labels(request):  # type: ignore[no-untyped-def]
    return [value["label"] for value in tool_values(request, "assurance_read_evidence") if value.get("complete")]


def review_key_of(store, mission_id, purpose):  # type: ignore[no-untyped-def]
    row = store.connection.execute(
        "SELECT review_key FROM assurance_review_bindings WHERE mission_id=? "
        "AND json_extract(binding_json,'$.subject.purpose')=?", (mission_id, purpose)).fetchone()
    assert row is not None, purpose
    return row["review_key"]


def batches(store, review_key):  # type: ignore[no-untyped-def]
    return [decode(r["batch_json"]) for r in store.connection.execute(
        "SELECT batch_json FROM assurance_disclosure_batches WHERE review_key=? ORDER BY batch_no", (review_key,))]


async def complete_read(root, report) -> None:  # type: ignore[no-untyped-def]
    provider = StepReviewer({"TASK_CONTENT": [
        ("assurance_find_evidence", {"query": "sources/notes/"}),
        lambda request, data: ("assurance_read_evidence", {"label": find_label(request, EXTRA[0])}),
        lambda request, data: ("assurance_read_evidence", {"label": find_label(request, SECOND[0])}),
        lambda request, data: cite(data, read_labels(request)),
    ]})
    async with reviewed_mission(root, provider, sources=(EXTRA, SECOND)) as case:
        mission = await case.settle()
        assert str(mission.status.value) == "COMPLETED", mission.final_report
        store = case.store
        review_key = review_key_of(store, case.mission_id, "TASK_CONTENT")
        chain = batches(store, review_key)
        assert [b["batch_no"] for b in chain] == [0, 1], chain
        labels = [e["label"] for e in chain[1]["entries"]]
        assert len(labels) == 2, chain[1]
        visible = chain[1]["visible_message_ids"]
        assert len(visible) == 2 and list(visible) == sorted(visible), visible  # a set, whatever the request order
        record = store.connection.execute(
            "SELECT record_id, verdict FROM review_records WHERE mission_id=? AND official=1 AND purpose='TASK_CONTENT'",
            (case.mission_id,)).fetchone()
        assert record is not None and str(record["verdict"]).endswith("ACCEPT"), record
        before = len(chain)
        for _ in range(2):
            await case.world.drain(timeout=5)
        report["task_content_complete_read"] = {
            "review_key": review_key, "provider_calls": provider.review_calls["TASK_CONTENT"],
            "batches": [{"batch_no": b["batch_no"], "labels": [e["label"] for e in b["entries"]],
                         "visible_messages": len(b["visible_message_ids"])} for b in chain],
            "appended_label": labels[0], "appended_labels": labels, "record_id": str(record["record_id"]),
            "verdict": str(record["verdict"]), "replay_added_batch": len(batches(store, review_key)) != before}


async def partial_read(root, report) -> None:  # type: ignore[no-untyped-def]
    seen = {}

    def cite_partial(request, data):  # type: ignore[no-untyped-def]
        [page] = [value for value in tool_values(request, "assurance_read_evidence")]
        assert page["complete"] is False and page["disclosure"] == "PARTIAL_NOT_CITABLE", page
        seen["label"], seen["page"] = page["label"], page["content"]
        return cite(data, [page["label"]])

    provider = StepReviewer({"TASK_CONTENT": [
        ("assurance_find_evidence", {}),
        lambda request, data: ("assurance_read_evidence", {"label": find_label(request, EXTRA[0]), "offset": 2,
                                                           "max_chars": 4}),
        cite_partial,
        lambda request, data: cite(data, [seen["label"]]),
    ]})
    async with reviewed_mission(root, provider, sources=(EXTRA, SECOND)) as case:
        store = case.store
        # 第 1 次引用没读全的那页 → 要求重写；第 2 次仍引用 → 按"没有可采用的回复"记成判不下来、
        # 交人复核（阶段 C）。从头到尾没有验收。
        await case.run_until(lambda: bool(store.list_approvals(case.mission_id, "PENDING")), timeout=60)
        review_key = review_key_of(store, case.mission_id, "TASK_CONTENT")
        chain = batches(store, review_key)
        # 那一页从来没有被披露（每次调用的初始材料各成一批，但页面标签不在任何一批里）。
        disclosed = any(e["label"] == seen["label"] for b in chain for e in b["entries"])
        assert not disclosed, chain
        repair = store.connection.execute(
            "SELECT json_extract(receipt_json,'$.error_code') FROM commit_receipts WHERE kind="
            "'AssuranceReviewInterpretationRejected' AND json_extract(receipt_json,'$.review_key')=?",
            (review_key,)).fetchone()
        assert repair is not None and repair[0] == "UNEXPOSED_EVIDENCE", repair
        ordinals = [r[0] for r in store.connection.execute(
            "SELECT ordinal FROM assurance_review_invocations WHERE review_key=? ORDER BY ordinal", (review_key,))]
        assert ordinals == [1, 2], ordinals
        limitations = [limitation
                       for (raw,) in store.connection.execute(
                           "SELECT record_json FROM review_records WHERE mission_id=?", (case.mission_id,))
                       for record in [json.loads(raw)] if record["verdict"] == "INCONCLUSIVE"
                       for criterion in record["criteria"] for limitation in criterion["limitations"]]
        assert limitations and set(limitations) == {"REVIEW_NO_USABLE_REPLY:UNEXPOSED_EVIDENCE"}, limitations
        reason = (limitations[0].partition(":")[2],)
        assert count(store, "SELECT COUNT(*) FROM acceptances WHERE mission_id=?", case.mission_id) == 0
        report["task_content_partial_read"] = {"review_key": review_key, "partial_label_disclosed": disclosed,
                                               "batches": [b["batch_no"] for b in chain], "rejection": reason[0],
                                               "invocations": ordinals, "page": seen["page"]}


async def mission_final(root, report) -> None:  # type: ignore[no-untyped-def]
    provider = StepReviewer({"MISSION_FINAL": [
        ("assurance_find_evidence", {"query": "sources/notes/"}),
        lambda request, data: ("assurance_read_evidence", {"label": find_label(request, EXTRA[0])}),
        lambda request, data: cite(data, read_labels(request)),
    ]})
    async with reviewed_mission(root, provider, sources=(EXTRA, SECOND)) as case:
        mission = await case.settle()
        assert str(mission.status.value) == "COMPLETED", mission.final_report
        store = case.store
        review_key = review_key_of(store, case.mission_id, "MISSION_FINAL")
        chain = batches(store, review_key)
        assert [b["batch_no"] for b in chain] == [0, 1], chain
        record = store.connection.execute(
            "SELECT record_id FROM review_records WHERE mission_id=? AND official=1 AND purpose='MISSION_FINAL'",
            (case.mission_id,)).fetchone()
        assert record is not None
        report["mission_final_no_attempt"] = {"review_key": review_key, "record_id": str(record["record_id"]),
                                              "appended_label": chain[1]["entries"][0]["label"]}


async def main() -> None:
    quick()
    report = {"status": "PASS",
              "scope": "product deployment, real main loop, user-attached sources: reviewer evidence tools -> "
                       "complete reads disclosed as batch 1 and citable; a partial page never disclosed "
                       "(UNEXPOSED_EVIDENCE after one repair); the final review reads with no Attempt. Scripted "
                       "model replies only; not a real model, Host or UI."}
    with TemporaryDirectory(prefix="assurance-evidence-tools-") as temp:
        root = Path(temp)
        await complete_read(root / "complete", report)
        await partial_read(root / "partial", report)
        await mission_final(root / "final", report)
    report["sources_sha256"] = source_sha256(["verification/reviewer_evidence_tools.py", "runtime/tool_gateway.py",
                                              "assurance/review_input.py", "orchestrator/assurance_review_collect.py"])
    write_report("evidence-tools-seam", report)


if __name__ == "__main__":
    asyncio.run(main())
