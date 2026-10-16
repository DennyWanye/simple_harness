"""Durable DeepResearch v5 controls, snapshots, and immutable lineage.

The schema for these records is owned by the workflow.db v3 migration.  This
module deliberately contains repository semantics only so an already-created
v3 database never depends on a second same-version DDL pass.
"""

from __future__ import annotations

from .research_repository_domains.controls import ResearchControlMixin
from .research_repository_domains.snapshots import SnapshotLineageMixin
from .research_repository_domains.support import ResearchRepositoryError
from .research_repository_domains.v6_continuations import (
    ContinuationCreateResultV1,
    ResearchReachability,
    V6ContinuationMixin,
)

# 拆分前从本模块导入的名字照旧可用（同名别名表示有意转出）
from .research_repository_domains.v6_continuations import (
    _v6_continuation_identity as _v6_continuation_identity,
)
from .research_repository_domains.v6_snapshots import V6SnapshotMixin

_CONTROL_TERMINAL = {"consumed", "rejected", "expired"}


class ResearchWorkflowRepository(
    ResearchControlMixin, SnapshotLineageMixin, V6SnapshotMixin, V6ContinuationMixin
):
    """Transactional repository for the workflow.db v3 research tables."""


__all__ = [
    "ContinuationCreateResultV1",
    "ResearchReachability",
    "ResearchRepositoryError",
    "ResearchWorkflowRepository",
]
