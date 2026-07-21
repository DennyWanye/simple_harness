from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from deskpet.execution.contracts import (
    ActorContext,
    ContractValidationError,
    LiveCursor,
    OutcomeStatus,
    RunContext,
    RunEvent,
    RunEventCandidate,
    delegate_idempotency_key,
    fingerprint_json,
    root_idempotency_key,
    team_idempotency_key,
    workflow_idempotency_key,
)


def _context() -> RunContext:
    capability_hash = fingerprint_json({"tools": ["read"]})
    return RunContext(
        session_id="session",
        root_run_id="run",
        parent_run_id=None,
        request_id="request",
        turn_id="turn",
        venue="text",
        workspace={"root": "F:/workspace"},
        capability_hash=capability_hash,
        provider_plan={"model": "fixture"},
        trace_id="trace",
        principal_id="principal",
    )


def test_contracts_are_frozen_and_deserialization_fails_closed() -> None:
    context = _context()
    with pytest.raises(FrozenInstanceError):
        context.session_id = "other"  # type: ignore[misc]

    encoded = context.to_dict()
    assert RunContext.from_dict(encoded) == context
    with pytest.raises(ContractValidationError, match="unknown"):
        RunContext.from_dict({**encoded, "model_supplied_session": "forged"})
    with pytest.raises(ContractValidationError, match="schema_version"):
        RunContext.from_dict({**encoded, "schema_version": 2})

    actor = ActorContext(
        principal_id="principal", session_id="session", auth_epoch=0
    )
    with pytest.raises(ContractValidationError, match="unknown"):
        ActorContext.from_dict({**actor.to_dict(), "future_field": True})


def test_all_run_idempotency_namespaces_have_stable_shapes() -> None:
    assert root_idempotency_key("s", "r", "t") == "root:s:r:t"
    assert delegate_idempotency_key("parent", "command", "profile") == (
        "delegate:parent:command:profile"
    )
    assert team_idempotency_key("team", "task", 4) == "team:team:task:4"
    assert workflow_idempotency_key("parent", "node", "slot") == (
        "workflow:parent:node:slot"
    )
    with pytest.raises(ContractValidationError):
        team_idempotency_key("team", "task", -1)


def test_run_event_order_domains_are_explicit_and_mutually_exclusive() -> None:
    candidate = RunEventCandidate(
        event_key="token-1",
        kind="assistant.delta",
        status=OutcomeStatus.WAITING,
        driver_kind="react",
    )
    durable = RunEvent(
        event_id="event-durable",
        run_id="run",
        root_run_id="run",
        session_id="session",
        durable_seq=1,
        candidate=candidate,
        created_at=1.0,
    )
    assert durable.durable is True
    assert durable.live_cursor is None

    live = RunEvent(
        event_id="event-live",
        run_id="run",
        root_run_id="run",
        session_id="session",
        durable_seq=None,
        live_cursor=LiveCursor(stream_epoch="stream-1", live_seq=1),
        candidate=candidate,
        created_at=1.0,
    )
    assert live.durable is False
    with pytest.raises(ContractValidationError, match="exactly one"):
        RunEvent(
            event_id="event-invalid",
            run_id="run",
            root_run_id="run",
            session_id="session",
            durable_seq=1,
            live_cursor=LiveCursor(stream_epoch="stream-1", live_seq=1),
            candidate=candidate,
            created_at=1.0,
        )


def test_execution_package_has_no_workflow_dependency() -> None:
    package = Path(__file__).parents[2] / "deskpet" / "execution"
    for source_path in package.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    not alias.name.startswith("deskpet.workflows")
                    for alias in node.names
                ), source_path
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("deskpet.workflows"), source_path
