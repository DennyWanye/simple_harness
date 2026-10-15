"""Explicit SDK control/child/workflow canonical sources. No diagnostic inference."""

from simple_harness.execution.audit import (
    CORE_SOURCES,
    RunOperationAuditV1,
    audit_hash,
    audit_reference,
)

STATES = frozenset(
    {
        "created",
        "committed",
        "claimed",
        "pending",
        "scheduled",
        "acked",
        "failed",
        "cancelled",
        "issued",
        "waiting",
        "completed",
        "resolved",
        "queued",
        "running",
        "prepared",
        "ready",
        "consumed",
        "quarantined",
        "abandoned",
        "staged",
        "preparing",
        "started",
        "succeeded",
        "unknown",
        "partial",
        "denied",
        "allowed",
        "expired",
    }
)


def core_rows(connection, run_id, *, limit):
    for table, keys, owners, kind in CORE_SOURCES:
        where = " OR ".join(f"{column}=?" for column in owners)
        rows = connection.execute(
            f"SELECT * FROM {table} WHERE {where} ORDER BY {','.join(keys)} LIMIT ?",
            (*([run_id] * len(owners)), limit),
        )
        for row in rows:
            yield table, keys, kind, dict(row)
    # Continuation preparation belongs to its real continuation's Run. The staging
    # XOR consumption constraint prevents duplication with consumed_run_id above.
    for row in connection.execute(
        "SELECT s.* FROM context_preparation_staging s JOIN continuations c "
        "ON c.continuation_id=s.consumed_continuation_id WHERE c.run_id=? "
        "ORDER BY s.stage_id LIMIT ?",
        (run_id, limit),
    ):
        yield "context_preparation_staging", ("stage_id",), "context", dict(row)
    # No Run column here: association is the canonical spawn operation, not time/ID guessing.
    for row in connection.execute(
        "SELECT r.* FROM workflow_spawn_continuation_ready r "
        "JOIN workflow_spawn_continuations c ON c.operation_id=r.operation_id "
        "WHERE c.parent_run_id=? ORDER BY r.ready_receipt_id LIMIT ?",
        (run_id, limit),
    ):
        yield "workflow_spawn_continuation_ready", ("ready_receipt_id",), "workflow", dict(row)


def core_operation(table, keys, kind, row):
    identity = table + ":" + audit_hash([row[k] for k in keys])
    state = row.get("state", row.get("phase", row.get("terminal_state", "recorded")))
    # Full original bytes are hashed, never exported. BLOBs use explicit hex encoding.
    source = {k: {"bytes_hex": v.hex()} if isinstance(v, bytes) else v for k, v in row.items()}
    return RunOperationAuditV1(
        kind + ":" + identity,
        kind,
        state if state in STATES else "recorded",
        row.get("version", 0),
        audit_hash(source),
        identity,
        record_type="receipt",
        operation_name=table,
        created_at=row.get("created_at", row.get("issued_at")),
        runtime_epoch=row.get("runtime_lease_epoch"),
        claim_epoch=row.get("claim_epoch"),
        attempt_count=row.get("attempt_count"),
        effect_id=row.get("effect_id"),
        request_hash=row.get("request_fingerprint", row.get("input_hash")),
        result_hash=row.get("outcome_hash", row.get("output_hash")),
        related_run_refs=tuple(
            sorted(
                {
                    audit_reference("run", row[key])
                    for key in (
                        "parent_run_id",
                        "child_run_id",
                        "source_run_id",
                        "target_run_id",
                    )
                    if row.get(key) is not None
                }
            )
        ),
    )
