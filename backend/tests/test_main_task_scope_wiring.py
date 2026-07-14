# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

from types import SimpleNamespace

import pytest


SID1 = "11111111-1111-4111-8111-111111111111"


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)


def test_main_chat_scope_helper_resolves_new_and_remaps_group(monkeypatch):
    import main
    from deskpet.session.task_scope import TaskSessionManager

    manager = TaskSessionManager(id_factory=lambda: SID1)
    manager.register_peer("default")
    manager.register_peer("message-panel-main")
    monkeypatch.setattr(main, "task_session_manager", manager)
    monkeypatch.setattr(
        main,
        "_chat_peer_groups",
        {
            "default": "default",
            "message-panel-main": "default",
        },
    )

    decision = main._resolve_chat_task_scope(
        base_sid="default",
        text="/new build a crawler",
        payload={},
    )

    assert decision.effective_sid == SID1
    assert decision.stripped_text == "build a crawler"
    assert main._chat_peer_groups == {
        "default": SID1,
        "message-panel-main": SID1,
    }


def test_main_chat_scope_helper_keeps_default_bc(monkeypatch):
    import main
    from deskpet.session.task_scope import TaskSessionManager

    manager = TaskSessionManager()
    monkeypatch.setattr(main, "task_session_manager", manager)
    monkeypatch.setattr(main, "_chat_peer_groups", {"default": "default"})

    decision = main._resolve_chat_task_scope(
        base_sid="default",
        text="plain chat",
        payload={},
    )

    assert decision.effective_sid == "default"
    assert decision.stripped_text == "plain chat"
    assert main._chat_peer_groups == {"default": "default"}


def test_main_history_filter_includes_uuid_and_legacy_task_only():
    import main

    assert main._is_companion_history_session_id("default") is True
    assert main._is_companion_history_session_id(SID1) is True
    assert main._is_companion_history_session_id("task-default-22") is True
    assert main._is_companion_history_session_id("code-abc") is False
    assert main._is_companion_history_session_id(
        "code-abc",
        code_base_session_ids={"code-abc"},
    ) is True
    assert main._is_companion_history_session_id(
        "code-internal",
        code_base_session_ids={"code-abc"},
    ) is False
    assert main._is_companion_history_session_id("mr_abc") is False
    assert main._is_companion_history_session_id("epi_abc") is False
    assert main._is_companion_history_session_id("") is False


def test_code_workflow_legacy_fallback_preserves_existing_provider_order():
    import main

    local = object()
    cloud = object()

    assert main._select_code_workflow_provider(
        local_llm=local,
        cloud_llm=cloud,
    ) is local
    assert main._select_code_workflow_provider(
        local_llm=local,
        cloud_llm=None,
    ) is local


def test_code_workflow_builds_complete_resolved_provider_chain(monkeypatch):
    import main

    captured: list[dict] = []

    def fake_provider(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(**kwargs)

    class Registry:
        def resolve_api_key(self, entry_id):
            return {"first": "key-1", "second": "key-2"}[entry_id]

    entries = [
        SimpleNamespace(
            id="first",
            base_url="https://first.example",
            model="model-1",
            temperature=0.2,
            code_params={"reasoning_effort": "high"},
            source="relay",
        ),
        SimpleNamespace(
            id="second",
            base_url="https://second.example",
            model="model-2",
            temperature=0.4,
            code_params={},
            source="custom",
        ),
    ]
    monkeypatch.setattr(main, "OpenAICompatibleProvider", fake_provider)

    providers = main._build_code_workflow_provider_chain(
        entries=entries,
        registry=Registry(),
    )

    assert [provider.model for provider in providers] == ["model-1", "model-2"]
    assert [provider.provider_id for provider in providers] == ["first", "second"]
    assert [item["api_key"] for item in captured] == ["key-1", "key-2"]
    assert [item["is_relay"] for item in captured] == [True, False]


@pytest.mark.asyncio
async def test_main_broadcast_uses_new_sid_for_session_switch_events(monkeypatch):
    import main

    origin = _FakeWS()
    peer = _FakeWS()
    monkeypatch.setattr(
        main,
        "_control_connections",
        {"default": origin, "message-panel-main": peer},
    )
    monkeypatch.setattr(
        main,
        "_chat_peer_groups",
        {
            "default": SID1,
            "message-panel-main": SID1,
        },
    )
    evt = {
        "type": "session_switched",
        "payload": {
            "old_sid": "default",
            "new_sid": SID1,
            "reason": "explicit_new",
        },
    }

    await main._broadcast_default_chat_peers(origin, evt)

    assert peer.sent == [evt]


@pytest.mark.asyncio
async def test_new_session_origin_echo_uses_resolved_sid():
    import main

    origin = _FakeWS()

    await main._send_new_session_origin_user_echo(origin, SID1, "build a crawler")

    assert origin.sent == [
        {
            "type": "chat_v2_user_echo",
            "payload": {"session_id": SID1, "text": "build a crawler"},
        }
    ]


@pytest.mark.asyncio
async def test_new_session_origin_echo_skips_empty_text():
    import main

    origin = _FakeWS()

    await main._send_new_session_origin_user_echo(origin, SID1, "  ")

    assert origin.sent == []


def test_context_compaction_model_reads_typed_nested_config() -> None:
    import main

    cfg = SimpleNamespace(
        context=SimpleNamespace(
            compaction=SimpleNamespace(model="gpt-5-mini")
        )
    )
    assert main._configured_context_compaction_model(cfg) == "gpt-5-mini"


def test_live_context_compaction_model_reloads_next_value(
    monkeypatch, tmp_path
) -> None:
    import main
    import p4_ipc

    values = iter(("gpt-5-mini", "claude-sonnet-4-5"))

    def reload(_path):
        return SimpleNamespace(
            context=SimpleNamespace(
                compaction=SimpleNamespace(model=next(values))
            )
        )

    monkeypatch.setattr(main, "load_config", reload)
    # This unit exercises the boot-config reload fallback.  A developer's
    # real persisted userdata config must not bypass the mocked source.
    monkeypatch.setattr(
        p4_ipc,
        "_context_compaction_config_path",
        lambda: tmp_path / "missing-config.toml",
    )
    assert main._live_context_compaction_model() == "gpt-5-mini"
    assert main._live_context_compaction_model() == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_explicit_new_task_projects_and_mounts_protected_context(monkeypatch):
    import main
    from deskpet.agent.assembler.bundle import ContextBundle
    from deskpet.agent.context_task import TaskContextProjector

    monkeypatch.setattr(
        main,
        "_build_context_task_projector",
        lambda **_kwargs: TaskContextProjector(),
    )
    snapshot = await main._project_initial_context_snapshot(
        session_id="session-1",
        request_id="request-1",
        user_text="Build a long-running report",
        explicit_new=True,
    )
    bundle = ContextBundle(task_type="chat")
    messages = [{"role": "user", "content": "Build a long-running report"}]
    main._attach_task_snapshot_to_request(bundle, messages, snapshot)

    assert snapshot is not None
    assert snapshot.task_scope_id == "session:session-1"
    assert snapshot.pending[0].fact_id == "request:request-1"
    assert bundle.fragments[-1].protected is True
    assert messages[0]["role"] == "system"
    assert messages[0]["__deskpet_context"]["lifetime"] == "task"
    assert messages[1]["role"] == "user"


@pytest.mark.asyncio
async def test_main_attaches_only_epoch_fenced_workflow_history():
    import main

    class SessionDB:
        async def get_session_delivery_state(self, session_id):
            assert session_id == "session-1"
            return {"epoch": 4, "deleted_at": None}

    class WorkflowService:
        async def hydrate_session_history_event_ids(
            self, session_id, event_ids, *, current_session_epoch, session_deleted
        ):
            assert session_id == "session-1"
            assert event_ids == ["event-1"]
            assert current_session_epoch == 4
            assert session_deleted is False
            return {
                "events": [
                    {
                        "event_id": "event-1",
                        "run_id": "run-1",
                        "seq": 3,
                        "event_type": "workflow.progress",
                        "payload": {"ordinal": 2, "total": 7},
                    }
                ]
            }

    messages = [{"id": "event-1", "role": "assistant", "text": "progress"}]
    await main._attach_workflow_history_events(
        rows=[{"workflow_event_id": "event-1"}],
        messages=messages,
        session_id="session-1",
        session_db=SessionDB(),
        workflow_service=WorkflowService(),
    )

    assert messages[0]["workflow_event"]["run_id"] == "run-1"


@pytest.mark.asyncio
async def test_chat_final_stalled_originator_does_not_block_peer(monkeypatch):
    import asyncio
    import main

    class StalledWS:
        async def send_json(self, _msg):
            await asyncio.Future()

    origin = StalledWS()
    peer = _FakeWS()
    evt = {
        "type": "chat_v2_final",
        "payload": {"session_id": "default", "text": "done"},
    }
    previous_connections = dict(main._control_connections)
    previous_groups = dict(main._chat_peer_groups)
    monkeypatch.setattr(main, "_PEER_BROADCAST_TIMEOUT_S", 0.01)
    try:
        main._control_connections.clear()
        main._chat_peer_groups.clear()
        main._control_connections["default"] = origin
        main._control_connections["message-panel-main"] = peer
        main._chat_peer_groups["default"] = "default"
        main._chat_peer_groups["message-panel-main"] = "default"
        await asyncio.wait_for(
            main._send_chat_final(
                origin,
                evt,
                session_id="default",
                request_id="request-1",
            ),
            timeout=0.2,
        )
    finally:
        main._control_connections.clear()
        main._control_connections.update(previous_connections)
        main._chat_peer_groups.clear()
        main._chat_peer_groups.update(previous_groups)

    assert peer.sent == [evt]
