from __future__ import annotations

from types import SimpleNamespace

import pytest

from deskpet.capabilities.platform import CapabilityPlatform
from deskpet.capabilities.run_catalog import SqliteRunCatalogLeasePreparer
from deskpet.capabilities.store import (
    CapabilitySkillInstallIntent,
    CapabilitySkillInstallMember,
    CapabilityStore,
    initialize_capability_database,
)
from deskpet.sdk_adapters.skill_install_verification import (
    SkillInstallVerificationRunService,
)
from deskpet.tools.registry import ToolRegistry


class _Catalog:
    async def sdk_project_resource_records_from_lease(self, **_kwargs):
        return (SimpleNamespace(skill_locator="alpha"),)

    async def verify_fresh_run_page_in(self, **kwargs):
        await kwargs["lease"].require_ready()
        assert kwargs["expected_skill_names"] == ("alpha",)
        return SimpleNamespace(evidence_hash="8" * 64)


class _Stack:
    def __init__(self) -> None:
        self.payload = None

    def read_skill_install_verification_evidence(self, attempt):
        assert self.payload["status"] == "succeeded"
        return SimpleNamespace(
            status="terminal_succeeded",
            terminal_event_id="terminal-1",
            terminal_event_hash="9" * 64,
            evidence_hash="a" * 64,
            reason_code=None,
        )


class _Ingress:
    def __init__(self, store, stack) -> None:
        self.store = store
        self.stack = stack
        self.service = None

    async def start_skill_install_verification(self, **values):
        attempt = await self.store.get_skill_install_verification_attempt(
            values["attempt_id"]
        )
        driver = self.service.driver_for_attempt(attempt)
        result = await driver.start(
            SimpleNamespace(
                run=SimpleNamespace(
                    run_id=attempt.expected_run_id,
                    execution_session_id=attempt.verifier_session_id,
                    request_id=attempt.request_id,
                ),
                start=SimpleNamespace(start_fingerprint="7" * 64),
            ),
            context=None,
            cancel=None,
        )
        self.stack.payload = result.payload
        return SimpleNamespace(run_id=attempt.expected_run_id)

    async def wait_idle(self, _run_id):
        return None


@pytest.mark.asyncio
async def test_real_store_capability_and_sdk_saga_reaches_attested(tmp_path) -> None:
    database = await initialize_capability_database(tmp_path / "capability.db")
    store = CapabilityStore(database, clock=lambda: 100.0)
    await store.initialize()
    intent = CapabilitySkillInstallIntent(
        intent_id="intent-1",
        effect_id="effect-1",
        call_id="call-1",
        root_run_id="root-1",
        run_id="run-1",
        channel="chat",
        project_scope_key="project:v2:" + "1" * 64,
        principal_id="user-1",
        source={
            "project_id": "project-1",
            "project_revision": 1,
            "project_identity": "identity-1",
        },
        exact_commit="a" * 40,
        archive_hash="2" * 64,
        raw_tree_hash="3" * 64,
        member_set_stamp="4" * 64,
        permission_set_hash="5" * 64,
        confirmation_nonce="nonce",
        confirmation_version=1,
        expires_at=200.0,
        status="staging",
        state_version=1,
        settlement_ref=None,
        cleanup_ref=None,
        verification_ref=None,
        error=None,
        created_at=100.0,
        updated_at=100.0,
    )
    member = CapabilitySkillInstallMember(
        intent_id=intent.intent_id,
        ordinal=0,
        normalized_name="alpha",
        pack_id="alpha",
        version="1.0.0",
        manifest_hash="6" * 64,
        content_hash="7" * 64,
        source_digest="8" * 64,
        member={"skill_name": "alpha"},
    )
    await store.create_skill_install_intent(intent, (member,))
    awaiting = await store.cas_skill_install_intent(
        intent.intent_id, expected_state_version=1, status="awaiting_confirmation"
    )
    await store.handoff_skill_install_intent(
        intent.intent_id,
        expected_state_version=awaiting.state_version,
        operation_id="operation-1",
        idempotency_key="install-1",
        confirmation_receipt_hash="receipt-1",
    )
    publishing = await store.get_skill_install_intent(intent.intent_id)
    pending = await store.cas_skill_install_intent(
        intent.intent_id,
        expected_state_version=publishing.state_version,
        status="published_pending_runtime_verification",
        settlement_ref="b" * 64,
    )
    registry = ToolRegistry()
    platform = CapabilityPlatform(
        registry=registry,
        store=store,
        user_data_root=tmp_path / "capabilities",
        register_control_surface=False,
        first_party_pack_roots=(),
    )
    platform.run_catalog_lease_preparer = SqliteRunCatalogLeasePreparer(
        store=store,
        registry=registry,
        hub=platform.hub,
        process_instance_id="process-1",
    )
    stack = _Stack()
    ingress = _Ingress(store, stack)
    service = SkillInstallVerificationRunService(
        store=store,
        platform=platform,
        catalog_source=_Catalog(),
        resolver=object(),
        ingress=ingress,
        runtime_stack=stack,
        tool_catalog_generation=lambda: 1,
    )
    ingress.service = service

    result = await service.verify_skill_install(
        intent=pending,
        manager_receipt={
            "operation_id": "operation-1",
            "manager_receipt_hash": "b" * 64,
            "committed_set_stamp": pending.member_set_stamp,
        },
        members=(member,),
    )

    assert result["status"] == "succeeded"
    final_intent = await store.get_skill_install_intent(intent.intent_id)
    final_attempt = await store.get_current_skill_install_verification_attempt(
        intent.intent_id
    )
    assert final_intent.status == "succeeded"
    assert final_attempt.status == "attested"
    release = await store.get_snapshot_lease_release_receipt(
        final_attempt.lease_intent_id
    )
    assert release is not None
    assert release.status == "committed"
    await store.close()
