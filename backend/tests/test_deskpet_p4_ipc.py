# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Tests for the P4-S11 IPC handlers (skills / decisions / permissions).

2026-09-10：MemoryPanel 记忆检索 / L1 文件记忆 / embedder 状态 / facts /
遗忘撤销的用例随认知记忆 SDK 移除（4b4dfba23、4087be659）一并删除。

Cover the remaining message types with:

- happy path (service registered, returns shaped payload)
- degraded path (service absent → empty list + reason)
- error path (service raises → empty list + error log, no crash)
- validation path (bad payload → error frame)

Isolated from main.py — tests talk to ``p4_ipc.handle`` directly with a
``FakeWebSocket`` and an in-memory ``FakeServiceContext``.
"""
from __future__ import annotations

from typing import Any

import pytest

import p4_ipc


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, msg: dict[str, Any]) -> None:
        self.sent.append(msg)


class FakeServiceContext:
    def __init__(self, **services: Any) -> None:
        self._services = dict(services)

    def get(self, name: str) -> Any:
        return self._services.get(name)


class FakeSkillLoader:
    def __init__(self, skills: list[dict[str, Any]] | Exception) -> None:
        self._skills = skills

    def list_skills(self) -> list[dict[str, Any]]:
        if isinstance(self._skills, Exception):
            raise self._skills
        return list(self._skills)


class FakeAssembler:
    def __init__(self, decisions: list[dict[str, Any]] | Exception) -> None:
        self._decisions = decisions
        self.last_n: int | None = None

    def recent_decisions(self, n: int = 20) -> list[dict[str, Any]]:
        self.last_n = n
        if isinstance(self._decisions, Exception):
            raise self._decisions
        return list(self._decisions)


class FakeAttemptStore:
    def public_for_session(self, session_id: str) -> list[dict[str, Any]]:
        return [{"session_id": session_id, "attempt_id": "a1", "state": "succeeded"}]


# ---------------------------------------------------------------------------
# skills_list
# ---------------------------------------------------------------------------
class TestSkillsList:
    @pytest.mark.asyncio
    async def test_returns_list_when_loader_registered(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext(
            skill_loader=FakeSkillLoader(
                [
                    {"name": "recall-yesterday", "scope": "built-in"},
                    {"name": "hello", "scope": "user"},
                ]
            )
        )
        await p4_ipc.handle(ws, "s1", "skills_list", {}, sc)
        assert len(ws.sent) == 1
        m = ws.sent[0]
        assert m["type"] == "skills_list_response"
        assert len(m["payload"]["skills"]) == 2
        assert m["payload"]["skills"][0]["name"] == "recall-yesterday"

    @pytest.mark.asyncio
    async def test_graceful_when_loader_absent(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext()  # no skill_loader
        await p4_ipc.handle(ws, "s1", "skills_list", {}, sc)
        m = ws.sent[0]
        assert m["type"] == "skills_list_response"
        assert m["payload"]["skills"] == []
        assert m["payload"]["reason"] == "skill_loader_not_registered"

    @pytest.mark.asyncio
    async def test_loader_raise_returns_empty_not_crash(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext(skill_loader=FakeSkillLoader(RuntimeError("boom")))
        await p4_ipc.handle(ws, "s1", "skills_list", {}, sc)
        m = ws.sent[0]
        assert m["type"] == "skills_list_response"
        assert m["payload"]["skills"] == []


# ---------------------------------------------------------------------------
# decisions_list
# ---------------------------------------------------------------------------
class TestDecisionsList:
    @pytest.mark.asyncio
    async def test_returns_decisions_respecting_limit(self) -> None:
        ws = FakeWebSocket()
        ass = FakeAssembler(
            [
                {"task_type": "chat", "latency_ms": 42},
                {"task_type": "recall", "latency_ms": 180},
            ]
        )
        sc = FakeServiceContext(context_assembler=ass)
        await p4_ipc.handle(
            ws, "s1", "decisions_list", {"limit": 10}, sc
        )
        assert ass.last_n == 10
        m = ws.sent[0]
        assert m["type"] == "decisions_list_response"
        assert len(m["payload"]["decisions"]) == 2

    @pytest.mark.asyncio
    async def test_limit_clamped_to_safe_range(self) -> None:
        ws = FakeWebSocket()
        ass = FakeAssembler([])
        sc = FakeServiceContext(context_assembler=ass)
        await p4_ipc.handle(ws, "s1", "decisions_list", {"limit": 999}, sc)
        assert ass.last_n == 200  # clamped
        ws.sent.clear()
        await p4_ipc.handle(ws, "s1", "decisions_list", {"limit": 0}, sc)
        assert ass.last_n == 1  # clamped
        ws.sent.clear()
        await p4_ipc.handle(ws, "s1", "decisions_list", {"limit": "bogus"}, sc)
        assert ass.last_n == 50  # default on bad input

    @pytest.mark.asyncio
    async def test_graceful_when_assembler_absent(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext()
        await p4_ipc.handle(ws, "s1", "decisions_list", {}, sc)
        m = ws.sent[0]
        assert m["payload"]["decisions"] == []
        assert m["payload"]["reason"] == "context_assembler_not_registered"

    @pytest.mark.asyncio
    async def test_context_os_attempts_are_scoped_to_requested_session(self) -> None:
        ws = FakeWebSocket()
        sc = FakeServiceContext(context_attempt_store=FakeAttemptStore())
        await p4_ipc.handle(ws, "session-a", "decisions_list", {"limit": 5}, sc)
        payload = ws.sent[0]["payload"]
        assert payload["attempts"] == [
            {"session_id": "session-a", "attempt_id": "a1", "state": "succeeded"}
        ]
        assert "reason" not in payload


# ---------------------------------------------------------------------------
# Membership guard
# ---------------------------------------------------------------------------
def test_message_type_membership() -> None:
    """main.py dispatches via P4_IPC_MESSAGE_TYPES — keep this contract stable."""
    assert p4_ipc.P4_IPC_MESSAGE_TYPES == frozenset(
        {
            "skills_list",
            "decisions_list",
            # Phase 1.1.6（context-1m-rearch）：模型上下文配置卡片。
            "model_context_get",
            "model_context_set",
            "context_compaction_get",
            "context_compaction_set",
            "model_provision_status",
            # WI-TG-2：ApprovalCenterPanel 只读「列 pending 权限请求」。
            "permissions_pending_list",
        }
    )
    # 2026-09-10 认知记忆 SDK 移除（4b4dfba23）：记忆面板九条消息与
    # embedder_status 不得回流到分发表。
    for removed in (
        "memory_search",
        "memory_l1_list",
        "memory_l1_delete",
        "embedder_status",
        "memory_facts_list",
        "memory_forget",
        "memory_forget_undo",
        "memory_pin",
        "memory_unpin",
    ):
        assert removed not in p4_ipc.P4_IPC_MESSAGE_TYPES


# ---------------------------------------------------------------------------
# WI-TG-2 — permissions_pending_list handler
# ---------------------------------------------------------------------------
class _FakeGate:
    """Read-only gate stub exposing list_pending(session_id=...)."""

    def __init__(self, pending: list[dict[str, Any]]) -> None:
        self._pending = pending
        self.last_session_id: Any = "<unset>"

    def list_pending(self, session_id: str | None = None) -> list[dict[str, Any]]:
        self.last_session_id = session_id
        if session_id is None:
            return list(self._pending)
        return [p for p in self._pending if p.get("session_id") == session_id]


@pytest.mark.asyncio
async def test_permissions_pending_list_returns_pending() -> None:
    """Unfiltered snapshot is independent of the control connection id."""
    gate = _FakeGate(
        [
            {"request_id": "r1", "category": "shell", "session_id": "s1"},
            {"request_id": "r2", "category": "network", "session_id": "s2"},
        ]
    )
    ws = FakeWebSocket()
    sc = FakeServiceContext(permission_gate=gate)
    await p4_ipc.handle(ws, "s1", "permissions_pending_list", {}, sc)
    assert len(ws.sent) == 1
    resp = ws.sent[0]
    assert resp["type"] == "permissions_pending_list_response"
    assert gate.last_session_id is None
    pend = resp["payload"]["pending"]
    assert [p["request_id"] for p in pend] == ["r1", "r2"]


@pytest.mark.asyncio
async def test_permissions_pending_list_explicit_session_filter() -> None:
    gate = _FakeGate(
        [
            {"request_id": "r1", "category": "shell", "session_id": "s1"},
            {"request_id": "r2", "category": "network", "session_id": "s2"},
        ]
    )
    ws = FakeWebSocket()
    sc = FakeServiceContext(permission_gate=gate)
    await p4_ipc.handle(
        ws,
        "message-panel-main",
        "permissions_pending_list",
        {"session_id": "s2"},
        sc,
    )
    assert gate.last_session_id == "s2"
    assert [p["request_id"] for p in ws.sent[0]["payload"]["pending"]] == ["r2"]


@pytest.mark.asyncio
async def test_permissions_pending_list_gate_unregistered() -> None:
    """No gate → empty list + reason, never raises."""
    ws = FakeWebSocket()
    sc = FakeServiceContext()  # no permission_gate
    await p4_ipc.handle(ws, "s1", "permissions_pending_list", {}, sc)
    resp = ws.sent[0]
    assert resp["type"] == "permissions_pending_list_response"
    assert resp["payload"]["pending"] == []
    assert resp["payload"]["reason"] == "permission_gate_not_registered"
