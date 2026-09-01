# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5a Task 5 — mandatory occurrence-inbox reconcile before ``no_recall``.

REQUIRED negative test (AC-4, challenge ledger S5A-BR-F3): a real v7 store is
seeded through the PRODUCTION write path — mutation plan creating a
prospective intent, then ``apply_prospective_signal`` with a test-injected
signal-authority resolver producing a real ``matched`` trigger occurrence.
Raw DB seeding is impossible by design (open-time FK + audit-chain checks).

Asserts: ``no_recall`` is refused while an eligible occurrence is pending;
the eligible summary enters the per-turn snapshot; a non-presentable
(CANCELLED) occurrence never blocks; Host-presented membership opens the gate.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import pytest_asyncio
from simple_harness.contracts import JsonValue, RunId, fingerprint_json
from simple_harness.runtime import (
    EVIDENCE_ITEM_AUTHORITY_SCHEMA_VERSION,
    EVIDENCE_NORMALIZATION_IDENTITY_UTF8_V1,
    AdmittedEvidenceAuthority,
    ConflictStatus,
    DeliveryRecipient,
    DisclosureContext,
    DisclosureGeneration,
    DisclosurePurpose,
    DisclosureReasonCode,
    DisclosureSource,
    DisclosureTrust,
    EpistemicStatus,
    EvidenceActorRole,
    EvidenceItemAuthority,
    EvidenceProvenance,
    EvidenceReasonCode,
    EvidenceRef,
    EvidenceSourceKind,
    EvidenceSpanRef,
    EvidenceSupportKind,
    InformationAttribute,
    IntendedAudience,
    LongTermMemoryType,
    MemoryMutationKind,
    MemoryMutationOperation,
    MemoryMutationPlan,
    MemoryMutationPlanOutcome,
    MemoryScopeRef,
    PrivacyClass,
    ProspectiveLifecycleState,
    ProspectiveMemoryPayload,
    ProspectiveSignalAuthorityRef,
    ProspectiveSignalIntent,
    ProspectiveSignalKind,
    ProspectiveTimeTrigger,
    SanitizedEvidenceEnvelope,
    SanitizedEvidenceReceipt,
    ValidTimeInterval,
    VerificationState,
    issue_prospective_signal_authority,
)
from simple_harness_memory.core.mutations import InformationClassificationPolicy

from deskpet.memory.human_memory_v7 import (
    HumanMemoryV7Runtime,
    local_memory_principal,
)
from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.sdk_adapters.context_authority import (
    ContextRouteLedgerStore,
    NoRecallBlockedError,
    ProductRunContextAuthority,
    ProductRuntimeDecisionSink,
)

SUBJECT = "deskpet-local-owner-v1"
RUN = RunId("run-no-recall-1")
NOW = time.time()


def _disclosure() -> DisclosureContext:
    return DisclosureContext(
        run_id="run-1",
        subject=SUBJECT,
        recipient=DeliveryRecipient.USER_SELF,
        recipient_id=SUBJECT,
        intended_audience=IntendedAudience.USER_SELF,
        purpose=DisclosurePurpose.PERSONALIZATION,
        source=DisclosureSource.AUTHENTICATED_HOST,
        trust=DisclosureTrust.TRUSTED_AUTHORITY,
        generation=DisclosureGeneration.CURRENT,
        authority_ref="host-disclosure-1",
        reason_codes=(DisclosureReasonCode.MINIMUM_NECESSARY,),
    )


def _admitted() -> tuple[SanitizedEvidenceEnvelope, SanitizedEvidenceReceipt]:
    payload: dict[str, JsonValue] = {
        "item_id": "message-1",
        "public_text": "明天下午三点提醒我发周报",
    }
    envelope = SanitizedEvidenceEnvelope(
        evidence_id="evidence-1",
        run_id="run-1",
        subject=SUBJECT,
        source_kind=EvidenceSourceKind.USER_MESSAGE,
        source_ref="turn-1/user",
        source_hash="a" * 64,
        sanitized_payload=cast(dict, payload),
        sanitized_hash=fingerprint_json(payload),
        filter_policy_version="credential-filter/v1",
        removed_spans=(),
        disclosure_context=_disclosure(),
        evidence_refs=(),
    )
    receipt = SanitizedEvidenceReceipt(
        receipt_id="admission-1",
        run_id=envelope.run_id,
        subject=envelope.subject,
        evidence_id=envelope.evidence_id,
        envelope_hash=envelope.envelope_hash,
        source_hash=envelope.source_hash,
        sanitized_hash=envelope.sanitized_hash,
        filter_policy_version=envelope.filter_policy_version,
        accepted=True,
        reason_codes=(EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
        disclosure_context=envelope.disclosure_context,
        evidence_refs=envelope.evidence_refs,
        admitted_at=NOW - 120.0,
    )
    return envelope, receipt


def _span(
    envelope: SanitizedEvidenceEnvelope, receipt: SanitizedEvidenceReceipt
) -> EvidenceSpanRef:
    text = cast(str, envelope.sanitized_payload["public_text"])
    return EvidenceSpanRef(
        span_id="span-1",
        evidence_id=envelope.evidence_id,
        envelope_hash=envelope.envelope_hash,
        sanitized_hash=envelope.sanitized_hash,
        admission_receipt_id=receipt.receipt_id,
        admission_receipt_hash=receipt.receipt_hash,
        source_kind=envelope.source_kind,
        item_ordinal=1,
        item_id="message-1",
        item_json_pointer="/public_text",
        start_byte=0,
        end_byte=len(text.encode("utf-8")),
        exact_quote=text,
        quote_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        source_hash=envelope.source_hash,
        normalization_version=EVIDENCE_NORMALIZATION_IDENTITY_UTF8_V1,
        actor_role=EvidenceActorRole.USER,
        provenance=EvidenceProvenance.AUTHENTICATED_USER,
        support_kind=EvidenceSupportKind.EXPLICIT_USER_ASSERTION,
        typed_observation=None,
    )


class _SeedAuthority:
    """Production-shaped resolver injection (never raw DB seeding)."""

    def __init__(self, envelope, receipt, span) -> None:
        self._admitted = AdmittedEvidenceAuthority(
            envelope,
            receipt,
            EvidenceItemAuthority(
                schema_version=EVIDENCE_ITEM_AUTHORITY_SCHEMA_VERSION,
                authority_id="item-authority-1",
                evidence_id=span.evidence_id,
                envelope_hash=span.envelope_hash,
                sanitized_hash=span.sanitized_hash,
                source_hash=span.source_hash,
                source_kind=span.source_kind,
                item_ordinal=span.item_ordinal,
                item_id=span.item_id,
                item_json_pointer=span.item_json_pointer,
                normalization_version=span.normalization_version,
                actor_role=span.actor_role,
                provenance=span.provenance,
                required_privacy_class=PrivacyClass.PERSONAL,
                required_information_attributes=(),
                classification_authority_ref="host-classification-1",
                issuer_ref="host-evidence-1",
            ),
        )
        self.signals: dict[str, object] = {}

    async def resolve_admitted_evidence(self, span):
        return self._admitted

    async def resolve_typed_observation(self, reference):
        raise ValueError("no typed observation is registered")

    async def resolve_memory_action_authority(self, reference):
        raise ValueError("no action authority is registered")

    async def resolve_prospective_signal_authority(self, reference):
        return self.signals[reference.authority_id]


def _create_prospective_operation(span: EvidenceSpanRef) -> MemoryMutationOperation:
    return MemoryMutationOperation(
        operation_id="create-prospective",
        kind=MemoryMutationKind.CREATE,
        memory_type=LongTermMemoryType.PROSPECTIVE,
        payload=ProspectiveMemoryPayload(
            "发周报", ProspectiveTimeTrigger(NOW - 20.0, "Asia/Shanghai")
        ),
        target=None,
        depends_on_operation_ids=(),
        lifecycle_state=ProspectiveLifecycleState.PENDING,
        epistemic_status=EpistemicStatus.EXPLICIT_USER,
        conflict_status=ConflictStatus.UNCONTESTED,
        verification_state=VerificationState.SOURCE_BOUND,
        valid_time_interval=ValidTimeInterval(None, None),
        proposed_privacy_class=PrivacyClass.PERSONAL,
        proposed_information_attributes=(InformationAttribute.GOAL,),
        evidence_spans=(span,),
        reason_code="explicit_future_action",
    )


def _create_semantic_operation(span: EvidenceSpanRef) -> MemoryMutationOperation:
    from simple_harness.runtime import SemanticLifecycleState, SemanticMemoryPayload

    return MemoryMutationOperation(
        operation_id="create-semantic",
        kind=MemoryMutationKind.CREATE,
        memory_type=LongTermMemoryType.SEMANTIC,
        payload=SemanticMemoryPayload(
            "user:self", "report_style", "weekly report should be concise", ("default",)
        ),
        target=None,
        depends_on_operation_ids=(),
        lifecycle_state=SemanticLifecycleState.ACTIVE,
        epistemic_status=EpistemicStatus.EXPLICIT_USER,
        conflict_status=ConflictStatus.UNCONTESTED,
        verification_state=VerificationState.SOURCE_BOUND,
        valid_time_interval=ValidTimeInterval(None, None),
        proposed_privacy_class=PrivacyClass.PERSONAL,
        proposed_information_attributes=(InformationAttribute.PREFERENCE,),
        evidence_spans=(span,),
        reason_code="explicit_user_assertion",
    )


def _grant(
    authority: _SeedAuthority,
    *,
    memory_id: str,
    revision: int,
    kind: ProspectiveSignalKind,
    transition_from: ProspectiveLifecycleState,
    transition_to: ProspectiveLifecycleState,
    observed_at: float,
    signal_id: str,
    outbox_id: str | None = None,
    outbox_hash: str | None = None,
) -> ProspectiveSignalAuthorityRef:
    intent = ProspectiveSignalIntent(
        signal_id=signal_id,
        subject=SUBJECT,
        scope=MemoryScopeRef.personal(SUBJECT),
        target_memory_id=memory_id,
        target_revision=revision,
        signal_kind=kind,
        trigger=ProspectiveTimeTrigger(NOW - 20.0, "Asia/Shanghai"),
        scheduler_registration_ref="scheduler-registration-1",
        registration_revision=1,
        signal_receipt_id=f"receipt-{signal_id}",
        signal_receipt_hash=hashlib.sha256(signal_id.encode()).hexdigest(),
        observed_at=observed_at,
        transition_from=transition_from,
        transition_to=transition_to,
        outbox_id=outbox_id,
        outbox_payload_hash=outbox_hash,
        run_id="run-1",
        operation_id=f"operation-{signal_id}",
    )
    grant = issue_prospective_signal_authority(
        intent,
        authority_id=f"authority-{signal_id}",
        issued_at=NOW - 60.0,
        expires_at=NOW + 3600.0,
        nonce=f"nonce-{signal_id}",
        issuer_ref="host-prospective-signal:v1",
    )
    authority.signals[grant.authority_id] = grant
    return ProspectiveSignalAuthorityRef.from_authority(grant)


async def _seed_matched_occurrence(memory_db: Path) -> None:
    """Create intent + matched occurrence via the production write path."""

    from simple_harness_memory import build_human_memory_v7
    from simple_harness_memory.core.identity import MemoryScope

    envelope, receipt = _admitted()
    span = _span(envelope, receipt)
    authority = _SeedAuthority(envelope, receipt, span)
    manager = await build_human_memory_v7(
        memory_db,
        evidence_authority=authority,
        memory_action_authority=authority,
        prospective_signal_authority=authority,
        classification_policy=InformationClassificationPolicy(
            policy_id="memory-classification-policy",
            policy_version="1",
            authority_ref="memory-policy-registry:classification/v1",
            required_privacy_class=PrivacyClass.PERSONAL,
            required_information_attributes=(),
        ),
    )
    try:
        backend = manager._backend
        principal = local_memory_principal()
        scope = MemoryScope.personal(SUBJECT)
        await manager.ingest_committed_evidence(envelope, receipt)
        await backend.apply_memory_mutation_plan(
            principal=principal,
            scope=scope,
            plan=MemoryMutationPlan(
                plan_id="plan-1",
                run_id="run-1",
                turn_id="turn-1",
                subject=SUBJECT,
                base_revision=1,
                outcome=MemoryMutationPlanOutcome.MUTATE,
                operations=(
                    _create_prospective_operation(span),
                    _create_semantic_operation(span),
                ),
                disclosure_context=_disclosure(),
                evidence_refs=(
                    EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),
                ),
                idempotency_key="idempotency-1",
            ),
        )
        async with backend.connection.execute(
            "SELECT h.memory_id,o.outbox_id,o.payload_hash FROM "
            "cognitive_memory_heads h JOIN outbox o ON o.principal_id=h.principal_id "
            "WHERE o.topic='memory.prospective.registration.requested' "
            "AND h.memory_type='prospective'"
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        memory_id, outbox_id, outbox_hash = str(row[0]), str(row[1]), str(row[2])
        accepted = _grant(
            authority,
            memory_id=memory_id,
            revision=1,
            kind=ProspectiveSignalKind.REGISTRATION_ACCEPTED,
            transition_from=ProspectiveLifecycleState.PENDING,
            transition_to=ProspectiveLifecycleState.PENDING,
            observed_at=NOW - 30.0,
            signal_id="accepted-1",
            outbox_id=outbox_id,
            outbox_hash=outbox_hash,
        )
        await backend.apply_prospective_signal(
            principal=principal, scope=scope, reference=accepted
        )
        due = _grant(
            authority,
            memory_id=memory_id,
            revision=1,
            kind=ProspectiveSignalKind.TIME_DUE,
            transition_from=ProspectiveLifecycleState.PENDING,
            transition_to=ProspectiveLifecycleState.TRIGGERED,
            observed_at=NOW - 20.0,
            signal_id="due-1",
        )
        applied = await backend.apply_prospective_signal(
            principal=principal, scope=scope, reference=due
        )
        assert applied.lifecycle_state is ProspectiveLifecycleState.TRIGGERED
    finally:
        await manager.close()


@pytest_asyncio.fixture()
async def gate(tmp_path: Path):
    state_db = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(state_db)
    memory_db = tmp_path / "human_memory_v7.db"
    await _seed_matched_occurrence(memory_db)
    v7 = HumanMemoryV7Runtime(memory_db)
    ledger = ContextRouteLedgerStore(state_db)

    async def reconcile(presented):
        return await v7.pending_occurrences(presented)

    yield SimpleNamespace(
        state_db=state_db,
        memory_db=memory_db,
        v7=v7,
        ledger=ledger,
        reconcile=reconcile,
        sink=ProductRuntimeDecisionSink(ledger=ledger, reconcile=reconcile),
    )
    await v7.close()


@pytest.mark.asyncio
async def test_no_recall_is_refused_while_matched_occurrence_pending(gate) -> None:
    with pytest.raises(NoRecallBlockedError):
        await gate.sink.record_no_recall(
            run_id=RUN, provider_turn_ordinal=1, request_fingerprint="a" * 64
        )
    with sqlite3.connect(gate.state_db) as db:
        rows = db.execute(
            "SELECT COUNT(*) FROM context_route_decisions WHERE origin='no_recall'"
        ).fetchone()
    assert rows[0] == 0


@pytest.mark.asyncio
async def test_pending_summary_enters_per_turn_snapshot(gate) -> None:
    from simple_harness.contracts.messages import Message, MessageRole
    from simple_harness.runtime.context import ContextSnapshot

    class _Context:
        revision = 1

        def load(self, run_id):
            return ContextSnapshot(
                1, (Message(role=MessageRole.USER, content="随便聊聊"),)
            )

    ports = SimpleNamespace(
        context=_Context(),
        react_checkpoint=SimpleNamespace(
            read_start_snapshot=lambda run_id: {"input": {}}
        ),
    )
    exposure = SimpleNamespace(provider_specs=lambda run_id: ())
    authority = ProductRunContextAuthority(
        ports_resolver=lambda: ports,
        exposure_resolver=lambda run_id: exposure,
        ledger=gate.ledger,
        reconcile=gate.reconcile,
    )
    from simple_harness.execution.context_authority import (
        ContextRouteState,
        RunContextAuthorityRequest,
    )

    snapshot = await authority.prepare_snapshot(
        RunContextAuthorityRequest(
            RUN, 1, 1, ContextRouteState.UNROUTED, None, "f" * 64
        )
    )
    joined = "".join(str(message.content) for message in snapshot.messages)
    assert "pending_prospective_occurrences" in joined
    assert "发周报" in joined


@pytest.mark.asyncio
async def test_host_presented_membership_opens_the_gate(gate) -> None:
    pending = await gate.v7.pending_occurrences(frozenset())
    assert len(pending) == 1
    key = pending[0].occurrence_key
    # Simulate the S5b presentation write on the Host's own cursor table.
    with sqlite3.connect(gate.state_db) as db:
        db.execute(
            "INSERT INTO occurrence_presented(occurrence_key,memory_id,"
            "prospective_revision,presented_at,presented_run_id) VALUES (?,?,?,?,?)",
            (key, pending[0].memory_id, pending[0].prospective_revision,
             time.time(), RUN.value),
        )
        db.commit()
    receipt = await gate.sink.record_no_recall(
        run_id=RUN, provider_turn_ordinal=1, request_fingerprint="a" * 64
    )
    assert receipt.route.value == "direct_standalone"


@pytest.mark.asyncio
async def test_suppressed_occurrence_never_blocks(tmp_path: Path) -> None:
    """对照 (S5A-DS-F1): a suppressed intent's occurrence must not block
    ``no_recall``, and its content never surfaces in the snapshot summary."""

    state_db = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(state_db)
    memory_db = tmp_path / "human_memory_v7.db"
    await _seed_matched_occurrence(memory_db)

    from simple_harness_memory import build_human_memory_v7
    from simple_harness_memory.core.suppression import (
        SuppressionRequest,
        SuppressionScopeKind,
    )

    manager = await build_human_memory_v7(memory_db)
    try:
        backend = manager._backend
        async with backend.connection.execute(
            "SELECT memory_id FROM prospective_trigger_events LIMIT 1"
        ) as cursor:
            memory_id = str((await cursor.fetchone())[0])
        await manager.suppress(
            principal=local_memory_principal(),
            request=SuppressionRequest(
                request_id="suppress-1",
                subject=SUBJECT,
                scope_kind=SuppressionScopeKind.MEMORY,
                scope_ref=memory_id,
                reason_code="user_requested_forgetting",
                requested_at=NOW,
            ),
        )
    finally:
        await manager.close()

    v7 = HumanMemoryV7Runtime(memory_db)
    try:
        pending = await v7.pending_occurrences(frozenset())
        assert pending == ()
        ledger = ContextRouteLedgerStore(state_db)

        async def reconcile(presented):
            return await v7.pending_occurrences(presented)

        sink = ProductRuntimeDecisionSink(ledger=ledger, reconcile=reconcile)
        result = await sink.record_no_recall(
            run_id=RUN, provider_turn_ordinal=1, request_fingerprint="b" * 64
        )
        assert result.recall_refs == ()
    finally:
        await v7.close()


@pytest.mark.asyncio
async def test_memory_standalone_route_returns_typed_fragments(gate) -> None:
    """AC-5: memory_standalone commits MEMORY_STANDALONE with typed fragments
    from the real v7 store, second-pass gated and deduplicated."""

    from simple_harness.runtime.task_scope_protocol import TaskScopeRoute

    from deskpet.sdk_adapters.context_route import ContextRouteToolService

    async def recall_executor(**kwargs):
        return await gate.v7.typed_recall(**kwargs)

    tool = ContextRouteToolService(
        service_factory_getter=lambda: None,
        binding_store_factory=lambda: None,
        binding_append_getter=lambda: None,
        ledger=gate.ledger,
        tool_context_getter=lambda: SimpleNamespace(
            run_id=RUN,
            effect_id=SimpleNamespace(value="effect-recall-1"),
            task_execution_envelope=SimpleNamespace(
                raw_call_id="raw-recall-1", turn_ordinal=1
            ),
        ),
        recall_executor=recall_executor,
    )
    result = await tool.handle_context_route(
        {"route": "memory_standalone", "query": "concise report"}
    )
    assert "context_route_receipt" in result, result
    receipt = result["context_route_receipt"]
    assert receipt["route"] == TaskScopeRoute.MEMORY_STANDALONE.value
    assert result["fragments"], "semantic memory must be recalled"
    contents = str(result["fragments"])
    assert "concise" in contents
    for fragment in result["fragments"]:
        assert fragment["bytes"] > 0 and fragment["tokens"] > 0
        assert fragment["lane"] in {"long_term_typed", "short_horizon"}
    assert receipt["recall_refs"], "receipt must carry recall refs"
    with sqlite3.connect(gate.state_db) as db:
        rows = db.execute(
            "SELECT route,recall_refs_json FROM context_route_decisions "
            "WHERE sdk_run_id=?",
            (RUN.value,),
        ).fetchall()
    assert rows and rows[0][0] == "memory_standalone"


@pytest.mark.asyncio
async def test_loop_level_no_recall_gate_with_reconcile_wired(gate) -> None:
    """P2 audit gap: the full ReActLoop with authority+sink+reconcile wired.

    A pending eligible occurrence must surface in the per-turn snapshot AND
    turn the terminal no_recall into a stable loop error (fail closed)."""

    from simple_harness.contracts import RequestId
    from simple_harness.execution.fences import RunFenceLease
    from simple_harness.execution.uow import ExecutionLease
    from simple_harness.providers import CancelToken
    from simple_harness.runtime.drivers.react_loop import ReActRunInput
    from simple_harness.runtime.kernel import RuntimeServices

    from deskpet.sdk_adapters.task_execution import ProductTaskExecutionAuthority
    from tests.sdk_adapters.test_s5a_milestone_route_loop import (
        HarnessCheckpoint,
        HarnessContext,
        RouteExposure,
        ScriptedProvider,
        _answer,
        _loop,
    )

    run = RunId("run-loop-gate-1")
    context = HarnessContext("随便聊聊")
    checkpoint = HarnessCheckpoint()
    provider = ScriptedProvider([_answer("好的")])
    exposure = RouteExposure()
    ports = SimpleNamespace(
        context=context,
        react_checkpoint=SimpleNamespace(
            read_start_snapshot=lambda _rid: {"input": {}}
        ),
    )
    authority = ProductRunContextAuthority(
        ports_resolver=lambda: ports,
        exposure_resolver=lambda _rid: exposure,
        ledger=gate.ledger,
        reconcile=gate.reconcile,
    )

    class _NoTools:
        async def execute(self, **values):
            raise AssertionError("no tools in this lane")

    class _NoopReconciliation:
        async def observe(self, invocation):
            raise AssertionError("no provider reconciliation in this lane")

    noop = object()
    services = RuntimeServices(
        provider=provider,
        tools=_NoTools(),
        authorization=noop,
        context=context,
        delivery=noop,
        tool_reconciliation=noop,
        reconciliation=noop,
        provider_reconciliation=_NoopReconciliation(),
        react_checkpoint=checkpoint,
        run_context_authority=authority,
        runtime_decision_sink=gate.sink,
        task_execution_authority=ProductTaskExecutionAuthority(),
    )
    with pytest.raises(NoRecallBlockedError):
        await _loop().run(
            ReActRunInput(run, RequestId("req-loop-gate"), tool_exposure=exposure),
            services=services,
            execution_lease=ExecutionLease(
                run.value, "runtime.kernel", "worker-1", 1, 100.0
            ),
            run_fence=RunFenceLease(run, 1, "worker-1", 1),
            cancel=CancelToken(),
            initial_messages=(),
        )
    # The eligible pending summary DID reach the provider payload.
    joined = "".join(
        str(m.content) for m in provider.calls[0].messages
    )
    assert "pending_prospective_occurrences" in joined
    assert "发周报" in joined
