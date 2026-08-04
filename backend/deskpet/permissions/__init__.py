# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S20 permission gate package.

Re-exports PermissionGate + PermissionGateConfig for convenience.
"""

from .gate import PermissionGate, PermissionGateConfig

__all__ = ["PermissionGate", "PermissionGateConfig"]
"""DeskPet authorization policies and task-scoped grants."""

from .policy import AuthorizationPolicy, AuthorizationPolicyState
from deskpet.types.task_grants import ResourceSelector, TaskGrant

__all__ = [
    "AuthorizationPolicy",
    "AuthorizationPolicyState",
    "ResourceSelector",
    "TaskGrant",
]
