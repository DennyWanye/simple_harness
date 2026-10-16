"""Cleanup stages: deliveries, tombstones, checkpoints, blobs, research records and orphans."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import RetentionCore
from .support import (
    RUN_STAGE,
    RetentionStageResult,
    _CleanupPlan,
    _column_values,
    _placeholders,
    _ResearchCleanupPlan,
)

DELIVERY_STAGE = "delivered_terminal_events"
TOMBSTONE_STAGE = "evaluation_tombstones"
CHECKPOINT_STAGE = "checkpoint_reachability"
BLOB_REF_STAGE = "blob_refs"
ORPHAN_STAGE = "orphan_grace"
RESERVATION_STAGE = "expired_target_reservations"
CONTROL_STAGE = "expired_research_controls"
LINEAGE_STAGE = "research_lineage_and_pins"
SNAPSHOT_STAGE = "research_snapshot_refs"


class CleanupStageMixin(RetentionCore):
    """Cleanup stages: deliveries, tombstones, checkpoints, blobs, research records and orphans."""

    __slots__ = ()

    async def _cleanup_deliveries(
        self, plan: _CleanupPlan, *, dry_run: bool
    ) -> RetentionStageResult:
        items = tuple(
            [
                *(f"delivery:{item}" for item in plan.delivery_ids),
                *(f"event:{item}" for item in plan.event_ids),
            ]
        )
        applied = 0
        if items and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                if plan.delivery_ids:
                    cursor = await db.execute(
                        f"DELETE FROM workflow_deliveries WHERE delivery_id IN ({_placeholders(plan.delivery_ids)})",
                        plan.delivery_ids,
                    )
                    applied += cursor.rowcount
                if plan.event_ids:
                    cursor = await db.execute(
                        f"DELETE FROM workflow_events WHERE event_id IN ({_placeholders(plan.event_ids)})",
                        plan.event_ids,
                    )
                    applied += cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(
            name=DELIVERY_STAGE,
            candidates=items,
            applied=applied,
            protected=plan.protected,
        )

    async def _retain_tombstones(
        self, plan: _CleanupPlan, *, dry_run: bool
    ) -> RetentionStageResult:
        retained = tuple(
            run_id for run_id in plan.full_run_ids if run_id not in set(plan.final_run_ids)
        )
        items = tuple(
            [
                *(f"evaluation:{item}" for item in plan.expired_evaluation_ids),
                *(f"tombstone:{item}" for item in retained),
                *(f"expired-tombstone:{item}" for item in plan.final_run_ids),
            ]
        )
        applied = 0
        if plan.full_run_ids and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                if plan.expired_evaluation_ids:
                    cursor = await db.execute(
                        f"DELETE FROM evaluations WHERE evaluation_id IN ({_placeholders(plan.expired_evaluation_ids)})",
                        plan.expired_evaluation_ids,
                    )
                    applied += cursor.rowcount
                cursor = await db.execute(
                    f"""UPDATE workflow_runs SET active_nodes_json='[]',lease_owner=NULL,
                    lease_expires_at=NULL,heartbeat_at=NULL,cancel_reason=NULL,error_json=NULL,
                    recovery_action=NULL WHERE run_id IN ({_placeholders(plan.full_run_ids)})""",
                    plan.full_run_ids,
                )
                applied += cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(name=TOMBSTONE_STAGE, candidates=items, applied=applied)

    async def _cleanup_checkpoints(
        self, plan: _CleanupPlan, *, dry_run: bool
    ) -> RetentionStageResult:
        items = tuple(
            [
                *(f"owner:{item}" for item in plan.full_run_ids),
                *(f"checkpoint:{key.diagnostic_id}" for key in plan.checkpoint_keys),
            ]
        )
        applied = 0
        if plan.full_run_ids and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    f"DELETE FROM workflow_checkpoint_owners WHERE run_id IN ({_placeholders(plan.full_run_ids)})",
                    plan.full_run_ids,
                )
                applied += cursor.rowcount
                for key in plan.checkpoint_keys:
                    params = (key.thread_id, key.checkpoint_ns, key.checkpoint_id)
                    cursor = await db.execute(
                        """DELETE FROM workflow_pending_writes
                        WHERE thread_id=? AND checkpoint_ns=? AND base_checkpoint_id=?""",
                        params,
                    )
                    applied += cursor.rowcount
                    cursor = await db.execute(
                        """DELETE FROM workflow_checkpoint_effects
                        WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?""",
                        params,
                    )
                    applied += cursor.rowcount
                    cursor = await db.execute(
                        """DELETE FROM workflow_checkpoints
                        WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?""",
                        params,
                    )
                    applied += cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(name=CHECKPOINT_STAGE, candidates=items, applied=applied)

    async def _cleanup_blob_refs(
        self, plan: _CleanupPlan, *, dry_run: bool, delete_runs: bool = True
    ) -> RetentionStageResult:
        owner_groups = self._owner_groups(plan)
        ref_items = await self._matching_ref_items(owner_groups)
        items = tuple(
            [
                *ref_items,
                *(f"trace:{item}" for item in plan.trace_ids),
                *(f"node:{item}" for item in plan.node_ids),
                *(f"effect:{item}" for item in plan.effect_ids),
                *(f"run:{item}" for item in plan.final_run_ids if delete_runs),
            ]
        )
        applied = 0
        if any(owner_groups.values()) and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                for owner_kind, owner_ids in owner_groups.items():
                    if not owner_ids:
                        continue
                    cursor = await db.execute(
                        f"""DELETE FROM workflow_blob_refs WHERE owner_kind=?
                        AND owner_id IN ({_placeholders(owner_ids)})""",
                        (owner_kind, *owner_ids),
                    )
                    applied += cursor.rowcount
                if plan.node_ids:
                    cursor = await db.execute(
                        f"DELETE FROM workflow_node_effects WHERE node_execution_id IN ({_placeholders(plan.node_ids)})",
                        plan.node_ids,
                    )
                    applied += cursor.rowcount
                if plan.effect_ids:
                    cursor = await db.execute(
                        f"DELETE FROM workflow_checkpoint_effects WHERE effect_id IN ({_placeholders(plan.effect_ids)})",
                        plan.effect_ids,
                    )
                    applied += cursor.rowcount
                if plan.full_run_ids:
                    for table in (
                        "trace_runs",
                        "workflow_nodes",
                        "workflow_decisions",
                        "workflow_effects",
                    ):
                        cursor = await db.execute(
                            f"DELETE FROM {table} WHERE run_id IN ({_placeholders(plan.full_run_ids)})",
                            plan.full_run_ids,
                        )
                        applied += cursor.rowcount
                if plan.final_run_ids and delete_runs:
                    placeholders = _placeholders(plan.final_run_ids)
                    for query in (
                        f"DELETE FROM workflow_start_requests WHERE run_id IN ({placeholders})",
                        f"DELETE FROM workflow_target_reservations WHERE run_id IN ({placeholders})",
                    ):
                        cursor = await db.execute(query, plan.final_run_ids)
                        applied += cursor.rowcount
                    cursor = await db.execute(
                        f"DELETE FROM workflow_runs WHERE run_id IN ({placeholders})",
                        plan.final_run_ids,
                    )
                    applied += cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(name=BLOB_REF_STAGE, candidates=items, applied=applied)

    async def _cleanup_research_controls(
        self,
        candidates: dict[str, tuple[str, ...]],
        *,
        dry_run: bool,
    ) -> RetentionStageResult:
        cleanup_ids = tuple(sorted(candidates.get("cleanup_eligible", ())))
        overdue = tuple(sorted(candidates.get("settle_deadline_exceeded", ())))
        applied = 0
        if cleanup_ids and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    f"""DELETE FROM workflow_run_control_commands
                    WHERE command_id IN ({_placeholders(cleanup_ids)})
                      AND status IN ('consumed','rejected','expired')""",
                    cleanup_ids,
                )
                applied = cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(
            name=CONTROL_STAGE,
            candidates=tuple(f"command:{item}" for item in cleanup_ids),
            applied=applied,
            protected=tuple(f"settle-overdue:{item}" for item in overdue),
        )

    async def _cleanup_research_lineage(
        self, plan: _ResearchCleanupPlan, *, dry_run: bool
    ) -> RetentionStageResult:
        items = tuple(
            [
                *(f"lineage:{item}" for item in plan.lineage_operation_ids),
                *(f"pin:{item}" for item in plan.pin_ids),
            ]
        )
        applied = 0
        if items and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                for operation_id in plan.lineage_operation_ids:
                    cursor = await db.execute(
                        "DELETE FROM workflow_research_lineage WHERE operation_id=?",
                        (operation_id,),
                    )
                    applied += cursor.rowcount
                if plan.pin_ids:
                    cursor = await db.execute(
                        f"""DELETE FROM workflow_research_snapshot_pins
                        WHERE pin_id IN ({_placeholders(plan.pin_ids)})""",
                        plan.pin_ids,
                    )
                    applied += cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(name=LINEAGE_STAGE, candidates=items, applied=applied)

    async def _cleanup_research_snapshots(
        self, plan: _ResearchCleanupPlan, *, dry_run: bool
    ) -> RetentionStageResult:
        items = tuple(
            [
                *(
                    f"snapshot-ref:{snapshot_hash}:{blob_ref}"
                    for snapshot_hash, blob_ref in plan.snapshot_blob_refs
                ),
                *(f"snapshot:{item}" for item in plan.snapshot_hashes),
            ]
        )
        applied = 0
        if plan.snapshot_hashes and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    f"""DELETE FROM workflow_blob_refs
                    WHERE owner_kind='research_snapshot'
                      AND owner_id IN ({_placeholders(plan.snapshot_hashes)})""",
                    plan.snapshot_hashes,
                )
                applied += cursor.rowcount
                cursor = await db.execute(
                    f"""DELETE FROM workflow_research_snapshots
                    WHERE snapshot_hash IN ({_placeholders(plan.snapshot_hashes)})""",
                    plan.snapshot_hashes,
                )
                applied += cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(name=SNAPSHOT_STAGE, candidates=items, applied=applied)

    async def _cleanup_research_runs(
        self, plan: _ResearchCleanupPlan, *, dry_run: bool
    ) -> RetentionStageResult:
        items = tuple(f"run:{item}" for item in plan.run_ids)
        applied = 0
        if plan.run_ids and not dry_run:
            db = await self._connect()
            try:
                await db.execute("BEGIN IMMEDIATE")
                placeholders = _placeholders(plan.run_ids)
                for query in (
                    f"DELETE FROM workflow_start_requests WHERE run_id IN ({placeholders})",
                    f"DELETE FROM workflow_target_reservations WHERE run_id IN ({placeholders})",
                ):
                    cursor = await db.execute(query, plan.run_ids)
                    applied += cursor.rowcount
                cursor = await db.execute(
                    f"DELETE FROM workflow_runs WHERE run_id IN ({placeholders})",
                    plan.run_ids,
                )
                applied += cursor.rowcount
                await db.commit()
            except BaseException:
                if db.in_transaction:
                    await db.rollback()
                raise
            finally:
                await db.close()
        return RetentionStageResult(name=RUN_STAGE, candidates=items, applied=applied)

    @staticmethod
    def _owner_groups(plan: _CleanupPlan) -> dict[str, tuple[str, ...]]:
        owner_groups = {
            "run": plan.full_run_ids,
            "workflow_run": plan.full_run_ids,
            "event": plan.event_ids,
            "workflow_event": plan.event_ids,
            "delivery": plan.delivery_ids,
            "workflow_delivery": plan.delivery_ids,
            "node": plan.node_ids,
            "workflow_node": plan.node_ids,
            "effect": plan.effect_ids,
            "workflow_effect": plan.effect_ids,
            "trace": plan.trace_ids,
            "trace_run": plan.trace_ids,
            "span": plan.span_ids,
            "trace_span": plan.span_ids,
            "evaluation": plan.expired_evaluation_ids,
        }
        checkpoint_owner_ids = tuple(
            {key.checkpoint_id for key in plan.checkpoint_keys}
            | {key.diagnostic_id for key in plan.checkpoint_keys}
        )
        owner_groups["checkpoint"] = checkpoint_owner_ids
        owner_groups["workflow_checkpoint"] = checkpoint_owner_ids
        for owner_kind, owner_id in plan.dangling_ref_owners:
            owner_groups[owner_kind] = tuple(sorted({*owner_groups.get(owner_kind, ()), owner_id}))
        return owner_groups

    async def _matching_ref_items(
        self, owner_groups: dict[str, tuple[str, ...]]
    ) -> tuple[str, ...]:
        db = await self._connect()
        try:
            items: list[str] = []
            for owner_kind, owner_ids in owner_groups.items():
                if not owner_ids:
                    continue
                rows = await (
                    await db.execute(
                        f"""SELECT sha256,owner_id FROM workflow_blob_refs WHERE owner_kind=?
                        AND owner_id IN ({_placeholders(owner_ids)}) ORDER BY sha256,owner_id""",
                        (owner_kind, *owner_ids),
                    )
                ).fetchall()
                items.extend(f"ref:{owner_kind}:{row['owner_id']}:{row['sha256']}" for row in rows)
            return tuple(sorted(items))
        finally:
            await db.close()

    async def _cleanup_orphans(
        self,
        now: float,
        *,
        plan: _CleanupPlan,
        dry_run: bool,
        extra_removed_owners: tuple[tuple[str, str], ...] = (),
    ) -> RetentionStageResult:
        cutoff = now - self.policy.orphan_grace_seconds
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    """SELECT b.sha256,b.relative_path,b.created_at,r.owner_kind,r.owner_id
                    FROM workflow_blobs b LEFT JOIN workflow_blob_refs r ON r.sha256=b.sha256
                    ORDER BY b.sha256,r.owner_kind,r.owner_id"""
                )
            ).fetchall()
            registered = await (
                await db.execute("SELECT sha256,relative_path FROM workflow_blobs")
            ).fetchall()
            continuation_spec_digests = {
                str(row[0])
                for row in await (
                    await db.execute(
                        "SELECT spec_blob_digest FROM workflow_research_continuation_heads"
                    )
                ).fetchall()
            }
        finally:
            await db.close()

        warnings: list[str] = []
        database_files: list[tuple[str, Path]] = []
        registered_paths: set[Path] = set()
        for row in registered:
            path = self._safe_blob_path(str(row["relative_path"]))
            if path is None:
                warnings.append(f"invalid-blob-path:{row['sha256']}")
                continue
            registered_paths.add(path)
            if not path.exists():
                warnings.append(f"missing-blob-file:{row['sha256']}")

        removed_owners = {
            (owner_kind, owner_id)
            for owner_kind, owner_ids in self._owner_groups(plan).items()
            for owner_id in owner_ids
        }
        removed_owners.update(extra_removed_owners)
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = grouped.setdefault(
                str(row["sha256"]),
                {
                    "relative_path": str(row["relative_path"]),
                    "created_at": float(row["created_at"]),
                    "owners": [],
                },
            )
            if row["owner_kind"] is not None:
                item["owners"].append((str(row["owner_kind"]), str(row["owner_id"])))
        for digest, item in grouped.items():
            if digest in continuation_spec_digests:
                continue
            if item["created_at"] > cutoff:
                continue
            owners = item["owners"]
            if owners and (not dry_run or any(owner not in removed_owners for owner in owners)):
                continue
            path = self._safe_blob_path(item["relative_path"])
            if path is None:
                warnings.append(f"invalid-blob-path:{digest}")
                continue
            database_files.append((digest, path))

        filesystem_orphans: list[Path] = []
        if self.blob_root.exists():
            root = self.blob_root.resolve()
            for path in self.blob_root.rglob("*"):
                if not path.is_file():
                    continue
                resolved = path.resolve()
                try:
                    resolved.relative_to(root)
                except ValueError:
                    warnings.append(f"unsafe-symlink:{path.name}")
                    continue
                if resolved in registered_paths:
                    continue
                try:
                    if path.stat().st_mtime <= cutoff:
                        filesystem_orphans.append(resolved)
                except OSError as exc:
                    warnings.append(
                        f"stat-failed:{self._diagnostic_path(path)}:{type(exc).__name__}"
                    )

        items = tuple(
            [
                *(f"blob:{digest}" for digest, _ in database_files),
                *(f"file:{self._diagnostic_path(path)}" for path in sorted(filesystem_orphans)),
            ]
        )
        applied = 0
        if not dry_run:
            if database_files:
                digests = tuple(digest for digest, _ in database_files)
                db = await self._connect()
                try:
                    await db.execute("BEGIN IMMEDIATE")
                    cursor = await db.execute(
                        f"""DELETE FROM workflow_blobs WHERE sha256 IN ({_placeholders(digests)})
                        AND NOT EXISTS(
                            SELECT 1 FROM workflow_blob_refs r
                            WHERE r.sha256=workflow_blobs.sha256
                        ) AND NOT EXISTS(
                            SELECT 1 FROM workflow_research_continuation_heads h
                            WHERE h.spec_blob_digest=workflow_blobs.sha256
                        )""",
                        digests,
                    )
                    applied += cursor.rowcount
                    await db.commit()
                except BaseException:
                    if db.in_transaction:
                        await db.rollback()
                    raise
                finally:
                    await db.close()
                for _, path in database_files:
                    try:
                        existed = path.exists()
                        path.unlink(missing_ok=True)
                        applied += int(existed)
                    except OSError as exc:
                        warnings.append(
                            f"unlink-failed:{self._diagnostic_path(path)}:{type(exc).__name__}"
                        )
            for path in filesystem_orphans:
                try:
                    path.unlink(missing_ok=True)
                    applied += 1
                except OSError as exc:
                    warnings.append(
                        f"unlink-failed:{self._diagnostic_path(path)}:{type(exc).__name__}"
                    )
        return RetentionStageResult(
            name=ORPHAN_STAGE,
            candidates=items,
            applied=applied,
            warnings=tuple(warnings),
        )

    def _safe_blob_path(self, relative_path: str) -> Path | None:
        root = self.blob_root.resolve()
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    def _diagnostic_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.blob_root.resolve()).as_posix()
        except ValueError:
            return "[outside-blob-root]"

    async def _reconcile_reservations(self, now: float, *, dry_run: bool) -> RetentionStageResult:
        db = await self._connect()
        try:
            keys = await _column_values(
                db,
                """SELECT reservation_key FROM workflow_target_reservations
                WHERE status IN ('prepared','claimed')
                  AND lease_expires_at IS NOT NULL AND lease_expires_at<=?
                ORDER BY reservation_key""",
                (now,),
            )
            applied = 0
            if keys and not dry_run:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    f"""DELETE FROM workflow_target_reservations
                    WHERE reservation_key IN ({_placeholders(keys)})
                      AND status IN ('prepared','claimed')
                      AND lease_expires_at IS NOT NULL AND lease_expires_at<=?""",
                    (*keys, now),
                )
                applied = cursor.rowcount
                await db.commit()
            return RetentionStageResult(
                name=RESERVATION_STAGE,
                candidates=tuple(f"reservation:{key}" for key in keys),
                applied=applied,
            )
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()
