# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
"""步骤中途提议动作：搜索 → 看结果决定归档哪张 → 提议 → 批准 → 执行 → 同一步续做 → 完成（不联网，脚本模型）。

    python tools_step_action.py

建任务时只写"找出长颈鹿照片并归档"，不写归档哪一张：执行步骤的模型先调只读工具 ``photo_search``，
看到结果后在结果块里提议 ``photos.move``（``outcome=blocked``），系统检查、冻结、自动代批、执行，
再给同一步开新尝试，模型在 ``action_results`` 里看到回执，写 NOTES.md 交结果。

同一份代码再演示"总要人批"：``share`` 标了 ``always_needs_approval``，提议后停在 ``host.approvals()``
等人；这里由脚本代替人点拒绝，模型在下一次尝试里看到 REJECTED，照样把任务做完。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from agent_orchestrator.host import DomainTool, HostSettings, OrchestrationHost, Principal
from agent_orchestrator.host.offline import (
    FixtureWordCounter,
    ScriptedProvider,
    package_of,
    worker_reply,
)
from agent_orchestrator.runtime.connectors import (
    ActionPrepareContext,
    ConnectorRejected,
    OperationSpec,
    Receipt,
    params_hash,
)


class PhotoLibrary:
    """假相册。``prepare`` 是应用自己的检查与整理：归档目标一律规范成 ``/archive/<文件名>``。"""

    name = "photos"
    supports_idempotency = True
    supports_reconciliation = True
    lookup_authority = "authoritative"
    lookup_fenced = True
    operations = {
        "move": OperationSpec("move", "L2", ("to",), description="把照片移到 to 指定的路径"),
        "share": OperationSpec(
            "share", "L2", ("with",), description="分享给 with", always_needs_approval=True
        ),
    }

    def __init__(self) -> None:
        self.files = {"/photos/giraffe-1.jpg", "/photos/cat.jpg"}
        self.ledger: dict[str, Receipt] = {}
        self.fenced: set[str] = set()

    def normalize_target(self, target: str) -> str:
        return target.strip()

    def prepare(self, operation, target, params, *, context: ActionPrepareContext):
        """提议进账本前、批准后交接前各调一次（``context.phase``）；两次结果必须一致。"""

        if operation == "move":
            if target not in self.files:
                raise ConnectorRejected(f"{target} 不在相册里")
            return {**params, "to": "/archive/" + str(params["to"]).strip("/").split("/")[-1]}
        return None  # 原样

    def execute(self, operation, target, params, *, idempotency_key):
        if idempotency_key in self.fenced:
            raise ConnectorRejected("fenced")
        if idempotency_key not in self.ledger:  # 同一个键只执行一次
            if operation == "move":
                self.files.discard(target)
                self.files.add(params["to"])
            self.ledger[idempotency_key] = Receipt(
                idempotency_key,
                self.name,
                operation,
                target,
                params_hash(params),
                True,
                after={"operation": operation, **params},
            )
        return self.ledger[idempotency_key]

    def lookup(self, idempotency_key):
        found = self.ledger.get(idempotency_key)
        if found is None:
            self.fenced.add(idempotency_key)
        return found


def search_tool(photos: PhotoLibrary) -> DomainTool:
    def search(arguments, context):
        return {"matches": sorted(p for p in photos.files if arguments["q"] in p)}

    return DomainTool(
        name="photo_search",
        schema={
            "type": "object",
            "description": "按关键词搜相册，返回匹配的路径",
            "properties": {"q": {"type": "string", "description": "关键词"}},
            "required": ["q"],
            "additionalProperties": False,
        },
        invoke=search,
    )


def scripted_worker(operation: str, params: dict):
    """代替真实模型的执行者：先搜，再提议一个动作，看到结果后写文件交结果。"""

    def reply(request):
        package = package_of(request)
        results = package.get("action_results") or []
        tool_turns = sum(1 for m in request.messages if "tool" in str(m.role).lower())
        if tool_turns == 0 and not results:
            return ("photo_search", {"q": "giraffe"})
        if not results:
            found = json.loads(str(request.messages[-1].content))  # 工具结果（数据）
            target = (found.get("matches") or ["/photos/giraffe-1.jpg"])[0]
            envelope = {
                "task_id": package["task_contract"]["task_id"],
                "attempt_id": package["attempt"]["attempt_id"],
                "outcome": "blocked",
                "summary": "搜到了长颈鹿照片，先归档",
                "claims": [],
                "evidence": [],
                "artifacts": [],
                "proposed_tasks": [],
                "used_knowledge": [],
                "risks": [],
                "cost": {"tool_calls": 1},
                "action_request": {
                    "connector": "photos",
                    "operation": operation,
                    "target": target,
                    "params": params,
                    "reason": "搜索结果里的长颈鹿照片",
                },
            }
            return (
                "<result_envelope>"
                + json.dumps(envelope, ensure_ascii=False)
                + "</result_envelope>"
            )
        return worker_reply(request)  # 看到动作结果了：写文件、交结果

    return reply


async def run(operation: str, params: dict, *, person_decides: str | None) -> None:
    photos = PhotoLibrary()
    root = Path(tempfile.mkdtemp(prefix="step-action-"))
    host = await OrchestrationHost.open(
        root,
        principal=Principal("user-1", "我"),
        provider=ScriptedProvider(worker=scripted_worker(operation, params)),
        counter=FixtureWordCounter(),
        settings=HostSettings(max_action_level="L2"),
        connectors=[photos],
        tools=[search_tool(photos)],
        auto_mode=True,
        require_acceptance=False,
    )
    try:
        mission_id = host.create_mission(
            {
                "goal": "找出长颈鹿照片并处理，然后写一份 NOTES.md 说明做了什么。",
                "success_criteria": ["file:NOTES.md"],
                "allowed_actions": ["photos.move", "photos.share"],
                "idempotency_key": f"step-{operation}",
            }
        )["mission_id"]
        if person_decides is not None:
            for _ in range(100):  # 等审批卡出现（总要人批：自动模式也不代批）
                await host.drain(timeout=10)
                pending = [a for a in host.approvals(mission_id) if a["state"] == "PENDING"]
                if pending:
                    break
                await asyncio.sleep(0.1)
            card = pending[0]["summary"]
            print(
                "等人批准：", card["connector"], card["operation"], card["target"], card["params"]
            )
            host.decide(pending[0]["request_id"], person_decides, reason="脚本代替人决定")
        final = await host.run_until_settled(mission_id, timeout=600)
        print("任务：", final["snapshot"]["mission"]["status"])
        for action in host.step_actions(mission_id):
            print(
                "动作：",
                action["operation"],
                action["target"],
                action["params"],
                "→",
                action["state"],
            )
        print("相册现在有：", sorted(photos.files))
        for artifact in final["snapshot"]["artifacts"]:
            if artifact["path"] == "NOTES.md":
                print(
                    "NOTES.md：",
                    host.artifact_read(artifact["id"])["content"][:80].replace("\n", " "),
                )
    finally:
        await host.close()


async def main() -> None:
    print("== 自动代批：归档")
    await run("move", {"to": "giraffe-1.jpg"}, person_decides=None)
    print("== 总要人批：分享（人点了拒绝）")
    await run("share", {"with": "family"}, person_decides="reject")


asyncio.run(main())
