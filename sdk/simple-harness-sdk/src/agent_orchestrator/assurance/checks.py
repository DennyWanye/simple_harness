# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Pure three-valued check/review decisions, independent of authority admission."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from .codec import (
    MAX_BYTES,
    AssuranceError,
    array,
    canonical,
    decode,
    fields,
    integer,
    one_of,
    text,
    unique_texts,
)
from .refs import AssuranceRef


class Grade(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


def grade(value: object) -> Grade:
    return Grade(one_of(value, {"PASS", "FAIL", "UNKNOWN"}))


def tri_all(grades: Sequence[Grade]) -> Grade:
    if not grades:
        raise AssuranceError("EMPTY_CHECK_SET")
    if Grade.FAIL in grades:
        return Grade.FAIL
    return Grade.PASS if all(g is Grade.PASS for g in grades) else Grade.UNKNOWN


def tri_any(grades: Sequence[Grade]) -> Grade:
    if not grades:
        raise AssuranceError("EMPTY_CHECK_SET")
    if Grade.PASS in grades:
        return Grade.PASS
    return Grade.FAIL if all(g is Grade.FAIL for g in grades) else Grade.UNKNOWN


@dataclass(frozen=True, slots=True)
class Formula:
    operator: str
    criterion: str | None = None
    children: tuple[Formula, ...] = ()

    def __post_init__(self) -> None:
        if self.operator == "criterion":
            text(self.criterion)
            if self.children:
                raise AssuranceError("FORMULA_INVALID")
        elif self.operator in {"all", "any"}:
            if self.criterion is not None or not 1 <= len(self.children) <= 256:
                raise AssuranceError("FORMULA_INVALID")
        else:
            raise AssuranceError("FORMULA_INVALID")

    @classmethod
    def from_json(cls, value: object, criteria: frozenset[str]) -> Formula:
        canonical(value)
        count = 0

        def visit(node: object, depth: int) -> Formula:
            nonlocal count
            count += 1
            if depth > 16 or count > 512:
                raise AssuranceError("FORMULA_LIMIT")
            if not isinstance(node, dict) or len(node) != 1:
                raise AssuranceError("FORMULA_INVALID")
            operator = next(iter(node))
            if operator == "criterion":
                name = text(node[operator])
                if name not in criteria:
                    raise AssuranceError("FORMULA_UNKNOWN_CRITERION", name)
                return cls(operator, criterion=name)
            if operator not in {"all", "any"}:
                raise AssuranceError("FORMULA_INVALID")
            return cls(
                operator,
                children=tuple(
                    visit(child, depth + 1) for child in array(node[operator], minimum=1)
                ),
            )

        return visit(value, 0)

    def evaluate(self, grades: Mapping[str, Grade]) -> tuple[Grade, frozenset[str]]:
        if self.operator == "criterion":
            if self.criterion not in grades:
                raise AssuranceError("FORMULA_UNKNOWN_CRITERION")
            value = grades[self.criterion]
            return value, frozenset({self.criterion}) if value is Grade.PASS else frozenset()
        evaluated = [child.evaluate(grades) for child in self.children]
        if self.operator == "any":
            for value, witness in evaluated:
                if value is Grade.PASS:
                    return value, witness  # Frozen formula order, never model ranking.
            return tri_any([value for value, _ in evaluated]), frozenset()
        result = tri_all([value for value, _ in evaluated])
        return result, (
            frozenset().union(*(w for _, w in evaluated)) if result is Grade.PASS else frozenset()
        )


@dataclass(frozen=True, slots=True)
class CriterionPolicy:
    criterion_id: str
    mode: str
    any_check_sets: tuple[tuple[AssuranceRef, ...], ...]

    def __post_init__(self) -> None:
        text(self.criterion_id)
        if self.mode == "SEMANTIC":
            if self.any_check_sets:
                raise AssuranceError("SEMANTIC_HAS_CHECKS")
        elif self.mode == "CHECKED":
            if not 1 <= len(self.any_check_sets) <= 16:
                raise AssuranceError("CHECKED_GROUPS_REQUIRED")
            seen = set()
            for group in self.any_check_sets:
                if not 1 <= len(group) <= 32 or len(set(group)) != len(group):
                    raise AssuranceError("CHECKED_GROUP_INVALID")
                if any(ref.kind != "check_spec" for ref in group):
                    raise AssuranceError("CHECK_SPEC_REF_REQUIRED")
                identity = frozenset(group)
                if identity in seen:
                    raise AssuranceError("DUPLICATE_CHECK_GROUP")
                seen.add(identity)
        else:
            raise AssuranceError("CHECK_POLICY_UNRESOLVED")
        normalized = tuple(
            sorted(
                (tuple(sorted(group, key=lambda ref: ref.key)) for group in self.any_check_sets),
                key=lambda group: tuple(ref.key for ref in group),
            )
        )
        object.__setattr__(self, "any_check_sets", normalized)

    @classmethod
    def from_json(cls, value: object) -> CriterionPolicy:
        row = fields(value, {"criterion_id", "mode", "any_check_sets"})
        groups = array(row["any_check_sets"], maximum=16)
        return cls(
            row["criterion_id"],
            row["mode"],
            tuple(
                tuple(
                    AssuranceRef.from_json(ref, kinds={"check_spec"})
                    for ref in array(group, minimum=1, maximum=32)
                )
                for group in groups
            ),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "mode": self.mode,
            "any_check_sets": [[ref.to_json() for ref in group] for group in self.any_check_sets],
        }


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Normalized result AFTER the importer verifies producer, subject and current use.

    This value is not an authorization token; none of these pure functions writes
    an official review, acceptance or receipt.
    """

    spec: AssuranceRef
    receipt: AssuranceRef
    execution_state: str
    assertion_grade: Grade
    source_valid: bool

    def __post_init__(self) -> None:
        if self.spec.kind != "check_spec" or self.receipt.kind not in {
            "execution_receipt",
            "local_check_receipt",
        }:
            raise AssuranceError("CHECK_REF_INVALID")
        one_of(self.execution_state, {"SUCCEEDED", "ERROR", "CANCELLED", "NOT_RUN", "RUNNING"})
        if not isinstance(self.assertion_grade, Grade) or type(self.source_valid) is not bool:
            raise AssuranceError("CHECK_RESULT_TYPE")

    @property
    def effective(self) -> Grade:
        return (
            self.assertion_grade
            if self.source_valid and self.execution_state == "SUCCEEDED"
            else Grade.UNKNOWN
        )


@dataclass(frozen=True, slots=True)
class CheckGate:
    grade: Grade | None  # None = NOT_APPLICABLE; never fabricate a PASS receipt.
    consumed: tuple[AssuranceRef, ...]
    reason: str


def evaluate_check_gate(
    policy: CriterionPolicy, results: Mapping[AssuranceRef, CheckResult]
) -> CheckGate:
    if policy.mode == "SEMANTIC":
        return CheckGate(None, (), "CHECK_NOT_APPLICABLE")
    evaluated = []
    for group in policy.any_check_sets:
        rows = [results.get(spec) for spec in group]
        if any(row is not None and row.spec != spec for row, spec in zip(rows, group)):
            raise AssuranceError("CHECK_RESULT_IDENTITY")
        result = tri_all([row.effective if row else Grade.UNKNOWN for row in rows])
        consumed = tuple(sorted({row.receipt for row in rows if row}, key=lambda ref: ref.key))
        evaluated.append((result, consumed))
    for result, consumed in evaluated:
        if result is Grade.PASS:
            return CheckGate(result, consumed, "CHECK_GROUP_SATISFIED")
    result = tri_any([value for value, _ in evaluated])
    return CheckGate(
        result,
        tuple(sorted({ref for _, refs in evaluated for ref in refs}, key=lambda ref: ref.key)),
        "CHECK_GROUPS_FAILED" if result is Grade.FAIL else "CHECK_EVIDENCE_INCOMPLETE",
    )


@dataclass(frozen=True, slots=True)
class Assessment:
    criterion_id: str
    verdict: Grade
    evidence_ids: tuple[str, ...]
    reason: str
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Finding:
    criterion_id: str
    severity: str
    reason: str


@dataclass(frozen=True, slots=True)
class GlobalFinding:
    """A problem the reviewer found that belongs to no single criterion (原计划 F04 后半；2026-10-06 晚补).

    Whether it is a security problem, or a problem at all, is the reviewer's judgement; the
    Harness only keeps the order: a BLOCKER one fails the mandatory criteria, see
    :func:`global_blocker_targets`."""

    severity: str
    reason: str


@dataclass(frozen=True, slots=True)
class ClaimConfirmation:
    """The reviewer's word on one claim of the reviewed result (知识进库, 阶段 C)."""

    claim_id: str
    confirmed: bool
    evidence_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class MethodJudgement:
    """The reviewer's word on one method the Mission adopted (做法跨任务复用, 阶段 C3)."""

    method_ref: str
    reusable: bool
    purpose: str
    at_fault: bool
    reason: str


@dataclass(frozen=True, slots=True)
class SummaryCheck:
    """The reviewer's word on the reviewed result's summary (摘要层, 阶段 C3)."""

    faithful: bool
    reason: str


#: The reply's shape, level by level: the keys each object may carry.  One table, read
#: by the strict parser below and by :func:`decode_review_reply`'s tolerance.
_REPLY_KEYS = frozenset({"schema_version", "verdict", "assessments", "findings", "global_findings", "claims",
                         "methods", "summary"})
_ASSESSMENT_KEYS = frozenset({"criterion_id", "verdict", "evidence_ids", "reason", "limitations"})
_FINDING_KEYS = frozenset({"criterion_id", "severity", "reason"})
_GLOBAL_FINDING_KEYS = frozenset({"severity", "reason"})
#: The three severities, one table for criterion and global findings alike.
SEVERITIES = frozenset({"BLOCKER", "WARNING", "INFO"})
_CLAIM_KEYS = frozenset({"claim_id", "confirmed", "evidence_ids", "reason"})
_METHOD_KEYS = frozenset({"method_ref", "reusable", "purpose", "at_fault", "reason"})
_SUMMARY_KEYS = frozenset({"faithful", "reason"})
REVIEW_REPLY_SCHEMA_VERSION = 5
#: At most this many global findings in one reply.
MAX_GLOBAL_FINDINGS = 16


@dataclass(frozen=True, slots=True)
class ReviewReply:
    verdict: str
    assessments: tuple[Assessment, ...]
    findings: tuple[Finding, ...]
    #: Only the claims the reviewer wrote about; one it left out counts as unconfirmed.
    claims: tuple[ClaimConfirmation, ...] = ()
    #: Only the methods the reviewer wrote about; one it left out is neither reusable nor at fault.
    methods: tuple[MethodJudgement, ...] = ()
    #: None when the reviewer did not speak about the summary (it then counts as unchecked).
    summary: SummaryCheck | None = None
    #: Problems belonging to no single criterion; none written means none found.
    global_findings: tuple[GlobalFinding, ...] = ()

    @classmethod
    def from_json(cls, value: object) -> ReviewReply:
        canonical(value)
        row = fields(value, {"schema_version", "verdict", "assessments", "findings"},
                     {"global_findings", "claims", "methods", "summary"})
        if integer(row["schema_version"]) != REVIEW_REPLY_SCHEMA_VERSION:
            raise AssuranceError("REVIEW_SCHEMA_VERSION")
        verdict = one_of(row["verdict"], {"ACCEPT", "REWORK", "INCONCLUSIVE", "REJECTED"})
        assessments = []
        ids = set()
        for item in array(row["assessments"], minimum=1):
            a = fields(item, {"criterion_id", "verdict", "evidence_ids", "reason", "limitations"})
            name = text(a["criterion_id"])
            if name in ids:
                raise AssuranceError("DUPLICATE_CRITERION")
            ids.add(name)
            assessments.append(
                Assessment(
                    name,
                    grade(a["verdict"]),
                    unique_texts(a["evidence_ids"], maximum=64),
                    text(a["reason"], limit=2000),
                    unique_texts(a["limitations"], maximum=16),
                )
            )
        findings = []
        for item in array(row["findings"], maximum=128):
            f = fields(item, {"criterion_id", "severity", "reason"})
            name = text(f["criterion_id"])
            if name not in ids:
                raise AssuranceError("FINDING_SCOPE")
            findings.append(
                Finding(
                    name,
                    one_of(f["severity"], SEVERITIES),
                    text(f["reason"], limit=2000),
                )
            )
        global_findings = []
        for item in array(row.get("global_findings", []), maximum=MAX_GLOBAL_FINDINGS):
            g = fields(item, set(_GLOBAL_FINDING_KEYS))
            global_findings.append(GlobalFinding(one_of(g["severity"], SEVERITIES), text(g["reason"], limit=2000)))
        claims = []
        claim_ids = set()
        for item in array(row.get("claims", []), maximum=256):
            c = fields(item, set(_CLAIM_KEYS))
            name = text(c["claim_id"])
            if name in claim_ids:
                raise AssuranceError("DUPLICATE_CLAIM")
            claim_ids.add(name)
            if type(c["confirmed"]) is not bool:
                raise AssuranceError("ENUM_INVALID")
            claims.append(ClaimConfirmation(
                name, c["confirmed"], unique_texts(c["evidence_ids"], maximum=64), text(c["reason"], limit=1000)))
        methods = []
        method_refs = set()
        for item in array(row.get("methods", []), maximum=64):
            m = fields(item, set(_METHOD_KEYS))
            name = text(m["method_ref"])
            if name in method_refs:
                raise AssuranceError("DUPLICATE_METHOD")
            method_refs.add(name)
            if type(m["reusable"]) is not bool or type(m["at_fault"]) is not bool:
                raise AssuranceError("ENUM_INVALID")
            # a purpose is what a reusable method is listed under; without one it cannot be listed
            purpose = text(m["purpose"], limit=120) if m["reusable"] or m["purpose"] != "" else ""
            methods.append(MethodJudgement(name, m["reusable"], purpose, m["at_fault"],
                                           text(m["reason"], limit=1000)))
        summary = None
        if row.get("summary") is not None:
            s = fields(row["summary"], set(_SUMMARY_KEYS))
            if type(s["faithful"]) is not bool:
                raise AssuranceError("ENUM_INVALID")
            summary = SummaryCheck(s["faithful"], text(s["reason"], limit=1000))
        return cls(verdict, tuple(assessments), tuple(findings), tuple(claims), tuple(methods), summary,
                   tuple(global_findings))


_FENCE = re.compile(r"\A```(?:json)?[ \t]*\r?\n(.*)\r?\n```\Z", re.DOTALL)
_EMPTY = (None, "", [], {})


def _drop_empty_extras(value: object, allowed: frozenset[str]) -> object:
    if not isinstance(value, dict):
        return value
    return {key: item for key, item in value.items()
            if key in allowed or not any(item == empty and type(item) is type(empty) for empty in _EMPTY)}


def decode_review_reply(raw: str | bytes) -> ReviewReply:
    """The one decoder of a reviewer's reply (格式口径, 用户 2026-10-02 定).

    Tolerated, because they change nothing about what the reviewer said: the whole reply
    wrapped in one code fence; a key the shape does not have whose value is empty.
    Still refused, exactly as before: text before or after the JSON, an extra key with a
    value, an over-long reply.  The raw bytes stay the record; only the reading changes.
    """

    try:
        body = raw.decode("utf-8", "strict") if isinstance(raw, bytes) else str(raw)
    except UnicodeDecodeError:
        body = ""
    fenced = _FENCE.match(body.strip()) if len(body.encode("utf-8")) <= MAX_BYTES else None
    value = decode(fenced.group(1)) if fenced else decode(raw)
    if isinstance(value, dict):
        value = _drop_empty_extras(value, _REPLY_KEYS)
        for key, allowed in (("assessments", _ASSESSMENT_KEYS), ("findings", _FINDING_KEYS),
                             ("global_findings", _GLOBAL_FINDING_KEYS), ("claims", _CLAIM_KEYS),
                             ("methods", _METHOD_KEYS)):
            if isinstance(value.get(key), list):
                value[key] = [_drop_empty_extras(item, allowed) for item in value[key]]
        if isinstance(value.get("summary"), dict):
            value["summary"] = _drop_empty_extras(value["summary"], _SUMMARY_KEYS)
    return ReviewReply.from_json(value)


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    acceptable: bool
    effective_grades: Mapping[str, Grade]
    success_witness: frozenset[str]
    consumed_receipts: tuple[AssuranceRef, ...]
    reasons: tuple[str, ...]


def global_blocker_targets(
    reply: ReviewReply, mandatory: tuple[str, ...], catalogue: frozenset[str] | set[str]
) -> tuple[str, ...]:
    """The criteria a BLOCKER global finding fails: the mandatory ones, or every criterion when
    there are none (原计划 F04："全局安全 finding 必须映射 mandatory"；用户 2026-10-06 晚定).

    One rule of order, no reading of the words: a reply carrying one cannot be accepted, and no
    branch of the formula can route around it.  Empty when there is no BLOCKER global finding."""

    if not any(item.severity == "BLOCKER" for item in reply.global_findings):
        return ()
    return tuple(sorted(mandatory)) if mandatory else tuple(sorted(catalogue))


def decide_review(
    reply: ReviewReply,
    formula: Formula,
    mandatory: tuple[str, ...],
    policies: Mapping[str, CriterionPolicy],
    results: Mapping[AssuranceRef, CheckResult],
) -> ReviewDecision:
    if len(mandatory) != len(set(mandatory)) or not set(mandatory) <= policies.keys():
        raise AssuranceError("MANDATORY_CRITERIA_INVALID")
    gates = {}
    grades = {}
    reasons = []
    for assessment in reply.assessments:
        name = assessment.criterion_id
        policy = policies.get(name)
        if policy is None or policy.criterion_id != name:
            raise AssuranceError("CHECK_POLICY_UNRESOLVED")
        if name in grades:
            raise AssuranceError("DUPLICATE_CRITERION")
        gate = evaluate_check_gate(policy, results)
        gates[name] = gate
        grades[name] = (
            assessment.verdict if gate.grade is None else tri_all((assessment.verdict, gate.grade))
        )
        reasons.append(f"{name}:{gate.reason}")
    if grades.keys() != policies.keys():
        raise AssuranceError("POLICY_CATALOGUE_MISMATCH")
    for finding in reply.findings:
        if finding.criterion_id not in grades:
            raise AssuranceError("FINDING_SCOPE")
        if finding.severity == "BLOCKER":
            grades[finding.criterion_id] = Grade.FAIL
    for name in global_blocker_targets(reply, mandatory, set(grades)):
        grades[name] = Grade.FAIL
    result, witness = formula.evaluate(grades)
    if mandatory:
        result = tri_all((result, *(grades[name] for name in mandatory)))
        if result is Grade.PASS:
            witness = witness.union(mandatory)
    acceptable = reply.verdict == "ACCEPT" and result is Grade.PASS
    selected = witness if acceptable else grades.keys()
    consumed = tuple(
        sorted({ref for name in selected for ref in gates[name].consumed}, key=lambda ref: ref.key)
    )
    return ReviewDecision(
        acceptable,
        MappingProxyType(grades),
        witness if acceptable else frozenset(),
        consumed,
        tuple(reasons),
    )
