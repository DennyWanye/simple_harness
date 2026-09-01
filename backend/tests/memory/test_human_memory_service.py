from __future__ import annotations

import sqlite3
from dataclasses import fields
from pathlib import Path

import pytest

from deskpet.memory.human_memory_service import (
    AppendDeterministicEventsRequest,
    AuthenticatedHostSnapshot,
    CreateTaskScopeRequest,
    HumanMemoryHostService,
    HumanMemoryHostServiceError,
    SaveCheckpointRequest,
)
from deskpet.memory.schema import (
    StartupCompositionMode,
    StartupEpoch,
    StartupEpochDecision,
)
from deskpet.memory.s4_value_adapter import S4ValuePublicAdapter


def _startup(mode: StartupCompositionMode) -> StartupEpochDecision:
    return StartupEpochDecision(
        StartupEpoch.HUMAN_RESUME
        if mode is StartupCompositionMode.HUMAN
        else StartupEpoch.LEGACY,
        mode,
        41 if mode is StartupCompositionMode.HUMAN else 34,
        "test",
    )


def _auth(subject: str = "subject-a") -> AuthenticatedHostSnapshot:
    return AuthenticatedHostSnapshot(subject, f"principal-{subject}", "auth-test-1")


def test_public_request_dtos_cannot_carry_host_authority() -> None:
    forbidden = {
        "subject",
        "allowed_scope_ids",
        "mode",
        "authority",
        "authority_ref",
        "database_path",
        "worker_authority",
    }
    for request_type in (
        CreateTaskScopeRequest,
        AppendDeterministicEventsRequest,
        SaveCheckpointRequest,
    ):
        assert forbidden.isdisjoint(field.name for field in fields(request_type))


def test_facade_rejects_non_human_composition_before_constructing_stores(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        HumanMemoryHostServiceError,
        match="human_memory_program_legacy_database_unsupported",
    ):
        HumanMemoryHostService(
            tmp_path / "legacy.db",
            auth=_auth(),
            startup=_startup(StartupCompositionMode.LEGACY),
        )


@pytest.mark.asyncio
async def test_bulk_seed_is_subject_bound_and_uses_injected_canonical_port(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE task_scopes(task_scope_id TEXT PRIMARY KEY, subject TEXT NOT NULL)"
        )
        db.execute("INSERT INTO task_scopes VALUES ('scope-a','subject-a')")
        db.commit()

    class SeedPort:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def append_deterministic_events(self, **values):  # type: ignore[no-untyped-def]
            self.calls.append(values)
            return {"event_count": values["count"], "receipt_ref": "bulk-1"}

    seed = SeedPort()
    service = HumanMemoryHostService(
        path,
        auth=_auth(),
        startup=_startup(StartupCompositionMode.HUMAN),
        deterministic_event_seed=seed,
    )
    result = await service.append_deterministic_events(
        AppendDeterministicEventsRequest("scope-a", 100_000, "canary", "seed-1")
    )
    assert result == {"event_count": 100_000, "receipt_ref": "bulk-1"}
    assert seed.calls == [
        {
            "subject": "subject-a",
            "task_scope_id": "scope-a",
            "count": 100_000,
            "canary": "canary",
            "idempotency_key": "seed-1",
        }
    ]

    wrong = HumanMemoryHostService(
        path,
        auth=_auth("subject-b"),
        startup=_startup(StartupCompositionMode.HUMAN),
        deterministic_event_seed=seed,
    )
    with pytest.raises(HumanMemoryHostServiceError, match="human_memory_permission_denied"):
        await wrong.append_deterministic_events(
            AppendDeterministicEventsRequest("scope-a", 1, "canary", "seed-2")
        )


@pytest.mark.asyncio
async def test_raw_manifest_is_deterministic_and_excludes_other_subject_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE task_scopes(task_scope_id TEXT PRIMARY KEY, subject TEXT NOT NULL);
            CREATE TABLE task_scope_events(
                event_id TEXT PRIMARY KEY,
                task_scope_id TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE human_memory_evidence(
                evidence_id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        db.execute("INSERT INTO task_scopes VALUES ('scope-a','subject-a')")
        db.execute("INSERT INTO task_scopes VALUES ('scope-b','subject-b')")
        db.execute("INSERT INTO task_scope_events VALUES ('a1','scope-a','alpha')")
        db.execute("INSERT INTO task_scope_events VALUES ('b1','scope-b','beta')")
        db.execute("INSERT INTO human_memory_evidence VALUES ('a1','subject-a','alpha')")
        db.execute("INSERT INTO human_memory_evidence VALUES ('b1','subject-b','beta')")
        db.commit()

    service = HumanMemoryHostService(
        path,
        auth=_auth(),
        startup=_startup(StartupCompositionMode.HUMAN),
    )
    first = await service.raw_integrity_manifest()
    second = await service.raw_integrity_manifest()
    assert first == second
    assert first["raw_sets"]["scope.identities"]["row_count"] == 1
    assert first["raw_sets"]["scope.events"]["row_count"] == 1
    assert first["raw_sets"]["program.evidence"]["row_count"] == 1


def test_value_adapter_treats_fixture_subject_as_assertion_not_dto_field(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / ".local-test-evidence" / "s4"
    evidence.mkdir(parents=True)
    adapter = S4ValuePublicAdapter(
        fixture={"fixture_id": "fixture-1", "subject": "subject-a"},
        artifact_dir=evidence,
    )

    class Service:
        async def raw_integrity_manifest(self):  # type: ignore[no-untyped-def]
            return {"raw_sets": {}}

    adapter._service = Service()  # type: ignore[assignment]
    assert adapter.invoke(
        "recovery.manifest", {"subject": "subject-a"}
    ) == {
        "ok": True,
        "status": 200,
        "payload": {"raw_sets": {}},
    }
    rejected = adapter.invoke(
        "recovery.manifest", {"subject": "subject-b"}
    )
    assert rejected["ok"] is False
    assert rejected["code"] == "human_memory_permission_denied"
