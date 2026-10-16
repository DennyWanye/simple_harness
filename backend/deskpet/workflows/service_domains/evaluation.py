"""Evaluations, experiment comparison and run detail."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..evaluation.models import EvaluationOutcome, HumanEvaluationRecord, PairwiseEvaluationRecord
from .core import WorkflowServiceCore
from .support import WorkflowServiceError, _plain


class EvaluationDetailMixin(WorkflowServiceCore):
    """Evaluations, experiment comparison and run detail."""

    __slots__ = ()

    @staticmethod
    def _evaluation_dict(record: object) -> dict[str, Any]:
        value = _plain(record)
        if isinstance(value, Mapping) and isinstance(value.get("outcome"), Mapping):
            return {
                "evaluation_id": value["evaluation_id"],
                "trace_id": value["trace_id"],
                "run_id": value.get("run_id"),
                "span_id": value.get("span_id"),
                "created_at": value["created_at"],
                **dict(value["outcome"]),
            }
        return dict(value)

    async def submit_evaluation(
        self,
        *,
        trace_id: str,
        evaluator_name: str,
        evaluator_version: str,
        evaluator_type: str,
        verdict: str,
        score: float | None = None,
        labels: Sequence[str] = (),
        explanation: str | None = None,
        evidence_refs: Sequence[str] = (),
        run_id: str | None = None,
        span_id: str | None = None,
        evaluation_id: str | None = None,
        degraded: bool = False,
        left_version_key: str | None = None,
        right_version_key: str | None = None,
    ) -> dict[str, Any]:
        evaluator_type = str(evaluator_type).strip().lower()
        common = dict(
            trace_id=trace_id,
            evaluator_name=evaluator_name,
            evaluator_version=evaluator_version,
            score=score,
            labels=tuple(labels),
            explanation=explanation,
            evidence_refs=tuple(evidence_refs),
            run_id=run_id,
            span_id=span_id,
            evaluation_id=evaluation_id,
        )
        if evaluator_type == "human":
            record = await self.evaluation_store.record_human(
                HumanEvaluationRecord(verdict=verdict, **common)
            )
        elif evaluator_type == "pairwise":
            if not left_version_key or not right_version_key:
                raise WorkflowServiceError(
                    "invalid_evaluation",
                    "Pairwise evaluation requires both version keys",
                )
            record = await self.evaluation_store.record_pairwise(
                PairwiseEvaluationRecord(
                    winner=verdict,
                    left_version_key=left_version_key,
                    right_version_key=right_version_key,
                    **common,
                )
            )
        else:
            record = await self.evaluation_store.record_evaluation(
                trace_id=trace_id,
                run_id=run_id,
                span_id=span_id,
                evaluation_id=evaluation_id,
                outcome=EvaluationOutcome(
                    evaluator_name=evaluator_name,
                    evaluator_version=evaluator_version,
                    evaluator_type=evaluator_type,
                    verdict=verdict,
                    score=score,
                    labels=tuple(labels),
                    explanation=explanation,
                    evidence_refs=tuple(evidence_refs),
                    degraded=degraded,
                ),
            )
        return self._evaluation_dict(record)

    async def compare_experiments(
        self, left_experiment_id: str, right_experiment_id: str
    ) -> dict[str, Any]:
        return _plain(await self.evaluation_store.compare(left_experiment_id, right_experiment_id))

    async def run_detail(self, run_id: str) -> dict[str, Any]:
        run = await self._require_run(run_id)
        declared_nodes, edges = self._definition_projection(run)
        by_id = {item["id"]: item for item in declared_nodes}
        for item in await self._node_projection(run_id):
            by_id[item["id"]] = {**by_id.get(item["id"], {}), **item}
        trace = await self.trace_tree(run_id=run_id)
        history = await self.checkpoint_history(run_id)
        evaluations = [
            self._evaluation_dict(item)
            for item in await self.evaluation_store.list_evaluations(run_id=run_id)
        ]
        decisions = [
            self._decision_dict(item)
            for item in await self.human_store.list_open_decisions(run_id=run_id)
        ]
        # Native Harness children persist their suspension boundary in
        # ``execution_decisions``.  ContextTrace is also the recovery UI for
        # those execution-owned workflow runs, so hiding that second decision
        # store leaves a real child permanently parked after restart even
        # though the inspector can see the waiting run.  Project the same
        # immutable decision fence here; resolution is routed back through the
        # RunKernel in ``main.py`` rather than the legacy human store.
        list_execution_decisions = getattr(self._owner_uow, "list_open_decision_projections", None)
        if callable(list_execution_decisions):
            for projection in await list_execution_decisions(str(run["session_id"])):
                decision = projection["decision"]
                request = decision.request
                if request.run_id != run_id:
                    continue
                raw_prompt = _plain(request.prompt)
                nested_prompt = raw_prompt.get("prompt")
                display_prompt = (
                    dict(nested_prompt) if isinstance(nested_prompt, Mapping) else raw_prompt
                )
                decisions.append(
                    {
                        "decision_id": request.decision_id,
                        "run_id": request.run_id,
                        "kind": request.kind.value,
                        "status": decision.status.value,
                        "prompt": display_prompt,
                        "nonce": request.nonce,
                        "version": decision.decision_version,
                        "expires_at": request.expires_at,
                        "created_at": decision.created_at,
                        "execution_decision": True,
                    }
                )
        deliveries = await self.list_deliveries(run_id=run_id)
        delivery_aggregate = await self.outbox.delivery_aggregate(run_id)
        return {
            "run_id": run_id,
            "run": self._run_summary(run),
            "nodes": list(by_id.values()),
            "edges": edges,
            "spans": trace["spans"],
            "checkpoints": history["checkpoints"],
            "evaluations": evaluations,
            "decisions": decisions,
            "deliveries": deliveries["items"],
            "delivery_aggregate": delivery_aggregate,
        }

    get_run_detail = run_detail
    get_run = run_detail
