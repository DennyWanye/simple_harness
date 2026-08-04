"""Deterministic public projection of one durable Root.

This module is deliberately a pure reducer.  It consumes only already-redacted
``PublicFactEnvelope`` values from the inspector read boundary; it never opens a
database and never interprets narration or exception text.  Keeping the
semantic projection here makes replays, pagination and cold recovery byte
stable.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence


PROJECTION_CONTRACT_VERSION = "semantic-run-view/v1"


class RootOutcomeStatus(str, Enum):
    RUNNING = "running"
    WAITING = "waiting"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    COMPLETED_WITH_RECOVERY = "completed_with_recovery"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class PhaseTaxonomy(str, Enum):
    UNDERSTAND = "understand"
    PREPARE = "prepare"
    DELEGATE = "delegate"
    EXECUTE = "execute"
    VERIFY_REPAIR = "verify_repair"
    WAIT_USER = "wait_user"
    DELIVER = "deliver"


class PhaseStatus(str, Enum):
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    COMPLETED_WITH_RECOVERY = "completed_with_recovery"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


_TAXONOMY_ORDER = (
    PhaseTaxonomy.UNDERSTAND,
    PhaseTaxonomy.PREPARE,
    PhaseTaxonomy.DELEGATE,
    PhaseTaxonomy.EXECUTE,
    PhaseTaxonomy.VERIFY_REPAIR,
    PhaseTaxonomy.WAIT_USER,
    PhaseTaxonomy.DELIVER,
)

_TAXONOMY_TITLES = {
    PhaseTaxonomy.UNDERSTAND: "理解需求",
    PhaseTaxonomy.PREPARE: "准备与规划",
    PhaseTaxonomy.DELEGATE: "委派",
    PhaseTaxonomy.EXECUTE: "执行",
    PhaseTaxonomy.VERIFY_REPAIR: "验证与修复",
    PhaseTaxonomy.WAIT_USER: "等待用户",
    PhaseTaxonomy.DELIVER: "交付",
}

_SOURCE_RANK = {
    "workflow": 0,
    "execution": 1,
    "state": 2,
    "content": 3,
}

_KIND_RANK = {
    "user_request": 0,
    "run": 1,
    "provider": 2,
    "workflow_step": 3,
    "child_command": 4,
    "child": 5,
    "tool": 6,
    "attempt": 7,
    "failure_report": 8,
    "failure_set": 9,
    "boundary": 10,
    "block_signal": 11,
    "narration": 12,
    "run_terminal": 13,
}

_ALLOWED_BLOCK_REASONS = frozenset(
    {
        "provider_binding_unavailable",
        "capability_unavailable",
        "external_dependency_unavailable",
        "workspace_unavailable",
    }
)

_SAFE_ITEM_FIELDS = (
    "narration_code",
    "safe_text",
    "status",
    "action_code",
    "safe_target_label",
    "detail_ref",
    "tool_name",
)


@dataclass(frozen=True, slots=True)
class _Fact:
    stable_id: str
    root_run_id: str
    source: str
    kind: str
    original_kind: str
    payload: Mapping[str, Any]
    source_seq: int | None
    created_at: float | None


@dataclass(frozen=True, order=True, slots=True)
class CausalOrderKeyV1:
    topological_depth: int
    source_rank: int
    kind_rank: int
    stable_id: str

    def to_list(self) -> list[Any]:
        return [
            self.topological_depth,
            self.source_rank,
            self.kind_rank,
            self.stable_id,
        ]


def _read(value: object, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _text_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(
        sorted(
            {
                normalized
                for item in value
                if (normalized := _text(item)) is not None
            }
        )
    )


def _normalise_fact(raw: object) -> _Fact:
    stable_id = _text(_read(raw, "stable_id"))
    root_run_id = _text(_read(raw, "root_run_id"))
    source = _text(_read(raw, "source"))
    original_kind = _text(_read(raw, "kind"))
    payload = _read(raw, "public_payload", {})
    if (
        stable_id is None
        or root_run_id is None
        or source is None
        or original_kind is None
    ):
        raise ValueError("public fact identity is incomplete")
    if not isinstance(payload, Mapping):
        raise ValueError("public fact payload must be a mapping")
    source_seq = _read(raw, "source_seq")
    if isinstance(source_seq, bool) or not isinstance(source_seq, int):
        source_seq = None
    created_at = _read(raw, "created_at")
    if isinstance(created_at, bool) or not isinstance(created_at, (int, float)):
        created_at = None
    public_payload = dict(payload)
    identity = stable_id.split(":", 1)[1] if ":" in stable_id else stable_id
    kind = {
        "execution_run": "run",
        "workflow_run": "run",
        "workflow_node": "workflow_step",
        "tool_detail": "tool",
        "provider_detail": "provider",
        "provider_audit": "provider",
        "message": "content",
        "message_archive": "content",
        "provider_batch": "provider",
        "provider_call": "tool",
    }.get(original_kind, original_kind)
    if original_kind in {"execution_run", "workflow_run"}:
        public_payload.setdefault("run_id", identity)
    elif original_kind == "attempt":
        public_payload.setdefault("attempt_id", identity)
    elif original_kind == "failure_report":
        public_payload.setdefault("report_ref", identity)
    elif original_kind == "failure_set":
        public_payload.setdefault("failure_set_id", identity)
    elif original_kind == "block_signal":
        public_payload.setdefault("signal_id", identity)
    elif original_kind == "workflow_node":
        public_payload.setdefault("workflow_step_id", identity)
    elif original_kind == "provider_call":
        public_payload.setdefault("tool_name", public_payload.get("raw_tool_name"))
        public_payload.setdefault("activity_kind", "mutate")
        public_payload.setdefault("mapping_reason", "provider_call_boundary")
    if original_kind in {"message", "message_archive"}:
        public_payload.setdefault("author", public_payload.get("role"))
        public_payload.setdefault("safe_text", public_payload.get("content"))
    if original_kind == "boundary":
        public_payload.setdefault("boundary_kind", public_payload.get("wait_kind"))
        state = _text(public_payload.get("state"))
        public_payload.setdefault(
            "resolved",
            state in {"resolved", "completed", "cancelled", "expired", "rejected"},
        )
    if original_kind in {"execution_event", "workflow_event"}:
        event_kind = _text(public_payload.get("event_kind")) or ""
        status = _text(public_payload.get("status"))
        if (
            _text(public_payload.get("role")) == "root"
            and (_is_terminal_status(status) or event_kind.endswith(".final"))
        ):
            kind = "run_terminal"
            public_payload.setdefault("run_id", root_run_id)
        elif original_kind == "workflow_event" and (
            public_payload.get("workflow_step_id") is not None
            or public_payload.get("phase_hint") is not None
        ):
            kind = "workflow_step"
    return _Fact(
        stable_id=stable_id,
        root_run_id=root_run_id,
        source=source,
        kind=kind,
        original_kind=original_kind,
        payload=public_payload,
        source_seq=source_seq,
        created_at=None if created_at is None else float(created_at),
    )


def _fact_refs(fact: _Fact) -> tuple[str, ...]:
    payload = fact.payload
    refs = set(_text_list(payload.get("parent_refs")))
    for field in (
        "parent_ref",
        "parent_run_id",
        "provider_batch_id",
        "invocation_id",
        "attempt_id",
        "failed_attempt_id",
        "supersedes_attempt_id",
        "trigger_failure_set_id",
        "failure_set_id",
        "report_ref",
        "primary_report_ref",
        "child_run_id",
        "terminal_event_id",
        "created_event_id",
    ):
        if fact.kind == "attempt" and field == "provider_batch_id":
            continue
        if fact.kind == "provider" and field == "failure_set_id":
            continue
        ref = _text(payload.get(field))
        if ref is not None and ref != fact.stable_id:
            refs.add(ref)
    refs.update(_text_list(payload.get("report_refs")))
    refs.update(_text_list(payload.get("evidence_refs")))
    return tuple(sorted(refs))


def _causal_order(
    facts: Sequence[_Fact],
) -> tuple[
    dict[str, CausalOrderKeyV1],
    dict[str, frozenset[str]],
    tuple[str, ...],
]:
    facts_by_id = {fact.stable_id: fact for fact in facts}
    aliases: dict[str, set[str]] = {}
    for fact in facts:
        aliases.setdefault(fact.stable_id, set()).add(fact.stable_id)
        if ":" in fact.stable_id:
            aliases.setdefault(fact.stable_id.split(":", 1)[1], set()).add(
                fact.stable_id
            )
        alias_fields = {
            "execution_run": ("run_id",),
            "workflow_run": ("run_id",),
            "attempt": ("attempt_id",),
            "failure_set": ("failure_set_id",),
            "failure_report": ("report_ref",),
            "block_signal": ("signal_id",),
            "workflow_node": ("workflow_step_id",),
            "provider_batch": ("provider_batch_id",),
            "provider_detail": ("invocation_id",),
        }.get(fact.original_kind, ())
        if fact.kind == "provider":
            alias_fields = tuple(
                dict.fromkeys((*alias_fields, "provider_batch_id", "invocation_id"))
            )
        elif fact.kind == "tool":
            alias_fields = tuple(
                dict.fromkeys((*alias_fields, "effect_id", "call_record_id"))
            )
        for field in alias_fields:
            alias = _text(fact.payload.get(field))
            if alias is not None:
                aliases.setdefault(alias, set()).add(fact.stable_id)
    incoming: dict[str, set[str]] = {stable_id: set() for stable_id in facts_by_id}
    outgoing: dict[str, set[str]] = {stable_id: set() for stable_id in facts_by_id}
    for fact in facts:
        for ref in _fact_refs(fact):
            for resolved in aliases.get(ref, ()):
                if resolved != fact.stable_id:
                    incoming[fact.stable_id].add(resolved)
                    outgoing[resolved].add(fact.stable_id)
        if fact.kind == "attempt":
            batch_id = _text(fact.payload.get("provider_batch_id"))
            for resolved in aliases.get(batch_id or "", ()):
                if resolved != fact.stable_id:
                    incoming[resolved].add(fact.stable_id)
                    outgoing[fact.stable_id].add(resolved)
        elif fact.kind == "provider":
            failure_set_id = _text(fact.payload.get("failure_set_id"))
            for resolved in aliases.get(failure_set_id or "", ()):
                if resolved != fact.stable_id:
                    incoming[resolved].add(fact.stable_id)
                    outgoing[fact.stable_id].add(resolved)

    # source_seq only orders facts from the same source.  It cannot invent a
    # cross-database causal edge.
    by_source: dict[tuple[str, str], list[_Fact]] = {}
    for fact in facts:
        if fact.source_seq is not None:
            stream_id = (
                _text(fact.payload.get("source_stream"))
                or _text(fact.payload.get("run_id"))
                or _text(fact.payload.get("stream_id"))
                or fact.stable_id
            )
            by_source.setdefault(
                (fact.source, stream_id), []
            ).append(fact)
    for group in by_source.values():
        ordered = sorted(group, key=lambda item: (item.source_seq, item.stable_id))
        for earlier, later in zip(ordered, ordered[1:]):
            if earlier.source_seq == later.source_seq:
                continue
            incoming[later.stable_id].add(earlier.stable_id)
            outgoing[earlier.stable_id].add(later.stable_id)

    depths: dict[str, int] = {}
    remaining = set(facts_by_id)
    diagnostics: set[str] = set()
    while remaining:
        ready = sorted(
            stable_id
            for stable_id in remaining
            if not (incoming[stable_id] & remaining)
        )
        if not ready:
            # Deterministic cycle repair: the lexicographically smallest fact
            # becomes a new component root.  We report incompleteness instead
            # of pretending the broken graph is authoritative.
            ready = [min(remaining)]
            diagnostics.add("causal_cycle")
            incoming[ready[0]].difference_update(remaining)
        for stable_id in ready:
            parents = incoming[stable_id] - remaining
            depths[stable_id] = (
                max((depths[parent] for parent in parents), default=-1) + 1
            )
            remaining.remove(stable_id)

    keys = {
        fact.stable_id: CausalOrderKeyV1(
            topological_depth=depths[fact.stable_id],
            source_rank=_SOURCE_RANK.get(fact.source.split(":", 1)[0], 99),
            kind_rank=_KIND_RANK.get(fact.kind, 99),
            stable_id=fact.stable_id,
        )
        for fact in facts
    }
    ancestors: dict[str, frozenset[str]] = {}
    for stable_id in sorted(keys, key=lambda item: keys[item]):
        direct = incoming[stable_id]
        expanded = set(direct)
        for parent in direct:
            expanded.update(ancestors.get(parent, ()))
        ancestors[stable_id] = frozenset(expanded)
    return keys, ancestors, tuple(sorted(diagnostics))


def _is_terminal_status(status: str | None) -> bool:
    return status in {"completed", "failed", "cancelled"}


def _root_fact(facts: Sequence[_Fact], root_run_id: str) -> _Fact | None:
    candidates = [
        fact
        for fact in facts
        if fact.kind in {"run", "run_terminal"}
        and _text(fact.payload.get("run_id")) == root_run_id
        and _text(fact.payload.get("role")) in {None, "root"}
    ]
    terminal = [
        fact
        for fact in candidates
        if _is_terminal_status(_text(fact.payload.get("status")))
    ]
    pool = terminal or candidates
    if not pool:
        return None
    authority_rank = {
        "execution_event": 3,
        "execution_run": 2,
        "workflow_event": 1,
        "workflow_run": 0,
    }
    # Durable event authority wins over summary rows; stable id breaks ties.
    # Wall-clock time is intentionally not a semantic ordering input.
    return max(
        pool,
        key=lambda item: (
            authority_rank.get(item.original_kind, -1),
            item.stable_id,
        ),
    )


def _root_lifecycle_fact(
    facts: Sequence[_Fact], root_run_id: str
) -> _Fact | None:
    """Return the durable root row that owns wall-clock lifecycle fields.

    Terminal events are the status authority, but their compact payload may
    omit ``started_at`` and ``ended_at``.  Timing therefore comes from the
    persisted execution-run summary.
    """

    candidates = [
        fact
        for fact in facts
        if fact.original_kind == "execution_run"
        and _text(fact.payload.get("run_id")) == root_run_id
        and _text(fact.payload.get("role")) in {None, "root"}
    ]
    if candidates:
        return min(candidates, key=lambda item: item.stable_id)
    compatible = [
        fact
        for fact in facts
        if fact.kind in {"run", "run_terminal"}
        and _text(fact.payload.get("run_id")) == root_run_id
        and _text(fact.payload.get("role")) in {None, "root"}
        and (
            fact.payload.get("started_at") is not None
            or fact.payload.get("ended_at") is not None
        )
    ]
    return min(compatible, key=lambda item: item.stable_id) if compatible else None


def _unresolved_boundaries(facts: Sequence[_Fact]) -> tuple[_Fact, ...]:
    return tuple(
        fact
        for fact in facts
        if fact.kind == "boundary"
        and _text(fact.payload.get("boundary_kind")) in {"human", "admission"}
        and fact.payload.get("resolved") is not True
        and _text(fact.payload.get("status")) not in {
            "resolved",
            "rejected",
            "cancelled",
            "expired",
        }
    )


def _valid_block_signals(
    facts: Sequence[_Fact], root_run_id: str
) -> tuple[_Fact, ...]:
    return tuple(
        fact
        for fact in facts
        if fact.kind == "block_signal"
        and _text(fact.payload.get("root_run_id")) in {None, root_run_id}
        and _text(fact.payload.get("reason_code")) in _ALLOWED_BLOCK_REASONS
    )


def _recovery_chain(
    facts: Sequence[_Fact],
    *,
    root_run_id: str,
    root_terminal: _Fact | None,
    order: Mapping[str, CausalOrderKeyV1],
    ancestors: Mapping[str, frozenset[str]],
) -> tuple[str, ...]:
    if root_terminal is None or _text(root_terminal.payload.get("status")) != "completed":
        return ()
    child_candidates = [
        fact
        for fact in facts
        if fact.kind in {"child", "run", "run_terminal"}
        and _text(fact.payload.get("role")) == "child"
        and _text(fact.payload.get("status")) in {"failed", "cancelled"}
        and _text(fact.payload.get("run_id")) is not None
    ]
    children: dict[str | None, _Fact] = {}
    child_authority = {"run": 0, "child": 1, "run_terminal": 2}
    for fact in sorted(child_candidates, key=lambda item: item.stable_id):
        child_id = _text(fact.payload.get("run_id"))
        current = children.get(child_id)
        if current is None or child_authority[fact.kind] > child_authority[current.kind]:
            children[child_id] = fact
    reports = [
        fact
        for fact in facts
        if fact.kind == "failure_report"
        and _text(fact.payload.get("child_run_id")) in children
    ]
    failure_sets = [fact for fact in facts if fact.kind == "failure_set"]
    replacements = [
        fact
        for fact in facts
        if fact.kind == "attempt"
        and (
            _text(fact.payload.get("trigger_failure_set_id")) is not None
            or _text(fact.payload.get("supersedes_attempt_id")) is not None
        )
    ]
    for report in sorted(reports, key=lambda item: item.stable_id):
        child_id = _text(report.payload.get("child_run_id"))
        child = children.get(child_id)
        if child is None:
            continue
        report_ref = _text(report.payload.get("report_ref")) or report.stable_id
        report_attempt = _text(report.payload.get("attempt_id"))
        matching_sets = [
            failure_set
            for failure_set in failure_sets
            if report_ref in _text_list(failure_set.payload.get("report_refs"))
            or _text(failure_set.payload.get("report_ref")) == report_ref
            or _text(failure_set.payload.get("primary_report_ref")) == report_ref
            or (
                report_attempt is not None
                and _text(failure_set.payload.get("failed_attempt_id"))
                == report_attempt
            )
        ]
        for failure_set in matching_sets:
            set_id = (
                _text(failure_set.payload.get("failure_set_id"))
                or failure_set.stable_id
            )
            failed_attempt_id = _text(failure_set.payload.get("failed_attempt_id"))
            replacement = next(
                (
                    item
                    for item in sorted(
                        replacements, key=lambda candidate: order[candidate.stable_id]
                    )
                    if _text(item.payload.get("status"))
                    in {"succeeded", "completed"}
                    and (
                        _text(item.payload.get("trigger_failure_set_id")) == set_id
                        or (
                            failed_attempt_id is not None
                            and _text(item.payload.get("supersedes_attempt_id"))
                            == failed_attempt_id
                        )
                    )
                ),
                None,
            )
            terminal_ancestors = ancestors.get(
                root_terminal.stable_id, frozenset()
            )
            precedes_terminal = replacement is not None and (
                replacement.stable_id in terminal_ancestors
                or (
                    replacement.created_at is not None
                    and root_terminal.created_at is not None
                    and replacement.created_at < root_terminal.created_at
                )
            )
            if (
                replacement is None
                or not precedes_terminal
            ):
                continue
            return tuple(
                dict.fromkeys(
                    (
                        child.stable_id,
                        report.stable_id,
                        failure_set.stable_id,
                        replacement.stable_id,
                        root_terminal.stable_id,
                    )
                )
            )
    return ()


def _aggregate_outcome(
    facts: Sequence[_Fact],
    *,
    root_run_id: str,
    order: Mapping[str, CausalOrderKeyV1],
    ancestors: Mapping[str, frozenset[str]],
    projection_complete: bool,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    root = _root_fact(facts, root_run_id)
    lifecycle = _root_lifecycle_fact(facts, root_run_id)
    root_status = None if root is None else _text(root.payload.get("status"))
    boundaries = _unresolved_boundaries(facts)
    blocks = _valid_block_signals(facts, root_run_id)
    diagnostics: set[str] = set()
    recovery = _recovery_chain(
        facts,
        root_run_id=root_run_id,
        root_terminal=root,
        order=order,
        ancestors=ancestors,
    )
    child_warning_refs: dict[tuple[str | None, str | None], list[str]] = {}
    for fact in sorted(facts, key=lambda item: item.stable_id):
        if (
            fact.kind in {"child", "run", "run_terminal"}
            and _text(fact.payload.get("role")) == "child"
            and _text(fact.payload.get("status")) in {"failed", "cancelled"}
        ):
            key = (
                _text(fact.payload.get("run_id")),
                _text(fact.payload.get("status")),
            )
            child_warning_refs.setdefault(key, []).append(fact.stable_id)
    child_warnings = [
        {
            "run_id": run_id,
            "status": status,
            "evidence_refs": refs,
        }
        for (run_id, status), refs in child_warning_refs.items()
    ]

    # The priority order is a public presentation contract, not execution
    # status mutation.
    if root_status == "cancelled":
        status = RootOutcomeStatus.CANCELLED
        explanation = "root_cancelled"
        evidence = [root.stable_id] if root else []
    elif root_status == "completed":
        if recovery:
            status = RootOutcomeStatus.COMPLETED_WITH_RECOVERY
            explanation = "child_failure_recovered"
            evidence = list(recovery)
        else:
            status = RootOutcomeStatus.COMPLETED
            explanation = "root_completed"
            evidence = [root.stable_id] if root else []
    elif root_status == "failed" and blocks:
        status = RootOutcomeStatus.BLOCKED
        explanation = str(blocks[0].payload["reason_code"])
        evidence = [root.stable_id, *(item.stable_id for item in blocks)] if root else []
    elif root_status == "failed":
        status = RootOutcomeStatus.FAILED
        explanation = "root_failed"
        evidence = [root.stable_id] if root else []
    elif boundaries:
        status = RootOutcomeStatus.WAITING
        explanation = "waiting_for_user_or_admission"
        evidence = [item.stable_id for item in boundaries]
    elif root is not None or facts:
        status = RootOutcomeStatus.RUNNING if projection_complete else RootOutcomeStatus.UNKNOWN
        explanation = "root_running" if projection_complete else "projection_incomplete"
        evidence = [] if root is None else [root.stable_id]
    else:
        status = RootOutcomeStatus.UNKNOWN
        explanation = "root_facts_missing"
        evidence = []

    if blocks and root_status != "failed":
        diagnostics.add("block_signal_terminal_conflict")
    return (
        {
            "status": status.value,
            "explanation_code": explanation,
            "evidence_refs": list(dict.fromkeys(evidence)),
            "child_warnings": child_warnings,
            "started_at": (
                None if lifecycle is None else lifecycle.payload.get("started_at")
            ),
            "ended_at": (
                None if lifecycle is None else lifecycle.payload.get("ended_at")
            ),
        },
        tuple(sorted(diagnostics)),
    )


def _map_phase(fact: _Fact, *, first_mutation_depth: int | None, depth: int) -> tuple[PhaseTaxonomy, str]:
    payload = fact.payload
    status = _text(payload.get("status"))
    role = _text(payload.get("role"))
    event_kind = _text(payload.get("event_kind"))
    if role == "root" and (
        _is_terminal_status(status) or event_kind in {"run.final", "root.final"}
    ):
        return PhaseTaxonomy.DELIVER, "root_terminal"
    if fact.kind == "boundary" and _text(payload.get("boundary_kind")) in {
        "human",
        "admission",
    }:
        return (
            PhaseTaxonomy.WAIT_USER,
            "unresolved_boundary"
            if fact in _unresolved_boundaries((fact,))
            else "resolved_boundary",
        )
    if fact.kind in {"failure_report", "failure_set"} or (
        fact.kind == "attempt"
        and (
            _text(payload.get("trigger_failure_set_id")) is not None
            or _text(payload.get("supersedes_attempt_id")) is not None
            or int(payload.get("plan_version") or 1) > 1
        )
    ) or (role == "child" and status in {"failed", "cancelled"}):
        return PhaseTaxonomy.VERIFY_REPAIR, "recovery_fact"
    if fact.kind in {"child", "child_command"} or role == "child":
        return PhaseTaxonomy.DELEGATE, "child_lifecycle"
    if fact.kind == "tool":
        activity = _text(payload.get("activity_kind"))
        if activity == "inspect" and (
            first_mutation_depth is None or depth < first_mutation_depth
        ):
            return PhaseTaxonomy.UNDERSTAND, "tool_inspect_before_mutation"
        if activity == "plan":
            return PhaseTaxonomy.PREPARE, "tool_plan"
        if activity in {"verify", "test", "build", "health"} or int(
            payload.get("plan_version") or 1
        ) > 1:
            return PhaseTaxonomy.VERIFY_REPAIR, "tool_verify"
        if activity == "wait":
            return PhaseTaxonomy.WAIT_USER, "tool_wait"
        if activity in {"mutate", "communicate"}:
            return PhaseTaxonomy.EXECUTE, "tool_execute"
        return PhaseTaxonomy.EXECUTE, "legacy_unknown_tool"
    if fact.kind == "workflow_step":
        hint = _text(payload.get("phase_hint"))
        hints = {
            "understand": PhaseTaxonomy.UNDERSTAND,
            "prepare": PhaseTaxonomy.PREPARE,
            "plan": PhaseTaxonomy.PREPARE,
            "delegate": PhaseTaxonomy.DELEGATE,
            "execute": PhaseTaxonomy.EXECUTE,
            "verify": PhaseTaxonomy.VERIFY_REPAIR,
            "repair": PhaseTaxonomy.VERIFY_REPAIR,
            "wait": PhaseTaxonomy.WAIT_USER,
            "deliver": PhaseTaxonomy.DELIVER,
        }
        if hint in hints:
            return hints[hint], "workflow_phase_hint"
        return PhaseTaxonomy.EXECUTE, "workflow_unknown_hint"
    if fact.kind == "provider":
        if payload.get("detached") is True and _text(payload.get("purpose")) == "system_maintenance":
            raise LookupError("detached_system_maintenance")
        stage = _text(payload.get("provider_stage"))
        if stage in {"preanalysis", "classifier"}:
            return PhaseTaxonomy.PREPARE, "provider_preanalysis"
        if first_mutation_depth is None or depth < first_mutation_depth:
            return PhaseTaxonomy.UNDERSTAND, "provider_before_effect"
        return PhaseTaxonomy.EXECUTE, "provider_main"
    if fact.kind in {"user_request", "content"} and _text(payload.get("author")) in {None, "user"}:
        return PhaseTaxonomy.UNDERSTAND, "user_request"
    return PhaseTaxonomy.EXECUTE, "unmapped_execution_fact"


def _semantic_phases(
    facts: Sequence[_Fact],
    *,
    root_run_id: str,
    order: Mapping[str, CausalOrderKeyV1],
    aggregate: Mapping[str, Any],
    projection_complete: bool,
) -> tuple[dict[str, Any], ...]:
    effect_call_keys = {
        (
            _text(fact.payload.get("run_id"))
            or _text(fact.payload.get("root_run_id")),
            _text(fact.payload.get("call_id")),
        )
        for fact in facts
        if fact.kind == "tool"
        and _text(fact.payload.get("effect_id")) is not None
        and _text(fact.payload.get("call_id")) is not None
    }
    visible_facts = tuple(
        fact
        for fact in facts
        if not (
            fact.kind == "tool"
            and _text(fact.payload.get("provider_call_id")) is not None
            and (
                _text(fact.payload.get("run_id"))
                or _text(fact.payload.get("root_run_id")),
                _text(fact.payload.get("provider_call_id")),
            )
            in effect_call_keys
        )
    )
    mutation_depths = [
        order[fact.stable_id].topological_depth
        for fact in visible_facts
        if fact.kind == "tool"
        and _text(fact.payload.get("activity_kind")) in {"mutate", "communicate"}
    ]
    first_mutation_depth = min(mutation_depths) if mutation_depths else None
    grouped: dict[PhaseTaxonomy, list[tuple[_Fact, str]]] = {}
    for fact in sorted(visible_facts, key=lambda item: order[item.stable_id]):
        try:
            taxonomy, reason = _map_phase(
                fact,
                first_mutation_depth=first_mutation_depth,
                depth=order[fact.stable_id].topological_depth,
            )
        except LookupError:
            continue
        grouped.setdefault(taxonomy, []).append((fact, reason))

    if not grouped:
        return ()
    ordered_taxonomies = [item for item in _TAXONOMY_ORDER if item in grouped]
    current = max(
        ordered_taxonomies,
        key=lambda taxonomy: max(
            order[fact.stable_id] for fact, _ in grouped[taxonomy]
        ),
    )
    aggregate_status = RootOutcomeStatus(str(aggregate["status"]))
    recovery_refs = set(_text_list(aggregate.get("evidence_refs")))
    phases: list[dict[str, Any]] = []
    for taxonomy in ordered_taxonomies:
        members = grouped[taxonomy]
        if not projection_complete and not any(
            _is_terminal_status(_text(fact.payload.get("status")))
            for fact, _ in members
        ):
            status = PhaseStatus.UNKNOWN
        elif taxonomy is not current:
            status = PhaseStatus.COMPLETED
        elif aggregate_status is RootOutcomeStatus.CANCELLED:
            status = PhaseStatus.CANCELLED
        elif aggregate_status in {RootOutcomeStatus.FAILED, RootOutcomeStatus.BLOCKED}:
            status = PhaseStatus.FAILED
        elif aggregate_status is RootOutcomeStatus.WAITING:
            status = PhaseStatus.WAITING
        elif aggregate_status in {
            RootOutcomeStatus.COMPLETED,
            RootOutcomeStatus.COMPLETED_WITH_RECOVERY,
        }:
            status = PhaseStatus.COMPLETED
        else:
            status = PhaseStatus.RUNNING
        if (
            taxonomy is PhaseTaxonomy.VERIFY_REPAIR
            and aggregate_status is RootOutcomeStatus.COMPLETED_WITH_RECOVERY
            and recovery_refs.intersection(fact.stable_id for fact, _ in members)
        ):
            status = PhaseStatus.COMPLETED_WITH_RECOVERY

        evidence_refs = [fact.stable_id for fact, _ in members]
        tool_refs = [fact.stable_id for fact, _ in members if fact.kind == "tool"]
        child_refs = [
            fact.stable_id
            for fact, _ in members
            if fact.kind in {"child", "child_command"}
            or _text(fact.payload.get("role")) == "child"
        ]
        steps_by_id: dict[str, dict[str, Any]] = {}
        items: list[dict[str, Any]] = []
        for fact, _ in members:
            step_id = _text(fact.payload.get("workflow_step_id"))
            if step_id is not None:
                step = dict(steps_by_id.get(step_id, {"workflow_step_id": step_id}))
                for field in ("label", "status", "step_index", "step_total"):
                    value = fact.payload.get(field)
                    if value is not None:
                        step[field] = value
                # One semantic workflow step may emit several facts as it moves
                # from running to completed. Preserve its first position while
                # folding later state into that same public row.
                steps_by_id[step_id] = step
            item: dict[str, Any] = {
                "stable_id": fact.stable_id,
                "kind": fact.kind,
            }
            for field in _SAFE_ITEM_FIELDS:
                value = fact.payload.get(field)
                if isinstance(value, (str, int, float, bool)) and value != "":
                    item[field] = value
            items.append(item)
        steps = list(steps_by_id.values())
        numeric_steps = [
            step for step in steps if isinstance(step.get("step_index"), int)
        ]
        totals = [
            int(step["step_total"])
            for step in steps
            if isinstance(step.get("step_total"), int)
            and int(step["step_total"]) > 0
        ]
        phase_id = hashlib.sha256(
            f"{PROJECTION_CONTRACT_VERSION}\0{root_run_id}\0{taxonomy.value}".encode(
                "utf-8"
            )
        ).hexdigest()
        reasons = tuple(dict.fromkeys(reason for _, reason in members))
        phase: dict[str, Any] = {
            "phase_id": phase_id,
            "taxonomy": taxonomy.value,
            "title": _TAXONOMY_TITLES[taxonomy],
            "status": status.value,
            "mapping_reason": reasons[0] if len(reasons) == 1 else "mixed",
            "evidence_refs": evidence_refs,
            "tool_refs": tool_refs,
            "child_refs": child_refs,
            "workflow_steps": steps,
            "items": items,
            "order_key": order[evidence_refs[0]].to_list(),
        }
        if numeric_steps:
            phase["current_step"] = max(int(step["step_index"]) for step in numeric_steps)
        if totals:
            phase["total_steps"] = max(totals)
        phases.append(phase)
    return tuple(phases)


def _read_cut_complete(read_cut: object) -> tuple[bool, tuple[str, ...]]:
    completeness = _read(read_cut, "source_completeness", {})
    unmatched = _read(read_cut, "unmatched_refs", ())
    complete = True
    diagnostics: set[str] = set()
    if isinstance(completeness, Mapping):
        if any(value is not True for value in completeness.values()):
            complete = False
            diagnostics.add("source_incomplete")
    else:
        completeness = {}
    source_cuts = tuple(
        cut
        for name in ("workflow", "state")
        if (cut := _read(read_cut, name)) is not None
    )
    if source_cuts and any(_read(cut, "complete") is not True for cut in source_cuts):
        complete = False
        diagnostics.add("source_incomplete")
    if _text_list(unmatched):
        complete = False
        diagnostics.add("unmatched_refs")
    return complete, tuple(sorted(diagnostics))


def reduce_public_manifest(
    facts: Iterable[object],
    read_cut: object,
    session_id: str,
    root_run_id: str,
) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], bool, tuple[str, ...]]:
    """Reduce one frozen public fact set into aggregate and semantic phases.

    Duplicate stable ids with equal public values are replay-safe.  A duplicate
    identity carrying different bytes is treated as an incomplete projection;
    no last-writer choice is made.
    """

    if _text(session_id) is None or _text(root_run_id) is None:
        raise ValueError("session_id and root_run_id are required")
    normalised = [_normalise_fact(raw) for raw in facts]
    by_id: dict[str, _Fact] = {}
    diagnostics: set[str] = set()
    for fact in normalised:
        if fact.root_run_id != root_run_id:
            diagnostics.add("cross_root_fact")
            continue
        existing = by_id.get(fact.stable_id)
        if existing is None:
            by_id[fact.stable_id] = fact
        elif existing != fact:
            diagnostics.add("stable_id_conflict")
    ordered_facts = tuple(by_id[stable_id] for stable_id in sorted(by_id))
    order, ancestors, order_diagnostics = _causal_order(ordered_facts)
    diagnostics.update(order_diagnostics)
    read_complete, read_diagnostics = _read_cut_complete(read_cut)
    diagnostics.update(read_diagnostics)
    projection_complete = read_complete and not diagnostics.intersection(
        {"causal_cycle", "stable_id_conflict", "cross_root_fact"}
    )
    aggregate, aggregate_diagnostics = _aggregate_outcome(
        ordered_facts,
        root_run_id=root_run_id,
        order=order,
        ancestors=ancestors,
        projection_complete=projection_complete,
    )
    diagnostics.update(aggregate_diagnostics)
    if "block_signal_terminal_conflict" in diagnostics:
        projection_complete = False
    phases = _semantic_phases(
        ordered_facts,
        root_run_id=root_run_id,
        order=order,
        aggregate=aggregate,
        projection_complete=projection_complete,
    )
    return aggregate, phases, projection_complete, tuple(sorted(diagnostics))


__all__ = [
    "CausalOrderKeyV1",
    "PROJECTION_CONTRACT_VERSION",
    "PhaseStatus",
    "PhaseTaxonomy",
    "RootOutcomeStatus",
    "reduce_public_manifest",
]
