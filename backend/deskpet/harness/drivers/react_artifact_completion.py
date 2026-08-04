"""Same-Run completion latch for single-output artifact creation tools."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from deskpet.execution.contracts import OutcomeStatus, thaw_json
from deskpet.workflows.effects import (
    NormalizedToolOutcome,
    PreparedToolCall,
    ToolOutcomeState,
    target_reservation_key,
)

from .react_boundary import ReactCommandBoundary


_SINGLE_ARTIFACT_CREATE_SUFFIXES: Mapping[str, str] = MappingProxyType(
    {
        "doc_create": ".docx",
        "excel_create": ".xlsx",
        "pdf_export": ".pdf",
        "ppt_create": ".pptx",
        "ppt_pro": ".pptx",
    }
)


def artifact_completion_slot(
    call: PreparedToolCall, *, session_id: str
) -> tuple[str, str] | None:
    """Identify one user-visible create target within the current Run.

    Registry-generated output paths contain the provider call id, so two
    semantically identical create attempts otherwise look unrelated. Fold
    those paths into one default slot. Explicit paths retain their target
    identity, which keeps intentionally requested variants possible.
    """

    suffix = _SINGLE_ARTIFACT_CREATE_SUFFIXES.get(call.tool_name)
    if suffix is None or len(call.prepared_targets) != 1:
        return None
    target = call.prepared_targets[0]
    safe_session = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in session_id
    )
    safe_call = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in call.stable_call_id
    )
    generated_name = f"{call.tool_name}-{safe_session}-{safe_call}{suffix}"
    if Path(target.final_path).name.casefold() == generated_name.casefold():
        return f"{call.tool_name}:default", target.final_path
    return (
        f"{call.tool_name}:target:{target.reservation_key}",
        target.final_path,
    )


def remember_artifact_completion(
    boundary: ReactCommandBoundary,
    *,
    call: PreparedToolCall,
    outcome: NormalizedToolOutcome,
    status: OutcomeStatus,
    metadata: Mapping[str, Any],
) -> ReactCommandBoundary:
    """Persist the first verified result for one artifact output slot."""

    if (
        status is not OutcomeStatus.SUCCEEDED
        or outcome.state is not ToolOutcomeState.SUCCESS
    ):
        return boundary
    identified = artifact_completion_slot(call, session_id=boundary.session_id)
    if identified is None:
        return boundary
    slot, target_path = identified
    state = copy.deepcopy(dict(boundary.completion_state))
    latches = dict(state.get("artifact_completion_latches") or {})
    latches.setdefault(
        slot,
        {
            "slot": slot,
            "tool_name": call.tool_name,
            "original_call_id": call.stable_call_id,
            "target_path": target_path,
            "outcome": outcome.to_dict(),
            "status": status.value,
            "metadata": thaw_json(dict(metadata)),
        },
    )
    state["artifact_completion_latches"] = latches
    return replace(boundary, completion_state=state)


def replay_artifact_completions(
    *,
    session_id: str,
    calls: tuple[PreparedToolCall, ...],
    completion_state: Mapping[str, Any],
) -> tuple[
    tuple[NormalizedToolOutcome | None, ...],
    tuple[OutcomeStatus | None, ...],
    tuple[Mapping[str, Any], ...],
]:
    """Return verified prior outcomes and leave corrupt entries executable."""

    outcomes: list[NormalizedToolOutcome | None] = [None] * len(calls)
    statuses: list[OutcomeStatus | None] = [None] * len(calls)
    metadata: list[Mapping[str, Any]] = [{} for _call in calls]
    latches = completion_state.get("artifact_completion_latches")
    if not isinstance(latches, Mapping):
        return tuple(outcomes), tuple(statuses), tuple(metadata)
    for index, call in enumerate(calls):
        identified = artifact_completion_slot(call, session_id=session_id)
        if identified is None:
            continue
        slot, _target_path = identified
        raw_latch = latches.get(slot)
        if not isinstance(raw_latch, Mapping):
            continue
        original_call_id = str(raw_latch.get("original_call_id") or "")
        target_path = str(raw_latch.get("target_path") or "")
        if (
            str(raw_latch.get("slot") or "") != slot
            or str(raw_latch.get("tool_name") or "") != call.tool_name
            or not original_call_id
            or not target_path
        ):
            continue
        suffix = _SINGLE_ARTIFACT_CREATE_SUFFIXES[call.tool_name]
        if not target_path.casefold().endswith(suffix):
            continue
        if slot.endswith(":default"):
            safe_session = "".join(
                character if character.isalnum() or character in "-_" else "_"
                for character in session_id
            )
            safe_original_call = "".join(
                character if character.isalnum() or character in "-_" else "_"
                for character in original_call_id
            )
            expected_name = (
                f"{call.tool_name}-{safe_session}-"
                f"{safe_original_call}{suffix}"
            )
            if Path(target_path).name.casefold() != expected_name.casefold():
                continue
        elif (
            target_reservation_key(target_path)
            != call.prepared_targets[0].reservation_key
        ):
            continue
        raw_outcome = raw_latch.get("outcome")
        try:
            outcome = (
                NormalizedToolOutcome.from_dict(raw_outcome)
                if isinstance(raw_outcome, Mapping)
                else None
            )
            status = OutcomeStatus(str(raw_latch.get("status") or ""))
        except (TypeError, ValueError, KeyError):
            continue
        if (
            outcome is None
            or outcome.state is not ToolOutcomeState.SUCCESS
            or status is not OutcomeStatus.SUCCEEDED
        ):
            continue
        original_metadata = raw_latch.get("metadata")
        if not isinstance(original_metadata, Mapping):
            continue
        outcome_value = thaw_json(outcome.value)
        reported_paths: list[str] = []
        if isinstance(outcome_value, Mapping):
            for path_key in ("path", "output_path"):
                value = outcome_value.get(path_key)
                if isinstance(value, str) and value:
                    reported_paths.append(value)
            artifacts = outcome_value.get("artifacts")
            if isinstance(artifacts, (list, tuple)):
                reported_paths.extend(
                    str(artifact["path"])
                    for artifact in artifacts
                    if isinstance(artifact, Mapping)
                    and isinstance(artifact.get("path"), str)
                    and artifact.get("path")
                )
        if reported_paths:
            target_key = target_reservation_key(target_path)
            if any(
                target_reservation_key(path) != target_key
                for path in reported_paths
            ):
                continue
        replay_metadata = thaw_json(dict(original_metadata))
        replay_metadata["artifact_completion_latch"] = {
            "state": "reused",
            "slot": slot,
            "original_call_id": original_call_id,
            "target_path": target_path,
            "message": (
                "This artifact was already created in the current task. "
                "Use the existing result and finish the response; do not "
                "create another copy unless the user explicitly requested "
                "a distinct output path."
            ),
        }
        outcomes[index] = outcome
        statuses[index] = OutcomeStatus.SUCCEEDED
        metadata[index] = replay_metadata
    return tuple(outcomes), tuple(statuses), tuple(metadata)


__all__ = [
    "artifact_completion_slot",
    "remember_artifact_completion",
    "replay_artifact_completions",
]
