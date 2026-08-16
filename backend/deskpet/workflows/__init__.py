# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Lazy compatibility API for the pre-cutover DeskPet Workflow engine.

The active SDK graphs are children of this package, so importing them must not
implicitly activate legacy execution authority. Existing callers retain the
same public names and load their legacy module only on first access.
"""

from __future__ import annotations

from importlib import import_module


_CONTRACTS = frozenset(
    {
        "ChannelSpec", "EffectKind", "EffectPolicy", "JsonType", "JsonValue",
        "NodeExecutionIdentity", "NodeStatus", "ReducerKind", "RetryPolicy",
        "StatePatch", "ToolAccess", "ToolInventoryEntry", "WorkflowContext",
        "WorkflowRunStatus", "WorkflowState",
    }
)
_DEFINITION = frozenset(
    {
        "END_NODE", "CompiledWorkflow", "ConditionalEdge", "DurabilityMode",
        "Edge", "NodeDefinition", "NodeDispatch", "WorkflowDefinition",
        "WorkflowExecutable", "WorkflowManifest", "compile_workflow",
    }
)
_ERRORS = frozenset(
    {
        "AsyncOnlyWorkflowError", "InvalidStatePatch", "LeaseLostError",
        "StateMergeConflict", "UnsupportedDeltaChannelError",
        "WorkflowContractError", "WorkflowDefinitionError",
        "WorkflowDependencyUnavailable", "WorkflowErrorCode", "WorkflowNodeError",
    }
)
_EXECUTION_PORTS = frozenset({"CheckpointExecutionAdapter", "WorkflowExecutionPorts"})

__all__ = sorted(_CONTRACTS | _DEFINITION | _ERRORS | _EXECUTION_PORTS)


def __getattr__(name: str):
    if name in _CONTRACTS:
        return getattr(import_module(".contracts", __name__), name)
    if name in _DEFINITION:
        return getattr(import_module(".definition", __name__), name)
    if name in _ERRORS:
        return getattr(import_module(".errors", __name__), name)
    if name in _EXECUTION_PORTS:
        return getattr(import_module(".execution_ports", __name__), name)
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
