"""Strict declarative ``personal_workflow/v1`` contracts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol

PERSONAL_WORKFLOW_INTERPRETER_ID = "workflow.personal_v1"
PERSONAL_WORKFLOW_INTERPRETER_VERSION = "1"
PERSONAL_WORKFLOW_NODE_TYPES = frozenset(
    {"input", "template", "condition", "tool_call", "output"}
)
PERSONAL_WORKFLOW_CONDITIONS = frozenset(
    {"eq", "ne", "exists", "in", "gt", "gte", "lt", "lte"}
)
_NODE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_POINTER_RE = re.compile(r"^/(?:[^~/]|~[01])+(?:/(?:[^~/]|~[01])+)*$")


class PersonalWorkflowError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PersonalWorkflowNode:
    id: str
    type: str
    bindings: Mapping[str, str]
    config: Mapping[str, Any]
    retry: int = 0
    tool_binding: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PersonalWorkflowV1:
    name: str
    description: str
    entry_node: str
    nodes: tuple[PersonalWorkflowNode, ...]
    outputs: Mapping[str, str]
    max_steps: int
    graph_hash: str
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class PersonalWorkflowNodeExecution:
    node_id: str
    logical_effect_id: str
    stable_call_id: str
    attempt_ordinal: int
    tool_binding: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PersonalWorkflowCheckpoint:
    graph_hash: str
    node_id: str
    output_hash: str
    effect_receipt_ref: str | None


class PersonalWorkflowCheckpointPort(Protocol):
    async def load_checkpoint(
        self, *, child_run_id: str, graph_hash: str, node_id: str
    ) -> PersonalWorkflowCheckpoint | None: ...

    async def save_checkpoint(
        self,
        *,
        child_run_id: str,
        checkpoint: PersonalWorkflowCheckpoint,
    ) -> None: ...


def _mapping(
    value: object,
    *,
    name: str,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, Any]:
    optional = optional or set()
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise PersonalWorkflowError("invalid_object", f"{name} must be an object")
    missing = required - set(value)
    extra = set(value) - required - optional
    if missing:
        raise PersonalWorkflowError(
            "missing_field", f"{name} is missing {sorted(missing)}"
        )
    if extra:
        raise PersonalWorkflowError(
            "unknown_field", f"{name} has unknown fields {sorted(extra)}"
        )
    return dict(value)


def _pointer(
    value: object,
    name: str,
    *,
    roots: tuple[str, ...],
) -> str:
    if not isinstance(value, str) or not _POINTER_RE.fullmatch(value):
        raise PersonalWorkflowError(
            "invalid_json_pointer", f"{name} must be a JSON Pointer"
        )
    if not any(value == root or value.startswith(f"{root}/") for root in roots):
        raise PersonalWorkflowError(
            "invalid_pointer_root",
            f"{name} must point into one of {list(roots)}",
        )
    return value


def _resolve_tool_binding(
    tool_name: str,
    resolver: Callable[[str], Mapping[str, Any]] | None,
) -> Mapping[str, Any]:
    if resolver is None:
        raise PersonalWorkflowError(
            "tool_binding_missing", "a frozen ToolSpec resolver is required"
        )
    facts = dict(resolver(tool_name))
    required = {
        "stable_handler_id",
        "tool_name",
        "spec_ref",
        "schema_hash",
        "execution_build_identity",
        "effect_policy_hash",
        "effect",
        "idempotent",
    }
    if set(facts) != required or facts.get("tool_name") != tool_name:
        raise PersonalWorkflowError(
            "tool_binding_invalid",
            "tool resolver must return one exact frozen ToolSpec topology",
        )
    for key in required - {"idempotent"}:
        if not isinstance(facts[key], str) or not facts[key]:
            raise PersonalWorkflowError(
                "tool_binding_invalid", f"{key} must be a non-empty string"
            )
    if (
        facts["effect"] not in {"read_only", "idempotent_read"}
        or facts["idempotent"] is not True
    ):
        raise PersonalWorkflowError(
            "unsafe_tool_retry",
            "personal workflow tools must be frozen read-only and idempotent",
        )
    return MappingProxyType(facts)


def personal_workflow_logical_effect_id(
    *,
    child_run_id: str,
    selection_id: str,
    graph_hash: str,
    node_id: str,
) -> str:
    return hashlib.sha256(
        "\x1f".join(
            (child_run_id, selection_id, graph_hash, node_id)
        ).encode("utf-8")
    ).hexdigest()


def plan_personal_workflow_tool_node(
    workflow: PersonalWorkflowV1,
    *,
    node_id: str,
    child_run_id: str,
    selection_id: str,
    attempt_ordinal: int,
) -> PersonalWorkflowNodeExecution:
    if (
        isinstance(attempt_ordinal, bool)
        or not isinstance(attempt_ordinal, int)
        or attempt_ordinal < 0
    ):
        raise PersonalWorkflowError(
            "invalid_attempt_ordinal",
            "attempt_ordinal must be a non-negative integer",
        )
    node = next((item for item in workflow.nodes if item.id == node_id), None)
    if node is None or node.type != "tool_call" or node.tool_binding is None:
        raise PersonalWorkflowError(
            "tool_node_missing", f"{node_id!r} is not a frozen tool node"
        )
    logical_effect_id = personal_workflow_logical_effect_id(
        child_run_id=child_run_id,
        selection_id=selection_id,
        graph_hash=workflow.graph_hash,
        node_id=node_id,
    )
    return PersonalWorkflowNodeExecution(
        node_id=node_id,
        logical_effect_id=logical_effect_id,
        stable_call_id=hashlib.sha256(
            f"{logical_effect_id}\x1fcall".encode("utf-8")
        ).hexdigest(),
        attempt_ordinal=attempt_ordinal,
        tool_binding=node.tool_binding,
    )


def parse_personal_workflow_v1(
    value: object,
    *,
    tool_resolver: Callable[[str], Mapping[str, Any]] | None = None,
) -> PersonalWorkflowV1:
    raw = _mapping(
        value,
        name="workflow",
        required={
            "schema_version",
            "name",
            "description",
            "entry_node",
            "nodes",
            "outputs",
            "max_steps",
        },
    )
    if raw["schema_version"] != 1:
        raise PersonalWorkflowError(
            "unsupported_schema", "personal workflow schema_version must be 1"
        )
    if not isinstance(raw["name"], str) or not raw["name"].strip():
        raise PersonalWorkflowError("invalid_name", "name must not be blank")
    if not isinstance(raw["description"], str):
        raise PersonalWorkflowError(
            "invalid_description", "description must be a string"
        )
    max_steps = raw["max_steps"]
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 1 <= max_steps <= 32:
        raise PersonalWorkflowError(
            "invalid_max_steps", "max_steps must be between 1 and 32"
        )
    if not isinstance(raw["nodes"], list) or not raw["nodes"]:
        raise PersonalWorkflowError("invalid_nodes", "nodes must be a non-empty list")

    nodes: list[PersonalWorkflowNode] = []
    node_ids: set[str] = set()
    dependencies: dict[str, set[str]] = {}
    for index, item in enumerate(raw["nodes"]):
        row = _mapping(
            item,
            name=f"nodes[{index}]",
            required={"id", "type", "bindings", "config"},
            optional={"retry"},
        )
        node_id = row["id"]
        node_type = row["type"]
        if not isinstance(node_id, str) or not _NODE_ID_RE.fullmatch(node_id):
            raise PersonalWorkflowError("invalid_node_id", f"invalid node id: {node_id!r}")
        if node_id in node_ids:
            raise PersonalWorkflowError("duplicate_node", f"duplicate node: {node_id}")
        if node_type not in PERSONAL_WORKFLOW_NODE_TYPES:
            raise PersonalWorkflowError(
                "unknown_node_type", f"unsupported node type: {node_type!r}"
            )
        bindings = _mapping(
            row["bindings"],
            name=f"nodes[{index}].bindings",
            required=set(),
            optional=set(row["bindings"]) if isinstance(row["bindings"], dict) else set(),
        )
        normalized_bindings = {
            key: _pointer(
                pointer,
                f"nodes[{index}].bindings.{key}",
                roots=("/input", "/nodes"),
            )
            for key, pointer in sorted(bindings.items())
        }
        config = _mapping(
            row["config"],
            name=f"nodes[{index}].config",
            required=set(),
            optional=set(row["config"]) if isinstance(row["config"], dict) else set(),
        )
        retry = row.get("retry", 0)
        if isinstance(retry, bool) or not isinstance(retry, int) or not 0 <= retry <= 2:
            raise PersonalWorkflowError("invalid_retry", "retry must be between 0 and 2")
        if node_type != "tool_call" and retry != 0:
            raise PersonalWorkflowError(
                "retry_not_allowed", "only tool_call nodes may retry"
            )
        tool_binding: Mapping[str, Any] | None = None
        if node_type == "condition":
            if set(config) != {"operator"} or config["operator"] not in PERSONAL_WORKFLOW_CONDITIONS:
                raise PersonalWorkflowError(
                    "invalid_condition", "condition must use one fixed operator"
                )
        elif node_type == "tool_call":
            if set(config) != {"tool_name"} or not isinstance(config["tool_name"], str):
                raise PersonalWorkflowError(
                    "invalid_tool_call", "tool_call requires only tool_name"
                )
            tool_binding = _resolve_tool_binding(
                config["tool_name"], tool_resolver
            )
        elif node_type == "template":
            if set(config) != {"template"} or not isinstance(config["template"], str):
                raise PersonalWorkflowError(
                    "invalid_template", "template requires only template text"
                )
        elif config:
            raise PersonalWorkflowError(
                "unexpected_node_config", f"{node_type} does not accept config"
            )
        refs = {
            pointer.split("/", 3)[2]
            for pointer in normalized_bindings.values()
            if pointer.startswith("/nodes/") and len(pointer.split("/", 3)) >= 3
        }
        dependencies[node_id] = refs
        nodes.append(
            PersonalWorkflowNode(
                id=node_id,
                type=node_type,
                bindings=MappingProxyType(normalized_bindings),
                config=MappingProxyType(config),
                retry=retry,
                tool_binding=tool_binding,
            )
        )
        node_ids.add(node_id)

    if raw["entry_node"] not in node_ids:
        raise PersonalWorkflowError("entry_missing", "entry_node does not exist")
    for node_id, refs in dependencies.items():
        unknown = refs - node_ids
        if unknown:
            raise PersonalWorkflowError(
                "binding_node_missing",
                f"{node_id} references unknown nodes {sorted(unknown)}",
            )
    outputs_raw = _mapping(
        raw["outputs"],
        name="outputs",
        required=set(),
        optional=set(raw["outputs"]) if isinstance(raw["outputs"], dict) else set(),
    )
    outputs = {
        key: _pointer(pointer, f"outputs.{key}", roots=("/nodes",))
        for key, pointer in sorted(outputs_raw.items())
    }
    output_refs = {
        pointer.split("/", 3)[2]
        for pointer in outputs.values()
        if pointer.startswith("/nodes/") and len(pointer.split("/", 3)) >= 3
    }
    unknown_outputs = output_refs - node_ids
    if unknown_outputs:
        raise PersonalWorkflowError(
            "output_node_missing",
            f"outputs reference unknown nodes {sorted(unknown_outputs)}",
        )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise PersonalWorkflowError("cycle", "personal workflow must be a DAG")
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in dependencies[node_id]:
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in sorted(node_ids):
        visit(node_id)
    if max_steps < len(nodes):
        raise PersonalWorkflowError(
            "max_steps_too_small", "max_steps cannot be smaller than node count"
        )
    canonical = json.dumps(
        raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return PersonalWorkflowV1(
        name=raw["name"].strip(),
        description=raw["description"],
        entry_node=raw["entry_node"],
        nodes=tuple(nodes),
        outputs=MappingProxyType(outputs),
        max_steps=max_steps,
        graph_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


__all__ = [
    "PERSONAL_WORKFLOW_CONDITIONS",
    "PERSONAL_WORKFLOW_INTERPRETER_ID",
    "PERSONAL_WORKFLOW_INTERPRETER_VERSION",
    "PERSONAL_WORKFLOW_NODE_TYPES",
    "PersonalWorkflowCheckpoint",
    "PersonalWorkflowCheckpointPort",
    "PersonalWorkflowError",
    "PersonalWorkflowNode",
    "PersonalWorkflowNodeExecution",
    "PersonalWorkflowV1",
    "personal_workflow_logical_effect_id",
    "plan_personal_workflow_tool_node",
    "parse_personal_workflow_v1",
]
