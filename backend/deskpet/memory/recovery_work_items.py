# SPDX-License-Identifier: BUSL-1.1

"""Worker-side recognition of exact recovery-generation park receipts."""

from __future__ import annotations

import base64
import hashlib
import json

import aiosqlite

_WORKER_SPECS = {
    "projection-source": ("task_scope_projection_source_outbox", "outbox_id"),
    "projection-legacy": ("task_scope_projection_outbox", "outbox_id"),
    "search": ("task_scope_search_outbox", "outbox_id"),
    "foreground-signal": ("foreground_signal_outbox", "signal_id"),
    "foreground-lease": ("foreground_run_heads", "host_run_id"),
}


def _typed_value(value: object) -> dict[str, object]:
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bytes):
        return {"type": "blob", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "real", "value": format(value, ".17g")}
    return {"type": "text", "value": str(value)}


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def is_human_memory_work_item_parked_tx(
    db: aiosqlite.Connection,
    *,
    worker_kind: str,
    source_table: str,
    primary_key: str,
    item_pk: str,
) -> bool:
    """Return whether the current recovery generation parked this exact row."""

    if _WORKER_SPECS.get(worker_kind) != (source_table, primary_key):
        raise RuntimeError("human_memory_recovery_worker_kind_invalid")
    fence_cursor = await db.execute(
        "SELECT state,generation FROM human_memory_recovery_fence WHERE singleton=1"
    )
    fence = await fence_cursor.fetchone()
    await fence_cursor.close()
    if fence is None:
        return False
    row_cursor = await db.execute(
        f'SELECT * FROM "{source_table}" WHERE "{primary_key}"=?', (item_pk,)
    )
    row = await row_cursor.fetchone()
    columns = [str(item[0]) for item in row_cursor.description or ()]
    await row_cursor.close()
    if row is None:
        return False
    content_hash = _canonical_hash(
        {
            "table": source_table,
            "columns": columns,
            "values": [_typed_value(row[index]) for index in range(len(row))],
        }
    )
    if str(fence["state"]) in {"QUIESCED", "SEALED", "FAILED_CLOSED"}:
        # These states never admit worker progress.  Exact receipts are still
        # required by quiesce/manifest verification; this branch is the
        # worker-side fail-safe if that evidence is later corrupted.
        return True
    if worker_kind == "foreground-lease":
        # A recovery-parked lease stays revoked after reopen.  A legitimate
        # reclaim changes the durable head generation/content hash, so the old
        # receipt no longer matches and cannot fence the successor lease.
        parked_cursor = await db.execute(
            "SELECT disposition,item_content_hash FROM human_memory_recovery_work_items "
            "WHERE worker_kind=? AND source_table=? AND item_pk=? "
            "ORDER BY generation DESC LIMIT 1",
            (worker_kind, source_table, item_pk),
        )
    elif str(fence["state"]) == "OPEN":
        return False
    else:
        parked_cursor = await db.execute(
            "SELECT disposition,item_content_hash FROM human_memory_recovery_work_items "
            "WHERE generation=? AND worker_kind=? AND source_table=? AND item_pk=?",
            (fence["generation"], worker_kind, source_table, item_pk),
        )
    parked = await parked_cursor.fetchone()
    await parked_cursor.close()
    return bool(
        parked is not None
        and str(parked["disposition"]) in {"parked", "lease_parked"}
        and str(parked["item_content_hash"]) == content_hash
    )


__all__ = ["is_human_memory_work_item_parked_tx"]
