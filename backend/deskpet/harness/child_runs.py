"""Durable child commands and parent-signal replay."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any

from deskpet.execution.contracts import (
    AttachmentPolicy,
    ChildCommandIntent,
    PersistenceLevel,
    ProfileLaunchTicket,
    RunContext,
    RunCreate,
    RunRecord,
    RunStartSnapshotRecord,
    RunStatus,
    canonical_json,
    delegate_idempotency_key,
    fingerprint_json,
)
from deskpet.execution.contracts import thaw_json
from deskpet.execution.uow_ports import ChildRunUnitOfWork
from deskpet.harness.contracts import PreparedRunContextV1
from deskpet.harness.ports import DriverEvent, JoinPolicy


class ChildRunCoordinator:
    def __init__(
        self,
        store: ChildRunUnitOfWork,
        *,
        snapshot_leaser: Any | None = None,
    ) -> None:
        self._store = store
        self._snapshot_leaser = snapshot_leaser
        self._reconcile_trigger: Callable[[], None] | None = None

    def bind_reconcile_trigger(self, trigger: Callable[[], None]) -> None:
        if self._reconcile_trigger is not None:
            raise RuntimeError("child reconciliation trigger is already bound")
        self._reconcile_trigger = trigger

    def trigger_reconciliation(self) -> None:
        """Wake the short-lived reconciler after a durable child fact commits."""

        if self._reconcile_trigger is not None:
            self._reconcile_trigger()

    @staticmethod
    def _intent(
        parent: RunRecord,
        command: DriverEvent,
        *,
        profile_ticket: ProfileLaunchTicket | None = None,
    ) -> ChildCommandIntent:
        if parent.run_id != command.run_id:
            raise ValueError("delegate command names another parent run")
        if parent.persistence_level is not PersistenceLevel.DURABLE:
            raise ValueError("delegate parent must cross a durable boundary first")
        attachment = {
            JoinPolicy.JOIN_BEFORE_FINAL: AttachmentPolicy.ATTACHED,
            JoinPolicy.ROOT_TERMINAL_CHILD: AttachmentPolicy.ROOT_TERMINAL_CHILD,
            JoinPolicy.DETACHED: AttachmentPolicy.DETACHED,
        }[JoinPolicy(command.join_policy)]
        if command.attachment_policy.value != attachment.value:
            raise ValueError("delegate attachment and join policy disagree")
        operation_id = hashlib.sha256(
            f"execution-child-operation|{parent.run_id}|{command.command_id}".encode()
        ).hexdigest()
        child_run_id = "child-" + hashlib.sha256(
            f"execution-child-run|{parent.run_id}|{command.command_id}".encode()
        ).hexdigest()[:32]
        child_request = thaw_json(command.child_request)
        assert isinstance(child_request, dict)
        ticket_ref = command.data.get("profile_launch_ticket_ref")
        launch_request = command.data.get("profile_launch_request")
        if ticket_ref is not None:
            if profile_ticket is None or not isinstance(launch_request, Mapping):
                raise ValueError(
                    "profile launch delegate requires its durable ticket"
                )
            if (
                fingerprint_json(dict(launch_request))
                != profile_ticket.request_fingerprint
            ):
                raise ValueError(
                    "profile launch request differs from its durable ticket"
                )
            trusted_child_payload_hash = str(
                launch_request.get("trusted_child_payload_hash") or ""
            )
            if (
                len(trusted_child_payload_hash) != 64
                or fingerprint_json(child_request)
                != trusted_child_payload_hash
            ):
                raise ValueError(
                    "profile launch child payload differs from its durable ticket"
                )
        for untrusted_host_field in (
            "capability_snapshot",
            "capability_snapshot_lease",
            "owner_key",
            "run_catalog_content_stamp",
            "process_catalog_stamp",
        ):
            if (
                untrusted_host_field == "capability_snapshot"
                and ticket_ref is not None
            ):
                continue
            child_request.pop(untrusted_host_field, None)
        capability_subset = tuple(sorted(set(command.capability_subset)))
        if ticket_ref is not None:
            assert profile_ticket is not None
            assert isinstance(launch_request, Mapping)
            if (
                profile_ticket.ticket_ref != ticket_ref
                or profile_ticket.parent_run_id != parent.run_id
            ):
                raise ValueError("profile launch ticket names another delegate")
            if (
                command.route_hint != profile_ticket.profile_key
                or launch_request.get("profile_key") != profile_ticket.profile_key
                or int(launch_request.get("profile_catalog_generation") or 0)
                != profile_ticket.profile_catalog_generation
                or launch_request.get("task_scope_id") != profile_ticket.task_scope_id
            ):
                raise ValueError("profile launch route differs from its durable ticket")
            for field_name in ("profile_key", "driver_kind"):
                supplied = child_request.get(field_name)
                expected = getattr(profile_ticket, field_name)
                if supplied is not None and supplied != expected:
                    raise ValueError(
                        f"profile launch child {field_name} override is forbidden"
                    )
            for field_name in (
                "objective",
                "workspace_ref",
                "task_scope_id",
                "profile_catalog_generation",
                "trigger_failure_set_id",
                "focused_failure_ref",
                "supersedes_run_id",
            ):
                if child_request.get(field_name) != launch_request.get(field_name):
                    raise ValueError(
                        f"profile launch child {field_name} differs from ticket request"
                    )
            if list(child_request.get("input_refs") or ()) != list(
                launch_request.get("input_refs") or ()
            ):
                raise ValueError(
                    "profile launch child input_refs differ from ticket request"
                )
            objective = launch_request.get("objective")
            if any(
                child_request.get(field_name) != objective
                for field_name in ("text", "request", "topic")
            ):
                raise ValueError(
                    "profile launch child objective aliases differ from ticket request"
                )
            capability_ref = profile_ticket.capability_snapshot_ref
            profile_key = profile_ticket.profile_key
            driver_kind = profile_ticket.driver_kind
        else:
            if profile_ticket is not None or launch_request is not None:
                raise ValueError("legacy delegate cannot carry a partial launch ticket")
            capability_snapshot = {"tools": list(capability_subset)}
            capability_ref = fingerprint_json(capability_snapshot)
            profile_key = command.route_hint
            driver_kind = str(child_request.get("driver_kind") or "react")
        context = parent.context
        child_spec = RunCreate(
            run_id=child_run_id,
            idempotency_key=delegate_idempotency_key(
                parent.run_id, command.command_id, profile_key
            ),
            context=RunContext(
                session_id=context.session_id,
                root_run_id=context.root_run_id,
                parent_run_id=parent.run_id,
                request_id=f"{context.request_id}:child:{command.command_id}",
                turn_id=context.turn_id,
                venue=context.venue,
                workspace=thaw_json(context.workspace),
                capability_hash=capability_ref,
                provider_plan=thaw_json(context.provider_plan),
                trace_id=f"child-trace-{operation_id[:32]}",
                principal_id=context.principal_id,
                auth_epoch=context.auth_epoch,
                owner_key=context.owner_key,
                profile_generation=context.profile_generation,
                binding_epoch=context.binding_epoch,
            ),
            payload_fingerprint=fingerprint_json(child_request),
            capability_fingerprint=capability_ref,
            driver_kind=driver_kind,
            profile_key=profile_key,
            persistence_level=PersistenceLevel.DURABLE,
            status=RunStatus.QUEUED,
        )
        return ChildCommandIntent(
            operation_id=operation_id,
            parent_run_id=parent.run_id,
            command_id=command.command_id,
            child_spec=child_spec,
            child_request=child_request,
            capability_subset=capability_subset,
            attachment_policy=attachment,
            capability_snapshot_ref=capability_ref,
        )

    @staticmethod
    def _start_snapshot(
        parent: RunRecord,
        intent: ChildCommandIntent,
        *,
        parent_start: RunStartSnapshotRecord | None,
        trusted_capability_snapshot: Mapping[str, Any],
        prepared_catalog_lease: Any | None = None,
    ) -> RunStartSnapshotRecord:
        """Derive an immutable child start only from host-trusted parent state."""

        child_request = thaw_json(intent.child_request)
        assert isinstance(child_request, dict)
        text = str(
            child_request.get("text")
            or child_request.get("task")
            or child_request.get("request")
            or child_request.get("topic")
            or ""
        )
        raw_messages = child_request.get("messages")
        canonical_messages = (
            thaw_json(raw_messages)
            if isinstance(raw_messages, (list, tuple)) and raw_messages
            else [{"role": "user", "content": text}]
        )
        capability_snapshot = thaw_json(dict(trusted_capability_snapshot))
        assert isinstance(capability_snapshot, dict)
        if not capability_snapshot:
            capability_snapshot = {
                "capabilities": sorted(intent.capability_subset),
            }
        # A cloned lease is owned by the child transaction. Never copy a
        # parent's lease-intent identity into the child's immutable start.
        capability_snapshot.pop("capability_lease_intent_hash", None)
        lease_intent_ref = (
            None
            if prepared_catalog_lease is None
            else prepared_catalog_lease.lease_intent_id
        )
        lease_intent_hash = (
            None
            if prepared_catalog_lease is None
            else prepared_catalog_lease.lease_intent_hash
        )
        if lease_intent_ref is None:
            capability_snapshot.pop("capability_lease_intent_ref", None)
        else:
            capability_snapshot["capability_lease_intent_ref"] = (
                lease_intent_ref
            )
        capability_snapshot["capability_hash"] = (
            intent.child_spec.capability_fingerprint
        )
        prepared_refs: dict[str, Any] = {
            "parent_run_id": parent.run_id,
            "parent_start_fingerprint": (
                None if parent_start is None else parent_start.start_fingerprint
            ),
            "parent_prepared_refs": (
                {}
                if parent_start is None
                else json.loads(parent_start.prepared_refs_json)
            ),
        }
        session_cursor = {
            "session_projection_cursor": 0,
        }
        sanitized_request = {
            "payload": child_request,
            "text": text,
        }
        provider_launch_policy = {
            "provider_plan": thaw_json(intent.child_spec.context.provider_plan),
        }
        terminal_deliveries: list[dict[str, Any]] = []
        snapshot_payload = {
            "snapshot_schema_version": 1,
            "canonical_messages": canonical_messages,
            "session_cursor": session_cursor,
            "prepared_refs": prepared_refs,
            "sanitized_request": sanitized_request,
            "run_context": intent.child_spec.context.to_dict(),
            "run_spec": intent.child_spec.to_dict(),
            "capability_snapshot": capability_snapshot,
            "provider_launch_policy": provider_launch_policy,
            "terminal_deliveries": terminal_deliveries,
            "capability_lease_intent_ref": lease_intent_ref,
            "capability_lease_intent_hash": lease_intent_hash,
        }
        return RunStartSnapshotRecord(
            run_id=intent.child_run_id,
            snapshot_schema_version=1,
            start_fingerprint=fingerprint_json(snapshot_payload),
            canonical_messages_json=canonical_json(canonical_messages),
            session_cursor_json=canonical_json(session_cursor),
            prepared_refs_json=canonical_json(prepared_refs),
            sanitized_request_json=canonical_json(sanitized_request),
            run_context_json=canonical_json(
                intent.child_spec.context.to_dict()
            ),
            run_spec_json=canonical_json(intent.child_spec.to_dict()),
            capability_snapshot_json=canonical_json(capability_snapshot),
            capability_snapshot_hash=fingerprint_json(capability_snapshot),
            provider_launch_policy_json=canonical_json(
                provider_launch_policy
            ),
            terminal_deliveries_json=canonical_json(terminal_deliveries),
            terminal_deliveries_hash=fingerprint_json(terminal_deliveries),
            capability_lease_intent_ref=lease_intent_ref,
            capability_lease_intent_hash=lease_intent_hash,
            # Parent creation time is a stable replay input even for legacy
            # parents that predate RunStartSnapshot.
            created_at=parent.created_at,
        )

    async def submit(
        self, parent: RunRecord, command: DriverEvent, *, recovery_lease: RecoveryLease | None = None
    ) -> ChildCommandRecord:
        ticket_ref = command.data.get("profile_launch_ticket_ref")
        profile_ticket = (
            None
            if ticket_ref is None
            else await self._store.get_profile_launch_ticket(str(ticket_ref))
        )
        if ticket_ref is not None and profile_ticket is None:
            raise ValueError("profile launch ticket does not exist")
        intent = self._intent(parent, command, profile_ticket=profile_ticket)
        parent_start = await self._store.read_run_start_snapshot(parent.run_id)
        trusted_snapshot = (
            {}
            if parent_start is None
            else json.loads(parent_start.capability_snapshot_json)
        )
        if parent_start is not None and (
            fingerprint_json(trusted_snapshot)
            != parent_start.capability_snapshot_hash
        ):
            raise ValueError("parent capability snapshot hash mismatch")
        snapshot_ref = str(
            trusted_snapshot.get("catalog_snapshot_ref") or ""
        )
        prepared_catalog_lease = None
        if snapshot_ref:
            if (
                parent_start is None
                or parent_start.capability_lease_intent_ref is None
                or parent_start.capability_lease_intent_hash is None
            ):
                raise RuntimeError(
                    "trusted_parent_snapshot_lease_identity_missing"
                )
            prepare_child = getattr(
                self._snapshot_leaser,
                "prepare_child_lease_projection",
                None,
            )
            if not callable(prepare_child):
                raise RuntimeError(
                    "child_snapshot_projection_owner_unavailable"
                )
            prepared_catalog_lease = await prepare_child(
                source_snapshot_ref=snapshot_ref,
                source_run_id=parent.run_id,
                source_lease_intent_id=(
                    parent_start.capability_lease_intent_ref
                ),
                source_lease_intent_hash=(
                    parent_start.capability_lease_intent_hash
                ),
                child_run_id=intent.child_run_id,
                root_run_id=intent.child_spec.context.root_run_id,
                request_id=intent.child_spec.context.request_id,
                turn_id=intent.child_spec.context.turn_id,
                owner_operation_id=intent.operation_id,
            )
        start_snapshot = self._start_snapshot(
            parent,
            intent,
            parent_start=parent_start,
            trusted_capability_snapshot=trusted_snapshot,
            prepared_catalog_lease=prepared_catalog_lease,
        )
        start_extensions = (
            ()
            if prepared_catalog_lease is None
            else (prepared_catalog_lease.start_commit_extension,)
        )
        try:
            if profile_ticket is not None:
                record = await self._store.claim_profile_launch_and_commit_child(
                    profile_ticket.ticket_ref,
                    intent,
                    dict(command.profile_launch_request),
                    expected_ticket_version=profile_ticket.version,
                    start_snapshot=start_snapshot,
                    start_commit_extensions=start_extensions,
                    recovery_lease=recovery_lease,
                )
            else:
                record = (
                    await self._store.commit_child_command_and_precreate_child(
                        intent,
                        start_snapshot,
                        start_commit_extensions=start_extensions,
                        recovery_lease=recovery_lease,
                    )
                )
        except BaseException:
            if prepared_catalog_lease is not None:
                durable_start = await self._store.read_run_start_snapshot(
                    intent.child_run_id
                )
                if durable_start is None:
                    await prepared_catalog_lease.release_prepared()
                    retire_prepared = getattr(
                        self._snapshot_leaser,
                        "retire_run_catalog_ready",
                        None,
                    )
                    if callable(retire_prepared):
                        await retire_prepared(intent.child_run_id)
            raise
        self.trigger_reconciliation()
        return record

    async def activate_child_snapshot_after_commit(
        self, start_snapshot: RunStartSnapshotRecord
    ) -> PreparedRunContextV1 | None:
        activate = getattr(
            self._snapshot_leaser,
            "activate_child_snapshot_after_commit",
            None,
        )
        if not callable(activate):
            if start_snapshot.capability_lease_intent_ref is not None:
                raise RuntimeError(
                    "child_snapshot_projection_owner_unavailable"
                )
            return None
        lease = await activate(start_snapshot)
        if lease is None:
            return None
        return PreparedRunContextV1(
            persistence_required=True,
            capability_lease_intent_ref=lease.lease_intent_id,
            capability_lease_intent_hash=lease.lease_intent_hash,
            terminal_commit_extensions=(
                lease.terminal_commit_extension,
            ),
            after_terminal_commit_cleanup=(
                lease.after_terminal_cleanup,
            ),
        )
