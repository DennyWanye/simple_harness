# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Mission uncertainty derives from accepted Task evidence and the original catalog.

This is not a semantic judge or a source trust upgrade. Structural/execution checks
remain with their actual verifiers; a model candidate alone proves nothing.

片 D 第 2 项（2026-10-02）：走保证通道的任务（``assured=True``），文字要求"满足与否"以已认证
的最终审查结论为准（:func:`document_judgment`），这里对它们只做**秩序检查**：引用能解析、
资料是当前版本、声称状态可用、评估可用、"不确定"必须附说明。秩序检查没过记 ``FAIL``——只有
它能否决审阅员的结论；没有问题记 ``PASS``（有贡献）、``INCONCLUSIVE``（如实声明了不确定）
或 ``UNCLAIMED``（没有声称指向这一条）。"声称原文与要求字面相等才算通过"和"不确定占比
阈值"只剩不走保证通道的旧平面模式在用，随那条线一起处理。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..contracts import ClaimStatus, ContractError, Mission, TaskStatus, ids
from ..contracts.models import sha256_hex
from ..governance.domains import (
    DomainProfileV1,
    requires_mission_source_binding,
    supports_document_assessments,
)
from .assessments import (
    accepted_assessments_for,
    mission_contract_revision,
    mission_criterion_catalog,
    normalise_literal,
)

if TYPE_CHECKING:
    from ..storage.store import Store


def mission_coverage(
    store: Store, mission: Mission, domain: DomainProfileV1, *, artifact_store: Any = None,
    assured: bool = False,
) -> dict[str, Any]:
    if not supports_document_assessments(domain):
        raise ContractError("Mission coverage requires a frozen document assessment profile")
    current_sources = requires_mission_source_binding(domain)
    if current_sources:
        from ..artifacts.store import ArtifactStore
        from ..memory.source_dependencies import (
            source_dependencies_for,
            source_versions_current_issues,
        )

        artifact_store = artifact_store or ArtifactStore(store.path.parent / "artifacts")
    catalog = mission_criterion_catalog(mission)
    evaluations = []
    for task in store.list_tasks(mission.id):
        if task.status is TaskStatus.CANCELLED or (
            task.paused and task.status in {TaskStatus.READY, TaskStatus.BLOCKED}
        ):
            continue
        if task.status is not TaskStatus.COMPLETED:
            continue
        binding, assessments = accepted_assessments_for(store, task=task)
        grouped: dict[str, list[Any]] = defaultdict(list)
        for assessment in assessments:
            grouped[assessment.claim_id].append(assessment)
        for ordinal, proposal in enumerate(binding.envelope.claims, 1):
            cid = ids.claim_id(binding.result_id, ordinal)
            claim = store.get_claim(cid)
            rows = grouped.get(cid, [])
            issues: list[dict[str, Any]] = []
            if current_sources:
                versions, issues = source_dependencies_for(
                    store,
                    mission_id=mission.id,
                    evidence_refs=[ref for row in rows for ref in row.evidence_refs],
                    used_knowledge=binding.envelope.used_knowledge,
                )
                issues.extend(
                    source_versions_current_issues(store, mission.id, versions, artifact_store)
                )
                if any(issue["code"] == "ERROR" for issue in issues):
                    raise ContractError("Mission source provenance unavailable")
            evaluations.append((binding, ordinal, proposal, claim, rows, issues))
    verdicts = []
    for criterion in catalog:
        kind, text = criterion["kind"], criterion["text"]
        item: dict[str, Any] = {
            **dict(criterion),
            "verdict": "FAIL",
            "reasons": [],
            "claim_ids": [],
            "task_assessment_receipt_ids": [],
            "limitations": [],
        }
        if current_sources:
            item.update(excluded_claim_ids=[], source_provenance_issues=[])
        if kind in {"file", "action", "arbitration"}:
            item["verdict"] = "STRUCTURAL"
            item["reasons"] = ["requires_actual_structural_or_execution_check"]
            verdicts.append(item)
            continue
        passed, uncertain, failed = False, False, False
        # 秩序检查：sound = 有一条贡献过了全部秩序检查（不看它的措辞）。
        sound, order_reasons = False, set()
        unassessed: list[str] = []
        for binding, ordinal, proposal, claim, rows, issues in evaluations:
            candidate = criterion["criterion_id"] in proposal.mission_criterion_ids
            matches = (
                any(c.path == text.removeprefix("cite:") for c in proposal.citations)
                if kind == "cite"
                else normalise_literal(proposal.content) == normalise_literal(text)
            )
            if not candidate and not matches:
                continue
            if issues:
                # Exclude this contribution only: an independent live replacement may
                # still establish the criterion. Never rewrite the historical Claim.
                item["excluded_claim_ids"].append(ids.claim_id(binding.result_id, ordinal))
                item["source_provenance_issues"].extend(issues)
                continue
            if claim is None or claim.status not in {
                ClaimStatus.VERIFIED,
                ClaimStatus.SUPPORTED,
                ClaimStatus.UNDER_REVIEW,
            }:
                failed = True
                item["reasons"].append("claim_not_usable")
                order_reasons.add("claim_not_usable")
                continue
            if not rows:
                # 2026-10-01（真机 mission-4c97c315ed039e6f）：多步任务里执行者常把全部任务判据都声称
                # 一遍，而验证只对本步契约里的判据出评估。没有评估的声称不作数、也不否决——由有评估的
                # 贡献决定；一条评估都没有才算"缺有效评估"。
                unassessed.append(claim.id)
                continue
            if any(r.verdict not in {"PASS", "INCONCLUSIVE"} for r in rows):
                failed = True
                item["reasons"].append("task_assessment_not_usable")
                order_reasons.add("task_assessment_not_usable")
                continue
            refs = [ref for row in rows for ref in row.evidence_refs]
            if not refs or any(ref.get("status") != "resolved" for ref in refs):
                failed = True
                item["reasons"].append("citation_not_resolved")
                order_reasons.add("citation_not_resolved")
                continue
            inconclusive = [r for r in rows if r.verdict == "INCONCLUSIVE"]
            if inconclusive:
                if not candidate:
                    failed = True
                    item["reasons"].append("missing_mission_candidate_binding")
                    order_reasons.add("missing_mission_candidate_binding")
                    continue
                limitations = [
                    limitation
                    for limitation in binding.envelope.limitations
                    if limitation.claim_id == f"claim:{ordinal}"
                    and limitation.criterion_id in {r.criterion_id for r in inconclusive}
                ]
                if {limitation.criterion_id for limitation in limitations} != {
                    r.criterion_id for r in inconclusive
                }:
                    failed = True
                    item["reasons"].append("missing_task_limitations")
                    order_reasons.add("missing_task_limitations")
                    continue
                uncertain = sound = True
                item["reasons"].append("task_evidence_inconclusive")
                item["limitations"].extend(
                    {
                        "task_id": binding.task_id,
                        "result_id": binding.result_id,
                        "claim_id": claim.id,
                        "task_criterion_id": limitation.criterion_id,
                        "missing": limitation.missing,
                    }
                    for limitation in limitations
                )
            elif matches:
                passed = sound = True
            else:
                # 评估通过、引用都解析了，只是声称的原文与要求不字面相等：秩序上没有问题。
                sound = True
                failed = True
                item["reasons"].append("candidate_without_task_uncertainty_or_content_binding")
                if not (assured and kind not in PROGRAM_JUDGED_KINDS):
                    continue  # 旧平面模式：没有字面绑定的贡献不记入
            item["claim_ids"].append(claim.id)
            item["task_assessment_receipt_ids"].extend(r.receipt_id for r in rows)
        if unassessed and not passed and not uncertain:
            failed = True
            item["reasons"].append("missing_valid_task_assessment")
        item["verdict"] = (
            "FAIL" if failed else "INCONCLUSIVE" if uncertain else "PASS" if passed else "FAIL"
        )
        if not passed and not uncertain and not item["reasons"]:
            item["reasons"].append("no_accepted_content_binding")
        if current_sources and item["excluded_claim_ids"]:
            item["excluded_claim_ids"] = sorted(set(item["excluded_claim_ids"]))
            item["source_provenance_issues"] = list(
                {sha256_hex(i): i for i in item["source_provenance_issues"]}.values()
            )
            if not passed and not uncertain:
                item["reasons"].append("no_current_source_basis")
            if not sound:
                order_reasons.add("no_current_source_basis")
        item["reasons"] = sorted(set(item["reasons"]))
        item["claim_ids"] = sorted(set(item["claim_ids"]))
        item["task_assessment_receipt_ids"] = sorted(set(item["task_assessment_receipt_ids"]))
        item["limitations"] = sorted(item["limitations"], key=sha256_hex)
        if assured and kind not in PROGRAM_JUDGED_KINDS:
            # 保证通道：这一行只说秩序。措辞是否字面相等、有没有人声称，都不是失败。
            item["verdict"] = ("FAIL" if order_reasons else "INCONCLUSIVE" if uncertain
                               else "PASS" if sound else "UNCLAIMED")
            item["reasons"] = sorted(order_reasons) or (["task_evidence_inconclusive"] if uncertain else [])
        verdicts.append(item)
    numerator = sum(item["verdict"] == "INCONCLUSIVE" for item in verdicts)
    denominator = len(catalog)
    limit = domain.completion_rules.get("inconclusive_share_limit")
    if isinstance(limit, bool) or not isinstance(limit, (int, float)) or not 0 <= limit <= 1:
        raise ContractError("invalid frozen Mission inconclusive share limit")
    share = numerator / denominator if denominator else 0.0
    body = {
        "schema": 1,
        "mission_id": mission.id,
        "mission_contract_revision": mission_contract_revision(mission),
        "criteria": verdicts,
        "numerator": numerator,
        "denominator": denominator,
        "share": share,
        "limit": limit,
        # 保证通道上"不确定"占多少算证据不足是最终审查的判断，这个阈值不停机。
        "insufficient": not assured
        and share > limit
        and not (
            current_sources
            and any(
                item["verdict"] == "FAIL" and item.get("excluded_claim_ids") for item in verdicts
            )
        ),
    }
    return {**body, "hash": sha256_hex(body)}


#: 由程序确定性判定的要求种类（有没有引用到那份资料）；其余文字要求在保证通道上归最终审查。
PROGRAM_JUDGED_KINDS = frozenset({"cite"})


def _review_decides(assessed: Mapping[str, Any], *, assured: bool) -> bool:
    return assured and assessed.get("kind") not in PROGRAM_JUDGED_KINDS


def document_judgment(criterion: str, assessed: Mapping[str, Any], *, assured: bool,
                      grade: str | None) -> dict[str, Any]:
    """一条非结构性的任务要求最终算满足与否，以及是谁判的。

    ``assessed`` 是 :func:`mission_coverage`（同一个 ``assured``）给这条要求的那一行；``grade``
    是已认证的最终审查对它的结论（没有就是 None）。保证通道上的文字要求：秩序检查没过 →
    不满足；否则以审查结论为准。``cite:`` 要求和不走保证通道的任务照旧由覆盖结果决定。
    """
    evidence = {
        "criterion_id": assessed["criterion_id"],
        "limitations": list(assessed["limitations"]),
        "task_assessment_receipt_ids": list(assessed["task_assessment_receipt_ids"]),
    }
    if not _review_decides(assessed, assured=assured):
        return {"criterion": criterion, **evidence,
                "met": assessed["verdict"] in {"PASS", "INCONCLUSIVE"},
                "verdict": assessed["verdict"], "judge": "document_coverage",
                "reason": "; ".join(assessed["reasons"])}
    if assessed["verdict"] == "FAIL":
        return {"criterion": criterion, **evidence, "met": False, "verdict": "FAIL",
                "judge": "document_order_check", "reason": "; ".join(assessed["reasons"])}
    return {"criterion": criterion, **evidence, "met": grade == "PASS", "judge": "assurance_review",
            "source": "certified_root_resolution" if grade is not None else "unavailable",
            "reason": ("official MISSION_FINAL review, current root-resolution certificate: " + str(grade)
                       if grade is not None else "no certified root resolution judged this criterion")}


def coverage_objection(item: Mapping[str, Any], assessed: Mapping[str, Any], *,
                       assured: bool) -> str | None:
    """提交任务判定时，这一条与覆盖结果有没有冲突（没有就是 None）。

    保证通道上的文字要求只查秩序：秩序检查没过却说满足，拒收；说"秩序检查没过"而实际没有
    秩序问题，也拒收。其余照旧——判定必须与覆盖结果一致。
    """
    met = item.get("met")
    if type(met) is not bool:
        return "met_is_not_boolean"
    if not _review_decides(assessed, assured=assured):
        return None if met == (assessed["verdict"] in {"PASS", "INCONCLUSIVE"}) else "disagrees_with_coverage"
    if assessed["verdict"] == "FAIL":
        return "order_check_failed" if met else None
    if item.get("judge") == "document_order_check":
        return "order_check_not_failed"
    return None


def replace_document_judgment(item: dict[str, Any], judgment: Mapping[str, Any]) -> None:
    """就地把一行判定换成新的判法。三种判法带的字段不一样（``verdict`` 只有程序判的有，
    ``source`` 只有审查判的有），先清掉上一种判法留下的，再写新的；这一行上不属于判定本身
    的字段（被排除的声称、资料问题）不动。"""
    for key in ("verdict", "source"):
        item.pop(key, None)
    item.update(judgment)


def reconcile_document_judgments(judgments: list[dict[str, Any]], rows: Any, *, assured: bool,
                                 refresh_stale: bool) -> str | None:
    """提交前把任务判定逐条对一遍覆盖结果；第一处冲突的原因，没有就是 None。

    ``refresh_stale``（资料绑定的领域）：判定是早先留存的，等动作或批准的这段时间资料可能换了
    版本。原本成立、现在秩序上不成立的那一条，按现在的覆盖结果就地改成不满足后提交一次，
    而不是拒收整次提交；判定行自己带的要求编号对不上的不改（那不是留存的判定）。
    """
    for item, assessed in zip(judgments, rows, strict=True):
        if assessed["verdict"] == "STRUCTURAL":
            continue
        objection = coverage_objection(item, assessed, assured=assured)
        if (
            objection is not None
            and refresh_stale
            and item.get("met") is True
            and item.get("judge") in {"document_coverage", "assurance_review"}
            and item.get("criterion_id") == assessed["criterion_id"]
        ):
            fresh = document_judgment(str(item["criterion"]), assessed, assured=assured, grade=None)
            if _review_decides(assessed, assured=assured):
                replace_document_judgment(item, fresh)
            else:  # 旧平面口径：照旧只改这三样
                item.update({key: fresh[key] for key in ("met", "verdict", "reason")})
            objection = coverage_objection(item, assessed, assured=assured)
        if objection is not None:
            return objection
    return None


__all__ = ("PROGRAM_JUDGED_KINDS", "coverage_objection", "document_judgment", "mission_coverage",
           "reconcile_document_judgments", "replace_document_judgment")
