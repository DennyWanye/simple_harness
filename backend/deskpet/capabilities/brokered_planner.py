# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Validate worker plans and map them to registered host primitives.

This module never executes a tool.  It produces one stable internal call at a
time so the harness can run normal prepare/authorization/effect/UoW/receipt
handling between actions.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from .effect_plan import (
    BrokeredEffectPlanRecord,
    EffectPlan,
    EffectPlanValidationError,
)
from .input_views import InputBindingSnapshot

ResourceAuthorizer = Callable[[str, str, str], bool]


@dataclass(frozen=True, slots=True)
class BrokeredPreparedAction:
    tool_name: str
    args: Mapping[str, Any]
    stable_call_id: str
    provider_backfill: bool = False


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _path_identity(path: Path) -> str:
    raw = str(path.resolve(strict=False))
    return os.path.normcase(raw) if os.name == "nt" else raw


def _within_roots(path: Path, roots: tuple[Path, ...]) -> bool:
    candidate = _path_identity(path)
    for root in roots:
        root_value = _path_identity(root).rstrip("\\/")
        if candidate == root_value:
            return True
        separator = "\\" if os.name == "nt" else "/"
        if candidate.startswith(root_value + separator):
            return True
    return False


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BrokeredEffectPlanner:
    """Host validator for the V1 ordered ``rename_file`` action set."""

    def __init__(
        self,
        *,
        resource_authorizer: ResourceAuthorizer | None = None,
        max_actions: int = 256,
    ) -> None:
        self.resource_authorizer = resource_authorizer
        self.max_actions = max_actions

    def validate_and_record(
        self,
        raw_plan: Any,
        *,
        snapshot: InputBindingSnapshot,
        root_run_id: str,
        parent_call_id: str,
        provider_call_id: str,
        tool_spec_fingerprint: str,
        task_grant_id: str,
        catalog_stamp: Mapping[str, Any],
    ) -> BrokeredEffectPlanRecord:
        if snapshot.root_run_id != root_run_id:
            raise EffectPlanValidationError("input snapshot belongs to another root run")
        if not task_grant_id:
            raise EffectPlanValidationError("task_grant_id is required")
        if self.resource_authorizer is None:
            raise EffectPlanValidationError(
                "a TaskGrant resource authorizer is required"
            )
        plan = EffectPlan.parse(raw_plan, max_actions=self.max_actions)
        self._validate_resources(plan, snapshot, task_grant_id)
        plan_payload = plan.to_mapping()
        try:
            plan_hash = hashlib.sha256(
                _canonical_json(
                    {
                        "plan": plan_payload,
                        "snapshot_fingerprint": snapshot.fingerprint,
                        "task_grant_id": task_grant_id,
                        "catalog_stamp": dict(catalog_stamp),
                        "tool_spec_fingerprint": tool_spec_fingerprint,
                    }
                ).encode("utf-8")
            ).hexdigest()
        except (TypeError, ValueError) as exc:
            raise EffectPlanValidationError(
                f"brokered plan metadata is not JSON-safe: {exc}"
            ) from exc
        return BrokeredEffectPlanRecord(
            plan_ref=f"brokered-plan:{root_run_id}:{parent_call_id}:{plan_hash[:24]}",
            root_run_id=root_run_id,
            parent_call_id=parent_call_id,
            provider_call_id=provider_call_id,
            tool_spec_fingerprint=tool_spec_fingerprint,
            task_grant_id=task_grant_id,
            catalog_stamp=dict(catalog_stamp),
            input_snapshot_ref=snapshot.snapshot_ref,
            plan_hash=plan_hash,
            actions=tuple(action.to_mapping() for action in plan.actions),
            action_receipt_refs=tuple(None for _ in plan.actions),
            next_action_index=0,
            status="validated",
        )

    def next_action(
        self,
        record: BrokeredEffectPlanRecord,
        *,
        snapshot: InputBindingSnapshot,
    ) -> BrokeredPreparedAction | None:
        if record.input_snapshot_ref != snapshot.snapshot_ref:
            raise EffectPlanValidationError("record references another input snapshot")
        if record.status in {"failed", "cancelled", "succeeded"}:
            return None
        if record.next_action_index >= len(record.actions):
            return None
        sources = self._simulate_sources(
            EffectPlan.parse({"actions": list(record.actions)}),
            snapshot,
            stop_before=record.next_action_index,
        )
        action = record.actions[record.next_action_index]
        source = self._resolve_source(
            str(action["source_ref"]), sources, snapshot, record.next_action_index
        )
        destination = source.parent / str(action["target_name"])
        expected_hash = _file_sha256(source)
        authorizer = self.resource_authorizer
        if authorizer is None:
            raise EffectPlanValidationError(
                "a TaskGrant resource authorizer is required"
            )
        if not authorizer(
            str(source), str(destination), record.task_grant_id
        ):
            raise EffectPlanValidationError("TaskGrant no longer covers the action")
        return BrokeredPreparedAction(
            tool_name="move_file",
            args={
                "source": str(source),
                "destination": str(destination),
                "expected_source_hash": expected_hash,
                "overwrite": False,
            },
            stable_call_id=f"{record.plan_ref}:{record.next_action_index}",
            provider_backfill=False,
        )

    @staticmethod
    def record_receipt(
        record: BrokeredEffectPlanRecord,
        *,
        receipt_ref: str,
        succeeded: bool,
    ) -> BrokeredEffectPlanRecord:
        if record.status in {"failed", "cancelled", "succeeded"}:
            raise ValueError("cannot append a receipt to a terminal brokered plan")
        index = record.next_action_index
        if index >= len(record.actions):
            raise ValueError("brokered plan has no pending action")
        receipts = list(record.action_receipt_refs)
        if receipts[index] is not None:
            raise ValueError("action receipt was already recorded")
        receipts[index] = receipt_ref
        if not succeeded:
            return replace(
                record,
                action_receipt_refs=tuple(receipts),
                status="failed",
            )
        next_index = index + 1
        return replace(
            record,
            action_receipt_refs=tuple(receipts),
            next_action_index=next_index,
            status="succeeded" if next_index == len(record.actions) else "running",
        )

    def _validate_resources(
        self,
        plan: EffectPlan,
        snapshot: InputBindingSnapshot,
        task_grant_id: str,
    ) -> None:
        authorizer = self.resource_authorizer
        if authorizer is None:
            raise EffectPlanValidationError(
                "a TaskGrant resource authorizer is required"
            )
        roots = tuple(Path(root).resolve(strict=True) for root in snapshot.workspace_roots)
        sources = self._initial_sources(snapshot)
        occupancy: set[str] = {_path_identity(path) for path in sources["inputs"].values()}
        hash_by_path: dict[str, str] = {}
        for index, path in sources["inputs"].items():
            binding = snapshot.bindings[index]
            if binding.kind not in {"file", "artifact"}:
                continue
            if not path.is_file():
                raise EffectPlanValidationError(f"input:{index} is not a file")
            actual_hash = _file_sha256(path)
            if binding.content_hash is None or actual_hash != binding.content_hash:
                raise EffectPlanValidationError(f"input:{index} changed after snapshot")
            hash_by_path[_path_identity(path)] = actual_hash

        action_outputs: dict[int, Path] = {}
        input_positions = dict(sources["inputs"])
        for index, action in enumerate(plan.actions):
            source = self._resolve_source(
                action.source_ref,
                {"inputs": input_positions, "actions": action_outputs},
                snapshot,
                index,
            )
            source_id = _path_identity(source)
            if source_id not in occupancy:
                raise EffectPlanValidationError(
                    f"action {index} source is no longer present in the ordered plan"
                )
            destination = (source.parent / action.target_name).resolve(strict=False)
            if destination.parent != source.parent:
                raise EffectPlanValidationError(
                    f"action {index} must rename within the same directory"
                )
            if not _within_roots(source, roots) or not _within_roots(destination, roots):
                raise EffectPlanValidationError(f"action {index} is outside workspace scope")
            destination_id = _path_identity(destination)
            if destination_id == source_id:
                raise EffectPlanValidationError(
                    f"action {index} does not change the source path"
                )
            if destination_id != source_id and destination_id in occupancy:
                raise EffectPlanValidationError(
                    f"action {index} destination collides with a current input"
                )
            # A file not represented in the immutable snapshot is an external
            # collision. A plan may free an input path in an earlier action;
            # that case is represented by virtual occupancy and is allowed.
            initial_ids = {
                _path_identity(path) for path in sources["inputs"].values()
            }
            if (
                destination_id != source_id
                and destination.exists()
                and destination_id not in initial_ids
            ):
                raise EffectPlanValidationError(
                    f"action {index} destination already exists"
                )
            if not authorizer(
                str(source), str(destination), task_grant_id
            ):
                raise EffectPlanValidationError(
                    f"TaskGrant does not cover action {index}"
                )

            occupancy.discard(source_id)
            occupancy.add(destination_id)
            content_hash = hash_by_path.pop(source_id, None)
            if content_hash is not None:
                hash_by_path[destination_id] = content_hash
            for input_index, current in tuple(input_positions.items()):
                if _path_identity(current) == source_id:
                    input_positions[input_index] = destination
            action_outputs[index] = destination

    def _simulate_sources(
        self,
        plan: EffectPlan,
        snapshot: InputBindingSnapshot,
        *,
        stop_before: int,
    ) -> dict[str, dict[int, Path]]:
        sources = self._initial_sources(snapshot)
        input_positions = dict(sources["inputs"])
        action_outputs: dict[int, Path] = {}
        for index, action in enumerate(plan.actions[:stop_before]):
            source = self._resolve_source(
                action.source_ref,
                {"inputs": input_positions, "actions": action_outputs},
                snapshot,
                index,
            )
            destination = source.parent / action.target_name
            for input_index, current in tuple(input_positions.items()):
                if _path_identity(current) == _path_identity(source):
                    input_positions[input_index] = destination
            action_outputs[index] = destination
        return {"inputs": input_positions, "actions": action_outputs}

    @staticmethod
    def _initial_sources(
        snapshot: InputBindingSnapshot,
    ) -> dict[str, dict[int, Path]]:
        return {
            "inputs": {
                index: Path(binding.canonical_resource).resolve(strict=False)
                for index, binding in enumerate(snapshot.bindings)
                if binding.kind in {"file", "artifact"}
            },
            "actions": {},
        }

    @staticmethod
    def _resolve_source(
        source_ref: str,
        sources: Mapping[str, Mapping[int, Path]],
        snapshot: InputBindingSnapshot,
        action_index: int,
    ) -> Path:
        prefix, raw_index = source_ref.split(":", 1)
        index = int(raw_index)
        selected = sources["inputs" if prefix == "input" else "actions"]
        try:
            return selected[index]
        except KeyError as exc:
            raise EffectPlanValidationError(
                f"action {action_index} references unknown {source_ref}"
            ) from exc
