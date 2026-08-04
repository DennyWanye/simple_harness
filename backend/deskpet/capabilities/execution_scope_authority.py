"""Runtime execution-scope authority for mutable pre-execution workspaces.

The capability platform module is part of local tool build provenance.  Keep
the runtime-only workspace resolver separate so a directory decision cannot
accidentally change unrelated tool fingerprints.
"""

from __future__ import annotations

import json

from deskpet.capabilities.platform import CurrentExecutionScopeFacts
from deskpet.capabilities.store import CapabilityStore


class SqliteCurrentExecutionScopeAuthority:
    """Resolve immutable identity/catalog facts plus the current Run workspace."""

    def __init__(self, store: CapabilityStore) -> None:
        self._store = store

    async def resolve_current_execution_scope(
        self,
        *,
        run_id: str,
        owner_key: str,
        profile_generation: int,
        binding_epoch: int,
    ) -> CurrentExecutionScopeFacts:
        if not run_id:
            raise RuntimeError("execution_scope_run_id_missing")
        async with self._store.read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT s.run_context_json,
                              r.workspace_json AS current_workspace_json,
                              i.run_catalog_content_stamp,
                              c.request_scope_canonical_json
                       FROM execution_run_start_snapshots AS s
                       JOIN execution_runs AS r ON r.run_id=s.run_id
                       JOIN capability_snapshot_lease_intents AS i
                         ON i.run_id=s.run_id AND i.status='bound'
                       JOIN capability_run_catalog_snapshots AS c
                         ON c.run_catalog_content_stamp=
                            i.run_catalog_content_stamp
                       WHERE s.run_id=?
                       LIMIT 1""",
                    (run_id,),
                )
            ).fetchone()
            if row is None:
                raise RuntimeError("execution_scope_durable_facts_missing")
            entries = await (
                await db.execute(
                    """SELECT pack_id
                       FROM capability_run_catalog_snapshot_entries
                       WHERE run_catalog_content_stamp=?
                         AND entry_kind='pack'
                       ORDER BY ordinal""",
                    (str(row["run_catalog_content_stamp"]),),
                )
            ).fetchall()

        context = json.loads(str(row["run_context_json"]))
        workspace = json.loads(str(row["current_workspace_json"]))
        scope_payload = json.loads(str(row["request_scope_canonical_json"]))
        if (
            str(context.get("owner_key") or "") != owner_key
            or int(context.get("profile_generation") or 0) != profile_generation
            or int(context.get("binding_epoch") or 0) != binding_epoch
        ):
            raise RuntimeError("execution_scope_start_identity_mismatch")
        capability_hash = str(context.get("capability_hash") or "")
        scope_hash = str(workspace.get("scope_hash") or "")
        if not capability_hash or not scope_hash:
            raise RuntimeError("execution_scope_start_fingerprint_missing")
        return CurrentExecutionScopeFacts(
            owner_key=owner_key,
            profile_generation=profile_generation,
            binding_epoch=binding_epoch,
            capability_hash=capability_hash,
            scope_hash=scope_hash,
            owner_binding_set_stamp=str(row["run_catalog_content_stamp"]),
            scope=str(scope_payload.get("scope") or "user"),
            scope_key=str(scope_payload.get("scope_key") or owner_key),
            pack_ids=tuple(str(item["pack_id"]) for item in entries),
        )


__all__ = ["SqliteCurrentExecutionScopeAuthority"]
