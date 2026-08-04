#!/usr/bin/env python3
"""Generate/check the checked-in core execution build manifest."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


def _load_identity_helpers(repo_root: Path) -> tuple[Any, Any]:
    module_path = (
        repo_root / "backend" / "deskpet" / "tools" / "build_identity.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_deskpet_execution_build_identity", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load build_identity.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.canonical_hash, module.canonical_json_bytes


def _read_object(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {path}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _rows(manifest: Mapping[str, Any], name: str) -> dict[str, Mapping[str, Any]]:
    if int(manifest.get("schema_version", 0)) != 1:
        raise ValueError(f"{name} schema_version must be 1")
    raw = manifest.get("handlers")
    if not isinstance(raw, list):
        raise ValueError(f"{name}.handlers must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    tools: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError(f"{name} contains a malformed handler")
        handler_id = str(item.get("handler_id") or "")
        tool_name = str(item.get("tool_name") or "")
        if not handler_id or handler_id in result:
            raise ValueError(f"{name} contains a duplicate/empty handler id")
        if not tool_name or tool_name in tools:
            raise ValueError(f"{name} contains a duplicate/empty tool name")
        result[handler_id] = item
        tools.add(tool_name)
    return result


def _has_reparse_component(root: Path, candidate: Path) -> bool:
    relative = candidate.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            return True
        attributes = int(getattr(info, "st_file_attributes", 0))
        reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        if attributes & reparse_flag:
            return True
    return False


def _resolve_artifact(repo_root: Path, raw_path: str) -> tuple[str, Path]:
    if not raw_path or "\\" in raw_path:
        raise ValueError(f"artifact path must be canonical POSIX: {raw_path!r}")
    pure = PurePosixPath(raw_path)
    if pure.is_absolute() or ":" in raw_path or any(
        part in {"", ".", ".."} for part in pure.parts
    ):
        raise ValueError(f"artifact path escapes repo root: {raw_path!r}")
    candidate = repo_root.joinpath(*pure.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repo_root)
    except (OSError, ValueError) as exc:
        raise ValueError(f"artifact is missing or outside repo: {raw_path}") from exc
    if _has_reparse_component(repo_root, candidate):
        raise ValueError(f"artifact path traverses a reparse point: {raw_path}")
    if not resolved.is_file():
        raise ValueError(f"artifact is not a regular file: {raw_path}")
    return pure.as_posix(), resolved


def generate(
    *,
    repo_root: Path,
    sources_path: Path,
    effect_path: Path,
) -> bytes:
    repo_root = repo_root.resolve(strict=True)
    canonical_hash, canonical_json_bytes = _load_identity_helpers(repo_root)
    sources = _read_object(sources_path)
    effects = _read_object(effect_path)
    source_rows = _rows(sources, sources_path.name)
    effect_rows = _rows(effects, effect_path.name)
    if set(source_rows) != set(effect_rows):
        raise ValueError("execution source/effect handler sets must be identical")

    common_artifacts = sources.get("common_artifacts", ())
    if not isinstance(common_artifacts, list):
        raise ValueError("common_artifacts must be a list")
    if len(set(str(item) for item in common_artifacts)) != len(common_artifacts):
        raise ValueError("common_artifacts contains duplicates")
    handlers: list[dict[str, Any]] = []
    for handler_id in sorted(source_rows):
        source = source_rows[handler_id]
        effect = effect_rows[handler_id]
        tool_name = str(source["tool_name"])
        if str(effect["tool_name"]) != tool_name:
            raise ValueError(f"effect tool mismatch for {handler_id}")
        if str(source.get("authority_phase") or "") not in {
            "both",
            "legacy",
            "companion",
        }:
            raise ValueError(f"invalid authority_phase for {handler_id}")
        if str(source.get("lifecycle") or "") not in {"active", "planned"}:
            raise ValueError(f"invalid lifecycle for {handler_id}")
        handler_artifacts = source.get("artifacts")
        if not isinstance(handler_artifacts, list) or not handler_artifacts:
            raise ValueError(f"artifacts missing for {handler_id}")
        artifact_paths = [
            *(str(item) for item in common_artifacts),
            *(str(item) for item in handler_artifacts),
        ]
        if len(set(str(item) for item in artifact_paths)) != len(artifact_paths):
            raise ValueError(f"duplicate artifacts for {handler_id}")
        artifacts: list[dict[str, str]] = []
        for raw_path in sorted(str(item) for item in artifact_paths):
            normalized, path = _resolve_artifact(repo_root, raw_path)
            artifacts.append(
                {
                    "path": normalized,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        build_payload = {
            "algorithm": "deskpet-core-execution-build-v1",
            "handler_id": handler_id,
            "tool_name": tool_name,
            "artifacts": artifacts,
        }
        handlers.append(
            {
                "handler_id": handler_id,
                "tool_name": tool_name,
                "authority_phase": str(source["authority_phase"]),
                "lifecycle": str(source["lifecycle"]),
                "artifacts": artifacts,
                "build_digest": canonical_hash(build_payload),
            }
        )
    manifest = {
        "schema_version": 1,
        "algorithm": "deskpet-core-execution-build-v1",
        "sources_manifest_hash": canonical_hash(sources),
        "handlers": handlers,
    }
    return canonical_json_bytes(manifest)


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    default_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--repo-root", type=Path, default=default_root)
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--effects", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve(strict=True)
    tools_dir = repo_root / "backend" / "deskpet" / "tools"
    sources = (args.sources or tools_dir / "execution_build_sources.json").resolve()
    effects = (args.effects or tools_dir / "tool_effect_policy_manifest.json").resolve()
    output = (args.output or tools_dir / "execution_build_manifest.json").resolve()
    try:
        expected = generate(
            repo_root=repo_root,
            sources_path=sources,
            effect_path=effects,
        )
        if args.write:
            _write_atomic(output, expected)
            print(f"wrote {output}")
            return 0
        actual = output.read_bytes()
        if actual != expected:
            print(f"stale execution build manifest: {output}", file=sys.stderr)
            return 1
        print(f"execution build manifest is current: {output}")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"execution build manifest error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
