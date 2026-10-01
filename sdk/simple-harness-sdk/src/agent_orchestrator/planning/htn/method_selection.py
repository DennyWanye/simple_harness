# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""做法候选的程序过滤，与取证请求的准入（HTN 精简 片 A 第 3 项，2026-10-01）。

为目标选哪个做法由规划器判断；程序只做过滤：按能力与类型把跑不了的做法筛掉，把剩下的
候选连同"为什么别的不适用"如实交给规划器。这里不再有"只有一个候选就由程序直接选""这组
候选已经问过一次就回无变更"这类程序代答，也没有按部署切换的选择策略。

本模块是纯函数：不派发模型、不改计划、不写证据。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ...contracts.models import ContractError, sha256_hex
from ...contracts.planning_decisions import RequestEvidenceDecision
from ...knowledge.predicates import PredicateRegistry
from .applicability import ApplicabilityReport, ApplicabilityStatus
from .observation_pipeline import ObserverIndex

MAX_EVIDENCE_QUESTIONS = 8


@dataclass(frozen=True, slots=True)
class MethodSelectionCandidateV1:
    """A candidate identity and its deterministic applicability result."""

    method_id: str
    method_version: int = 1
    method_content_hash: str = ""
    report: ApplicabilityReport | ApplicabilityStatus = ApplicabilityStatus.APPLICABLE
    bindings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.method_id, str) or not self.method_id:
            raise ContractError("selection candidate method_id must be non-empty")
        if type(self.method_version) is not int or self.method_version < 1:
            raise ContractError("selection candidate method_version must be positive")
        if not isinstance(self.method_content_hash, str):
            raise ContractError("selection candidate method_content_hash must be text")
        report = self.report
        if isinstance(report, ApplicabilityReport):
            status = report.status
        else:
            status = ApplicabilityStatus(report)
        object.__setattr__(self, "report", status)
        if not isinstance(self.bindings, Mapping):
            raise ContractError("selection candidate bindings must be an object")
        object.__setattr__(self, "bindings", dict(self.bindings))

    @property
    def applicable(self) -> bool:
        return self.report is ApplicabilityStatus.APPLICABLE

    def to_json(self) -> dict[str, Any]:
        return {
            "method_id": self.method_id,
            "method_version": self.method_version,
            "method_content_hash": self.method_content_hash,
            "status": str(self.report),
            "bindings": dict(self.bindings),
        }


def _candidate(value: MethodSelectionCandidateV1 | Mapping[str, Any]) -> MethodSelectionCandidateV1:
    if isinstance(value, MethodSelectionCandidateV1):
        return value
    if isinstance(value, Mapping):
        return MethodSelectionCandidateV1(
            method_id=value.get("method_id", value.get("id", "")),
            method_version=value.get("method_version", value.get("version", 1)),
            method_content_hash=value.get("method_content_hash", value.get("content_hash", "")),
            report=value.get("report", value.get("status", ApplicabilityStatus.APPLICABLE)),
            bindings=value.get("bindings", {}),
        )
    raise ContractError("selection candidates must be typed candidates or objects")


def normalize_candidates(
    candidates: Iterable[MethodSelectionCandidateV1 | Mapping[str, Any]],
) -> tuple[MethodSelectionCandidateV1, ...]:
    """Normalize and deterministically order a candidate set."""

    result = tuple(_candidate(item) for item in candidates)
    keys = [(item.method_id, item.method_version, item.method_content_hash) for item in result]
    if len(set(keys)) != len(keys):
        raise ContractError("selection candidate set contains duplicate method identities")
    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.method_id,
                item.method_version,
                item.method_content_hash,
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class MethodCandidates:
    """One open goal's candidates after the program filter."""

    candidates: tuple[MethodSelectionCandidateV1, ...]
    applicable: tuple[MethodSelectionCandidateV1, ...]


def filter_candidates(
    candidates: Iterable[MethodSelectionCandidateV1 | Mapping[str, Any]],
) -> MethodCandidates:
    """The whole candidate set, and the part of it that can run at all."""

    normalized = normalize_candidates(candidates)
    return MethodCandidates(normalized, tuple(item for item in normalized if item.applicable))


def _predicate_key(value: str) -> tuple[str, int | None]:
    if not isinstance(value, str) or not value:
        raise ContractError("evidence question predicate_key must be non-empty")
    if "@" not in value:
        return value, None
    name, version = value.rsplit("@", 1)
    try:
        parsed = int(version)
    except ValueError as error:
        raise ContractError("evidence question predicate_key has invalid version") from error
    if not name or parsed < 1:
        raise ContractError("evidence question predicate_key has invalid predicate ref")
    return name, parsed


def _observer_available(predicate_key: str, observers: Any) -> bool:
    if isinstance(observers, ObserverIndex):
        name, _ = _predicate_key(predicate_key)
        return observers.observer_for(name) is not None
    if isinstance(observers, Mapping):
        name, _ = _predicate_key(predicate_key)
        return bool(observers.get(predicate_key, observers.get(name)))
    if isinstance(observers, (set, frozenset, tuple, list)):
        name, _ = _predicate_key(predicate_key)
        return predicate_key in observers or name in observers
    return False


def validate_evidence_request(
    request: RequestEvidenceDecision,
    *,
    registry: PredicateRegistry,
    observers: Any,
    authorized_predicates: Iterable[str] = (),
    authorized_scopes: Iterable[str] = (),
) -> RequestEvidenceDecision:
    """Admit a formal H3 ``REQUEST_EVIDENCE`` against producer-backed facts.

    A question is accepted only when its exact predicate version is registered,
    this deployment has an observer for it, and the caller has an explicit
    predicate or authority-scope grant.  The function never treats a registry
    declaration or a model-provided authority string as a grant.
    """

    if not isinstance(request, RequestEvidenceDecision):
        raise ContractError("evidence request must be a RequestEvidenceDecision")
    if len(request.questions) > MAX_EVIDENCE_QUESTIONS:
        raise ContractError("REQUEST_EVIDENCE may contain at most 8 questions")
    if not isinstance(registry, PredicateRegistry):
        raise ContractError("evidence request requires a PredicateRegistry")
    predicate_grants = frozenset(str(item) for item in authorized_predicates)
    scope_grants = frozenset(str(item) for item in authorized_scopes)
    seen: set[str] = set()
    for position, question in enumerate(request.questions):
        key = question.predicate_key
        question_identity = sha256_hex(
            {"predicate_key": key, "arguments": dict(question.arguments)}
        )
        if question_identity in seen:
            raise ContractError(f"REQUEST_EVIDENCE repeats question {position}")
        seen.add(question_identity)
        name, version = _predicate_key(key)
        signatures = tuple(
            item
            for item in registry.signatures()
            if item.predicate_ref.id == name
            and (version is None or item.predicate_ref.version == version)
        )
        if len(signatures) != 1:
            raise ContractError(
                f"evidence predicate {key!r} is not registered at one exact version"
            )
        signature = signatures[0]
        if not _observer_available(key, observers):
            raise ContractError(f"no observer is installed for evidence predicate {key!r}")
        if key not in predicate_grants and name not in predicate_grants and (
            signature.authority_scope is None or signature.authority_scope not in scope_grants
        ):
            raise ContractError(f"evidence predicate {key!r} is outside the caller authority")
    return request


__all__ = (
    "MAX_EVIDENCE_QUESTIONS",
    "MethodCandidates",
    "MethodSelectionCandidateV1",
    "filter_candidates",
    "normalize_candidates",
    "validate_evidence_request",
)
