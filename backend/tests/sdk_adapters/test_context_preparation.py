import pytest

from deskpet.sdk_adapters.context_preparation import (
    SdkAttachmentLimitExceeded,
    SdkContextPreparationService,
    SdkContextSources,
)


@pytest.mark.asyncio
async def test_prepares_one_exact_snapshot_and_excludes_non_conversation_rows() -> None:
    calls = {"history": 0, "memory": 0}

    async def history(_session_id):
        calls["history"] += 1
        return [
            {"role": "user", "content": "以前的对话", "projection_kind": "user_message"},
            {"role": "assistant", "content": "PUBLIC-SUMMARY-CANARY", "projection_kind": "workflow_progress", "context_visibility": "exclude"},
            {"role": "user", "content": "SESSION-B-CANARY", "projection_kind": "user_message", "root_run_id": "current-root"},
        ]

    async def memory(_session_id, _text):
        calls["memory"] += 1
        return [{"text": "喜欢乌龙茶"}]

    service = SdkContextPreparationService(
        SdkContextSources(
            history=history,
            persona=lambda: "你是桌面助手",
            memory=memory,
            skills=lambda _text: [{"instruction": "使用公开资料回答"}],
            project=lambda _sid: {"project_name": "示例"},
        )
    )
    snapshot = await service.prepare(
        session_id="session-a",
        request_id="request-a",
        root_run_id="current-root",
        sdk_run_id="sdk-a",
        turn_id="turn-a",
        text="现在的问题",
        provider_binding={"provider_id": "p", "model_id": "m", "context_window": 10000},
        catalog={"generation": 4, "content_fingerprint": "f" * 64, "tool_count": 2, "schema_token_count": 30},
    )

    assert calls == {"history": 1, "memory": 1}
    private = snapshot.private_record()
    rendered = str(private["provider_messages"])
    assert "以前的对话" in rendered
    assert "喜欢乌龙茶" in rendered
    assert "使用公开资料回答" in rendered
    assert "PUBLIC-SUMMARY-CANARY" not in rendered
    assert "SESSION-B-CANARY" not in rendered
    assert snapshot.snapshot_fingerprint == type(snapshot).build(
        session_id="session-a",
        request_id="request-a",
        root_run_id="current-root",
        sdk_run_id="sdk-a",
        turn_id="turn-a",
        provider_binding={"provider_id": "p", "model_id": "m", "context_window": 10000},
        provider_messages=private["provider_messages"],
        catalog=private["catalog"],
        attachments=private["attachments"],
        sections=private["sections"],
        budget=private["budget"],
    ).snapshot_fingerprint


@pytest.mark.asyncio
async def test_structured_attachment_is_private_and_bounded() -> None:
    service = SdkContextPreparationService(SdkContextSources(history=lambda _sid: []))
    snapshot = await service.prepare(
        session_id="s", request_id="r", root_run_id="root", sdk_run_id="sdk", turn_id="t",
        text="read", provider_binding={"context_window": 1000},
        catalog={"tool_count": 0, "schema_token_count": 0},
        attachment_blocks=({"type": "input_text", "data": "FIRST LINE\nbody-canary"},),
    )
    private = snapshot.private_record()
    assert private["provider_messages"][-1]["content"][1]["data"].startswith("FIRST LINE")
    assert "body-canary" not in str(private["attachments"])

    with pytest.raises(SdkAttachmentLimitExceeded):
        await service.prepare(
            session_id="s", request_id="r2", root_run_id="root2", sdk_run_id="sdk2", turn_id="t2",
            text="read", provider_binding={}, catalog={},
            attachment_blocks=({"type": "input_text", "data": "x" * (8 * 1024 * 1024 + 1)},),
        )
