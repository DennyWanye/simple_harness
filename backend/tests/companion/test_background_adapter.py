from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.companion.contracts import LeaseClaim, OwnerRef
from deskpet.companion.run_adapter import (
    BackgroundRunAdapter,
    BackgroundRunDurableResultV1,
)
from deskpet.execution.contracts import OutcomeStatus
from deskpet.harness.contracts import HostContext, HostExtensionRefV1
from deskpet.harness.kernel import root_run_identity


def test_production_background_composition_is_zero_tool_and_context_os_free() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "main.py"
    ).read_text(encoding="utf-8")
    assert "if _is_companion_background_request(request):" in source
    assert "isinstance(payload, Mapping)" in source
    assert 'isinstance(payload.get("payload"), Mapping)' in source
    assert "run_tool_registry = ToolRegistry()" in source
    assert "enable_verify_gate=not companion_background" in source
    activation = source[
        source.index(
            "async def _activate_companion_runtime_adapter_and_open_ingress"
        ) :
    ]
    assert "available_capabilities=frozenset()" in activation


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def monotonic(self) -> float:
        return self.value


class FakeStore:
    def __init__(self, *, tokens: int = 120, milliseconds: int = 4_000) -> None:
        self.tokens = tokens
        self.milliseconds = milliseconds

    def get_job(self, owner, *, job_id):
        return {
            "kind": "reflection",
            "budget_reserved_tokens": self.tokens,
            "budget_reserved_ms": self.milliseconds,
        }


def _event(
    kind: str,
    status: OutcomeStatus,
    payload: dict,
    *,
    artifacts: tuple[str, ...] = (),
    error: dict | None = None,
):
    return SimpleNamespace(
        kind=kind,
        status=status,
        candidate=SimpleNamespace(payload=payload, error=error),
        artifact_refs=artifacts,
    )


async def _events(items):
    for item in items:
        yield item


@dataclass
class FakeHandle:
    run_id: str
    events: object


class FakeKernelRunClient:
    def __init__(self, items) -> None:
        self.items = items
        self.calls = []

    async def start(self, request, host, *, prepared=None):
        self.calls.append((request, host, prepared))
        return FakeHandle("run-bg-1", _events(self.items))


class BlockingHandle:
    def __init__(self) -> None:
        self.run_id = "run-bg-blocking"
        self.cancel_reasons: list[str] = []

    @property
    def events(self):
        async def iterator():
            await asyncio.Event().wait()
            if False:
                yield None

        return iterator()

    async def cancel(self, reason: str):
        self.cancel_reasons.append(reason)


class BlockingKernelRunClient:
    def __init__(self) -> None:
        self.handle = BlockingHandle()
        self.started = asyncio.Event()

    async def start(self, request, host, *, prepared=None):
        self.started.set()
        return self.handle


@dataclass(frozen=True)
class FakeTerminalExtension:
    descriptor: HostExtensionRefV1


def _host() -> HostContext:
    return HostContext(
        session_id="background-session",
        principal_id="principal-1",
        auth_epoch=7,
        capability_hash="a" * 64,
        available_capabilities=frozenset(),
        provider_plan=("provider",),
        trace_id="trace-bg",
    )


def _claim(**payload) -> LeaseClaim:
    return LeaseClaim(
        item_id="job-1",
        claim_owner="worker",
        claim_epoch=3,
        lease_expires_at="2099-01-01T00:00:00Z",
        attempt=1,
        payload={
            "owner_key": "owner-key",
            "purpose": "reflection",
            "text": "reflect",
            "evidence_ids": ["evidence-1"],
            **payload,
        },
    )


@pytest.mark.asyncio
async def test_background_run_uses_kernel_client_without_product_session() -> None:
    client = FakeKernelRunClient(
        [
            _event("token", OutcomeStatus.ACCEPTED, {"content": "hello "}),
            _event(
                "terminal",
                OutcomeStatus.SUCCEEDED,
                {
                    "text": "hello world",
                    "usage": {"prompt_tokens": 4, "completion_tokens": 6},
                },
            ),
        ]
    )
    adapter = BackgroundRunAdapter(
        client=client,
        store=FakeStore(),
        host_factory=lambda owner, claim: _host(),
        clock=FakeClock(),
    )

    result = await adapter(OwnerRef("profile-a", 1), _claim())

    assert result.status == "succeeded"
    assert result.budget_actual_tokens == 10
    assert len(client.calls) == 1
    request, _, prepared = client.calls[0]
    assert request["venue"] == "background"
    assert request["proposed_tools"] == ()
    assert request["payload"]["companion_background"] == {
        "schema_version": 1,
        "job_id": "job-1",
        "purpose": "reflection",
        "evidence_ids": ["evidence-1"],
        "capture_growth": False,
    }
    descriptor = prepared.host_extensions["deskpet.companion.background_run.v1"]
    assert prepared.persistence_required is True
    assert descriptor.ref == "companion-job:profile-a:1:job-1"
    assert prepared.terminal_commit_extensions == ()


@pytest.mark.asyncio
async def test_runtime_cancellation_cancels_the_durable_background_run() -> None:
    client = BlockingKernelRunClient()
    adapter = BackgroundRunAdapter(
        client=client,
        store=FakeStore(),
        host_factory=lambda owner, claim: _host(),
        clock=FakeClock(),
    )
    task = asyncio.create_task(adapter(OwnerRef("profile-a", 1), _claim()))
    await client.started.wait()
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client.handle.cancel_reasons == [
        "companion_background_runtime_cancelled"
    ]


@pytest.mark.asyncio
async def test_host_factory_appends_terminal_extension_with_stable_run_id() -> None:
    terminal_descriptor = HostExtensionRefV1(
        kind="deskpet.companion.reflection_terminal.v1",
        ref="reflection-terminal:job-1",
        content_hash="c" * 64,
    )
    factory_calls = []

    async def prepared_factory(
        owner,
        claim,
        facts,
        base_context,
        execution_run_id,
    ):
        factory_calls.append(
            (owner, claim, facts, base_context, execution_run_id)
        )
        return replace(
            base_context,
            terminal_commit_extensions=(
                FakeTerminalExtension(terminal_descriptor),
            ),
        )

    first_client = FakeKernelRunClient(
        [_event("terminal", OutcomeStatus.SUCCEEDED, {"text": "done"})]
    )
    second_client = FakeKernelRunClient(
        [_event("terminal", OutcomeStatus.SUCCEEDED, {"text": "done"})]
    )
    owner = OwnerRef("profile-a", 1)
    claim = _claim()
    for client in (first_client, second_client):
        adapter = BackgroundRunAdapter(
            client=client,
            store=FakeStore(),
            host_factory=lambda owner, claim: _host(),
            prepared_context_factory=prepared_factory,
            clock=FakeClock(),
        )
        await adapter(owner, claim)

    expected_run_id = root_run_identity(
        "background-session",
        "companion-background:profile-a:1:job-1:attempt:1",
        "companion-background:profile-a:1:job-1:attempt:1",
    )[1].run_id
    assert len(factory_calls) == 2
    assert {call[4] for call in factory_calls} == {expected_run_id}
    first_prepared = first_client.calls[0][2]
    second_prepared = second_client.calls[0][2]
    assert len(first_prepared.terminal_commit_extensions) == 1
    assert (
        first_prepared.terminal_commit_extensions[0].descriptor
        == terminal_descriptor
    )
    assert first_prepared.prepared_fingerprint == (
        second_prepared.prepared_fingerprint
    )
    assert first_prepared.host_extensions == (
        second_prepared.host_extensions
    )


@pytest.mark.asyncio
async def test_prepared_context_factory_cannot_replace_base_host_facts() -> None:
    client = FakeKernelRunClient([])

    def replace_host_facts(owner, claim, facts, base_context, run_id):
        return replace(base_context, host_extensions={})

    adapter = BackgroundRunAdapter(
        client=client,
        store=FakeStore(),
        host_factory=lambda owner, claim: _host(),
        prepared_context_factory=replace_host_facts,
    )

    with pytest.raises(
        ValueError,
        match="companion_background_prepared_fact_override:host_extensions",
    ):
        await adapter(OwnerRef("profile-a", 1), _claim())
    assert client.calls == []


@pytest.mark.asyncio
async def test_missing_provider_usage_settles_reserved_upper_bounds_once() -> None:
    client = FakeKernelRunClient(
        [_event("terminal", OutcomeStatus.SUCCEEDED, {"text": "done"})]
    )
    adapter = BackgroundRunAdapter(
        client=client,
        store=FakeStore(tokens=321, milliseconds=8_765),
        host_factory=lambda owner, claim: _host(),
        clock=FakeClock(),
    )
    owner = OwnerRef("profile-a", 1)
    claim = _claim()

    first = await adapter(owner, claim)
    second = await adapter(owner, claim)

    assert first is second
    assert first.budget_actual_tokens == 321
    assert first.budget_actual_ms == 8_765
    assert len(client.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fenced", [False, True])
async def test_host_postprocessor_receives_canonical_structured_result_once(
    fenced: bool,
) -> None:
    proposal = {
        "schema_id": "deskpet.companion.growth-proposal.v1",
        "decision": "abstain",
    }
    serialized = json.dumps(
        proposal,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    terminal_payload = {
        "text": f"```json\n{serialized}\n```" if fenced else serialized,
        "usage": {"total_tokens": 17},
    }
    client = FakeKernelRunClient(
        [_event("terminal", OutcomeStatus.SUCCEEDED, terminal_payload)]
    )
    observed = []

    async def postprocess(result) -> None:
        observed.append(result)

    adapter = BackgroundRunAdapter(
        client=client,
        store=FakeStore(),
        host_factory=lambda owner, claim: _host(),
        result_postprocessor=postprocess,
        clock=FakeClock(),
    )
    owner = OwnerRef("profile-a", 1)
    claim = _claim()

    first = await adapter(owner, claim)
    replay = await adapter(owner, claim)

    expected_body = {
        "schema_version": 1,
        "run_id": "run-bg-1",
        "job_id": "job-1",
        "status": "succeeded",
        "text": terminal_payload["text"],
        "payload": terminal_payload,
        "error": None,
        "artifact_refs": [],
    }
    expected_hash = hashlib.sha256(
        json.dumps(
            expected_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    assert replay is first
    assert first.result_ref == "companion-background:job-1:run-bg-1"
    assert first.result_hash == expected_hash
    assert len(client.calls) == 1
    assert len(observed) == 1
    delivered = observed[0]
    assert delivered.owner == owner
    assert delivered.job_id == "job-1"
    assert delivered.claim_owner == "worker"
    assert delivered.claim_epoch == 3
    assert delivered.purpose == "reflection"
    assert delivered.evidence_ids == ("evidence-1",)
    assert delivered.execution_run_id == "run-bg-1"
    assert delivered.execution_session_id == "background-session"
    assert delivered.result_ref == first.result_ref
    assert delivered.result_hash == first.result_hash
    assert delivered.structured_result == proposal
    assert delivered.terminal_payload == terminal_payload
    assert delivered.artifact_refs == ()


@pytest.mark.asyncio
async def test_postprocessor_failure_does_not_cache_false_job_success() -> None:
    client = FakeKernelRunClient(
        [
            _event(
                "terminal",
                OutcomeStatus.SUCCEEDED,
                {"text": '{"decision":"abstain"}'},
            )
        ]
    )
    attempts = []

    async def reject(result) -> None:
        attempts.append((result.result_ref, result.result_hash))
        raise RuntimeError("durable_projection_failed")

    adapter = BackgroundRunAdapter(
        client=client,
        store=FakeStore(),
        host_factory=lambda owner, claim: _host(),
        result_postprocessor=reject,
        clock=FakeClock(),
    )
    owner = OwnerRef("profile-a", 1)
    claim = _claim()

    for _ in range(2):
        with pytest.raises(RuntimeError, match="durable_projection_failed"):
            await adapter(owner, claim)

    assert len(client.calls) == 2
    assert client.calls[0][0]["request_id"] == client.calls[1][0]["request_id"]
    assert attempts[0] == attempts[1]


@pytest.mark.asyncio
async def test_postprocessor_can_replace_only_durable_job_settlement_identity() -> None:
    client = FakeKernelRunClient(
        [_event("terminal", OutcomeStatus.SUCCEEDED, {"text": "local draft"})]
    )

    async def normalize(_result):
        return BackgroundRunDurableResultV1(
            result_ref='json:{"kind":"reminder_draft","text":"local draft"}',
            result_hash="d" * 64,
            reason_code="reminder_draft_committed",
        )

    adapter = BackgroundRunAdapter(
        client=client,
        store=FakeStore(),
        host_factory=lambda owner, claim: _host(),
        result_postprocessor=normalize,
        clock=FakeClock(),
    )

    result = await adapter(
        OwnerRef("profile-a", 1),
        _claim(
            purpose="delegated_task",
            delegated_grants=[],
            request_payload={
                "companion_stage": "reminder_draft",
                "external_send_allowed": False,
            },
        ),
    )

    assert result.status == "succeeded"
    assert result.result_ref.startswith("json:")
    assert result.result_hash == "d" * 64
    assert result.reason_code == "reminder_draft_committed"
    request = client.calls[0][0]
    assert request["proposed_tools"] == ()


@pytest.mark.asyncio
async def test_failed_attempt_exposes_reason_to_fresh_replan_run() -> None:
    failure = {
        "attempt": 1,
        "claim_epoch": 3,
        "observed_at": "2026-07-25T00:00:00.000Z",
        "failure": {
            "execution_run_id": "run-failed",
            "terminal_error": {
                "code": "driver_failed",
                "message": "invalid structured response",
            },
        },
    }
    first_client = FakeKernelRunClient(
        [
            _event(
                "terminal",
                OutcomeStatus.FAILED,
                {"text": ""},
                error={
                    "code": "driver_failed",
                    "message": "invalid structured response",
                },
            )
        ]
    )
    first_adapter = BackgroundRunAdapter(
        client=first_client,
        store=FakeStore(),
        host_factory=lambda owner, claim: _host(),
        clock=FakeClock(),
    )
    failed = await first_adapter(OwnerRef("profile-a", 1), _claim())
    assert failed.status == "failed"
    assert failed.failure_context["terminal_error"]["code"] == "driver_failed"

    second_client = FakeKernelRunClient(
        [_event("terminal", OutcomeStatus.SUCCEEDED, {"text": "done"})]
    )
    second_adapter = BackgroundRunAdapter(
        client=second_client,
        store=FakeStore(),
        host_factory=lambda owner, claim: _host(),
        clock=FakeClock(),
    )
    retry_claim = replace(
        _claim(replan_failure=failure),
        claim_epoch=4,
        attempt=2,
    )
    await second_adapter(OwnerRef("profile-a", 1), retry_claim)
    request = second_client.calls[0][0]
    assert request["request_id"].endswith(":attempt:2")
    assert "上一次模型执行失败" in request["text"]
    assert "invalid structured response" in request["text"]


@pytest.mark.asyncio
async def test_payload_cannot_override_host_prepared_facts() -> None:
    client = FakeKernelRunClient([])
    adapter = BackgroundRunAdapter(
        client=client,
        store=FakeStore(),
        host_factory=lambda owner, claim: _host(),
    )

    with pytest.raises(
        ValueError, match="companion_background_host_fact_override:job_id"
    ):
        await adapter(
            OwnerRef("profile-a", 1),
            _claim(request_payload={"job_id": "attacker-job"}),
        )
    assert client.calls == []


@pytest.mark.asyncio
async def test_request_payload_cannot_select_result_postprocessor() -> None:
    client = FakeKernelRunClient([])
    adapter = BackgroundRunAdapter(
        client=client,
        store=FakeStore(),
        host_factory=lambda owner, claim: _host(),
    )

    with pytest.raises(
        ValueError,
        match="companion_background_host_fact_override:result_postprocessor",
    ):
        await adapter(
            OwnerRef("profile-a", 1),
            _claim(request_payload={"result_postprocessor": "model-selected"}),
        )
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forbidden_key",
    ("prepared_context_factory", "terminal_commit_extensions"),
)
async def test_request_payload_cannot_override_prepared_context_factory(
    forbidden_key,
) -> None:
    client = FakeKernelRunClient([])
    adapter = BackgroundRunAdapter(
        client=client,
        store=FakeStore(),
        host_factory=lambda owner, claim: _host(),
    )

    with pytest.raises(
        ValueError,
        match=f"companion_background_host_fact_override:{forbidden_key}",
    ):
        await adapter(
            OwnerRef("profile-a", 1),
            _claim(request_payload={forbidden_key: "model-selected"}),
        )
    assert client.calls == []


@pytest.mark.asyncio
async def test_reflection_cannot_request_secondary_growth() -> None:
    adapter = BackgroundRunAdapter(
        client=FakeKernelRunClient([]),
        store=FakeStore(),
        host_factory=lambda owner, claim: _host(),
    )
    with pytest.raises(
        ValueError, match="companion_background_secondary_growth_forbidden"
    ):
        await adapter(OwnerRef("profile-a", 1), _claim(capture_growth=True))
