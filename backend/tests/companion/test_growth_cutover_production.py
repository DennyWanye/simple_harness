from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from deskpet.capabilities.contracts import (
    EMPTY_RECEIPT_SET_HASH,
    OwnerScopeKey,
    PlatformDetailToken,
    PlatformDetailTokenVector,
)
from deskpet.companion.authority import (
    GrowthAuthorityPhase,
    GrowthAuthorityRouter,
    GrowthIngressUnavailable,
)
from deskpet.companion.contracts import OwnerRef
from deskpet.companion.cutover import (
    DEFAULT_POST_MARKER_STEPS,
    DEFAULT_PRE_MARKER_STEPS,
    GrowthAuthorityCutoverCoordinator,
    GrowthCutoverPlan,
    GrowthCutoverStepAuthorization,
)
from deskpet.companion.cutover_production import (
    LegacySkillMigrationBlocked,
    ProductionGrowthCutoverStepExecutor,
    RetiredLegacyGrowthAuthority,
    import_legacy_growth_snapshot,
    read_capability_owner_cutover_snapshot,
    read_legacy_growth_snapshot,
)
from deskpet.companion.preferences import PreferencePolicy, PreferenceResolver
from deskpet.companion.store import CompanionStore


def _legacy_state_db(path) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            """CREATE TABLE pending_skill_candidates(
                 id INTEGER PRIMARY KEY,
                 name TEXT,
                 description TEXT,
                 trigger_pattern TEXT,
                 steps_json TEXT,
                 status TEXT,
                 created_at REAL
               )"""
        )
        db.execute(
            """INSERT INTO pending_skill_candidates
               VALUES(1,'weekly-report','legacy candidate','weekly report',
                      '["read","write"]','pending',1.0)"""
        )


def _authorization(step: str) -> GrowthCutoverStepAuthorization:
    return GrowthCutoverStepAuthorization(
        cutover_operation_id="cutover-1",
        plan_hash="plan-hash-1",
        step=step,
        expected_step_hash=f"hash:{step}",
        marker_committed=step in DEFAULT_POST_MARKER_STEPS,
        legacy_owner_profile_id="legacy_local_profile",
        legacy_owner_generation=1,
        source_owner_policy="legacy_local_only",
        old_binding_generation=4,
        new_binding_generation=5,
        old_owner_binding_set_stamp="owner-set-old",
        new_owner_binding_set_stamp="owner-set-new",
    )


def test_legacy_snapshot_is_read_only_and_imports_only_to_local_owner(
    tmp_path,
) -> None:
    state_db = tmp_path / "state.db"
    preference_json = tmp_path / "preference_memory.json"
    skills_root = tmp_path / "skills" / "user"
    preference_json.write_text(
        json.dumps(
            [{"kind": "intent", "label": "task", "text": "weekly report"}]
        ),
        encoding="utf-8",
    )
    _legacy_state_db(state_db)

    snapshot = read_legacy_growth_snapshot(
        state_db_path=state_db,
        preference_path=preference_json,
        skill_roots=(skills_root,),
    )
    with sqlite3.connect(state_db) as db:
        before = db.total_changes
        assert db.execute(
            "SELECT status FROM pending_skill_candidates WHERE id=1"
        ).fetchone()[0] == "pending"
        assert db.total_changes == before

    store = CompanionStore(tmp_path / "companion.db")
    owner = store.create_profile(
        profile_id="legacy_local_profile",
        generation=1,
        identity_namespace_hash="local",
    )
    resolver = PreferenceResolver(store, policy=PreferencePolicy())
    result = import_legacy_growth_snapshot(
        store=store,
        preference_resolver=resolver,
        owner=owner,
        snapshot=snapshot,
    )
    replay = import_legacy_growth_snapshot(
        store=store,
        preference_resolver=resolver,
        owner=owner,
        snapshot=snapshot,
    )

    assert result["source_owner_policy"] == "legacy_local_only"
    assert result["activation_allowed"] is False
    assert result["legacy_skill_strategy"] == "no_legacy_skill_mutation"
    assert result["legacy_skill_file_count"] == 0
    assert result["imported_preferences"] == 1
    assert replay["imported_preferences"] == 0
    with store.read() as db:
        events = [
            dict(row)
            for row in db.execute(
                """SELECT * FROM growth_events
                   WHERE profile_id=? AND profile_generation=?
                   ORDER BY event_id""",
                (owner.profile_id, owner.profile_generation),
            ).fetchall()
        ]
    assert {
        item["reason_code"] for item in events
    } >= {"legacy_preference_imported", "legacy_needs_evidence"}
    assert all(
        item["profile_id"] == "legacy_local_profile"
        and int(item["profile_generation"]) == 1
        for item in events
    )
    with pytest.raises(ValueError, match="legacy_local_profile"):
        import_legacy_growth_snapshot(
            store=store,
            preference_resolver=resolver,
            owner=OwnerRef("relay-a", 1),
            snapshot=snapshot,
        )


def test_nonempty_legacy_skill_blocks_before_any_import_or_marker_proof(
    tmp_path,
) -> None:
    state_db = tmp_path / "state.db"
    preference_json = tmp_path / "preference_memory.json"
    skills = tmp_path / "skills" / "user" / "weekly-report"
    skills.mkdir(parents=True)
    skill_file = skills / "SKILL.md"
    skill_file.write_text("# weekly report", encoding="utf-8")
    preference_json.write_text(
        json.dumps(
            [{"kind": "intent", "label": "task", "text": "weekly report"}]
        ),
        encoding="utf-8",
    )
    _legacy_state_db(state_db)
    snapshot = read_legacy_growth_snapshot(
        state_db_path=state_db,
        preference_path=preference_json,
        skill_roots=(skills.parent,),
    )
    assert len(snapshot.skill_files) == 1
    expected_hash = snapshot.skill_files[0]["content_hash"]

    store = CompanionStore(tmp_path / "companion.db")
    owner = store.create_profile(
        profile_id="legacy_local_profile",
        generation=1,
        identity_namespace_hash="local",
    )
    resolver = PreferenceResolver(store, policy=PreferencePolicy())

    with pytest.raises(
        LegacySkillMigrationBlocked,
        match="legacy_skill_immutable_migration_unavailable",
    ) as blocked:
        import_legacy_growth_snapshot(
            store=store,
            preference_resolver=resolver,
            owner=owner,
            snapshot=snapshot,
        )

    assert blocked.value.file_count == 1
    assert len(blocked.value.inventory_hash) == 64
    assert skill_file.read_text(encoding="utf-8") == "# weekly report"
    assert snapshot.skill_files[0]["content_hash"] == expected_hash
    with store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM growth_events").fetchone()[0] == 0
    with sqlite3.connect(state_db) as db:
        assert db.execute(
            "SELECT status FROM pending_skill_candidates WHERE id=1"
        ).fetchone()[0] == "pending"


@pytest.mark.asyncio
async def test_nonempty_legacy_skill_aborts_cutover_before_irreversible_marker(
    tmp_path,
) -> None:
    skills = tmp_path / "skills" / "user" / "weekly-report"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("# weekly report", encoding="utf-8")
    snapshot = read_legacy_growth_snapshot(
        state_db_path=tmp_path / "state.db",
        preference_path=tmp_path / "preference_memory.json",
        skill_roots=(skills.parent,),
    )
    store = CompanionStore(tmp_path / "companion.db")
    owner = store.create_profile(
        profile_id="legacy_local_profile",
        generation=1,
        identity_namespace_hash="local",
    )
    resolver = PreferenceResolver(store, policy=PreferencePolicy())

    class _Authority:
        async def read(self, _request):
            return "read"

        async def write(self, _request):
            return "write"

    router = GrowthAuthorityRouter(
        store=store,
        legacy=_Authority(),
        companion=_Authority(),
    )
    await router.start()
    steps = DEFAULT_PRE_MARKER_STEPS + DEFAULT_POST_MARKER_STEPS
    plan = GrowthCutoverPlan(
        cutover_operation_id="legacy-skill-block",
        migration_version=1,
        migration_hash=snapshot.migration_hash,
        legacy_owner_profile_id=owner.profile_id,
        legacy_owner_generation=owner.profile_generation,
        old_binding_generation=17,
        new_binding_generation=17,
        old_owner_binding_set_stamp="a" * 64,
        new_owner_binding_set_stamp="a" * 64,
        step_hashes={step: f"hash:{step}" for step in steps},
    )

    def legacy_import(_authorization):
        return import_legacy_growth_snapshot(
            store=store,
            preference_resolver=resolver,
            owner=owner,
            snapshot=snapshot,
        )

    coordinator = GrowthAuthorityCutoverCoordinator(
        router=router,
        store=store,
        executor=ProductionGrowthCutoverStepExecutor(
            {"legacy_import": legacy_import}
        ),
    )
    with pytest.raises(LegacySkillMigrationBlocked):
        await coordinator.execute(plan)

    assert router.current.phase is GrowthAuthorityPhase.LEGACY
    assert router.current.roll_forward_required is False
    rows = store.list_growth_authority_cutover_journal(
        cutover_operation_id=plan.cutover_operation_id
    )
    assert any(
        row["journal_payload"]["reason"]
        == "growth_cutover_pre_marker_aborted"
        for row in rows
    )
    assert all(row["substep_phase"] != "roll_forward_marker" for row in rows)
    with store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM growth_events").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_capability_cutover_snapshot_reads_real_generation_and_owner_stamp(
) -> None:
    owner_key = "companion:legacy_local_profile:1"
    scope_key = "legacy_local_profile"
    key = OwnerScopeKey(owner_key, "user", scope_key)
    stamp = "a" * 64

    class _CapabilityStore:
        def __init__(self) -> None:
            self.requested = ()

        async def state(self):
            return SimpleNamespace(binding_generation=17)

        async def read_detail_token_vector(self, keys):
            self.requested = tuple(keys)
            return PlatformDetailTokenVector(
                (
                    PlatformDetailToken(
                        key=key,
                        exists=True,
                        version=3,
                        owner_catalog_generation=4,
                        committed_owner_binding_set_stamp=stamp,
                        manager_receipt_set_hash=EMPTY_RECEIPT_SET_HASH,
                    ),
                )
            )

    store = _CapabilityStore()
    snapshot = await read_capability_owner_cutover_snapshot(
        capability_store=store,
        owner_key=owner_key,
        scope_key=scope_key,
    )

    assert snapshot.binding_generation == 17
    assert snapshot.owner_binding_set_stamp == stamp
    assert snapshot.owner_key == owner_key
    assert snapshot.scope_key == scope_key
    assert store.requested == (key,)


@pytest.mark.parametrize(
    "step",
    DEFAULT_PRE_MARKER_STEPS + DEFAULT_POST_MARKER_STEPS,
)
@pytest.mark.asyncio
async def test_production_callback_reconciles_applied_effect_when_receipt_is_lost(
    step,
) -> None:
    ordered_steps = DEFAULT_PRE_MARKER_STEPS + DEFAULT_POST_MARKER_STEPS
    switch_index = ordered_steps.index("handler_switch")
    initial_phase = (
        "legacy"
        if ordered_steps.index(step) <= switch_index
        else "companion"
    )
    durable = {
        "applied": False,
        "physical_count": 0,
        "writer_phase": initial_phase,
    }
    writer_observations = []

    def callback(authorization):
        writer_observations.append(
            (
                durable["writer_phase"] == "legacy",
                durable["writer_phase"] == "companion",
            )
        )
        if not durable["applied"]:
            durable["physical_count"] += 1
            durable["applied"] = True
            if authorization.step == "handler_switch":
                durable["writer_phase"] = "companion"
        writer_observations.append(
            (
                durable["writer_phase"] == "legacy",
                durable["writer_phase"] == "companion",
            )
        )
        return {
            "step": authorization.step,
            "effect_applied": True,
            "writer_phase": durable["writer_phase"],
        }

    authorization = _authorization(step)
    first = await ProductionGrowthCutoverStepExecutor(
        {step: callback}
    ).execute(authorization)

    # Simulate process loss after the durable callback but before the
    # coordinator can persist ``first`` into its authority journal.
    recovered = await ProductionGrowthCutoverStepExecutor(
        {step: callback}
    ).execute(authorization)

    assert first == recovered
    assert first["receipt_hash"] == authorization.expected_step_hash
    assert first["result_hash"]
    assert durable["physical_count"] == 1
    assert set(writer_observations).issubset(
        {(True, False), (False, True)}
    )
    if step == "handler_switch":
        assert writer_observations == [
            (True, False),
            (False, True),
            (False, True),
            (False, True),
        ]
    else:
        assert len(set(writer_observations)) == 1


@pytest.mark.parametrize("unproved", [True, "ok", ["ok"], {"": ""}, {}])
@pytest.mark.asyncio
async def test_production_executor_rejects_truthy_or_empty_unstructured_proof(
    unproved,
) -> None:
    def callback(_authorization):
        return unproved

    executor = ProductionGrowthCutoverStepExecutor(
        {"legacy_import": callback}
    )
    with pytest.raises(RuntimeError, match="step_not_proved"):
        await executor.execute(_authorization("legacy_import"))


@pytest.mark.asyncio
async def test_final_binary_cannot_revive_legacy_growth_callbacks() -> None:
    retired = RetiredLegacyGrowthAuthority()
    with pytest.raises(GrowthIngressUnavailable, match="retired"):
        await retired.read(object())
    with pytest.raises(GrowthIngressUnavailable, match="retired"):
        await retired.write(object())
