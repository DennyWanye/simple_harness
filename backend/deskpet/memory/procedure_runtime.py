"""Actual Procedure use -> registered Scope tool terminal -> public observation."""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping

import logging

import simple_harness as h
from simple_harness_memory import MemoryScope
from deskpet.memory.procedure_applicability import ProcedureUseRejected, current_snapshot
from deskpet.memory.procedure_use_store import ProcedureUseStore, _checked
from deskpet.task_scope.protocol import canonical_hash

log = logging.getLogger(__name__)


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

    async def discover(self, arguments, context):
        from deskpet.memory.trusted_disclosure import resolve_current_disclosure
        arguments = h.thaw_json(arguments)
        arguments.pop("deskpet_public_progress", None)
        # r10: the model omits the cursor on a first page; a required-but-empty
        # cursor only produced tool_arguments.missing and a failed attempt.
        arguments.setdefault("after", "")
        if set(arguments) != {"query", "after"}:
            raise ProcedureUseRejected("procedure_discovery_arguments_invalid")
        runtime = self.runtime_getter()
        disclosure = await resolve_current_disclosure(db_path=self.store.path, subject=runtime.principal().actor_id,
            run_id=context.run_id.value, request_id=context.request_id.value)
        page = await self.operation_audit.invoke(await runtime.manager(), "discover_procedure_drafts",
            principal=runtime.principal(), scope=runtime.scope(), disclosure_context=disclosure,
            query=arguments["query"], after=arguments["after"], limit=8, max_bytes=32768)
        current = await resolve_current_disclosure(db_path=self.store.path, subject=runtime.principal().actor_id,
            run_id=context.run_id.value, request_id=context.request_id.value)
        if current != disclosure:
            raise ProcedureUseRejected("procedure_discovery_disclosure_changed")
        return {"kind":"procedure_draft_preview", "execution_authorized":False,
            "candidates":[{"candidate":c.to_json(), "history_binding":{
                "memory_id":c.memory_id, "revision":c.revision, "candidate_hash":c.source_hash}}
                for c in page.candidates], "next_after":page.next_after, "omitted_oversize":page.omitted_oversize}

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
        # r14: the model bound two steps, then re-invented the arguments and
        # read ``execution_authorized: false`` as "not allowed". Echo the exact
        # frozen calls back with an explicit next action. The binding, the exact
        # match and the deny path are all unchanged; only the answer is legible.
        from deskpet.memory.procedure_guidance import bind_next_action, bound_step_calls
        calls = bound_step_calls(use["steps"])
        return {"procedure_use_id": use_id, "memory_id": target.memory_id,
                "revision": target.revision, "steps": len(use["steps"]),
                "execution_authorized": False, "binding_frozen": True,
                "bound_steps": calls, "next_action": bind_next_action(calls)}

    async def before_call(self, context, call):
        if call.name in {"procedure_use", "procedure_discover", "context_route", "task_scope_search", "task_scope_update", "prospective_ack"}:
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
                memory_id=use["memory_id"], revision=use["target_revision"], allow_observation_rebase=True)
        except (MemoryWriterConflict, SuppressionDenied) as error:
            # Expected current-visibility/revision changes are a public deny;
            # corruption and cancellation still propagate as actual failures.
            raise ProcedureUseRejected("procedure_use_target_unavailable") from error
        _same_use_definition(use, current)
        await self.store.reserve(authority=self.authorities.resolve(context.run_id),
            registry=self.registry, call=call, context=context)

    async def current_fingerprints(self, run_id):
        """Applicability proven by a Run that is executing *right now*.

        HM-TO-A6 事件 S: ``SdkRunToolAuthorityRegistry.resolve`` only knows live
        foreground Runs and drops each record at that Run's terminal, so it raises
        ``KeyError(<run id>)`` for every background lane and for every already
        finished turn.  Answering ``()`` fails closed (a Procedure disappears from
        recall; nothing is granted), but on the foreground path the Run is supposed
        to be live, so that case is logged rather than left anonymous — a
        registration/terminal ordering bug must not present as "Procedure memories
        quietly stopped being recalled".  For the off-Run question see
        ``applied_use_fingerprints``.
        """
        if self.authorities is None or self.registry is None:
            return ()
        try:
            authority = self.authorities.resolve(run_id)
        except KeyError:
            log.warning("memory.procedure_applicability_run_not_live run_id=%s", run_id)
            return ()
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

    async def applied_use_fingerprints(self):
        """Applicability of Procedures with a *consumed* observation, for lanes with no live Run.

        The post-turn analysis lane has no Tool authority of its own and runs after the
        foreground terminal has already dropped that Run's record (native run 8:
        COMPLETED at t, analysis reserved at t+0.5 s), so ``current_fingerprints`` can
        never answer for it.  These rows are the Host's honest off-Run answer, and the
        difference from the live path must be stated plainly:

        * kept — "a never-used Procedure stays invisible".  ``ProcedureUseStore.bind``
          is the only writer of ``procedure_uses``, and only a use whose observation
          reached the SDK (journal phase ``applied``) is counted here, so neither a
          merely bound nor a rejected use qualifies.
        * **dropped** — "and it is still applicable *now*".  ``current_fingerprints``
          re-derives ``current_snapshot`` against the live tool set and route and admits
          a fingerprint only if it still matches; off-Run there is no current tool set to
          re-derive against.  A Procedure whose tool was since renamed, re-signed or
          revoked therefore leaves foreground recall but stays an analysis-lane endpoint.
          It is only ever a *candidate*: the SDK re-resolves every relation endpoint at
          apply time, and ``SemanticCorrectionAuthority.check`` re-verifies disclosure.

        Ordering is not what makes a replayed batch stable — ``prepare`` returns the
        persisted candidate snapshot and ``snapshot_for_attempt`` re-checks its hash.
        A corrupt ``procedure_uses`` body propagates (``_checked``): the analysis lane
        records it as ``relation_procedure_applicability_unavailable`` rather than
        losing one row in silence.
        """
        async with self.store.connection() as db:
            await db.execute("BEGIN")
            async with db.execute(
                "SELECT * FROM procedure_uses WHERE subject=? ORDER BY created_at DESC LIMIT 128",
                (self.store.principal.actor_id,)) as cursor:
                uses = tuple(_checked(row) for row in await cursor.fetchall())
        values = set()
        for use in uses:
            if await self.store.journal(use["use_id"], "applied") is None:
                continue
            fingerprint = use.get("applicability_fingerprint")
            if type(fingerprint) is str and fingerprint:
                values.add(fingerprint)
        return tuple(sorted(values))

    async def observe_group(self, group, manager):
        """One bounded recovery per worker visit; original refs always get first replay."""
        runtime = self.runtime_getter()
        actual = await runtime.conversation_evidence_authority.registrations_for_run(group.host_run_id)
        if actual != group:
            raise ProcedureUseRejected("procedure_terminal_group_changed")
        use = await self.store.use_for_run(group.terminal_source[0].run_id)
        if use is None:
            return
        async with self._observation_lock:
            if (await self.store.journal(use["use_id"], "applied") is not None
                    or await self.store.journal(use["use_id"], "rejected") is not None):
                return
            prepared = await self.store.prepared(use["use_id"])
            verified = await self._step_sources(use, group)
            if not verified:
                return
            if prepared is None:
                from simple_harness_memory.core.errors import MemoryValidationError
                try:
                    prepared = await self._prepare(use, group, verified, manager)
                except MemoryValidationError as error:
                    if str(error) != "procedure_observation_source_already_counted":
                        raise
                    await self._reject_counted_source(use, group, verified, error)
                    return
            reference = h.ProcedureObservationAuthorityRef.from_authority(
                h.ProcedureObservationAuthority.from_json(prepared["authority"]))
            from simple_harness_memory.core.errors import MemoryValidationError, MemoryWriterConflict
            try:
                result = await self._consume(manager, reference)
            except (MemoryValidationError, MemoryWriterConflict) as error:
                if str(error) not in {"procedure_observation_authority_expired",
                    "procedure_observation_authority_rejected", "procedure_observation_revision_stale",
                    "procedure_observation_expected_transition_differs"}:
                    raise
                # The SDK resolves the exact old ref again and checks durable
                # non-consumption + genuine expiry/staleness in its transaction.
                # A generic authority rejection alone never authorizes renewal.
                try:
                    prepared = await self._prepare(use, group, verified, manager, previous=reference)
                except MemoryValidationError as recovery_error:
                    if str(recovery_error) != "procedure_observation_source_already_counted":
                        raise
                    # Another actual use consumed this Scope while our original
                    # prepared ref was pending. Keep both historical facts; no
                    # replacement authority or invented consumption is needed.
                    await self._reject_counted_source(use, group, verified, recovery_error)
                    return
                except MemoryWriterConflict as recovery_error:
                    if str(recovery_error) != "procedure_observation_previous_already_consumed":
                        raise
                    result = await self._consume(manager, reference)
                else:
                    reference = h.ProcedureObservationAuthorityRef.from_authority(
                        h.ProcedureObservationAuthority.from_json(prepared["authority"]))
                    result = await self._consume(manager, reference)
            await self.store.journal(use["use_id"], "applied", {"result": result.to_json(),
                "result_hash": result.result_hash, "reference": reference.to_json()})

    async def _reject_counted_source(self, use, group, verified, error):
        # Record only the exact public rejection, for initial and recovery
        # prepares alike. Never replace an existing prepared ref or SDK result.
        await self.store.journal(use["use_id"], "rejected", {
            "reason": str(error), "host_run_id": group.host_run_id,
            "source_registration_hashes": [item.registration_hash for item, _ in verified]})

    async def _consume(self, manager, reference):
        runtime = self.runtime_getter()
        return await self.operation_audit.invoke(manager, "record_procedure_observation",
            principal=runtime.principal(), scope=MemoryScope.personal(runtime.principal().actor_id), reference=reference)

    async def _step_sources(self, use, group):
        reservations = await self.store.reservations(use["use_id"])
        if not 1 <= len(reservations) <= len(use["steps"]):
            return ()
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
            if (source["tool_name"] != reservation["call"]["tool"] or source["sdk_run_id"] != use["sdk_run_id"]
                    or source["internal_call_id"] != reservation["call_id"]):
                raise ProcedureUseRejected("procedure_actual_terminal_identity_differs")
            verified.append((registration, source))
        if _observed_outcome(use, verified) is None:
            return ()
        return tuple(verified)

    async def _prepare(self, use, group, verified, manager, *, previous=None):
        runtime = self.runtime_getter()
        current = await self.operation_audit.invoke(manager, "read_procedure_use_target", principal=runtime.principal(),
            scope=MemoryScope.personal(runtime.principal().actor_id), memory_id=use["memory_id"],
            revision=use["target_revision"], allow_observation_rebase=True)
        _same_use_definition(use, current)
        registration, source = verified[-1]
        outcome = _observed_outcome(use, verified)
        if outcome is None:
            raise ProcedureUseRejected("procedure_observation_outcome_unproved")
        preparation = await self.operation_audit.invoke(manager, "prepare_procedure_observation",
            principal=runtime.principal(), scope=MemoryScope.personal(runtime.principal().actor_id),
            observation_id=use["use_id"], target_memory_id=use["memory_id"], target_revision=use["target_revision"],
            kind=h.ProcedureObservationKind.TERMINAL_OUTCOME,
            applicability=h.ProcedureApplicabilityContext.from_json(use["applicability"]),
            hazard=h.ProcedureHazard(use["hazard"]), task_scope_id=use["task_scope_id"],
            evidence_span=_terminal_span(registration, source["effect_id"]),
            terminal_receipt_id=registration.metadata.tool_causal_link.terminal_receipt_id,
            terminal_receipt_hash=registration.metadata.tool_causal_link.terminal_receipt_hash,
            outcome=outcome,
            attributable=outcome is h.ProcedureObservationOutcome.SUCCESS,
            observed_at=registration.metadata.occurred_at, run_id=use["sdk_run_id"],
            operation_id="procedure-observe:" + canonical_hash(use["use_id"]),
            allow_observation_rebase=True, previous_reference=previous)
        now = self.store.clock()
        identity = use["use_id"] if previous is None else "procedure-recovery:" + canonical_hash(previous.to_json())
        authority = h.issue_procedure_observation_authority(preparation.intent,
            authority_id=identity, issued_at=now, expires_at=now + 300,
            nonce=canonical_hash([identity, preparation.intent.intent_hash]), issuer_ref="host-procedure-use/v1")
        body = {"authority": authority.to_json(), "host_run_id": group.host_run_id,
            "source_registration_hashes": [item.registration_hash for item, _ in verified],
            "source_revision": use["target_revision"], "preparation": preparation.to_json()}
        if previous is None:
            return await self.store.journal(use["use_id"], "prepared", body)
        body["previous_reference"] = previous.to_json()
        return await self.store.append_attempt(use["use_id"], previous, body)

    async def verify_observation_source(self, use, prepared, authority):
        """Resolver rechecks real persistent sources independently of the grant."""
        intent = authority.intent
        if (intent.run_id != use["sdk_run_id"] or intent.task_scope_id != use["task_scope_id"]
                or intent.target_memory_id != use["memory_id"] or intent.target_revision < use["target_revision"]
                or intent.applicability.to_json() != use["applicability"]
                or intent.hazard.value != use["hazard"]
                or intent.kind is not h.ProcedureObservationKind.TERMINAL_OUTCOME):
            raise ProcedureUseRejected("procedure_observation_use_binding_differs")
        if "preparation" in prepared:
            from simple_harness_memory.core.procedure_operation_observation import ProcedureOperationObservationV1
            value = prepared["preparation"]
            observation = ProcedureOperationObservationV1(**value["operation_observation"])
            if (prepared.get("source_revision") != use["target_revision"]
                    or value["intent"] != intent.to_json() or observation.source_hash != intent.intent_hash
                    or observation.operation != "prepare_procedure_observation"):
                raise ProcedureUseRejected("procedure_actual_preparation_differs")
        elif intent.target_revision != use["target_revision"]:
            raise ProcedureUseRejected("procedure_legacy_source_revision_differs")
        group = await self.runtime_getter().conversation_evidence_authority.registrations_for_run(prepared["host_run_id"])
        reservations = await self.store.reservations(use["use_id"])
        if not 1 <= len(reservations) <= len(use["steps"]):
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
                    or source["internal_call_id"] != reservation["call_id"]):
                raise ProcedureUseRejected("procedure_observation_terminal_source_differs")
            verified.append(registration)
        actual_outcome = _observed_outcome(use, [(r, r.envelope.sanitized_payload["source"]["tool_terminal_attestation"]["payload"]["source"]) for r in verified])
        if (actual_outcome is None or intent.outcome is not actual_outcome
                or intent.attributable is not (actual_outcome is h.ProcedureObservationOutcome.SUCCESS)):
            raise ProcedureUseRejected("procedure_observation_attribution_unproved")
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


def _same_use_definition(use, current):
    from simple_harness_memory.core.lifecycle_results import UNBOUND_PROCEDURE_APPLICABILITY
    old = use["target"]
    if (current.revision < use["target_revision"]
            or any(getattr(current, key) != old[key] for key in ("memory_id", "risk_level", "qualification_epoch"))
            or list(current.step_hashes) != old["step_hashes"]
            or current.applicability_fingerprint not in (UNBOUND_PROCEDURE_APPLICABILITY, use["applicability_fingerprint"])
            or current.bound_hazard not in (None, use["hazard"])):
        raise ProcedureUseRejected("procedure_use_target_changed")


def _observed_outcome(use, verified):
    """An exact failed executed prefix proves failure, never its semantic cause.

    UNKNOWN/cancelled/rejected or missing tool terminals are not failure evidence.
    All successful steps are attributable to the explicitly bound physical use;
    an error may instead be infrastructure, arguments, permissions or environment.
    """
    if not verified or any(source["state"] != "succeeded" for _, source in verified[:-1]):
        return None
    state = verified[-1][1]["state"]
    if state == "failed":
        return h.ProcedureObservationOutcome.FAILURE
    if state == "succeeded" and len(verified) == len(use["steps"]):
        return h.ProcedureObservationOutcome.SUCCESS
    return None
