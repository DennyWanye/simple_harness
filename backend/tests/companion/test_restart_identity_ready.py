# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 AK —— 重启就绪信号必须有界发出。

现场：同 userdata 重启后 `profile_bindings` 在 05:53:37.612Z 已经是
`status='ready'`（耐久绑定成功），但 UI 三分多钟一直停在「等待主对话就绪」，
`native.log` 里没有任何报错。前端唯一的就绪输入是 `companion_profile_bound`
（`tauri-app/src/primary/controller.ts`），而这一帧在 `companion_profile_bind`
处理链**跑完之后**才发；链上的 `_ensure_companion_inbox_route` 与
`bind_and_drain` 是两个无界 await，重启时短时索引重放正霸占 SDK 写道，
它们可以卡任意久 —— 就绪帧因此永远发不出去。

本用例钉死修好的语义：这段收尾有明确预算，任何卡死/异常都降级成稳定码，
就绪帧照发；成功路径仍要带回 inbox session_id。
"""

import asyncio

import pytest

import main


class _Identity:
    owner = "owner"
    binding_epoch = 1


@pytest.mark.asyncio
async def test_success_returns_session_id_and_no_failure_code():
    drained = []

    async def ensure_route(identity):
        return "inbox-session"

    class Service:
        async def bind_and_drain(self, identity):
            drained.append(identity)
            return True

    session_id, code = await main._settle_companion_bind_projection(
        _Identity(), ensure_route=ensure_route, notification_service=Service()
    )
    assert session_id == "inbox-session" and code is None
    assert len(drained) == 1


@pytest.mark.asyncio
async def test_stalled_projection_degrades_to_stable_code_instead_of_hanging():
    """依赖卡死时必须在预算内返回稳定码，而不是把就绪帧无限期扣住。"""

    async def ensure_route(identity):
        await asyncio.sleep(30)
        pytest.fail("unbounded route resolution must not be awaited to completion")

    session_id, code = await asyncio.wait_for(
        main._settle_companion_bind_projection(
            _Identity(),
            ensure_route=ensure_route,
            notification_service=None,
            timeout_seconds=0.05,
        ),
        timeout=5,
    )
    assert session_id is None
    assert code == main.COMPANION_BIND_PROJECTION_TIMEOUT_CODE


@pytest.mark.asyncio
async def test_stalled_history_drain_still_reports_a_code():
    """路由拿到了但闭合历史抽干卡住：同样降级，不再静默等待。"""

    async def ensure_route(identity):
        return "inbox-session"

    class Service:
        async def bind_and_drain(self, identity):
            await asyncio.sleep(30)

    session_id, code = await main._settle_companion_bind_projection(
        _Identity(),
        ensure_route=ensure_route,
        notification_service=Service(),
        timeout_seconds=0.05,
    )
    assert session_id is None
    assert code == main.COMPANION_BIND_PROJECTION_TIMEOUT_CODE


@pytest.mark.asyncio
async def test_dependency_error_projects_its_stable_code():
    class Missing(RuntimeError):
        code = "companion_session_store_unavailable"

    async def ensure_route(identity):
        raise Missing("companion_session_store_unavailable")

    session_id, code = await main._settle_companion_bind_projection(
        _Identity(), ensure_route=ensure_route, notification_service=None
    )
    assert session_id is None and code == "companion_session_store_unavailable"


def test_bind_handler_sends_readiness_after_the_bounded_settle():
    """源码断言：就绪帧的下发不再被收尾链的成败挡住。

    体例同 `tests/companion/test_runtime_shutdown.py`——这一段在 /ws/control
    的长处理函数里，靠源码顺序把「有界收尾 → 无条件发就绪帧」钉住。
    """

    from pathlib import Path

    source = Path(main.__file__).read_text(encoding="utf-8")
    handler = source[source.index('if msg_type == "companion_profile_bind":\n                        # 事件 AK'):]
    settle = handler.index("_settle_companion_bind_projection(")
    ready = handler.index("await ws.send_json(response)")
    assert settle < ready, "就绪帧必须在有界收尾之后无条件下发"
    assert "projection_degraded_code" in handler[:ready]
