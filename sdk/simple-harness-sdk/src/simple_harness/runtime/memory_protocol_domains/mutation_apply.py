# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Applying memory mutations: results, receipts and receipt verification."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, cast

from simple_harness.contracts import JsonValue

from ..disclosure_protocol import (
    _digest,
    _domain_hash,
    _exact_keys,
    _identifier,
    _object,
    _objects,
    _positive_int,
    _strings,
)
from ..memory_action_protocol import MemoryActionIntent
from .common import _bounded_identifiers, _optional_timestamp, _trusted_current_time
from .mutation_payloads import (
    MemoryMutationApplyMode,
    MemoryMutationKind,
    MemoryMutationPlanOutcome,
)
from .mutation_plan import MemoryMutationPlan
from .types import MEMORY_MUTATION_SCHEMA_VERSION, _memory_mutation_schema_version


@dataclass(frozen=True, slots=True)
class MemoryMutationApplyReceiptRef:
    receipt_id: str
    receipt_hash: str

    def __post_init__(self) -> None:
        _identifier(self.receipt_id, "receipt_id")
        _digest(self.receipt_hash, "receipt_hash")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "receipt_id": self.receipt_id,
            "receipt_hash": self.receipt_hash,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> MemoryMutationApplyReceiptRef:
        _exact_keys(
            value,
            {"receipt_id", "receipt_hash"},
            "MemoryMutationApplyReceiptRef",
        )
        return cls(
            _identifier(value["receipt_id"], "receipt_id"),
            _digest(value["receipt_hash"], "receipt_hash"),
        )


class MemoryMutationApplyOutcome(StrEnum):
    COMMITTED = "committed"
    NEEDS_USER_CONFIRMATION = "needs_user_confirmation"
    REJECTED = "rejected"


class MemoryMutationApplyReasonCode(StrEnum):
    COMMITTED = "memory_mutation_committed"
    NO_MUTATION = "memory_mutation_no_mutation"
    ACTION_AUTHORITY_REQUIRED = "memory_action_authority_required"
    ACTION_AUTHORITY_REJECTED = "memory_action_authority_rejected"
    VALIDATION_REJECTED = "memory_mutation_validation_rejected"


@dataclass(frozen=True, slots=True)
class MemoryActionConfirmationItem:
    """Exact action intent the Host may present for authenticated confirmation."""

    intent: MemoryActionIntent
    item_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.intent) is not MemoryActionIntent:
            raise TypeError("intent must use MemoryActionIntent")
        object.__setattr__(
            self,
            "item_hash",
            _domain_hash(
                "simple-harness/memory-action-confirmation-item/v1",
                self.to_json(),
            ),
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {"intent": self.intent.to_json(), "intent_hash": self.intent.intent_hash}

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> MemoryActionConfirmationItem:
        _exact_keys(
            value,
            {"intent", "intent_hash"},
            "MemoryActionConfirmationItem",
        )
        intent = MemoryActionIntent.from_json(_object(value["intent"], "intent"))
        if _digest(value["intent_hash"], "intent_hash") != intent.intent_hash:
            raise ValueError("MemoryActionConfirmationItem intent_hash differs")
        return cls(intent)


@dataclass(frozen=True, slots=True)
class MemoryMutationApplyResult:
    """Stable result wire; confirmation is not represented as an exception."""

    result_id: str
    plan_id: str
    plan_hash: str
    run_id: str
    turn_id: str
    subject: str
    outcome: MemoryMutationApplyOutcome
    receipt_ref: MemoryMutationApplyReceiptRef | None
    confirmation_items: tuple[MemoryActionConfirmationItem, ...]
    reason_code: MemoryMutationApplyReasonCode
    decided_at: float
    schema_version: int = MEMORY_MUTATION_SCHEMA_VERSION
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != MEMORY_MUTATION_SCHEMA_VERSION:
            raise ValueError("unsupported MemoryMutationApplyResult schema_version")
        for value, name in (
            (self.result_id, "result_id"),
            (self.plan_id, "plan_id"),
            (self.run_id, "run_id"),
            (self.turn_id, "turn_id"),
            (self.subject, "subject"),
        ):
            _identifier(value, name)
        _digest(self.plan_hash, "plan_hash")
        outcome = MemoryMutationApplyOutcome(self.outcome)
        reason = MemoryMutationApplyReasonCode(self.reason_code)
        if (
            self.receipt_ref is not None
            and type(self.receipt_ref) is not MemoryMutationApplyReceiptRef
        ):
            raise TypeError("receipt_ref must use MemoryMutationApplyReceiptRef")
        items = tuple(self.confirmation_items)
        if not all(type(item) is MemoryActionConfirmationItem for item in items):
            raise TypeError("confirmation_items must use MemoryActionConfirmationItem")
        if len(items) > 128 or len({item.intent.intent_hash for item in items}) != len(items):
            raise ValueError("confirmation_items must be bounded and unique")
        if outcome is MemoryMutationApplyOutcome.COMMITTED:
            if self.receipt_ref is None or items:
                raise ValueError("committed result requires only receipt_ref")
            if reason not in {
                MemoryMutationApplyReasonCode.COMMITTED,
                MemoryMutationApplyReasonCode.NO_MUTATION,
            }:
                raise ValueError("committed result reason differs")
        elif outcome is MemoryMutationApplyOutcome.NEEDS_USER_CONFIRMATION:
            if self.receipt_ref is not None or not items:
                raise ValueError(
                    "needs_user_confirmation requires confirmation_items and no receipt"
                )
            if reason is not MemoryMutationApplyReasonCode.ACTION_AUTHORITY_REQUIRED:
                raise ValueError("needs_user_confirmation reason differs")
        else:
            if self.receipt_ref is not None or items:
                raise ValueError("rejected result cannot carry receipt or confirmation items")
            if reason not in {
                MemoryMutationApplyReasonCode.ACTION_AUTHORITY_REJECTED,
                MemoryMutationApplyReasonCode.VALIDATION_REJECTED,
            }:
                raise ValueError("rejected result reason differs")
        decided_at = _trusted_current_time(self.decided_at)
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "confirmation_items", items)
        object.__setattr__(self, "reason_code", reason)
        object.__setattr__(self, "decided_at", decided_at)
        object.__setattr__(
            self,
            "result_hash",
            _domain_hash(
                "simple-harness/memory-mutation-apply-result/v4",
                self.to_json(),
            ),
        )

    def validate_plan(self, plan: MemoryMutationPlan) -> None:
        if type(plan) is not MemoryMutationPlan:
            raise TypeError("plan must use MemoryMutationPlan")
        if (
            self.plan_id,
            self.plan_hash,
            self.run_id,
            self.turn_id,
            self.subject,
        ) != (
            plan.plan_id,
            plan.plan_hash,
            plan.run_id,
            plan.turn_id,
            plan.subject,
        ):
            raise ValueError("apply result does not exactly bind mutation plan")
        if self.outcome is MemoryMutationApplyOutcome.COMMITTED:
            expected = (
                MemoryMutationApplyReasonCode.NO_MUTATION
                if plan.outcome is MemoryMutationPlanOutcome.NO_MUTATION
                else MemoryMutationApplyReasonCode.COMMITTED
            )
            if self.reason_code is not expected:
                raise ValueError("committed result reason differs from plan outcome")
            missing_authority = tuple(
                operation.operation_id
                for operation in plan.operations
                if operation.kind
                in {
                    MemoryMutationKind.REVISE,
                    MemoryMutationKind.SUPERSEDE,
                    MemoryMutationKind.SUPPRESS,
                }
                and operation.action_authority_ref is None
            )
            if missing_authority:
                raise ValueError(
                    "committed result requires action authority for every protected operation"
                )
        if self.outcome is MemoryMutationApplyOutcome.NEEDS_USER_CONFIRMATION:
            expected_intents = {
                plan.action_intent(operation.operation_id).intent_hash
                for operation in plan.operations
                if operation.kind
                in {
                    MemoryMutationKind.REVISE,
                    MemoryMutationKind.SUPERSEDE,
                    MemoryMutationKind.SUPPRESS,
                }
                and operation.action_authority_ref is None
            }
            actual_intents = {item.intent.intent_hash for item in self.confirmation_items}
            if actual_intents != expected_intents:
                raise ValueError("confirmation items do not exactly cover missing authorities")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "result_id": self.result_id,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "subject": self.subject,
            "outcome": self.outcome.value,
            "receipt_ref": None if self.receipt_ref is None else self.receipt_ref.to_json(),
            "confirmation_items": [item.to_json() for item in self.confirmation_items],
            "reason_code": self.reason_code.value,
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> MemoryMutationApplyResult:
        _exact_keys(
            value,
            {
                "schema_version",
                "result_id",
                "plan_id",
                "plan_hash",
                "run_id",
                "turn_id",
                "subject",
                "outcome",
                "receipt_ref",
                "confirmation_items",
                "reason_code",
                "decided_at",
            },
            "MemoryMutationApplyResult",
        )
        receipt_value = value["receipt_ref"]
        return cls(
            result_id=_identifier(value["result_id"], "result_id"),
            plan_id=_identifier(value["plan_id"], "plan_id"),
            plan_hash=_digest(value["plan_hash"], "plan_hash"),
            run_id=_identifier(value["run_id"], "run_id"),
            turn_id=_identifier(value["turn_id"], "turn_id"),
            subject=_identifier(value["subject"], "subject"),
            outcome=MemoryMutationApplyOutcome(value["outcome"]),  # type: ignore[arg-type]
            receipt_ref=(
                None
                if receipt_value is None
                else MemoryMutationApplyReceiptRef.from_json(_object(receipt_value, "receipt_ref"))
            ),
            confirmation_items=tuple(
                MemoryActionConfirmationItem.from_json(item)
                for item in _objects(value["confirmation_items"], "confirmation_items")
            ),
            reason_code=MemoryMutationApplyReasonCode(value["reason_code"]),  # type: ignore[arg-type]
            decided_at=_trusted_current_time(value["decided_at"]),
            schema_version=_memory_mutation_schema_version(
                value["schema_version"], "MemoryMutationApplyResult"
            ),
        )


@dataclass(frozen=True, slots=True)
class MemoryMutationApplyReceipt:
    """Authority-issued receipt for one indivisible plan transaction."""

    receipt_id: str
    authority_ref: str
    plan_id: str
    plan_hash: str
    run_id: str
    subject: str
    base_revision: int
    committed_revision: int
    canonical_operation_ids: tuple[str, ...]
    apply_mode: MemoryMutationApplyMode
    committed_at: float
    schema_version: int = MEMORY_MUTATION_SCHEMA_VERSION
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != MEMORY_MUTATION_SCHEMA_VERSION:
            raise ValueError("unsupported MemoryMutationApplyReceipt schema_version")
        for value, name in (
            (self.receipt_id, "receipt_id"),
            (self.authority_ref, "authority_ref"),
            (self.plan_id, "plan_id"),
            (self.run_id, "run_id"),
            (self.subject, "subject"),
        ):
            _identifier(value, name)
        _digest(self.plan_hash, "plan_hash")
        _positive_int(self.base_revision, "base_revision")
        _positive_int(self.committed_revision, "committed_revision")
        operation_ids = _bounded_identifiers(
            self.canonical_operation_ids,
            "canonical_operation_ids",
        )
        apply_mode = MemoryMutationApplyMode(self.apply_mode)
        if apply_mode is not MemoryMutationApplyMode.STRICT_ATOMIC:
            raise ValueError("apply receipt must use strict_atomic mode")
        committed_at = _trusted_current_time(self.committed_at)
        object.__setattr__(self, "canonical_operation_ids", operation_ids)
        object.__setattr__(self, "apply_mode", apply_mode)
        object.__setattr__(self, "committed_at", committed_at)
        object.__setattr__(
            self,
            "receipt_hash",
            _domain_hash(
                "simple-harness/memory-mutation-apply-receipt/v4",
                self.to_json(),
            ),
        )

    def validate_plan(self, plan: MemoryMutationPlan) -> None:
        if not isinstance(plan, MemoryMutationPlan):
            raise TypeError("plan must use MemoryMutationPlan")
        if (
            self.plan_id != plan.plan_id
            or self.plan_hash != plan.plan_hash
            or self.run_id != plan.run_id
            or self.subject != plan.subject
            or self.apply_mode is not plan.apply_mode
        ):
            raise ValueError("apply receipt does not exactly bind mutation plan")
        if self.base_revision != plan.base_revision:
            raise ValueError("apply receipt base_revision differs from mutation plan")
        expected_ids = tuple(operation.operation_id for operation in plan.topological_operations())
        if self.canonical_operation_ids != expected_ids:
            raise ValueError("apply receipt does not cover all canonical operations")
        missing_authority = tuple(
            operation.operation_id
            for operation in plan.operations
            if operation.kind
            in {
                MemoryMutationKind.REVISE,
                MemoryMutationKind.SUPERSEDE,
                MemoryMutationKind.SUPPRESS,
            }
            and operation.action_authority_ref is None
        )
        if missing_authority:
            raise ValueError(
                "apply receipt requires action authority for every protected operation"
            )
        expected_committed_revision = plan.base_revision + (
            1 if plan.outcome is MemoryMutationPlanOutcome.MUTATE else 0
        )
        if self.committed_revision != expected_committed_revision:
            raise ValueError("apply receipt does not bind the atomic base-to-committed revision")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "authority_ref": self.authority_ref,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "run_id": self.run_id,
            "subject": self.subject,
            "base_revision": self.base_revision,
            "committed_revision": self.committed_revision,
            "canonical_operation_ids": list(self.canonical_operation_ids),
            "apply_mode": self.apply_mode.value,
            "committed_at": self.committed_at,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> MemoryMutationApplyReceipt:
        _exact_keys(
            value,
            {
                "schema_version",
                "receipt_id",
                "authority_ref",
                "plan_id",
                "plan_hash",
                "run_id",
                "subject",
                "base_revision",
                "committed_revision",
                "canonical_operation_ids",
                "apply_mode",
                "committed_at",
            },
            "MemoryMutationApplyReceipt",
        )
        return cls(
            receipt_id=_identifier(value["receipt_id"], "receipt_id"),
            authority_ref=_identifier(value["authority_ref"], "authority_ref"),
            plan_id=_identifier(value["plan_id"], "plan_id"),
            plan_hash=_digest(value["plan_hash"], "plan_hash"),
            run_id=_identifier(value["run_id"], "run_id"),
            subject=_identifier(value["subject"], "subject"),
            base_revision=_positive_int(value["base_revision"], "base_revision"),
            committed_revision=_positive_int(value["committed_revision"], "committed_revision"),
            canonical_operation_ids=_strings(
                value["canonical_operation_ids"], "canonical_operation_ids"
            ),
            apply_mode=MemoryMutationApplyMode(value["apply_mode"]),  # type: ignore[arg-type]
            committed_at=cast(float, _optional_timestamp(value["committed_at"], "committed_at")),
            schema_version=_memory_mutation_schema_version(
                value["schema_version"], "MemoryMutationApplyReceipt"
            ),
        )


class MemoryMutationApplyAuthorityPort(Protocol):
    """Trusted Memory boundary that resolves durable apply receipts."""

    async def resolve_memory_mutation_apply_receipt(
        self, receipt_ref: MemoryMutationApplyReceiptRef
    ) -> MemoryMutationApplyReceipt: ...


async def verify_memory_mutation_apply_receipt(
    plan: MemoryMutationPlan,
    receipt_ref: MemoryMutationApplyReceiptRef,
    authority: MemoryMutationApplyAuthorityPort,
) -> MemoryMutationApplyReceipt:
    """Resolve the receipt from authority and verify an all-or-nothing commit."""

    if not isinstance(receipt_ref, MemoryMutationApplyReceiptRef):
        raise TypeError("receipt_ref must use MemoryMutationApplyReceiptRef")
    receipt = await authority.resolve_memory_mutation_apply_receipt(receipt_ref)
    if not isinstance(receipt, MemoryMutationApplyReceipt):
        raise TypeError("apply authority returned an invalid receipt")
    if (
        receipt.receipt_id != receipt_ref.receipt_id
        or receipt.receipt_hash != receipt_ref.receipt_hash
    ):
        raise ValueError("apply authority receipt differs from receipt_ref")
    receipt.validate_plan(plan)
    return receipt
