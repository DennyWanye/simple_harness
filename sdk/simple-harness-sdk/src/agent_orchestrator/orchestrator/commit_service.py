# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (long event / receipt literals)

"""Commit Service: the single logical writer of formal orchestration state (§15, §17.5).

Every public method is one ``Store.transaction()`` that applies a checked
proposal, appends the corresponding Events (§16.2) and, where a proposal carries a
base version, records a commit receipt so a replay returns the *same* receipt
instead of applying twice (§17.4).  Agents never call this; only the
Orchestrator, the API and the recovery path do.
"""

from __future__ import annotations

from .action_commits import ActionCommitsMixin
from .budget_accounts import (
    GLOBAL_ACCOUNT_PREFIX,
    Reservation,
    global_account_id,
    is_global_account,
    mission_account,
    task_account,
)
from .commit_service_domains.accounting import AccountingCommitsMixin, cooldown_started_at
from .commit_service_domains.attempt_lifecycle import AttemptLifecycleCommitsMixin
from .commit_service_domains.grading import GradingCommitsMixin
from .commit_service_domains.installs import InstallCommitsMixin
from .commit_service_domains.mission_lifecycle import MissionLifecycleCommitsMixin
from .commit_service_domains.results import ResultCommitsMixin
from .commit_service_domains.support import (
    SERVICE_INTENT_REHANDED_OFF,
    CommitRejected,
    MissionConflict,
    MissionSpec,
    NonModelFailuresExhausted,
)
from .human_commits import HumanCommitsMixin
from .obligation_commits import ObligationCommitsMixin
from .operation_completion import OperationCompletionCommitsMixin
from .operation_materialization import OperationMaterializationCommitsMixin
from .operation_reconciliation import OperationReconciliationCommitsMixin
from .plan_commits import PlanCommitsMixin
from .planning_admission_commits import PlanningAdmissionCommitsMixin
from .policy_commits import PolicyCommitsMixin
from .protected_tail_commits import ProtectedTailCommitsMixin
from .resolution_commits import ResolutionCommitsMixin
from .source_commits import SourceCommitsMixin


class CommitService(
    AccountingCommitsMixin,
    InstallCommitsMixin,
    MissionLifecycleCommitsMixin,
    GradingCommitsMixin,
    AttemptLifecycleCommitsMixin,
    ResultCommitsMixin,
    ProtectedTailCommitsMixin,
    ActionCommitsMixin,
    HumanCommitsMixin,
    PolicyCommitsMixin,
    SourceCommitsMixin,
    ObligationCommitsMixin,
    PlanningAdmissionCommitsMixin,
    PlanCommitsMixin,
    ResolutionCommitsMixin,
    OperationCompletionCommitsMixin,
    OperationMaterializationCommitsMixin,
    OperationReconciliationCommitsMixin,
):  # step 7: the action ledger + approvals half; step 9: the policy registry half; P2.3a: the plan revision half; P2.3c: the accept half
    """The single writer of orchestrator state, composed from one mixin per commit kind."""


__all__ = (
    "CommitRejected",
    "CommitService",
    "GLOBAL_ACCOUNT_PREFIX",
    "MissionConflict",
    "MissionSpec",
    "NonModelFailuresExhausted",
    "Reservation",
    "SERVICE_INTENT_REHANDED_OFF",
    "cooldown_started_at",
    "global_account_id",
    "is_global_account",
    "mission_account",
    "task_account",
)
