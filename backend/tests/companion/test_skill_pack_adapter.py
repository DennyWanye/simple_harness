from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import jsonschema
import pytest

from deskpet.capabilities.manifest import (
    PackEnvironment,
    PackManifestError,
    load_and_validate_pack,
    parse_pack_manifest,
)
from deskpet.companion.skills import (
    ManagedSkillDiscoveryProjection,
    inventory_first_party_skill_packs,
)
from deskpet.skills.loader import SkillLoader
from paths import first_party_capability_pack_roots

ENV = PackEnvironment(
    deskpet_version="0.6.0",
    os="windows",
    architecture="x86_64",
    python_version="3.11",
)
EXPECTED_SKILLS = {
    "deep-research", "doc-edit", "excel-generate", "file-organize",
    "pdf-export", "ppt-generate", "ppt-tips", "recall-yesterday",
    "run-deskpet", "screenshot-ocr", "source-check", "summarize-day",
    "translate-doc", "verify-deskpet", "weather-report", "web-read",
    "windows-path-debug",
}
EXPECTED_IDENTITIES = {
    "deep-research": ("e28da8dd2dfb25f7d08e42b0c391cad1736e8d2ee2b1317bf0e73f0ce3959ea6", "f8d66c4075cd0e280a9ae3f606dd4608072a32fedd0fb6e4b49df3865fc87a2e"),
    "doc-edit": ("0ddc047d8340d6d47fe79479704b94ff9b6c4101b509c9e2c998e715258c7649", "e2f3af05b2fa52522169b9bab4848e9a4987a48a993f5f89476b78d8adb17f44"),
    "excel-generate": ("1852b97510049db2c63daaa06f70b854b0ab521dd0a66037922e7f2d0e0a401b", "919c5cc01be833cf56baddf3e6f6fa64c362140e825ae12b3490a3ac2c9bab48"),
    "file-organize": ("6980e509fb80c6d6d2896b52d4574245153b612b7343317e158530387928691f", "b4161e88250ea960ccd1fe7f840c7aa0f1ae3ec5496f3bc605d81c455b72eeb4"),
    "pdf-export": ("3cc9ce2d97b54e7f78cb17a2990b86e48a3b319123c99804c4bb80dca8a346d3", "171f7dfb67bcd14667812f60a04c115d06f4dfbfe5fae3b4b47c5bd151e9c310"),
    "ppt-generate": ("ec9a77349b41978c4942ef7e83d36545be6a05f4c16e7793dd4e79d497d5a504", "40e7743248d5a96e4cd79d7e513bce7dbf42df5ba80ccbfb47451f1cf8bda37c"),
    "ppt-tips": ("94b70fb13d466393425b9aa7b92cb6a7e3fd3df05d00bfc12a9f2143e7d20d7b", "8820771a49d623ba29018106e329898f49dd0657b84852c1073e9b9e3efb58b2"),
    "recall-yesterday": ("12cb33891b418f852dffc969575797a068e5240efbe2a95d9e8992cd951b2e82", "05d373cb42e5b70367685d2cf254c814978a7eb87d6e11612ff5dfc91b18c2e7"),
    "run-deskpet": ("572c8bc221f14c93b1dc3bbd004be2c9a7cd80b51e0cd110d8367ccbce6e7038", "2a789ff646f189421bdae03b88e53de0ef4fe257f433e811b27cccb471162b2e"),
    "screenshot-ocr": ("95be9f635e96ad7be531d86d76b42700268f8b256dbbaa6626b25572ae9825b1", "aa12029b45ca03a67722450fc8750b0e2774343c1847aad8a9feb3a5d767d3fc"),
    "source-check": ("2f88bf09ec7eae26bce949ad1009ae142f7a5dc3d923a94ae05378d5099593fc", "cbad6c32fbbcbac747b982b59e21f4c1dfc1551947450a80900aac32f75037cc"),
    "summarize-day": ("ebfa762eebe587d0e2de71c461c4507f27b27d09ea9edf04aed377817a218a31", "d14055ef19d25abd86af410a7084b952cb2a003b05fd90a5c517d6e5cd901240"),
    "translate-doc": ("57f864c06b00d5ce583baa22f0c7384465d56b9e97bbff16b5900bc0ccf6ef59", "2518517a192e4667dae1c8cdc574788f007e005615611a80fd384c7b34a5e739"),
    "verify-deskpet": ("c327f8188bfa833ffec906575e4da5b7db4b53ce6769d823800748ad32994de9", "db0ab801f184d6f5c9bf6211470dcfdfcca29bc2138201d5dc6ac477d0fb9b88"),
    "weather-report": ("a55dafd6652307a1d4652a35a7d950f417880a0ece79aaf9c2ae2bda3298c009", "6cdfafa0ce5e28b7ba1012d34447f9e3043e305fec5553319e9be35b5bca8530"),
    "web-read": ("ceb1ceac24485115e581ac0b7a3355d07adfed231f70861dc0362695b99365bf", "2d56e5703f40c4bb11a401692a0165eb7c8a88c9ea6b39a49af67c200a0a7f2f"),
    "windows-path-debug": ("f5342ddd6750ef6af89f6901b48a14b56166caa5b1d4a2839d94c9779bc7791b", "ef3ef2c5d51aac257e4d835aa3fc5e4d19bb98bf01731230e61340c0c7ecbe8a"),
}


def test_shipped_skill_inventory_is_exact_and_hash_validated() -> None:
    inventory = inventory_first_party_skill_packs(
        first_party_capability_pack_roots(), environment=ENV
    )
    assert {item.skill_id for item in inventory} == EXPECTED_SKILLS
    assert len({item.pack_id for item in inventory}) == 17
    assert all(item.pack_id == f"skill-{item.skill_id}" for item in inventory)
    assert all(len(item.content_hash) == len(item.manifest_hash) == 64 for item in inventory)
    assert {
        item.skill_id: (item.manifest_hash, item.content_hash)
        for item in inventory
    } == EXPECTED_IDENTITIES
    summarize = next(item for item in inventory if item.skill_id == "summarize-day")
    assert summarize.allowed_tools == ("memory_recall",)


def test_legacy_v1_pack_remains_readable_and_unknown_schema_fails_closed() -> None:
    root = first_party_capability_pack_roots()[0]
    result = load_and_validate_pack(root / "godot", environment=ENV)
    assert result.manifest.schema_version == 1
    assert result.manifest.workflows == ()
    raw = json.loads((root / "godot" / "deskpet-pack.json").read_text("utf-8"))
    raw["schema_version"] = 99
    with pytest.raises(PackManifestError, match="unsupported schema_version"):
        parse_pack_manifest(raw)
    raw["schema_version"] = "2"
    with pytest.raises(PackManifestError, match="must be an integer"):
        parse_pack_manifest(raw)


def test_loader_rejects_script_payload_and_has_no_execution_surface(tmp_path: Path) -> None:
    skill = tmp_path / "danger"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: danger\ndescription: no\nauthor: test\nversion: 1.0.0\n"
        "requires_script: true\n---\nbody\n",
        encoding="utf-8",
    )
    (skill / "script.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    loader = SkillLoader(skill_dirs=[tmp_path])
    loader.reload()
    assert loader.get("danger") is None
    assert not hasattr(loader, "invoke_script")
    source = Path(__file__).parents[2] / "deskpet" / "skills" / "loader.py"
    assert "create_subprocess_exec" not in source.read_text("utf-8")


def test_legacy_builtin_tree_no_longer_owns_shipped_skill_content() -> None:
    root = Path(__file__).parents[2] / "deskpet" / "skills" / "builtin"
    assert list(root.rglob("SKILL.md")) == []


def test_managed_projection_exposes_typed_immutable_selection() -> None:
    inventory = inventory_first_party_skill_packs(
        first_party_capability_pack_roots(), environment=ENV
    )
    projection = ManagedSkillDiscoveryProjection(inventory)
    selection = projection.resolve_selection("summarize-day")
    assert selection.owner_key == "builtin"
    assert selection.allowed_tools == ("memory_recall",)
    assert len(selection.scope_hash) == 64
    meta = projection.get("summarize-day")
    assert meta is not None
    assert (meta.owner_key, meta.pack_id, meta.manifest_hash, meta.content_hash) == (
        selection.owner_key,
        selection.pack_id,
        selection.manifest_hash,
        selection.content_hash,
    )
    assert "allowed-tools:" not in projection.read_body("summarize-day")


def test_v2_schema_validates_offline_without_external_refs() -> None:
    schema_path = (
        Path(__file__).parents[2]
        / "deskpet"
        / "capabilities"
        / "schemas"
        / "deskpet-pack-v2.schema.json"
    )
    schema = json.loads(schema_path.read_text("utf-8"))
    assert not any(
        isinstance(value, str) and "deskpet-pack-v1" in value
        for value in _walk_json(schema)
    )
    validator = jsonschema.Draft202012Validator(schema)
    for manifest_path in (
        first_party_capability_pack_roots()[0].glob("skill-*/deskpet-pack.json")
    ):
        validator.validate(json.loads(manifest_path.read_text("utf-8")))


def _walk_json(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_json(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json(item)
    else:
        yield value


def test_dev_and_frozen_inventory_have_identical_hashes(
    monkeypatch, tmp_path: Path
) -> None:
    import paths

    dev = inventory_first_party_skill_packs(
        first_party_capability_pack_roots(), environment=ENV
    )
    frozen_root = tmp_path / "capability-packs"
    frozen_root.mkdir()
    for source in first_party_capability_pack_roots()[0].glob("skill-*"):
        shutil.copytree(source, frozen_root / source.name)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", str(tmp_path), raising=False)
    frozen = inventory_first_party_skill_packs(
        paths.first_party_capability_pack_roots(), environment=ENV
    )
    assert [
        (item.skill_id, item.manifest_hash, item.content_hash) for item in dev
    ] == [
        (item.skill_id, item.manifest_hash, item.content_hash) for item in frozen
    ]


def test_skill_body_canonical_tool_refs_are_declared() -> None:
    tool_manifest = json.loads(
        (
            Path(__file__).parents[2]
            / "deskpet"
            / "tools"
            / "tool_effect_policy_manifest.json"
        ).read_text("utf-8")
    )
    known = {
        str(item["tool_name"]) for item in tool_manifest["handlers"]
    }
    known.add("mcp_call")
    projection = ManagedSkillDiscoveryProjection(
        inventory_first_party_skill_packs(
            first_party_capability_pack_roots(), environment=ENV
        )
    )
    for meta in projection.all():
        refs = {
            match.group(1)
            for match in re.finditer(r"`([A-Za-z][A-Za-z0-9_-]{0,63})`", projection.read_body(meta.name))
            if match.group(1) in known
        }
        assert refs <= set(meta.allowed_tools), (meta.name, refs - set(meta.allowed_tools))
