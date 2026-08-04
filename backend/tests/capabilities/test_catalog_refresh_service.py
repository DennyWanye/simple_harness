from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from deskpet.capabilities.contracts import (
    CapabilityScope,
    CapabilityVersionDescriptor,
    canonical_json,
    fingerprint_json,
)
from deskpet.capabilities.hub import CapabilityHub, ToolRegistryCatalogSource
from deskpet.capabilities.refresh import (
    CapabilityContinuationCommitEvidence,
    CapabilityRefreshService,
    CapabilityRefreshStageEvidence,
    CapabilityRefreshStagingService,
    RegistryExposureToolSetRebuilder,
    SqliteCapabilityRefreshSnapshotRepository,
    context_os_snapshot_ref,
    exposure_intent_payload,
    exposure_intent_ref,
)
from deskpet.capabilities.refresh_contracts import (
    CapabilityOperationReceipt,
    CapabilityRefreshIntent,
)
from deskpet.capabilities.store import (
    CAPABILITY_OPERATION_PHASES,
    CapabilityStore,
    CapabilityStoreConflict,
    CapabilityVersionRecord,
    initialize_capability_database,
)
from deskpet.tools.capabilities import (
    ToolCapabilityResolver,
    ToolEligibilityContext,
    ToolExposureIntent,
)
from deskpet.tools.prepared_snapshot import load_context_os_snapshot
from deskpet.tools.registry import (
    PreparedToolCallStale,
    ToolRegistry,
    tool_spec_fingerprint,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _schema(version: str) -> dict:
    return {
        "name": "godot__check",
        "description": f"Godot {version}",
        "parameters": {"type": "object", "properties": {}},
    }


def _register(registry: ToolRegistry, version: str) -> None:
    registry.register(
        "godot__check",
        "capability:godot",
        _schema(version),
        lambda _args, _task: json.dumps({"ok": True, "version": version}),
        source=f"capability:godot:{version}:{_hash(f'manifest:{version}')}",
        spec_version=f"capability-pack:{version}",
        runtime_provenance_ref=_hash(f"runtime:{version}"),
    )


def _record(tmp_path, registry: ToolRegistry, version: str):
    spec = registry.catalog_snapshot().specs[0]
    descriptor = CapabilityVersionDescriptor(
        capability_id="godot",
        display_name=f"Godot {version}",
        version=version,
        kind="function_tool",
        source=f"fixture:godot@{version}",
        description="Godot helper",
        aliases=("game",),
        logical_tool_ids=("check",),
        provider_tool_names=("godot__check",),
        permission_categories=("filesystem_read",),
        effect_kinds=("read_only",),
        schema_hash=spec.schema_hash,
        manifest_hash=_hash(f"manifest:{version}"),
        health="healthy",
    )
    return CapabilityVersionRecord(
        descriptor=descriptor,
        install_path=tmp_path / "packs" / "godot" / version,
        validation_status="healthy",
        expected_tool_fingerprints=(tool_spec_fingerprint(spec),),
        parent_version=None,
        parent_manifest_hash=None,
        derived_from_receipt_ref=None,
        created_at=100.0,
    )


class _Snapshots:
    def __init__(self) -> None:
        self.contexts: dict[str, dict] = {}
        self.exposures: dict[str, ToolExposureIntent] = {}

    async def load_context_os(self, snapshot_ref: str):
        return load_context_os_snapshot(self.contexts[snapshot_ref])

    async def put_context_os(self, snapshot_ref: str, payload):
        assert fingerprint_json(dict(payload)) == snapshot_ref
        self.contexts.setdefault(snapshot_ref, dict(payload))
        return snapshot_ref

    async def load_exposure_intent(self, intent_ref: str):
        return self.exposures[intent_ref]

    def put_initial(self, prepared, eligibility, exposure):
        from deskpet.tools.prepared_snapshot import dump_context_os_snapshot

        context_ref = context_os_snapshot_ref(prepared, eligibility)
        self.contexts[context_ref] = dump_context_os_snapshot(
            prepared, eligibility
        )
        intent_ref = exposure_intent_ref(exposure)
        assert fingerprint_json(exposure_intent_payload(exposure)) == intent_ref
        self.exposures[intent_ref] = exposure
        return context_ref, intent_ref


class _Crash(BaseException):
    pass


async def _fixture(
    tmp_path,
    *,
    source_kind: str = "tool_effect",
    stage_refresh: bool = True,
):
    database = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(database, clock=lambda: 100.0)
    registry = ToolRegistry()
    _register(registry, "1.0.0")
    v1 = _record(tmp_path, registry, "1.0.0")
    await store.record_version(v1)
    binding = await store.set_binding(
        scope="user",
        scope_key="default",
        pack_id="godot",
        version="1.0.0",
        manifest_hash=v1.descriptor.manifest_hash,
        expected_generation=0,
    )
    hub = CapabilityHub(
        store=store,
        registry_source=ToolRegistryCatalogSource(registry),
    )
    scope = CapabilityScope(user_key="default")
    old_catalog = await hub.snapshot_and_acquire_lease(
        run_id="run-1",
        root_run_id="root-1",
        scope=scope,
    )
    exposure = ToolExposureIntent(direct_selectors=("*",))
    eligibility = ToolEligibilityContext(
        "session-1", "request-1", "chat"
    )
    old_tool_set = ToolCapabilityResolver(registry).resolve_draft(
        exposure, eligibility=eligibility
    ).finalize(scope_id="scope-1")
    snapshots = _Snapshots()
    old_tool_ref, exposure_ref = snapshots.put_initial(
        old_tool_set, eligibility, exposure
    )
    old_call = registry.prepare_call(
        "godot__check",
        {},
        "session-1",
        "call-old",
        catalog_snapshot_ref=old_catalog.snapshot_ref,
    )

    await store.create_operation(
        operation_id="operation-update",
        idempotency_key="operation-update",
        kind="update",
        request={"source": "fixture:v2"},
        root_run_id="root-1",
        pack_id="godot",
        requested_scope="user",
        requested_scope_key="default",
    )
    before = registry.catalog_snapshot()
    candidate = ToolRegistry()
    _register(candidate, "2.0.0")
    v2 = _record(tmp_path, candidate, "2.0.0")
    await store.record_version(v2)
    new_spec = candidate.catalog_snapshot().specs[0]
    registry.compare_and_swap_catalog(
        expected_revision=before.revision,
        expected_fingerprints={
            "godot__check": tool_spec_fingerprint(before.specs[0])
        },
        replacements=(new_spec,),
    )
    successor = await store.set_binding(
        scope="user",
        scope_key="default",
        pack_id="godot",
        version="2.0.0",
        manifest_hash=v2.descriptor.manifest_hash,
        expected_generation=binding.generation,
    )
    for phase in CAPABILITY_OPERATION_PHASES:
        await store.commit_phase("operation-update", phase)
    receipt = CapabilityOperationReceipt(
        operation_id="operation-update",
        root_run_id="root-1",
        parent_command_id="command-update",
        parent_effect_id="effect-update",
        action="update",
        refresh_nonce="refresh-nonce",
        old_stamp=old_catalog.stamp,
        published_binding_generation=successor.generation,
        affected_capability_ids=("godot",),
        affected_version_refs=(
            f"godot@2.0.0:{v2.descriptor.manifest_hash}",
        ),
        affected_tool_spec_fingerprints=(
            tool_spec_fingerprint(new_spec),
        ),
        manifest_hashes=(v2.descriptor.manifest_hash,),
    )
    await store.put_operation_receipt(receipt)
    intent = CapabilityRefreshIntent(
        intent_id="refresh-intent",
        root_run_id="root-1",
        run_id="run-1",
        operation_id="operation-update",
        source_kind=source_kind,  # type: ignore[arg-type]
        source_command_id="command-update",
        source_effect_id="effect-update",
        refresh_nonce=receipt.refresh_nonce,
        expected_continuation_version=5,
        old_stamp=old_catalog.stamp,
        old_catalog_snapshot_ref=old_catalog.snapshot_ref,
        old_tool_set_snapshot_ref=old_tool_ref,
        exposure_intent_ref=exposure_ref,
    )
    async with store.write_transaction() as db:
        await db.execute(
            """CREATE TABLE fake_continuation(
                run_id TEXT PRIMARY KEY,root_run_id TEXT NOT NULL,
                version INTEGER NOT NULL,refresh_pending TEXT,
                catalog_snapshot_ref TEXT,tool_set_snapshot_ref TEXT,
                stamp_fingerprint TEXT,context_os_json TEXT,
                driver_runtime TEXT,outer_effect_state TEXT,
                provider_outcome_staged INTEGER NOT NULL,
                child_terminal_acked INTEGER NOT NULL,
                provider_backfill_blocked INTEGER NOT NULL
            )"""
        )
        await db.execute(
            """INSERT INTO fake_continuation(
                run_id,root_run_id,version,refresh_pending,
                catalog_snapshot_ref,tool_set_snapshot_ref,stamp_fingerprint,
                context_os_json,driver_runtime,outer_effect_state,
                provider_outcome_staged,child_terminal_acked,
                provider_backfill_blocked
            ) VALUES('run-1','root-1',4,NULL,?,?,?,?,?,'claimed',0,0,0)""",
            (
                old_catalog.snapshot_ref,
                old_tool_ref,
                old_catalog.stamp.fingerprint,
                canonical_json(snapshots.contexts[old_tool_ref]),
                "live-driver",
            ),
        )

    async def stage_source(db, staged_intent):
        tool_source = staged_intent.source_kind == "tool_effect"
        cursor = await db.execute(
            """UPDATE fake_continuation SET
                version=version+1,refresh_pending=?,outer_effect_state=?,
                provider_outcome_staged=?,child_terminal_acked=?,
                provider_backfill_blocked=1
               WHERE run_id=? AND root_run_id=? AND version=?
                 AND refresh_pending IS NULL""",
            (
                staged_intent.intent_id,
                "settled" if tool_source else "deferred_pending",
                int(tool_source),
                int(not tool_source),
                staged_intent.run_id,
                staged_intent.root_run_id,
                staged_intent.expected_continuation_version - 1,
            ),
        )
        assert cursor.rowcount == 1
        return CapabilityRefreshStageEvidence(
            root_run_id=staged_intent.root_run_id,
            run_id=staged_intent.run_id,
            source_kind=staged_intent.source_kind,
            source_command_id=staged_intent.source_command_id,
            source_effect_id=staged_intent.source_effect_id,
            previous_version=staged_intent.expected_continuation_version - 1,
            new_version=staged_intent.expected_continuation_version,
            refresh_pending_intent_id=staged_intent.intent_id,
            outer_effect_state=(
                "settled" if tool_source else "deferred_pending"
            ),
            provider_outcome_staged=tool_source,
            child_terminal_acked=not tool_source,
            provider_backfill_blocked=True,
        )

    staging = CapabilityRefreshStagingService(store)
    if stage_refresh:
        if source_kind == "tool_effect":
            await staging.settle_control_effect_and_stage_refresh(
                intent, stage_source
            )
        else:
            await staging.ack_child_terminal_and_stage_refresh(
                intent, stage_source
            )

    service = CapabilityRefreshService(
        store=store,
        hub=hub,
        snapshots=snapshots,
        rebuilder=RegistryExposureToolSetRebuilder(
            ToolCapabilityResolver(registry)
        ),
    )
    return {
        "store": store,
        "registry": registry,
        "hub": hub,
        "scope": scope,
        "service": service,
        "intent": intent,
        "staging": staging,
        "stage_source": stage_source,
        "old_call": old_call,
        "v1": v1,
        "v2": v2,
    }


async def _commit_continuation(db, prepared):
    commit = prepared.commit
    cursor = await db.execute(
        """UPDATE fake_continuation SET
            version=version+1,refresh_pending=NULL,
            catalog_snapshot_ref=?,tool_set_snapshot_ref=?,
            stamp_fingerprint=?,context_os_json=?,driver_runtime=NULL
           WHERE run_id=? AND root_run_id=? AND version=?
             AND refresh_pending=?""",
        (
            commit.new_catalog_snapshot_ref,
            commit.new_tool_set_snapshot_ref,
            commit.new_stamp.fingerprint,
            canonical_json(commit.to_dict()["new_context_os"]),
            commit.run_id,
            commit.root_run_id,
            commit.expected_continuation_version,
            commit.intent_id,
        ),
    )
    if cursor.rowcount != 1:
        raise CapabilityStoreConflict(
            "continuation_version_conflict", "fake continuation changed"
        )
    return CapabilityContinuationCommitEvidence(
        root_run_id=commit.root_run_id,
        run_id=commit.run_id,
        previous_version=commit.expected_continuation_version,
        new_version=commit.expected_continuation_version + 1,
        refresh_pending_cleared=True,
        new_catalog_snapshot_ref=commit.new_catalog_snapshot_ref,
        new_tool_set_snapshot_ref=commit.new_tool_set_snapshot_ref,
        new_stamp_fingerprint=commit.new_stamp.fingerprint,
        context_os_fingerprint=fingerprint_json(
            commit.to_dict()["new_context_os"]
        ),
        driver_runtime_cleared=True,
    )


@pytest.mark.asyncio
async def test_same_run_refresh_swaps_snapshots_in_one_transaction(
    tmp_path,
) -> None:
    fixture = await _fixture(tmp_path)
    prepared = await fixture["service"].refresh(
        fixture["intent"].intent_id,
        scope=fixture["scope"],
        commit_continuation=_commit_continuation,
    )
    assert prepared.restart_required
    assert prepared.commit.root_run_id == "root-1"
    assert prepared.commit.run_id == "run-1"
    assert prepared.prepared_tool_set.scope_id == "scope-1"
    assert prepared.prepared_tool_set.revision == 2
    assert prepared.prepared_tool_set.has_direct("godot__check")
    assert prepared.catalog_snapshot.get("godot").version.version == "2.0.0"
    with pytest.raises(TypeError):
        prepared.commit.new_context_os["tool_set"]["revision"] = 99

    intent = await fixture["store"].get_refresh_intent("refresh-intent")
    assert intent is not None and intent.status == "committed"
    assert intent.commit_hash == prepared.commit.fingerprint
    async with fixture["store"].read_connection() as db:
        row = await (
            await db.execute(
                "SELECT * FROM fake_continuation WHERE run_id='run-1'"
            )
        ).fetchone()
    assert int(row["version"]) == 6
    assert row["refresh_pending"] is None
    assert row["driver_runtime"] is None
    assert str(row["catalog_snapshot_ref"]) == prepared.commit.new_catalog_snapshot_ref
    assert not await fixture["store"].version_has_active_lease(
        "godot",
        "1.0.0",
        fixture["v1"].descriptor.manifest_hash,
    )
    assert await fixture["store"].version_has_active_lease(
        "godot",
        "2.0.0",
        fixture["v2"].descriptor.manifest_hash,
    )
    with pytest.raises(PreparedToolCallStale):
        fixture["registry"].resolve_prepared_spec(fixture["old_call"])

    async with fixture["store"].write_transaction() as db:
        committed = await fixture["store"].bind(db).commit_refresh_intent(
            prepared.commit
        )
        assert committed.commit_hash == prepared.commit.fingerprint
    conflicting = replace(
        prepared.commit,
        new_tool_set_snapshot_ref=_hash("different-tool-set"),
    )
    with pytest.raises(CapabilityStoreConflict) as caught:
        async with fixture["store"].write_transaction() as db:
            await fixture["store"].bind(db).commit_refresh_intent(conflicting)
    assert caught.value.code == "refresh_commit_conflict"


@pytest.mark.asyncio
async def test_sqlite_refresh_snapshots_roundtrip_and_reject_forged_refs(
    tmp_path,
) -> None:
    database = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(database)
    snapshots = SqliteCapabilityRefreshSnapshotRepository(store)
    registry = ToolRegistry()
    _register(registry, "1.0.0")
    eligibility = ToolEligibilityContext(
        "session-1", "request-1", "chat"
    )
    exposure = ToolExposureIntent(direct_selectors=("*",))
    prepared = ToolCapabilityResolver(registry).resolve_draft(
        exposure, eligibility=eligibility
    ).finalize(scope_id="scope-1")
    context_ref = context_os_snapshot_ref(prepared, eligibility)
    intent_ref = exposure_intent_ref(exposure)

    from deskpet.tools.prepared_snapshot import dump_context_os_snapshot

    context_payload = dump_context_os_snapshot(prepared, eligibility)
    assert (
        await snapshots.put_context_os(context_ref, context_payload)
        == context_ref
    )
    assert (
        await snapshots.put_exposure_intent(intent_ref, exposure)
        == intent_ref
    )
    restored, restored_eligibility = await snapshots.load_context_os(
        context_ref
    )
    assert restored == prepared
    assert restored_eligibility == eligibility
    assert await snapshots.load_exposure_intent(intent_ref) == exposure

    with pytest.raises(Exception) as caught:
        await snapshots.put_context_os(_hash("forged"), context_payload)
    assert getattr(caught.value, "code", "") == "refresh_snapshot_ref_mismatch"


@pytest.mark.asyncio
async def test_refresh_crash_rolls_back_continuation_lease_and_nonce(
    tmp_path,
) -> None:
    fixture = await _fixture(tmp_path)

    async def crash_after_continuation(db, prepared):
        await _commit_continuation(db, prepared)
        raise _Crash()

    with pytest.raises(_Crash):
        await fixture["service"].refresh(
            fixture["intent"].intent_id,
            scope=fixture["scope"],
            commit_continuation=crash_after_continuation,
        )
    intent = await fixture["store"].get_refresh_intent("refresh-intent")
    assert intent is not None and intent.status == "pending"
    async with fixture["store"].read_connection() as db:
        row = await (
            await db.execute(
                "SELECT * FROM fake_continuation WHERE run_id='run-1'"
            )
        ).fetchone()
    assert int(row["version"]) == 5
    assert str(row["refresh_pending"]) == "refresh-intent"
    assert not await fixture["store"].version_has_active_lease(
        "godot",
        "2.0.0",
        fixture["v2"].descriptor.manifest_hash,
    )
    assert await fixture["store"].version_has_active_lease(
        "godot",
        "1.0.0",
        fixture["v1"].descriptor.manifest_hash,
    )

    recovered = await fixture["service"].refresh(
        fixture["intent"].intent_id,
        scope=fixture["scope"],
        commit_continuation=_commit_continuation,
    )
    assert recovered.commit.run_id == "run-1"
    assert (await fixture["store"].get_refresh_intent("refresh-intent")).status == (
        "committed"
    )


@pytest.mark.asyncio
async def test_brokered_reference_can_keep_old_snapshot_after_refresh(
    tmp_path,
) -> None:
    fixture = await _fixture(tmp_path)
    prepared = await fixture["service"].refresh(
        fixture["intent"].intent_id,
        scope=fixture["scope"],
        commit_continuation=_commit_continuation,
        release_old_snapshot=False,
    )
    assert await fixture["store"].version_has_active_lease(
        "godot",
        "1.0.0",
        fixture["v1"].descriptor.manifest_hash,
    )
    assert fixture["registry"].resolve_prepared_spec(fixture["old_call"])
    assert await fixture["hub"].release_lease(
        prepared.commit.old_catalog_snapshot_ref, "run-1"
    ) == 1
    with pytest.raises(PreparedToolCallStale):
        fixture["registry"].resolve_prepared_spec(fixture["old_call"])


@pytest.mark.asyncio
async def test_staging_rejects_receipt_nonce_or_parent_identity_drift(
    tmp_path,
) -> None:
    fixture = await _fixture(tmp_path)
    original = fixture["intent"]
    bad = replace(
        original,
        intent_id="different-intent",
        refresh_nonce="forged-nonce",
    )
    with pytest.raises(CapabilityStoreConflict) as caught:
        async with fixture["store"].write_transaction() as db:
            await fixture["store"].bind(db).stage_refresh_intent(bad)
    assert caught.value.code == "refresh_receipt_mismatch"


@pytest.mark.asyncio
async def test_child_terminal_staging_keeps_outer_effect_deferred(
    tmp_path,
) -> None:
    fixture = await _fixture(tmp_path, source_kind="child_terminal")
    async with fixture["store"].read_connection() as db:
        row = await (
            await db.execute(
                "SELECT * FROM fake_continuation WHERE run_id='run-1'"
            )
        ).fetchone()
    assert int(row["version"]) == 5
    assert str(row["refresh_pending"]) == "refresh-intent"
    assert str(row["outer_effect_state"]) == "deferred_pending"
    assert int(row["provider_outcome_staged"]) == 0
    assert int(row["child_terminal_acked"]) == 1
    assert int(row["provider_backfill_blocked"]) == 1


@pytest.mark.asyncio
async def test_source_staging_crash_rolls_back_marker_and_effect_settlement(
    tmp_path,
) -> None:
    fixture = await _fixture(tmp_path, stage_refresh=False)

    async def crash_after_source(db, intent):
        await fixture["stage_source"](db, intent)
        raise _Crash()

    with pytest.raises(_Crash):
        await fixture[
            "staging"
        ].settle_control_effect_and_stage_refresh(
            fixture["intent"], crash_after_source
        )
    assert await fixture["store"].get_refresh_intent("refresh-intent") is None
    async with fixture["store"].read_connection() as db:
        row = await (
            await db.execute(
                "SELECT * FROM fake_continuation WHERE run_id='run-1'"
            )
        ).fetchone()
    assert int(row["version"]) == 4
    assert row["refresh_pending"] is None
    assert str(row["outer_effect_state"]) == "claimed"

    staged = await fixture[
        "staging"
    ].settle_control_effect_and_stage_refresh(
        fixture["intent"], fixture["stage_source"]
    )
    assert staged.status == "pending"
