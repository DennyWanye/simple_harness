"""Durable interpreter seam for frozen ``workflow.personal_v1`` graphs."""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from deskpet.companion.personal_workflow import (
    PersonalWorkflowNode,
    personal_workflow_logical_effect_id,
    plan_personal_workflow_tool_node,
)
from deskpet.execution.contracts import (
    RunContext,
    RunStartSnapshotRecord,
    fingerprint_json,
    thaw_json,
)
from deskpet.harness.context import HostContextFactory
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.registry import ToolRegistry
from deskpet.workflows.effects import (
    EffectExecutionContext,
    EffectJournal,
    NormalizedToolOutcome,
    ToolOutcomeState,
)

from ..contracts import JsonValue, NodeExecutionIdentity, WorkflowContext
from ..definitions.personal_workflow import (
    PersonalWorkflowSelectionV1,
    initial_state,
)
from ..runtime_adapters import WorkflowRuntimeAdapter
from ..store import RunFence
from .code_runtime import ToolDispatchPort


class PersonalWorkflowRuntimeError(RuntimeError):
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


@dataclass(frozen=True, slots=True)
class PersonalWorkflowEffectReceiptV1:
    logical_effect_id: str
    stable_call_id: str
    attempt_ordinal: int
    output: Mapping[str, JsonValue]
    receipt_ref: str
    receipt_hash: str

    def __post_init__(self) -> None:
        if (
            not self.logical_effect_id
            or not self.stable_call_id
            or not self.receipt_ref
            or isinstance(self.attempt_ordinal, bool)
            or self.attempt_ordinal < 0
        ):
            raise ValueError("personal_effect_receipt_invalid")
        expected = _hash(
            {
                "schema": "personal-workflow-effect-receipt-v1",
                "logical_effect_id": self.logical_effect_id,
                "stable_call_id": self.stable_call_id,
                "attempt_ordinal": self.attempt_ordinal,
                "output": dict(self.output),
                "receipt_ref": self.receipt_ref,
            }
        )
        if self.receipt_hash != expected:
            raise ValueError("personal_effect_receipt_hash_mismatch")

    @classmethod
    def issue(
        cls,
        *,
        logical_effect_id: str,
        stable_call_id: str,
        attempt_ordinal: int,
        output: Mapping[str, JsonValue],
        receipt_ref: str,
    ) -> PersonalWorkflowEffectReceiptV1:
        values = {
            "logical_effect_id": logical_effect_id,
            "stable_call_id": stable_call_id,
            "attempt_ordinal": attempt_ordinal,
            "output": copy.deepcopy(dict(output)),
            "receipt_ref": receipt_ref,
        }
        return cls(
            **values,
            receipt_hash=_hash(
                {"schema": "personal-workflow-effect-receipt-v1", **values}
            ),
        )


class PersonalWorkflowEffectPort(Protocol):
    """Projection port backed by the existing Workflow effect journal."""

    async def execute(
        self,
        *,
        node_id: str,
        execution_identity: NodeExecutionIdentity,
        attempt_ordinal: int,
        logical_effect_id: str,
        stable_call_id: str,
        tool_binding: Mapping[str, Any],
        arguments: Mapping[str, JsonValue],
    ) -> PersonalWorkflowEffectReceiptV1: ...


EffectContextSource = (
    EffectExecutionContext
    | Callable[
        [str],
        EffectExecutionContext | Awaitable[EffectExecutionContext],
    ]
)


class JournaledPersonalWorkflowEffects:
    """Map Personal Workflow calls onto the existing EffectJournal authority."""

    def __init__(
        self,
        *,
        journal: EffectJournal,
        tool_registry: ToolRegistry,
        session_id: str,
        effect_context: EffectContextSource,
        execution_context: ToolExecutionContext | None = None,
        dispatch_fence_acquirer: Callable[
            [str], Awaitable[Any]
        ] | None = None,
    ) -> None:
        if not session_id.strip():
            raise ValueError("personal workflow session_id is required")
        self._journal = journal
        self._registry = tool_registry
        self._session_id = session_id
        self._effect_context = effect_context
        self._execution_context = execution_context
        self._dispatch_fence_acquirer = dispatch_fence_acquirer

    async def _context(self, node_id: str) -> EffectExecutionContext:
        selected: EffectExecutionContext | Awaitable[EffectExecutionContext]
        if callable(self._effect_context):
            selected = self._effect_context(node_id)
        else:
            selected = self._effect_context
        if inspect.isawaitable(selected):
            selected = await selected
        if not isinstance(selected, EffectExecutionContext):
            raise PersonalWorkflowRuntimeError(
                "personal_effect_context_unavailable"
            )
        if (
            selected.workflow_name != "personal_workflow"
            or selected.workflow_version != "v1"
        ):
            raise PersonalWorkflowRuntimeError(
                "personal_effect_context_identity_mismatch"
            )
        return selected

    @staticmethod
    def _policy_hash(spec: Any) -> str:
        policy = getattr(spec, "effect_policy", None)
        if policy is None:
            raise PersonalWorkflowRuntimeError(
                "personal_effect_policy_missing"
            )
        return fingerprint_json(
            {
                "policy_id": str(policy.policy_id),
                "version": str(policy.version),
                "kind": str(getattr(policy.kind, "value", policy.kind)),
                "max_attempts": int(policy.max_attempts),
                "reusable_across_branches": bool(
                    policy.reusable_across_branches
                ),
            }
        )

    def _verify_frozen_binding(
        self,
        *,
        prepared: Any,
        tool_binding: Mapping[str, Any],
    ) -> None:
        try:
            spec = self._registry.resolve_prepared_spec(prepared)
        except (KeyError, RuntimeError, ValueError) as exc:
            raise PersonalWorkflowRuntimeError(
                "personal_tool_binding_stale"
            ) from exc
        build = (
            None
            if spec is None
            else getattr(spec, "execution_build_identity", None)
        )
        expected = {
            "stable_handler_id": (
                "" if spec is None else str(spec.stable_handler_id)
            ),
            "tool_name": prepared.tool_name,
            "spec_ref": prepared.tool_spec_fingerprint,
            "schema_hash": prepared.schema_hash,
            "execution_build_identity": (
                "" if build is None else str(build.fingerprint)
            ),
            "effect_policy_hash": (
                "" if spec is None else self._policy_hash(spec)
            ),
            "effect": "read_only",
            "idempotent": True,
        }
        if dict(tool_binding) != expected:
            raise PersonalWorkflowRuntimeError(
                "personal_tool_binding_stale"
            )
        policy_kind = str(
            getattr(
                getattr(getattr(spec, "effect_policy", None), "kind", None),
                "value",
                "",
            )
        )
        if policy_kind not in {
            "idempotent_read",
            "deterministic_reusable",
        }:
            raise PersonalWorkflowRuntimeError(
                "personal_tool_effect_not_idempotent"
            )

    async def execute(
        self,
        *,
        node_id: str,
        execution_identity: NodeExecutionIdentity,
        attempt_ordinal: int,
        logical_effect_id: str,
        stable_call_id: str,
        tool_binding: Mapping[str, Any],
        arguments: Mapping[str, JsonValue],
    ) -> PersonalWorkflowEffectReceiptV1:
        context = await self._context(node_id)
        if execution_identity.run_id != context.fence.run_id:
            raise PersonalWorkflowRuntimeError(
                "personal_effect_run_identity_mismatch"
            )
        if (
            isinstance(attempt_ordinal, bool)
            or not isinstance(attempt_ordinal, int)
            or attempt_ordinal < 0
            or attempt_ordinal != execution_identity.attempt - 1
        ):
            raise PersonalWorkflowRuntimeError(
                "personal_effect_attempt_identity_mismatch"
            )
        node_execution_id = hashlib.sha256(
            "|".join(
                (
                    execution_identity.run_id,
                    execution_identity.checkpoint_id,
                    execution_identity.task_id,
                    execution_identity.node_id,
                )
            ).encode("utf-8")
        ).hexdigest()
        context = EffectExecutionContext(
            journal=context.journal,
            fence=context.fence,
            node_execution_id=node_execution_id,
            workflow_name=context.workflow_name,
            workflow_version=context.workflow_version,
            node_id=execution_identity.node_id,
            reuse_checkpoint=context.reuse_checkpoint,
        )
        execution_context = self._execution_context
        if execution_context is None:
            raise PersonalWorkflowRuntimeError(
                "personal_tool_execution_context_unavailable"
            )
        from dataclasses import replace

        execution_context = replace(
            execution_context,
            call_id=stable_call_id,
            effect_id=logical_effect_id,
        )
        spec_ref = str(tool_binding.get("spec_ref") or "")
        if (
            len(spec_ref) != 64
            or any(character not in "0123456789abcdef" for character in spec_ref)
        ):
            raise PersonalWorkflowRuntimeError(
                "personal_tool_spec_ref_invalid"
            )
        catalog_snapshot_ref = (
            execution_context.capability_snapshot_ref.strip()
        )
        if not catalog_snapshot_ref:
            raise PersonalWorkflowRuntimeError(
                "personal_catalog_snapshot_ref_unavailable"
            )
        prepared = self._registry.prepare_frozen_call(
            str(tool_binding["tool_name"]),
            dict(arguments),
            self._session_id,
            stable_call_id,
            expected_tool_spec_fingerprint=spec_ref,
            catalog_snapshot_ref=catalog_snapshot_ref,
            execution_context=execution_context,
        )
        self._verify_frozen_binding(
            prepared=prepared,
            tool_binding=tool_binding,
        )
        dispatch = ToolDispatchPort(
            self._registry,
            session_id=self._session_id,
            effect_context=context,
            execution_context=execution_context,
            workflow_name="personal_workflow",
            dispatch_fence_acquirer=self._dispatch_fence_acquirer,
        )
        result = await dispatch.dispatch(
            (prepared,),
            workflow_step_id=logical_effect_id,
            prior_results={},
            authorizations={},
        )
        envelope = result.get(stable_call_id)
        if not isinstance(envelope, Mapping):
            raise PersonalWorkflowRuntimeError(
                "personal_tool_outcome_missing"
            )
        raw_outcome = envelope.get("outcome")
        if not isinstance(raw_outcome, Mapping):
            raise PersonalWorkflowRuntimeError(
                "personal_tool_outcome_invalid"
            )
        outcome = NormalizedToolOutcome.from_dict(raw_outcome)
        if outcome.state is not ToolOutcomeState.SUCCESS:
            raise PersonalWorkflowRuntimeError("personal_tool_failed")
        raw_value = thaw_json(outcome.value)
        if (
            isinstance(raw_value, Mapping)
            and raw_value.get("ok") is True
            and "result" in raw_value
        ):
            raw_value = thaw_json(raw_value["result"])
        output: Mapping[str, JsonValue]
        if isinstance(raw_value, Mapping):
            output = dict(raw_value)
        else:
            output = {"result": raw_value}
        effect_id = str(envelope.get("effect_id") or "")
        if not effect_id:
            raise PersonalWorkflowRuntimeError(
                "personal_effect_receipt_missing"
            )
        return PersonalWorkflowEffectReceiptV1.issue(
            logical_effect_id=logical_effect_id,
            stable_call_id=stable_call_id,
            attempt_ordinal=attempt_ordinal,
            output=output,
            receipt_ref=f"workflow-effect:{effect_id}",
        )


def _pointer(value: object, pointer: str) -> JsonValue:
    current: object = value
    for raw in pointer.split("/")[1:]:
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if key not in current:
                raise PersonalWorkflowRuntimeError("personal_pointer_missing")
            current = current[key]
        elif isinstance(current, list):
            try:
                current = current[int(key)]
            except (ValueError, IndexError) as exc:
                raise PersonalWorkflowRuntimeError(
                    "personal_pointer_missing"
                ) from exc
        else:
            raise PersonalWorkflowRuntimeError("personal_pointer_missing")
    return copy.deepcopy(current)  # type: ignore[return-value]


def _resolved_bindings(
    node: PersonalWorkflowNode,
    *,
    inputs: Mapping[str, JsonValue],
    outputs: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    root = {"input": dict(inputs), "nodes": dict(outputs)}
    return {
        name: _pointer(root, pointer)
        for name, pointer in node.bindings.items()
    }


def _condition(operator: str, values: Mapping[str, JsonValue]) -> bool:
    left = values.get("left")
    right = values.get("right")
    if operator == "exists":
        return left is not None
    if operator == "eq":
        return left == right
    if operator == "ne":
        return left != right
    if operator == "in":
        return isinstance(right, (list, str, dict)) and left in right
    try:
        if operator == "gt":
            return left > right  # type: ignore[operator]
        if operator == "gte":
            return left >= right  # type: ignore[operator]
        if operator == "lt":
            return left < right  # type: ignore[operator]
        if operator == "lte":
            return left <= right  # type: ignore[operator]
    except TypeError as exc:
        raise PersonalWorkflowRuntimeError(
            "personal_condition_type_mismatch"
        ) from exc
    raise PersonalWorkflowRuntimeError("personal_condition_unknown")


class PersonalWorkflowRuntime:
    """Interpret a frozen graph; only EffectJournal owns durable tool facts."""

    def __init__(
        self,
        effects: PersonalWorkflowEffectPort,
        *,
        after_effect_settled: (
            Callable[
                [PersonalWorkflowEffectReceiptV1],
                None | Awaitable[None],
            ]
            | None
        ) = None,
    ) -> None:
        self._effects = effects
        self._after_effect_settled = after_effect_settled

    async def execute(
        self,
        *,
        child_run_id: str,
        selection: PersonalWorkflowSelectionV1,
        inputs: Mapping[str, JsonValue],
        execution_identity: NodeExecutionIdentity,
    ) -> Mapping[str, JsonValue]:
        if not isinstance(selection, PersonalWorkflowSelectionV1):
            raise PersonalWorkflowRuntimeError(
                "personal_selection_typed_required"
            )
        workflow = selection.workflow
        if (
            execution_identity.run_id != child_run_id
            or execution_identity.workflow_name != "personal_workflow"
            or execution_identity.workflow_version != "v1"
            or execution_identity.attempt < 1
        ):
            raise PersonalWorkflowRuntimeError(
                "personal_execution_identity_mismatch"
            )
        attempt_ordinal = execution_identity.attempt - 1
        outputs: dict[str, JsonValue] = {}
        for node in self._topological_nodes(workflow.nodes):
            bindings = _resolved_bindings(
                node, inputs=inputs, outputs=outputs
            )
            if node.type == "input":
                output: JsonValue = copy.deepcopy(dict(inputs))
            elif node.type == "template":
                try:
                    output = str(node.config["template"]).format_map(bindings)
                except KeyError as exc:
                    raise PersonalWorkflowRuntimeError(
                        "personal_template_binding_missing"
                    ) from exc
            elif node.type == "condition":
                output = _condition(str(node.config["operator"]), bindings)
            elif node.type == "tool_call":
                execution = plan_personal_workflow_tool_node(
                    workflow,
                    node_id=node.id,
                    child_run_id=child_run_id,
                    selection_id=selection.selection_id,
                    attempt_ordinal=attempt_ordinal,
                )
                receipt = await self._effects.execute(
                    node_id=node.id,
                    execution_identity=execution_identity,
                    attempt_ordinal=attempt_ordinal,
                    logical_effect_id=execution.logical_effect_id,
                    stable_call_id=execution.stable_call_id,
                    tool_binding=execution.tool_binding,
                    arguments=bindings,
                )
                self._verify_receipt(
                    receipt,
                    child_run_id=child_run_id,
                    selection=selection,
                    node_id=node.id,
                )
                if self._after_effect_settled is not None:
                    settled = self._after_effect_settled(receipt)
                    if inspect.isawaitable(settled):
                        await settled
                output = copy.deepcopy(dict(receipt.output))
            elif node.type == "output":
                output = copy.deepcopy(bindings)
            else:
                raise PersonalWorkflowRuntimeError(
                    "personal_node_type_unknown"
                )
            outputs[node.id] = copy.deepcopy(output)
        root = {"nodes": outputs}
        return {
            name: _pointer(root, pointer)
            for name, pointer in workflow.outputs.items()
        }

    @staticmethod
    def _topological_nodes(
        nodes: tuple[PersonalWorkflowNode, ...],
    ) -> tuple[PersonalWorkflowNode, ...]:
        by_id = {node.id: node for node in nodes}
        dependencies = {
            node.id: {
                pointer.split("/", 3)[2]
                for pointer in node.bindings.values()
                if pointer.startswith("/nodes/")
            }
            for node in nodes
        }
        result: list[PersonalWorkflowNode] = []
        done: set[str] = set()
        while len(result) < len(nodes):
            ready = sorted(
                node_id
                for node_id, required in dependencies.items()
                if node_id not in done and required <= done
            )
            if not ready:
                raise PersonalWorkflowRuntimeError("personal_graph_cycle")
            for node_id in ready:
                result.append(by_id[node_id])
                done.add(node_id)
        return tuple(result)

    @staticmethod
    def _verify_receipt(
        receipt: PersonalWorkflowEffectReceiptV1,
        *,
        child_run_id: str,
        selection: PersonalWorkflowSelectionV1,
        node_id: str,
    ) -> None:
        expected = personal_workflow_logical_effect_id(
            child_run_id=child_run_id,
            selection_id=selection.selection_id,
            graph_hash=selection.graph_hash,
            node_id=node_id,
        )
        if receipt.logical_effect_id != expected:
            raise PersonalWorkflowRuntimeError(
                "personal_effect_receipt_identity_mismatch"
            )


ExecutionContextFactory = Callable[
    [RunStartSnapshotRecord],
    ToolExecutionContext
    | None
    | Awaitable[ToolExecutionContext | None],
]
RunStartSnapshotReader = Callable[
    [str],
    RunStartSnapshotRecord
    | None
    | Awaitable[RunStartSnapshotRecord | None],
]


def _tool_execution_context_from_start(
    snapshot: RunStartSnapshotRecord,
) -> ToolExecutionContext:
    """Rebuild host-only tool authority from the immutable execution RunStart."""

    run_context = RunContext.from_dict(
        json.loads(snapshot.run_context_json)
    )
    capability_snapshot = json.loads(snapshot.capability_snapshot_json)
    if not isinstance(capability_snapshot, Mapping) or (
        fingerprint_json(dict(capability_snapshot))
        != snapshot.capability_snapshot_hash
        or capability_snapshot.get("capability_hash")
        != run_context.capability_hash
    ):
        raise PersonalWorkflowRuntimeError(
            "personal_run_start_capability_mismatch"
        )
    extensions = capability_snapshot.get("host_extensions")
    companion = (
        extensions.get("deskpet.companion.selection.v1")
        if isinstance(extensions, Mapping)
        else None
    )
    active_scope_ids = tuple(
        sorted(
            str(item)
            for item in (
                companion.get("active_skill_scope_ids", ())
                if isinstance(companion, Mapping)
                else ()
            )
        )
    )
    exact_refs: list[Mapping[str, Any]] = []
    if isinstance(companion, Mapping):
        for scope in companion.get("skill_invocation_scopes", ()):
            if not isinstance(scope, Mapping):
                continue
            for item in scope.get("allowed_tool_refs", ()):
                if isinstance(item, Mapping):
                    exact_refs.append(dict(item))
    ref_hashes = tuple(
        sorted({fingerprint_json(dict(item)) for item in exact_refs})
    )
    return HostContextFactory().create_tool_context(
        run_context,
        run_id=snapshot.run_id,
        call_id="personal-start-pending-call",
        effect_id="personal-start-pending-effect",
        capability_snapshot_ref=str(
            capability_snapshot.get("catalog_snapshot_ref")
            or snapshot.capability_snapshot_hash
        ),
        active_skill_scope_ids=active_scope_ids,
        effective_skill_tool_ref_hashes=ref_hashes,
        effective_skill_tool_refs_hash=fingerprint_json(
            sorted(
                (dict(item) for item in exact_refs),
                key=_canonical_json,
            )
        ),
    )


def build_personal_runtime_adapter(
    runtime: PersonalWorkflowRuntime | None = None,
    *,
    journal: EffectJournal | None = None,
    tool_registry: ToolRegistry | None = None,
    run_start_snapshot_reader: RunStartSnapshotReader | None = None,
    execution_context_factory: ExecutionContextFactory | None = None,
    dispatch_fence_acquirer: Callable[
        [str], Awaitable[Any]
    ] | None = None,
) -> WorkflowRuntimeAdapter:
    """Build the static adapter from an injected runtime or production ports.

    Production composition supplies the existing workflow.db ``EffectJournal``
    and process ``ToolRegistry``.  No Personal Workflow table, token, decision,
    or checkpoint authority is created here.
    """

    if runtime is not None and not isinstance(
        runtime, PersonalWorkflowRuntime
    ):
        raise TypeError("personal_workflow_runtime_required")
    if runtime is None and (
        not isinstance(journal, EffectJournal)
        or not isinstance(tool_registry, ToolRegistry)
        or not callable(run_start_snapshot_reader)
    ):
        raise TypeError(
            "personal workflow requires EffectJournal, ToolRegistry, and "
            "a trusted RunStart reader"
        )

    async def trusted_context_factory(
        row: Mapping[str, Any],
        start_payload: Mapping[str, Any],
    ) -> WorkflowContext:
        selected_runtime = runtime
        if selected_runtime is None:
            assert journal is not None and tool_registry is not None
            row_values = dict(row)
            run_id = str(row_values.get("run_id") or "").strip()
            session_id = str(
                row_values.get("session_id")
                or row_values.get("delivery_session_id")
                or ""
            ).strip()
            owner = str(row_values.get("lease_owner") or "").strip()
            lease_epoch = int(row_values.get("lease_epoch") or 0)
            run_version = int(row_values.get("run_version") or 0)
            if (
                not run_id
                or not session_id
                or not owner
                or lease_epoch < 1
                or run_version < 0
            ):
                raise PersonalWorkflowRuntimeError(
                    "personal_workflow_fence_unavailable"
                )
            fence = RunFence(
                run_id,
                owner,
                lease_epoch,
                run_version,
            )
            node_digest = _hash(
                {
                    "run_id": run_id,
                    "node_id": "execute",
                    "checkpoint_ns": str(
                        row_values.get("head_checkpoint_ns") or ""
                    ),
                    "checkpoint_id": str(
                        row_values.get("head_checkpoint_id") or "root"
                    ),
                }
            )
            effect_context = EffectExecutionContext(
                journal=journal,
                fence=fence,
                node_execution_id=f"effect-node-{node_digest[:32]}",
                workflow_name="personal_workflow",
                workflow_version="v1",
                node_id="execute",
            )
            execution_context = None
            assert run_start_snapshot_reader is not None
            trusted_start = run_start_snapshot_reader(run_id)
            if inspect.isawaitable(trusted_start):
                trusted_start = await trusted_start
            if not isinstance(trusted_start, RunStartSnapshotRecord):
                raise PersonalWorkflowRuntimeError(
                    "personal_run_start_unavailable"
                )
            if trusted_start.run_id != run_id:
                raise PersonalWorkflowRuntimeError(
                    "personal_run_start_identity_mismatch"
                )
            if execution_context_factory is not None:
                execution_context = execution_context_factory(
                    trusted_start,
                )
                if inspect.isawaitable(execution_context):
                    execution_context = await execution_context
                if execution_context is not None and not isinstance(
                    execution_context,
                    ToolExecutionContext,
                ):
                    raise TypeError(
                        "personal execution context factory returned "
                        "an unsupported value"
                    )
            else:
                execution_context = _tool_execution_context_from_start(
                    trusted_start
                )
            selected_runtime = PersonalWorkflowRuntime(
                JournaledPersonalWorkflowEffects(
                    journal=journal,
                    tool_registry=tool_registry,
                    session_id=session_id,
                    effect_context=effect_context,
                    execution_context=execution_context,
                    dispatch_fence_acquirer=dispatch_fence_acquirer,
                )
            )
        return WorkflowContext(
            ports={"personal_workflow_runtime": selected_runtime}
        )

    return WorkflowRuntimeAdapter(
        workflow_name="personal_workflow",
        workflow_version="v1",
        state_factory=initial_state,
        context_factory=trusted_context_factory,
    )


__all__ = [
    "ExecutionContextFactory",
    "JournaledPersonalWorkflowEffects",
    "PersonalWorkflowEffectPort",
    "PersonalWorkflowEffectReceiptV1",
    "PersonalWorkflowRuntime",
    "PersonalWorkflowRuntimeError",
    "RunStartSnapshotReader",
    "build_personal_runtime_adapter",
]
