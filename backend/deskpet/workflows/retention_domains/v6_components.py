"""Deep-research v6 component closure, ownership and deletion."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import aiosqlite

from ..contracts import TERMINAL_RUN_STATUSES
from ..store.research_repository import ResearchWorkflowRepository
from .core import RetentionCore
from .support import RUN_STAGE, RetentionStageResult, _column_values, _placeholders

logger = logging.getLogger(__name__)


class V6ComponentCleanupMixin(RetentionCore):
    """Deep-research v6 component closure, ownership and deletion."""

    __slots__ = ()

    async def _cleanup_v6_components(self, now: float, *, dry_run: bool) -> RetentionStageResult:
        """Collect dead v6 continuation components in one SQLite transaction.

        The single-head row is the durable edge.  Reachability is deliberately
        undirected here: retaining either endpoint retains the complete chain.
        Legacy v1-v5 lineage remains on the historical staged collector.
        """

        db = await self._connect()
        deleted: list[str] = []
        protected: list[str] = []
        files: list[Path] = []
        try:
            if not dry_run:
                await db.execute("BEGIN IMMEDIATE")
            components = await self._v6_continuation_components(db)
            for run_ids in components:
                reasons = await self._v6_component_root_reasons(db, run_ids, now)
                label = "+".join(sorted(reasons))
                if reasons:
                    protected.extend(f"{run_id}:{label}" for run_id in run_ids)
                    continue
                deleted.extend(run_ids)
                if not dry_run:
                    files.extend(await self._delete_v6_component(db, run_ids))
            if not dry_run:
                await db.commit()
        except BaseException:
            if db.in_transaction:
                await db.rollback()
            raise
        finally:
            await db.close()

        warnings: list[str] = []
        if not dry_run:
            for path in sorted(set(files)):
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    warnings.append(
                        f"unlink-failed:{self._diagnostic_path(path)}:{type(exc).__name__}"
                    )
        return RetentionStageResult(
            name=RUN_STAGE,
            candidates=tuple(f"run:{item}" for item in sorted(deleted)),
            applied=len(deleted) if not dry_run else 0,
            protected=tuple(sorted(protected)),
            warnings=tuple(warnings),
        )

    @staticmethod
    async def _v6_continuation_components(
        db: aiosqlite.Connection,
    ) -> tuple[tuple[str, ...], ...]:
        rows = await (
            await db.execute(
                """SELECT parent_run_id,child_run_id
                FROM workflow_research_continuation_heads
                ORDER BY parent_run_id,child_run_id"""
            )
        ).fetchall()
        adjacency: dict[str, set[str]] = {}
        for row in rows:
            parent = str(row["parent_run_id"])
            child = str(row["child_run_id"])
            adjacency.setdefault(parent, set()).add(child)
            adjacency.setdefault(child, set()).add(parent)
        result: list[tuple[str, ...]] = []
        unseen = set(adjacency)
        while unseen:
            start = min(unseen)
            stack = [start]
            component: set[str] = set()
            while stack:
                current = stack.pop()
                if current in component:
                    continue
                component.add(current)
                stack.extend(adjacency.get(current, ()))
            unseen.difference_update(component)
            result.append(tuple(sorted(component)))
        return tuple(result)

    async def _v6_component_root_reasons(
        self,
        db: aiosqlite.Connection,
        run_ids: tuple[str, ...],
        now: float,
    ) -> set[str]:
        """Return live or fail-closed reasons which protect a component."""

        reasons: set[str] = set()
        placeholders = _placeholders(run_ids)
        runs = await (
            await db.execute(
                f"""SELECT run_id,workflow_name,workflow_version,status,ended_at
                FROM workflow_runs WHERE run_id IN ({placeholders})""",
                run_ids,
            )
        ).fetchall()
        if len(runs) != len(run_ids):
            return {"corrupt_component"}
        terminal_cutoff = now - self.policy.terminal_seconds
        for row in runs:
            if str(row["workflow_name"]) != "deep_research" or str(row["workflow_version"]) != "v6":
                reasons.add("identity_mismatch")
            status = str(row["status"])
            if status not in {item.value for item in TERMINAL_RUN_STATUSES}:
                reasons.add("nonterminal")
            elif row["ended_at"] is None:
                reasons.add("corrupt_terminal")
            elif float(row["ended_at"]) > terminal_cutoff:
                reasons.add("terminal_window")

        active_control = await (
            await db.execute(
                f"""SELECT 1 FROM workflow_run_control_commands
                WHERE run_id IN ({placeholders})
                  AND status IN ('open','accepted','observed','settled') LIMIT 1""",
                run_ids,
            )
        ).fetchone()
        if active_control is not None:
            reasons.add("open_control")

        active_delivery = await (
            await db.execute(
                f"""SELECT 1 FROM workflow_deliveries
                WHERE run_id IN ({placeholders}) AND required_durable=1
                  AND status NOT IN ('delivered','discarded')
                  AND NOT(status='failed' AND next_attempt_at IS NULL AND attempts>=5)
                LIMIT 1""",
                run_ids,
            )
        ).fetchone()
        if active_delivery is not None:
            reasons.add("required_delivery")

        active_pin = await (
            await db.execute(
                f"""SELECT 1 FROM workflow_research_snapshot_pins
                WHERE run_id IN ({placeholders})
                  AND (expires_at IS NULL OR expires_at>?) LIMIT 1""",
                (*run_ids, now),
            )
        ).fetchone()
        if active_pin is not None:
            reasons.add("snapshot_pin")

        if not await self._v6_component_closure_is_valid(db, run_ids):
            reasons.add("closure_invalid")
        if await self._v6_component_has_external_owner(db, run_ids):
            reasons.add("external_blob_owner")
        return reasons

    async def _v6_component_closure_is_valid(
        self, db: aiosqlite.Connection, run_ids: tuple[str, ...]
    ) -> bool:
        placeholders = _placeholders(run_ids)
        heads = await (
            await db.execute(
                f"""SELECT * FROM workflow_research_continuation_heads
                WHERE parent_run_id IN ({placeholders}) OR child_run_id IN ({placeholders})
                ORDER BY parent_run_id""",
                (*run_ids, *run_ids),
            )
        ).fetchall()
        for head in heads:
            if (
                str(head["parent_run_id"]) not in run_ids
                or str(head["child_run_id"]) not in run_ids
            ):
                return False
            spec = await (
                await db.execute(
                    "SELECT sha256,relative_path FROM workflow_blobs WHERE sha256=?",
                    (head["spec_blob_digest"],),
                )
            ).fetchone()
            if spec is None:
                return False
            spec_path = self._safe_blob_path(str(spec["relative_path"]))
            if spec_path is None or not spec_path.is_file():
                return False
            try:
                if hashlib.sha256(spec_path.read_bytes()).hexdigest() != str(spec["sha256"]):
                    return False
            except OSError:
                return False
            lineage = await (
                await db.execute(
                    """SELECT parent_run_id,parent_operation_id,snapshot_hash
                    FROM workflow_research_lineage
                    WHERE operation_id=? AND run_id=?""",
                    (head["child_operation_id"], head["child_run_id"]),
                )
            ).fetchone()
            if (
                lineage is None
                or str(lineage["parent_run_id"]) != str(head["parent_run_id"])
                or str(lineage["parent_operation_id"]) != str(head["parent_operation_id"])
                or str(lineage["snapshot_hash"]) != str(head["source_snapshot_hash"])
            ):
                return False
            snapshot = await (
                await db.execute(
                    """SELECT run_id,manifest_ref FROM workflow_research_snapshots
                    WHERE snapshot_hash=?""",
                    (head["source_snapshot_hash"],),
                )
            ).fetchone()
            if snapshot is None or str(snapshot["run_id"]) != str(head["parent_run_id"]):
                return False
            refs = await (
                await db.execute(
                    """SELECT b.sha256,b.relative_path FROM workflow_blob_refs r
                    JOIN workflow_blobs b ON b.sha256=r.sha256
                    WHERE r.owner_kind='research_snapshot' AND r.owner_id=?
                    ORDER BY b.sha256""",
                    (head["source_snapshot_hash"],),
                )
            ).fetchall()
            if not refs:
                return False
            manifest_ref = str(snapshot["manifest_ref"])
            manifest_digest = (
                manifest_ref[7:] if manifest_ref.startswith("sha256:") else manifest_ref
            )
            if manifest_digest not in {str(row["sha256"]) for row in refs}:
                return False
            manifest_row = next(
                (row for row in refs if str(row["sha256"]) == manifest_digest), None
            )
            if manifest_row is None:
                return False
            try:
                snapshot_value = json.loads(
                    self._safe_blob_path(str(manifest_row["relative_path"])).read_text(
                        encoding="utf-8"
                    )
                )

                ResearchWorkflowRepository._validate_v6_snapshot(
                    snapshot_value, parent_run_id=str(head["parent_run_id"])
                )
                if snapshot_value.get("snapshot_hash") != str(head["source_snapshot_hash"]):
                    return False
                declared_closure = {str(ref)[7:] for ref in snapshot_value.get("closure_refs", ())}
                if not declared_closure <= {str(row["sha256"]) for row in refs}:
                    return False
            except Exception:  # noqa: BLE001 - 闭包数据坏了时按无效处理
                logger.debug("v6_component_closure_check_failed", exc_info=True)
                return False
            for row in refs:
                path = self._safe_blob_path(str(row["relative_path"]))
                if path is None or not path.is_file():
                    return False
                try:
                    if hashlib.sha256(path.read_bytes()).hexdigest() != str(row["sha256"]):
                        return False
                except OSError:
                    return False

        # Every component checkpoint must remain dereferenceable while it is
        # retained.  Missing owner/checkpoint edges are treated as corruption.
        missing_checkpoint = await (
            await db.execute(
                f"""SELECT 1 FROM workflow_checkpoint_owners owner
                LEFT JOIN workflow_checkpoints checkpoint
                  ON checkpoint.thread_id=owner.thread_id
                 AND checkpoint.checkpoint_ns=owner.checkpoint_ns
                 AND checkpoint.checkpoint_id=owner.checkpoint_id
                WHERE owner.run_id IN ({placeholders})
                  AND checkpoint.checkpoint_id IS NULL LIMIT 1""",
                run_ids,
            )
        ).fetchone()
        return missing_checkpoint is None

    async def _v6_component_owner_pairs(
        self, db: aiosqlite.Connection, run_ids: tuple[str, ...]
    ) -> set[tuple[str, str]]:
        placeholders = _placeholders(run_ids)
        pairs: set[tuple[str, str]] = set()
        for kind in ("run", "workflow_run", "run_staging"):
            pairs.update((kind, run_id) for run_id in run_ids)
        table_kinds = (
            ("workflow_events", "event_id", ("event", "workflow_event")),
            ("workflow_deliveries", "delivery_id", ("delivery", "workflow_delivery")),
            ("workflow_nodes", "node_execution_id", ("node", "workflow_node")),
            ("workflow_effects", "effect_id", ("effect", "workflow_effect")),
            ("trace_runs", "trace_id", ("trace", "trace_run")),
            ("evaluations", "evaluation_id", ("evaluation",)),
        )
        for table, column, kinds in table_kinds:
            values = await _column_values(
                db,
                f"SELECT {column} FROM {table} WHERE run_id IN ({placeholders})",
                run_ids,
            )
            for kind in kinds:
                pairs.update((kind, value) for value in values)
        trace_ids = tuple(owner_id for kind, owner_id in pairs if kind == "trace")
        if trace_ids:
            span_ids = await _column_values(
                db,
                f"SELECT span_id FROM trace_spans WHERE trace_id IN ({_placeholders(trace_ids)})",
                trace_ids,
            )
            for kind in ("span", "trace_span"):
                pairs.update((kind, value) for value in span_ids)
        checkpoints = await (
            await db.execute(
                f"""SELECT thread_id,checkpoint_ns,checkpoint_id
                FROM workflow_checkpoint_owners WHERE run_id IN ({placeholders})""",
                run_ids,
            )
        ).fetchall()
        for row in checkpoints:
            checkpoint_id = str(row["checkpoint_id"])
            diagnostic = f"{row['thread_id']}:{row['checkpoint_ns']}:{checkpoint_id}"
            pairs.update(
                {
                    ("checkpoint", checkpoint_id),
                    ("workflow_checkpoint", checkpoint_id),
                    ("checkpoint", diagnostic),
                    ("workflow_checkpoint", diagnostic),
                }
            )
        snapshots = await _column_values(
            db,
            f"SELECT snapshot_hash FROM workflow_research_snapshots WHERE run_id IN ({placeholders})",
            run_ids,
        )
        pairs.update(("research_snapshot", item) for item in snapshots)
        pending = await (
            await db.execute(
                "SELECT owner_id FROM workflow_blob_refs WHERE owner_kind='pending_task'"
            )
        ).fetchall()
        pairs.update(
            ("pending_task", str(row["owner_id"]))
            for row in pending
            if str(row["owner_id"]).split(":", 1)[0] in run_ids
        )
        return pairs

    async def _v6_component_has_external_owner(
        self, db: aiosqlite.Connection, run_ids: tuple[str, ...]
    ) -> bool:
        owner_pairs = await self._v6_component_owner_pairs(db, run_ids)
        if not owner_pairs:
            return False
        owned_digests: set[str] = set()
        for kind, owner_id in owner_pairs:
            if kind not in {
                "checkpoint",
                "workflow_checkpoint",
                "delivery",
                "workflow_delivery",
            }:
                continue
            rows = await (
                await db.execute(
                    "SELECT sha256 FROM workflow_blob_refs WHERE owner_kind=? AND owner_id=?",
                    (kind, owner_id),
                )
            ).fetchall()
            owned_digests.update(str(row[0]) for row in rows)
        for digest in owned_digests:
            rows = await (
                await db.execute(
                    "SELECT owner_kind,owner_id FROM workflow_blob_refs WHERE sha256=?",
                    (digest,),
                )
            ).fetchall()
            if any((str(row[0]), str(row[1])) not in owner_pairs for row in rows):
                return True

        # A checkpoint itself may be shared even when its payload has no
        # registered blob-ref row.
        placeholders = _placeholders(run_ids)
        shared_checkpoint = await (
            await db.execute(
                f"""SELECT 1 FROM workflow_checkpoint_owners own
                JOIN workflow_checkpoint_owners external
                  ON external.thread_id=own.thread_id
                 AND external.checkpoint_ns=own.checkpoint_ns
                 AND external.checkpoint_id=own.checkpoint_id
                WHERE own.run_id IN ({placeholders})
                  AND external.run_id NOT IN ({placeholders}) LIMIT 1""",
                (*run_ids, *run_ids),
            )
        ).fetchone()
        return shared_checkpoint is not None

    async def _delete_v6_component(
        self, db: aiosqlite.Connection, run_ids: tuple[str, ...]
    ) -> tuple[Path, ...]:
        """Delete one already-revalidated component in the contractual order."""

        placeholders = _placeholders(run_ids)
        checkpoint_rows = await (
            await db.execute(
                f"""SELECT thread_id,checkpoint_ns,checkpoint_id
                FROM workflow_checkpoint_owners WHERE run_id IN ({placeholders})""",
                run_ids,
            )
        ).fetchall()
        effect_ids = await _column_values(
            db,
            f"SELECT effect_id FROM workflow_effects WHERE run_id IN ({placeholders})",
            run_ids,
        )
        node_ids = await _column_values(
            db,
            f"SELECT node_execution_id FROM workflow_nodes WHERE run_id IN ({placeholders})",
            run_ids,
        )
        event_ids = await _column_values(
            db,
            f"SELECT event_id FROM workflow_events WHERE run_id IN ({placeholders})",
            run_ids,
        )
        delivery_ids = await _column_values(
            db,
            f"SELECT delivery_id FROM workflow_deliveries WHERE run_id IN ({placeholders})",
            run_ids,
        )
        trace_ids = await _column_values(
            db,
            f"SELECT trace_id FROM trace_runs WHERE run_id IN ({placeholders})",
            run_ids,
        )
        evaluation_ids = await _column_values(
            db,
            f"SELECT evaluation_id FROM evaluations WHERE run_id IN ({placeholders})",
            run_ids,
        )
        snapshots = await _column_values(
            db,
            f"SELECT snapshot_hash FROM workflow_research_snapshots WHERE run_id IN ({placeholders})",
            run_ids,
        )
        owner_pairs = await self._v6_component_owner_pairs(db, run_ids)
        candidate_digests: set[str] = set()
        for kind, owner_id in owner_pairs:
            rows = await (
                await db.execute(
                    "SELECT sha256 FROM workflow_blob_refs WHERE owner_kind=? AND owner_id=?",
                    (kind, owner_id),
                )
            ).fetchall()
            candidate_digests.update(str(row[0]) for row in rows)
        head_rows = await (
            await db.execute(
                f"""SELECT spec_blob_digest FROM workflow_research_continuation_heads
                WHERE parent_run_id IN ({placeholders}) OR child_run_id IN ({placeholders})""",
                (*run_ids, *run_ids),
            )
        ).fetchall()
        candidate_digests.update(str(row[0]) for row in head_rows)

        # checkpoint/effect/pending/owner refs -> checkpoint/effect records
        for row in checkpoint_rows:
            params = (row["thread_id"], row["checkpoint_ns"], row["checkpoint_id"])
            await db.execute(
                """DELETE FROM workflow_checkpoint_effects
                WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?""",
                params,
            )
            await db.execute(
                """DELETE FROM workflow_pending_writes
                WHERE thread_id=? AND checkpoint_ns=? AND base_checkpoint_id=?""",
                params,
            )
        await db.execute(
            f"DELETE FROM workflow_checkpoint_owners WHERE run_id IN ({placeholders})",
            run_ids,
        )
        for kind, owner_id in owner_pairs:
            if kind in {"research_snapshot", "run_staging"}:
                continue
            await db.execute(
                "DELETE FROM workflow_blob_refs WHERE owner_kind=? AND owner_id=?",
                (kind, owner_id),
            )
        for row in checkpoint_rows:
            params = (row["thread_id"], row["checkpoint_ns"], row["checkpoint_id"])
            await db.execute(
                """DELETE FROM workflow_checkpoints
                WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?
                  AND NOT EXISTS(
                    SELECT 1 FROM workflow_checkpoint_owners owner
                    WHERE owner.thread_id=workflow_checkpoints.thread_id
                      AND owner.checkpoint_ns=workflow_checkpoints.checkpoint_ns
                      AND owner.checkpoint_id=workflow_checkpoints.checkpoint_id
                  )""",
                params,
            )
        if effect_ids:
            effect_placeholders = _placeholders(effect_ids)
            await db.execute(
                f"DELETE FROM workflow_checkpoint_effects WHERE effect_id IN ({effect_placeholders})",
                effect_ids,
            )
            await db.execute(
                f"DELETE FROM workflow_effect_attempt_heads WHERE canonical_effect_id IN ({effect_placeholders}) OR run_id IN ({placeholders})",
                (*effect_ids, *run_ids),
            )
            await db.execute(
                f"DELETE FROM workflow_effect_targets WHERE effect_id IN ({effect_placeholders})",
                effect_ids,
            )
            await db.execute(
                f"DELETE FROM workflow_research_resource_reservations WHERE effect_id IN ({effect_placeholders})",
                effect_ids,
            )
            await db.execute(
                f"DELETE FROM workflow_effect_budget_reservations WHERE effect_id IN ({effect_placeholders})",
                effect_ids,
            )
        if node_ids:
            await db.execute(
                f"DELETE FROM workflow_node_effects WHERE node_execution_id IN ({_placeholders(node_ids)})",
                node_ids,
            )
        await self._delete_v6_component_rows(
            db,
            delivery_ids=delivery_ids,
            evaluation_ids=evaluation_ids,
            event_ids=event_ids,
            placeholders=placeholders,
            run_ids=run_ids,
            snapshots=snapshots,
            trace_ids=trace_ids,
        )

        return await self._delete_v6_component_blobs(candidate_digests, db, placeholders, run_ids)

    async def _delete_v6_component_rows(
        self,
        db: aiosqlite.Connection,
        *,
        delivery_ids,
        evaluation_ids,
        event_ids,
        placeholders,
        run_ids: tuple[str, ...],
        snapshots,
        trace_ids,
    ) -> None:
        await db.execute(f"DELETE FROM workflow_effects WHERE run_id IN ({placeholders})", run_ids)
        await db.execute(f"DELETE FROM workflow_nodes WHERE run_id IN ({placeholders})", run_ids)
        await self._fault("retention.v6.after_checkpoint_data")

        # head -> pins -> descendant-to-root lineage
        await db.execute(
            f"""DELETE FROM workflow_research_continuation_heads
            WHERE parent_run_id IN ({placeholders}) OR child_run_id IN ({placeholders})""",
            (*run_ids, *run_ids),
        )
        await db.execute(
            f"""DELETE FROM workflow_blob_refs
            WHERE owner_kind='run_staging' AND owner_id IN ({placeholders})""",
            run_ids,
        )
        await self._fault("retention.v6.after_heads")
        await db.execute(
            f"DELETE FROM workflow_research_snapshot_pins WHERE run_id IN ({placeholders})",
            run_ids,
        )
        await self._fault("retention.v6.after_pins")
        lineage_rows = await (
            await db.execute(
                f"""SELECT operation_id,parent_operation_id FROM workflow_research_lineage
                WHERE run_id IN ({placeholders})""",
                run_ids,
            )
        ).fetchall()
        by_operation = {str(row["operation_id"]): row for row in lineage_rows}

        def depth(operation_id: str) -> int:
            current = operation_id
            seen: set[str] = set()
            value = 0
            while current in by_operation and current not in seen:
                seen.add(current)
                parent = by_operation[current]["parent_operation_id"]
                if parent is None:
                    break
                current = str(parent)
                value += 1
            return value

        for operation_id in sorted(by_operation, key=lambda item: (-depth(item), item)):
            await db.execute(
                "DELETE FROM workflow_research_lineage WHERE operation_id=?",
                (operation_id,),
            )
        await self._fault("retention.v6.after_lineage")

        # component-wide start/audit operations, requests, and session refs
        await db.execute(
            f"DELETE FROM workflow_operations WHERE run_id IN ({placeholders})", run_ids
        )
        await self._fault("retention.v6.after_operations")
        await db.execute(
            f"DELETE FROM workflow_start_requests WHERE run_id IN ({placeholders})",
            run_ids,
        )
        await self._fault("retention.v6.after_start_requests")
        await db.execute(
            f"DELETE FROM workflow_session_refs WHERE run_id IN ({placeholders})", run_ids
        )
        await self._fault("retention.v6.after_session_refs")

        # Snapshots own the inherited closure.  Release it only after lineage.
        if snapshots:
            snapshot_placeholders = _placeholders(snapshots)
            await db.execute(
                f"""DELETE FROM workflow_blob_refs
                WHERE owner_kind='research_snapshot'
                  AND owner_id IN ({snapshot_placeholders})""",
                snapshots,
            )
            await db.execute(
                f"DELETE FROM workflow_research_snapshots WHERE snapshot_hash IN ({snapshot_placeholders})",
                snapshots,
            )
        await self._fault("retention.v6.after_snapshots")

        # Remove every remaining component-owned non-FK row before run rows.
        if delivery_ids:
            await db.execute(
                f"DELETE FROM workflow_deliveries WHERE delivery_id IN ({_placeholders(delivery_ids)})",
                delivery_ids,
            )
        if event_ids:
            await db.execute(
                f"DELETE FROM workflow_events WHERE event_id IN ({_placeholders(event_ids)})",
                event_ids,
            )
        await db.execute(
            f"DELETE FROM workflow_target_reservations WHERE run_id IN ({placeholders})",
            run_ids,
        )
        await db.execute(
            f"DELETE FROM workflow_receipt_ledger WHERE run_id IN ({placeholders})",
            run_ids,
        )
        await db.execute(
            f"DELETE FROM workflow_fork_requests WHERE source_run_id IN ({placeholders}) OR child_run_id IN ({placeholders})",
            (*run_ids, *run_ids),
        )
        if evaluation_ids:
            evaluation_placeholders = _placeholders(evaluation_ids)
            await db.execute(
                f"DELETE FROM eval_results WHERE evaluation_id IN ({evaluation_placeholders}) OR run_id IN ({placeholders})",
                (*evaluation_ids, *run_ids),
            )
            await db.execute(
                f"DELETE FROM evaluations WHERE evaluation_id IN ({evaluation_placeholders})",
                evaluation_ids,
            )
        else:
            await db.execute(f"DELETE FROM eval_results WHERE run_id IN ({placeholders})", run_ids)
        if trace_ids:
            await db.execute(
                f"DELETE FROM trace_runs WHERE trace_id IN ({_placeholders(trace_ids)})",
                trace_ids,
            )
        await db.execute(
            f"DELETE FROM workflow_decisions WHERE run_id IN ({placeholders})", run_ids
        )
        await db.execute(
            f"DELETE FROM workflow_run_control_commands WHERE run_id IN ({placeholders})",
            run_ids,
        )
        await db.execute(
            f"DELETE FROM workflow_research_deadlines WHERE run_id IN ({placeholders})",
            run_ids,
        )
        await db.execute(
            f"DELETE FROM workflow_research_resource_budgets WHERE run_id IN ({placeholders})",
            run_ids,
        )

    async def _delete_v6_component_blobs(
        self,
        candidate_digests: set[str],
        db: aiosqlite.Connection,
        placeholders,
        run_ids: tuple[str, ...],
    ) -> tuple[Path, ...]:
        paths: list[Path] = []
        for digest in sorted(candidate_digests):
            row = await (
                await db.execute(
                    "SELECT relative_path FROM workflow_blobs WHERE sha256=?",
                    (digest,),
                )
            ).fetchone()
            if row is None:
                continue
            cursor = await db.execute(
                """DELETE FROM workflow_blobs WHERE sha256=?
                  AND NOT EXISTS(SELECT 1 FROM workflow_blob_refs WHERE sha256=?)
                  AND NOT EXISTS(
                    SELECT 1 FROM workflow_research_continuation_heads
                    WHERE spec_blob_digest=?
                  )""",
                (digest, digest, digest),
            )
            if cursor.rowcount:
                path = self._safe_blob_path(str(row["relative_path"]))
                if path is not None:
                    paths.append(path)
        await self._fault("retention.v6.after_blobs")

        # Run rows are last, descendants before the root.
        parent_rows = await (
            await db.execute(
                f"SELECT run_id,parent_run_id FROM workflow_runs WHERE run_id IN ({placeholders})",
                run_ids,
            )
        ).fetchall()
        parent_map = {
            str(row["run_id"]): (
                None if row["parent_run_id"] is None else str(row["parent_run_id"])
            )
            for row in parent_rows
        }

        def run_depth(run_id: str) -> int:
            current = run_id
            seen: set[str] = set()
            value = 0
            while parent_map.get(current) in parent_map and current not in seen:
                seen.add(current)
                current = str(parent_map[current])
                value += 1
            return value

        ordered = sorted(run_ids, key=lambda item: (-run_depth(item), item))
        for run_id in ordered[:-1]:
            await db.execute("DELETE FROM workflow_runs WHERE run_id=?", (run_id,))
        await self._fault("retention.v6.after_children")
        if ordered:
            await db.execute("DELETE FROM workflow_runs WHERE run_id=?", (ordered[-1],))
        await self._fault("retention.v6.after_root")
        return tuple(paths)
