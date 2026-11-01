# SPDX-License-Identifier: Apache-2.0
"""做法候选视图，与非模型故障的原地重做（HTN 精简 片 A 第 3 项，2026-10-01）。

为目标选做法由规划器判断：这里只把每个待定目标的候选如实列出来。程序不再代答（"只有
一个候选就直接选""这组候选问过一次就回无变更"两条已删）。

不经规划器、由系统直接提交的决定只有两种，都是"原样重做这一步"，都走与规划决定相同的准入
与提交管道，来源如实记录、不算规划器的一轮：

- 基础设施重试（``system_infrastructure_retry``）：上一次失败不是模型自己做错的（格式、服务
  出错、执行或审阅被打断）。规划器此前有回复被拒时让位给规划器。
- 人工接管重试（``human_takeover_retry``）：用户对失败收场的一步点了"带话重试"。这是人的
  决定，不让位；但真实外部操作未决仍先核对。每笔接管只提交一次（账本里的
  ``NativePlanningDecisionPrepared`` 记着接管号），被准入拒了就交回规划器，不每轮反复。
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any, NamedTuple

from simple_harness.contracts import canonical_json

from ..contracts.htn import MethodRef
from ..contracts.models import ContractError
from ..contracts.planning_decisions import PlanningDecisionEnvelopeV1
from ..storage import planning_decision_store
from ..storage.planning_admission_store import PlanningAdmissionStore
from .hierarchical_dispatch import append_hierarchical_event
from .method_plan_reviews import adoption_refusal
from .planning_repair_requests import request_concerns, subject_targets
from .stop_conditions import HUMAN_RETRY_ORIGIN, NATIVE_DECISION_ORIGINS, SYSTEM_RETRY_ORIGIN


class NativeDecision(NamedTuple):
    """系统直接提交进决定管道的一份决定及其来源；人工接管重做还带那笔接管的记录号。"""

    document: dict[str, Any]
    origin: str
    override_id: str | None = None


def candidate_context(dispatch: Any, mission_id: str, reports: Any) -> list[dict[str, Any]]:
    """Per open goal, the candidates that can run and may be adopted now.  Read only.

    A method the plan-commit gate would refuse (this Mission's review of it is pending, awaits
    the person, or sent it back) is not offered as a candidate: it is listed under
    ``not_adoptable`` with its review state — the gate's own rule, one function (HTN 补齐 F1
    偏差单 2).  The reviewer's words stay in ``views.methods``."""

    candidates = dispatch.method_candidates(mission_id, reports=reports)
    network = dispatch.network(mission_id)
    seeded = dispatch.seed_method_keys()
    rows = []
    for occurrence, result in sorted(candidates.items()):
        usable, held = [], []
        for item in result.applicable:
            refusal = adoption_refusal(
                dispatch.store,
                mission_id,
                MethodRef(
                    method_id=item.method_id,
                    version=int(item.method_version),
                    content_hash=item.method_content_hash,
                ),
                seeded,
            )
            if refusal is None:
                usable.append(item)
            else:
                held.append(
                    {
                        "method_id": item.method_id,
                        "method_version": int(item.method_version),
                        "review": refusal["review"],
                    }
                )
        rows.append(
            {
                "occurrence_id": occurrence,
                "applicable": [item.to_json() for item in usable[:12]],
                "applicable_count": len(usable),
                "omitted_count": max(0, len(usable) - 12),
                "not_adoptable": held[:12],
                "bindings": dict(network.binding_for_occurrence(occurrence).typed_parameters),
            }
        )
    return rows


# 2026-09-28 用户决定：不是模型自己做错的失败（格式没写对、服务出错、执行或审阅被打断）
# 由系统原样重做该步，不交给规划器，也不扣任务次数（failure_classes）。同一步合计的上限
# 在创建尝试时把关（NonModelFailuresExhausted），服务一直坏着不会无限重做。
from .failure_classes import NON_MODEL, interrupted_review


def _lost_execution(request: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    return (
        request.get("trigger_source") == "RUNTIME_UNAVAILABLE"
        and context.get("reason") == "provider_outcome_unknown"
    )


def _interrupted_review(request: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    """The step's only failure is its content review whose call was interrupted.

    The strict review rules cannot open a second review while the interrupted call is
    still unreconciled (a DeepSeek call never becomes reconcilable), so the step is
    redone instead — the user's intent "an interruption must not end the step".
    """
    if (
        request.get("trigger_source") != "VERIFIER_ACCEPTANCE_REJECT"
        or context.get("event_type") != "VerificationFailed"
    ):
        return False
    return interrupted_review((context.get("detail") or {}).get("failures") or ())


def _not_models_fault(request: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    return (
        context.get("failure_class") in NON_MODEL
        or _lost_execution(request, context)
        or _interrupted_review(request, context)
    )


def _attempt_refs(request: Mapping[str, Any]) -> list[str]:
    return [str(r) for r in (request.get("trigger_refs") or ()) if ":attempt-" in str(r)]


def _retry_document(
    package: Mapping[str, Any], item: Mapping[str, Any], *, rationale: str
) -> dict[str, Any] | None:
    """把一条失败请求编成"原样重做这一步"的决定；拿不准的身份一律不编（交给规划器）。"""
    views = package.get("views") or {}
    request = item.get("request") or {}
    protocol = package.get("planning_protocol") or {}
    if "RETRY_SAME_METHOD" not in (protocol.get("enabled_repair_kinds") or ()):
        return None
    refs = _attempt_refs(request)
    if len(refs) != 1:
        return None
    failed = refs[0]
    task_id, marker, ordinal = failed.rpartition(":attempt-")
    if not marker or not ordinal.isdigit():
        return None
    subject = next(
        (s for s in package.get("planning_subjects", ()) if s.get("task_id") == task_id), None
    )
    if subject is None:
        return None
    # 同一步还有要规划器判断的请求（例如改了要求）：原样重做会把它一并算作已处理，规划器就再也
    # 看不到它。系统不代答，整包交给规划器。
    targets = subject_targets(subject)
    if any(
        entry is not item
        and not _not_models_fault(
            (entry.get("request") or {}), ((entry.get("request") or {}).get("context") or {})
        )
        and request_concerns(entry, targets)
        for entry in package.get("repair_requests") or ()
    ):
        return None
    occurrence = subject["occurrence_id"]
    instance_ids = {
        str(child.get("instance_id"))
        for plan in views.get("plans", ())
        for instance in plan.get("adopted_methods", ())
        for child in instance.get("child_bindings", ())
        if occurrence in (child.get("occurrence_id"), child.get("goal_occurrence_id"))
    }
    if len(instance_ids) != 1:
        return None
    instance_id = next(iter(instance_ids))
    ref = [
        r
        for r in package.get("visible_refs", ())
        if r.get("kind") == "method_instance" and r.get("id") == instance_id
    ]
    if len(ref) != 1:
        return None
    document: dict[str, Any] = {
        "schema_version": 1,
        "subject_key": subject["subject_key"],
        "decision_type": "REPAIR",
        "reason_refs": [ref[0]],
        "assumptions": [],
        "uncertainties": [],
        "alternatives": [],
        "replan_triggers": [],
        "rationale": rationale,
        "payload": {
            "repair_kind": "RETRY_SAME_METHOD",
            "failed_attempt_id": failed,
            "method_instance_ref": ref[0],
        },
    }
    return PlanningDecisionEnvelopeV1.from_json(document).to_json()


def _runtime_lost_retry(package: Mapping[str, Any]) -> dict[str, Any] | None:
    views = package.get("views") or {}
    if any(row.get("source") == "planning" for row in views.get("failures", ())):
        return None
    # 审阅 2026-09-28：包里是整个任务的全部待处理请求，两步同时失败时不止一条——挑第一条
    # 非模型原因的原地处理，其余留给后续几轮。
    item = next(
        (
            entry
            for entry in package.get("repair_requests") or ()
            if _not_models_fault(
                (entry.get("request") or {}), ((entry.get("request") or {}).get("context") or {})
            )
            and not (entry.get("impact") or {}).get("unresolved_operations")
        ),
        None,
    )
    if item is None:
        return None
    return _retry_document(
        package,
        item,
        rationale=(
            "上一次失败不是模型自己做错（格式、服务出错或执行/审阅被中断）；"
            "系统按规则原样重做该步，不扣任务次数，原调用的用量照常记账。"
        ),
    )


def _when(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _human_takeover_retry(
    package: Mapping[str, Any], submitted_takeovers: Collection[str]
) -> tuple[dict[str, Any], str] | None:
    """用户对失败收场的一步点了"带话重试"：系统按人的决定原样重做。

    只认"接管晚于这次失败的尝试"的那笔接管（更早的接管说的是上一次）。每笔接管只提交一次：
    ``submitted_takeovers`` 是账本里已经为之提交过原地重做的接管号，提交过的（不论准入
    批了还是拒了）不再提交，拒了就交回规划器——不从包里的失败索引推断，那一节会被裁剪。
    真实外部操作未决（impact.unresolved_operations）仍先核对——人说重做也不能跳过这一条。
    """
    views = package.get("views") or {}
    # 人工接管记录是包的一节（与 repair_requests 同级），不在 views 里
    takeovers = [
        row
        for row in package.get("human_takeovers") or ()
        if row.get("action") == "takeover_retry_with_note"
        and str(row.get("override_id") or "") not in submitted_takeovers
    ]
    if not takeovers:
        return None
    failures = tuple(views.get("failures", ()))
    for item in package.get("repair_requests") or ():
        request = item.get("request") or {}
        if (item.get("impact") or {}).get("unresolved_operations"):
            continue
        refs = _attempt_refs(request)
        if len(refs) != 1:
            continue
        failed = refs[0]
        task_id = failed.rpartition(":attempt-")[0]
        attempt_row = next(
            (
                row
                for row in failures
                if row.get("source") == "attempt"
                and (row.get("attempt_review_ref") or {}).get("id") == failed
            ),
            None,
        )
        if attempt_row is None:
            continue
        failed_at = _when(attempt_row.get("last_seen"))
        latest = max(
            (
                row
                for row in takeovers
                if row.get("task_id") == task_id and _when(row.get("at")) > failed_at
            ),
            key=lambda row: _when(row.get("at")),
            default=None,
        )
        if latest is None:
            continue
        basis = str(latest.get("basis") or "").strip()
        document = _retry_document(
            package,
            item,
            rationale=(
                "用户对这一步点了带话重试"
                + (f"（理由：{basis[:200]}）" if basis else "")
                + "；系统按人的决定原样重做该步，经同一套准入核对，不扣任务次数，"
                "原调用的用量按上限保留。"
            ),
        )
        if document is not None:
            return document, str(latest.get("override_id") or "")
    return None


def infrastructure_retry(package: Mapping[str, Any]) -> dict[str, Any] | None:
    """The system's own in-place redo of a step whose failure was not the model's, or None."""
    return _runtime_lost_retry(package)


def native_decision(
    package: Mapping[str, Any], *, submitted_takeovers: Collection[str] = ()
) -> NativeDecision | None:
    """开一轮规划前，系统能不能不问规划器就把这一步原样重做：先看人的接管，再看非模型故障。

    ``submitted_takeovers``：账本里已经提交过原地重做的接管号（``NativePlanningDecisionPrepared``
    的 ``override_id``），同一笔接管不提交第二次。
    """
    human = _human_takeover_retry(package, frozenset(str(x) for x in submitted_takeovers))
    if human is not None:
        document, override_id = human
        return NativeDecision(document, HUMAN_RETRY_ORIGIN, override_id or None)
    system = _runtime_lost_retry(package)
    if system is not None:
        return NativeDecision(system, SYSTEM_RETRY_ORIGIN)
    return None


async def dispatch_local(handler: Any, intent: Any) -> bool:
    document = intent.config.get("native_planning_decision")
    if not isinstance(document, Mapping):
        return False
    origin = intent.config.get("native_planning_decision_origin")
    if origin not in NATIVE_DECISION_ORIGINS:
        raise ContractError("a native planning decision must record its origin")
    mission = handler.store.get_mission(intent.mission_id)
    dispatch = None if mission is None else handler._new_mode(mission)
    if mission is None or dispatch is None or intent.kind != "plan":
        raise ContractError("system retry lost its original hierarchical Mission")
    text = "<planning_decision>" + canonical_json(dict(document)) + "</planning_decision>"
    append_hierarchical_event(
        handler.store,
        "NativePlanningDecisionPrepared",
        mission.id,
        key=intent.intent_id,
        payload={
            "intent_id": intent.intent_id,
            "origin": str(origin),
            # 人工接管重做记下那笔接管号：同一笔接管只提交一次（选择器据此跳过）
            "override_id": intent.config.get("native_planning_decision_takeover"),
            "decision": dict(document),
        },
    )
    # No Agent/turn/provider receipt is fabricated. This method already accepts a
    # locally resumed decision and reads the original request's durable authority.
    await handler._collect_plan_decision(intent, None, mission, text, dispatch)
    return True


def awaits_authority(store: Any, intent: Any) -> bool:
    """A new request waits for a real issuer binding before any dispatch.

    Expired/revoked/stale bindings go through original admission and its normal
    refusal path; they cannot turn into an indefinite wait for an immutable row
    to be rewritten. Only absence is an external input still to be supplied.
    """
    if intent.kind != "plan":
        return False

    with store.read_view():
        decisions = planning_decision_store.PlanningDecisionStore(store)
        protocol = decisions.get_mission_protocol(intent.mission_id)
        if protocol is None:  # a flat-mode Mission has no planning protocol
            return False
        request = decisions.get_planning_request_for_intent(intent.intent_id)
        return (
            request is not None
            and PlanningAdmissionStore(store).get_request_binding(request.request_id) is None
        )


__all__ = (
    "HUMAN_RETRY_ORIGIN",
    "NATIVE_DECISION_ORIGINS",
    "SYSTEM_RETRY_ORIGIN",
    "NativeDecision",
    "awaits_authority",
    "candidate_context",
    "dispatch_local",
    "infrastructure_retry",
    "native_decision",
)
