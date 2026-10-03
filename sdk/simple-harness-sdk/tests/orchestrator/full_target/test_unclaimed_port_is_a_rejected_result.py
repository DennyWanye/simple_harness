# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""A Worker result whose port claims do not match the plan is a *rejected result*.

Found 2026-10-01 while moving loop fixtures onto the current planning protocol: under
the completion protocol ``record_result`` validates the envelope's port claims and
raised ``OperationCompletionError`` — a ``ContractError`` the collection loop does not
isolate — so one model mistake ended ``run()`` for every Mission in the process.  A
model may be wrong; its mistake must become a recorded refusal, never a crash.

2026-10-03（HTN 补齐阶段 A′）：世界换成产品同形部署（建任务时绑定执行图、保证通道、原生执行池），
计划由规划器提出、经独立审阅、采用后提交，执行与收集是主循环真跑；只有模型回复是脚本。两档参数：

* ``unclaimed``：执行者写了文件、交了结果，但没认领计划为这一步声明的必需端口；
* ``undeclared``：执行者把产出认领到计划没有声明的端口上（原端到端 §15 "认领未声明端口"）。

两档都是：结果在收集时按名拒收（没认领 → ``completion_inputs_refused`` /
``OP_COMPLETION_PORT_CLAIMS_UNAVAILABLE``；认领未声明端口 → 读结果时就以 ``envelope_invalid`` /
``output_port_not_declared`` 拒），什么都不登记、不验收；主循环自己照常转（``run_until`` 里主循环
抛异常会原样冒出来）。原端到端 §15 的"检查层 ERROR 不算过"在产品世界里只有"必需检查层没部署"这一种
来源，产品对它的处置是停掉这一步（``verifier_unavailable``），不是拒收一份结果，归不进这里的参数；
它的纯函数规则由 ``verifier_router`` 的用例钉住（偏离：不在本文件补这一档）。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from h1i_seed import CONFIG, run_until  # noqa: E402

from agent_orchestrator.testing.product_world import product_world  # noqa: E402
from agent_orchestrator.testing.scripted_replies import (  # noqa: E402
    LayeredScriptedProvider,
    broken_result,
    worker_reply,
)


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _never_claims(request: Any) -> Any:
    """写出文件、交结果，但结果里不认领任何端口。"""
    reply = worker_reply(request)
    if isinstance(reply, tuple):
        return reply
    body = json.loads(reply[len("<result_envelope>"):-len("</result_envelope>")])
    body["outputs"] = {}
    return "<result_envelope>" + json.dumps(body, ensure_ascii=False) + "</result_envelope>"


WORKERS = {"unclaimed": _never_claims, "undeclared": broken_result}


@pytest.mark.parametrize("variant", sorted(WORKERS))
def test_an_unclaimed_declared_port_is_refused_as_a_result_and_the_loop_survives(tmp_path, variant) -> None:
    provider = LayeredScriptedProvider(worker=WORKERS[variant])

    async def case() -> dict[str, Any]:
        async with product_world(tmp_path / "root", provider, **CONFIG) as world:
            mission_id = world.create({"goal": "写一份 NOTES.md", "idempotency_key": f"port-{variant}",
                                       "success_criteria": ["file:NOTES.md"]})["mission_id"]
            store = world.store

            def refused() -> list[Any]:
                return [event for event in store.list_events(mission_id) if event.type == "ResultRejected"]

            await run_until(world, lambda: bool(refused()), timeout=60)
            events = list(store.list_events(mission_id))
            return {
                "rejected": [dict(event.payload) | {"task_id": event.task_id} for event in refused()],
                "submitted": [event for event in events if event.type == "ResultSubmitted"],
                "acceptances": store.connection.execute(
                    "SELECT COUNT(*) FROM acceptance_commit_receipts WHERE mission_id=?", (mission_id,)).fetchone()[0],
            }

    outcome = asyncio.run(case())
    [row] = outcome["rejected"][:1]
    if variant == "unclaimed":
        assert row["reason"] == "completion_inputs_refused", outcome["rejected"]
        assert row["detail"]["code"] == "OP_COMPLETION_PORT_CLAIMS_UNAVAILABLE", row
        assert "unclaimed" in row["detail"]["error"], row
    else:
        # 认领了没声明的端口：读结果时就按名拒（还没到完成要求那一层）。
        assert row["reason"] == "envelope_invalid", outcome["rejected"]
        assert row["detail"]["error"].startswith("output_port_not_declared"), row
    # nothing was registered or accepted for the refused envelope
    assert outcome["submitted"] == []
    assert outcome["acceptances"] == 0
