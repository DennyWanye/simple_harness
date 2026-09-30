"""G real Host dispatch/facade oracles; no provider calls are needed for creation."""

from unittest.mock import Mock

import pytest
from deskpet.orchestration.handlers import handle
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import notes_provider, notes_request


def batch(key="g-batch"):
    return {"mission": notes_request(key, domain="doc-research-v1"),
            "sources": [{"path": "sources/A.md", "content": "# 标题\r\n\r\n原文。\r\n",
                         "kind": "text/markdown"}]}


async def opened(root, principal):
    service = OrchestrationService(root, OrchestrationSettings(), principal=principal,
                                   provider=notes_provider(), drive=False)
    await service.start()
    assert service.status()["state"] == "available", service.status()
    return service


@pytest.mark.asyncio
async def test_real_snapshot_domain_wrapper_drives_projection_and_source_currentness(
    orchestration_root, principal, monkeypatch
):
    from agent_orchestrator.memory.verified_knowledge import KnowledgeIndex
    from deskpet.orchestration.projection import project_detail

    service = await opened(orchestration_root, principal)
    calls = []
    original = KnowledgeIndex.stale

    def tracked(index, ids=None):
        calls.append(ids)
        return original(index, ids)

    monkeypatch.setattr(KnowledgeIndex, "stale", tracked)
    try:
        reply = await handle(service, "mission_create_with_sources", batch("snapshot-wrapper"))
        assert reply["payload"]["ok"], reply
        mid = reply["payload"]["data"]["mission_id"]
        # Unmodified facade snapshot, not a model Mission with invented domain_id.
        view = service._call("snapshot", mid)
        snapshot = view["snapshot"]
        assert "domain_id" not in snapshot["mission"]
        frozen = snapshot["mission_domain"]
        assert frozen["domain_id"] == frozen["json"]["id"] == "doc-research-v1"
        expected = {"id": frozen["domain_id"], "version": frozen["domain_version"]}
        assert project_detail(view)["document"]["domain"] == expected
        assert service.mission_detail(mid)["document"]["domain"] == expected
        assert len(calls) == 1  # Actual doc currentness entrance must not be skipped.

        code = await handle(service, "mission_create", notes_request("snapshot-code"))
        assert code["payload"]["ok"], code
        assert "document" not in service.mission_detail(code["payload"]["data"]["mission_id"])
        assert len(calls) == 1  # Code detail still performs no source-currentness read.
        assert service._call("snapshot", mid) == view
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_atomic_batch_through_real_handler_reopens_with_same_sources(orchestration_root, principal):
    service = await opened(orchestration_root, principal)
    try:
        reply = await handle(service, "mission_create_with_sources", batch(), request_id="ipc-1")
        assert reply["payload"]["ok"], reply
        assert reply["payload"]["request_id"] == "ipc-1"
        mid = reply["payload"]["data"]["mission_id"]
        first = service.mission_detail(mid)["document"]
        assert first["sources"][0]["path"] == "sources/A.md"
        again = await handle(service, "mission_create_with_sources", batch())
        assert again["payload"]["data"]["mission_id"] == mid
    finally:
        await service.close()
    reopened = await opened(orchestration_root, principal)
    try:
        assert reopened.mission_detail(mid)["document"]["sources"] == first["sources"]
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_bad_second_source_rolls_back_and_does_not_wake(orchestration_root, principal):
    service = await opened(orchestration_root, principal)
    try:
        service.wake = Mock()
        request = batch()
        request["sources"].append({"path": "../outside", "content": "bad", "kind": "text/plain"})
        reply = await handle(service, "mission_create_with_sources", request)
        assert reply["payload"]["ok"] is False
        assert service.list_missions() == []
        service.wake.assert_not_called()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_register_and_reject_revoke_preserve_original_source(orchestration_root, principal):
    service = await opened(orchestration_root, principal)
    try:
        created = await handle(service, "mission_create_with_sources", batch())
        assert created["payload"]["ok"], created
        mid = created["payload"]["data"]["mission_id"]
        registered = await handle(service, "mission_source_register", {
            "mission_id": mid, "path": "sources/B.md", "content": "新资料。", "kind": "text/plain",
            "idempotency_key": "register-B"})
        assert registered["payload"]["ok"], registered
        source = next(s for s in service.mission_detail(mid)["document"]["sources"] if s["path"] == "sources/B.md")
        request = {"mission_id": mid, "path": source["path"], "expected_version_hash": source["version_hash"],
                   "reason": "申请撤销", "idempotency_key": "revoke-B"}
        forged = await handle(service, "mission_source_revoke", {**request, "principal_id": "forged"})
        assert forged["payload"]["ok"] is False
        assert service.approvals(mid) == []
        pending = await handle(service, "mission_source_revoke", request)
        assert pending["payload"]["ok"], pending
        approval = service.approvals(mid)[0]
        empty_reason = await handle(service, "mission_approval_decide", {
            "approval_id": approval["request_id"], "decision": "reject"})
        assert empty_reason["payload"]["ok"] is False
        rejected = await handle(service, "mission_approval_decide", {
            "approval_id": approval["request_id"], "decision": "reject", "reason": "保留该资料"})
        assert rejected["payload"]["ok"], rejected
        after = next(s for s in service.mission_detail(mid)["document"]["sources"] if s["path"] == source["path"])
        assert after == source
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["supersede", "revoke"])
async def test_source_change_uses_existing_approval_and_fixed_principal(orchestration_root, principal, operation):
    service = await opened(orchestration_root, principal)
    try:
        reply = await handle(service, "mission_create_with_sources", batch())
        assert reply["payload"]["ok"], reply
        mid = reply["payload"]["data"]["mission_id"]
        old = service.mission_detail(mid)["document"]["sources"][0]
        command = {"mission_id": mid, "path": old["path"], "expected_version_hash": old["version_hash"],
                   "idempotency_key": "change", "reason": "用户撤销"}
        if operation == "supersede":
            command.pop("reason")
            command.update(content="新版本。", kind="text/plain")
        changed = await handle(service, f"mission_source_{operation}", command)
        assert changed["payload"]["ok"], changed
        pending = service.approvals(mid)
        assert len(pending) == 1 and pending[0]["kind"] == "source_change"
        assert pending[0]["source_change"]["expected_version_hash"] == old["version_hash"]
        decided = await handle(service, "mission_approval_decide", {
            "approval_id": pending[0]["request_id"], "decision": "approve"})
        assert decided["payload"]["ok"], decided
        detail = service.mission_detail(mid)
        assert detail["approvals"][0]["granted_by"] == [principal.principal_id]
        history = next(s for s in detail["document"]["sources"] if s["version_hash"] == old["version_hash"])
        assert history["revoked"] if operation == "revoke" else history["superseded_by"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_citation_read_dispatch_preserves_binding_and_character_page(tmp_path, principal):
    service = OrchestrationService(tmp_path, OrchestrationSettings(), principal=principal, drive=False)
    service._state = "available"
    service._call = Mock(return_value={"text": "原\r\n文", "offset": 3, "next_offset": 7,
                                     "total_chars": 10, "path": "sources/A.md", "version_hash": "a" * 64})
    reply = await handle(service, "mission_citation_read", {
        "mission_id": "m", "result_id": "r", "receipt_id": "receipt", "citation_index": 2,
        "offset": 3, "limit": 4}, request_id="page")
    assert reply["payload"]["ok"], reply
    service._call.assert_called_once_with("citation_read", "m", result_id="r", receipt_id="receipt",
                                          citation_index=2, offset=3, limit=4)
    data = reply["payload"]["data"]
    assert data["citation_index"] == 2 and data["citation_id"]
    assert data["text"] == "原\r\n文" and data["next_offset"] == 7
    assert data["version"] == data["version_hash"] == "a" * 64


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,offset,delayed", [("document-ui", 0, True), (None, 0, False), ("document-ui", 3, False)])
async def test_only_native_fixture_first_page_delays_delivery(tmp_path, principal, monkeypatch, scenario, offset, delayed):
    from unittest.mock import AsyncMock

    from deskpet.orchestration import handlers

    service = OrchestrationService(tmp_path, OrchestrationSettings(), principal=principal,
                                   test_scenario=scenario, drive=False)
    service._state = "available"
    original = {"text": "真实原文", "offset": offset, "next_offset": None,
                "total_chars": 4, "path": "sources/A.md", "version_hash": "a" * 64}
    service._call = Mock(return_value=original)
    delay = AsyncMock()
    monkeypatch.setattr(handlers.asyncio, "sleep", delay)
    reply = await handle(service, "mission_citation_read", {
        "mission_id": "m", "result_id": "r", "receipt_id": "receipt", "citation_index": 2,
        "offset": offset, "limit": 4}, request_id="real-page")
    assert reply["payload"]["ok"]
    assert reply["payload"]["data"]["text"] == original["text"]
    assert service._call.call_count == 1
    if delayed:
        delay.assert_awaited_once_with(1.5)
    else:
        delay.assert_not_awaited()


@pytest.mark.asyncio
async def test_citation_read_rejects_client_path_before_facade(tmp_path, principal):
    service = OrchestrationService(tmp_path, OrchestrationSettings(), principal=principal, drive=False)
    service._state = "available"
    service._call = Mock()
    reply = await handle(service, "mission_citation_read", {
        "mission_id": "m", "result_id": "r", "receipt_id": "receipt", "citation_index": 0,
        "path": "/private/file"})
    assert reply["payload"]["ok"] is False
    service._call.assert_not_called()


@pytest.mark.asyncio
async def test_real_sdk_accepted_report_25_claims_and_receipt_click(orchestration_root, principal):
    """Actual SDK runner/producer/storage; scripted provider, never a real model call."""
    import asyncio
    import json

    from agent_orchestrator.testing.fixtures import (
        RoleScriptedProvider,
        critic_step,
        envelope_step,
        graph_proposal_step,
        package_of,
    )

    from ._support import NOTES_TASK, SCRIPTED_LANE

    path = "sources/A.md"
    quotes = [f"第{i}项记录完整。" for i in range(1, 26)]
    content = "# 仅为资料记录\n\n" + "\n\n".join(quotes) + "\n"
    task = {**NOTES_TASK, "success_criteria": [f"cite:{path}"],
            "verification_policy": ["format_check", "rule_check", "critic_review"],
            "outputs": ["REPORT.md"]}

    def worker(request):
        version = package_of(request)["source_versions"][path]

        def cite(envelope):
            for i, claim in enumerate(envelope["claims"]):
                claim["citations"] = [{"path": path, "version": version, "start_line": 3 + 2 * i,
                                       "end_line": 3 + 2 * i, "quote": quotes[i]}]
            return envelope

        return envelope_step(summary="模型正文只是分析", artifacts=["REPORT.md"],
                             claims=quotes, override=cite)(request)

    reviewed = []

    def review(request):
        tool_outputs = [json.loads(message.content) for message in request.messages
                        if str(message.role) == "tool"]
        actual = tool_outputs[-1]["value"]["content"]
        reviewed.append(actual)
        met = actual == content
        return critic_step(verdict="PASS" if met else "FAIL", criteria_met=met)(request)

    provider = RoleScriptedProvider({
        "planner": [graph_proposal_step([task])],
        "worker": [("workspace_write_file", {"path": "REPORT.md", "content": content}), worker],
        "critic": [("workspace_read_file", {"path": "REPORT.md"}), review] * 3,
    })
    # 脚本化旧协议 Provider 只在夹具通道可用（见 _support.SCRIPTED_LANE）
    service = OrchestrationService(orchestration_root, OrchestrationSettings(), principal=principal,
                                   provider=provider, drive=False, test_scenario=SCRIPTED_LANE)
    await service.start()
    try:
        command = {"mission": notes_request("accepted-report", domain="doc-research-v1",
                                             success_criteria=[f"cite:{path}"]),
                   "sources": [{"path": path, "content": content, "kind": "text/markdown"}]}
        created = await handle(service, "mission_create_with_sources", command)
        assert created["payload"]["ok"], created
        mid = created["payload"]["data"]["mission_id"]
        await asyncio.wait_for(service.drain(), timeout=20)
        detail = service.mission_detail(mid)
        assert detail["mission"]["status"] == "COMPLETED", detail
        assert reviewed and all(actual == content for actual in reviewed)
        doc = detail["document"]
        assert len(doc["claims"]) == 25
        assert all(c["source_trust"] == "untrusted_external" for c in doc["claims"])
        target = doc["claims"][-1]["citations"][0]
        identity = {key: target[key] for key in ("mission_id", "result_id", "receipt_id", "citation_index")}
        before = service._orchestrator.store.snapshot(mid)
        pages, offset = [], 0
        while True:
            reply = await handle(service, "mission_citation_read", {**identity, "offset": offset, "limit": 4})
            assert reply["payload"]["ok"], reply
            page = reply["payload"]["data"]
            assert page["citation_id"] == target["citation_id"]
            assert page["version"] == target["version"]
            assert page["parent_headings"][0]["text"] == "# 仅为资料记录\n"
            pages.append(page["text"])
            if page["next_offset"] is None:
                break
            assert page["next_offset"] > offset
            offset = page["next_offset"]
        expected_line = content.splitlines(keepends=True)[target["locator"]["start_line"] - 1]
        assert "".join(pages) == expected_line
        assert service._orchestrator.store.snapshot(mid) == before
    finally:
        await service.close()
