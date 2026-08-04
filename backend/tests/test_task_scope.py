# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations


def _manager(*session_ids: str):
    from deskpet.session.task_scope import TaskSessionManager

    generated = iter(session_ids or ("session-new",))
    return TaskSessionManager(id_factory=lambda: next(generated))


def test_resolve_new_strips_prefix_and_creates_fresh_session():
    manager = _manager("session-new")

    decision = manager.resolve("default", "/new research rust tokio", explicit_new=False)

    assert decision.effective_sid == "session-new"
    assert decision.created is True
    assert decision.reason == "explicit_new"
    assert decision.stripped_text == "research rust tokio"
    assert manager.active_sid("default") == "session-new"


def test_resolve_payload_new_without_prefix_keeps_text():
    manager = _manager("session-new")

    decision = manager.resolve("default", "research rust tokio", explicit_new=True)

    assert decision.effective_sid == "session-new"
    assert decision.created is True
    assert decision.reason == "explicit_new"
    assert decision.stripped_text == "research rust tokio"


def test_resolve_payload_new_from_current_session_creates_another_flat_session():
    manager = _manager("session-one", "session-two")

    first = manager.resolve("default", "make a ppt", explicit_new=True)
    second = manager.resolve(first.effective_sid, "new topic", explicit_new=True)

    assert first.effective_sid == "session-one"
    assert second.effective_sid == "session-two"
    assert "task-task" not in second.effective_sid
    assert manager.active_sid("default") == "session-two"


def test_resolve_regular_message_from_active_task_stays_in_that_task():
    manager = _manager("session-new")
    first = manager.resolve("default", "make a ppt", explicit_new=True)

    followup = manager.resolve(first.effective_sid, "你给我大纲了吗？", explicit_new=False)

    assert followup.effective_sid == "session-new"
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
    manager = _manager("session-new")
    manager.register_peer("default")
    manager.register_peer("message-panel-main")

    decision = manager.resolve("default", "/new topic b", explicit_new=False)
    manager.remap_peer_group("default", decision.effective_sid)

    assert manager.peer_group("default") == "session-new"
    assert manager.peer_group("message-panel-main") == "session-new"
    assert manager.peers_for_group("session-new") == [
        "default",
        "message-panel-main",
    ]


def test_legacy_task_session_creates_fresh_flat_session():
    manager = _manager("session-new")

    decision = manager.resolve(
        "task-default-22",
        "/new keep one product session",
        explicit_new=False,
    )

    assert decision.effective_sid == "session-new"
    assert decision.created is True
    assert decision.stripped_text == "keep one product session"
