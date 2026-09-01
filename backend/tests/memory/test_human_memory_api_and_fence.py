from __future__ import annotations

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
