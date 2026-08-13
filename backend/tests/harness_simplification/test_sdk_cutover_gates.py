from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.acceptance import build_sdk_symbol_disposition as symbols
from scripts.acceptance import verify_sdk_oracles as oracles


ROOT = Path(__file__).resolve().parents[3]
PLAN_DIR = ROOT / "plans/2026-08-13-simple-harness-sdk"


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _disposition(entries: list[dict[str, str]]) -> dict[str, object]:
    normalized = sorted(entries, key=lambda item: (item["source_path"], item["symbol"]))
    return {
        "schema_version": 1,
        "review": {"status": "frozen"},
        "disposition_sha256": _hash(symbols._canonical_entries(normalized)),
        "entries": entries,
    }


def test_frozen_oracle_and_symbol_golden_files_pass() -> None:
    oracle_report = oracles.verify_repository(ROOT)
    symbol_report = symbols.verify_repository(ROOT)

    assert oracle_report["status"] == "PASS"
    assert len(oracle_report["files"]) == 12
    assert {item["status"] for item in oracle_report["files"].values()} == {
        "unchanged"
    }
    assert symbol_report == {
        "status": "PASS",
        "entries": 184,
        "disposition_sha256": "6e1d9a0d4efb85956fad184a432cd2d59b139454b00c64ecd2c5dbf5fd739fe4",
    }


def test_oracle_allows_import_fixture_change_when_assertions_are_unchanged() -> None:
    original = (
        b"from old_owner import Runner\n\ndef test_value():\n"
        b"    assert Runner.key == 'agent.general'\n"
    )
    migrated = (
        b"from simple_harness import Runner\n\ndef test_value(new_fixture):\n"
        b"    assert Runner.key == 'agent.general'\n"
    )

    report = oracles.verify_oracle_bytes(
        original,
        [("migrated.py", migrated)],
        behavior_change_id="BC-SDK-IMPORTS",
        filename="source.py",
    )

    assert report["original_assertions"] == 1
    assert report["retired_assertions"] == 0


def test_oracle_rejects_unapproved_expected_rewrite() -> None:
    original = b"def test_terminal():\n    assert terminal == 'failed'\n"
    rewritten = b"def test_terminal():\n    assert terminal == 'completed'\n"

    with pytest.raises(oracles.OracleInvariantError) as caught:
        oracles.verify_oracle_bytes(
            original,
            [("rewritten.py", rewritten)],
            behavior_change_id="BC-SDK-IMPORTS",
            filename="source.py",
        )

    assert caught.value.code == "FROZEN_ORACLE_CHANGED"


def test_symbol_gate_rejects_source_drift(tmp_path: Path) -> None:
    source = tmp_path / "owner.py"
    source.write_text("class RunKernel:\n    pass\n", encoding="utf-8")
    frozen = {
        "source_files": {"owner.py": "0" * 64},
        "public_symbol_inventory": {"count": 1, "sha256": "0" * 64},
    }

    with pytest.raises(symbols.SymbolDispositionError) as caught:
        symbols.discover_inventory(tmp_path, frozen)

    assert caught.value.code == "SOURCE_DRIFT"


def test_symbol_gate_rejects_unclassified_symbol() -> None:
    inventory = [{"source_path": "owner.py", "symbol": "RunKernel"}]

    with pytest.raises(symbols.SymbolDispositionError) as caught:
        symbols.validate_dispositions(inventory, _disposition([]))

    assert caught.value.code == "UNCLASSIFIED_SYMBOL"


def test_symbol_gate_rejects_duplicate_target() -> None:
    inventory = [
        {"source_path": "a.py", "symbol": "First"},
        {"source_path": "b.py", "symbol": "Second"},
    ]
    entries = [
        {
            "source_path": "a.py",
            "symbol": "First",
            "disposition": "sdk_private",
            "target_symbol": "simple_harness.runtime._Owner",
            "rationale": "reviewed",
        },
        {
            "source_path": "b.py",
            "symbol": "Second",
            "disposition": "sdk_private",
            "target_symbol": "simple_harness.runtime._Owner",
            "rationale": "reviewed",
        },
    ]

    with pytest.raises(symbols.SymbolDispositionError) as caught:
        symbols.validate_dispositions(inventory, _disposition(entries))

    assert caught.value.code == "DUPLICATE_TARGET_SYMBOL"


def test_symbol_gate_rejects_forbidden_product_survivor(tmp_path: Path) -> None:
    (tmp_path / "authority.py").write_text(
        "class RunKernel:\n    pass\n", encoding="utf-8"
    )

    with pytest.raises(symbols.SymbolDispositionError) as caught:
        symbols.assert_no_forbidden_survivors(
            tmp_path,
            {"forbidden_product_authority_definitions_after_cutover": ["RunKernel"]},
        )

    assert caught.value.code == "FORBIDDEN_PRODUCT_AUTHORITY_SURVIVOR"
