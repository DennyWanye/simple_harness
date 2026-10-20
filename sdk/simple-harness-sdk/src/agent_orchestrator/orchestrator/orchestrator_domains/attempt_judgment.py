# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Judgment inputs, next-attempt selection and criteria evaluation."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from simple_harness.agents import AgentConfig, AgentLimits

from ...artifacts.store import ArtifactStoreError, read_verified
from ...artifacts.versioning import ArtifactConflict, UpstreamInput
from ...context.context_builder import (
    CONTEXT_BUILDER_VERSION,
    ContextRejected,
    TaskPackage,
    _seal,
    build_worker_package,
)
from ...context.retrieval import KnowledgeContext, RetrievalUnavailable
from ...contracts import (
    TERMINAL_ATTEMPT,
    Artifact,
    Attempt,
    AttemptStatus,
    ContractError,
    Mission,
    MissionStopReason,
    Task,
    ids,
)
from ...contracts.error_table import handoff_refusal_transient
from ...contracts.models import sha256_hex
from ...deployment.root import current_statements
from ...governance.budgets import BudgetExhausted
from ...governance.policies import effective_tools
from ...graph.eligibility import EligiblePrimitiveTask
from ...graph.projection_validation import GraphIntegrityError
from ...graph.terminal import accepted_tasks
from ...runtime.action_schema import ACTION_PREFIX
from ...runtime.agent_worker import user_message_json
from ...runtime.first_request_budget import FirstRequestBudget
from ...runtime.model_router import RoutingUnavailable
from ...runtime.role_templates import role_for_task
from ...runtime.tool_gateway import TOOL_ANSWER_MARGIN, run_pytest
from ...storage import htn_store
from ...storage.store import StoreError
from ..action_commits import HANDOFF_READY_STATES, judgment_key
from ..assurance_point_use import context_claims
from ..budget_accounts import Reservation, is_global_account, mission_account, task_account
from ..commit_service import CommitRejected, NonModelFailuresExhausted
from ..failure_classes import (
    INFRA,
    classify_failure,
    output_exhausted,
    output_exhausted_fact,
    turn_cap_exceeded,
    turn_cap_fact,
)
from ..hierarchical_dispatch import HierarchicalDispatch, WriteConflictPending
from ..occurrence_tasks import read_only_leaf
from ..planning_retry import pending_retry_permit, retry_decision_required
from ..planning_runtime_block import pending_block
from ..scoped_content_review import task_content_prompt_scope
from ..worker_context import parent_goal as read_parent_goal
from ..worker_context import upstream_steps
from .core import OrchestratorCore
from .support import _under_source_root

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from ...contracts.resolution import GoalResolution


def _turn_error_code(failure: Mapping[str, Any]) -> str | None:
    error = failure.get("error")
    return error.get("error_code") if isinstance(error, Mapping) else None


def retry_feedback(
    attempts: Sequence[Attempt], previous: Attempt | None
) -> tuple[list[str], list[Mapping[str, Any]]]:
    """What the repair Attempt is told about the failures before it.

    2026-09-26 真机文档任务: a provider turn failure in between hid the earlier
    content rejection, so the next Attempt repeated the rejected mistake.  Walk back
    past turn failures to the most recent failure that judged the content.
    """
    feedback: list[str] = []
    verifier_feedback: list[Mapping[str, Any]] = []
    if previous is None:
        return feedback, verifier_feedback
    chain: list[Attempt] = []
    for earlier in reversed(attempts[: list(attempts).index(previous) + 1]):
        if earlier.status not in TERMINAL_ATTEMPT or not earlier.failure:
            continue
        chain.append(earlier)
        if earlier.failure.get("reason") != "turn_failed":
            break
    for failed in chain:
        failure = failed.failure or {}
        reason = str(failure.get("reason"))
        # 2026-09-25 UI 全量点击: an "inconclusive" failure (e.g. one claim's missing
        # limitation) carries the same verifier failures; the repair Attempt used to
        # get only "inconclusive: " and could not know which pair to fix.
        if reason in {"verification_failed", "inconclusive"}:
            for item in failure.get("failures", []):
                if isinstance(item, Mapping):
                    feedback.append(f"{item.get('layer')}: {item.get('summary')}")
                    verifier_feedback.append(dict(item))
        elif reason == "turn_failed" and _turn_error_code(failure) == "react_wall_clock_exceeded":
            # 2026-10-10 parse 重跑：单轮时限到了被终止，原来写成"模型调用失败"——不是事实
            feedback.append("上一次尝试超过了单轮时限，被系统终止；这一轮从头做。")
        elif reason == "turn_failed" and turn_cap_exceeded(failure.get("error")):
            # 2026-10-10 parse 重跑：做满 24 次调用被终止，原来写"模型调用失败"——只写事实
            # 上一行 turn_cap_exceeded 已确认 error 是映射
            feedback.append(
                turn_cap_fact(cast("Mapping[str, Any]", failure.get("error"))) + "这一轮从头做。"
            )
        elif reason == "turn_failed" and output_exhausted(failure.get("error")):
            # 2026-10-09：原来拼的是一段原始字典，模型读不懂；只写事实，怎么办由它判断
            feedback.append(output_exhausted_fact(failure.get("error")))
        elif reason == "turn_failed":
            # 服务侧出错的那一轮同样只写一句事实，不拼原始字典。
            # 只有归类为服务侧（INFRA）的才说"服务侧错误"；模型自己这一轮的失败（回合上限、
            # 工具参数错…）写错误码和错误自带的说明，不替它定性。
            error: Any = failure.get("error") if isinstance(failure.get("error"), Mapping) else {}
            code = error.get("error_code") or failure.get("error_kind") or "unknown"
            if classify_failure(failure) == INFRA:
                feedback.append(
                    f"上一轮有一次模型调用没有拿到回复（服务侧错误，代码 {code}）；这一轮从头做。"
                )
            else:
                said = error.get("message")
                said = f"：{said}" if isinstance(said, str) and said.strip() else ""
                feedback.append(
                    f"上一轮因一次模型调用出错而失败（代码 {code}{said}）；这一轮从头做。"
                )
        elif reason == "executor_stalled":
            # 2026-10-10（parse/docopt 重跑）：被当成卡死终止的那一轮，原来给下一轮的是 "executor_stalled: "
            stalled = failure.get("stalled_seconds")
            how_long = f"{float(stalled):.0f} 秒" if isinstance(stalled, (int, float)) else "太久"
            feedback.append(f"上一次尝试 {how_long}没有进展，被系统终止；这一轮从头做。")
        else:
            feedback.append(f"{reason}: {failure.get('error', '')}")
    return feedback, verifier_feedback


class AttemptJudgmentMixin(OrchestratorCore):
    """Judgment inputs, next-attempt selection and criteria evaluation."""

    __slots__ = ()

    def _hierarchical_judgment_inputs(
        self, mission: Mission, new_mode: HierarchicalDispatch, tasks: Sequence[Task]
    ) -> list[UpstreamInput]:
        """The Mission Judge's tree for a hierarchical Mission (P2.3k / defect N3).

        Read from what the root ``GoalResolution`` was formed out of and nothing else:
        the CURRENT acceptances of the adopted plan (``root_contributions``), each
        acceptance's accepted artifacts, and the outputs P2.3h indexed on declared
        ports.  One path written by several contributions is not a conflict here —
        ordering in this mode is the typed network's, not ``dependency_ids`` — so the
        rule is override, weighted ``(criterion-linked, port-indexed, acceptance
        order)``: the output of a step the plan made answerable for a root criterion
        wins, then an output the reviewer read at a declared port, then the later
        acceptance (the clock is the tie-break, never the rule).

        P2.3k verification P1-1 (AER I05, "what the reviewer saw is what is
        delivered"): an output a root criterion is linked to is **never dropped**.
        When two criterion-linked port outputs land on one path — C1-r1's ``verify``
        ``report`` and ``summarize`` ``summary`` were both ``REPORT.md`` — the loser
        keeps its bytes in the tree under ``accepted-outputs/<task>/<port>/<path>``,
        and the record names both artifacts.  Nothing raises; what was superseded
        and where it was kept is written down once, in
        ``ArtifactMergeNotApplicableUnderHierarchical``.
        """

        semantics = new_mode.semantics()
        contributing = {
            item for ids in new_mode.root_contributions(mission.id).values() for item in ids
        }
        # ``list_acceptances`` orders by ``accepted_at_ms`` then id: the clock is the
        # tie-break between contributions, never the rule.
        acceptances = [
            item
            for item in semantics.list_acceptances(mission.id)
            if str(item.acceptance_id) in contributing
        ]
        rank_of = {str(item.acceptance_id): index for index, item in enumerate(acceptances)}
        linked = {
            str(item.task_id)
            for item in self._root_review(mission, new_mode).carried_criteria(mission.id)
        }
        tasks_by_id = {task.id: task for task in tasks}
        # path → every placement offered for it, each (weight, task_id, artifact, port)
        offered: dict[str, list[tuple[tuple[int, int, int], str, Artifact, str | None]]] = {}

        def place(task_id: str, artifact: Artifact | None, rank: int, port: str | None) -> None:
            if artifact is None:
                return
            weight = (int(task_id in linked), int(port is not None), rank)
            offered.setdefault(artifact.path, []).append((weight, task_id, artifact, port))

        for acceptance in acceptances:
            task_id = str(acceptance.task_id)
            rank = rank_of[str(acceptance.acceptance_id)]
            task = tasks_by_id.get(task_id) or self.store.get_task(task_id)
            for artifact_id in () if task is None else task.accepted_artifacts:
                place(task_id, self.store.get_artifact(artifact_id), rank, None)
        for row in semantics.list_acceptance_outputs(mission.id):
            acceptance_id = str(row.get("acceptance_id", ""))
            if acceptance_id not in rank_of:
                continue
            place(
                str(row.get("producer_task_ref", "")),
                self.store.get_artifact(str(row.get("artifact_id", ""))),
                rank_of[acceptance_id],
                str(row.get("output_port", "")) or None,
            )
        tree: dict[str, UpstreamInput] = {}
        superseded: list[dict[str, Any]] = []
        for path in sorted(offered):
            placements = sorted(offered[path], key=lambda item: item[0], reverse=True)
            _weight, kept_task, kept_artifact, _port = placements[0]
            tree[path] = UpstreamInput(
                kept_task, path, kept_artifact.content_hash, kept_artifact.id
            )
            losers: list[dict[str, Any]] = []
            seen_artifacts = {kept_artifact.id}
            for weight, task_id, artifact, port in placements[1:]:
                if (
                    artifact.id in seen_artifacts
                    or artifact.content_hash == kept_artifact.content_hash
                ):
                    continue  # the same bytes under another placement are not a loss
                seen_artifacts.add(artifact.id)
                kept_at = None
                if weight[0] and port is not None:
                    # I05: evidence a root criterion was judged on stays deliverable.
                    kept_at = f"accepted-outputs/{task_id}/{port}/{path}"
                    tree[kept_at] = UpstreamInput(
                        task_id, kept_at, artifact.content_hash, artifact.id
                    )
                losers.append(
                    {
                        "task_id": task_id,
                        "artifact_id": artifact.id,
                        "content_hash": artifact.content_hash,
                        "linked": bool(weight[0]),
                        "port": port,
                        "kept_at": kept_at,
                    }
                )
            if not losers:
                continue
            kept_by = "acceptance_order"
            if kept_task in linked:
                kept_by = (
                    "acceptance_order_between_linked"
                    if any(item["linked"] for item in losers)
                    else "criterion_link"
                )
            superseded.append(
                {
                    "path": path,
                    "kept_task_id": kept_task,
                    "kept_artifact_id": kept_artifact.id,
                    "kept_content_hash": kept_artifact.content_hash,
                    "kept_by": kept_by,
                    "superseded_task_ids": list(dict.fromkeys(item["task_id"] for item in losers)),
                    "superseded": losers,
                }
            )
        self.commit.record_artifact_merge_not_applicable(
            mission.id,
            subject=f"{mission.id}:judge:artifact-merge",
            contributions=len(acceptances),
            artifacts=len(tree),
            superseded=superseded,
        )
        if superseded:
            self._note(
                f"hierarchical mission {mission.id}: judgment tree keeps the criterion-linked / "
                f"port / last writer of {[item['path'] for item in superseded]}; a linked loser "
                "is kept under accepted-outputs/; the legacy merge is not applied"
            )
        return [tree[path] for path in sorted(tree)]

    async def _next_attempt(
        self,
        mission: Mission,
        task: Task,
        attempts: Sequence[Attempt],
        *,
        allocation: Mapping[str, Any] | None = None,
        admission: Any = None,
    ) -> bool:
        # Review F1: every other caller of this entry (repair, a manual drive) must be
        # fail-closed too, or the refusal in ``_decide`` would only cover the common path.
        if self._assembly_missing(mission, at="next_attempt"):
            return False
        new_mode = self._new_mode(mission)
        if new_mode is None:
            return False
        if self._dispatch_held(mission, task, admission, new_mode):
            return False
        # a repair follows the last *failed* Attempt
        previous, feedback, verifier_feedback = self._attempt_feedback(mission, task, attempts)
        # P2.3b / §24.1 decision 4: the Attempt starts from the resolved InputManifest,
        # so an ORDER-only predecessor contributes nothing.
        try:
            # 清单（端口绑定 + 交付绑定）的物化结果就是全部输入，没有第二套叠加。
            inputs = new_mode.attempt_inputs(mission.id, task.id)
        except WriteConflictPending as error:
            # 阶段 D：上游两步把同一个文件写成了两样；这一步不开工，等规划器处理写入冲突修复请求
            self._note(f"task {task.id} not dispatched: {error}")
            return False
        except ArtifactConflict as error:
            self._commit_stop_task(
                task.id,
                stop_reason=MissionStopReason.ARTIFACT_CONFLICT,
                detail={"error": str(error)},
            )
            await self._release_mission(mission.id)
            self._note(f"task {task.id} stopped: artifact conflict ({error})")
            return True
        bound = self.policy_for(mission.id)  # step 9 (plan D9-4'): the Mission's own version
        role = self._template(role_for_task(task), mission.id)  # D5-9: approach
        role = self._hierarchical_worker_template(role, mission.id)
        untrusted = [str(p) for p in (mission.final_report or {}).get("untrusted_sources", [])]
        # 现行计划网络只读一次：检索按它的目标树排序，父目标与直接上游也读它
        network_error: Exception | None = None
        try:
            network = new_mode.network(mission.id)
        except (GraphIntegrityError, ContractError, StoreError, LookupError) as error:
            network, network_error = None, error
        try:
            knowledge = self._gather_knowledge(mission, task, network)
        except RetrievalUnavailable as error:
            # S4-07 / D4-11': never "no knowledge" — degrade explicitly or block visibly
            count = self.commit.record_retrieval_unavailable(
                task.id, reason=str(error), policy=self._config.on_retrieval_failure
            )
            if self._config.on_retrieval_failure == "degrade":
                knowledge = KnowledgeContext.unavailable(str(error))
                self._note(f"task {task.id}: retrieval unavailable, degraded ({error})")
            else:
                if count >= self._config.max_retrieval_failures:
                    self._commit_stop_task(
                        task.id,
                        stop_reason=MissionStopReason.RETRIEVAL_UNAVAILABLE,
                        detail={"failures": count, "reason": str(error)},
                    )
                    await self._release_mission(mission.id)
                    self._note(f"task {task.id} stopped: retrieval unavailable {count} times")
                else:
                    self._note(f"task {task.id}: retrieval unavailable, blocked ({count})")
                return True
        task_kind = str((mission.final_report or {}).get("task_kind") or "code")
        try:
            decision = self._router_for(mission.id).route(
                role=role.name,
                task_kind=task_kind,
                previous_attempts=attempts,
                unavailable_until=self.commit.unavailable_until(),
                now=self.store.now,
            )
        except RoutingUnavailable as unavailable:
            return await self._defer_for_profile(mission, task, unavailable)
        placeholder = Attempt(
            id=ids.attempt_id(task.id, len(attempts) + 1),
            task_id=task.id,
            mission_id=mission.id,
            role=role.name,
            model=decision.model,
            prompt_version=role.prompt_version,
            context_version="pending",
            budget_reserved=task.budget,
            lease_owner=None,
            lease_expires_at=None,
            status=AttemptStatus.PENDING,
            retry_of=None if previous is None else previous.id,
            created_at=self.store.now,
            version=1,
            ordinal=len(attempts) + 1,
            creation_key="pending",
            input_id="attempt-input",
            task_version=task.version,
            feedback=tuple(feedback),
        )
        untrusted, previous_files, source_binding, source_versions = self._workspace_files_for(
            mission, previous, inputs, untrusted
        )
        # 原计划 §10 第 3 项：父目标与直接上游从分层网络读——这一步所在做法
        # 细化的目标（原文、它负责的要求原文），与数据边上把产出交给它的生产者（目标、状态、已验收
        # 结论的摘要、交到这一步的产物）。``Task.dependency_ids`` 在分层下恒空，不再按它过滤。

        try:
            if network is None:
                raise network_error  # type: ignore[misc]
            step_parent = read_parent_goal(self.store, network, mission, task)
            upstream = upstream_steps(self.store, network, mission, task, inputs)
        except (GraphIntegrityError, ContractError, StoreError, LookupError) as error:
            # 读不到网络时如实说"读不到"，不编一个空的"没有上游"
            self._note(f"task {task.id}: hierarchical context unreadable ({error})")
            step_parent = {"data_not_instruction": True, "unavailable": str(error)[:300]}
            upstream = [{"unavailable": str(error)[:300]}]
        try:
            package = build_worker_package(
                mission,
                task,
                placeholder,
                mission_requirements=current_statements(self.store, mission),
                previous_attempts=attempts,
                verifier_feedback=verifier_feedback,
                workspace_files=previous_files,
                dependencies=upstream,
                knowledge=knowledge,
                untrusted_sources=untrusted,
                role=role.name,
                domain=self.commit.domain_for(mission.id),
                source_versions=source_versions,
            )
        except ContextRejected as error:
            self._commit_stop_task(
                task.id,
                stop_reason=MissionStopReason.CONTEXT_REJECTED,
                detail={"error": str(error)[:300]},
            )
            await self._release_mission(mission.id)
            self._note(f"task {task.id} stopped: worker package refused ({error})")
            return True
        package = self._with_leaf_context(
            mission, task=task, new_mode=new_mode, package=package, step_parent=step_parent
        )
        # D6-7: Mission ∩ Task ∩ Role ∩ Deployment, frozen into the intent below
        # P2.3u: a hierarchical read-only leaf also drops patch/apply-class tools.
        read_only, allowed = self._leaf_tools(mission, task, role, new_mode)
        # D6-8: the Attempt's tool-call cap = the deployment's per-turn cap, narrowed by the
        # Task budget's own dimension; it is reserved up front and enforced at the gateway
        tool_cap = self._config.max_tool_calls_per_turn
        if task.budget.max_tool_calls is not None:
            tool_cap = min(tool_cap, task.budget.max_tool_calls)
        if self._tool_calls_limited(mission, task):
            # review P1-2: never reserve more than the chain can still hold; in-flight
            # reservations are not spending — only a spent dimension is exhaustion
            reservable, spent_room = self._tool_call_room(mission, task)
            if spent_room is not None and spent_room > 0 and reservable is not None:
                if reservable <= 0:
                    self._note(f"task {task.id}: tool calls all reserved in flight; waiting")
                    return False
                tool_cap = min(tool_cap, reservable)
        # 检索次数按账户链还能预留的数给这次尝试；用完不停任务，只让网关拒绝检索
        search_cap = self._search_call_room(mission, task)
        # 规划器批准了（原样重试）上一次尝试的资源申请 → 这次的工具上限加上批准数
        resource_grant = self._resource_grant(mission, task)
        if resource_grant is not None:
            tool_cap = int(resource_grant["base_cap"]) + int(resource_grant["amount"])
        config = AgentConfig(
            name=f"{role.name}-{placeholder.ordinal}",
            instructions=role.instructions,
            model_profile_ref=decision.profile_id,  # the label *is* the pool it runs in
            tool_names=allowed,
            limits=AgentLimits(
                max_model_calls_per_turn=self._config.max_model_calls_per_turn,
                # SDK 单回合上限多留一轮余量，网关按 tool_cap 拒绝并告知
                max_tool_calls_per_turn=tool_cap + TOOL_ANSWER_MARGIN,
                turn_deadline_seconds=min(
                    self._config.turn_deadline_seconds,
                    float(task.budget.max_runtime_seconds or self._config.turn_deadline_seconds),
                ),
            ),
        )
        message = user_message_json(package.text)
        tokens = self._config.attempt_reserve_tokens
        critic_tail = None
        first_critic_binding: dict[str, Any] = {}
        if "critic_review" in task.verification_policy:
            try:
                critic_decision = self._route_service("critic", mission.id)
            except RoutingUnavailable as unavailable:
                return await self._defer_for_profile(mission, task, unavailable)
            first = self._first_critic_budget(critic_decision)
            if isinstance(first, FirstRequestBudget):
                first_reservation = self._first_critic_reservation(first)
                first_critic_binding = {
                    "first_critic_budget": {
                        "provider_input_cap": first.provider_input_cap.to_json(),
                        "output_ceiling": first.output_ceiling,
                        "minimum_tokens": first.minimum_tokens,
                    }
                }
                critic_tail = first_reservation
            else:
                first_critic_binding = {"first_critic_budget_unknown": first.reason}
                critic_tail = self._reservation(self._config.critic_reserve_tokens)
        self._attempts.deferred.pop(task.id, None)
        tokens = self._attempt_reserve_tokens(task, tokens, critic_tail)
        try:
            attempt, _intent = self.commit.create_attempt(
                task.id,
                critic_tail=critic_tail,
                role=role.name,
                model=decision.model,
                prompt_version=role.prompt_version,
                context_version=package.context_version,
                reservation=replace(
                    self._reservation(tokens),
                    tool_calls=tool_cap if self._tool_calls_limited(mission, task) else 0,
                    search_calls=search_cap or 0,
                ),
                runtime_profile_id=decision.profile_id,
                routing=decision.to_json(),
                intent_config={
                    "agent_config": config.to_json(),
                    "message": message,
                    "attempt_id": placeholder.id,
                    "allowed_tools": list(allowed),
                    **self._service_config(decision),
                    "max_tool_calls": tool_cap,
                    **({} if search_cap is None else {"max_search_calls": search_cap}),
                    **({} if resource_grant is None else {"resource_grant": resource_grant}),
                    "context_version": package.context_version,
                    "prompt_version": role.prompt_version,
                    "policy_version_id": self.policy_version_of(mission.id),
                    "task_version": task.version,
                    "role": role.name,
                    **first_critic_binding,
                    "knowledge": knowledge.frozen_ids,  # D4-10: what this Attempt saw
                    "retrieval_version": knowledge.retrieval.version,
                    "retrieval_status": knowledge.retrieval.status,
                    "context_builder_version": CONTEXT_BUILDER_VERSION,
                    "untrusted_sources": untrusted,
                    **({"read_only_leaf": True} if read_only else {}),
                    **source_binding,
                    "allocation": dict(
                        allocation or {}
                    ),  # D5-8': the §29.3 score it was granted on
                },
                input_hash=sha256_hex(message),
                retry_of=placeholder.retry_of,
                feedback=feedback,
                inputs=[item.to_json() for item in inputs],
                max_open_attempts=min(
                    int(bound["mission_concurrency"]), self._config.max_concurrency
                ),
                max_running_attempts=self._config.max_running_attempts,
                context_evidence=context_claims(knowledge, upstream),
            )
        except CommitRejected as error:
            return await self._stop_on_attempt_rejected(mission, task, error)
        except BudgetExhausted as error:
            return await self._stop_on_budget_exhausted(mission, task, error)
        self._note(
            f"attempt {attempt.id} created (retry_of={attempt.retry_of}, inputs={len(inputs)})"
        )
        return True

    def _dispatch_held(
        self, mission: Mission, task: Task, admission: Any, new_mode: HierarchicalDispatch
    ) -> bool:
        """True when this Task must not be dispatched now (each check says why where it applies)."""
        # P2.3b / §18.5 rule 4: before anything else, a compound is refused here with
        # NEEDS_REFINEMENT.  The gate is ``form`` from the semantic binding, not the
        # status string — ``TaskStatus.READY`` on a compound is a rebuildable display
        # index and never a permission to dispatch.
        intercepted = new_mode.intercept_worker_dispatch(mission.id, task.id)
        if intercepted is not None:
            self._note(
                f"task {task.id} not dispatched: {intercepted.reason} "
                f"(occurrence {intercepted.occurrence_id})"
            )
            return True
        # P2.3c part 2 / TG §8.3: the dispatch transaction re-checks.  An
        # ``EligiblePrimitiveTask`` is *not* a capability — it records that a
        # controlled check passed at ``admitted_at_ms`` and grants nothing — so
        # this entry refuses a Task that arrived without one, however it got here.
        # ``_decide`` hands its admission down so the common path does not re-read
        # the whole plan; every other caller (repair, a manual drive)
        # pays for the fresh read rather than skipping the gate.
        if admission is None:
            admission = new_mode.admissions(mission.id).admission_for(task.id)
        # Review F11: ``isinstance`` and not a duck-typed ``gate_passed`` probe.
        # ``EligiblePrimitiveTask.gate_passed`` is guarded by the admission token,
        # but a structural test would let *any* object carrying a true attribute of
        # that name through this door — which is exactly the "admission with a flag"
        # shape ``admissions()`` is written to avoid.
        if not isinstance(admission, EligiblePrimitiveTask) or not admission.gate_passed:
            self._note(
                f"task {task.id} not dispatched: no admission from the readiness gate "
                "(§18.5 constraint 4)"
            )
            return True

        if pending_block(self.store, mission.id) is not None:
            return True

        if (
            retry_decision_required(self.store, mission.id, task.id)
            and pending_retry_permit(self.store, mission.id, task.id) is None
        ):
            return True
        # A Worker already in flight can produce another pending verification.
        # Account for that obligation before creating more work, across Missions.
        active_missions = {item.id for item in self._active_missions()}
        pending_verification = sum(
            stored.envelope.mission_id in active_missions
            for stored in self.store.list_results_by_verification("PENDING", "RUNNING")
        )
        potential_results = sum(
            attempt.mission_id in active_missions
            and self.store.find_result_for_attempt(attempt.id) is None
            for attempt in self.store.list_attempts_by_status(
                "PENDING", "CLAIMED", "RUNNING", "SUBMITTED", "VERIFYING"
            )
        )
        return pending_verification + potential_results >= self._config.max_pending_verifications

    def _attempt_feedback(
        self, mission: Mission, task: Task, attempts: Sequence[Attempt]
    ) -> tuple[Attempt | None, list[str], list[Mapping[str, Any]]]:
        previous = next(
            (a for a in reversed(attempts) if a.status in TERMINAL_ATTEMPT and a.failure), None
        )
        feedback, verifier_feedback = retry_feedback(attempts, previous)
        for event in self.store.list_events(mission.id):  # D7-9': a person's notes, as data
            if event.type == "HumanCommentAdded" and event.payload.get("target_id") in {
                task.id,
                mission.id,
            }:
                feedback.append(
                    f"human note from {event.payload.get('principal_id')} "
                    f"(information, not a permission change): {event.payload.get('text')}"
                )
        return previous, feedback, verifier_feedback

    def _workspace_files_for(
        self,
        mission: Mission,
        previous: Attempt | None,
        inputs: list[UpstreamInput],
        untrusted: list[str],
    ) -> tuple[list[str], list[str], dict[str, Any], Any]:
        seed = dict((mission.final_report or {}).get("workspace_seed", {}))
        source_binding = self._active_source_binding(mission.id)
        source_versions = source_binding.get("source_versions")
        source_paths = set(source_versions or {})
        if source_binding:
            untrusted = sorted(set(untrusted) | source_paths)
        previous_files = sorted({*seed, *(item.path for item in inputs), *source_paths})
        if previous is not None:
            try:
                previous_files = sorted(
                    set(self.assembled.workspaces.get(previous.id).list_files()) | source_paths
                )
            except Exception:  # noqa: BLE001 - 读不到上一轮工作区文件时只用本轮
                logger.debug("previous_workspace_files_failed", exc_info=True)
        if source_binding:
            previous_files = [
                path
                for path in previous_files
                if path in source_paths
                or not _under_source_root(path, source_binding["source_roots"])
            ]
        return untrusted, previous_files, source_binding, source_versions

    def _with_leaf_context(
        self,
        mission: Mission,
        *,
        task: Task,
        new_mode: HierarchicalDispatch,
        package: TaskPackage,
        step_parent: Any,
    ) -> TaskPackage:
        if step_parent is not None:
            # §10 第 3 项的另一半：父目标。与 dependencies（直接上游）一样是数据，不是指令。
            package = _seal({**dict(package.package), "parent_goal": step_parent})
        # P2.3c part 2d, decision 4: tell the leaf which output ports its own
        # occurrence declares.  The names are the plan's, not the model's — the
        # model supplies the *local key* (which file) and nothing else (TG design
        # §3.2).  An empty list means nothing downstream consumes this leaf, and
        # the envelope's ``outputs`` may then be omitted.
        declared_ports = new_mode.declared_output_ports_for(mission.id, task.id)
        if declared_ports:
            package = _seal(
                {
                    **dict(package.package),
                    "declared_output_ports": {
                        "data_not_instruction": True,
                        "version": "declared-output-ports-v1",
                        "ports": [dict(item) for item in declared_ports],
                    },
                }
            )
        # P2.3h: tell the leaf which **root** criteria the plan hangs on it, with
        # the method's own ``evidence_requirement`` for each — the sentence its
        # output at the listed ports has to satisfy, because that output is what
        # the root review reads for that criterion.  Absent when it carries none.
        carried = new_mode.carried_root_criteria_for(mission.id, task.id)
        if carried:
            package = _seal(
                {
                    **dict(package.package),
                    "carried_root_criteria": {
                        "data_not_instruction": True,
                        "version": "carried-root-criteria-v1",
                        "note": (
                            "the root (MISSION_FINAL) review judges each root_criterion_id "
                            "below on this task's accepted output at the listed ports; that "
                            "output must show what evidence_requirement states"
                        ),
                        "criteria": [dict(item) for item in carried],
                    },
                }
            )
        # A step that runs after the final review sent the task back sees what the
        # reviewer said — the findings, verbatim, as data.  What to do about them
        # was the Planner's decision and is in the step's own instructions.
        final_review_findings = self._final_review_findings_for_workers(mission.id)
        if final_review_findings:
            package = _seal(
                {
                    **dict(package.package),
                    "review_feedback": {
                        "data_not_instruction": True,
                        "version": "final-review-feedback-v2",
                        "note": (
                            "the final review of the whole task returned these findings "
                            "before this step was dispatched"
                        ),
                        "findings": final_review_findings,
                    },
                }
            )

        content_scope = task_content_prompt_scope(self.store, mission.id, task.id)
        # The Task contract is a durable document-assessment identity. Keep it
        # byte-equivalent to the committed Task; the separate content scope
        # narrows this turn's responsibility without rewriting that contract.
        return _seal({**dict(package.package), "task_content_scope": content_scope})

    def _leaf_tools(
        self, mission: Mission, task: Task, role: Any, new_mode: HierarchicalDispatch
    ) -> tuple[bool, tuple[str, ...]]:
        read_only = False
        operator_tool_limit = None
        semantic = new_mode.semantics().task_semantics_of(mission.id, task.id)
        read_only = semantic is not None and read_only_leaf(semantic)
        if semantic is not None and semantic.operator_ref is not None:
            operator = semantic.operator_ref
            operator_tool_limit = dict(self._config.deployment_policy.operator_tool_allowlists).get(
                f"{operator.id}@{operator.version}:{operator.content_hash}",
                () if self._config.deployment_policy.require_operator_tool_policy else None,
            )
        allowed = effective_tools(
            mission_tools=mission.allowed_tools,
            task_tools=task.allowed_tools,
            role_tools=(*role.tool_names, *self._config.domain_tools),
            deployment=self._config.deployment_policy,
            read_only_leaf=read_only,
        )
        if operator_tool_limit is not None:
            allowed = tuple(name for name in allowed if name in operator_tool_limit)
        return read_only, allowed

    def _attempt_reserve_tokens(
        self, task: Task, tokens: int, critic_tail: Reservation | None
    ) -> int:
        if self._pressure.is_raised:  # §18.5 "缩小每个 Attempt 预算" (D6-3 ④)
            tokens = max(4_000, int(tokens * self._config.reduced_reserve_ratio))
        if task.budget.max_tokens is not None:
            tokens = min(tokens, max(1, task.budget.max_tokens))
            # step 4: a repair reserves what the Task still has rather than failing on a
            # nominal share it no longer can afford (the reservation is a cap, not a spend)
            with self.store.transaction():
                remaining = self.commit.ledger.account(task_account(task.id)).remaining_tokens()
            if remaining is not None:
                critic_share = (
                    critic_tail.tokens
                    if critic_tail is not None
                    else self._config.critic_reserve_tokens
                    if "critic_review" in task.verification_policy
                    else 0
                )
                head_room = remaining - critic_share  # keep the Critic's own share free
                if 0 < head_room < tokens:
                    tokens = head_room
        return tokens

    async def _stop_on_attempt_rejected(
        self, mission: Mission, task: Task, error: CommitRejected
    ) -> bool:
        if isinstance(error, NonModelFailuresExhausted):
            # 2026-09-28：格式、服务、打断这类不扣次数的失败同一步已到上限——多半是服务
            # 或格式本身有问题，停下这一步并写明，不无限重做。
            self._commit_stop_task(
                task.id,
                stop_reason=MissionStopReason.RUNTIME_UNAVAILABLE,
                detail={
                    "reason": "non_model_failures_exhausted",
                    "failures": error.failure_count,
                    "cap": error.cap,
                },
            )
            await self._release_mission(mission.id)
            self._note(
                f"task {task.id} stopped: non_model_failures_exhausted ({error.failure_count})"
            )
            return True
        self._note(f"task {task.id}: no new attempt ({error})")
        return False

    async def _stop_on_budget_exhausted(
        self, mission: Mission, task: Task, error: BudgetExhausted
    ) -> bool:
        detail = {
            "dimension": error.dimension,
            "requested": error.requested,
            "remaining": error.remaining,
            "account": error.account_id,
        }
        reason = (
            MissionStopReason.MAX_ATTEMPTS_REACHED
            if error.dimension == "attempts"
            else MissionStopReason.BUDGET_EXHAUSTED
        )
        if is_global_account(error.account_id):
            # review P1-1 / D6-1': the deployment-wide pool ran out — no Task and no
            # Mission is to blame; the Mission stops with the Global scope named
            reason = MissionStopReason.BUDGET_EXHAUSTED
            self._commit_fail_mission(
                mission.id, stop_reason=reason, detail={**detail, "scope": "global"}
            )
            self._note(f"mission {mission.id} stopped: {reason} ({error.dimension}, global pool)")
        elif error.account_id == mission_account(mission.id):
            # D3-12': the Mission pool itself is exhausted (any dimension) — no Task
            # is to blame and the stop reason is the pool's: budget_exhausted
            reason = MissionStopReason.BUDGET_EXHAUSTED
            self._commit_fail_mission(
                mission.id, stop_reason=reason, detail={**detail, "scope": "mission"}
            )
            self._note(f"mission {mission.id} stopped: {reason} ({error.dimension}, mission pool)")
        else:
            self._commit_stop_task(task.id, stop_reason=reason, detail=detail)
            self._note(f"task {task.id} stopped: {reason} ({error.dimension})")
        await self._release_mission(mission.id)
        return True

    async def _judge(self, mission: Mission, tasks: Sequence[Task]) -> bool:
        key = judgment_key(tasks)
        cached = self.commit.criteria_judgment(mission.id, key)  # booked only for an arbitration
        evaluated = cached if cached is not None else await self._evaluate_criteria(mission, tasks)
        if evaluated is None:
            return True
        judgments, summary = evaluated
        ruled, created = self._arbitrated(mission, tasks, key, judgments)
        if ruled is None:  # D7-8' ②: a person rules first; the judgment is kept meanwhile
            if cached is None:
                self.commit.record_criteria_judgment(mission.id, key, judgments, summary=summary)
            return created or cached is None
        judged = self.commit.judge_mission(mission.id, judgments=ruled, summary=summary)
        self._note(f"mission {mission.id} judged: {judged.status} ({judged.stop_reason})")
        return True

    async def _evaluate_criteria(
        self, mission: Mission, tasks: Sequence[Task]
    ) -> tuple[list[dict[str, Any]], str] | None:
        """D21 / ORCH §12.4 / D3-9': judge the Mission's own success criteria on the
        *integrated* tree — the seed plus every Task's accepted artifacts applied in
        topological order — independently of the Task PASSes.  ``pytest:`` criteria run
        there, ``file:`` criteria are checked there and free-text criteria restate the
        certified grades of the official MISSION_FINAL review."""

        self._reimport_unsettled(mission)
        # P2.3k / defect N3: the tree is read from the root resolution's own
        # contributions, never from ``Task.dependency_ids`` (a materialised occurrence
        # leaves them empty by design, §18.5 constraint 4).
        new_mode = self._new_mode(mission)
        if new_mode is None:
            raise ContractError("hierarchical_assembly_missing: no judgment without the assembly")
        try:
            merged = self._hierarchical_judgment_inputs(mission, new_mode, tasks)
        except ArtifactConflict as error:
            self._commit_fail_mission(
                mission.id,
                stop_reason=MissionStopReason.ARTIFACT_CONFLICT,
                detail={"error": str(error)},
            )
            self._note(f"mission {mission.id} failed at judgment: {error}")
            return None
        seed = dict((mission.final_report or {}).get("workspace_seed", {}))
        files: dict[str, Path | bytes] = {}
        artifacts: list[Artifact] = []
        try:
            for item in merged:
                artifact = self.store.get_artifact(item.artifact_id)
                if artifact is None:
                    continue
                files[item.path] = read_verified(artifact)  # P3.2 D3: hash re-checked
                artifacts.append(artifact)
            # 2026-10-06 真实模型验收（编程题）：每次尝试的工作区都带着任务的资料，模型写的测试读它们；
            # 任务级判定树却只有产物，pytest 在这里找不到 sources/ 就全红，任务被判"要求未满足"。
            # 判定树同样带现行版本的资料（产物从不写在资料根下，不会覆盖）。
            for path, data in self._current_source_files(mission).items():
                files.setdefault(path, data)
        except ArtifactStoreError as error:
            self._commit_fail_mission(
                mission.id,
                stop_reason=MissionStopReason.ARTIFACT_CONFLICT,
                detail={"error": str(error)},
            )
            self._note(f"mission {mission.id} failed at judgment: {error}")
            return None
        # P0-2: one judgment tree per orchestrator instance — another instance may be
        # running pytest in its own; the judgment Commit itself is idempotent
        view_id = f"{mission.id}-judge-{self._owner}"
        copy = self.assembled.workspaces.integrated_copy(view_id, seed=seed, files=files)
        self._register_copy(
            "judge",
            f"{view_id}-verify",
            mission_id=mission.id,
            attempt_id="",
            detail={"artifacts": sorted(a.id for a in artifacts), "seed": sorted(seed)},
        )
        # 2026-10-11 偏离 5：判定看的是全部验收步骤各自的总结，不是任取一步的

        summaries = []
        for accepted in accepted_tasks(list(tasks)):
            stored = self.store.get_result(accepted.accepted_result_id or "")
            if stored is not None and stored.envelope.summary:
                summaries.append(stored.envelope.summary)
        summary = "\n".join(summaries)
        test_runs: dict[str, dict[str, Any]] = {}
        for criterion in current_statements(self.store, mission):
            if not criterion.startswith("pytest:"):
                continue
            target = criterion.removeprefix("pytest:").strip() or None
            if not self._config.deployment_policy.local_code_execution:
                # host support 0.9.8: a criterion from before the switch is judged unmet —
                # never run on this machine
                test_runs[criterion] = {
                    "passed": False,
                    "error": "local_code_execution is off in this deployment: pytest criteria are not run on this machine",
                    "stdout": "",
                }
                continue
            try:
                if target is not None:
                    copy.resolve(target)
                test_run = await run_pytest(
                    str(copy.root),
                    path=target,
                    timeout=self._config.test_timeout_seconds,
                    executor=self._executor,
                )
                test_runs[criterion] = {**test_run.to_json(), "passed": test_run.passed}
            except Exception as error:  # noqa: BLE001
                logger.debug("evaluate_criteria_resolve_failed", exc_info=True)
                test_runs[criterion] = {"passed": False, "error": str(error), "stdout": ""}
        # The free-text criteria were judged by the official MISSION_FINAL review and
        # restated, certified, on the adopted root resolution; the judgment restates
        # the certified grades (Host real model run 20, 2026-09-23).
        assured_grades = self._assured_root_grades(mission, new_mode)
        judgments: list[dict[str, Any]] = []
        for ordinal, criterion in enumerate(current_statements(self.store, mission)):
            if criterion.startswith("pytest:"):
                outcome = test_runs.get(criterion, {})
                judgments.append(
                    {
                        "criterion": criterion,
                        "met": bool(outcome.get("passed")),
                        "judge": "code_test",
                        "reason": (outcome.get("stdout") or outcome.get("error") or "")[-300:],
                    }
                )
            elif criterion.startswith("file:"):
                relative = criterion.removeprefix("file:")
                try:
                    met = copy.resolve(relative).is_file()
                except Exception:  # noqa: BLE001
                    logger.debug("evaluate_criteria_is_file_failed", exc_info=True)
                    met = False
                judgments.append(
                    {
                        "criterion": criterion,
                        "met": met,
                        "judge": "rule_check",
                        "reason": "file exists" if met else "file missing",
                    }
                )
            elif criterion.startswith(ACTION_PREFIX):
                continue  # D7-7': judged from the action ledger by the caller
            else:
                grade = None if assured_grades is None else assured_grades.get(criterion)
                judgments.append(
                    {
                        "criterion": criterion,
                        "met": grade == "PASS",
                        "judge": "assurance_review",
                        "source": "certified_root_resolution"
                        if grade is not None
                        else "unavailable",
                        "reason": "official MISSION_FINAL review, current root-resolution certificate: "
                        + str(grade)
                        if grade is not None
                        else "no certified root resolution judged this criterion",
                    }
                )
        return judgments, summary

    def _assured_root_grades(self, mission: Mission, new_mode: Any) -> dict[str, str] | None:
        """Criterion statement → certified grade from the adopted root resolutions
        of the Mission; None when they are not formed."""
        if new_mode is None:
            return None

        htn = htn_store.HtnStore(self.store)
        network = new_mode.network(mission.id)
        duties = tuple(dict.fromkeys(str(d) for d in network.required_obligations))
        resolutions = [htn.adopted_goal_resolution(mission.id, duty) for duty in duties]
        if not resolutions or any(item is None for item in resolutions):
            return None
        grades: dict[str, str] = {}
        # 上面已排除含 None 的情况
        for resolution in cast("list[GoalResolution]", resolutions):
            if str(resolution.validity) != "CURRENT" or str(resolution.verdict) != "ACCEPT":
                return None
            requirements = htn.get_requirements_revision(
                mission.id, int(resolution.requirements_version)
            )
            statements = {c.criterion_id: c.statement for c in requirements.criteria}
            for item in resolution.criteria:
                statement = statements.get(item.criterion_id)
                if statement is not None:
                    grades[statement] = str(item.verdict)
        return grades

    async def _decide_actions(self, mission: Mission, tasks: Sequence[Task]) -> bool:
        """D7-7' / D7-5': judgment in two stages.  ① The non-action criteria are judged once
        per integrated tree and put on the books.  ② Only then are the actions looked at: an
        approved (or L0/L1) action is handed off as the *last* step of the judgment, a
        revoked / expired one fails the Mission (a rejected or failed system-prepared action
        is the operation line's: ``system_operations`` resubmits, asks the planner or stops).

        Assurance 1.1 §7.2（2026-10-06 车道 O）: the judgment does **not** wait for every
        required action to be SUCCEEDED.  The business requirement is judged by the root
        resolution; whether an effect really took hold is the closeout's to converge
        (UNKNOWN → BLOCKED_UNKNOWN, in flight → root scope unmet), and only the unique
        final writer completes the Mission.  Once judged, this method keeps running every
        cycle so a later revocation / expiry of an approval still stops the Mission here and
        a handoff-ready action is still handed off; a later rejection / failure of the action
        is the operation line's (``system_operations``: resubmit, ask the planner or a named
        stop), not stopped here.  With nothing to do it answers "no progress" and ``run()``
        goes idle (never a no-progress FAILED)."""

        progressed = False
        key = judgment_key(tasks)
        cached = self.commit.criteria_judgment(mission.id, key)
        if cached is None:
            evaluated = await self._evaluate_criteria(mission, tasks)
            if evaluated is None:
                return True
            self.commit.record_criteria_judgment(
                mission.id, key, evaluated[0], summary=evaluated[1]
            )
            cached = evaluated
            progressed = True
        plain, summary = cached
        ruled, created = self._arbitrated(mission, tasks, key, plain)
        if ruled is None:
            return progressed or created  # a person rules on a Verifier conflict first
        plain = ruled
        if not all(bool(item.get("met")) for item in plain):
            self._judge_with_actions(mission, plain, summary, unmet="another criterion is unmet")
            return True
        self.commit.expire_approvals(mission.id)
        actions = {
            criterion: self.commit.action_for_criterion(mission.id, criterion, self._connectors)
            for criterion in current_statements(self.store, mission)
            if criterion.startswith(ACTION_PREFIX)
        }
        for criterion, action in actions.items():
            state = None if action is None else str(action["state"])
            if action is not None and state in {"REVOKED", "EXPIRED"}:
                self._commit_fail_mission(
                    mission.id,
                    stop_reason=MissionStopReason.APPROVAL_REJECTED,
                    detail={
                        "kind": str(state).lower(),
                        "criterion": criterion,
                        "action_key": action["action_key"],
                    },
                )
                await self._release_mission(mission.id)
                return True
            # Assurance §7.2：系统准备的动作被人拒绝（REJECTED）、没生效（FAILED）、
            # 还没产生（申请单在等审阅 / 批准 / 物化）或被撤销后待重交，都归系统操作那条线
            # （``system_operations`` / ``operation_runtime``：内容换了就替代重交、证实没生效按原内容重交到
            # 上限、否则交规划器一次或具名停下）；判定不再等效果，这里就不第二次判它们，也不判"没有候选"。
            # 撤销 / 过期的批准没有别的接手方，仍在这里停任务。
        waiting = any(
            a is not None and a["state"] not in HANDOFF_READY_STATES | {"SUCCEEDED"}
            for a in actions.values()
        )  # review P2-1: nothing is handed off while another action of the Mission waits
        for criterion, action in actions.items():
            if waiting or action is None or action["state"] not in HANDOFF_READY_STATES:
                continue
            key_ = str(action["action_key"])
            done = await self.actions.hand_off(key_)
            after = self.store.get_action(key_) if done is None else done
            if after is not None and after["state"] in HANDOFF_READY_STATES:
                if handoff_refusal_transient(self.actions.last_refusal.get(key_, "")):
                    continue  # stays handoff-ready; tried again next round (阶段 B 裁决第 5 类)
                # the deployment will not run it (cap, budget, switched off): it cannot happen
                self._commit_fail_mission(
                    mission.id,
                    stop_reason=MissionStopReason.ACTION_FAILED,
                    detail={
                        "criterion": criterion,
                        "action_key": key_,
                        "reason": "handoff_refused:" + self.actions.last_refusal.get(key_, ""),
                    },
                )
                await self._release_mission(mission.id)
                return True
            self._note(
                f"mission {mission.id}: action {key_} → {None if after is None else after['state']}"
            )
            progressed = True
        if progressed:
            return True
        if self.commit.assured_closeout_pending(mission.id):
            # Handoff item 7 / §7.2: judged; the CLOSEOUT consumer converges the effects
            # (BLOCKED_UNKNOWN / DRAINING keep the Mission ACTIVE) and the unique final
            # writer completes it.  Nothing to re-judge: idle, not stalled.
            return False
        # Assurance §7.2：判定不等每个要求动作 SUCCEEDED——业务要求由根结论判定，
        # 效果（发布有没有真的生效）由收尾核对：结果不明 → BLOCKED_UNKNOWN、在途 → 范围未满足，
        # 收敛后才 READY → 完成。
        self._judge_with_actions(mission, plain, summary, unmet=None)
        return True

    def _judge_with_actions(
        self,
        mission: Mission,
        plain: Sequence[Mapping[str, Any]],
        summary: str,
        *,
        unmet: str | None,
    ) -> None:
        by_criterion = {str(item.get("criterion")): dict(item) for item in plain}
        judgments: list[dict[str, Any]] = []
        for ordinal, criterion in enumerate(current_statements(self.store, mission)):
            if not criterion.startswith(ACTION_PREFIX):
                judgments.append(by_criterion[criterion])
                continue
            action = self.commit.action_for_criterion(mission.id, criterion, self._connectors)
            state = None if action is None else str(action["state"])
            # Assurance §7.2：业务要求由根结论判定；这项效果有没有真的生效由
            # 收尾核对（结果不明 → BLOCKED_UNKNOWN、在途 → 范围未满足），完成只由唯一的收尾写方写。
            # 这一行记的是判定时动作走到了哪一步，不是"效果已生效"。
            judgments.append(
                {
                    "criterion": criterion,
                    "met": unmet is None,
                    "judge": "assurance_closeout",
                    "reason": unmet
                    or f"效果由收尾核对后才完成；判定时动作状态：{state or '尚未产生'}",
                    "action_key": None if action is None else action["action_key"],
                    "action_state": state,
                }
            )
        judged = self.commit.judge_mission(mission.id, judgments=judgments, summary=summary)
        self._note(f"mission {mission.id} judged: {judged.status} ({judged.stop_reason})")
