from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.capabilities.builder import CapabilityBuilderHost
from deskpet.capabilities.contracts import CapabilityVersionDescriptor
from deskpet.capabilities.store import (
    CapabilityStore,
    CapabilityStoreConflict,
    CapabilityStoreError,
    CapabilityVersionRecord,
    initialize_capability_database,
)
from deskpet.capabilities.tools import register_capability_tools
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.tools.registry import ToolRegistry
from deskpet.tools.orchestration_controls import (
    CAPABILITY_REPAIR,
    _capability_mutation_schema,
    register_orchestration_controls,
)


MANIFEST_HASH = "a" * 64
TOOL_FINGERPRINT = "b" * 64
SCHEMA_HASH = "c" * 64
ERROR_FINGERPRINT = "d" * 64


class _Registry:
    @staticmethod
    def catalog_snapshot() -> SimpleNamespace:
        return SimpleNamespace(revision=7)


async def _installed_store(tmp_path: Path) -> tuple[CapabilityStore, Path]:
    database = tmp_path / "workflow.db"
    await initialize_capability_database(database)
    install_path = tmp_path / "installed" / "photo-renamer" / "1.0.0"
    install_path.mkdir(parents=True)
    (install_path / "worker.py").write_text("print('parent')\n", encoding="utf-8")
    store = CapabilityStore(database)
    descriptor = CapabilityVersionDescriptor(
        capability_id="photo-renamer",
        display_name="Photo Renamer",
        version="1.0.0",
        kind="pack",
        source="generated",
        description="Rename photos deterministically.",
        aliases=("photo renamer",),
        logical_tool_ids=("photo_rename",),
        provider_tool_names=("photo_rename",),
        permission_categories=("write_file",),
        effect_kinds=("staged_file",),
        schema_hash=SCHEMA_HASH,
        manifest_hash=MANIFEST_HASH,
        health="healthy",
    )
    await store.record_version(
        CapabilityVersionRecord(
            descriptor=descriptor,
            install_path=install_path,
            validation_status="healthy",
            expected_tool_fingerprints=(TOOL_FINGERPRINT,),
            parent_version=None,
            parent_manifest_hash=None,
            derived_from_receipt_ref=None,
            created_at=1.0,
        )
    )
    await store.set_binding(
        scope="run",
        scope_key="root-1",
        pack_id="photo-renamer",
        version="1.0.0",
        manifest_hash=MANIFEST_HASH,
        expected_generation=0,
    )
    return store, install_path


@pytest.mark.asyncio
async def test_failure_receipt_is_host_validated_and_repair_budget_is_three(
    tmp_path: Path,
) -> None:
    store, _ = await _installed_store(tmp_path)
    receipt = await store.issue_failure_receipt(
        root_run_id="root-1",
        run_id="root-1",
        attempt_id="attempt-1",
        failure_report_ref="failure-report-1",
        provider_call_id="call-1",
        effect_id="1" * 64,
        capability_id="photo-renamer",
        pack_version="1.0.0",
        manifest_hash=MANIFEST_HASH,
        tool_name="photo_rename",
        tool_spec_fingerprint=TOOL_FINGERPRINT,
        canonical_args={"folder": "photos"},
        error_code="api_shape_changed",
        error_fingerprint=ERROR_FINGERPRINT,
        evidence_refs=("failure-report-1",),
    )

    assert await store.get_failure_receipt(receipt.receipt_ref) == receipt
    replay = await store.issue_failure_receipt(
        root_run_id="root-1",
        run_id="root-1",
        attempt_id="attempt-1",
        failure_report_ref="failure-report-1",
        provider_call_id="call-1",
        effect_id="1" * 64,
        capability_id="photo-renamer",
        pack_version="1.0.0",
        manifest_hash=MANIFEST_HASH,
        tool_name="photo_rename",
        tool_spec_fingerprint=TOOL_FINGERPRINT,
        canonical_args={"folder": "photos"},
        error_code="api_shape_changed",
        error_fingerprint=ERROR_FINGERPRINT,
        evidence_refs=("failure-report-1",),
    )
    assert replay == receipt

    first = await store.claim_repair_attempt(
        root_run_id="root-1",
        failure_receipt_ref=receipt.receipt_ref,
        control_call_id="repair-call-1",
    )
    assert first.attempt_no == 1
    assert (
        await store.claim_repair_attempt(
            root_run_id="root-1",
            failure_receipt_ref=receipt.receipt_ref,
            control_call_id="repair-call-1",
        )
    ) == first
    for number in (2, 3):
        attempt = await store.claim_repair_attempt(
            root_run_id="root-1",
            failure_receipt_ref=receipt.receipt_ref,
            control_call_id=f"repair-call-{number}",
        )
        assert attempt.attempt_no == number
    with pytest.raises(CapabilityStoreConflict) as exhausted:
        await store.claim_repair_attempt(
            root_run_id="root-1",
            failure_receipt_ref=receipt.receipt_ref,
            control_call_id="repair-call-4",
        )
    assert exhausted.value.code == "capability_repair_budget_exhausted"

    with pytest.raises(CapabilityStoreError) as forged:
        await store.issue_failure_receipt(
            root_run_id="root-1",
            run_id="root-1",
            attempt_id="attempt-2",
            failure_report_ref="failure-report-2",
            provider_call_id="call-2",
            effect_id="2" * 64,
            capability_id="photo-renamer",
            pack_version="1.0.0",
            manifest_hash=MANIFEST_HASH,
            tool_name="photo_rename",
            tool_spec_fingerprint="e" * 64,
            canonical_args={"folder": "photos"},
            error_code="forged",
            error_fingerprint="f" * 64,
        )
    assert forged.value.code == "failure_capability_provenance_mismatch"


@pytest.mark.asyncio
async def test_repair_admission_derives_parent_args_scope_and_draft(
    tmp_path: Path,
) -> None:
    store, parent_path = await _installed_store(tmp_path)
    receipt = await store.issue_failure_receipt(
        root_run_id="root-1",
        run_id="root-1",
        attempt_id="attempt-1",
        failure_report_ref="failure-report-1",
        provider_call_id="call-1",
        effect_id="1" * 64,
        capability_id="photo-renamer",
        pack_version="1.0.0",
        manifest_hash=MANIFEST_HASH,
        tool_name="photo_rename",
        tool_spec_fingerprint=TOOL_FINGERPRINT,
        canonical_args={"folder": "photos"},
        error_code="api_shape_changed",
        error_fingerprint=ERROR_FINGERPRINT,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    host = CapabilityBuilderHost(
        store.path,
        _Registry(),
        staging_base=tmp_path / "staging",
    )
    launch = await host.admit(
        root_run_id="root-1",
        parent_run_id="root-1",
        parent_goal_ref="goal-1",
        objective="Repair the failed managed adapter.",
        original_args=None,
        canonical_messages=(),
        task_workspace=str(workspace),
        requested_scope="user",
        repair_receipt_ref=receipt.receipt_ref,
        repair_control_call_id="repair-call-1",
    )

    assert launch.search_evidence.evidence_kind == "repair_receipt"
    assert launch.lineage.operation_kind == "repair"
    assert launch.lineage.original_args == {"folder": "photos"}
    assert launch.lineage.parent_version == "1.0.0"
    assert launch.lineage.parent_manifest_hash == MANIFEST_HASH
    assert launch.install_scope == "run"
    assert Path(launch.initial_draft, "worker.py").read_text(
        encoding="utf-8"
    ) == (parent_path / "worker.py").read_text(encoding="utf-8")


def test_repair_tool_schema_accepts_only_receipt_and_generation() -> None:
    schema = _capability_mutation_schema(
        ProfileRegistry(
            (
                ProfileSpec(
                    profile_key="agent.general",
                    route_tag=None,
                    driver_kind="react",
                    launch_policy="root_only",
                ),
            ),
            generation=9,
        ),
        repair=True,
    )
    assert schema["name"] == CAPABILITY_REPAIR
    assert set(schema["parameters"]["properties"]) == {
        "failure_receipt_ref",
        "catalog_generation",
    }
    assert schema["parameters"]["additionalProperties"] is False


def test_repair_has_one_delegate_control_owner_after_platform_registration() -> None:
    registry = ToolRegistry()
    register_capability_tools(registry, SimpleNamespace())
    assert registry.has(CAPABILITY_REPAIR) is False
    profiles = ProfileRegistry(
        (
            ProfileSpec(
                profile_key="agent.general",
                route_tag=None,
                driver_kind="react",
                launch_policy="root_only",
            ),
            ProfileSpec(
                profile_key="agent.specialist",
                route_tag=None,
                driver_kind="react",
                launch_policy="model_spawnable",
            ),
        ),
        generation=9,
    )

    register_orchestration_controls(registry, profiles)

    assert registry.has(CAPABILITY_REPAIR) is True
    assert registry.dispatch_kind(CAPABILITY_REPAIR) == "delegate_control"
