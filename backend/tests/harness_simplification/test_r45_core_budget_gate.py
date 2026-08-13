from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.bench import harness_baseline


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        [harness_baseline._git_executable(), *args], cwd=repo, text=True
    ).strip()


def test_r45_fixture_locks_ten_source_backed_deletion_budgets() -> None:
    fixture = harness_baseline.load_r45_core_budget_fixture()
    budgets = fixture["deletion_budgets"]

    assert len(budgets) == 10
    assert [item["deletion_loc"] for item in budgets] == [
        65,
        38,
        20,
        20,
        18,
        38,
        18,
        12,
        14,
        12,
    ]
    assert sum(item["deletion_loc"] for item in budgets) == 255
    assert all(
        source["path"] and source["symbol"] and len(source["source_hash"]) == 64
        for item in budgets
        for source in item["sources"]
    )
    assert {
        item["path"] for item in fixture["non_core_root_exceptions"]
    } >= {"backend/deskpet/workflows/store/execution_uow.py"}


def test_r45_fixture_source_hash_drift_fails_closed(tmp_path: Path) -> None:
    fixture = json.loads(
        harness_baseline.R45_CORE_BUDGET_FIXTURE.read_text(encoding="utf-8")
    )
    fixture["deletion_budgets"][0]["sources"][0]["source_hash"] = "0" * 64
    tampered = tmp_path / "tampered-budget.json"
    tampered.write_text(json.dumps(fixture), encoding="utf-8")

    with pytest.raises(
        harness_baseline.BenchmarkInvariantError, match="budget source drift"
    ):
        harness_baseline.load_r45_core_budget_fixture(tampered)


def test_r45_new_non_core_exception_hash_drift_fails_closed(
    tmp_path: Path,
) -> None:
    fixture = json.loads(
        harness_baseline.R45_CORE_BUDGET_FIXTURE.read_text(encoding="utf-8")
    )
    added = next(
        item
        for item in fixture["non_core_root_exceptions"]
        if item.get("base_absent") is True
    )
    added["source_hash"] = "0" * 64
    tampered = tmp_path / "tampered-new-exception.json"
    tampered.write_text(json.dumps(fixture), encoding="utf-8")

    with pytest.raises(
        harness_baseline.BenchmarkInvariantError,
        match="new non-core root exception drift",
    ):
        harness_baseline.load_r45_core_budget_fixture(tampered)


def _migration_fixture(
    *,
    target: int = 100,
    physical: int = 10,
    effective: int = 10,
    group: str = "legacy_agent_loop",
) -> dict[str, object]:
    return {
        "target_total_loc": target,
        "baseline_noncohort_raw_effective_loc": target - effective,
        "cohorts": [
            {
                "id": "test-cohort",
                "baseline_physical_loc": physical,
                "baseline_raw_effective_loc": effective,
                "paths": [{"path": "backend/cohort.py", "group": group}],
            }
        ],
    }


def _migration_row(
    *, physical: int, effective: int, group: str = "legacy_agent_loop"
) -> dict[str, object]:
    return {
        "path": "backend/cohort.py",
        "counted": True,
        "group": group,
        "current_loc": physical,
        "effective_loc": effective,
    }


def test_r45_migration_formula_covers_base_and_independent_deltas() -> None:
    fixture = _migration_fixture()
    baseline = harness_baseline.calculate_r45_migration_adjustment(
        [_migration_row(physical=10, effective=10)],
        fixture,
        raw_total_loc=100,
    )
    assert baseline["overcount_loc"] == 0
    assert baseline["adjusted_total_loc"] == 100

    cohort_growth = harness_baseline.calculate_r45_migration_adjustment(
        [_migration_row(physical=13, effective=13)],
        fixture,
        raw_total_loc=103,
    )
    assert cohort_growth["overcount_loc"] == 0
    assert cohort_growth["adjusted_total_loc"] == 103

    noncohort_growth = harness_baseline.calculate_r45_migration_adjustment(
        [_migration_row(physical=10, effective=10)],
        fixture,
        raw_total_loc=103,
    )
    assert noncohort_growth["overcount_loc"] == 0
    assert noncohort_growth["adjusted_total_loc"] == 103

    cohort_deletion = harness_baseline.calculate_r45_migration_adjustment(
        [_migration_row(physical=7, effective=7)],
        fixture,
        raw_total_loc=97,
    )
    assert cohort_deletion["adjusted_total_loc"] == 97

    # A shared-foundation deletion changes physical LOC but not its raw
    # positive-additions contribution. The replacement formula still credits
    # exactly the real physical deletion, not the unrelated shared baseline.
    shared_fixture = _migration_fixture(physical=10, effective=2)
    shared_deletion = harness_baseline.calculate_r45_migration_adjustment(
        [_migration_row(physical=7, effective=2)],
        shared_fixture,
        raw_total_loc=100,
    )
    assert shared_deletion["overcount_loc"] == 3
    assert shared_deletion["adjusted_total_loc"] == 97


def test_r45_migration_formula_fails_on_missing_group_or_invalid_snapshot() -> None:
    fixture = _migration_fixture()
    with pytest.raises(
        harness_baseline.BenchmarkInvariantError, match="path is not counted"
    ):
        harness_baseline.calculate_r45_migration_adjustment(
            [], fixture, raw_total_loc=100
        )
    with pytest.raises(harness_baseline.BenchmarkInvariantError, match="group drift"):
        harness_baseline.calculate_r45_migration_adjustment(
            [_migration_row(physical=10, effective=10, group="wrong")],
            fixture,
            raw_total_loc=100,
        )
    with pytest.raises(harness_baseline.BenchmarkInvariantError, match="negative"):
        harness_baseline.calculate_r45_migration_adjustment(
            [_migration_row(physical=10, effective=10)],
            fixture,
            raw_total_loc=9,
        )


def test_r45_migration_fixture_locks_current_adjustment() -> None:
    fixture = harness_baseline.load_r45_migration_cohort_fixture()
    assert fixture["target_total_loc"] == 33_618
    assert fixture["baseline_physical_loc"] == 5_236
    assert fixture["baseline_raw_effective_loc"] == 4_651
    assert fixture["baseline_noncohort_raw_effective_loc"] == 28_967

    loc = harness_baseline._orchestration_loc()
    audit = harness_baseline.build_r45_core_audit(loc)
    assert audit["migration_overcount_loc"] == 507
    assert audit["total_loc"] == audit["raw_total_loc"] - 507


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda value: value["cohorts"][0]["paths"][0].__setitem__(
                "base_sha256", "0" * 64
            ),
            "source drift",
        ),
        (
            lambda value: value["cohorts"][0]["paths"][0].__setitem__(
                "base_git_blob", "0" * 40
            ),
            "source drift",
        ),
        (
            lambda value: value["cohorts"][0]["paths"][0].__setitem__(
                "group", "wrong"
            ),
            "source drift",
        ),
        (
            lambda value: value["cohorts"][0].__setitem__("reason", "wrong"),
            "reason drift",
        ),
        (
            lambda value: value.__setitem__("target_total_loc", 33_619),
            "target drift",
        ),
        (
            lambda value: value.__setitem__("baseline_physical_loc", 5_237),
            "total drift",
        ),
    ],
)
def test_r45_migration_fixture_tamper_fails_closed(
    tmp_path: Path, mutate, message: str
) -> None:
    fixture = json.loads(
        harness_baseline.R45_MIGRATION_COHORT_FIXTURE.read_text(encoding="utf-8")
    )
    mutate(fixture)
    tampered = tmp_path / "tampered-cohort.json"
    tampered.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(harness_baseline.BenchmarkInvariantError, match=message):
        harness_baseline.load_r45_migration_cohort_fixture(tampered)


def test_r45_migration_physical_loc_rejects_statement_compression() -> None:
    with pytest.raises(
        harness_baseline.BenchmarkInvariantError, match="compressed multi-statement"
    ):
        harness_baseline._assert_no_statement_line_compression(
            b"first = 1; second = 2\n", "backend/cohort.py"
        )
    harness_baseline._assert_no_statement_line_compression(
        b"first = 1\nsecond = 2\n", "backend/cohort.py"
    )


def test_r45_migration_real_move_and_copy_are_non_evadable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = repo / "backend/cohort.py"
    source.parent.mkdir(parents=True)
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.email", "bench@example.invalid")
    _git(repo, "config", "user.name", "Harness Benchmark")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    content = source.read_bytes()
    manifest = {
        "expected_total_loc": 3,
        "files": [
            {
                "path": "backend/cohort.py",
                "loc": 3,
                "sha256": harness_baseline._sha256(content),
                "git_blob": _git(repo, "rev-parse", f"{base}:backend/cohort.py"),
                "group": "legacy_agent_loop",
            }
        ],
    }
    fixture = _migration_fixture(target=3, physical=3, effective=3)

    moved = repo / "backend/new_production.py"
    source.rename(moved)
    move_inventory = harness_baseline.build_current_loc_inventory(
        manifest, repo=repo, base_commit=base
    )
    move_adjustment = harness_baseline.calculate_r45_migration_adjustment(
        move_inventory["files"], fixture, raw_total_loc=move_inventory["current_total"]
    )
    assert move_inventory["current_total"] == 3
    assert move_adjustment["adjusted_total_loc"] == 3

    moved.rename(source)
    shutil.copyfile(source, moved)
    copy_inventory = harness_baseline.build_current_loc_inventory(
        manifest, repo=repo, base_commit=base
    )
    copy_adjustment = harness_baseline.calculate_r45_migration_adjustment(
        copy_inventory["files"], fixture, raw_total_loc=copy_inventory["current_total"]
    )
    assert copy_inventory["current_total"] == 6
    assert copy_adjustment["adjusted_total_loc"] == 6

    moved.unlink()
    source.unlink()
    deleted_inventory = harness_baseline.build_current_loc_inventory(
        manifest, repo=repo, base_commit=base
    )
    deleted_adjustment = harness_baseline.calculate_r45_migration_adjustment(
        deleted_inventory["files"],
        fixture,
        raw_total_loc=deleted_inventory["current_total"],
    )
    assert deleted_adjustment["adjusted_total_loc"] == 0


def test_r45_root_accounting_counts_new_and_non_core_additions(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    core = repo / "backend/deskpet/harness/core.py"
    exception = repo / "backend/deskpet/harness/team.py"
    core.parent.mkdir(parents=True)
    core.write_text("a\nb\nc\n", encoding="utf-8")
    exception.write_text("legacy\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.email", "bench@example.invalid")
    _git(repo, "config", "user.name", "Harness Benchmark")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    exception.write_text("legacy\nnew-owner-line\n", encoding="utf-8")
    new_core = repo / "backend/deskpet/harness/new_owner.py"
    new_core.write_text("new\nowner\n", encoding="utf-8")
    rows = [
        {
            "path": "backend/deskpet/harness/core.py",
            "counted": True,
            "group": "harness_core",
            "effective_loc": 3,
            "current_loc": 3,
        },
        {
            "path": "backend/deskpet/harness/team.py",
            "counted": True,
            "group": "plan_backend_production",
            "effective_loc": 2,
            "current_loc": 2,
        },
        {
            "path": "backend/deskpet/harness/new_owner.py",
            "counted": True,
            "group": "plan_backend_production",
            "effective_loc": 2,
            "current_loc": 2,
        },
        {
            "path": "backend/shared/moved_core.py",
            "counted": True,
            "group": "execution_core",
            "effective_loc": 4,
            "current_loc": 4,
        },
    ]

    total, attributions, unknown = harness_baseline._r45_account_root_rows(
        rows,
        repo=repo,
        base_commit=base,
        core_groups=("execution_core", "harness_core"),
        core_roots=("backend/deskpet/execution/", "backend/deskpet/harness/"),
        exceptions={"backend/deskpet/harness/team.py": {}},
    )

    assert total == 10
    assert unknown == []
    assert {item["reason"] for item in attributions} == {
        "loc_group:harness_core",
        "loc_group:execution_core",
        "non_core_baseline_positive_additions",
        "new_core_path",
    }


def test_r45_root_accounting_counts_existing_source_inside_frozen_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    source = repo / "backend/deskpet/harness/ambiguous.py"
    source.parent.mkdir(parents=True)
    source.write_text("existing\n", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.email", "bench@example.invalid")
    _git(repo, "config", "user.name", "Harness Benchmark")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    total, _attributions, unknown = harness_baseline._r45_account_root_rows(
        [
            {
                "path": "backend/deskpet/harness/ambiguous.py",
                "counted": True,
                "group": "shared_foundation_additions",
                "effective_loc": 0,
                "current_loc": 1,
            }
        ],
        repo=repo,
        base_commit=base,
        core_groups=("execution_core", "harness_core"),
        core_roots=("backend/deskpet/execution/", "backend/deskpet/harness/"),
        exceptions={},
    )

    assert total == 1
    assert unknown == []


def test_non_core_import_only_change_does_not_flag_unchanged_method() -> None:
    base = b"""import old_module\n\nclass WorkflowLauncher:\n    def recover_pending(self):\n        value = 1\n        value += 1\n        return value\n"""
    current = base.replace(b"import old_module", b"import new_module")

    moved, unknown = harness_baseline._r45_classify_non_core_symbols(
        path="backend/deskpet/workflows/launcher.py",
        content=current,
        base_content=base,
        base_symbol_hashes={},
        base_symbols_by_name={"recover_pending": {"core-hash"}},
    )

    assert moved == []
    assert unknown == []


@pytest.mark.parametrize(
    "path",
    [
        "backend/deskpet/types/task_grants.py",
        "backend/deskpet/workflows/proposal_state.py",
    ],
)
def test_explicit_shared_contract_files_do_not_masquerade_as_core(
    path: str,
) -> None:
    content = b"""class SharedContract:
    def to_dict(self):
        value = 1
        value += 1
        return value
"""
    moved, unknown = harness_baseline._r45_classify_non_core_symbols(
        path=path,
        content=content,
        base_content=None,
        base_symbol_hashes={},
        base_symbols_by_name={"to_dict": {"core-hash"}},
    )

    assert moved == []
    assert unknown == []


def test_non_core_same_named_method_change_still_fails_closed() -> None:
    base = b"""class WorkflowLauncher:\n    def recover_pending(self):\n        value = 1\n        value += 1\n        return value\n"""
    current = base.replace(b"value = 1", b"value = 2")

    moved, unknown = harness_baseline._r45_classify_non_core_symbols(
        path="backend/deskpet/workflows/launcher.py",
        content=current,
        base_content=base,
        base_symbol_hashes={},
        base_symbols_by_name={"recover_pending": {"core-hash"}},
    )

    assert moved == []
    assert unknown == [
        "backend/deskpet/workflows/launcher.py::WorkflowLauncher.recover_pending"
    ]


def test_current_harness_state_has_an_explicit_bounded_expansion() -> None:
    loc = harness_baseline._orchestration_loc()
    audit = harness_baseline.build_r45_core_audit(loc)

    construction = harness_baseline.validate_harness_boundary_construction_gate(
        audit
    )
    assert construction["passed"], construction["checks"]

    final = harness_baseline.validate_r45_final_gate(audit)
    assert final["passed"] is all(final["checks"].values())


def test_r45_cli_modes_propagate_gate_exit_status(monkeypatch) -> None:
    audit = {
        "total_loc": 33_618,
        "core_loc": 5_725,
        "kernel_loc": 820,
        "public_operations": list(harness_baseline.R45_PUBLIC_OPERATIONS),
        "unknown_classifications": [],
        "deletion_budget_loc": 255,
    }
    monkeypatch.setattr(
        harness_baseline,
        "_orchestration_loc",
        lambda: {"current_total": 33_618},
    )
    monkeypatch.setattr(harness_baseline, "build_r45_core_audit", lambda _loc: audit)
    monkeypatch.setattr(harness_baseline, "_git_commit", lambda: "test-head")

    monkeypatch.setattr(
        "sys.argv", ["harness_baseline.py", "--loc-only", "--r45-baseline"]
    )
    assert harness_baseline.main() == 0

    monkeypatch.setattr(
        "sys.argv", ["harness_baseline.py", "--loc-only", "--r45-transition-gate"]
    )
    assert harness_baseline.main() == 0

    monkeypatch.setattr(
        "sys.argv", ["harness_baseline.py", "--loc-only", "--r45-final-gate"]
    )
    assert harness_baseline.main() == 1


def test_r45_transition_gate_rejects_peak_growth() -> None:
    audit = {
        "total_loc": harness_baseline.R45_TRANSITIONAL_TOTAL_LOC + 1,
        "core_loc": 5_725,
        "kernel_loc": 820,
        "public_operations": list(harness_baseline.R45_PUBLIC_OPERATIONS),
        "unknown_classifications": [],
        "deletion_budget_loc": 255,
    }

    gate = harness_baseline.validate_r45_transition_gate(audit)

    assert gate["passed"] is False
    assert gate["checks"]["total_loc_lte_34300"] is False
