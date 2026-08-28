from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.capabilities.contracts import fingerprint_json
from deskpet.capabilities.skill_install import (
    AuthorizedSkillInstallReceipt,
    ProjectSkillInstallError,
    ProjectSkillInstallService,
    SkillInstallProjectAuthority,
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
        validated_ref=SimpleNamespace(
            source_kind="git",
            archive_hash="d" * 64,
            manifest_hash="e" * 64,
            file_set_hash="1" * 64,
            entry_count=2,
            total_uncompressed_bytes=len(archive),
            limits_hash="2" * 64,
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

    async def publish_skill_install_batch(self, **kwargs):
        self.calls += 1
        assert [member.pack_id for member in kwargs["members"]] == ["alpha"]
        return {
            "manager_receipt_hash": "4" * 64,
            "operation_id": kwargs["handoff"].operation_id,
            "committed_set_stamp": kwargs["intent"].member_set_stamp,
        }


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
