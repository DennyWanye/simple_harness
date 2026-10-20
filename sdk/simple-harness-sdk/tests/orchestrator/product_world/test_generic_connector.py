# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""应用连接器（不是文件发布）的整条链：建任务时写参数 → 自动确认 → 系统准备申请单 → 审阅 →
自动模式主机代批 → 执行 → 回执核对 → 完成；"总要人批"等人；回执丢了按查询核对。

用户 2026-10-20：副作用建任务时写明走连接器，auto 模式系统自动审批；允许应用把操作标成
"总要人批"。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agent_orchestrator.governance.policies import DeploymentPolicy
from agent_orchestrator.runtime.connectors import (
    ConnectorRejected,
    ConnectorTransportError,
    OperationSpec,
    Receipt,
    params_hash,
)
from agent_orchestrator.testing.product_world import product_world
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider

TARGET = "/photos/a.jpg"


class Photos:
    """幂等、可查询的假相册。``lose_first``：第一次执行的回执在路上丢了（``applies``
    决定那次到底生效没有）。"""

    name = "photos"
    supports_idempotency = True
    supports_reconciliation = True
    lookup_authority = "authoritative"

    def __init__(self, *, fenced: bool = True, person: bool = False) -> None:
        self.lookup_fenced = fenced
        self.operations = {
            "move": OperationSpec(
                "move", "L2", ("to",), description="移动照片", always_needs_approval=person
            )
        }
        self.ledger: dict[str, Receipt] = {}
        self.fence: set[str] = set()
        self.executions: list[str] = []  # 真正生效的键
        self.attempts = 0
        self.lose_first = False
        self.late: tuple[str, str, dict[str, Any]] | None = None

    def normalize_target(self, target: str) -> str:
        return target.strip()

    def apply(self, operation: str, target: str, params: Any, key: str) -> Receipt:
        if key in self.fence:
            raise ConnectorRejected("fenced by an earlier lookup")
        if key not in self.ledger:
            self.executions.append(key)
            self.ledger[key] = Receipt(
                key, self.name, operation, target, params_hash(params), True, after=dict(params)
            )
        return self.ledger[key]

    async def execute(self, operation, target, params, *, idempotency_key):
        self.attempts += 1
        if self.lose_first and self.attempts == 1:
            # 原请求还在网络里：先不落地，回执丢了
            self.late = (operation, target, dict(params))
            raise ConnectorTransportError("connection reset")
        return self.apply(operation, target, params, idempotency_key)

    def lookup(self, idempotency_key):
        found = self.ledger.get(idempotency_key)
        if found is None and self.lookup_fenced:
            self.fence.add(idempotency_key)
        return found

    def land_late(self, key: str) -> None:
        """丢掉回执的那个原请求现在才到。"""

        assert self.late is not None
        operation, target, params = self.late
        try:
            self.apply(operation, target, params, key)
        except ConnectorRejected:
            pass


def _request(key: str) -> dict[str, Any]:
    return {
        "goal": "把 /photos/a.jpg 移到归档，并在 NOTES.md 记一笔",
        "success_criteria": [
            "file:NOTES.md",
            {"statement": f"action:photos.move:{TARGET}", "params": {"to": "/archive/a.jpg"}},
        ],
        "idempotency_key": key,
    }


def _world(root: Path, photos: Photos):
    return product_world(
        root,
        LayeredScriptedProvider(),
        connectors={"photos": photos},
        deployment_policy=DeploymentPolicy(enabled_connectors=("photos",), max_action_level="L2"),
    )


def _events(world: Any, mission_id: str, kind: str) -> list[Any]:
    return [e for e in world.store.iter_events(mission_id) if e.type == kind]


def test_auto_mode_runs_a_generic_operation_once_and_records_the_host_grant(tmp_path):
    photos = Photos()

    async def case():
        async with _world(tmp_path, photos) as world:
            mission_id = world.create(_request("g-1"))["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=10)
            assert str(mission.status) == "COMPLETED"
            assert len(photos.executions) == 1
            [action] = world.store.list_actions(mission_id)
            assert action["params"]["to"] == "/archive/a.jpg"  # 参数来自建任务时写下的那份
            granted = _events(world, mission_id, "ApprovalGranted")
            assert granted and all(
                e.payload["approval_source"] == "HOST_AUTO_PERMISSION" for e in granted
            )
            assert all(e.actor_type == "system" for e in granted)  # 记成主机，不是人
            assert _events(world, mission_id, "OperationOutcomeAccepted")

    asyncio.run(case())


def test_always_needs_approval_waits_for_a_person_even_in_auto_mode(tmp_path):
    photos = Photos(person=True)

    async def case():
        async with _world(tmp_path, photos) as world:
            mission_id = world.create(_request("g-2"))["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=6)
            assert str(mission.status) != "COMPLETED"
            assert photos.executions == []
            [pending] = [r for r in world.control.approvals(mission_id) if r["kind"] == "action"]
            assert pending["state"] == "PENDING" and pending["person_required"] is True
            refused = None
            try:
                world.control.host_auto_approve_action(pending["request_id"])
            except Exception as error:  # noqa: BLE001 - 主机代批被拒
                refused = error
            assert refused is not None
            world.control.decide(pending["request_id"], "approve")
            mission = await world.run_until_settled(mission_id, rounds=10)
            assert str(mission.status) == "COMPLETED"
            assert len(photos.executions) == 1
            [granted] = _events(world, mission_id, "ApprovalGranted")
            assert granted.payload["approval_source"] == "HUMAN"

    asyncio.run(case())


def test_a_lost_reply_is_proven_unapplied_by_a_fenced_lookup_and_never_duplicates(tmp_path):
    """原请求在查询为空之后才落地：封口让它被拒，换新键重做，只生效一次。"""

    photos = Photos(fenced=True)
    photos.lose_first = True

    async def case():
        async with _world(tmp_path, photos) as world:
            mission_id = world.create(_request("g-3"))["mission_id"]
            for _ in range(12):
                await world.drain()
                actions = world.store.list_actions(mission_id)
                if photos.fence and photos.late is not None:
                    photos.land_late(next(iter(photos.fence)))
                    photos.late = None
                mission = world.store.get_mission(mission_id)
                if str(mission.status) == "COMPLETED":
                    break
            assert str(mission.status) == "COMPLETED", [a["state"] for a in actions]
            assert len(photos.executions) == 1  # 迟到的原请求没有再生效一次
            assert photos.executions[0] not in photos.fence

    asyncio.run(case())


class AsyncLookupPhotos(Photos):
    """同上，但 ``lookup`` 是 ``async def``（核验员 2026-10-20：封口证明要用执行器问到的答案）。"""

    async def lookup(self, idempotency_key):  # type: ignore[override]
        return Photos.lookup(self, idempotency_key)


def test_an_async_fenced_lookup_also_proves_nonapplication_and_redoes_once(tmp_path):
    photos = AsyncLookupPhotos(fenced=True)
    photos.lose_first = True

    async def case():
        async with _world(tmp_path, photos) as world:
            mission_id = world.create(_request("g-5"))["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=12)
            assert str(mission.status) == "COMPLETED"
            assert len(photos.executions) == 1
            [fenced] = [
                a
                for a in world.store.list_actions(mission_id)
                if "lookup_fenced_key" in str(a.get("error"))
            ]
            assert fenced["handoffs"] == 1  # 封口后没有同键重交

    asyncio.run(case())


def test_an_unfenced_lookup_cannot_prove_nonapplication_and_waits_for_a_person(tmp_path):
    photos = Photos(fenced=False)
    photos.lose_first = True

    async def case():
        async with _world(tmp_path, photos) as world:
            mission_id = world.create(_request("g-4"))["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=8)
            assert str(mission.status) != "COMPLETED"
            [action] = world.store.list_actions(mission_id)
            assert action["state"] == "UNKNOWN"
            assert photos.attempts == 1 and photos.executions == []  # 没有盲目重交

    asyncio.run(case())


def test_an_artifact_reference_in_params_is_bound_and_executed(tmp_path):
    """参数里的 ``{"$artifact": "NOTES.md"}`` 准备申请单与建动作两处按同一办法绑成产物引用
    （2026-10-20 真实模型一局发现：建动作那一步按发布的办法绑，申请单永远被拒）。"""

    import hashlib

    photos = Photos()
    photos.operations = {"move": OperationSpec("move", "L2", ("to", "file"))}

    async def case():
        async with _world(tmp_path, photos) as world:
            request = _request("g-6")
            request["success_criteria"][1]["params"] = {
                "to": "/archive/a.jpg",
                "file": {"$artifact": "NOTES.md"},
            }
            mission_id = world.create(request)["mission_id"]
            mission = await world.run_until_settled(mission_id, rounds=10)
            assert str(mission.status) == "COMPLETED"
            assert len(photos.executions) == 1
            [action] = world.store.list_actions(mission_id)
            bound = action["params"]["file"]
            assert set(bound) == {"artifact_path", "content_hash", "size", "storage_uri"}
            assert bound["artifact_path"] == "NOTES.md"
            data = Path(bound["storage_uri"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == bound["content_hash"]
            assert len(data) == bound["size"]

    asyncio.run(case())
