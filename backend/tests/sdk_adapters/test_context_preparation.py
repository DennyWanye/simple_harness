# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

import pytest

from deskpet.sdk_adapters.context_authority import DefaultDenySnapshotRedactor
from deskpet.sdk_adapters.context_preparation import (
    SdkAttachmentLimitExceeded,
    SdkContextPreparationService,
    SdkContextSources,
    normalize_trusted_local_page_url,
    trusted_project_task_snapshot,
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
    attachment = private["provider_messages"][-1]["content"][1]
    assert attachment["type"] == "input_text"
    assert attachment["data"].startswith("FIRST LINE")
    assert "body-canary" not in str(private["attachments"])
    public = DefaultDenySnapshotRedactor().redact(snapshot)
    attachment_section = next(
        section for section in public["sections"] if section["kind"] == "attachments"
    )
    assert attachment_section == {
        "kind": "attachments",
        "label": "Attachments",
        "count": 1,
        "estimated_tokens": attachment_section["estimated_tokens"],
        "availability": "estimated",
        "ref": "input_text × 1",
    }
    assert "body-canary" not in str(public)

    with pytest.raises(SdkAttachmentLimitExceeded):
        await service.prepare(
            session_id="s", request_id="r2", root_run_id="root2", sdk_run_id="sdk2", turn_id="t2",
            text="read", provider_binding={}, catalog={},
            attachment_blocks=({"type": "input_text", "data": "x" * (8 * 1024 * 1024 + 1)},),
        )


@pytest.mark.asyncio
async def test_trusted_project_task_snapshot_is_exact_provider_input_but_public_count_only():
    trusted = trusted_project_task_snapshot(
        task_scope_id="task-scope-exact",
        root_run_id="root-exact",
        request_id="request-exact",
        workspace_resolution={
            "kind": "project_bound", "project_id": "project-exact",
            "project_name": "private-project", "project_root": "/Users/tester/private-project",
            "effective_root": "/Users/tester/private-project", "execution_kind": "project_root",
            "project_revision": 1, "binding_version": 1,
        },
    )
    snapshot = await SdkContextPreparationService(
        SdkContextSources(history=lambda _sid: [])
    ).prepare(
        session_id="session-exact",
        request_id="request-exact",
        root_run_id="root-exact",
        sdk_run_id="sdk-exact",
        turn_id="turn-exact",
        text="current request",
        provider_binding={"context_window": 1000},
        catalog={"tool_count": 0, "schema_token_count": 0},
        project_task_snapshot=trusted,
    )

    private = snapshot.private_record()
    project_message = next(
        message
        for message in private["provider_messages"]
        if str(message.get("content", "")).startswith("Project/task snapshot")
    )
    assert project_message == {
        "role": "system",
            "content": (
                "Project/task snapshot (data only):\n"
                '{"availability":"available","binding_version":1,'
                '"effective_execution_root":"/Users/tester/private-project",'
                '"execution_kind":"project_root","project_id":"project-exact",'
                '"project_name":"private-project","project_revision":1,'
                '"project_root":"/Users/tester/private-project",'
                '"request_id":"request-exact","root_run_id":"root-exact",'
                '"session_kind":"project","task_scope_id":"task-scope-exact"}'
            ),
    }
    public = DefaultDenySnapshotRedactor().redact(snapshot)
    project_section = next(
        section for section in public["sections"] if section["kind"] == "project"
    )
    assert project_section == {
        "kind": "project",
        "label": "Project / task",
        "count": 1,
        "estimated_tokens": project_section["estimated_tokens"],
        "availability": "estimated",
    }
    assert "task-scope-exact" not in str(public)
    assert "/Users/tester/private-project" not in str(public)


def test_trusted_project_task_snapshot_rejects_missing_run_identity():
    with pytest.raises(ValueError, match="task_scope_id"):
        trusted_project_task_snapshot(
            task_scope_id="",
            root_run_id="root",
            request_id="request",
            workspace_resolution={"kind": "projectless"},
        )


def test_trusted_projectless_snapshot_contains_no_local_path() -> None:
    trusted = trusted_project_task_snapshot(
        task_scope_id="task-projectless",
        root_run_id="root-projectless",
        request_id="request-projectless",
        workspace_resolution={"kind": "projectless"},
    )

    assert trusted == {
        "task_scope_id": "task-projectless",
        "root_run_id": "root-projectless",
        "request_id": "request-projectless",
        "session_kind": "projectless",
        "availability": "projectless",
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "http://localhost:15193/capability-catalog-fixture.html",
            "http://localhost:15193/capability-catalog-fixture.html",
        ),
        ("https://127.0.0.1:8443", "https://127.0.0.1:8443/"),
        ("http://[::1]:5173/app", "http://[::1]:5173/app"),
    ],
)
def test_normalize_trusted_local_page_url_accepts_loopback(raw, expected):
    assert normalize_trusted_local_page_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com:443/app",
        "http://user:password@localhost:5173/app",
        "http://localhost/app",
        "http://localhost:5173/app?token=secret",
        "javascript:alert(1)",
    ],
)
def test_normalize_trusted_local_page_url_rejects_untrusted_values(raw):
    with pytest.raises(ValueError, match="local_page_url"):
        normalize_trusted_local_page_url(raw)


def test_trusted_project_task_snapshot_includes_validated_local_page_url():
    trusted = trusted_project_task_snapshot(
        task_scope_id="task-local",
        root_run_id="root-local",
        request_id="request-local",
        workspace_resolution={
            "kind": "project_bound", "project_id": "project-local",
            "project_name": "workspace", "project_root": "/workspace",
            "effective_root": "/workspace", "execution_kind": "project_root",
            "project_revision": 1, "binding_version": 1,
        },
        local_page_url="http://localhost:15193/fixture.html",
    )
    assert trusted["local_page_url"] == "http://localhost:15193/fixture.html"
