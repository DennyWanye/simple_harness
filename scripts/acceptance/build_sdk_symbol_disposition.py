#!/usr/bin/env python3
"""Validate the frozen SDK cutover symbol inventory and reviewed dispositions."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PLAN_DIR = ROOT / "plans/2026-08-13-simple-harness-sdk"
FROZEN_PATH = PLAN_DIR / "cutover-symbols.json"
DISPOSITION_PATH = PLAN_DIR / "source-symbol-disposition.json"
SUPPLEMENTAL_PATH = PLAN_DIR / "workflow-errors-supplemental-oracle.json"
SUPPLEMENTAL_SOURCE_COMMIT = "122ec55989f8a77e023aeb44ba1b4dae1b694269"
SUPPLEMENTAL_SOURCE_PATH = "backend/deskpet/workflows/errors.py"
SUPPLEMENTAL_SOURCE_SHA256 = (
    "2d5e1c536f2a1c73dcae8034425572c3749ca904029d44253af2f0bcdb01bb93"
)
SUPPLEMENTAL_TARGETS = {
    name: f"simple_harness.workflow.errors.{name}"
    for name in (
        "WorkflowErrorCode",
        "ErrorDisposition",
        "ERROR_DISPOSITIONS",
        "WorkflowContractError",
        "WorkflowDefinitionError",
        "InvalidStatePatch",
        "StateMergeConflict",
        "WorkflowDependencyUnavailable",
        "AsyncOnlyWorkflowError",
        "UnsupportedDeltaChannelError",
        "LeaseLostError",
        "WorkflowNodeError",
    )
}
ALLOWED_DISPOSITIONS = frozenset(
    {"sdk_public", "sdk_private", "product_adapter", "product_owned", "retire"}
)


class SymbolDispositionError(RuntimeError):
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
        raise SymbolDispositionError("SYMBOL_CONFIG_INVALID", f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SymbolDispositionError(
            "SYMBOL_CONFIG_INVALID", f"{path}: expected object"
        )
    return value


def _defined_public_symbols(source: bytes, *, filename: str) -> set[str]:
    try:
        tree = ast.parse(source, filename=filename)
    except (SyntaxError, ValueError) as exc:
        raise SymbolDispositionError("SOURCE_DRIFT", f"{filename}: {exc}") from exc
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.add(target.id)
    return names


def _git_show(repo: Path, commit: str, relative: str) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise SymbolDispositionError("SOURCE_DRIFT", f"{commit}:{relative}: {detail}")
    return result.stdout


def _assert_sdk_targets(sdk_root: Path, targets: dict[str, str]) -> None:
    by_module: dict[str, set[str]] = {}
    for target in targets.values():
        module, _, symbol = target.rpartition(".")
        if not module or not symbol:
            raise SymbolDispositionError("SYMBOL_CONFIG_INVALID", target)
        by_module.setdefault(module, set()).add(symbol)
    for module, expected in sorted(by_module.items()):
        relative = Path(*module.split(".")).with_suffix(".py")
        candidates = (sdk_root / "src" / relative, sdk_root / relative)
        module_path = next((path for path in candidates if path.is_file()), None)
        if module_path is None:
            raise SymbolDispositionError("MISSING_SDK_TARGET", str(candidates[0]))
        actual = _defined_public_symbols(
            module_path.read_bytes(), filename=str(module_path)
        )
        missing = expected - actual
        if missing:
            raise SymbolDispositionError(
                "MISSING_SDK_TARGET", f"{module}: {', '.join(sorted(missing))}"
            )


def validate_supplemental_oracle(
    repo: Path,
    *,
    supplemental_path: Path = SUPPLEMENTAL_PATH,
    expected_source_commit: str | None = None,
    sdk_root: Path | None = None,
    final_product_root: Path | None = None,
) -> dict[str, Any]:
    """Validate the additive Workflow error authority without changing 184."""

    supplemental = _load_json(supplemental_path)
    if supplemental.get("schema_version") != 1:
        raise SymbolDispositionError("SYMBOL_CONFIG_INVALID", "supplemental schema")
    if supplemental.get("source_commit") != SUPPLEMENTAL_SOURCE_COMMIT or (
        expected_source_commit is not None
        and supplemental.get("source_commit") != expected_source_commit
    ):
        raise SymbolDispositionError(
            "SOURCE_DRIFT", "supplemental source_commit mismatch"
        )
    if supplemental.get("source_path") != SUPPLEMENTAL_SOURCE_PATH:
        raise SymbolDispositionError(
            "SOURCE_DRIFT", "supplemental source_path mismatch"
        )
    if supplemental.get("source_sha256") != SUPPLEMENTAL_SOURCE_SHA256:
        raise SymbolDispositionError(
            "SOURCE_DRIFT", "supplemental source hash mismatch"
        )

    entries = supplemental.get("entries")
    if not isinstance(entries, list):
        raise SymbolDispositionError("SYMBOL_CONFIG_INVALID", "supplemental entries")
    normalized: dict[str, str] = {}
    seen_targets: set[str] = set()
    for raw in entries:
        if not isinstance(raw, dict) or set(raw) != {
            "symbol",
            "disposition",
            "target_symbol",
        }:
            raise SymbolDispositionError("SYMBOL_CONFIG_INVALID", "supplemental entry")
        symbol = raw.get("symbol")
        disposition = raw.get("disposition")
        target = raw.get("target_symbol")
        if not all(isinstance(value, str) and value for value in (symbol, target)):
            raise SymbolDispositionError("SYMBOL_CONFIG_INVALID", "supplemental entry")
        if disposition != "sdk_public":
            raise SymbolDispositionError("UNCLASSIFIED_SYMBOL", str(symbol))
        assert isinstance(symbol, str) and isinstance(target, str)
        if symbol in normalized:
            raise SymbolDispositionError("DUPLICATE_SYMBOL_DISPOSITION", symbol)
        if target in seen_targets:
            raise SymbolDispositionError("DUPLICATE_TARGET_SYMBOL", target)
        normalized[symbol] = target
        seen_targets.add(target)
    if normalized != SUPPLEMENTAL_TARGETS:
        missing = set(SUPPLEMENTAL_TARGETS) - set(normalized)
        extra = set(normalized) - set(SUPPLEMENTAL_TARGETS)
        wrong = {
            symbol
            for symbol in set(normalized) & set(SUPPLEMENTAL_TARGETS)
            if normalized[symbol] != SUPPLEMENTAL_TARGETS[symbol]
        }
        raise SymbolDispositionError(
            "UNCLASSIFIED_SYMBOL",
            f"supplemental missing={sorted(missing)} extra={sorted(extra)} wrong={sorted(wrong)}",
        )

    forbidden = supplemental.get(
        "forbidden_product_authority_definitions_after_cutover"
    )
    if (
        not isinstance(forbidden, list)
        or len(forbidden) != len(set(forbidden))
        or set(forbidden) != set(SUPPLEMENTAL_TARGETS)
    ):
        raise SymbolDispositionError(
            "SYMBOL_CONFIG_INVALID", "supplemental forbidden survivor inventory"
        )

    baseline = _git_show(repo, SUPPLEMENTAL_SOURCE_COMMIT, SUPPLEMENTAL_SOURCE_PATH)
    current_path = repo / SUPPLEMENTAL_SOURCE_PATH
    if (
        _sha256(baseline) != SUPPLEMENTAL_SOURCE_SHA256
        or not current_path.is_file()
        or _sha256(current_path.read_bytes()) != SUPPLEMENTAL_SOURCE_SHA256
    ):
        raise SymbolDispositionError("SOURCE_DRIFT", SUPPLEMENTAL_SOURCE_PATH)
    discovered = _defined_public_symbols(
        baseline, filename=f"{SUPPLEMENTAL_SOURCE_COMMIT}:{SUPPLEMENTAL_SOURCE_PATH}"
    )
    if discovered != set(SUPPLEMENTAL_TARGETS):
        raise SymbolDispositionError(
            "SYMBOL_INVENTORY_DRIFT",
            f"supplemental expected 12, got {len(discovered)}",
        )
    if sdk_root is not None:
        _assert_sdk_targets(sdk_root, normalized)
    if final_product_root is not None:
        assert_no_forbidden_survivors(
            final_product_root,
            {
                "forbidden_product_authority_definitions_after_cutover": list(
                    SUPPLEMENTAL_TARGETS
                )
            },
        )
    return {
        "source_commit": SUPPLEMENTAL_SOURCE_COMMIT,
        "source_sha256": SUPPLEMENTAL_SOURCE_SHA256,
        "entries": len(normalized),
        "sdk_targets": len(normalized) if sdk_root is not None else None,
        "forbidden_survivors": 0 if final_product_root is not None else None,
    }


def discover_inventory(repo: Path, frozen: dict[str, Any]) -> list[dict[str, str]]:
    source_files = frozen.get("source_files")
    if not isinstance(source_files, dict):
        raise SymbolDispositionError(
            "SYMBOL_CONFIG_INVALID", "source_files must be an object"
        )
    entries: list[dict[str, str]] = []
    for relative, expected_hash in sorted(source_files.items()):
        path = repo / relative
        if not path.is_file():
            raise SymbolDispositionError("SOURCE_DRIFT", f"missing {relative}")
        source = path.read_bytes()
        actual_hash = _sha256(source)
        if actual_hash != expected_hash:
            raise SymbolDispositionError(
                "SOURCE_DRIFT",
                f"{relative}: expected {expected_hash}, got {actual_hash}",
            )
        try:
            tree = ast.parse(source, filename=relative)
        except (SyntaxError, ValueError) as exc:
            raise SymbolDispositionError("SOURCE_DRIFT", f"{relative}: {exc}") from exc
        for node in tree.body:
            if isinstance(
                node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ) and not node.name.startswith("_"):
                entries.append({"source_path": relative, "symbol": node.name})
    entries.sort(key=lambda item: (item["source_path"], item["symbol"]))
    lines = [f"{item['source_path']}:{item['symbol']}" for item in entries]
    inventory_hash = _sha256("\n".join(lines).encode("utf-8"))
    expected_inventory = frozen.get("public_symbol_inventory", {})
    if len(entries) != expected_inventory.get(
        "count"
    ) or inventory_hash != expected_inventory.get("sha256"):
        raise SymbolDispositionError(
            "SYMBOL_INVENTORY_DRIFT",
            "expected count/hash "
            f"{expected_inventory.get('count')}/"
            f"{expected_inventory.get('sha256')}, "
            f"got {len(entries)}/{inventory_hash}",
        )
    return entries


def _canonical_entries(entries: list[dict[str, str]]) -> bytes:
    return json.dumps(
        entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def validate_dispositions(
    inventory: list[dict[str, str]], disposition: dict[str, Any]
) -> dict[str, Any]:
    if disposition.get("schema_version") != 1:
        raise SymbolDispositionError(
            "SYMBOL_CONFIG_INVALID", "schema_version must be 1"
        )
    review = disposition.get("review")
    if not isinstance(review, dict) or review.get("status") != "frozen":
        raise SymbolDispositionError(
            "SYMBOL_REVIEW_REQUIRED", "review.status must be frozen"
        )
    raw_entries = disposition.get("entries")
    if not isinstance(raw_entries, list):
        raise SymbolDispositionError(
            "SYMBOL_CONFIG_INVALID", "entries must be an array"
        )

    expected_keys = {(item["source_path"], item["symbol"]) for item in inventory}
    seen: set[tuple[str, str]] = set()
    targets: dict[str, tuple[str, str]] = {}
    normalized: list[dict[str, str]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise SymbolDispositionError(
                "SYMBOL_CONFIG_INVALID", "entry must be an object"
            )
        source_path = raw.get("source_path")
        symbol = raw.get("symbol")
        disposition_name = raw.get("disposition")
        target = raw.get("target_symbol")
        rationale = raw.get("rationale")
        if not all(
            isinstance(value, str) and value
            for value in (source_path, symbol, target, rationale)
        ):
            raise SymbolDispositionError(
                "SYMBOL_CONFIG_INVALID", "entry has an empty required field"
            )
        key = (source_path, symbol)
        if key in seen:
            raise SymbolDispositionError(
                "DUPLICATE_SYMBOL_DISPOSITION", f"{source_path}:{symbol}"
            )
        seen.add(key)
        if disposition_name not in ALLOWED_DISPOSITIONS:
            raise SymbolDispositionError(
                "UNCLASSIFIED_SYMBOL", f"{source_path}:{symbol}"
            )
        if target in targets:
            prior = targets[target]
            raise SymbolDispositionError(
                "DUPLICATE_TARGET_SYMBOL",
                f"{target}: {prior[0]}:{prior[1]} and {source_path}:{symbol}",
            )
        targets[target] = key
        normalized.append(
            {
                "source_path": source_path,
                "symbol": symbol,
                "disposition": disposition_name,
                "target_symbol": target,
                "rationale": rationale,
            }
        )

    missing = expected_keys - seen
    extra = seen - expected_keys
    if missing or extra:
        detail = f"missing={len(missing)} extra={len(extra)}"
        if missing:
            detail += f" first_missing={min(missing)}"
        if extra:
            detail += f" first_extra={min(extra)}"
        raise SymbolDispositionError("UNCLASSIFIED_SYMBOL", detail)
    normalized.sort(key=lambda item: (item["source_path"], item["symbol"]))
    actual_hash = _sha256(_canonical_entries(normalized))
    if actual_hash != disposition.get("disposition_sha256"):
        raise SymbolDispositionError(
            "DISPOSITION_HASH_MISMATCH",
            f"expected {disposition.get('disposition_sha256')}, got {actual_hash}",
        )
    return {"entries": len(normalized), "disposition_sha256": actual_hash}


def assert_no_forbidden_survivors(product_root: Path, frozen: dict[str, Any]) -> None:
    forbidden = set(
        frozen.get("forbidden_product_authority_definitions_after_cutover", [])
    )
    survivors: list[str] = []
    for path in sorted(product_root.rglob("*.py")):
        relative_parts = path.relative_to(product_root).parts
        if (
            "tests" in relative_parts
            or "__pycache__" in relative_parts
            or "sdk_adapters" in relative_parts
        ):
            continue
        try:
            tree = ast.parse(path.read_bytes(), filename=str(path))
        except (SyntaxError, ValueError) as exc:
            raise SymbolDispositionError(
                "PRODUCT_SOURCE_PARSE_FAILED", f"{path}: {exc}"
            ) from exc
        for node in tree.body:
            if (
                isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in forbidden
            ):
                survivors.append(f"{path}:{node.lineno}:{node.name}")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in forbidden:
                        survivors.append(f"{path}:{node.lineno}:{target.id}")
    if survivors:
        raise SymbolDispositionError(
            "FORBIDDEN_PRODUCT_AUTHORITY_SURVIVOR", "; ".join(survivors[:20])
        )


def verify_repository(
    repo: Path = ROOT,
    *,
    frozen_path: Path = FROZEN_PATH,
    disposition_path: Path = DISPOSITION_PATH,
    supplemental_path: Path = SUPPLEMENTAL_PATH,
    sdk_root: Path | None = None,
    final_product_root: Path | None = None,
) -> dict[str, Any]:
    frozen = _load_json(frozen_path)
    disposition = _load_json(disposition_path)
    if disposition.get("source_commit") != frozen.get("source_commit"):
        raise SymbolDispositionError(
            "SYMBOL_CONFIG_INVALID",
            "disposition source_commit does not match frozen inventory",
        )
    if disposition.get("inventory") != frozen.get("public_symbol_inventory"):
        raise SymbolDispositionError(
            "SYMBOL_CONFIG_INVALID",
            "disposition inventory metadata does not match frozen inventory",
        )
    inventory = discover_inventory(repo, frozen)
    report = validate_dispositions(inventory, disposition)
    supplemental = validate_supplemental_oracle(
        repo,
        supplemental_path=supplemental_path,
        expected_source_commit=frozen.get("source_commit"),
        sdk_root=sdk_root,
        final_product_root=final_product_root,
    )
    if final_product_root is not None:
        assert_no_forbidden_survivors(final_product_root, frozen)
        report["forbidden_survivors"] = 0
    return {"status": "PASS", **report, "supplemental": supplemental}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--frozen", type=Path, default=FROZEN_PATH)
    parser.add_argument("--disposition", type=Path, default=DISPOSITION_PATH)
    parser.add_argument("--supplemental", type=Path, default=SUPPLEMENTAL_PATH)
    parser.add_argument(
        "--sdk-root",
        type=Path,
        help="Optionally require every supplemental target in this SDK checkout.",
    )
    parser.add_argument(
        "--final-product-root",
        type=Path,
        help="At final cutover, scan this product Python root for forbidden authorities.",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = verify_repository(
            args.repo.resolve(),
            frozen_path=args.frozen.resolve(),
            disposition_path=args.disposition.resolve(),
            supplemental_path=args.supplemental.resolve(),
            sdk_root=args.sdk_root.resolve() if args.sdk_root else None,
            final_product_root=(
                args.final_product_root.resolve() if args.final_product_root else None
            ),
        )
    except SymbolDispositionError as exc:
        if args.json:
            print(
                json.dumps({"status": "FAIL", "code": exc.code, "detail": exc.detail})
            )
        else:
            print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            f"SDK_SYMBOL_DISPOSITION_PASS entries={report['entries']} "
            f"sha256={report['disposition_sha256']} "
            f"supplemental={report['supplemental']['entries']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
