from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

import pytest

from deskpet.workflows.contracts import (
    NodeExecutionIdentity,
    WorkflowContext,
    WorkflowDefinitionError,
)
from deskpet.workflows.definitions import deep_research_v5_nodes
from deskpet.workflows.definitions.deep_research_v5_contracts import (
    DimensionCoverage,
    ResearchControlCommand,
)
from deskpet.workflows.definitions.deep_research_v5_loop_policy import LoopPolicyState
from deskpet.workflows.definitions.deep_research_v5_nodes import (
    _report_synthesis_payload,
    fetch_handler,
    gap_evaluate_handler,
    repair_join_handler,
)
from deskpet.workflows.definitions.research_core import (
    ResearchStageCancelled,
    ResearchStageOutputError,
)
from deskpet.workflows.definitions.v5 import (
    DEEP_RESEARCH_V5,
    DEEP_RESEARCH_V5_DEFINITION,
    deep_research_initial_state,
)
from deskpet.workflows.errors import InvalidStatePatch, WorkflowNodeError
from deskpet.workflows.native import InMemoryNativeCheckpointStore, NativeWorkflowExecutable


FIXTURE = Path(__file__).parent / "fixtures" / "deep_research_v5_graph_loop.json"


class VirtualClock:
    def __init__(self, active: float = 0.0) -> None:
        self.active = active
        self.wall = datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc)

    def active_seconds(
        self, *, operation_id: str, accumulated_active_seconds: float
    ) -> float:
        assert operation_id
        assert self.active >= accumulated_active_seconds
        return self.active

    def wall_time(self) -> str:
        return (self.wall + timedelta(seconds=self.active)).isoformat()


StageHandler = Callable[[str, Mapping[str, object]], Mapping[str, object]]


class StagePort:
    def __init__(
        self,
        handler: StageHandler | None = None,
        *,
        name: str = "stage",
        events: list[str] | None = None,
    ) -> None:
        self.handler = handler or (lambda _stage, _payload: {})
        self.calls: list[str] = []
        self.payloads: list[Mapping[str, object]] = []
        self.name = name
        self.events = events if events is not None else []

    async def execute(self, *, stage, payload, identity):  # type: ignore[no-untyped-def]
        assert identity is not None
        self.calls.append(stage)
        self.payloads.append(copy.deepcopy(dict(payload)))
        self.events.append(f"{self.name}:{stage}")
        return copy.deepcopy(dict(self.handler(stage, payload)))


class ControlPort:
    def __init__(self, command: ResearchControlCommand | None = None) -> None:
        self.command = command
        self.polls = 0
        self.settles = 0
        self.consumes = 0

    async def poll(self, *, run_id: str, checkpoint_id: str | None):
        assert run_id
        self.polls += 1
        return self.command

    async def settle(
        self, command_id, *, checkpoint_ns, checkpoint_id, result
    ):
        assert self.command is not None and self.command.command_id == command_id
        payload = self.command.to_json()
        payload["status"] = "settled"
        payload["observed_checkpoint_id"] = checkpoint_id
        payload["result"] = copy.deepcopy(dict(result))
        self.command = ResearchControlCommand.from_json(payload)
        self.settles += 1
        return self.command

    async def consume(
        self, command_id, *, checkpoint_ns, checkpoint_id, result
    ):
        assert self.command is not None and self.command.command_id == command_id
        payload = self.command.to_json()
        payload["status"] = "consumed"
        payload["observed_checkpoint_id"] = checkpoint_id
        payload["result"] = copy.deepcopy(dict(result))
        self.command = ResearchControlCommand.from_json(payload)
        self.consumes += 1
        return self.command


class SnapshotPort:
    async def persist_research_snapshot(
        self,
        *,
        run_id,
        operation_id,
        values,
        execution_identity,
        created_at,
        continue_until,
    ):
        assert execution_identity is not None
        digest = "a" * 64
        return {
            "schema_version": 1,
            "snapshot_hash": digest,
            "manifest_ref": digest,
            "parent_run_id": run_id,
            "dimension_coverages": copy.deepcopy(values["dimension_coverages"]),
            "passage_blob_refs": [],
            "source_families": [],
            "query_fingerprints": copy.deepcopy(
                values.get("executed_query_fingerprints", [])
            ),
            "budget_summary": {},
            "created_at": created_at,
            "continue_until": continue_until,
        }


class BlockingFetchPort:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, *, stage, payload, identity):  # type: ignore[no-untyped-def]
        assert stage == "fetch"
        assert payload["topic"] == "topic"
        assert identity is not None
        self.started.set()
        await self.release.wait()
        return {"quality_score": 0.0}


class ObservingControlPort(ControlPort):
    async def poll(self, *, run_id: str, checkpoint_id: str | None):
        command = await super().poll(run_id=run_id, checkpoint_id=checkpoint_id)
        if command is None:
            return None
        payload = command.to_json()
        payload["status"] = "observed"
        payload["observed_checkpoint_id"] = checkpoint_id
        self.command = ResearchControlCommand.from_json(payload)
        return self.command


def _command(action: str, *, run_id: str = "run-v5") -> ResearchControlCommand:
    now = "2026-07-16T08:00:00+00:00"
    return ResearchControlCommand(
        command_id=f"command-{action}",
        run_id=run_id,
        action=action,  # type: ignore[arg-type]
        idempotency_key=f"key-{action}",
        status="accepted",
        expected_run_version=1,
        observed_checkpoint_id=None,
        payload={},
        result={},
        expires_at="2026-07-16T09:00:00+00:00",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_fetch_observes_generate_now_while_atomic_effect_is_inflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deep_research_v5_nodes, "CONTROL_OBSERVE_INTERVAL_SECONDS", 0.001
    )
    fetch = BlockingFetchPort()
    control = ObservingControlPort(_command("generate_now"))
    identity = NodeExecutionIdentity(
        workflow_name="deep_research",
        workflow_version="v5",
        thread_id="thread-v5",
        run_id="run-v5",
        checkpoint_id="checkpoint-fetch",
        checkpoint_ns="",
        task_id="task-fetch",
        node_id="fetch",
        attempt=1,
    )
    context = WorkflowContext(
        ports={"fetch": fetch, "control": control}, identity=identity
    )
    task = asyncio.create_task(
        fetch_handler(
            {"run_id": "run-v5", "values": {"topic": "topic"}}, context
        )
    )
    await fetch.started.wait()
    while control.polls == 0:
        await asyncio.sleep(0)
    fetch.release.set()

    patch = await task

    assert patch.values["values"]["control_command"]["status"] == "observed"
    assert patch.values["values"]["fetch_result"] == {"quality_score": 0.0}


@pytest.mark.asyncio
async def test_control_cancelled_fetch_settles_as_business_terminal_not_node_failure() -> None:
    clock = VirtualClock(active=125.0)
    context, ports = _ports(clock, control=_command("cancel_settle"))

    def cancel_fetch(_stage: str, _payload: Mapping[str, object]) -> Mapping[str, object]:
        raise ResearchStageCancelled("deadline fenced fetch")

    ports["fetch"].handler = cancel_fetch

    result, _, _ = await _run(context)

    assert result["values"]["fetch_result"] == {
        "status": "cancelled",
        "cancel_reason": "control_settle_fence",
        "cancelled_stage": "fetch",
    }
    assert result["values"]["terminal_status"] == "completed"
    assert result["values"]["delivery_decision"]["status"] == "insufficient_evidence"
    control = context.ports["control"]
    assert isinstance(control, ControlPort)
    assert control.command is not None and control.command.status == "consumed"


def _coverage_payload(payload: Mapping[str, object], covered_count: int) -> list[dict]:
    brief = payload["research_brief"]
    assert isinstance(brief, Mapping)
    dimensions = brief["dimensions"]
    assert isinstance(dimensions, list)
    result: list[dict] = []
    for index, dimension in enumerate(dimensions):
        assert isinstance(dimension, Mapping)
        dimension_id = str(dimension["dimension_id"])
        covered = index < covered_count
        evidence_ids = (
            (f"evidence-{dimension_id}-1", f"evidence-{dimension_id}-2")
            if covered
            else ()
        )
        result.append(
            DimensionCoverage(
                dimension_id=dimension_id,
                status="covered" if covered else "uncovered",
                evidence_passage_ids=evidence_ids,
                winning_evidence_ids=evidence_ids,
                source_family_ids=(
                    (f"family-{dimension_id}-1", f"family-{dimension_id}-2")
                    if covered
                    else ()
                ),
                first_party_satisfied=covered,
                relevance_score=0.9 if covered else 0.0,
                gap_reasons=() if covered else ("missing_evidence",),
            ).to_json()
        )
    return result


def _evidence_candidates(payload: Mapping[str, object], covered_count: int) -> list[dict]:
    brief = payload["research_brief"]
    assert isinstance(brief, Mapping)
    dimensions = brief["dimensions"]
    assert isinstance(dimensions, list)
    result: list[dict] = []
    for index, dimension in enumerate(dimensions[:covered_count]):
        assert isinstance(dimension, Mapping)
        dimension_id = str(dimension["dimension_id"])
        for source_index in (1, 2):
            candidate_id = f"evidence-{dimension_id}-{source_index}"
            url = f"https://official.example/{dimension_id}/{source_index}"
            support_text = " ".join(
                [
                    f"教育维度 {dimension_id} 已有直接证据。",
                    f"教育 {dimension_id} current_state。",
                    f"教育 {dimension_id} driver_change。",
                    f"教育 {dimension_id} impact。",
                    *(
                        f"教育 {dimension_id} 关键判断 {key_index}。"
                        for key_index in range(1, 8)
                    ),
                ]
            )
            result.append(
                {
                    "candidate_id": candidate_id,
                    "dimension_id": dimension_id,
                    "url": url,
                    "canonical_url": url,
                    "title": f"Official evidence {source_index}",
                    "family_id": f"family-{dimension_id}-{source_index}",
                    "source_tier": "first_party",
                    "source_type": "official_document",
                    "relevance": 0.9,
                    "body_text": support_text,
                    "span_text": support_text,
                    "content_hash": "a" * 64,
                    "page_flags": [],
                    "invalid_reason": None,
                    "published_date": "",
                    "admitted": True,
                }
            )
    return result


def _structured_draft(payload: Mapping[str, object]) -> dict:
    brief = payload["research_brief"]
    assert isinstance(brief, Mapping)
    dimensions = brief["dimensions"]
    coverages = payload["dimension_coverages"]
    assert isinstance(dimensions, list) and isinstance(coverages, list)
    analyses: list[dict] = []
    claims: list[dict] = []
    covered_dimensions: list[tuple[Mapping[str, object], Mapping[str, object]]] = []
    for dimension, coverage in zip(dimensions, coverages, strict=True):
        assert isinstance(dimension, Mapping) and isinstance(coverage, Mapping)
        if coverage["status"] != "covered":
            continue
        covered_dimensions.append((dimension, coverage))
        dimension_id = str(dimension["dimension_id"])
        winning = list(coverage["winning_evidence_ids"])
        analyses.append(
            {
                "schema_version": 1,
                "dimension_id": dimension_id,
                "direct_answer": f"教育维度 {dimension_id} 已有直接证据。",
                "fact_claim_ids": [f"fact-{dimension_id}"],
                "inference_claim_ids": [],
                "limitation_claim_ids": [],
                "winning_evidence_ids": winning,
                "relevance_score": 0.9,
                "confidence": "high",
            }
        )
        for kind in ("current_state", "driver_change", "impact"):
            claims.append(
                {
                    "claim_id": f"{kind}-{dimension_id}",
                    "dimension_id": dimension_id,
                    "text": f"教育 {dimension_id} {kind}。",
                    "kind": kind,
                    "source_ids": winning[:1],
                    "supported_fact_refs": winning[:1],
                    "intent_tokens": ["教育"],
                    "is_inference": False,
                    "metadata_pseudo_judgment": False,
                }
            )
    key_count = min(7, max(3, len(covered_dimensions))) if covered_dimensions else 0
    for index in range(key_count):
        dimension, coverage = covered_dimensions[index % len(covered_dimensions)]
        dimension_id = str(dimension["dimension_id"])
        winning = list(coverage["winning_evidence_ids"])
        claims.append(
            {
                "claim_id": f"key-{index}-{dimension_id}",
                "dimension_id": dimension_id,
                "text": f"教育 {dimension_id} 关键判断 {index + 1}。",
                "kind": "key_judgment",
                "source_ids": winning[:1],
                "supported_fact_refs": winning[:1],
                "intent_tokens": ["教育"],
                "is_inference": False,
                "metadata_pseudo_judgment": False,
            }
        )
    return {"analyses": analyses, "claims": claims}


def _ports(
    clock: VirtualClock,
    *,
    fetch_covered: int = 0,
    gap_handler: StageHandler | None = None,
    query_strategy_handler: StageHandler | None = None,
    quality_handler: StageHandler | None = None,
    modeling_handler: StageHandler | None = None,
    control: ResearchControlCommand | None = None,
) -> tuple[WorkflowContext, dict[str, StagePort]]:
    def llm_handler(stage: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        if stage == "modeling" and modeling_handler is not None:
            return modeling_handler(stage, payload)
        if stage == "query_strategy" and query_strategy_handler is not None:
            return query_strategy_handler(stage, payload)
        if stage == "report_synthesis":
            return _structured_draft(payload)
        if stage == "quality_audit":
            return (quality_handler or (lambda _s, _p: {"route": "persist"}))(
                stage, payload
            )
        if stage == "targeted_repair":
            return {"route": "quality_audit"}
        return {}

    def fetch_handler(stage: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        if stage == "fetch":
            return {
                "coverages": _coverage_payload(payload, fetch_covered),
                "evidence_candidates": _evidence_candidates(payload, fetch_covered),
                "quality_score": 10.0 if fetch_covered else 0.0,
            }
        if gap_handler is not None:
            return gap_handler(stage, payload)
        return {}

    events: list[str] = []
    ports = {
        "llm": StagePort(llm_handler, name="llm", events=events),
        "search": StagePort(gap_handler, name="search", events=events),
        "fetch": StagePort(fetch_handler, name="fetch", events=events),
        "artifact": StagePort(
            lambda _s, _p: {"report_ref": "artifact:report-v5"},
            name="artifact",
            events=events,
        ),
    }
    context = WorkflowContext(
        ports={
            **ports,
            "clock": clock,
            "control": ControlPort(control),
            "snapshot": SnapshotPort(),
        }
    )
    return context, ports


@pytest.mark.asyncio
async def test_invalid_advisory_modeling_falls_back_to_deterministic_brief() -> None:
    clock = VirtualClock(active=125.0)
    context, ports = _ports(
        clock,
        control=_command("generate_now"),
        modeling_handler=lambda _stage, _payload: {
            "status": "insufficient_evidence_for_synthesis",
            "query_strategy": {"queries": []},
            "modeling_output": {
                "recommended_research_dimensions": [
                    {"dimension_id": "D1", "name": "总体规模与结构"}
                ]
            },
        },
    )

    result, _, _ = await _run(context)

    assert result["values"]["terminal_status"] == "completed"
    assert result["values"]["modeling_fallback"] == {
        "reason": "invalid_modeling_output_after_repair",
        "validation_errors": [
            "unknown modeling fields: ['recommended_research_dimensions']",
            "unknown modeling fields: ['recommended_research_dimensions']",
        ],
    }
    assert ports["llm"].calls.count("modeling") == 2
    assert ports["llm"].payloads[1]["repair_attempt"] == 1
    assert result["values"]["research_brief"]["profile"] == "policy_education"


@pytest.mark.asyncio
async def test_committed_non_json_modeling_output_gets_one_bounded_repair() -> None:
    calls = 0

    def modeling(_stage, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ResearchStageOutputError("v5 modeling returned non-JSON content")
        assert payload["repair_attempt"] == 1
        return {
            "modeling_output": {
                "subjects": ["primary education"],
                "expected_decision": "assess education evidence",
                "dimensions": [
                    {
                        "dimension_id": f"support_{index}",
                        "question": f"What supporting evidence {index}?",
                        "importance": "supporting" if index > 1 else "core",
                        "expected_source_types": ["official"],
                        "query_targets": [f"support target {index}"],
                        "first_party_required": False,
                        "not_applicable_when": ["user_explicitly_excludes_dimension"],
                    }
                    for index in range(1, 4)
                ],
            }
        }

    context, ports = _ports(
        VirtualClock(active=125.0),
        control=_command("generate_now"),
        modeling_handler=modeling,
    )
    result, _, _ = await _run(context)
    assert calls == 2
    assert ports["llm"].payloads[1]["repair_attempt"] == 1
    assert "modeling_fallback" not in result["values"]


@pytest.mark.asyncio
async def test_two_committed_non_json_modeling_outputs_use_safe_deterministic_fallback() -> None:
    def modeling(_stage, _payload):
        raise ResearchStageOutputError("v5 modeling returned non-JSON content")

    context, ports = _ports(
        VirtualClock(active=125.0),
        control=_command("generate_now"),
        modeling_handler=modeling,
    )

    result, _, _ = await _run(context)

    assert ports["llm"].calls.count("modeling") == 2
    assert ports["llm"].payloads[1]["repair_attempt"] == 1
    assert result["values"]["model_result"] == {}
    assert result["values"]["modeling_fallback"] == {
        "reason": "invalid_modeling_output_after_repair",
        "validation_errors": [
            "v5 modeling returned non-JSON content",
            "v5 modeling returned non-JSON content",
        ],
    }
    assert result["values"]["research_brief"]["profile"] == "policy_education"


async def _run(context: WorkflowContext, store: InMemoryNativeCheckpointStore | None = None):
    store = store or InMemoryNativeCheckpointStore()
    executable = DEEP_RESEARCH_V5.bind(checkpointer=store)
    state = deep_research_initial_state(
        topic="中国小学教育现状和国家下一步计划",
        run_id="run-v5",
        thread_id="thread-v5",
    )
    result = await executable.ainvoke(
        state,
        context,
        thread_id="thread-v5",
        run_id="run-v5",
    )
    return result, store, executable


def test_fixture_pins_graph_loop_budgets() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["fixture_schema_version"] == 1
    assert fixture["budgets"] == {
        "soft_checkpoint_seconds": 300.0,
        "lease_seconds": 120.0,
        "automatic_cap_seconds": 900.0,
        "plateau_rounds": 2,
        "gap_work_iterations": 64,
        "repair_iterations": 16,
        "max_supersteps": 384,
        "recursion_limit": 512,
    }
    assert DEEP_RESEARCH_V5_DEFINITION.prompt_manifest["llm_roles"] == [
        "modeling",
        "query_strategy",
        "dimension_analysis",
        "report_synthesis",
        "quality_audit",
        "targeted_repair",
    ]


def test_report_synthesis_payload_is_bounded_to_admitted_evidence() -> None:
    huge_body = "full fetched page " * 20_000
    payload = _report_synthesis_payload({
        "research_brief": {"schema_version": 1, "user_question": "compare models"},
        "dimension_coverages": [{"dimension_id": "ranking", "status": "partial"}],
        "synthesis_route": "partial_synthesis",
        "dimension_analysis_result": None,
        "search_result": {"raw": huge_body},
        "fetch_result": {"raw": huge_body},
        "evidence_candidates": [
            {
                "candidate_id": "accepted",
                "dimension_id": "ranking",
                "title": "Benchmark",
                "family_id": "family-1",
                "source_tier": "secondary",
                "source_type": "article",
                "published_date": "2026-07-17",
                "relevance": 0.8,
                "span_text": "direct supporting excerpt",
                "body_text": huge_body,
                "admitted": True,
            },
            {
                "candidate_id": "rejected",
                "dimension_id": "ranking",
                "body_text": huge_body,
                "admitted": False,
            },
        ],
    })

    encoded = json.dumps(payload, ensure_ascii=False)
    assert set(payload) == {
        "research_brief",
        "dimension_coverages",
        "synthesis_route",
        "dimension_analysis_result",
        "evidence_candidates",
    }
    assert [item["candidate_id"] for item in payload["evidence_candidates"]] == [
        "accepted"
    ]
    assert payload["evidence_candidates"][0]["evidence_text"] == (
        "direct supporting excerpt"
    )
    assert "full fetched page" not in encoded
    assert len(encoded.encode("utf-8")) < 16_000


def test_workflow_context_accepts_independent_control_port_without_changing_legacy_ports() -> None:
    existing = WorkflowContext(ports={"llm": object(), "search": object(), "fetch": object()})
    assert set(existing.ports) == {"llm", "search", "fetch"}
    control = ControlPort()
    assert WorkflowContext(ports={"control": control}).port("control") is control
    with pytest.raises(WorkflowDefinitionError, match="Unknown workflow ports"):
        WorkflowContext(ports={"not-a-port": object()})


@pytest.mark.asyncio
async def test_sc_lease_then_two_no_gain_rounds_plateau_and_checkpoint() -> None:
    clock = VirtualClock(active=300.0)

    def query_strategy(
        _stage: str, payload: Mapping[str, object]
    ) -> Mapping[str, object]:
        work_item = payload["work_item"]
        assert isinstance(work_item, Mapping)
        return {
            "queries": [
                {
                    "dimension_id": str(work_item["dimension_id"]),
                    "query_family": "general",
                    "comparison_axis": "none",
                    "source_target": str(work_item["source_target"]),
                    "query": "adaptive query after deterministic no gain",
                    "freshness_window": "2021-07-16..2026-07-16",
                }
            ]
        }

    context, ports = _ports(
        clock, fetch_covered=1, query_strategy_handler=query_strategy
    )
    result, store, _ = await _run(context)

    loop = LoopPolicyState.from_json(result["values"]["loop_policy"])
    assert loop.lease_count == 1
    assert loop.control_mode == "plateau_settling"
    assert result["loop_counters"]["gap_work_iterations"] == 2
    assert len(result["values"]["committed_gap_work_ids"]) == 2
    assert ports["search"].calls.count("gap_work") == 2
    assert ports["llm"].calls.count("query_strategy") == 1
    events = ports["llm"].events
    assert events.index("search:gap_work") < events.index("llm:query_strategy")
    gap_payloads = [
        payload
        for stage, payload in zip(ports["search"].calls, ports["search"].payloads)
        if stage == "gap_work"
    ]
    assert gap_payloads[1]["work_item"]["query"] == (
        "adaptive query after deterministic no gain"
    )
    assert result["values"]["terminal_status"] == "completed"
    assert result["values"]["terminal_public"] == {
        "delivery_status": "insufficient_evidence",
        "action_matrix": [{"action_id": "continue_research", "enabled": True}],
    }
    assert store.snapshot is not None and not store.snapshot.frontier


@pytest.mark.asyncio
async def test_malformed_query_strategy_falls_through_to_safe_delivery() -> None:
    clock = VirtualClock(active=300.0)
    context, ports = _ports(
        clock,
        fetch_covered=1,
        query_strategy_handler=lambda _stage, _payload: {
            "queries": ["not a dimension-query object"]
        },
    )

    result, _, _ = await _run(context)

    assert ports["llm"].calls.count("query_strategy") == 1
    assert result["values"]["query_strategy_queries"] == []
    assert result["values"]["query_strategy_diagnostics"] == {
        "invalid_query_count": 1,
        "valid_query_count": 0,
    }
    assert result["values"]["terminal_status"] == "completed"
    assert result["values"]["delivery_decision"]["status"] in {
        "partial",
        "insufficient_evidence",
    }


@pytest.mark.asyncio
async def test_sc_cap_allows_started_atomic_work_to_finish_then_forbids_more() -> None:
    clock = VirtualClock(active=899.0)
    gap_calls = 0

    def gap(stage: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        nonlocal gap_calls
        if stage != "gap_work":
            return {}
        gap_calls += 1
        clock.active = 900.0
        work_item = payload["work_item"]
        assert isinstance(work_item, Mapping)
        dimension_id = str(work_item["dimension_id"])
        coverage = DimensionCoverage(
            dimension_id,
            "covered",
            (f"passage-{dimension_id}",),
            (f"evidence-{dimension_id}",),
            (f"family-{dimension_id}",),
            True,
            0.9,
        )
        coverages = [
            copy.deepcopy(dict(item))
            for item in payload["dimension_coverages"]
            if isinstance(item, Mapping)
        ]
        coverages = [
            coverage.to_json()
            if item["dimension_id"] == dimension_id
            else item
            for item in coverages
        ]
        return {
            "coverages": coverages,
            "quality_score": 20.0,
            "admission_decisions": [{
                "candidate_id": "candidate-gap",
                "dimension_id": dimension_id,
                "accepted": False,
                "stage": "passage_admission",
                "reason": "dimension_relevance_below_threshold",
            }],
            "rejection_reason_counts": {"dimension_relevance_below_threshold": 1},
            "rejection_summary_by_dimension": [{
                "dimension_id": dimension_id,
                "reason_counts": {"dimension_relevance_below_threshold": 1},
            }],
        }

    context, _ = _ports(clock, fetch_covered=1, gap_handler=gap)
    result, _, _ = await _run(context)
    loop = LoopPolicyState.from_json(result["values"]["loop_policy"])
    assert gap_calls == 1
    assert loop.control_mode == "automatic_cap_settling"
    assert loop.prohibit_new_upstream is True
    assert loop.current_progress.coverage_points == 2.0
    assert result["loop_counters"]["gap_work_iterations"] == 1
    assert result["values"]["rejection_reason_counts"] == {
        "dimension_relevance_below_threshold": 1
    }
    assert result["values"]["rejection_summary_by_dimension"][0]["reason_counts"] == {
        "dimension_relevance_below_threshold": 1
    }


@pytest.mark.asyncio
async def test_sc_now_fences_gap_and_is_idempotent_across_later_nodes() -> None:
    clock = VirtualClock(active=125.0)
    command = _command("generate_now")
    context, ports = _ports(clock, control=command)
    result, _, _ = await _run(context)

    loop = LoopPolicyState.from_json(result["values"]["loop_policy"])
    assert loop.control_mode == "generate_now_settling"
    assert loop.settle_deadline_active_seconds == 155.0
    assert ports["search"].calls.count("gap_work") == 0
    assert ports["llm"].calls.count("dimension_analysis") == 0
    assert ports["llm"].calls.count("report_synthesis") == 0
    assert ports["artifact"].calls.count("persist") == 0
    assert result["values"]["delivery_decision"]["status"] == "insufficient_evidence"
    assert result["values"]["terminal_status"] == "completed"


@pytest.mark.asyncio
async def test_cancel_settle_fences_upstream_but_preserves_engine_business_terminal_split() -> None:
    clock = VirtualClock(active=125.0)
    context, ports = _ports(clock, control=_command("cancel_settle"))
    result, _, _ = await _run(context)

    loop = LoopPolicyState.from_json(result["values"]["loop_policy"])
    assert loop.control_mode == "cancelled"
    assert ports["search"].calls.count("gap_work") == 0
    assert result["values"]["terminal_status"] == "completed"
    assert result["values"]["delivery_decision"]["status"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_cancel_settle_with_committed_evidence_uses_no_new_llm_and_consumes_control() -> None:
    clock = VirtualClock(active=125.0)
    context, ports = _ports(
        clock, fetch_covered=1, control=_command("cancel_settle")
    )
    control = context.ports["control"]
    assert isinstance(control, ControlPort)
    result, _, _ = await _run(context)

    assert ports["llm"].calls.count("modeling") == 2
    for role in (
        "query_strategy",
        "dimension_analysis",
        "report_synthesis",
        "quality_audit",
        "targeted_repair",
    ):
        assert ports["llm"].calls.count(role) == 0
    assert ports["search"].calls.count("gap_work") == 0
    assert ports["artifact"].calls.count("persist") == 0
    assert result["values"]["terminal_status"] == "completed"
    assert result["values"]["delivery_decision"]["status"] == "insufficient_evidence"
    assert control.settles == 1
    assert control.consumes == 1
    assert control.command is not None and control.command.status == "consumed"


@pytest.mark.asyncio
async def test_model_cannot_force_repair_after_deterministic_gate_passes() -> None:
    clock = VirtualClock(active=1.0)
    context, ports = _ports(
        clock,
        fetch_covered=6,
        quality_handler=lambda _s, _p: {"route": "repair_work"},
    )
    result, store, _ = await _run(context)

    assert result["loop_counters"]["repair_iterations"] == 0
    assert ports["llm"].calls.count("dimension_analysis") == 1
    assert ports["llm"].calls.count("report_synthesis") == 1
    assert ports["llm"].calls.count("targeted_repair") == 0
    assert result["values"]["terminal_status"] == "completed"
    assert store.snapshot is not None and store.snapshot.step < 384


@pytest.mark.asyncio
async def test_valid_partial_persists_without_non_terminal_report_repair() -> None:
    clock = VirtualClock(active=125.0)
    context, ports = _ports(
        clock,
        fetch_covered=3,
        control=_command("generate_now"),
        quality_handler=lambda _s, _p: {"route": "repair_work"},
    )
    result, store, _ = await _run(context)

    assert result["values"]["delivery_decision"]["status"] == "partial"
    assert result["loop_counters"]["repair_iterations"] == 0
    assert ports["llm"].calls.count("targeted_repair") == 0
    assert result["values"]["terminal_status"] == "completed"
    assert store.snapshot is not None and store.snapshot.step < 384


@pytest.mark.asyncio
async def test_missing_artifact_report_ref_fails_closed_without_placeholder() -> None:
    clock = VirtualClock(active=1.0)
    context, ports = _ports(clock, fetch_covered=6)
    ports["artifact"].handler = lambda _stage, _payload: {}
    with pytest.raises(WorkflowNodeError, match="workflow_node:persist:permanent"):
        await _run(context)


@pytest.mark.asyncio
async def test_terminal_checkpoint_replay_does_not_repeat_external_effects() -> None:
    clock = VirtualClock(active=125.0)
    context, ports = _ports(clock, control=_command("generate_now"))
    result, store, executable = await _run(context)
    calls_before = {name: tuple(port.calls) for name, port in ports.items()}

    replayed = await executable.ainvoke(
        deep_research_initial_state(topic="ignored", run_id="run-v5"),
        context,
        thread_id="thread-v5",
        run_id="run-v5",
    )
    assert replayed == result
    assert {name: tuple(port.calls) for name, port in ports.items()} == calls_before
    assert store.snapshot is not None and not store.snapshot.frontier


@pytest.mark.asyncio
async def test_lease_takeover_projects_from_durable_active_time_without_monotonic_state() -> None:
    clock = VirtualClock(active=300.0)
    context, _ = _ports(clock, fetch_covered=1)
    state = deep_research_initial_state(topic="教育", run_id="run-v5")
    # Run enough deterministic setup through a completed graph, then use the
    # durable loop payload as a restart/takeover input with a new clock object.
    completed, _, _ = await _run(context)
    loop = LoopPolicyState.from_json(completed["values"]["loop_policy"])
    takeover = VirtualClock(active=loop.accumulated_active_seconds + 10.0)
    takeover_context, _ = _ports(takeover)
    state["values"] = copy.deepcopy(completed["values"])
    state["values"]["gap_evaluate_route"] = None
    patch = await gap_evaluate_handler(state, takeover_context)
    resumed = LoopPolicyState.from_json(patch.to_dict()["values"]["loop_policy"])
    assert resumed.accumulated_active_seconds == loop.accumulated_active_seconds + 10.0
    assert "monotonic" not in json.dumps(resumed.to_json())


@pytest.mark.asyncio
async def test_skipped_repair_result_does_not_consume_static_budget() -> None:
    clock = VirtualClock(active=1.0)
    context, _ = _ports(clock, fetch_covered=6)
    completed, _, _ = await _run(context)
    state = copy.deepcopy(completed)
    state["loop_counters"]["repair_iterations"] = 0
    state["values"]["committed_repair_ids"] = []
    state["values"]["active_repair_id"] = "repair-skipped"
    state["values"]["repair_result"] = {
        "status": "skipped_fence",
        "repair_id": "repair-skipped",
    }

    patch = await repair_join_handler(state, context)
    updated = patch.to_dict()
    assert updated["loop_counters"]["repair_iterations"] == 0
    assert updated["values"]["committed_repair_ids"] == []


def test_native_terminal_projection_keeps_v4_and_strictly_adds_v5() -> None:
    v4_public = {
        "metrics": {
            "actual_requests": 0,
            "hits": 0,
            "empty": 0,
            "timeouts": 0,
            "cooldown_skips": 0,
            "busy_skips": 0,
            "queue_timeouts": 0,
            "probes": 0,
            "rescue_considered_count": 0,
            "rescue_executed_count": 0,
            "candidates": 0,
        },
        "diagnostic_codes": [],
        "skipped_stage_ids": [],
    }
    v4_intents = NativeWorkflowExecutable._terminal_intents(
        {
            "workflow_name": "deep_research",
            "workflow_version": "v4",
            "values": {"terminal_public": v4_public},
        },
        run_id="run-v4",
        status="completed",
        error=None,
        recovery_action=None,
    )
    assert v4_intents[0]["payload"]["metrics"] == v4_public["metrics"]

    v5_intents = NativeWorkflowExecutable._terminal_intents(
        {
            "workflow_name": "deep_research",
            "workflow_version": "v5",
            "values": {"terminal_public": {"delivery_status": "partial"}},
        },
        run_id="run-v5",
        status="completed",
        error=None,
        recovery_action=None,
    )
    assert v5_intents[0]["payload"]["delivery_status"] == "partial"

    for public in (
        {"delivery_status": "unknown"},
        {"delivery_status": "partial", "extra": True},
    ):
        with pytest.raises(InvalidStatePatch, match="invalid"):
            NativeWorkflowExecutable._terminal_intents(
                {
                    "workflow_name": "deep_research",
                    "workflow_version": "v5",
                    "values": {"terminal_public": public},
                },
                run_id="run-v5",
                status="completed",
                error=None,
                recovery_action=None,
            )

    with pytest.raises(InvalidStatePatch, match="supported deep_research version"):
        NativeWorkflowExecutable._terminal_intents(
            {
                "workflow_name": "deep_research",
                "workflow_version": "v6",
                "values": {"terminal_public": {"delivery_status": "completed"}},
            },
            run_id="run-v6",
            status="completed",
            error=None,
            recovery_action=None,
        )
