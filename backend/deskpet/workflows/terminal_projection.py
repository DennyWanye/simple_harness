# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Versioned, fail-closed workflow terminal projections.

Only values returned by a registered projector may cross from graph state into
``workflow.final``.  The registry key deliberately includes a capability name
so adding another public projection never silently widens this contract.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .contracts import JsonValue, WorkflowContext, validate_json_value
from .errors import InvalidStatePatch
from .progress import (
    DEEP_RESEARCH_DIAGNOSTIC_CODES,
    DEEP_RESEARCH_RETRY_ACTION_IDS,
    DEEP_RESEARCH_SKIPPED_STAGE_IDS,
    DEEP_RESEARCH_TERMINAL_METRIC_KEYS,
)


TERMINAL_PUBLIC_CAPABILITY = "terminal_public"
TERMINAL_COMMIT_CAPABILITY = "terminal_commit"
TerminalProjector = Callable[[object, str], dict[str, JsonValue] | None]
AsyncTerminalCommitProjector = Callable[
    [Mapping[str, JsonValue], WorkflowContext], Awaitable[dict[str, JsonValue]]
]
AsyncTerminalCommitRequestFactory = Callable[..., Mapping[str, JsonValue]]


def parse_terminal_blob_ref(value: object) -> str:
    """Parse the workflow-generic content-addressed wire reference."""

    raw = str(value or "")
    if not raw.startswith("sha256:"):
        raise InvalidStatePatch(
            "invalid_terminal_blob_ref", "terminal blob ref must use sha256:<digest>"
        )
    digest = raw[7:]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise InvalidStatePatch(
            "invalid_terminal_blob_ref", "terminal blob digest must be lowercase sha256"
        )
    return digest


@dataclass(frozen=True, slots=True)
class WorkflowActionContext:
    engine_status: str
    brief_committed: bool = False
    delivery_status: str | None = None
    active_control_status: str | None = None
    snapshot_available: bool = False


class VersionedActionMatrix:
    """Fail-closed action policy keyed by immutable workflow version."""

    _ACTIONS = {
        ("deep_research", "v4"): frozenset({"retry_from_start"}),
        ("deep_research", "v5"): frozenset(
            {"generate_now", "continue_research", "retry_from_start", "cancel_settle"}
        ),
        ("deep_research", "v6"): frozenset(
            {"generate_now", "continue_research", "cancel_settle"}
        ),
    }

    def allows(
        self,
        workflow_name: str,
        workflow_version: str,
        action_id: str,
        context: WorkflowActionContext,
    ) -> bool:
        key = (str(workflow_name), str(workflow_version))
        if action_id not in self._ACTIONS.get(key, frozenset()):
            return False
        if key == ("deep_research", "v4"):
            return action_id == "retry_from_start" and context.engine_status == "failed"
        if action_id == "generate_now":
            return context.engine_status == "running" and context.brief_committed
        if action_id == "continue_research":
            return (
                context.engine_status == "completed"
                and context.delivery_status in {"partial", "insufficient_evidence"}
                and context.snapshot_available
            )
        if action_id == "retry_from_start":
            return context.engine_status == "failed"
        if action_id == "cancel_settle":
            return (
                context.engine_status == "running"
                and context.active_control_status in {"accepted", "observed"}
            )
        return False


class TerminalProjectionRegistry:
    """Immutable-by-key registry for bounded public terminal schemas."""

    def __init__(self) -> None:
        self._projectors: dict[tuple[str, str, str], TerminalProjector] = {}

    def register(
        self,
        workflow_name: str,
        workflow_version: str,
        capability: str,
        projector: TerminalProjector,
    ) -> None:
        key = (str(workflow_name), str(workflow_version), str(capability))
        if not all(key) or key in self._projectors:
            raise ValueError(f"terminal projection already registered or invalid: {key!r}")
        self._projectors[key] = projector

    def project(
        self,
        *,
        workflow_name: str,
        workflow_version: str,
        capability: str,
        raw: object,
        engine_status: str,
    ) -> dict[str, JsonValue] | None:
        key = (str(workflow_name), str(workflow_version), str(capability))
        projector = self._projectors.get(key)
        if projector is None:
            raise InvalidStatePatch(
                "invalid_terminal_public",
                "terminal_public is reserved for a supported deep_research version; "
                "projection schema/version/capability is not registered",
            )
        result = projector(raw, str(engine_status))
        if result is not None:
            validate_json_value(result, path="$.terminal_public")
        return copy.deepcopy(result)


@dataclass(frozen=True, slots=True)
class AsyncTerminalCommitCapability:
    workflow_name: str
    workflow_version: str
    capability: str
    request_factory: AsyncTerminalCommitRequestFactory
    projector: AsyncTerminalCommitProjector


class AsyncTerminalCommitProjectionRegistry:
    """Async manifest projector registry, separate from legacy public schemas."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], AsyncTerminalCommitCapability] = {}

    def register(
        self,
        workflow_name: str,
        workflow_version: str,
        capability: str,
        projector: AsyncTerminalCommitProjector,
        *,
        request_factory: AsyncTerminalCommitRequestFactory,
    ) -> None:
        name = str(workflow_name).strip()
        version = str(workflow_version).strip()
        selected_capability = str(capability).strip()
        key = (name, version)
        if (
            not all((*key, selected_capability))
            or key in self._entries
            or not callable(projector)
            or not callable(request_factory)
        ):
            raise ValueError(f"async terminal projection already registered or invalid: {key!r}")
        self._entries[key] = AsyncTerminalCommitCapability(
            workflow_name=name,
            workflow_version=version,
            capability=selected_capability,
            request_factory=request_factory,
            projector=projector,
        )

    def get(
        self, workflow_name: str, workflow_version: str
    ) -> AsyncTerminalCommitCapability | None:
        return self._entries.get((str(workflow_name), str(workflow_version)))

    async def project(
        self,
        *,
        workflow_name: str,
        workflow_version: str,
        capability: str,
        request: Mapping[str, JsonValue],
        context: WorkflowContext,
    ) -> dict[str, JsonValue]:
        key = (str(workflow_name), str(workflow_version))
        entry = self._entries.get(key)
        if entry is None or entry.capability != str(capability):
            raise InvalidStatePatch(
                "invalid_terminal_commit",
                "async terminal commit capability is not registered",
            )
        result = await entry.projector(copy.deepcopy(dict(request)), context)
        validate_json_value(result, path="$.terminal_commit_projection")
        return copy.deepcopy(result)


def project_deep_research_v4(raw: object, _engine_status: str) -> dict[str, JsonValue] | None:
    """The pre-registry v4 validator, retained byte-for-byte in output shape."""

    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise InvalidStatePatch("invalid_terminal_public", "terminal_public must be an object")
    allowed_root = {"metrics", "diagnostic_codes", "skipped_stage_ids", "retry_action_id"}
    required_root = {"metrics", "diagnostic_codes", "skipped_stage_ids"}
    if not required_root.issubset(raw) or any(str(key) not in allowed_root for key in raw):
        raise InvalidStatePatch("invalid_terminal_public", "terminal_public has an invalid schema")

    raw_metrics = raw.get("metrics")
    if not isinstance(raw_metrics, Mapping) or any(
        str(key) not in DEEP_RESEARCH_TERMINAL_METRIC_KEYS for key in raw_metrics
    ):
        raise InvalidStatePatch("invalid_terminal_public", "terminal_public metrics are invalid")
    metrics: dict[str, JsonValue] = {}
    for key, value in raw_metrics.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000:
            raise InvalidStatePatch("invalid_terminal_public", "terminal_public metric value is invalid")
        metrics[str(key)] = value

    raw_diagnostics = raw.get("diagnostic_codes")
    if (
        not isinstance(raw_diagnostics, Sequence)
        or isinstance(raw_diagnostics, (str, bytes))
        or len(raw_diagnostics) > 16
    ):
        raise InvalidStatePatch("invalid_terminal_public", "terminal diagnostics are invalid")
    diagnostics = [str(value) for value in raw_diagnostics]
    if any(value not in DEEP_RESEARCH_DIAGNOSTIC_CODES for value in diagnostics):
        raise InvalidStatePatch("invalid_terminal_public", "terminal diagnostic code is invalid")

    raw_skipped = raw.get("skipped_stage_ids")
    if (
        not isinstance(raw_skipped, Sequence)
        or isinstance(raw_skipped, (str, bytes))
        or len(raw_skipped) > 7
    ):
        raise InvalidStatePatch("invalid_terminal_public", "terminal skipped stages are invalid")
    skipped = [str(value) for value in raw_skipped]
    if len(skipped) != len(set(skipped)) or any(
        value not in DEEP_RESEARCH_SKIPPED_STAGE_IDS for value in skipped
    ):
        raise InvalidStatePatch("invalid_terminal_public", "terminal skipped stage is invalid")

    result: dict[str, JsonValue] = {
        "metrics": metrics,
        "diagnostic_codes": diagnostics,
        "skipped_stage_ids": skipped,
    }
    if "retry_action_id" in raw:
        retry_action = str(raw.get("retry_action_id") or "")
        if retry_action not in DEEP_RESEARCH_RETRY_ACTION_IDS:
            raise InvalidStatePatch("invalid_terminal_public", "terminal retry action is invalid")
        result["retry_action_id"] = retry_action
    return result


def _bounded_count(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000:
        raise InvalidStatePatch("invalid_terminal_public", f"{field} is invalid")
    return value


def project_deep_research_v5(raw: object, engine_status: str) -> dict[str, JsonValue]:
    """Validate the v5 business result without changing the engine terminal."""

    if engine_status != "completed":
        raise InvalidStatePatch(
            "invalid_terminal_public",
            "v5 research delivery may only project from a completed engine terminal",
        )
    if not isinstance(raw, Mapping):
        raise InvalidStatePatch("invalid_terminal_public", "v5 terminal_public must be an object")
    allowed = {"delivery_status", "quality_summary", "coverage_summary", "action_matrix"}
    if "delivery_status" not in raw or any(str(key) not in allowed for key in raw):
        raise InvalidStatePatch("invalid_terminal_public", "v5 terminal_public has an invalid schema")
    delivery_status = raw.get("delivery_status")
    if delivery_status not in {"completed", "partial", "insufficient_evidence"}:
        raise InvalidStatePatch("invalid_terminal_public", "v5 delivery_status is invalid")
    result: dict[str, JsonValue] = {"delivery_status": str(delivery_status)}

    if "quality_summary" in raw:
        quality = raw.get("quality_summary")
        if not isinstance(quality, Mapping) or set(quality) != {
            "score", "passed", "hard_failure_count"
        }:
            raise InvalidStatePatch("invalid_terminal_public", "v5 quality_summary is invalid")
        score = quality.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 100:
            raise InvalidStatePatch("invalid_terminal_public", "v5 quality score is invalid")
        if not isinstance(quality.get("passed"), bool):
            raise InvalidStatePatch("invalid_terminal_public", "v5 quality passed flag is invalid")
        result["quality_summary"] = {
            "score": float(score),
            "passed": bool(quality["passed"]),
            "hard_failure_count": _bounded_count(
                quality.get("hard_failure_count"), "quality_summary.hard_failure_count"
            ),
        }

    if "coverage_summary" in raw:
        coverage = raw.get("coverage_summary")
        keys = {"covered", "partial", "insufficient", "not_applicable", "total"}
        if not isinstance(coverage, Mapping) or set(coverage) != keys:
            raise InvalidStatePatch("invalid_terminal_public", "v5 coverage_summary is invalid")
        normalized = {key: _bounded_count(coverage.get(key), f"coverage_summary.{key}") for key in keys}
        if sum(normalized[key] for key in keys - {"total"}) != normalized["total"]:
            raise InvalidStatePatch("invalid_terminal_public", "v5 coverage total is inconsistent")
        result["coverage_summary"] = normalized

    if "action_matrix" in raw:
        actions = raw.get("action_matrix")
        if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)) or len(actions) > 4:
            raise InvalidStatePatch("invalid_terminal_public", "v5 action_matrix is invalid")
        normalized_actions: list[JsonValue] = []
        seen: set[str] = set()
        enabled_actions: set[str] = set()
        for item in actions:
            if not isinstance(item, Mapping) or set(item) != {"action_id", "enabled"}:
                raise InvalidStatePatch("invalid_terminal_public", "v5 action entry is invalid")
            action_id = str(item.get("action_id") or "")
            if action_id not in {"generate_now", "continue_research", "retry_from_start", "cancel_settle"}:
                raise InvalidStatePatch("invalid_terminal_public", "v5 action id is invalid")
            if action_id in seen or not isinstance(item.get("enabled"), bool):
                raise InvalidStatePatch("invalid_terminal_public", "v5 action entry is invalid")
            seen.add(action_id)
            if bool(item["enabled"]):
                enabled_actions.add(action_id)
            normalized_actions.append({"action_id": action_id, "enabled": bool(item["enabled"])})
        if "continue_research" in enabled_actions and delivery_status == "completed":
            raise InvalidStatePatch("invalid_terminal_public", "completed delivery cannot continue")
        result["action_matrix"] = normalized_actions

    return result


def build_default_terminal_projection_registry() -> TerminalProjectionRegistry:
    registry = TerminalProjectionRegistry()
    registry.register("deep_research", "v4", TERMINAL_PUBLIC_CAPABILITY, project_deep_research_v4)
    registry.register("deep_research", "v5", TERMINAL_PUBLIC_CAPABILITY, project_deep_research_v5)
    return registry


def build_default_async_terminal_commit_registry() -> AsyncTerminalCommitProjectionRegistry:
    registry = AsyncTerminalCommitProjectionRegistry()

    async def _project_v6(
        request: Mapping[str, JsonValue], context: WorkflowContext
    ) -> dict[str, JsonValue]:
        # Lazy import keeps the generic workflow kernel independent from the
        # DeepResearch definition package at import time.
        from .definitions.deep_research_v6_delivery import project_v6_terminal_commit

        return await project_v6_terminal_commit(request, context)

    def _request_v6(**values: object) -> Mapping[str, JsonValue]:
        from .definitions.deep_research_v6_delivery import build_terminal_commit_request

        return build_terminal_commit_request(**values).to_json()  # type: ignore[arg-type]

    registry.register(
        "deep_research", "v6", TERMINAL_COMMIT_CAPABILITY, _project_v6,
        request_factory=_request_v6,
    )
    return registry


__all__ = [
    "AsyncTerminalCommitCapability",
    "AsyncTerminalCommitProjectionRegistry",
    "TERMINAL_COMMIT_CAPABILITY",
    "TERMINAL_PUBLIC_CAPABILITY",
    "TerminalProjectionRegistry",
    "VersionedActionMatrix",
    "WorkflowActionContext",
    "build_default_async_terminal_commit_registry",
    "build_default_terminal_projection_registry",
    "parse_terminal_blob_ref",
    "project_deep_research_v4",
    "project_deep_research_v5",
]
