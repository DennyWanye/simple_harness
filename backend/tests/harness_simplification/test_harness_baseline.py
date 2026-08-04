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


def test_locked_manifests_reproduce_both_approved_totals() -> None:
    phase0 = harness_baseline.load_and_verify_manifest(
        harness_baseline.PHASE0_MANIFEST
    )
    rollback = harness_baseline.load_and_verify_manifest(
        harness_baseline.ROLLBACK_MANIFEST
    )
    assert phase0["commit"] == harness_baseline.PHASE0_COMMIT
    assert phase0["expected_total_loc"] == 21_563
    assert rollback["commit"] == harness_baseline.ROLLBACK_COMMIT
    assert rollback["expected_total_loc"] == 33_228


def test_manifest_drift_fails_closed(tmp_path: Path) -> None:
    manifest = json.loads(harness_baseline.PHASE0_MANIFEST.read_text(encoding="utf-8"))
    manifest["files"][0]["loc"] += 1
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(harness_baseline.BenchmarkInvariantError, match="manifest drift"):
        harness_baseline.load_and_verify_manifest(tampered)


def test_manifest_path_or_group_substitution_fails_closed(tmp_path: Path) -> None:
    manifest = json.loads(harness_baseline.PHASE0_MANIFEST.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "backend/deskpet/retrieval/runtime.py"
    tampered_path = tmp_path / "path-tampered.json"
    tampered_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(harness_baseline.BenchmarkInvariantError, match="path/group drift"):
        harness_baseline.load_and_verify_manifest(tampered_path)

    manifest = json.loads(harness_baseline.PHASE0_MANIFEST.read_text(encoding="utf-8"))
    manifest["files"][0]["group"] = "unrelated"
    tampered_group = tmp_path / "group-tampered.json"
    tampered_group.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(harness_baseline.BenchmarkInvariantError, match="path/group drift"):
        harness_baseline.load_and_verify_manifest(tampered_group)


def test_lifecycle_probe_refuses_registry_fallback() -> None:
    class IncompleteKernel:
        async def start(self):
            return None

    class IncompleteUow:
        async def create(self):
            return None

    with pytest.raises(
        harness_baseline.BenchmarkInvariantError,
        match="refusing legacy registry fallback",
    ):
        harness_baseline.validate_run_lifecycle_api(IncompleteKernel, IncompleteUow)


@pytest.mark.asyncio
async def test_completed_run_probe_uses_real_kernel_uow_final_and_close() -> None:
    result = await harness_baseline._completed_run_memory_probe(25)
    assert result["probe"] == "RunKernel.start -> UoW.finalize -> RunKernel.close"
    assert result["kernel_start_calls"] == 25
    assert result["uow_terminal_rows"] == 25
    assert result["uow_final_events"] == 25
    assert result["kernel_close_calls"] == 25
    assert result["completed_run_strong_refs"] == 0


def test_lifecycle_probe_has_no_compatibility_ledger_import() -> None:
    source = Path(harness_baseline.__file__).read_text(encoding="utf-8")
    assert "execution.ledger" not in source
    assert "ExecutionLedger(" not in source


def test_compare_rejects_legacy_or_scaled_lifecycle_baselines() -> None:
    current = json.loads(
        (harness_baseline.PLAN_DIR / "r0-benchmark.json").read_text(encoding="utf-8")
    )
    legacy = json.loads(
        harness_baseline.LEGACY_PHASE0_BENCHMARK.read_text(encoding="utf-8")
    )
    with pytest.raises(harness_baseline.BenchmarkInvariantError, match="schema must be 2"):
        harness_baseline.compare(current, legacy)

    scaled = json.loads(json.dumps(current))
    scaled["ten_thousand_completed_runs"]["runs"] = 1
    scaled["ten_thousand_completed_runs"]["kernel_start_calls"] = 1
    with pytest.raises(harness_baseline.BenchmarkInvariantError, match="canonical 10k"):
        harness_baseline.compare(scaled, current)

    current["live_stream"] = {
        "token_delta_sqlite_writes": 0,
        "metadata_bytes_per_active_run": 128 * 1024,
    }
    canonical = harness_baseline.compare(current, current)
    assert canonical["checks"]["completed_run_strong_refs_not_increased"] is True
    assert canonical["checks"]["orchestration_raw_loc_lt_33925"] is True
    assert canonical["checks"]["event_loop_lag_p99_lte_20ms"] is True
    assert canonical["checks"]["event_loop_throughput_within_10pct"] is True
    assert canonical["checks"]["token_delta_sqlite_writes_eq_zero"] is True
    assert canonical["checks"]["kernel_metadata_lte_128kb_per_active_run"] is True


def test_loc_inventory_counts_renames_and_untracked_but_excludes_tests(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "bench@example.invalid")
    _git(repo, "config", "user.name", "Harness Benchmark")
    source = repo / "backend/original.py"
    source.parent.mkdir(parents=True)
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    content = source.read_bytes()
    blob = _git(repo, "rev-parse", f"{base}:backend/original.py")
    manifest = {
        "expected_total_loc": 3,
        "files": [
            {
                "path": "backend/original.py",
                "loc": 3,
                "sha256": harness_baseline._sha256(content),
                "git_blob": blob,
                "group": "legacy_agent_loop",
            }
        ],
    }
    moved = repo / "backend/tests/moved.py"
    moved.parent.mkdir(parents=True)
    moved.write_bytes(source.read_bytes())
    source.unlink()
    (repo / "backend/new.py").write_text("new\nfile\n", encoding="utf-8")
    tests = repo / "backend/tests/ignored.py"
    tests.write_text("ignore\nme\nforever\n", encoding="utf-8")

    result = harness_baseline.build_current_loc_inventory(
        manifest, repo=repo, base_commit=base
    )
    rows = {row["path"]: row for row in result["files"]}
    assert result["current_total"] == 5
    assert rows["backend/tests/moved.py"]["counted"] is True
    assert (
        rows["backend/tests/moved.py"]["reason"]
        == "rename_or_copy_of:backend/original.py"
    )
    assert rows["backend/new.py"]["counted"] is True
    assert rows["backend/tests/ignored.py"]["counted"] is False
    assert result["unknown_classifications"] == []


def test_loc_inventory_counts_committed_copy_into_excluded_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "bench@example.invalid")
    _git(repo, "config", "user.name", "Harness Benchmark")
    source = repo / "backend/original.py"
    source.parent.mkdir(parents=True)
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    content = source.read_bytes()
    manifest = {
        "expected_total_loc": 3,
        "files": [
            {
                "path": "backend/original.py",
                "loc": 3,
                "sha256": harness_baseline._sha256(content),
                "git_blob": _git(repo, "rev-parse", f"{base}:backend/original.py"),
                "group": "legacy_agent_loop",
            }
        ],
    }
    copied = repo / "backend/vendor/copied.py"
    copied.parent.mkdir(parents=True)
    copied.write_bytes(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "copy into excluded path")

    result = harness_baseline.build_current_loc_inventory(
        manifest, repo=repo, base_commit=base
    )
    rows = {row["path"]: row for row in result["files"]}
    assert result["current_total"] == 6
    assert rows["backend/vendor/copied.py"]["counted"] is True
    assert rows["backend/vendor/copied.py"]["renamed_from"] == "backend/original.py"


def test_loc_inventory_preserves_unicode_paths_in_all_git_states(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "bench@example.invalid")
    _git(repo, "config", "user.name", "Harness Benchmark")
    baseline = repo / "backend/base.py"
    dirty = repo / "backend/待修改.py"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("base\n", encoding="utf-8")
    dirty.write_text("old\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    content = baseline.read_bytes()
    manifest = {
        "expected_total_loc": 1,
        "files": [
            {
                "path": "backend/base.py",
                "loc": 1,
                "sha256": harness_baseline._sha256(content),
                "git_blob": _git(repo, "rev-parse", f"{base}:backend/base.py"),
                "group": "legacy_agent_loop",
            }
        ],
    }
    committed = repo / "backend/已提交.py"
    committed.write_text("committed\nmodule\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "unicode committed")
    dirty.write_text("dirty\nmodule\n", encoding="utf-8")
    untracked = repo / "backend/新模块.py"
    untracked.write_text("new\nuntracked\nmodule\n", encoding="utf-8")

    result = harness_baseline.build_current_loc_inventory(
        manifest, repo=repo, base_commit=base
    )
    rows = {row["path"]: row for row in result["files"]}
    # Existing shared-foundation files contribute positive added lines; the
    # removed BASE line does not offset the two replacement lines.
    assert result["current_total"] == 8
    assert rows["backend/已提交.py"]["current_loc"] == 2
    assert rows["backend/待修改.py"]["current_loc"] == 2
    assert rows["backend/新模块.py"]["current_loc"] == 3


def test_loc_inventory_charges_only_positive_growth_for_existing_shared_foundation(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "bench@example.invalid")
    _git(repo, "config", "user.name", "Harness Benchmark")
    harness = repo / "backend/harness.py"
    shared = repo / "backend/shared.py"
    harness.parent.mkdir(parents=True)
    harness.write_text("harness\n", encoding="utf-8")
    shared.write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    harness_content = harness.read_bytes()
    manifest = {
        "expected_total_loc": 1,
        "files": [
            {
                "path": "backend/harness.py",
                "loc": 1,
                "sha256": harness_baseline._sha256(harness_content),
                "git_blob": _git(repo, "rev-parse", f"{base}:backend/harness.py"),
                "group": "legacy_agent_loop",
            }
        ],
    }

    shared.write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "grow shared foundation")
    result = harness_baseline.build_current_loc_inventory(
        manifest, repo=repo, base_commit=base
    )
    row = next(item for item in result["files"] if item["path"] == "backend/shared.py")
    assert row["group"] == "shared_foundation_additions"
    assert row["baseline_loc"] == 3
    assert row["current_loc"] == 5
    assert row["effective_loc"] == 2
    assert row["baseline_sha256"] == harness_baseline._sha256(b"one\ntwo\nthree\n")
    assert result["current_total"] == 3

    # Deleting unrelated shared-foundation lines cannot buy down harness LOC.
    shared.write_text("one\n", encoding="utf-8")
    result = harness_baseline.build_current_loc_inventory(
        manifest, repo=repo, base_commit=base
    )
    row = next(item for item in result["files"] if item["path"] == "backend/shared.py")
    assert row["effective_loc"] == 0
    assert result["current_total"] == 1


def test_loc_inventory_charges_partial_manifest_transplant_into_shared_file(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "bench@example.invalid")
    _git(repo, "config", "user.name", "Harness Benchmark")
    harness = repo / "backend/harness.py"
    shared = repo / "backend/shared.py"
    harness.parent.mkdir(parents=True)
    harness.write_text("moved-one\nmoved-two\nmoved-three\n", encoding="utf-8")
    shared.write_text("old-one\nold-two\nold-three\nkeep-a\nkeep-b\nkeep-c\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    content = harness.read_bytes()
    manifest = {
        "expected_total_loc": 3,
        "files": [
            {
                "path": "backend/harness.py",
                "loc": 3,
                "sha256": harness_baseline._sha256(content),
                "git_blob": _git(repo, "rev-parse", f"{base}:backend/harness.py"),
                "group": "legacy_agent_loop",
            }
        ],
    }

    harness.unlink()
    shared.write_text(
        "moved-one\nmoved-two\nmoved-three\nkeep-a\nkeep-b\nkeep-c\n",
        encoding="utf-8",
    )
    result = harness_baseline.build_current_loc_inventory(
        manifest, repo=repo, base_commit=base
    )
    row = next(item for item in result["files"] if item["path"] == "backend/shared.py")
    assert row["baseline_loc"] == 6
    assert row["current_loc"] == 6
    assert row["effective_loc"] == 3
    assert result["current_total"] == 3


def test_r1_loc_gate_fails_above_rollback_baseline(monkeypatch) -> None:
    failed = harness_baseline.validate_r1_loc_gate(
        {"current_total": harness_baseline.ROLLBACK_EXPECTED_LOC + 1}
    )
    assert failed["passed"] is False

    monkeypatch.setattr(
        harness_baseline,
        "_orchestration_loc",
        lambda: {"current_total": harness_baseline.ROLLBACK_EXPECTED_LOC + 1},
    )
    monkeypatch.setattr(harness_baseline, "_git_commit", lambda: "test-head")
    monkeypatch.setattr(
        "sys.argv", ["harness_baseline.py", "--loc-only", "--r1-gate"]
    )
    assert harness_baseline.main() == 1


def test_r55_construction_gate_checks_raw_adjusted_core_and_kernel() -> None:
    passing = harness_baseline.validate_r55_construction_gate(
        {
            "raw_total_loc": 34_800,
            "total_loc": 34_250,
            "core_loc": 5_950,
            "kernel_loc": 925,
            "public_operations": harness_baseline.R45_PUBLIC_OPERATIONS,
            "unknown_classifications": [],
            "deletion_budget_loc": 255,
        }
    )
    assert passing["passed"] is True

    for field, limit in (
        ("raw_total_loc", 34_800),
        ("total_loc", 34_250),
        ("core_loc", 5_950),
        ("kernel_loc", 925),
    ):
        values = {
            "raw_total_loc": 34_800,
            "total_loc": 34_250,
            "core_loc": 5_950,
            "kernel_loc": 925,
            "public_operations": harness_baseline.R45_PUBLIC_OPERATIONS,
            "unknown_classifications": [],
            "deletion_budget_loc": 255,
        }
        values[field] = limit + 1
        assert harness_baseline.validate_r55_construction_gate(values)["passed"] is False


def test_r6_final_gate_uses_locked_r55_core_as_dynamic_ceiling() -> None:
    current = {
        "raw_total_loc": 33_924,
        "total_loc": 33_415,
        "core_loc": 5_800,
        "kernel_loc": 900,
        "public_operations": harness_baseline.R45_PUBLIC_OPERATIONS,
        "unknown_classifications": [],
        "deletion_budget_loc": 255,
    }
    budget = {"r45_core_audit": {"core_loc": 5_800}}
    assert harness_baseline.validate_r6_final_gate(current, budget)["passed"] is True
    current["core_loc"] = 5_801
    result = harness_baseline.validate_r6_final_gate(current, budget)
    assert result["checks"]["core_loc_lte_r55_a"] is False
    assert result["passed"] is False


def test_r55_budget_loader_verifies_commit_source_hashes(tmp_path, monkeypatch) -> None:
    commit = "a" * 40
    content = b"source\n"
    audit = {
        "raw_total_loc": 34_000,
        "total_loc": 33_500,
        "core_loc": 5_700,
        "kernel_loc": 800,
        "public_operations": harness_baseline.R45_PUBLIC_OPERATIONS,
        "unknown_classifications": [],
        "deletion_budget_loc": 255,
    }
    budget = {
        "schema_version": 2,
        "environment": {"commit": commit},
        "orchestration_loc": {
            "head_commit": commit,
            "files": [
                {
                    "path": "backend/example.py",
                    "counted": True,
                    "current_sha256": harness_baseline._sha256(content),
                    "current_git_blob": "blob-a",
                }
            ],
        },
        "r45_core_audit": audit,
        "r55_gate": {"passed": True},
    }
    path = tmp_path / "budget.json"
    path.write_text(json.dumps(budget), encoding="utf-8")
    monkeypatch.setattr(harness_baseline, "_commit_content", lambda *_a, **_k: content)
    monkeypatch.setattr(
        harness_baseline,
        "_git",
        lambda *args, **_kwargs: commit if "^{commit}" in args[-1] else "blob-a",
    )
    assert harness_baseline.load_r55_admission_budget(path)["environment"]["commit"] == commit
    budget["orchestration_loc"]["files"][0]["current_sha256"] = "bad"
    path.write_text(json.dumps(budget), encoding="utf-8")
    with pytest.raises(harness_baseline.BenchmarkInvariantError, match="source drift"):
        harness_baseline.load_r55_admission_budget(path)


def test_r55_budget_loader_does_not_treat_excluded_tests_as_source_lock(tmp_path, monkeypatch) -> None:
    commit = "a" * 40
    audit = {
        "raw_total_loc": 34_000, "total_loc": 33_500,
        "core_loc": 5_700, "kernel_loc": 800,
        "public_operations": harness_baseline.R45_PUBLIC_OPERATIONS,
        "unknown_classifications": [], "deletion_budget_loc": 255,
    }
    budget = {
        "schema_version": 2, "environment": {"commit": commit},
        "orchestration_loc": {"head_commit": commit, "files": [
            {"path": "backend/production.py", "counted": True,
             "current_sha256": harness_baseline._sha256(b"source\n"),
             "current_git_blob": "blob-a"},
            {"path": "backend/tests/test_generated.py", "counted": False,
             "current_sha256": "later-working-tree-hash",
             "current_git_blob": "later-working-tree-blob"},
        ]},
        "r45_core_audit": audit, "r55_gate": {"passed": True},
    }
    path = tmp_path / "budget.json"
    path.write_text(json.dumps(budget), encoding="utf-8")
    monkeypatch.setattr(harness_baseline, "_commit_content", lambda *_a, **_k: b"source\n")
    monkeypatch.setattr(
        harness_baseline, "_git",
        lambda *args, **_kwargs: commit if "^{commit}" in args[-1] else "blob-a",
    )

    assert harness_baseline.load_r55_admission_budget(path)["environment"]["commit"] == commit


def test_loc_inventory_counts_ignored_new_backend_production(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "bench@example.invalid")
    _git(repo, "config", "user.name", "Harness Benchmark")
    harness = repo / "backend/harness.py"
    harness.parent.mkdir(parents=True)
    harness.write_text("harness\n", encoding="utf-8")
    (repo / ".gitignore").write_text("backend/hidden.py\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    content = harness.read_bytes()
    manifest = {
        "expected_total_loc": 1,
        "files": [
            {
                "path": "backend/harness.py",
                "loc": 1,
                "sha256": harness_baseline._sha256(content),
                "git_blob": _git(repo, "rev-parse", f"{base}:backend/harness.py"),
                "group": "legacy_agent_loop",
            }
        ],
    }
    hidden = repo / "backend/hidden.py"
    hidden.write_text("hidden\nproduction\nlogic\n", encoding="utf-8")

    result = harness_baseline.build_current_loc_inventory(
        manifest, repo=repo, base_commit=base
    )
    row = next(item for item in result["files"] if item["path"] == "backend/hidden.py")
    assert row["git_change"] == "!"
    assert row["reason"] == "new_backend_production"
    assert row["effective_loc"] == 3
    assert result["current_total"] == 4


def test_loc_inventory_counts_manifest_copy_to_ignored_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "bench@example.invalid")
    _git(repo, "config", "user.name", "Harness Benchmark")
    harness = repo / "backend/harness.py"
    harness.parent.mkdir(parents=True)
    harness.write_text("one\ntwo\nthree\n", encoding="utf-8")
    (repo / ".gitignore").write_text("backend/vendor/\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    content = harness.read_bytes()
    manifest = {
        "expected_total_loc": 3,
        "files": [
            {
                "path": "backend/harness.py",
                "loc": 3,
                "sha256": harness_baseline._sha256(content),
                "git_blob": _git(repo, "rev-parse", f"{base}:backend/harness.py"),
                "group": "legacy_agent_loop",
            }
        ],
    }
    harness.unlink()
    copied = repo / "backend/vendor/runtime.py"
    copied.parent.mkdir(parents=True)
    copied.write_bytes(b"# wrapper\n" + content)

    result = harness_baseline.build_current_loc_inventory(
        manifest, repo=repo, base_commit=base
    )
    row = next(
        item for item in result["files"] if item["path"] == "backend/vendor/runtime.py"
    )
    assert row["git_change"] == "!"
    assert row["renamed_from"] == "backend/harness.py"
    assert row["effective_loc"] == 4
    assert result["current_total"] == 4


def test_bulk_local_environment_is_an_explicit_non_source_boundary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "bench@example.invalid")
    _git(repo, "config", "user.name", "Harness Benchmark")
    harness = repo / "backend/harness.py"
    harness.parent.mkdir(parents=True)
    harness.write_text("harness\n", encoding="utf-8")
    (repo / ".gitignore").write_text("backend/.venv/\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    content = harness.read_bytes()
    manifest = {
        "expected_total_loc": 1,
        "files": [
            {
                "path": "backend/harness.py",
                "loc": 1,
                "sha256": harness_baseline._sha256(content),
                "git_blob": _git(repo, "rev-parse", f"{base}:backend/harness.py"),
                "group": "legacy_agent_loop",
            }
        ],
    }
    package = repo / "backend/.venv/Lib/site-packages/dependency.py"
    package.parent.mkdir(parents=True)
    package.write_text("third\nparty\npackage\n", encoding="utf-8")

    result = harness_baseline.build_current_loc_inventory(
        manifest, repo=repo, base_commit=base
    )
    assert all(".venv" not in item["path"] for item in result["files"])
    assert result["current_total"] == 1


def test_ignored_vendor_copy_prefilter_preserves_repeated_line_coverage(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "bench@example.invalid")
    _git(repo, "config", "user.name", "Harness Benchmark")
    harness = repo / "backend/harness.py"
    harness.parent.mkdir(parents=True)
    source_text = "repeat\n" * 8 + "unique-one\nunique-two\n"
    harness.write_text(source_text, encoding="utf-8")
    (repo / ".gitignore").write_text("backend/vendor/\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    content = harness.read_bytes()
    manifest = {
        "expected_total_loc": 10,
        "files": [
            {
                "path": "backend/harness.py",
                "loc": 10,
                "sha256": harness_baseline._sha256(content),
                "git_blob": _git(repo, "rev-parse", f"{base}:backend/harness.py"),
                "group": "legacy_agent_loop",
            }
        ],
    }
    harness.unlink()
    copied = repo / "backend/vendor/runtime.py"
    copied.parent.mkdir(parents=True)
    copied.write_text("wrapper\n" + "repeat\n" * 8, encoding="utf-8")

    result = harness_baseline.build_current_loc_inventory(
        manifest, repo=repo, base_commit=base
    )
    row = next(item for item in result["files"] if item["path"] == "backend/vendor/runtime.py")
    assert row["renamed_from"] == "backend/harness.py"
    assert row["effective_loc"] == 9
    assert result["current_total"] == 9


def test_shared_additions_survive_rm_cached_and_ignore(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "bench@example.invalid")
    _git(repo, "config", "user.name", "Harness Benchmark")
    harness = repo / "backend/harness.py"
    shared = repo / "backend/shared.py"
    harness.parent.mkdir(parents=True)
    harness.write_text("harness\n", encoding="utf-8")
    shared.write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    content = harness.read_bytes()
    manifest = {
        "expected_total_loc": 1,
        "files": [
            {
                "path": "backend/harness.py",
                "loc": 1,
                "sha256": harness_baseline._sha256(content),
                "git_blob": _git(repo, "rev-parse", f"{base}:backend/harness.py"),
                "group": "legacy_agent_loop",
            }
        ],
    }
    _git(repo, "rm", "--cached", "backend/shared.py")
    (repo / ".gitignore").write_text("backend/shared.py\n", encoding="utf-8")
    shared.write_text("base\nadded-one\nadded-two\n", encoding="utf-8")

    result = harness_baseline.build_current_loc_inventory(
        manifest, repo=repo, base_commit=base
    )
    row = next(item for item in result["files"] if item["path"] == "backend/shared.py")
    assert row["effective_loc"] == 2
    assert result["current_total"] == 3


def test_loc_inventory_fails_closed_on_assume_unchanged_index_flag(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "bench@example.invalid")
    _git(repo, "config", "user.name", "Harness Benchmark")
    harness = repo / "backend/harness.py"
    shared = repo / "backend/shared.py"
    harness.parent.mkdir(parents=True)
    harness.write_text("harness\n", encoding="utf-8")
    shared.write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    content = harness.read_bytes()
    manifest = {
        "expected_total_loc": 1,
        "files": [
            {
                "path": "backend/harness.py",
                "loc": 1,
                "sha256": harness_baseline._sha256(content),
                "git_blob": _git(repo, "rev-parse", f"{base}:backend/harness.py"),
                "group": "legacy_agent_loop",
            }
        ],
    }
    _git(repo, "update-index", "--assume-unchanged", "backend/shared.py")
    shared.write_text("base\nadded-one\nadded-two\n", encoding="utf-8")

    with pytest.raises(
        harness_baseline.BenchmarkInvariantError,
        match="hidden Git index flags on production Python: backend/shared.py",
    ):
        harness_baseline.build_current_loc_inventory(
            manifest, repo=repo, base_commit=base
        )


def test_loc_inventory_fails_closed_for_rewritten_move_outside_backend(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "bench@example.invalid")
    _git(repo, "config", "user.name", "Harness Benchmark")
    source = repo / "backend/original.py"
    source.parent.mkdir(parents=True)
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    content = source.read_bytes()
    manifest = {
        "expected_total_loc": 3,
        "files": [
            {
                "path": "backend/original.py",
                "loc": 3,
                "sha256": harness_baseline._sha256(content),
                "git_blob": _git(repo, "rev-parse", f"{base}:backend/original.py"),
                "group": "legacy_agent_loop",
            }
        ],
    }
    source.unlink()
    moved = repo / "scripts/迁移.py"
    moved.parent.mkdir(parents=True)
    moved.write_text("rewritten\nproduction\nlogic\n", encoding="utf-8")

    with pytest.raises(
        harness_baseline.BenchmarkInvariantError,
        match=r"unknown LOC classifications: scripts/迁移\.py",
    ):
        harness_baseline.build_current_loc_inventory(
            manifest, repo=repo, base_commit=base
        )
