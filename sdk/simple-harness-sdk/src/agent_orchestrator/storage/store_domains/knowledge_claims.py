# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""Claims and knowledge entries."""

from __future__ import annotations

from simple_harness.contracts import canonical_json

from ...contracts import Claim
from ...memory.verified_knowledge import KnowledgeRecord
from .core import StoreCore
from .records import _loads


class StoreKnowledgeClaimMixin(StoreCore):
    """Claims and knowledge entries."""

    __slots__ = ()

    # ------------------------------------------------------------------ claims
    def upsert_claim(self, claim: Claim) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO claims(claim_id,mission_id,result_id,status,version,json,updated_at,key)"
                " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(claim_id) DO UPDATE SET status = excluded.status,"
                " version = excluded.version, json = excluded.json, updated_at = excluded.updated_at,"
                " key = excluded.key",
                (
                    claim.id,
                    claim.mission_id,
                    claim.result_id,
                    str(claim.status),
                    claim.version,
                    canonical_json(claim.to_json()),
                    self.now,
                    claim.key,
                ),
            )

    def get_claim(self, claim_id: str) -> Claim | None:
        row = self._connection.execute(
            "SELECT json FROM claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        return None if row is None else Claim.from_json(_loads(row[0]))

    def list_claims(self, result_id: str) -> list[Claim]:
        rows = self._connection.execute(
            "SELECT json FROM claims WHERE result_id = ? ORDER BY claim_id", (result_id,)
        ).fetchall()
        return [Claim.from_json(_loads(row[0])) for row in rows]

    def list_mission_claims(self, mission_id: str) -> list[Claim]:
        rows = self._connection.execute(
            "SELECT json FROM claims WHERE mission_id = ? ORDER BY claim_id", (mission_id,)
        ).fetchall()
        return [Claim.from_json(_loads(row[0])) for row in rows]

    # --------------------------------------------------------------- knowledge
    def upsert_knowledge(self, record: KnowledgeRecord) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO knowledge(knowledge_id,mission_id,claim_id,key,status,version,source_task,json,created_at,updated_at,"
                "validity_interval,permitted_uses,assurance_level)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(knowledge_id) DO UPDATE SET status = excluded.status,"
                " version = excluded.version, json = excluded.json, updated_at = excluded.updated_at,"
                " validity_interval = excluded.validity_interval, permitted_uses = excluded.permitted_uses,"
                " assurance_level = excluded.assurance_level",
                (
                    record.id,
                    record.mission_id,
                    record.claim_id,
                    record.key,
                    record.status,
                    record.version,
                    record.source_task,
                    canonical_json(record.to_json()),
                    record.created_at,
                    self.now,
                    # 第 2 批 K02：三列与 JSON 同一次写入；None 落成 NULL，不猜
                    None
                    if record.validity_interval is None
                    else canonical_json(dict(record.validity_interval)),
                    None
                    if record.permitted_uses is None
                    else canonical_json(list(record.permitted_uses)),
                    record.assurance_level,
                ),
            )

    def get_knowledge(self, knowledge_id: str) -> KnowledgeRecord | None:
        row = self._connection.execute(
            "SELECT json FROM knowledge WHERE knowledge_id = ?", (knowledge_id,)
        ).fetchone()
        return None if row is None else KnowledgeRecord.from_json(_loads(row[0]))

    def list_knowledge(
        self, mission_id: str, *, status: str | None = None
    ) -> list[KnowledgeRecord]:
        """Knowledge of one Mission only — the Mission boundary is the permission
        pre-filter of retrieval (S4-06)."""

        if status is None:
            rows = self._connection.execute(
                "SELECT json FROM knowledge WHERE mission_id = ? ORDER BY created_at, knowledge_id",
                (mission_id,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT json FROM knowledge WHERE mission_id = ? AND status = ? ORDER BY created_at, knowledge_id",
                (mission_id, status),
            ).fetchall()
        return [KnowledgeRecord.from_json(_loads(row[0])) for row in rows]
