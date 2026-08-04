"""Fail-closed gate for the approved real Harness public-root fixture.

Unlike the ordinary pytest module, this release-gate entrypoint never turns a
missing source into SKIP.  All four source coordinates must be supplied and
the real-root regeneration test must pass byte-identically.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]


REQUIRED_ENV = (
    "DESKPET_FIXTURE_WORKFLOW_DB",
    "DESKPET_FIXTURE_STATE_DB",
    "DESKPET_FIXTURE_SESSION_ID",
    "DESKPET_FIXTURE_ROOT_RUN_ID",
)


def main() -> int:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        print(
            "fixture source is not configured: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    for name in REQUIRED_ENV[:2]:
        source = Path(os.environ[name]).resolve()
        if not source.is_file():
            print(f"fixture source does not exist: {name}={source}", file=sys.stderr)
            return 2

    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="deskpet-real-fixture-") as temp:
        snapshot_dir = Path(temp)
        workflow_snapshot = snapshot_dir / "workflow.db"
        state_snapshot = snapshot_dir / "state.db"
        for source_name, target in zip(REQUIRED_ENV[:2], (
            workflow_snapshot,
            state_snapshot,
        )):
            source = Path(os.environ[source_name]).resolve()
            with (
                closing(
                    sqlite3.connect(
                        f"file:{source.as_posix()}?mode=ro", uri=True
                    )
                ) as source_db,
                closing(sqlite3.connect(target)) as target_db,
            ):
                source_db.backup(target_db)

        child_env = dict(os.environ)
        child_env["PYTHONPATH"] = str(BACKEND_ROOT) + os.pathsep + str(
            child_env.get("PYTHONPATH") or ""
        )
        child_env[REQUIRED_ENV[0]] = str(workflow_snapshot)
        child_env[REQUIRED_ENV[1]] = str(state_snapshot)
        # The approved source is historical.  Production startup migrates it
        # before the read service opens, so an isolated child process migrates
        # the snapshot and releases every SQLite handle before verification.
        migration = subprocess.run(
            (
                sys.executable,
                "-c",
                "import asyncio,sys; from deskpet.memory.migrator import ensure_v9; "
                "asyncio.run(ensure_v9(sys.argv[1]))",
                str(state_snapshot),
            ),
            cwd=repo_root,
            env=child_env,
            check=False,
        )
        if migration.returncode:
            return int(migration.returncode)
        command = (
            sys.executable,
            "-m",
            "pytest",
            "backend/tests/test_generate_harness_public_fixture.py::"
            "test_real_public_root_regenerates_committed_fixture_byte_identically",
            "-q",
        )
        completed = subprocess.run(
            command, cwd=repo_root, env=child_env, check=False
        )
        # Windows may release the child process' final SQLite handle a few
        # milliseconds after process exit.  Let TemporaryDirectory remove the
        # snapshots cleanly instead of turning a successful gate into a false
        # cleanup failure.
        time.sleep(0.2)
        return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
