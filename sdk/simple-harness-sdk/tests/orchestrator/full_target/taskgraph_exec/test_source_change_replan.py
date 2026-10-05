# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""资料换版本 / 撤销 → 什么时候问规划器、什么时候只记一笔（2026-09-30 方案 B，2026-10-05 裁决改写）。

拿着旧版还在跑的尝试：等它跑完再评估。跑完后：有已通过的步骤是拿旧版做的 → 一条请求交给规划器
（见 ``product_world/test_source_change.py``）；没有 → 只记一条评估，不发请求。新登记资料不发请求。

产品同形部署上的真实编排器、执行图与执行者派发；只有模型回复是脚本。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[3]
for _extra in (_TESTS / "orchestrator" / "full_target", _TESTS / "orchestrator" / "full_target" / "taskgraph_exec"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

import json  # noqa: E402

from production_fixture import OUTPUT, enabled_world, result_envelope, scripted_worker  # noqa: E402

from agent_orchestrator.contracts.state_machines import TERMINAL_ATTEMPT  # noqa: E402
from agent_orchestrator.orchestrator.planning_repair_requests import (  # noqa: E402
    ADDRESSED,
    REQUESTED,
    SOURCE_CHANGE_ASSESSED,
    collect_triggers,
)


def _no_claims(request):  # type: ignore[no-untyped-def]
    """The file is written, but the result states no claim (its rule check fails)."""
    body = json.loads(result_envelope(request)[len("<result_envelope>"):-len("</result_envelope>")])
    body["claims"] = []
    return "<result_envelope>" + json.dumps(body, ensure_ascii=False) + "</result_envelope>"


def _worker():  # type: ignore[no-untyped-def]
    return scripted_worker(("workspace_write_file", {"path": OUTPUT, "content": "Repository facts."}), _no_claims)
PATH = "sources/data.csv"


def _events(loop, mission_id: str, event_type: str):  # type: ignore[no-untyped-def]
    return [e for e in loop.store.list_events(mission_id) if e.type == event_type]


def _source_requests(loop, mission_id: str):  # type: ignore[no-untyped-def]
    """Repair requests opened by a source change (the scripted Worker's own
    ``VerificationFailed`` — it submits no claims — opens its ordinary request too)."""
    return [e for e in _events(loop, mission_id, REQUESTED) if str(e.payload["source_key"]).startswith("source:")]


def test_a_superseded_source_waits_for_the_running_attempt_and_is_only_noted_when_no_step_passed_on_it(tmp_path):
    async def case():  # type: ignore[no-untyped-def]
        async with enabled_world(tmp_path, key="tg-source-replan", worker=_worker(), hold_worker=True) as world:
            loop, mission = world.loop, world.mission
            control = world.product.control  # the deployment's authenticated control channel
            first = control.register_source({"mission_id": mission.id, "path": PATH, "content": "region,amount\n华东,1\n",
                                             "kind": "text", "idempotency_key": "reg-data-1"})
            await world.commit_seed()  # the Worker's model call is held: its Attempt is running
            [attempt] = [a for t in loop.store.list_tasks(mission.id) for a in loop.store.list_attempts(t.id)]
            # 派发时冻结的就是第 1 版
            assert loop._frozen_source_binding(attempt)["source_versions"][PATH] == first["version_hash"]
            assert collect_triggers(loop, mission) is False and not _events(loop, mission.id, REQUESTED)

            # 换版本（经一次批准）。尝试还在跑：**先不评估**（2026-09-30 真机：当轮发请求，规划器只会
            # 回 WAIT 等它跑完，修复轮又拒绝 WAIT，白花两轮）。
            proposal = control.supersede_source({"mission_id": mission.id, "path": PATH, "content": "region,amount\n华东,2\n",
                                                 "kind": "text", "idempotency_key": "sup-data-1",
                                                 "expected_version_hash": first["version_hash"]})
            control.decide(proposal["request_id"], "approve", nonce="approve-sup-1")
            assert _events(loop, mission.id, "SourceSuperseded")
            assert collect_triggers(loop, mission) is False
            assert not _source_requests(loop, mission.id) and not _events(loop, mission.id, SOURCE_CHANGE_ASSESSED)
            # 新登记的资料不发请求
            control.register_source({"mission_id": mission.id, "path": "sources/extra.md", "content": "extra",
                                     "kind": "markdown", "idempotency_key": "reg-extra-1"})
            collect_triggers(loop, mission)
            assert not _source_requests(loop, mission.id)

            # 执行者跑完（这里的脚本执行者不交 claim，验收判 FAIL，走普通的验收失败请求）
            world.provider.release.set()
            await world.run_worker()
            assert loop.store.get_attempt(attempt.id).status in TERMINAL_ATTEMPT
            collect_triggers(loop, mission)
            # 现在评估：拿着旧版跑过的这一步没有通过验收 → 只记评估、不发资料请求
            [assessed] = _events(loop, mission.id, SOURCE_CHANGE_ASSESSED)
            assert assessed.payload["reason"] == "source_superseded"
            assert assessed.payload["old_version"] == first["version_hash"]
            [step] = assessed.payload["steps_on_old_version"]
            assert (step["attempt_id"], step["cited"]) == (attempt.id, False) and step["status"] != "ACCEPTED"
            assert not _source_requests(loop, mission.id)
            assert [e.payload["source_key"] for e in _events(loop, mission.id, REQUESTED)] == [
                "event:" + next(e.idempotency_key for e in _events(loop, mission.id, "VerificationFailed"))]
            assert not _events(loop, mission.id, ADDRESSED)
            collect_triggers(loop, mission)
            assert len(_events(loop, mission.id, SOURCE_CHANGE_ASSESSED)) == 1

            # 跑完后再换一次版本：没有拿着这一版跑过的尝试 → 评估记录为空
            second = control.supersede_source({"mission_id": mission.id, "path": PATH, "content": "region,amount\n华东,3\n",
                                               "kind": "text", "idempotency_key": "sup-data-2",
                                               "expected_version_hash": proposal["version_hash"]})
            control.decide(second["request_id"], "approve", nonce="approve-sup-2")
            collect_triggers(loop, mission)
            assert not _source_requests(loop, mission.id)
            assert [e.payload["steps_on_old_version"] for e in _events(loop, mission.id, SOURCE_CHANGE_ASSESSED)][-1] == []
            assert len(_events(loop, mission.id, SOURCE_CHANGE_ASSESSED)) == 2

    asyncio.run(case())
