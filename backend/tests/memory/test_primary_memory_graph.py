"""Real signed HUMAN facade + installed public Memory graph; no SDK private SQL."""

from dataclasses import replace

import aiosqlite
import pytest
import simple_harness as h
import simple_harness_memory as m
from deskpet.memory.analysis_proposal import admitted_item, derive_span
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_service import AppendPrimaryEventRequest
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
from deskpet.memory.primary_visibility import read_evidence_pair
from tests.memory.test_primary_cognitive_controls import setup


async def graph_fixture(tmp_path):
    s = await setup(tmp_path, queue_source=False)
    committed = await s["service"].append_primary_event(
        AppendPrimaryEventRequest(
            {
                "text": "Please keep replies concise. My response procedure is to lead with the answer and omit unnecessary detail. The concise preference applies to this procedure."
            },
            "graph-source",
        )
    )
    async with aiosqlite.connect(s["path"]) as db:
        db.row_factory = aiosqlite.Row
        envelope, receipt = await read_evidence_pair(
            db=db,
            subject=s["auth"].subject,
            primary_ref=s["primary"],
            evidence_id=committed["evidence_ref"],
        )
    manager = s["manager"]
    await manager.ingest_committed_evidence(envelope, receipt)
    item = admitted_item(envelope, receipt)
    span = derive_span(item, item.text, span_id="graph-explicit-source")
    procedure = h.MemoryMutationOperation(
        operation_id="graph-procedure",
        kind=h.MemoryMutationKind.CREATE,
        memory_type=h.LongTermMemoryType.PROCEDURE,
        payload=h.ProcedureMemoryPayload(
            "concise response procedure",
            ("answering user:self",),
            ("lead with the answer", "omit unnecessary detail"),
            h.ProcedureRiskLevel.LOW,
        ),
        target=None,
        depends_on_operation_ids=(),
        lifecycle_state=h.ProcedureLifecycleState.ACTIVE,
        epistemic_status=h.EpistemicStatus.EXPLICIT_USER,
        conflict_status=h.ConflictStatus.UNCONTESTED,
        verification_state=h.VerificationState.SOURCE_BOUND,
        valid_time_interval=h.ValidTimeInterval(None, None),
        proposed_privacy_class=h.PrivacyClass.PERSONAL,
        proposed_information_attributes=(h.InformationAttribute.PREFERENCE,),
        evidence_spans=(span,),
        reason_code="explicit_user_assertion",
    )
    relation = replace(
        procedure,
        operation_id="graph-relation",
        memory_type=h.LongTermMemoryType.SEMANTIC,
        lifecycle_state=h.SemanticLifecycleState.ACTIVE,
        depends_on_operation_ids=(procedure.operation_id,),
        payload=h.SemanticRelationMemoryPayload(
            h.SemanticRelationKind.APPLIES_TO,
            h.ExistingMemoryTarget(s["memory_id"], 1),
            h.CreatedByOperationTarget(procedure.operation_id),
        ),
    )
    plan = h.MemoryMutationPlan(
        "graph-plan",
        envelope.run_id,
        "graph-turn",
        s["auth"].subject,
        2,
        h.MemoryMutationPlanOutcome.MUTATE,
        (procedure, relation),
        envelope.disclosure_context,
        (h.EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),),
        "graph-plan-idempotency",
    )
    applied = await manager.apply_memory_mutation_plan(
        principal=s["runtime"].principal(),
        scope=m.MemoryScope.personal(s["auth"].subject),
        plan=plan,
    )
    assert applied.outcome is h.MemoryMutationApplyOutcome.COMMITTED
    receipt_view = await manager.get_memory_mutation_receipt_view(
        principal=s["runtime"].principal(), receipt_ref=applied.receipt_ref
    )
    s["created"] = {
        operation.operation_id: operation for operation in receipt_view.operations
    }
    return s


@pytest.mark.asyncio
async def test_actual_public_graph_endpoint_has_real_relation_and_bounded_closed_endpoints(
    tmp_path,
):
    s = await graph_fixture(tmp_path)
    try:
        response = await s["command"]("primary.memory.graph")
        assert response["payload"]["ok"], response
        graph = response["payload"]["result"]
        actual = await s["manager"].get_twin_graph_view(
            principal=s["runtime"].principal()
        )
        assert len(graph["nodes"]) == 2 and len(graph["edges"]) == 1
        assert graph["edges"] == [edge.to_json() for edge in actual.edges]
        assert s["created"]["graph-relation"].memory_id not in {
            n["memory_id"] for n in graph["nodes"]
        }
        assert {(n["node_id"], n["source_node_hash"]) for n in graph["nodes"]} == {
            (n.node_id, n.node_hash) for n in actual.nodes
        }
        assert all(n["source_refs"] for n in graph["nodes"])
        # Optional ignored artifact feeds the actual Cytoscape integration test.
        import json
        import os
        from pathlib import Path

        destination = os.environ.get("HOST_GRAPH_FIXTURE")
        if destination:
            target = Path(destination).resolve()
            assert ".local-test-evidence" in target.parts
            target.write_text(json.dumps(graph, ensure_ascii=False))
        limited = await s["command"]("primary.memory.graph", {"node_limit": 1})
        assert limited["payload"]["ok"]
        assert len(limited["payload"]["result"]["nodes"]) == 1
        assert limited["payload"]["result"]["edges"] == []
        assert limited["payload"]["result"]["truncated"] == {
            "nodes": True,
            "edges": True,
        }
        if destination:
            forgotten = await s["command"]("primary.memory.forget", s["payload"])
            assert forgotten["payload"]["ok"]
            after = await s["command"]("primary.memory.graph")
            assert after["payload"]["ok"] and not after["payload"]["result"]["edges"]
            target.with_suffix(".forgotten.json").write_text(
                json.dumps(after["payload"]["result"], ensure_ascii=False)
            )
    finally:
        await s["runtime"].close()


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["endpoint", "relation"])
async def test_suppression_and_actual_reopen_do_not_revive_graph_edge(tmp_path, target):
    s = await graph_fixture(tmp_path)
    try:
        if target == "endpoint":
            response = await s["command"]("primary.memory.forget", s["payload"])
            assert response["payload"]["ok"], response
        else:
            # Exact canonical relation identity comes from the public mutation receipt,
            # not parsing an opaque edge ID. Production UI does not yet have this authority.
            await s["manager"].suppress(
                principal=s["runtime"].principal(),
                request=m.SuppressionRequest(
                    "explicit-relation-forget",
                    s["auth"].subject,
                    m.SuppressionScopeKind.MEMORY,
                    s["created"]["graph-relation"].memory_id,
                    "user_forget",
                    200.0,
                ),
            )
        response = await s["command"]("primary.memory.graph")
        assert (
            response["payload"]["ok"] and response["payload"]["result"]["edges"] == []
        )
        await s["runtime"].close()
        s["box"]["runtime"] = HumanMemoryV7Runtime(
            s["memory_path"], evidence_authority=HostEvidenceAuthority(s["path"])
        )
        reopened = await s["command"]("primary.memory.graph")
        assert (
            reopened["payload"]["ok"] and reopened["payload"]["result"]["edges"] == []
        )
    finally:
        await s["box"]["runtime"].close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"node_limit": True},
        {"edge_limit": 401},
        {"node_limit": 0},
        {"primary_ref": "other-primary"},
        {"subject": "other-owner"},
    ],
)
async def test_graph_request_rejects_invalid_bounds_and_borrowed_authority(
    tmp_path, body
):
    s = await setup(tmp_path, queue_source=False)
    try:
        response = await s["command"]("primary.memory.graph", body)
        assert response["payload"]["ok"] is False
        assert "result" not in response["payload"]
    finally:
        await s["runtime"].close()


@pytest.mark.asyncio
async def test_graph_slow_public_read_rebind_is_rejected_by_real_signed_fence(
    tmp_path, monkeypatch
):
    s = await setup(tmp_path, queue_source=False)
    original = s["manager"].get_twin_graph_view

    async def slow(*, principal):
        view = await original(principal=principal)
        await s["rebind"]()
        return view

    monkeypatch.setattr(s["manager"], "get_twin_graph_view", slow)
    try:
        response = await s["command"]("primary.memory.graph")
        assert response["payload"]["ok"] is False
        assert "result" not in response["payload"]
    finally:
        await s["runtime"].close()


def notified_commands(s, changes):
    from deskpet.memory.human_memory_api import handle_human_memory_command

    factory = replace(s["factory"], display_invalidation=changes)

    async def command(operation, body=None):
        with s["connection"].request_scope(s["control"], s["challenge"]):
            return await handle_human_memory_command(
                {
                    "type": "human_memory_request",
                    "request_id": "graph-notification",
                    "operation": operation,
                    "request": {"primary_ref": s["primary"], **(body or {})},
                },
                factory=factory,
                auth=s["auth"],
            )

    return command


@pytest.mark.asyncio
@pytest.mark.parametrize("ack_loss", [False, True])
async def test_actual_suppression_produces_content_free_invalidation_even_on_ack_loss(
    tmp_path, monkeypatch, ack_loss
):
    from deskpet.memory.display_invalidation import MemoryDisplayInvalidation

    s = await setup(tmp_path, queue_source=False)
    events = []

    async def broadcast(event):
        events.append(event)

    changes = MemoryDisplayInvalidation(broadcast)
    command = notified_commands(s, changes)
    original = s["manager"].suppress

    async def suppress(**kwargs):
        result = await original(**kwargs)
        if ack_loss:
            raise RuntimeError("deterministic ACK lost after actual SDK commit")
        return result

    monkeypatch.setattr(s["manager"], "suppress", suppress)
    try:
        response = await command("primary.memory.forget", s["payload"])
        assert response["payload"]["ok"] is (not ack_loss)
        assert events == [{"type": "human_memory_changed", "payload": {}}]
        assert changes.generation == 1
        view = await s["manager"].get_twin_graph_view(
            principal=s["runtime"].principal()
        )
        assert not view.nodes
        assert len(s["action_rows"]()) == 1
    finally:
        await s["runtime"].close()


@pytest.mark.asyncio
async def test_same_bound_slow_graph_crossed_by_real_forget_cannot_disclose_old_view(
    tmp_path, monkeypatch
):
    import asyncio

    from deskpet.memory.display_invalidation import MemoryDisplayInvalidation

    s = await setup(tmp_path, queue_source=False)

    async def broadcast(_):
        pass

    command = notified_commands(s, MemoryDisplayInvalidation(broadcast))
    original = s["manager"].get_twin_graph_view
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def slow(*, principal):
        nonlocal calls
        calls += 1
        result = await original(principal=principal)
        if calls == 1:
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(s["manager"], "get_twin_graph_view", slow)
    try:
        task = asyncio.create_task(command("primary.memory.graph"))
        await entered.wait()
        forgotten = await command("primary.memory.forget", s["payload"])
        assert forgotten["payload"]["ok"]
        release.set()
        old = await task
        assert old["payload"]["ok"] is False and "result" not in old["payload"]
        fresh = await command("primary.memory.graph")
        assert fresh["payload"]["result"]["nodes"] == []
    finally:
        release.set()
        await s["runtime"].close()


@pytest.mark.asyncio
async def test_final_identity_boundary_crossed_by_actual_suppression_rejects_old_graph(
    tmp_path, monkeypatch
):
    """Dirac counterexample: the last identity await is after controls.graph."""
    import asyncio
    from contextlib import asynccontextmanager

    from deskpet.memory import human_memory_api as api
    from deskpet.memory import writer_fence as fence
    from deskpet.memory.display_invalidation import MemoryDisplayInvalidation

    s = await setup(tmp_path, queue_source=False)

    async def unavailable(_):
        raise RuntimeError("notification unavailable")

    changes = MemoryDisplayInvalidation(unavailable)
    command = notified_commands(s, changes)
    original_dispatch, original_boundary = (
        api._dispatch,
        fence.human_memory_request_boundary,
    )
    graph_task = asyncio.current_task()
    prepared, injected = False, False

    async def dispatch(service, operation, request, request_id):
        nonlocal prepared
        result = await original_dispatch(service, operation, request, request_id)
        if operation == "primary.memory.graph":
            prepared = True
        return result

    @asynccontextmanager
    async def boundary():
        nonlocal injected
        if asyncio.current_task() is graph_task and prepared and not injected:
            injected = True
            result = await asyncio.create_task(
                command("primary.memory.forget", s["payload"])
            )
            assert result["payload"]["ok"]
        async with original_boundary():
            yield

    monkeypatch.setattr(api, "_dispatch", dispatch)
    monkeypatch.setattr(fence, "human_memory_request_boundary", boundary)
    try:
        result = await command("primary.memory.graph")
        assert injected and changes.generation == 1
        assert result["payload"]["ok"] is False and "result" not in result["payload"]
        assert not (
            await s["manager"].get_twin_graph_view(principal=s["runtime"].principal())
        ).nodes
    finally:
        await s["runtime"].close()
