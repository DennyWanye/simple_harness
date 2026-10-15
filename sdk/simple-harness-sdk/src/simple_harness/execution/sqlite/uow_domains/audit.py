# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Run, context-stage and command operation audit records and reads."""

from __future__ import annotations

import sqlite3

from simple_harness.contracts import RunId, canonical_json, thaw_json
from simple_harness.execution.audit import (
    RunAuditUnavailable,
    RunOperationAuditSnapshotV1,
    audit_error_code,
    audit_hash,
    audit_label_syntax,
)
from simple_harness.execution.effects import effect_request_hash
from simple_harness.execution.runtime_audit import RUNTIME_BOUNDARIES
from simple_harness.execution.uow import UnitOfWorkConflict

from ..audit import head_fact, read_snapshot, read_terminal_record
from ..audit_pages import open_pages, read_page
from ...context_use_requirements import validate_recovery
from ..decision_terminal import eligible, recover
from .core import ExecutionUnitOfWorkCore
from .support import _fault


class RunAuditMixin(ExecutionUnitOfWorkCore):
    """Run, context-stage and command operation audit records and reads."""

    __slots__ = ()

    def configure_context_use_authority(self, scope):
        if self._context_use_configured and self._context_use_scope != scope:
            raise ValueError("context_use_uow_configuration_changed")
        self._context_use_scope = scope
        self._context_use_configured = True

    def validate_context_use_recovery(self):

        with self.database.transaction(read_only=True) as connection:
            validate_recovery(connection, self._context_use_scope)

    def read_run_terminal_record(self, run_id: RunId):
        """Exact public metadata for ordinary root terminal consumers."""

        if not isinstance(run_id, RunId):
            raise TypeError("run_id must use RunId")
        with self.database.audit_reader() as reader:
            with reader.transaction(read_only=True) as connection:
                return read_terminal_record(connection, run_id.value)

    def read_expired_authorization_terminal_recovery(self, run_id: RunId):
        """Read exact eligibility from public Run identity; no Host SQL required."""

        if not isinstance(run_id, RunId):
            raise TypeError("run_id must use RunId")
        with self.database.audit_reader() as reader:
            with reader.transaction(read_only=True) as connection:
                found = eligible(connection, run_id.value)
                return None if found is None else found[0]

    def recover_expired_authorization_terminal(self, witness, *, now: float, fault=None):
        """Explicit bounded repair, with all witness facts rechecked in one TX."""

        with self.database.transaction() as connection:
            proof = recover(self, connection, witness, now=now, fault=fault)
        _fault(fault, "authorization_terminal_recovery.after_commit")
        return proof

    def read_run_operation_audit(
        self, run_id: RunId, *, limit: int = 256
    ) -> RunOperationAuditSnapshotV1:

        if not isinstance(run_id, RunId):
            raise TypeError("run_id must use RunId")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 4096:
            raise ValueError("audit limit must be 1..4096")
        # One transaction owns every source read; no transaction spans API calls.
        try:
            with self.database.audit_reader() as reader:
                with reader.transaction(read_only=True) as connection:
                    return read_snapshot(connection, run_id.value, limit)
        except sqlite3.DatabaseError:
            raise RunAuditUnavailable("audit_store_unavailable") from None
        except (KeyError, TypeError, ValueError):
            raise RunAuditUnavailable("audit_source_invalid") from None

    def open_run_operation_audit(self, run_id: RunId, *, page_size: int = 256):

        with self.database.audit_reader() as reader:
            return open_pages(reader, run_id, page_size=page_size)

    def read_run_operation_audit_page(self, run_id: RunId, *, cursor: str):

        with self.database.audit_reader() as reader:
            return read_page(reader, run_id, cursor=cursor)

    def open_context_stage_operation_audit(self, stage_id: str, *, page_size: int = 256):

        with self.database.audit_reader() as reader:
            return open_pages(reader, stage_id=stage_id, page_size=page_size)

    def read_context_stage_operation_audit_page(self, stage_id: str, *, cursor: str):

        with self.database.audit_reader() as reader:
            return read_page(reader, stage_id=stage_id, cursor=cursor)

    def open_command_operation_audit(self, command_id: str, *, page_size: int = 256):

        with self.database.audit_reader() as reader:
            return open_pages(reader, command_id=command_id, page_size=page_size)

    def read_command_operation_audit_page(self, command_id: str, *, cursor: str):

        with self.database.audit_reader() as reader:
            return read_page(reader, command_id=command_id, cursor=cursor)

    def _audit_operation_head(self, connection, kind, identity, now):

        run_id, payload = head_fact(connection, kind, identity)
        event_id = "audit-head:" + audit_hash(
            dict(kind=kind, identity=identity, version=payload["source_version"])
        )
        prefix = event_id + ":"
        old = connection.execute(
            "SELECT payload_json FROM run_events WHERE event_id LIKE ?", (prefix + "%",)
        ).fetchone()
        if old is not None:
            if old[0] != canonical_json(payload):
                raise UnitOfWorkConflict("audit source version changed")
            return
        self._insert_event(
            connection,
            event_id=prefix + audit_hash(payload),
            run_id=run_id,
            kind="audit.transition.v1",
            payload=payload,
            now=now,
        )
        _fault(self.workflow_fault, f"operation_audit.{kind}.{payload['state']}.after_write")

    def record_runtime_operation(
        self,
        *,
        name,
        operation_id,
        state,
        lease,
        identity_hash,
        receipt_hash,
        started_at,
        now,
        contract,
        error_code=None,
        parent_operation_id=None,
        child_operation_ids=None,
    ):

        if name not in RUNTIME_BOUNDARIES or state not in {
            "started",
            "completed",
            "failed",
            "interrupted",
            "rejected",
        }:
            raise ValueError("invalid runtime audit boundary")
        payload = dict(
            name=name,
            contract=contract,
            operation_id=operation_id,
            state=state,
            runtime_epoch=lease.epoch,
            owner_hash=audit_hash(lease.owner_id),
            identity_hash=identity_hash,
            receipt_hash=receipt_hash,
            started_at=started_at,
            error_code=error_code,
            parent_operation_id=parent_operation_id,
            child_operation_ids=child_operation_ids,
        )
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, lease, now=now)
            self._insert_event(
                connection,
                event_id="audit-runtime:" + operation_id + ":" + state + ":" + audit_hash(payload),
                run_id=lease.run_id,
                kind="audit.runtime.v2",
                payload=payload,
                now=now,
            )

    def record_tool_audit(
        self,
        *,
        effect_id,
        call,
        state,
        execution_lease,
        run_fence,
        now,
        raw_call_id=None,
        turn_ordinal=0,
        call_ordinal=0,
        error_code=None,
        registered_tool_name=None,
    ):

        if state not in {
            "requested",
            "waiting",
            "rejected",
            "failed",
            "succeeded",
            "partial",
            "unknown",
        }:
            raise ValueError("invalid tool audit state")
        payload = dict(
            operation_id="tool:" + effect_id.value,
            call_id=call.call_id.value,
            request_hash=effect_request_hash(
                tool_name=call.name, arguments=thaw_json(call.arguments)
            ),
            state=state,
            operation_name=audit_label_syntax(registered_tool_name),
            registered_tool_name=audit_label_syntax(registered_tool_name),
            operation_name_hash=audit_hash(call.name),
            effect_id=effect_id.value,
            raw_call_id_hash=None if raw_call_id is None else audit_hash(raw_call_id),
            turn_ordinal=turn_ordinal,
            call_ordinal=call_ordinal,
            error_code=audit_error_code(error_code),
            error_code_hash=None if error_code is None else audit_hash(str(error_code)),
        )
        identity = "audit-tool:" + audit_hash(
            dict(run_id=execution_lease.run_id, operation_id=payload["operation_id"], state=state)
        )
        if state != "requested":
            identity += ":" + audit_hash(payload)
        with self.database.transaction() as connection:
            self._require_runtime_lease(connection, execution_lease, now=now)
            self._require_run_fence(connection, run_fence, execution_lease=execution_lease)
            prefix = identity + ":"
            existing = connection.execute(
                "SELECT payload_json FROM run_events WHERE event_id LIKE ?", (prefix + "%",)
            ).fetchone()
            if existing is not None:
                if existing[0] != canonical_json(payload):
                    raise UnitOfWorkConflict("tool audit intent conflict")
                return
            self._insert_event(
                connection,
                event_id=prefix + audit_hash(payload),
                run_id=execution_lease.run_id,
                kind="audit.tool.v1",
                payload=payload,
                now=now,
            )
