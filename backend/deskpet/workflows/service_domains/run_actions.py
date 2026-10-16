"""Decisions, resume, cancel, run actions, retry and checkpoint forks."""

from __future__ import annotations

import copy
import inspect
import re
import uuid
from collections.abc import Mapping
from typing import Any

from deskpet.execution.contracts import ActorContext, AuthorizationError, RunRef

from ..contracts import JsonValue, WorkflowContext, canonical_json, validate_json_value
from ..human import HumanDecision
from ..store.checkpointer import NativeCheckpointStore
from ..store.research_repository import ResearchRepositoryError
from ..terminal_projection import WorkflowActionContext
from .core import WorkflowServiceCore
from .support import WorkflowServiceError, WorkflowStartIdentity, _plain

_UUID_V4_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class RunActionMixin(WorkflowServiceCore):
    """Decisions, resume, cancel, run actions, retry and checkpoint forks."""

    __slots__ = ()

    @staticmethod
    def _decision_dict(decision: HumanDecision | object) -> dict[str, Any]:
        return _plain(decision)

    async def resolve_decision(
        self,
        decision_id: str,
        *,
        nonce: str,
        response: JsonValue,
        expected_version: int,
    ) -> dict[str, Any]:
        decision = await self.human_store.resolve_decision(
            decision_id,
            nonce=nonce,
            response=response,
            expected_version=expected_version,
        )
        responses = await self.human_store.build_resume_payload(decision_id)
        launcher = getattr(self, "launcher", None)
        schedule = getattr(launcher, "schedule_resume", None)
        if callable(schedule):
            accepted = schedule(decision.run_id, responses)
            if inspect.isawaitable(accepted):
                accepted = await accepted
            return {
                **self._decision_dict(decision),
                "resume": {
                    "triggered": True,
                    "accepted": True,
                    "completion_semantics": "accepted_async",
                    "interrupt_ids": list(responses),
                    "dispatch": _plain(accepted),
                },
            }
        resumed = await self.resume_run(decision.run_id, responses, trusted=True)
        return {
            **self._decision_dict(decision),
            "resume": {
                "triggered": True,
                "interrupt_ids": list(responses),
                "result": resumed,
            },
        }

    async def resume_run(
        self,
        run_id: str,
        responses: Mapping[str, JsonValue],
        context: WorkflowContext | None = None,
        trusted: bool = False,
    ) -> dict[str, Any]:
        if not trusted:
            authoritative = await self.human_store.build_run_resume_payload(run_id)
            if canonical_json(dict(responses)) != canonical_json(authoritative):
                raise WorkflowServiceError(
                    "invalid_resume_payload",
                    "Workflow resume responses must come from resolved decisions",
                )
        launcher = getattr(self, "launcher", None)
        method = getattr(launcher, "resume_run", None)
        uses_launcher = callable(method)
        if not callable(method):
            method = getattr(self.runner, "resume", None)
            await self.require_legacy_owner(run_id, "resume")
        if not callable(method):
            raise WorkflowServiceError(
                "workflow_resume_unavailable", "The workflow runner cannot resume runs"
            )
        if uses_launcher:
            return _plain(await method(run_id, dict(responses)))
        return _plain(await method(run_id, dict(responses), context))

    resume = resume_run

    async def cancel_run(
        self, run_id: str, *, reason: str = "user", actor: ActorContext | None = None
    ) -> dict[str, Any]:
        if await self.execution_owner(run_id) is not None:
            if actor is None or actor.root_run_id is None:
                raise WorkflowServiceError(
                    "actor_required",
                    "execution-owned cancel requires trusted actor context",
                )
            try:
                await self._owner_uow.authorize(RunRef(run_id, actor.session_id), actor, "cancel")
            except AuthorizationError as exc:
                raise WorkflowServiceError(exc.code, str(exc)) from exc
            return _plain(await self.cancel_precreated(run_id, reason))
        method = getattr(self.runner, "request_cancel", None)
        if not callable(method):
            raise WorkflowServiceError(
                "workflow_cancel_unavailable", "The workflow runner cannot cancel runs"
            )
        return _plain(await method(run_id, reason))

    cancel = cancel_run

    async def execute_run_action(
        self,
        run_id: str,
        *,
        action_id: str,
        idempotency_key: str,
        expected_version: int,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> dict[str, Any]:
        """Execute one action through the immutable workflow-version matrix."""

        run_id = self._required(run_id, "run_id")
        action_id = self._required(action_id, "action_id")
        idempotency_key = self._required(idempotency_key, "idempotency_key")
        action_payload, row = await self._require_action_run_version(
            expected_version, payload, run_id
        )
        workflow_name = str(row.get("workflow_name") or "")
        workflow_version = str(row.get("workflow_version") or "")
        engine_status = str(row.get("status") or "")

        if (workflow_name, workflow_version, action_id) == (
            "deep_research",
            "v4",
            "retry_from_start",
        ):
            if not self.action_matrix.allows(
                workflow_name,
                workflow_version,
                action_id,
                WorkflowActionContext(engine_status=engine_status),
            ):
                raise WorkflowServiceError(
                    "workflow_action_not_allowed", "workflow action is not allowed"
                )
            return await self.retry_run_from_start(
                run_id, action_id=action_id, retry_key=idempotency_key
            )

        if workflow_name != "deep_research" or workflow_version not in {"v5", "v6"}:
            raise WorkflowServiceError(
                "workflow_action_not_supported",
                "workflow version does not support this action",
            )
        repository = self.research_repository
        try:
            if workflow_version == "v6" and action_id == "continue_research":
                return await self._continue_v6_research(
                    action_id,
                    action_payload=action_payload,
                    idempotency_key=idempotency_key,
                    repository=repository,
                    run_id=run_id,
                )

            if action_id == "retry_from_start":
                if action_payload:
                    raise WorkflowServiceError(
                        "invalid_action_payload", "retry_from_start does not accept action payload"
                    )
                if not self.action_matrix.allows(
                    workflow_name,
                    workflow_version,
                    action_id,
                    WorkflowActionContext(engine_status=engine_status),
                ):
                    raise WorkflowServiceError(
                        "workflow_action_not_allowed", "retry_from_start is not allowed"
                    )
                return await self.retry_run_from_start(
                    run_id, action_id=action_id, retry_key=idempotency_key
                )
            if action_id == "generate_now":
                brief_id, brief_ns = await self._generate_now_brief(
                    action_payload, row, run_id, workflow_version
                )
                created, opened = await self._open_action_command(
                    action_id,
                    engine_status=engine_status,
                    expected_version=expected_version,
                    idempotency_key=idempotency_key,
                    repository=repository,
                    row=row,
                    run_id=run_id,
                    workflow_name=workflow_name,
                    workflow_version=workflow_version,
                )
                command = await repository.accept_generate_now(
                    str(opened["command_id"]),
                    brief_checkpoint_ns=brief_ns,
                    brief_checkpoint_id=brief_id,
                )
                return await self._accepted_generate_now(action_id, command, created, run_id)

            active = await repository.active_control(run_id)
            if action_id == "cancel_settle":
                command, existing_marker = await self._existing_action_command(
                    action_id,
                    active=active,
                    engine_status=engine_status,
                    expected_version=expected_version,
                    idempotency_key=idempotency_key,
                    repository=repository,
                    run_id=run_id,
                    workflow_name=workflow_name,
                    workflow_version=workflow_version,
                )
                return await self._wake_cancel_settle(action_id, command, existing_marker, run_id)

            if workflow_version == "v5" and action_id == "continue_research":
                if action_payload:
                    raise WorkflowServiceError(
                        "invalid_action_payload",
                        "continue_research does not accept client snapshot or lineage identity",
                    )
                child_run_id, created, lineage = await self._v5_continuation_child(
                    action_id,
                    engine_status=engine_status,
                    idempotency_key=idempotency_key,
                    repository=repository,
                    run_id=run_id,
                    workflow_name=workflow_name,
                    workflow_version=workflow_version,
                )
                return {
                    "run_id": child_run_id,
                    "parent_run_id": run_id,
                    "action_id": action_id,
                    "accepted": True,
                    "created": bool(created),
                    "lineage": _plain(lineage),
                }
        except WorkflowServiceError:
            raise
        except ResearchRepositoryError as exc:
            raise WorkflowServiceError(exc.code, str(exc)) from exc
        raise WorkflowServiceError(
            "workflow_action_not_supported", "workflow version does not support this action"
        )

    async def _wake_cancel_settle(
        self, action_id: str, command, existing_marker, run_id: str
    ) -> dict[str, Any]:
        launcher = getattr(self, "launcher", None)
        wake = getattr(launcher, "wake_run_control", None)
        if callable(wake):
            await wake(run_id)
        return {
            "run_id": run_id,
            "action_id": action_id,
            "accepted": True,
            "created": bool(command.get("created", not isinstance(existing_marker, Mapping))),
            "command": _plain(command),
        }

    async def _require_action_run_version(
        self, expected_version: int, payload: Mapping[str, JsonValue] | None, run_id: str
    ):
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0
        ):
            raise WorkflowServiceError(
                "invalid_run_version", "expected_version must be non-negative"
            )
        action_payload = copy.deepcopy(dict(payload or {}))
        validate_json_value(action_payload, path="$.action_payload")
        await self.require_legacy_owner(run_id, "action")
        row = await self.run_store.get_run(run_id)
        if row is None:
            raise WorkflowServiceError("not_found", "workflow run was not found")
        if int(row.get("run_version", -1)) != expected_version:
            raise WorkflowServiceError(
                "stale_run_version",
                "workflow run version changed",
                current_version=int(row.get("run_version", -1)),
            )
        return action_payload, row

    async def _continue_v6_research(
        self, action_id: str, *, action_payload, idempotency_key: str, repository, run_id: str
    ) -> dict[str, Any]:
        if action_payload:
            raise WorkflowServiceError(
                "invalid_action_payload",
                "v6 continue_research accepts an empty action payload only; "
                "a new scope requires a new root run",
            )
        create_or_get = getattr(repository, "create_or_get_continuation_v6", None)
        if not callable(create_or_get):
            raise WorkflowServiceError(
                "workflow_action_unavailable",
                "v6 continuation repository support is unavailable",
            )
        # The repository is the sole manifest/snapshot/lineage owner.  Do
        # not resolve any parent material here: doing so would create a
        # check/use window before the continuation-head transaction.
        result = _plain(await create_or_get(run_id, idempotency_key))
        child_run_id, start_payload = self._v6_continuation_child(result)
        launcher = getattr(self, "launcher", None)
        launch = getattr(launcher, "launch_existing_run", None)
        if not callable(launch):
            raise WorkflowServiceError(
                "workflow_action_unavailable", "continuation launcher is unavailable"
            )
        # Notify/launch for both the first and replay audit result.  A
        # commit-before-notify crash is recovered by the launcher's
        # generic created-run scan using this same persisted payload.
        await launch(child_run_id, start_payload=dict(start_payload))
        return {
            "run_id": child_run_id,
            "parent_run_id": run_id,
            "action_id": action_id,
            "accepted": True,
            "created": bool(result["created"]),
            "child_operation_id": str(result["child_operation_id"]),
            "audit_operation_id": str(result["audit_operation_id"]),
            "start_request_hash": str(result["start_request_hash"]),
        }

    async def _accepted_generate_now(
        self, action_id: str, command, created, run_id: str
    ) -> dict[str, Any]:
        if str(command.get("status")) != "accepted":
            raise WorkflowServiceError(
                "workflow_action_rejected",
                "generate_now lost the run/head/brief compare-and-swap",
            )
        launcher = getattr(self, "launcher", None)
        wake = getattr(launcher, "wake_run_control", None)
        if callable(wake):
            await wake(run_id)
        return {
            "run_id": run_id,
            "action_id": action_id,
            "accepted": True,
            "created": bool(created),
            "command": _plain(command),
        }

    def _v6_continuation_child(self, result):
        if not isinstance(result, Mapping):
            raise WorkflowServiceError(
                "invalid_continuation_result",
                "v6 continuation repository returned an invalid result",
            )
        expected_result_keys = {
            "schema_version",
            "parent_run_id",
            "child_run_id",
            "child_operation_id",
            "created",
            "start_payload",
            "start_request_hash",
            "audit_operation_id",
        }
        if set(result) != expected_result_keys or result.get("schema_version") != 1:
            raise WorkflowServiceError(
                "invalid_continuation_result",
                "v6 continuation repository result keys differ",
            )
        child_run_id = self._required(str(result["child_run_id"]), "child_run_id")
        start_payload = result["start_payload"]
        if not isinstance(start_payload, Mapping) or set(start_payload) != {
            "schema_version",
            "parent_run_id",
            "source_snapshot_hash",
        }:
            raise WorkflowServiceError(
                "invalid_continuation_result",
                "v6 continuation persisted start payload differs",
            )
        return child_run_id, start_payload

    async def _generate_now_brief(self, action_payload, row, run_id: str, workflow_version):
        if workflow_version == "v6" and action_payload:
            raise WorkflowServiceError(
                "invalid_action_payload", "v6 generate_now does not accept action payload"
            )
        if workflow_version == "v6":
            brief_ns = str(row.get("head_checkpoint_ns") or "")
            brief_id = self._required(
                str(row.get("head_checkpoint_id") or ""), "head_checkpoint_id"
            )
        elif not action_payload:
            # The public v5 projection deliberately does not expose internal
            # checkpoint identities. Resolve the exact current head on the
            # server and verify that it contains the committed modeling
            # contract before using it as the brief CAS ancestor.
            brief_ns = str(row.get("head_checkpoint_ns") or "")
            brief_id = self._required(
                str(row.get("head_checkpoint_id") or ""), "head_checkpoint_id"
            )
            checkpoint = await NativeCheckpointStore(self.run_store.path).get_checkpoint(
                run_id,
                brief_id,
                checkpoint_ns=brief_ns,
            )
            state = checkpoint.get("state") if isinstance(checkpoint, Mapping) else None
            values = state.get("values") if isinstance(state, Mapping) else None
            if not (
                isinstance(values, Mapping)
                and isinstance(values.get("research_brief"), Mapping)
                and isinstance(values.get("dimension_coverages"), list)
            ):
                raise WorkflowServiceError(
                    "workflow_action_not_allowed",
                    "generate_now requires a committed research brief",
                )
        elif set(action_payload) == {"brief_checkpoint_ns", "brief_checkpoint_id"}:
            brief_ns = str(action_payload["brief_checkpoint_ns"])
            brief_id = self._required(
                str(action_payload["brief_checkpoint_id"]), "brief_checkpoint_id"
            )
        else:
            raise WorkflowServiceError(
                "invalid_action_payload",
                "generate_now accepts no payload or exactly brief_checkpoint_ns/brief_checkpoint_id",
            )
        return brief_id, brief_ns

    async def _open_action_command(
        self,
        action_id: str,
        *,
        engine_status,
        expected_version: int,
        idempotency_key: str,
        repository,
        row,
        run_id: str,
        workflow_name,
        workflow_version,
    ):
        if not self.action_matrix.allows(
            workflow_name,
            workflow_version,
            action_id,
            WorkflowActionContext(
                engine_status=engine_status,
                brief_committed=True,
            ),
        ):
            raise WorkflowServiceError("workflow_action_not_allowed", "generate_now is not allowed")
        opened, created = await repository.open_control(
            run_id=run_id,
            idempotency_key=idempotency_key,
            action=action_id,
            expected_run_version=expected_version,
            expected_head_checkpoint_ns=str(row.get("head_checkpoint_ns") or ""),
            expected_head_checkpoint_id=(
                str(row["head_checkpoint_id"])
                if row.get("head_checkpoint_id") is not None
                else None
            ),
            payload={},
        )
        return created, opened

    async def _existing_action_command(
        self,
        action_id: str,
        *,
        active,
        engine_status,
        expected_version: int,
        idempotency_key: str,
        repository,
        run_id: str,
        workflow_name,
        workflow_version,
    ):
        if not self.action_matrix.allows(
            workflow_name,
            workflow_version,
            action_id,
            WorkflowActionContext(
                engine_status=engine_status,
                active_control_status=(str(active.get("status")) if active else None),
            ),
        ):
            raise WorkflowServiceError(
                "workflow_action_not_allowed", "cancel_settle is not allowed"
            )
        request_cancel = getattr(repository, "request_cancel_settle", None)
        if not callable(request_cancel):
            raise WorkflowServiceError(
                "workflow_action_unavailable",
                "cancel_settle repository support is unavailable",
            )
        existing_marker = (
            active.get("payload", {}).get("_cancel_settle")
            if active and isinstance(active.get("payload"), Mapping)
            else None
        )
        command = await request_cancel(
            run_id,
            idempotency_key=idempotency_key,
            expected_run_version=expected_version,
        )
        return command, existing_marker

    async def _v5_continuation_child(
        self,
        action_id: str,
        *,
        engine_status,
        idempotency_key: str,
        repository,
        run_id: str,
        workflow_name,
        workflow_version,
    ):
        resolve_source = getattr(repository, "resolve_continuation_source", None)
        if not callable(resolve_source):
            raise WorkflowServiceError(
                "workflow_action_unavailable",
                "continuation repository resolver is unavailable",
            )
        source = await resolve_source(run_id)
        final_payload: Mapping[str, Any] | None = None
        for event in reversed(await self.run_store.events_after(run_id, 0)):
            if str(event.get("event_type")) == "workflow.final" and isinstance(
                event.get("payload"), Mapping
            ):
                final_payload = event["payload"]
                break
        delivery_status = (
            str(final_payload.get("delivery_status")) if final_payload is not None else None
        )
        if not self.action_matrix.allows(
            workflow_name,
            workflow_version,
            action_id,
            WorkflowActionContext(
                engine_status=engine_status,
                delivery_status=delivery_status,
                # The repository is the authoritative snapshot/pin/expiry
                # validator; this is only a coarse matrix precondition.
                snapshot_available=True,
            ),
        ):
            raise WorkflowServiceError(
                "workflow_action_not_allowed", "continue_research is not allowed"
            )
        stable = uuid.uuid5(uuid.NAMESPACE_URL, f"deskpet:{run_id}:{idempotency_key}")
        child_run_id = str(stable)
        child_operation_id = f"research:{child_run_id}"
        snapshot_hash = self._required(str(source["snapshot_hash"]), "snapshot_hash")
        parent_operation_id = self._required(
            str(source["parent_operation_id"]), "parent_operation_id"
        )
        manifest = await self._load_research_snapshot_manifest(
            manifest_ref=self._required(str(source["manifest_ref"]), "manifest_ref"),
            expected_hash=snapshot_hash,
        )
        start = await self.run_store.get_start_snapshot(run_id)
        start_payload = start.get("start_payload") if isinstance(start, Mapping) else None
        if not isinstance(start_payload, Mapping):
            raise WorkflowServiceError(
                "invalid_start_snapshot", "parent workflow start payload is invalid"
            )
        topic = self._required(str(start_payload.get("topic") or ""), "topic")
        parent_report_ref = (
            str(source["parent_report_ref"])
            if source.get("parent_report_ref") is not None
            else await self._parent_report_ref(run_id)
        )
        if delivery_status == "partial" and not parent_report_ref:
            raise WorkflowServiceError(
                "parent_report_missing",
                "partial continuation parent has no durable report reference",
            )
        child_payload: dict[str, JsonValue] = {
            "topic": topic,
            "mode": str(start_payload.get("mode") or "standard"),
            "research_config": copy.deepcopy(dict(start_payload.get("research_config") or {})),
            "blob_root": str(start_payload.get("blob_root") or ""),
            "snapshot_hash": snapshot_hash,
            "parent_operation_id": parent_operation_id,
            "operation_id": child_operation_id,
            "continuation": True,
            "only_gaps": True,
            "continuation_snapshot": manifest,
        }
        launcher = getattr(self, "launcher", None)
        launch = getattr(launcher, "launch_existing_run", None)
        if not callable(launch):
            raise WorkflowServiceError(
                "workflow_action_unavailable", "continuation launcher is unavailable"
            )
        lineage, created = await repository.create_child_from_snapshot(
            parent_run_id=run_id,
            parent_operation_id=parent_operation_id,
            snapshot_hash=snapshot_hash,
            child_run_id=child_run_id,
            child_operation_id=child_operation_id,
            child_trace_id=uuid.uuid5(stable, "trace").hex,
            child_thread_id=uuid.uuid5(stable, "thread").hex,
            child_request_key=f"continue:{run_id}:{idempotency_key}",
            child_request_id=f"continue:{idempotency_key}",
            child_turn_id=uuid.uuid5(stable, "turn").hex,
            budget_lease_id=uuid.uuid5(stable, "budget").hex,
            start_payload=child_payload,
            parent_report_ref=parent_report_ref,
        )
        if created:
            await launch(child_run_id, start_payload=child_payload)
        return child_run_id, created, lineage

    async def retry_run_from_start(
        self,
        run_id: str,
        *,
        action_id: str,
        retry_key: str,
    ) -> dict[str, Any]:
        """Create one idempotent versioned research retry under the session lock."""

        source_run_id = self._required(run_id, "run_id")
        if action_id != "retry_from_start":
            raise WorkflowServiceError("invalid_retry_action", "retry action is not supported")
        if not _UUID_V4_RE.fullmatch(str(retry_key)):
            raise WorkflowServiceError("invalid_retry_key", "retry_key must be a lowercase UUID v4")
        await self.require_legacy_owner(source_run_id, "retry")
        initial = await self.run_store.get_start_snapshot(source_run_id)
        if initial is None:
            raise WorkflowServiceError("not_found", "workflow run was not found")
        initial_identity = initial.get("identity")
        if not isinstance(initial_identity, Mapping):
            raise WorkflowServiceError(
                "invalid_start_snapshot", "workflow start identity is invalid"
            )
        delivery_session_id = self._required(
            str(initial_identity.get("delivery_session_id") or ""), "delivery_session_id"
        )
        lock = self.session_lock(delivery_session_id)
        async with lock:
            source = await self.run_store.get_start_snapshot(source_run_id)
            if source is None:
                raise WorkflowServiceError("not_found", "workflow run was not found")
            identity_value = source.get("identity")
            if not isinstance(identity_value, Mapping):
                raise WorkflowServiceError(
                    "invalid_start_snapshot", "workflow start identity is invalid"
                )
            try:
                identity = WorkflowStartIdentity(**dict(identity_value))
            except (TypeError, ValueError) as exc:
                raise WorkflowServiceError(
                    "invalid_start_snapshot", "workflow start identity is invalid"
                ) from exc
            if identity.delivery_session_id != delivery_session_id:
                raise WorkflowServiceError(
                    "retry_session_changed", "workflow delivery session changed"
                )
            if (
                str(source.get("status") or "") != "failed"
                or str(source.get("workflow_name") or "") != "deep_research"
                or str(source.get("workflow_version") or "") not in {"v4", "v5"}
                or identity.workflow_name != "deep_research"
            ):
                raise WorkflowServiceError(
                    "workflow_retry_not_allowed",
                    "only failed supported deep_research runs can be retried from start",
                )
            delivery_ref = await self.run_store.get_session_ref(source_run_id, "delivery")
            if (
                delivery_ref is None
                or delivery_ref.get("deleted_at") is not None
                or str(delivery_ref.get("session_id") or "") != delivery_session_id
            ):
                raise WorkflowServiceError(
                    "session_deleted", "workflow delivery session is deleted"
                )
            if self._session_delivery_state_reader is None:
                raise WorkflowServiceError(
                    "session_state_unavailable", "session delivery state reader is unavailable"
                )
            delivery_state = await self._session_delivery_state_reader(delivery_session_id)
            if delivery_state.get("deleted_at") is not None:
                raise WorkflowServiceError(
                    "session_deleted", "workflow delivery session is deleted"
                )
            if int(delivery_state.get("epoch", -1)) != int(delivery_ref.get("session_epoch", -2)):
                raise WorkflowServiceError(
                    "session_epoch_mismatch", "workflow delivery session epoch changed"
                )
            launcher = getattr(self, "launcher", None)
            method = getattr(launcher, "retry_from_start_locked", None)
            if not callable(method):
                raise WorkflowServiceError(
                    "workflow_retry_unavailable", "workflow retry launcher is unavailable"
                )
            return _plain(
                await method(
                    source,
                    retry_key=retry_key,
                    session_lock=lock,
                )
            )

    async def cancel_runs_for_session(
        self,
        session_id: str,
        *,
        session_kind: str | None = None,
        reason: str = "session_deleted",
    ) -> list[dict[str, Any]]:
        """Fence every nonterminal run bound to a deleted session."""

        run_ids = await self.run_store.tombstone_session_refs(
            self._required(session_id, "session_id"),
            session_kind=session_kind,
        )
        cancelled: list[dict[str, Any]] = []
        for run_id in run_ids:
            cancelled.append(await self.cancel_run(run_id, reason=reason))
        return cancelled

    async def fork_checkpoint(
        self,
        *,
        run_id: str,
        checkpoint_id: str,
        expected_version: int,
        state_patch: Mapping[str, JsonValue] | None = None,
        fork_key: str | None = None,
        confirm_dangerous_effects: bool = False,
    ) -> dict[str, Any]:
        run_id = self._required(run_id, "run_id")
        await self.require_legacy_owner(run_id, "fork")
        method = self._forker
        if method is None:
            method = getattr(self.runner, "fork_checkpoint", None) or getattr(
                self.runner, "fork", None
            )
        if not callable(method):
            raise WorkflowServiceError(
                "workflow_fork_unavailable",
                "Checkpoint fork support is not available in the current runner",
            )
        patch = dict(state_patch or {})
        validate_json_value(patch, path="$.state_patch")
        arguments = {
            "run_id": run_id,
            "checkpoint_id": checkpoint_id,
            "expected_version": expected_version,
            "state_patch": patch,
            "fork_key": fork_key,
        }
        parameters = inspect.signature(method).parameters
        accepts_confirmation = "confirm_dangerous_effects" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        )
        if accepts_confirmation:
            arguments["confirm_dangerous_effects"] = confirm_dangerous_effects
        elif confirm_dangerous_effects:
            raise WorkflowServiceError(
                "workflow_fork_confirmation_unsupported",
                "The configured fork adapter cannot enforce dangerous effect confirmation",
            )
        result = method(**arguments)
        if inspect.isawaitable(result):
            result = await result
        plain = _plain(result)
        launcher = getattr(self, "launcher", None)
        child_run_id = plain.get("run_id") if isinstance(plain, Mapping) else None
        if child_run_id and callable(getattr(launcher, "recover_pending", None)):
            await launcher.recover_pending(only_run_ids={str(child_run_id)})
        return plain

    fork = fork_checkpoint
