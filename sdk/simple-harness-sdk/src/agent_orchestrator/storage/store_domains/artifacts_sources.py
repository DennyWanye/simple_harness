# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""Artifacts, workspaces, sources and receipts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from simple_harness.contracts import canonical_json

from ...contracts import Artifact
from .core import StoreCore
from .records import (
    _WORKSPACE_COLUMNS,
    ARTIFACT_VERIFICATION_STATES,
    WORKSPACE_STATES,
    StoreError,
    _loads,
    _workspace_row,
)


class StoreArtifactSourceMixin(StoreCore):
    """Artifacts, workspaces, sources and receipts."""

    __slots__ = ()

    # --------------------------------------------------------------- artifacts
    def upsert_artifact(self, artifact: Artifact) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO artifacts(artifact_id,mission_id,task_id,attempt_id,path,content_hash,version,"
                "json,created_at) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(artifact_id) DO NOTHING",
                (
                    artifact.id,
                    artifact.mission_id,
                    artifact.task_id,
                    artifact.attempt_id,
                    artifact.path,
                    artifact.content_hash,
                    artifact.version,
                    canonical_json(artifact.to_json()),
                    artifact.created_at or self.now,
                ),
            )

    def update_artifact_verification(self, artifact_id: str, status: str) -> None:
        """P3.1 fix F-ORCH-3: an artifact follows its result's verdict — VERIFIED when the
        result is accepted, REJECTED when it failed.  The only field of an artifact row that
        changes after it was recorded (``upsert_artifact`` never rewrites a row).

        A missing row means the library is damaged: ``record_result`` writes every artifact
        in the same transaction, so it fails loudly rather than carrying on (review P2-5).
        """

        if status not in ARTIFACT_VERIFICATION_STATES:
            raise StoreError(f"unknown artifact verification status {status!r}")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT json FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if row is None:
                raise StoreError(f"artifact {artifact_id} is not recorded")
            data = json.loads(row[0])
            data["verification_status"] = status
            connection.execute(
                "UPDATE artifacts SET json = ? WHERE artifact_id = ?",
                (canonical_json(data), artifact_id),
            )

    # --------------------------------------------------------------- workspaces (P3.2 D4)
    def register_workspace(
        self,
        workspace_id: str,
        *,
        kind: str,
        mission_id: str,
        attempt_id: str,
        base_snapshot: str,
        state: str,
        detail: Mapping[str, Any],
    ) -> None:
        """Register (or re-register) one workspace directory: its identity and state."""

        if state not in WORKSPACE_STATES:
            raise StoreError(f"unknown workspace state {state!r}")
        now = self.now
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO workspaces(workspace_id,kind,mission_id,attempt_id,base_snapshot,"
                "state,json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(workspace_id) DO UPDATE SET base_snapshot=excluded.base_snapshot,"
                "state=excluded.state,json=excluded.json,updated_at=excluded.updated_at",
                (
                    workspace_id,
                    kind,
                    mission_id,
                    attempt_id,
                    base_snapshot,
                    state,
                    canonical_json(dict(detail)),
                    now,
                    now,
                ),
            )

    def set_workspace_state(self, workspace_id: str, state: str) -> None:
        if state not in WORKSPACE_STATES:
            raise StoreError(f"unknown workspace state {state!r}")
        with self.transaction() as connection:
            updated = connection.execute(
                "UPDATE workspaces SET state = ?, updated_at = ? WHERE workspace_id = ?",
                (state, self.now, workspace_id),
            ).rowcount
            if not updated:
                raise StoreError(f"workspace {workspace_id} is not registered")

    def get_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            f"SELECT {_WORKSPACE_COLUMNS} FROM workspaces WHERE workspace_id = ?", (workspace_id,)
        ).fetchone()
        return None if row is None else _workspace_row(row)

    def list_workspaces(self, mission_id: str | None = None) -> list[dict[str, Any]]:
        if mission_id is None:
            rows = self._connection.execute(
                f"SELECT {_WORKSPACE_COLUMNS} FROM workspaces ORDER BY created_at, workspace_id"
            ).fetchall()
        else:
            rows = self._connection.execute(
                f"SELECT {_WORKSPACE_COLUMNS} FROM workspaces WHERE mission_id = ? "
                "ORDER BY created_at, workspace_id",
                (mission_id,),
            ).fetchall()
        return [_workspace_row(row) for row in rows]

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        row = self._connection.execute(
            "SELECT json FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        return None if row is None else Artifact.from_json(_loads(row[0]))

    def list_artifacts(self, attempt_id: str) -> list[Artifact]:
        rows = self._connection.execute(
            "SELECT json FROM artifacts WHERE attempt_id = ? ORDER BY path, version", (attempt_id,)
        ).fetchall()
        return [Artifact.from_json(_loads(row[0])) for row in rows]

    def list_mission_artifacts(self, mission_id: str) -> list[Artifact]:
        """Every artifact of the Mission (version lineage is per (mission, path), D3-8')."""

        rows = self._connection.execute(
            "SELECT json FROM artifacts WHERE mission_id = ? ORDER BY path, version, artifact_id",
            (mission_id,),
        ).fetchall()
        return [Artifact.from_json(_loads(row[0])) for row in rows]

    # --------------------------------------------------------------- sources
    def get_source(
        self, mission_id: str, path: str, version_hash: str | None = None
    ) -> dict[str, Any] | None:
        """An exact historical version, or the active version when hash is omitted."""

        if not self.has_table("sources"):
            return None
        sql = "SELECT * FROM sources WHERE mission_id = ? AND path = ?"
        args: list[Any] = [mission_id, path]
        if version_hash is None:
            sql += " AND superseded_by IS NULL AND revoked = 0"
        else:
            sql += " AND version_hash = ?"
            args.append(version_hash)
        row = self._connection.execute(sql, args).fetchone()
        return None if row is None else {**dict(row), "revoked": bool(row["revoked"])}

    def list_sources(self, mission_id: str, active_only: bool = False) -> list[dict[str, Any]]:
        if not self.has_table("sources"):
            return []
        sql = "SELECT * FROM sources WHERE mission_id = ?"
        if active_only:
            sql += " AND superseded_by IS NULL AND revoked = 0"
        rows = self._connection.execute(sql + " ORDER BY path, version_hash", (mission_id,))
        return [{**dict(row), "revoked": bool(row["revoked"])} for row in rows]

    def put_source(self, record: Mapping[str, Any]) -> None:
        """CommitService-only lifecycle write; source bytes never enter SQLite."""

        fields = (
            "mission_id",
            "tenant_id",
            "path",
            "version_hash",
            "kind",
            "trust",
            "registered_at",
            "superseded_by",
            "revoked",
            "revision",
        )
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO sources(mission_id,tenant_id,path,version_hash,kind,trust,"
                "registered_at,superseded_by,revoked,revision) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(mission_id,path,version_hash) DO UPDATE SET "
                "superseded_by=excluded.superseded_by, revoked=excluded.revoked, "
                "revision=excluded.revision",
                tuple(
                    int(record[field]) if field == "revoked" else record[field] for field in fields
                ),
            )

    # --------------------------------------------------------------- receipts
    def get_receipt(self, commit_id: str) -> Mapping[str, Any] | None:
        row = self._connection.execute(
            "SELECT receipt_json FROM commit_receipts WHERE commit_id = ?", (commit_id,)
        ).fetchone()
        return None if row is None else _loads(row[0])

    def insert_receipt(
        self,
        *,
        commit_id: str,
        kind: str,
        subject_id: str,
        base_version: int | None,
        proposal_hash: str,
        receipt: Mapping[str, Any],
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO commit_receipts(commit_id,kind,subject_id,base_version,proposal_hash,"
                "receipt_json,applied_at) VALUES (?,?,?,?,?,?,?)",
                (
                    commit_id,
                    kind,
                    subject_id,
                    base_version,
                    proposal_hash,
                    canonical_json(dict(receipt)),
                    self.now,
                ),
            )
