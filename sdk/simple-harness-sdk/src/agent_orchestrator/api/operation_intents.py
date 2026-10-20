# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Operation status reads bound to the existing authenticated facade caller.

申请单只由系统按已批准的效果准备（``system_operations``）；人提交模型写的申请单的入口已删
（用户 2026-10-20）。"""

from __future__ import annotations

from typing import Any

from ..contracts.semantic_base import identifier
from ..governance.permissions import Principal


class OperationIntentApi:
    def __init__(self, orchestrator: Any, *, tenant_id: str, principal: Principal) -> None:
        if not isinstance(principal, Principal) or principal.kind != "human":
            raise ValueError("operation status requires an authenticated human caller")
        self._orchestrator = orchestrator
        self._tenant = identifier(tenant_id, "tenant_id")
        self._principal = principal

    def status(self, intent_id: str) -> dict[str, Any]:
        return self._orchestrator.commit.operation_intent_status(
            identifier(intent_id, "intent_id"),
            tenant_id=self._tenant,
            principal=self._principal,
        )


__all__ = ("OperationIntentApi",)
