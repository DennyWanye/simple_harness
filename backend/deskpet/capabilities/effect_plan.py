# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Strict schema for compute-only, host-brokered side effects."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Mapping, Sequence

from .input_views import InputBindingSnapshot

_SOURCE_REF = re.compile(r"^(input|action):([0-9]+)$")


class EffectPlanValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RenameFileAction:
    kind: Literal["rename_file"]
    source_ref: str
    target_name: str

    def to_mapping(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source_ref": self.source_ref,
            "target_name": self.target_name,
        }


@dataclass(frozen=True, slots=True)
class EffectPlan:
    actions: tuple[RenameFileAction, ...]

    @classmethod
    def parse(
        cls, value: Any, *, max_actions: int = 256
    ) -> "EffectPlan":
        if not isinstance(value, Mapping):
            raise EffectPlanValidationError("effect_plan must be an object")
        if set(value) != {"actions"}:
            raise EffectPlanValidationError("effect_plan only accepts the actions field")
        raw_actions = value["actions"]
        if not isinstance(raw_actions, list):
            raise EffectPlanValidationError("effect_plan.actions must be an array")
        if not raw_actions:
            raise EffectPlanValidationError("effect_plan.actions must not be empty")
        if len(raw_actions) > max_actions:
            raise EffectPlanValidationError(
                f"effect_plan exceeds the {max_actions} action limit"
            )

        actions: list[RenameFileAction] = []
        for index, raw in enumerate(raw_actions):
            if not isinstance(raw, Mapping):
                raise EffectPlanValidationError(f"action {index} must be an object")
            if set(raw) != {"kind", "source_ref", "target_name"}:
                raise EffectPlanValidationError(
                    f"action {index} has unsupported or missing fields"
                )
            if raw["kind"] != "rename_file":
                raise EffectPlanValidationError(
                    f"action {index} kind {raw['kind']!r} is not host-mappable"
                )
            source_ref = raw["source_ref"]
            if not isinstance(source_ref, str):
                raise EffectPlanValidationError(f"action {index} source_ref must be text")
            match = _SOURCE_REF.fullmatch(source_ref)
            if match is None:
                raise EffectPlanValidationError(f"action {index} has an invalid source_ref")
            if match.group(1) == "action" and int(match.group(2)) >= index:
                raise EffectPlanValidationError(
                    f"action {index} may only depend on an earlier action"
                )
            target_name = raw["target_name"]
            cls._validate_target_name(target_name, index=index)
            actions.append(
                RenameFileAction(
                    kind="rename_file",
                    source_ref=source_ref,
                    target_name=target_name,
                )
            )
        return cls(tuple(actions))

    @staticmethod
    def _validate_target_name(value: Any, *, index: int) -> None:
        if not isinstance(value, str) or not value or value in {".", ".."}:
            raise EffectPlanValidationError(
                f"action {index} target_name must be a non-empty basename"
            )
        if value != value.strip() or "\x00" in value:
            raise EffectPlanValidationError(
                f"action {index} target_name has unsafe whitespace or NUL"
            )
        if "/" in value or "\\" in value or ":" in value:
            raise EffectPlanValidationError(
                f"action {index} target_name must not contain a path"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {"actions": [action.to_mapping() for action in self.actions]}


@dataclass(frozen=True, slots=True)
class BrokeredEffectPlanRecord:
    plan_ref: str
    root_run_id: str
    parent_call_id: str
    provider_call_id: str
    tool_spec_fingerprint: str
    task_grant_id: str
    catalog_stamp: Mapping[str, Any]
    input_snapshot_ref: str
    plan_hash: str
    actions: tuple[Mapping[str, Any], ...]
    action_receipt_refs: tuple[str | None, ...]
    next_action_index: int
    status: Literal["validated", "running", "succeeded", "failed", "cancelled"]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "actions", tuple(copy.deepcopy(dict(action)) for action in self.actions)
        )
        object.__setattr__(self, "catalog_stamp", copy.deepcopy(dict(self.catalog_stamp)))
        if len(self.actions) != len(self.action_receipt_refs):
            raise ValueError("each action needs one receipt slot")
        if not 0 <= self.next_action_index <= len(self.actions):
            raise ValueError("next_action_index is out of range")
        if self.status not in {
            "validated",
            "running",
            "succeeded",
            "failed",
            "cancelled",
        }:
            raise ValueError("brokered plan status is invalid")
        completed = self.action_receipt_refs[: self.next_action_index]
        future = self.action_receipt_refs[self.next_action_index + 1 :]
        if any(item is None for item in completed) or any(
            item is not None for item in future
        ):
            raise ValueError("brokered plan receipt frontier is invalid")
        if self.status == "validated" and (
            self.next_action_index != 0
            or any(item is not None for item in self.action_receipt_refs)
        ):
            raise ValueError("validated brokered plan cannot contain receipts")
        if self.status == "running" and not (
            0 < self.next_action_index < len(self.actions)
        ):
            raise ValueError("running brokered plan needs a partial receipt frontier")
        if self.status == "succeeded" and (
            self.next_action_index != len(self.actions)
            or any(item is None for item in self.action_receipt_refs)
        ):
            raise ValueError("succeeded brokered plan needs every receipt")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "plan_ref": self.plan_ref,
            "root_run_id": self.root_run_id,
            "parent_call_id": self.parent_call_id,
            "provider_call_id": self.provider_call_id,
            "tool_spec_fingerprint": self.tool_spec_fingerprint,
            "task_grant_id": self.task_grant_id,
            "catalog_stamp": copy.deepcopy(dict(self.catalog_stamp)),
            "input_snapshot_ref": self.input_snapshot_ref,
            "plan_hash": self.plan_hash,
            "actions": [copy.deepcopy(dict(action)) for action in self.actions],
            "action_receipt_refs": list(self.action_receipt_refs),
            "next_action_index": self.next_action_index,
            "status": self.status,
        }

    def assert_integrity(self, snapshot: InputBindingSnapshot) -> None:
        if (
            self.input_snapshot_ref != snapshot.snapshot_ref
            or self.root_run_id != snapshot.root_run_id
        ):
            raise EffectPlanValidationError(
                "brokered plan input snapshot binding changed"
            )
        payload = {
            "plan": {"actions": [dict(action) for action in self.actions]},
            "snapshot_fingerprint": snapshot.fingerprint,
            "task_grant_id": self.task_grant_id,
            "catalog_stamp": dict(self.catalog_stamp),
            "tool_spec_fingerprint": self.tool_spec_fingerprint,
        }
        expected_hash = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        expected_ref = (
            f"brokered-plan:{self.root_run_id}:{self.parent_call_id}:"
            f"{expected_hash[:24]}"
        )
        if self.plan_hash != expected_hash or self.plan_ref != expected_ref:
            raise EffectPlanValidationError(
                "brokered plan identity fingerprint changed"
            )

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any]
    ) -> "BrokeredEffectPlanRecord":
        expected = {
            "plan_ref",
            "root_run_id",
            "parent_call_id",
            "provider_call_id",
            "tool_spec_fingerprint",
            "task_grant_id",
            "catalog_stamp",
            "input_snapshot_ref",
            "plan_hash",
            "actions",
            "action_receipt_refs",
            "next_action_index",
            "status",
        }
        actions = value.get("actions")
        receipts = value.get("action_receipt_refs")
        if (
            set(value) != expected
            or not isinstance(value.get("catalog_stamp"), Mapping)
            or not isinstance(actions, (list, tuple))
            or not all(isinstance(item, Mapping) for item in actions)
            or not isinstance(receipts, (list, tuple))
        ):
            raise EffectPlanValidationError(
                "durable brokered plan record shape is invalid"
            )
        # Re-parse actions on restore so a modified continuation cannot add a
        # host operation that the original strict planner never accepted.
        parsed = EffectPlan.parse({"actions": [dict(item) for item in actions]})
        return cls(
            plan_ref=str(value["plan_ref"]),
            root_run_id=str(value["root_run_id"]),
            parent_call_id=str(value["parent_call_id"]),
            provider_call_id=str(value["provider_call_id"]),
            tool_spec_fingerprint=str(value["tool_spec_fingerprint"]),
            task_grant_id=str(value["task_grant_id"]),
            catalog_stamp=dict(value["catalog_stamp"]),
            input_snapshot_ref=str(value["input_snapshot_ref"]),
            plan_hash=str(value["plan_hash"]),
            actions=tuple(action.to_mapping() for action in parsed.actions),
            action_receipt_refs=tuple(
                None if item is None else str(item) for item in receipts
            ),
            next_action_index=int(value["next_action_index"]),
            status=str(value["status"]),  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class BrokeredCommandBoundary:
    parent_index: int
    parent_prepared_call_ref: str
    provider_call_id: str
    plan_ref: str
    current_inner_call_ref: str | None
    aggregate_value_ref: str | None
    status: Literal["deferred_pending", "running_actions", "terminal"]
    input_snapshot: InputBindingSnapshot
    plan_record: BrokeredEffectPlanRecord
    value: Any = None
    artifacts: tuple[Any, ...] = ()
    observations: tuple[Any, ...] = ()
    parent_outcome_metadata: Mapping[str, Any] = field(default_factory=dict)
    current_inner_call: Mapping[str, Any] | None = None
    current_inner_context: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.parent_index < 0:
            raise ValueError("brokered parent index must be non-negative")
        if not all(
            (
                self.parent_prepared_call_ref,
                self.provider_call_id,
                self.plan_ref,
            )
        ):
            raise ValueError("brokered boundary identities are required")
        if self.plan_ref != self.plan_record.plan_ref:
            raise ValueError("brokered boundary plan_ref mismatch")
        if (
            self.input_snapshot.snapshot_ref
            != self.plan_record.input_snapshot_ref
            or self.input_snapshot.root_run_id != self.plan_record.root_run_id
        ):
            raise ValueError("brokered boundary input snapshot mismatch")
        try:
            self.plan_record.assert_integrity(self.input_snapshot)
        except EffectPlanValidationError as exc:
            raise ValueError(str(exc)) from exc
        has_inner = self.current_inner_call is not None
        if has_inner != (self.current_inner_context is not None):
            raise ValueError("brokered inner call and context must be paired")
        if has_inner != (self.current_inner_call_ref is not None):
            raise ValueError("brokered inner call ref must match its payload")
        if has_inner and str(
            self.current_inner_call.get("stable_call_id")  # type: ignore[union-attr]
        ) != self.current_inner_call_ref:
            raise ValueError("brokered inner call identity changed")
        if self.status == "running_actions" and not has_inner:
            raise ValueError("running brokered boundary needs an inner call")
        if self.status != "running_actions" and has_inner:
            raise ValueError("only a running brokered boundary may hold an inner call")
        if self.status == "terminal" and self.plan_record.status not in {
            "succeeded",
            "failed",
            "cancelled",
        }:
            raise ValueError("terminal brokered boundary needs a terminal plan")
        for item in (self.value, list(self.artifacts), list(self.observations)):
            try:
                json.dumps(item, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError("brokered aggregate data must be JSON-safe") from exc
        object.__setattr__(self, "artifacts", tuple(copy.deepcopy(self.artifacts)))
        object.__setattr__(
            self, "observations", tuple(copy.deepcopy(self.observations))
        )
        object.__setattr__(
            self,
            "parent_outcome_metadata",
            MappingProxyType(copy.deepcopy(dict(self.parent_outcome_metadata))),
        )
        if self.current_inner_call is not None:
            object.__setattr__(
                self,
                "current_inner_call",
                MappingProxyType(copy.deepcopy(dict(self.current_inner_call))),
            )
            object.__setattr__(
                self,
                "current_inner_context",
                MappingProxyType(copy.deepcopy(dict(self.current_inner_context or {}))),
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "parent_index": self.parent_index,
            "parent_prepared_call_ref": self.parent_prepared_call_ref,
            "provider_call_id": self.provider_call_id,
            "plan_ref": self.plan_ref,
            "current_inner_call_ref": self.current_inner_call_ref,
            "aggregate_value_ref": self.aggregate_value_ref,
            "status": self.status,
            "input_snapshot": self.input_snapshot.to_mapping(),
            "plan_record": self.plan_record.to_mapping(),
            "value": copy.deepcopy(self.value),
            "artifacts": copy.deepcopy(list(self.artifacts)),
            "observations": copy.deepcopy(list(self.observations)),
            "parent_outcome_metadata": copy.deepcopy(
                dict(self.parent_outcome_metadata)
            ),
            "current_inner_call": (
                None
                if self.current_inner_call is None
                else copy.deepcopy(dict(self.current_inner_call))
            ),
            "current_inner_context": (
                None
                if self.current_inner_context is None
                else copy.deepcopy(dict(self.current_inner_context))
            ),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BrokeredCommandBoundary":
        expected = {
            "parent_index",
            "parent_prepared_call_ref",
            "provider_call_id",
            "plan_ref",
            "current_inner_call_ref",
            "aggregate_value_ref",
            "status",
            "input_snapshot",
            "plan_record",
            "value",
            "artifacts",
            "observations",
            "parent_outcome_metadata",
            "current_inner_call",
            "current_inner_context",
        }
        if (
            set(value) != expected
            or not isinstance(value.get("input_snapshot"), Mapping)
            or not isinstance(value.get("plan_record"), Mapping)
            or not isinstance(value.get("artifacts"), list)
            or not isinstance(value.get("observations"), list)
            or not isinstance(value.get("parent_outcome_metadata"), Mapping)
            or value.get("current_inner_call") is not None
            and not isinstance(value.get("current_inner_call"), Mapping)
            or value.get("current_inner_context") is not None
            and not isinstance(value.get("current_inner_context"), Mapping)
        ):
            raise EffectPlanValidationError(
                "durable brokered command boundary shape is invalid"
            )
        return cls(
            parent_index=int(value["parent_index"]),
            parent_prepared_call_ref=str(value["parent_prepared_call_ref"]),
            provider_call_id=str(value["provider_call_id"]),
            plan_ref=str(value["plan_ref"]),
            current_inner_call_ref=(
                None
                if value["current_inner_call_ref"] is None
                else str(value["current_inner_call_ref"])
            ),
            aggregate_value_ref=(
                None
                if value["aggregate_value_ref"] is None
                else str(value["aggregate_value_ref"])
            ),
            status=str(value["status"]),  # type: ignore[arg-type]
            input_snapshot=InputBindingSnapshot.from_mapping(
                value["input_snapshot"]
            ),
            plan_record=BrokeredEffectPlanRecord.from_mapping(
                value["plan_record"]
            ),
            value=copy.deepcopy(value["value"]),
            artifacts=tuple(copy.deepcopy(value["artifacts"])),
            observations=tuple(copy.deepcopy(value["observations"])),
            parent_outcome_metadata=dict(value["parent_outcome_metadata"]),
            current_inner_call=(
                None
                if value["current_inner_call"] is None
                else dict(value["current_inner_call"])
            ),
            current_inner_context=(
                None
                if value["current_inner_context"] is None
                else dict(value["current_inner_context"])
            ),
        )


@dataclass(frozen=True, slots=True)
class DeferredToolAcceptedSignal:
    run_id: str
    command_id: str
    parent_call_id: str
    parent_effect_id: str
    deferred_kind: Literal["brokered_effect_plan"]
    envelope: Mapping[str, Any]
