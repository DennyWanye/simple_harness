# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Read-only projection of existing task facts into Context OS state.

The projector deliberately owns no task lifecycle.  Goal, workflow, receipt,
and artifact stores remain authoritative; this module only normalizes their
current facts into a deterministic, session-scoped cache shape.
"""
from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


def _stable_revision(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return format(value, ".9f")
    return str(value)


def _revision_digest(rows: Sequence[object]) -> str:
    payload = json.dumps(
        sorted(str(item) for item in rows),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceRevision:
    source: str
    entity_id: str
    revision: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "entity_id": self.entity_id,
            "revision": self.revision,
        }


@dataclass(frozen=True, slots=True)
class TaskFact:
    fact_id: str
    text: str
    source: str
    status: str
    result: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "fact_id": self.fact_id,
            "text": self.text,
            "source": self.source,
            "status": self.status,
            "result": self.result,
        }


@dataclass(frozen=True, slots=True)
class ArtifactIdentity:
    artifact_id: str
    source: str
    kind: str = ""
    path: str = ""
    url: str = ""
    title: str = ""
    sha256: str = ""
    status: str = "available"

    def to_dict(self) -> dict[str, str]:
        return {
            "artifact_id": self.artifact_id,
            "source": self.source,
            "kind": self.kind,
            "path": self.path,
            "url": self.url,
            "title": self.title,
            "sha256": self.sha256,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class ProjectionConflict:
    field: str
    kept_source: str
    ignored_source: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "field": self.field,
            "kept_source": self.kept_source,
            "ignored_source": self.ignored_source,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class TaskContextFragment:
    fragment_id: str
    source: str
    lifetime: str
    priority: int
    trim_policy: str
    protected: bool
    reason: str
    content: str


@dataclass(frozen=True, slots=True)
class TaskContextSnapshot:
    session_id: str
    task_scope_id: str
    objective: str
    decisions: tuple[TaskFact, ...] = ()
    completed: tuple[TaskFact, ...] = ()
    pending: tuple[TaskFact, ...] = ()
    artifacts: tuple[ArtifactIdentity, ...] = ()
    blockers: tuple[TaskFact, ...] = ()
    narrative_summary: str = ""
    source_revisions: tuple[SourceRevision, ...] = ()
    conflicts: tuple[ProjectionConflict, ...] = ()
    source_errors: tuple[str, ...] = ()

    def to_store_record(self) -> dict[str, Any]:
        revisions = {
            f"{item.source}:{item.entity_id}": item.to_dict()
            for item in sorted(
                self.source_revisions,
                key=lambda item: (item.source, item.entity_id, item.revision),
            )
        }
        return {
            "session_id": self.session_id,
            "task_scope_id": self.task_scope_id,
            "source_revisions": revisions,
            "objective": self.objective,
            "decisions": [item.to_dict() for item in self.decisions],
            "completed": [item.to_dict() for item in self.completed],
            "pending": [item.to_dict() for item in self.pending],
            "artifacts": [item.to_dict() for item in self.artifacts],
            "blockers": [item.to_dict() for item in self.blockers],
            "narrative_summary": self.narrative_summary,
            "conflicts": [item.to_dict() for item in self.conflicts],
            "source_errors": list(self.source_errors),
        }

    def as_prompt_text(self) -> str:
        lines = ["[当前任务态]", f"目标: {self.objective or '(未提供)'}"]
        if self.decisions:
            lines.append(
                "Decisions: " + "; ".join(item.text for item in self.decisions)
            )
        for label, facts in (
            ("已完成", self.completed),
            ("待处理", self.pending),
            ("阻塞", self.blockers),
        ):
            if facts:
                lines.append(f"{label}: " + "；".join(item.text for item in facts))
        if self.artifacts:
            lines.append(
                "产物: "
                + "；".join(
                    item.title or item.path or item.url or item.artifact_id
                    for item in self.artifacts
                )
            )
        return "\n".join(lines)

    def to_protected_fragment(self) -> TaskContextFragment:
        digest = hashlib.sha256(
            json.dumps(
                self.to_store_record(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        return TaskContextFragment(
            fragment_id=f"task-context:{digest}",
            source="task_context_projector",
            lifetime="task",
            priority=90,
            trim_policy="never",
            protected=True,
            reason="authoritative task-state projection",
            content=self.as_prompt_text(),
        )


@dataclass(frozen=True, slots=True)
class AuthorityProjection:
    authority: str
    entity_id: str
    revision: str
    objective: str
    decisions: tuple[TaskFact, ...] = ()
    completed: tuple[TaskFact, ...] = ()
    pending: tuple[TaskFact, ...] = ()
    artifacts: tuple[ArtifactIdentity, ...] = ()
    blockers: tuple[TaskFact, ...] = ()
    narrative_summary: str = ""


class ProjectionSource(Protocol):
    async def project(self, session_id: str) -> AuthorityProjection | None: ...


class GoalProjectionAdapter:
    """Adapter for the existing SessionGoalStore + optional TaskGraphStore."""

    def __init__(self, goal_store: object, task_graph_store: object | None = None) -> None:
        self._goals = goal_store
        self._tasks = task_graph_store

    async def project(self, session_id: str) -> AuthorityProjection | None:
        getter = getattr(self._goals, "get", None)
        goal = getter(session_id) if callable(getter) else None
        if goal is None or str(getattr(goal, "status", "active")) != "active":
            return None
        goal_id = str(getattr(goal, "goal_id", "") or "")
        if not goal_id:
            return None

        nodes: Sequence[object] = ()
        list_tasks = getattr(self._tasks, "list", None)
        if callable(list_tasks):
            value = list_tasks(goal_id)
            nodes = await value if inspect.isawaitable(value) else value

        decisions: list[TaskFact] = []
        completed: list[TaskFact] = []
        pending: list[TaskFact] = []
        blockers: list[TaskFact] = []
        revisions: list[object] = [
            (
                goal_id,
                _stable_revision(
                    getattr(goal, "updated_at", None) or getattr(goal, "set_at", None)
                ),
                str(getattr(goal, "status", "active")),
            )
        ]
        for node in nodes or ():
            status = str(getattr(node, "status", "pending") or "pending").lower()
            fact = TaskFact(
                fact_id=str(getattr(node, "task_id", "")),
                text=str(getattr(node, "title", "")),
                source="goal_task",
                status=status,
                result=str(getattr(node, "result", "") or ""),
            )
            revisions.append(
                (
                    fact.fact_id,
                    _stable_revision(getattr(node, "updated_at", None)),
                    status,
                    fact.result,
                )
            )
            if status in {"completed", "done", "succeeded"}:
                completed.append(fact)
            elif status in {"failed", "blocked", "cancelled"}:
                blockers.append(fact)
            else:
                pending.append(fact)
                # Only explicitly typed notes are promoted into protected
                # decisions. Plain non-terminal results are progress/evidence
                # and must not silently acquire decision authority.
                decision_prefix = "[decision] "
                if fact.result.startswith(decision_prefix):
                    decisions.append(
                        TaskFact(
                            fact_id=f"{fact.fact_id}:result",
                            text=fact.result[len(decision_prefix) :],
                            source="goal_task",
                            status="decision",
                        )
                    )

        return AuthorityProjection(
            authority="goal",
            entity_id=goal_id,
            revision=_revision_digest(revisions),
            objective=str(getattr(goal, "text", "") or ""),
            decisions=tuple(decisions),
            completed=tuple(completed),
            pending=tuple(pending),
            blockers=tuple(blockers),
        )


class ReceiptProjectionAdapter:
    """Convert signed receipt rows without treating accepted work as done."""

    def __init__(self, receipt_store: object) -> None:
        self._store = receipt_store

    async def project(self, session_id: str) -> AuthorityProjection | None:
        loader = getattr(self._store, "load_session", None)
        if not callable(loader):
            return None
        value = loader(session_id)
        receipts = await value if inspect.isawaitable(value) else value
        if not receipts:
            return None

        completed: list[TaskFact] = []
        pending: list[TaskFact] = []
        blockers: list[TaskFact] = []
        artifacts: list[ArtifactIdentity] = []
        revisions: list[str] = []
        for receipt in receipts:
            receipt_id = str(getattr(receipt, "receipt_id", "") or "")
            outcome = str(getattr(receipt, "outcome", "") or "").lower()
            phase = str(getattr(receipt, "phase", "") or "").lower()
            ok = bool(getattr(receipt, "ok", False))
            text = str(getattr(receipt, "tool_name", "") or receipt_id)
            fact = TaskFact(
                fact_id=f"receipt:{receipt_id}",
                text=text,
                source="receipt",
                status=outcome or phase or ("success" if ok else "unknown"),
            )
            if ok and (outcome == "success" or (not outcome and phase != "accepted")):
                completed.append(fact)
            elif outcome == "failed" or (not outcome and not ok and phase != "accepted"):
                blockers.append(fact)
            else:
                # accepted/pending/legacy non-success receipts are never
                # completion evidence.
                pending.append(fact)
            for sha in getattr(receipt, "artifacts", ()) or ():
                artifacts.append(
                    ArtifactIdentity(
                        artifact_id=str(sha),
                        source="receipt",
                        sha256=str(sha),
                    )
                )
            revisions.append(
                (
                    receipt_id,
                    _stable_revision(getattr(receipt, "ended_at", None)),
                    outcome,
                    phase,
                    ok,
                )
            )

        return AuthorityProjection(
            authority="receipt",
            entity_id=session_id,
            revision=_revision_digest(revisions),
            objective="",
            completed=tuple(completed),
            pending=tuple(pending),
            artifacts=tuple(artifacts),
            blockers=tuple(blockers),
        )


class WorkflowProjectionAdapter:
    """Project the latest active workflow without mutating workflow authority."""

    _TERMINAL = {"completed", "succeeded", "failed", "cancelled"}

    def __init__(self, workflow_service: object) -> None:
        self._service = workflow_service

    async def project(self, session_id: str) -> AuthorityProjection | None:
        list_runs = getattr(self._service, "list_runs", None)
        if not callable(list_runs):
            return None
        value = list_runs(session_id=session_id, limit=20)
        page = await value if inspect.isawaitable(value) else value
        runs = page.get("runs", ()) if isinstance(page, Mapping) else ()
        active = next(
            (
                run
                for run in runs
                if str(run.get("status", "")).lower() not in self._TERMINAL
            ),
            None,
        )
        if not isinstance(active, Mapping):
            return None
        run_id = str(active.get("run_id") or "")
        if not run_id:
            return None
        status = str(active.get("status") or "running").lower()
        pending = tuple(
            TaskFact(
                fact_id=f"workflow:{run_id}:node:{node}",
                text=str(node),
                source="workflow",
                status=status,
            )
            for node in active.get("active_nodes", ()) or ()
        )
        decisions = ()
        recovery_action = active.get("recovery_action")
        if recovery_action:
            decisions = (
                TaskFact(
                    fact_id=f"workflow:{run_id}:recovery",
                    text=str(recovery_action),
                    source="workflow",
                    status="decision",
                ),
            )
        blockers = ()
        if active.get("error"):
            blockers = (
                TaskFact(
                    fact_id=f"workflow:{run_id}:error",
                    text=str(active["error"]),
                    source="workflow",
                    status="blocked",
                ),
            )
        return AuthorityProjection(
            authority="workflow",
            entity_id=run_id,
            revision=_revision_digest(
                (
                    active.get("run_version", 0),
                    active.get("event_seq", 0),
                    active.get("updated_at"),
                    status,
                )
            ),
            objective=str(active.get("workflow_name") or "Continue workflow"),
            decisions=decisions,
            pending=pending,
            blockers=blockers,
        )


class TaskContextProjector:
    """Merge optional authority adapters with workflow→goal precedence."""

    def __init__(
        self,
        *,
        workflow_source: ProjectionSource | None = None,
        goal_source: ProjectionSource | None = None,
        receipt_source: ProjectionSource | None = None,
        artifact_source: ProjectionSource | None = None,
    ) -> None:
        self._sources = {
            "workflow": workflow_source,
            "goal": goal_source,
            "receipt": receipt_source,
            "artifact": artifact_source,
        }

    async def project(
        self,
        *,
        effective_sid: str,
        request_id: str = "",
        user_text: str = "",
        explicit_new: bool = False,
        structured_evidence: Sequence[TaskFact] = (),
        narrative_summary: str = "",
    ) -> TaskContextSnapshot | None:
        projections: dict[str, AuthorityProjection] = {}
        source_errors: list[str] = []
        for name, source in self._sources.items():
            if source is None:
                continue
            try:
                result = source.project(effective_sid)
                projection = await result if inspect.isawaitable(result) else result
                if projection is not None:
                    projections[name] = projection
            except Exception as exc:  # noqa: BLE001 - optional stores safe-fail
                source_errors.append(f"{name}:{type(exc).__name__}")

        primary = projections.get("workflow") or projections.get("goal")
        receipt_projection = projections.get("receipt")
        artifact_projection = projections.get("artifact")
        has_structured = bool(
            explicit_new
            or structured_evidence
            or receipt_projection
            or artifact_projection
        )
        if primary is None and not has_structured:
            return None

        if primary is not None:
            task_scope_id = f"{primary.authority}:{primary.entity_id}"
            objective = primary.objective
        else:
            task_scope_id = f"session:{effective_sid}"
            objective = user_text.strip()

        conflicts: list[ProjectionConflict] = []
        workflow = projections.get("workflow")
        goal = projections.get("goal")
        if workflow and goal and goal.objective and goal.objective != workflow.objective:
            conflicts.append(
                ProjectionConflict(
                    field="objective",
                    kept_source="workflow",
                    ignored_source="goal",
                    reason="workflow authority has higher precedence",
                )
            )

        ordered_sources = [
            projection
            for projection in (workflow, goal, receipt_projection, artifact_projection)
            if projection is not None
        ]
        completed: list[TaskFact] = []
        pending: list[TaskFact] = []
        decisions: list[TaskFact] = []
        artifacts: list[ArtifactIdentity] = []
        blockers: list[TaskFact] = []
        revisions: list[SourceRevision] = []
        seen_facts: dict[str, TaskFact] = {}
        seen_artifacts: set[str] = set()

        def add_fact(target: list[TaskFact], fact: TaskFact) -> None:
            key = fact.fact_id or f"{fact.source}:{fact.text}"
            prior = seen_facts.get(key)
            if prior is not None:
                if prior != fact:
                    conflicts.append(
                        ProjectionConflict(
                            field=f"fact:{key}",
                            kept_source=prior.source,
                            ignored_source=fact.source,
                            reason="higher-precedence fact retained",
                        )
                    )
                return
            seen_facts[key] = fact
            target.append(fact)

        for projection in ordered_sources:
            revisions.append(
                SourceRevision(
                    source=projection.authority,
                    entity_id=projection.entity_id,
                    revision=projection.revision,
                )
            )
            for fact in projection.decisions:
                add_fact(decisions, fact)
            for fact in projection.completed:
                add_fact(completed, fact)
            for fact in projection.pending:
                add_fact(pending, fact)
            for fact in projection.blockers:
                add_fact(blockers, fact)
            for artifact in projection.artifacts:
                key = artifact.artifact_id or artifact.sha256 or artifact.path or artifact.url
                if key and key not in seen_artifacts:
                    seen_artifacts.add(key)
                    artifacts.append(artifact)

        # Request-local typed evidence is the lowest structured authority and
        # may supplement, but never overwrite, store-backed facts.
        for fact in structured_evidence:
            add_fact(pending, fact)

        if primary is None and request_id:
            revisions.append(
                SourceRevision("session_request", request_id, request_id)
            )

        sort_fact = lambda item: (item.source, item.fact_id, item.text)  # noqa: E731
        return TaskContextSnapshot(
            session_id=effective_sid,
            task_scope_id=task_scope_id,
            objective=objective,
            decisions=tuple(sorted(decisions, key=sort_fact)),
            completed=tuple(sorted(completed, key=sort_fact)),
            pending=tuple(sorted(pending, key=sort_fact)),
            artifacts=tuple(sorted(artifacts, key=lambda item: (item.source, item.artifact_id))),
            blockers=tuple(sorted(blockers, key=sort_fact)),
            narrative_summary=narrative_summary,
            source_revisions=tuple(
                sorted(revisions, key=lambda item: (item.source, item.entity_id))
            ),
            conflicts=tuple(conflicts),
            source_errors=tuple(sorted(source_errors)),
        )


__all__ = [
    "ArtifactIdentity",
    "AuthorityProjection",
    "GoalProjectionAdapter",
    "ProjectionConflict",
    "ProjectionSource",
    "ReceiptProjectionAdapter",
    "SourceRevision",
    "TaskContextFragment",
    "TaskContextProjector",
    "TaskContextSnapshot",
    "TaskFact",
    "WorkflowProjectionAdapter",
]
