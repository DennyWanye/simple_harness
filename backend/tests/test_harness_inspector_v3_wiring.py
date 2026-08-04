from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


class _Ws:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, value):
        self.messages.append(dict(value))


class _ReadService:
    async def create_manifest(self, *, session_id, root_run_id):
        return SimpleNamespace(
            projection_id="projection-1",
            session_id=session_id,
            root_run_id=root_run_id,
        )

    @staticmethod
    def snapshot(manifest):
        return SimpleNamespace(
            to_dict=lambda: {
                "schema_version": "3",
                "projection_id": manifest.projection_id,
                "session_id": manifest.session_id,
                "root_run_id": manifest.root_run_id,
                "aggregate_outcome": {"status": "running"},
                "semantic_phases": [],
                "totals": {},
                "projection_complete": False,
                "diagnostics": [],
                "read_cut": {},
            }
        )

    async def query_details(self, **kwargs):
        return SimpleNamespace(
            to_dict=lambda: {
                "schema_version": "3",
                "projection_id": kwargs["projection_id"],
                "query_kind": kwargs["query_kind"],
                "items": [],
                "total": 0,
                "next_cursor": None,
                "projection_complete": False,
            }
        )


async def _drain_inspector_tasks(main) -> None:
    tasks = tuple(main._harness_inspector_tasks.values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_v3_snapshot_and_details_use_correlated_background_slots(monkeypatch):
    import main

    ws = _Ws()
    monkeypatch.setattr(
        main.service_context,
        "harness_public_read_service",
        _ReadService(),
    )
    handled = await main._handle_control_ws_message(
        {
            "type": "harness_inspector_snapshot_request",
            "request_id": "snapshot-request",
            "payload": {
                "session_id": "session-1",
                "root_run_id": "root-1",
                "schema_version": "3",
            },
        },
        session_id="session-1",
        ws=ws,
    )
    assert handled is True
    assert (id(ws), "snapshot", "root-1") in main._harness_inspector_tasks
    await _drain_inspector_tasks(main)
    assert ws.messages[-1]["type"] == "harness_inspector_snapshot_response"
    assert ws.messages[-1]["request_id"] == "snapshot-request"
    assert ws.messages[-1]["snapshot"]["schema_version"] == "3"

    await main._handle_control_ws_message(
        {
            "type": "harness_inspector_details_request",
            "request_id": "details-request",
            "payload": {
                "session_id": "session-1",
                "root_run_id": "root-1",
                "projection_id": "projection-1",
                "query_kind": "tool_details",
                "page_size": 20,
            },
        },
        session_id="session-1",
        ws=ws,
    )
    assert (id(ws), "details", "root-1") in main._harness_inspector_tasks
    await _drain_inspector_tasks(main)
    assert ws.messages[-1]["type"] == "harness_inspector_details_response"
    assert ws.messages[-1]["request_id"] == "details-request"


@pytest.mark.asyncio
async def test_v3_cancel_stops_only_matching_connection_root(monkeypatch):
    import main

    started = asyncio.Event()
    never = asyncio.Event()

    class _Blocking(_ReadService):
        async def create_manifest(self, **kwargs):
            started.set()
            await never.wait()
            return await super().create_manifest(**kwargs)

    ws = _Ws()
    monkeypatch.setattr(
        main.service_context,
        "harness_public_read_service",
        _Blocking(),
    )
    await main._handle_control_ws_message(
        {
            "type": "harness_inspector_snapshot_request",
            "request_id": "snapshot-request",
            "payload": {"session_id": "session-1", "root_run_id": "root-1"},
        },
        session_id="session-1",
        ws=ws,
    )
    await started.wait()
    await main._handle_control_ws_message(
        {
            "type": "harness_inspector_cancel",
            "request_id": "cancel-request",
            "payload": {"root_run_id": "root-1", "request_kind": "snapshot"},
        },
        session_id="session-1",
        ws=ws,
    )
    await asyncio.sleep(0)
    assert ws.messages[-1] == {
        "type": "harness_inspector_cancelled",
        "request_id": "cancel-request",
        "cancelled": 1,
        "root_run_id": "root-1",
    }
    assert (id(ws), "snapshot", "root-1") not in main._harness_inspector_tasks
