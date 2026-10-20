# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (long event / receipt literals)

"""Module-level definitions of the commit service: rejections, mission specs and account names."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ...contracts import AttemptStatus, Budget
from ...contracts.planning_decisions import PLANNING_DECISION_V1
from ...governance.domains import CODE_DOMAIN
from ...storage.store import StoreError
from ..plan_commits import HIERARCHICAL_SEMANTICS, SEMANTICS_KEY, normalise_semantics
from ..planning_protocol_binding import checked_planning_protocol

#: P2.3k / defect N3.  Appended once per hierarchical Mission when the Mission Judge
#: builds its integrated tree: the former all-ancestors merge (override legal only along
#: ``Task.dependency_ids``, anything else an ``ArtifactConflict``; removed 2026-10-06) is not applied, because
#: in this mode ``dependency_ids`` is empty by design (§18.5 constraint 4) and the rule
#: read every pair of leaves that wrote the same path as "independent branches".  The
#: Grok C3 episodes lost a root ``GoalResolution`` that already stood to exactly that
#: (``MissionFailed{artifact_conflict}`` one event after ``GoalResolutionCommitted``).
#: The tree is read from the resolution's own contributions instead; the record says so
#: and names every path more than one contribution wrote and which writer was kept.
ARTIFACT_MERGE_NOT_APPLICABLE = "ArtifactMergeNotApplicableUnderHierarchical"

#: P2.3c part 2c (review F6).  Appended when ``judge_mission`` is asked to conclude a
#: hierarchical Mission whose root duty carries no adopted ``GoalResolution``.  A new
#: event type for the same reason as the one above: the judgment was not *wrong*, it
#: was asked at a door that does not decide this — in this mode a Mission is completed
#: out of its root resolution and never out of a sweep of Task statuses (§6.3, §8.1).
HIERARCHICAL_JUDGMENT_REFUSED = "HierarchicalJudgmentRefused"

#: P2.3f.  A hierarchical Mission's service intent (Planner, root
#: reviewer, Critic) whose executor turn was left waiting on a Provider hand-off with
#: an *unknown* outcome was handed off once more — same subject, same reservation, a
#: new executor.  The runtime records the unknown invocation honestly and by design
#: settles nothing for it (the request may have reached the model), and nothing in
#: the orchestration loop resolved that wait: the intent stayed SUBMITTED until the
#: caller's deadline.  Durable so the bound ("once") survives a restart and an
#: operator can see which turn was abandoned and which one answered.
SERVICE_INTENT_REHANDED_OFF = "ServiceIntentRehandedOff"

SUBMITTED_STATES = frozenset({AttemptStatus.SUBMITTED, AttemptStatus.VERIFYING})

ACTOR_SYSTEM = "system"
ORCHESTRATOR_ID = "orchestrator"


class CommitRejected(StoreError):
    """The proposal violates a contract, a budget or the state machine; nothing was written."""


class MissionConflict(CommitRejected):
    """Same (tenant, idempotency_key) with a different specification."""


class NonModelFailuresExhausted(CommitRejected):
    """同一步不扣次数的失败（格式、服务、打断）已到上限；什么都没预留。"""

    def __init__(self, task_id: str, failure_count: int, cap: int) -> None:
        self.task_id = task_id
        self.failure_count = failure_count
        self.cap = cap
        super().__init__(
            f"task {task_id} failed {failure_count} times for reasons that are not the model's"
            f" own mistakes (format, service or interruption); cap {cap}"
        )


@dataclass(frozen=True, slots=True)
class MissionSpec:
    """Input of ``create_mission`` (§5: 任务章程)."""

    goal: str
    success_criteria: tuple[str, ...]
    tenant_id: str
    idempotency_key: str
    # 两种计数型停止默认开启，阈值取部署政策（``stop_conditions`` 模块）
    stop_conditions: tuple[str, ...] = (
        "verification_passed",
        "budget_exhausted",
        "no_new_knowledge",
        "result_duplication",
    )
    allowed_tools: tuple[str, ...] = ()
    risk_level: str = "sandbox"
    budget: Budget = field(default_factory=Budget)
    task_kind: str = "code"
    workspace_seed: Mapping[str, str] = field(default_factory=dict)
    untrusted_sources: tuple[str, ...] = ()  # step 4 (D4-12): path prefixes of external content
    domain: str = CODE_DOMAIN  # P3.3 (D1): the domain profile this Mission freezes
    runtime_profile_id: str | None = None
    # 2026-10-01: the default is the hierarchical mode on the one planning protocol.
    # The flat mode was removed on 2026-10-02: naming it is refused here, before any
    # entry (facade, CLI, benchmark runner) can write a row.
    orchestration_semantics_version: str = HIERARCHICAL_SEMANTICS
    planning_protocol_version: str = PLANNING_DECISION_V1
    #: ``action:`` 要求的参数，按要求编号（``c-user-<n>``，按位置）；进章程哈希，第 1 版要求从这里读。
    action_params: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: 执行中可以提议的动作名单（``连接器.操作``）；名单外的提议一律要人批。进章程哈希。
    allowed_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        normalise_semantics(self.orchestration_semantics_version)
        checked_planning_protocol(self.planning_protocol_version)

    @property
    def bound_planning_protocol(self) -> str:
        """The planning protocol this Mission is created under."""

        return self.planning_protocol_version

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "goal": self.goal,
            "success_criteria": list(self.success_criteria),
            "tenant_id": self.tenant_id,
            "idempotency_key": self.idempotency_key,
            "stop_conditions": list(self.stop_conditions),
            "allowed_tools": list(self.allowed_tools),
            "risk_level": self.risk_level,
            "budget": self.budget.to_json(),
            "task_kind": self.task_kind,
            "workspace_seed": dict(self.workspace_seed),
        }
        if self.untrusted_sources:
            data["untrusted_sources"] = list(self.untrusted_sources)
        if self.domain != CODE_DOMAIN:
            # the default must not change ``spec_hash`` — a Host that re-sends the same
            # request after upgrading would otherwise get a MissionConflict
            data["domain"] = self.domain
        if self.runtime_profile_id is not None:
            data["runtime_profile_id"] = self.runtime_profile_id
        if self.action_params:
            data["action_params"] = {key: dict(value) for key, value in self.action_params.items()}
        if self.allowed_actions:
            data["allowed_actions"] = list(self.allowed_actions)
        data[SEMANTICS_KEY] = self.orchestration_semantics_version
        data["planning_protocol_version"] = self.planning_protocol_version
        return data
