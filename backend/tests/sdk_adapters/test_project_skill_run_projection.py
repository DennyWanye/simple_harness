from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.capabilities.manifest import load_and_validate_pack
from deskpet.capabilities.contracts import CapabilityBinding
from deskpet.capabilities.run_catalog import (
    FirstPartyFrozenSkillResolver,
    PreparedRunCatalogLease,
)
from deskpet.execution.contracts import fingerprint_json
from deskpet.sdk_adapters.capability_catalog import (
    ProductCapabilityCatalogSourceAdapter,
    SkillRunPageInVerificationEvidence,
)
from deskpet.tools.capabilities import PreparedToolSet
from deskpet.tools.prepared_snapshot import dump_prepared_tool_set


def _write_project_skill_pack(root: Path) -> SimpleNamespace:
    skill_path = root / "skills" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\n"
        "name: installed-skill\n"
        "description: Use the installed Project Skill.\n"
        "allowed-tools: []\n"
        "---\n"
        "Use this exact immutable instruction.\n",
        encoding="utf-8",
    )
    content_hash = hashlib.sha256(skill_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 2,
        "id": "installed-skill",
        "name": "installed-skill",
        "version": "1.0.0",
        "source": {
            "type": "git",
            "uri": "https://github.com/example/installed-skill",
            "revision": "a" * 40,
        },
        "compatibility": {
            "deskpet": ">=0.0.0",
            "os": ["linux", "macos", "windows"],
            "architectures": ["aarch64", "x86_64"],
            "python": ">=3.11",
        },
        "entries": {
            "skills": [
                {
                    "id": "installed-skill",
                    "path": "skills/SKILL.md",
                    "allowed_tools": [],
                }
            ],
            "workflows": [],
            "tools": [],
            "mcp_servers": [],
        },
        "permissions": [],
        "effects": [],
        "dependencies": {"python": [], "commands": []},
        "files": [{"path": "skills/SKILL.md", "sha256": content_hash}],
        "uninstall": {
            "stop_servers": False,
            "remove_environment_when_unreferenced": False,
        },
    }
    (root / "deskpet-pack.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    validation = load_and_validate_pack(root)
    return SimpleNamespace(
        descriptor=validation.descriptor,
        install_path=root,
    )


class _ReadyGate:
    def __init__(self) -> None:
        self.required = False

    async def require_ready(self, **_kwargs) -> None:
        self.required = True


def _lease(
    *,
    manifest_hash: str,
    owner_key: str = "project-owner",
    project_scope_key: str = "project:v2:a",
) -> PreparedRunCatalogLease:
    entry = {
        "entry_kind": "pack",
        "descriptor_fingerprint": "1" * 64,
        "pack_id": "installed-skill",
        "version": "1.0.0",
        "manifest_hash": manifest_hash,
        "selected_binding": {
            "binding_id": "binding-a",
            "capability_id": "installed-skill",
            "version": "1.0.0",
            "manifest_hash": manifest_hash,
            "scope": "project",
            "scope_key": project_scope_key,
            "active": True,
            "generation": 3,
            "owner_key": owner_key,
            "management_policy": "user_managed",
            "management_generation": 1,
        },
    }
    gate = _ReadyGate()
    return PreparedRunCatalogLease(
        snapshot_ref="2" * 64,
        run_catalog_content_stamp="3" * 64,
        process_catalog_stamp="4" * 64,
        lease_intent_id="lease-a",
        lease_intent_hash="5" * 64,
        prepared_tool_set_fingerprint="6" * 64,
        run_id="fresh-verification-run",
        root_run_id="fresh-verification-run",
        entry_set_hash="7" * 64,
        expected_entry_count=1,
        prepared_tool_set_capture_hash="8" * 64,
        prepared_tool_set_envelope={},
        lease_entries=(entry,),
        projection_receipt_id="projection-a",
        projection_receipt_hash="9" * 64,
        process_instance_id="process-a",
        _store=None,
        _hub=None,
        _ready_gate=gate,
        _pin=None,
    )


class _VersionStore:
    def __init__(self, record: SimpleNamespace, row: dict[str, object] | None = None):
        self.record = record
        self.row = row

    async def get_version(self, pack_id, version, manifest_hash):
        descriptor = self.record.descriptor
        if (
            pack_id,
            version,
            manifest_hash,
        ) == (
            descriptor.capability_id,
            descriptor.version,
            descriptor.manifest_hash,
        ):
            return self.record
        return None

    @asynccontextmanager
    async def read_connection(self):
        row = self.row

        class _Cursor:
            async def fetchall(self):
                return [] if row is None else [row]

        class _Db:
            async def execute(self, _sql, _params):
                return _Cursor()

        yield _Db()


@pytest.mark.asyncio
async def test_project_records_come_only_from_exact_frozen_project_lease(
    tmp_path: Path,
) -> None:
    record = _write_project_skill_pack(tmp_path / "pack")
    manifest_hash = record.descriptor.manifest_hash
    lease = _lease(manifest_hash=manifest_hash)
    adapter = ProductCapabilityCatalogSourceAdapter()
    store = _VersionStore(record)

    records = await adapter.sdk_project_resource_records_from_lease(
        store=store,
        lease=lease,
        owner_key="project-owner",
        project_scope_key="project:v2:a",
    )
    other_project = await adapter.sdk_project_resource_records_from_lease(
        store=store,
        lease=lease,
        owner_key="project-owner",
        project_scope_key="project:v2:b",
    )
    projectless = await adapter.sdk_project_resource_records_from_lease(
        store=store,
        lease=lease,
        owner_key="project-owner",
        project_scope_key=None,
    )

    assert [item.skill_locator for item in records] == ["installed-skill"]
    assert records[0].content_hash == hashlib.sha256(
        (record.install_path / "skills" / "SKILL.md").read_bytes()
    ).hexdigest()
    assert records[0].metadata["manifest_hash"] == manifest_hash
    assert len(str(records[0].metadata["scope_hash"])) == 64
    assert other_project == ()
    assert projectless == ()


@pytest.mark.asyncio
async def test_global_records_come_from_exact_frozen_user_snapshot(
    tmp_path: Path,
) -> None:
    record = _write_project_skill_pack(tmp_path / "global-pack")
    owner_key = "user:v2:" + "9" * 64
    binding = CapabilityBinding(
        binding_id="global-binding",
        capability_id=record.descriptor.capability_id,
        version=record.descriptor.version,
        manifest_hash=record.descriptor.manifest_hash,
        scope="user",
        scope_key=owner_key,
        active=True,
        generation=1,
        owner_key=owner_key,
        management_policy="user_managed",
        management_generation=1,
    )
    snapshot = SimpleNamespace(
        descriptors=(
            SimpleNamespace(
                version=record.descriptor,
                visible_bindings=(binding,),
            ),
        )
    )

    records = await (
        ProductCapabilityCatalogSourceAdapter()
        .sdk_global_resource_records_from_snapshot(
            store=_VersionStore(record),
            snapshot=snapshot,
            owner_key=owner_key,
            user_scope_key=owner_key,
        )
    )

    assert [item.skill_locator for item in records] == ["installed-skill"]
    assert records[0].metadata["owner_key"] == owner_key
    assert records[0].metadata["pack_id"] == "installed-skill"


@pytest.mark.asyncio
async def test_ready_fresh_run_pages_exact_content_and_returns_body_free_evidence(
    tmp_path: Path,
) -> None:
    record = _write_project_skill_pack(tmp_path / "pack")
    lease = _lease(manifest_hash=record.descriptor.manifest_hash)
    adapter = ProductCapabilityCatalogSourceAdapter()
    projection_store = _VersionStore(record)
    records = await adapter.sdk_project_resource_records_from_lease(
        store=projection_store,
        lease=lease,
        owner_key="project-owner",
        project_scope_key="project:v2:a",
    )
    prepared = PreparedToolSet.create(
        scope_id="verification-tools",
        revision=1,
        registry_revision=1,
        direct=(),
        deferred=(),
        activated=(),
        denied_names=(),
        policy_fingerprint="policy",
        decisions=(),
    )
    row = {
        "snapshot_ref": lease.snapshot_ref,
        "run_catalog_content_stamp": lease.run_catalog_content_stamp,
        "selected_owner_key": "project-owner",
        "pack_id": "installed-skill",
        "version": "1.0.0",
        "manifest_hash": record.descriptor.manifest_hash,
        "prepared_tool_set_envelope_json": json.dumps(
            {
                "prepared": dump_prepared_tool_set(prepared),
                "exact_tools": [],
            }
        ),
    }
    resolver = FirstPartyFrozenSkillResolver(
        store=_VersionStore(record, row),
        inventory=(),
    )

    evidence = await adapter.verify_fresh_run_page_in(
        lease=lease,
        resolver=resolver,
        owner_key="project-owner",
        project_scope_key="project:v2:a",
        records=records,
        expected_skill_names=("installed-skill",),
    )

    assert evidence.run_id == "fresh-verification-run"
    assert evidence.members[0].manifest_hash == record.descriptor.manifest_hash
    assert evidence.members[0].content_hash == records[0].content_hash
    assert "instruction" not in json.dumps(evidence.to_json())
    assert len(evidence.evidence_hash) == 64
    assert lease._ready_gate.required is True

    with pytest.raises(
        RuntimeError,
        match="skill_run_verification_project_scope_mismatch",
    ):
        await adapter.verify_fresh_run_page_in(
            lease=lease,
            resolver=resolver,
            owner_key="project-owner",
            project_scope_key="project:v2:b",
            records=records,
            expected_skill_names=("installed-skill",),
        )


def test_run_page_in_evidence_is_host_issued() -> None:
    with pytest.raises(
        ValueError,
        match="skill_run_verification_evidence_host_only",
    ):
        SkillRunPageInVerificationEvidence(
            run_id="run",
            owner_key="project-owner",
            project_scope_key="project:v2:a",
            capability_snapshot_ref="a" * 64,
            run_catalog_content_stamp="b" * 64,
            process_catalog_stamp="c" * 64,
            members=(),
            evidence_hash=fingerprint_json({}),
        )
