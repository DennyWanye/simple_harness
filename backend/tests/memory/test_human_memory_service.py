from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import fields
from pathlib import Path

import pytest

from deskpet.memory.human_memory_service import (
    AppendBindingRequest,
    AppendDeterministicEventsRequest,
    AppendPrimaryEventRequest,
    AuditRefsRequest,
    AuthenticatedHostSnapshot,
    ControlRunRequest,
    CreateTaskScopeRequest,
    HumanMemoryHostService,
    HumanMemoryHostServiceError,
    MutateTaskScopeRequest,
    OpenTaskScopeRequest,
    ReadTaskScopeViewRequest,
    SaveCheckpointRequest,
    SearchTaskScopesRequest,
)
from deskpet.memory.schema import (
    StartupCompositionMode,
    StartupEpoch,
    StartupEpochDecision,
    dispatch_startup_epoch,
    inspect_startup_epoch,
)
from deskpet.memory.s4_value_adapter import S4ValuePublicAdapter
from deskpet.task_scope.store import CanonicalTaskScopeStore


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
        AppendPrimaryEventRequest,
        MutateTaskScopeRequest,
        AppendBindingRequest,
        ControlRunRequest,
        AuditRefsRequest,
        SearchTaskScopesRequest,
        OpenTaskScopeRequest,
        ReadTaskScopeViewRequest,
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


@pytest.mark.asyncio
async def test_create_goal_is_canonical_and_survives_cold_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(path, approved_fresh_lane=True)
    service = HumanMemoryHostService(path, auth=_auth(), startup=startup)
    created = await service.create_task_scope(
        CreateTaskScopeRequest("alpha", "Alpha", "retain canonical goal", "create-a")
    )
    await service.rebuild_derived(str(created["scope_ref"]))

    restarted = HumanMemoryHostService(
        path,
        auth=_auth(),
        startup=inspect_startup_epoch(path, approved_fresh_lane=False),
    )
    search = await restarted.search_task_scopes(
        SearchTaskScopesRequest("canonical goal")
    )
    assert search["candidates"][0]["goal"] == "retain canonical goal"
    opened = await restarted.open_task_scope(
        OpenTaskScopeRequest(str(created["scope_ref"]))
    )
    assert "retain canonical goal" in str(opened["resume_package"])

    with pytest.raises(Exception, match="task_scope_identity_conflict"):
        await restarted.create_task_scope(
            CreateTaskScopeRequest("alpha", "Alpha", "different goal", "create-a")
        )


@pytest.mark.asyncio
async def test_public_evidence_pages_roundtrip_complete_oversized_unicode_event(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    startup = await dispatch_startup_epoch(path, approved_fresh_lane=True)
    service = HumanMemoryHostService(path, auth=_auth(), startup=startup)
    created = await service.create_task_scope(
        CreateTaskScopeRequest("roundtrip", "Roundtrip", "retain every field", "create-r")
    )
    scope_ref = str(created["scope_ref"])
    mutation = await service.mutate_task_scope(
        MutateTaskScopeRequest(scope_ref, "status", "active", "mutation-r")
    )
    assert await service.mutate_task_scope(
        MutateTaskScopeRequest(scope_ref, "status", "active", "mutation-r")
    ) == mutation
    oversized = "记忆🧠" * 12_000
    await CanonicalTaskScopeStore(path).append_host_event(
        task_scope_id=scope_ref,
        event_kind="host.turn",
        source_event_id="ordinary-indexed",
        payload={"event_index": 3, "text": "ordinary"},
    )
    await CanonicalTaskScopeStore(path).append_host_event(
        task_scope_id=scope_ref,
        event_kind="host.turn",
        source_event_id="oversized-unicode",
        payload={"event_index": 7, "text": oversized, "nested": {"ordinal": 7}},
    )
    await service.rebuild_derived(scope_ref)
    view = await service.read_view(ReadTaskScopeViewRequest(scope_ref, "EVIDENCE"))

    recovered: list[dict[str, object]] = []
    chunks: dict[str, list[tuple[int, str]]] = {}
    for page in view["pages"]:
        assert len(str(page["content"]).encode("utf-8")) <= 32 * 1024
        recovered.extend(page.get("events", []))
        chunk = page.get("event_chunk")
        if chunk:
            chunks.setdefault(str(chunk["event_content_sha256"]), []).append(
                (int(chunk["ordinal"]), str(chunk["content"]))
            )
    for parts in chunks.values():
        raw = b"".join(
            base64.b64decode(content)
            for _, content in sorted(parts)
        )
        recovered.append(json.loads(raw))

    oversized_event = next(
        event for event in recovered if event["source_event_id"] == "oversized-unicode"
    )
    assert oversized_event["payload"] == {
        "event_index": 7,
        "text": oversized,
        "nested": {"ordinal": 7},
    }
    assert oversized_event["event_index"] == 7
    assert oversized_event["event_kind"] == "host.turn"
    assert oversized_event["source_kind"] == "host"
    mutation_event = next(
        event for event in recovered if event["event_kind"] == "mutation.plan"
    )
    assert mutation_event["evidence_links"]
    assert "payload" in mutation_event
    assert "steps" in mutation_event
    ordinary = next(
        event for event in recovered if event["source_event_id"] == "ordinary-indexed"
    )
    assert ordinary["event_index"] == 3
    assert ordinary["payload"] == {"event_index": 3, "text": "ordinary"}
