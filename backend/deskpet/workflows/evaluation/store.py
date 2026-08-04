"""SQLite persistence for datasets, experiments, results, and evaluations."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import aiosqlite

from ..store.schema import initialize_workflow_db
from .models import (
    EvaluationDataset,
    EvaluationExample,
    EvaluationExperiment,
    EvaluationOutcome,
    EvaluationRecord,
    ExperimentAggregate,
    ExperimentComparison,
    ExperimentResult,
    HumanEvaluationRecord,
    PairwiseEvaluationRecord,
)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _load_json(value: str) -> Any:
    return json.loads(value)


class EvaluationStore:
    """Persist evaluation data in the existing ``workflow.db`` tables."""

    def __init__(self, path: str | Path, *, clock=time.time) -> None:
        self.path = Path(path)
        self._clock = clock

    async def initialize(self) -> None:
        await initialize_workflow_db(self.path)

    async def _connect(self) -> aiosqlite.Connection:
        await self.initialize()
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        return db

    async def record_evaluation(
        self,
        *,
        trace_id: str,
        outcome: EvaluationOutcome,
        run_id: str | None = None,
        span_id: str | None = None,
        evaluation_id: str | None = None,
        created_at: float | None = None,
    ) -> EvaluationRecord:
        if not str(trace_id).strip():
            raise ValueError("trace_id must not be empty")
        record = EvaluationRecord(
            evaluation_id=evaluation_id or uuid.uuid4().hex,
            trace_id=str(trace_id),
            run_id=run_id,
            span_id=span_id,
            outcome=outcome,
            created_at=self._clock() if created_at is None else float(created_at),
        )
        db = await self._connect()
        try:
            await db.execute(
                """INSERT INTO evaluations(
                    evaluation_id,trace_id,run_id,span_id,evaluator_name,evaluator_version,
                    evaluator_type,score,verdict,labels_json,explanation,evidence_refs_json,
                    degraded,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.evaluation_id,
                    record.trace_id,
                    record.run_id,
                    record.span_id,
                    outcome.evaluator_name,
                    outcome.evaluator_version,
                    outcome.evaluator_type,
                    outcome.score,
                    outcome.verdict,
                    _json(outcome.labels),
                    outcome.explanation,
                    _json(outcome.evidence_refs),
                    int(outcome.degraded),
                    record.created_at,
                ),
            )
            await db.commit()
            return record
        finally:
            await db.close()

    async def record_human(self, record: HumanEvaluationRecord) -> EvaluationRecord:
        return await self.record_evaluation(
            trace_id=record.trace_id,
            run_id=record.run_id,
            span_id=record.span_id,
            evaluation_id=record.evaluation_id,
            created_at=record.created_at,
            outcome=record.to_outcome(),
        )

    async def record_pairwise(self, record: PairwiseEvaluationRecord) -> EvaluationRecord:
        return await self.record_evaluation(
            trace_id=record.trace_id,
            run_id=record.run_id,
            span_id=record.span_id,
            evaluation_id=record.evaluation_id,
            created_at=record.created_at,
            outcome=record.to_outcome(),
        )

    async def get_evaluation(self, evaluation_id: str) -> EvaluationRecord | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute("SELECT * FROM evaluations WHERE evaluation_id=?", (evaluation_id,))
            ).fetchone()
            return self._evaluation_from_row(row) if row is not None else None
        finally:
            await db.close()

    async def list_evaluations(
        self,
        *,
        trace_id: str | None = None,
        run_id: str | None = None,
    ) -> list[EvaluationRecord]:
        clauses: list[str] = []
        params: list[str] = []
        if trace_id is not None:
            clauses.append("trace_id=?")
            params.append(trace_id)
        if run_id is not None:
            clauses.append("run_id=?")
            params.append(run_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    f"SELECT * FROM evaluations{where} ORDER BY created_at,evaluation_id",
                    params,
                )
            ).fetchall()
            return [self._evaluation_from_row(row) for row in rows]
        finally:
            await db.close()

    @staticmethod
    def _evaluation_from_row(row: aiosqlite.Row) -> EvaluationRecord:
        return EvaluationRecord(
            evaluation_id=str(row["evaluation_id"]),
            trace_id=str(row["trace_id"]),
            run_id=row["run_id"],
            span_id=row["span_id"],
            outcome=EvaluationOutcome(
                evaluator_name=str(row["evaluator_name"]),
                evaluator_version=str(row["evaluator_version"]),
                evaluator_type=str(row["evaluator_type"]),
                score=row["score"],
                verdict=str(row["verdict"]),
                labels=tuple(_load_json(row["labels_json"])),
                explanation=row["explanation"],
                evidence_refs=tuple(_load_json(row["evidence_refs_json"])),
                degraded=bool(row["degraded"]),
            ),
            created_at=float(row["created_at"]),
        )

    async def create_dataset(
        self,
        *,
        name: str,
        version: str,
        metadata: dict[str, Any] | None = None,
        dataset_id: str | None = None,
    ) -> EvaluationDataset:
        now = self._clock()
        db = await self._connect()
        try:
            existing = await (
                await db.execute(
                    "SELECT * FROM eval_datasets WHERE name=? AND version=?",
                    (name, version),
                )
            ).fetchone()
            if existing is not None:
                return self._dataset_from_row(existing)
            item = EvaluationDataset(
                dataset_id=dataset_id or uuid.uuid4().hex,
                name=name,
                version=version,
                metadata=dict(metadata or {}),
                created_at=now,
            )
            await db.execute(
                "INSERT INTO eval_datasets(dataset_id,name,version,metadata_json,created_at) VALUES(?,?,?,?,?)",
                (item.dataset_id, item.name, item.version, _json(item.metadata), item.created_at),
            )
            await db.commit()
            return item
        finally:
            await db.close()

    async def get_dataset(self, dataset_id: str) -> EvaluationDataset | None:
        db = await self._connect()
        try:
            row = await (
                await db.execute("SELECT * FROM eval_datasets WHERE dataset_id=?", (dataset_id,))
            ).fetchone()
            return self._dataset_from_row(row) if row is not None else None
        finally:
            await db.close()

    @staticmethod
    def _dataset_from_row(row: aiosqlite.Row) -> EvaluationDataset:
        return EvaluationDataset(
            dataset_id=str(row["dataset_id"]),
            name=str(row["name"]),
            version=str(row["version"]),
            metadata=dict(_load_json(row["metadata_json"])),
            created_at=float(row["created_at"]),
        )

    async def add_example(
        self,
        *,
        dataset_id: str,
        input_data: dict[str, Any],
        expected: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        example_id: str | None = None,
    ) -> EvaluationExample:
        item = EvaluationExample(
            example_id=example_id or uuid.uuid4().hex,
            dataset_id=dataset_id,
            input_data=dict(input_data),
            expected=dict(expected),
            metadata=dict(metadata or {}),
            created_at=self._clock(),
        )
        db = await self._connect()
        try:
            await db.execute(
                """INSERT INTO eval_examples(
                    example_id,dataset_id,input_json,expected_json,metadata_json,created_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    item.example_id,
                    item.dataset_id,
                    _json(item.input_data),
                    _json(item.expected),
                    _json(item.metadata),
                    item.created_at,
                ),
            )
            await db.commit()
            return item
        finally:
            await db.close()

    async def list_examples(self, dataset_id: str) -> list[EvaluationExample]:
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    "SELECT * FROM eval_examples WHERE dataset_id=? ORDER BY example_id",
                    (dataset_id,),
                )
            ).fetchall()
            return [
                EvaluationExample(
                    example_id=str(row["example_id"]),
                    dataset_id=str(row["dataset_id"]),
                    input_data=dict(_load_json(row["input_json"])),
                    expected=dict(_load_json(row["expected_json"])),
                    metadata=dict(_load_json(row["metadata_json"])),
                    created_at=float(row["created_at"]),
                )
                for row in rows
            ]
        finally:
            await db.close()

    async def start_experiment(
        self,
        *,
        dataset_id: str,
        version_key: str,
        config: dict[str, Any] | None = None,
        experiment_id: str | None = None,
    ) -> EvaluationExperiment:
        item = EvaluationExperiment(
            experiment_id=experiment_id or uuid.uuid4().hex,
            dataset_id=dataset_id,
            version_key=version_key,
            config=dict(config or {}),
            status="running",
            started_at=self._clock(),
        )
        db = await self._connect()
        try:
            await db.execute(
                """INSERT INTO eval_experiments(
                    experiment_id,dataset_id,version_key,config_json,status,started_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    item.experiment_id,
                    item.dataset_id,
                    item.version_key,
                    _json(item.config),
                    item.status,
                    item.started_at,
                ),
            )
            await db.commit()
            return item
        finally:
            await db.close()

    async def finish_experiment(self, experiment_id: str, *, status: str = "completed") -> None:
        db = await self._connect()
        try:
            cursor = await db.execute(
                "UPDATE eval_experiments SET status=?,ended_at=? WHERE experiment_id=?",
                (status, self._clock(), experiment_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(experiment_id)
            await db.commit()
        finally:
            await db.close()

    async def record_result(self, result: ExperimentResult) -> None:
        db = await self._connect()
        try:
            await db.execute(
                """INSERT INTO eval_results(
                    experiment_id,example_id,run_id,evaluation_id,status,score,latency_ms,
                    error_taxonomy,result_json
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(experiment_id,example_id) DO UPDATE SET
                    run_id=excluded.run_id,evaluation_id=excluded.evaluation_id,
                    status=excluded.status,score=excluded.score,latency_ms=excluded.latency_ms,
                    error_taxonomy=excluded.error_taxonomy,result_json=excluded.result_json""",
                (
                    result.experiment_id,
                    result.example_id,
                    result.run_id,
                    result.evaluation_id,
                    result.status,
                    result.score,
                    result.latency_ms,
                    result.error_taxonomy,
                    _json(result.result),
                ),
            )
            await db.commit()
        finally:
            await db.close()

    async def list_results(self, experiment_id: str) -> list[ExperimentResult]:
        db = await self._connect()
        try:
            rows = await (
                await db.execute(
                    "SELECT * FROM eval_results WHERE experiment_id=? ORDER BY example_id",
                    (experiment_id,),
                )
            ).fetchall()
            return [
                ExperimentResult(
                    experiment_id=str(row["experiment_id"]),
                    example_id=str(row["example_id"]),
                    run_id=row["run_id"],
                    evaluation_id=row["evaluation_id"],
                    status=str(row["status"]),
                    score=row["score"],
                    latency_ms=row["latency_ms"],
                    error_taxonomy=row["error_taxonomy"],
                    result=dict(_load_json(row["result_json"])),
                )
                for row in rows
            ]
        finally:
            await db.close()

    async def aggregate(self, experiment_id: str) -> ExperimentAggregate:
        db = await self._connect()
        try:
            experiment = await (
                await db.execute(
                    "SELECT version_key FROM eval_experiments WHERE experiment_id=?",
                    (experiment_id,),
                )
            ).fetchone()
            if experiment is None:
                raise KeyError(experiment_id)
            row = await (
                await db.execute(
                    """SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN r.status='completed' THEN 1 ELSE 0 END) AS completed,
                        SUM(CASE WHEN e.verdict='pass' THEN 1 ELSE 0 END) AS passed,
                        SUM(CASE WHEN r.error_taxonomy IS NOT NULL OR r.status='error' THEN 1 ELSE 0 END) AS errors,
                        AVG(r.score) AS average_score,
                        AVG(r.latency_ms) AS average_latency_ms
                    FROM eval_results r
                    LEFT JOIN evaluations e ON e.evaluation_id=r.evaluation_id
                    WHERE r.experiment_id=?""",
                    (experiment_id,),
                )
            ).fetchone()
            total = int(row["total"] or 0)
            passed = int(row["passed"] or 0)
            return ExperimentAggregate(
                experiment_id=experiment_id,
                version_key=str(experiment["version_key"]),
                total=total,
                completed=int(row["completed"] or 0),
                passed=passed,
                errors=int(row["errors"] or 0),
                pass_rate=passed / total if total else 0.0,
                average_score=float(row["average_score"]) if row["average_score"] is not None else None,
                average_latency_ms=(
                    float(row["average_latency_ms"])
                    if row["average_latency_ms"] is not None
                    else None
                ),
            )
        finally:
            await db.close()

    async def compare(self, left_experiment_id: str, right_experiment_id: str) -> ExperimentComparison:
        left = await self.aggregate(left_experiment_id)
        right = await self.aggregate(right_experiment_id)
        return ExperimentComparison(
            left=left,
            right=right,
            pass_rate_delta=right.pass_rate - left.pass_rate,
            average_score_delta=self._optional_delta(left.average_score, right.average_score),
            average_latency_ms_delta=self._optional_delta(
                left.average_latency_ms,
                right.average_latency_ms,
            ),
            error_delta=right.errors - left.errors,
        )

    @staticmethod
    def _optional_delta(left: float | None, right: float | None) -> float | None:
        if left is None or right is None:
            return None
        return right - left
