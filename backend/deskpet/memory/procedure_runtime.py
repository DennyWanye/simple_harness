"""Actual Procedure use -> registered Scope tool terminal -> public observation."""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping

import simple_harness as h
from simple_harness_memory import MemoryScope
from deskpet.memory.procedure_applicability import ProcedureUseRejected, current_snapshot
from deskpet.memory.procedure_use_store import ProcedureUseStore, _checked
from deskpet.task_scope.protocol import canonical_hash


class ProcedureRuntime:
    def __init__(self, *, store: ProcedureUseStore, runtime_getter):
        self.store, self.runtime_getter = store, runtime_getter
        self.authorities = self.registry = None
        self._observation_lock = asyncio.Lock()
        self.store.source_verifier = self.verify_observation_source
        from deskpet.operation_audit.procedure_operations import ProcedureOperationJournal
        self.operation_audit = ProcedureOperationJournal(store.path.parent / "operation-audit.db")

    def bind_tools(self, authorities, registry):
        self.authorities, self.registry = authorities, registry

    async def bind_use(self, arguments, context):
        if not isinstance(arguments, Mapping):
            raise ProcedureUseRejected("procedure_use_arguments_invalid")
        arguments = h.thaw_json(arguments)
        arguments.pop("deskpet_public_progress", None)
        if set(arguments) != {"memory_id", "revision", "steps"}:
            raise ProcedureUseRejected("procedure_use_arguments_invalid")
        steps = _decode_steps(arguments["steps"])
        authority = self.authorities.resolve(context.run_id)
        if authority.request_id != context.request_id.value:
            raise ProcedureUseRejected("procedure_use_request_differs")
        runtime = self.runtime_getter()
        manager = await runtime.manager()
        target = await self.operation_audit.invoke(manager, "read_procedure_use_target", principal=runtime.principal(),
            scope=MemoryScope.personal(runtime.principal().actor_id),
            memory_id=arguments["memory_id"], revision=arguments["revision"])
        use_id = "procedure-use:" + canonical_hash([
            runtime.principal().actor_id, context.run_id.value, target.memory_id, target.revision])
        use = await self.store.bind(authority=authority, registry=self.registry,
            target=target, steps=steps, use_id=use_id, context=context)
        return {"procedure_use_id": use_id, "memory_id": target.memory_id,
                "revision": target.revision, "steps": len(use["steps"]),
                "execution_authorized": False}

    async def before_call(self, context, call):
        if call.name in {"procedure_use", "context_route", "task_scope_search", "task_scope_update", "prospective_ack"}:
            return  # These controls are never counted as procedure steps.
        use = await self.store.use_for_run(context.run_id.value)
        if use is None:
            return
        runtime = self.runtime_getter()
        from simple_harness_memory.core.errors import MemoryWriterConflict
        from simple_harness_memory.core.suppression import SuppressionDenied
        try:
            current = await self.operation_audit.invoke(await runtime.manager(), "read_procedure_use_target",
                principal=runtime.principal(), scope=MemoryScope.personal(runtime.principal().actor_id),
                memory_id=use["memory_id"], revision=use["target_revision"])
        except (MemoryWriterConflict, SuppressionDenied) as error:
            # Expected current-visibility/revision changes are a public deny;
            # corruption and cancellation still propagate as actual failures.
            raise ProcedureUseRejected("procedure_use_target_unavailable") from error
        if current.source_hash != use["target_source_hash"]:
            raise ProcedureUseRejected("procedure_use_target_changed")
        await self.store.reserve(authority=self.authorities.resolve(context.run_id),
            registry=self.registry, call=call, context=context)

    async def current_fingerprints(self, run_id):
        if self.authorities is None or self.registry is None:
            return ()
        authority = self.authorities.resolve(run_id)
        async with self.store.connection() as db:
            await db.execute("BEGIN")
            from deskpet.memory.procedure_route import resolve_procedure_route
            try:
                route = await resolve_procedure_route(self.store.path, db, run_id=authority.run_id,
                    subject=self.store.principal.actor_id)
            except ProcedureUseRejected:
                return ()
            async with db.execute("SELECT * FROM procedure_uses WHERE subject=? ORDER BY created_at DESC LIMIT 128",
                                  (self.store.principal.actor_id,)) as cursor:
                uses = tuple(_checked(row) for row in await cursor.fetchall())
        values = set()
        for use in uses:
            try:
                current, _ = current_snapshot(authority, self.registry,
                    tool_names=[step["tool"] for step in use["steps"]], route=route)
                if current.fingerprint == use["applicability_fingerprint"]:
                    values.add(current.fingerprint)
            except (ProcedureUseRejected, KeyError, RuntimeError):
                continue
        return tuple(sorted(values))

    async def observe_group(self, group, manager):
        """Called after registration of the actual whole conversation group.

        The group is re-read from the Host authority, not accepted as a caller's
        self-reported observation. Independent registration/Scope checks remain
        inside the SDK as well.
        """
        runtime = self.runtime_getter()
        actual = await runtime.conversation_evidence_authority.registrations_for_run(group.host_run_id)
        if actual != group:
            raise ProcedureUseRejected("procedure_terminal_group_changed")
        run_id = group.terminal_source[0].run_id
        use = await self.store.use_for_run(run_id)
        if use is None:
            return
        async with self._observation_lock:
            if await self.store.journal(use["use_id"], "applied") is not None:
                return
            prepared = await self.store.journal(use["use_id"], "prepared")
            if prepared is None:
                reservations = await self.store.reservations(use["use_id"])
                if len(reservations) != len(use["steps"]):
                    # A completed Run with fewer actual calls is not a completed
                    # Procedure, regardless of the assistant's final text.
                    return
                by_call = {}
                for registration in group.registrations:
                    link = registration.metadata.tool_causal_link
                    if link is not None:
                        if link.tool_call_id in by_call:
                            raise ProcedureUseRejected("procedure_duplicate_tool_source")
                        by_call[link.tool_call_id] = registration
                verified = []
                for reservation in reservations:
                    registration = by_call.get(reservation["call_id"])
                    if registration is None or registration.metadata.task_scope_id != use["task_scope_id"]:
                        raise ProcedureUseRejected("procedure_scope_terminal_missing")
                    source = registration.envelope.sanitized_payload["source"]["tool_terminal_attestation"]["payload"]["source"]
                    if (source["tool_name"] != reservation["call"]["tool"]
                            or source["sdk_run_id"] != use["sdk_run_id"]
                            or source["internal_call_id"] != reservation["call_id"]):
                        raise ProcedureUseRejected("procedure_actual_terminal_identity_differs")
                    verified.append((registration, source))
                if any(source["state"] != "succeeded" for _, source in verified):
                    return  # A tool error is not proof that the Procedure caused it.
                registration, source = verified[-1]
                span = _terminal_span(registration, source["effect_id"])
                preparation = await self.operation_audit.invoke(manager, "prepare_procedure_observation",
                    principal=runtime.principal(), scope=MemoryScope.personal(runtime.principal().actor_id),
                    observation_id=use["use_id"], target_memory_id=use["memory_id"],
                    target_revision=use["target_revision"], kind=h.ProcedureObservationKind.TERMINAL_OUTCOME,
                    applicability=h.ProcedureApplicabilityContext.from_json(use["applicability"]),
                    hazard=h.ProcedureHazard(use["hazard"]), task_scope_id=use["task_scope_id"], evidence_span=span,
                    terminal_receipt_id=registration.metadata.tool_causal_link.terminal_receipt_id,
                    terminal_receipt_hash=registration.metadata.tool_causal_link.terminal_receipt_hash,
                    outcome=h.ProcedureObservationOutcome.SUCCESS, attributable=True,
                    observed_at=registration.metadata.occurred_at, run_id=run_id,
                    operation_id="procedure-observe:" + canonical_hash(use["use_id"]),
                )
                intent = preparation.intent
                now = self.store.clock()
                authority = h.issue_procedure_observation_authority(intent,
                    authority_id=use["use_id"], issued_at=now, expires_at=now + 300,
                    nonce=canonical_hash([use["use_id"], intent.intent_hash]), issuer_ref="host-procedure-use/v1")
                prepared = await self.store.journal(use["use_id"], "prepared", {
                    "authority": authority.to_json(), "host_run_id": group.host_run_id,
                    "source_registration_hashes": [
                        item.registration_hash for item, _ in verified]})
            authority = h.ProcedureObservationAuthority.from_json(prepared["authority"])
            reference = h.ProcedureObservationAuthorityRef.from_authority(authority)
            result = await self.operation_audit.invoke(manager, "record_procedure_observation", principal=runtime.principal(),
                scope=MemoryScope.personal(runtime.principal().actor_id), reference=reference)
            await self.store.journal(use["use_id"], "applied", {"result": result.to_json(),
                "result_hash": result.result_hash, "reference": reference.to_json()})

    async def verify_observation_source(self, use, prepared, authority):
        """Resolver rechecks real persistent sources independently of the grant."""
        intent = authority.intent
        if (intent.run_id != use["sdk_run_id"] or intent.task_scope_id != use["task_scope_id"]
                or intent.target_memory_id != use["memory_id"] or intent.target_revision != use["target_revision"]
                or intent.applicability.to_json() != use["applicability"]
                or intent.hazard.value != use["hazard"] or not intent.attributable
                or intent.outcome is not h.ProcedureObservationOutcome.SUCCESS):
            raise ProcedureUseRejected("procedure_observation_use_binding_differs")
        group = await self.runtime_getter().conversation_evidence_authority.registrations_for_run(prepared["host_run_id"])
        reservations = await self.store.reservations(use["use_id"])
        if len(reservations) != len(use["steps"]):
            raise ProcedureUseRejected("procedure_observation_steps_incomplete")
        by_call = {r.metadata.tool_causal_link.tool_call_id: r for r in group.registrations
                   if r.metadata.tool_causal_link is not None}
        verified = []
        for reservation in reservations:
            registration = by_call.get(reservation["call_id"])
            if registration is None or registration.metadata.task_scope_id != use["task_scope_id"]:
                raise ProcedureUseRejected("procedure_observation_scope_source_differs")
            source = registration.envelope.sanitized_payload["source"]["tool_terminal_attestation"]["payload"]["source"]
            if (source["tool_name"] != reservation["call"]["tool"] or source["sdk_run_id"] != use["sdk_run_id"]
                    or source["state"] != "succeeded"):
                raise ProcedureUseRejected("procedure_observation_terminal_source_differs")
            verified.append(registration)
        if [r.registration_hash for r in verified] != prepared["source_registration_hashes"]:
            raise ProcedureUseRejected("procedure_observation_source_hashes_differ")
        final = verified[-1]
        source = final.envelope.sanitized_payload["source"]["tool_terminal_attestation"]["payload"]["source"]
        if (intent.evidence_span != _terminal_span(final, source["effect_id"])
                or intent.observed_at != final.metadata.occurred_at
                or intent.terminal_receipt_id != final.metadata.tool_causal_link.terminal_receipt_id
                or intent.terminal_receipt_hash != final.metadata.tool_causal_link.terminal_receipt_hash):
            raise ProcedureUseRejected("procedure_observation_final_receipt_differs")


def _decode_steps(steps):
    """Explicit JSON encoding for H078's closed-object Tool schema subset."""
    if type(steps) is not list or not 1 <= len(steps) <= 16:
        raise ProcedureUseRejected("procedure_use_steps_invalid")
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value
    def constant(_):
        raise ValueError("nonfinite number")
    decoded = []
    for step in steps:
        if type(step) is not dict or set(step) != {"text", "tool", "arguments_json"}:
            raise ProcedureUseRejected("procedure_use_steps_invalid")
        raw = step["arguments_json"]
        try:
            if type(raw) is not str or len(raw.encode()) > 16384:
                raise ValueError("arguments bytes")
            arguments = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
            if type(arguments) is not dict:
                raise ValueError("arguments object")
            canonical_hash(arguments)  # Also rejects numeric overflow/non-JSON values.
        except (TypeError, ValueError, RecursionError, UnicodeError) as error:
            raise ProcedureUseRejected("procedure_use_step_arguments_invalid") from error
        decoded.append(dict(text=step["text"], tool=step["tool"], arguments=arguments))
    return decoded


def _terminal_span(registration, effect_id):
    envelope, receipt = registration.envelope, registration.admission_receipt
    return h.EvidenceSpanRef(span_id="procedure-step:" + effect_id,
        evidence_id=envelope.evidence_id, envelope_hash=envelope.envelope_hash,
        sanitized_hash=envelope.sanitized_hash, admission_receipt_id=receipt.receipt_id,
        admission_receipt_hash=receipt.receipt_hash, source_kind=envelope.source_kind,
        item_ordinal=1, item_id=envelope.evidence_id,
        item_json_pointer="/source/tool_terminal_attestation/payload/source/effect_id",
        start_byte=0, end_byte=len(effect_id.encode()), exact_quote=effect_id,
        quote_hash=hashlib.sha256(effect_id.encode()).hexdigest(), source_hash=envelope.source_hash,
        normalization_version=h.EVIDENCE_NORMALIZATION_IDENTITY_UTF8_V1,
        actor_role=h.EvidenceActorRole.TOOL, provenance=h.EvidenceProvenance.TRUSTED_TOOL,
        support_kind=h.EvidenceSupportKind.CONTEXT_ONLY, typed_observation=None)
