#!/usr/bin/env python3
"""Black-box helpers for the frozen S4 Runtime execution oracle.

The formal adapter is a public-contract driver supplied by the Host validation
run. This module never imports Host or SDK implementation modules.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any
import zipfile


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_fixture(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "fixture_id",
        "subject",
        "wrong_actor",
        "data_format",
        "task_scopes",
        "workspace",
        "queue",
        "expected_route",
        "required_human_ports",
        "required_v44_audit_sets",
        "required_execution_sequence",
        "fault_boundaries",
        "stable_errors",
        "wheel_contract",
        "forbidden_audit_keys",
    }
    missing = sorted(required - value.keys())
    if missing:
        raise ValueError(f"fixture missing keys: {missing}")
    if value["task_scopes"]["a"]["event_count"] != 100000:
        raise ValueError("runtime scope A must freeze exactly 100000 events")
    if len(value["queue"]["turns"]) != 2:
        raise ValueError("execution value fixture requires exactly two queued turns")
    if len(value["fault_boundaries"]) < 8:
        raise ValueError("fault matrix must cover every prepare/start/bind/terminal boundary")
    return value


def require_evidence_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if ".local-test-evidence" not in resolved.parts:
        raise ValueError(
            "artifact-dir must be inside the Host ignored .local-test-evidence directory"
        )
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def load_adapter(
    path: Path, fixture: dict[str, Any], artifact_dir: Path
) -> tuple[Any, dict[str, str]]:
    adapter_path = path.expanduser().resolve()
    adapter_bytes = adapter_path.read_bytes()
    spec = importlib.util.spec_from_file_location(
        "s4_runtime_public_contract_adapter", adapter_path
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot import adapter: {adapter_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    factory = getattr(module, "create_adapter", None)
    if not callable(factory):
        raise ValueError(
            "adapter must export create_adapter(*, fixture, artifact_dir)"
        )
    adapter = factory(fixture=fixture, artifact_dir=artifact_dir)
    if not callable(getattr(adapter, "invoke", None)):
        raise ValueError(
            "adapter instance must expose invoke(operation, request) -> JSON object"
        )
    return adapter, {
        "path": str(adapter_path),
        "sha256": sha256_bytes(adapter_bytes),
    }


def invoke(adapter: Any, operation: str, request: dict[str, Any]) -> dict[str, Any]:
    result = adapter.invoke(operation, request)
    if not isinstance(result, dict):
        raise AssertionError(f"{operation} returned non-object result")
    return result


def expect_ok(result: dict[str, Any], operation: str) -> dict[str, Any]:
    if result.get("ok") is not True:
        raise AssertionError(f"{operation} failed: {result.get('code') or result}")
    status = result.get("status")
    if isinstance(status, int) and status >= 500:
        raise AssertionError(f"{operation} returned server error {status}")
    return result


def expect_error(
    result: dict[str, Any], operation: str, expected_code: str
) -> dict[str, Any]:
    if result.get("ok") is not False or result.get("code") != expected_code:
        raise AssertionError(f"{operation}: expected {expected_code}, got {result}")
    status = result.get("status")
    if isinstance(status, int) and status >= 500:
        raise AssertionError(f"{operation}: stable rejection must not be 5xx")
    return result


def payload(result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("payload", result)
    if not isinstance(value, dict):
        raise AssertionError("adapter payload must be a JSON object")
    return value


def require_fields(value: dict[str, Any], fields: set[str], label: str) -> None:
    missing = sorted(field for field in fields if value.get(field) in (None, ""))
    if missing:
        raise AssertionError(f"{label} missing fields: {missing}")


def assert_no_forbidden_keys(value: Any, forbidden: set[str], label: str) -> None:
    if isinstance(value, dict):
        present = forbidden.intersection(str(key).lower() for key in value)
        if present:
            raise AssertionError(f"{label} exposed forbidden keys: {sorted(present)}")
        for child in value.values():
            assert_no_forbidden_keys(child, forbidden, label)
    elif isinstance(value, list):
        for child in value:
            assert_no_forbidden_keys(child, forbidden, label)


def inspect_wheel(
    wheel_path: Path,
    *,
    expected_sha256: str,
    expected_version: str,
    expected_distribution: str,
) -> dict[str, str]:
    wheel = wheel_path.expanduser().resolve()
    wheel_bytes = wheel.read_bytes()
    observed_sha = sha256_bytes(wheel_bytes)
    if observed_sha != expected_sha256:
        raise AssertionError(
            f"candidate wheel SHA-256 mismatch: {observed_sha} != {expected_sha256}"
        )
    with zipfile.ZipFile(wheel) as archive:
        metadata_names = sorted(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        if len(metadata_names) != 1:
            raise AssertionError("candidate wheel must contain exactly one METADATA")
        metadata = archive.read(metadata_names[0]).decode("utf-8")
    fields: dict[str, str] = {}
    for line in metadata.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        fields.setdefault(key.strip().lower(), raw.strip())
    observed_name = fields.get("name", "").lower().replace("_", "-")
    if observed_name != expected_distribution.lower().replace("_", "-"):
        raise AssertionError(f"candidate wheel distribution mismatch: {observed_name}")
    if fields.get("version") != expected_version:
        raise AssertionError(
            f"candidate wheel version mismatch: {fields.get('version')}"
        )
    return {
        "filename": wheel.name,
        "sha256": observed_sha,
        "distribution": observed_name,
        "version": fields["version"],
    }


def write_result(artifact_dir: Path, name: str, value: dict[str, Any]) -> Path:
    path = artifact_dir / name
    path.write_bytes(canonical_bytes(value) + b"\n")
    return path
