"""Profile launch tickets."""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from typing import Any

import aiosqlite

from deskpet.execution.contracts import (
    AttemptStatus,
    ChildCommandIntent,
    ChildCommandRecord,
    IdempotencyConflict,
    LinkKind,
    ProfileLaunchConsumeResult,
    ProfileLaunchTicket,
    ProfileLaunchTicketState,
    ProviderActionAdmissionState,
    RecoveryLease,
    RunNotFound,
    RunStartSnapshotRecord,
    RunStatus,
    TaskGoalStatus,
    TerminalConflict,
    VersionConflict,
    canonical_json,
    fingerprint_json,
    thaw_json,
)

from .core import ExecutionUowCore


class ProfileTicketMixin(ExecutionUowCore):
    """Profile launch tickets."""

    __slots__ = ()

    async def issue_profile_launch_ticket(self, ticket: ProfileLaunchTicket) -> ProfileLaunchTicket:
        """Issue one immutable launch authorization for a provider spawn call."""

        if (
            ticket.state is not ProfileLaunchTicketState.ISSUED
            or ticket.version != 0
            or ticket.child_command_id is not None
            or ticket.child_run_id is not None
        ):
            raise IdempotencyConflict(
                "invalid_profile_ticket", "new launch tickets must start issued"
            )
        async with self._write_transaction() as db:
            personal_selection_id = ticket.personal_selection_id
            personal_selection_fingerprint = ticket.personal_selection_fingerprint
            if ticket.profile_key == "workflow.personal_v1":
                if personal_selection_id is None or personal_selection_fingerprint is None:
                    raise IdempotencyConflict(
                        "personal_selection_required",
                        "personal workflow tickets require one frozen selection",
                    )
                consumed_selection = await (
                    await db.execute(
                        """SELECT * FROM execution_profile_launch_tickets
                        WHERE parent_run_id=? AND personal_selection_id=?""",
                        (ticket.parent_run_id, personal_selection_id),
                    )
                ).fetchone()
                if consumed_selection is not None and (
                    str(consumed_selection["ticket_ref"]) != ticket.ticket_ref
                    or str(consumed_selection["spawn_call_id"]) != ticket.spawn_call_id
                ):
                    raise IdempotencyConflict(
                        "selection_already_consumed",
                        "personal workflow selection already owns a child launch",
                    )
            elif personal_selection_id is not None or personal_selection_fingerprint is not None:
                raise IdempotencyConflict(
                    "personal_selection_profile_mismatch",
                    "only workflow.personal_v1 may bind a personal selection",
                )
            existing_row = await (
                await db.execute(
                    """SELECT * FROM execution_profile_launch_tickets
                    WHERE ticket_ref=?
                       OR (parent_run_id=? AND spawn_call_id=?)""",
                    (
                        ticket.ticket_ref,
                        ticket.parent_run_id,
                        ticket.spawn_call_id,
                    ),
                )
            ).fetchone()
            if existing_row is not None:
                existing = self._row_to_profile_ticket(existing_row)
                if not self._same_ticket_intent(existing, ticket):
                    raise IdempotencyConflict(
                        "profile_ticket_conflict",
                        "parent spawn call already names another launch intent",
                    )
                return existing
            parent = await (
                await db.execute(
                    """SELECT root_run_id,status FROM execution_runs
                    WHERE run_id=?""",
                    (ticket.parent_run_id,),
                )
            ).fetchone()
            goal = await (
                await db.execute(
                    """SELECT task_scope_id,status FROM execution_task_goals
                    WHERE root_run_id=?""",
                    (ticket.root_run_id,),
                )
            ).fetchone()
            attempt = await (
                await db.execute(
                    """SELECT run_id,root_run_id,provider_turn_id,
                    provider_batch_id,status FROM execution_attempt_records
                    WHERE attempt_id=?""",
                    (ticket.attempt_id,),
                )
            ).fetchone()
            call = await (
                await db.execute(
                    """SELECT admission_state FROM execution_provider_action_calls
                    WHERE root_run_id=? AND provider_batch_id=?
                      AND provider_call_id=?""",
                    (
                        ticket.root_run_id,
                        str(attempt["provider_batch_id"]) if attempt is not None else "",
                        ticket.spawn_call_id,
                    ),
                )
            ).fetchone()
            if (
                parent is None
                or goal is None
                or attempt is None
                or call is None
                or str(parent["root_run_id"]) != ticket.root_run_id
                or str(parent["status"])
                in {
                    RunStatus.COMPLETED.value,
                    RunStatus.FAILED.value,
                    RunStatus.CANCELLED.value,
                }
                or str(goal["task_scope_id"]) != ticket.task_scope_id
                or str(goal["status"])
                not in {
                    TaskGoalStatus.ACTIVE.value,
                    TaskGoalStatus.WAITING_EXTERNAL.value,
                }
                or str(attempt["run_id"]) != ticket.parent_run_id
                or str(attempt["root_run_id"]) != ticket.root_run_id
                or str(attempt["provider_turn_id"]) != ticket.provider_turn_id
                or str(attempt["status"])
                not in {
                    AttemptStatus.RUNNING.value,
                    AttemptStatus.WAITING_EXTERNAL.value,
                }
                or str(call["admission_state"])
                not in {
                    ProviderActionAdmissionState.ADMITTED.value,
                    ProviderActionAdmissionState.PREPARED.value,
                    ProviderActionAdmissionState.WAITING_EXTERNAL.value,
                }
            ):
                raise IdempotencyConflict(
                    "profile_ticket_scope_conflict",
                    "launch ticket must bind the live parent attempt and spawn call",
                )
            if ticket.trigger_failure_set_id is not None:
                trigger = await (
                    await db.execute(
                        """SELECT 1 FROM execution_attempt_failure_sets
                        WHERE failure_set_id=? AND root_run_id=?""",
                        (ticket.trigger_failure_set_id, ticket.root_run_id),
                    )
                ).fetchone()
                if trigger is None:
                    raise IdempotencyConflict(
                        "profile_ticket_trigger_missing",
                        "ticket trigger failure set is not durable",
                    )
            now = float(self._clock())
            try:
                await db.execute(
                    """INSERT INTO execution_profile_launch_tickets(
                    ticket_ref,schema_version,parent_run_id,root_run_id,
                    task_scope_id,attempt_id,provider_turn_id,profile_key,
                    driver_kind,profile_catalog_generation,
                    capability_snapshot_ref,task_grant_ref,spawn_call_id,
                    personal_selection_id,personal_selection_fingerprint,
                    trigger_failure_set_id,request_fingerprint,state,
                    child_command_id,child_run_id,ticket_version,created_at,
                    updated_at,consumed_at,cancelled_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'issued',
                    NULL,NULL,0,?,?,NULL,NULL)""",
                    (
                        ticket.ticket_ref,
                        ticket.schema_version,
                        ticket.parent_run_id,
                        ticket.root_run_id,
                        ticket.task_scope_id,
                        ticket.attempt_id,
                        ticket.provider_turn_id,
                        ticket.profile_key,
                        ticket.driver_kind,
                        ticket.profile_catalog_generation,
                        ticket.capability_snapshot_ref,
                        ticket.task_grant_ref,
                        ticket.spawn_call_id,
                        personal_selection_id,
                        personal_selection_fingerprint,
                        ticket.trigger_failure_set_id,
                        ticket.request_fingerprint,
                        now,
                        now,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                if personal_selection_id is not None:
                    raise IdempotencyConflict(
                        "selection_already_consumed",
                        "personal workflow selection already owns a child launch",
                    ) from exc
                raise IdempotencyConflict(
                    "profile_ticket_unique_conflict",
                    "parent spawn call already owns a launch ticket",
                ) from exc
            self._fault("profile_ticket_issue_after_insert")
            self._fault("profile_ticket_issue_before_commit")
            row = await (
                await db.execute(
                    """SELECT * FROM execution_profile_launch_tickets
                    WHERE ticket_ref=?""",
                    (ticket.ticket_ref,),
                )
            ).fetchone()
            assert row is not None
            return self._row_to_profile_ticket(row)

    async def get_profile_launch_ticket(self, ticket_ref: str) -> ProfileLaunchTicket | None:
        async with self._read_connection() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_profile_launch_tickets
                    WHERE ticket_ref=?""",
                    (ticket_ref,),
                )
            ).fetchone()
            return None if row is None else self._row_to_profile_ticket(row)

    async def claim_profile_launch_and_commit_child(
        self,
        ticket_ref: str,
        intent: ChildCommandIntent,
        launch_request: Mapping[str, Any],
        *,
        expected_ticket_version: int,
        start_snapshot: RunStartSnapshotRecord | None = None,
        start_commit_extensions: Sequence[Any] = (),
        recovery_lease: RecoveryLease | None = None,
        transaction_hook: Any | None = None,
    ) -> ChildCommandRecord:
        """Consume a profile ticket and create its child command/link atomically."""

        frozen_request = thaw_json(dict(launch_request))
        assert isinstance(frozen_request, dict)
        async with self._write_transaction() as db:
            await self._assert_optional_recovery_fence_tx(db, recovery_lease, intent.parent_run_id)
            ticket_row = await (
                await db.execute(
                    """SELECT * FROM execution_profile_launch_tickets
                    WHERE ticket_ref=?""",
                    (ticket_ref,),
                )
            ).fetchone()
            if ticket_row is None:
                raise IdempotencyConflict(
                    "profile_ticket_missing",
                    "profile launch ticket does not exist",
                )
            ticket = self._row_to_profile_ticket(ticket_row)
            if fingerprint_json(frozen_request) != ticket.request_fingerprint:
                raise IdempotencyConflict(
                    "profile_ticket_fingerprint_conflict",
                    "launch request fingerprint differs from the issued ticket",
                )
            expected_launch = {
                "profile_key": ticket.profile_key,
                "parent_run_id": ticket.parent_run_id,
                "root_run_id": ticket.root_run_id,
                "task_scope_id": ticket.task_scope_id,
                "trigger_failure_set_id": ticket.trigger_failure_set_id,
                "profile_catalog_generation": ticket.profile_catalog_generation,
            }
            if any(
                frozen_request.get(field_name) != expected
                for field_name, expected in expected_launch.items()
            ):
                raise IdempotencyConflict(
                    "profile_ticket_request_binding_conflict",
                    "launch request identity differs from the durable ticket",
                )
            if (
                intent.parent_run_id != ticket.parent_run_id
                or intent.command_id != ticket.child_command_id
                and ticket.child_command_id is not None
                or intent.child_run_id != ticket.child_run_id
                and ticket.child_run_id is not None
                or intent.child_spec.context.root_run_id != ticket.root_run_id
                or intent.child_spec.profile_key != ticket.profile_key
                or intent.child_spec.driver_kind != ticket.driver_kind
                or intent.capability_snapshot_ref != ticket.capability_snapshot_ref
            ):
                raise IdempotencyConflict(
                    "profile_ticket_child_binding_conflict",
                    "child command differs from the durable profile ticket",
                )
            if ticket.profile_key == "workflow.personal_v1":
                selection = intent.child_request.get("personal_workflow_selection")
                if not isinstance(selection, Mapping) or (
                    str(selection.get("selection_id") or "") != str(ticket.personal_selection_id)
                    or str(selection.get("selection_fingerprint") or "")
                    != str(ticket.personal_selection_fingerprint)
                ):
                    raise IdempotencyConflict(
                        "personal_selection_child_binding_conflict",
                        "child start does not contain the ticket's frozen selection",
                    )

            parent = await (
                await db.execute(
                    "SELECT * FROM execution_runs WHERE run_id=?",
                    (ticket.parent_run_id,),
                )
            ).fetchone()
            if parent is None:
                raise RunNotFound("parent_not_found", "delegate parent must already be durable")
            existing_command = await (
                await db.execute(
                    """SELECT * FROM execution_child_commands
                    WHERE operation_id=? OR (parent_run_id=? AND command_id=?)
                    OR child_run_id=? ORDER BY operation_id=? DESC LIMIT 1""",
                    (
                        intent.operation_id,
                        intent.parent_run_id,
                        intent.command_id,
                        intent.child_run_id,
                        intent.operation_id,
                    ),
                )
            ).fetchone()
            if existing_command is not None and (
                str(existing_command["operation_id"]) != intent.operation_id
                or str(existing_command["intent_fingerprint"]) != intent.intent_fingerprint
            ):
                raise IdempotencyConflict(
                    "child_operation_conflict",
                    "operation, parent command, or child id names another intent",
                )

            if ticket.state is ProfileLaunchTicketState.CANCELLED:
                raise TerminalConflict(
                    "profile_ticket_cancelled",
                    "cancelled profile launch ticket cannot create a child",
                )
            if ticket.state is ProfileLaunchTicketState.ISSUED:
                goal = await (
                    await db.execute(
                        "SELECT status FROM execution_task_goals WHERE root_run_id=?",
                        (ticket.root_run_id,),
                    )
                ).fetchone()
                attempt = await (
                    await db.execute(
                        "SELECT status FROM execution_attempt_records WHERE attempt_id=?",
                        (ticket.attempt_id,),
                    )
                ).fetchone()
                call = await (
                    await db.execute(
                        """SELECT admission_state
                        FROM execution_provider_action_calls
                        WHERE root_run_id=? AND provider_call_id=?""",
                        (ticket.root_run_id, ticket.spawn_call_id),
                    )
                ).fetchone()
                if (
                    goal is None
                    or attempt is None
                    or call is None
                    or str(parent["status"])
                    in {
                        RunStatus.COMPLETED.value,
                        RunStatus.FAILED.value,
                        RunStatus.CANCELLED.value,
                    }
                    or str(goal["status"])
                    not in {
                        TaskGoalStatus.ACTIVE.value,
                        TaskGoalStatus.WAITING_EXTERNAL.value,
                    }
                    or str(attempt["status"])
                    not in {
                        AttemptStatus.RUNNING.value,
                        AttemptStatus.WAITING_EXTERNAL.value,
                    }
                    or str(call["admission_state"])
                    not in {
                        ProviderActionAdmissionState.ADMITTED.value,
                        ProviderActionAdmissionState.PREPARED.value,
                        ProviderActionAdmissionState.WAITING_EXTERNAL.value,
                    }
                ):
                    raise TerminalConflict(
                        "profile_ticket_parent_terminal",
                        "launch ticket parent boundary is no longer live",
                    )
                now = float(self._clock())
                try:
                    cursor = await db.execute(
                        """UPDATE execution_profile_launch_tickets
                        SET state='consumed',child_command_id=?,child_run_id=?,
                            ticket_version=ticket_version+1,updated_at=?,
                            consumed_at=?
                        WHERE ticket_ref=? AND state='issued'
                          AND ticket_version=?""",
                        (
                            intent.command_id,
                            intent.child_run_id,
                            now,
                            now,
                            ticket_ref,
                            expected_ticket_version,
                        ),
                    )
                except aiosqlite.IntegrityError as exc:
                    raise IdempotencyConflict(
                        "profile_ticket_child_unique_conflict",
                        "child identity is already bound to another launch ticket",
                    ) from exc
                if cursor.rowcount != 1:
                    raise VersionConflict(
                        "profile_ticket_cas_conflict",
                        "launch ticket consume compare-and-set lost",
                    )
                self._fault("profile_ticket_claim_after_ticket_cas")
            elif (
                ticket.child_command_id,
                ticket.child_run_id,
            ) != (intent.command_id, intent.child_run_id):
                raise IdempotencyConflict(
                    "profile_ticket_child_conflict",
                    "launch ticket already names another child",
                )

            expected_owner = (
                await self._required_recovery_owner_tx(db)
                if recovery_lease is not None
                else await self._required_start_owner_tx(db)
            )
            self._assert_run_owner(parent, expected_owner)
            if existing_command is None:
                now = float(self._clock())
                await db.execute(
                    """INSERT INTO execution_child_commands(
                    operation_id,schema_version,parent_run_id,command_id,
                    child_run_id,profile_key,join_policy,
                    capability_snapshot_ref,capability_subset_json,
                    child_request_json,child_spec_json,intent_fingerprint,
                    status,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'pending',?,?)""",
                    (
                        intent.operation_id,
                        intent.schema_version,
                        intent.parent_run_id,
                        intent.command_id,
                        intent.child_run_id,
                        intent.child_spec.profile_key,
                        intent.attachment_policy.value,
                        intent.capability_snapshot_ref,
                        canonical_json(list(intent.capability_subset)),
                        canonical_json(thaw_json(intent.child_request)),
                        canonical_json(intent.child_spec.to_dict()),
                        intent.intent_fingerprint,
                        now,
                        now,
                    ),
                )
            self._fault("profile_ticket_claim_after_child_command")

            child, child_created = await self._insert_run_for_owner_tx(
                db, intent.child_spec, version=0, expected_owner=expected_owner
            )
            link_id = self._stable_child_link_id(intent.operation_id)
            existing_link = await (
                await db.execute(
                    "SELECT * FROM execution_run_links WHERE link_id=?",
                    (link_id,),
                )
            ).fetchone()
            expected_link = {
                "root_run_id": intent.child_spec.context.root_run_id,
                "parent_run_id": intent.parent_run_id,
                "child_run_id": intent.child_run_id,
                "attachment_policy": intent.attachment_policy.value,
                "link_kind": LinkKind.STRUCTURAL.value,
                "domain_kind": "",
                "domain_id": "",
            }
            if existing_link is None:
                await db.execute(
                    """INSERT INTO execution_run_links(
                    link_id,schema_version,root_run_id,parent_run_id,
                    child_run_id,attachment_policy,link_kind,domain_kind,
                    domain_id,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        link_id,
                        1,
                        expected_link["root_run_id"],
                        expected_link["parent_run_id"],
                        expected_link["child_run_id"],
                        expected_link["attachment_policy"],
                        expected_link["link_kind"],
                        "",
                        "",
                        self._clock(),
                    ),
                )
                cursor = await db.execute(
                    """UPDATE execution_runs SET version=version+1,updated_at=?
                    WHERE run_id=? AND version=? AND terminal_event_id IS NULL""",
                    (self._clock(), intent.child_run_id, int(child["version"])),
                )
                if cursor.rowcount != 1:
                    raise VersionConflict(
                        "stale_run_version",
                        "child changed before launch link commit",
                    )
            elif any(
                str(existing_link[field_name]) != value
                for field_name, value in expected_link.items()
            ):
                raise IdempotencyConflict(
                    "child_link_conflict",
                    "profile launch child link differs from the durable ticket",
                )
            elif child_created:
                raise IdempotencyConflict(
                    "child_link_without_run",
                    "profile launch link existed before its child run",
                )
            self._fault("profile_ticket_claim_after_child_link")
            if start_snapshot is not None:
                if start_snapshot.run_id != intent.child_run_id:
                    raise IdempotencyConflict(
                        "child_start_scope_conflict",
                        "child start snapshot is bound to another Run",
                    )
                await self._ensure_start_snapshot_tx(
                    db,
                    spec=intent.child_spec,
                    start_snapshot=start_snapshot,
                    run_created=child_created,
                    start_commit_extensions=start_commit_extensions,
                )
                self._fault("profile_ticket_claim_after_child_start_snapshot")
            if transaction_hook is not None:
                hooked = transaction_hook(db, intent)
                if inspect.isawaitable(hooked):
                    await hooked
            self._fault("profile_ticket_claim_before_commit")
            row = await (
                await db.execute(
                    "SELECT * FROM execution_child_commands WHERE operation_id=?",
                    (intent.operation_id,),
                )
            ).fetchone()
            assert row is not None
            return self._row_to_child_command(row)

    async def consume_profile_launch_ticket(
        self,
        ticket_ref: str,
        *,
        request_fingerprint: str,
        child_command_id: str,
        child_run_id: str,
        expected_version: int,
    ) -> ProfileLaunchConsumeResult:
        """One-shot CAS consume; exact retries return the original child identity."""

        if not child_command_id.strip() or not child_run_id.strip():
            raise ValueError("child command and run identities are required")
        async with self._write_transaction() as db:
            row = await (
                await db.execute(
                    """SELECT * FROM execution_profile_launch_tickets
                    WHERE ticket_ref=?""",
                    (ticket_ref,),
                )
            ).fetchone()
            if row is None:
                raise IdempotencyConflict(
                    "profile_ticket_missing", "profile launch ticket does not exist"
                )
            ticket = self._row_to_profile_ticket(row)
            if ticket.request_fingerprint != request_fingerprint:
                raise IdempotencyConflict(
                    "profile_ticket_fingerprint_conflict",
                    "launch request fingerprint differs from the issued ticket",
                )
            if ticket.state is ProfileLaunchTicketState.CONSUMED:
                if (
                    ticket.child_command_id,
                    ticket.child_run_id,
                ) != (child_command_id, child_run_id):
                    raise IdempotencyConflict(
                        "profile_ticket_child_conflict",
                        "launch ticket already names another child",
                    )
                return ProfileLaunchConsumeResult(ticket, True)
            if ticket.state is ProfileLaunchTicketState.CANCELLED:
                raise TerminalConflict(
                    "profile_ticket_cancelled",
                    "cancelled profile launch ticket cannot be consumed",
                )
            parent = await (
                await db.execute(
                    "SELECT status FROM execution_runs WHERE run_id=?",
                    (ticket.parent_run_id,),
                )
            ).fetchone()
            goal = await (
                await db.execute(
                    "SELECT status FROM execution_task_goals WHERE root_run_id=?",
                    (ticket.root_run_id,),
                )
            ).fetchone()
            attempt = await (
                await db.execute(
                    "SELECT status FROM execution_attempt_records WHERE attempt_id=?",
                    (ticket.attempt_id,),
                )
            ).fetchone()
            call = await (
                await db.execute(
                    """SELECT admission_state FROM execution_provider_action_calls
                    WHERE root_run_id=? AND provider_call_id=?""",
                    (ticket.root_run_id, ticket.spawn_call_id),
                )
            ).fetchone()
            if (
                parent is None
                or goal is None
                or attempt is None
                or call is None
                or str(parent["status"])
                in {
                    RunStatus.COMPLETED.value,
                    RunStatus.FAILED.value,
                    RunStatus.CANCELLED.value,
                }
                or str(goal["status"])
                not in {
                    TaskGoalStatus.ACTIVE.value,
                    TaskGoalStatus.WAITING_EXTERNAL.value,
                }
                or str(attempt["status"])
                not in {
                    AttemptStatus.RUNNING.value,
                    AttemptStatus.WAITING_EXTERNAL.value,
                }
                or str(call["admission_state"])
                not in {
                    ProviderActionAdmissionState.ADMITTED.value,
                    ProviderActionAdmissionState.PREPARED.value,
                    ProviderActionAdmissionState.WAITING_EXTERNAL.value,
                }
            ):
                raise TerminalConflict(
                    "profile_ticket_parent_terminal",
                    "launch ticket parent boundary is no longer live",
                )
            now = float(self._clock())
            try:
                cursor = await db.execute(
                    """UPDATE execution_profile_launch_tickets
                    SET state='consumed',child_command_id=?,child_run_id=?,
                        ticket_version=ticket_version+1,updated_at=?,consumed_at=?
                    WHERE ticket_ref=? AND state='issued' AND ticket_version=?""",
                    (
                        child_command_id,
                        child_run_id,
                        now,
                        now,
                        ticket_ref,
                        expected_version,
                    ),
                )
            except aiosqlite.IntegrityError as exc:
                raise IdempotencyConflict(
                    "profile_ticket_child_unique_conflict",
                    "child identity is already bound to another launch ticket",
                ) from exc
            if cursor.rowcount != 1:
                raise VersionConflict(
                    "profile_ticket_cas_conflict",
                    "launch ticket consume compare-and-set lost",
                )
            self._fault("profile_ticket_consume_after_cas")
            self._fault("profile_ticket_consume_before_commit")
            updated = await (
                await db.execute(
                    """SELECT * FROM execution_profile_launch_tickets
                    WHERE ticket_ref=?""",
                    (ticket_ref,),
                )
            ).fetchone()
            assert updated is not None
            return ProfileLaunchConsumeResult(self._row_to_profile_ticket(updated), False)

    async def recover_profile_launch_tickets(
        self,
        *,
        parent_run_id: str | None = None,
        limit: int = 100,
    ) -> tuple[ProfileLaunchTicket, ...]:
        """Cancel explicitly cancelled parents, then return live issued tickets."""

        if limit <= 0:
            return ()
        async with self._write_transaction() as db:
            now = float(self._clock())
            if parent_run_id is not None:
                await db.execute(
                    """UPDATE execution_profile_launch_tickets AS tickets
                    SET state='cancelled',ticket_version=ticket_version+1,
                        updated_at=?,cancelled_at=?
                    WHERE tickets.state='issued'
                      AND tickets.parent_run_id=?
                      AND (
                        EXISTS(
                            SELECT 1 FROM execution_runs AS parent
                            WHERE parent.run_id=tickets.parent_run_id
                              AND parent.status='cancelled'
                        )
                        OR EXISTS(
                            SELECT 1 FROM execution_task_goals AS goal
                            WHERE goal.root_run_id=tickets.root_run_id
                              AND goal.status='cancelled'
                        )
                        OR EXISTS(
                            SELECT 1 FROM execution_attempt_records AS attempt
                            WHERE attempt.attempt_id=tickets.attempt_id
                              AND attempt.status='cancelled'
                        )
                      )""",
                    (now, now, parent_run_id),
                )
                rows = await (
                    await db.execute(
                        """SELECT tickets.*
                        FROM execution_profile_launch_tickets AS tickets
                        JOIN execution_runs AS parent
                          ON parent.run_id=tickets.parent_run_id
                        JOIN execution_task_goals AS goal
                          ON goal.root_run_id=tickets.root_run_id
                        JOIN execution_attempt_records AS attempt
                          ON attempt.attempt_id=tickets.attempt_id
                        JOIN execution_provider_action_calls AS calls
                          ON calls.root_run_id=tickets.root_run_id
                         AND calls.provider_call_id=tickets.spawn_call_id
                        WHERE tickets.state='issued'
                          AND tickets.parent_run_id=?
                          AND parent.status NOT IN ('completed','failed','cancelled')
                          AND goal.status IN ('active','waiting_external')
                          AND attempt.status IN ('running','waiting_external')
                          AND calls.admission_state IN (
                            'admitted','prepared','waiting_external'
                          )
                        ORDER BY tickets.created_at,tickets.ticket_ref
                        LIMIT ?""",
                        (parent_run_id, limit),
                    )
                ).fetchall()
            else:
                await db.execute(
                    """UPDATE execution_profile_launch_tickets AS tickets
                    SET state='cancelled',ticket_version=ticket_version+1,
                        updated_at=?,cancelled_at=?
                    WHERE tickets.state='issued'
                      AND (
                        EXISTS(
                            SELECT 1 FROM execution_runs AS parent
                            WHERE parent.run_id=tickets.parent_run_id
                              AND parent.status='cancelled'
                        )
                        OR EXISTS(
                            SELECT 1 FROM execution_task_goals AS goal
                            WHERE goal.root_run_id=tickets.root_run_id
                              AND goal.status='cancelled'
                        )
                        OR EXISTS(
                            SELECT 1 FROM execution_attempt_records AS attempt
                            WHERE attempt.attempt_id=tickets.attempt_id
                              AND attempt.status='cancelled'
                        )
                      )""",
                    (now, now),
                )
                rows = await (
                    await db.execute(
                        """SELECT tickets.*
                    FROM execution_profile_launch_tickets AS tickets
                    JOIN execution_runs AS parent
                      ON parent.run_id=tickets.parent_run_id
                    JOIN execution_task_goals AS goal
                      ON goal.root_run_id=tickets.root_run_id
                    JOIN execution_attempt_records AS attempt
                      ON attempt.attempt_id=tickets.attempt_id
                    JOIN execution_provider_action_calls AS calls
                      ON calls.root_run_id=tickets.root_run_id
                     AND calls.provider_call_id=tickets.spawn_call_id
                    WHERE tickets.state='issued'
                      AND parent.status NOT IN ('completed','failed','cancelled')
                      AND goal.status IN ('active','waiting_external')
                      AND attempt.status IN ('running','waiting_external')
                      AND calls.admission_state IN (
                        'admitted','prepared','waiting_external'
                    )
                    ORDER BY tickets.created_at,tickets.ticket_ref
                    LIMIT ?""",
                        (limit,),
                    )
                ).fetchall()
            self._fault("profile_ticket_recover_before_commit")
            return tuple(self._row_to_profile_ticket(row) for row in rows)
