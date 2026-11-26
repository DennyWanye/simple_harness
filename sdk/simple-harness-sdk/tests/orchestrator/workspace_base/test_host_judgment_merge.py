# SPDX-License-Identifier: Apache-2.0
"""两步各负责一份说明，同时修改同一份项目配置；从宿主核对最终交付。"""

import asyncio
import json
from pathlib import Path

from test_host_base import _host, _mission

from agent_orchestrator.artifacts.workspace_base import InMemoryBase, git_blob_id
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


def _planner(request):
    reply = planner_reply(request)
    if reply is None or "PROPOSE_METHOD" not in reply:
        return reply
    body = json.loads(
        reply.removeprefix("<planning_decision>").removesuffix("</planning_decision>")
    )
    method = one_step_method(package_of(request)["method_proposal_contexts"][0])
    method["steps"].append(dict(method["steps"][0], local_id="configure"))
    method["composition"]["criterion_links"][1]["child_step"] = "configure"
    body["payload"]["method_proposal"]["method"] = method
    return "<planning_decision>" + json.dumps(body, ensure_ascii=False) + "</planning_decision>"


def _worker(request):
    reply = worker_reply(request)
    if isinstance(reply, tuple):
        return reply
    contract = package_of(request)["task_contract"]
    [document] = contract["outputs"]
    written = sum("tool" in str(message.role).lower() for message in request.messages)
    if written == 1:
        return "workspace_write_file", {
            "path": CONFIG,
            "content": FIRST if document == "left.md" else SECOND,
        }
    body = json.loads(reply.removeprefix("<result_envelope>").removesuffix("</result_envelope>"))
    body["artifacts"].append(CONFIG)
    body["evidence"].append(CONFIG)
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
