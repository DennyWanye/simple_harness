from __future__ import annotations

import asyncio
import hashlib

import pytest

from deskpet.capabilities.catalog_gate import CapabilityCatalogGate
from deskpet.capabilities.contracts import (
    EMPTY_OWNER_BINDING_SET_STAMP,
    EMPTY_RECEIPT_SET_HASH,
    OwnerScopeKey,
    PlatformDetailSnapshot,
    PlatformDetailToken,
    PlatformDetailTokenVector,
)
from deskpet.capabilities.platform import CapabilityPlatform
from deskpet.companion.contracts import (
    CandidateAttempt,
    CandidateMode,
    CandidatePackage,
    CandidatePackageBlob,
    CandidatePackageFile,
    GrowthEvent,
    OwnerRef,
)
from deskpet.companion.detail_query import (
    CompanionDetailQueryError,
    CompanionDetailQueryPort,
)
from deskpet.companion.identity_gate import FrozenOwnerIdentity
from deskpet.companion.store import CompanionStore


class _Platform:
    def __init__(self):
        self.version = 1
        self.keys = ()

    async def read_detail_snapshot(self, keys):
        self.keys = tuple(keys)
        return {
            "tokens": [
                {**key.to_dict(), "exists": False, "version": self.version}
                for key in keys
            ],
            "bindings": [],
        }

    async def read_detail_token_vector(self, keys):
        return (await self.read_detail_snapshot(keys))["tokens"]


class _RejectEmptyPlatform(_Platform):
    async def read_detail_snapshot(self, keys):
        if not keys:
            raise RuntimeError("empty platform reads must be short-circuited")
        return await super().read_detail_snapshot(keys)

    async def read_detail_token_vector(self, keys):
        if not keys:
            raise RuntimeError("empty platform reads must be short-circuited")
        return await super().read_detail_token_vector(keys)


def _notification(store, owner):
    for index, payload in enumerate(
        (
            {"title": "first", "value": "Bearer abcdefghijklmnop"},
            {"id": "second", "api_key": "sk_abcdefghijklmnop"},
        ),
        start=1,
    ):
        store.record_growth_event(
            GrowthEvent(
                owner=owner,
                event_id=f"event-{index}",
                source_kind="run",
                source_ref=f"run-{index}",
                context_key=f"context-{index}",
                root_run_id=f"root-{index}",
                reason_code="verified",
                payload=payload,
            )
        )
    return store.create_notification(
        owner,
        notification_id="n1",
        kind="growth_digest",
        source_refs=["event-1", "event-2"],
        summary="summary",
        detail={
            # Forged/pre-filled body must not become the detail authority.
            "evidence": [
                {"id": "forged", "value": "must-not-be-returned"},
            ],
            "platform_keys": [
                {"scope": "user", "scope_key": "user-a"},
                {
                    "owner_key": "builtin",
                    "scope": "builtin",
                    "scope_key": "builtin",
                },
            ],
        },
        available_actions=[],
    )


def _authoritative_chain(store: CompanionStore, owner: OwnerRef):
    store.record_growth_event(
        GrowthEvent(
            owner=owner,
            event_id="chain-event",
            source_kind="run",
            source_ref="chain-run",
            context_key="chain-context",
            root_run_id="chain-root",
            reason_code="verified",
            payload={"summary": "authoritative evidence"},
        )
    )
    store.create_growth_target(
        owner,
        target_id="chain-target",
        kind="skill",
        stable_name="summarize-day",
        pack_id="personal.summarize-day",
    )
    payload = b"# authoritative candidate\n"
    digest = hashlib.sha256(payload).hexdigest()
    package = CandidatePackage(
        package_id="chain-package",
        candidate_mode=CandidateMode.BUILTIN_OVERRIDE,
        pack_id="personal.summarize-day",
        version="2.0.0",
        candidate_content_hash="chain-content",
        candidate_manifest_hash="chain-manifest",
        candidate_package_hash="chain-package-hash",
        archive_hash="chain-archive",
        effect_topology_hash="chain-effects",
        source_facts={
            "owner_key": "builtin",
            "scope": "builtin",
            "scope_key": "summarize-day",
        },
        target_facts={
            "owner_key": "companion:profile-a:1",
            "scope": "user",
            "scope_key": "profile-a",
            "expected_absent": True,
        },
        files=(
            CandidatePackageFile(
                "SKILL.md", "instruction", digest, len(payload), "skill"
            ),
        ),
        blobs=(CandidatePackageBlob("file", "skill", digest, payload),),
    )
    attempt = CandidateAttempt(
        candidate_id="chain-candidate",
        candidate_attempt_key="chain-attempt",
        proposal_source_kind="reflection",
        proposal_source_ref="chain-reflection",
        source_hash="chain-source-hash",
        candidate_mode=CandidateMode.BUILTIN_OVERRIDE,
        target_id="chain-target",
        target_owner_key="companion:profile-a:1",
        target_scope="user",
        target_scope_key="profile-a",
        target_expected_absent=True,
        target_expected_binding_generation=0,
        evidence_event_ids=("chain-event",),
        evidence_set_hash="chain-evidence-set",
        builder_receipt_ref="chain-builder-receipt",
        builder_receipt_hash="chain-builder-hash",
        source_owner_key="builtin",
        source_scope="builtin",
        source_scope_key="summarize-day",
        source_version="1.0.0",
        source_manifest_hash="builtin-manifest",
        source_binding_generation=1,
    )
    store.create_candidate(owner, package, attempt)
    now = store._now()
    with store._write() as db:
        db.execute(
            """INSERT INTO evaluation_runs(
                 profile_id,profile_generation,evaluation_id,candidate_id,candidate_mode,
                 suite_hash,attempt_key,baseline_kind,old_snapshot_hash,candidate_snapshot_hash,
                 source_owner_key,source_scope,source_scope_key,source_pack_id,source_version,
                 source_manifest_hash,source_binding_generation,runner_id,runner_policy_hash,
                 provider_id,model_id,status,claim_epoch,reason_code,schema_version,
                 created_at,updated_at
               ) VALUES (?,?,?,?,?,'suite','eval-attempt','source_pack','base-snapshot',
                         'candidate-snapshot','builtin','builtin','summarize-day',
                         'personal.summarize-day','1.0.0','builtin-manifest',1,
                         'runner','runner-policy','provider','model','passed',0,
                         'evaluation_passed',1,?,?)""",
            (
                owner.profile_id,
                owner.profile_generation,
                "chain-evaluation",
                "chain-candidate",
                "builtin_override",
                now,
                now,
            ),
        )
        db.execute(
            """INSERT INTO evaluation_reports(
                 profile_id,profile_generation,report_id,evaluation_id,
                 candidate_package_hash,dataset_hash,suite_hash,results_root_hash,
                 verdict,reason_code,required_case_count,committed_result_count,
                 schema_version,created_at
               ) VALUES (?,?,? ,?,'chain-package-hash','dataset','suite','results',
                         'passed','evaluation_passed',1,1,1,?)""",
            (
                owner.profile_id,
                owner.profile_generation,
                "chain-report",
                "chain-evaluation",
                now,
            ),
        )
        db.execute(
            """INSERT INTO risk_assessments(
                 profile_id,profile_generation,risk_id,candidate_id,
                 candidate_package_hash,static_preflight_json,effect_topology_diff_json,
                 risk,risk_hash,reason_code,schema_version,created_at
               ) VALUES (?,?,'chain-risk','chain-candidate','chain-package-hash',
                         '{}','{}','low','chain-risk-hash','safe',1,?)""",
            (owner.profile_id, owner.profile_generation, now),
        )
        db.execute(
            """INSERT INTO growth_decisions(
                 profile_id,profile_generation,decision_id,nonce,candidate_id,report_id,
                 risk_id,candidate_mode,source_fence_json,target_owner_key,target_scope,
                 target_scope_key,target_expected_absent,target_expected_binding_generation,
                 decision,actor,reason_code,activation_package_hash,activation_risk_ack,
                 decision_hash,schema_version,created_at
               ) VALUES (?,?,'chain-decision','secret-nonce','chain-candidate',
                         'chain-report','chain-risk','builtin_override','{}',
                         'companion:profile-a:1',
                         'user','profile-a',1,0,'activate','system','safe_auto',
                         'chain-package-hash','none','chain-decision-hash',1,?)""",
            (owner.profile_id, owner.profile_generation, now),
        )
        db.execute(
            """INSERT INTO capability_activation_requests(
                 profile_id,profile_generation,activation_request_id,request_fingerprint,
                 action,candidate_id,candidate_mode,activation_mode,target_owner_key,
                 target_scope,target_scope_key,pack_id,target_version,target_manifest_hash,
                 candidate_package_hash,archive_hash,source_fence_json,
                 target_expected_absent,target_expected_binding_generation,report_id,risk_id,
                 decision_id,manager_idempotency_key,status,claim_epoch,attempt,reason_code,
                 schema_version,created_at,updated_at
               ) VALUES (?,?,'chain-request','chain-request-fingerprint','install',
                         'chain-candidate','builtin_override','normal',
                         'companion:profile-a:1','user',
                         'profile-a','personal.summarize-day','2.0.0','chain-manifest',
                         'chain-package-hash','chain-archive','{}',1,0,'chain-report',
                         'chain-risk','chain-decision','manager-key','succeeded',0,1,
                         'activated',1,?,?)""",
            (owner.profile_id, owner.profile_generation, now, now),
        )
        db.execute(
            """INSERT INTO capability_activation_receipts(
                 profile_id,profile_generation,activation_request_id,manager_operation_id,
                 action,candidate_mode,pack_id,version,manifest_hash,source_fence_hash,
                 target_owner_key,target_scope,binding_generation,owner_binding_set_stamp,
                 process_projection_fingerprint,result_hash,settled_at,reason_code,
                 schema_version
               ) VALUES (?,?,'chain-request','chain-operation','install',
                         'builtin_override','personal.summarize-day','2.0.0',
                         'chain-manifest','source-fence','companion:profile-a:1','user',1,
                         'owner-stamp','projection','receipt-result',?,'activated',1)""",
            (owner.profile_id, owner.profile_generation, now),
        )
        db.execute(
            """INSERT INTO capability_version_supports(
                 profile_id,profile_generation,pack_id,version,manifest_hash,candidate_id,
                 evidence_set_json,evidence_set_hash,build_receipt_ref,build_receipt_hash,
                 report_id,decision_id,activation_receipt_hash,support_state,support_hash,
                 reason_code,schema_version,created_at,updated_at
               ) VALUES (?,?,'personal.summarize-day','2.0.0','chain-manifest',
                         'chain-candidate','["chain-event"]','chain-evidence-set',
                         'chain-builder-receipt','chain-builder-hash','chain-report',
                         'chain-decision','receipt-result','active','support-hash',
                         'activated',1,?,?)""",
            (owner.profile_id, owner.profile_generation, now, now),
        )
        db.execute(
            """INSERT INTO audit_events(
                 profile_id,profile_generation,audit_id,actor,action,reason_code,
                 after_hash,lineage_ref,audit_hash,schema_version,created_at
               ) VALUES (?,?,'chain-audit','companion_host','capability_install',
                         'activated','receipt-result','chain-request','chain-audit-hash',1,?)""",
            (owner.profile_id, owner.profile_generation, now),
        )
    return store.create_notification(
        owner,
        notification_id="chain-notification",
        kind="activation",
        source_refs=["chain-event"],
        summary="authoritative chain",
        detail={
            "evidence": [{"summary": "forged detail must be ignored"}],
            "precedence_keys": [
                {"scope": "run", "scope_key": "run-a"},
                {"scope": "project", "scope_key": "project-a"},
            ],
        },
        available_actions=["view_detail", "rollback"],
    )


@pytest.mark.asyncio
async def test_detail_exact_protocol_pagination_redaction_and_cursor_fences(tmp_path):
    owner = OwnerRef("profile-a", 1)
    frozen = FrozenOwnerIdentity(owner, "owner-a", 9)
    store = CompanionStore(tmp_path / "companion.db")
    store.create_profile(
        profile_id="profile-a",
        generation=1,
        identity_namespace_hash="identity-a",
    )
    notification = _notification(store, owner)
    platform = _Platform()
    query = CompanionDetailQueryPort(
        store=store, platform_store=platform, cursor_secret=b"s" * 32
    )
    version = await query.current_detail_version(
        frozen_identity=frozen, notification=notification
    )
    request = {
        "notification_id": "n1",
        "section": "evidence",
        "cursor": None,
        "page_size": 1,
        "expected_detail_version": version,
    }
    first = await query.query(
        frozen_identity=frozen, control_epoch=9, request=request
    )
    assert first["detail_version"] == version
    assert first["items"][0]["id"]
    assert "Bearer abcdefghijklmnop" not in str(first)
    assert first["next_cursor"]
    cursor_body = query._decode_cursor(first["next_cursor"])
    assert cursor_body["last_sort_key"]
    assert "offset" not in cursor_body

    second = await query.query(
        frozen_identity=frozen,
        control_epoch=9,
        request={**request, "cursor": first["next_cursor"]},
    )
    assert second["items"][0]["id"]
    assert "sk_abcdefghijklmnop" not in str(second)
    assert "must-not-be-returned" not in str(first) + str(second)
    assert second["next_cursor"] is None

    with pytest.raises(CompanionDetailQueryError, match="cursor_invalid"):
        await query.query(
            frozen_identity=frozen,
            control_epoch=9,
            request={
                **request,
                "cursor": first["next_cursor"][:-1] + "x",
            },
        )
    with pytest.raises(CompanionDetailQueryError, match="cursor_invalid"):
        await query.query(
            frozen_identity=frozen,
            control_epoch=10,
            request={**request, "cursor": first["next_cursor"]},
        )

    with pytest.raises(CompanionDetailQueryError) as extra_field:
        await query.query(
            frozen_identity=frozen,
            control_epoch=9,
            request={**request, "profile_id": "profile-b"},
        )
    assert extra_field.value.code == "unavailable"
    with pytest.raises(CompanionDetailQueryError) as oversized:
        await query.query(
            frozen_identity=frozen,
            control_epoch=9,
            request={**request, "page_size": 21},
        )
    assert oversized.value.code == "unavailable"


@pytest.mark.asyncio
async def test_detail_without_platform_keys_does_not_require_catalog_readiness(
    tmp_path,
):
    owner = OwnerRef("profile-a", 1)
    frozen = FrozenOwnerIdentity(owner, "owner-a", 9)
    store = CompanionStore(tmp_path / "companion.db")
    store.create_profile(
        profile_id="profile-a",
        generation=1,
        identity_namespace_hash="identity-a",
    )
    notification = store.create_notification(
        owner,
        notification_id="no-platform-keys",
        kind="growth_forget",
        source_refs=[],
        summary="forgotten",
        detail={"overview": []},
        available_actions=["view_detail"],
    )
    query = CompanionDetailQueryPort(
        store=store,
        platform_store=_RejectEmptyPlatform(),
        cursor_secret=b"s" * 32,
    )

    version = await query.current_detail_version(
        frozen_identity=frozen,
        notification=notification,
    )

    assert version


@pytest.mark.asyncio
async def test_detail_walks_authoritative_lineage_and_ignores_prefilled_sections(
    tmp_path,
):
    owner = OwnerRef("profile-a", 1)
    frozen = FrozenOwnerIdentity(owner, "companion:profile-a:1", 9)
    store = CompanionStore(tmp_path / "companion.db")
    store.create_profile(
        profile_id="profile-a",
        generation=1,
        identity_namespace_hash="identity-a",
    )
    notification = _authoritative_chain(store, owner)
    platform = _Platform()
    query = CompanionDetailQueryPort(
        store=store, platform_store=platform, cursor_secret=b"s" * 32
    )
    version = await query.current_detail_version(
        frozen_identity=frozen, notification=notification
    )
    common = {
        "notification_id": "chain-notification",
        "cursor": None,
        "page_size": 20,
        "expected_detail_version": version,
    }
    expected_tables = {
        "evidence": {"growth_events", "candidate_evidence"},
        "diff": {
            "candidate_artifacts",
            "candidate_packages",
            "candidate_package_files",
            "candidate_package_sources",
        },
        "evaluation": {
            "evaluation_runs",
            "evaluation_reports",
            "risk_assessments",
        },
        "decision": {"growth_decisions", "capability_activation_requests"},
        "operation_receipt": {
            "capability_activation_receipts",
            "capability_version_supports",
        },
        "audit": {"audit_events", "lineage_edges"},
    }
    rendered = []
    for section, required in expected_tables.items():
        result = await query.query(
            frozen_identity=frozen,
            control_epoch=9,
            request={**common, "section": section},
        )
        tables = {item["source_table"] for item in result["items"]}
        assert required.issubset(tables)
        rendered.append(str(result))
    combined = "".join(rendered)
    assert "authoritative evidence" in combined
    assert "base-snapshot" in combined
    assert "chain-evaluation" in combined
    assert "chain-decision" in combined
    assert "chain-operation" in combined
    assert "chain-audit" in combined
    assert "forged detail must be ignored" not in combined
    assert "secret-nonce" not in combined
    assert {key.scope for key in platform.keys} == {
        "run",
        "project",
        "user",
        "builtin",
    }


@pytest.mark.asyncio
async def test_detail_detects_platform_vector_change_between_reads(tmp_path):
    owner = OwnerRef("profile-a", 1)
    frozen = FrozenOwnerIdentity(owner, "owner-a", 9)
    store = CompanionStore(tmp_path / "companion.db")
    store.create_profile(
        profile_id="profile-a",
        generation=1,
        identity_namespace_hash="identity-a",
    )
    notification = _notification(store, owner)

    class _ChangingPlatform(_Platform):
        async def read_detail_snapshot(self, keys):
            value = await super().read_detail_snapshot(keys)
            self.version += 1
            return value

    query = CompanionDetailQueryPort(
        store=store,
        platform_store=_ChangingPlatform(),
        cursor_secret=b"s" * 32,
    )
    with pytest.raises(CompanionDetailQueryError, match="detail_changed"):
        await query.current_detail_version(
            frozen_identity=frozen, notification=notification
        )


@pytest.mark.asyncio
async def test_detail_detects_companion_version_change_during_page(tmp_path):
    owner = OwnerRef("profile-a", 1)
    frozen = FrozenOwnerIdentity(owner, "owner-a", 9)
    store = CompanionStore(tmp_path / "companion.db")
    store.create_profile(
        profile_id="profile-a",
        generation=1,
        identity_namespace_hash="identity-a",
    )
    notification = _notification(store, owner)
    query = CompanionDetailQueryPort(
        store=store, platform_store=_Platform(), cursor_secret=b"s" * 32
    )
    version = await query.current_detail_version(
        frozen_identity=frozen, notification=notification
    )
    original = store.get_detail_version
    calls = 0

    def changing(owner_ref):
        nonlocal calls
        calls += 1
        if calls == 2:
            store.create_notification(
                owner,
                notification_id="n2",
                kind="growth_digest",
                source_refs=[],
                summary="concurrent",
                detail={"overview": []},
                available_actions=[],
            )
        return original(owner_ref)

    store.get_detail_version = changing
    with pytest.raises(CompanionDetailQueryError, match="detail_changed"):
        await query.query(
            frozen_identity=frozen,
            control_epoch=9,
            request={
                "notification_id": "n1",
                "section": "overview",
                "cursor": None,
                "page_size": 20,
                "expected_detail_version": version,
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            {"exists": False, "version": 0, "source": "run"},
            {"exists": True, "version": 1, "source": "run"},
        ),
        (
            {"exists": True, "version": 4, "source": "project"},
            {"exists": False, "version": 5, "source": "project"},
        ),
        (
            {"exists": True, "version": 2, "source": "builtin"},
            {"exists": True, "version": 3, "source": "builtin"},
        ),
    ],
)
async def test_detail_detects_each_exact_platform_token_transition(
    tmp_path, before, after
):
    owner = OwnerRef("profile-a", 1)
    frozen = FrozenOwnerIdentity(owner, "owner-a", 9)
    store = CompanionStore(tmp_path / "companion.db")
    store.create_profile(
        profile_id="profile-a",
        generation=1,
        identity_namespace_hash="identity-a",
    )
    notification = _notification(store, owner)

    class _Transition:
        calls = 0

        async def read_detail_snapshot(self, keys):
            self.calls += 1
            token = before if self.calls == 1 else after
            return {
                "tokens": [{**key.to_dict(), **token} for key in keys],
                "bindings": [],
            }

    query = CompanionDetailQueryPort(
        store=store,
        platform_store=_Transition(),
        cursor_secret=b"s" * 32,
    )
    with pytest.raises(CompanionDetailQueryError, match="detail_changed"):
        await query.current_detail_version(
            frozen_identity=frozen, notification=notification
        )


@pytest.mark.asyncio
async def test_platform_keys_are_derived_from_authority_and_complete_hints():
    keys = CompanionDetailQueryPort._platform_keys(
        owner_key="owner-a",
        authority={
            "rows": [
                {
                    "source_table": "candidate_artifacts",
                    "target_owner_key": "owner-a",
                    "target_scope": "user",
                    "target_scope_key": "user-a",
                    "source_owner_key": "builtin",
                    "source_scope": "builtin",
                    "source_scope_key": "builtin-a",
                },
                {
                    "source_table": "capability_activation_receipts",
                    "fallback_owner_key": "builtin",
                    "fallback_scope": "builtin",
                    "fallback_scope_key": "builtin-fallback",
                },
            ]
        },
        notification={
            "detail": {
                "precedence_keys": [
                    {"scope": "run", "scope_key": "run-a"},
                    {"scope": "project", "scope_key": "project-a"},
                ]
            }
        },
    )
    assert [key.scope for key in keys] == [
        "builtin",
        "builtin",
        "project",
        "run",
        "user",
    ]


@pytest.mark.asyncio
async def test_platform_detail_facade_holds_publish_gate_and_preserves_absent_keys():
    keys = (
        OwnerScopeKey("builtin", "builtin", "summarize-day"),
        OwnerScopeKey("owner-a", "project", "project-a"),
        OwnerScopeKey("owner-a", "run", "run-a"),
        OwnerScopeKey("owner-a", "user", "user-a"),
    )
    vector = PlatformDetailTokenVector(
        tuple(
            PlatformDetailToken(
                key=key,
                exists=False,
                version=0,
                owner_catalog_generation=0,
                committed_owner_binding_set_stamp=EMPTY_OWNER_BINDING_SET_STAMP,
                manager_receipt_set_hash=EMPTY_RECEIPT_SET_HASH,
            )
            for key in keys
        )
    )

    class _TypedStore:
        snapshot_calls = 0
        token_calls = 0

        async def read_detail_snapshot(self, requested):
            assert platform.publish_lock.locked()
            assert tuple(requested) == keys
            self.snapshot_calls += 1
            return PlatformDetailSnapshot(vector, ())

        async def read_detail_token_vector(self, requested):
            assert platform.publish_lock.locked()
            assert tuple(requested) == keys
            self.token_calls += 1
            return vector

    platform = CapabilityPlatform.__new__(CapabilityPlatform)
    platform.publish_lock = asyncio.Lock()
    platform.catalog_gate = CapabilityCatalogGate()
    platform.store = _TypedStore()
    snapshot = await platform.read_detail_snapshot(reversed(keys))
    reread = await platform.read_detail_token_vector(reversed(keys))
    assert tuple(item.key for item in snapshot.tokens.items) == keys
    assert tuple(item.key for item in reread.items) == keys
    assert platform.store.snapshot_calls == 1
    assert platform.store.token_calls == 1


@pytest.mark.asyncio
async def test_platform_keys_reject_cross_owner_but_accept_builtin_owner(tmp_path):
    owner = OwnerRef("profile-a", 1)
    frozen = FrozenOwnerIdentity(owner, "owner-a", 9)
    store = CompanionStore(tmp_path / "companion.db")
    store.create_profile(
        profile_id="profile-a",
        generation=1,
        identity_namespace_hash="identity-a",
    )
    notification = store.create_notification(
        owner,
        notification_id="n1",
        kind="activation",
        source_refs=[],
        summary="activation",
        detail={
            "platform_keys": [
                {
                    "owner_key": "owner-b",
                    "scope": "user",
                    "scope_key": "user-b",
                },
                {
                    "owner_key": "builtin",
                    "scope": "builtin",
                    "scope_key": "builtin",
                },
            ]
        },
        available_actions=[],
    )
    query = CompanionDetailQueryPort(
        store=store, platform_store=_Platform(), cursor_secret=b"s" * 32
    )
    with pytest.raises(CompanionDetailQueryError) as rejected:
        await query.current_detail_version(
            frozen_identity=frozen, notification=notification
        )
    assert rejected.value.code == "unavailable"
