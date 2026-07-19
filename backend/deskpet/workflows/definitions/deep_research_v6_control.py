"""Durable control-journal seams shared by the DeepResearch v6 nodes.

The runner exposes the committed *input* frontier through ``context.identity``.
Consequently page loading may inspect an accepted command without observing it;
the following candidate-extraction node observes the command before any new
semantic upstream work, and evidence admission owns settlement after the typed
partial frontier is committed.
"""

from __future__ import annotations

import inspect
from typing import Mapping

from ..contracts import JsonValue, WorkflowContext
from .deep_research_v5_contracts import ResearchControlCommand


async def _await(value: object) -> object:
    return await value if inspect.isawaitable(value) else value


async def poll_v6_control(
    context: WorkflowContext,
    *,
    run_id: str,
    observe: bool,
) -> ResearchControlCommand | None:
    """Read the one durable command, optionally observing it at this frontier."""

    port = context.ports.get("control")
    if port is None:
        return None
    poll = getattr(port, "poll", None)
    if not callable(poll):
        raise TypeError("workflow control port must provide poll()")
    identity = context.identity
    value = await _await(
        poll(
            run_id=run_id,
            checkpoint_ns=(identity.checkpoint_ns if observe and identity else None),
            checkpoint_id=(identity.checkpoint_id if observe and identity else None),
        )
    )
    if value is None:
        return None
    if not isinstance(value, ResearchControlCommand):
        raise TypeError("control poll must return ResearchControlCommand or None")
    if value.run_id != run_id:
        raise ValueError("control command belongs to another run")
    return value


async def settle_v6_control(
    context: WorkflowContext,
    command: ResearchControlCommand | None,
    *,
    result: Mapping[str, JsonValue],
) -> ResearchControlCommand | None:
    """CAS the observed command to settled at the committed input frontier."""

    if command is None or command.status == "settled":
        return command
    if command.status not in {"accepted", "observed"}:
        return command
    identity = context.identity
    port = context.ports.get("control")
    settle = getattr(port, "settle", None)
    if identity is None or not callable(settle):
        raise TypeError("v6 control settlement requires an identified control port")
    value = await _await(
        settle(
            command.command_id,
            checkpoint_ns=identity.checkpoint_ns,
            checkpoint_id=identity.checkpoint_id,
            result=dict(result),
        )
    )
    if not isinstance(value, ResearchControlCommand) or value.status != "settled":
        raise TypeError("control settle must return a settled ResearchControlCommand")
    return value


async def consume_v6_control(
    context: WorkflowContext,
    *,
    run_id: str,
    result: Mapping[str, JsonValue],
) -> ResearchControlCommand | None:
    """Consume the settled command while committing the terminal manifest node."""

    command = await poll_v6_control(context, run_id=run_id, observe=False)
    if command is None or command.status != "settled":
        return command
    identity = context.identity
    port = context.ports.get("control")
    consume = getattr(port, "consume", None)
    if identity is None or not callable(consume):
        raise TypeError("v6 control consumption requires an identified control port")
    value = await _await(
        consume(
            command.command_id,
            checkpoint_ns=identity.checkpoint_ns,
            checkpoint_id=identity.checkpoint_id,
            result=dict(result),
        )
    )
    if not isinstance(value, ResearchControlCommand) or value.status != "consumed":
        raise TypeError("control consume must return a consumed ResearchControlCommand")
    return value


__all__ = ["consume_v6_control", "poll_v6_control", "settle_v6_control"]
