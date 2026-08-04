from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from deskpet.agent.turn_preparer import ProductTurnPreparer, TurnInput
from deskpet.capabilities.contracts import RunCatalogEntryIdentity
from deskpet.companion.contracts import OwnerRef, RunGrowthSnapshot
from deskpet.companion.identity_gate import (
    FrozenOwnerIdentity,
    IdentityReadyGate,
)
from deskpet.companion.preferences import PreferenceResolution
from deskpet.companion.turn_authority import (
    CompanionCatalogLeaseCaptureV1,
    CompanionTurnAuthority,
    CompanionTurnPreparationRequestV1,
    FrozenPersonalWorkflowCandidateV1,
    ModelPersonalWorkflowMatcher,
)
from deskpet.execution.contracts import fingerprint_json


OWNER = OwnerRef("profile-1", 1)
OWNER_KEY = "companion:profile-1:1"


def _graph() -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": "daily-three",
        "description": "Recall today's priorities",
        "entry_node": "input",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "bindings": {},
                "config": {},
            },
            {
                "id": "recall",
                "type": "tool_call",
                "bindings": {"query": "/input/objective"},
                "config": {"tool_name": "memory_recall"},
            },
            {
                "id": "output",
                "type": "output",
                "bindings": {"value": "/nodes/recall/result"},
                "config": {},
            },
        ],
        "outputs": {"value": "/nodes/output/value"},
        "max_steps": 3,
    }


class _Preferences:
    def __init__(self) -> None:
        self.freeze_calls = []

    def resolve(self, owner, *, request_id, overrides=(), relevant_keys=None):
        assert owner == OWNER
        return PreferenceResolution(
            owner=owner,
            request_id=request_id,
            items=(),
            snapshot_hash=fingerprint_json(
                {"owner": OWNER_KEY, "request_id": request_id}
            ),
            dependencies=(),
        )

    def freeze_run_snapshot(
        self,
        resolution,
        *,
        run_id,
        additional_dependencies=(),
        owner_memory_read_scopes=(),
    ):
        self.freeze_calls.append(
            (
                resolution,
                run_id,
                tuple(additional_dependencies),
                tuple(owner_memory_read_scopes),
            )
        )
        payload = {
            "request_id": resolution.request_id,
            "run_id": run_id,
            "dependencies": [
                [item.dependency_kind, item.dependency_id]
                for item in additional_dependencies
            ],
        }
        return RunGrowthSnapshot(
            snapshot_id="growth:" + fingerprint_json(payload),
            request_id=resolution.request_id,
            run_id=run_id,
            snapshot_generation=0,
            snapshot_hash=fingerprint_json(payload),
        )


class _CandidateSource:
    def __init__(self, candidate) -> None:
        self.candidate = candidate
        self.calls = 0

    async def frozen_candidates(self, request, identity):
        self.calls += 1
        assert identity.owner_key == OWNER_KEY
        assert request.turn.text == "show my daily priorities"
        return (self.candidate,)


class _Matcher:
    def __init__(self, candidate_id: str) -> None:
        self.candidate_id = candidate_id
        self.calls = 0

    async def select_candidate(self, *, query, candidates):
        self.calls += 1
        assert query == "show my daily priorities"
        assert tuple(item.candidate_id for item in candidates) == (
            self.candidate_id,
        )
        return self.candidate_id


def _candidate() -> FrozenPersonalWorkflowCandidateV1:
    graph = _graph()
    manifest_hash = fingerprint_json({"manifest": "daily-three"})
    graph_hash = fingerprint_json(graph)
    identity = {
        "owner_key": OWNER_KEY,
        "pack_id": "personal.daily-three",
        "version": "1.0.0",
        "manifest_hash": manifest_hash,
        "binding_generation": 4,
        "workflow_id": "daily-three",
        "graph_content_hash": graph_hash,
    }
    return FrozenPersonalWorkflowCandidateV1(
        candidate_id=(
            "personal-workflow-candidate:" + fingerprint_json(identity)
        ),
        owner_key=OWNER_KEY,
        pack_id="personal.daily-three",
        version="1.0.0",
        manifest_hash=manifest_hash,
        binding_generation=4,
        workflow_id="daily-three",
        graph=graph,
        graph_content_hash=graph_hash,
        display_name="Daily priorities",
        description="Recall today's priorities",
    )


@pytest.mark.asyncio
async def test_personal_workflow_matcher_uses_model_choice_without_text_rules() -> None:
    candidate = _candidate()

    class Provider:
        async def chat_stream(self, messages, **kwargs):
            assert kwargs == {"temperature": 0.0, "max_tokens": 128}
            assert "unrelated wording still chosen by model" in messages[1]["content"]
            yield '{"candidate_id":'
            yield f'"{candidate.candidate_id}"'
            yield "}"

    matcher = ModelPersonalWorkflowMatcher(lambda: Provider())
    assert (
        await matcher.select_candidate(
            query="unrelated wording still chosen by model",
            candidates=(candidate,),
        )
        == candidate.candidate_id
    )


@pytest.mark.asyncio
async def test_personal_workflow_matcher_fails_closed_on_unoffered_model_id() -> None:
    candidate = _candidate()

    class Provider:
        async def chat_stream(self, _messages, **_kwargs):
            yield '{"candidate_id":"invented"}'

    matcher = ModelPersonalWorkflowMatcher(lambda: Provider())
    assert (
        await matcher.select_candidate(query="anything", candidates=(candidate,))
        is None
    )


def _capture(candidate) -> CompanionCatalogLeaseCaptureV1:
    effect_policy = {
        "policy_id": "memory-recall-read",
        "version": "v1",
        "kind": "idempotent_read",
        "max_attempts": 2,
        "reusable_across_branches": True,
    }
    envelope = {
        "pack_id": candidate.pack_id,
        "version": candidate.version,
        "manifest_hash": candidate.manifest_hash,
        "selected_binding": {
            "binding_id": "binding-1",
            "capability_id": candidate.pack_id,
            "version": candidate.version,
            "manifest_hash": candidate.manifest_hash,
            "scope": "user",
            "scope_key": OWNER_KEY,
            "active": True,
            "generation": candidate.binding_generation,
            "owner_key": OWNER_KEY,
            "management_policy": "user_managed",
            "management_generation": 1,
        },
        "visible_bindings": [],
        "descriptor": {
            "capability_id": candidate.pack_id,
            "version": candidate.version,
            "manifest_hash": candidate.manifest_hash,
        },
        "tool_spec_fingerprints": [],
        "instruction_refs_hash": fingerprint_json([]),
        "workflow_refs_hash": fingerprint_json([]),
        "runtime_descriptor_hash": fingerprint_json([]),
    }
    catalog_entry = RunCatalogEntryIdentity(
        entry_kind="pack",
        descriptor_fingerprint=fingerprint_json(
            {"entry_kind": "pack", "envelope": envelope}
        ),
        canonical_envelope=envelope,
    )
    return CompanionCatalogLeaseCaptureV1(
        run_catalog_content_stamp="catalog-stamp-1",
        process_catalog_stamp="process-stamp-1",
        catalog_snapshot_ref="snapshot-1",
        capability_lease_intent_ref="lease-intent-1",
        exact_tools=(
            {
                "name": "memory_recall",
                "stable_handler_id": "core.memory_recall.v1",
                "tool_spec_fingerprint": fingerprint_json(
                    {"tool": "memory_recall"}
                ),
                "schema_hash": fingerprint_json({"schema": "memory_recall"}),
                "execution_build_identity": {
                    "source": "deskpet.tools.memory_recall",
                    "version": "v1",
                },
                "dispatch_adapter_id": "builtin.function",
                "dispatch_adapter_version": "v1",
                "dispatch_adapter_fingerprint": fingerprint_json(
                    {"adapter": "builtin.function", "version": "v1"}
                ),
                "effect_policy": effect_policy,
                "idempotency": "required",
            },
        ),
        lease_entries=(catalog_entry.to_dict(),),
    )


@pytest.mark.asyncio
async def test_finalize_uses_prepared_candidate_without_second_live_read() -> None:
    gate = IdentityReadyGate()
    gate.bind(FrozenOwnerIdentity(OWNER, OWNER_KEY, 7))
    preferences = _Preferences()
    candidate = _candidate()
    source = _CandidateSource(candidate)
    matcher = _Matcher(candidate.candidate_id)
    authority = CompanionTurnAuthority(
        identity_gate=gate,
        preference_resolver=preferences,
        personal_workflow_source=source,
        personal_workflow_matcher=matcher,
    )
    turn = TurnInput(
        "show my daily priorities",
        "session-1",
        request_id="request-1",
        turn_id="turn-1",
        root_run_id="run-1",
    )
    prepared = await authority.prepare_turn(
        CompanionTurnPreparationRequestV1(turn=turn, services={})
    )

    # A profile switch after phase 1 proves finalization does not re-freeze
    # identity or ask the source/matcher to select against live state again.
    gate.unbind(expected_binding_epoch=7)
    finalized = await authority.finalize_after_catalog(
        prepared, _capture(candidate), run_id="run-1"
    )

    assert source.calls == 1
    assert matcher.calls == 1
    assert len(preferences.freeze_calls) == 1
    selection = finalized.selection_payload[
        "personal_workflow_selection"
    ]
    assert selection["pack_id"] == candidate.pack_id
    assert selection["run_catalog_content_stamp"] == "catalog-stamp-1"
    assert selection["tool_bindings"]["memory_recall"]["tool_name"] == (
        "memory_recall"
    )
    dependency = next(
        item
        for item in finalized.growth_dependencies
        if item.dependency_kind == "personal_workflow"
    )
    assert dependency.binding_generation == candidate.binding_generation
    assert dependency.catalog_content_stamp == "catalog-stamp-1"


@pytest.mark.asyncio
async def test_skill_candidate_to_catalog_capture_exact_change_fails_closed() -> None:
    gate = IdentityReadyGate()
    gate.bind(FrozenOwnerIdentity(OWNER, OWNER_KEY, 7))
    candidate = _candidate()
    scope_hash = fingerprint_json({"skill": candidate.pack_id, "version": "old"})
    scope_id = f"skill-scope:{scope_hash}"
    authority = CompanionTurnAuthority(
        identity_gate=gate,
        preference_resolver=_Preferences(),
    )
    prepared = await authority.prepare_turn(
        CompanionTurnPreparationRequestV1(
            turn=TurnInput(
                "use my daily skill",
                "session-1",
                request_id="request-skill-race",
                turn_id="turn-skill-race",
                root_run_id="run-skill-race",
            ),
            services={},
            selected_instruction_refs=(
                {
                    "pack_id": candidate.pack_id,
                    "version": candidate.version,
                    "manifest_hash": candidate.manifest_hash,
                },
            ),
            skill_invocation_scopes=(
                {
                    "scope_id": scope_id,
                    "scope_hash": scope_hash,
                    "pack_id": candidate.pack_id,
                    "version": candidate.version,
                    "manifest_hash": candidate.manifest_hash,
                    "allowed_tools": ["memory_recall"],
                },
            ),
            active_skill_scope_ids=(scope_id,),
        )
    )
    changed = replace(
        candidate,
        version="2.0.0",
        manifest_hash=fingerprint_json({"manifest": "daily-three-v2"}),
        binding_generation=5,
    )

    with pytest.raises(
        RuntimeError, match="prepared_skill_catalog_binding_mismatch"
    ):
        await authority.finalize_after_catalog(
            prepared,
            _capture(changed),
            run_id="run-skill-race",
        )


@pytest.mark.asyncio
async def test_skill_dependency_uses_normalized_catalog_binding_generation() -> None:
    gate = IdentityReadyGate()
    gate.bind(FrozenOwnerIdentity(OWNER, OWNER_KEY, 7))
    candidate = _candidate()
    scope_hash = fingerprint_json(
        {"skill": candidate.pack_id, "version": candidate.version}
    )
    scope_id = f"skill-scope:{scope_hash}"
    authority = CompanionTurnAuthority(
        identity_gate=gate,
        preference_resolver=_Preferences(),
    )
    prepared = await authority.prepare_turn(
        CompanionTurnPreparationRequestV1(
            turn=TurnInput(
                "use my daily skill",
                "session-1",
                request_id="request-skill-positive",
                turn_id="turn-skill-positive",
                root_run_id="run-skill-positive",
            ),
            services={},
            selected_instruction_refs=(
                {
                    "pack_id": candidate.pack_id,
                    "version": candidate.version,
                    "manifest_hash": candidate.manifest_hash,
                },
            ),
            skill_invocation_scopes=(
                {
                    "scope_id": scope_id,
                    "scope_hash": scope_hash,
                    "pack_id": candidate.pack_id,
                    "version": candidate.version,
                    "manifest_hash": candidate.manifest_hash,
                    "allowed_tools": ["memory_recall"],
                },
            ),
            active_skill_scope_ids=(scope_id,),
        )
    )

    finalized = await authority.finalize_after_catalog(
        prepared,
        _capture(candidate),
        run_id="run-skill-positive",
    )

    dependency = next(
        item
        for item in finalized.growth_dependencies
        if item.dependency_kind == "capability_pack"
    )
    assert dependency.binding_generation == candidate.binding_generation
    assert dependency.catalog_content_stamp == "catalog-stamp-1"


@pytest.mark.asyncio
async def test_personal_candidate_to_catalog_capture_exact_change_fails_closed() -> None:
    gate = IdentityReadyGate()
    gate.bind(FrozenOwnerIdentity(OWNER, OWNER_KEY, 7))
    candidate = _candidate()
    authority = CompanionTurnAuthority(
        identity_gate=gate,
        preference_resolver=_Preferences(),
        personal_workflow_source=_CandidateSource(candidate),
        personal_workflow_matcher=_Matcher(candidate.candidate_id),
    )
    prepared = await authority.prepare_turn(
        CompanionTurnPreparationRequestV1(
            turn=TurnInput(
                "show my daily priorities",
                "session-1",
                request_id="request-catalog-race",
                turn_id="turn-catalog-race",
                root_run_id="run-catalog-race",
            ),
            services={},
        )
    )
    changed = replace(
        candidate,
        manifest_hash=fingerprint_json({"manifest": "daily-three-rebound"}),
        binding_generation=5,
    )

    with pytest.raises(
        RuntimeError, match="personal_workflow_catalog_binding_mismatch"
    ):
        await authority.finalize_after_catalog(
            prepared,
            _capture(changed),
            run_id="run-catalog-race",
        )


@pytest.mark.asyncio
async def test_owner_memory_scope_uses_the_same_frozen_identity() -> None:
    gate = IdentityReadyGate()
    gate.bind(FrozenOwnerIdentity(OWNER, OWNER_KEY, 7))
    seen = []

    async def capture(_turn, _services, identity):
        seen.append(identity)
        gate.unbind(expected_binding_epoch=7)
        gate.bind(
            FrozenOwnerIdentity(
                OwnerRef("profile-b", 1),
                "companion:profile-b:1",
                8,
            )
        )
        return SimpleNamespace(
            profile_id=identity.owner.profile_id,
            profile_generation=identity.owner.profile_generation,
            binding_epoch=identity.binding_epoch,
            scope_ref="owner-memory-scope-1",
            scope_hash="a" * 64,
            session_set_hash="b" * 64,
            to_dict=lambda: {
                "profile_id": identity.owner.profile_id,
                "profile_generation": identity.owner.profile_generation,
                "binding_epoch": identity.binding_epoch,
            },
        )

    authority = CompanionTurnAuthority(
        identity_gate=gate,
        preference_resolver=_Preferences(),
        owner_memory_read_scope=capture,
    )
    prepared = await authority.prepare_turn(
        CompanionTurnPreparationRequestV1(
            turn=TurnInput(
                "remember this",
                "session-1",
                request_id="request-memory",
                turn_id="turn-memory",
                root_run_id="run-memory",
            ),
            services={},
        )
    )

    assert seen == [FrozenOwnerIdentity(OWNER, OWNER_KEY, 7)]
    assert prepared.owner == OWNER
    assert prepared.owner_memory_read_scopes[0]["binding_epoch"] == 7


@pytest.mark.asyncio
async def test_owner_memory_scope_can_remain_dormant_before_authority_cutover() -> None:
    gate = IdentityReadyGate()
    gate.bind(FrozenOwnerIdentity(OWNER, OWNER_KEY, 7))
    authority = CompanionTurnAuthority(
        identity_gate=gate,
        preference_resolver=_Preferences(),
        owner_memory_read_scope=lambda _turn, _services, _identity: None,
    )

    prepared = await authority.prepare_turn(
        CompanionTurnPreparationRequestV1(
            turn=TurnInput(
                "legacy-owned turn",
                "session-1",
                request_id="request-legacy-read",
                turn_id="turn-legacy-read",
                root_run_id="run-legacy-read",
            ),
            services={},
        )
    )

    assert prepared.owner_memory_read_scopes == ()
    assert not any(
        item.dependency_kind == "memory_scope"
        for item in prepared.base_dependencies
    )


@pytest.mark.asyncio
async def test_turn_preparer_retains_typed_authority_state_for_venue() -> None:
    gate = IdentityReadyGate()
    gate.bind(FrozenOwnerIdentity(OWNER, OWNER_KEY, 3))
    authority = CompanionTurnAuthority(
        identity_gate=gate,
        preference_resolver=_Preferences(),
    )
    preparer = ProductTurnPreparer(companion_turn_authority=authority)
    turn = TurnInput(
        "hello",
        "session-1",
        request_id="request-1",
        turn_id="turn-1",
        root_run_id="run-1",
    )
    config = SimpleNamespace(
        raw={},
        features=SimpleNamespace(
            context_os_v1=False,
            summary_quality_loop=False,
        ),
    )
    prepared = await preparer.prepare_context(
        turn,
        services={},
        config=config,
        local_llm=SimpleNamespace(model="fixture", base_url=""),
        tool_registry=SimpleNamespace(has=lambda _name: False),
        current_message_id=None,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _items: None,
        summary_build_reinject_msg=lambda text: {
            "role": "system",
            "content": text,
        },
    )

    assert prepared.companion_authority_state is not None
    assert prepared.companion_authority_state.owner_key == OWNER_KEY
    assert prepared.companion_turn_finalizer is authority
    assert prepared.run_prepared_context is None


def test_turn_preparer_rejects_removed_legacy_preference_callbacks() -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        ProductTurnPreparer(preference_resolver=object())  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_product_composition_injects_exact_typed_authority(
    monkeypatch,
) -> None:
    from deskpet.harness.adapters import product_composition
    from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
    from deskpet.tools.registry import ToolRegistry

    authority = object()
    runtime = SimpleNamespace(run_client=object())

    async def build_runtime(**_kwargs):
        return runtime

    monkeypatch.setattr(
        product_composition, "build_harness_runtime", build_runtime
    )
    profiles = ProfileRegistry(
        (
            ProfileSpec("agent.general", "general", "react"),
            ProfileSpec(
                "workflow.fixture",
                "fixture",
                "workflow",
                workflow_key="fixture.v1",
                workflow_name="fixture",
                workflow_version="v1",
                state_factory=dict,
                context_factory=dict,
            ),
        )
    )
    _runtime, venue = (
        await product_composition.build_product_harness_composition(
            uow=object(),
            profiles=profiles,
            workflow_launcher=object(),
            tool_registry=ToolRegistry(),
            loop_factory=object(),
            companion_turn_authority=authority,
        )
    )

    assert venue._preparer._companion_turn_authority is authority
