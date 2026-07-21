"""Test-only bridge from TeamStore domain outbox to generic child commands."""

from __future__ import annotations

import json

from deskpet.agent.team.team_store import TeamChildRunCommand, TeamStore
from deskpet.execution.contracts import (
    ActorContext,
    ChildCommandIntent,
    LegacyRunProjection,
    RunRef,
    TERMINAL_RUN_STATUSES,
    canonical_json,
    thaw_json,
)
from deskpet.execution.ports import ExecutionUnitOfWork


class TeamChildRunReconciler:
    """Replay frozen Team intents and project authoritative child terminals."""

    def __init__(self, store: TeamStore, uow: ExecutionUnitOfWork) -> None:
        self._store = store
        self._uow = uow
        self.last_errors: tuple[str, ...] = ()

    @staticmethod
    def _intent(command: TeamChildRunCommand) -> ChildCommandIntent:
        if command.intent_payload_json is None:
            raise ValueError("legacy team child outbox has no frozen intent payload")
        raw = json.loads(command.intent_payload_json)
        intent = ChildCommandIntent.from_dict(raw)
        if (
            intent.operation_id != command.operation_id
            or intent.command_id != command.operation_id
            or intent.child_run_id != command.child_run_id
        ):
            raise ValueError("team outbox identity differs from frozen child intent")
        return intent

    async def reconcile_commands_once(
        self, team_id: str, *, limit: int = 100
    ) -> None:
        errors: list[str] = []
        for command in await self._store.pending_child_commands(team_id, limit=limit):
            try:
                intent = self._intent(command)
                await self._uow.commit_child_command(intent)
                await self._store.acknowledge_child_command(
                    team_id, command.operation_id
                )
            except Exception as exc:
                errors.append(
                    f"{command.operation_id}:{type(exc).__name__}:{exc}"
                )
        self.last_errors = tuple(errors)

    async def reconcile_terminals_once(
        self, team_id: str, *, limit: int = 100
    ) -> None:
        errors: list[str] = []
        for _, domain_command in await self._store.child_commands_for_terminal(
            team_id, limit=limit
        ):
            try:
                durable_command = await self._uow.get_child_command(
                    domain_command.operation_id
                )
                if durable_command is None:
                    raise ValueError("generic child command is missing")
                intent = self._intent(domain_command)
                if durable_command.intent.intent_fingerprint != intent.intent_fingerprint:
                    raise ValueError("generic child command differs from frozen Team intent")
                context = intent.child_spec.context
                actor = context.actor()
                child = await self._uow.query(
                    RunRef(intent.child_run_id, context.session_id), actor
                )
                if isinstance(child, LegacyRunProjection):
                    raise ValueError("Team child resolved to a legacy projection")
                if child.status not in TERMINAL_RUN_STATUSES:
                    continue
                if child.terminal_event_id is None:
                    raise ValueError("terminal Team child has no terminal event")
                event = await self._uow.get_event(child.terminal_event_id)
                payload = thaw_json(event.candidate.payload)
                result = payload.get("text") if isinstance(payload, dict) else None
                if not isinstance(result, str) and isinstance(payload, dict):
                    result = payload.get("content")
                if not isinstance(result, str):
                    result = canonical_json(payload)
                await self._store.apply_child_terminal(
                    team_id,
                    domain_command.operation_id,
                    terminal_event_id=child.terminal_event_id,
                    child_status=child.status.value,
                    result=result,
                )
            except Exception as exc:
                errors.append(
                    f"{domain_command.operation_id}:{type(exc).__name__}:{exc}"
                )
        self.last_errors = tuple(errors)
