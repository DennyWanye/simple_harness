from __future__ import annotations

import json
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


def test_r45_root_accounting_rejects_unclassified_existing_source(tmp_path: Path) -> None:
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

    assert total == 0
    assert unknown == ["backend/deskpet/harness/ambiguous.py"]


def test_r45_current_state_cannot_regress_and_final_status_is_derived() -> None:
    loc = harness_baseline._orchestration_loc()
    audit = harness_baseline.build_r45_core_audit(loc)

    assert audit["total_loc"] <= 33_618
    assert audit["core_loc"] <= 5_725
    assert audit["kernel_loc"] <= 850
    assert audit["public_operations"] == [
        "start",
        "observe",
        "signal",
        "cancel",
        "recover",
        "close",
    ]
    assert audit["unknown_classifications"] == []

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
        "sys.argv", ["harness_baseline.py", "--loc-only", "--r45-final-gate"]
    )
    assert harness_baseline.main() == 1
