# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""``action:`` 要求的写法与系统申请单（candidate）的字段。

申请单只由系统按已批准的效果生成（``system_operations``）；模型不写申请单，Worker 也不再
收到申请单合同（人提交模型写的申请单的入口已删，用户 2026-10-20）。
"""

from __future__ import annotations

from ..contracts.models import ContractError

CANDIDATE_FIELDS = ("connector", "operation", "target", "params", "reason")

# P3.2 D6: what a candidate names (artifact_path) versus what the system binds from the
# Result's own accepted Artifacts — a model may never state the identity of the bytes
BOUND_ARTIFACT_FIELDS = frozenset({"artifact_id", "content_hash", "size", "storage_uri"})

ACTION_PREFIX = "action:"


def parse_action_criterion(criterion: str) -> tuple[str, str, str] | None:
    """``action:<connector>.<operation>:<target>`` (plan D7-7) — ``None`` for any other
    criterion; a malformed action criterion is a contract error."""

    if not criterion.startswith(ACTION_PREFIX):
        return None
    head, sep, target = criterion[len(ACTION_PREFIX) :].strip().partition(":")
    connector, dot, operation = head.partition(".")
    if not (sep and dot and connector.strip() and operation.strip() and target.strip()):
        raise ContractError(
            f"an action criterion is action:<connector>.<operation>:<target>, got {criterion!r}"
        )
    return connector.strip(), operation.strip(), target.strip()


__all__ = (
    "ACTION_PREFIX",
    "BOUND_ARTIFACT_FIELDS",
    "CANDIDATE_FIELDS",
    "parse_action_criterion",
)
