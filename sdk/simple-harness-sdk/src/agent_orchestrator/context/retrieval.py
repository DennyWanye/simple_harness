# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Knowledge retrieval for the Context Builder (§10.1, 理论 04-4, plan D4-9).

Retrieval is *not* vector similarity: a deterministic score combines semantic
relevance (token overlap between the Task and the knowledge), the knowledge's
trust level, its graph distance to the Task and whether it comes from the Task's
branch (both read from the hierarchical plan, :class:`GoalTree`), its age and its
reuse value, then removes duplicates and superseded entries.  The permission pre-filter is the
Mission boundary (S4-06): only this Mission's records are ever considered.  The
weights below are this build's implementation convention (plan §6.1), versioned
as ``RETRIEVAL_VERSION`` so every context package records which ranking it saw
(30-29).

The result is small: ids, scores and the reasons; the builder reads the full
records back from the store (回读原文) — never a paraphrase.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..contracts import Claim, ClaimStatus, Task
from ..memory.verified_knowledge import KnowledgeRecord, knowledge_ref

RETRIEVAL_VERSION = "retrieval-v4-hierarchy-structure"
WEIGHTS = {
    # 推后第 2 批 K06：09-10 §10.1 的"Task DAG 距离"(proximity) 与"分支相关性"(branch)
    # 各占原 proximity 的一半，普通最高分仍是 7.0
    "relevance": 3.0, "trust": 2.0, "proximity": 0.5, "branch": 0.5, "recency": 0.5, "reuse": 0.5,
    # An explicit reference outranks the maximum ordinary score (7.0).
    "exact_reference": 8.0,
}
# Only Verified Knowledge is ranked in this build (plan §6.1 / review P2-6): the trust
# factor is constant for it and is kept as a weighted term so a later build that ranks
# candidate claims for critic templates changes RETRIEVAL_VERSION, not the shape.
TRUST = {"VERIFIED": 1.0, "SUPPORTED": 0.5}
DEFAULT_LIMIT = 12
_TOKEN = re.compile(r"[A-Za-z0-9_]+|[一-鿿]")


class RetrievalUnavailable(RuntimeError):
    """The knowledge index could not be read (S4-07): the caller must degrade or block
    explicitly — never report "no knowledge"."""


def tokens(text: str) -> frozenset[str]:
    return frozenset(t.lower() for t in _TOKEN.findall(text or ""))


def relevance(query: frozenset[str], text: str) -> float:
    """Overlap coefficient of the token sets (|q ∩ c| / min(|q|, |c|)): a short, exact
    statement is not penalised for the query's length."""

    candidate = tokens(text)
    if not query or not candidate:
        return 0.0
    return len(query & candidate) / min(len(query), len(candidate))


def normalised_content(content: str) -> str:
    return " ".join(sorted(tokens(content)))


@dataclass(frozen=True, slots=True)
class Scored:
    id: str
    score: float
    parts: Mapping[str, float]
    status: str
    key: str | None
    stance: str
    source_task: str

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "score": round(self.score, 4),
            "parts": {k: round(v, 4) for k, v in self.parts.items()},
            "status": self.status,
            "key": self.key,
            "stance": self.stance,
            "source_task": self.source_task,
        }


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    version: str
    status: str  # ok | unavailable | disabled
    items: tuple[Scored, ...]
    superseded: tuple[Mapping[str, Any], ...]
    dropped: Mapping[str, Any]
    considered: int
    reason: str | None = None
    limit: int = DEFAULT_LIMIT

    def to_json(self) -> dict[str, Any]:
        return {
            "retrieval_version": self.version,
            "status": self.status,
            "reason": self.reason,
            "considered": self.considered,
            "returned": len(self.items),
            "limit": self.limit,
            "dropped": {
                k: (dict(v) if isinstance(v, Mapping) else list(v)) for k, v in self.dropped.items()
            },
            "items": [item.to_json() for item in self.items],
            "superseded": [dict(item) for item in self.superseded],
        }

    @classmethod
    def unavailable(cls, reason: str, *, status: str = "unavailable") -> RetrievalResult:
        return cls(RETRIEVAL_VERSION, status, (), (), {}, 0, reason=reason)


@dataclass(frozen=True, slots=True)
class GoalTree:
    """The hierarchical plan's structure as retrieval reads it (推后第 2 批 K06).

    Nodes are plan *occurrences*.  Tree edges join a refined goal occurrence to the
    occurrences its adopted method places under it; precedence edges are the plan's
    data requirements and order constraints.  ``Task.dependency_ids`` is empty under a
    hierarchical plan, so neither is read from it.  Only structure — never what a piece
    of knowledge says."""

    parent: Mapping[str, str | None]
    task_of: Mapping[str, str]
    neighbours: Mapping[str, frozenset[str]]

    @classmethod
    def build(cls, *, roots: Iterable[str], children: Mapping[str, Iterable[str]],
              task_of: Mapping[str, str], precedence: Iterable[tuple[str, str]]) -> GoalTree:
        parent: dict[str, str | None] = {}
        links: dict[str, set[str]] = {}
        pending = deque((str(root), None) for root in roots)
        while pending:
            node, above = pending.popleft()
            if node in parent:
                continue
            parent[node] = above
            links.setdefault(node, set())
            if above is not None:
                links[node].add(above)
                links[above].add(node)
            pending.extend((str(child), node) for child in children.get(node, ()))
        for before, after in precedence:
            before, after = str(before), str(after)
            if before in parent and after in parent:
                links[before].add(after)
                links[after].add(before)
        return cls(parent=parent, task_of={node: str(task_of[node]) for node in parent},
                   neighbours={node: frozenset(near) for node, near in links.items()})

    @classmethod
    def from_network(cls, network: Any) -> GoalTree:
        """The active plan's tree: each occurrence's adopted method places its children."""
        children: dict[str, tuple[str, ...]] = {}
        for spec in network.occurrences:
            draft = network.adopted_instance_for(spec.occurrence_id)
            if draft is not None:
                children[str(spec.occurrence_id)] = tuple(
                    str(child.occurrence_id) for child in draft.child_bindings)
        precedence = [(str(item.producer_occurrence), str(item.consumer_occurrence))
                      for item in network.data_requirements]
        precedence += [(str(item.before), str(item.after)) for item in network.order_constraints]
        return cls.build(roots=[str(root) for root in network.root_occurrence_ids], children=children,
                         task_of={str(spec.occurrence_id): str(spec.task_id) for spec in network.occurrences},
                         precedence=precedence)

    def occurrences_of(self, task_id: str) -> tuple[str, ...]:
        return tuple(sorted(node for node, task in self.task_of.items() if task == task_id))

    def _path(self, node: str) -> list[str]:
        path = [node]
        while (above := self.parent[path[-1]]) is not None:
            path.append(above)
        return path[::-1]

    def distance(self, task_id: str, source_task: str) -> int | None:
        """Fewest edges between any occurrence of the two steps; ``None`` when unconnected
        (e.g. the source step is no longer in the active plan)."""
        targets = set(self.occurrences_of(source_task))
        start = self.occurrences_of(task_id)
        if not targets or not start:
            return None
        seen = set(start)
        frontier = deque((node, 0) for node in start)
        while frontier:
            node, steps = frontier.popleft()
            if node in targets:
                return steps
            for near in sorted(self.neighbours.get(node, ())):
                if near not in seen:
                    seen.add(near)
                    frontier.append((near, steps + 1))
        return None

    def branch_share(self, task_id: str, source_task: str) -> float:
        """How much of the step's root path the source shares: the depth of their lowest
        common goal over the step's own depth (1.0 for the step itself or a root step)."""
        best = 0.0
        for mine in self.occurrences_of(task_id):
            own = self._path(mine)
            for theirs in self.occurrences_of(source_task):
                common = 0
                for left, right in zip(own, self._path(theirs)):
                    if left != right:
                        break
                    common += 1
                if common:
                    best = max(best, 1.0 if len(own) == 1 else (common - 1) / (len(own) - 1))
        return best


def structure_parts(task_id: str, source_task: str, tree: GoalTree) -> dict[str, float]:
    """The two structural factors of one knowledge record for one step."""
    steps = tree.distance(task_id, source_task)
    return {"proximity": 0.0 if steps is None else 1.0 / (1 + steps),
            "branch": tree.branch_share(task_id, source_task)}


def rank_knowledge(
    task: Task,
    records: Sequence[KnowledgeRecord],
    *,
    goal_tree: GoalTree,
    limit: int = DEFAULT_LIMIT,
    query_text: str | None = None,
) -> RetrievalResult:
    """Deterministic ranking of one Mission's knowledge for ``task`` (D4-9)."""

    query_raw = query_text or " ".join((task.goal, *task.success_criteria, task.rationale))
    query = tokens(query_raw)
    reference = query_raw.strip()
    own = [r for r in records if r.mission_id == task.mission_id]  # permission pre-filter
    considered = len(own)
    superseded: list[dict[str, Any]] = [
        {"id": r.id, "superseded_by": r.superseded_by, "key": r.key, "status": r.status}
        for r in own
        if r.status == "SUPERSEDED"
    ]
    superseded_ids = [str(item["id"]) for item in superseded]
    # Out-of-date knowledge never reaches here: the caller filters by knowledge_standing.
    live = [r for r in own if r.status == "VERIFIED"]
    if not live:
        return RetrievalResult(
            RETRIEVAL_VERSION,
            "ok",
            (),
            tuple(superseded),
            {"superseded": superseded_ids},
            considered,
            limit=limit,
        )
    newest = max(r.created_at for r in live)
    oldest = min(r.created_at for r in live)
    span = max(newest - oldest, 1e-9)
    scored: list[Scored] = []
    unrelated: list[str] = []
    for record in live:
        parts = {
            "relevance": relevance(query, " ".join((record.content, record.key or ""))),
            "trust": TRUST.get(record.status, 0.0),
            **structure_parts(task.id, record.source_task, goal_tree),
            "recency": (record.created_at - oldest) / span if newest > oldest else 1.0,
            "reuse": min(len(record.used_by), 3) / 3.0,
        }
        # Match the complete, case-sensitive identity or evidence reference. Do
        # not tokenize paths: shared directories/basenames are not exact hits.
        # This runs after Mission and status filtering and before dedup.
        if reference and (reference == record.id or reference in record.evidence):
            parts["exact_reference"] = 1.0
        if parts["relevance"] == 0 and "exact_reference" not in parts:
            unrelated.append(record.id)
            continue
        score = sum(WEIGHTS[name] * value for name, value in parts.items())
        scored.append(
            Scored(
                record.id,
                score,
                parts,
                record.status,
                record.key,
                record.stance,
                record.source_task,
            )
        )
    scored.sort(key=lambda item: (-item.score, item.id))
    kept: list[Scored] = []
    duplicates: list[str] = []
    duplicate_of: dict[str, str] = {}  # P2-7: dropped id → the record that represents it
    representative: dict[str, str] = {}
    by_id = {r.id: r for r in live}
    for item in scored:
        record = by_id[item.id]
        subject = (
            f"key:{record.key}:{record.stance}"
            if record.key
            else f"content:{normalised_content(record.content)}"
        )
        if subject in representative:
            duplicates.append(item.id)
            duplicate_of[item.id] = representative[subject]
            continue
        representative[subject] = item.id
        kept.append(item)
    truncated = [item.id for item in kept[limit:]]
    return RetrievalResult(
        RETRIEVAL_VERSION,
        "ok",
        tuple(kept[:limit]),
        tuple(superseded),
        {
            "duplicate": duplicates,
            "duplicate_of": duplicate_of,
            "superseded": superseded_ids,
            "over_limit": truncated,
            "no_relevance": unrelated,
        },
        considered,
        reason="no_relevant_match_use_knowledge_catalog" if not kept else None,
        limit=limit,
    )


def disputed_claims(claims: Sequence[Claim], *, mission_id: str) -> list[dict[str, Any]]:
    """§10 item 7: contested claims, always marked, never as facts."""

    return [
        {
            "claim_id": claim.id,
            "status": str(claim.status),
            "key": claim.key,
            "stance": claim.stance,
            "content": claim.content,
            "evidence": list(claim.evidence),  # P2-8: the verifier sees the references
            "source_task": claim.source_task,
            "disputed_by": list(claim.disputed_by),  # who disagrees
            "marker": "DISPUTED — 争议中，不是事实",
        }
        for claim in claims
        if claim.mission_id == mission_id and claim.status is ClaimStatus.DISPUTED
    ]


def candidate_claims(
    claims: Sequence[Claim], *, mission_id: str, statuses: Sequence[ClaimStatus]
) -> list[dict[str, Any]]:
    wanted = set(statuses)
    return [
        {
            "claim_id": claim.id,
            "status": str(claim.status),
            "key": claim.key,
            "stance": claim.stance,
            "content": claim.content,
            "source_task": claim.source_task,
            "evidence": list(claim.evidence),
            "marker": "UNVERIFIED — 候选结论，不能当作事实",
        }
        for claim in claims
        if claim.mission_id == mission_id and claim.status in wanted
    ]


@dataclass(frozen=True, slots=True)
class KnowledgeContext:
    """What the Context Builder puts into a package (visibility applied there)."""

    retrieval: RetrievalResult
    verified: tuple[Mapping[str, Any], ...] = ()
    disputed: tuple[Mapping[str, Any], ...] = ()
    candidates: tuple[Mapping[str, Any], ...] = ()
    rejected: tuple[Mapping[str, Any], ...] = ()
    #: accepted steps' summaries the reviewer confirmed faithful (the blackboard's summary layer)
    step_summaries: tuple[Mapping[str, Any], ...] = ()
    raw_refs: Mapping[str, Any] | None = None  # §11 layer 1: references only (P2-11)

    @classmethod
    def unavailable(cls, reason: str, *, status: str = "unavailable") -> KnowledgeContext:
        return cls(retrieval=RetrievalResult.unavailable(reason, status=status))

    @property
    def frozen_ids(self) -> list[dict[str, Any]]:
        return [{"id": item["id"], "version": item["version"]} for item in self.verified]


def knowledge_view(record: KnowledgeRecord, scored: Scored | None = None) -> dict[str, Any]:
    """The full record as offered to an Agent (回读原文): id/version first, then the
    statement, its subject, its verifier and its provenance."""

    view = {
        "id": record.id,
        "version": record.version,
        # how a result cites it in used_knowledge, and where its verification came from
        "ref": knowledge_ref(record.id, record.version),
        "basis": str(record.verifier.get("basis") or "test_observation"),
        "status": record.status,
        "key": record.key,
        "stance": record.stance,
        "content": record.content,
        "type": record.type,
        "verifier": dict(record.verifier),
        "evidence": list(record.evidence),
        "source_task": record.source_task,
        "source_attempt": record.source_attempt,
        "dependencies": [knowledge_ref(item["id"], item["version"])
                         for item in record.support.get("knowledge", ())],
        "used_by": list(record.used_by),
        "disputed_by": list(record.disputed_by),
        # 第 2 批 K02：原计划 §11.2 的三个登记项，没登记的就是 None
        "validity_interval": None if record.validity_interval is None else dict(record.validity_interval),
        "permitted_uses": None if record.permitted_uses is None else list(record.permitted_uses),
        "assurance_level": record.assurance_level,
        "score": None if scored is None else round(scored.score, 4),
    }
    return view


__all__ = (
    "DEFAULT_LIMIT",
    "RETRIEVAL_VERSION",
    "TRUST",
    "WEIGHTS",
    "KnowledgeContext",
    "RetrievalResult",
    "RetrievalUnavailable",
    "Scored",
    "GoalTree",
    "candidate_claims",
    "disputed_claims",
    "knowledge_view",
    "rank_knowledge",
    "relevance",
    "structure_parts",
    "tokens",
)
