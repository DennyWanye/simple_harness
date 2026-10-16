"""The single transactional writer for ``companion.db``.

All public mutations start with ``BEGIN IMMEDIATE``.  Idempotent replays verify
the complete immutable input before returning an existing row; they never bump
the owner detail version.
"""

from __future__ import annotations

from .store_domains.build_admission import BuildAdmissionMixin
from .store_domains.candidate_builds import CandidateBuildMixin
from .store_domains.candidates import CandidateMixin
from .store_domains.capability_activation import CapabilityActivationMixin
from .store_domains.capability_mutations import CapabilityMutationMixin

# 拆分前从本模块导入的名字照旧可用（同名别名表示有意转出）
from .store_domains.evaluation_admission import (
    SAFE_STATIC_RUNNER_POLICY_HASH as SAFE_STATIC_RUNNER_POLICY_HASH,
)
from .store_domains.evaluation_admission import EvaluationAdmissionMixin
from .store_domains.evaluation_cases import EvaluationCaseMixin
from .store_domains.evaluation_results import EvaluationResultMixin
from .store_domains.forgetting import GrowthForgettingMixin
from .store_domains.growth_authority import GrowthAuthorityMixin
from .store_domains.growth_events import GrowthEventMixin
from .store_domains.jobs import JobQueueMixin
from .store_domains.outbox import OutboxNotificationMixin
from .store_domains.profiles import ProfileControlMixin
from .store_domains.reminders import ReminderMixin
from .store_domains.runs import RunBindingMixin
from .store_domains.support import canonical_hash as canonical_hash
from .store_domains.support import canonical_json as canonical_json


class CompanionStore(
    ProfileControlMixin,
    GrowthEventMixin,
    BuildAdmissionMixin,
    CandidateBuildMixin,
    CandidateMixin,
    CapabilityActivationMixin,
    JobQueueMixin,
    OutboxNotificationMixin,
    RunBindingMixin,
    EvaluationAdmissionMixin,
    EvaluationCaseMixin,
    EvaluationResultMixin,
    CapabilityMutationMixin,
    GrowthForgettingMixin,
    ReminderMixin,
    GrowthAuthorityMixin,
):
    """Companion growth store composed from the domain mixins in store_domains/."""
