# SPDX-License-Identifier: Apache-2.0
"""Durable H4 triggers from original results, validity and requirements records.

Requests carry no guessed model action. Impact is calculated under the same writer
snapshot as the request; the original PlanningDecision pipeline chooses and commits.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..contracts.semantic_base import content_hash_of
from ..planning.htn.repair_adapter import RepairEventAdapter
from ..planning.htn.repair_decision import analyze_impact
from .assurance_recheck import closeout_stale_findings
from .hierarchical_dispatch import append_hierarchical_event
from .repair_impact import read_repair_impact_indexes

REQUESTED = "PlanningRepairRequested"
ADDRESSED = "PlanningRepairAddressed"
#: 一步的失败事件 → 修复请求的触发源类型。请求只带事实，由规划器决定重试、换做法、补步骤
#: 还是问人。
STEP_FAILURE_SOURCES = {"ResultRejected": "WorkerRejected", "VerificationFailed": "VerifierAcceptanceRejected",
                        "AttemptLost": "WorkerRejected", "AttemptTimedOut": "WorkerRejected",
                        # 2026-09-29 真机第十、十一局：步骤如实报告"卡住"（缺上游文件）后尝试进"等重试"，
                        # 却没人问规划器，几秒后判"没有可派发的工作"、整局失败。如实的卡住/失败/没进展
                        # 报告也交给规划器（带上步骤自己的说明）。
                        "OutcomeRecorded": "WorkerRejected"}
#: 架构方案 B（2026-09-30）：一次资料变更没影响到任何在跑的尝试或引用它的已通过结果，
#: 只记这一条（不发修复请求），下一轮不再重算。
SOURCE_CHANGE_ASSESSED = "SourceChangeAssessed"
SOURCE_CHANGE_EVENTS = {"SourceSuperseded": "source_superseded", "SourceRevoked": "source_revoked"}


def source_change_triggers(handler: Any, dispatch: Any, mission: Any, *, seen: set[str],
                           active_tasks: set[str]) -> bool:
    """架构方案 B：资料换版本 / 撤销 → 只对**有证据**的受影响对象发一条"证据失效"修复请求。

    派发时冻结的 ``source_versions`` 是当时全部现行资料，不是这一步用了哪些，所以：
    已通过的结果只按引用（claims 的 citations）判定；拿着旧版还在跑的尝试等它跑完再评估
    （验收会对照当前资料，被拒走普通的验收失败请求）；还没派发的步骤不算（下次派发自动
    拿新版）。新登记的资料不发请求。影响范围由已有的影响分析沿依赖算到下游；请求以资料
    事件的幂等键去重。
    """
    from ..contracts.state_machines import TERMINAL_ATTEMPT

    store = handler.store
    events = tuple(store.iter_events(mission.id))
    assessed = {e.payload.get("source_key") for e in events if e.type == SOURCE_CHANGE_ASSESSED}
    produced = False
    for event in events:
        reason = SOURCE_CHANGE_EVENTS.get(event.type)
        source_key = "source:" + event.idempotency_key
        if reason is None or source_key in seen or source_key in assessed:
            continue
        rows = [dict(r) for r in (event.payload.get("sources") or ()) if isinstance(r, dict)]
        old = rows[0] if rows else {}
        path, old_hash = str(old.get("path") or ""), str(old.get("version_hash") or "")
        revoked = reason == "source_revoked"
        new_hash = None if revoked else next((str(r.get("version_hash")) for r in rows[1:]), None)
        # 拿着旧版跑的尝试：还在跑就**先不评估**——2026-09-30 真机：当轮发请求，规划器只会回 WAIT
        # 等它跑完，修复轮又拒绝 WAIT，白花两轮次数；它跑完后验收本来就对照当前资料（审阅员按
        # 现行版本判、引用按当前性判），被拒就走普通的验收失败请求。跑完再评估，把它们记在案。
        on_old_version: list[str] = []
        still_running = False
        for task_id in sorted(active_tasks):
            for attempt in store.list_attempts(task_id):
                frozen = handler._frozen_source_binding(attempt).get("source_versions", {})
                if path in frozen and (revoked or str(frozen[path]) == old_hash):
                    on_old_version.append(attempt.id)
                    still_running |= attempt.status not in TERMINAL_ATTEMPT
        if still_running:
            continue
        accepted: list[str] = []
        for result_id, task_id in store.connection.execute(
                "SELECT result_id, task_id FROM results WHERE mission_id=? AND verification_state='DONE'"
                " ORDER BY received_at", (mission.id,)).fetchall():
            if str(task_id) not in active_tasks:
                continue
            stored = store.get_result(str(result_id))
            if stored is None or str(stored.verdict or "").upper() != "PASS":
                continue
            cited = any(c.path == path and (revoked or c.version == old_hash)
                        for claim in stored.envelope.claims for c in claim.citations)
            if cited:
                accepted.append(str(result_id))
        detail = {"reason": reason, "path": path, "old_version": old_hash, "new_version": new_hash,
                  "source_event": event.idempotency_key,
                  "affected": {"attempts_on_old_version": on_old_version, "accepted_results": accepted}}
        refs = tuple(accepted)
        if not refs:
            append_hierarchical_event(store, SOURCE_CHANGE_ASSESSED, mission.id, key=source_key,
                                      payload={"source_key": source_key, **detail})
            continue
        produced |= record_request(dispatch, mission.id, event_type="EvidenceInvalidated",
                                   trigger_refs=refs, source_key=source_key, detail=detail)
    return produced


def stale_evidence_triggers(handler: Any, dispatch: Any, mission: Any, *, seen: set[str],
                            active_tasks: set[str]) -> bool:
    """第 4 项（2026-10-01）：收尾评估发现"通过的依据已变"→ 给受影响的步骤记一条证据失效修复请求。

    收尾消费者只拦收尾并把变了的证书写进评估正文（``stale_certificates``）；这里把它翻成规划器
    看得懂的请求（与资料换版同一条路 ``EvidenceInvalidated``），同一张证书同一处变化只记一次；
    规划器处理过（``PlanningRepairAddressed``）后收尾评估不再把它算作拦截。
    """
    produced = False
    for stale in closeout_stale_findings(handler.store, mission.id):
        source_key = str(stale["source_key"])
        if source_key in seen:
            continue
        task_id = stale.get("task_id")
        refs = (str(task_id),) if task_id and str(task_id) in active_tasks else (mission.id,)
        produced |= record_request(dispatch, mission.id, event_type="EvidenceInvalidated", trigger_refs=refs,
                                   source_key=source_key,
                                   detail={"reason": "evidence_stale", **{k: v for k, v in stale.items() if k != "source_key"}})
    return produced


#: 片 B：计划里有目标还没有做法 → 每个计划版本一条请求，幂等键是这个前缀加计划版本号。
OPEN_GOALS_PREFIX = "open-goals:"


def superseded_open_goal_requests(pending: Any, plan_revision: int) -> list[str]:
    """待处理的"目标还没有做法"请求里，属于旧计划版本的那些（请求编号）。

    这种请求说的是"第 N 版计划里这些目标没有做法"。计划已经到了别的版本，这句话就过时了：
    还开着的目标由新版本自己的请求去说，不让两条请求指着同一个目标。
    """
    current = OPEN_GOALS_PREFIX + str(int(plan_revision))
    return [str(row["request_id"]) for row in pending
            if str(row.get("source_key", "")).startswith(OPEN_GOALS_PREFIX)
            and row.get("source_key") != current]


def open_goal_triggers(handler: Any, dispatch: Any, mission: Any, *, seen: set[str]) -> bool:
    """片 B：当前计划里有目标还没有做法 → 一条通用请求把规划器叫来（每个计划版本一条）。

    此前这是主循环里一条专用入口（"展开未细化目标"），自己记事件、自己开规划轮。现在与
    步骤失败、证据失效走同一条路：请求里只有事实（哪几个目标、什么类型），选做法、提做法
    还是问人由规划器定；规划器为其中任何一个目标提交了做法，这条请求就算处理了，剩下的
    目标由新计划版本的请求接着问。第一份计划之前的根目标不归这里（那是规划的起点）。
    """
    from ..contracts.htn import TaskForm

    store = handler.store
    active = dispatch.semantics().active_plan_revision(mission.id)
    if active is None:
        return False
    revision = int(active.revision)
    produced = False
    for request_id in superseded_open_goal_requests(pending_requests(store, mission.id), revision):
        append_hierarchical_event(store, ADDRESSED, mission.id, key="system:" + request_id,
            payload={"decision_id": None, "decision_type": "SYSTEM_SUPERSEDED", "status": "COMMITTED",
                     "subject_key": None, "repair_request_ids": [request_id],
                     "superseded_by_plan_revision": revision})
        produced = True
    source_key = OPEN_GOALS_PREFIX + str(revision)
    if source_key in seen:
        return produced
    network = dispatch.network(mission.id)
    open_goals = [spec for spec in network.occurrences
                  if spec.form is TaskForm.COMPOUND
                  and network.adopted_instance_for(spec.occurrence_id) is None]
    if not open_goals:
        return produced
    tasks = tuple(str(spec.task_id) for spec in open_goals)
    detail = {"reason": "goal_has_no_method", "open_goals": [
        {"task_id": str(spec.task_id), "occurrence_id": str(spec.occurrence_id),
         "goal_type": str(network.binding_for_occurrence(spec.occurrence_id).goal_signature.signature_id)}
        for spec in open_goals]}
    return record_request(
        dispatch, mission.id, event_type="GoalUnrefined", trigger_refs=tasks, source_key=source_key,
        detail=detail, scope=tasks + tuple(str(spec.occurrence_id) for spec in open_goals),
        new_work=tasks) or produced


def settle_addressed_requests(handler: Any, dispatch: Any, mission: Any) -> bool:
    """架构方案 B 前置 2（用户 2026-09-29 决定）：修复请求由**系统**消费的出口。

    此前只有"提交了计划改动且目标与影响范围相交"才消费请求；规划器判断旧贡献不用重做时，
    请求永远挂着，让"还欠修复"一直为真，之后任何一轮被拒都逼着开新轮直到次数用完。现在：
    影响范围里没有新增工作、且每个受影响的叶子步骤都在请求之后重新验收通过（``TaskCompleted``
    晚于请求）时，系统记一条 ``PlanningRepairAddressed``（decision_type=SYSTEM_REVALIDATED）。
    影响范围含义务、操作、方法实例等只有计划改动才能了结的对象时，不由系统消费。
    """
    from ..contracts.htn import TaskForm

    store = handler.store
    events = tuple(store.iter_events(mission.id))
    handled = {rid for e in events if e.type == ADDRESSED for rid in e.payload.get("repair_request_ids", ())}
    network = dispatch.network(mission.id)
    leaf_of_occurrence = {str(s.occurrence_id): str(s.task_id) for s in network.occurrences if s.form is not TaskForm.COMPOUND}
    compound_occurrences = {str(s.occurrence_id) for s in network.occurrences if s.form is TaskForm.COMPOUND}
    leaf_tasks = set(leaf_of_occurrence.values())
    compound_tasks = {str(s.task_id) for s in network.occurrences if s.form is TaskForm.COMPOUND}
    # 影响分析沿"子步骤 → 所属复合目标 → 采纳的方法实例"把父级也算进来；父级经叶子了结。
    parents = compound_occurrences | compound_tasks | {str(i.instance_id) for i in network.method_instances}
    acceptances = {str(a.acceptance_id): str(a.task_id) for a in dispatch.semantics().list_acceptances(mission.id)}
    # 分层语义下叶子验收通过写 AcceptanceCommitted（带 task_id）；旧任务状态机同时写 TaskCompleted。
    completed: dict[str, tuple[int, str]] = {}
    for e in events:
        if e.type in {"AcceptanceCommitted", "TaskCompleted"} and e.task_id:
            completed[str(e.task_id)] = (int(e.seq or 0), e.idempotency_key)
    produced = False
    for e in events:
        if e.type != REQUESTED or e.payload["request_id"] in handled:
            continue
        impact = e.payload.get("impact", {})
        if impact.get("new_work"):
            continue
        tasks: set[str] = set()
        settleable = True
        for item in (str(i) for group in ("revalidate", "supersede") for i in impact.get(group, ())):
            if item == mission.id or item in parents:
                continue  # a parent is settled through its leaves
            if item in leaf_tasks:
                tasks.add(item)
            elif item in leaf_of_occurrence:
                tasks.add(leaf_of_occurrence[item])
            elif item in acceptances:
                tasks.add(acceptances[item])
            else:
                attempt = store.get_attempt(item)
                if attempt is not None:
                    tasks.add(str(attempt.task_id))
                    continue
                row = store.connection.execute("SELECT task_id FROM results WHERE result_id=?", (item,)).fetchone()
                if row is not None:
                    tasks.add(str(row[0]))
                    continue
                settleable = False  # obligations, operations, method instances: only a plan change settles them
                break
        if not settleable or not tasks:
            continue
        request_seq = int(e.seq or 0)
        settled = {t: completed[t][1] for t in sorted(tasks) if t in completed and completed[t][0] > request_seq}
        if len(settled) != len(tasks):
            continue
        append_hierarchical_event(store, ADDRESSED, mission.id, key="system:" + str(e.payload["request_id"]),
            payload={"decision_id": None, "decision_type": "SYSTEM_REVALIDATED", "status": "COMMITTED",
                     "subject_key": None, "repair_request_ids": [e.payload["request_id"]], "settled_by": settled})
        produced = True
    return produced


def failure_fingerprint(event_type: str, payload: Any) -> str:
    """同一步的两次失败是不是"同一个失败"：去掉每次都变的东西（耗时、工作区路径）后的指纹。

    验收失败按"哪一层 + 具体问题"算（``verification_failure_fingerprint``）；其余失败按
    事件类型、原因和涉及路径算。只用来如实报告"连续几次一样"，不据此替规划器做任何决定。
    """
    from .occurrence_tasks import verification_failure_fingerprint

    body = dict(payload or {})
    if event_type == "VerificationFailed":
        return verification_failure_fingerprint(body.get("failures") or ())
    detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
    return content_hash_of({"event_type": event_type, "reason": body.get("reason"),
                            "outcome": body.get("outcome"),
                            "paths": sorted(str(item) for item in detail.get("paths") or ())})


def _is_step_failure(event: Any) -> bool:
    if event.type not in STEP_FAILURE_SOURCES or not event.task_id:
        return False
    return (event.type != "OutcomeRecorded"
            or event.payload.get("outcome") in {"blocked", "failure", "no_progress"})


def step_failure_facts(events: Any, event: Any) -> dict[str, Any]:
    """这一步到这次为止失败了几次、连续几次是同一个失败、失败指纹（片 0 第 2 步，2026-10-01）。

    此前"只读步骤越权改文件"和"同一步反复同样失败"到次数就由 Harness 取消步骤、退掉做法再开
    专用规划轮；现在只把这三个事实放进请求，规划器自己判断重试还有没有意义。
    """
    history = [e for e in events if _is_step_failure(e) and e.task_id == event.task_id
               and int(e.seq or 0) <= int(event.seq or 0)]
    fingerprint = failure_fingerprint(event.type, event.payload)
    identical = 0
    for earlier in reversed(history):
        if failure_fingerprint(earlier.type, earlier.payload) != fingerprint:
            break
        identical += 1
    return {"step_failures": len(history), "consecutive_identical": identical,
            "failure_fingerprint": fingerprint}


def record_request(dispatch: Any, mission_id: str, *, event_type: str,
                   trigger_refs: tuple[str, ...], source_key: str,
                   detail: dict[str, Any], scope: tuple[str, ...] | None = None,
                   new_work: tuple[str, ...] = ()) -> bool:
    """``scope``：这条请求是"关于"哪些步骤的；不给就按触发引用推（该步骤及其上级目标）。
    关于整个任务的请求（最终审查打回）由调用方给出全部步骤——任何一步上的计划改动都算处理了它。
    ``new_work``：这条请求要的是还不存在的工作（没有做法的目标）；只有计划改动能了结它。"""
    store = dispatch.store
    with store.transaction():
        if any(e.type == REQUESTED and e.payload.get("source_key") == source_key
               for e in store.iter_events(mission_id)):
            return False
        network = dispatch.network(mission_id)
        request = RepairEventAdapter.request_from_event(
            {"type": event_type, "trigger_refs": trigger_refs,
             "payload": {"context": detail}}, mission_id=mission_id,
            plan_revision=int(network.plan_revision))
        indexes = read_repair_impact_indexes(store, network, mission_id)
        # 要的是还不存在的工作时，没有任何已有成果受影响：不把上级目标和兄弟步骤报成"待重新验收"。
        impact = (analyze_impact(new_work=new_work, **indexes) if new_work
                  else analyze_impact(request, **indexes))
        append_hierarchical_event(store, REQUESTED, mission_id, key=source_key,
            payload={"source_key": source_key, "request_id": request.request_id,
                     "request": request.to_json(), "impact": impact.to_json(),
                     "trigger_scope": (sorted(scope) if scope is not None
                                       else trigger_scope(store, network, mission_id, trigger_refs))})
    return True


def trigger_scope(store: Any, network: Any, mission_id: str, trigger_refs: tuple[str, ...]) -> list[str]:
    """The steps a request is *about*: each trigger's own task and occurrence plus the
    compound goals above it.  Empty when no trigger names a step
    (a Mission-level trigger such as a requirements amendment).

    2026-09-30 真机（结构修复第 2 局）：消费规则只看影响范围，影响范围含下游；规划器只重做
    下游第二步，"第一步引用了旧资料"的请求也被记成已处理，第一步从没重做。影响范围仍用于
    展示与系统消费；"规划器处理了这条请求"只认它直接指向的步骤或其上级。
    """
    tasks: set[str] = set()
    for ref in trigger_refs:
        ref = str(ref)
        if store.get_task(ref) is not None:
            tasks.add(ref)
            continue
        attempt = store.get_attempt(ref)
        if attempt is not None:
            tasks.add(str(attempt.task_id))
            continue
        row = store.connection.execute(
            "SELECT task_id FROM results WHERE result_id=? AND mission_id=?", (ref, mission_id)).fetchone()
        if row is not None:
            tasks.add(str(row[0]))
    if not tasks:
        return []
    parent: dict[str, str] = {}
    for instance in network.method_instances:
        if not network.is_adopted(instance.instance_id):
            continue
        for child in instance.child_bindings:
            parent[str(child.occurrence_id)] = str(instance.effective_goal_occurrence_id)
    by_occurrence = {str(s.occurrence_id): s for s in network.occurrences}
    scope: set[str] = set(tasks)
    frontier = [str(s.occurrence_id) for s in network.occurrences if str(s.task_id) in tasks]
    while frontier:
        occurrence = frontier.pop()
        if occurrence in scope:
            continue
        scope.add(occurrence)
        spec = by_occurrence.get(occurrence)
        if spec is not None:
            # Not the obligation: sibling steps of one goal share it, so it would make a
            # decision on any sibling "address" this request.
            scope.add(str(spec.task_id))
        if occurrence in parent:
            frontier.append(parent[occurrence])
    return sorted(scope)


def collect_triggers(handler: Any, mission: Any) -> bool:
    dispatch = handler._new_mode(mission)
    if dispatch is None:
        return False
    store = handler.store
    produced = False
    active_tasks = {str(spec.task_id) for spec in dispatch.network(mission.id).occurrences}
    seen = {e.payload.get("source_key") for e in store.iter_events(mission.id) if e.type == REQUESTED}
    from .failure_classes import classify_failure

    def failure_class(attempt_id: str | None) -> dict[str, str]:
        # 2026-09-28：请求里带上"谁的错"，非模型原因由系统原地重做（planning_selection）。
        attempt = store.get_attempt(attempt_id) if attempt_id else None
        return {} if attempt is None or not attempt.failure else {
            "failure_class": classify_failure(attempt.failure)}
    sources = STEP_FAILURE_SOURCES
    produced |= settle_addressed_requests(handler, dispatch, mission)
    produced |= source_change_triggers(handler, dispatch, mission, seen=seen, active_tasks=active_tasks)
    produced |= stale_evidence_triggers(handler, dispatch, mission, seen=seen, active_tasks=active_tasks)
    produced |= open_goal_triggers(handler, dispatch, mission, seen=seen)
    events = tuple(store.iter_events(mission.id))
    for event in events:
        source_key = "event:" + event.idempotency_key
        if event.type not in sources or source_key in seen:
            continue
        if (event.type == "OutcomeRecorded"
                and event.payload.get("outcome") not in {"blocked", "failure", "no_progress"}):
            continue
        if event.task_id and event.task_id not in active_tasks:
            continue
        if (event.type in {"AttemptLost", "AttemptTimedOut"}
                and event.payload.get("reason") in {"runtime_unavailable", "provider_outcome_unknown"}):
            # The authoritative failure row below produces the runtime request.
            continue
        refs = tuple(dict.fromkeys(str(x) for x in (event.attempt_id, event.task_id) if x)) or (mission.id,)
        produced |= record_request(dispatch, mission.id, event_type=sources[event.type],
            trigger_refs=refs, source_key=source_key,
            detail={"source_event": event.idempotency_key, "event_type": event.type,
                    "detail": dict(event.payload), **failure_class(event.attempt_id),
                    **({"occurrence": step_failure_facts(events, event)} if event.task_id else {})})
    htn = dispatch.semantics()
    for state in ("PENDING", "RECHECKING"):
        for dirty in htn.list_dirty(mission.id, state=state):
            # A committed repair already revoked these execution rights. Its
            # internal recheck marker is not a new evidence failure to replan.
            if dirty.reason == "dispatch_generation_revoked":
                continue
            source_key = "dirty:" + content_hash_of({k: v for k, v in asdict(dirty).items() if k != "state"})
            if source_key not in seen:
                produced |= record_request(dispatch, mission.id, event_type="EvidenceInvalidated",
                    trigger_refs=(dirty.subject_id,), source_key=source_key, detail=asdict(dirty))
    # Internal leaf/composition requirements snapshots are not user amendments.
    # Only a persisted revision carrying its amendment credential opens this trigger.
    for revision in htn.list_requirements_revisions(mission.id):
        source_key = "requirements:" + str(revision.revision_id)
        if revision.amendment_credential_ref and source_key not in seen:
            produced |= record_request(dispatch, mission.id, event_type="RequirementsUpdated",
                trigger_refs=(mission.id,), source_key=source_key,
                detail={"requirements": revision.to_json(), "content_hash": content_hash_of(revision.to_json())})
    for task in store.list_tasks(mission.id):
        if task.id not in active_tasks:
            continue
        for attempt in store.list_attempts(task.id):
            failure = attempt.failure
            source_key = "runtime:" + attempt.id
            if failure and failure.get("reason") in {"runtime_unavailable", "provider_outcome_unknown"} and source_key not in seen:
                produced |= record_request(dispatch, mission.id, event_type="RuntimeUnavailable",
                    trigger_refs=(attempt.id,), source_key=source_key,
                    detail={**dict(failure), **failure_class(attempt.id)})
    # A committed retry is bound to the exact task/plan/input/operation read.
    # If it becomes stale before dispatch, reopen a system request; silently
    # retaining the old addressed trigger would leave the Task blocked forever.
    from .planning_retry import RETRY_AUTHORIZED, pending_retry_permit, retry_decision_required
    permits = [e for e in store.iter_events(mission.id) if e.type == RETRY_AUTHORIZED]
    for task in store.list_tasks(mission.id):
        if task.id not in active_tasks or not retry_decision_required(store, mission.id, task.id):
            continue
        latest = max(store.list_attempts(task.id), key=lambda item: item.ordinal)
        previous = next((e for e in reversed(permits) if e.payload.get("task_id") == task.id
                         and e.payload.get("failed_attempt_id") == latest.id), None)
        if previous is None or pending_retry_permit(store, mission.id, task.id) is not None:
            continue
        from ..runtime.planning_operations import StoreOperationReader, build_operation_snapshot, SourceUnavailable
        try:
            operations = build_operation_snapshot(mission.id, reader=StoreOperationReader(store))
            operation_state = operations.read_digest
        except SourceUnavailable as error:
            operation_state = error.reason
        current = htn.task_semantics_of(mission.id, task.id)
        retry_state = {"previous_decision": previous.payload["decision_id"], "attempt_id": latest.id,
                 "task_version": task.version, "plan_revision": int(dispatch.network(mission.id).plan_revision),
                 "binding_hash": None if current is None else content_hash_of(current.to_json()),
                 "operation_state": operation_state}
        source_key = "retry_stale:" + content_hash_of(retry_state)
        if source_key not in seen:
            produced |= record_request(dispatch, mission.id, event_type="WorkerRejected",
                trigger_refs=(latest.id, task.id), source_key=source_key,
                detail={"reason": "committed_retry_binding_changed", **retry_state})
    return produced


def pending_requests(store: Any, mission_id: str) -> list[dict[str, Any]]:
    events = tuple(store.iter_events(mission_id))
    handled = {request_id for e in events if e.type == "PlanningRepairAddressed"
               for request_id in e.payload.get("repair_request_ids", ())}
    return [dict(e.payload) for e in events if e.type == REQUESTED and e.payload["request_id"] not in handled]


def repair_goal_occurrences(store: Any, network: Any) -> tuple[str, ...]:
    """Expose alternatives for adopted compounds affected by an original H4 trigger.

    Visibility is not a repair authorization. The decision compiler still checks
    the current instance, impact, accepted work, and operation state at admission.
    """
    from ..contracts.htn import TaskForm

    mission_id = str(network.mission_id)
    affected = {str(item) for row in pending_requests(store, mission_id)
                for group in ("revalidate", "supersede", "new_work")
                for item in row["impact"].get(group, ())}
    return tuple(sorted(str(spec.occurrence_id) for spec in network.occurrences
                        if spec.form is TaskForm.COMPOUND
                        and affected.intersection((str(spec.occurrence_id), str(spec.task_id)))))


def address_requests(store: Any, mission_id: str, *, package: Any,
                     decision_id: str, decision_type: str, status: str,
                     subject_key: str) -> None:
    """Only a committed plan change for the subject a trigger is about consumes it.

    Evidence, human questions, proposals and WAIT preserve the request so the
    resumed planner can still see the failure that opened the service call.
    """
    if status != "COMMITTED" or decision_type not in {"REFINE", "REPAIR", "BIND_EXISTING_GOAL"} or not isinstance(package, dict):
        return
    subject = next((s for s in package.get("planning_subjects", ())
                    if s.get("subject_key") == subject_key), None)
    if subject is None:
        return
    targets = {str(subject[k]) for k in ("occurrence_id", "task_id", "obligation_id") if subject.get(k)}
    addressed = []
    for request in package.get("repair_requests", ()):
        impact = request.get("impact", {})
        scope = {str(item) for item in request.get("trigger_scope", ())}
        if scope:
            # Only a decision on the step the request is about (or a goal above it), or on
            # work the request itself called for, addresses it.
            affected = scope | {str(item) for item in impact.get("new_work", ())}
        else:
            # A Mission-level trigger (no step named): any affected subject answers it.
            affected = {str(item) for group in ("revalidate", "supersede", "new_work")
                        for item in impact.get(group, ())}
        if targets & affected:
            addressed.append(request["request_id"])
    if addressed:
        append_hierarchical_event(store, "PlanningRepairAddressed", mission_id, key=decision_id,
            payload={"decision_id": decision_id, "decision_type": decision_type, "status": status,
                     "subject_key": subject_key, "repair_request_ids": addressed})
