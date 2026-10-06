# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""2026-10-05 用户决定：主 Agent 建任务时可以附资料、能查到有哪些任务、能把任务的资料换成新版本。

工具只整理命令：附了资料就走与任务页同一个"任务 + 资料"原子批次；换资料先核对用户读到的版本还是
现行的，再提交一条替换命令（命令号由运行号与调用号派生），变更本身仍等人批准。
"""

from __future__ import annotations

import pytest

from deskpet.orchestration.chat_tool import (
    MissionStartRefused,
    mission_list,
    mission_status,
    start_mission,
    update_mission_source,
)
from deskpet.orchestration.service import OrchestrationRequestError
from deskpet.sdk_adapters.tool_authority import SDK_DIRECT_TOOL_KERNEL, describe_call_zh
from deskpet.sdk_adapters.tools import HOST_COMPOSED_TOOL_NAMES, PRODUCT_TOOL_NAMES

OLD, NEW = "a" * 64, "b" * 64


class _Service:
    def __init__(self) -> None:
        self.created: list[tuple[str, dict]] = []
        self.commands: list[tuple[str, dict]] = []
        self.sources = [
            {"path": "sources/requirements.md", "version_hash": "0" * 64, "kind": "markdown",
             "superseded_by": OLD, "revoked": False},
            {"path": "sources/requirements.md", "version_hash": OLD, "kind": "markdown",
             "superseded_by": None, "revoked": False},
            {"path": "sources/gone.md", "version_hash": "c" * 64, "kind": "markdown",
             "superseded_by": None, "revoked": True},
        ]

    def status(self):
        return {"available": True}

    def create_mission(self, request):
        self.created.append(("plain", dict(request)))
        return {"mission_id": "m-1", "created": True}

    def create_mission_with_sources(self, request):
        self.created.append(("with_sources", dict(request)))
        return {"mission_id": "m-2", "created": True}

    def mission_detail(self, mission_id):
        if mission_id == "m-none":
            raise OrchestrationRequestError("not_found", "没有这个任务")
        return {"mission": {"id": mission_id, "status": "ACTIVE", "goal": "按需求写方案"}, "sources": self.sources,
                "approvals": [{"request_id": "approval-s", "state": "PENDING", "kind": "source_change",
                               "source_change": {"operation": "supersede", "path": "sources/requirements.md"}}]}

    def list_missions(self, *, limit):
        rows = [{"mission_id": "m-2", "goal": "按需求写方案", "status": "ACTIVE", "created_at": 2.0,
                 "pending_approvals": 1, "ui_state": "x"},
                {"mission_id": "m-1", "goal": "写笔记", "status": "COMPLETED", "created_at": 1.0,
                 "pending_approvals": 0, "ui_state": "y"}]
        return rows[:limit]

    def source_command(self, operation, request):
        self.commands.append((operation, dict(request)))
        return {"version_hash": NEW, "state": "PENDING", "request_id": "approval-source-1"}


def test_start_without_sources_is_the_plain_create_and_with_sources_is_the_atomic_batch():
    service = _Service()
    plain = start_mission(lambda: service, {"goal": "写笔记", "success_criteria": ["file:a.md"]},
                          run_id="r", call_id="c1")
    assert plain["mission_id"] == "m-1" and plain["sources"] == []
    attached = start_mission(lambda: service, {
        "goal": "按需求写方案", "success_criteria": ["file:plan.md"],
        "sources": [{"path": "requirements.md", "content": "# 需求\n第一版"},
                    {"path": "sources/notes.txt", "content": "备注"}]}, run_id="r", call_id="c2")
    assert attached["mission_id"] == "m-2"
    assert attached["sources"] == ["sources/requirements.md", "sources/notes.txt"]
    assert [kind for kind, _ in service.created] == ["plain", "with_sources"]
    batch = service.created[1][1]
    assert batch["mission"] == {"goal": "按需求写方案", "success_criteria": ["file:plan.md"],
                                "idempotency_key": "chat-mission:r:c2"}
    assert batch["sources"] == [
        {"path": "sources/requirements.md", "content": "# 需求\n第一版", "kind": "markdown"},
        {"path": "sources/notes.txt", "content": "备注", "kind": "text"}]


@pytest.mark.parametrize("sources", [
    [{"path": "a.md"}],
    [{"path": "", "content": "x"}],
    [{"path": "a.md", "content": ""}],
    [{"path": "a.md", "content": "x"}, {"path": "sources/a.md", "content": "y"}],
    [{"path": "a.md", "content": "x", "kind": "markdown"}],
    "a.md",
])
def test_start_refuses_malformed_sources_before_creating_anything(sources):
    service = _Service()
    with pytest.raises(MissionStartRefused) as refused:
        start_mission(lambda: service, {"goal": "g", "success_criteria": ["c"], "sources": sources},
                      run_id="r", call_id="c")
    assert refused.value.code == "invalid_arguments" and service.created == []


def test_status_shows_only_current_sources_and_the_pending_source_change():
    status = mission_status(lambda: _Service(), {"mission_id": "m-2"})
    assert status["sources"] == [{"path": "sources/requirements.md", "version_hash": OLD}]
    assert status["waiting_for"] == ["等用户批准资料变更 sources/requirements.md"]


def test_list_gives_ids_goals_and_states_newest_first():
    listing = mission_list(lambda: _Service(), {})
    assert [(m["mission_id"], m["goal"], m["status_zh"], m["pending_approvals"]) for m in listing["missions"]] == [
        ("m-2", "按需求写方案", "进行中", 1), ("m-1", "写笔记", "已完成", 0)]
    assert set(listing["missions"][0]) == {"mission_id", "goal", "status", "status_zh", "created_at",
                                           "pending_approvals", "recovery_isolated"}
    assert listing["missions"][0]["recovery_isolated"] is None  # 没被重启核对隔离
    assert [m["mission_id"] for m in mission_list(lambda: _Service(), {"limit": 1})["missions"]] == ["m-2"]
    for bad in ({"limit": 0}, {"limit": 51}, {"limit": "3"}, {"mission_id": "m"}):
        with pytest.raises(MissionStartRefused) as refused:
            mission_list(lambda: _Service(), bad)
        assert refused.value.code == "invalid_arguments"


def _update(**over):
    return {"mission_id": "m-2", "path": "requirements.md", "expected_version_hash": OLD,
            "content": "# 需求\n第二版", **over}


def test_source_update_sends_one_supersede_bound_to_the_version_the_user_read():
    service = _Service()
    receipt = update_mission_source(lambda: service, _update(), run_id="r", call_id="c9")
    assert service.commands == [("supersede", {
        "mission_id": "m-2", "path": "sources/requirements.md", "content": "# 需求\n第二版", "kind": "markdown",
        "expected_version_hash": OLD, "idempotency_key": "chat-source:r:c9"})]
    assert receipt["new_version_hash"] == NEW and receipt["state"] == "PENDING"
    assert receipt["approval_request_id"] == "approval-source-1" and receipt["mission_id"] == "m-2"


@pytest.mark.parametrize(("over", "code"), [
    ({"expected_version_hash": "0" * 64}, "SOURCE_VERSION_STALE"),   # 那一版早被替换了
    ({"path": "gone.md", "expected_version_hash": "c" * 64}, "SOURCE_UNKNOWN"),  # 已撤销的不算现行
    ({"path": "other.md"}, "SOURCE_UNKNOWN"),
    ({"mission_id": "m-none"}, "not_found"),
    ({"content": ""}, "invalid_arguments"),
    ({"kind": "markdown"}, "invalid_arguments"),
])
def test_source_update_refusals_send_nothing(over, code):
    service = _Service()
    with pytest.raises(MissionStartRefused) as refused:
        update_mission_source(lambda: service, _update(**over), run_id="r", call_id="c")
    assert refused.value.code == code and service.commands == []


def test_the_new_tools_are_product_tools_the_model_always_sees():
    for name in ("mission_list", "mission_source_update"):
        assert name in PRODUCT_TOOL_NAMES and name in HOST_COMPOSED_TOOL_NAMES and name in SDK_DIRECT_TOOL_KERNEL
    assert describe_call_zh("mission_list", {}) == "查看有哪些后台任务"
    assert describe_call_zh("mission_source_update", {"path": "requirements.md"}) == (
        "把后台任务的资料 requirements.md 换成新版本（提交后仍需你批准）")
