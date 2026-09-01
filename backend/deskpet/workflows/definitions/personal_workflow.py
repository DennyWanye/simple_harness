"""Frozen host selection and static interpreter graph for Personal Workflow v1."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from deskpet.companion.personal_workflow import (
    PersonalWorkflowV1,
    parse_personal_workflow_v1,
)

from ..contracts import (
    ChannelSpec,
    JsonType,
    JsonValue,
    ReducerKind,
    StatePatch,
    WorkflowContext,
    WorkflowState,
)
from ..definition import (
    END_NODE,
    CompiledWorkflow,
    Edge,
    NodeDefinition,
    WorkflowDefinition,
    compile_workflow,
)

WORKFLOW_NAME = "personal_workflow"
WORKFLOW_VERSION = "v1"
STATE_SCHEMA_VERSION = 1
PROFILE_KEY = "workflow.personal_v1"
SELECTION_EXTENSION_KEY = "deskpet.companion.selection.v1"


class PersonalWorkflowSelectionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def personal_workflow_query_hash(value: str) -> str:
    text = " ".join(str(value).strip().split())
    if not text:
        raise PersonalWorkflowSelectionError("personal_query_required")
    return _hash(
        {
            "schema": "personal-workflow-query-v1",
            "normalized_query": text,
        }
    )


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PersonalWorkflowSelectionError(f"{name}_required")
    return value.strip()


def _digest(value: object, name: str) -> str:
    text = _required(value, name)
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise PersonalWorkflowSelectionError(f"{name}_invalid")
    return text


def _closed_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise PersonalWorkflowSelectionError(f"{name}_invalid")
    try:
        cloned = json.loads(_canonical_json(dict(value)))
    except (TypeError, ValueError) as exc:
        raise PersonalWorkflowSelectionError(f"{name}_invalid") from exc
    if not isinstance(cloned, dict):
        raise PersonalWorkflowSelectionError(f"{name}_invalid")
    return MappingProxyType(cloned)


@dataclass(frozen=True, slots=True)
class PersonalWorkflowSelectionV1:
    """Selection derived only from the parent's trusted capability snapshot."""

    selection_id: str
    selection_fingerprint: str
    owner_key: str
    pack_id: str
    version: str
    manifest_hash: str
    binding_generation: int
    graph: Mapping[str, Any]
    graph_hash: str
    query_hash: str
    run_catalog_content_stamp: str
    lease_entries: tuple[Mapping[str, Any], ...]
    effect_topology: Mapping[str, Any]
    tool_bindings: Mapping[str, Mapping[str, Any]]

    def __post_init__(self) -> None:
        for name in (
            "selection_id",
            "owner_key",
            "pack_id",
            "version",
            "run_catalog_content_stamp",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        for name in (
            "selection_fingerprint",
            "manifest_hash",
            "graph_hash",
            "query_hash",
        ):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        if (
            isinstance(self.binding_generation, bool)
            or self.binding_generation < 1
        ):
            raise PersonalWorkflowSelectionError("binding_generation_invalid")
        graph = _closed_mapping(self.graph, "graph")
        topology = _closed_mapping(self.effect_topology, "effect_topology")
        bindings = _closed_mapping(self.tool_bindings, "tool_bindings")
        entries = tuple(
            _closed_mapping(item, "lease_entry") for item in self.lease_entries
        )
        if not entries:
            raise PersonalWorkflowSelectionError("lease_entries_required")
        object.__setattr__(self, "graph", graph)
        object.__setattr__(self, "effect_topology", topology)
        object.__setattr__(self, "tool_bindings", bindings)
        object.__setattr__(self, "lease_entries", entries)

        parsed = parse_personal_workflow_v1(
            dict(graph),
            tool_resolver=lambda name: self._tool_binding(name),
        )
        if parsed.graph_hash != self.graph_hash:
            raise PersonalWorkflowSelectionError("personal_graph_hash_mismatch")
        expected_id = "personal-selection:" + _hash(self._identity_payload())
        if self.selection_id != expected_id:
            raise PersonalWorkflowSelectionError("personal_selection_id_mismatch")
        if self.selection_fingerprint != _hash(self._fingerprint_payload()):
            raise PersonalWorkflowSelectionError(
                "personal_selection_fingerprint_mismatch"
            )

    def _tool_binding(self, name: str) -> Mapping[str, Any]:
        value = self.tool_bindings.get(name)
        if not isinstance(value, Mapping):
            raise PersonalWorkflowSelectionError(
                "personal_tool_binding_missing"
            )
        return value

    def _identity_payload(self) -> dict[str, object]:
        return {
            "schema": "personal-workflow-selection-id-v1",
            "owner_key": self.owner_key,
            "pack_id": self.pack_id,
            "version": self.version,
            "manifest_hash": self.manifest_hash,
            "binding_generation": self.binding_generation,
            "graph_hash": self.graph_hash,
            "query_hash": self.query_hash,
        }

    def _fingerprint_payload(self) -> dict[str, object]:
        return {
            "schema": "personal-workflow-selection-fingerprint-v1",
            "identity": self._identity_payload(),
            "selection_id": self.selection_id,
            "run_catalog_content_stamp": self.run_catalog_content_stamp,
            "lease_entries": [dict(item) for item in self.lease_entries],
            "effect_topology": dict(self.effect_topology),
            "tool_bindings": {
                name: dict(value)
                for name, value in sorted(self.tool_bindings.items())
            },
        }

    @property
    def workflow(self) -> PersonalWorkflowV1:
        return parse_personal_workflow_v1(
            dict(self.graph),
            tool_resolver=lambda name: self._tool_binding(name),
        )

    def to_child_payload(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "selection_id": self.selection_id,
            "selection_fingerprint": self.selection_fingerprint,
            "owner_key": self.owner_key,
            "pack_id": self.pack_id,
            "version": self.version,
            "manifest_hash": self.manifest_hash,
            "binding_generation": self.binding_generation,
            "graph": copy.deepcopy(dict(self.graph)),
            "graph_hash": self.graph_hash,
            "query_hash": self.query_hash,
            "run_catalog_content_stamp": self.run_catalog_content_stamp,
            "lease_entries": [
                copy.deepcopy(dict(item)) for item in self.lease_entries
            ],
            "effect_topology": copy.deepcopy(dict(self.effect_topology)),
            "tool_bindings": {
                name: copy.deepcopy(dict(value))
                for name, value in self.tool_bindings.items()
            },
        }

    @classmethod
    def from_authoritative_mapping(
        cls, value: Mapping[str, Any]
    ) -> PersonalWorkflowSelectionV1:
        required = {
            "schema_version",
            "selection_id",
            "selection_fingerprint",
            "owner_key",
            "pack_id",
            "version",
            "manifest_hash",
            "binding_generation",
            "graph",
            "graph_hash",
            "query_hash",
            "run_catalog_content_stamp",
            "lease_entries",
            "effect_topology",
            "tool_bindings",
        }
        if set(value) != required or value.get("schema_version") != 1:
            raise PersonalWorkflowSelectionError(
                "personal_selection_schema_invalid"
            )
        lease_entries = value["lease_entries"]
        if not isinstance(lease_entries, Sequence) or isinstance(
            lease_entries, (str, bytes, bytearray)
        ):
            raise PersonalWorkflowSelectionError("lease_entries_invalid")
        return cls(
            selection_id=value["selection_id"],
            selection_fingerprint=value["selection_fingerprint"],
            owner_key=value["owner_key"],
            pack_id=value["pack_id"],
            version=value["version"],
            manifest_hash=value["manifest_hash"],
            binding_generation=value["binding_generation"],
            graph=value["graph"],
            graph_hash=value["graph_hash"],
            query_hash=value["query_hash"],
            run_catalog_content_stamp=value["run_catalog_content_stamp"],
            lease_entries=tuple(lease_entries),
            effect_topology=value["effect_topology"],
            tool_bindings=value["tool_bindings"],
        )

    @classmethod
    def issue(
        cls,
        *,
        owner_key: str,
        pack_id: str,
        version: str,
        manifest_hash: str,
        binding_generation: int,
        graph: Mapping[str, Any],
        query_hash: str,
        run_catalog_content_stamp: str,
        lease_entries: Sequence[Mapping[str, Any]],
        effect_topology: Mapping[str, Any],
        tool_bindings: Mapping[str, Mapping[str, Any]],
    ) -> PersonalWorkflowSelectionV1:
        parsed = parse_personal_workflow_v1(
            graph,
            tool_resolver=lambda name: tool_bindings[name],
        )
        identity = {
            "schema": "personal-workflow-selection-id-v1",
            "owner_key": owner_key,
            "pack_id": pack_id,
            "version": version,
            "manifest_hash": manifest_hash,
            "binding_generation": binding_generation,
            "graph_hash": parsed.graph_hash,
            "query_hash": query_hash,
        }
        selection_id = "personal-selection:" + _hash(identity)
        fingerprint = {
            "schema": "personal-workflow-selection-fingerprint-v1",
            "identity": identity,
            "selection_id": selection_id,
            "run_catalog_content_stamp": run_catalog_content_stamp,
            "lease_entries": [dict(item) for item in lease_entries],
            "effect_topology": dict(effect_topology),
            "tool_bindings": {
                name: dict(value)
                for name, value in sorted(tool_bindings.items())
            },
        }
        return cls(
            selection_id=selection_id,
            selection_fingerprint=_hash(fingerprint),
            owner_key=owner_key,
            pack_id=pack_id,
            version=version,
            manifest_hash=manifest_hash,
            binding_generation=binding_generation,
            graph=graph,
            graph_hash=parsed.graph_hash,
            query_hash=query_hash,
            run_catalog_content_stamp=run_catalog_content_stamp,
            lease_entries=tuple(lease_entries),
            effect_topology=effect_topology,
            tool_bindings=tool_bindings,
        )


def selection_from_capability_snapshot(
    capability_snapshot: Mapping[str, Any],
) -> PersonalWorkflowSelectionV1 | None:
    """Read only the host extension; similarly named request fields are ignored."""

    extensions = capability_snapshot.get("host_extensions")
    if not isinstance(extensions, Mapping):
        return None
    extension = extensions.get(SELECTION_EXTENSION_KEY)
    if not isinstance(extension, Mapping):
        return None
    raw = extension.get("personal_workflow_selection")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise PersonalWorkflowSelectionError("personal_selection_invalid")
    return PersonalWorkflowSelectionV1.from_authoritative_mapping(raw)


async def _execute_handler(
    state: WorkflowState, context: WorkflowContext
) -> StatePatch:
    runtime = context.ports.get("personal_workflow_runtime")
    execute = getattr(runtime, "execute", None)
    if not callable(execute):
        raise RuntimeError("personal workflow runtime port is unavailable")
    values = dict(state.get("values") or {})
    selection = PersonalWorkflowSelectionV1.from_authoritative_mapping(
        values["personal_workflow_selection"]
    )
    result = await execute(
        child_run_id=str(state["run_id"]),
        selection=selection,
        inputs=dict(values.get("inputs") or {}),
        execution_identity=context.identity,
    )
    return StatePatch(
        {
            "values": {
                **values,
                "outputs": copy.deepcopy(dict(result)),
                "terminal_status": "success",
            }
        }
    )


PERSONAL_WORKFLOW_V1_DEFINITION = WorkflowDefinition(
    name=WORKFLOW_NAME,
    version=WORKFLOW_VERSION,
    state_schema_version=STATE_SCHEMA_VERSION,
    entry_node="execute",
    nodes=(NodeDefinition("execute", _execute_handler),),
    channels={
        "values": ChannelSpec(
            value_type=JsonType.OBJECT,
            reducer=ReducerKind.SINGLE_WRITER,
            allowed_writers=frozenset({"execute"}),
        )
    },
    edges=(Edge("execute", END_NODE),),
    recursion_limit=4,
    max_supersteps=2,
    prompt_manifest={"profile": PROFILE_KEY},
    policy_manifest={
        "implementation": "personal-workflow-interpreter-v1",
        "selection_source": "trusted-parent-start-snapshot-only",
        "effect_identity": "child-selection-graph-node-v1",
    },
)

PERSONAL_WORKFLOW_V1: CompiledWorkflow = compile_workflow(
    PERSONAL_WORKFLOW_V1_DEFINITION
)


def initial_state(
    *,
    run_id: str,
    personal_workflow_selection: Mapping[str, Any],
    inputs: Mapping[str, JsonValue],
    thread_id: str | None = None,
    session_id: str = "",
    **_ignored: object,
) -> WorkflowState:
    selection = PersonalWorkflowSelectionV1.from_authoritative_mapping(
        personal_workflow_selection
    )
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "workflow_name": WORKFLOW_NAME,
        "workflow_version": WORKFLOW_VERSION,
        "thread_id": thread_id or run_id,
        "run_id": run_id,
        "session_id": session_id,
        "active_nodes": [],
        "active_step_id": None,
        "status": "pending",
        "values": {
            "personal_workflow_selection": selection.to_child_payload(),
            "inputs": copy.deepcopy(dict(inputs)),
        },
        "blob_refs": [],
        "artifact_refs": [],
        "receipt_refs": [],
        "loop_counters": {},
        "budgets": {},
        "errors": [],
    }


__all__ = [
    "PERSONAL_WORKFLOW_V1",
    "PERSONAL_WORKFLOW_V1_DEFINITION",
    "PROFILE_KEY",
    "SELECTION_EXTENSION_KEY",
    "STATE_SCHEMA_VERSION",
    "WORKFLOW_NAME",
    "WORKFLOW_VERSION",
    "PersonalWorkflowSelectionError",
    "PersonalWorkflowSelectionV1",
    "initial_state",
    "personal_workflow_query_hash",
    "selection_from_capability_snapshot",
]
