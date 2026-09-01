# SPDX-License-Identifier: BUSL-1.1

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from simple_harness import ContextRouteOrigin

from deskpet.execution.foreground_queue import (
    AdmissionReceipt,
    ClaimedExecution,
    ContextLineage,
    ForegroundRunSnapshot,
    PreparationCandidate,
    PreparationDraftReceipt,
    RunState,
)
from deskpet.execution.foreground_runtime import (
    AuthenticatedTerminalObservation,
    BoundProviderAuthority,
    ForegroundRuntimeError,
    ForegroundRuntimeExecutionAuthority,
    FrozenContextAuthority,
    FrozenProviderAuthority,
    FrozenToolAuthority,
)
from deskpet.sdk_adapters.ingress import IngressStartReceipt
from deskpet.task_scope.protocol import canonical_hash, canonical_json


SUBJECT = "actor-1"


def _candidate(index: int) -> PreparationCandidate:
    payload = {
        "schema_version": 1,
        "subject": SUBJECT,
        "turn_id": f"turn-{index}",
        "primary_conversation_id": "conversation-1",
        "task_scope_id": f"scope-{index}",
        "evidence_id": f"evidence-{index}",
        "evidence_hash": f"{index}" * 64,
        "enqueue_sequence": index,
        "turn_hash": f"{index + 2}" * 64,
        "turn": {
            "schema_version": 1,
            "subject": SUBJECT,
            "primary_conversation_id": "conversation-1",
            "task_scope_id": f"scope-{index}",
            "evidence_id": f"evidence-{index}",
            "evidence_hash": f"{index}" * 64,
            "idempotency_key": f"enqueue-{index}",
            "payload": {"schema_version": 1, "text": f"turn {index}"},
        },
        "binding_set_revision": 1,
        "binding_set_receipt_id": f"binding-{index}",
        "binding_set_receipt_hash": f"{index + 5}" * 64,
    }
    return PreparationCandidate(
        subject=SUBJECT,
        turn_id=f"turn-{index}",
        primary_conversation_id="conversation-1",
        task_scope_id=f"scope-{index}",
        evidence_id=f"evidence-{index}",
        evidence_hash=f"{index}" * 64,
        enqueue_sequence=index,
        turn_hash=f"{index + 2}" * 64,
        binding_set_revision=1,
        binding_set_receipt_id=f"binding-{index}",
        binding_set_receipt_hash=f"{index + 5}" * 64,
        candidate_hash=canonical_hash(payload),
        candidate_json=canonical_json(payload),
    )


def _snapshot(
    candidate: PreparationCandidate,
    *,
    host_run_id: str,
    sdk_run_id: str | None = None,
    owner_id: str = "owner-1",
    generation: int = 1,
    state: RunState | None = None,
) -> ForegroundRunSnapshot:
    return ForegroundRunSnapshot(
        host_run_id,
        sdk_run_id,
        SUBJECT,
        candidate.primary_conversation_id,
        candidate.turn_id,
        candidate.task_scope_id,
        candidate.binding_set_revision,
        candidate.binding_set_receipt_id,
        candidate.binding_set_receipt_hash,
        f"context-{candidate.turn_id}",
        1,
        "c" * 64,
        state or (RunState.CLAIMED if sdk_run_id is None else RunState.RUNNING),
        None,
        owner_id,
        generation,
        10_000_000_000.0,
        "d" * 64,
        "e" * 64,
    )


class _Store:
    def __init__(self, candidates: list[PreparationCandidate]) -> None:
        self.candidates = candidates
        self.active: ForegroundRunSnapshot | None = None
        self.claimed: dict[str, ClaimedExecution] = {}
        self.start_observations: list[str] = []
        self.prior_start_outcomes: tuple[str, ...] = ()
        self.terminals: list[str] = []
        self.closed_leases: list[tuple[str, int]] = []

    async def current_snapshot(self, subject: str):  # type: ignore[no-untyped-def]
        assert subject == SUBJECT
        return self.active

    async def read_next_preparation_candidate(self, subject: str):  # type: ignore[no-untyped-def]
        assert subject == SUBJECT
        return self.candidates[0] if self.candidates else None

    async def prepare_candidate(self, **kwargs):  # type: ignore[no-untyped-def]
        candidate = self.candidates[0]
        assert kwargs["expected_candidate_hash"] == candidate.candidate_hash
        return PreparationDraftReceipt(
            f"draft-{candidate.turn_id}",
            SUBJECT,
            candidate.turn_id,
            candidate.candidate_hash,
            f"context-{candidate.turn_id}",
            1,
            "c" * 64,
            "a" * 64,
            candidate.candidate_json,
        )

    async def claim_next(self, **kwargs):  # type: ignore[no-untyped-def]
        candidate = self.candidates[0]
        host_run_id = f"host-{candidate.turn_id}"
        self.active = _snapshot(candidate, host_run_id=host_run_id)
        self.claimed[host_run_id] = ClaimedExecution(
            host_run_id,
            kwargs["owner_id"],
            1,
            kwargs["preparation_draft_id"],
            kwargs["preparation_draft_hash"],
            candidate,
            "d" * 64,
            "f" * 64,
        )
        return AdmissionReceipt(
            f"admission-{candidate.turn_id}",
            "1" * 64,
            host_run_id,
            candidate.turn_id,
            SUBJECT,
            candidate.enqueue_sequence,
            RunState.CLAIMED,
            kwargs["owner_id"],
            1,
            10_000_000_000.0,
            "d" * 64,
            False,
        )

    async def read_claimed_execution(self, **kwargs):  # type: ignore[no-untyped-def]
        return self.claimed[kwargs["host_run_id"]]

    async def record_execution_preparation(self, **kwargs):  # type: ignore[no-untyped-def]
        return kwargs

    async def record_start_intent(self, **kwargs):  # type: ignore[no-untyped-def]
        return kwargs

    async def bind_sdk_run(self, **kwargs):  # type: ignore[no-untyped-def]
        assert self.active is not None
        self.active = _snapshot(
            self.claimed[kwargs["host_run_id"]].candidate,
            host_run_id=kwargs["host_run_id"],
            sdk_run_id=kwargs["sdk_run_id"],
        )
        return kwargs

    async def record_start_observation(self, **kwargs):  # type: ignore[no-untyped-def]
        self.start_observations.append(kwargs["outcome"])
        return kwargs

    async def read_start_observation_outcomes(self, **kwargs):  # type: ignore[no-untyped-def]
        return self.prior_start_outcomes

    async def record_sdk_started(self, **kwargs):  # type: ignore[no-untyped-def]
        return self.active

    async def pending_signals(self, host_run_id: str):  # type: ignore[no-untyped-def]
        return ()

    async def heartbeat(self, **kwargs):  # type: ignore[no-untyped-def]
        return kwargs

    async def close_current_lease(self, **kwargs):  # type: ignore[no-untyped-def]
        self.closed_leases.append(
            (kwargs["host_run_id"], kwargs["generation"])
        )
        return kwargs

    async def record_reconciliation(self, **kwargs):  # type: ignore[no-untyped-def]
        return kwargs

    async def record_sdk_terminal(self, **kwargs):  # type: ignore[no-untyped-def]
        self.terminals.append(kwargs["host_run_id"])
        candidate = self.claimed[kwargs["host_run_id"]].candidate
        assert self.candidates[0] == candidate
        self.candidates.pop(0)
        self.active = None
        return kwargs


class _Context:
    def __init__(self) -> None:
        self.routes = []

    async def draft_lineage(self, candidate):  # type: ignore[no-untyped-def]
        return ContextLineage(f"context-{candidate.turn_id}", 1, "c" * 64)

    async def prepare(self, **kwargs):  # type: ignore[no-untyped-def]
        candidate = kwargs["claimed"].candidate
        return FrozenContextAuthority(
            f"context:{candidate.turn_id}",
            "2" * 64,
            f"snapshot-{candidate.turn_id}",
            ({"role": "user", "content": f"turn {candidate.enqueue_sequence}"},),
            f"turn {candidate.enqueue_sequence}",
            (f"resume-{candidate.turn_id}",),
        )

    async def verify_initial_route(self, receipt):  # type: ignore[no-untyped-def]
        assert receipt.schema_version == 3
        assert receipt.origin is ContextRouteOrigin.HOST_INITIAL
        assert receipt.host_authority_hash == "f" * 64
        self.routes.append(receipt)


@dataclass
class _Binding:
    catalog_generation: int = 7
    catalog_fingerprint: str = "catalog-1"
    budget_fingerprint: str = "budget-1"

    def to_record(self):  # type: ignore[no-untyped-def]
        return {"schema_version": 1, "catalog_generation": 7}


class _Provider:
    def __init__(self) -> None:
        self.terminal = []

    async def freeze(self, **kwargs):  # type: ignore[no-untyped-def]
        return FrozenProviderAuthority(
            "provider:1", "3" * 64, "provider", "incarnation", 1, 1,
            "model", {}, 4096,
        )

    async def bind(self, **kwargs):  # type: ignore[no-untyped-def]
        return BoundProviderAuthority("provider-binding:1", "4" * 64, _Binding())

    def mark_terminal(self, sdk_run_id: str, state: str) -> None:
        self.terminal.append((sdk_run_id, state))


class _Tools:
    def __init__(self) -> None:
        self.terminal = []

    async def freeze(self, **kwargs):  # type: ignore[no-untyped-def]
        return FrozenToolAuthority(
            "tools:1",
            "5" * 64,
            {"generation": 7, "content_fingerprint": "catalog-1", "tool_names": []},
            {"schema_version": 1},
        )

    def mark_terminal(self, sdk_run_id: str, state: str) -> None:
        self.terminal.append((sdk_run_id, state))


class _Ingress:
    def __init__(self) -> None:
        self.starts = []
        self.records = {}

    async def start(self, **kwargs):  # type: ignore[no-untyped-def]
        from deskpet.sdk_adapters.ingress import SdkRuntimeIngress

        run_id = SdkRuntimeIngress._compute_run_id(  # noqa: SLF001
            kwargs["session_id"], kwargs["request_id"], kwargs["turn_id"]
        ).value
        self.starts.append(kwargs)
        self.records[run_id] = type(
            "Record", (), {"state": type("State", (), {"value": "completed"})(), "version": 1}
        )()
        return IngressStartReceipt(run_id, 1, kwargs["session_id"], kwargs["request_id"])

    def query(self, run_id: str):  # type: ignore[no-untyped-def]
        return self.records.get(run_id)


class _Terminal:
    async def observe(self, **kwargs):  # type: ignore[no-untyped-def]
        return AuthenticatedTerminalObservation(
            RunState.COMPLETED,
            f"sdk-terminal:{kwargs['sdk_run_id']}",
            "6" * 64,
        )


@pytest.mark.asyncio
async def test_fifo_turns_each_start_exactly_one_host_initial_sdk_run() -> None:
    store = _Store([_candidate(1), _candidate(2)])
    ingress = _Ingress()
    context = _Context()
    runtime = ForegroundRuntimeExecutionAuthority(
        store=store,  # type: ignore[arg-type]
        subject=SUBJECT,
        owner_id="owner-1",
        ingress=ingress,  # type: ignore[arg-type]
        context=context,
        provider=_Provider(),
        tools=_Tools(),
        terminal_observer=_Terminal(),
    )
    await asyncio.gather(
        runtime.after_enqueue(subject=SUBJECT),
        runtime.after_enqueue(subject=SUBJECT),
    )
    await runtime.drain()

    assert runtime.last_error is None
    assert len(ingress.starts) == 2
    assert len({item["initial_route_receipt"].run_id for item in ingress.starts}) == 2
    assert [route.task_scope_id for route in context.routes] == ["scope-1", "scope-2"]
    assert store.start_observations == ["RETURNED", "RETURNED"]
    assert store.terminals == ["host-turn-1", "host-turn-2"]


@pytest.mark.asyncio
async def test_restart_with_durable_binding_queries_sdk_and_never_starts_again() -> None:
    candidate = _candidate(1)
    store = _Store([])
    host_run_id = "host-turn-1"
    claimed = ClaimedExecution(
        host_run_id,
        "owner-1",
        1,
        "draft-turn-1",
        "a" * 64,
        candidate,
        "d" * 64,
        "f" * 64,
    )
    store.claimed[host_run_id] = claimed
    ingress = _Ingress()
    from deskpet.sdk_adapters.ingress import SdkRuntimeIngress

    session_id = "foreground-execution-" + __import__("hashlib").sha256(
        host_run_id.encode()
    ).hexdigest()
    request_id = "foreground-request-turn-1"
    sdk_run_id = SdkRuntimeIngress._compute_run_id(  # noqa: SLF001
        session_id, request_id, "turn-1"
    ).value
    store.active = _snapshot(
        candidate,
        host_run_id=host_run_id,
        sdk_run_id=sdk_run_id,
    )
    ingress.records[sdk_run_id] = type(
        "Record", (), {"state": type("State", (), {"value": "waiting"})(), "version": 4}
    )()

    class WaitingTerminal:
        async def observe(self, **kwargs):  # type: ignore[no-untyped-def]
            return None

    runtime = ForegroundRuntimeExecutionAuthority(
        store=store,  # type: ignore[arg-type]
        subject=SUBJECT,
        owner_id="owner-1",
        ingress=ingress,  # type: ignore[arg-type]
        context=_Context(),
        provider=_Provider(),
        tools=_Tools(),
        terminal_observer=WaitingTerminal(),
    )
    await runtime.after_enqueue(subject=SUBJECT)
    await runtime.drain()

    assert runtime.last_error is None
    assert ingress.starts == []
    assert store.start_observations == ["QUERY_FOUND"]


@pytest.mark.asyncio
async def test_restart_after_bind_before_start_retries_same_sdk_identity_once() -> None:
    candidate = _candidate(1)
    store = _Store([candidate])
    host_run_id = "host-turn-1"
    claimed = ClaimedExecution(
        host_run_id,
        "owner-1",
        1,
        "draft-turn-1",
        "a" * 64,
        candidate,
        "d" * 64,
        "f" * 64,
    )
    store.claimed[host_run_id] = claimed
    ingress = _Ingress()
    from deskpet.sdk_adapters.ingress import SdkRuntimeIngress

    session_id = "foreground-execution-" + __import__("hashlib").sha256(
        host_run_id.encode()
    ).hexdigest()
    request_id = "foreground-request-turn-1"
    sdk_run_id = SdkRuntimeIngress._compute_run_id(  # noqa: SLF001
        session_id, request_id, "turn-1"
    ).value
    store.active = _snapshot(
        candidate,
        host_run_id=host_run_id,
        sdk_run_id=sdk_run_id,
        state=RunState.CLAIMED,
    )

    runtime = ForegroundRuntimeExecutionAuthority(
        store=store,  # type: ignore[arg-type]
        subject=SUBJECT,
        owner_id="owner-1",
        ingress=ingress,  # type: ignore[arg-type]
        context=_Context(),
        provider=_Provider(),
        tools=_Tools(),
        terminal_observer=_Terminal(),
    )
    await runtime.after_enqueue(subject=SUBJECT)
    await runtime.drain()

    assert runtime.last_error is None
    assert len(ingress.starts) == 1
    assert store.start_observations == ["QUERY_MISSING", "RETURNED"]
    assert ingress.starts[0]["initial_route_receipt"].run_id == sdk_run_id


@pytest.mark.asyncio
async def test_start_raise_after_sdk_commit_is_reconciled_without_second_start() -> None:
    store = _Store([_candidate(1)])

    class RaiseAfterCommit(_Ingress):
        async def start(self, **kwargs):  # type: ignore[no-untyped-def]
            await super().start(**kwargs)
            raise RuntimeError("transport_lost_after_commit")

    ingress = RaiseAfterCommit()
    runtime = ForegroundRuntimeExecutionAuthority(
        store=store,  # type: ignore[arg-type]
        subject=SUBJECT,
        owner_id="owner-1",
        ingress=ingress,  # type: ignore[arg-type]
        context=_Context(),
        provider=_Provider(),
        tools=_Tools(),
        terminal_observer=_Terminal(),
    )
    await runtime.after_enqueue(subject=SUBJECT)
    await runtime.drain()

    assert runtime.last_error is None
    assert len(ingress.starts) == 1
    assert store.start_observations == ["RAISED", "QUERY_FOUND"]
    assert store.terminals == ["host-turn-1"]


@pytest.mark.asyncio
async def test_subject_mismatch_is_rejected_before_driver_creation() -> None:
    runtime = ForegroundRuntimeExecutionAuthority(
        store=_Store([]),  # type: ignore[arg-type]
        subject=SUBJECT,
        owner_id="owner-1",
        ingress=_Ingress(),  # type: ignore[arg-type]
        context=_Context(),
        provider=_Provider(),
        tools=_Tools(),
        terminal_observer=_Terminal(),
    )
    with pytest.raises(ForegroundRuntimeError) as exc:
        await runtime.after_enqueue(subject="other")
    assert exc.value.code == "foreground_runtime_subject_mismatch"


@pytest.mark.asyncio
async def test_close_expires_owned_active_lease_for_immediate_restart() -> None:
    candidate = _candidate(1)
    store = _Store([])
    store.active = _snapshot(candidate, host_run_id="host-turn-1")
    runtime = ForegroundRuntimeExecutionAuthority(
        store=store,  # type: ignore[arg-type]
        subject=SUBJECT,
        owner_id="owner-1",
        ingress=_Ingress(),  # type: ignore[arg-type]
        context=_Context(),
        provider=_Provider(),
        tools=_Tools(),
        terminal_observer=_Terminal(),
    )
    runtime._driver = asyncio.create_task(asyncio.sleep(0))  # noqa: SLF001

    await runtime.close()

    assert store.closed_leases == [("host-turn-1", 1)]


@pytest.mark.asyncio
async def test_human_lifespan_registers_one_real_runtime_and_legacy_does_not(
    tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    import main
    from deskpet.memory.schema import StartupCompositionMode

    monkeypatch.setattr(main, "_state_db_path", tmp_path / "state.db")
    monkeypatch.setattr(main._paths, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "_sdk_ingress", _Ingress())
    monkeypatch.setattr(main, "_sdk_runtime_stack", object())
    monkeypatch.setattr(main, "_provider_registry", object())
    monkeypatch.setattr(main, "_sdk_provider_binding_resolver", object())
    monkeypatch.setattr(
        main,
        "_sdk_runtime_catalog",
        {"generation": 1, "content_fingerprint": "catalog", "tool_names": []},
    )
    monkeypatch.setattr(main, "_sdk_runtime_tool_inventory", ())
    monkeypatch.setattr(main, "_sdk_tool_authority_registry", object())
    monkeypatch.setattr(main.service_context, "capability_store", object())
    epoch = type("Epoch", (), {"composition_mode": StartupCompositionMode.HUMAN})()

    await main._activate_human_memory_host_ports(epoch)
    scheduler = main.service_context.get("human_memory_foreground_scheduler_wake")
    runtime = main.service_context.get(
        "human_memory_foreground_runtime_execution_authority"
    )
    assert scheduler is runtime
    assert isinstance(runtime, ForegroundRuntimeExecutionAuthority)
    assert runtime.subject == "deskpet-local-owner-v1"
    await runtime.close()

    legacy = type(
        "Epoch", (), {"composition_mode": StartupCompositionMode.LEGACY}
    )()
    await main._activate_human_memory_host_ports(legacy)
    assert main.service_context.get("human_memory_foreground_scheduler_wake") is None
    assert (
        main.service_context.get(
            "human_memory_foreground_runtime_execution_authority"
        )
        is None
    )
