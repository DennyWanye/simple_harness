"""One-shot reader for frozen pre-general-agent execution records.

New roots and children must never consult historical UI mode/session state.
The workflow recovery bootstrap may call this adapter only after it has found
an already-persisted legacy ``code_complex@v1`` run.  The adapter projects the
old project binding into the frozen workflow context and does not register or
recreate a mode manager.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping


async def resolve_frozen_legacy_workspace(
    *,
    row: Mapping[str, Any],
    start_payload: Mapping[str, Any],
    session_db: Any | None,
) -> str | None:
    """Resolve the workspace for an existing frozen legacy run only."""

    workflow_name = str(row.get("workflow_name") or "")
    if workflow_name != "code_complex":
        value = start_payload.get("workspace_ref")
        return str(value) if value else None

    explicit = start_payload.get("workspace_ref")
    if explicit:
        return str(Path(str(explicit)).expanduser().resolve(strict=False))

    session_ref = start_payload.get("session_ref")
    if isinstance(session_ref, Mapping):
        legacy_root = session_ref.get("project_root")
        if legacy_root:
            return str(Path(str(legacy_root)).expanduser().resolve(strict=False))

    if session_db is None:
        return None
    session_id = str(row.get("session_id") or "")
    rows = await session_db.list_code_sessions()
    for item in rows:
        if str(item.get("base_session_id") or "") != session_id:
            continue
        project_root = item.get("project_root")
        if project_root:
            return str(
                Path(str(project_root)).expanduser().resolve(strict=False)
            )
    return None


__all__ = ["resolve_frozen_legacy_workspace"]
