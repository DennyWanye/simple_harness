# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Transactional capability-pack lifecycle orchestrator."""

from __future__ import annotations

from .manager_domains.core import (
    CapabilityEnvironmentPreparer,
    CapabilityLayout,
    CapabilityPublisher,
    FoundationEnvironmentPreparer,
    FoundationPublisher,
    PublishReconciliation,
)
from .manager_domains.install import InstallMixin
from .manager_domains.publishing import PublishMixin
from .manager_domains.recovery import RecoveryMixin
from .manager_domains.removal import RemovalMixin
from .manager_domains.support import (
    CapabilityCandidate,
    CapabilityInstallResult,
    CapabilityManagerError,
    CapabilityPublication,
    CapabilityRehydrateFailure,
    CapabilityRollbackResult,
    CapabilityUninstallResult,
    _operation_id,
)

# 拆分前从本模块导入的名字照旧可用（同名别名表示有意转出）
from .store import CapabilityOperationRecord as CapabilityOperationRecord


def capability_operation_id(idempotency_key: str) -> str:
    """Return the Manager-owned stable operation identity for one request."""

    return _operation_id(idempotency_key)


class CapabilityPackManager(PublishMixin, InstallMixin, RemovalMixin, RecoveryMixin):
    """Run the fixed package lifecycle with durable phase intents."""


__all__ = [
    "CapabilityRehydrateFailure",
    "CapabilityCandidate",
    "CapabilityEnvironmentPreparer",
    "CapabilityInstallResult",
    "CapabilityLayout",
    "CapabilityManagerError",
    "CapabilityPackManager",
    "CapabilityPublication",
    "CapabilityPublisher",
    "CapabilityRollbackResult",
    "CapabilityUninstallResult",
    "FoundationEnvironmentPreparer",
    "FoundationPublisher",
    "PublishReconciliation",
    "capability_operation_id",
]
