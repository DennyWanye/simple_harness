# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import uuid


SID1 = "11111111-1111-4111-8111-111111111111"
SID2 = "22222222-2222-4222-8222-222222222222"


def _uuid_manager(*ids: str):
    from deskpet.session.task_scope import TaskSessionManager

    queue = list(ids) or [SID1]
    return TaskSessionManager(id_factory=lambda: queue.pop(0))


def test_resolve_new_strips_prefix_and_creates_uuid_sid():
    manager = _uuid_manager(SID1)

    decision = manager.resolve("default", "/new research rust tokio", explicit_new=False)

    assert uuid.UUID(decision.effective_sid).version == 4
    assert decision.effective_sid == SID1
    assert decision.created is True
    assert decision.reason == "explicit_new"
    assert decision.stripped_text == "research rust tokio"
    assert manager.active_sid("default") == SID1


def test_resolve_payload_new_without_prefix_keeps_text():
    manager = _uuid_manager(SID1)

    decision = manager.resolve("default", "research rust tokio", explicit_new=True)

    assert decision.effective_sid == SID1
    assert decision.created is True
    assert decision.reason == "explicit_new"
    assert decision.stripped_text == "research rust tokio"


def test_resolve_payload_new_from_active_task_does_not_nest_task_sid():
    manager = _uuid_manager(SID1, SID2)

    first = manager.resolve("default", "make a ppt", explicit_new=True)
    second = manager.resolve(first.effective_sid, "new topic", explicit_new=True)

    assert first.effective_sid == SID1
    assert second.effective_sid == SID2
    assert "task-task" not in second.effective_sid
    assert manager.active_sid("default") == SID2


def test_resolve_regular_message_from_active_task_stays_in_that_task():
    manager = _uuid_manager(SID1)
    first = manager.resolve("default", "make a ppt", explicit_new=True)

    followup = manager.resolve(first.effective_sid, "你给我大纲了吗？", explicit_new=False)

    assert followup.effective_sid == SID1
    assert followup.created is False
    assert followup.stripped_text == "你给我大纲了吗？"


def test_resolve_continue_strips_prefix_and_forces_l2():
    from deskpet.session.task_scope import TaskSessionManager

    manager = TaskSessionManager()

    decision = manager.resolve("default", "/continue compare again", explicit_new=False)

    assert decision.effective_sid == "default"
    assert decision.created is False
    assert decision.reason == "continue"
    assert decision.stripped_text == "compare again"
    assert decision.force_l2_page_in == "always"


def test_resolve_force_l2_marks_continue_without_stripping_text():
    from deskpet.session.task_scope import TaskSessionManager

    manager = TaskSessionManager()

    decision = manager.resolve(
        "default",
        "compare again",
        explicit_new=False,
        force_l2=True,
    )

    assert decision.effective_sid == "default"
    assert decision.reason == "continue"
    assert decision.stripped_text == "compare again"
    assert decision.force_l2_page_in == "always"


def test_resolve_default_is_bc_identity():
    from deskpet.session.task_scope import TaskSessionManager

    manager = TaskSessionManager()

    decision = manager.resolve("default", "hello", explicit_new=False)

    assert decision.effective_sid == "default"
    assert decision.created is False
    assert decision.reason == "default"
    assert decision.stripped_text == "hello"
    assert decision.force_l2_page_in is None


def test_peer_group_remaps_default_peers_together():
    manager = _uuid_manager(SID1)
    manager.register_peer("default")
    manager.register_peer("message-panel-main")

    decision = manager.resolve("default", "/new topic b", explicit_new=False)
    manager.remap_peer_group("default", decision.effective_sid)

    assert manager.peer_group("default") == SID1
    assert manager.peer_group("message-panel-main") == SID1
    assert manager.peers_for_group(SID1) == [
        "default",
        "message-panel-main",
    ]
