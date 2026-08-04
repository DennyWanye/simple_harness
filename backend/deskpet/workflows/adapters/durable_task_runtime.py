"""Production ports for ``durable_task@v1``."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..definitions.code_nodes import TaskSessionRefV1
from .code_runtime import (  # noqa: F401
    ProposalPort,
    ToolDispatchPort,
    capability_snapshot,
    derive_effect_id,
)


def workflow_session_ref(
    *,
    session_id: str,
    task_scope_id: str,
    delivery_session_id: str,
    workspace_root: str | Path,
    session_epoch: int = 0,
    task_epoch: int = 0,
) -> TaskSessionRefV1:
    """Build the mode-free identity persisted by new durable task runs."""

    normalized_root = str(
        Path(workspace_root).expanduser().resolve(strict=False)
    )
    workspace_hash = hashlib.sha256(
        normalized_root.casefold().encode("utf-8")
    ).hexdigest()
    return TaskSessionRefV1(
        session_id=session_id,
        task_scope_id=task_scope_id,
        delivery_session_id=delivery_session_id,
        workspace_hash=workspace_hash,
        session_epoch=session_epoch,
        task_epoch=task_epoch,
    )

__all__ = [
    "ProposalPort",
    "ToolDispatchPort",
    "capability_snapshot",
    "derive_effect_id",
    "workflow_session_ref",
]
