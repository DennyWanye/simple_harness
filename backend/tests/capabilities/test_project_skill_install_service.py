from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
import pytest

from deskpet.capabilities.contracts import canonical_global_owner_key, fingerprint_json
from deskpet.capabilities.skill_install import (
    AuthorizedSkillInstallReceipt,
    GlobalSkillInstallAuthority,
    ProjectSkillInstallError,
    ProjectSkillInstallService,
    SkillInstallProjectAuthority,
)
from deskpet.capabilities.package_limits import (
    CapabilityPackageLimitsV1,
    ValidatedCapabilityPackageRefV1,
)
from deskpet.capabilities.skill_source import (
    CanonicalSkillBatch,
    CanonicalSkillPack,
    CapabilitySourceError,
    ResolvedSkillSourceEvidence,
)
from deskpet.capabilities.store import CapabilityStore, initialize_capability_database
from deskpet.product_state.database import ProductStateDatabase


def _archive(name: str = "alpha") -> bytes:
    manifest = {
        "id": name,
        "version": "1.0.0",
        "entries": {"skills": [{"id": name, "allowed_tools": ["read_file"]}]},
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        package.writestr("deskpet-pack.json", json.dumps(manifest))
    return output.getvalue()


def _batch() -> CanonicalSkillBatch:
    evidence = ResolvedSkillSourceEvidence.issue(
        normalized_url="https://github.com/acme/skills",
        requested_ref="HEAD",
        exact_commit="a" * 40,
        archive_hash="b" * 64,
        raw_file_set_digest="c" * 64,
        selected_subdirectories=("alpha",),
    )
    archive = _archive()
    pack = CanonicalSkillPack(
        skill_name="alpha",
        selected_subdirectory="alpha",
        archive_bytes=archive,
        archive_hash="d" * 64,
        manifest_hash="e" * 64,
        content_digest="f" * 64,
        validated_ref=ValidatedCapabilityPackageRefV1.issue(
            source_kind="git",
            limits=CapabilityPackageLimitsV1(),
            archive_hash="d" * 64,
            manifest_hash="e" * 64,
            file_set_hash="1" * 64,
            entry_count=2,
            total_uncompressed_bytes=len(archive),
        ),
    )
    return CanonicalSkillBatch(evidence=evidence, packs=(pack,), batch_digest="3" * 64)


class Source:
    def __init__(self, *, error: CapabilitySourceError | None = None):
        self.error = error
        self.calls = 0

    async def resolve(self, *_args, **_kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return _batch()


class Publisher:
    def __init__(self):
        self.calls = 0
        self.activation_calls = 0
        self.operation_ids = []

    async def publish_skill_install_batch(self, **kwargs):
        self.calls += 1
        self.operation_ids.append(kwargs["handoff"].operation_id)
        assert [member.pack_id for member in kwargs["members"]] == ["alpha"]
        return {
            "manager_receipt_hash": "4" * 64,
            "operation_id": kwargs["handoff"].operation_id,
            "committed_set_stamp": kwargs["intent"].member_set_stamp,
        }

    async def activate_skill_install_batch(self, **kwargs):
        self.activation_calls += 1
        assert kwargs["intent"].install_scope_key.startswith("user:v2:")
        return {"publication_state": "active", "activation_hash": "a" * 64}


class Verifier:
    def __init__(self, store):
        self.store = store

    async def verify_skill_install(self, **kwargs):
        intent = kwargs["intent"]
        receipt = kwargs["manager_receipt"]
        attempt = await self.store.allocate_skill_install_verification_attempt(
            intent.intent_id,
            expected_state_version=intent.state_version,
            manager_operation_id=receipt["operation_id"],
            manager_receipt_hash=receipt["manager_receipt_hash"],
            committed_set_stamp=intent.member_set_stamp,
            project_scope_key=intent.project_scope_key,
            expected_member_set_stamp=intent.member_set_stamp,
            verifier_session_id="verification-session",
        )
        phases = (
            ("start_submitted", {
                "lease_intent_id": "lease-test",
                "lease_intent_hash": "a" * 64,
                "capability_snapshot_ref": "b" * 64,
                "run_catalog_content_stamp": "c" * 64,
                "process_catalog_stamp": "d" * 64,
                "projection_receipt_id": "projection-test",
                "projection_receipt_hash": "e" * 64,
            }),
            ("run_durable", {"actual_run_id": attempt.expected_run_id}),
            ("catalog_ready", {}),
            ("page_in_proven", {"evidence_hash": "f" * 64}),
            ("terminal_observed", {
                "terminal_event_id": "terminal-test",
                "terminal_event_hash": "1" * 64,
            }),
            ("lease_released", {
                "release_receipt_id": "release-test",
                "release_receipt_hash": "2" * 64,
                "release_owner_event_hash": "3" * 64,
            }),
        )
        for status, fields in phases:
            attempt = await self.store.cas_skill_install_verification_attempt(
                attempt.attempt_id,
                expected_state_version=attempt.state_version,
                status=status,
                **fields,
            )
        current = await self.store.get_skill_install_intent(intent.intent_id)
        attestation = await self.store.attach_skill_install_verification_attestation(
            intent.intent_id,
            expected_intent_state_version=current.state_version,
            attempt_id=attempt.attempt_id,
            expected_attempt_state_version=attempt.state_version,
            attestation={"schema": "test", "evidence_hash": "4" * 64},
        )
        return {"run_id": attempt.expected_run_id, "verification_ref": attestation.verification_ref}


class AllocatingFailVerifier:
    def __init__(self, store):
        self.store = store

    async def verify_skill_install(self, **kwargs):
        intent = kwargs["intent"]
        receipt = kwargs["manager_receipt"]
        await self.store.allocate_skill_install_verification_attempt(
            intent.intent_id,
            expected_state_version=intent.state_version,
            manager_operation_id=receipt["operation_id"],
            manager_receipt_hash=receipt["manager_receipt_hash"],
            committed_set_stamp=intent.member_set_stamp,
            project_scope_key=intent.project_scope_key,
            expected_member_set_stamp=intent.member_set_stamp,
            verifier_session_id="verification-session",
        )
        raise RuntimeError("verification interrupted")


def _project() -> SkillInstallProjectAuthority:
    return SkillInstallProjectAuthority(
        project_id="project-1",
        project_revision=1,
        project_identity="identity-1",
        project_scope_key="project:v1:key",
        principal_id="user-1",
    )


async def _service(tmp_path: Path, source: Source, clock):
    database = await initialize_capability_database(tmp_path / "execution.db")
    store = CapabilityStore(database, clock=clock)
    publisher = Publisher()
    service = ProjectSkillInstallService(
        store=store,
        source=source,  # type: ignore[arg-type]
        staging_root=tmp_path / "staging",
        batch_publisher=publisher,
        runtime_verifier=Verifier(store),
        clock=clock,
        confirmation_ttl_seconds=10,
    )
    return database, store, publisher, service


def test_product_state_is_not_a_skill_install_execution_owner(tmp_path) -> None:
    product = ProductStateDatabase(tmp_path / "product.db")
    product.initialize()
    product_store = CapabilityStore(product)
    with pytest.raises(
        ProjectSkillInstallError, match="execution-owned Capability store"
    ):
        ProjectSkillInstallService(
            store=product_store,
            source=Source(),  # type: ignore[arg-type]
            staging_root=tmp_path / "staging",
            batch_publisher=Publisher(),
            runtime_verifier=Verifier(product_store),
        )
    count = product.connection.execute(
        "SELECT COUNT(*) FROM capability_skill_install_intents"
    ).fetchone()
    assert int(count[0]) == 0
    product.close()


@pytest.mark.asyncio
async def test_stage_is_exact_idempotent_and_confirm_requires_typed_receipt(tmp_path) -> None:
    clock = lambda: 100.0
    source = Source()
    database, store, publisher, service = await _service(tmp_path, source, clock)
    ready = await service.stage(
        url="https://github.com/acme/skills",
        project=_project(), run_id="run-1", root_run_id="root-1",
        call_id="call-1", effect_id="effect-1",
    )
    replay = await service.stage(
        url="https://github.com/acme/skills",
        project=_project(), run_id="run-1", root_run_id="root-1",
        call_id="call-1", effect_id="effect-1",
    )
    assert replay == ready
    assert source.calls == 1
    intent = await store.get_skill_install_intent(ready.intent_id)
    assert intent is not None
    members = await store.skill_install_members(intent.intent_id)
    assert set(members[0].member["validated_ref"]) == {
        "source_kind",
        "policy_version",
        "baseline_hash",
        "policy_hash",
        "archive_hash",
        "manifest_hash",
        "file_set_hash",
        "entry_count",
        "total_uncompressed_bytes",
        "validation_receipt_hash",
    }
    receipt = AuthorizedSkillInstallReceipt(
        channel="chat", intent_id=intent.intent_id,
        content_digest=intent.member_set_stamp,
        project_scope_key=intent.project_scope_key,
        principal_id=intent.principal_id,
        decision_nonce="sdk-final-nonce",
        decision_version=7,
        expires_at=intent.expires_at,
        approved=True,
        run_id="run-1", call_id="call-1", effect_id="effect-1",
        decision_sdk_receipt_hash="5" * 64,
        decision_host_receipt_hash="6" * 64,
        handoff_sdk_receipt_hash="7" * 64,
        handoff_host_receipt_hash="8" * 64,
    )
    result = await service.confirm_authorized(receipt)
    assert result["status"] == "succeeded"
    assert publisher.calls == 1
    rebound = await store.get_skill_install_intent(intent.intent_id)
    assert rebound is not None
    assert (rebound.confirmation_nonce, rebound.confirmation_version) == (
        "sdk-final-nonce", 7
    )
    assert (await service.confirm_authorized(receipt))["status"] == "succeeded"
    assert publisher.calls == 1


@pytest.mark.asyncio
async def test_pending_runtime_verification_resumes_without_new_authorization(
    tmp_path,
) -> None:
    clock = lambda: 100.0
    _database, store, publisher, service = await _service(
        tmp_path, Source(), clock
    )
    service.runtime_verifier = AllocatingFailVerifier(store)
    ready = await service.stage(
        url="https://github.com/acme/skills",
        project=_project(),
        run_id="run-1",
        root_run_id="root-1",
        call_id="call-1",
        effect_id="effect-1",
    )
    intent = await store.get_skill_install_intent(ready.intent_id)
    assert intent is not None
    receipt = AuthorizedSkillInstallReceipt(
        channel="chat",
        intent_id=intent.intent_id,
        content_digest=intent.member_set_stamp,
        project_scope_key=intent.project_scope_key,
        principal_id=intent.principal_id,
        decision_nonce="sdk-initial",
        decision_version=0,
        expires_at=intent.expires_at,
        approved=True,
        run_id="run-1",
        call_id="call-1",
        effect_id="effect-1",
        decision_sdk_receipt_hash="5" * 64,
        decision_host_receipt_hash="6" * 64,
        handoff_sdk_receipt_hash="7" * 64,
        handoff_host_receipt_hash="8" * 64,
    )
    with pytest.raises(RuntimeError, match="verification interrupted"):
        await service.confirm_authorized(receipt)
    pending = await store.get_skill_install_intent(intent.intent_id)
    assert pending is not None
    assert pending.status == "published_pending_runtime_verification"
    assert pending.confirmation_version == 0

    service.runtime_verifier = Verifier(store)
    recovered = await service.reconcile_pending_runtime_verifications()

    assert len(recovered) == 1
    succeeded = await store.get_skill_install_intent(intent.intent_id)
    assert succeeded is not None and succeeded.status == "succeeded"
    assert publisher.calls == 1


@pytest.mark.asyncio
async def test_global_stage_uses_stable_owner_and_content_idempotency(tmp_path) -> None:
    clock = lambda: 100.0
    source = Source()
    database, store, _publisher, service = await _service(tmp_path, source, clock)
    seed = "9" * 64
    owner = GlobalSkillInstallAuthority.from_identity_seed(
        principal_id="user-1", identity_namespace_hash=seed
    )
    assert owner.global_owner_key == canonical_global_owner_key(seed)

    first = await service.stage(
        url="https://github.com/acme/skills", owner=owner, run_id="run-1",
        root_run_id="root-1", call_id="call-1", effect_id="effect-1",
    )
    replay = await service.stage(
        url="https://github.com/acme/skills", owner=owner, run_id="run-2",
        root_run_id="root-2", call_id="call-2", effect_id="effect-2",
    )
    assert replay == first
    intent = await store.get_skill_install_intent(first.intent_id)
    assert intent is not None
    assert intent.schema_version == 2
    assert intent.install_scope == "user"
    assert intent.install_scope_key == owner.global_owner_key


@pytest.mark.asyncio
async def test_global_stage_can_retry_after_durable_source_failure(tmp_path) -> None:
    source = Source(error=CapabilitySourceError("github_timeout", "temporary"))
    database, store, _publisher, service = await _service(
        tmp_path, source, lambda: 100.0
    )
    owner = GlobalSkillInstallAuthority.from_identity_seed(
        principal_id="user-1", identity_namespace_hash="9" * 64
    )
    failed = await service.stage(
        url="https://github.com/acme/skills", owner=owner, run_id="run-1",
        root_run_id="root-1", call_id="call-1", effect_id="effect-1",
    )
    assert failed.code == "github_timeout"

    source.error = None
    ready = await service.stage(
        url="https://github.com/acme/skills", owner=owner, run_id="run-1",
        root_run_id="root-1", call_id="call-1", effect_id="effect-1",
        retry_failure_receipt_ref=failed.failure_receipt_ref,
        retry_attempt_generation=failed.attempt_generation,
        retry_command_id="retry-command-1",
    )

    intent = await store.get_skill_install_intent(ready.intent_id)
    assert intent is not None
    assert intent.status == "awaiting_confirmation"
    assert intent.effect_id.startswith("effect-1:resolved:")


@pytest.mark.asyncio
async def test_global_confirm_scopes_operation_and_activates_only_after_verification(tmp_path) -> None:
    database, store, publisher, service = await _service(tmp_path, Source(), lambda: 100.0)
    owner = GlobalSkillInstallAuthority.from_identity_seed(
        principal_id="host-principal", identity_namespace_hash="9" * 64
    )
    ready = await service.stage(
        url="https://github.com/acme/skills", owner=owner, run_id="run-1",
        root_run_id="root-1", call_id="call-1", effect_id="effect-1",
        channel="settings",
    )
    intent = await store.get_skill_install_intent(ready.intent_id)
    assert intent is not None
    receipt = AuthorizedSkillInstallReceipt(
        channel="settings", intent_id=intent.intent_id,
        content_digest=intent.member_set_stamp,
        project_scope_key=owner.global_owner_key,
        principal_id="host-principal", decision_nonce=intent.confirmation_nonce,
        decision_version=intent.confirmation_version, expires_at=intent.expires_at,
        approved=True, ui_decision_event_ref="ui-event", window_receipt_hash="w" * 64,
    )
    result = await service.confirm_authorized(receipt)
    expected_domain = fingerprint_json({
        "schema": "global-skill-install-operation-v2",
        "owner_scope_key": owner.global_owner_key,
        "exact_commit": intent.exact_commit,
        "member_set_stamp": intent.member_set_stamp,
    })
    assert publisher.operation_ids == [f"skill-install-batch:{expected_domain}"]
    assert publisher.activation_calls == 1
    assert result["activation"]["publication_state"] == "active"


@pytest.mark.asyncio
async def test_global_verification_failure_never_activates_binding(tmp_path) -> None:
    database, store, publisher, service = await _service(tmp_path, Source(), lambda: 100.0)

    class RejectingVerifier:
        async def verify_skill_install(self, **_kwargs):
            raise RuntimeError("fresh Run page-in rejected")

    service.runtime_verifier = RejectingVerifier()
    owner = GlobalSkillInstallAuthority.from_identity_seed(
        principal_id="host-principal", identity_namespace_hash="8" * 64
    )
    ready = await service.stage(
        url="https://github.com/acme/skills", owner=owner, run_id="run-fail",
        root_run_id="root-fail", call_id="call-fail", effect_id="effect-fail",
        channel="settings",
    )
    intent = await store.get_skill_install_intent(ready.intent_id)
    assert intent is not None
    receipt = AuthorizedSkillInstallReceipt(
        channel="settings", intent_id=intent.intent_id,
        content_digest=intent.member_set_stamp,
        project_scope_key=owner.global_owner_key,
        principal_id="host-principal", decision_nonce=intent.confirmation_nonce,
        decision_version=intent.confirmation_version, expires_at=intent.expires_at,
        approved=True, ui_decision_event_ref="ui-fail", window_receipt_hash="f" * 64,
    )
    with pytest.raises(RuntimeError, match="page-in rejected"):
        await service.confirm_authorized(receipt)
    assert publisher.calls == 1
    assert publisher.activation_calls == 0
    pending = await store.get_skill_install_intent(intent.intent_id)
    assert pending is not None
    assert pending.status == "published_pending_runtime_verification"


@pytest.mark.asyncio
async def test_expiry_uses_cleanup_pending_cas_and_removes_exact_stage(tmp_path) -> None:
    now = [100.0]
    database, store, _publisher, service = await _service(tmp_path, Source(), lambda: now[0])
    ready = await service.stage(
        url="https://github.com/acme/skills", project=_project(), run_id="run-1",
        root_run_id="root-1", call_id="call-1", effect_id="effect-1",
    )
    assert service._stage_path(ready.intent_id).exists()
    now[0] = 111.0
    await service.reconcile_expired()
    intent = await store.get_skill_install_intent(ready.intent_id)
    assert intent is not None and intent.status == "expired"
    assert not service._stage_path(ready.intent_id).exists()


@pytest.mark.asyncio
async def test_source_failure_is_durable_structured_and_has_no_stage(tmp_path) -> None:
    source = Source(error=CapabilitySourceError("github_archive_http_error", "network failed"))
    database, store, _publisher, service = await _service(tmp_path, source, lambda: 100.0)
    rejected = await service.stage(
        url="https://github.com/acme/skills", project=_project(), run_id="run-1",
        root_run_id="root-1", call_id="call-1", effect_id="effect-1",
    )
    assert rejected.code == "github_archive_http_error"
    assert rejected.retryable is True
    intent_id = f"skill-install:{fingerprint_json({'schema': 'skill-install-preflight-v1', 'run_id': 'run-1', 'call_id': 'call-1', 'effect_id': 'effect-1', 'url': 'https://github.com/acme/skills', 'requested_ref': 'HEAD', 'project_scope_key': 'project:v1:key'})}"
    intent = await store.get_skill_install_intent(intent_id)
    assert intent is not None and intent.status == "stage_failed"
    assert intent.settlement_ref == rejected.failure_receipt_ref


@pytest.mark.asyncio
async def test_global_nonretryable_stage_failure_replays_across_restart_without_source_call(
    tmp_path,
) -> None:
    source = Source(
        error=CapabilitySourceError("github_skill_not_found", "no Skill found")
    )
    database, _store, _publisher, service = await _service(
        tmp_path, source, lambda: 100.0
    )
    owner = GlobalSkillInstallAuthority.from_identity_seed(
        principal_id="user-1", identity_namespace_hash="7" * 64
    )
    first = await service.stage(
        url="https://github.com/acme/skills.git", owner=owner,
        run_id="run-1", root_run_id="root-1", call_id="call-1",
        effect_id="effect-1", visible_skill_names=("Beta", "alpha"),
    )
    assert source.calls == 1
    reopened_store = CapabilityStore(database, clock=lambda: 101.0)
    restarted = ProjectSkillInstallService(
        store=reopened_store,
        source=source, staging_root=tmp_path / "staging",
        batch_publisher=Publisher(), runtime_verifier=Verifier(reopened_store),
        clock=lambda: 101.0,
    )
    replay = await restarted.stage(
        url="https://github.com/acme/skills", owner=owner,
        run_id="run-2", root_run_id="root-2", call_id="call-2",
        effect_id="effect-2", visible_skill_names=("alpha", "beta"),
    )
    assert replay == first
    assert source.calls == 1


@pytest.mark.asyncio
async def test_global_stage_failure_changed_input_gets_new_identity_and_source_attempt(
    tmp_path,
) -> None:
    source = Source(
        error=CapabilitySourceError("github_skill_not_found", "no Skill found")
    )
    database, _store, _publisher, service = await _service(
        tmp_path, source, lambda: 100.0
    )
    owner = GlobalSkillInstallAuthority.from_identity_seed(
        principal_id="user-1", identity_namespace_hash="6" * 64
    )
    first = await service.stage(
        url="https://github.com/acme/skills", owner=owner,
        run_id="run-1", root_run_id="root-1", call_id="call-1", effect_id="effect-1",
        visible_skill_names=("alpha",),
    )
    changed = await service.stage(
        url="https://github.com/acme/skills", owner=owner,
        run_id="run-2", root_run_id="root-2", call_id="call-2", effect_id="effect-2",
        visible_skill_names=("beta",),
    )
    assert first.failure_receipt_ref != changed.failure_receipt_ref
    assert source.calls == 2


@pytest.mark.asyncio
async def test_global_retry_requires_explicit_generation_and_deduplicates_command(
    tmp_path,
) -> None:
    source = Source(
        error=CapabilitySourceError("github_archive_http_error", "network failed")
    )
    database, _store, _publisher, service = await _service(
        tmp_path, source, lambda: 100.0
    )
    owner = GlobalSkillInstallAuthority.from_identity_seed(
        principal_id="user-1", identity_namespace_hash="5" * 64
    )
    request = {
        "url": "https://github.com/acme/skills",
        "owner": owner,
        "run_id": "run-1",
        "root_run_id": "root-1",
        "call_id": "call-1",
        "effect_id": "effect-1",
    }
    first = await service.stage(**request)
    assert first.retryable is True
    assert first.attempt_generation == 1
    assert first.allowed_actions == ("retry", "change_source", "cancel")
    assert json.loads(first.sdk_public_message()) == {
        "allowed_actions": ["retry", "change_source", "cancel"],
        "attempt_generation": 1,
        "code": "github_archive_http_error",
        "correlation_id": first.correlation_id,
        "failure_receipt_ref": first.failure_receipt_ref,
        "public_message": "network failed",
        "retryable": True,
        "schema": "skill-install-preflight-rejection-v1",
    }

    replay_without_retry = await service.stage(
        **{**request, "run_id": "run-2", "call_id": "call-2", "effect_id": "effect-2"}
    )
    assert replay_without_retry == first
    assert source.calls == 1

    retry_args = {
        "retry_failure_receipt_ref": first.failure_receipt_ref,
        "retry_attempt_generation": 1,
        "retry_command_id": "retry-command-1",
    }
    second = await service.stage(
        **{**request, "run_id": "run-3", "call_id": "call-3", "effect_id": "effect-3"},
        **retry_args,
    )
    assert second.retryable is True
    assert second.attempt_generation == 2
    assert second.failure_receipt_ref != first.failure_receipt_ref
    assert source.calls == 2

    duplicate = await service.stage(
        **{**request, "run_id": "run-4", "call_id": "call-4", "effect_id": "effect-4"},
        **retry_args,
    )
    assert duplicate == second
    assert source.calls == 2

    source.error = None
    success_retry_args = {
        "retry_failure_receipt_ref": second.failure_receipt_ref,
        "retry_attempt_generation": 2,
        "retry_command_id": "retry-command-2",
    }
    ready = await service.stage(
        **{**request, "run_id": "run-5", "call_id": "call-5", "effect_id": "effect-5"},
        **success_retry_args,
    )
    assert ready.intent_id.startswith("skill-install-global:")
    assert source.calls == 3

    success_duplicate = await service.stage(
        **{**request, "run_id": "run-6", "call_id": "call-6", "effect_id": "effect-6"},
        **success_retry_args,
    )
    assert success_duplicate == ready
    assert source.calls == 3
