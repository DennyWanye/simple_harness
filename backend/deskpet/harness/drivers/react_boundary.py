"""Durable ReAct command boundary and its JSON codec."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping

from deskpet.capabilities.effect_plan import BrokeredCommandBoundary
from deskpet.execution.contracts import (
    OutcomeStatus,
    ProviderLaunchSnapshot,
    RunContext,
    RunCreate,
    thaw_json,
)
from deskpet.execution.evidence import EvidenceSelection, UNKNOWN_EVIDENCE
from deskpet.harness.skill_scope import (
    skill_tool_intersection_from_snapshot,
)
from deskpet.harness.ports import (
    AttachmentPolicy,
    DelegateRun,
    DriverEvent,
    DriverStart,
    JoinPolicy,
    OpenDecision,
)
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.workflows.effects import (
    NormalizedToolOutcome,
    PreparedToolCall,
)

def _call_payload(call: PreparedToolCall) -> dict[str, Any]:
    return call.to_dict()
def _load_call(value: Mapping[str, Any]) -> PreparedToolCall:
    return PreparedToolCall.from_dict(value)
def _context_payload(context: ToolExecutionContext) -> dict[str, Any]:
    return {'scope_id': context.scope_id, 'session_id': context.session_id, 'request_id': context.request_id, 'origin': context.origin, 'root_run_id': context.root_run_id, 'parent_run_id': context.parent_run_id, 'turn_id': context.turn_id, 'venue': context.venue, 'workspace': context.workspace, 'write_scope_root': context.write_scope_root, 'capability_hash': context.capability_hash, 'scope_hash': context.scope_hash, 'provider_plan': list(context.provider_plan), 'run_id': context.run_id, 'command_id': context.command_id, 'call_id': context.call_id, 'effect_id': context.effect_id, 'trace_id': context.trace_id, 'owner_key': context.owner_key, 'profile_generation': context.profile_generation, 'binding_epoch': context.binding_epoch, 'capability_snapshot_ref': context.capability_snapshot_ref, 'active_skill_scope_ids': list(context.active_skill_scope_ids), 'effective_skill_tool_ref_hashes': list(context.effective_skill_tool_ref_hashes), 'effective_skill_tool_refs_hash': context.effective_skill_tool_refs_hash}
def _load_context(value: Mapping[str, Any]) -> ToolExecutionContext:
    data = dict(value)
    data['provider_plan'] = tuple((str(item) for item in data.get('provider_plan', ())))
    data['active_skill_scope_ids'] = tuple(
        str(item) for item in data.get('active_skill_scope_ids', ())
    )
    data['effective_skill_tool_ref_hashes'] = tuple(
        str(item)
        for item in data.get('effective_skill_tool_ref_hashes', ())
    )
    return ToolExecutionContext(**data)


def _skill_tool_intersection(
    capability_snapshot: Mapping[str, Any],
    request_payload: Mapping[str, Any],
    *,
    active_scope_ids: tuple[str, ...] | None = None,
    activated_scopes: tuple[Mapping[str, Any], ...] = (),
) -> Any | None:
    return skill_tool_intersection_from_snapshot(
        capability_snapshot,
        request_payload,
        active_scope_ids=active_scope_ids,
        activated_scopes=activated_scopes,
    )
def _outcome_payload(outcome: NormalizedToolOutcome) -> dict[str, Any]:
    return outcome.to_dict()
def _load_outcome(value: Mapping[str, Any]) -> NormalizedToolOutcome:
    return NormalizedToolOutcome.from_dict(value)
@dataclass(frozen=True)
class ReactCommandBoundary:
    run_id: str
    session_id: str
    command_id: str
    command_kind: str
    canonical_messages: tuple[Mapping[str, Any], ...]
    session_projection_cursor: int
    prepared_context_ref: str | None
    tool_set_snapshot_ref: str | None
    pending_calls: tuple[PreparedToolCall, ...]
    tool_contexts: tuple[ToolExecutionContext, ...]
    outcomes: tuple[NormalizedToolOutcome | None, ...]
    provider_state: Mapping[str, Any]
    iteration: int
    completion_state: Mapping[str, Any]
    capability_snapshot: Mapping[str, Any] = field(default_factory=dict)
    request_payload: Mapping[str, Any] = field(default_factory=dict)
    run_context: RunContext | None = None
    run_spec: RunCreate | None = None
    pending_decision: DriverEvent | None = None
    pending_delegate: DriverEvent | None = None
    version: int = 0
    outcome_statuses: tuple[OutcomeStatus | None, ...] = ()
    outcome_metadata: tuple[Mapping[str, Any], ...] = ()
    authorization_indexes: tuple[int, ...] = ()
    durable_indexes: tuple[int, ...] = ()
    confirm_only_names: tuple[str, ...] = ()
    confirm_only_snapshot_ref: str | None = None
    confirm_only_snapshot_hash: str | None = None
    raw_failures: tuple[Mapping[str, Any], ...] = ()
    provider_call_order: tuple[str, ...] = ()
    brokered_commands: tuple[BrokeredCommandBoundary, ...] = ()
    capability_snapshot_ref: str | None = None
    active_skill_scope_ids: tuple[str, ...] = ()
    activated_skill_scopes: tuple[Mapping[str, Any], ...] = ()
    effective_skill_tool_refs_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.outcome_statuses:
            object.__setattr__(self, 'outcome_statuses', (None,) * len(self.outcomes))
        if not self.outcome_metadata:
            object.__setattr__(self, 'outcome_metadata', tuple(MappingProxyType({}) for _ in self.outcomes))
        if not (len(self.pending_calls) == len(self.tool_contexts) == len(self.outcomes) == len(self.outcome_statuses) == len(self.outcome_metadata)):
            raise ValueError('pending calls, contexts and outcomes must align')
        commands = tuple(self.brokered_commands)
        if len({item.parent_index for item in commands}) != len(commands):
            raise ValueError("brokered parent indexes must be unique")
        for command in commands:
            if command.parent_index >= len(self.pending_calls):
                raise ValueError("brokered parent index is outside the provider batch")
            call = self.pending_calls[command.parent_index]
            if command.provider_call_id != call.stable_call_id:
                raise ValueError("brokered command provider call binding mismatch")
            if (
                command.status != "terminal"
                and self.outcomes[command.parent_index] is not None
            ):
                raise ValueError(
                    "active brokered parent cannot have a provider-visible outcome"
                )
        object.__setattr__(self, "brokered_commands", commands)
        snapshot_ref = str(
            self.capability_snapshot.get("catalog_snapshot_ref") or ""
        )
        if self.capability_snapshot_ref is None:
            object.__setattr__(
                self,
                "capability_snapshot_ref",
                snapshot_ref or None,
            )
        elif self.capability_snapshot_ref != snapshot_ref:
            raise ValueError(
                "command boundary capability snapshot ref mismatch"
            )
        selection = (
            self.capability_snapshot.get("host_extensions", {})
            .get("deskpet.companion.selection.v1")
            if isinstance(
                self.capability_snapshot.get("host_extensions"), Mapping
            )
            else None
        )
        initial_active = (
            ()
            if not isinstance(selection, Mapping)
            else tuple(
                str(item)
                for item in selection.get("active_skill_scope_ids", ())
            )
        )
        active_scopes = tuple(
            sorted(
                dict.fromkeys(
                    self.active_skill_scope_ids or initial_active
                )
            )
        )
        object.__setattr__(self, "active_skill_scope_ids", active_scopes)
        activated_scopes = tuple(
            MappingProxyType(dict(thaw_json(item)))
            for item in self.activated_skill_scopes
        )
        object.__setattr__(
            self, "activated_skill_scopes", activated_scopes
        )
        intersection = _skill_tool_intersection(
            self.capability_snapshot,
            self.request_payload,
            active_scope_ids=active_scopes,
            activated_scopes=activated_scopes,
        )
        computed_hash = (
            None
            if intersection is None
            else intersection.effective_tool_refs_hash
        )
        if self.effective_skill_tool_refs_hash is None:
            object.__setattr__(
                self,
                "effective_skill_tool_refs_hash",
                computed_hash,
            )
        elif self.effective_skill_tool_refs_hash != computed_hash:
            raise ValueError(
                "command boundary effective Skill ToolRefs drifted"
            )
        for context in self.tool_contexts:
            context_intersection = _skill_tool_intersection(
                self.capability_snapshot,
                self.request_payload,
                active_scope_ids=context.active_skill_scope_ids,
                activated_scopes=activated_scopes,
            )
            if (
                context.capability_snapshot_ref != (snapshot_ref or "")
                or context.effective_skill_tool_ref_hashes
                != (
                    ()
                    if context_intersection is None
                    else context_intersection.effective_tool_ref_hashes
                )
                or context.effective_skill_tool_refs_hash
                != (
                    ""
                    if context_intersection is None
                    else context_intersection.effective_tool_refs_hash
                )
            ):
                raise ValueError(
                    "tool context differs from the trusted Skill ToolRef "
                    "boundary"
                )
        object.__setattr__(
            self,
            "raw_failures",
            tuple(
                MappingProxyType(dict(thaw_json(item)))
                for item in self.raw_failures
            ),
        )
        identities = {
            *(call.stable_call_id for call in self.pending_calls),
            *(str(item.get("provider_call_id") or "") for item in self.raw_failures),
        }
        if "" in identities:
            raise ValueError("provider call identities may not be empty")
        if self.provider_call_order:
            if set(self.provider_call_order) != identities or len(
                self.provider_call_order
            ) != len(identities):
                raise ValueError("provider call order must cover each call exactly once")
        else:
            object.__setattr__(
                self,
                "provider_call_order",
                (
                    *(call.stable_call_id for call in self.pending_calls),
                    *(
                        str(item["provider_call_id"])
                        for item in self.raw_failures
                    ),
                ),
            )
        object.__setattr__(
            self,
            "canonical_messages",
            tuple(
                MappingProxyType(dict(thaw_json(item)))
                for item in self.canonical_messages
            ),
        )
        for name in ('provider_state', 'completion_state', 'capability_snapshot', 'request_payload'):
            object.__setattr__(
                self,
                name,
                MappingProxyType(dict(thaw_json(getattr(self, name)))),
            )
        confirm_only = tuple(sorted(dict.fromkeys(self.confirm_only_names)))
        if confirm_only and (
            not self.confirm_only_snapshot_ref
            or not self.confirm_only_snapshot_hash
        ):
            raise ValueError("confirm-only boundary is missing frozen snapshot fences")
        object.__setattr__(self, "confirm_only_names", confirm_only)
    @property
    def pending_indexes(self) -> tuple[int, ...]:
        return tuple((index for index, outcome in enumerate(self.outcomes) if outcome is None))
    @property
    def provider_execution_indexes(self) -> tuple[int, ...]:
        deferred = {
            command.parent_index
            for command in self.brokered_commands
            if command.status != "terminal"
        }
        return tuple(
            index for index in self.pending_indexes if index not in deferred
        )
    @property
    def active_brokered_commands(self) -> tuple[BrokeredCommandBoundary, ...]:
        return tuple(
            command
            for command in self.brokered_commands
            if command.status != "terminal"
        )
    def with_outcomes(self, updates: Mapping[int, NormalizedToolOutcome], statuses: Mapping[int, OutcomeStatus], metadata: Mapping[int, Mapping[str, Any]] | None=None) -> 'ReactCommandBoundary':
        outcomes = list(self.outcomes)
        outcome_statuses = list(self.outcome_statuses)
        outcome_metadata = list(self.outcome_metadata)
        for index, outcome in updates.items():
            if outcomes[index] is not None and outcomes[index] != outcome:
                raise ValueError('outcome already recorded with different value')
            outcomes[index] = outcome
            outcome_statuses[index] = OutcomeStatus(statuses[index])
            if metadata and index in metadata:
                outcome_metadata[index] = MappingProxyType(
                    dict(thaw_json(metadata[index]))
                )
        return replace(self, outcomes=tuple(outcomes), outcome_statuses=tuple(outcome_statuses), outcome_metadata=tuple(outcome_metadata), version=self.version + 1)
    def to_start(self, scoped_evidence: EvidenceSelection | None=UNKNOWN_EVIDENCE) -> DriverStart:
        launch = self.completion_state if self.command_kind == 'provider_launch' else {}
        snapshot = launch.get('provider_launch_snapshot')
        return DriverStart(run_id=self.run_id, session_id=self.session_id, canonical_messages=self.canonical_messages, session_projection_cursor=self.session_projection_cursor, prepared_context_ref=self.prepared_context_ref, tool_set_snapshot_ref=self.tool_set_snapshot_ref, provider_state=self.provider_state, iteration=self.iteration, completion_state=self.completion_state, run_context=self.run_context, run_spec=self.run_spec, capability_snapshot=self.capability_snapshot, request_payload=self.request_payload, scoped_evidence=scoped_evidence, launch_operation_id=str(launch['launch_operation_id']) if snapshot is not None else None, provider_launch_snapshot=ProviderLaunchSnapshot.from_dict(snapshot) if isinstance(snapshot, Mapping) else None, active_skill_scope_ids=self.active_skill_scope_ids, activated_skill_scopes=self.activated_skill_scopes)
    def to_payload(self) -> dict[str, Any]:
        delegate = self.pending_delegate
        decision = self.pending_decision
        return {
            "session_id": self.session_id,
            "command_id": self.command_id,
            "command_kind": self.command_kind,
            "canonical_messages": [
                dict(item) for item in self.canonical_messages
            ],
            "session_projection_cursor": self.session_projection_cursor,
            "prepared_context_ref": self.prepared_context_ref,
            "tool_set_snapshot_ref": self.tool_set_snapshot_ref,
            "calls": [_call_payload(call) for call in self.pending_calls],
            "contexts": [
                _context_payload(context) for context in self.tool_contexts
            ],
            "outcomes": [
                _outcome_payload(outcome) if outcome is not None else None
                for outcome in self.outcomes
            ],
            "outcome_statuses": [
                status.value if status is not None else None
                for status in self.outcome_statuses
            ],
            "outcome_metadata": [
                dict(item) for item in self.outcome_metadata
            ],
            "authorization_indexes": list(self.authorization_indexes),
            "durable_indexes": list(self.durable_indexes),
            "confirm_only_names": list(self.confirm_only_names),
            "confirm_only_snapshot_ref": self.confirm_only_snapshot_ref,
            "confirm_only_snapshot_hash": self.confirm_only_snapshot_hash,
            "raw_failures": [dict(item) for item in self.raw_failures],
            "provider_call_order": list(self.provider_call_order),
            "brokered_commands": [
                item.to_mapping() for item in self.brokered_commands
            ],
            "capability_snapshot_ref": self.capability_snapshot_ref,
            "active_skill_scope_ids": list(self.active_skill_scope_ids),
            "activated_skill_scopes": [
                dict(item) for item in self.activated_skill_scopes
            ],
            "effective_skill_tool_refs_hash": (
                self.effective_skill_tool_refs_hash
            ),
            "provider_state": dict(self.provider_state),
            "iteration": self.iteration,
            "completion_state": dict(self.completion_state),
            "capability_snapshot": dict(self.capability_snapshot),
            "request_payload": dict(self.request_payload),
            "run_context": (
                None
                if self.run_context is None
                else self.run_context.to_dict()
            ),
            "run_spec": (
                None if self.run_spec is None else self.run_spec.to_dict()
            ),
            "decision": (
                None
                if decision is None
                else {
                    "run_id": decision.run_id,
                    "command_id": decision.command_id,
                    "decision_id": decision.decision_id,
                    "nonce": decision.nonce,
                    "kind": decision.decision_kind,
                    "prompt": dict(decision.prompt),
                    "prompt_schema_version": decision.prompt_schema_version,
                    "expires_at": decision.expires_at,
                    "domain_kind": decision.domain_kind,
                    "domain_id": decision.domain_id,
                    "call_id": decision.call_id,
                    "effect_id": decision.effect_id,
                    "tool_name": decision.tool_name,
                    "args_hash": decision.args_hash,
                    "capability_hash": decision.capability_hash,
                    "scope_hash": decision.scope_hash,
                }
            ),
            "delegate": (
                None
                if delegate is None
                else {
                    "run_id": delegate.run_id,
                    "command_id": delegate.command_id,
                    "child_request": dict(delegate.child_request),
                    "route_hint": delegate.route_hint,
                    "capability_subset": list(delegate.capability_subset),
                    "attachment_policy": delegate.attachment_policy.value,
                    "join_policy": delegate.join_policy.value,
                    "profile_launch_ticket_ref": delegate.profile_launch_ticket_ref,
                    "profile_launch_request": (
                        None
                        if delegate.profile_launch_request is None
                        else dict(delegate.profile_launch_request)
                    ),
                }
            ),
        }

    @classmethod
    def from_record(cls, record: Any) -> 'ReactCommandBoundary':
        value = dict(record.payload)
        decision = value.get('decision')
        delegate = value.get('delegate')
        return cls(run_id=record.run_id, session_id=str(value['session_id']), command_id=str(value['command_id']), command_kind=str(value['command_kind']), canonical_messages=tuple(value['canonical_messages']), session_projection_cursor=int(value['session_projection_cursor']), prepared_context_ref=value.get('prepared_context_ref'), tool_set_snapshot_ref=value.get('tool_set_snapshot_ref'), pending_calls=tuple((_load_call(item) for item in value['calls'])), tool_contexts=tuple((_load_context(item) for item in value['contexts'])), outcomes=tuple((_load_outcome(item) if item is not None else None for item in value['outcomes'])), provider_state=dict(value['provider_state']), iteration=int(value['iteration']), completion_state=dict(value['completion_state']), capability_snapshot=dict(value.get('capability_snapshot') or {}), request_payload=dict(value.get('request_payload') or {}), run_context=RunContext.from_dict(value['run_context']) if value.get('run_context') is not None else None, run_spec=RunCreate.from_dict(value['run_spec']) if value.get('run_spec') is not None else None, pending_decision=OpenDecision(**decision) if decision is not None else None, pending_delegate=DelegateRun(run_id=str(delegate['run_id']), command_id=str(delegate['command_id']), child_request=dict(delegate['child_request']), route_hint=str(delegate['route_hint']), capability_subset=tuple(delegate['capability_subset']), attachment_policy=AttachmentPolicy(str(delegate['attachment_policy'])), join_policy=JoinPolicy(str(delegate['join_policy'])), profile_launch_ticket_ref=delegate.get('profile_launch_ticket_ref'), profile_launch_request=delegate.get('profile_launch_request')) if delegate is not None else None, version=int(record.version), outcome_statuses=tuple(OutcomeStatus(item) if item is not None else None for item in value.get('outcome_statuses', ())), outcome_metadata=tuple(dict(item) for item in value.get('outcome_metadata', ())), authorization_indexes=tuple(int(item) for item in value.get('authorization_indexes', ())), durable_indexes=tuple(int(item) for item in value.get('durable_indexes', ())), confirm_only_names=tuple(str(item) for item in value.get('confirm_only_names', ())), confirm_only_snapshot_ref=value.get('confirm_only_snapshot_ref'), confirm_only_snapshot_hash=value.get('confirm_only_snapshot_hash'), raw_failures=tuple(dict(item) for item in value.get('raw_failures', ())), provider_call_order=tuple(str(item) for item in value.get('provider_call_order', ())), brokered_commands=tuple(BrokeredCommandBoundary.from_mapping(item) for item in value.get('brokered_commands', ())), capability_snapshot_ref=value.get('capability_snapshot_ref'), active_skill_scope_ids=tuple(str(item) for item in value.get('active_skill_scope_ids', ())), activated_skill_scopes=tuple(dict(item) for item in value.get('activated_skill_scopes', ())), effective_skill_tool_refs_hash=value.get('effective_skill_tool_refs_hash'))

__all__ = ["ReactCommandBoundary"]
