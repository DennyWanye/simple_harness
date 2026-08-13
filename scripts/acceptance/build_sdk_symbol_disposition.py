#!/usr/bin/env python3
"""Validate the frozen SDK cutover symbol inventory and reviewed dispositions."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
PLAN_DIR = ROOT / "plans/2026-08-13-simple-harness-sdk"
FROZEN_PATH = PLAN_DIR / "cutover-symbols.json"
DISPOSITION_PATH = PLAN_DIR / "source-symbol-disposition.json"
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
        raise SymbolDispositionError("SYMBOL_CONFIG_INVALID", f"{path}: expected object")
    return value


def discover_inventory(repo: Path, frozen: dict[str, Any]) -> list[dict[str, str]]:
    source_files = frozen.get("source_files")
    if not isinstance(source_files, dict):
        raise SymbolDispositionError("SYMBOL_CONFIG_INVALID", "source_files must be an object")
    entries: list[dict[str, str]] = []
    for relative, expected_hash in sorted(source_files.items()):
        path = repo / relative
        if not path.is_file():
            raise SymbolDispositionError("SOURCE_DRIFT", f"missing {relative}")
        source = path.read_bytes()
        actual_hash = _sha256(source)
        if actual_hash != expected_hash:
            raise SymbolDispositionError(
                "SOURCE_DRIFT", f"{relative}: expected {expected_hash}, got {actual_hash}"
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
    if (
        len(entries) != expected_inventory.get("count")
        or inventory_hash != expected_inventory.get("sha256")
    ):
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
        raise SymbolDispositionError("SYMBOL_CONFIG_INVALID", "schema_version must be 1")
    review = disposition.get("review")
    if not isinstance(review, dict) or review.get("status") != "frozen":
        raise SymbolDispositionError("SYMBOL_REVIEW_REQUIRED", "review.status must be frozen")
    raw_entries = disposition.get("entries")
    if not isinstance(raw_entries, list):
        raise SymbolDispositionError("SYMBOL_CONFIG_INVALID", "entries must be an array")

    expected_keys = {(item["source_path"], item["symbol"]) for item in inventory}
    seen: set[tuple[str, str]] = set()
    targets: dict[str, tuple[str, str]] = {}
    normalized: list[dict[str, str]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise SymbolDispositionError("SYMBOL_CONFIG_INVALID", "entry must be an object")
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
            raise SymbolDispositionError("DUPLICATE_SYMBOL_DISPOSITION", f"{source_path}:{symbol}")
        seen.add(key)
        if disposition_name not in ALLOWED_DISPOSITIONS:
            raise SymbolDispositionError("UNCLASSIFIED_SYMBOL", f"{source_path}:{symbol}")
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
            if isinstance(
                node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ) and node.name in forbidden:
                survivors.append(f"{path}:{node.lineno}:{node.name}")
    if survivors:
        raise SymbolDispositionError(
            "FORBIDDEN_PRODUCT_AUTHORITY_SURVIVOR", "; ".join(survivors[:20])
        )


def verify_repository(
    repo: Path = ROOT,
    *,
    frozen_path: Path = FROZEN_PATH,
    disposition_path: Path = DISPOSITION_PATH,
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
    if final_product_root is not None:
        assert_no_forbidden_survivors(final_product_root, frozen)
        report["forbidden_survivors"] = 0
    return {"status": "PASS", **report}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--frozen", type=Path, default=FROZEN_PATH)
    parser.add_argument("--disposition", type=Path, default=DISPOSITION_PATH)
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
            final_product_root=(
                args.final_product_root.resolve() if args.final_product_root else None
            ),
        )
    except SymbolDispositionError as exc:
        if args.json:
            print(
                json.dumps(
                    {"status": "FAIL", "code": exc.code, "detail": exc.detail}
                )
            )
        else:
            print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            f"SDK_SYMBOL_DISPOSITION_PASS entries={report['entries']} "
            f"sha256={report['disposition_sha256']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
