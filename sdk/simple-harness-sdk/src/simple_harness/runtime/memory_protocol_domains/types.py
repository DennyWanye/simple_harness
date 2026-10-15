# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Shared memory protocol enums and schema versions."""

from __future__ import annotations

from enum import StrEnum

from ..disclosure_protocol import COGNITIVE_MEMORY_SCHEMA_VERSION

RECALL_DECISION_SCHEMA_VERSION = 4
MEMORY_MUTATION_SCHEMA_VERSION = 5
MEMORY_MUTATION_VALIDATION_DIAGNOSTIC_SCHEMA_VERSION = 1


class LongTermMemoryType(StrEnum):
    EPISODE = "episode"
    SEMANTIC = "semantic"
    PROCEDURE = "procedure"
    PROSPECTIVE = "prospective"


class SemanticMemoryKind(StrEnum):
    CLAIM = "claim"
    RELATION = "relation"


class SemanticRelationKind(StrEnum):
    APPLIES_TO = "applies_to"


class MemoryMutationValidationReasonCode(StrEnum):
    INVALID_WIRE = "invalid_memory_mutation_plan_wire"
    UNSUPPORTED_SCHEMA_VERSION = "memory_mutation_unsupported_schema_version"
    DUPLICATE_OPERATION = "duplicate_operation"
    RELATION_ENDPOINT_DEPENDENCY_REQUIRED = "relation_endpoint_dependency_required"
    INVALID_DEPENDENCY_ORDER = "invalid_dependency_order"
    RELATION_ENDPOINT_CREATE_REQUIRED = "relation_endpoint_create_required"
    INVALID_RELATION_KIND = "invalid_relation_kind"
    INVALID_RELATION_SOURCE_TYPE = "invalid_relation_source_type"
    INVALID_RELATION_TARGET_TYPE = "invalid_relation_target_type"
    RELATION_SELF_LOOP = "relation_self_loop"
    RELATION_ENDPOINT_MUST_BE_CLAIM = "relation_endpoint_must_be_claim"
    EVIDENCE_REF_REQUIRED = "evidence_ref_required"


class _MemoryMutationAdmissionError(ValueError):
    def __init__(self, reason_code: MemoryMutationValidationReasonCode, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class WorkingMemoryRole(StrEnum):
    CURRENT_QUERY = "current_query"
    RECENT_CAUSAL_WINDOW = "recent_causal_window"
    TASK_SCOPE_PROJECTION = "task_scope_projection"
    RECALLED_MEMORY = "recalled_memory"
    TOOL_STATE = "tool_state"


class ContextFragmentType(StrEnum):
    CURRENT_QUERY = "current_query"
    RECENT_CAUSAL_WINDOW = "recent_causal_window"
    SHORT_HORIZON = "short_horizon"
    TASK_SCOPE_PROJECTION = "task_scope_projection"
    RECALLED_MEMORY = "recalled_memory"
    RECALL_CONFIRMATION = "recall_confirmation"
    TOOL_CATALOG = "tool_catalog"
    SKILL_INSTRUCTIONS = "skill_instructions"


def _cognitive_schema_version(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} schema_version must be an integer")
    if value != COGNITIVE_MEMORY_SCHEMA_VERSION:
        raise ValueError(f"unsupported {name} schema_version")
    return value


def _memory_mutation_schema_version(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} schema_version must be an integer")
    if value != MEMORY_MUTATION_SCHEMA_VERSION:
        raise ValueError(f"unsupported {name} schema_version")
    return value


def _recall_decision_schema_version(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} schema_version must be an integer")
    if value != RECALL_DECISION_SCHEMA_VERSION:
        raise ValueError(f"unsupported {name} schema_version")
    return value
