# SPDX-License-Identifier: Apache-2.0
"""System-prepared operation intents: the ``AUTHORIZED_SLOT`` source.

2026-09-29（plans/2026-09-28-system-operations）：发布这类参数早已被用户在确认页定死的操作，
由系统按已批准效果准备申请单，模型只做内容。真机第四、五局里模型写申请单反复出错，把整个
任务 12 次尝试耗光。

- 要做什么：只从已批准效果覆盖的那条 ``action:`` 要求里读（一件事只记一处）。
- 发布哪个文件：当前已接受的内容产出里路径等于目标（其次文件名相同）的唯一产物。申请单里
  的候选引用仍是那份审过的真实文件本身，只有申请单 JSON 由系统生成，所以原有的来源、参数
  绑定、审阅与核对检查全部照旧成立，发布的一定是审过的字节。
- 谁提交：确认页批准该规格的那个人（提交入口只接受人；规格含效果时不允许系统代为确认）。
- 只准备需要人批准的操作（发布是 L2），人仍在批准卡片上逐个批准。
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from ..contracts.htn import TaskForm
from ..contracts.models import ContractError
from ..contracts.operation_intents import (
    CompletionSlotV2,
    OperationIntentSourceKind,
    OperationIntentSourceV2,
    SubmitOperationIntentV2,
)
from ..contracts.semantic_base import TypedRef, TypedRefKind, content_hash_of
from ..governance.permissions import Principal
from ..planning.htn.grounding import derive_id
from ..storage.htn_store import HtnStore
from ..storage.operation_completion_store import OperationCompletionStore
from ..storage.operation_intent_store import OperationIntentStore
from .action_commits import parse_action_criterion

SYSTEM_REASON = "用户在确认页批准的操作：发布 {source} 到 {target}"


def effect_operation(effect: Any, requirements: Any) -> tuple[str, str, str]:
    """The one ``action:<connector>.<operation>:<target>`` an approved effect covers."""

    statements = {item.criterion_id: item.statement for item in requirements.criteria}
    found = [parsed for criterion in effect.criterion_ids
             if (parsed := parse_action_criterion(statements.get(criterion, ""))) is not None]
    if len(found) != 1:
        raise ContractError(f"effect {effect.effect_key!r} covers {len(found)} operations, not one")
    return found[0]


def source_matches(path: str, target: str) -> bool:
    return path == target or path.rsplit("/", 1)[-1] == target.rsplit("/", 1)[-1]


def system_candidate_bytes(operation: tuple[str, str, str], source_path: str) -> bytes:
    connector, name, target = operation
    candidate = {"connector": connector, "operation": name, "target": target,
                 "params": {"artifact_path": source_path},
                 "reason": SYSTEM_REASON.format(source=source_path, target=target)}
    return json.dumps(candidate, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def authorized_slot_operation(
    store: Any, command: SubmitOperationIntentV2, principal: Principal, effect: Any,
    requirements: Any,
) -> tuple[str, str, str]:
    """Check the slot authority and return the operation it names (read-only)."""

    source = command.intent_source
    receipt = store.get_receipt(str(source.origin_receipt_id))
    authority = (receipt or {}).get("authority") or {}
    if (receipt is None or receipt.get("kind") != "operation_completion_spec_approved"
            or receipt.get("mission_id") != command.mission_id
            or receipt.get("spec_hash") != command.completion_slot.spec_hash
            or source.slot_key != command.completion_slot.effect_key
            or authority.get("issuer_id") != principal.principal_id):
        raise ContractError("AUTHORIZED_SLOT authority differs from the approved Spec")
    return effect_operation(effect, requirements)


#: 审阅没做成或判断不了时，系统替代重交的次数上限；规划器为同一发布补步骤的次数上限。
SYSTEM_RESUBMIT_CAP = 2
SOURCE_REPAIR_CAP = 2
_RETRY_VERDICTS = frozenset({"REVIEW_FAILED", "INCONCLUSIVE"})
_REFUSED_VERDICTS = frozenset({"REWORK", "REJECTED"})


def _leaves(store: Any, htn: HtnStore, mission_id: str) -> list[Any] | None:
    """Tasks of the active plan's primitive leaves (each with ``upstream`` task ids from the
    plan's own ORDER/DATA edges), or None while any leaf has no accepted result.

    审阅 2026-09-29：分层任务的 ``Task.dependency_ids`` 故意留空，上下游只能从计划网络读。
    """

    active = htn.active_plan_revision(mission_id)
    if active is None:
        return None
    members = htn.list_plan_memberships(mission_id, active.revision)
    task_of = {str(member.occurrence_id): str(member.task_id) for member in members}
    before: dict[str, set[str]] = {}
    for order in htn.list_order_constraints(mission_id, active.revision):
        before.setdefault(str(order.after), set()).add(str(order.before))
    for data in htn.list_data_requirements(mission_id, active.revision):
        before.setdefault(str(data.consumer_occurrence), set()).add(str(data.producer_occurrence))

    def upstream(occurrence: str) -> frozenset[str]:
        seen: set[str] = set()
        stack = list(before.get(occurrence, ()))
        while stack:
            item = stack.pop()
            if item not in seen:
                seen.add(item)
                stack.extend(before.get(item, ()))
        return frozenset(task_of[item] for item in seen if item in task_of)

    tasks = []
    for member in members:
        if member.form is not TaskForm.PRIMITIVE:
            continue
        task = store.get_task(str(member.task_id))
        if task is None or not task.accepted_result_id:
            return None
        tasks.append(SimpleNamespace(task=task, upstream=upstream(str(member.occurrence_id))))
    return tasks or None


def _sources(store: Any, htn: HtnStore, mission_id: str, leaves: list[Any],
             target: str) -> list[tuple[Any, Any, Any]]:
    """(artifact, acceptance, task) — only the current plan's leaves, only the result each
    leaf finally had accepted (审阅 2026-09-29：旧步骤、旧版本的接受记录一直算"当前"，
    续写同一文件的正常计划会被判成多个匹配而卡住)。"""

    # 2026-09-29 第 5 批：计划里声明写出这个文件的步骤（任务行 outputs 含它）恰好一个时，
    # 只在它和它下游的步骤里、按路径完全相同找（下游续写步骤可以改它，取最下游的一版）；
    # 没有声明或声明不唯一时照旧按路径/文件名在全部步骤里找。
    declared = [leaf for leaf in leaves if target in tuple(leaf.task.outputs)]
    exact_only = len(declared) == 1
    if exact_only:
        producer = str(declared[0].task.id)
        leaves = [leaf for leaf in leaves
                  if str(leaf.task.id) == producer or producer in leaf.upstream]
    current = {}
    for acceptance in htn.list_acceptances(mission_id):
        if str(acceptance.validity) == "CURRENT":
            for ref in acceptance.artifact_refs:
                current.setdefault((ref.id, ref.content_hash), acceptance)
    exact, by_name = [], []
    for leaf in leaves:
        task = leaf.task
        result = store.get_result(task.accepted_result_id)
        for artifact_id in () if result is None else result.artifacts:
            artifact = store.get_artifact(artifact_id)
            if (artifact is None or artifact.path.startswith("actions/")
                    or artifact.verification_status != "VERIFIED"):
                continue
            acceptance = current.get((artifact.id, artifact.content_hash))
            if acceptance is None:
                continue
            if artifact.path == target:
                exact.append((artifact, acceptance, leaf))
            elif not exact_only and source_matches(artifact.path, target):
                by_name.append((artifact, acceptance, leaf))
    found = exact or by_name
    if len(found) > 1:
        # 续写链上多个版本：取最下游的那一版（其余版本都在它的上游）。
        downstream = [item for item in found
                      if {str(other[2].task.id) for other in found if other is not item} <= item[2].upstream]
        if len(downstream) == 1:
            return downstream
    return found


def _stop(orch: Any, mission_id: str, detail: dict[str, Any]) -> bool:
    from ..contracts import MissionStopReason

    orch._commit_fail_mission(mission_id, stop_reason=MissionStopReason.MISSION_CRITERIA_UNMET,
                              detail={"reason": "system_operation_blocked", **detail})
    orch._note(f"mission {mission_id} stopped: system operation blocked ({detail})")
    return True


def _ask_planner_once(orch: Any, mission: Any, *, key: str, event_type: str, detail: dict[str, Any],
                      stop: Any) -> bool:
    """问规划器一次（同一个 ``key`` 只问一次）；它提交过决定而事情仍没解决，就调 ``stop`` 停。

    "回应过"只认规划器真正提交的决定（格式错被退回不算，审阅 2026-09-29）；它提交的决定是
    去问用户、问题还没答时不算回应完——等人答。"""

    from ..storage.planning_human_store import PlanningHumanStore
    from .planning_repair_requests import REQUESTED, record_request

    store = orch.store
    events = tuple(store.iter_events(mission.id))
    asked = next((e for e in events if e.type == REQUESTED and e.payload.get("source_key") == key), None)
    if asked is not None:
        answered = any(e.type == "PlanningDecisionEvaluated" and e.seq > asked.seq
                       and e.payload.get("status") == "COMMITTED" for e in events)
        if answered and not PlanningHumanStore(store).pending(mission.id):
            return stop("；规划器已回应但仍未解决")
        return False
    dispatch = orch._new_mode(mission)
    if dispatch is None:
        return stop("")
    root = next(iter(dispatch.network(mission.id).root_occurrence_ids), None)
    root_task = None if root is None else dispatch.network(mission.id).binding_for_occurrence(root).task_id
    return record_request(dispatch, mission.id, event_type=event_type,
                          trigger_refs=tuple(str(x) for x in (root_task,) if x) or (mission.id,),
                          source_key=key, detail=detail)


def _ask_planner_for_source(orch: Any, mission: Any, htn: HtnStore, effect_key: str,
                            target: str, matches: list[str]) -> bool:
    """找不到要发布的文件（或判断不了用哪一版）：请规划器补/改步骤；它回应过仍不行就停。"""

    from .planning_repair_requests import REQUESTED

    active = htn.active_plan_revision(mission.id)
    prefix = f"system-operation-source:{effect_key}:"
    reason = (f"已批准的发布 {target} 找不到" + ("唯一的" if matches else "") + "来源文件："
              + ("、".join(matches) + " 都匹配，需指定其中一个步骤的产出" if matches
                 else f"当前计划里没有任何步骤产出 {target}，需要补一个写出它的步骤"))

    def stop(why: str) -> bool:
        return _stop(orch, mission.id, {"effect_key": effect_key, "target": target, "explanation": reason + why})

    key = prefix + str(active.revision)
    mine = [e for e in orch.store.iter_events(mission.id)
            if e.type == REQUESTED and str(e.payload.get("source_key", "")).startswith(prefix)]
    if all(e.payload.get("source_key") != key for e in mine) and len(mine) >= SOURCE_REPAIR_CAP:
        return stop(f"；已请规划器补过 {len(mine)} 次")
    return _ask_planner_once(orch, mission, key=key, event_type="VerifierAcceptanceRejected", stop=stop,
                             detail={"reason": "system_operation_source_unresolved", "effect_key": effect_key,
                                     "target": target, "matches": matches, "explanation": reason})


def _ask_planner_after_rejection(orch: Any, mission: Any, rejected: dict[str, Any]) -> bool:
    """阶段 B 裁决第 2 类：人拒绝了发布卡——把拒绝理由原文交给规划器（改内容、问人或不改由它判断）。

    上限（秩序，不是判断）：每次拒绝只问一次；规划器回应了而内容没换出新版本就停；同一效果被人
    拒绝累计 ``SOURCE_REPAIR_CAP`` 次直接停。被拒过的同一份内容系统不会再自动重交。"""

    from ..contracts.state_machines import MissionStopReason

    count = len(rejected["rejections"])
    latest = rejected["rejections"][-1]

    def stop(why: str) -> bool:
        orch._commit_fail_mission(mission.id, stop_reason=MissionStopReason.APPROVAL_REJECTED, detail={
            "reason": "operation_rejected", "effect_key": rejected["effect_key"], "target": rejected["target"],
            "rejections": rejected["rejections"], "explanation": f"发布 {rejected['target']} 被人拒绝" + why})
        return True

    if count >= SOURCE_REPAIR_CAP:
        return stop(f"；已被拒绝 {count} 次")
    return _ask_planner_once(
        orch, mission, key=f"operation-rejected:{rejected['effect_key']}:{latest['request_id']}",
        event_type="OperationNotApplied", stop=lambda why: stop(why.replace("仍未解决", "内容未变")),
        detail={"reason": "operation_rejected_by_person", "effect_key": rejected["effect_key"],
                "target": rejected["target"], "rejection_reason": latest["reason"],
                "rejected_by": latest["rejected_by"], "rejections": count,
                "remaining": SOURCE_REPAIR_CAP - count})


def _needs_approval(orch: Any, operation: tuple[str, str, str]) -> bool:
    from ..governance.policies import DeploymentPolicy, action_decision

    connectors = getattr(orch, "_connectors", None) or {}
    config = getattr(orch, "_config", None)
    deployment = getattr(config, "deployment_policy", None) or DeploymentPolicy()
    decision = action_decision(deployment, connectors.get(operation[0]), operation[1])
    return decision.refused is None and decision.required_approvals >= 1


def pending_system_operations(orch: Any, mission_id: str) -> list[dict[str, Any]]:
    """What the system would do now for each approved effect (read-only).

    Each entry has ``command`` (submit it), ``stop`` (stop the Mission, with why),
    ``ask`` (ask the planner for a source step) or ``skip`` (nothing to do yet).
    """

    store = orch.store
    mission = store.get_mission(mission_id)
    if mission is None or str(mission.status) != "ACTIVE":
        return []
    htn = HtnStore(store)
    requirements = htn.latest_requirements_revision(mission_id)
    if requirements is None:
        return []
    row = OperationCompletionStore(store).get_spec_exact(
        mission_id, requirements.revision, requirements.content_hash())
    spec = None if row is None else row.get("document")
    if spec is None or not spec.effects:
        return []
    leaves = _leaves(store, htn, mission_id)
    if leaves is None:
        return []  # 内容还没全部通过
    receipt = store.get_receipt(str(row["approval_receipt_id"]))
    authority = (receipt or {}).get("authority") or {}
    if not authority.get("issuer_id") or not authority.get("tenant_id"):
        return []
    principal = Principal(authority["issuer_id"])
    intents = OperationIntentStore(store).for_mission(mission_id)
    superseded = {item["supersedes_intent_id"] for item in intents if item["supersedes_intent_id"]}
    spec_hash = spec.content_hash()
    plans: list[dict[str, Any]] = []
    for effect in spec.effects:
        try:
            operation = effect_operation(effect, requirements)
        except ContractError as error:
            plans.append({"effect_key": effect.effect_key, "skip": str(error)})
            continue
        if not _needs_approval(orch, operation):
            # 只代办需要人批准的操作；不需要批准的会被自动执行，不由系统代为提交。
            plans.append({"effect_key": effect.effect_key, "skip": "operation_needs_no_approval"})
            continue
        found = _sources(store, htn, mission_id, leaves, operation[2])
        if len(found) != 1:
            plans.append({"effect_key": effect.effect_key, "target": operation[2], "ask": True,
                          "matches": sorted(artifact.path for artifact, _, _ in found)})
            continue
        artifact, acceptance, _ = found[0]
        mine = []
        for item in intents:
            completion = json.loads(item["binding_json"]).get("completion", {})
            if completion.get("effect_key") == effect.effect_key and completion.get("spec_hash") == spec_hash:
                mine.append(item)
        heads = [item for item in mine if item["intent_id"] not in superseded]
        supersedes = None
        if heads:
            head = heads[-1]
            status = orch.commit.operation_intent_status(
                head["intent_id"], tenant_id=authority["tenant_id"], principal=principal)
            if status.get("materialization") is not None:
                # 已进入执行链，由批准卡片和执行流程接手——除非这一版的动作已经证实没生效、
                # 且不是人拒绝的（阶段 B 裁决第 1 类）：按原内容重交一张新卡，有上限。
                action = _materialized_action(store, head["intent_id"])
                if action is not None and action["state"] == "REJECTED":
                    # 阶段 B 裁决第 2 类：人拒绝了这张卡。内容换出了新版本（字节变了）就替代重交，
                    # 再出一张卡；没换就交规划器（同一份内容不再拿去问已经说"不"的人）。
                    if _artifact_hash(store, head["candidate_artifact_id"]) != artifact.content_hash:
                        plans.append(_resubmit_plan(effect, operation, head["intent_id"], mission_id, spec_hash,
                                                    row, acceptance, artifact, len(mine), authority))
                    else:
                        plans.append({"effect_key": effect.effect_key, "rejected": {
                            "effect_key": effect.effect_key, "target": operation[2],
                            "rejections": _person_rejections(store, mine)}})
                    continue
                if action is None or not _proven_not_applied(store, action):
                    continue
                if len(mine) > SYSTEM_RESUBMIT_CAP:
                    from ..runtime.operation_reconciliation import nonapplication_outcome

                    attempts = [nonapplication_outcome(store, item) for item in
                                (_materialized_action(store, intent["intent_id"]) for intent in mine)
                                if item is not None and item["state"] == "FAILED"]
                    plans.append({"effect_key": effect.effect_key, "stop_failed": {
                        "reason": "publish_not_applied", "effect_key": effect.effect_key,
                        "target": operation[2], "attempts": attempts}})
                    continue
                supersedes = head["intent_id"]
                plans.append(_resubmit_plan(effect, operation, supersedes, mission_id, spec_hash,
                                            row, acceptance, artifact, len(mine), authority))
                continue
            state = str(status.get("state"))
            if head["candidate_artifact_id"] == artifact.id:
                if state in _REFUSED_VERDICTS:
                    plans.append({"effect_key": effect.effect_key, "stop": {
                        "effect_key": effect.effect_key, "target": operation[2], "verdict": state,
                        "explanation": f"审阅员不认可系统准备的发布申请（{state}），需要人来判断"}})
                    continue
                if state not in _RETRY_VERDICTS:
                    continue  # 审阅中或已通过待物化：等
                if len(mine) > SYSTEM_RESUBMIT_CAP:
                    plans.append({"effect_key": effect.effect_key, "stop": {
                        "effect_key": effect.effect_key, "target": operation[2], "verdict": state,
                        "explanation": f"发布申请的审阅已 {len(mine)} 次没能完成"}})
                    continue
            supersedes = head["intent_id"]  # 内容换了新版本，或审阅没做成：替代重交
        plans.append(_resubmit_plan(effect, operation, supersedes, mission_id, spec_hash,
                                    row, acceptance, artifact, len(mine), authority))
    return plans


def _materialized_action(store: Any, intent_id: str) -> Any:
    receipt = store.get_receipt("materialize:" + intent_id)
    return None if receipt is None else store.get_action(str(receipt.get("action_key") or ""))


def _artifact_hash(store: Any, artifact_id: Any) -> str | None:
    artifact = store.get_artifact(str(artifact_id or ""))
    return None if artifact is None else str(artifact.content_hash)


def _person_rejections(store: Any, intents: list[Any]) -> list[dict[str, Any]]:
    """Each card of this effect a person rejected, oldest first: who and their words."""
    found = []
    for intent in intents:
        action = _materialized_action(store, intent["intent_id"])
        if action is None or action["state"] != "REJECTED":
            continue
        for request in store.list_approvals(str(action["mission_id"]), "REJECTED"):
            if request.get("kind") == "action" and str(request.get("subject_key")) == str(action["action_key"]):
                found.append((float(request.get("closed_at") or 0), {
                    "request_id": request["request_id"], "action_key": action["action_key"],
                    "rejected_by": request.get("rejected_by"), "reason": request.get("reason") or ""}))
    return [item for _, item in sorted(found, key=lambda pair: (pair[0], pair[1]["request_id"]))]


def _proven_not_applied(store: Any, action: Any) -> bool:
    """FAILED after leaving our hands, with a stored proof it never happened."""
    from ..runtime.operation_reconciliation import stored_negative_proof

    return (action["state"] == "FAILED" and int(action.get("handoffs") or 0) >= 1
            and stored_negative_proof(store, action))


def _resubmit_plan(effect: Any, operation: Any, supersedes: Any, mission_id: str, spec_hash: str,
                   row: Any, acceptance: Any, artifact: Any, ordinal: int, authority: Any) -> dict[str, Any]:
    return {
            "effect_key": effect.effect_key, "target": operation[2], "supersedes": supersedes,
            "command": SubmitOperationIntentV2(
                schema_version=2, mission_id=mission_id,
                idempotency_key=derive_id("system-operation", mission_id, spec_hash,
                                          effect.effect_key, str(acceptance.acceptance_id),
                                          artifact.id, str(ordinal)),
                intent_source=OperationIntentSourceV2(
                    OperationIntentSourceKind.AUTHORIZED_SLOT,
                    origin_receipt_id=str(row["approval_receipt_id"]), slot_key=effect.effect_key),
                candidate_artifact_ref=TypedRef(TypedRefKind.ARTIFACT, artifact.id,
                                                artifact.version, artifact.content_hash),
                prepared_acceptance_refs=(TypedRef(
                    TypedRefKind.ACCEPTANCE, str(acceptance.acceptance_id), 1,
                    content_hash_of(acceptance.to_json())),),
                supersedes_intent_id=supersedes,
                completion_slot=CompletionSlotV2(spec_hash, effect.effect_key)),
            "tenant_id": authority["tenant_id"], "principal_id": authority["issuer_id"],
        }


def prepare_system_operations(orch: Any, mission_id: str) -> bool:
    """Act on every ready system operation; a refused submission is noted, never raised."""

    progressed = False
    for plan in pending_system_operations(orch, mission_id):
        if "stop" in plan:
            return _stop(orch, mission_id, plan["stop"])
        if "stop_failed" in plan:
            # 阶段 B 裁决第 1 类：同一效果按原内容重交到上限仍没生效——具名停，列出每次的结局
            from ..contracts.state_machines import MissionStopReason

            orch._commit_fail_mission(mission_id, stop_reason=MissionStopReason.ACTION_FAILED,
                                      detail=dict(plan["stop_failed"]))
            return True
        if "rejected" in plan:
            if _ask_planner_after_rejection(orch, orch.store.get_mission(mission_id), plan["rejected"]):
                return True
            continue
        if plan.get("ask"):
            mission = orch.store.get_mission(mission_id)
            if _ask_planner_for_source(orch, mission, HtnStore(orch.store), plan["effect_key"],
                                       plan["target"], plan["matches"]):
                return True
            continue
        command = plan.get("command")
        if command is None:
            continue
        try:
            # 人手动提交的入口（``Orchestrator.submit_operation_intent``）先装配操作运行时再提交；
            # 系统代办直接调底层提交，必须自己先装，否则进程里没人手动提交过时每轮都被
            # "operation runtime is unavailable" 拒掉、任务空转（2026-09-29 真机）。只在真要
            # 提交时装：没有发布效果的任务所在环境可能根本没注册发布连接器。
            from .operation_runtime import ensure_operation_runtime

            ensure_operation_runtime(orch)
            orch.commit.submit_operation_intent(
                command, tenant_id=plan["tenant_id"], principal=Principal(plan["principal_id"]))
            progressed = True
        except (ContractError, ValueError) as error:  # OperationCompletionError / sources errors
            orch._note(f"system operation {plan['effect_key']} not submitted: {error}")
            if getattr(error, "code", None) == "OP_CAPABILITY_UNSUPPORTED":
                # 阶段 B 裁决第 4 类: the deployment changed after confirmation and its
                # profile can no longer reach the confirmed milestone. Deterministic: no
                # retry; the Mission stops by name with the fact recorded.
                from ..contracts.state_machines import MissionStopReason

                orch._commit_fail_mission(mission_id, stop_reason=MissionStopReason.ACTION_FAILED, detail={
                    "reason": "operation_capability_unsupported", "effect_key": plan["effect_key"],
                    "error": str(error)[:300]})
                return True
    return progressed
