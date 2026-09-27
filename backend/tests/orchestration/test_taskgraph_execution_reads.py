# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""NEXT-TG-1.0 §8.2: the execution-process reads are SDK reads carried over the control channel.

The Host only checks the request shape and ownership, forwards to the SDK read API
bound to its own tenant/principal, and maps SDK refusals to the wire unchanged.  It
reads no SDK table itself (``live_graph.py`` is gone).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agent_orchestrator.api.taskgraph import _fail

from deskpet.orchestration.service import OrchestrationRequestError
from deskpet.orchestration.taskgraph import TaskGraphRequestError, read_taskgraph

M = "mission-x"


class _Api:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def execution_snapshot(self, mission_id: str, *, cursor: str | None = None, limit: int = 100) -> dict[str, Any]:
        self.calls.append(("snapshot", mission_id, cursor, limit))
        if cursor == "stale":
            _fail("SNAPSHOT_CHANGED", "执行过程在翻页期间有变化，请重新读取第一页", retry="REQUERY")
        return {"mission_id": mission_id, "complete": True}

    def execution_detail(self, mission_id: str, node_id: str, *, through_journal_seq: int | None = None) -> dict[str, Any]:
        self.calls.append(("detail", mission_id, node_id, through_journal_seq))
        return {"mission_id": mission_id, "node": {"node_id": node_id}}


def _service(api: _Api, owned: set[str] = frozenset({M})) -> Any:  # type: ignore[assignment]
    def mission(mission_id: str) -> None:
        if mission_id not in owned:
            raise OrchestrationRequestError("not_found", "任务不存在")
    orchestrator = SimpleNamespace(taskgraph_read_api=lambda **_: api)
    return SimpleNamespace(_require=lambda: SimpleNamespace(_mission=mission), _orchestrator=orchestrator,
                           tenant_id="t", _principal="p")


def test_execution_reads_forward_to_the_sdk_read_api():
    api = _Api()
    service = _service(api)
    assert read_taskgraph(service, "execution_snapshot", {"mission_id": M})["complete"] is True
    read_taskgraph(service, "execution_snapshot", {"mission_id": M, "cursor": "c1", "limit": 50})
    read_taskgraph(service, "execution_detail", {"mission_id": M, "node_id": "attempt:a", "through_journal_seq": 7})
    assert api.calls == [("snapshot", M, None, 100), ("snapshot", M, "c1", 50), ("detail", M, "attempt:a", 7)]


@pytest.mark.parametrize("operation,body", [
    ("execution_snapshot", {"mission_id": M, "tenant_id": "other"}),
    ("execution_snapshot", {"mission_id": M, "limit": -1}),
    ("execution_snapshot", {"mission_id": M, "cursor": ""}),
    ("execution_detail", {"mission_id": M}),
    ("execution_detail", {"mission_id": M, "node_id": "n", "through_journal_seq": "7"}),
])
def test_bad_requests_never_reach_the_sdk(operation, body):
    api = _Api()
    with pytest.raises(OrchestrationRequestError) as caught:
        read_taskgraph(_service(api), operation, body)
    assert caught.value.code == "invalid_request" and api.calls == []


def test_ownership_first_and_sdk_refusals_keep_their_code():
    api = _Api()
    with pytest.raises(OrchestrationRequestError) as foreign:
        read_taskgraph(_service(api, owned=set()), "execution_snapshot", {"mission_id": M})
    assert foreign.value.code == "not_found" and api.calls == []
    with pytest.raises(TaskGraphRequestError) as stale:
        read_taskgraph(_service(api), "execution_snapshot", {"mission_id": M, "cursor": "stale"})
    assert stale.value.wire["code"] == "SNAPSHOT_CHANGED" and stale.value.wire["retry_kind"] == "REQUERY"


def test_the_host_no_longer_reads_sdk_tables_for_the_graph():
    root = Path(__file__).parents[2] / "deskpet" / "orchestration"
    assert not (root / "live_graph.py").exists()
    from deskpet.orchestration import handlers
    assert "mission_live_graph" not in handlers._ACTIONS and "mission_planning_decisions" not in handlers._ACTIONS
