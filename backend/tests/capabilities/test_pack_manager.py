from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from deskpet.capabilities.manager import (
    CapabilityCandidate,
    CapabilityManagerError,
    CapabilityPackManager,
    CapabilityPublication,
    PublishReconciliation,
)
from deskpet.capabilities.manifest import (
    PackEnvironment,
    PackManifestError,
    windows_extended_path,
)
from deskpet.capabilities.package_limits import (
    CapabilityPackageLimitsV1,
    CapabilityPackageValidator,
)
from deskpet.capabilities.source import (
    CapabilitySourceResolver,
    PackSourceRequest,
    StagedPack,
)
from deskpet.capabilities.store import (
    CapabilityStore,
    CapabilityStoreConflict,
    initialize_capability_database,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_pack(
    root: Path,
    *,
    version: str,
    revision: str,
    execution_profile: str = "native-adapter",
    bad_hash: bool = False,
) -> PackSourceRequest:
    entry = root / "tools" / "main.py"
    schema = root / "tools" / "check.schema.json"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("print('ok')\n", encoding="utf-8")
    schema.write_text(
        json.dumps({"type": "object", "additionalProperties": False}),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "id": "godot",
        "name": "Godot",
        "version": version,
        "source": {"type": "local", "uri": str(root), "revision": revision},
        "compatibility": {
            "deskpet": ">=0.5.0",
            "os": ["windows"],
            "architectures": ["x86_64"],
            "python": ">=3.11",
        },
        "entries": {
            "skills": [],
            "tools": [
                {
                    "id": "check",
                    "provider_name": "godot__check",
                    "runtime": "deskpet-json-tool-v1",
                    "execution_profile": execution_profile,
                    "input_views": [],
                    "entry": "tools/main.py",
                    "schema": "tools/check.schema.json",
                    "healthcheck": "check",
                }
            ],
            "mcp_servers": [],
        },
        "permissions": ["filesystem_read"],
        "effects": ["read_only"],
        "dependencies": {"python": [], "commands": []},
        "files": [
            {
                "path": "tools/main.py",
                "sha256": "0" * 64 if bad_hash else _file_hash(entry),
            },
            {"path": "tools/check.schema.json", "sha256": _file_hash(schema)},
        ],
        "uninstall": {
            "stop_servers": True,
            "remove_environment_when_unreferenced": True,
        },
    }
    (root / "deskpet-pack.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return PackSourceRequest("local", str(root), revision)


class _FakePublisher:
    def __init__(self) -> None:
        self.revision = 10
        self.stopped = []
        self.reconcile_status = "rolled_back"

    async def prepare_candidate(
        self, validation, *, install_root, operation_id
    ):
        del install_root, operation_id
        fingerprint = _hash(
            f"{validation.manifest.id}:{validation.manifest.version}:tool"
        )
        return CapabilityCandidate(
            expected_registry_revision=self.revision,
            tool_spec_fingerprints=(fingerprint,),
            old_specs=(),
            new_specs=(
                {
                    "name": "godot__check",
                    "fingerprint": fingerprint,
                },
            ),
            publisher_state={"version": validation.manifest.version},
        )

    async def prepare_installed(self, record, *, operation_id):
        del operation_id
        return CapabilityCandidate(
            expected_registry_revision=self.revision,
            tool_spec_fingerprints=record.expected_tool_fingerprints,
            old_specs=(),
            new_specs=(
                {
                    "name": "godot__check",
                    "fingerprint": record.expected_tool_fingerprints[0],
                },
            ),
            publisher_state={"version": record.descriptor.version},
        )

    async def prepare_uninstall(self, record, binding, *, operation_id):
        del binding, operation_id
        return CapabilityCandidate(
            expected_registry_revision=self.revision,
            tool_spec_fingerprints=(),
            old_specs=(
                {
                    "name": "godot__check",
                    "fingerprint": record.expected_tool_fingerprints[0],
                },
            ),
            new_specs=(),
            publisher_state={"uninstall": True},
        )

    async def publish(self, candidate, *, operation_id):
        del operation_id
        assert candidate.expected_registry_revision == self.revision
        self.revision += 1
        return CapabilityPublication(
            registry_revision=self.revision,
            tool_spec_fingerprints=candidate.tool_spec_fingerprints,
            evidence={"published": True},
        )

    async def reconcile(self, intent):
        del intent
        return PublishReconciliation(status=self.reconcile_status)

    async def stop_binding(self, binding):
        self.stopped.append(binding)


async def _manager(
    tmp_path,
    *,
    publisher=None,
    fault_injector=None,
    source_resolver=None,
):
    db = await initialize_capability_database(tmp_path / "workflow.db")
    store = CapabilityStore(db, clock=lambda: 100.0)
    manager = CapabilityPackManager(
        store=store,
        user_data_root=tmp_path / "user data",
        publisher=publisher or _FakePublisher(),
        environment=PackEnvironment(
            deskpet_version="0.6.0",
            os="windows",
            architecture="x86_64",
            python_version="3.11.9",
        ),
        fault_injector=fault_injector,
        source_resolver=source_resolver,
    )
    return manager, store


@pytest.mark.asyncio
async def test_install_publishes_immutable_version_and_is_idempotent(tmp_path) -> None:
    source_root = tmp_path / "source with spaces"
    request = _write_pack(source_root, version="1.0.0", revision="r1")
    publisher = _FakePublisher()
    manager, store = await _manager(tmp_path, publisher=publisher)
    result = await manager.install(
        request,
        scope="user",
        scope_key="default",
        idempotency_key="install-r1",
        expected_pack_id="godot",
    )
    assert result.operation.status == "succeeded"
    assert result.operation.phase == "published"
    assert result.binding.active
    assert result.install_path.is_dir()
    assert result.install_path != source_root
    assert not manager.layout.staging_path(result.operation.operation_id).exists()
    assert (await store.state()).pending_publish_count == 0
    assert "owner_key" not in result.operation.request
    assert "expected_binding_generation" not in result.operation.request
    assert "management_policy" not in result.operation.request

    duplicate = await manager.install(
        request,
        scope="user",
        scope_key="default",
        idempotency_key="install-r1",
        expected_pack_id="godot",
    )
    assert duplicate.operation.operation_id == result.operation.operation_id
    assert duplicate.install_path == result.install_path
    assert publisher.revision == 11


@pytest.mark.asyncio
async def test_install_honors_exact_owner_generation_and_replays_receipt(
    tmp_path,
) -> None:
    request = _write_pack(
        tmp_path / "owner-source",
        version="1.0.0",
        revision="owner-r1",
    )
    manager, store = await _manager(tmp_path)
    kwargs = {
        "scope": "user",
        "scope_key": "profile",
        "idempotency_key": "companion-owner-install-r1",
        "expected_pack_id": "godot",
        "owner_key": "companion:alice:1",
        "expected_binding_generation": 0,
        "management_policy": "user_managed",
    }

    first = await manager.install(request, **kwargs)
    replay = await manager.install(request, **kwargs)

    assert first.binding.owner_key == "companion:alice:1"
    assert first.binding.generation == 1
    assert first.binding.management_policy == "user_managed"
    assert len(first.manager_receipt_hash) == 64
    assert replay.manager_receipt_hash == first.manager_receipt_hash
    assert (
        replay.committed_owner_binding_set_stamp
        == first.committed_owner_binding_set_stamp
    )
    assert (
        replay.process_projection_fingerprint
        == first.process_projection_fingerprint
    )
    assert (
        await store.get_binding(
            "user",
            "profile",
            "godot",
            owner_key="companion:alice:1",
        )
    ) == first.binding

    with pytest.raises(CapabilityStoreConflict, match="binding changed"):
        await manager.install(
            request,
            **{
                **kwargs,
                "idempotency_key": "companion-owner-install-stale",
            },
        )
    current = await store.get_binding(
        "user",
        "profile",
        "godot",
        owner_key="companion:alice:1",
    )
    assert current is not None
    assert current.generation == 1


@pytest.mark.asyncio
async def test_uninstall_honors_exact_owner_generation_and_returns_receipt(
    tmp_path,
) -> None:
    request = _write_pack(
        tmp_path / "owner-uninstall-source",
        version="1.0.0",
        revision="owner-uninstall-r1",
    )
    manager, store = await _manager(tmp_path)
    installed = await manager.install(
        request,
        scope="user",
        scope_key="profile",
        idempotency_key="companion-owner-uninstall-install",
        expected_pack_id="godot",
        owner_key="companion:alice:1",
        expected_binding_generation=0,
        management_policy="user_managed",
    )

    removed = await manager.uninstall(
        pack_id="godot",
        scope="user",
        scope_key="profile",
        idempotency_key="companion-owner-uninstall",
        owner_key="companion:alice:1",
        expected_binding_generation=installed.binding.generation,
    )

    assert removed.binding.owner_key == "companion:alice:1"
    assert removed.binding.generation == installed.binding.generation + 1
    assert not removed.binding.active
    assert len(removed.manager_receipt_hash) == 64
    assert len(removed.committed_owner_binding_set_stamp) == 64
    assert len(removed.process_projection_fingerprint) == 64
    assert (
        await store.get_binding(
            "user",
            "profile",
            "godot",
            owner_key="companion:alice:1",
        )
    ) == removed.binding
    assert (
        await store.get_binding(
            "user",
            "profile",
            "godot",
        )
    ) is None


@pytest.mark.asyncio
async def test_discovered_pack_id_keeps_original_request_idempotent(tmp_path) -> None:
    request = _write_pack(
        tmp_path / "source", version="1.0.0", revision="r1"
    )
    publisher = _FakePublisher()
    manager, _store = await _manager(tmp_path, publisher=publisher)
    first = await manager.install(
        request,
        scope="user",
        scope_key="default",
        idempotency_key="discover-pack-id",
    )
    repeated = await manager.install(
        request,
        scope="user",
        scope_key="default",
        idempotency_key="discover-pack-id",
    )
    assert first.operation.pack_id == "godot"
    assert repeated.operation.operation_id == first.operation.operation_id
    assert publisher.revision == 11


@pytest.mark.asyncio
async def test_integrity_failure_never_creates_version_or_binding(tmp_path) -> None:
    source = _write_pack(
        tmp_path / "bad", version="1.0.0", revision="bad", bad_hash=True
    )
    manager, store = await _manager(tmp_path)
    with pytest.raises(PackManifestError) as caught:
        await manager.install(
            source,
            scope="user",
            scope_key="default",
            idempotency_key="bad-install",
            expected_pack_id="godot",
        )
    assert caught.value.code == "hash_mismatch"
    assert await store.list_versions("godot") == ()
    assert await store.get_binding("user", "default", "godot") is None
    operation_id = _hash("capability:bad-install")
    assert not manager.layout.staging_path(operation_id).exists()


@pytest.mark.asyncio
async def test_generated_pack_cannot_publish_native_adapter(tmp_path) -> None:
    source = _write_pack(
        tmp_path / "generated",
        version="1.0.0",
        revision="generated",
        execution_profile="native-adapter",
    )
    manager, store = await _manager(tmp_path)
    with pytest.raises(CapabilityManagerError) as caught:
        await manager.install(
            source,
            scope="run",
            scope_key="root-1",
            idempotency_key="generated-install",
            expected_pack_id="godot",
            generated=True,
        )
    assert caught.value.code == "generated_native_adapter_forbidden"
    assert await store.get_binding("run", "root-1", "godot") is None


@pytest.mark.asyncio
async def test_update_creates_new_version_and_rollback_switches_pointer(
    tmp_path,
) -> None:
    publisher = _FakePublisher()
    manager, store = await _manager(tmp_path, publisher=publisher)
    v1 = _write_pack(tmp_path / "v1", version="1.0.0", revision="r1")
    first = await manager.install(
        v1,
        scope="project",
        scope_key="project:fixture",
        idempotency_key="v1",
        expected_pack_id="godot",
    )
    v2 = _write_pack(tmp_path / "v2", version="2.0.0", revision="r2")
    second = await manager.update(
        v2,
        scope="project",
        scope_key="project:fixture",
        idempotency_key="v2",
        expected_pack_id="godot",
    )
    assert second.binding.version == "2.0.0"
    assert len(await store.list_versions("godot")) == 2
    assert first.install_path.is_dir()

    rolled_back = await manager.rollback(
        pack_id="godot",
        target_version="1.0.0",
        target_manifest_hash=first.validation.manifest.manifest_hash,
        scope="project",
        scope_key="project:fixture",
        idempotency_key="rollback-v1",
    )
    assert rolled_back.binding.version == "1.0.0"
    assert second.install_path.is_dir()


@pytest.mark.asyncio
async def test_uninstall_unbinds_but_retains_version_for_recovery(tmp_path) -> None:
    publisher = _FakePublisher()
    manager, _store = await _manager(tmp_path, publisher=publisher)
    source = _write_pack(tmp_path / "v1", version="1.0.0", revision="r1")
    installed = await manager.install(
        source,
        scope="user",
        scope_key="default",
        idempotency_key="install",
        expected_pack_id="godot",
    )
    removed = await manager.uninstall(
        pack_id="godot",
        scope="user",
        scope_key="default",
        idempotency_key="uninstall",
    )
    assert not removed.binding.active
    assert removed.files_retained
    assert installed.install_path.is_dir()
    assert publisher.stopped


class _Crash(BaseException):
    pass


@pytest.mark.asyncio
async def test_crash_after_catalog_swap_leaves_intent_for_reconciliation(
    tmp_path,
) -> None:
    def crash(point: str) -> None:
        if point == "after:catalog_swapped":
            raise _Crash()

    publisher = _FakePublisher()
    manager, store = await _manager(
        tmp_path, publisher=publisher, fault_injector=crash
    )
    source = _write_pack(tmp_path / "v1", version="1.0.0", revision="r1")
    with pytest.raises(_Crash):
        await manager.install(
            source,
            scope="user",
            scope_key="default",
            idempotency_key="crash-install",
            expected_pack_id="godot",
        )
    operation_id = _hash("capability:crash-install")
    operation = await store.get_operation(operation_id)
    assert operation is not None
    assert (operation.phase, operation.status) == ("catalog_swapped", "running")
    assert (await store.state()).pending_publish_count == 1
    assert await store.get_binding("user", "default", "godot") is None

    recovered = await manager.recover()
    assert len(recovered) == 1
    assert recovered[0].status == "failed"
    assert (await store.state()).pending_publish_count == 0


@pytest.mark.asyncio
async def test_recovery_completes_published_catalog_swap_with_original_cas(
    tmp_path,
) -> None:
    def crash(point: str) -> None:
        if point == "after:catalog_swapped":
            raise _Crash()

    publisher = _FakePublisher()
    publisher.reconcile_status = "published"
    manager, store = await _manager(
        tmp_path, publisher=publisher, fault_injector=crash
    )
    source = _write_pack(tmp_path / "v1", version="1.0.0", revision="r1")
    with pytest.raises(_Crash):
        await manager.install(
            source,
            scope="user",
            scope_key="default",
            idempotency_key="recover-published",
            expected_pack_id="godot",
        )

    recovered = await manager.recover()
    binding = await store.get_binding("user", "default", "godot")
    assert len(recovered) == 1
    assert (recovered[0].phase, recovered[0].status) == (
        "published",
        "succeeded",
    )
    assert binding is not None and binding.active
    assert (await store.state()).pending_publish_count == 0


@pytest.mark.asyncio
async def test_bound_recovery_preserves_companion_owner_and_manager_receipt(
    tmp_path,
) -> None:
    def crash(point: str) -> None:
        if point == "after:bound":
            raise _Crash()

    manager, store = await _manager(tmp_path, fault_injector=crash)
    source = _write_pack(
        tmp_path / "companion-recovery",
        version="1.0.0",
        revision="companion-r1",
    )
    with pytest.raises(_Crash):
        await manager.install(
            source,
            scope="user",
            scope_key="profile",
            idempotency_key="companion-bound-recovery",
            expected_pack_id="godot",
            owner_key="companion:alice:1",
            expected_binding_generation=0,
            management_policy="user_managed",
        )

    recovered = await manager.recover()
    assert len(recovered) == 1
    assert (recovered[0].phase, recovered[0].status) == (
        "published",
        "succeeded",
    )
    binding = await store.get_binding(
        "user",
        "profile",
        "godot",
        owner_key="companion:alice:1",
    )
    assert binding is not None
    assert binding.management_policy == "user_managed"
    replay = await manager.install(
        source,
        scope="user",
        scope_key="profile",
        idempotency_key="companion-bound-recovery",
        expected_pack_id="godot",
        owner_key="companion:alice:1",
        expected_binding_generation=0,
        management_policy="user_managed",
    )
    assert replay.manager_receipt_hash
    assert replay.committed_owner_binding_set_stamp
    assert replay.process_projection_fingerprint


class _OrdinaryPublishFailure(_FakePublisher):
    async def publish(self, candidate, *, operation_id):
        del candidate, operation_id
        raise RuntimeError("publisher unavailable")


@pytest.mark.asyncio
async def test_ordinary_publish_error_becomes_recoverable_unknown(tmp_path) -> None:
    publisher = _OrdinaryPublishFailure()
    manager, store = await _manager(tmp_path, publisher=publisher)
    source = _write_pack(tmp_path / "v1", version="1.0.0", revision="r1")
    with pytest.raises(RuntimeError, match="publisher unavailable"):
        await manager.install(
            source,
            scope="user",
            scope_key="default",
            idempotency_key="ordinary-publish-failure",
            expected_pack_id="godot",
        )
    operation_id = _hash("capability:ordinary-publish-failure")
    operation = await store.get_operation(operation_id)
    assert operation is not None
    assert (operation.phase, operation.status, operation.ended_at) == (
        "publish_intent",
        "unknown",
        None,
    )

    recovered = await manager.recover()
    assert len(recovered) == 1
    assert recovered[0].status == "failed"
    assert (await store.state()).pending_publish_count == 0


@pytest.mark.asyncio
async def test_uninstall_bound_crash_recovery_stops_old_runtime(tmp_path) -> None:
    publisher = _FakePublisher()
    manager, _store = await _manager(tmp_path, publisher=publisher)
    source = _write_pack(tmp_path / "v1", version="1.0.0", revision="r1")
    await manager.install(
        source,
        scope="user",
        scope_key="default",
        idempotency_key="install-before-uninstall-crash",
        expected_pack_id="godot",
    )

    def crash(point: str) -> None:
        if point == "after:bound":
            raise _Crash()

    manager._fault_injector = crash
    with pytest.raises(_Crash):
        await manager.uninstall(
            pack_id="godot",
            scope="user",
            scope_key="default",
            idempotency_key="uninstall-crash",
        )
    assert publisher.stopped == []

    manager._fault_injector = None
    recovered = await manager.recover()
    assert len(recovered) == 1
    assert recovered[0].status == "succeeded"
    assert len(publisher.stopped) == 1


def _archive_pack(source_root: Path, archive_path: Path) -> None:
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_STORED,
    ) as archive:
        for item in sorted(source_root.rglob("*")):
            if item.is_file():
                archive.write(item, item.relative_to(source_root).as_posix())


@pytest.mark.asyncio
@pytest.mark.parametrize("source_type", ["local_archive", "companion_growth"])
async def test_archive_transport_accepts_legacy_local_manifest_identity(
    tmp_path: Path,
    source_type: str,
) -> None:
    source_root = tmp_path / source_type / "source"
    _write_pack(source_root, version="1.0.0", revision="archive-r1")
    archive_path = tmp_path / source_type / "pack.zip"
    _archive_pack(source_root, archive_path)
    request = PackSourceRequest(
        source_type=source_type,
        uri=str(archive_path),
        revision="archive-r1",
    )
    manager, store = await _manager(tmp_path / source_type)

    result = await manager.install(
        request,
        scope="user",
        scope_key="default",
        idempotency_key=f"install-{source_type}",
        expected_pack_id="godot",
    )

    assert result.operation.status == "succeeded"
    assert result.install_path.is_dir()
    binding = await store.get_binding("user", "default", "godot")
    assert binding is not None and binding.active


@pytest.mark.asyncio
async def test_install_supports_windows_length_immutable_pack_path(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "long-path-source"
    version = "1.0.0+g." + ("a" * 24) + "." + ("b" * 24)
    request = _write_pack(
        source_root,
        version=version,
        revision=version,
    )
    long_root = tmp_path / "e"
    manager, _store = await _manager(long_root)

    result = await manager.install(
        request,
        scope="user",
        scope_key="profile",
        idempotency_key="install-windows-length-pack-path",
        expected_pack_id="godot",
    )

    assert len(str(result.install_path)) > 260
    assert result.operation.status == "succeeded"
    assert windows_extended_path(result.install_path).is_dir()


@pytest.mark.asyncio
async def test_tamper_before_replace_fails_without_installed_root_or_binding(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    request = _write_pack(source_root, version="1.0.0", revision="r1")
    holder: dict[str, CapabilityPackManager] = {}

    def tamper(point: str) -> None:
        if point == "before:install_replace":
            operation_dirs = tuple(holder["manager"].layout.staging.iterdir())
            assert len(operation_dirs) == 1
            (operation_dirs[0] / "tools" / "main.py").write_text(
                "print('tampered')\n",
                encoding="utf-8",
            )

    manager, store = await _manager(tmp_path, fault_injector=tamper)
    holder["manager"] = manager

    with pytest.raises(
        CapabilityManagerError,
        match="validated_ref_materialized_mismatch",
    ) as caught:
        await manager.install(
            request,
            scope="user",
            scope_key="default",
            idempotency_key="tamper-before-replace",
            expected_pack_id="godot",
        )

    assert caught.value.code == "capability_package_limit_exceeded"
    assert await store.get_binding("user", "default", "godot") is None
    assert not any(manager.layout.packs.rglob("deskpet-pack.json"))
    assert not manager.layout.staging_path(
        _hash("capability:tamper-before-replace")
    ).exists()


class _RawPathSourceResolver:
    def resolve_declared_source(
        self, request: PackSourceRequest
    ) -> PackSourceRequest:
        return request

    async def stage(
        self, request: PackSourceRequest, destination: Path
    ) -> StagedPack:
        shutil.copytree(Path(request.uri), destination)
        return StagedPack(root=destination, source=request)


@pytest.mark.asyncio
async def test_manager_rejects_raw_staged_path_without_validated_ref(
    tmp_path: Path,
) -> None:
    request = _write_pack(
        tmp_path / "source",
        version="1.0.0",
        revision="r1",
    )
    manager, store = await _manager(
        tmp_path,
        source_resolver=_RawPathSourceResolver(),
    )

    with pytest.raises(CapabilityManagerError) as caught:
        await manager.install(
            request,
            scope="user",
            scope_key="default",
            idempotency_key="raw-path-rejected",
            expected_pack_id="godot",
        )

    assert caught.value.code == "validated_package_ref_required"
    assert await store.get_binding("user", "default", "godot") is None
    assert not any(manager.layout.packs.rglob("deskpet-pack.json"))


@pytest.mark.asyncio
async def test_manager_rejects_validated_ref_from_foreign_policy_baseline(
    tmp_path: Path,
) -> None:
    request = _write_pack(
        tmp_path / "source",
        version="1.0.0",
        revision="r1",
    )
    foreign_validator = CapabilityPackageValidator(
        CapabilityPackageLimitsV1(baseline_hash="f" * 64)
    )
    manager, store = await _manager(
        tmp_path,
        source_resolver=CapabilitySourceResolver(
            package_validator=foreign_validator
        ),
    )

    with pytest.raises(
        CapabilityManagerError,
        match="validated_ref_materialized_mismatch:baseline_hash",
    ) as caught:
        await manager.install(
            request,
            scope="user",
            scope_key="default",
            idempotency_key="foreign-policy-rejected",
            expected_pack_id="godot",
        )

    assert caught.value.code == "capability_package_limit_exceeded"
    assert await store.get_binding("user", "default", "godot") is None
    assert not any(manager.layout.packs.rglob("deskpet-pack.json"))
