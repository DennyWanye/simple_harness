"""User decision 2026-09-26: in auto mode the Host confirms content-only requirements.

A new Mission waited in CREATED for the person to confirm its completion
requirements and looked stuck.  Content-only requirements (no ``action:`` criterion)
are now confirmed by the Host, recorded as HOST_AUTO_PERMISSION; anything with an
operation, and manual mode, keep the button.  An operation is a criterion carrying the
structured ``action:`` prefix — nothing else (片 D 第 4 项，2026-10-02).
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

from hashlib import sha256

from agent_orchestrator.deployment.duties import DeploymentDuties

REF = {"kind": "requirements", "id": "req-1", "revision": 1, "content_hash": "a" * 64}


def _service(*, mode="auto", criteria=("file:a.md", "file:b.md"), state="CONFIRMATION_REQUIRED"):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE missions(mission_id, status, created_at)")
    db.execute("INSERT INTO missions VALUES ('m1', 'CREATED', 1)")
    mission = SimpleNamespace(id="m1", success_criteria=tuple(criteria))
    calls = []

    class Control:
        def snapshot(self, mission_id):
            calls.append(("snapshot", (mission_id,)))
            return {"snapshot": {"operation_workspace": {
                "state": state, "editable": True, "requirements_ref": REF,
                "criteria": [{"id": "c-user-1", "required": True}, {"id": "c-user-2", "required": True},
                             {"id": "c-note", "required": False}]}}}

        def approve_operation_completion_spec(self, command):
            calls.append(("approve_operation_completion_spec", (command,)))
            return {}

    orchestrator = SimpleNamespace(store=SimpleNamespace(connection=db, get_mission=lambda _id: mission))
    duties = DeploymentDuties(orchestrator, Control(), tenant_id="t", principal=SimpleNamespace(principal_id="p"),
                              wake=lambda: calls.append(("wake", ())))
    return (duties, mode), calls


def _run(fake):
    duties, mode = fake
    return duties.auto_confirm_content_completion(auto=mode == "auto")


def test_content_only_requirements_are_confirmed_by_the_host_once():
    fake, calls = _service()
    assert _run(fake) == 1
    [(_, (command,))] = [c for c in calls if c[0] == "approve_operation_completion_spec"]
    assert command["approval_source"] == "HOST_AUTO_PERMISSION"
    assert command["proposal"]["mode"] == "CONTENT_ONLY" and command["proposal"]["effects"] == []
    assert command["proposal"]["content_criterion_ids"] == ["c-user-1", "c-user-2"]
    assert command["expected_requirements_ref"] == REF
    # 命令号是已有回执的身份（HTN 补齐阶段 A′ 搬进 SDK 时字节不变）
    key = f"m1:{REF['revision']}:{REF['content_hash']}"
    assert command["command_id"] == "host-auto-completion-" + sha256(key.encode()).hexdigest()[:32]
    assert ("wake", ()) in calls
    assert _run(fake) == 0  # the same requirements are never confirmed twice


def test_an_operation_manual_mode_or_an_approved_spec_keeps_the_button():
    for fake, _ in (_service(criteria=("file:a.md", "action:file_publish.x")), _service(mode="manual"),
                    _service(state="APPROVED")):
        assert _run(fake) == 0


def test_only_the_structured_prefix_marks_an_operation():
    """HTN 精简 片 D 第 4 项（2026-10-02）：一条要求是内容还是操作，只认主 Agent 整理任务时写好的
    结构化前缀 ``action:``。此前 Host 另用一张词表（发布、上传、发送、deploy……）去猜白话要求
    是不是操作——那是程序在判语义：写"说明如何发布"的文档任务会被挡住等人点确认。词表删除；
    白话要求与别的文字要求一样按内容确认，它满足与否由最终审查判。"""
    import deskpet.orchestration.service as service_module

    assert not hasattr(service_module, "_OPERATION_WORDS")
    for criteria in (("写 NOTES.md", "NOTES.md 里说明如何发布到授权的发布目录"),
                     ("upload report.pdf to the share",), ("Deploy the site",)):
        fake, calls = _service(criteria=criteria)
        assert _run(fake) == 1
        assert [c for c in calls if c[0] == "approve_operation_completion_spec"]
    # the structured prefix keeps the button wherever it stands in the list
    fake, calls = _service(criteria=("写 NOTES.md", "  action:file_publish.publish:NOTES.md"))
    assert _run(fake) == 0
    assert not [c for c in calls if c[0] == "approve_operation_completion_spec"]
