# SPDX-License-Identifier: Apache-2.0
"""两步各负责一份说明，同时修改同一份项目配置；从宿主核对最终交付。"""

import asyncio
import json
from hashlib import sha256
from pathlib import Path

import pytest
from test_host_base import _host, _mission

from agent_orchestrator.artifacts.workspace_base import DirectoryBase, InMemoryBase, git_blob_id
from agent_orchestrator.testing.fixtures import package_of
from agent_orchestrator.testing.scripted_replies import (
    LayeredScriptedProvider,
    one_step_method,
    planner_reply,
    worker_reply,
)

CONFIG = ".editorconfig"
BASE = "root = true\n\n[*]\nindent_style = space\n\n[*.py]\nindent_size = 2\n"
FIRST = BASE.replace("true", "false")
SECOND = BASE.replace("size = 2", "size = 4")


def _planner(request, *, children=("configure",)):
    reply = planner_reply(request)
    if reply is None or "PROPOSE_METHOD" not in reply:
        return reply
    body = json.loads(
        reply.removeprefix("<planning_decision>").removesuffix("</planning_decision>")
    )
    method = one_step_method(package_of(request)["method_proposal_contexts"][0])
    for index, child in enumerate(children, 1):
        method["steps"].append(dict(method["steps"][0], local_id=child))
        method["composition"]["criterion_links"][index]["child_step"] = child
    body["payload"]["method_proposal"]["method"] = method
    return "<planning_decision>" + json.dumps(body, ensure_ascii=False) + "</planning_decision>"


def _worker(request, *, names=(CONFIG, CONFIG), distinct=False):
    reply = worker_reply(request)
    if isinstance(reply, tuple):
        return reply
    contract = package_of(request)["task_contract"]
    [document] = contract["outputs"]
    config = names[0 if document == "left.md" else 1]
    written = sum("tool" in str(message.role).lower() for message in request.messages)
    if written == 1:
        return "workspace_write_file", {
            "path": config,
            "content": document if distinct else (FIRST if document == "left.md" else SECOND),
        }
    body = json.loads(reply.removeprefix("<result_envelope>").removesuffix("</result_envelope>"))
    body["artifacts"].append(config)
    body["evidence"].append(config)
    body["cost"]["tool_calls"] = 2
    return "<result_envelope>" + json.dumps(body, ensure_ascii=False) + "</result_envelope>"


def test_two_independent_steps_deliver_the_judged_file(tmp_path):
    async def case():
        provider = LayeredScriptedProvider(planner=_planner, worker=_worker)
        async with _host(
            tmp_path, provider=provider, workspace_base=InMemoryBase({CONFIG: BASE.encode()})
        ) as host:
            mission_id = host.create_mission(
                {
                    "goal": "分别写两份说明，并更新项目的编辑器配置",
                    "success_criteria": ["file:left.md", "file:right.md"],
                    "idempotency_key": "two-writers",
                }
            )["mission_id"]
            await host.run_until_settled(mission_id, timeout=120.0)
            mission = _mission(host, mission_id)
            assert mission["status"] == "COMPLETED", mission["final_report"]
            change = mission["final_report"]["change_set"]
            [entry] = [row for row in change["files"] if row["path"] == CONFIG]
            expected = FIRST.replace("size = 2", "size = 4").encode()
            assert len(set(entry["merged_steps"])) == 2
            assert entry["artifact_id"] is None and entry["blob"] == git_blob_id(expected)
            [tree] = list(Path(tmp_path).rglob(f"{mission_id}-judge-*-verify"))
            assert (
                (tree / CONFIG).read_bytes()
                == host._bases().read(change["commit"], CONFIG)
                == expected
            )

    asyncio.run(case())


@pytest.mark.parametrize("names", [(CONFIG.upper(), CONFIG), ("café.ini", "cafe\u0301.ini")])
def test_ambiguous_delivery_reports_all_three_proposals(tmp_path, names):
    async def case():
        target = tmp_path / "target"
        target.mkdir()
        (target / "keep").write_bytes(b"original")
        provider = LayeredScriptedProvider(
            planner=lambda request: _planner(request, children=("second", "third")),
            worker=lambda request: _worker(request, names=names, distinct=True),
        )
        async with _host(
            tmp_path / "host", provider=provider, workspace_base=DirectoryBase(target)
        ) as host:
            mission_id = host.create_mission(
                {
                    "goal": "写三份说明和编辑器配置",
                    "success_criteria": ["file:left.md", "file:middle.md", "file:right.md"],
                    "idempotency_key": "alias",
                }
            )["mission_id"]
            await host.run_until_settled(mission_id, timeout=120)
            assert _mission(host, mission_id)["status"] == "COMPLETED"
            view = host.delivery(mission_id)
            [tree] = list((tmp_path / "host").rglob(f"{mission_id}-judge-*-verify"))
            committed = host._bases().files(view["commit"])
            rows = {row["path"]: row for row in view["files"]}
            assert set(rows) == {*names, "left.md", "middle.md", "right.md"}
            store = host.orchestrator.store
            accepted = [
                store.get_artifact(key)
                for task in store.list_tasks(mission_id)
                for key in task.accepted_artifacts
            ]
            expected = {(a.path, a.task_id, a.id) for a in accepted if a.path in names}
            assert len(expected) == 3
            assert {
                (name, p["task_id"], p["artifact_id"])
                for name in names
                for p in rows[name]["refused_proposals"]
            } == expected
            receipt = host.apply_delivery(mission_id, operation_key="apply")
            assert len(receipt["files"]) == len(rows)
            assert {r["path"] for r in receipt["files"]} == set(rows)
            for result in receipt["files"]:
                name = result["path"]
                row = rows[name]
                if name in names:
                    assert (
                        result["status"] == "refused"
                        and result["reason"] == row["refused"] == "path_spelling"
                    )
                    assert row["blob"] is None and row["diff"] is None
                    assert (
                        name not in committed
                        and not (tree / name).exists()
                        and not (target / name).exists()
                    )
                else:
                    assert result["status"] == "applied"
                    data = (tree / name).read_bytes()
                    assert data == committed[name] == (target / name).read_bytes()
                    assert (
                        row["blob"] == git_blob_id(data)
                        and row["sha256"] == sha256(data).hexdigest()
                    )
            assert (target / "keep").read_bytes() == b"original"

    asyncio.run(case())
