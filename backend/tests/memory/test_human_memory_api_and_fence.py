from __future__ import annotations

import json
from pathlib import Path

import pytest
from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import (
    AuthenticatedHostSnapshot,
    HumanMemoryHostServiceFactory,
)
from deskpet.memory.primary_authority import PrimaryAuthorityImmutableError
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.memory.session_db import SessionDB


def _auth() -> AuthenticatedHostSnapshot:
    return AuthenticatedHostSnapshot(
        "subject-api", "principal-api", "host:test-api"
    )


@pytest.mark.asyncio
async def test_ws_api_rejects_client_authority_and_binds_host_subject(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(path, approved_fresh_lane=True)
    factory = HumanMemoryHostServiceFactory(path, startup)
    rejected = await handle_human_memory_command(
        {
            "type": "human_memory_request",
            "request_id": "r1",
            "operation": "primary.open",
            "request": {"subject": "attacker"},
        },
        factory=factory,
        auth=_auth(),
    )
    assert rejected["payload"]["error"]["code"] == (
        "human_memory_public_authority_field_rejected"
    )

    opened = await handle_human_memory_command(
        {
            "type": "human_memory_request",
            "request_id": "r2",
            "operation": "primary.open",
            "request": {},
        },
        factory=factory,
        auth=_auth(),
    )
    assert opened["payload"]["ok"] is True
    receipt = await HumanMemoryProgramStore(path).open_primary_conversation(
        "subject-api"
    )
    assert opened["payload"]["result"]["primary_ref"] == (
        receipt.primary_conversation_id
    )


@pytest.mark.asyncio
async def test_ws_api_reports_stable_legacy_epoch_code() -> None:
    response = await handle_human_memory_command(
        {
            "type": "human_memory_request",
            "request_id": "r-legacy",
            "operation": "primary.open",
            "request": {},
        },
        factory=None,
        auth=_auth(),
    )
    assert response["payload"]["error"]["code"] == (
        "human_memory_legacy_epoch_unsupported"
    )


@pytest.mark.asyncio
async def test_ws_api_exposes_bounded_subject_bound_evidence_pages(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(path, approved_fresh_lane=True)
    factory = HumanMemoryHostServiceFactory(path, startup)
    created = await handle_human_memory_command(
        {
            "type": "human_memory_request",
            "request_id": "create-evidence",
            "operation": "task_scope.create",
            "request": {
                "fixture_key": "evidence-api",
                "title": "Evidence API",
                "goal": "bounded reads",
            },
        },
        factory=factory,
        auth=_auth(),
    )
    scope_ref = created["payload"]["result"]["scope_ref"]
    mutated = await handle_human_memory_command(
        {
            "type": "human_memory_request",
            "request_id": "mutate-evidence",
            "operation": "task_scope.mutate",
            "request": {
                "scope_ref": scope_ref,
                "mutation": {"kind": "status", "value": "active"},
            },
        },
        factory=factory,
        auth=_auth(),
    )
    assert mutated["payload"]["ok"] is True
    await factory.bind(_auth()).rebuild_derived(scope_ref)

    top = await handle_human_memory_command(
        {
            "type": "human_memory_request",
            "request_id": "read-top",
            "operation": "task_scope.view",
            "request": {"scope_ref": scope_ref, "kind": "EVIDENCE"},
        },
        factory=factory,
        auth=_auth(),
    )
    view = top["payload"]["result"]
    assert "pages" not in view
    assert len(json.dumps(top, ensure_ascii=False).encode()) <= 32 * 1024

    groups = await handle_human_memory_command(
        {
            "type": "human_memory_request",
            "request_id": "read-groups",
            "operation": "task_scope.evidence_groups",
            "request": {
                "scope_ref": scope_ref,
                "source_ref": view["source_ref"],
                "source_hash": view["source_hash"],
                "limit": 1,
            },
        },
        factory=factory,
        auth=_auth(),
    )
    group = groups["payload"]["result"]["groups"][0]
    page = await handle_human_memory_command(
        {
            "type": "human_memory_request",
            "request_id": "read-page",
            "operation": "task_scope.evidence_page",
            "request": {
                "scope_ref": scope_ref,
                "source_ref": view["source_ref"],
                "source_hash": view["source_hash"],
                "group_ref": group["group_ref"],
                "group_hash": group["group_hash"],
            },
        },
        factory=factory,
        auth=_auth(),
    )
    assert page["payload"]["ok"] is True
    assert len(json.dumps(page, ensure_ascii=False).encode()) <= 32 * 1024
    decoded = json.loads(page["payload"]["result"]["page"]["content"])
    assert decoded["events"]

    rejected = await handle_human_memory_command(
        {
            "type": "human_memory_request",
            "request_id": "authority-smuggle",
            "operation": "task_scope.evidence_groups",
            "request": {
                "scope_ref": scope_ref,
                "source_ref": view["source_ref"],
                "source_hash": view["source_hash"],
                "worker_authority": "attacker",
            },
        },
        factory=factory,
        auth=_auth(),
    )
    assert rejected["payload"]["error"]["code"] == (
        "human_memory_public_authority_field_rejected"
    )


@pytest.mark.asyncio
async def test_sessiondb_final_boundaries_reject_primary_before_legacy_write(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    await dispatch_startup_epoch(path, approved_fresh_lane=True)
    primary = await HumanMemoryProgramStore(path).initialize_subject("subject-fence")
    session_db = SessionDB(path)

    for operation in (
        lambda: session_db.ensure_session(primary.primary_conversation_id),
        lambda: session_db.set_session_title(primary.primary_conversation_id, "x"),
        lambda: session_db.clear(primary.primary_conversation_id),
    ):
        with pytest.raises(
            PrimaryAuthorityImmutableError,
            match="human_memory_primary_authority_immutable",
        ):
            await operation()
