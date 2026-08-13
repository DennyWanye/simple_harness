#!/usr/bin/env python3
"""Verify frozen SDK extraction oracles without freezing obsolete imports forever.

Unchanged files pass by their byte hash.  A changed file is accepted only when
BC-SDK-IMPORTS is approved and every pre-cutover assertion is still present,
apart from assertion fingerprints explicitly retired by an auditable receipt.
Additional assertions are allowed; changing an expected value is not.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
PLAN_DIR = ROOT / "plans/2026-08-13-simple-harness-sdk"
LOCK_PATH = PLAN_DIR / "testcase-lock.json"
BC_PATH = PLAN_DIR / "behavior-changes/BC-SDK-IMPORTS.json"
SOURCE_REQUEST_PATH = PLAN_DIR / "source-request.md"
ALLOWED_RETIREMENT_REASONS = frozenset(
    {"legacy_router", "ticketless_child", "personal_preselection_matcher"}
)


class OracleInvariantError(RuntimeError):
    """A stable, machine-readable oracle gate failure."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OracleInvariantError("ORACLE_CONFIG_INVALID", f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise OracleInvariantError("ORACLE_CONFIG_INVALID", f"{path}: expected object")
    return value


def _assertion_fingerprints(source: bytes, *, filename: str) -> tuple[str, ...]:
    try:
        tree = ast.parse(source, filename=filename)
    except (SyntaxError, ValueError) as exc:
        raise OracleInvariantError("ORACLE_PARSE_FAILED", f"{filename}: {exc}") from exc
    assertions: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            assertions.append(("assert", node))
            continue
        if not isinstance(node, ast.Call):
            continue
        call_name = ""
        if isinstance(node.func, ast.Name):
            call_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            call_name = node.func.attr
        # pytest raises/warns encode expected exception semantics outside an
        # ``assert`` statement.  unittest-style assert methods and pytest.fail
        # are frozen for the same reason.
        if call_name in {"raises", "warns", "fail"} or (
            call_name.startswith("assert") and len(call_name) > len("assert")
        ):
            assertions.append(("assertion_call", node))
    fingerprints = [
        _sha256(
            (
                kind
                + ":"
                + ast.dump(node, annotate_fields=True, include_attributes=False)
            ).encode("utf-8")
        )
        for kind, node in assertions
    ]
    return tuple(sorted(fingerprints))


def assertion_ast_hash(source: bytes, *, filename: str = "<oracle>") -> str:
    """Return an order-insensitive, multiplicity-preserving assertion AST hash."""

    return _sha256("\n".join(_assertion_fingerprints(source, filename=filename)).encode())


def verify_oracle_bytes(
    original: bytes,
    candidates: Iterable[tuple[str, bytes]],
    *,
    behavior_change_id: str | None,
    retired_assertions: Iterable[dict[str, str]] = (),
    filename: str = "<oracle>",
) -> dict[str, Any]:
    """Verify one original oracle against one or more migrated candidate files."""

    if behavior_change_id != "BC-SDK-IMPORTS":
        raise OracleInvariantError(
            "FROZEN_ORACLE_CHANGED", f"{filename}: no approved BC-SDK-IMPORTS transform"
        )

    original_fingerprints = Counter(_assertion_fingerprints(original, filename=filename))
    current_fingerprints: Counter[str] = Counter()
    candidate_names: list[str] = []
    for candidate_name, candidate_source in candidates:
        candidate_names.append(candidate_name)
        current_fingerprints.update(
            _assertion_fingerprints(candidate_source, filename=candidate_name)
        )

    retired: Counter[str] = Counter()
    for item in retired_assertions:
        fingerprint = item.get("sha256", "")
        reason = item.get("reason", "")
        if reason not in ALLOWED_RETIREMENT_REASONS or not re.fullmatch(
            r"[0-9a-f]{64}", fingerprint
        ):
            raise OracleInvariantError(
                "ORACLE_RECEIPT_INVALID",
                f"{filename}: invalid retired assertion fingerprint/reason",
            )
        retired[fingerprint] += 1

    missing = original_fingerprints - current_fingerprints
    unauthorized = missing - retired
    overclaimed = retired - missing
    if unauthorized:
        raise OracleInvariantError(
            "FROZEN_ORACLE_CHANGED",
            f"{filename}: {sum(unauthorized.values())} assertion(s) changed or removed",
        )
    if overclaimed:
        raise OracleInvariantError(
            "ORACLE_RECEIPT_INVALID",
            f"{filename}: receipt retires assertion(s) that remain or never existed",
        )
    return {
        "assertion_ast_sha256": assertion_ast_hash(original, filename=filename),
        "candidate_files": candidate_names,
        "original_assertions": sum(original_fingerprints.values()),
        "retired_assertions": sum(retired.values()),
        "added_assertions": sum((current_fingerprints - original_fingerprints).values()),
    }


def _git_show(repo: Path, commit: str, relative: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise OracleInvariantError(
            "ORACLE_BASELINE_UNAVAILABLE", f"{commit}:{relative}: {detail}"
        )
    return result.stdout


def _validate_approval(bc: dict[str, Any], source_request_path: Path) -> None:
    if bc.get("behavior_change_id") != "BC-SDK-IMPORTS" or bc.get("status") != "approved":
        raise OracleInvariantError("BEHAVIOR_CHANGE_NOT_APPROVED", "BC-SDK-IMPORTS")
    event = bc.get("approval_event")
    if not isinstance(event, dict) or event.get("source_request_id") != "SR-8":
        raise OracleInvariantError("BEHAVIOR_CHANGE_NOT_APPROVED", "missing SR-8 event")
    try:
        ledger = source_request_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OracleInvariantError("BEHAVIOR_CHANGE_NOT_APPROVED", str(exc)) from exc
    match = re.search(r"^\| SR-8 \| “(.*?)” \|", ledger, re.MULTILINE)
    if match is None or _sha256(match.group(1).encode("utf-8")) != event.get("text_sha256"):
        raise OracleInvariantError("BEHAVIOR_CHANGE_NOT_APPROVED", "SR-8 hash mismatch")


def _receipt_for(receipts: dict[str, Any], relative: str) -> dict[str, Any] | None:
    transforms = receipts.get("transforms", {})
    if not isinstance(transforms, dict):
        raise OracleInvariantError("ORACLE_RECEIPT_INVALID", "transforms must be an object")
    value = transforms.get(relative)
    if value is not None and not isinstance(value, dict):
        raise OracleInvariantError("ORACLE_RECEIPT_INVALID", f"{relative}: expected object")
    return value


def verify_repository(
    repo: Path = ROOT,
    *,
    lock_path: Path = LOCK_PATH,
    bc_path: Path = BC_PATH,
    source_request_path: Path = SOURCE_REQUEST_PATH,
    receipt_path: Path | None = None,
    replacement_root: Path | None = None,
) -> dict[str, Any]:
    lock = _load_json(lock_path)
    bc = _load_json(bc_path)
    _validate_approval(bc, source_request_path)
    receipts = _load_json(receipt_path) if receipt_path else {"transforms": {}}
    source_commit = lock.get("source_commit")
    files = lock.get("files")
    if not isinstance(source_commit, str) or not isinstance(files, dict):
        raise OracleInvariantError("ORACLE_CONFIG_INVALID", "invalid testcase lock")

    results: dict[str, Any] = {}
    for relative, expected_hash in sorted(files.items()):
        if not isinstance(relative, str) or not isinstance(expected_hash, str):
            raise OracleInvariantError("ORACLE_CONFIG_INVALID", "invalid testcase lock entry")
        current_path = repo / relative
        current = current_path.read_bytes() if current_path.is_file() else None
        if current is not None and _sha256(current) == expected_hash:
            results[relative] = {"status": "unchanged", "sha256": expected_hash}
            continue

        original = _git_show(repo, source_commit, relative)
        if _sha256(original) != expected_hash:
            raise OracleInvariantError(
                "ORACLE_BASELINE_HASH_MISMATCH", f"{relative}: git bytes differ from lock"
            )
        receipt = _receipt_for(receipts, relative)
        candidates: list[tuple[str, bytes]] = []
        if current is not None:
            candidates.append((relative, current))
        if receipt:
            replacements = receipt.get("replacement_paths", [])
            if not isinstance(replacements, list) or not all(
                isinstance(item, str) for item in replacements
            ):
                raise OracleInvariantError(
                    "ORACLE_RECEIPT_INVALID", f"{relative}: invalid replacement_paths"
                )
            base = replacement_root or repo
            for replacement in replacements:
                replacement_path = base / replacement
                if not replacement_path.is_file():
                    raise OracleInvariantError(
                        "ORACLE_RECEIPT_INVALID", f"{relative}: missing {replacement}"
                    )
                candidates.append((replacement, replacement_path.read_bytes()))
        if not candidates:
            raise OracleInvariantError(
                "FROZEN_ORACLE_CHANGED", f"{relative}: deleted without replacement"
            )
        result = verify_oracle_bytes(
            original,
            candidates,
            behavior_change_id=(
                receipt.get("behavior_change_id")
                if receipt
                else bc.get("behavior_change_id")
            ),
            retired_assertions=receipt.get("retired_assertions", []) if receipt else (),
            filename=relative,
        )
        result["status"] = "approved_transform"
        result["sha256"] = _sha256(current) if current is not None else None
        results[relative] = result
    return {
        "status": "PASS",
        "behavior_change_id": "BC-SDK-IMPORTS",
        "source_commit": source_commit,
        "files": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--lock", type=Path, default=LOCK_PATH)
    parser.add_argument("--behavior-change", type=Path, default=BC_PATH)
    parser.add_argument("--source-request", type=Path, default=SOURCE_REQUEST_PATH)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--replacement-root", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = verify_repository(
            args.repo.resolve(),
            lock_path=args.lock.resolve(),
            bc_path=args.behavior_change.resolve(),
            source_request_path=args.source_request.resolve(),
            receipt_path=args.receipt.resolve() if args.receipt else None,
            replacement_root=args.replacement_root.resolve() if args.replacement_root else None,
        )
    except OracleInvariantError as exc:
        if args.json:
            print(json.dumps({"status": "FAIL", "code": exc.code, "detail": exc.detail}))
        else:
            print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"SDK_ORACLES_PASS files={len(report['files'])} bc=BC-SDK-IMPORTS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
