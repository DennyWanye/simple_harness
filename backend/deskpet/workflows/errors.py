# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""契约校验失败的错误类型（带稳定错误码）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class WorkflowContractError(ValueError):
    """Base class for stable, machine-readable contract failures."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class WorkflowDefinitionError(WorkflowContractError):
    pass


class InvalidStatePatch(WorkflowContractError):
    pass


__all__ = ["InvalidStatePatch", "WorkflowContractError", "WorkflowDefinitionError"]
