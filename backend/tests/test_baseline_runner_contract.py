from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_baseline_runner_lists_every_manifest_shard_in_order(tmp_path) -> None:
    manifest = json.loads(
        (REPO_ROOT / "baseline-shards.json").read_text(encoding="utf-8")
    )
    expected_ids = [shard["id"] for shard in manifest["shards"]]
    assert len(expected_ids) == len(set(expected_ids)) == 17
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "baseline_runner.py"),
            "--run-dir",
            str(tmp_path / "unused"),
            "--list",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    listed_ids = [line.split(":", 1)[0] for line in completed.stdout.splitlines()]
    assert listed_ids == expected_ids
    assert expected_ids[:7] == [
        "backend-a",
        "backend-b",
        "backend-c",
        "backend-d-f",
        "backend-g-l",
        "backend-m-r",
        "backend-s-z",
    ]
    assert {
        "backend-capabilities",
        "backend-companion",
        "backend-sdk-adapters",
        "root-tests",
        "frontend-vitest",
        "frontend-typecheck",
        "frontend-lint",
        "frontend-build",
        "rust-test",
        "rust-check",
    } <= set(expected_ids)


def test_alpha_shards_list_every_top_level_backend_test_exactly_once() -> None:
    discovered: list[str] = []
    for lo, hi in (
        ("a", "a"),
        ("b", "b"),
        ("c", "c"),
        ("d", "f"),
        ("g", "l"),
        ("m", "r"),
        ("s", "z"),
    ):
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "backend" / "scripts" / "run_pytest_alpha_shard.py"),
                str(REPO_ROOT / "backend" / "tests"),
                lo,
                hi,
                "--list",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        discovered.extend(completed.stdout.splitlines())
    expected = sorted(
        str(path) for path in (REPO_ROOT / "backend" / "tests").glob("test_*.py")
    )
    assert len(discovered) == len(set(discovered))
    assert sorted(discovered) == expected
