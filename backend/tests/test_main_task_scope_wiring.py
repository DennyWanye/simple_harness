# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


SID1 = "11111111-1111-4111-8111-111111111111"


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)


@pytest.mark.asyncio
async def test_product_harness_wrapper_does_not_wall_clock_cancel_run(
    monkeypatch,
):
    import main
    from deskpet.harness.kernel import root_run_identity

    cancelled: list[tuple[str, str, str]] = []
    sent: list[tuple[dict, str, str]] = []

    async def fake_chat(
        websocket,
        text: str,
        session_id: str,
        memory_policy_override=None,
        task_scope_explicit_new: bool = False,
        user_attachment_blocks=(),
        client_request_id: str | None = None,
        client_turn_id: str | None = None,
    ) -> None:
        del (
            websocket,
            text,
            session_id,
            memory_policy_override,
            task_scope_explicit_new,
            user_attachment_blocks,
            client_request_id,
            client_turn_id,
        )
        await asyncio.sleep(0.01)

    async def fake_cancel(session_id, run_id, *, reason):
        cancelled.append((session_id, run_id, reason))
        return True

    async def fake_send(_ws, msg, *, session_id, request_id):
        sent.append((msg, session_id, request_id))

    monkeypatch.setattr(main, "_run_product_harness_chat", fake_chat)
    monkeypatch.setattr(main, "_cancel_product_harness_run", fake_cancel)
    monkeypatch.setattr(
        main,
        "_product_harness_has_live_attached_child",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=False),
    )
    monkeypatch.setattr(main, "_send_chat_final", fake_send)
    monkeypatch.setattr(main, "_chat_turn_timeout_s", lambda: 0.001)

    ws = _FakeWS()
    await main._run_product_harness_chat_with_timeout(
        ws,
        "build a game",
        "session-timeout",
        client_request_id="request-timeout",
        client_turn_id="turn-timeout",
    )

    assert cancelled == []
    assert sent == []


@pytest.mark.asyncio
async def test_product_harness_timeout_keeps_live_attached_child_running(
    monkeypatch,
):
    import main

    finished = asyncio.Event()
    cancelled: list[tuple[str, str, str]] = []
    sent: list[dict] = []

    async def fake_chat(
        websocket,
        text: str,
        session_id: str,
        memory_policy_override=None,
        task_scope_explicit_new: bool = False,
        user_attachment_blocks=(),
        client_request_id: str | None = None,
        client_turn_id: str | None = None,
    ) -> None:
        del (
            websocket,
            text,
            session_id,
            memory_policy_override,
            task_scope_explicit_new,
            user_attachment_blocks,
            client_request_id,
            client_turn_id,
        )
        await asyncio.sleep(0.02)
        finished.set()

    async def fake_cancel(session_id, run_id, *, reason):
        cancelled.append((session_id, run_id, reason))
        return True

    async def fake_send(_ws, msg, **_kwargs):
        sent.append(msg)

    async def has_live_child(_session_id, _run_id):
        return True

    monkeypatch.setattr(main, "_run_product_harness_chat", fake_chat)
    monkeypatch.setattr(main, "_cancel_product_harness_run", fake_cancel)
    monkeypatch.setattr(
        main,
        "_product_harness_has_live_attached_child",
        has_live_child,
    )
    monkeypatch.setattr(main, "_send_chat_final", fake_send)
    monkeypatch.setattr(main, "_chat_turn_timeout_s", lambda: 0.001)

    await main._run_product_harness_chat_with_timeout(
        _FakeWS(),
        "build a complex presentation",
        "session-live-child",
        client_request_id="request-live-child",
        client_turn_id="turn-live-child",
    )

    assert finished.is_set()
    assert cancelled == []
    assert sent == []


@pytest.mark.asyncio
async def test_product_harness_timeout_does_not_cancel_chat_that_finishes_during_probe(
    monkeypatch,
):
    import main

    cancelled: list[str] = []
    sent: list[dict] = []

    async def fake_chat(
        websocket,
        text: str,
        session_id: str,
        memory_policy_override=None,
        task_scope_explicit_new: bool = False,
        user_attachment_blocks=(),
        client_request_id: str | None = None,
        client_turn_id: str | None = None,
    ) -> None:
        del (
            websocket,
            text,
            session_id,
            memory_policy_override,
            task_scope_explicit_new,
            user_attachment_blocks,
            client_request_id,
            client_turn_id,
        )
        await asyncio.sleep(0.003)

    async def slow_false_probe(*_args):
        await asyncio.sleep(0.012)
        return False

    async def fake_cancel(*_args, **_kwargs):
        cancelled.append("cancelled")
        return True

    async def fake_send(_ws, msg, **_kwargs):
        sent.append(msg)

    monkeypatch.setattr(main, "_run_product_harness_chat", fake_chat)
    monkeypatch.setattr(
        main,
        "_product_harness_has_live_attached_child",
        slow_false_probe,
    )
    monkeypatch.setattr(main, "_cancel_product_harness_run", fake_cancel)
    monkeypatch.setattr(main, "_send_chat_final", fake_send)
    monkeypatch.setattr(main, "_chat_turn_timeout_s", lambda: 0.001)

    await main._run_product_harness_chat_with_timeout(
        _FakeWS(),
        "quick completion",
        "session-probe-race",
    )

    assert cancelled == []
    assert sent == []


@pytest.mark.asyncio
async def test_product_harness_wrapper_ignores_legacy_child_timeout_probe(
    monkeypatch,
):
    import main

    probe_results = iter((True, False))
    cancelled: list[str] = []
    sent: list[dict] = []

    async def fake_chat(
        websocket,
        text: str,
        session_id: str,
        memory_policy_override=None,
        task_scope_explicit_new: bool = False,
        user_attachment_blocks=(),
        client_request_id: str | None = None,
        client_turn_id: str | None = None,
    ) -> None:
        del (
            websocket,
            text,
            session_id,
            memory_policy_override,
            task_scope_explicit_new,
            user_attachment_blocks,
            client_request_id,
            client_turn_id,
        )
        await asyncio.sleep(0.003)

    async def changing_probe(*_args):
        return next(probe_results)

    async def fake_cancel(*_args, **_kwargs):
        cancelled.append("cancelled")
        return True

    async def fake_send(_ws, msg, **_kwargs):
        sent.append(msg)

    monkeypatch.setattr(main, "_run_product_harness_chat", fake_chat)
    monkeypatch.setattr(
        main,
        "_product_harness_has_live_attached_child",
        changing_probe,
    )
    monkeypatch.setattr(main, "_cancel_product_harness_run", fake_cancel)
    monkeypatch.setattr(main, "_send_chat_final", fake_send)
    monkeypatch.setattr(main, "_chat_turn_timeout_s", lambda: 0.001)

    await main._run_product_harness_chat_with_timeout(
        _FakeWS(),
        "child later terminates",
        "session-recheck",
    )

    assert cancelled == []
    assert sent == []


@pytest.mark.asyncio
async def test_product_harness_timeout_wrapper_cancellation_cleans_presentation_task(
    monkeypatch,
):
    import main

    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def fake_chat(
        websocket,
        text: str,
        session_id: str,
        memory_policy_override=None,
        task_scope_explicit_new: bool = False,
        user_attachment_blocks=(),
        client_request_id: str | None = None,
        client_turn_id: str | None = None,
    ) -> None:
        del (
            websocket,
            text,
            session_id,
            memory_policy_override,
            task_scope_explicit_new,
            user_attachment_blocks,
            client_request_id,
            client_turn_id,
        )
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            cleaned.set()

    monkeypatch.setattr(main, "_run_product_harness_chat", fake_chat)
    monkeypatch.setattr(main, "_chat_turn_timeout_s", lambda: 60.0)

    wrapper = asyncio.create_task(
        main._run_product_harness_chat_with_timeout(
            _FakeWS(),
            "cancel wrapper",
            "session-wrapper-cancel",
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    wrapper.cancel()
    with pytest.raises(asyncio.CancelledError):
        await wrapper

    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_product_harness_wrapper_cancellation_needs_no_child_probe(
    monkeypatch,
):
    import main

    probe_started = asyncio.Event()
    cleaned = asyncio.Event()

    async def fake_chat(
        websocket,
        text: str,
        session_id: str,
        memory_policy_override=None,
        task_scope_explicit_new: bool = False,
        user_attachment_blocks=(),
        client_request_id: str | None = None,
        client_turn_id: str | None = None,
    ) -> None:
        del (
            websocket,
            text,
            session_id,
            memory_policy_override,
            task_scope_explicit_new,
            user_attachment_blocks,
            client_request_id,
            client_turn_id,
        )
        probe_started.set()
        try:
            await asyncio.sleep(60)
        finally:
            cleaned.set()

    async def slow_probe(*_args):
        probe_started.set()
        await asyncio.sleep(60)
        return True

    monkeypatch.setattr(main, "_run_product_harness_chat", fake_chat)
    monkeypatch.setattr(
        main,
        "_product_harness_has_live_attached_child",
        slow_probe,
    )
    monkeypatch.setattr(main, "_chat_turn_timeout_s", lambda: 0.001)

    wrapper = asyncio.create_task(
        main._run_product_harness_chat_with_timeout(
            _FakeWS(),
            "cancel during probe",
            "session-probe-cancel",
        )
    )
    await asyncio.wait_for(probe_started.wait(), timeout=1)
    wrapper.cancel()
    with pytest.raises(asyncio.CancelledError):
        await wrapper

    assert cleaned.is_set()


def test_main_chat_scope_helper_allocates_fresh_session_before_route_bind(monkeypatch):
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
    assert decision.created is True
    assert decision.stripped_text == "build a crawler"
    assert main._chat_peer_groups == {
        "default": "default",
        "message-panel-main": "default",
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

    # 2026-08-09：保留会话移除后 "default" 不再是合法会话 id，也不该再进清单。
    assert main._is_companion_history_session_id("default") is False
    assert main._is_companion_history_session_id(SID1) is True
    assert main._is_companion_history_session_id("task-default-22") is True
    assert main._is_companion_history_session_id("code-abc") is False
    assert main._is_companion_history_session_id("code-internal") is False
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


def test_general_agent_builds_complete_resolved_provider_chain(monkeypatch):
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

    providers = main._build_agent_provider_chain(
        entries=entries,
        registry=Registry(),
    )

    assert [provider.model for provider in providers] == ["model-1", "model-2"]
    assert [provider.provider_id for provider in providers] == ["first", "second"]
    assert [item["api_key"] for item in captured] == ["key-1", "key-2"]
    assert [item["is_relay"] for item in captured] == [True, False]


def test_code_recovery_rebuilds_exact_frozen_provider_and_model(monkeypatch):
    import main

    captured = {}

    def fake_provider(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    entry = SimpleNamespace(
        id="provider-1", base_url="https://provider.example",
        model="current-default", temperature=0.3,
        code_params={"reasoning_effort": "high"}, source="relay",
    )
    registry = SimpleNamespace(
        get_entry=lambda provider_id: entry if provider_id == entry.id else None,
        resolve_api_key=lambda _provider_id: "secret",
    )
    monkeypatch.setattr(main, "OpenAICompatibleProvider", fake_provider)

    provider = main._resolve_code_recovery_provider(
        provider_snapshot={"provider_id": "provider-1"},
        model_snapshot={"model": "frozen-model"},
        registry=registry,
        fallback=None,
    )

    assert provider.provider_id == "provider-1"
    assert captured["model"] == "frozen-model"
    assert captured["code_params"] == {"reasoning_effort": "high"}


def test_code_recovery_fails_closed_when_frozen_provider_is_unavailable():
    import main

    fallback = SimpleNamespace(provider_id="other", model="other-model")
    registry = SimpleNamespace(get_entry=lambda _provider_id: None)

    with pytest.raises(RuntimeError, match="frozen code provider unavailable"):
        main._resolve_code_recovery_provider(
            provider_snapshot={"provider_id": "missing"},
            model_snapshot={"model": "frozen-model"},
            registry=registry,
            fallback=fallback,
        )


def test_code_recovery_capability_contract_allows_child_narrowing():
    import main

    main._validate_code_recovery_capability_subset(
        prepared_names={
            "tool_search",
            "tool_describe",
            "tool_activate",
            "workflow_spawn",
        },
        allowed_tools={
            "tool_search",
            "tool_describe",
            "tool_activate",
        },
    )


def test_code_recovery_capability_contract_rejects_widening():
    import main

    with pytest.raises(
        RuntimeError,
        match="code recovery capability snapshot mismatch",
    ):
        main._validate_code_recovery_capability_subset(
            prepared_names={"tool_search", "tool_describe"},
            allowed_tools={"tool_search", "run_shell"},
        )


@pytest.mark.asyncio
async def test_product_host_exposes_durable_profiles_but_hides_legacy_starters(
    monkeypatch,
):
    import main

    provider = SimpleNamespace(
        provider_id="provider-1", model="model-1",
        adapter_id="openai-compatible", adapter_version="v1",
    )
    monkeypatch.setattr(
        main, "_resolve_agent_provider_chain",
        lambda *args, **kwargs: __import__("asyncio").sleep(0, result=[provider]),
    )
    registry = SimpleNamespace(
        list_tools=lambda: ["read_file", "deepresearch", "ppt_pro"],
        catalog_snapshot=lambda: SimpleNamespace(revision=7),
    )
    monkeypatch.setattr(main, "deskpet_tool_registry_v2", registry)
    monkeypatch.setattr(main.service_context, "get", lambda _name: None)

    host, *_ = await main._issue_product_harness_host(
        "session-1", workspace="C:/work"
    )

    assert {
        "workflow",
        "deep_research",
        "ppt_workflow",
        "durable_task",
    } <= host.available_capabilities
    assert "read_file" in host.available_capabilities
    assert "deepresearch" not in host.available_capabilities
    assert "ppt_pro" not in host.available_capabilities


@pytest.mark.asyncio
async def test_cancel_run_does_not_require_provider_or_workspace(
    monkeypatch,
) -> None:
    import main

    issued: dict[str, object] = {}
    cancelled: list[tuple[object, object, str]] = []
    host = SimpleNamespace(session_id="session-1")

    async def issue_host(session_id: str, **kwargs):
        issued.update({"session_id": session_id, **kwargs})
        return host, None, (), None

    async def cancel(ref, actor, reason):
        cancelled.append((ref, actor, reason))
        return SimpleNamespace(cancelled=False, status="failed")

    monkeypatch.setattr(main, "_harness_accepting", True)
    monkeypatch.setattr(
        main,
        "_harness_runtime",
        SimpleNamespace(run_client=SimpleNamespace(cancel=cancel)),
    )
    monkeypatch.setattr(main, "_issue_product_harness_host", issue_host)
    monkeypatch.setattr(main.service_context, "get", lambda _name: None)

    assert await main._cancel_product_harness_run(
        "session-1",
        "run-terminal",
        reason="user_interrupt",
    )
    assert issued == {
        "session_id": "session-1",
        "workspace": None,
        "allow_provider_unavailable": True,
        "allow_workspace_unavailable": True,
    }
    assert cancelled == [
        (
            {
                "run_id": "run-terminal",
                "expected_session_id": "session-1",
            },
            host,
            "user_interrupt",
        )
    ]


def test_selected_project_directory_uses_native_parent_and_safe_child(tmp_path):
    import main

    parent = tmp_path / "games"
    parent.mkdir()
    expected = parent / "apocalypse-demo"
    assert main._resolve_selected_project_directory(
        str(parent),
        "apocalypse-demo",
    ) == str(expected.resolve())

    with pytest.raises(ValueError, match="project_folder_name_invalid"):
        main._resolve_selected_project_directory(str(parent), "../escape")
    occupied = parent / "occupied"
    occupied.mkdir()
    (occupied / "project.godot").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="project_directory_not_empty"):
        main._resolve_selected_project_directory(str(parent), "occupied")
    assert main._resolve_selected_project_directory(
        str(occupied), "model-suggested-name", allow_existing=True
    ) == str(occupied.resolve())


@pytest.mark.asyncio
async def test_chat_retry_reuses_client_identity_without_duplicate_user_message(
    monkeypatch,
):
    import main
    from deskpet.harness.adapters.venues import ProductVenueRunResult

    class SessionDb:
        def __init__(self):
            self.appends = 0

        async def append_message(self, **_kwargs):
            self.appends += 1
            return 99

    class Venue:
        def __init__(self):
            self.turn = None

        async def open(self, turn, *_args, **_kwargs):
            self.turn = turn
            return ProductVenueRunResult(None, "resumed")

    session_db = SessionDb()
    venue = Venue()
    host = SimpleNamespace(
        session_id="session-1", write_scope_root=None,
        available_capabilities=frozenset(),
    )
    provider = SimpleNamespace(model="model-1")
    run_client = SimpleNamespace(
        resume=lambda *_args, **_kwargs: __import__("asyncio").sleep(
            0, result=SimpleNamespace(run_id="existing-run")
        )
    )
    monkeypatch.setattr(main, "_harness_accepting", True)
    monkeypatch.setattr(main, "_harness_venue", venue)
    monkeypatch.setattr(main, "_harness_runtime", SimpleNamespace(run_client=run_client))
    monkeypatch.setattr(
        main.service_context, "get",
        lambda name: session_db if name == "session_db" else None,
    )
    monkeypatch.setattr(
        main, "_issue_product_harness_host",
        lambda *_args, **_kwargs: __import__("asyncio").sleep(
            0, result=(host, provider, [provider], None)
        ),
    )
    monkeypatch.setattr(
        main, "deskpet_tool_registry_v2",
        SimpleNamespace(
            set_session_context=lambda *_args: None,
            catalog_snapshot=lambda: SimpleNamespace(revision=1),
        ),
    )

    reserved_frames = []
    await main._run_product_harness_chat(
        SimpleNamespace(send_json=lambda frame: reserved_frames.append(frame)),
        "retry", "session-1",
        client_request_id="request-stable",
        client_turn_id="turn-stable",
    )

    assert session_db.appends == 0
    assert venue.turn.request_id == "request-stable"
    assert venue.turn.turn_id == "turn-stable"
    assert reserved_frames[0]["type"] == "chat_v2_run_reserved"
    assert reserved_frames[0]["payload"] == {
        "session_id": "session-1",
        "run_id": venue.turn.root_run_id,
        "request_id": "request-stable",
        "turn_id": "turn-stable",
        "task_scope_id": venue.turn.task_scope_id,
        "projection_version": 0,
    }


@pytest.mark.asyncio
async def test_new_root_inherits_the_same_sessions_selected_project(
    monkeypatch,
):
    import main
    from deskpet.harness.adapters.venues import ProductVenueRunResult

    selected_root = r"C:\games\apocalypse-demo"

    class UnitOfWork:
        async def get_latest_session_project_context(self, session_id):
            assert session_id == "session-project"
            return {
                "project_name": "apocalypse-demo",
                "project_root": selected_root,
            }

    class Venue:
        def __init__(self):
            self.turn = None

        async def open(self, turn, *_args, **_kwargs):
            self.turn = turn
            return ProductVenueRunResult(None, "resumed")

    venue = Venue()
    issued = {}

    async def issue_host(session_id, *, workspace=None, **_kwargs):
        issued["session_id"] = session_id
        issued["workspace"] = workspace
        host = SimpleNamespace(
            session_id=session_id,
            workspace=workspace,
            write_scope_root=workspace,
            available_capabilities=frozenset(),
        )
        provider = SimpleNamespace(model="model-1")
        return host, provider, [provider], None

    monkeypatch.setattr(main, "_harness_accepting", True)
    monkeypatch.setattr(main, "_harness_venue", venue)
    monkeypatch.setattr(
        main,
        "_harness_runtime",
        SimpleNamespace(
            run_client=SimpleNamespace(
                resume=lambda *_args, **_kwargs: __import__("asyncio").sleep(
                    0, result=SimpleNamespace(run_id="existing-run")
                )
            )
        ),
    )
    workflow_service = SimpleNamespace(execution_uow=UnitOfWork())
    monkeypatch.setattr(
        main.service_context,
        "get",
        lambda name: workflow_service if name == "workflow_service" else None,
    )
    monkeypatch.setattr(main, "_issue_product_harness_host", issue_host)
    contexts = []
    monkeypatch.setattr(
        main,
        "deskpet_tool_registry_v2",
        SimpleNamespace(
            set_session_context=lambda sid, context: contexts.append((sid, context)),
            catalog_snapshot=lambda: SimpleNamespace(revision=1),
        ),
    )

    frames = []
    await main._run_product_harness_chat(
        SimpleNamespace(send_json=lambda frame: frames.append(frame)),
        "继续完善这个项目",
        "session-project",
        client_request_id="request-project-2",
        client_turn_id="turn-project-2",
    )

    assert issued == {
        "session_id": "session-project",
        "workspace": selected_root,
    }
    # Session inheritance belongs to the trusted host.  It must not masquerade
    # as a path explicitly supplied by this new user turn.
    assert venue.turn.workspace_ref is None
    assert contexts == [
        (
            "session-project",
            {
                "_session_id": "session-project",
                "_project_root": selected_root,
                "_write_scope_root": selected_root,
            },
        )
    ]


@pytest.mark.asyncio
async def test_continuation_targets_existing_root_and_advances_boundary(
    monkeypatch,
):
    import main

    class UnitOfWork:
        def __init__(self):
            self.boundary_reads = 0

        async def get_task_run_projection(self, _run_id):
            return SimpleNamespace(
                session_id="main-session",
                task_scope_id="scope-1",
            )

        async def get_conversation_boundary(self, _run_id):
            self.boundary_reads += 1
            return SimpleNamespace(
                session_id="main-session",
                task_scope_id="scope-1",
                boundary_ref="boundary-1",
                version=1 if self.boundary_reads == 1 else 2,
                continuation_message_refs=(),
            )

    class SessionDb:
        def __init__(self):
            self.appends = []

        async def append_message(self, **kwargs):
            self.appends.append(kwargs)
            return 101

    captured_signals = []

    async def signal(ref, _host, signal):
        captured_signals.append((ref, signal))
        return SimpleNamespace(accepted=True, reason=None)

    uow = UnitOfWork()
    session_db = SessionDb()
    workflow_service = SimpleNamespace(execution_uow=uow)
    monkeypatch.setattr(main, "_harness_accepting", True)
    monkeypatch.setattr(
        main,
        "_harness_runtime",
        SimpleNamespace(run_client=SimpleNamespace(signal=signal)),
    )
    monkeypatch.setattr(
        main.service_context,
        "get",
        lambda name: {
            "workflow_service": workflow_service,
            "session_db": session_db,
            "vector_worker": None,
        }.get(name),
    )
    monkeypatch.setattr(
        main,
        "_issue_product_harness_host",
        lambda *_args, **_kwargs: __import__("asyncio").sleep(
            0,
            result=(
                SimpleNamespace(session_id="main-session"),
                None,
                (),
                None,
            ),
        ),
    )

    async def ignore_broadcast(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        main, "_broadcast_default_chat_peers", ignore_broadcast
    )
    websocket = _FakeWS()
    await main._run_product_harness_continuation(
        websocket,
        "continue this task",
        "main-session",
        root_run_id="root-1",
        task_scope_id="scope-1",
        expected_boundary_version=1,
        request_id="request-followup",
    )

    assert captured_signals == [
        (
            {
                "run_id": "root-1",
                "expected_session_id": "main-session",
            },
            {
                "kind": "user_continuation",
                "task_scope_id": "scope-1",
                "message_ref": "request:request-followup",
                "content": "continue this task",
                "expected_boundary_version": 1,
            },
        )
    ]
    assert session_db.appends == [
        {
            "session_id": "main-session",
            "role": "user",
            "content": "continue this task",
            "workflow_event_id": (
                "user-continuation:root-1:request:request-followup"
            ),
            "root_run_id": "root-1",
            "task_scope_id": "scope-1",
        }
    ]
    assert websocket.sent[-1] == {
        "type": "chat_v2_continuation_accepted",
        "payload": {
            "session_id": "main-session",
            "request_id": "request-followup",
            "run_id": "root-1",
            "task_scope_id": "scope-1",
            "conversation_boundary_ref": "boundary-1",
            "conversation_boundary_version": 2,
            "queued": False,
        },
    }


@pytest.mark.asyncio
async def test_task_projection_ipc_lists_and_updates_without_run_control(
    monkeypatch,
):
    import main

    projection = SimpleNamespace(
        projection_id="projection-1",
        session_id="main-session",
        root_run_id="root-1",
        task_scope_id="scope-1",
        ui_state="open",
        version=0,
    )
    updated = SimpleNamespace(
        **{
            **projection.__dict__,
            "ui_state": "closed",
            "version": 1,
        }
    )

    class UnitOfWork:
        async def list_task_run_projections(
            self, session_id, *, include_closed=False
        ):
            assert session_id == "main-session"
            assert include_closed is False
            return (projection,)

        async def get_conversation_boundary(self, run_id):
            assert run_id == "root-1"
            return SimpleNamespace(
                boundary_ref="boundary-1",
                version=3,
            )

        async def get_task_work_context(self, run_id):
            assert run_id == "root-1"
            return SimpleNamespace(
                workspace_root="C:/games/apocalypse",
                workspace_source="user_path",
            )

        async def get_task_run_lifecycle(
            self, run_id, *, expected_session_id
        ):
            assert (run_id, expected_session_id) == (
                "root-1",
                "main-session",
            )
            return {
                "status": "completed",
                "started_at": 10.0,
                "updated_at": 20.0,
                "ended_at": 20.0,
            }

        async def get_task_run_projection(self, run_id):
            assert run_id == "root-1"
            return projection

        async def set_task_run_projection_state(
            self, run_id, ui_state, *, expected_version
        ):
            assert (run_id, ui_state, expected_version) == (
                "root-1",
                "closed",
                0,
            )
            return updated

    monkeypatch.setattr(
        main.service_context,
        "get",
        lambda name: (
            SimpleNamespace(execution_uow=UnitOfWork())
            if name == "workflow_service"
            else None
        ),
    )
    websocket = _FakeWS()
    assert await main._handle_control_ws_message(
        {
            "type": "task_projections_list",
            "payload": {"session_id": "main-session"},
        },
        session_id="main-session",
        ws=websocket,
    )
    assert websocket.sent[-1]["type"] == "task_projections_response"
    assert websocket.sent[-1]["payload"]["projections"] == [
        {
            "projection_id": "projection-1",
            "session_id": "main-session",
            "run_id": "root-1",
            "task_scope_id": "scope-1",
            "ui_state": "open",
            "version": 0,
            "conversation_boundary_ref": "boundary-1",
            "conversation_boundary_version": 3,
            "workspace_root": "C:/games/apocalypse",
            "workspace_source": "user_path",
            "status": "completed",
            "started_at": 10.0,
            "updated_at": 20.0,
            "ended_at": 20.0,
        }
    ]

    assert await main._handle_control_ws_message(
        {
            "type": "task_projection_update",
            "payload": {
                "session_id": "main-session",
                "run_id": "root-1",
                "ui_state": "closed",
                "expected_version": 0,
            },
        },
        session_id="main-session",
        ws=websocket,
    )
    assert websocket.sent[-1] == {
        "type": "task_projection_updated",
        "payload": {
            "projection_id": "projection-1",
            "session_id": "main-session",
            "run_id": "root-1",
            "task_scope_id": "scope-1",
            "ui_state": "closed",
            "version": 1,
        },
    }


@pytest.mark.asyncio
async def test_task_projection_hydration_replays_open_project_directory_card(
    monkeypatch,
):
    import main
    from deskpet.execution.contracts import DecisionOpen, DecisionRecord

    decision = DecisionRecord(
        request=DecisionOpen(
            decision_id="decision-project-1",
            run_id="root-project-1",
            nonce="nonce-project-1",
            kind="workflow_hitl",
            prompt_schema_version=1,
            prompt={
                "title": "选择项目保存位置",
                "required_action": "确认现有目录",
                "wait_ref": "wait-project-1",
                "project_name": "Jurassic Park: Escape",
                "folder_name": "jurassic-park-escape",
                "project_kind": "Godot 4 game",
                "directory_mode": "use_existing",
            },
            expires_at=None,
            domain_kind="external",
            domain_id="project_directory",
            call_id="call-project-1",
            effect_id="effect-project-1",
            tool_name="project_directory_select",
            args_hash="a" * 64,
            capability_hash="b" * 64,
            scope_hash="c" * 64,
        ),
        status="open",
        response_schema_version=None,
        response=None,
        decision_version=0,
        created_at=10.0,
        resolved_at=None,
    )

    class UnitOfWork:
        async def list_task_run_projections(
            self, session_id, *, include_closed=False
        ):
            assert (session_id, include_closed) == ("main-session", False)
            return ()

        async def list_open_decision_projections(self, session_id):
            assert session_id == "main-session"
            return (
                {
                    "decision": decision,
                    "session_id": session_id,
                    "root_run_id": "root-project-1",
                    "request_id": "request-project-1",
                    "turn_id": "turn-project-1",
                    "driver_kind": "react",
                    "workspace_root": r"F:\projects\jurassic-park-escape",
                    "workspace_source": "user_path",
                },
            )

    monkeypatch.setattr(
        main.service_context,
        "get",
        lambda name: (
            SimpleNamespace(execution_uow=UnitOfWork())
            if name == "workflow_service"
            else None
        ),
    )
    websocket = _FakeWS()

    assert await main._handle_control_ws_message(
        {
            "type": "task_projections_list",
            "payload": {"session_id": "main-session"},
        },
        session_id="message-panel-main",
        ws=websocket,
    )

    assert [frame["type"] for frame in websocket.sent] == [
        "task_projections_response",
        "project_directory_request",
    ]
    replay = websocket.sent[-1]["payload"]
    assert replay["session_id"] == "main-session"
    assert replay["run_id"] == "root-project-1"
    assert replay["decision_id"] == "decision-project-1"
    assert replay["wait_ref"] == "wait-project-1"
    assert replay["directory_mode"] == "use_existing"
    assert replay["parent_directory"] == r"F:\projects\jurassic-park-escape"
    assert replay["rehydrated"] is True


@pytest.mark.asyncio
async def test_harness_inspector_control_message_is_session_fenced_and_correlated(
    monkeypatch,
) -> None:
    import main

    expected_snapshot = {
        "schema_version": 1,
        "session_id": "main-session",
        "run": {"run_id": "root-1"},
    }

    class UnitOfWork:
        async def inspect_harness_run(
            self,
            run_id,
            *,
            expected_session_id,
        ):
            assert (run_id, expected_session_id) == (
                "root-1",
                "main-session",
            )
            return expected_snapshot

    monkeypatch.setattr(
        main.service_context,
        "get",
        lambda name: (
            SimpleNamespace(execution_uow=UnitOfWork())
            if name == "workflow_service"
            else None
        ),
    )
    websocket = _FakeWS()
    assert await main._handle_control_ws_message(
        {
            "type": "harness_inspector_snapshot",
            "request_id": "inspector-request-1",
            "payload": {
                "session_id": "main-session",
                "run_id": "root-1",
            },
        },
        session_id="main-session",
        ws=websocket,
    )
    assert websocket.sent[-1] == {
        "type": "harness_inspector_snapshot_response",
        "request_id": "inspector-request-1",
        "ok": True,
        "payload": expected_snapshot,
    }


def test_transport_disconnect_does_not_cancel_durable_run() -> None:
    import inspect
    import main

    source = inspect.getsource(main._run_product_harness_chat)
    assert 'session.cancel("transport_task_cancelled")' not in source


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
async def test_blocking_decision_broadcast_reaches_product_shell_across_topics(
    monkeypatch,
):
    import main

    origin = _FakeWS()
    pet_window = _FakeWS()
    unrelated_topic = _FakeWS()
    monkeypatch.setattr(
        main,
        "_control_connections",
        {
            "message-panel-main": origin,
            "default": pet_window,
            "code-other": unrelated_topic,
        },
    )
    monkeypatch.setattr(
        main,
        "_chat_peer_groups",
        {
            "message-panel-main": SID1,
            "default": "default",
            "code-other": "other-session",
        },
    )
    evt = {
        "type": "permission_request",
        "payload": {
            "session_id": SID1,
            "request_id": "permission-1",
            "tool_name": "workflow_spawn",
        },
    }

    await main._broadcast_default_chat_peers(origin, evt)

    assert origin.sent == []
    assert pet_window.sent == [evt]
    assert unrelated_topic.sent == [evt]


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


@pytest.mark.asyncio
async def test_live_research_v5_llm_adapter_preserves_provider_usage() -> None:
    import main

    class Provider:
        model = "gpt-test"

        async def chat_with_tools_at_most_once(self, **kwargs):
            assert kwargs["max_tokens"] == 321
            return {
                "content": '{"route":"persist"}',
                "model": self.model,
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
                "request_id": "request-v5",
            }

    call = main._make_research_llm_call_v2(Provider())
    result = await call("prompt", max_output_tokens=321, stable_call_id="stable-v5")
    assert result.content == '{"route":"persist"}'
    assert result.input_tokens == 12
    assert result.output_tokens == 7
    assert result.usage_source == "provider"


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
async def test_main_hydrates_large_workflow_history_in_bounded_pages():
    import main

    event_ids = [f"event-{index}" for index in range(205)]
    calls = []

    class SessionDB:
        async def get_session_delivery_state(self, session_id):
            return {"epoch": 7, "deleted_at": None}

    class WorkflowService:
        human_store = None

        async def hydrate_session_history_event_ids(
            self, session_id, page, *, current_session_epoch, session_deleted
        ):
            assert len(page) <= 100
            calls.append(list(page))
            return {
                "events": [
                    {
                        "event_id": event_id,
                        "run_id": "run-1",
                        "event_type": "workflow.progress",
                        "payload": {"ordinal": index + 1, "total": 205},
                    }
                    for index, event_id in enumerate(page)
                ]
            }

    messages = [
        {"id": event_id, "role": "assistant", "text": "progress"}
        for event_id in event_ids
    ]
    await main._attach_workflow_history_events(
        rows=[{"workflow_event_id": event_id} for event_id in event_ids],
        messages=messages,
        session_id="session-1",
        session_db=SessionDB(),
        workflow_service=WorkflowService(),
    )

    assert [len(page) for page in calls] == [100, 100, 5]
    assert all("workflow_event" in message for message in messages)


@pytest.mark.asyncio
async def test_main_hydrates_canonical_decision_with_current_projection_state():
    import main
    from types import SimpleNamespace

    from deskpet.execution.contracts import OutcomeStatus, RunEvent, RunEventCandidate

    event = RunEvent(
        event_id="canonical-decision",
        run_id="run-1",
        root_run_id="run-1",
        session_id="session-1",
        durable_seq=3,
        candidate=RunEventCandidate(
            event_key="decision:decision-1:open:v0",
            kind="workflow.decision",
            status=OutcomeStatus.WAITING,
            driver_kind="workflow",
            payload={
                "decision_id": "decision-1",
                "decision_kind": "ppt_outline",
                "status": "open",
                "version": 0,
                "prompt": {
                    "kind": "ppt_outline",
                    "outline_id": "outline-1",
                    "outline_markdown": "# Outline",
                },
            },
        ),
        created_at=123.0,
    )

    class ExecutionUow:
        async def get_event(self, event_id):
            assert event_id == "canonical-decision"
            return event

        async def get_decision_projection(
            self, decision_id, *, expected_run_id, expected_session_id
        ):
            assert (decision_id, expected_run_id, expected_session_id) == (
                "decision-1", "run-1", "session-1"
            )
            return SimpleNamespace(
                status=SimpleNamespace(value="allowed"),
                decision_version=1,
            )

    class WorkflowService:
        execution_uow = ExecutionUow()
        human_store = None

        async def hydrate_session_history_event_ids(self, *args, **kwargs):
            raise AssertionError("canonical history must not query legacy workflow_events")

    class SessionDB:
        async def get_session_delivery_state(self, session_id):
            return {"epoch": 2, "deleted_at": None}

    messages = [{"id": "canonical-decision", "role": "assistant", "text": "waiting"}]
    await main._attach_workflow_history_events(
        rows=[{"workflow_event_id": "canonical-decision"}],
        messages=messages,
        session_id="session-1",
        session_db=SessionDB(),
        workflow_service=WorkflowService(),
    )

    hydrated = messages[0]["workflow_event"]
    assert hydrated["event_type"] == "workflow.decision"
    assert hydrated["payload"]["status"] == "allowed"
    assert hydrated["payload"]["version"] == 1


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


@pytest.mark.asyncio
async def test_chat_error_reaches_session_peer():
    import main

    origin = _FakeWS()
    peer = _FakeWS()
    evt = {
        "type": "chat_v2_error",
        "payload": {
            "session_id": "default",
            "request_id": "request-1",
            "run_id": "run-1",
            "error": "provider_dispatch_unknown_after_handoff",
        },
    }
    previous_connections = dict(main._control_connections)
    previous_groups = dict(main._chat_peer_groups)
    try:
        main._control_connections.clear()
        main._chat_peer_groups.clear()
        main._control_connections["default"] = origin
        main._control_connections["message-panel-main"] = peer
        main._chat_peer_groups["default"] = "default"
        main._chat_peer_groups["message-panel-main"] = "default"
        await main._send_chat_error(
            origin,
            evt,
            session_id="default",
            request_id="request-1",
        )
    finally:
        main._control_connections.clear()
        main._control_connections.update(previous_connections)
        main._chat_peer_groups.clear()
        main._chat_peer_groups.update(previous_groups)

    assert origin.sent == [evt]
    assert peer.sent == [evt]
