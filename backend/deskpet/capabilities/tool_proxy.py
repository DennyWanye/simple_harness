# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""ToolRegistry-facing proxy for process-isolated local capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from ..workflows.effects import NormalizedToolOutcome
from .brokered_planner import BrokeredEffectPlanner
from .effect_plan import BrokeredEffectPlanRecord, EffectPlanValidationError
from .input_views import InputBindingSnapshot, InputViewRequest, InputViewResolver
from .local_runtime import LocalRuntimeRequest, LocalToolRuntime


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, list):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class LocalToolDefinition:
    name: str
    argv: tuple[str, ...]
    execution_profile: Literal["native-adapter", "brokered-effect-v1"]
    tool_spec_fingerprint: str
    timeout_seconds: float = 30.0
    generated: bool = False

    def __post_init__(self) -> None:
        if not self.argv or isinstance(self.argv, (str, bytes)):
            raise ValueError("local tool argv must be an argv tuple")
        if self.generated and self.execution_profile != "brokered-effect-v1":
            raise ValueError("generated tools must use brokered-effect-v1")


@dataclass(frozen=True, slots=True)
class BrokeredPlanEnvelope:
    value: Any
    artifacts: tuple[Any, ...]
    observations: tuple[Any, ...]
    input_snapshot: InputBindingSnapshot
    plan_record: BrokeredEffectPlanRecord

    DURABLE_KIND = "brokered_effect_plan"
    DURABLE_SCHEMA_VERSION = 1

    def to_signal_envelope(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "artifacts": list(self.artifacts),
            "observations": list(self.observations),
            "input_snapshot_ref": self.input_snapshot.snapshot_ref,
            "plan_ref": self.plan_record.plan_ref,
            "plan_hash": self.plan_record.plan_hash,
        }

    def to_durable_envelope(self) -> dict[str, Any]:
        """Return the host-only deferred payload persisted by the effect UoW."""

        return {
            "__deskpet_deferred__": {
                "kind": self.DURABLE_KIND,
                "schema_version": self.DURABLE_SCHEMA_VERSION,
                "value": self.value,
                "artifacts": list(self.artifacts),
                "observations": list(self.observations),
                "input_snapshot": self.input_snapshot.to_mapping(),
                "plan_record": self.plan_record.to_mapping(),
            }
        }

    @classmethod
    def from_durable_value(cls, value: Any) -> "BrokeredPlanEnvelope | None":
        if not isinstance(value, Mapping) or set(value) != {"__deskpet_deferred__"}:
            return None
        payload = value["__deskpet_deferred__"]
        expected = {
            "kind",
            "schema_version",
            "value",
            "artifacts",
            "observations",
            "input_snapshot",
            "plan_record",
        }
        if (
            not isinstance(payload, Mapping)
            or set(payload) != expected
            or payload.get("kind") != cls.DURABLE_KIND
            or payload.get("schema_version") != cls.DURABLE_SCHEMA_VERSION
            or not isinstance(payload.get("artifacts"), (list, tuple))
            or not isinstance(payload.get("observations"), (list, tuple))
            or not isinstance(payload.get("input_snapshot"), Mapping)
            or not isinstance(payload.get("plan_record"), Mapping)
        ):
            raise EffectPlanValidationError(
                "durable brokered plan envelope shape is invalid"
            )
        snapshot = InputBindingSnapshot.from_mapping(payload["input_snapshot"])
        record = BrokeredEffectPlanRecord.from_mapping(payload["plan_record"])
        if (
            record.input_snapshot_ref != snapshot.snapshot_ref
            or record.root_run_id != snapshot.root_run_id
        ):
            raise EffectPlanValidationError(
                "durable brokered plan envelope binding changed"
            )
        record.assert_integrity(snapshot)
        return cls(
            value=_thaw_json(payload["value"]),
            artifacts=tuple(_thaw_json(payload["artifacts"])),
            observations=tuple(_thaw_json(payload["observations"])),
            input_snapshot=snapshot,
            plan_record=record,
        )


class LocalToolProxy:
    """Compute a local result; brokered effects remain host-side plans."""

    def __init__(
        self,
        *,
        runtime: LocalToolRuntime,
        input_resolver: InputViewResolver,
        brokered_planner: BrokeredEffectPlanner,
    ) -> None:
        self.runtime = runtime
        self.input_resolver = input_resolver
        self.brokered_planner = brokered_planner

    async def invoke(
        self,
        definition: LocalToolDefinition,
        args: Mapping[str, Any],
        *,
        input_requests: Sequence[InputViewRequest],
        workspace_roots: Sequence[str],
        temp_dir: str,
        root_run_id: str,
        run_id: str,
        effect_id: str,
        parent_call_id: str,
        provider_call_id: str,
        task_grant_id: str,
        catalog_stamp: Mapping[str, Any],
    ) -> NormalizedToolOutcome | BrokeredPlanEnvelope:
        snapshot: InputBindingSnapshot | None = None
        if input_requests or definition.execution_profile == "brokered-effect-v1":
            try:
                snapshot = self.input_resolver.resolve(
                    input_requests,
                    root_run_id=root_run_id,
                    workspace_roots=workspace_roots,
                )
            except (OSError, TypeError, ValueError) as exc:
                return NormalizedToolOutcome.failure("input_view_invalid", str(exc))

        worker_args = dict(args)
        if definition.execution_profile == "brokered-effect-v1":
            if snapshot is None:  # pragma: no cover - guarded by the branch above
                return NormalizedToolOutcome.malformed(
                    "brokered-effect-v1 requires an input snapshot"
                )
            # Paths supplied to the public tool are not forwarded.  Generated
            # code sees only immutable, opaque views materialized by the host.
            worker_args = {"input_snapshot": snapshot.worker_payload()}
        request = LocalRuntimeRequest(
            tool=definition.name,
            args=worker_args,
            root_run_id=root_run_id,
            run_id=run_id,
            effect_id=effect_id,
            workspace_roots=()
            if definition.execution_profile == "brokered-effect-v1"
            else tuple(workspace_roots),
            temp_dir=""
            if definition.execution_profile == "brokered-effect-v1"
            else temp_dir,
        )
        try:
            runtime_result = await self.runtime.execute(
                definition.argv,
                request,
                timeout_seconds=definition.timeout_seconds,
            )
        except (OSError, TypeError, ValueError) as exc:
            return NormalizedToolOutcome.failure("local_tool_runtime_failed", str(exc))
        if runtime_result.status == "malformed":
            return NormalizedToolOutcome.malformed(
                runtime_result.error_message or "local tool returned malformed output"
            )
        if runtime_result.status != "success" or runtime_result.response is None:
            return NormalizedToolOutcome.failure(
                runtime_result.error_code or "local_tool_failed",
                runtime_result.error_message or "local tool failed",
                value={
                    "exit_code": runtime_result.exit_code,
                    "stderr": runtime_result.stderr,
                    "lease_id": runtime_result.lease_id,
                },
            )

        response = runtime_result.response
        if definition.execution_profile == "native-adapter":
            if "effect_plan" in response:
                return NormalizedToolOutcome.malformed(
                    "native-adapter response must not include effect_plan"
                )
            return NormalizedToolOutcome.success(
                {
                    "value": response.get("value"),
                    "artifacts": response.get("artifacts", []),
                    "observations": response.get("observations", []),
                }
            )

        if "effect_plan" not in response:
            return NormalizedToolOutcome.malformed(
                "brokered-effect-v1 response must include effect_plan"
            )
        if snapshot is None:  # pragma: no cover - guarded before worker launch
            return NormalizedToolOutcome.malformed(
                "brokered-effect-v1 requires an input snapshot"
            )
        try:
            record = self.brokered_planner.validate_and_record(
                response["effect_plan"],
                snapshot=snapshot,
                root_run_id=root_run_id,
                parent_call_id=parent_call_id,
                provider_call_id=provider_call_id,
                tool_spec_fingerprint=definition.tool_spec_fingerprint,
                task_grant_id=task_grant_id,
                catalog_stamp=catalog_stamp,
            )
        except EffectPlanValidationError as exc:
            return NormalizedToolOutcome.failure("effect_plan_rejected", str(exc))
        return BrokeredPlanEnvelope(
            value=response.get("value"),
            artifacts=tuple(response.get("artifacts", [])),
            observations=tuple(response.get("observations", [])),
            input_snapshot=snapshot,
            plan_record=record,
        )
