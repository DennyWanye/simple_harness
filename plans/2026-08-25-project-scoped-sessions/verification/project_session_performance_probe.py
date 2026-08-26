#!/usr/bin/env python3
"""Current-production 500x200 Project/Session bounded read-model probe."""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from deskpet.memory.schema import initialize_state_db
from deskpet.memory.session_db import SessionDB
from deskpet.session.project_binding import (
    ProjectBindingService,
    ProjectSessionError,
    SessionCreationService,
    _filesystem_identity,
)


PROJECTS = 500
SESSIONS_PER_PROJECT = 200
ITERATIONS = 25


def percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * percentile) - 1))
    return ordered[index]


async def run_probe() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="simple-harness-project-perf-") as raw:
        root = Path(raw).resolve()
        db_path = root / "state.db"
        await initialize_state_db(db_path)
        identity = _filesystem_identity(root)
        projects = [
            (
                f"project-{index:04d}",
                f"Project {index:04d}",
                str(root),
                "folder",
                f"{identity}:{index:04d}",
                1,
                float(index),
                float(index),
                float(index),
            )
            for index in range(PROJECTS)
        ]
        sessions = []
        bindings = []
        catalog = []
        delivery = []
        receipts = []
        for project_index in range(PROJECTS):
            project_id = f"project-{project_index:04d}"
            for session_index in range(SESSIONS_PER_PROJECT):
                session_id = f"task-{project_index:04d}-{session_index:04d}"
                created_at = float(project_index * SESSIONS_PER_PROJECT + session_index)
                sessions.append((session_id, created_at, "{}"))
                bindings.append((session_id, project_id, "project_root", 1, created_at))
                catalog.append((session_id, "conversation", created_at))
                delivery.append((session_id, 0, None, None))
                receipts.append(
                    (
                        f"request-{session_id}",
                        f"intent-{session_id}",
                        session_id,
                        "{}",
                        "active",
                        created_at,
                    )
                )
        with sqlite3.connect(db_path) as db:
            db.executemany("INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?)", projects)
            db.executemany("INSERT INTO sessions(id,created_at,metadata) VALUES(?,?,?)", sessions)
            db.executemany(
                "INSERT INTO session_project_bindings(session_id,project_id,execution_kind,binding_version,created_at) "
                "VALUES(?,?,?,?,?)",
                bindings,
            )
            db.executemany("INSERT INTO session_catalog_entries VALUES(?,?,?)", catalog)
            db.executemany("INSERT INTO session_delivery_state VALUES(?,?,?,?)", delivery)
            db.executemany(
                "INSERT INTO session_creation_receipts(request_id,intent_hash,session_id,result_json,lifecycle,created_at) "
                "VALUES(?,?,?,?,?,?)",
                receipts,
            )
            explain = [
                str(row[3])
                for row in db.execute(
                    "EXPLAIN QUERY PLAN SELECT s.id FROM sessions s "
                    "JOIN session_catalog_entries ce ON ce.session_id=s.id AND ce.product_kind='conversation' "
                    "JOIN session_project_bindings b ON b.session_id=s.id "
                    "LEFT JOIN session_delivery_state d ON d.session_id=s.id "
                    "WHERE b.project_id=? AND d.deleted_at IS NULL "
                    "ORDER BY s.created_at DESC,s.id ASC LIMIT 51",
                    ("project-0499",),
                )
            ]

        session_db = SessionDB(db_path)
        await session_db.initialize()
        service = ProjectBindingService(db_path, write_lock=session_db._write_lock)
        creation = SessionCreationService(service, session_db=session_db)
        catalog_times: list[float] = []
        page_times: list[float] = []
        catalog_payload = page_payload = b""
        for _ in range(ITERATIONS + 1):
            started = time.perf_counter()
            catalog_result = await service.list_project_page(limit=50)
            elapsed_catalog = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            page_result = await service.list_session_page(
                scope_kind="project", project_id="project-0499", limit=50
            )
            elapsed_page = (time.perf_counter() - started) * 1000
            catalog_payload = json.dumps(catalog_result, separators=(",", ":")).encode()
            page_payload = json.dumps(page_result, separators=(",", ":")).encode()
            if catalog_times:
                catalog_times.append(elapsed_catalog)
                page_times.append(elapsed_page)
            else:
                catalog_times.append(elapsed_catalog)
                page_times.append(elapsed_page)
        catalog_times = catalog_times[1:]
        page_times = page_times[1:]
        result = {
            "fixture": {"projects": PROJECTS, "sessions": len(sessions)},
            "iterations": ITERATIONS,
            "catalog_ms": {
                "median": round(statistics.median(catalog_times), 3),
                "p95": round(percentile(catalog_times, 0.95), 3),
            },
            "session_page_ms": {
                "median": round(statistics.median(page_times), 3),
                "p95": round(percentile(page_times, 0.95), 3),
            },
            "payload_bytes": {
                "catalog": len(catalog_payload),
                "session_page": len(page_payload),
                "combined": len(catalog_payload) + len(page_payload),
            },
            "explain": explain,
        }
        first_page = await service.list_session_page(
            scope_kind="project", project_id="project-0499", limit=50
        )
        stale_cursor = first_page["next_cursor"]
        if not stale_cursor:
            raise AssertionError("large fixture did not produce a continuation cursor")
        created = await creation.create_conversation_session(
            request_id="concurrent-created",
            project_id="project-0499",
        )
        deleted_id = "task-0499-0000"
        await creation.mark_deleted(deleted_id)
        renamed_id = "task-0499-0001"
        await session_db.set_session_title(renamed_id, "Concurrent Rename")
        stale_rejected = False
        try:
            await service.list_session_page(
                scope_kind="project",
                project_id="project-0499",
                cursor=stale_cursor,
                limit=50,
            )
        except ProjectSessionError as exc:
            stale_rejected = exc.code == "stale_cursor"

        refreshed_ids: list[str] = []
        refreshed_titles: dict[str, str] = {}
        cursor: str | None = None
        revisions: list[int] = []
        while True:
            page = await service.list_session_page(
                scope_kind="project",
                project_id="project-0499",
                cursor=cursor,
                limit=50,
            )
            revisions.append(int(page["catalog_revision"]))
            for item in page["items"]:
                refreshed_ids.append(str(item["session_id"]))
                refreshed_titles[str(item["session_id"])] = str(item["title"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        created_id = str(created["session"]["session_id"])
        mutation_consistency = {
            "stale_cursor_rejected": stale_rejected,
            "catalog_revisions": sorted(set(revisions)),
            "pages": len(revisions),
            "final_count": len(refreshed_ids),
            "unique_count": len(set(refreshed_ids)),
            "created_present": created_id in refreshed_ids,
            "deleted_absent": deleted_id not in refreshed_ids,
            "renamed_title": refreshed_titles.get(renamed_id),
            "misgrouped_count": sum(
                1 for session_id in refreshed_ids
                if not session_id.startswith("task-0499-") and session_id != created_id
            ),
        }
        result["concurrent_mutation"] = mutation_consistency
        passed = (
            result["catalog_ms"]["p95"] <= 200  # type: ignore[index]
            and result["session_page_ms"]["p95"] <= 200  # type: ignore[index]
            and result["payload_bytes"]["catalog"] <= 128 * 1024  # type: ignore[index]
            and result["payload_bytes"]["session_page"] <= 128 * 1024  # type: ignore[index]
            and result["payload_bytes"]["combined"] <= 256 * 1024  # type: ignore[index]
            and stale_rejected
            and len(set(revisions)) == 1
            and len(refreshed_ids) == SESSIONS_PER_PROJECT
            and len(set(refreshed_ids)) == SESSIONS_PER_PROJECT
            and created_id in refreshed_ids
            and deleted_id not in refreshed_ids
            and refreshed_titles.get(renamed_id) == "Concurrent Rename"
            and mutation_consistency["misgrouped_count"] == 0
        )
        result["decision"] = "PASS" if passed else "FAIL"
        await session_db.close()
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(run_probe())
    wire = json.dumps(result, indent=2, sort_keys=True)
    print(wire)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(wire + "\n", encoding="utf-8")
    return 0 if result["decision"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
