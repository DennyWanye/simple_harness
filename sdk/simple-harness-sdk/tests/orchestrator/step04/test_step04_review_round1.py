# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 4 · code review round 1 dispositions (journal §3): P0-1 / P1-2 a child budget
inherits every Mission-bounded dimension, P1-3 / P1-4 one canonical workspace path for the untrusted-source rule, P1-5 a v1 library
with duplicate artifact lineage rows still upgrades, real-run finding: SDK error payloads with
tuples become plain JSON.

HTN 补齐阶段 A′：P2-13"取代保留记录版本"那半条删除——产品里执行者声明到不了 VERIFIED，模型的
``supersedes`` 一律被拒，没有任何一条路把知识标成已取代（主循环里的实际行为钉在
``test_claims_knowledge_main_loop.py::test_a_model_cannot_supersede_verified_knowledge``）；
"拒收结果时错误详情里的元组入库"那半条原来手工建尝试再拒收，删除，只留 ``jsonable`` 本身。"""

from __future__ import annotations

import sqlite3

from agent_orchestrator.artifacts.paths import normalise_workspace_path, under_prefix
from agent_orchestrator.contracts import Budget, ClaimStatus, jsonable
from agent_orchestrator.governance.budget_limits import inherit_limits
from agent_orchestrator.governance.domains import CODE_PROFILE
from agent_orchestrator.memory.claims import grade_claim
from agent_orchestrator.runtime.tool_gateway import is_untrusted
from agent_orchestrator.storage import schema
from agent_orchestrator.storage.store import Store


# ------------------------------------------------------------------ P0-1 / P1-2
def test_a_child_budget_inherits_every_bounded_dimension():
    parent = Budget(max_tokens=100_000, max_attempts=6, max_runtime_seconds=600)
    child = inherit_limits(Budget(max_tokens=20_000, max_attempts=2), parent)
    assert child.fits_within(parent) and child.max_runtime_seconds == 600


# ------------------------------------------------------------------ P1-3 / P1-4
def test_one_canonical_path_backs_the_untrusted_rule():
    assert normalise_workspace_path("./docs/./vendor_notes.md") == "docs/vendor_notes.md"
    assert (
        normalise_workspace_path("/docs//x.md") == "docs/x.md"
        and normalise_workspace_path("docs\\x.md") == "docs/x.md"
    )
    for path in (
        "docs/vendor_notes.md",
        "./docs/vendor_notes.md",
        "./docs/./vendor_notes.md",
        "docs//vendor_notes.md",
        "/docs/vendor_notes.md",
    ):
        assert is_untrusted(path, ("docs/",)) and under_prefix(path, ["docs"])
    assert not is_untrusted("docsx/vendor_notes.md", ("docs/",)) and not is_untrusted(
        "notes/docs/x.md", ("docs/",)
    )
    for evidence in (
        ["pytest:./docs/vendor_notes.md"],
        ["./docs/vendor_notes.md"],
        ["docs/../docs/vendor_notes.md"],
    ):
        grade = grade_claim(
            "c",
            evidence,
            verifier_results=[],
            artifact_paths=["docs/vendor_notes.md", "notes/x.md"],
            untrusted_prefixes=["docs/"],
            domain=CODE_PROFILE,
        )
        assert grade.status is ClaimStatus.UNDER_REVIEW and grade.basis["grade"] == "unsupported", (
            evidence
        )


# ------------------------------------------------------------------ P1-5
def test_v1_library_with_duplicate_lineage_rows_upgrades_and_renumbers(tmp_path):
    path = tmp_path / "orchestrator.db"
    connection = sqlite3.connect(path)
    for statement in schema.MIGRATIONS[0].ddl.split(";"):
        if statement.strip():
            connection.execute(statement)
    connection.execute(
        "INSERT INTO orch_schema_migrations VALUES (?,?,?,?)",
        (1, schema.MIGRATIONS[0].name, schema.MIGRATIONS[0].checksum, 1.0),
    )
    connection.execute("INSERT INTO missions VALUES ('m1','t','k','ACTIVE',1,'h','{}',1.0,1.0)")
    connection.execute("INSERT INTO tasks VALUES ('m1:task-1','m1',1,'COMPLETED',1,'{}',1.0)")
    for ordinal in (1, 2):
        connection.execute(
            "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"m1:task-1:attempt-{ordinal}",
                "m1:task-1",
                "m1",
                ordinal,
                "COMPLETED",
                1,
                None,
                None,
                None,
                None,
                "{}",
                1.0,
            ),
        )
    for ordinal, digest in ((1, "a" * 64), (2, "b" * 64)):  # the L3-2 defect: both got version 1
        artifact_id = f"artifact-{ordinal}"
        document = {
            "id": artifact_id,
            "mission_id": "m1",
            "task_id": "m1:task-1",
            "attempt_id": f"m1:task-1:attempt-{ordinal}",
            "type": "file",
            "path": "x.py",
            "version": 1,
            "content_hash": digest,
            "size_bytes": 1,
            "produced_by": "a",
        }
        import json

        connection.execute(
            "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?,?,?)",
            (
                artifact_id,
                "m1",
                "m1:task-1",
                f"m1:task-1:attempt-{ordinal}",
                "x.py",
                digest,
                1,
                json.dumps(document),
                float(ordinal),
            ),
        )
    connection.commit()
    connection.close()
    store = Store.open(path)
    versions = {a.id: a.version for a in store.list_mission_artifacts("m1")}
    assert versions == {"artifact-1": 1, "artifact-2": 2}
    assert (
        store.connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE name='artifacts_lineage_idx'"
        ).fetchone()[0]
        == 1
    )
    store.close()


# ------------------------------------------------------------------ real-run finding
def test_sdk_error_payloads_with_tuples_are_jsonable():
    assert jsonable({"error": {"output_cap_escalations": (1, 2), "kinds": {"b", "a"}}}) == {
        "error": {"output_cap_escalations": [1, 2], "kinds": ["a", "b"]}
    }
