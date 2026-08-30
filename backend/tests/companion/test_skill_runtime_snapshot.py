# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import hashlib
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.companion.skills import (
    ManagedSkillDiscoveryProjection,
    inventory_first_party_skill_packs,
)
from deskpet.skills.loader import SkillPackSnapshotResolver
from deskpet.skills.skill_matcher import SkillMatcher
from deskpet.tools.capabilities import (
    PreparedToolCapability,
    PreparedToolSet,
    ToolCapabilityRef,
    ToolExecutionContext,
    canonical_hash,
)
from deskpet.tools.prepared_snapshot import (
    dump_prepared_tool_set,
    intersect_prepared_skill_tools,
)
from paths import first_party_capability_pack_roots


class _VersionStore:
    def __init__(self, item) -> None:
        self.item = item
        self.calls: list[tuple[str, str, str]] = []

    async def get_version(self, pack_id: str, version: str, manifest_hash: str):
        self.calls.append((pack_id, version, manifest_hash))
        item = self.item
        if (pack_id, version, manifest_hash) != (
            item.pack_id,
            item.version,
            item.manifest_hash,
        ):
            return None
        return SimpleNamespace(
            descriptor=SimpleNamespace(
                capability_id=item.pack_id,
                version=item.version,
                manifest_hash=item.manifest_hash,
            ),
            install_path=item.pack_root,
        )


class _VersionStoreMany:
    def __init__(self, *items) -> None:
        self.items = {
            (item.pack_id, item.version, item.manifest_hash): item
            for item in items
        }
        self.calls: list[tuple[str, str, str]] = []

    async def get_version(self, pack_id: str, version: str, manifest_hash: str):
        key = (pack_id, version, manifest_hash)
        self.calls.append(key)
        item = self.items.get(key)
        if item is None:
            return None
        return SimpleNamespace(
            descriptor=SimpleNamespace(
                capability_id=item.pack_id,
                version=item.version,
                manifest_hash=item.manifest_hash,
            ),
            install_path=item.pack_root,
        )


def _copy_summarize_pack(tmp_path: Path):
    source = first_party_capability_pack_roots()[0] / "skill-summarize-day"
    root = tmp_path / "packs"
    root.mkdir()
    shutil.copytree(source, root / source.name)
    item = inventory_first_party_skill_packs([root])[0]
    projection = ManagedSkillDiscoveryProjection([item])
    return item, projection.resolve_selection("summarize-day")


def _copy_versioned_summarize_pack(
    tmp_path: Path,
    *,
    version: str,
    marker: str,
):
    source = first_party_capability_pack_roots()[0] / "skill-summarize-day"
    inventory_root = tmp_path / version
    target = inventory_root / source.name
    shutil.copytree(source, target)
    skill_path = target / "skills" / "summarize-day" / "SKILL.md"
    skill_path.write_text(
        skill_path.read_text("utf-8") + f"\n\n{marker}\n",
        encoding="utf-8",
    )
    manifest_path = target / "deskpet-pack.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["version"] = version
    manifest["source"]["revision"] = f"test-{version}"
    relative = "skills/summarize-day/SKILL.md"
    manifest["files"][0]["sha256"] = hashlib.sha256(
        skill_path.read_bytes()
    ).hexdigest()
    assert manifest["files"][0]["path"] == relative
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    item = inventory_first_party_skill_packs([inventory_root])[0]
    projection = ManagedSkillDiscoveryProjection([item])
    return item, projection.resolve_selection("summarize-day")


@pytest.mark.asyncio
async def test_snapshot_resolver_reads_exact_manager_root_and_hashes(
    tmp_path: Path,
) -> None:
    item, scope = _copy_summarize_pack(tmp_path)
    store = _VersionStore(item)
    resolver = SkillPackSnapshotResolver(version_store=store)

    resolved = await resolver.resolve_instruction(scope, ("today",))

    assert resolved.scope == scope
    assert "allowed-tools:" not in resolved.instruction
    assert "today" in resolved.instruction
    assert store.calls == [(item.pack_id, item.version, item.manifest_hash)]
    relative = item.skill_path.relative_to(item.pack_root).as_posix()
    assert await resolver.resolve_resource(scope, relative) == item.skill_path.read_bytes()


@pytest.mark.asyncio
async def test_snapshot_resolver_reads_declared_resource_relative_to_skill_entry(
    tmp_path: Path,
) -> None:
    item, _scope = _copy_summarize_pack(tmp_path)
    manifest_path = item.pack_root / "deskpet-pack.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    resource = item.pack_root / "skills" / "shared" / "config.md"
    resource.parent.mkdir(parents=True)
    resource.write_text("shared config", encoding="utf-8")
    manifest["files"].append(
        {
            "path": "skills/shared/config.md",
            "sha256": hashlib.sha256(resource.read_bytes()).hexdigest(),
        }
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    item = inventory_first_party_skill_packs([item.pack_root.parent])[0]
    scope = ManagedSkillDiscoveryProjection([item]).resolve_selection("summarize-day")
    resolver = SkillPackSnapshotResolver(version_store=_VersionStore(item))

    resolved_path, raw = await resolver.resolve_skill_resource(
        scope, "../shared/config.md"
    )

    assert resolved_path == "skills/shared/config.md"
    assert raw == b"shared config"


@pytest.mark.asyncio
async def test_snapshot_resolver_rejects_post_capture_mutation(
    tmp_path: Path,
) -> None:
    item, scope = _copy_summarize_pack(tmp_path)
    resolver = SkillPackSnapshotResolver(version_store=_VersionStore(item))
    item.skill_path.write_text(
        item.skill_path.read_text("utf-8") + "\nundeclared mutation",
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="mismatch"):
        await resolver.resolve_instruction(scope)


@pytest.mark.asyncio
async def test_manager_selection_switches_v1_to_v2_but_old_scope_stays_v1(
    tmp_path: Path,
) -> None:
    item_v1, scope_v1 = _copy_versioned_summarize_pack(
        tmp_path,
        version="1.0.0",
        marker="VERSION ONE",
    )
    item_v2, scope_v2 = _copy_versioned_summarize_pack(
        tmp_path,
        version="2.0.0",
        marker="VERSION TWO",
    )

    class _Selection:
        current = scope_v1

        def resolve_selection(self, name: str):
            assert name == "summarize-day"
            return self.current

    selection = _Selection()
    resolver = SkillPackSnapshotResolver(
        version_store=_VersionStoreMany(item_v1, item_v2),
        selection_source=selection,
    )

    assert "VERSION ONE" in await resolver.execute("summarize-day")
    selection.current = scope_v2
    assert "VERSION TWO" in await resolver.execute("summarize-day")
    old = await resolver.resolve_instruction(scope_v1)
    assert "VERSION ONE" in old.instruction
    assert "VERSION TWO" not in old.instruction


@pytest.mark.asyncio
async def test_manager_scope_identity_isolated_per_profile_owner(
    tmp_path: Path,
) -> None:
    item, _scope = _copy_summarize_pack(tmp_path)
    store = _VersionStore(item)
    resolver = SkillPackSnapshotResolver(version_store=store)

    owner_a = await resolver.resolve_scope(
        owner_key="profile-a",
        pack_id=item.pack_id,
        skill_id=item.skill_id,
        version=item.version,
        manifest_hash=item.manifest_hash,
    )
    owner_b = await resolver.resolve_scope(
        owner_key="profile-b",
        pack_id=item.pack_id,
        skill_id=item.skill_id,
        version=item.version,
        manifest_hash=item.manifest_hash,
    )

    assert owner_a.owner_key == "profile-a"
    assert owner_b.owner_key == "profile-b"
    assert owner_a.scope_hash != owner_b.scope_hash
    assert (await resolver.resolve_instruction(owner_a)).scope == owner_a
    assert (await resolver.resolve_instruction(owner_b)).scope == owner_b


@pytest.mark.asyncio
async def test_old_run_remount_uses_frozen_v1_after_live_selection_moves_to_v2(
    tmp_path: Path,
) -> None:
    from agent.agent_loop import AgentLoop

    item_v1, scope_v1 = _copy_versioned_summarize_pack(
        tmp_path,
        version="1.0.0",
        marker="REMOUNT VERSION ONE",
    )
    item_v2, scope_v2 = _copy_versioned_summarize_pack(
        tmp_path,
        version="2.0.0",
        marker="REMOUNT VERSION TWO",
    )

    class _Selection:
        current = scope_v2
        calls = 0

        def resolve_selection(self, _name: str):
            self.calls += 1
            return self.current

    selection = _Selection()
    resolver = SkillPackSnapshotResolver(
        version_store=_VersionStoreMany(item_v1, item_v2),
        selection_source=selection,
    )
    loop = AgentLoop.__new__(AgentLoop)
    loop.skill_loader = resolver
    loop._frozen_skill_remounts = {}
    loop._skills_used_order = ["summarize-day"]
    loop._skills_used_this_run = {"summarize-day"}
    durable_result = {
        **scope_v1.to_dict(),
        "instruction": "UNTRUSTED TOOL PAYLOAD BODY",
    }

    await loop._prepare_frozen_skill_remounts(
        [
            {
                "role": "tool",
                "name": "skill_invoke",
                "tool_call_id": "call-1",
                "content": json.dumps(durable_result),
            }
        ],
        prepared_context=None,
        external_feedback={},
    )
    mounted = loop._remount_skills([], "old-run")
    content = mounted[0]["content"]

    assert "REMOUNT VERSION ONE" in content
    assert "REMOUNT VERSION TWO" not in content
    assert "UNTRUSTED TOOL PAYLOAD BODY" not in content
    assert selection.calls == 0


def _capability(name: str, *, toolset: str = "core") -> PreparedToolCapability:
    schema = {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }
    ref = ToolCapabilityRef(
        capability_id=f"tool:{name}",
        name=name,
        toolset=toolset,
        source="builtin",
        description=name,
        schema_hash=canonical_hash(schema["function"]),
    )
    return PreparedToolCapability(ref=ref, canonical_schema=schema)


def _exact_fact(cap: PreparedToolCapability) -> dict[str, object]:
    return {
        "name": cap.ref.name,
        "stable_handler_id": f"builtin:{cap.ref.name}",
        "tool_spec_fingerprint": canonical_hash({"name": cap.ref.name}),
        "schema_hash": cap.ref.schema_hash,
        "execution_build_identity": {"provider": "builtin", "build": "test"},
        "dispatch_adapter_id": "builtin",
        "dispatch_adapter_version": "v1",
        "dispatch_adapter_fingerprint": canonical_hash(
            {"adapter": cap.ref.name}
        ),
        "effect_policy": None,
        "idempotency": "read_only",
    }


def test_skill_scope_only_narrows_base_prepared_tool_set(tmp_path: Path) -> None:
    _item, scope = _copy_summarize_pack(tmp_path)
    prepared = PreparedToolSet.create(
        scope_id="base-tools",
        revision=1,
        registry_revision=1,
        direct=(_capability("memory_recall"), _capability("shell_exec")),
        deferred=(),
        activated=(),
        denied_names=(),
        policy_fingerprint="policy",
        decisions=(),
    )

    narrowed = intersect_prepared_skill_tools(prepared, (scope,))

    assert narrowed.active_scope_ids == (scope.scope_id,)
    assert narrowed.effective_tool_names == ("memory_recall",)
    assert narrowed.prepared_tool_set.has_direct("memory_recall")
    assert not narrowed.prepared_tool_set.has_direct("shell_exec")
    assert len(narrowed.effective_tool_refs_hash) == 64


def test_exact_capture_missing_allowed_tool_fails_closed(tmp_path: Path) -> None:
    _item, scope = _copy_summarize_pack(tmp_path)
    memory = _capability("memory_recall")
    shell = _capability("shell_exec")
    prepared = PreparedToolSet.create(
        scope_id="base-tools",
        revision=1,
        registry_revision=1,
        direct=(memory, shell),
        deferred=(),
        activated=(),
        denied_names=(),
        policy_fingerprint="policy",
        decisions=(),
    )

    with pytest.raises(ValueError, match="missing"):
        intersect_prepared_skill_tools(
            prepared,
            (scope,),
            exact_tool_facts=(_exact_fact(shell),),
        )


def test_skill_matcher_cache_isolates_owner_version_and_hash() -> None:
    class _Embedder:
        def encode(self, _text):
            return [1.0, 0.0]

    base = {
        "name": "same",
        "description": "same description",
        "pack_id": "skill-same",
        "version": "1.0.0",
        "manifest_hash": "a" * 64,
        "content_hash": "b" * 64,
    }
    owner_a = {**base, "owner_key": "owner-a"}
    owner_b = {**base, "owner_key": "owner-b"}
    version_b = {
        **base,
        "owner_key": "owner-a",
        "version": "2.0.0",
        "content_hash": "c" * 64,
    }
    matcher = SkillMatcher(_Embedder())
    matcher.build([owner_a, owner_b, version_b])

    assert len(matcher._cache) == 3
    assert matcher.match("query", [owner_a])
    assert matcher.match("query", [owner_b])
    assert matcher.match("query", [version_b])


@pytest.mark.asyncio
async def test_skill_invoke_emits_activation_intent_not_fake_receipt(caplog) -> None:
    from deskpet.tools import skill_tools

    caplog.set_level("INFO", logger="deskpet.tools.skill_tools")
    snapshot_ref = "snapshot-1"

    class _Resolver:
        async def resolve_frozen_instruction(self, **_kwargs):
            scope_hash = "d" * 64
            ref_payload = {
                "name": "memory_recall",
                "stable_handler_id": "builtin:memory_recall",
                "tool_spec_fingerprint": "a" * 64,
                "schema_hash": "c" * 64,
                "execution_build_identity": {
                    "provider": "builtin",
                    "build": "test",
                },
                "dispatch_adapter_id": "builtin",
                "dispatch_adapter_version": "v1",
                "dispatch_adapter_fingerprint": "b" * 64,
                "effect_policy": None,
                "idempotency": "read_only",
            }
            ref_hash = canonical_hash(ref_payload)
            return {
                "owner_key": "owner",
                "pack_id": "skill-demo",
                "skill_id": "demo",
                "version": "1.0.0",
                "manifest_hash": "a" * 64,
                "content_hash": "b" * 64,
                "instruction": "frozen body",
                "allowed_tools": ["memory_recall"],
                "scope_id": f"skill-scope:{scope_hash}",
                "scope_hash": scope_hash,
                "capability_snapshot_ref": snapshot_ref,
                "run_catalog_content_stamp": "catalog-stamp",
                "allowed_tool_refs": [ref_payload],
                "effective_tool_ref_hashes": [ref_hash],
                "effective_tool_refs_hash": "f" * 64,
            }

    skill_tools.bind(skill_resolver=_Resolver())
    try:
        raw = await skill_tools._handle(
            {"skill_name": "demo"},
            ToolExecutionContext(
                scope_id="base",
                session_id="session",
                request_id="request",
                run_id="run",
                root_run_id="run",
                capability_snapshot_ref=snapshot_ref,
            ),
        )
    finally:
        skill_tools.bind(skill_resolver=None)
    result = json.loads(raw)
    assert result["ok"] is True
    assert result["scope_activation"]["scope_id"] == "skill-scope:" + "d" * 64
    assert result["scope_activation"]["activation_id"].startswith(
        "skill-scope-activation:"
    )
    assert "loaded_skill_body_count=1" in caplog.text
    assert "instruction_content_hash=" in caplog.text
    assert "frozen body" not in caplog.text
    assert "activation_receipt" not in result


@pytest.mark.asyncio
async def test_skill_invoke_reads_only_frozen_packaged_support_resource(caplog) -> None:
    from deskpet.tools import skill_tools

    caplog.set_level("INFO", logger="deskpet.tools.skill_tools")
    snapshot_ref = "snapshot-resource"

    class _Resolver:
        async def resolve_frozen_resource(self, **kwargs):
            assert kwargs == {
                "run_id": "run-resource",
                "skill_name": "plan-bs",
                "relative_path": "../plan-test/config.md",
            }
            return {
                "skill_id": "plan-bs",
                "resource_path": "../plan-test/config.md",
                "resolved_path": "skills/plan-test/config.md",
                "content": "shared config",
                "content_hash": "a" * 64,
                "capability_snapshot_ref": snapshot_ref,
            }

    skill_tools.bind(skill_resolver=_Resolver())
    try:
        raw = await skill_tools._handle(
            {
                "skill_name": "plan-bs",
                "resource_path": "../plan-test/config.md",
            },
            ToolExecutionContext(
                scope_id="base",
                session_id="session",
                request_id="request",
                run_id="run-resource",
                root_run_id="run-resource",
                capability_snapshot_ref=snapshot_ref,
            ),
        )
    finally:
        skill_tools.bind(skill_resolver=None)

    result = json.loads(raw)
    assert result["ok"] is True
    assert result["content"] == "shared config"
    assert result["resolved_path"] == "skills/plan-test/config.md"
    assert "sdk_skill_resource_loaded" in caplog.text


@pytest.mark.asyncio
async def test_skill_invoke_logs_privacy_safe_resolution_failure(caplog) -> None:
    from deskpet.tools import skill_tools

    class _Resolver:
        async def resolve_frozen_instruction(self, **_kwargs):
            raise RuntimeError("sdk_frozen_skill_metadata_invalid")

    skill_tools.bind(skill_resolver=_Resolver())
    try:
        with pytest.raises(RuntimeError, match="sdk_frozen_skill_metadata_invalid"):
            await skill_tools._handle(
                {"skill_name": "translate-doc", "arguments": ["SECRET-BODY"]},
                ToolExecutionContext(
                    scope_id="base",
                    session_id="session",
                    request_id="request",
                    run_id="sdk-run",
                    root_run_id="sdk-run",
                    capability_snapshot_ref="snapshot-1",
                ),
            )
    finally:
        skill_tools.bind(skill_resolver=None)

    assert "sdk_skill_instruction_load_failed" in caplog.text
    assert "skill=translate-doc" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "stable_code=sdk_frozen_skill_metadata_invalid" in caplog.text
    assert "SECRET-BODY" not in caplog.text


@pytest.mark.asyncio
async def test_skill_invoke_rejects_low_risk_scope_widening_control() -> None:
    from deskpet.tools import skill_tools

    snapshot_ref = "snapshot-low-risk"

    class _Resolver:
        async def resolve_frozen_instruction(self, **_kwargs):
            scope_hash = "e" * 64
            ref_payload = {
                "name": "workflow_spawn",
                "stable_handler_id": "builtin:workflow_spawn",
                "tool_spec_fingerprint": "a" * 64,
                "schema_hash": "c" * 64,
                "execution_build_identity": {
                    "provider": "builtin",
                    "build": "test",
                },
                "dispatch_adapter_id": "builtin",
                "dispatch_adapter_version": "v1",
                "dispatch_adapter_fingerprint": "b" * 64,
                "effect_policy": None,
                "idempotency": "read_only",
            }
            return {
                "owner_key": "owner",
                "pack_id": "skill-low-risk",
                "skill_id": "low-risk",
                "version": "1.0.0",
                "manifest_hash": "a" * 64,
                "content_hash": "b" * 64,
                "instruction": "Use only the declared low-risk scope.",
                "allowed_tools": ["workflow_spawn"],
                "scope_id": f"skill-scope:{scope_hash}",
                "scope_hash": scope_hash,
                "capability_snapshot_ref": snapshot_ref,
                "run_catalog_content_stamp": "catalog-stamp",
                "allowed_tool_refs": [ref_payload],
                "effective_tool_ref_hashes": [canonical_hash(ref_payload)],
                "effective_tool_refs_hash": "f" * 64,
            }

    skill_tools.bind(skill_resolver=_Resolver())
    try:
        raw = await skill_tools._handle(
            {"skill_name": "low-risk"},
            ToolExecutionContext(
                scope_id="base",
                session_id="session",
                request_id="request",
                run_id="run",
                root_run_id="run",
                capability_snapshot_ref=snapshot_ref,
            ),
        )
    finally:
        skill_tools.bind(skill_resolver=None)

    assert json.loads(raw) == {
        "ok": False,
        "error": "frozen_skill_scope_widening_control",
    }


@pytest.mark.asyncio
async def test_run_catalog_resolver_uses_catalog_version_not_live_inventory(
    tmp_path: Path,
) -> None:
    from deskpet.capabilities.run_catalog import FirstPartyFrozenSkillResolver

    item, _scope = _copy_summarize_pack(tmp_path)
    prepared = PreparedToolSet.create(
        scope_id="base-tools",
        revision=1,
        registry_revision=1,
        direct=(_capability("memory_recall"), _capability("shell_exec")),
        deferred=(),
        activated=(),
        denied_names=(),
        policy_fingerprint="policy",
        decisions=(),
    )
    row = {
        "snapshot_ref": "snapshot-1",
        "run_catalog_content_stamp": "catalog-stamp",
        "selected_owner_key": "owner-a",
        "pack_id": item.pack_id,
        "version": item.version,
        "manifest_hash": item.manifest_hash,
        "prepared_tool_set_envelope_json": json.dumps(
            {
                "prepared": dump_prepared_tool_set(prepared),
                "exact_tools": [
                    {
                        "name": cap.ref.name,
                        "stable_handler_id": f"builtin:{cap.ref.name}",
                        "tool_spec_fingerprint": canonical_hash(
                            {"name": cap.ref.name}
                        ),
                        "schema_hash": cap.ref.schema_hash,
                        "execution_build_identity": {
                            "provider": "builtin",
                            "build": "test",
                        },
                        "dispatch_adapter_id": "builtin",
                        "dispatch_adapter_version": "v1",
                        "dispatch_adapter_fingerprint": canonical_hash(
                            {"adapter": cap.ref.name}
                        ),
                        "effect_policy": None,
                        "idempotency": "read_only",
                    }
                    for cap in prepared.direct
                ],
            }
        ),
    }

    class _Cursor:
        async def fetchall(self):
            return [row]

    class _Db:
        async def execute(self, _sql, _params):
            return _Cursor()

    class _Store(_VersionStore):
        @asynccontextmanager
        async def read_connection(self):
            yield _Db()

    resolver = FirstPartyFrozenSkillResolver(
        store=_Store(item),
        inventory=(),  # proves the process's current inventory is not authority
    )
    result = await resolver.resolve_frozen_instruction(
        run_id="run-1",
        skill_name="summarize-day",
        arguments=(),
    )

    assert result["scope_id"].startswith("skill-scope:")
    assert result["allowed_tools"] == ["memory_recall"]
    assert [item["name"] for item in result["allowed_tool_refs"]] == [
        "memory_recall"
    ]
    assert len(result["effective_tool_ref_hashes"]) == 1
