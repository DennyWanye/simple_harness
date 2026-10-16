"""Task contexts, user continuations, goals, plans and run projections."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import aiosqlite

from deskpet.execution.attempt_contracts import PlanVersionRecord, TaskGoalRecord
from deskpet.execution.contracts import (
    TERMINAL_RUN_STATUSES,
    IdempotencyConflict,
    RunNotFound,
    RunStatus,
    TaskGoalStatus,
    TerminalConflict,
    VersionConflict,
    fingerprint_json,
)
from deskpet.execution.delivery_contracts import RunEvent, RunEventCandidate
from deskpet.types.task_work_context import (
    ConversationBoundary,
    QueuedUserContinuation,
    TaskRunProjection,
    TaskWorkContext,
)

from .core import ExecutionUowCore
from .support import ContinuationRecord


class TaskContextMixin(ExecutionUowCore):
    """Task contexts, user continuations, goals, plans and run projections."""

    __slots__ = ()

    async def create_task_context(
        self,
        work_context: TaskWorkContext,
        conversation: ConversationBoundary,
        projection: TaskRunProjection,
    ) -> tuple[TaskWorkContext, ConversationBoundary, TaskRunProjection]:
        """Atomically bind one root to its task-local work and UI identity."""

        common = (
            work_context.session_id,
            work_context.root_run_id,
            work_context.task_scope_id,
        )
        if common != (
            conversation.session_id,
            conversation.root_run_id,
            conversation.task_scope_id,
        ) or common != (
            projection.session_id,
            projection.root_run_id,
            projection.task_scope_id,
        ):
            raise IdempotencyConflict(
                "task_context_identity_mismatch",
                "work, conversation, and projection identities must match",
            )
        if (
            conversation.version != 1
            or conversation.continuation_message_refs
            or projection.version != 0
        ):
            raise IdempotencyConflict(
                "invalid_initial_task_context",
                "new task context must begin at boundary v1/projection v0",
            )

        async with self._write_transaction() as db:
            run = await (
                await db.execute(
                    """SELECT session_id,root_run_id,parent_run_id
                    FROM execution_runs WHERE run_id=?""",
                    (work_context.root_run_id,),
                )
            ).fetchone()
            if (
                run is None
                or str(run["session_id"]) != work_context.session_id
                or str(run["root_run_id"]) != work_context.root_run_id
                or run["parent_run_id"] is not None
            ):
                raise IdempotencyConflict(
                    "task_context_root_conflict",
                    "task context requires the matching persisted root run",
                )

            work_row = await (
                await db.execute(
                    """SELECT * FROM execution_task_work_contexts
                    WHERE root_run_id=? OR task_scope_id=?""",
                    (
                        work_context.root_run_id,
                        work_context.task_scope_id,
                    ),
                )
            ).fetchone()
            if work_row is None:
                now = float(self._clock())
                await db.execute(
                    """INSERT INTO execution_task_work_contexts(
                    root_run_id,schema_version,session_id,task_scope_id,
                    workspace_root,workspace_source,binding_version,
                    created_at,updated_at
                    ) VALUES(?,1,?,?,?,?,?,?,?)""",
                    (
                        work_context.root_run_id,
                        work_context.session_id,
                        work_context.task_scope_id,
                        work_context.workspace_root,
                        work_context.workspace_source,
                        work_context.binding_version,
                        now,
                        now,
                    ),
                )
                work_row = await (
                    await db.execute(
                        """SELECT * FROM execution_task_work_contexts
                        WHERE root_run_id=?""",
                        (work_context.root_run_id,),
                    )
                ).fetchone()
            stored_work = self._row_to_task_work_context(work_row)
            if stored_work != work_context:
                raise IdempotencyConflict(
                    "task_work_context_conflict",
                    "root task workspace identity already differs",
                )

            conversation_row = await (
                await db.execute(
                    """SELECT * FROM execution_conversation_boundaries
                    WHERE boundary_ref=? OR root_run_id=? OR task_scope_id=?""",
                    (
                        conversation.boundary_ref,
                        conversation.root_run_id,
                        conversation.task_scope_id,
                    ),
                )
            ).fetchone()
            if conversation_row is None:
                now = float(self._clock())
                await db.execute(
                    """INSERT INTO execution_conversation_boundaries(
                    boundary_ref,schema_version,session_id,root_run_id,
                    task_scope_id,seed_message_refs_json,
                    continuation_message_refs_json,boundary_version,
                    created_at,updated_at
                    ) VALUES(?,1,?,?,?,?,?,1,?,?)""",
                    (
                        conversation.boundary_ref,
                        conversation.session_id,
                        conversation.root_run_id,
                        conversation.task_scope_id,
                        json.dumps(
                            list(conversation.seed_message_refs),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        "[]",
                        now,
                        now,
                    ),
                )
                conversation_row = await (
                    await db.execute(
                        """SELECT * FROM execution_conversation_boundaries
                        WHERE boundary_ref=?""",
                        (conversation.boundary_ref,),
                    )
                ).fetchone()
            stored_conversation = self._row_to_conversation_boundary(conversation_row)
            if stored_conversation != conversation:
                raise IdempotencyConflict(
                    "conversation_boundary_conflict",
                    "root conversation identity already differs",
                )

            projection_row = await (
                await db.execute(
                    """SELECT * FROM execution_task_run_projections
                    WHERE projection_id=? OR root_run_id=? OR task_scope_id=?""",
                    (
                        projection.projection_id,
                        projection.root_run_id,
                        projection.task_scope_id,
                    ),
                )
            ).fetchone()
            if projection_row is None:
                now = float(self._clock())
                await db.execute(
                    """INSERT INTO execution_task_run_projections(
                    projection_id,schema_version,session_id,root_run_id,
                    task_scope_id,ui_state,projection_version,
                    created_at,updated_at
                    ) VALUES(?,1,?,?,?,?,0,?,?)""",
                    (
                        projection.projection_id,
                        projection.session_id,
                        projection.root_run_id,
                        projection.task_scope_id,
                        projection.ui_state,
                        now,
                        now,
                    ),
                )
                projection_row = await (
                    await db.execute(
                        """SELECT * FROM execution_task_run_projections
                        WHERE projection_id=?""",
                        (projection.projection_id,),
                    )
                ).fetchone()
            stored_projection = self._row_to_task_run_projection(projection_row)
            if stored_projection != projection:
                raise IdempotencyConflict(
                    "task_run_projection_conflict",
                    "root task projection identity already differs",
                )
            return stored_work, stored_conversation, stored_projection

    async def get_task_work_context(self, root_run_id: str) -> TaskWorkContext | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_task_work_contexts
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            return None if row is None else self._row_to_task_work_context(row)

    async def rebind_task_workspace_for_project(
        self,
        root_run_id: str,
        project_root: str,
        *,
        expected_binding_version: int,
    ) -> TaskWorkContext:
        """Fenced one-time user rebind before any write or child execution.

        A fresh task may start in its generated default workspace or inherit the
        Session's previous project.  Creating a different project must be able
        to replace either provisional binding after the native picker confirms
        the new root.
        """

        resolved = str(Path(project_root).expanduser().resolve(strict=False))
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_task_work_contexts
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            if row is None:
                raise IdempotencyConflict(
                    "task_work_context_missing",
                    "project directory selection requires a task context",
                )
            current = self._row_to_task_work_context(row)
            if (
                current.workspace_source == "user_path"
                and current.workspace_root == resolved
                and current.binding_version == expected_binding_version + 1
            ):
                return current
            if (
                current.workspace_source not in {"task_default", "existing"}
                or current.binding_version != expected_binding_version
            ):
                raise IdempotencyConflict(
                    "task_workspace_rebind_conflict",
                    "the task workspace is already bound",
                )
            child = await (
                await db.execute(
                    """SELECT run_id FROM execution_runs
                    WHERE root_run_id=? AND parent_run_id IS NOT NULL
                    LIMIT 1""",
                    (root_run_id,),
                )
            ).fetchone()
            if child is not None:
                raise IdempotencyConflict(
                    "task_workspace_rebind_after_child",
                    "project location must be selected before child work starts",
                )
            write_effect = await (
                await db.execute(
                    """SELECT effect_id FROM execution_effects
                    WHERE run_id=? AND effect_type<>'idempotent_read'
                    LIMIT 1""",
                    (root_run_id,),
                )
            ).fetchone()
            if write_effect is not None:
                raise IdempotencyConflict(
                    "task_workspace_rebind_after_effect",
                    "project location must be selected before project writes",
                )
            run_row = await (
                await db.execute(
                    """SELECT session_id,venue,workspace_json
                    FROM execution_runs WHERE run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            if run_row is None:
                raise IdempotencyConflict(
                    "task_workspace_run_missing",
                    "project directory selection lost its root Run",
                )
            workspace = json.loads(str(run_row["workspace_json"]))
            workspace.update(root=resolved, write_scope_root=resolved)
            workspace["scope_hash"] = fingerprint_json(
                {
                    "session_id": str(run_row["session_id"]),
                    "workspace": resolved,
                    "write_scope_root": resolved,
                    "venue": str(run_row["venue"]),
                }
            )
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_task_work_contexts
                SET workspace_root=?,workspace_source='user_path',
                    binding_version=binding_version+1,updated_at=?
                WHERE root_run_id=?
                  AND workspace_source IN ('task_default','existing')
                  AND binding_version=?""",
                (
                    resolved,
                    now,
                    root_run_id,
                    expected_binding_version,
                ),
            )
            if cursor.rowcount != 1:
                raise IdempotencyConflict(
                    "task_workspace_rebind_conflict",
                    "the task workspace binding changed concurrently",
                )
            await db.execute(
                """UPDATE execution_runs
                SET workspace_json=?,version=version+1,updated_at=?
                WHERE run_id=?""",
                (
                    json.dumps(
                        workspace,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    now,
                    root_run_id,
                ),
            )
            updated = await (
                await db.execute(
                    """SELECT * FROM execution_task_work_contexts
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            return self._row_to_task_work_context(updated)

    async def get_conversation_boundary(self, root_run_id: str) -> ConversationBoundary | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_conversation_boundaries
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            return None if row is None else self._row_to_conversation_boundary(row)

    async def enqueue_user_continuation(
        self,
        root_run_id: str,
        message_ref: str,
        content: str,
        *,
        task_scope_id: str,
        expected_boundary_version: int,
    ) -> tuple[ConversationBoundary, QueuedUserContinuation, bool]:
        """Reserve one root-local message and durable FIFO position atomically."""

        content = str(content)
        if not message_ref or not content.strip():
            raise ValueError("continuation message reference and content are required")
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_conversation_boundaries
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            if row is None:
                raise RunNotFound(
                    "conversation_boundary_not_found",
                    f"conversation boundary does not exist: {root_run_id}",
                )
            current = self._row_to_conversation_boundary(row)
            if current.task_scope_id != task_scope_id:
                raise IdempotencyConflict(
                    "conversation_task_scope_mismatch",
                    "continuation does not target the root task scope",
                )
            queued_row = await (
                await db.execute(
                    """SELECT * FROM execution_user_continuations
                    WHERE root_run_id=? AND message_ref=?""",
                    (root_run_id, message_ref),
                )
            ).fetchone()
            if queued_row is not None:
                queued = self._row_to_user_continuation(queued_row)
                if queued.task_scope_id != task_scope_id or queued.content != content:
                    raise IdempotencyConflict(
                        "user_continuation_replay_conflict",
                        "continuation replay differs from the reserved message",
                    )
                return current, queued, True
            if message_ref in current.seed_message_refs:
                raise IdempotencyConflict(
                    "continuation_message_ref_conflict",
                    "a seed message cannot be replayed as a continuation",
                )
            now = float(self._clock())
            if message_ref in current.continuation_message_refs:
                reserved_version = current.continuation_message_refs.index(message_ref) + 2
                await db.execute(
                    """INSERT INTO execution_user_continuations(
                    root_run_id,message_ref,schema_version,task_scope_id,
                    content,reserved_boundary_version,status,created_at,
                    settled_at,error
                    ) VALUES(?,?,1,?,?,?,'bound',?,?,NULL)""",
                    (
                        root_run_id,
                        message_ref,
                        task_scope_id,
                        content,
                        reserved_version,
                        now,
                        now,
                    ),
                )
                queued_row = await (
                    await db.execute(
                        """SELECT * FROM execution_user_continuations
                        WHERE root_run_id=? AND message_ref=?""",
                        (root_run_id, message_ref),
                    )
                ).fetchone()
                return current, self._row_to_user_continuation(queued_row), True
            if current.version != expected_boundary_version:
                raise VersionConflict(
                    "stale_conversation_boundary",
                    "conversation boundary version changed",
                )
            run = await self._continuation_run_tx(db, root_run_id)
            status = RunStatus(str(run["status"]))
            if status in TERMINAL_RUN_STATUSES or status is RunStatus.CANCEL_REQUESTED:
                raise TerminalConflict(
                    "run_already_terminal",
                    "a terminal or cancelling run cannot accept continuation",
                )
            updated = current.append(
                message_ref,
                expected_version=expected_boundary_version,
            )
            run_cursor = await db.execute(
                """UPDATE execution_runs
                SET version=version+1,updated_at=?
                WHERE run_id=? AND version=? AND status IN (
                    'created','queued','running','waiting'
                )""",
                (
                    now,
                    root_run_id,
                    int(run["version"]),
                ),
            )
            if run_cursor.rowcount != 1:
                raise VersionConflict(
                    "user_continuation_run_fence_conflict",
                    "run changed while reserving the continuation",
                )
            await db.execute(
                """INSERT INTO execution_user_continuations(
                root_run_id,message_ref,schema_version,task_scope_id,content,
                reserved_boundary_version,status,created_at,settled_at,error
                ) VALUES(?,?,1,?,?,?,'pending',?,NULL,NULL)""",
                (
                    root_run_id,
                    message_ref,
                    task_scope_id,
                    content,
                    updated.version,
                    now,
                ),
            )
            cursor = await db.execute(
                """UPDATE execution_conversation_boundaries
                SET continuation_message_refs_json=?,
                    boundary_version=?,updated_at=?
                WHERE root_run_id=? AND boundary_version=?""",
                (
                    json.dumps(
                        list(updated.continuation_message_refs),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    updated.version,
                    now,
                    root_run_id,
                    expected_boundary_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_conversation_boundary",
                    "conversation boundary version changed",
                )
            queued = QueuedUserContinuation(
                root_run_id=root_run_id,
                task_scope_id=task_scope_id,
                message_ref=message_ref,
                content=content,
                reserved_boundary_version=updated.version,
                created_at=now,
            )
            return updated, queued, False

    async def get_next_user_continuation(self, root_run_id: str) -> QueuedUserContinuation | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_user_continuations
                    WHERE root_run_id=? AND status='pending'
                    ORDER BY reserved_boundary_version
                    LIMIT 1""",
                    (root_run_id,),
                )
            ).fetchone()
            return None if row is None else self._row_to_user_continuation(row)

    async def get_user_continuation(
        self, root_run_id: str, message_ref: str
    ) -> QueuedUserContinuation | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_user_continuations
                    WHERE root_run_id=? AND message_ref=?""",
                    (root_run_id, message_ref),
                )
            ).fetchone()
            return None if row is None else self._row_to_user_continuation(row)

    async def fail_user_continuation(
        self, root_run_id: str, message_ref: str, error: str
    ) -> QueuedUserContinuation:
        error = str(error).strip() or "continuation_apply_failed"
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_user_continuations
                    WHERE root_run_id=? AND message_ref=?""",
                    (root_run_id, message_ref),
                )
            ).fetchone()
            if row is None:
                raise RunNotFound(
                    "user_continuation_not_found",
                    f"queued continuation does not exist: {message_ref}",
                )
            current = self._row_to_user_continuation(row)
            if current.status == "failed":
                return current
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_user_continuations
                SET status='failed',settled_at=?,error=?
                WHERE root_run_id=? AND message_ref=?
                    AND status IN ('pending','bound')""",
                (now, error, root_run_id, message_ref),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "user_continuation_failure_conflict",
                    "continuation state changed while recording failure",
                )
            failed = await (
                await db.execute(
                    """SELECT * FROM execution_user_continuations
                    WHERE root_run_id=? AND message_ref=?""",
                    (root_run_id, message_ref),
                )
            ).fetchone()
            return self._row_to_user_continuation(failed)

    async def commit_user_continuation(
        self,
        root_run_id: str,
        message_ref: str,
        *,
        task_scope_id: str,
        expected_boundary_version: int,
        expected_continuation_version: int,
        continuation_payload: Mapping[str, Any],
        event: RunEventCandidate,
    ) -> tuple[ConversationBoundary, ContinuationRecord, RunEvent]:
        """Atomically append one root-local user message and Driver boundary."""

        if not message_ref:
            raise ValueError("continuation message reference is required")
        payload_json = self._continuation_payload_json(continuation_payload)
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_conversation_boundaries
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            if row is None:
                raise RunNotFound(
                    "conversation_boundary_not_found",
                    f"conversation boundary does not exist: {root_run_id}",
                )
            current = self._row_to_conversation_boundary(row)
            if current.task_scope_id != task_scope_id:
                raise IdempotencyConflict(
                    "conversation_task_scope_mismatch",
                    "continuation does not target the root task scope",
                )
            if message_ref in current.seed_message_refs:
                raise IdempotencyConflict(
                    "continuation_message_ref_conflict",
                    "a seed message cannot be replayed as a continuation",
                )
            run = await self._continuation_run_tx(db, root_run_id)
            if message_ref in current.continuation_message_refs:
                saved = await self._load_continuation_tx(db, root_run_id)
                if saved is None:
                    raise IdempotencyConflict(
                        "continuation_record_missing",
                        "root conversation exists without a Driver continuation",
                    )
                stored_event, _, _ = await self._append_event_tx(
                    db,
                    run,
                    expected_version=int(run["version"]),
                    event=event,
                    deliveries=(),
                )
                return current, saved, stored_event
            if current.version != expected_boundary_version:
                raise VersionConflict(
                    "stale_conversation_boundary",
                    "conversation boundary version changed",
                )
            saved, _ = await self._save_continuation_tx(
                db,
                run=run,
                expected_version=expected_continuation_version,
                payload_json=payload_json,
                decision=None,
                now=float(self._clock()),
            )
            updated = current.append(
                message_ref,
                expected_version=expected_boundary_version,
            )
            cursor = await db.execute(
                """UPDATE execution_conversation_boundaries
                SET continuation_message_refs_json=?,
                    boundary_version=?,updated_at=?
                WHERE root_run_id=? AND boundary_version=?""",
                (
                    json.dumps(
                        list(updated.continuation_message_refs),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    updated.version,
                    float(self._clock()),
                    root_run_id,
                    expected_boundary_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_conversation_boundary",
                    "conversation boundary version changed",
                )
            stored_event, _, _ = await self._append_event_tx(
                db,
                run,
                expected_version=int(run["version"]),
                event=event,
                deliveries=(),
            )
            return updated, saved, stored_event

    async def commit_queued_user_continuation(
        self,
        root_run_id: str,
        message_ref: str,
        *,
        task_scope_id: str,
        expected_continuation_version: int,
        continuation_payload: Mapping[str, Any],
        event: RunEventCandidate,
    ) -> tuple[ConversationBoundary, ContinuationRecord, RunEvent]:
        """Apply the oldest reserved message with the Driver boundary atomically."""

        payload_json = self._continuation_payload_json(continuation_payload)
        async with self._write_transaction() as db:
            queue_row = await (
                await db.execute(
                    """SELECT * FROM execution_user_continuations
                    WHERE root_run_id=? AND message_ref=?""",
                    (root_run_id, message_ref),
                )
            ).fetchone()
            if queue_row is None:
                raise RunNotFound(
                    "user_continuation_not_found",
                    f"queued continuation does not exist: {message_ref}",
                )
            queued = self._row_to_user_continuation(queue_row)
            if queued.task_scope_id != task_scope_id:
                raise IdempotencyConflict(
                    "conversation_task_scope_mismatch",
                    "continuation does not target the root task scope",
                )
            conversation_row = await (
                await db.execute(
                    """SELECT * FROM execution_conversation_boundaries
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            if conversation_row is None:
                raise RunNotFound(
                    "conversation_boundary_not_found",
                    f"conversation boundary does not exist: {root_run_id}",
                )
            conversation = self._row_to_conversation_boundary(conversation_row)
            if (
                conversation.task_scope_id != task_scope_id
                or message_ref not in conversation.continuation_message_refs
            ):
                raise IdempotencyConflict(
                    "queued_conversation_boundary_mismatch",
                    "queued continuation is outside the root conversation",
                )
            run = await self._continuation_run_tx(db, root_run_id)
            if queued.status == "bound":
                saved = await self._load_continuation_tx(db, root_run_id)
                if saved is None:
                    raise IdempotencyConflict(
                        "continuation_record_missing",
                        "applied continuation has no Driver boundary",
                    )
                stored_event, _, _ = await self._append_event_tx(
                    db,
                    run,
                    expected_version=int(run["version"]),
                    event=event,
                    deliveries=(),
                )
                return conversation, saved, stored_event
            if queued.status == "failed":
                raise TerminalConflict(
                    "user_continuation_failed",
                    "failed continuation cannot be rebound",
                )
            oldest = await (
                await db.execute(
                    """SELECT message_ref FROM execution_user_continuations
                    WHERE root_run_id=? AND status='pending'
                    ORDER BY reserved_boundary_version
                    LIMIT 1""",
                    (root_run_id,),
                )
            ).fetchone()
            if oldest is None or str(oldest["message_ref"]) != message_ref:
                raise IdempotencyConflict(
                    "user_continuation_order_conflict",
                    "queued continuations must be applied in FIFO order",
                )
            saved, _ = await self._save_continuation_tx(
                db,
                run=run,
                expected_version=expected_continuation_version,
                payload_json=payload_json,
                decision=None,
                now=float(self._clock()),
            )
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_user_continuations
                SET status='bound',settled_at=?
                WHERE root_run_id=? AND message_ref=? AND status='pending'""",
                (now, root_run_id, message_ref),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "user_continuation_claim_conflict",
                    "queued continuation was already applied",
                )
            stored_event, _, _ = await self._append_event_tx(
                db,
                run,
                expected_version=int(run["version"]),
                event=event,
                deliveries=(),
            )
            return conversation, saved, stored_event

    async def get_task_run_projection(self, root_run_id: str) -> TaskRunProjection | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_task_run_projections
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            return None if row is None else self._row_to_task_run_projection(row)

    async def list_task_run_projections(
        self,
        session_id: str,
        *,
        include_closed: bool = False,
    ) -> tuple[TaskRunProjection, ...]:
        where = "session_id=?" if include_closed else ("session_id=? AND ui_state<>'closed'")
        async with self._read_connection() as db:
            rows = await (
                await db.execute(
                    f"""SELECT * FROM execution_task_run_projections
                    WHERE {where}
                    ORDER BY updated_at DESC,projection_id ASC""",
                    (session_id,),
                )
            ).fetchall()
            return tuple(self._row_to_task_run_projection(row) for row in rows)

    async def get_task_run_lifecycle(
        self,
        root_run_id: str,
        *,
        expected_session_id: str,
    ) -> Mapping[str, Any] | None:
        """Return the durable lifecycle used to restore a task's UI projection.

        The session fence is part of the query so projection hydration cannot
        disclose or attach lifecycle state from another main session.
        """

        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT status,started_at,updated_at,ended_at
                    FROM execution_runs
                    WHERE run_id=? AND root_run_id=run_id AND session_id=?""",
                    (root_run_id, expected_session_id),
                )
            ).fetchone()
            if row is None:
                return None
            return {
                "status": str(row["status"]),
                "started_at": (None if row["started_at"] is None else float(row["started_at"])),
                "updated_at": float(row["updated_at"]),
                "ended_at": (None if row["ended_at"] is None else float(row["ended_at"])),
            }

    async def get_latest_session_project_context(
        self,
        session_id: str,
    ) -> Mapping[str, str | None]:
        """Return the Session's latest durable user-selected project root.

        A later root without a workspace must not erase the project selected by
        an earlier ``project_directory_select`` decision in the same Session.
        Legacy Runs that predate task work contexts still fall back to their
        latest persisted Run workspace.
        """

        session_key = str(session_id or "").strip()
        if not session_key:
            return {"project_name": None, "project_root": None}
        async with self._read_connection() as db:
            selected = await (
                await db.execute(
                    """SELECT workspace_root
                    FROM execution_task_work_contexts
                    WHERE session_id=? AND workspace_source='user_path'
                      AND workspace_root IS NOT NULL
                      AND TRIM(workspace_root)<>''
                    ORDER BY updated_at DESC,created_at DESC,root_run_id DESC
                    LIMIT 1""",
                    (session_key,),
                )
            ).fetchone()
            if selected is not None:
                root = str(selected["workspace_root"] or "").strip()
                name = root.rstrip("\\/").replace("\\", "/").rsplit("/", 1)[-1]
                return {
                    "project_name": name or None,
                    "project_root": root or None,
                }
            row = await (
                await db.execute(
                    """SELECT s.run_context_json,r.workspace_json
                    FROM execution_runs AS r
                    LEFT JOIN execution_run_start_snapshots AS s
                      ON s.run_id=r.run_id
                    WHERE r.session_id=? AND r.root_run_id=r.run_id
                    ORDER BY r.created_at DESC,r.run_id DESC
                    LIMIT 1""",
                    (session_key,),
                )
            ).fetchone()
        if row is None:
            return {"project_name": None, "project_root": None}
        try:
            context = (
                {} if row["run_context_json"] is None else json.loads(str(row["run_context_json"]))
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            context = {}
        workspace = context.get("workspace", {}) if isinstance(context, Mapping) else {}
        root = (
            str(workspace.get("root") or workspace.get("write_scope_root") or "")
            if isinstance(workspace, Mapping)
            else ""
        ).strip()
        if not root:
            try:
                run_workspace = json.loads(str(row["workspace_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                run_workspace = {}
            if isinstance(run_workspace, Mapping):
                root = str(
                    run_workspace.get("root") or run_workspace.get("write_scope_root") or ""
                ).strip()
        name = root.rstrip("\\/").replace("\\", "/").rsplit("/", 1)[-1]
        return {
            "project_name": name or None,
            "project_root": root or None,
        }

    async def set_task_run_projection_state(
        self,
        root_run_id: str,
        ui_state: str,
        *,
        expected_version: int,
    ) -> TaskRunProjection:
        if ui_state not in {"open", "background", "closed"}:
            raise ValueError(f"unsupported projection state: {ui_state}")
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_task_run_projections
                    WHERE root_run_id=?""",
                    (root_run_id,),
                )
            ).fetchone()
            if row is None:
                raise RunNotFound(
                    "task_run_projection_not_found",
                    f"task run projection does not exist: {root_run_id}",
                )
            current = self._row_to_task_run_projection(row)
            if current.ui_state == ui_state:
                return current
            if current.version != expected_version:
                raise VersionConflict(
                    "stale_task_run_projection",
                    "task run projection version changed",
                )
            next_version = current.version + 1
            cursor = await db.execute(
                """UPDATE execution_task_run_projections
                SET ui_state=?,projection_version=?,updated_at=?
                WHERE root_run_id=? AND projection_version=?""",
                (
                    ui_state,
                    next_version,
                    float(self._clock()),
                    root_run_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "stale_task_run_projection",
                    "task run projection version changed",
                )
            return replace(
                current,
                ui_state=ui_state,
                version=next_version,
            )

    async def create_task_goal(
        self,
        goal: TaskGoalRecord,
        initial_plan: PlanVersionRecord,
    ) -> tuple[TaskGoalRecord, PlanVersionRecord]:
        """Create the stable root goal and plan v1 in one idempotent transaction."""

        if (
            goal.status is not TaskGoalStatus.ACTIVE
            or goal.version != 0
            or goal.ended_at is not None
        ):
            raise IdempotencyConflict(
                "invalid_initial_goal", "a new task goal must start active at version zero"
            )
        if (
            initial_plan.root_run_id != goal.root_run_id
            or initial_plan.plan_version != 1
            or initial_plan.trigger_failure_set_id is not None
        ):
            raise IdempotencyConflict(
                "invalid_initial_plan", "a task goal must begin with plan version one"
            )
        async with self._write_transaction() as db:
            run = await (
                await db.execute(
                    "SELECT root_run_id,parent_run_id FROM execution_runs WHERE run_id=?",
                    (goal.root_run_id,),
                )
            ).fetchone()
            if (
                run is None
                or str(run["root_run_id"]) != goal.root_run_id
                or run["parent_run_id"] is not None
            ):
                raise IdempotencyConflict(
                    "task_goal_root_conflict",
                    "task goals can only bind an existing root execution run",
                )
            existing_row = await (
                await db.execute(
                    """SELECT * FROM execution_task_goals
                    WHERE goal_id=? OR root_run_id=? OR task_scope_id=?""",
                    (goal.goal_id, goal.root_run_id, goal.task_scope_id),
                )
            ).fetchone()
            if existing_row is not None:
                existing = self._row_to_task_goal(existing_row)
                if not self._same_goal_intent(existing, goal):
                    raise IdempotencyConflict(
                        "task_goal_conflict", "task goal identity already names another intent"
                    )
                plan_row = await (
                    await db.execute(
                        """SELECT * FROM execution_plan_versions
                        WHERE root_run_id=? AND plan_version=1""",
                        (goal.root_run_id,),
                    )
                ).fetchone()
                if plan_row is None:
                    raise IdempotencyConflict(
                        "task_goal_partial", "task goal exists without its initial plan"
                    )
                return existing, self._row_to_plan_version(plan_row)
            now = float(self._clock())
            await db.execute(
                """INSERT INTO execution_task_goals(
                goal_id,schema_version,root_run_id,task_scope_id,objective_ref,
                status,goal_version,created_at,updated_at,ended_at
                ) VALUES(?,?,?,?,?,'active',0,?,?,NULL)""",
                (
                    goal.goal_id,
                    goal.schema_version,
                    goal.root_run_id,
                    goal.task_scope_id,
                    goal.objective_ref,
                    now,
                    now,
                ),
            )
            await db.execute(
                """INSERT INTO execution_plan_versions(
                root_run_id,plan_version,trigger_failure_set_id,created_at
                ) VALUES(?,1,NULL,?)""",
                (goal.root_run_id, now),
            )
            self._fault("task_goal_before_commit")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_task_goals WHERE goal_id=?",
                    (goal.goal_id,),
                )
            ).fetchone()
            plan_row = await (
                await db.execute(
                    """SELECT * FROM execution_plan_versions
                    WHERE root_run_id=? AND plan_version=1""",
                    (goal.root_run_id,),
                )
            ).fetchone()
            assert row is not None and plan_row is not None
            return self._row_to_task_goal(row), self._row_to_plan_version(plan_row)

    async def get_task_goal(self, root_run_id: str) -> TaskGoalRecord | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM execution_task_goals WHERE root_run_id=?",
                    (root_run_id,),
                )
            ).fetchone()
            return None if row is None else self._row_to_task_goal(row)

    async def append_plan_version(self, plan: PlanVersionRecord) -> PlanVersionRecord:
        """Append one monotonic plan version after its failure trigger is durable."""

        async with self._write_transaction() as db:
            existing = await (
                await db.execute(
                    """SELECT * FROM execution_plan_versions
                    WHERE root_run_id=? AND plan_version=?""",
                    (plan.root_run_id, plan.plan_version),
                )
            ).fetchone()
            if existing is not None:
                record = self._row_to_plan_version(existing)
                if record.trigger_failure_set_id != plan.trigger_failure_set_id:
                    raise IdempotencyConflict(
                        "plan_version_conflict", "plan version trigger differs"
                    )
                return record
            goal = await (
                await db.execute(
                    "SELECT status FROM execution_task_goals WHERE root_run_id=?",
                    (plan.root_run_id,),
                )
            ).fetchone()
            if goal is None or str(goal["status"]) not in {
                TaskGoalStatus.ACTIVE.value,
                TaskGoalStatus.WAITING_EXTERNAL.value,
            }:
                raise IdempotencyConflict(
                    "plan_goal_not_active", "new plans require a nonterminal task goal"
                )
            if plan.plan_version > 1:
                if plan.trigger_failure_set_id is None:
                    raise IdempotencyConflict(
                        "plan_trigger_required", "replans require a failure set trigger"
                    )
                trigger = await (
                    await db.execute(
                        """SELECT 1 FROM execution_attempt_failure_sets
                        WHERE failure_set_id=? AND root_run_id=?""",
                        (plan.trigger_failure_set_id, plan.root_run_id),
                    )
                ).fetchone()
                if trigger is None:
                    raise IdempotencyConflict(
                        "plan_trigger_missing", "replan failure set is not durable"
                    )
            now = float(self._clock())
            try:
                await db.execute(
                    """INSERT INTO execution_plan_versions(
                    root_run_id,plan_version,trigger_failure_set_id,created_at
                    ) VALUES(?,?,?,?)""",
                    (
                        plan.root_run_id,
                        plan.plan_version,
                        plan.trigger_failure_set_id,
                        now,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                raise IdempotencyConflict(
                    "plan_version_not_monotonic",
                    "plan versions must be appended without gaps",
                ) from exc
            row = await (
                await db.execute(
                    """SELECT * FROM execution_plan_versions
                    WHERE root_run_id=? AND plan_version=?""",
                    (plan.root_run_id, plan.plan_version),
                )
            ).fetchone()
            assert row is not None
            return self._row_to_plan_version(row)

    async def cancel_task_goal(self, root_run_id: str, *, expected_version: int) -> TaskGoalRecord:
        """CAS the goal cancelled; ticket recovery performs scoped cancellation."""

        async with self._write_transaction() as db:
            now = float(self._clock())
            cursor = await db.execute(
                """UPDATE execution_task_goals
                SET status='cancelled',goal_version=goal_version+1,
                    updated_at=?,ended_at=?
                WHERE root_run_id=? AND goal_version=?
                  AND status IN ('active','waiting_external')""",
                (now, now, root_run_id, expected_version),
            )
            if cursor.rowcount != 1:
                row = await (
                    await db.execute(
                        "SELECT * FROM execution_task_goals WHERE root_run_id=?",
                        (root_run_id,),
                    )
                ).fetchone()
                if row is not None and str(row["status"]) == TaskGoalStatus.CANCELLED.value:
                    return self._row_to_task_goal(row)
                raise VersionConflict(
                    "task_goal_cas_conflict",
                    "task goal cancellation compare-and-set lost",
                )
            row = await (
                await db.execute(
                    "SELECT * FROM execution_task_goals WHERE root_run_id=?",
                    (root_run_id,),
                )
            ).fetchone()
            assert row is not None
            return self._row_to_task_goal(row)
