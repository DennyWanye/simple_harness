# SPDX-License-Identifier: BUSL-1.1
"""诊断导出里的"核对执行图历史"（HTN 补齐阶段 B 第 2 条，2026-10-03）。

导出时在临时副本上从种子版本重建到最新版本，诊断包里只有重建报告、历史版本清单（标"历史，不可执行"）
和相邻版本的结构差异；重建出来的库用完即删，包里没有任务原文。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskpet.orchestration.handlers import handle

from ._layered_lane import LayeredScriptedProvider, layered_service, notes_mission, quick_runtime, run_until_settled


@pytest.mark.asyncio
async def test_an_export_checks_the_graph_history_on_a_throwaway_copy(orchestration_root, principal, monkeypatch):
    quick_runtime(monkeypatch)
    service = layered_service(orchestration_root, principal, LayeredScriptedProvider())
    await service.start()
    try:
        goal = "HISTORY-GOAL-SENTINEL 写一份 NOTES.md"
        mission_id = service.create_mission(notes_mission("history-1", goal=goal))["mission_id"]
        assert (await run_until_settled(service, mission_id)).status.value == "COMPLETED"
        response = await handle(service, "mission_support_export", {"mission_id": mission_id})
        assert response["payload"]["ok"] is True, response
        report = json.loads(Path(response["payload"]["data"]["path"]).read_text(encoding="utf-8"))
        history = report["taskgraph_history"]
        assert history["status"] == "CHECKED"
        assert history["replay"]["status"] == "GRAPH_PROJECTION_VERIFIED"
        assert history["replay"]["runtime_status"] == "RUNTIME_RESUME_NOT_AUTHORIZED"
        revisions = history["revisions"]
        assert revisions[0]["source_kind"] == "SEED_COMMIT" and revisions[-1]["label"] == "当前"
        assert all(row["label"] == "历史，不可执行" for row in revisions[:-1])
        assert len(history["diffs"]) == len(revisions) - 1
        assert all("changes" in diff for diff in history["diffs"])
        assert "HISTORY-GOAL-SENTINEL" not in json.dumps(history, ensure_ascii=False)
        assert not list((service.root / "support" / "tmp").iterdir())  # the rebuilt library is gone
    finally:
        await service.close()
