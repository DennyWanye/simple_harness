from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.capabilities.builder import (
    CapabilityBuildError,
    CapabilityBuilderHost,
)
from deskpet.companion.build_admission import GrowthCandidateBuildPermitV1
from deskpet.capabilities.contracts import CatalogStamp, fingerprint_json
from deskpet.capabilities.manifest import PackEnvironment
from deskpet.capabilities.platform import CapabilityPlatform
from deskpet.capabilities.store import CapabilityStore
from deskpet.harness.context import HostContextFactory
from deskpet.harness.contracts import PreparedRunContextV1
from deskpet.tools.registry import ToolRegistry
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.store import SqliteExecutionUnitOfWork
from deskpet.execution.contracts import RunContext


def _environment() -> PackEnvironment:
    return PackEnvironment(
        deskpet_version="0.6.0",
        os="windows",
        architecture="x86_64",
        python_version="3.11.9",
    )


async def _admitted(
    tmp_path: Path,
    *,
    hits: list[dict[str, object]] | None = None,
    tool_service: object | None = None,
):
    uow = SqliteExecutionUnitOfWork(tmp_path / "execution.db")
    await uow.activate_runtime()
    store = CapabilityStore(uow)
    registry = ToolRegistry()
    state = await store.state()
    stamp = CatalogStamp(
        catalog_generation=state.catalog_generation,
        registry_revision=registry.catalog_snapshot().revision,
        binding_generation=state.binding_generation,
        skill_revision=0,
        mcp_revision=0,
    )
    durable_hits = list(hits or [])
    receipt_ref = fingerprint_json(
        {"root_run_id": "root-builder", "hits": durable_hits}
    )
    snapshot_ref = "catalog:builder-fixture"
    await store.record_search_receipt(
        receipt_id=receipt_ref,
        root_run_id="root-builder",
        catalog_stamp_fingerprint=stamp.fingerprint,
        query_hash=fingerprint_json({"query": "photo rename adapter"}),
        result={
            "snapshot_ref": snapshot_ref,
            "hits": durable_hits,
            "semantic_used": False,
        },
        created_at=1.0,
    )
    messages = (
        {
            "role": "tool",
            "tool_call_id": "search-call",
            "name": "capability_search",
            "content": json.dumps(
                {
                    "search_receipt_ref": receipt_ref,
                    "catalog_stamp": stamp.to_dict(),
                    "snapshot_ref": snapshot_ref,
                    "matches": [
                        {
                            "capability_id": item.get("capability_id"),
                            "version": item.get("version"),
                            "score": item.get("score"),
                            "executable": item.get("executable"),
                        }
                        for item in durable_hits
                    ],
                }
            ),
        },
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    host = CapabilityBuilderHost(
        uow,
        registry,
        environment=_environment(),
        staging_base=tmp_path / "deskpet-user-data" / "builder",
        tool_service=tool_service,
    )
    launch = await host.admit(
        root_run_id="root-builder",
        parent_run_id="run-builder",
        parent_goal_ref="goal-builder",
        objective="rename photos from deterministic metadata",
        original_args={"folder": "photos"},
        canonical_messages=messages,
        task_workspace=str(workspace),
        requested_scope="run",
    )
    return host, launch, uow, registry, messages


def _write_pack(
    launch,
    *,
    draft_index: int = 0,
    unsafe_worker: bool = False,
    execution_profile: str = "brokered-effect-v1",
) -> Path:
    root = launch.draft_path(draft_index)
    root.mkdir(parents=True, exist_ok=True)
    schema = root / "schemas" / "rename.schema.json"
    happy = root / "tests" / "happy.json"
    invalid = root / "tests" / "invalid.json"
    for path in (schema, happy, invalid):
        path.parent.mkdir(parents=True, exist_ok=True)
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {
                    "target_name": {"type": "string", "minLength": 1}
                },
                "required": ["target_name"],
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    happy.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tool": "rename",
                "args": {"target_name": "renamed.jpg"},
            }
        ),
        encoding="utf-8",
    )
    invalid.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tool": "rename",
                "args": {},
                "expected_error_code": "missing_target_name",
            }
        ),
        encoding="utf-8",
    )
    unsafe_import = "import os\n" if unsafe_worker else ""
    worker = root / "worker.py"
    worker.write_text(
        (
            f"{unsafe_import}import json\nimport sys\n"
            "request = json.load(sys.stdin)\n"
            "request_id = request['request_id']\n"
            "tool = request['tool']\n"
            "args = request['args']\n"
            "if tool == 'healthcheck':\n"
            "    response = {'ok': True, 'request_id': request_id, "
            "'value': {'protocol': 'deskpet-json-tool-v1', 'healthy': True}}\n"
            "elif not args.get('target_name'):\n"
            "    response = {'ok': False, 'request_id': request_id, "
            "'error': {'code': 'missing_target_name', "
            "'message': 'target_name is required'}}\n"
            "else:\n"
            "    response = {'ok': True, 'request_id': request_id, "
            "'value': {'target_name': args['target_name']}, "
            "'effect_plan': {'actions': [{'kind': 'rename_file', "
            "'source_ref': 'input:0', "
            "'target_name': args['target_name']}]}}\n"
            "json.dump(response, sys.stdout, ensure_ascii=False)\n"
        ),
        encoding="utf-8",
    )
    declared = [worker, schema, happy, invalid]
    files = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in declared
    ]
    manifest = {
        "schema_version": 1,
        "id": "photo_renamer",
        "name": "Photo Renamer",
        "version": f"1.0.{draft_index}",
        "source": {
            "type": "local",
            "uri": str(root.resolve()),
            "revision": launch.source_revision(draft_index),
        },
        "compatibility": {
            "deskpet": ">=0.5.0",
            "os": ["windows"],
            "architectures": ["x86_64"],
            "python": ">=3.11",
        },
        "entries": {
            "tools": [
                {
                    "id": "rename",
                    "provider_name": "photo_renamer__rename",
                    "runtime": "deskpet-json-tool-v1",
                    "execution_profile": execution_profile,
                    "input_views": ["file:metadata"],
                    "entry": "worker.py",
                    "schema": "schemas/rename.schema.json",
                    "healthcheck": "healthcheck",
                }
            ],
            "mcp_servers": [],
        },
        "permissions": ["filesystem_read", "filesystem_write"],
        "effects": ["staged_file"],
        "dependencies": {"python": [], "commands": []},
        "files": files,
        "uninstall": {
            "stop_servers": True,
            "remove_environment_when_unreferenced": True,
        },
    }
    (root / "deskpet-pack.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    return root


def _write_skill_pack(launch, *, draft_index: int = 0) -> Path:
    root = launch.draft_path(draft_index)
    skill = root / "skills" / "daily-three" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        "---\nname: daily-three\nallowed-tools: []\n---\n"
        "# Daily Three\n\nPick three priorities.\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 2,
        "id": "daily_three",
        "name": "Daily Three",
        "version": "1.0.0",
        "source": {
            "type": "local",
            "uri": str(root.resolve()),
            "revision": launch.source_revision(draft_index),
        },
        "compatibility": {
            "deskpet": ">=0.5.0",
            "os": ["windows"],
            "architectures": ["x86_64"],
            "python": ">=3.11",
        },
        "entries": {
            "skills": [
                {
                    "id": "daily-three",
                    "path": "skills/daily-three/SKILL.md",
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
        "files": [
            {
                "path": "skills/daily-three/SKILL.md",
                "sha256": hashlib.sha256(skill.read_bytes()).hexdigest(),
            }
        ],
        "uninstall": {
            "stop_servers": True,
            "remove_environment_when_unreferenced": True,
        },
    }
    (root / "deskpet-pack.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    return root


@pytest.mark.asyncio
async def test_builder_admission_uses_current_search_and_managed_staging(
    tmp_path: Path,
) -> None:
    _host, launch, _uow, _registry, _messages = await _admitted(tmp_path)

    assert Path(launch.task_workspace) == (tmp_path / "project").resolve()
    assert Path(launch.staging_root).is_relative_to(
        (tmp_path / "deskpet-user-data").resolve()
    )
    assert not Path(launch.staging_root).is_relative_to(
        Path(launch.task_workspace)
    )
    assert launch.draft_path(0).is_dir()


@pytest.mark.asyncio
async def test_builder_rejects_sufficient_executable_match(
    tmp_path: Path,
) -> None:
    with pytest.raises(CapabilityBuildError) as caught:
        await _admitted(
            tmp_path,
            hits=[
                {
                    "capability_id": "existing",
                    "version": "1.0.0",
                    "score": 92.0,
                    "match_kind": "exact",
                    "executable": True,
                    "descriptor_fingerprint": "a" * 64,
                }
            ],
        )
    assert caught.value.code == "sufficient_executable_capability_exists"


@pytest.mark.asyncio
async def test_builder_validates_compute_only_brokered_pack(
    tmp_path: Path,
) -> None:
    host, launch, _uow, _registry, _messages = await _admitted(tmp_path)
    _write_pack(launch)

    evidence = await host.validate_draft(launch, draft_index=0)

    assert evidence.install_request.generated is True
    assert evidence.install_request.scope == "run"
    assert evidence.install_request.expected_pack_id == "photo_renamer"
    assert "compute_only_json_worker" in evidence.checks
    assert "brokered_effect_profile" in evidence.checks


@pytest.mark.asyncio
async def test_builder_rejects_unsafe_or_native_generated_worker(
    tmp_path: Path,
) -> None:
    host, launch, _uow, _registry, _messages = await _admitted(tmp_path)
    _write_pack(launch, unsafe_worker=True)
    with pytest.raises(CapabilityBuildError) as unsafe:
        await host.validate_draft(launch, draft_index=0)
    assert unsafe.value.code == "generated_worker_unsafe"

    other = tmp_path / "other"
    other.mkdir()
    host2, launch2, _uow2, _registry2, _messages2 = await _admitted(other)
    _write_pack(launch2, execution_profile="native-adapter")
    with pytest.raises(CapabilityBuildError) as native:
        await host2.validate_draft(launch2, draft_index=0)
    assert native.value.code == "generated_tool_contract_invalid"


@pytest.mark.asyncio
async def test_builder_completion_is_closed_and_manager_owned(
    tmp_path: Path,
) -> None:
    class Service:
        def __init__(self) -> None:
            self.calls = []

        async def install(self, args, context, *, kind):
            self.calls.append((dict(args), context, kind))
            return {
                "ok": True,
                "operation_id": "operation-1",
                "status": "succeeded",
                "phase": "published",
                "pack_id": "photo_renamer",
                "version": "1.0.0",
                "manifest_hash": expected_manifest_hash,
                "scope": "run",
                "scope_key": "root-builder",
                "operation_receipt": {"receipt_ref": "host-owned"},
            }

    service = Service()
    host, launch, _uow, _registry, _messages = await _admitted(
        tmp_path,
        tool_service=service,
    )
    _write_pack(launch)
    evidence = await host.validate_draft(launch, draft_index=0)
    expected_manifest_hash = evidence.manifest_hash
    context = HostContextFactory().create_tool_context(
        RunContext(
            session_id="session-builder",
            root_run_id="root-builder",
            parent_run_id=None,
            request_id="request-builder",
            turn_id="turn-builder",
            venue="text",
            workspace={
                "root": str(tmp_path / "project"),
                "write_scope_root": str(tmp_path / "project"),
                "scope_hash": fingerprint_json(
                    {"root": str(tmp_path / "project")}
                ),
            },
            capability_hash="a" * 64,
            provider_plan={"providers": ["fixture"]},
            trace_id="trace-builder",
            principal_id="local:builder",
            auth_epoch=1,
        ),
        run_id="run-builder",
        command_id="command-builder",
        call_id="call-builder",
        effect_id="effect-builder",
    )
    result = await host.finalize_child_completion(
        launch,
        {
            "schema_version": 1,
            "lineage_id": launch.lineage.lineage_id,
            "draft_index": 0,
            "draft_path": str(launch.draft_path(0)),
        },
        context=context,
    )

    assert result["operation_receipt"] == {"receipt_ref": "host-owned"}
    assert result["builder_evidence"]["manifest_hash"] == expected_manifest_hash
    assert len(service.calls) == 1
    assert service.calls[0][0]["generated"] is True
    assert service.calls[0][2] == "install"

    with pytest.raises(CapabilityBuildError) as escaped:
        await host.finalize_child_completion(
            launch,
            {
                "schema_version": 1,
                "lineage_id": launch.lineage.lineage_id,
                "draft_index": 0,
                "draft_path": str(tmp_path / "outside"),
            },
            context=context,
        )
    assert escaped.value.code == "builder_staging_mismatch"


@pytest.mark.asyncio
async def test_general_builder_cannot_publish_governed_entries(
    tmp_path: Path,
) -> None:
    class Service:
        def __init__(self) -> None:
            self.calls = 0

        async def install(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("governed output must not reach Manager")

    service = Service()
    host, launch, _uow, _registry, _messages = await _admitted(
        tmp_path,
        tool_service=service,
    )
    _write_skill_pack(launch)

    with pytest.raises(CapabilityBuildError) as caught:
        await host.finalize_child_completion(
            launch,
            {
                "schema_version": 1,
                "lineage_id": launch.lineage.lineage_id,
                "draft_index": 0,
                "draft_path": str(launch.draft_path(0)),
            },
            context=None,
        )

    assert caught.value.code == "governance_admission_required"
    assert service.calls == 0


@pytest.mark.asyncio
async def test_candidate_only_finalize_issues_receipt_without_manager(
    tmp_path: Path,
) -> None:
    class Service:
        def __init__(self) -> None:
            self.calls = 0

        async def install(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("candidate-only output must not reach Manager")

    class Transaction:
        def __init__(self) -> None:
            self.values = None
            self.material = None
            self.verified = 0

        async def insert_candidate_draft_receipt(self, values, *, created_at):
            self.values = (dict(values), created_at)

        async def verify_candidate_draft_receipt(self, values):
            assert self.values is not None
            assert self.values[0] == dict(values)
            self.verified += 1

        async def insert_candidate_draft_material(self, **values):
            self.material = dict(values)

        async def verify_candidate_draft_material(self, **values):
            assert self.material is not None
            assert {
                key: value
                for key, value in self.material.items()
                if key not in {"archive_bytes", "created_at"}
            } == dict(values)

    service = Service()
    host, launch, _uow, _registry, _messages = await _admitted(
        tmp_path,
        tool_service=service,
    )
    permit = GrowthCandidateBuildPermitV1.issue(
        build_id="build-1",
        owner_key="companion:profile-1:1",
        proposal_ref="proposal-1",
        proposal_hash="a" * 64,
        evidence_set_hash="b" * 64,
        source_fence_hash="c" * 64,
        target_fence_hash="d" * 64,
        lease_epoch=1,
    )
    launch = host.admit_candidate_only_output(
        launch,
        permit=permit,
        builder_launch_id="builder-launch-1",
        child_run_id="child-builder-1",
        child_start_hash="e" * 64,
        expected_entry_kinds=("skill",),
    )
    _write_skill_pack(launch)
    completion = {
        "schema_version": 1,
        "lineage_id": launch.lineage.lineage_id,
        "draft_index": 0,
        "draft_path": str(launch.draft_path(0)),
    }
    terminal = SimpleNamespace(
        event_key="terminal:child-builder-1",
        kind="run.final",
        payload={"text": json.dumps(completion)},
    )
    record = SimpleNamespace(run_id="child-builder-1")
    transaction = Transaction()
    extension = host.candidate_terminal_commit_extension(launch)
    prepared = PreparedRunContextV1(
        persistence_required=True,
        terminal_commit_extensions=(extension,),
    )

    receipt_ref = await extension.apply_terminal_commit(
        transaction,
        record=record,
        terminal_event=terminal,
    )
    await extension.verify_terminal_replay(
        transaction,
        record=record,
        terminal_event=terminal,
    )

    assert receipt_ref["kind"] == "deskpet.candidate-draft.v1"
    assert receipt_ref["ref"].startswith("candidate-draft:")
    assert len(receipt_ref["content_hash"]) == 64
    assert transaction.values is not None
    assert transaction.values[0]["builder_launch_id"] == "builder-launch-1"
    assert transaction.values[0]["child_run_id"] == "child-builder-1"
    assert transaction.values[0]["proposal_hash"] == "a" * 64
    assert transaction.material is not None
    assert (
        hashlib.sha256(transaction.material["archive_bytes"]).hexdigest()
        == transaction.values[0]["archive_hash"]
    )
    with zipfile.ZipFile(
        io.BytesIO(transaction.material["archive_bytes"])
    ) as archive:
        file_members = [
            (path, archive.read(path))
            for path in archive.namelist()
            if path != "deskpet-pack.json"
        ]
    expected_file_set_hash = fingerprint_json(
        {
            "schema": "candidate-file-set-v1",
            "files": [
                {
                    "path": path,
                    "mode": 0o644,
                    "hash": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                }
                for path, content in file_members
            ],
        }
    )
    assert transaction.values[0]["file_set_hash"] == expected_file_set_hash
    candidate_content_hash = fingerprint_json(
        {
            "schema": "capability-candidate-content-v1",
            "manifest_hash": transaction.values[0]["manifest_hash"],
            "file_set_hash": expected_file_set_hash,
            "effect_topology_hash": transaction.values[0][
                "effect_topology_hash"
            ],
        }
    )
    assert (
        transaction.values[0]["validated_draft_hash"]
        == candidate_content_hash
    )
    assert transaction.verified == 1
    assert service.calls == 0
    assert prepared.prepared_fingerprint


@pytest.mark.asyncio
async def test_generated_pack_installs_and_rehydrates_after_restart(
    tmp_path: Path,
) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "execution.db")
    await uow.activate_runtime()
    store = CapabilityStore(uow)
    registry = ToolRegistry()
    user_data = tmp_path / "user-data"
    platform = CapabilityPlatform(
        registry=registry,
        store=store,
        user_data_root=user_data,
        environment=_environment(),
        first_party_pack_roots=(),
        register_control_surface=False,
        command_finder=lambda _name: None,
    )
    await platform.initialize()
    state = await store.state()
    stamp = CatalogStamp(
        catalog_generation=state.catalog_generation,
        registry_revision=registry.catalog_snapshot().revision,
        binding_generation=state.binding_generation,
        skill_revision=0,
        mcp_revision=0,
    )
    receipt_ref = fingerprint_json({"search": "restart photo renamer"})
    snapshot_ref = "catalog:restart"
    await store.record_search_receipt(
        receipt_id=receipt_ref,
        root_run_id="root-builder",
        catalog_stamp_fingerprint=stamp.fingerprint,
        query_hash=fingerprint_json({"query": "restart photo renamer"}),
        result={"snapshot_ref": snapshot_ref, "hits": [], "semantic_used": False},
        created_at=1.0,
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    host = CapabilityBuilderHost(
        uow,
        registry,
        environment=_environment(),
        runtime=platform.runtime,
        staging_base=platform.manager.layout.root / "builder",
        tool_service=platform.tool_service,
    )
    launch = await host.admit(
        root_run_id="root-builder",
        parent_run_id="run-builder",
        parent_goal_ref="goal-builder",
        objective="rename photos from metadata",
        original_args={"folder": "photos"},
        canonical_messages=(
            {
                "role": "tool",
                "tool_call_id": "search-call",
                "name": "capability_search",
                "content": json.dumps(
                    {
                        "search_receipt_ref": receipt_ref,
                        "catalog_stamp": stamp.to_dict(),
                        "snapshot_ref": snapshot_ref,
                        "matches": [],
                    }
                ),
            },
        ),
        task_workspace=str(workspace),
        requested_scope="run",
    )
    _write_pack(launch)
    context = ToolExecutionContext(
        scope_id="scope-builder",
        session_id="session-builder",
        request_id="request-builder",
        root_run_id="root-builder",
        turn_id="turn-builder",
        workspace=str(workspace),
        write_scope_root=str(workspace),
        capability_hash="a" * 64,
        scope_hash="b" * 64,
        run_id="run-builder",
        command_id="command-builder",
        call_id="call-builder",
        effect_id="effect-builder",
        trace_id="trace-builder",
    )
    result = await host.finalize_child_completion(
        launch,
        {
            "schema_version": 1,
            "lineage_id": launch.lineage.lineage_id,
            "draft_index": 0,
            "draft_path": str(launch.draft_path(0)),
        },
        context=context,
    )

    assert result["status"] == "succeeded"
    assert registry.has("photo_renamer__rename")
    binding = await store.get_binding("run", "root-builder", "photo_renamer")
    assert binding is not None and binding.active
    assert await platform.shutdown() == ()

    restarted_registry = ToolRegistry()
    restarted = CapabilityPlatform(
        registry=restarted_registry,
        store=store,
        user_data_root=user_data,
        environment=_environment(),
        first_party_pack_roots=(),
        register_control_surface=False,
        command_finder=lambda _name: None,
    )
    initialized = await restarted.initialize()
    assert len(initialized.rehydrated_publications) == 1
    assert restarted_registry.has("photo_renamer__rename")
    assert await restarted.shutdown() == ()
