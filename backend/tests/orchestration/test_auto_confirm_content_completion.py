"""User decision 2026-09-26: in auto mode the Host confirms content-only requirements.

A new Mission waited in CREATED for the person to confirm its completion
requirements and looked stuck.  Content-only requirements (no ``action:`` criterion)
are now confirmed by the Host, recorded as HOST_AUTO_PERMISSION; anything with an
operation, and manual mode, keep the button.
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

from deskpet.orchestration.service import OrchestrationService

REF = {"kind": "requirements", "id": "req-1", "revision": 1, "content_hash": "a" * 64}


def _service(*, mode="auto", criteria=("file:a.md", "file:b.md"), state="CONFIRMATION_REQUIRED"):
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE missions(mission_id, status, created_at)")
    db.execute("INSERT INTO missions VALUES ('m1', 'CREATED', 1)")
    mission = SimpleNamespace(id="m1", success_criteria=tuple(criteria))
    calls = []

    def call(method, *args):
        calls.append((method, args))
        if method == "snapshot":
            return {"snapshot": {"operation_workspace": {
                "state": state, "editable": True, "requirements_ref": REF,
                "criteria": [{"id": "c-user-1", "required": True}, {"id": "c-user-2", "required": True},
                             {"id": "c-note", "required": False}]}}}
        return {}

    async def reader():
        return mode

    fake = SimpleNamespace(
        _permission_mode_reader=reader, _control=object(), _auto_completion_done=set(),
        _orchestrator=SimpleNamespace(store=SimpleNamespace(connection=db, get_mission=lambda _id: mission)),
        _call=call, wake=lambda: calls.append(("wake", ())),
    )
    return fake, calls


def _run(fake):
    return asyncio.run(OrchestrationService._auto_confirm_content_completion(fake))


def test_content_only_requirements_are_confirmed_by_the_host_once():
    fake, calls = _service()
    assert _run(fake) == 1
    [(_, (command,))] = [c for c in calls if c[0] == "approve_operation_completion_spec"]
    assert command["approval_source"] == "HOST_AUTO_PERMISSION"
    assert command["proposal"]["mode"] == "CONTENT_ONLY" and command["proposal"]["effects"] == []
    assert command["proposal"]["content_criterion_ids"] == ["c-user-1", "c-user-2"]
    assert command["expected_requirements_ref"] == REF
    assert ("wake", ()) in calls
    assert _run(fake) == 0  # the same requirements are never confirmed twice


def test_an_operation_manual_mode_or_an_approved_spec_keeps_the_button():
    for fake, _ in (_service(criteria=("file:a.md", "action:file_publish.x")), _service(mode="manual"),
                    _service(state="APPROVED")):
        assert _run(fake) == 0


def test_a_plain_words_publish_criterion_keeps_the_button():
    # 2026-09-27 真机：「NOTES.md 已发布到授权的发布目录」被当成内容自动确认，发布被静默丢掉。
    for criteria in (("写 NOTES.md", "NOTES.md 已发布到授权的发布目录"), ("upload report.pdf to the share",),
                     ("Deploy the site",)):
        fake, calls = _service(criteria=criteria)
        assert _run(fake) == 0
        assert not [c for c in calls if c[0] == "approve_operation_completion_spec"]
    # the workspace's own statements are checked too, not only the mission's raw lines
    fake, calls = _service()
    original = fake._call

    def call(method, *args):
        result = original(method, *args)
        if method == "snapshot":
            result["snapshot"]["operation_workspace"]["criteria"][1]["statement"] = "把结果推送到群里"
        return result

    fake._call = call
    assert _run(fake) == 0
