from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_product_preflight_block_projects_public_error_without_client_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import main
    from deskpet.execution.run_block_signals import (
        PreflightBlocked,
        RootBlockReasonV1,
    )

    calls: list[dict[str, object]] = []
    broadcasts: list[dict[str, object]] = []

    class _Stack:
        async def commit_preflight_blocked_root(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(kwargs)

    class _WebSocket:
        def __init__(self) -> None:
            self.frames: list[dict[str, object]] = []

        async def send_json(self, frame: dict[str, object]) -> None:
            self.frames.append(frame)

    async def broadcast(_originator, frame):  # type: ignore[no-untyped-def]
        broadcasts.append(frame)

    monkeypatch.setattr(main, "_sdk_ingress", SimpleNamespace())
    monkeypatch.setattr(main, "_sdk_runtime_stack", _Stack())
    monkeypatch.setattr(main, "_broadcast_default_chat_peers", broadcast)
    websocket = _WebSocket()

    await main._commit_product_preflight_block(
        websocket=websocket,
        text="must not execute",
        session_id="session-1",
        request_id="request-1",
        turn_id="turn-1",
        task_scope_id="scope-1",
        host=object(),
        block=PreflightBlocked(
            RootBlockReasonV1.WORKSPACE_UNAVAILABLE,
            ("workspace-binding:project_root_missing",),
        ),
    )

    assert len(calls) == 1
    assert calls[0]["reason_code"] == "workspace_unavailable"
    assert [frame["type"] for frame in websocket.frames] == [
        "chat_v2_run_started",
        "chat_v2_error",
    ]
    error_payload = websocket.frames[-1]["payload"]
    assert isinstance(error_payload, dict)
    assert error_payload["code"] == "workspace_unavailable"
    assert error_payload["error"] == "项目目录不可用，请重新定位同一项目后再试。"
    assert [frame["type"] for frame in broadcasts] == [
        "chat_v2_run_started",
        "chat_v2_error",
    ]
