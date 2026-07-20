# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent.auto_resume import AutoResumeOrchestrator
from agent.session_activity import SessionActivityStore
from agent.supervisor import SupervisorAction
from deskpet.memory.session_db import SessionDB
from deskpet.permissions.gate import PermissionGate
from deskpet.tools.receipt_store import ReceiptStore
from deskpet.workflows.adapters.product_delivery import ProductDeliveryAdapter
from pipeline.tag_parser import StreamingTagParser, TagEvent
from pipeline.voice_pipeline import VoicePipeline


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).parent / "fixtures"
CENSUS_PATH = FIXTURES / "product_turn_parity_census.json"
BEHAVIOR_PATH = FIXTURES / "product_turn_behavior_cases.json"
KEY = b"r0-parity-census-key-32-bytes!!"


def _load_census_module():
    path = ROOT / "scripts" / "acceptance" / "harness_parity_census.py"
    spec = importlib.util.spec_from_file_location("harness_parity_census", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves postponed annotations through sys.modules.
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_generated_census_fixture_is_current() -> None:
    module = _load_census_module()
    generated = module.build_census(ROOT)
    assert generated == _fixture(CENSUS_PATH)


def test_census_covers_required_product_capabilities() -> None:
    census = _fixture(CENSUS_PATH)
    assert census["base_commit"] == "4d38979e"
    assert census["unmapped_count"] == 0
    assert census["item_count"] == sum(census["counts_by_kind"].values())
    assert set(census["counts_by_capability"]) == set(census["required_capabilities"])

    required_columns = {
        "capability",
        "legacy_callsites",
        "precondition",
        "input_fields",
        "output_frames",
        "side_effects",
        "ordering",
        "error_cancel",
        "new_owner",
        "automatic_test",
        "manual_case",
        "status",
    }
    for row in census["inventory"]:
        assert required_columns <= set(row)
        assert row["legacy_callsites"]
        assert row["status"] == "FROZEN"

    kinds = census["counts_by_kind"]
    assert kinds["ws_send"] > 0
    assert kinds["session_sink"] > 0
    assert kinds["vector_sink"] > 0
    assert kinds["file_sink"] > 0
    assert kinds["waiter"] > 0
    assert kinds["cancel"] > 0
    assert kinds["codify"] > 0
    assert kinds["permission_restore"] == 1


def test_census_freezes_turn_input_fields() -> None:
    census = _fixture(CENSUS_PATH)
    assert census["turn_input_fields"] == [
        "text",
        "session_id",
        "request_id",
        "turn_id",
        "venue",
        "mode",
        "memory_policy",
        "explicit_new",
        "attachment_blocks",
        "provider_ref",
        "capability_ref",
        "workspace_ref",
    ]


class _RecordingWS:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send_json(self, value: dict[str, Any]) -> None:
        self.frames.append(value)


def _voice(*, control_ws=None, broadcast=None) -> VoicePipeline:
    return VoicePipeline(
        vad=SimpleNamespace(threshold=0.5),
        asr=object(),
        agent=object(),
        tts=object(),
        control_ws=control_ws,
        session_id="voice-origin",
        broadcast=broadcast,
    )


@pytest.mark.asyncio
async def test_voice_user_echo_and_final_are_observed() -> None:
    expected = _fixture(BEHAVIOR_PATH)["voice_broadcast"]
    calls: list[tuple[object, dict[str, Any]]] = []

    async def broadcast(originator: object, frame: dict[str, Any]) -> None:
        calls.append((originator, frame))

    control = _RecordingWS()
    voice = _voice(control_ws=control, broadcast=broadcast)
    await voice._broadcast_chat_v2(
        "chat_v2_user_echo", "hello", session_id="peer-session"
    )
    await voice._broadcast_chat_v2(
        "chat_v2_final", "world", session_id="peer-session"
    )

    assert [frame for _, frame in calls] == expected
    assert all(originator is control for originator, _ in calls)


@pytest.mark.asyncio
async def test_voice_tag_events_are_observed() -> None:
    expected = _fixture(BEHAVIOR_PATH)["voice_tags"]
    control = _RecordingWS()
    voice = _voice(control_ws=control)
    await voice._emit_tag_event(TagEvent(kind="emotion", value="happy"))
    await voice._emit_tag_event(TagEvent(kind="action", value="wave"))
    assert control.frames == expected


def test_streaming_tags_are_removed_from_spoken_text_and_observed() -> None:
    expected = _fixture(BEHAVIOR_PATH)["streaming_tags"]
    parser = StreamingTagParser()
    output = list(parser.feed("Hello [emotion:happy]wor"))
    output.extend(parser.feed("ld[action:wave]"))
    output.extend(parser.flush())
    assert "".join(item for item in output if isinstance(item, str)) == expected["spoken_text"]
    tags = [item for item in output if isinstance(item, TagEvent)]
    assert [(item.kind, item.value) for item in tags] == [
        ("emotion", expected["emotion"]),
        ("action", expected["action"]),
    ]


@pytest.mark.asyncio
async def test_voice_codify_is_fire_and_forget(monkeypatch: pytest.MonkeyPatch) -> None:
    started = False
    release = __import__("asyncio").Event()

    async def worker() -> None:
        nonlocal started
        started = True
        await release.wait()

    voice = _voice()
    voice._app_config = object()
    voice._service_context = object()
    monkeypatch.setattr(voice, "_codify_worker", worker)
    await voice._maybe_codify_voice()
    await __import__("asyncio").sleep(0)
    assert started is True
    assert len(voice._codify_tasks) == 1
    release.set()
    await __import__("asyncio").gather(*voice._codify_tasks)


def test_permission_auto_mode_restore_is_observed(tmp_path: Path) -> None:
    expected = _fixture(BEHAVIOR_PATH)["permission_restore"]
    persistence = tmp_path / "permissions_auto_mode.json"
    first = PermissionGate()
    first.bind_persistence_path(persistence)
    first.set_auto_mode(expected["enabled"])

    restarted = PermissionGate()
    restarted.bind_persistence_path(persistence)
    assert restarted.load_auto_mode() is expected["enabled"]
    assert restarted.auto_mode is expected["enabled"]


class _Supervisor:
    async def diagnose(self, sid: str, snapshot: dict[str, Any]) -> SupervisorAction:
        assert sid == "session-r0"
        assert snapshot["reason"] == "max_iterations"
        return SupervisorAction(
            action="nudge",
            severity="yellow",
            diagnosis="continue safely",
            hint_for_main_agent="resume from the durable boundary",
            alert_id="r0-alert",
        )


@pytest.mark.asyncio
async def test_auto_resume_injects_system_hint_and_emits_started() -> None:
    expected = _fixture(BEHAVIOR_PATH)["auto_resume"]
    activity = SessionActivityStore()
    await activity.bump("session-r0", event_type="assistant_message")
    dispatched: list[tuple[str, list[dict[str, Any]]]] = []
    emitted: list[tuple[str, dict[str, Any]]] = []
    audited: list[dict[str, Any]] = []

    async def dispatch(sid: str, messages: list[dict[str, Any]]) -> None:
        dispatched.append((sid, messages))

    async def emit(kind: str, payload: dict[str, Any]) -> None:
        emitted.append((kind, payload))

    async def audit(record: dict[str, Any]) -> None:
        audited.append(record)

    orchestrator = AutoResumeOrchestrator(
        supervisor=_Supervisor(),
        chat_dispatcher=dispatch,
        activity_store=activity,
        ws_emitter=emit,
        audit_writer=audit,
    )
    result = await orchestrator.handle_failure(
        "session-r0",
        "max_iterations",
        {"reason": "max_iterations"},
        [{"role": "user", "content": "finish it"}],
    )

    assert result.action == "spawned"
    assert emitted[0][0] == expected["event_type"]
    assert audited[0]["action"] == expected["audit_action"]
    hint = dispatched[0][1][-1]
    assert hint["role"] == expected["message_role"]
    assert hint[expected["hint_marker"]] is True


@pytest.mark.asyncio
async def test_workflow_delivery_persists_before_publish(tmp_path: Path) -> None:
    expected = _fixture(BEHAVIOR_PATH)["workflow_delivery"]
    session_db = SessionDB(tmp_path / "state.db")
    await session_db.initialize()
    receipt_store = ReceiptStore(tmp_path / "receipts", key=KEY)
    artifact = tmp_path / "report.md"
    artifact.write_text("# frozen parity\n", encoding="utf-8")
    observations: list[dict[str, Any]] = []

    async def publish(payload: dict[str, Any]) -> None:
        messages = await session_db.get_messages("session-r0")
        observations.append(
            {
                "payload": payload,
                "messages": messages,
                "visible_during_publish": bool(messages),
            }
        )

    adapter = ProductDeliveryAdapter(
        session_db=session_db,
        receipt_store=receipt_store,
        artifact_publisher=publish,
    )
    event = {
        "event_id": "event-r0",
        "event_type": "workflow.artifact_card",
        "run_id": "run-r0",
        "payload": {
            "kind": "artifact_card",
            "payload": {"tool": "deepresearch", "path": str(artifact)},
        },
    }
    result = await adapter.deliver_artifact(
        event,
        {"event_id": "event-r0", "channel": "artifact", "target_id": "session-r0"},
    )

    assert result["artifact_count"] == expected["artifact_count"]
    assert observations[0]["visible_during_publish"] is expected["visible_during_publish"]
    persisted = observations[0]["messages"][0]
    assert persisted["role"] == expected["role"]
    assert persisted["projection_kind"] == expected["projection_kind"]
    assert observations[0]["payload"]["message_id"] == result["message_id"]
