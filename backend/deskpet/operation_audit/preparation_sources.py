"""Actual pre-SDK rejection audit from Host's existing verified terminal variant."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import aiosqlite

from deskpet.execution.preparation_rejection import read_preparation_rejection_tx
from deskpet.operation_audit.store import canonical, digest

_SCHEMA = """
CREATE TABLE IF NOT EXISTS preparation_audit_sources (
 source_ref TEXT PRIMARY KEY, source_hash TEXT NOT NULL, owner_ref TEXT NOT NULL,
 host_run_id TEXT NOT NULL, sdk_run_id TEXT, recorded_at REAL,
 source_json TEXT NOT NULL, source_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preparation_audit_findings (
 finding_id TEXT PRIMARY KEY, source_ref TEXT NOT NULL, source_hash TEXT NOT NULL,
 owner_ref TEXT NOT NULL, owner_component TEXT NOT NULL, rule_version TEXT NOT NULL,
 reason TEXT NOT NULL
);
"""


class PreparationAuditConsumer:
    def __init__(self, state_path, audit_path, *, subject):
        self.state_path, self.audit_path = Path(state_path), Path(audit_path)
        self.subject = subject
        self.owner_ref = digest(["host.audit.owner.v1", subject])

    async def discover(self, *, limit=32):
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("preparation_audit_limit_invalid")
        # Stable keyset anti-join prevents a malformed or previously saved source
        # from starving later rejections. Changed transition hashes are new input.
        await asyncio.to_thread(self._initialize)
        async with aiosqlite.connect(
            self.state_path.resolve().as_uri() + "?mode=ro", uri=True
        ) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "ATTACH DATABASE ? AS audit",
                (self.audit_path.resolve().as_uri() + "?mode=ro",),
            )
            await db.execute("BEGIN")
            rows = await (
                await db.execute(
                    "SELECT x.transition_id,x.transition_hash,x.host_run_id,h.primary_conversation_id FROM foreground_run_transitions x "
                    "JOIN foreground_run_heads h ON h.host_run_id=x.host_run_id "
                    "WHERE x.subject=? AND x.idempotency_key='preparation-rejected:v1' "
                    "AND NOT EXISTS (SELECT 1 FROM audit.preparation_audit_sources a WHERE a.source_ref=(x.transition_id || ':' || x.transition_hash) "
                    "AND a.source_hash=x.transition_hash) ORDER BY x.transition_id LIMIT ?",
                    (self.subject, limit),
                )
            ).fetchall()
            results = []
            for row in rows:
                try:
                    value = await read_preparation_rejection_tx(
                        db,
                        host_run_id=row["host_run_id"],
                        subject=self.subject,
                        primary_ref=row["primary_conversation_id"],
                    )
                    if value is None or value["transition_id"] != row["transition_id"]:
                        raise ValueError("preparation_source_not_current")
                    # Save only public-safe commitments, not the USER payload or
                    # full visibility snapshot. The original remains in Host S1.
                    safe = {
                        k: value[k]
                        for k in (
                            "transition_id",
                            "host_run_id",
                            "recorded_at",
                            "generation",
                        )
                    }
                    safe.update(
                        sdk_run_id=None,
                        reason=value["preparation_rejection"]["reason"],
                        rejection_hash=value["causal_evidence_hash"],
                        state="rejected",
                        usage=None,
                        cost=None,
                    )
                    results.append(
                        (
                            row["transition_id"],
                            row["transition_hash"],
                            row["host_run_id"],
                            safe,
                            "verified",
                        )
                    )
                except (ValueError, TypeError, KeyError):
                    results.append(
                        (
                            row["transition_id"],
                            row["transition_hash"],
                            row["host_run_id"],
                            {"reason": "preparation_source_invalid"},
                            "unverifiable",
                        )
                    )
        for result in results:
            await asyncio.to_thread(self._save, result)
        return len(results)

    def _initialize(self):
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.audit_path, timeout=2) as db:
            db.executescript(_SCHEMA)

    def _save(self, result):
        ref, source_hash, host_run, safe, status = result
        with sqlite3.connect(self.audit_path, timeout=2) as db:
            # Preserve conflicting/corrupt historical versions; never replace a
            # verified source in place. Fingerprint namespace is a Host audit key.
            key = ref + ":" + source_hash
            db.execute(
                "INSERT OR IGNORE INTO preparation_audit_sources VALUES(?,?,?,?,?,?,?,?)",
                (
                    key,
                    source_hash,
                    self.owner_ref,
                    host_run,
                    None,
                    safe.get("recorded_at"),
                    canonical(safe),
                    status,
                ),
            )
            rule = "host-preparation-rejection-v1"
            db.execute(
                "INSERT OR IGNORE INTO preparation_audit_findings VALUES(?,?,?,?,?,?,?)",
                (
                    digest([self.owner_ref, ref, source_hash, rule]),
                    key,
                    source_hash,
                    self.owner_ref,
                    "host_primary_context",
                    rule,
                    safe["reason"],
                ),
            )
