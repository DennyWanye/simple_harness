from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from deskpet.execution.contracts import (
    ActorContext,
    DecisionOpen,
    DecisionSignal,
    DecisionStatus,
    OutcomeStatus,
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunEventCandidate,
    RunRef,
    RunStatus,
    fingerprint_json,
    root_idempotency_key,
)
from deskpet.workflows.store import SqliteExecutionUnitOfWork


CAPABILITY_HASH = fingerprint_json({"tools": ["read", "write"]})


def _actor() -> ActorContext:
    return ActorContext(
        principal_id="principal-decisions",
        session_id="session-decisions",
        auth_epoch=1,
        root_run_id="run-decisions",
    )


def _spec() -> RunCreate:
    return RunCreate(
        run_id="run-decisions",
        idempotency_key=root_idempotency_key(
            "session-decisions", "request-decisions", "turn-decisions"
        ),
        context=RunContext(
            session_id="session-decisions",
            root_run_id="run-decisions",
            parent_run_id=None,
            request_id="request-decisions",
            turn_id="turn-decisions",
            venue="text",
            workspace={},
            capability_hash=CAPABILITY_HASH,
            provider_plan={"model": "fixture"},
            trace_id="trace-decisions",
            principal_id="principal-decisions",
            auth_epoch=1,
        ),
        payload_fingerprint=fingerprint_json({"prompt": "continue?"}),
        capability_fingerprint=CAPABILITY_HASH,
        driver_kind="react",
        profile_key="react.default",
        persistence_level=PersistenceLevel.DURABLE,
        status=RunStatus.RUNNING,
    )


def _decision(decision_id: str = "decision-1") -> DecisionOpen:
    return DecisionOpen(
        decision_id=decision_id,
        run_id="run-decisions",
        nonce=f"nonce-{decision_id}",
        kind="clarification",
        prompt_schema_version=1,
        prompt={"question": "continue?"},
        expires_at=None,
    )


@pytest.mark.asyncio
async def test_single_uow_decision_survives_restart_and_is_fenced(tmp_path) -> None:
    path = tmp_path / "workflow.db"
    first = SqliteExecutionUnitOfWork(path)
    await first.initialize()
    run = await first.create(_spec())
    actor = _actor()
    ref = RunRef("run-decisions", "session-decisions")
    opened, _ = await first.commit_decision(
        _decision(), actor, expected_run_version=run.record.version
    )
    assert opened.status is DecisionStatus.OPEN

    restarted = SqliteExecutionUnitOfWork(path)
    observed = await restarted.get_decision("decision-1", ref=ref, actor=actor)
    resolved, grant = await restarted.commit_decision(
        DecisionSignal(
            decision_id="decision-1",
            run_id="run-decisions",
            expected_session_id="session-decisions",
            nonce="nonce-decision-1",
            expected_version=observed.decision_version,
            allow=True,
            response_schema_version=1,
            response={"answer": "yes"},
        ),
        actor,
    )
    assert resolved.status is DecisionStatus.ALLOWED
    assert grant is None

    with pytest.raises(Exception):
        await restarted.commit_decision(
            DecisionSignal(
                decision_id="decision-1",
                run_id="run-decisions",
                expected_session_id="session-decisions",
                nonce="wrong",
                expected_version=0,
                allow=True,
                response_schema_version=1,
                response={"answer": "again"},
            ),
            actor,
        )


@pytest.mark.asyncio
async def test_run_context_owner_identity_survives_restart_and_idempotent_replay(
    tmp_path,
) -> None:
    path = tmp_path / "workflow.db"
    base = _spec()
    spec = replace(
        base,
        context=replace(
            base.context,
            owner_key="companion:profile-1:4",
            profile_generation=4,
            binding_epoch=7,
        ),
    )

    first = SqliteExecutionUnitOfWork(path)
    await first.initialize()
    created = await first.create(spec)
    assert created.created is True

    restarted = SqliteExecutionUnitOfWork(path)
    await restarted.initialize()
    replayed = await restarted.create(spec)

    assert replayed.created is False
    assert replayed.record.spec.context.owner_key == "companion:profile-1:4"
    assert replayed.record.spec.context.profile_generation == 4
    assert replayed.record.spec.context.binding_epoch == 7


@pytest.mark.asyncio
async def test_cancel_open_decisions_uses_same_uow_owner(tmp_path) -> None:
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db")
    await uow.initialize()
    created = await uow.create(_spec())
    actor = _actor()
    ref = RunRef("run-decisions", "session-decisions")
    await uow.commit_decision(
        _decision("decision-cancel"), actor,
        expected_run_version=created.record.version,
    )
    current = await uow.query(ref, actor)
    cancelling = await uow.commit_run_outcome(
        ref.run_id,
        expected_version=current.version,
        cancel_reason="user_stop",
        event=RunEventCandidate(
            event_key="cancel:decision-test",
            kind="cancel_requested",
            status=OutcomeStatus.CANCEL_REQUESTED,
            driver_kind="react",
            payload={"reason": "user_stop"},
        ),
    )
    cancelled = await uow.cancel_open_decisions(
        ref, actor, expected_run_version=cancelling.version
    )
    assert [item.status for item in cancelled] == [DecisionStatus.EXPIRED]


def test_removed_decision_and_effect_facades_are_not_constructed() -> None:
    root = Path(__file__).parents[2] / "deskpet"
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.rglob("*.py")
    )
    assert "class DecisionStore" not in production
    assert "class DecisionWakeupCache" not in production
    assert "class SqliteExecutionEffectJournal" not in production
