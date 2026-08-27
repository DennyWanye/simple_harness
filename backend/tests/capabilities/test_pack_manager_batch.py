from __future__ import annotations

import io
import json
import zipfile

import pytest

from deskpet.capabilities.manager import capability_operation_id
from deskpet.capabilities.skill_source import (
    CanonicalSkillBatch,
    CanonicalSkillPack,
    ResolvedSkillSourceEvidence,
)
from deskpet.capabilities.store import (
    CapabilitySkillInstallIntent,
    CapabilitySkillInstallMember,
)

from .test_pack_manager import _FakePublisher, _hash, _manager, _write_pack


def _canonical_pack(tmp_path, name: str) -> CanonicalSkillPack:
    root = tmp_path / name
    _write_pack(root, version="1.0.0", revision="r1")
    manifest_path = root / "deskpet-pack.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["id"] = name
    manifest["name"] = name.title()
    manifest["entries"]["tools"][0]["provider_name"] = f"{name}__check"
    manifest_path.write_text(json.dumps(manifest))
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())
    payload = raw.getvalue()
    # Use the same validator/policy that Manager will reissue at admission.
    from deskpet.capabilities.package_limits import CapabilityPackageValidator
    from deskpet.capabilities.manifest import PackEnvironment, load_and_validate_pack

    ref = CapabilityPackageValidator().validate_zip_archive(payload, source_kind="local")
    validation = load_and_validate_pack(root, environment=PackEnvironment(
        deskpet_version="0.6.0", os="windows", architecture="x86_64", python_version="3.11.9"
    ))
    return CanonicalSkillPack(
        skill_name=name, selected_subdirectory=name, archive_bytes=payload,
        archive_hash=ref.archive_hash, manifest_hash=validation.manifest.manifest_hash,
        content_digest=_hash(f"content:{name}"), validated_ref=ref,
    )


async def _stage_handoff(store, batch: CanonicalSkillBatch, staging_root):
    intent_id = "install-intent-1"
    stamp = batch.batch_digest
    intent = CapabilitySkillInstallIntent(
        intent_id=intent_id,effect_id="effect-1",call_id="call-1",root_run_id="root-1",
        run_id="run-1",channel="chat",project_scope_key="project:v1:p1:r1",
        principal_id="user-1",source={"url": "https://github.com/acme/skills"},
        exact_commit="a"*40,archive_hash=batch.evidence.archive_hash,
        raw_tree_hash=batch.evidence.raw_file_set_digest,member_set_stamp=stamp,
        permission_set_hash=_hash("permissions"),confirmation_nonce="nonce-1",
        confirmation_version=1,expires_at=200,status="staging",state_version=1,
        settlement_ref=None,cleanup_ref=None,verification_ref=None,error=None,
        created_at=100,updated_at=100,
    )
    staging_root.mkdir()
    members = tuple(CapabilitySkillInstallMember(
        intent_id=intent_id,ordinal=i,normalized_name=pack.skill_name,pack_id=pack.skill_name,
        version="1.0.0",manifest_hash=pack.manifest_hash,content_hash=pack.content_digest,
        source_digest=pack.archive_hash,member={
            "name": pack.skill_name,"archive_ref": f"{i}.zip","archive_hash": pack.archive_hash,
            "selected_subdirectory": pack.selected_subdirectory,
            "validated_ref": {name: getattr(pack.validated_ref,name) for name in (
                "source_kind","archive_hash","manifest_hash","file_set_hash",
                "entry_count","total_uncompressed_bytes")},
        },
    ) for i,pack in enumerate(batch.packs))
    for i, pack in enumerate(batch.packs):
        (staging_root / f"{i}.zip").write_bytes(pack.archive_bytes)
    await store.create_skill_install_intent(intent,members)
    await store.cas_skill_install_intent(intent_id,expected_state_version=1,status="awaiting_confirmation")
    operation_id = capability_operation_id("batch-1")
    handoff = await store.handoff_skill_install_intent(
        intent_id,expected_state_version=2,operation_id=operation_id,
        idempotency_key="batch-1",confirmation_receipt_hash=_hash("receipt"),
    )
    return intent, handoff, members


def _batch(tmp_path) -> CanonicalSkillBatch:
    packs = tuple(_canonical_pack(tmp_path, name) for name in ("alpha", "beta"))
    evidence = ResolvedSkillSourceEvidence.issue(
        normalized_url="https://github.com/acme/skills",requested_ref="main",
        exact_commit="a"*40,archive_hash=_hash("repo"),raw_file_set_digest=_hash("tree"),
        selected_subdirectories=("alpha","beta"),
    )
    return CanonicalSkillBatch(evidence,packs,_hash("confirmed-set"))


@pytest.mark.asyncio
async def test_batch_publish_commits_all_members_and_replays_receipt(tmp_path) -> None:
    manager, store = await _manager(tmp_path, publisher=_FakePublisher())
    batch = _batch(tmp_path)
    staging = tmp_path / "service-staging"
    intent,handoff,members = await _stage_handoff(store,batch,staging)
    result = await manager.publish_skill_install_batch(
        intent=intent,handoff=handoff,staging_root=staging,members=members,
    )
    assert len(result["binding_ids"]) == 2
    replay = await manager.publish_skill_install_batch(
        intent=intent,handoff=handoff,staging_root=staging,members=members,
    )
    assert replay["manager_receipt_hash"] == result["manager_receipt_hash"]


class _Crash(BaseException):
    pass


@pytest.mark.asyncio
async def test_batch_crash_after_catalog_swap_recovers_full_new(tmp_path) -> None:
    def crash(point: str) -> None:
        if point == "after:batch_catalog_swapped":
            raise _Crash()
    publisher = _FakePublisher()
    publisher.reconcile_status = "published"
    manager, store = await _manager(tmp_path,publisher=publisher,fault_injector=crash)
    batch = _batch(tmp_path)
    staging = tmp_path / "service-staging"
    intent,handoff,members = await _stage_handoff(store,batch,staging)
    with pytest.raises(_Crash):
        await manager.publish_skill_install_batch(
            intent=intent,handoff=handoff,staging_root=staging,members=members,
        )
    manager._fault_injector = None
    recovered = await manager.recover()
    assert recovered[0].status == "succeeded"
    assert await store.get_binding("project","project:v1:p1:r1","alpha",owner_key="user-1")
    assert await store.get_binding("project","project:v1:p1:r1","beta",owner_key="user-1")
