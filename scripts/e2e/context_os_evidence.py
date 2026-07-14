#!/usr/bin/env python3
"""Join and validate Context OS E2E evidence without retaining request bodies."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

IDS = ("purpose", "request_id", "attempt_id")
SENSITIVE = re.compile(
    r"(?i)(authorization|api[_-]?key|token|password|arguments|content)"
)
CASE_ID = re.compile(r"^E2E-CTX-(?:0[1-9]|1[01])$")
RAW_MARKER_ORACLE = "RAW-01/RAW-06/RAW-12"
OFF_TASK_TYPES = (
    "chat",
    "recall",
    "task",
    "code",
    "web_search",
    "plan",
    "emotion",
    "command",
)


class EvidenceOracleError(ValueError):
    """An observed E2E value does not satisfy its immutable oracle."""


def rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig")
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if SENSITIVE.search(key) else safe(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [safe(item) for item in value]
    if isinstance(value, str) and len(value) > 240:
        return {
            "sha256": hashlib.sha256(value.encode()).hexdigest(),
            "length": len(value),
        }
    return value


def join(
    paths: Mapping[str, Path], request_id: str | None = None
) -> dict[str, Any]:
    all_rows: list[dict[str, Any]] = []
    for source, path in paths.items():
        for row in rows(path):
            if request_id and row.get("request_id") != request_id:
                continue
            all_rows.append({"source": source, **safe(row)})
    all_rows.sort(key=lambda item: (float(item.get("ts", 0)), item["source"]))
    index: dict[str, list[dict[str, Any]]] = {}
    for row in all_rows:
        key = tuple(row.get(field) for field in IDS)
        if any(key):
            index.setdefault(
                "|".join(str(item or "") for item in key), []
            ).append(row)
    return {
        "schema_version": 1,
        "request_id": request_id,
        "event_count": len(all_rows),
        "events": all_rows,
        "attempt_index": index,
    }


def validate_e2e01_raw_value(observed: str) -> dict[str, Any]:
    """Require the exact UI value, not marker-presence or a set comparison."""
    passed = observed == RAW_MARKER_ORACLE
    result = {
        "oracle": "e2e01_raw_marker_value_exact",
        "expected": RAW_MARKER_ORACLE,
        "observed": observed,
        "passed": passed,
    }
    if not passed:
        raise EvidenceOracleError(
            f"E2E-CTX-01 raw value mismatch: {observed!r} != {RAW_MARKER_ORACLE!r}"
        )
    return result


def load_off_golden(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise EvidenceOracleError("OFF golden schema_version must be 1")
    revision = value.get("registry_revision")
    if not isinstance(revision, int) or revision < 1:
        raise EvidenceOracleError("OFF golden registry_revision must be a positive int")
    task_tools = value.get("task_tools")
    if not isinstance(task_tools, dict) or tuple(task_tools) != OFF_TASK_TYPES:
        raise EvidenceOracleError(
            "OFF golden must contain the eight task types in canonical order"
        )
    for task_type in OFF_TASK_TYPES:
        names = task_tools[task_type]
        if not isinstance(names, list) or not all(
            isinstance(name, str) and name for name in names
        ):
            raise EvidenceOracleError(
                f"OFF golden {task_type} must be an explicit string array"
            )
        if "*" in names:
            raise EvidenceOracleError(
                f"OFF golden {task_type} contains wildcard instead of calibrated names"
            )
    return value


def validate_e2e11_off_observation(
    observed: Mapping[str, Any], golden: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare OFF evidence directly with a checked-in legacy golden.

    The caller must supply actual OFF observations.  This function deliberately
    has no ON-policy input and therefore cannot derive a legacy baseline from
    the reactive capability policy.
    """
    expected_revision = golden["registry_revision"]
    actual_revision = observed.get("registry_revision")
    actual_tools = observed.get("task_tools")
    failures: list[dict[str, Any]] = []
    if actual_revision != expected_revision:
        failures.append(
            {
                "field": "registry_revision",
                "expected": expected_revision,
                "observed": actual_revision,
            }
        )
    if not isinstance(actual_tools, Mapping):
        failures.append(
            {"field": "task_tools", "expected": "mapping", "observed": actual_tools}
        )
    else:
        for task_type in OFF_TASK_TYPES:
            expected = golden["task_tools"][task_type]
            actual = actual_tools.get(task_type)
            if actual != expected:
                failures.append(
                    {
                        "field": f"task_tools.{task_type}",
                        "expected": expected,
                        "observed": actual,
                    }
                )
    result = {
        "oracle": "e2e11_off_legacy_tools_exact",
        "golden_registry_revision": expected_revision,
        "passed": not failures,
        "failures": failures,
    }
    if failures:
        raise EvidenceOracleError(
            "E2E-CTX-11 OFF observation differs from checked-in legacy golden"
        )
    return result


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise EvidenceOracleError(f"JSON object required: {path}")
    return value


def _source_manifest(paths: Mapping[str, Path]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, path in paths.items():
        exists = path.is_file()
        item: dict[str, Any] = {
            "path": str(path.resolve()),
            "exists": exists,
        }
        if exists:
            raw = path.read_bytes()
            item.update(
                {
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        result[name] = item
    return result


def build_case_evidence(
    *,
    case_id: str,
    session_ids: Iterable[str],
    userdata: Path,
    paths: Mapping[str, Path],
    request_id: str | None = None,
    observation: Mapping[str, Any] | None = None,
    off_golden: Path | None = None,
) -> dict[str, Any]:
    if not CASE_ID.fullmatch(case_id):
        raise EvidenceOracleError(f"invalid Context OS E2E case id: {case_id!r}")
    sessions = tuple(session_ids)
    if not sessions or any(not value.strip() for value in sessions):
        raise EvidenceOracleError("at least one non-empty isolated session id is required")
    resolved_userdata = userdata.resolve()
    if not resolved_userdata.is_dir():
        raise EvidenceOracleError(f"isolated userdata does not exist: {resolved_userdata}")

    oracles: list[dict[str, Any]] = []
    observation = observation or {}
    if case_id == "E2E-CTX-01":
        raw_value = observation.get("raw_marker_value")
        if not isinstance(raw_value, str):
            raise EvidenceOracleError(
                "E2E-CTX-01 observation.raw_marker_value is required"
            )
        oracles.append(validate_e2e01_raw_value(raw_value))
    if case_id == "E2E-CTX-11":
        if off_golden is None:
            raise EvidenceOracleError("E2E-CTX-11 requires --off-golden")
        oracles.append(
            validate_e2e11_off_observation(observation, load_off_golden(off_golden))
        )

    joined = join(paths, request_id)
    return {
        "schema_version": 2,
        "case_id": case_id,
        "isolation": {
            "userdata": str(resolved_userdata),
            "session_ids": list(sessions),
        },
        "request_id": request_id,
        "sources": _source_manifest(paths),
        "oracles": oracles,
        "event_count": joined["event_count"],
        "events": joined["events"],
        "attempt_index": joined["attempt_index"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-log", type=Path)
    parser.add_argument("--fixture-log", type=Path)
    parser.add_argument("--provider-log", type=Path)
    parser.add_argument("--request-id")
    parser.add_argument("--case-id")
    parser.add_argument("--session-id", action="append", default=[])
    parser.add_argument("--userdata", type=Path)
    parser.add_argument("--observation", type=Path)
    parser.add_argument("--off-golden", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    paths = {
        key: value
        for key, value in {
            "backend": args.backend_log,
            "fixture": args.fixture_log,
            "provider": args.provider_log,
        }.items()
        if value
    }
    if args.case_id:
        if args.userdata is None:
            parser.error("--userdata is required with --case-id")
        result = build_case_evidence(
            case_id=args.case_id,
            session_ids=args.session_id,
            userdata=args.userdata,
            paths=paths,
            request_id=args.request_id,
            observation=_read_json(args.observation),
            off_golden=args.off_golden,
        )
    else:
        result = join(paths, args.request_id)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
