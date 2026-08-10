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
# 2026-08-09 刷新：本 golden 停在 `0fcffcf` 之前，17 条里有 11 条陈旧。
#
# 成因：`0fcffcf`（2026-08-04「backend boots on macOS」）发现 11 个 pack manifest
# 带着**在另一台开发机上、按更早的文件字节**生成的 sha256，按当前字节重新生成了
# 它们；但本测试的期望值没跟着更新，于是从那天起一直红着。
#
# 刷新前独立核验过（不是"把实际值抄进来让它变绿"）：
#   逐个读 capability-packs/skill-*/deskpet-pack.json 的 files[].sha256，
#   与该文件实际字节的 sha256 比对 —— **17 个 pack 全部一致**。
#   即 pack 自身是自洽的，唯一停在旧值的就是这里。
#
# 重新生成方式（改动 pack 内容后照做）：
#   cd backend && PYTHONPATH=<repo>:$PWD .venv/bin/python -c "
#   from paths import first_party_capability_pack_roots
#   from deskpet.companion.skills import inventory_first_party_skill_packs
#   import sys; sys.path.insert(0,'.')
#   from tests.companion.test_skill_pack_adapter import ENV
#   for i in sorted(inventory_first_party_skill_packs(
#           first_party_capability_pack_roots(), environment=ENV),
#           key=lambda x: x.skill_id):
#       print(f'    \"{i.skill_id}\": (\"{i.manifest_hash}\", \"{i.content_hash}\"),')"
#
# ⚠️ 刷新前务必先确认 pack 的 manifest 与文件字节自洽——否则等于把一次真实的
# 内容漂移洗白成"预期值"，这条 golden 就白设了。
EXPECTED_IDENTITIES = {
    "deep-research": ("e28da8dd2dfb25f7d08e42b0c391cad1736e8d2ee2b1317bf0e73f0ce3959ea6", "f8d66c4075cd0e280a9ae3f606dd4608072a32fedd0fb6e4b49df3865fc87a2e"),
    "doc-edit": ("587163cbb2e9ebb1d56b59e77fbc9f3e5c471b452efdb75a6dc4b32f016709f1", "7dcd9a134e5cab267a82d46e83082ea7ea25408f5cbda6fa01ac149cf246bdee"),
    "excel-generate": ("a6ad4054c7e2da1b0c3e9ad860908d7754c97f3d3140ace735627402a9c5e125", "e48681682e28ece259abec1b0d8b2166cf3c545eb6020ef8f16056bc5ddeffd4"),
    "file-organize": ("6d55fd86025ef1b7686d7374744f50519af2e79e08891cf8c68c99584058b33d", "358da032713fabbfb7ac859106d9c4c5c881b756baa49e87ac8920fa3b8224b0"),
    "pdf-export": ("6eb1aad80c80e86a6fcdf43bc7da152667d2b8d06d7028cadb6990eae2d2a669", "9ef96ee041864bb6e897dd429203d3b0147b3ba288539319696a607b10e51563"),
    "ppt-generate": ("743f8da5d2bd2bc749fb228d51fa3f80390991d20c2dc3a37a34ab99e24194fa", "1c098280314a101b90f90227b8a37e2ebf69a94de02f3f7ba10462d543650d8d"),
    "ppt-tips": ("94b70fb13d466393425b9aa7b92cb6a7e3fd3df05d00bfc12a9f2143e7d20d7b", "8820771a49d623ba29018106e329898f49dd0657b84852c1073e9b9e3efb58b2"),
    "recall-yesterday": ("244847fb26364d965f0c52fbbdd10fb4974449fc90d0834fecb1d3872904f6b5", "0da6adbacfaf61a0b21df01e9c371562ae0387aba6f5e381e75f58014b220637"),
    "run-deskpet": ("572c8bc221f14c93b1dc3bbd004be2c9a7cd80b51e0cd110d8367ccbce6e7038", "2a789ff646f189421bdae03b88e53de0ef4fe257f433e811b27cccb471162b2e"),
    "screenshot-ocr": ("4fd64f9e19da9913488983afaf52025f3f097403bb0167d453655648705e2809", "0f77af2560233962827e315c115b2b636c68a91b0a7aaaf04795bb7053473255"),
    "source-check": ("2f88bf09ec7eae26bce949ad1009ae142f7a5dc3d923a94ae05378d5099593fc", "cbad6c32fbbcbac747b982b59e21f4c1dfc1551947450a80900aac32f75037cc"),
    "summarize-day": ("226811b0bac61ddef5663d46f39f8ee795fc5a2710b82f8196b96d94c9f8138e", "80f4f15a9eed81a034f875c25a816579e69f4bb851fe855dca3aedc93c419340"),
    "translate-doc": ("fe364ff884bb213aebb73bde9a29033f42ac3a85ce09b85d664b5d065f6b5d64", "bd2bb330f2992c91a79ad24a24bbcc105b82075124e00a3922b79838df489e35"),
    "verify-deskpet": ("c327f8188bfa833ffec906575e4da5b7db4b53ce6769d823800748ad32994de9", "db0ab801f184d6f5c9bf6211470dcfdfcca29bc2138201d5dc6ac477d0fb9b88"),
    "weather-report": ("6da299da0a0200ca3e501cf1495fdfcbd7109cdd9f9274997517b7cf0298b986", "ab1561167cc282e051332aa3ac7704127c53aaefbc5068ba34c7e09381ef2ff7"),
    "web-read": ("e2441eb26082bd1178b7304578a176262f5af8f9180465b86604d24f8fe672ee", "a49a8e2b5af9381ee3af2b9d2b5db9486cf7cb933c67941f21c5fdb34026e1b3"),
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
