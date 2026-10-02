# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 3 leftover L3-2 (D4-15): two candidates of one Task that write the same path get
distinct versions along the (mission, path) lineage — the version is assigned inside the
``record_result`` transaction and guarded by a UNIQUE index, never by the caller."""

from __future__ import annotations

import sqlite3

import pytest

from agent_orchestrator.runtime.output_blocks import PortClaim
from knowledge_helpers import (
    HASH_A,
    HASH_B,
    artifact,
    claim,
    drive_to_running,
    envelope,
    two_leaf_service,
)


#: 分层步骤声明了一个必需的输出端口，结果要认领它。
CLAIM = (PortClaim(port_key="result", path="tests/probe/test_impl_a.py"),)


def test_a_second_row_for_the_same_path_and_version_is_refused_by_the_schema(tmp_path):
    """One open Attempt per step (2026-10-02), so two results can no longer race for one
    provisional version; the lineage stays guarded by the schema itself."""

    service, mission, (task_a, _) = two_leaf_service(tmp_path)
    first = drive_to_running(service, task_a)
    path = "tests/probe/test_impl_a.py"
    stored = service.record_result(
        first.id,
        envelope=envelope(first, claims=[claim("c1")]),
        turn_id="turn-1",
        artifacts=[artifact(first, path, HASH_A, version=1)],
        usage_refs=(),
        port_claims=CLAIM,
    )
    assert service.store.get_artifact(stored.artifacts[0]).version == 1
    with pytest.raises(sqlite3.IntegrityError):
        with service.store.transaction() as connection:
            connection.execute(
                "INSERT INTO artifacts(artifact_id,mission_id,task_id,attempt_id,path,content_hash,version,json,created_at)"
                " VALUES ('dup', ?, ?, ?, ?, ?, 1, '{}', 1.0)",
                (mission.id, task_a.id, first.id, path, HASH_B),
            )


def test_duplicate_delivery_of_the_same_turn_keeps_the_version(tmp_path):
    service, mission, (task_a, _) = two_leaf_service(tmp_path)
    first = drive_to_running(service, task_a)
    path = "tests/probe/test_impl_a.py"
    s1 = service.record_result(
        first.id,
        envelope=envelope(first, claims=[claim("c1")]),
        turn_id="turn-1",
        artifacts=[artifact(first, path, HASH_A, version=1)],
        usage_refs=(),
        port_claims=CLAIM,
    )
    again = service.record_result(
        first.id,
        envelope=envelope(first, claims=[claim("c1")]),
        turn_id="turn-1",
        artifacts=[artifact(first, path, HASH_A, version=1)],
        usage_refs=(),
        port_claims=CLAIM,
    )
    assert again == s1
    versions = [
        a.version for a in service.store.list_mission_artifacts(mission.id) if a.path == path
    ]
    assert versions == [1]
