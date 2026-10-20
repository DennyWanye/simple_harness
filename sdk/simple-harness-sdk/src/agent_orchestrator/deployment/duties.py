# SPDX-License-Identifier: Apache-2.0
"""部署每一轮的职责（2026-10-03 从 Host 搬入，HTN 补齐阶段 A′）：

* 自动模式下代用户确认"只有内容"的完成映射；
* 自动模式下代用户签发待签的规划授权；
* 投影保证通道的无损检查策略（每个冻结范围、根终审、做法计划）。

都是秩序，不是判断：确认与授权只在用户选了自动模式时做，并如实记成 ``HOST_AUTO_PERMISSION``；
检查策略是要求书的无损投影，部署不增不减。命令号与批准来源字串是已有回执的身份，原样沿用
Host 的值（Host 测试钉住），改了会让已有任务重复签发。

调用方（Host 或 SDK 测试世界）持有"现在是不是自动模式"这一件事，其余都在这里。

重启核对没通过、已隔离的任务（``Orchestrator.recovery_isolated``，与主循环同一个判断）三样都不做：
本进程不替被隔离任务写任何东西（N3-27）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from hashlib import sha256
from typing import Any

from ..assurance.codec import fingerprint
from ..orchestrator.assurance_check_policy import (
    lossless_planning_subject_mapping,
    lossless_scope_mapping,
    mission_final_scope_id,
)
from ..storage import htn_store
from ..storage.assurance_store import AssuranceStore
from ..storage.taskgraph_binding import require_bound

logger = logging.getLogger(__name__)

TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


class DeploymentDuties:
    """The per-round deployment duties over one authenticated ``MissionControlV1``."""

    def __init__(
        self,
        orchestrator: Any,
        control: Any,
        *,
        tenant_id: str,
        principal: Any,
        wake: Callable[[], None] = lambda: None,
    ) -> None:
        self.orchestrator = orchestrator
        self.control = control
        self.tenant_id = tenant_id
        self.principal = principal
        self.wake = wake
        self.policy_scopes: set[str] = set()  # check policies this deployment approved
        self.policy_warned: set[str] = set()  # METHOD_PLAN projections already reported failing
        self.unassured_missions: set[str] = set()  # Missions outside the assured lane
        self.completion_done: set[str] = set()  # requirements confirmed here (or that must not be)
        self.planning_requests: set[str] = set()  # requests already issued for
        self.action_approvals: set[str] = set()  # action requests the host already granted

    def bind(self, orchestrator: Any, control: Any) -> None:
        """Point the duties at a (re)built Orchestrator lifetime; what was done is kept."""
        self.orchestrator, self.control = orchestrator, control

    @property
    def bound(self) -> bool:
        return self.orchestrator is not None and self.control is not None

    # ---- auto permission -------------------------------------------------------------------

    def auto_confirm_content_completion(self, *, auto: bool) -> int:
        """Auto permission mode: confirm the completion requirements the page shows.

        User decision 2026-09-26: a new Mission waited in CREATED for the person to confirm
        its completion requirements and looked stuck.  The deployment confirms the exact
        mapping the page would send, recorded as ``HOST_AUTO_PERMISSION`` on the principal's
        behalf, never as a person's click; manual mode keeps the button.

        * every required non-``action:`` criterion is content;
        * user decision 2026-10-20: every ``action:`` criterion becomes a required effect
          with its operation's default milestone, owned by the one unsatisfied obligation.
          With more than one obligation the system does not guess: the Mission stays on the
          page and an event says why.

        "An operation" is exactly a criterion with the structured ``action:`` prefix, which
        the main Agent writes when it puts the task together (2026-10-02).
        """
        if not auto or not self.bound:
            return 0
        store = self.orchestrator.store
        confirmed = 0
        rows = store.connection.execute(
            "SELECT mission_id FROM missions WHERE status NOT IN ("
            + ",".join("?" for _ in TERMINAL)
            + ") ORDER BY created_at DESC LIMIT 50",
            tuple(sorted(TERMINAL)),
        ).fetchall()
        unconfirmed = getattr(self.orchestrator, "_requirements_unconfirmed", None)
        for (mission_id,) in rows:
            if self.orchestrator.recovery_isolated(str(mission_id)):
                continue
            mission = store.get_mission(str(mission_id))
            if mission is None:
                continue
            # 只有现行要求还没确认的任务才去读确认页（每轮对每个进行中任务取整份快照太重）
            if unconfirmed is not None and not unconfirmed(mission):
                continue
            try:
                workspace = (self.control.snapshot(mission.id)["snapshot"] or {}).get(
                    "operation_workspace"
                )
            except Exception:  # noqa: BLE001 - 读快照失败时下一轮再试
                logger.debug("auto_confirm_snapshot_failed", exc_info=True)
                continue
            if (
                not isinstance(workspace, Mapping)
                or workspace.get("state") != "CONFIRMATION_REQUIRED"
                or workspace.get("editable") is not True
                or not workspace.get("requirements_ref")
            ):
                continue
            ref = dict(workspace["requirements_ref"])
            key = f"{mission.id}:{ref.get('revision')}:{ref.get('content_hash')}"
            if key in self.completion_done:
                continue
            operations = list(workspace.get("operations") or ())
            actions = {str(item["criterion_id"]) for item in operations}
            content = [
                str(c["id"])
                for c in workspace.get("criteria") or ()
                if c.get("required") is True and str(c["id"]) not in actions
            ]
            effects, why_not = self._auto_effects(workspace, operations)
            if why_not is not None:
                self._defer_auto_confirmation(mission.id, key, why_not)
                continue
            if not content and not effects:
                self.completion_done.add(key)
                continue
            try:
                self.control.approve_operation_completion_spec(
                    {
                        "mission_id": mission.id,
                        "command_id": "host-auto-completion-"
                        + sha256(key.encode()).hexdigest()[:32],
                        "expected_requirements_ref": ref,
                        "proposal": {
                            "schema_version": 1,
                            "mission_id": mission.id,
                            "requirements_ref": {
                                "id": ref["id"],
                                "revision": ref["revision"],
                                "content_hash": ref["content_hash"],
                            },
                            "mode": "REQUIRED_EFFECTS" if effects else "CONTENT_ONLY",
                            "content_criterion_ids": content,
                            "effects": effects,
                        },
                        "approval_source": "HOST_AUTO_PERMISSION",
                    }
                )
            except Exception:  # noqa: BLE001 - one refusal must not block the others
                logger.exception("auto completion confirmation failed for %s", mission.id)
                continue
            self.completion_done.add(key)
            confirmed += 1
        if confirmed:
            self.wake()
        return confirmed

    @staticmethod
    def _auto_effects(
        workspace: Mapping[str, Any], operations: list[Any]
    ) -> tuple[list[dict[str, Any]], str | None]:
        """每条 action: 要求一个必须完成的效果：挂在唯一的未满足义务上、取它的档案默认里程碑。
        返回（效果，做不了时的原因）。"""

        if not operations:
            return [], None
        obligations = list(workspace.get("obligations") or ())
        if len(obligations) != 1:
            return [], f"auto confirmation needs a person: {len(obligations)} obligations"
        effects = []
        for item in operations:
            chosen = next(
                (
                    m
                    for m in item.get("milestones") or ()
                    if m.get("id") == item.get("default_milestone")
                ),
                None,
            )
            if chosen is None:
                return [], f"no profile can verify {item['connector']}.{item['operation']}"
            key = f"effect-{item['criterion_id']}"
            effects.append(
                {
                    "effect_key": key,
                    "source_slot_key": key,
                    "obligation_id": str(obligations[0]["id"]),
                    "criterion_ids": [str(item["criterion_id"])],
                    "required_milestone": str(chosen["id"]),
                    "milestone_policy_ref": dict(chosen["milestone_policy_ref"]),
                    "evidence_policy_ref": dict(chosen["evidence_policy_ref"]),
                }
            )
        return effects, None

    def _defer_auto_confirmation(self, mission_id: str, key: str, reason: str) -> None:
        """自动确认做不了：写一条事件说明为什么（每版要求一次），任务停在确认页等人。"""

        if key in self.completion_done:
            return
        self.completion_done.add(key)
        commit = self.orchestrator.commit
        try:
            with self.orchestrator.store.transaction():
                commit._emit(
                    "OperationCompletionNeedsPerson",
                    mission_id,
                    key="auto-confirm:" + key,
                    payload={"mission_id": mission_id, "reason": reason},
                )
        except Exception:  # noqa: BLE001 - 说明写不进去不挡别的任务
            logger.exception("auto confirmation deferral event failed for %s", mission_id)

    def auto_authorize_planning(self, *, auto: bool) -> int:
        """Auto permission mode: issue each pending planning authority for the person.

        Planning authority lets the planner plan this round; it approves no effect, review
        or operation (those keep their own cards).  The grant is recorded as
        ``HOST_AUTO_PERMISSION`` on the principal's behalf — never as a person's click.
        """
        if not auto or not self.bound:
            return 0
        issued = 0
        try:
            pending = self.control.pending_planning_authorizations()
        except Exception:  # noqa: BLE001 - never stops the loop; retried next round
            logger.exception("pending planning authority read failed")
            return 0
        for row in pending:
            request_id = str(row["request_id"])
            if request_id in self.planning_requests or self.orchestrator.recovery_isolated(
                str(row["mission_id"])
            ):
                continue
            try:
                self.control.planning_authorization(
                    {
                        "operation": "issue",
                        "mission_id": str(row["mission_id"]),
                        "request_id": request_id,
                        "command_id": f"host-auto-planning:{request_id}",
                        "approval_source": "HOST_AUTO_PERMISSION",
                    }
                )
            except Exception:  # noqa: BLE001 - one refusal must not block the others
                logger.exception("auto planning authority failed for %s", request_id)
                continue
            self.planning_requests.add(request_id)
            issued += 1
        if issued:
            self.wake()
        return issued

    def auto_approve_actions(self, *, auto: bool) -> int:
        """自动模式：主机代批等审批的动作（用户 2026-10-20：auto 模式系统自动审批）。

        代批如实记成 ``HOST_AUTO_PERMISSION``，事件执行者是主机，不是人。应用标了"总要人批"
        的操作（``person_required``）留给人；资料换版本等别的审批也留给人。"""

        if not auto or not self.bound:
            return 0
        granted = 0
        try:
            pending = self.control.approvals()
        except Exception:  # noqa: BLE001 - never stops the loop; retried next round
            logger.exception("pending approvals read failed")
            return 0
        for row in pending:
            request_id = str(row.get("request_id"))
            if (
                row.get("kind") != "action"
                or row.get("state") != "PENDING"
                or row.get("person_required")
                or request_id in self.action_approvals
                or self.orchestrator.recovery_isolated(str(row.get("mission_id")))
            ):
                continue
            try:
                self.control.host_auto_approve_action(request_id)
            except Exception:  # noqa: BLE001 - one refusal must not block the others
                logger.exception("auto action approval failed for %s", request_id)
                continue
            self.action_approvals.add(request_id)
            granted += 1
        if granted:
            self.wake()
        return granted

    # ---- check policies --------------------------------------------------------------------

    def project_check_policies(self, mission_id: str | None = None) -> int:
        """Approve the lossless check-policy mapping for every frozen completion Scope of an
        assured Mission that has none, plus its final and operation policies and the
        METHOD_PLAN policy of every goal.

        The person already confirmed the requirements mapping (the completion Spec); each
        Scope the plan later freezes needs its per-Scope check policy before its content
        review can run (``CHECK_POLICY_UNRESOLVED`` otherwise).  The mapping is the SDK's
        lossless projection of the original requirements (semantic criteria → SEMANTIC,
        named checks → the exact registered CheckSpecs); nothing is added or dropped.  One
        command per Scope, replay-safe through the SDK's own approval receipt.
        """

        if not self.bound:
            return 0
        orchestrator = self.orchestrator
        store = orchestrator.store
        done = self.policy_scopes
        tenant_id = self.tenant_id
        principal_id = self.principal.principal_id
        sql = "SELECT mission_id, scope_id, document_json FROM operation_completion_scopes"
        args: tuple[Any, ...] = ()
        if mission_id is not None:
            sql += " WHERE mission_id=?"
            args = (mission_id,)
        rows = store.connection.execute(sql + " ORDER BY created_at_ms, scope_id", args).fetchall()

        def approve(mid: str, scope_id: str, purpose: str, effect_key: str | None = None) -> bool:
            key = {
                "CONTENT": scope_id,
                "MISSION_FINAL": f"mission-final:{scope_id}",
                "ACTION_PROPOSAL": f"action-proposal:{scope_id}",
                "OPERATION_OUTCOME": f"operation-outcome:{scope_id}:{effect_key}",
            }[purpose]
            if key in done:
                return False
            command_id = f"host-check-policy:{key}"
            receipt_id = "assurance-check-policy-approval:" + fingerprint(
                {
                    "mission": mid,
                    "tenant": tenant_id,
                    "principal": principal_id,
                    "command": command_id,
                }
            )
            if store.get_receipt(receipt_id) is not None:
                done.add(key)
                return False
            try:
                requirements_ref, scope_ref, mapping = lossless_scope_mapping(
                    orchestrator.commit,
                    mission_id=mid,
                    scope_id=scope_id,
                    purpose=purpose,
                    effect_key=effect_key,
                )
                command = {
                    "mission_id": mid,
                    "command_id": command_id,
                    "requirements_ref": requirements_ref.to_json(),
                    "completion_scope": scope_ref.to_json(),
                    "candidate_mapping": [policy.to_json() for policy in mapping],
                    "approval_source": "HOST_LOSSLESS_AUTO",
                }
                if purpose != "CONTENT":
                    command["purpose"] = purpose
                if effect_key is not None:
                    command["effect_key"] = effect_key
                self.control.approve_assurance_check_policy(command)
            except Exception as error:  # noqa: BLE001 - one Scope must never stop the round
                logger.warning(
                    "assurance %s check policy not projected for %s: %s: %s",
                    purpose,
                    scope_id,
                    type(error).__name__,
                    error,
                )
                return False
            done.add(key)
            return True

        approved = 0
        assured: set[str] = set()
        unassured = self.unassured_missions
        for row in rows:
            mid, scope_id = str(row[0]), str(row[1])
            if mid in unassured or orchestrator.recovery_isolated(mid):
                continue
            if mid not in assured:
                mission = store.get_mission(mid)
                if (
                    mission is None
                    or str(getattr(mission.status, "value", mission.status)) in TERMINAL
                ):
                    unassured.add(mid)  # a finished Mission needs no new policy
                    continue

                try:
                    # A global scan skips an older unbound Mission of a development library
                    # (``require_bound`` raises); the loop's contract gate stops it by name.
                    require_bound(store, mid)
                    if AssuranceStore(store).lane(mid) != "ASSURANCE_1_1":
                        unassured.add(mid)
                        continue
                except Exception:  # noqa: BLE001 - unbound, or without a lane row: not assured
                    unassured.add(mid)
                    continue
                assured.add(mid)
            approved += approve(mid, scope_id, "CONTENT")
            owned = tuple(json.loads(row[2]).get("owned_effect_keys") or ())
            if owned:
                approved += approve(mid, scope_id, "ACTION_PROPOSAL")
                for effect_key in owned:
                    approved += approve(mid, scope_id, "OPERATION_OUTCOME", str(effect_key))
        for mid in sorted(assured):
            try:
                if AssuranceStore(store).lane(mid) != "ASSURANCE_1_1":
                    continue
            except Exception:  # noqa: BLE001 - 读不到保证通道时按未保证处理
                logger.debug("assurance_lane_read_failed", exc_info=True)
                continue
            root_scope = mission_final_scope_id(orchestrator, mid)
            if root_scope is not None:
                approved += approve(mid, root_scope, "MISSION_FINAL")
        approved += self._project_method_plan_policies(mission_id)
        return approved

    def _project_method_plan_policies(self, mission_id: str | None) -> int:
        """Approve the METHOD_PLAN check policy for every goal a method may be proposed for.

        2026-10-01: the Planner proposes its own methods, and a proposed method is adopted
        only after its independent review.  That review is prepared inside the Planner
        decision's own transaction, where this projector cannot run, so the policy has to
        be there *before* the Planner is asked: from Mission creation for the root goal, and
        from the commit of the plan that introduced it for a sub-goal.  The policy is
        approved on the exact goal Task (its contract revision and hash), by the SDK's own
        lossless mapping of the requirements that goal covers.
        """

        orchestrator = self.orchestrator
        store = orchestrator.store
        done = self.policy_scopes
        unassured = self.unassured_missions
        sql = (
            "SELECT mission_id FROM missions WHERE status NOT IN ('COMPLETED','FAILED','CANCELLED')"
        )
        args: tuple[Any, ...] = ()
        if mission_id is not None:
            sql += " AND mission_id=?"
            args = (mission_id,)
        approved = 0
        for (mid,) in store.connection.execute(
            sql + " ORDER BY created_at, mission_id", args
        ).fetchall():
            mid = str(mid)
            if mid in unassured or orchestrator.recovery_isolated(mid):
                continue
            try:
                if AssuranceStore(store).lane(mid) != "ASSURANCE_1_1":
                    unassured.add(mid)
                    continue
            except Exception:  # noqa: BLE001 - a Mission without a lane row is not assured
                unassured.add(mid)
                continue
            htn = htn_store.HtnStore(store)
            requirements = htn.latest_requirements_revision(mid)
            # The policy is approved against one requirements revision; a revised requirements
            # document needs its own approval, so the revision is part of what "done" means.
            revision = 0 if requirements is None else int(requirements.revision)
            for binding in htn.list_task_semantics(mid, form="compound"):
                key = f"method-plan:{binding.task_id}:{int(binding.contract_revision)}:r{revision}"
                if key in done:
                    continue
                try:
                    requirements_ref, subject_ref, mapping = lossless_planning_subject_mapping(
                        orchestrator.commit, mission_id=mid, task_id=str(binding.task_id)
                    )
                    self.control.approve_assurance_check_policy(
                        {
                            "mission_id": mid,
                            "command_id": f"host-check-policy:{key}",
                            "requirements_ref": requirements_ref.to_json(),
                            "planning_subject": subject_ref.to_json(),
                            "candidate_mapping": [policy.to_json() for policy in mapping],
                            "purpose": "METHOD_PLAN",
                            "approval_source": "HOST_LOSSLESS_AUTO",
                        }
                    )
                except Exception as error:  # noqa: BLE001 - one goal must never stop the round
                    if key not in self.policy_warned:  # retried every round; said once
                        self.policy_warned.add(key)
                        logger.warning(
                            "assurance METHOD_PLAN check policy not projected for %s: %s: %s",
                            binding.task_id,
                            type(error).__name__,
                            error,
                        )
                    continue
                done.add(key)
                approved += 1
        return approved


__all__ = ("DeploymentDuties",)
