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

    canonical = harness_baseline.compare(current, current)
    assert canonical["checks"]["completed_run_strong_refs_not_increased"] is True
    assert canonical["checks"]["orchestration_loc_lte_17250"] is False


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
    assert result["current_total"] == 8
    assert rows["backend/已提交.py"]["current_loc"] == 2
    assert rows["backend/待修改.py"]["current_loc"] == 2
    assert rows["backend/新模块.py"]["current_loc"] == 3


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
