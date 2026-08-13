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


def _supplemental_copy(tmp_path: Path, mutate=None) -> Path:
    value = json.loads(
        (PLAN_DIR / "workflow-errors-supplemental-oracle.json").read_text(
            encoding="utf-8"
        )
    )
    if mutate is not None:
        mutate(value)
    path = tmp_path / "supplemental.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _runner_transform_copy(tmp_path: Path, mutate=None) -> Path:
    value = json.loads(
        (PLAN_DIR / "workflow-runner-h16-transform.json").read_text(encoding="utf-8")
    )
    if mutate is not None:
        mutate(value)
    path = tmp_path / "runner-transform.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _sdk_root(tmp_path: Path, source: str | None = None) -> Path:
    root = tmp_path / "sdk"
    target = root / "src/simple_harness/workflow/errors.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        source
        if source is not None
        else (ROOT / "backend/deskpet/workflows/errors.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / "src/simple_harness/workflow/runner.py").write_text(
        "class WorkflowRunner:\n"
        "    def __init__(self, registry, checkpoint, recovery, trace, execution_ports): pass\n",
        encoding="utf-8",
    )
    (root / "src/simple_harness/workflow/lease.py").write_text(
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class WorkflowLease:\n"
        "    run_id: str\n"
        "    runtime_lease_epoch: int\n"
        "    namespace: str = 'native'\n",
        encoding="utf-8",
    )
    return root


def test_frozen_oracle_and_symbol_golden_files_pass() -> None:
    oracle_report = oracles.verify_repository(ROOT)
    symbol_report = symbols.verify_repository(ROOT)

    assert oracle_report["status"] == "PASS"
    assert len(oracle_report["files"]) == 12
    assert {item["status"] for item in oracle_report["files"].values()} == {"unchanged"}
    frozen = json.loads((PLAN_DIR / "cutover-symbols.json").read_text(encoding="utf-8"))
    assert len(frozen["source_files"]) == 30
    assert frozen["public_symbol_inventory"] == {
        "algorithm": "sha256 of sorted UTF-8 lines path:top_level_public_class_or_function",
        "count": 184,
        "sha256": "99bc5748946121d038faa5c64d6185f9d86c9e7a7ac907fc2dedea434b216215",
    }
    assert oracle_report["supplemental"] == {
        "source_commit": "122ec55989f8a77e023aeb44ba1b4dae1b694269",
        "source_sha256": "2d5e1c536f2a1c73dcae8034425572c3749ca904029d44253af2f0bcdb01bb93",
        "entries": 12,
        "sdk_targets": None,
        "forbidden_survivors": None,
    }
    assert oracle_report["runner_transform"] == {
        "source_commit": "122ec55989f8a77e023aeb44ba1b4dae1b694269",
        "source_files": 2,
        "source_inventory": 7,
        "sdk_target": False,
    }
    assert symbol_report == {
        "status": "PASS",
        "entries": 184,
        "disposition_sha256": "6e1d9a0d4efb85956fad184a432cd2d59b139454b00c64ecd2c5dbf5fd739fe4",
        "supplemental": oracle_report["supplemental"],
        "runner_transform": oracle_report["runner_transform"],
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


def test_supplemental_gates_validate_all_sdk_targets_when_root_is_supplied(
    tmp_path: Path,
) -> None:
    sdk_root = _sdk_root(tmp_path)
    product_root = tmp_path / "final-product"
    product_root.mkdir()

    symbol_report = symbols.verify_repository(
        ROOT, sdk_root=sdk_root, final_product_root=product_root
    )
    oracle_report = oracles.verify_repository(
        ROOT, sdk_root=sdk_root, final_product_root=product_root
    )

    assert symbol_report["entries"] == 184
    assert symbol_report["disposition_sha256"] == (
        "6e1d9a0d4efb85956fad184a432cd2d59b139454b00c64ecd2c5dbf5fd739fe4"
    )
    assert symbol_report["supplemental"]["sdk_targets"] == 12
    assert oracle_report["supplemental"]["sdk_targets"] == 12
    assert symbol_report["supplemental"]["forbidden_survivors"] == 0
    assert symbol_report["runner_transform"]["sdk_target"] is True
    assert oracle_report["runner_transform"]["sdk_target"] is True


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["source_files"][1].__setitem__("sha256", "0" * 64),
        lambda value: value["source_files"][1]["required_inventory"].pop(),
        lambda value: value["source_behavior_mapping"][3].__setitem__(
            "source", "LeaseManager.release"
        ),
    ],
)
def test_runner_transform_rejects_source_inventory_or_mapping_drift(
    tmp_path: Path, mutation
) -> None:
    transform = _runner_transform_copy(tmp_path, mutation)

    with pytest.raises(symbols.SymbolDispositionError) as caught:
        symbols.validate_runner_authority_transform(ROOT, transform_path=transform)

    assert caught.value.code in {"SOURCE_DRIFT", "SYMBOL_CONFIG_INVALID"}


@pytest.mark.parametrize(
    "runner_source,lease_source",
    [
        (
            "class WorkflowRunner:\n    def __init__(self, lease): pass\n",
            "class WorkflowLease:\n    runtime_lease_epoch: int\n",
        ),
        (
            "class WorkflowRunner:\n    def __init__(self): pass\n",
            "class WorkflowLease:\n    runtime_lease_epoch: int = 1\n",
        ),
        (
            "class WorkflowRunner:\n    def __init__(self): pass\n",
            (
                "class WorkflowLease:\n    runtime_lease_epoch: int\n"
                "class WorkflowLeasePort: pass\n"
            ),
        ),
    ],
)
def test_runner_transform_rejects_sdk_authority_drift(
    tmp_path: Path, runner_source: str, lease_source: str
) -> None:
    sdk_root = _sdk_root(tmp_path)
    (sdk_root / "src/simple_harness/workflow/runner.py").write_text(
        runner_source, encoding="utf-8"
    )
    (sdk_root / "src/simple_harness/workflow/lease.py").write_text(
        lease_source, encoding="utf-8"
    )

    with pytest.raises(symbols.SymbolDispositionError) as caught:
        symbols.validate_runner_authority_transform(ROOT, sdk_root=sdk_root)

    assert caught.value.code == "AUTHORITY_RELOCATION_DRIFT"


@pytest.mark.parametrize("field", ["source_commit", "source_sha256"])
def test_supplemental_rejects_frozen_source_identity_drift(
    tmp_path: Path, field: str
) -> None:
    supplemental = _supplemental_copy(
        tmp_path, lambda value: value.__setitem__(field, "0" * 64)
    )

    with pytest.raises(symbols.SymbolDispositionError) as caught:
        symbols.validate_supplemental_oracle(ROOT, supplemental_path=supplemental)

    assert caught.value.code == "SOURCE_DRIFT"


def test_supplemental_rejects_current_source_byte_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = (ROOT / "backend/deskpet/workflows/errors.py").read_bytes()
    product_source = tmp_path / "backend/deskpet/workflows/errors.py"
    product_source.parent.mkdir(parents=True)
    product_source.write_bytes(source + b"\n# drift\n")
    monkeypatch.setattr(symbols, "_git_show", lambda *_args: source)

    with pytest.raises(symbols.SymbolDispositionError) as caught:
        symbols.validate_supplemental_oracle(tmp_path)

    assert caught.value.code == "SOURCE_DRIFT"


def test_supplemental_rejects_missing_and_extra_symbol(tmp_path: Path) -> None:
    def mutate(value: dict[str, object]) -> None:
        entries = value["entries"]
        assert isinstance(entries, list)
        entries.pop()
        entries.append(
            {
                "symbol": "InventedError",
                "disposition": "sdk_public",
                "target_symbol": "simple_harness.workflow.errors.InventedError",
            }
        )

    supplemental = _supplemental_copy(tmp_path, mutate)
    with pytest.raises(symbols.SymbolDispositionError) as caught:
        symbols.validate_supplemental_oracle(ROOT, supplemental_path=supplemental)

    assert caught.value.code == "UNCLASSIFIED_SYMBOL"


def test_supplemental_rejects_wrong_target_mapping(tmp_path: Path) -> None:
    def mutate(value: dict[str, object]) -> None:
        entries = value["entries"]
        assert isinstance(entries, list)
        entries[0]["target_symbol"] = "simple_harness.workflow.errors.WrongTarget"

    supplemental = _supplemental_copy(tmp_path, mutate)
    with pytest.raises(symbols.SymbolDispositionError) as caught:
        symbols.validate_supplemental_oracle(ROOT, supplemental_path=supplemental)

    assert caught.value.code == "UNCLASSIFIED_SYMBOL"


def test_supplemental_rejects_duplicate_target(tmp_path: Path) -> None:
    def mutate(value: dict[str, object]) -> None:
        entries = value["entries"]
        assert isinstance(entries, list)
        entries[1]["target_symbol"] = entries[0]["target_symbol"]

    supplemental = _supplemental_copy(tmp_path, mutate)
    with pytest.raises(symbols.SymbolDispositionError) as caught:
        symbols.validate_supplemental_oracle(ROOT, supplemental_path=supplemental)

    assert caught.value.code == "DUPLICATE_TARGET_SYMBOL"


def test_supplemental_rejects_non_public_disposition(tmp_path: Path) -> None:
    def mutate(value: dict[str, object]) -> None:
        entries = value["entries"]
        assert isinstance(entries, list)
        entries[0]["disposition"] = "sdk_private"

    supplemental = _supplemental_copy(tmp_path, mutate)
    with pytest.raises(symbols.SymbolDispositionError) as caught:
        symbols.validate_supplemental_oracle(ROOT, supplemental_path=supplemental)

    assert caught.value.code == "UNCLASSIFIED_SYMBOL"


def test_both_gates_reject_missing_sdk_target(tmp_path: Path) -> None:
    sdk_root = _sdk_root(tmp_path, "class WorkflowErrorCode: pass\n")

    with pytest.raises(symbols.SymbolDispositionError) as symbol_error:
        symbols.verify_repository(ROOT, sdk_root=sdk_root)
    with pytest.raises(oracles.OracleInvariantError) as oracle_error:
        oracles.verify_repository(ROOT, sdk_root=sdk_root)

    assert symbol_error.value.code == "MISSING_SDK_TARGET"
    assert oracle_error.value.code == "MISSING_SDK_TARGET"


@pytest.mark.parametrize(
    "source",
    [
        "class WorkflowNodeError(Exception):\n    pass\n",
        "ERROR_DISPOSITIONS = {}\n",
        "ERROR_DISPOSITIONS: dict = {}\n",
    ],
)
def test_supplemental_rejects_product_definition_or_constant_survivor(
    tmp_path: Path, source: str
) -> None:
    product_root = tmp_path / "product"
    product_root.mkdir()
    (product_root / "survivor.py").write_text(source, encoding="utf-8")

    with pytest.raises(symbols.SymbolDispositionError) as symbol_error:
        symbols.verify_repository(ROOT, final_product_root=product_root)
    with pytest.raises(oracles.OracleInvariantError) as oracle_error:
        oracles.verify_repository(ROOT, final_product_root=product_root)

    assert symbol_error.value.code == "FORBIDDEN_PRODUCT_AUTHORITY_SURVIVOR"
    assert oracle_error.value.code == "FORBIDDEN_PRODUCT_AUTHORITY_SURVIVOR"
