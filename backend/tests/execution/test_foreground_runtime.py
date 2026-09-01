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
    ControlKind,
    EffectBoundary,
    ForegroundQueueError,
    ForegroundRunSnapshot,
    PreparationCandidate,
    PreparationDraftReceipt,
    RunState,
    SignalEnvelope,
)
from deskpet.execution.foreground_runtime import (
    AuthenticatedTerminalObservation,
    BoundProviderAuthority,
    ForegroundEffectAdmissionGate,
    ForegroundRuntimeError,
    ForegroundRuntimeExecutionAuthority,
    FrozenContextAuthority,
    FrozenProviderAuthority,
    FrozenToolAuthority,
    resolve_host_terminal,
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
        self.current_generation = 1
        self.effect_admissions: list[str] = []
        self.signals: list[SignalEnvelope] = []
        self.acks: list[tuple[str, str]] = []
        self.pause_outcomes: list[str] = []

    async def authorize_effect(self, **kwargs):  # type: ignore[no-untyped-def]
        if int(kwargs["generation"]) != self.current_generation:
            raise ForegroundQueueError("foreground_generation_stale")
        self.effect_admissions.append(str(kwargs.get("boundary")))
        return kwargs

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
            f"admission-{host_run_id}",
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
        acked = {signal_id for signal_id, _ in self.acks}
        return tuple(
            signal for signal in self.signals if signal.signal_id not in acked
        )

    async def acknowledge_signal(self, **kwargs):  # type: ignore[no-untyped-def]
        self.acks.append((kwargs["signal_id"], kwargs["sdk_signal_id"]))
        return kwargs

    async def record_pause_outcome(self, **kwargs):  # type: ignore[no-untyped-def]
        self.pause_outcomes.append(kwargs["idempotency_key"])
        return kwargs

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
        claimed = kwargs["claimed"]
        candidate = claimed.candidate
        return FrozenContextAuthority(
            claimed.host_run_id,
            kwargs["sdk_run_id"],
            claimed.owner_id,
            claimed.generation,
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
        claimed = kwargs["claimed"]
        return FrozenProviderAuthority(
            claimed.host_run_id, kwargs["sdk_run_id"], claimed.owner_id,
            claimed.generation,
            "provider:1", "3" * 64, "provider", "incarnation", 1, 1,
            "model", {}, 4096,
        )

    async def bind(self, **kwargs):  # type: ignore[no-untyped-def]
        frozen = kwargs["frozen"]
        return BoundProviderAuthority(
            frozen.host_run_id, frozen.sdk_run_id, frozen.owner_id,
            frozen.generation,
            "provider-binding:1", "4" * 64, _Binding(),
        )

    def mark_terminal(self, sdk_run_id: str, state: str) -> None:
        self.terminal.append((sdk_run_id, state))


class _Tools:
    def __init__(self) -> None:
        self.terminal = []

    async def freeze(self, **kwargs):  # type: ignore[no-untyped-def]
        claimed = kwargs["claimed"]
        return FrozenToolAuthority(
            claimed.host_run_id,
            kwargs["sdk_run_id"],
            claimed.owner_id,
            claimed.generation,
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
        self.cancels: list[str] = []
        self.signals: list[dict] = []

    async def cancel(self, run_id: str) -> None:
        self.cancels.append(run_id)

    async def signal(self, **kwargs):  # type: ignore[no-untyped-def]
        self.signals.append(kwargs)
        return type(
            "Delivery", (), {"delivery_id": f"sdk-delivery:{kwargs['signal_id']}"}
        )()

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
        f"admission-{host_run_id}",
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
        f"admission-{host_run_id}",
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


def _signal_envelope(
    signal_id: str,
    *,
    host_run_id: str,
    sdk_run_id: str,
    kind: ControlKind,
    generation: int = 1,
) -> SignalEnvelope:
    return SignalEnvelope(
        signal_id,
        f"control-{signal_id}",
        host_run_id,
        sdk_run_id,
        generation,
        kind,
        "7" * 64,
    )


async def _wait_until(condition, *, timeout: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached before timeout")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_reclaim_between_bind_and_start_blocks_sdk_start_side_effect() -> None:
    class ReclaimAfterBind(_Store):
        async def bind_sdk_run(self, **kwargs):  # type: ignore[no-untyped-def]
            result = await super().bind_sdk_run(**kwargs)
            # A competing owner reclaims the lease after the durable SDK
            # binding commits but before this worker reaches ingress.start.
            self.current_generation = 2
            return result

    store = ReclaimAfterBind([_candidate(1)])
    ingress = _Ingress()
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

    assert isinstance(runtime.last_error, ForegroundQueueError)
    assert runtime.last_error.code == "foreground_generation_stale"
    assert ingress.starts == []
    assert store.terminals == []


@pytest.mark.asyncio
async def test_reclaim_between_signal_read_and_send_blocks_sdk_control() -> None:
    store = _Store([])
    host_run_id = "host-turn-1"
    sdk_run_id = "sdk-run-1"
    store.signals = [
        _signal_envelope(
            "sig-cancel",
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            kind=ControlKind.CANCEL,
        )
    ]
    # Lease reclaimed after the durable signal read, before the SDK send.
    store.current_generation = 2
    ingress = _Ingress()
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
    with pytest.raises(ForegroundQueueError) as exc:
        await runtime._deliver_controls(
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            generation=1,
        )
    assert exc.value.code == "foreground_generation_stale"
    assert ingress.cancels == []
    assert ingress.signals == []
    assert store.acks == []


@pytest.mark.asyncio
async def test_reclaim_between_tool_admission_and_dispatch_blocks_effect(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    from simple_harness import RequestId, RunId
    from simple_harness.tools import ToolContext
    from simple_harness.tools.contracts import CancellationToken
    from simple_harness.tools.executor import EffectExecutor

    from deskpet.sdk_adapters.tools import ProductEffectExecutor

    store = _Store([])
    gate = ForegroundEffectAdmissionGate()
    gate.register(
        store=store,  # type: ignore[arg-type]
        host_run_id="host-turn-1",
        sdk_run_id="sdk-run-1",
        owner_id="owner-1",
        generation=1,
    )
    # The runtime's earlier authorization admission passed at generation 1.
    await gate.authorize("sdk-run-1")
    assert store.effect_admissions == [str(EffectBoundary.TOOL)]

    dispatches: list[str] = []

    async def _count_dispatch(self, **kwargs):  # type: ignore[no-untyped-def]
        dispatches.append(str(kwargs["context"].run_id.value))

    monkeypatch.setattr(EffectExecutor, "execute", _count_dispatch)

    class _Registry:
        def assert_workspace_current(self, run_id) -> None:  # type: ignore[no-untyped-def]
            return None

    executor = ProductEffectExecutor(
        uow=object(),  # type: ignore[arg-type]
        registry=_Registry(),  # type: ignore[arg-type]
        authorization=object(),  # type: ignore[arg-type]
        reconciliation=object(),  # type: ignore[arg-type]
        foreground_admission=gate,
    )
    context = ToolContext(RunId("sdk-run-1"), RequestId("req-1"), CancellationToken())

    # Same-generation dispatch is admitted.
    await executor.execute(context=context)
    assert dispatches == ["sdk-run-1"]

    # Lease reclaimed between the authorization admission and this dispatch.
    store.current_generation = 2
    with pytest.raises(ForegroundQueueError) as exc:
        await executor.execute(context=context)
    assert exc.value.code == "foreground_generation_stale"
    assert dispatches == ["sdk-run-1"]

    # Unregistered Runs (legacy ingress) bypass the foreground gate.
    other = ToolContext(RunId("sdk-run-2"), RequestId("req-2"), CancellationToken())
    await executor.execute(context=other)
    assert dispatches == ["sdk-run-1", "sdk-run-2"]


@pytest.mark.asyncio
async def test_durable_control_commit_wakes_active_runtime_immediately() -> None:
    release_terminal = asyncio.Event()

    class BlockedTerminal:
        def __init__(self) -> None:
            self.state = RunState.COMPLETED

        async def observe(self, **kwargs):  # type: ignore[no-untyped-def]
            await release_terminal.wait()
            return AuthenticatedTerminalObservation(
                self.state,
                f"sdk-terminal:{kwargs['sdk_run_id']}",
                "6" * 64,
            )

    store = _Store([_candidate(1)])
    ingress = _Ingress()
    terminal = BlockedTerminal()
    runtime = ForegroundRuntimeExecutionAuthority(
        store=store,  # type: ignore[arg-type]
        subject=SUBJECT,
        owner_id="owner-1",
        ingress=ingress,  # type: ignore[arg-type]
        context=_Context(),
        provider=_Provider(),
        tools=_Tools(),
        terminal_observer=terminal,
    )
    await runtime.after_enqueue(subject=SUBJECT)
    await _wait_until(lambda: len(ingress.starts) == 1)
    assert store.active is not None and store.active.sdk_run_id is not None
    host_run_id = store.active.host_run_id
    sdk_run_id = store.active.sdk_run_id

    # PAUSE: durable commit + wake -> delivered, acked, PAUSED advanced.
    store.signals.append(
        _signal_envelope(
            "sig-pause",
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            kind=ControlKind.PAUSE,
        )
    )
    await runtime.after_control(subject=SUBJECT)
    await _wait_until(lambda: any(sid == "sig-pause" for sid, _ in store.acks))
    assert ingress.signals[0]["payload"] == {"kind": "pause"}
    assert store.pause_outcomes == ["runtime-pause:sig-pause"]
    pause_ack = next(ack for sid, ack in store.acks if sid == "sig-pause")
    assert pause_ack == "sdk-delivery:sig-pause"

    # STOP: distinct signal identity and distinct terminal semantics.
    store.signals.append(
        _signal_envelope(
            "sig-stop",
            host_run_id=host_run_id,
            sdk_run_id=sdk_run_id,
            kind=ControlKind.STOP,
        )
    )
    await runtime.after_control(subject=SUBJECT)
    await _wait_until(lambda: any(sid == "sig-stop" for sid, _ in store.acks))
    stop_ack = next(ack for sid, ack in store.acks if sid == "sig-stop")
    assert stop_ack == "sdk-stop:sig-stop"
    assert ingress.cancels == [sdk_run_id]

    terminal.state = RunState.STOPPED
    release_terminal.set()
    await runtime.drain()
    assert runtime.last_error is None
    assert store.terminals == [host_run_id]
    assert str(EffectBoundary.SDK_CONTROL) in store.effect_admissions
    assert str(EffectBoundary.SDK_START) in store.effect_admissions


def test_resolve_host_terminal_keeps_stop_and_cancel_distinct() -> None:
    assert (
        resolve_host_terminal("cancelled", RunState.STOP_REQUESTED.value)
        is RunState.STOPPED
    )
    assert (
        resolve_host_terminal("cancelled", RunState.CANCEL_REQUESTED.value)
        is RunState.CANCELLED
    )
    assert resolve_host_terminal("cancelled", None) is RunState.CANCELLED
    # The Host never fabricates a cancellation terminal over a finished Run.
    assert (
        resolve_host_terminal("completed", RunState.STOP_REQUESTED.value)
        is RunState.COMPLETED
    )
    assert (
        resolve_host_terminal("failed", RunState.STOP_REQUESTED.value)
        is RunState.FAILED
    )
    assert resolve_host_terminal("waiting", None) is None


class _BlockedTerminal:
    def __init__(self) -> None:
        self.state = RunState.COMPLETED
        self.release = asyncio.Event()

    async def observe(self, **kwargs):  # type: ignore[no-untyped-def]
        await self.release.wait()
        return AuthenticatedTerminalObservation(
            self.state,
            f"sdk-terminal:{kwargs['sdk_run_id']}",
            "6" * 64,
        )


def _live_runtime(store: _Store, ingress: _Ingress, terminal: _BlockedTerminal):
    return ForegroundRuntimeExecutionAuthority(
        store=store,  # type: ignore[arg-type]
        subject=SUBJECT,
        owner_id="owner-1",
        ingress=ingress,  # type: ignore[arg-type]
        context=_Context(),
        provider=_Provider(),
        tools=_Tools(),
        terminal_observer=terminal,
    )


@pytest.mark.asyncio
async def test_superseding_stop_during_pause_ack_is_still_delivered_live() -> None:
    """A STOP landing between the pause read and its ack must not end delivery."""

    class SupersedingStore(_Store):
        def __init__(self, candidates) -> None:  # type: ignore[no-untyped-def]
            super().__init__(candidates)
            self.superseded_once = False

        async def acknowledge_signal(self, **kwargs):  # type: ignore[no-untyped-def]
            if kwargs["signal_id"] == "sig-pause" and not self.superseded_once:
                self.superseded_once = True
                # The user committed STOP between the durable read and this
                # ack: the real store hides the pause via desired_control and
                # exposes the stop signal.
                self.signals = [
                    signal
                    for signal in self.signals
                    if signal.signal_id != "sig-pause"
                ]
                self.signals.append(
                    _signal_envelope(
                        "sig-stop",
                        host_run_id=kwargs["sdk_run_id"].replace("sdk", "host"),
                        sdk_run_id=kwargs["sdk_run_id"],
                        kind=ControlKind.STOP,
                    )
                )
                raise ForegroundQueueError("foreground_signal_superseded")
            return await super().acknowledge_signal(**kwargs)

    store = SupersedingStore([_candidate(1)])
    ingress = _Ingress()
    terminal = _BlockedTerminal()
    runtime = _live_runtime(store, ingress, terminal)
    await runtime.after_enqueue(subject=SUBJECT)
    await _wait_until(lambda: len(ingress.starts) == 1)
    assert store.active is not None and store.active.sdk_run_id is not None
    sdk_run_id = store.active.sdk_run_id

    store.signals.append(
        _signal_envelope(
            "sig-pause",
            host_run_id=store.active.host_run_id,
            sdk_run_id=sdk_run_id,
            kind=ControlKind.PAUSE,
        )
    )
    await runtime.after_control(subject=SUBJECT)
    await _wait_until(lambda: any(sid == "sig-stop" for sid, _ in store.acks))
    assert not any(sid == "sig-pause" for sid, _ in store.acks)
    stop_ack = next(ack for sid, ack in store.acks if sid == "sig-stop")
    assert stop_ack == "sdk-stop:sig-stop"
    assert ingress.cancels == [sdk_run_id]
    assert store.pause_outcomes == []

    terminal.state = RunState.STOPPED
    terminal.release.set()
    await runtime.drain()
    assert runtime.last_error is None
    assert store.terminals == [store.claimed[next(iter(store.claimed))].host_run_id]


@pytest.mark.asyncio
async def test_pause_outcome_race_with_stop_keeps_pump_alive() -> None:
    """A STOP moving the head off PAUSE_REQUESTED after the ack is benign."""

    class RacingStore(_Store):
        def __init__(self, candidates) -> None:  # type: ignore[no-untyped-def]
            super().__init__(candidates)
            self.raced_once = False

        async def record_pause_outcome(self, **kwargs):  # type: ignore[no-untyped-def]
            if not self.raced_once:
                self.raced_once = True
                self.signals.append(
                    _signal_envelope(
                        "sig-stop",
                        host_run_id=kwargs["host_run_id"],
                        sdk_run_id=kwargs["sdk_run_id"],
                        kind=ControlKind.STOP,
                    )
                )
                raise ForegroundQueueError("foreground_state_transition_invalid")
            return await super().record_pause_outcome(**kwargs)

    store = RacingStore([_candidate(1)])
    ingress = _Ingress()
    terminal = _BlockedTerminal()
    runtime = _live_runtime(store, ingress, terminal)
    await runtime.after_enqueue(subject=SUBJECT)
    await _wait_until(lambda: len(ingress.starts) == 1)
    assert store.active is not None and store.active.sdk_run_id is not None
    sdk_run_id = store.active.sdk_run_id

    store.signals.append(
        _signal_envelope(
            "sig-pause",
            host_run_id=store.active.host_run_id,
            sdk_run_id=sdk_run_id,
            kind=ControlKind.PAUSE,
        )
    )
    await runtime.after_control(subject=SUBJECT)
    await _wait_until(lambda: any(sid == "sig-stop" for sid, _ in store.acks))
    assert store.pause_outcomes == []
    assert ingress.cancels == [sdk_run_id]

    terminal.state = RunState.STOPPED
    terminal.release.set()
    await runtime.drain()
    assert runtime.last_error is None


@pytest.mark.asyncio
async def test_durable_cancel_commit_is_delivered_live() -> None:
    store = _Store([_candidate(1)])
    ingress = _Ingress()
    terminal = _BlockedTerminal()
    runtime = _live_runtime(store, ingress, terminal)
    await runtime.after_enqueue(subject=SUBJECT)
    await _wait_until(lambda: len(ingress.starts) == 1)
    assert store.active is not None and store.active.sdk_run_id is not None
    sdk_run_id = store.active.sdk_run_id

    store.signals.append(
        _signal_envelope(
            "sig-cancel",
            host_run_id=store.active.host_run_id,
            sdk_run_id=sdk_run_id,
            kind=ControlKind.CANCEL,
        )
    )
    await runtime.after_control(subject=SUBJECT)
    await _wait_until(lambda: any(sid == "sig-cancel" for sid, _ in store.acks))
    cancel_ack = next(ack for sid, ack in store.acks if sid == "sig-cancel")
    assert cancel_ack == "sdk-cancel:sig-cancel"
    assert ingress.cancels == [sdk_run_id]

    terminal.state = RunState.CANCELLED
    terminal.release.set()
    await runtime.drain()
    assert runtime.last_error is None


def test_production_registries_accept_stopped_terminal() -> None:
    """STOP resolves to Host terminal STOPPED; the shared SDK-adapter
    registries must release bindings for it instead of raising ValueError."""

    from deskpet.sdk_adapters.run_bindings import SdkRunBindingRegistry
    from deskpet.sdk_adapters.tool_authority import SdkRunToolAuthorityRegistry

    with pytest.raises(KeyError):
        SdkRunBindingRegistry().mark_terminal("missing-run", "stopped")
    with pytest.raises(KeyError):
        SdkRunToolAuthorityRegistry().mark_terminal("missing-run", "stopped")
