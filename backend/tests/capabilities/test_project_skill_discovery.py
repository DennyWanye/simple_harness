from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.capabilities.contracts import CapabilityBinding, CapabilityScope
from deskpet.capabilities.manifest import load_and_validate_pack
from deskpet.capabilities.project_skill_discovery import (
    CompositeSkillDiscoveryProjection,
    ProjectSkillDiscoveryService,
)
from deskpet.skills.loader import SkillPackSnapshotResolver


def _write_pack(root: Path, *names: str) -> SimpleNamespace:
    files = []
    skills = []
    for name in names:
        relative = f"skills/{name}/SKILL.md"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {name}\ndescription: Project command {name}.\n"
            "allowed-tools: []\n---\n"
            f"Execute {name}.\n",
            encoding="utf-8",
        )
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
        skills.append({"id": name, "path": relative, "allowed_tools": []})
    manifest = {
        "schema_version": 2,
        "id": "plan-test-pack",
        "name": "plan-test-pack",
        "version": "1.0.0",
        "source": {
            "type": "git",
            "uri": "https://github.com/example/plan-test",
            "revision": "a" * 40,
        },
        "compatibility": {
            "deskpet": ">=0.0.0",
            "os": ["linux", "macos", "windows"],
            "architectures": ["aarch64", "x86_64"],
            "python": ">=3.11",
        },
        "entries": {
            "skills": skills,
            "workflows": [],
            "tools": [],
            "mcp_servers": [],
        },
        "permissions": [],
        "effects": [],
        "dependencies": {"python": [], "commands": []},
        "files": files,
        "uninstall": {
            "stop_servers": False,
            "remove_environment_when_unreferenced": False,
        },
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "deskpet-pack.json").write_text(json.dumps(manifest), encoding="utf-8")
    validation = load_and_validate_pack(root)
    return SimpleNamespace(
        descriptor=validation.descriptor,
        install_path=root,
        validation_status="healthy",
    )


@pytest.mark.asyncio
async def test_project_projection_uses_only_exact_active_project_binding(
    tmp_path: Path,
) -> None:
    record = _write_pack(tmp_path / "pack", "plan-bs", "plan-task", "plan-test")
    scope = CapabilityScope.for_run(
        "slash-session-a",
        project_id="project-a",
        project_revision=1,
        project_identity="identity-a",
        user_key="sdk-runtime",
    )
    binding = CapabilityBinding(
        binding_id="binding-a",
        capability_id=record.descriptor.capability_id,
        version=record.descriptor.version,
        manifest_hash=record.descriptor.manifest_hash,
        scope="project",
        scope_key=scope.project_key or "",
        active=True,
        generation=1,
        owner_key="sdk-runtime",
        management_policy="user_managed",
    )
    descriptor = SimpleNamespace(
        version=record.descriptor,
        visible_bindings=(binding,),
    )

    class Hub:
        async def snapshot(self, requested_scope, *, owner_key=None):
            assert requested_scope == scope
            assert owner_key == "sdk-runtime"
            return SimpleNamespace(descriptors=(descriptor,))

    class Store:
        async def get_version(self, pack_id, version, manifest_hash):
            assert (pack_id, version, manifest_hash) == (
                record.descriptor.capability_id,
                record.descriptor.version,
                record.descriptor.manifest_hash,
            )
            return record

    store = Store()
    projection = await ProjectSkillDiscoveryService(
        store=store,
        hub=Hub(),
    ).projection_for_scope(scope)
    assert [item["name"] for item in projection.list_skills()] == [
        "plan-bs",
        "plan-task",
        "plan-test",
    ]
    selected = projection.resolve_selection("plan-test")
    assert selected.owner_key == "sdk-runtime"
    assert selected.manifest_hash == record.descriptor.manifest_hash
    resolved = await SkillPackSnapshotResolver(
        version_store=store,
    ).resolve_instruction(selected)
    assert resolved.instruction == "Execute plan-test."


def test_composite_rejects_skill_name_collisions() -> None:
    class Projection:
        def __init__(self, name: str) -> None:
            self.meta = SimpleNamespace(name=name)

        def list_metas(self):
            return [self.meta]

    with pytest.raises(RuntimeError, match="skill_name_collision:PLAN-TEST"):
        CompositeSkillDiscoveryProjection(
            (Projection("plan-test"), Projection("PLAN-TEST"))
        )
