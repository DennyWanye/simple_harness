from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from deskpet.capabilities.manifest import (
    PackEnvironment,
    PackManifestError,
    load_and_validate_pack,
    parse_pack_manifest,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_valid_pack(root: Path) -> dict[str, object]:
    skill = root / "skills" / "游戏 helper" / "SKILL.md"
    entry = root / "tools" / "godot" / "main.py"
    schema = root / "tools" / "godot" / "check.schema.json"
    for path in (skill, entry, schema):
        path.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("# Godot\n", encoding="utf-8")
    entry.write_text("def main(value):\n    return value\n", encoding="utf-8")
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    manifest: dict[str, object] = {
        "schema_version": 1,
        "id": "godot",
        "name": "Godot",
        "version": "1.0.0",
        "source": {
            "type": "local",
            "uri": str(root),
            "revision": "fixture-1",
        },
        "compatibility": {
            "deskpet": ">=0.5.0",
            "os": ["windows"],
            "architectures": ["x86_64"],
            "python": ">=3.11",
        },
        "entries": {
            "skills": [{"path": "skills/游戏 helper/SKILL.md"}],
            "tools": [
                {
                    "id": "project_check",
                    "provider_name": "godot__project_check",
                    "runtime": "deskpet-json-tool-v1",
                    "execution_profile": "native-adapter",
                    "input_views": [],
                    "entry": "tools/godot/main.py",
                    "schema": "tools/godot/check.schema.json",
                    "healthcheck": "project_check",
                }
            ],
            "mcp_servers": [],
        },
        "permissions": ["filesystem_read", "filesystem_write"],
        "effects": ["read_only", "staged_file"],
        "dependencies": {"python": [], "commands": []},
        "files": [
            {"path": "skills/游戏 helper/SKILL.md", "sha256": _sha(skill)},
            {"path": "tools/godot/main.py", "sha256": _sha(entry)},
            {"path": "tools/godot/check.schema.json", "sha256": _sha(schema)},
        ],
        "uninstall": {
            "stop_servers": True,
            "remove_environment_when_unreferenced": True,
        },
    }
    (root / "deskpet-pack.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def _environment() -> PackEnvironment:
    return PackEnvironment(
        deskpet_version="0.6.0",
        os="windows",
        architecture="x86_64",
        python_version="3.11.9",
    )


def test_valid_pack_with_unicode_and_spaces(tmp_path: Path) -> None:
    manifest = _write_valid_pack(tmp_path)
    result = load_and_validate_pack(tmp_path, environment=_environment())
    assert result.manifest.id == "godot"
    assert result.descriptor.provider_tool_names == ("godot__project_check",)
    assert result.descriptor.health == "healthy"
    assert result.manifest.manifest_hash == hashlib.sha256(
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def test_pack_validation_supports_windows_paths_longer_than_max_path(
    tmp_path: Path,
) -> None:
    segment_length = max(32, 235 - len(str(tmp_path)) - len("nested-") - 1)
    root = tmp_path / ("nested-" + "x" * segment_length)
    assert len(str(root)) < 260
    assert len(str(root / "skills" / "娓告垙 helper" / "SKILL.md")) > 260
    io_root = Path("\\\\?\\" + str(root)) if os.name == "nt" else root
    io_root.mkdir(parents=True)
    _write_valid_pack(io_root)

    result = load_and_validate_pack(root, environment=_environment())

    assert result.manifest.id == "godot"
    assert result.root == root.resolve()


def test_path_traversal_rejected_before_filesystem_access(tmp_path: Path) -> None:
    manifest = _write_valid_pack(tmp_path)
    manifest["files"] = [{"path": "../escape.py", "sha256": "0" * 64}]
    with pytest.raises(PackManifestError) as caught:
        parse_pack_manifest(manifest)
    assert caught.value.code == "path_traversal"


def test_hash_mismatch_and_extra_file_fail_closed(tmp_path: Path) -> None:
    manifest = _write_valid_pack(tmp_path)
    files = manifest["files"]
    assert isinstance(files, list)
    first = files[0]
    assert isinstance(first, dict)
    first["sha256"] = "0" * 64
    (tmp_path / "deskpet-pack.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(PackManifestError) as caught:
        load_and_validate_pack(tmp_path, environment=_environment())
    assert caught.value.code == "hash_mismatch"

    manifest = _write_valid_pack(tmp_path)
    (tmp_path / "surprise.txt").write_text("not declared", encoding="utf-8")
    with pytest.raises(PackManifestError) as caught:
        load_and_validate_pack(tmp_path, environment=_environment())
    assert caught.value.code == "pack_file_unlisted"


def test_provider_name_must_be_exact_and_not_reserved(tmp_path: Path) -> None:
    manifest = _write_valid_pack(tmp_path)
    entries = manifest["entries"]
    assert isinstance(entries, dict)
    tools = entries["tools"]
    assert isinstance(tools, list) and isinstance(tools[0], dict)
    tools[0]["provider_name"] = "godot.project_check"
    with pytest.raises(PackManifestError) as caught:
        parse_pack_manifest(manifest)
    assert caught.value.code == "provider_name_mismatch"

    tools[0]["provider_name"] = "godot__project_check"
    with pytest.raises(PackManifestError) as caught:
        parse_pack_manifest(
            manifest, reserved_tool_names={"godot__project_check"}
        )
    assert caught.value.code == "reserved_tool_name"


def test_permission_effect_closure_and_compatibility(tmp_path: Path) -> None:
    manifest = _write_valid_pack(tmp_path)
    manifest["effects"] = ["read_only"]
    with pytest.raises(PackManifestError) as caught:
        parse_pack_manifest(manifest)
    assert caught.value.code == "permission_effect_closure"

    _write_valid_pack(tmp_path)
    with pytest.raises(PackManifestError) as caught:
        load_and_validate_pack(
            tmp_path,
            environment=PackEnvironment(
                deskpet_version="0.6.0",
                os="linux",
                architecture="x86_64",
                python_version="3.11.9",
            ),
        )
    assert caught.value.code == "incompatible_os"
