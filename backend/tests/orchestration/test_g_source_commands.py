"""Reference material through the real Host dispatch and facade: atomic creation, source
register/supersede/revoke and their approvals.  No provider calls are needed.

2026-10-02 (strict citation option A): these run on the general task; the document
domain, its citation reads and its source-currentness projection were removed."""

from unittest.mock import Mock

import pytest
from deskpet.orchestration.handlers import handle
from agent_orchestrator.testing.word_counter import FixtureWordCounter
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import notes_provider, notes_request


def batch(key="g-batch"):
    return {"mission": notes_request(key),
            "sources": [{"path": "sources/A.md", "content": "# 标题\r\n\r\n原文。\r\n",
                         "kind": "text/markdown"}]}


async def opened(root, principal):
    service = OrchestrationService(root, OrchestrationSettings(), principal=principal,
                                   provider=notes_provider(), drive=False, native_test_counter=FixtureWordCounter())
    await service.start()
    assert service.status()["state"] == "available", service.status()
    return service


@pytest.mark.asyncio
async def test_atomic_batch_through_real_handler_reopens_with_same_sources(orchestration_root, principal):
    service = await opened(orchestration_root, principal)
    try:
        reply = await handle(service, "mission_create_with_sources", batch(), request_id="ipc-1")
        assert reply["payload"]["ok"], reply
        assert reply["payload"]["request_id"] == "ipc-1"
        mid = reply["payload"]["data"]["mission_id"]
        first = service.mission_detail(mid)["sources"]
        assert first[0]["path"] == "sources/A.md"
        again = await handle(service, "mission_create_with_sources", batch())
        assert again["payload"]["data"]["mission_id"] == mid
    finally:
        await service.close()
    reopened = await opened(orchestration_root, principal)
    try:
        assert reopened.mission_detail(mid)["sources"] == first
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
        source = next(s for s in service.mission_detail(mid)["sources"] if s["path"] == "sources/B.md")
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
        after = next(s for s in service.mission_detail(mid)["sources"] if s["path"] == source["path"])
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
        old = service.mission_detail(mid)["sources"][0]
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
        history = next(s for s in detail["sources"] if s["version_hash"] == old["version_hash"])
        assert history["revoked"] if operation == "revoke" else history["superseded_by"]
    finally:
        await service.close()
