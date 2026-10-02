# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""OCC-02: actual Host dispatcher/facade confirms an existing requirements mapping.

This is a source integration check. It is not native UI or an operation-outcome
test; the requirements are the ones the Host persisted at creation (SDK requirements Store).
"""

from __future__ import annotations

import pytest

from agent_orchestrator.contracts.semantic_base import TypedRef, TypedRefKind
from agent_orchestrator.orchestrator.operation_completion import OperationCompletionReader
from agent_orchestrator.storage.htn_store import HtnStore

from deskpet.orchestration.handlers import handle
from agent_orchestrator.testing.word_counter import FixtureWordCounter
from deskpet.orchestration.service import OrchestrationService, OrchestrationSettings

from ._support import notes_provider, notes_request


@pytest.mark.asyncio
async def test_completion_confirmation_uses_actual_host_principal(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root, OrchestrationSettings(), provider=notes_provider(),
        principal=principal, drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await service.start()
    try:
        created = await handle(service, "mission_create", notes_request("completion-host"))
        assert created["payload"]["ok"], created
        mission_id = created["payload"]["data"]["mission_id"]
        store = service._orchestrator.store
        # 2026-09 起 Host 在创建时就初始化分层根（hierarchical.initialize_root）：
        # 协议已绑定、用户要求第 1 版已由 SDK 要求存储写入。确认的是这份真实要求，
        # 不再在测试里另绑协议/另插要求（那样与已绑定的协议冲突）。
        requirements = HtnStore(store).latest_requirements_revision(mission_id)
        assert requirements is not None and requirements.revision == 1
        [criterion] = requirements.criteria
        assert criterion.statement == "file:NOTES.md"
        ref = TypedRef(
            kind=TypedRefKind.REQUIREMENTS, id=str(requirements.revision_id), revision=1,
            content_hash=requirements.content_hash(),
        )
        body = {
            "mission_id": mission_id, "command_id": "confirm-host-completion",
            "expected_requirements_ref": ref.to_json(),
            "proposal": {
                "schema_version": 1, "mission_id": mission_id,
                "requirements_ref": {
                    "id": ref.id, "revision": ref.revision, "content_hash": ref.content_hash,
                },
                "mode": "CONTENT_ONLY", "content_criterion_ids": [criterion.criterion_id],
                "effects": [],
            },
        }
        result = await handle(service, "mission_operation_completion_approve", body)
        assert result["payload"]["ok"], result
        receipt = result["payload"]["data"]
        assert receipt["authority"]["issuer_id"] == principal.principal_id
        assert receipt["authority"]["tenant_id"] == service.tenant_id
        assert receipt["authority"]["kind"] == "USER_CONFIRMED"
        assert OperationCompletionReader(store).read_requirements(
            mission_id, ref
        ).content_hash() == receipt["spec_hash"]
        before = store.connection.total_changes
        repeated = await handle(service, "mission_operation_completion_approve", body)
        assert repeated["payload"]["data"] == receipt
        assert store.connection.total_changes == before
        for field in ("principal", "approved", "requirement_authority"):
            rejected = await handle(
                service, "mission_operation_completion_approve", {**body, field: True},
            )
            assert rejected["payload"]["ok"] is False
            assert rejected["payload"]["error_code"] == "invalid_request"
        assert store.connection.total_changes == before
        await service._rebuild()  # the decision shadow was removed (第三刀第 1 步)
        recovered = await handle(service, "mission_operation_completion_approve", body)
        assert recovered["payload"]["ok"], recovered
        assert recovered["payload"]["data"] == receipt
        recovered_store = service._orchestrator.store
        assert OperationCompletionReader(recovered_store).read_requirements(
            mission_id, ref
        ).content_hash() == receipt["spec_hash"]
    finally:
        await service.close()
