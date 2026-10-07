# SPDX-License-Identifier: Apache-2.0
"""执行图三个只读视图的严格合同（原计划 §4.3 / 附录 E；第 2 批 T06、T07）。

视图（``TaskGraphViewV1``）、"为什么还没开工"解释（``TaskGraphExplanationV1``）、收敛视图
（``TaskGraphConvergenceViewV2``）三种返回在这里编解码：字段集合精确、枚举与上界同 ``graph/schemas/``
里的三份 Schema 一致（一致性由正负样本用例守住）。只核结构，不读库、不授权；``snapshot_hash``
是否对得上由组装方算，这里不重算。

推后第 3 批 U09：执行过程主画面（``TaskGraphExecutionViewV1``）与回合详情（``TaskGraphExecutionDetailV1``）
也在这里，对应 ``taskgraph-execution-view-v1`` / ``taskgraph-execution-detail-v1`` 两份 Schema。节点按
``kind``、回合条目按 ``t`` 分种，每种字段集合精确；与 Schema 的收拒完全相同（同一组正负样本守住）。
"""
from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NoReturn, cast

from ..contracts.models import ContractError

_JS_MAX = 2**53 - 1
READINESS = frozenset({
    "NOT_SELECTED", "NEEDS_REFINEMENT", "WAITING_ORDER", "WAITING_DATA", "WAITING_EVIDENCE",
    "WAITING_APPROVAL", "STALE_BINDING", "READY_CANDIDATE", "WAITING_OPERATION_UNKNOWN",
    "OBSERVER_UNAVAILABLE", "GRAPH_INTEGRITY", "VALIDITY_RECHECK_PENDING",
})
FORMS = frozenset({"compound", "primitive"})
EDGE_KINDS = frozenset({"refinement", "order", "data"})
VIEW_MODES = frozenset({"CURRENT", "HISTORICAL_STRUCTURE"})
JOB_STATES = frozenset({"FENCED", "WAITING", "READY", "APPLIED", "ABANDONED"})
TARGET_KINDS = frozenset({"RETIRING", "INPUT_REPLACED"})
FOLLOWUP_KINDS = frozenset({"REEVALUATE", "CONVERGE", "REQUEST_COMPOSITION"})


def _invalid(reason: str) -> NoReturn:
    raise ContractError(f"TASKGRAPH_VIEW_INVALID: {reason}")


def _text(value: object, name: str, *, maximum: int = 512, minimum: int = 1) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        _invalid(f"{name} must be a string of {minimum}..{maximum} characters")
    return value


def _integer(value: object, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= _JS_MAX:
        _invalid(f"{name} must be an integer in {minimum}..2^53-1")
    return value


def _hash(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        _invalid(f"{name} must be a lowercase SHA-256")
    return value


def _one_of(value: object, allowed: frozenset[str], name: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        _invalid(f"{name} must be one of {sorted(allowed)}")
    return value


def _fields(value: object, fields: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _invalid(f"{name} has missing or unknown fields")
    return value


def _array(value: object, name: str, *, maximum: int) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence) or len(value) > maximum:
        _invalid(f"{name} must be an array of at most {maximum} items")
    return tuple(value)


def _texts(value: object, name: str, *, maximum: int) -> tuple[str, ...]:
    return tuple(_text(item, name + "[]") for item in _array(value, name, maximum=maximum))


def _schema_version(value: object, expected: int) -> int:
    if type(value) is not int or value != expected:
        _invalid(f"schema_version must be {expected}")
    return value


# ------------------------------------------------------------------ shared pieces

@dataclass(frozen=True, slots=True, kw_only=True)
class ValidityEpochV1:
    scope_id: str
    epoch: int

    def __post_init__(self) -> None:
        _text(self.scope_id, "validity_epochs[].scope_id")
        _integer(self.epoch, "validity_epochs[].epoch")

    def to_json(self) -> dict[str, Any]:
        return {"scope_id": self.scope_id, "epoch": self.epoch}

    @classmethod
    def from_json(cls, value: object) -> ValidityEpochV1:
        row = _fields(value, {"scope_id", "epoch"}, "validity_epoch")
        return cls(scope_id=row["scope_id"], epoch=row["epoch"])


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadTokenV1:
    plan_revision: int
    through_seq: int
    validity_epochs: tuple[ValidityEpochV1, ...]
    snapshot_hash: str
    manifest_hash: str

    def __post_init__(self) -> None:
        _integer(self.plan_revision, "read_token.plan_revision")
        _integer(self.through_seq, "read_token.through_seq")
        epochs = _array(self.validity_epochs, "read_token.validity_epochs", maximum=256)
        if not all(isinstance(item, ValidityEpochV1) for item in epochs):
            _invalid("read_token.validity_epochs must contain ValidityEpochV1 values")
        object.__setattr__(self, "validity_epochs", epochs)
        _hash(self.snapshot_hash, "read_token.snapshot_hash")
        _hash(self.manifest_hash, "read_token.manifest_hash")

    def to_json(self) -> dict[str, Any]:
        return {"plan_revision": self.plan_revision, "through_seq": self.through_seq,
                "validity_epochs": [item.to_json() for item in self.validity_epochs],
                "snapshot_hash": self.snapshot_hash, "manifest_hash": self.manifest_hash}

    @classmethod
    def from_json(cls, value: object) -> ReadTokenV1:
        row = _fields(value, {"plan_revision", "through_seq", "validity_epochs", "snapshot_hash", "manifest_hash"},
                      "read_token")
        return cls(plan_revision=row["plan_revision"], through_seq=row["through_seq"],
                   validity_epochs=tuple(ValidityEpochV1.from_json(item) for item in
                                         _array(row["validity_epochs"], "read_token.validity_epochs", maximum=256)),
                   snapshot_hash=row["snapshot_hash"], manifest_hash=row["manifest_hash"])


@dataclass(frozen=True, slots=True, kw_only=True)
class ViewRefV1:
    """``{kind, id, revision, content_hash}``：根结论引用、解释里的来源引用、收敛作业的诊断引用。"""

    kind: str
    id: str
    revision: int
    content_hash: str

    def __post_init__(self) -> None:
        _text(self.kind, "ref.kind")
        _text(self.id, "ref.id")
        _integer(self.revision, "ref.revision")
        _hash(self.content_hash, "ref.content_hash")

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "revision": self.revision, "content_hash": self.content_hash}

    @classmethod
    def from_json(cls, value: object) -> ViewRefV1:
        row = _fields(value, {"kind", "id", "revision", "content_hash"}, "ref")
        return cls(kind=row["kind"], id=row["id"], revision=row["revision"], content_hash=row["content_hash"])


def _refs(value: object, name: str, *, maximum: int) -> tuple[ViewRefV1, ...]:
    return tuple(item if isinstance(item, ViewRefV1) else ViewRefV1.from_json(item)
                 for item in _array(value, name, maximum=maximum))


# ------------------------------------------------------------------ taskgraph-view-v1

@dataclass(frozen=True, slots=True, kw_only=True)
class ViewNodeV1:
    occurrence_id: str
    task_id: str
    obligation_id: str
    form: str
    contract_revision: int
    dispatch_generation: int
    phase: str
    readiness: str
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("occurrence_id", "task_id", "obligation_id", "phase"):
            _text(getattr(self, name), "nodes[]." + name)
        _one_of(self.form, FORMS, "nodes[].form")
        _integer(self.contract_revision, "nodes[].contract_revision")
        _integer(self.dispatch_generation, "nodes[].dispatch_generation")
        _one_of(self.readiness, READINESS, "nodes[].readiness")
        object.__setattr__(self, "reason_codes", _texts(self.reason_codes, "nodes[].reason_codes", maximum=32))

    def to_json(self) -> dict[str, Any]:
        return {"occurrence_id": self.occurrence_id, "task_id": self.task_id, "obligation_id": self.obligation_id,
                "form": self.form, "contract_revision": self.contract_revision,
                "dispatch_generation": self.dispatch_generation, "phase": self.phase,
                "readiness": self.readiness, "reason_codes": list(self.reason_codes)}

    @classmethod
    def from_json(cls, value: object) -> ViewNodeV1:
        row = _fields(value, {"occurrence_id", "task_id", "obligation_id", "form", "contract_revision",
                              "dispatch_generation", "phase", "readiness", "reason_codes"}, "node")
        return cls(**{key: row[key] for key in row if key != "reason_codes"},
                   reason_codes=_texts(row["reason_codes"], "nodes[].reason_codes", maximum=32))


@dataclass(frozen=True, slots=True, kw_only=True)
class ViewEdgeV1:
    kind: str
    source: str
    target: str
    identity: str

    def __post_init__(self) -> None:
        _one_of(self.kind, EDGE_KINDS, "edges[].kind")
        for name in ("source", "target", "identity"):
            _text(getattr(self, name), "edges[]." + name)

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "source": self.source, "target": self.target, "identity": self.identity}

    @classmethod
    def from_json(cls, value: object) -> ViewEdgeV1:
        row = _fields(value, {"kind", "source", "target", "identity"}, "edge")
        return cls(kind=row["kind"], source=row["source"], target=row["target"], identity=row["identity"])


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskGraphViewV1:
    mission_id: str
    read_token: ReadTokenV1
    nodes: tuple[ViewNodeV1, ...]
    edges: tuple[ViewEdgeV1, ...]
    planning_frontier: tuple[str, ...]
    execution_frontier: tuple[str, ...]
    root_resolution_refs: tuple[ViewRefV1, ...]
    view_mode: str
    next_cursor: None = None
    complete: bool = True
    schema_version: int = 1

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, 1)
        _text(self.mission_id, "mission_id")
        if not isinstance(self.read_token, ReadTokenV1):
            _invalid("read_token must be a ReadTokenV1")
        nodes = _array(self.nodes, "nodes", maximum=4096)
        edges = _array(self.edges, "edges", maximum=8192)
        if not all(isinstance(item, ViewNodeV1) for item in nodes):
            _invalid("nodes must contain ViewNodeV1 values")
        if not all(isinstance(item, ViewEdgeV1) for item in edges):
            _invalid("edges must contain ViewEdgeV1 values")
        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "edges", edges)
        object.__setattr__(self, "planning_frontier", _texts(self.planning_frontier, "planning_frontier", maximum=4096))
        object.__setattr__(self, "execution_frontier", _texts(self.execution_frontier, "execution_frontier", maximum=4096))
        roots = _refs(self.root_resolution_refs, "root_resolution_refs", maximum=256)
        if any(item.kind != "resolution" for item in roots):
            _invalid("root_resolution_refs[].kind must be 'resolution'")
        object.__setattr__(self, "root_resolution_refs", roots)
        if self.next_cursor is not None:
            _invalid("next_cursor must be null: the view is FULL_BOUNDED_SNAPSHOT")
        if self.complete is not True:
            _invalid("complete must be true")
        _one_of(self.view_mode, VIEW_MODES, "view_mode")

    def to_json(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "mission_id": self.mission_id,
                "read_token": self.read_token.to_json(),
                "nodes": [item.to_json() for item in self.nodes], "edges": [item.to_json() for item in self.edges],
                "planning_frontier": list(self.planning_frontier), "execution_frontier": list(self.execution_frontier),
                "root_resolution_refs": [item.to_json() for item in self.root_resolution_refs],
                "next_cursor": None, "complete": True, "view_mode": self.view_mode}

    @classmethod
    def from_json(cls, value: object) -> TaskGraphViewV1:
        row = _fields(value, {"schema_version", "mission_id", "read_token", "nodes", "edges", "planning_frontier",
                              "execution_frontier", "root_resolution_refs", "next_cursor", "complete", "view_mode"},
                      "view")
        return cls(schema_version=row["schema_version"], mission_id=row["mission_id"],
                   read_token=ReadTokenV1.from_json(row["read_token"]),
                   nodes=tuple(ViewNodeV1.from_json(item) for item in _array(row["nodes"], "nodes", maximum=4096)),
                   edges=tuple(ViewEdgeV1.from_json(item) for item in _array(row["edges"], "edges", maximum=8192)),
                   planning_frontier=cast(Any, row["planning_frontier"]),
                   execution_frontier=cast(Any, row["execution_frontier"]),
                   root_resolution_refs=cast(Any, row["root_resolution_refs"]),
                   next_cursor=cast(Any, row["next_cursor"]), complete=cast(Any, row["complete"]),
                   view_mode=row["view_mode"])


# ------------------------------------------------------------------ taskgraph-explanation-v1

@dataclass(frozen=True, slots=True, kw_only=True)
class TaskGraphExplanationV1:
    mission_id: str
    occurrence_id: str
    read_token: ReadTokenV1
    readiness: str
    reason_codes: tuple[str, ...]
    source_refs: tuple[ViewRefV1, ...]
    details: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, 1)
        _text(self.mission_id, "mission_id")
        _text(self.occurrence_id, "occurrence_id")
        if not isinstance(self.read_token, ReadTokenV1):
            _invalid("read_token must be a ReadTokenV1")
        _one_of(self.readiness, READINESS, "readiness")
        object.__setattr__(self, "reason_codes", _texts(self.reason_codes, "reason_codes", maximum=32))
        object.__setattr__(self, "source_refs", _refs(self.source_refs, "source_refs", maximum=64))
        details = _array(self.details, "details", maximum=32)
        object.__setattr__(self, "details", tuple(
            _text(item, "details[]", maximum=2000, minimum=0) for item in details))

    def to_json(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "mission_id": self.mission_id,
                "occurrence_id": self.occurrence_id, "read_token": self.read_token.to_json(),
                "readiness": self.readiness, "reason_codes": list(self.reason_codes),
                "source_refs": [item.to_json() for item in self.source_refs], "details": list(self.details)}

    @classmethod
    def from_json(cls, value: object) -> TaskGraphExplanationV1:
        row = _fields(value, {"schema_version", "mission_id", "occurrence_id", "read_token", "readiness",
                              "reason_codes", "source_refs", "details"}, "explanation")
        return cls(schema_version=row["schema_version"], mission_id=row["mission_id"],
                   occurrence_id=row["occurrence_id"], read_token=ReadTokenV1.from_json(row["read_token"]),
                   readiness=row["readiness"], reason_codes=cast(Any, row["reason_codes"]),
                   source_refs=cast(Any, row["source_refs"]), details=cast(Any, row["details"]))


# ------------------------------------------------------------------ taskgraph-convergence-view-v2

@dataclass(frozen=True, slots=True, kw_only=True)
class ConvergenceTargetV1:
    occurrence_id: str
    task_id: str
    expected_generation: int
    target_kind: str

    def __post_init__(self) -> None:
        _text(self.occurrence_id, "targets[].occurrence_id")
        _text(self.task_id, "targets[].task_id")
        _integer(self.expected_generation, "targets[].expected_generation")
        _one_of(self.target_kind, TARGET_KINDS, "targets[].target_kind")

    def to_json(self) -> dict[str, Any]:
        return {"occurrence_id": self.occurrence_id, "task_id": self.task_id,
                "expected_generation": self.expected_generation, "target_kind": self.target_kind}

    @classmethod
    def from_json(cls, value: object) -> ConvergenceTargetV1:
        row = _fields(value, {"occurrence_id", "task_id", "expected_generation", "target_kind"}, "target")
        return cls(occurrence_id=row["occurrence_id"], task_id=row["task_id"],
                   expected_generation=row["expected_generation"], target_kind=row["target_kind"])


@dataclass(frozen=True, slots=True, kw_only=True)
class ConvergenceJobV1:
    job_id: str
    decision_id: str
    source_revision: int
    candidate_hash: str
    impact_hash: str
    state: str
    row_version: int
    targets: tuple[ConvergenceTargetV1, ...]
    diagnostic_refs: tuple[ViewRefV1, ...]

    def __post_init__(self) -> None:
        _text(self.job_id, "jobs[].job_id")
        _text(self.decision_id, "jobs[].decision_id")
        _integer(self.source_revision, "jobs[].source_revision")
        _hash(self.candidate_hash, "jobs[].candidate_hash")
        _hash(self.impact_hash, "jobs[].impact_hash")
        _one_of(self.state, JOB_STATES, "jobs[].state")
        _integer(self.row_version, "jobs[].row_version", minimum=1)
        targets = _array(self.targets, "jobs[].targets", maximum=4096)
        object.__setattr__(self, "targets", tuple(
            item if isinstance(item, ConvergenceTargetV1) else ConvergenceTargetV1.from_json(item) for item in targets))
        object.__setattr__(self, "diagnostic_refs", _refs(self.diagnostic_refs, "jobs[].diagnostic_refs", maximum=64))

    def to_json(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "decision_id": self.decision_id, "source_revision": self.source_revision,
                "candidate_hash": self.candidate_hash, "impact_hash": self.impact_hash, "state": self.state,
                "row_version": self.row_version, "targets": [item.to_json() for item in self.targets],
                "diagnostic_refs": [item.to_json() for item in self.diagnostic_refs]}

    @classmethod
    def from_json(cls, value: object) -> ConvergenceJobV1:
        row = _fields(value, {"job_id", "decision_id", "source_revision", "candidate_hash", "impact_hash", "state",
                              "row_version", "targets", "diagnostic_refs"}, "job")
        return cls(**{key: row[key] for key in row})


@dataclass(frozen=True, slots=True, kw_only=True)
class BlockedNotificationV1:
    """连败被挡住的推进通知（HTN 阶段 B 第 2 条）：带重发要的行版本。"""

    message_id: str
    row_version: int
    kind: str
    subject_key: str
    error_code: str
    attempts: int

    def __post_init__(self) -> None:
        _text(self.message_id, "blocked_notifications[].message_id")
        _integer(self.row_version, "blocked_notifications[].row_version", minimum=1)
        _one_of(self.kind, FOLLOWUP_KINDS, "blocked_notifications[].kind")
        _text(self.subject_key, "blocked_notifications[].subject_key")
        _text(self.error_code, "blocked_notifications[].error_code")
        _integer(self.attempts, "blocked_notifications[].attempts")

    def to_json(self) -> dict[str, Any]:
        return {"message_id": self.message_id, "row_version": self.row_version, "kind": self.kind,
                "subject_key": self.subject_key, "error_code": self.error_code, "attempts": self.attempts}

    @classmethod
    def from_json(cls, value: object) -> BlockedNotificationV1:
        row = _fields(value, {"message_id", "row_version", "kind", "subject_key", "error_code", "attempts"},
                      "blocked_notification")
        return cls(**{key: row[key] for key in row})


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskGraphConvergenceViewV2:
    mission_id: str
    through_seq: int
    jobs: tuple[ConvergenceJobV1, ...]
    blocked_notifications: tuple[BlockedNotificationV1, ...]
    complete: bool = True
    schema_version: int = 2

    def __post_init__(self) -> None:
        _schema_version(self.schema_version, 2)
        _text(self.mission_id, "mission_id")
        _integer(self.through_seq, "through_seq")
        jobs = _array(self.jobs, "jobs", maximum=4096)
        object.__setattr__(self, "jobs", tuple(
            item if isinstance(item, ConvergenceJobV1) else ConvergenceJobV1.from_json(item) for item in jobs))
        blocked = _array(self.blocked_notifications, "blocked_notifications", maximum=4096)
        object.__setattr__(self, "blocked_notifications", tuple(
            item if isinstance(item, BlockedNotificationV1) else BlockedNotificationV1.from_json(item)
            for item in blocked))
        if self.complete is not True:
            _invalid("complete must be true")

    def to_json(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "mission_id": self.mission_id, "through_seq": self.through_seq,
                "jobs": [item.to_json() for item in self.jobs],
                "blocked_notifications": [item.to_json() for item in self.blocked_notifications],
                "complete": True}

    @classmethod
    def from_json(cls, value: object) -> TaskGraphConvergenceViewV2:
        row = _fields(value, {"schema_version", "mission_id", "through_seq", "jobs", "blocked_notifications",
                              "complete"}, "convergence_view")
        return cls(**{key: row[key] for key in row})


# ------------------------------------------------------------------ execution process（推后第 3 批 U09）
#
# 字段表即合同：``_record`` 按"必有字段 / 可有字段 → 核对函数"逐个核，多一个、少一个都拒。
# 上界取 ``orchestrator/taskgraph_execution_view.py`` 的截断长度；不定长的标识取 1024。

_Check = Callable[[object, str], Any]


def _t(minimum: int, maximum: int) -> _Check:
    return lambda value, name: _text(value, name, minimum=minimum, maximum=maximum)


def _nt(minimum: int, maximum: int) -> _Check:
    return lambda value, name: None if value is None else _text(value, name, minimum=minimum, maximum=maximum)


def _i(minimum: int = 0) -> _Check:
    return lambda value, name: _integer(value, name, minimum=minimum)


def _ni(minimum: int = 0) -> _Check:
    return lambda value, name: None if value is None else _integer(value, name, minimum=minimum)


def _signed_or_null(value: object, name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not -_JS_MAX <= value <= _JS_MAX:
        _invalid(f"{name} must be a safe integer or null")
    return value


def _bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        _invalid(f"{name} must be a boolean")
    return value


def _enum(*allowed: str) -> _Check:
    words = frozenset(allowed)
    return lambda value, name: _one_of(value, words, name)


def _const(expected: Any) -> _Check:
    def check(value: object, name: str) -> Any:
        if type(value) is not type(expected) or value != expected:
            _invalid(f"{name} must be {expected!r}")
        return value
    return check


def _list(item: _Check, maximum: int) -> _Check:
    return lambda value, name: [item(entry, f"{name}[]") for entry in _array(value, name, maximum=maximum)]


def _record(required: Mapping[str, _Check], optional: Mapping[str, _Check] | None = None, *,
            nullable: bool = False) -> _Check:
    extra = dict(optional or {})

    def check(value: object, name: str) -> dict[str, Any] | None:
        if value is None and nullable:
            return None
        if not isinstance(value, Mapping) or not set(required) <= set(value) or set(value) - set(required) - set(extra):
            _invalid(f"{name} has missing or unknown fields")
        return {key: rule(value[key], f"{name}.{key}") for key, rule in (*required.items(), *extra.items())
                if key in value}
    return check


def _tagged(tag: str, kinds: Mapping[str, _Check]) -> _Check:
    def check(value: object, name: str) -> dict[str, Any]:
        kind = value.get(tag) if isinstance(value, Mapping) else None
        if not isinstance(kind, str) or kind not in kinds:
            _invalid(f"{name}.{tag} must be one of {sorted(kinds)}")
        return kinds[kind](value, name)
    return check


_ID, _WORD = _t(1, 1024), _t(1, 128)
_NID = _nt(1, 1024)
_SUMMARY = _record({"text": _t(1, 240),
                    "source_kind": _enum("plan_commit_receipt", "result_envelope", "verification", "planning_decision",
                                         "repair_request", "review_record", "action"),
                    "source_ref": _ID}, nullable=True)
_TURN_REF = {"intent_id": _ID, "agent_id": _NID, "state": _WORD, "profile_id": _NID, "model": _nt(1, 512)}
_TURN = _record(_TURN_REF, nullable=True)


def _node_kind(kind: str, fields: Mapping[str, _Check], optional: Mapping[str, _Check] | None = None) -> _Check:
    return _record({"node_id": _ID, "kind": _const(kind), "at_ms": _ni(), **fields}, optional)


EXECUTION_NODE = _tagged("kind", {
    "attempt": _node_kind("attempt", {
        "attempt_id": _ID, "task_id": _ID, "occurrence_id": _NID, "plan_revision": _ni(), "ordinal": _i(1),
        "status": _WORD, "role": _WORD, "turn": _TURN, "summary": _SUMMARY, "result_id": _NID},
        {"outcome": _nt(1, 40)}),
    "check": _node_kind("check", {
        "result_id": _ID, "attempt_id": _ID, "verdict": _nt(1, 128), "state": _WORD,
        "layers": _list(_record({"layer": _WORD, "status": _WORD},
                                {"summary": _t(1, 400), "critic_intent_id": _ID}), 256),
        "turn": _TURN, "summary": _SUMMARY}),
    "review": _node_kind("review", {
        "purpose": _enum("MISSION_FINAL", "ACTION_PROPOSAL", "OPERATION_OUTCOME", "COMPOSITION", "METHOD_PLAN",
                         "MISSION_JUDGE"),
        "record_id": _NID, "verdict": _nt(1, 128), "turn": _TURN, "summary": _SUMMARY}),
    "planning": _node_kind("planning", {
        "role": _const("planner"), "request_id": _ID, "base_plan_revision": _ni(),
        "decisions": _list(_record({"decision_id": _ID, "decision_type": _nt(1, 128), "status": _WORD,
                                    "rejection_codes": _list(_t(0, 256), 16)}), 1000),
        "turn": _TURN, "summary": _SUMMARY}),
    "repair_request": _node_kind("repair_request", {
        "request_id": _ID, "trigger_refs": _list(_ID, 8), "source_event_type": _nt(1, 60), "summary": _SUMMARY}),
    "plan_revision": _node_kind("plan_revision", {
        "plan_revision": _i(), "state": _WORD, "base_revision": _ni(), "summary": _SUMMARY}),
    "operation": _node_kind("operation", {
        "action_key": _ID, "state": _WORD, "connector": _nt(1, 60), "operation": _nt(1, 60), "target": _nt(1, 200),
        "summary": _SUMMARY}),
})
_EXECUTION_EDGE = _record({
    "kind": _enum("attempt_of", "rework_of", "review_of", "reviews", "repair_requested", "decision_for",
                  "retry_authorized", "committed_as", "supersedes", "operation_of"),
    "source": _ID, "target": _ID, "target_layer": _enum("execution", "structure")})


def _execution_hash(value: object, name: str) -> str:
    return _hash(value, name)


_EXECUTION_VIEW = _record({
    "schema_version": _const(1),
    "mission_id": _t(1, 512),
    "view_mode": _const("CURRENT"),
    "read_token": lambda value, name: ReadTokenV1.from_json(value).to_json(),
    "graph": lambda value, name: None if value is None else TaskGraphViewV1.from_json(value).to_json(),
    "occurrence_labels": lambda value, name: None if value is None else _list(_record({
        "occurrence_id": _ID, "step_key": _t(0, 80), "step_index": _ni(),
        "duties": _list(_t(0, 600), 12)}), 10000)(value, name),
    "execution_cut": _record({
        "observed_at_ms": _i(), "imported_through_seq": _i(), "execution_hash": _execution_hash,
        "runtime_source_watermarks": _list(_record({"profile_id": _ID, "in_flight_turns": _i(1)}), 256),
        "coverage": _enum("COMPLETE", "PENDING_IMPORT")}),
    "execution_nodes": _list(EXECUTION_NODE, 200),
    "execution_edges": _list(_EXECUTION_EDGE, 4096),
    "next_cursor": _nt(1, 4096),
    "complete": _bool,
})

_TURN_ITEM = _tagged("t", {
    "say": _record({"t": _const("say"), "text": _t(0, 600)}),
    "submit": _record({"t": _const("submit"), "outcome": _t(0, 40), "text": _t(0, 600)}),
    "decision": _record({"t": _const("decision"), "decision_type": _t(0, 40), "text": _t(0, 600)}),
    "method": _record({"t": _const("method"), "steps": _list(_t(0, 80), 40), "text": _t(0, 600)}),
    "verdict": _record({"t": _const("verdict"), "verdict": _t(0, 40),
                        "reasons": _list(_record({"verdict": _t(0, 20), "text": _t(0, 400)}), 8)}),
    "tool": _record({"t": _const("tool"), "tool": _t(0, 60), "ok": _bool},
                    {"error": _t(0, 200), "path": _t(0, 200), "bytes": _i(), "chars": _i(),
                     "files": _list(_t(0, 120), 12), "file_count": _i(), "passed": _bool,
                     "returncode": _signed_or_null, "tail": _t(0, 200), "found": _i(), "count": _i(2)}),
})
_EXECUTION_DETAIL = _record({
    "schema_version": _const(1),
    "mission_id": _t(1, 512),
    "node": EXECUTION_NODE,
    "turn": _record({**_TURN_REF, "coverage": _enum("COMPLETE", "PENDING_IMPORT", "SOURCE_UNAVAILABLE"),
                     "through_journal_seq": _ni()}, nullable=True),
    "items": _list(_TURN_ITEM, 120),
    "hidden_items": _i(),
})


@dataclass(frozen=True, slots=True)
class TaskGraphExecutionViewV1:
    """``taskgraph.execution_snapshot`` 的一页：严格执行图（第一页）加执行过程节点与边。"""

    body: Mapping[str, Any]

    def to_json(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self.body))

    @classmethod
    def from_json(cls, value: object) -> TaskGraphExecutionViewV1:
        return cls(body=cast(dict[str, Any], _EXECUTION_VIEW(value, "execution_view")))


@dataclass(frozen=True, slots=True)
class TaskGraphExecutionDetailV1:
    """``taskgraph.execution_detail``：一个执行节点与（模型回合时）这一回合看得见的条目。"""

    body: Mapping[str, Any]

    def to_json(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self.body))

    @classmethod
    def from_json(cls, value: object) -> TaskGraphExecutionDetailV1:
        return cls(body=cast(dict[str, Any], _EXECUTION_DETAIL(value, "execution_detail")))


__all__ = ("BlockedNotificationV1", "ConvergenceJobV1", "ConvergenceTargetV1", "ReadTokenV1",
           "TaskGraphConvergenceViewV2", "TaskGraphExecutionDetailV1", "TaskGraphExecutionViewV1",
           "TaskGraphExplanationV1", "TaskGraphViewV1", "ValidityEpochV1", "ViewEdgeV1", "ViewNodeV1", "ViewRefV1")
