# SPDX-License-Identifier: Apache-2.0
"""做法候选视图，与非模型故障的原地重做（HTN 精简 片 A 第 3 项，2026-10-01）。

为目标选做法由规划器判断：这里只把每个待定目标的候选如实列出来。程序不再代答（"只有
一个候选就直接选""这组候选问过一次就回无变更"两条已删）。

唯一由系统自己发起的决定是基础设施重试：上一次失败不是模型自己做错的（格式、服务出错、
执行或审阅被打断），系统原样重做该步。它走与规划决定相同的准入与提交管道，但来源如实
记为 ``system_infrastructure_retry``，不是规划器的决定。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from simple_harness.contracts import canonical_json
from ..contracts.models import ContractError
from ..contracts.planning_decisions import PlanningDecisionEnvelopeV1
from .hierarchical_dispatch import append_hierarchical_event

SYSTEM_RETRY_ORIGIN = "system_infrastructure_retry"


def candidate_context(dispatch: Any, mission_id: str, reports: Any) -> list[dict[str, Any]]:
    """Per open goal, the candidates that can run and may be adopted now.  Read only.

    A method the plan-commit gate would refuse (this Mission's review of it is pending, awaits
    the person, or sent it back) is not offered as a candidate: it is listed under
    ``not_adoptable`` with its review state — the gate's own rule, one function (HTN 补齐 F1
    偏差单 2).  The reviewer's words stay in ``views.methods``."""
    from ..contracts.htn import MethodRef
    from .method_plan_reviews import adoption_refusal

    candidates = dispatch.method_candidates(mission_id, reports=reports)
    network = dispatch.network(mission_id)
    seeded = dispatch.seed_method_keys()
    rows = []
    for occurrence, result in sorted(candidates.items()):
        usable, held = [], []
        for item in result.applicable:
            refusal = adoption_refusal(dispatch.store, mission_id, MethodRef(
                method_id=item.method_id, version=int(item.method_version),
                content_hash=item.method_content_hash), seeded)
            if refusal is None:
                usable.append(item)
            else:
                held.append({"method_id": item.method_id, "method_version": int(item.method_version),
                             "review": refusal["review"]})
        rows.append({"occurrence_id": occurrence,
                     "applicable": [item.to_json() for item in usable[:12]],
                     "applicable_count": len(usable),
                     "omitted_count": max(0, len(usable) - 12),
                     "not_adoptable": held[:12],
                     "bindings": dict(network.binding_for_occurrence(occurrence).typed_parameters)})
    return rows


# 2026-09-28 用户决定：不是模型自己做错的失败（格式没写对、服务出错、执行或审阅被打断）
# 由系统原样重做该步，不交给规划器，也不扣任务次数（failure_classes）。同一步合计的上限
# 在创建尝试时把关（NonModelFailuresExhausted），服务一直坏着不会无限重做。
from .failure_classes import NON_MODEL, interrupted_review


def _lost_execution(request: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    return (request.get("trigger_source") == "RUNTIME_UNAVAILABLE"
            and context.get("reason") == "provider_outcome_unknown")


def _interrupted_review(request: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    """The step's only failure is its content review whose call was interrupted.

    The strict review rules cannot open a second review while the interrupted call is
    still unreconciled (a DeepSeek call never becomes reconcilable), so the step is
    redone instead — the user's intent "an interruption must not end the step".
    """
    if (request.get("trigger_source") != "VERIFIER_ACCEPTANCE_REJECT"
            or context.get("event_type") != "VerificationFailed"):
        return False
    return interrupted_review((context.get("detail") or {}).get("failures") or ())


def _not_models_fault(request: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    return (context.get("failure_class") in NON_MODEL
            or _lost_execution(request, context) or _interrupted_review(request, context))


def _runtime_lost_retry(package: Mapping[str, Any]) -> dict[str, Any] | None:
    views = package.get("views") or {}
    if any(row.get("source") == "planning" for row in views.get("failures", ())):
        return None
    # 审阅 2026-09-28：包里是整个任务的全部待处理请求，两步同时失败时不止一条——挑第一条
    # 非模型原因的原地处理，其余留给后续几轮。
    item = next((entry for entry in package.get("repair_requests") or ()
                 if _not_models_fault((entry.get("request") or {}),
                                      ((entry.get("request") or {}).get("context") or {}))
                 and not (entry.get("impact") or {}).get("unresolved_operations")), None)
    if item is None:
        return None
    request = item.get("request") or {}
    protocol = package.get("planning_protocol") or {}
    if "RETRY_SAME_METHOD" not in (protocol.get("enabled_repair_kinds") or ()):
        return None
    refs = [str(r) for r in (request.get("trigger_refs") or ()) if ":attempt-" in str(r)]
    if len(refs) != 1:
        return None
    failed = refs[0]
    task_id, marker, ordinal = failed.rpartition(":attempt-")
    if not marker or not ordinal.isdigit():
        return None
    subject = next((s for s in package.get("planning_subjects", ()) if s.get("task_id") == task_id), None)
    if subject is None:
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
    ref = [r for r in package.get("visible_refs", ())
           if r.get("kind") == "method_instance" and r.get("id") == instance_id]
    if len(ref) != 1:
        return None
    document: dict[str, Any] = {"schema_version": 1, "subject_key": subject["subject_key"],
        "decision_type": "REPAIR", "reason_refs": [ref[0]], "assumptions": [], "uncertainties": [],
        "alternatives": [], "replan_triggers": [],
        "rationale": ("上一次失败不是模型自己做错（格式、服务出错或执行/审阅被中断）；"
                      "系统按规则原样重做该步，不扣任务次数，原调用的用量照常记账。"),
        "payload": {"repair_kind": "RETRY_SAME_METHOD", "failed_attempt_id": failed,
                    "method_instance_ref": ref[0]}}
    return PlanningDecisionEnvelopeV1.from_json(document).to_json()


def infrastructure_retry(package: Mapping[str, Any]) -> dict[str, Any] | None:
    """The system's own in-place redo of a step whose failure was not the model's, or None."""
    return _runtime_lost_retry(package)


async def dispatch_local(handler: Any, intent: Any) -> bool:
    document = intent.config.get("native_planning_decision")
    if not isinstance(document, Mapping):
        return False
    mission = handler.store.get_mission(intent.mission_id)
    dispatch = None if mission is None else handler._new_mode(mission)
    if mission is None or dispatch is None or intent.kind != "plan":
        raise ContractError("system retry lost its original hierarchical Mission")
    text = "<planning_decision>" + canonical_json(dict(document)) + "</planning_decision>"
    append_hierarchical_event(handler.store, "NativePlanningDecisionPrepared", mission.id,
        key=intent.intent_id, payload={"intent_id": intent.intent_id,
            "origin": SYSTEM_RETRY_ORIGIN, "decision": dict(document)})
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
    from ..storage.planning_decision_store import PlanningDecisionStore
    from ..storage.planning_admission_store import PlanningAdmissionStore
    with store.read_view():
        decisions = PlanningDecisionStore(store)
        protocol = decisions.get_mission_protocol(intent.mission_id)
        if protocol is None:  # a flat-mode Mission has no planning protocol
            return False
        request = decisions.get_planning_request_for_intent(intent.intent_id)
        return request is not None and PlanningAdmissionStore(store).get_request_binding(request.request_id) is None
