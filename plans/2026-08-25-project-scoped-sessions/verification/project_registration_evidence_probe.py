from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from deskpet.memory.schema import initialize_state_db
from deskpet.session.project_binding import ProjectBindingService


def _count(db_path: Path, sql: str, session_id: str) -> int:
    with sqlite3.connect(db_path) as db:
        return int(db.execute(sql, (session_id,)).fetchone()[0])


async def _probe(args: argparse.Namespace) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="project-registration-evidence-") as raw:
        fixture = Path(raw).resolve()
        root = fixture / "repo"
        child = root / "src"
        child.mkdir(parents=True)
        subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
        symlink = fixture / "repo-alias"
        symlink.symlink_to(child, target_is_directory=True)
        db_path = fixture / "state.db"
        await initialize_state_db(db_path)
        service = ProjectBindingService(db_path)

        registrations = []
        aliases = [child, child / ".." / "src", symlink]
        for selected in aliases:
            project, created = await service.register_project(str(selected), "git_root")
            with sqlite3.connect(db_path) as db:
                project_count = int(db.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
            registrations.append(
                {
                    "selected": str(selected),
                    "project_id": project.project_id,
                    "created": created,
                    "project_count": project_count,
                    "canonical_root": project.canonical_root,
                    "filesystem_identity": project.filesystem_identity,
                }
            )

        project_ids = {item["project_id"] for item in registrations}
        dedupe_pass = (
            len(project_ids) == 1
            and [item["created"] for item in registrations] == [True, False, False]
            and [item["project_count"] for item in registrations] == [1, 1, 1]
        )

    session_id = args.session_id
    run_counts = {
        "execution_runs": _count(
            args.workflow_db,
            "SELECT COUNT(*) FROM execution_runs WHERE session_id=?",
            session_id,
        ),
        "workflow_runs": _count(
            args.workflow_db,
            "SELECT COUNT(*) FROM workflow_runs WHERE session_id=?",
            session_id,
        ),
        "sdk_execution_sessions": _count(
            args.sdk_db,
            "SELECT COUNT(*) FROM execution_sessions WHERE session_id=?",
            session_id,
        ),
        "provider_attempts": _count(
            args.state_db,
            "SELECT COUNT(*) FROM sdk_provider_attempt_audit WHERE session_id=?",
            session_id,
        ),
        "project_run_admissions": _count(
            args.state_db,
            "SELECT COUNT(*) FROM project_run_admissions WHERE session_id=?",
            session_id,
        ),
    }
    no_run_pass = all(value == 0 for value in run_counts.values())
    result = {
        "status": "PASS" if dedupe_pass and no_run_pass else "FAIL",
        "session_id": session_id,
        "registrations": registrations,
        "equivalent_alias_dedupe": dedupe_pass,
        "run_counts": run_counts,
        "no_provider_or_execution_run": no_run_pass,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--workflow-db", type=Path, required=True)
    parser.add_argument("--sdk-db", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(_probe(args))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
