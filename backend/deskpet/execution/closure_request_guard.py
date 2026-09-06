"""Source-bound Host closure input and physical preflight; no new SDK authority."""
from __future__ import annotations

from contextlib import asynccontextmanager
import json
from pathlib import Path
import time
from types import SimpleNamespace

import aiosqlite
from simple_harness import (DisclosureContext, EvidenceRef, EvidenceReasonCode,
    EvidenceSourceKind, SanitizedEvidenceEnvelope, SanitizedEvidenceReceipt, thaw_json)
from simple_harness.execution.provider_invocations import provider_request_json
from simple_harness.providers import ProviderRequestRejectedError

from deskpet.execution.primary_dependencies import dependencies, read_run_dependencies
from deskpet.memory.primary_visibility import read_evidence_pair
from deskpet.memory.trusted_disclosure import current_record_tx, binding_token, resolve_current_disclosure
from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
from deskpet.task_scope.protocol import canonical_hash

POLICY = "host-closure-attempt-input/v1"
SAFE_PENDING_REASONS = frozenset({"closure_source_incomplete", "closure_request_disclosure_rejected",
    "closure_attempt_unknown", "closure_attempt_failed", "closure_attempt_confirmed", "closure_timeout",
    "closure_model_declined", "closure_run_not_completed", "closure_no_final_answer", "closure_binding_unavailable"})


class ClosureSourceIncomplete(ValueError):
    """Fixed safe pending reason; no source text is copied to the receipt."""
    code = "closure_source_incomplete"


class ClosurePhysicalRequestRejected(ProviderRequestRejectedError):
    error_code = "closure_request_disclosure_rejected"
    default_message = "Current closure input cannot be verified."


async def rows(db, sql, args=()):
    cursor = await db.execute(sql, args)
    try:
        return await cursor.fetchall()
    finally:
        await cursor.close()


def input_id(attempt_id):
    return "closure-attempt-input:" + attempt_id


class ClosureRequestAuthority:
    def __init__(self, db_path, *, stack_getter, policy_factory, clock=time.time):
        self.path = Path(db_path)
        self.stack_getter, self.policy_factory, self.clock = stack_getter, policy_factory, clock

    @asynccontextmanager
    async def reader(self, *, snapshot=True):
        async with aiosqlite.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            if snapshot:
                await db.execute("BEGIN")
            yield db

    async def host_snapshot_tx(self, db, identity):
        """Only Host reads; usable inside the existing reservation write TX."""
        from deskpet.execution.foreground_queue import EffectBoundary, _EFFECT_BOUNDARY_ALLOWED_STATES
        from deskpet.execution.semantic_closure import dirty_state_tx, _scope_observation_tx
        found = await rows(db, "SELECT r.subject,r.primary_conversation_id,t.turn_json,t.turn_hash,"
            "h.owner_id,h.generation,h.current_state,h.lease_expires_at,b.binding_json,b.binding_hash "
            "FROM foreground_runs r JOIN foreground_run_heads h ON h.host_run_id=r.host_run_id "
            "JOIN foreground_turns t ON t.turn_id=r.turn_id AND t.subject=r.subject "
            "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id "
            "WHERE r.host_run_id=? AND b.sdk_run_id=?", (identity["host_run_id"], identity["sdk_run_id"]))
        if len(found) != 1:
            raise ClosureSourceIncomplete("closure_run_missing")
        row = dict(found[0])
        if (row["subject"] != identity["subject"] or row["generation"] != identity["generation"]
                or row["owner_id"] != identity["owner_id"]
                or row["current_state"] not in _EFFECT_BOUNDARY_ALLOWED_STATES[EffectBoundary.CLOSURE]
                or row["lease_expires_at"] <= self.clock()
                or canonical_hash(json.loads(row["turn_json"])) != row["turn_hash"]
                or canonical_hash(json.loads(row["binding_json"])) != row["binding_hash"]):
            raise ClosureSourceIncomplete("closure_run_not_current")
        # Heartbeat renewal is allowed; owner/generation/state remain frozen.
        row.pop("lease_expires_at")
        from deskpet.execution.evidence_ingress import ExecutionEvidenceIngress
        scope = await ExecutionEvidenceIngress(self.path).resolve_run_scope_tx(db, identity["sdk_run_id"])
        if scope is None or scope.task_scope_id != identity["scope"] or scope.subject != identity["subject"]:
            raise ClosureSourceIncomplete("closure_scope_binding_differs")
        dirty = await dirty_state_tx(db, identity["scope"])
        observation = await _scope_observation_tx(db, identity["scope"], dirty)
        # Existing observation query work is not globally bounded by these caps.
        if not dirty.is_dirty or len(dirty.material_events) > 32:
            raise ClosureSourceIncomplete("closure_event_coverage_incomplete")
        source_rows = []
        for evidence_id in observation["allowed_evidence_refs"]:
            envelope, receipt = await read_evidence_pair(db=db, subject=identity["subject"],
                primary_ref=row["primary_conversation_id"], evidence_id=evidence_id)
            origins = await rows(db, "SELECT DISTINCT b.sdk_run_id FROM foreground_runs r "
                "JOIN foreground_turns t ON t.turn_id=r.turn_id AND t.subject=r.subject "
                "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id "
                "WHERE r.subject=? AND r.primary_conversation_id=? "
                "AND (t.evidence_id=? OR b.sdk_run_id=?) LIMIT 2",
                (identity["subject"], row["primary_conversation_id"], evidence_id, envelope.run_id))
            if len(origins) != 1:
                raise ClosureSourceIncomplete("closure_source_origin_ambiguous")
            source_rows.append(dict(evidence_id=evidence_id, envelope_hash=envelope.envelope_hash,
                receipt_hash=receipt.receipt_hash, run_id=envelope.run_id, sdk_run_id=origins[0][0]))
        current = await current_record_tx(db, identity["subject"])
        scope_heads = await rows(db, "SELECT current_revision,event_watermark FROM task_scope_heads WHERE task_scope_id=?", (identity["scope"],))
        if len(scope_heads) != 1:
            raise ClosureSourceIncomplete("closure_scope_head_missing")
        return dict(run=row, observation=observation, sources=source_rows,
            policy_token=None if current is None else binding_token(current),
            scope_head=dict(scope_heads[0]),
            event_ids=[e.event_id for e in dirty.material_events])

    async def prepare(self, *, host_run_id, sdk_run_id, subject, owner_id, generation, scope,
                      observation, answer, binding_record, materialize=True):
        """Validate all transmitted fields, retaining the original observation."""
        try:
            return await self._prepare(host_run_id=host_run_id, sdk_run_id=sdk_run_id,
                subject=subject, owner_id=owner_id, generation=generation, scope=scope,
                observation=observation, answer=answer, binding_record=binding_record, materialize=materialize)
        except (ValueError, TypeError, KeyError) as exc:
            raise ClosureSourceIncomplete("closure_source_incomplete") from exc

    async def _prepare(self, *, host_run_id, sdk_run_id, subject, owner_id, generation, scope,
                       observation, answer, binding_record, materialize):
        from deskpet.execution.semantic_closure import _MAX_ANSWER_BYTES
        from deskpet.task_scope.search import TaskScopeSearchStore
        from deskpet.task_scope.disclosure import render_scope_disclosure
        binding = SdkRunBindingV1.from_record(binding_record)
        if binding.run_id != sdk_run_id:
            raise ClosureSourceIncomplete("closure_binding_differs")
        identity = dict(host_run_id=host_run_id, sdk_run_id=sdk_run_id, subject=subject,
                        owner_id=owner_id, generation=generation, scope=scope)
        stack = self.stack_getter()
        terminal = stack.read_run_terminal_evidence(sdk_run_id)
        facts = stack.read_closure_run_facts(sdk_run_id)
        actual_answer = (facts.last_assistant_message or "").strip().encode("utf-8")[:_MAX_ANSWER_BYTES].decode("utf-8", "ignore")
        if (terminal is None or terminal.state != "completed" or terminal.run_id != sdk_run_id
                or not actual_answer or actual_answer != answer
                or SdkRunBindingV1.from_record(facts.binding_record) != binding):
            raise ClosureSourceIncomplete("closure_terminal_differs")
        disclosure = await resolve_current_disclosure(db_path=self.path, subject=subject,
            run_id=sdk_run_id, request_id=binding.request_id)
        policy = self.policy_factory(subject)
        async with self.reader() as db:
            host = await self.host_snapshot_tx(db, identity)
            if host["observation"] != observation:
                raise ClosureSourceIncomplete("closure_observation_changed")
        # A public dependency read may recursively verify a retained scope via
        # open_exact, which records an access receipt on another connection.
        # Do not hold a Host read transaction over that writer (DELETE journal
        # otherwise self-deadlocks). Exact Host facts are fenced again below.
        async with self.reader(snapshot=False) as db:
            own = await read_run_dependencies(db=db, stack=stack, sdk_run_id=sdk_run_id)
            if own is None:
                raise ClosureSourceIncomplete("closure_final_answer_sources_missing")
            proofs = [own[1]]
            source_runs = {s["sdk_run_id"] for s in host["sources"]}
            # Pending identifiers/watermarks are structural; only known fixed
            # reason codes may travel without an arbitrary-text source proof.
            for pending in observation["pending_receipts"]:
                if (pending["reason_code"] not in SAFE_PENDING_REASONS
                        or type(pending["closure_watermark"]) is not int or pending["closure_watermark"] < 0):
                    raise ClosureSourceIncomplete("closure_pending_prose_unverified")
                source_runs.add(pending["sdk_run_id"])
            # Preserve actual source Run identity, including older Runs in this scope.
            for source_run in sorted(source_runs):
                source = await read_run_dependencies(db=db, stack=stack, sdk_run_id=source_run)
                if (source is None or source[0]["subject"] != subject
                        or source[0]["primary_conversation_id"] != host["run"]["primary_conversation_id"]):
                    raise ClosureSourceIncomplete("closure_source_origin_missing")
                proofs.append(source[1])
                source_binding = SdkRunBindingV1.from_record(stack.read_closure_run_facts(source_run).binding_record)
                await resolve_current_disclosure(db_path=self.path, subject=subject,
                    run_id=source_run, request_id=source_binding.request_id)
        async with self.reader() as db:
            events = []
            for event_id in host["event_ids"]:
                event_rows = await rows(db, "SELECT * FROM task_scope_events WHERE event_id=?", (event_id,))
                if len(event_rows) != 1:
                    raise ClosureSourceIncomplete("closure_event_missing")
                event = dict(event_rows[0]); payload = json.loads(event["payload_json"])
                if canonical_hash(payload) != event["payload_hash"]:
                    raise ClosureSourceIncomplete("closure_event_hash_differs")
                source_id = event["source_event_id"]
                if source_id.startswith("execution:"):
                    source_id = source_id.removeprefix("execution:")
                reservations = await rows(db, "SELECT * FROM harness_evidence_reservations "
                    "WHERE source_event_id=? AND task_scope_id=?", (source_id, scope))
                if len(reservations) != 1 or reservations[0]["run_id"] not in source_runs:
                    raise ClosureSourceIncomplete("closure_event_origin_missing")
                reservation = dict(reservations[0])
                fact = await stack.read_reserved_fact(SimpleNamespace(**reservation))
                if fact is None or fact.run_id != reservation["run_id"]:
                    raise ClosureSourceIncomplete("closure_event_public_fact_missing")
                if event["event_kind"] in {"host.file", "host.test"}:
                    if fact.objective is None or fact.objective.event_kind != event["event_kind"] or dict(fact.objective.payload) != payload:
                        raise ClosureSourceIncomplete("closure_objective_differs")
                elif event["event_kind"] == "harness.tool_invocation":
                    if payload.get("public_payload") != fact.public_payload() or payload.get("run_id") != fact.run_id:
                        raise ClosureSourceIncomplete("closure_tool_fact_differs")
                else:
                    raise ClosureSourceIncomplete("closure_event_source_unsupported")
                links = await rows(db, "SELECT evidence_id FROM task_scope_evidence_links WHERE event_id=? ORDER BY ordinal", (event_id,))
                if not links:
                    raise ClosureSourceIncomplete("closure_event_sources_missing")
                for link in links:
                    matching = [s for s in host["sources"] if s["evidence_id"] == link[0] and s["sdk_run_id"] == fact.run_id]
                    if len(matching) != 1:
                        raise ClosureSourceIncomplete("closure_event_source_binding_differs")
                    envelope, _ = await read_evidence_pair(db=db, subject=subject,
                        primary_ref=host["run"]["primary_conversation_id"], evidence_id=link[0])
                    if fact.objective is None or thaw_json(envelope.sanitized_payload) != dict(fact.objective.payload):
                        raise ClosureSourceIncomplete("closure_event_s1_differs")
                events.append(dict(event_id=event_id, payload_hash=event["payload_hash"],
                                   source_run_id=fact.run_id, source_event_id=fact.source_event_id))
        # Only existing authoritative field projection can explain scope prose.
        from deskpet.task_scope.projections import ProjectionIntegrityError
        from deskpet.task_scope.search import TaskScopeSearchError
        try:
            # Initial preparation may materialize the existing deterministic
            # projection. Physical revalidation reuses that exact source, but
            # the existing open API still records access receipts.
            opened = await TaskScopeSearchStore(self.path).open_exact(subject=subject,
                allowed_scope_ids=(scope,), task_scope_id=scope, materialized_only=not materialize)
        except (ProjectionIntegrityError, TaskScopeSearchError) as exc:
            raise ClosureSourceIncomplete("closure_scope_projection_unavailable") from exc
        projected = await render_scope_disclosure(db_path=self.path, package=opened.resume_package,
            subject=subject, stack=stack, policy=policy, disclosure_context=disclosure)
        fields = projected["disclosure"]["fields"]
        expected_scope = observation["task_scope"]
        if (projected["canonical_revision"] != expected_scope["current_revision"]
                or projected.get("status") != expected_scope["status"]
                or any(fields.get(k) != expected_scope[k] for k in ("title", "goal"))
                or (expected_scope.get("resume") not in (None, {}, "")
                    and fields.get("resume") != expected_scope["resume"])):
            # All transmitted prose must match its current producer projection
            # in full, including nonempty resume; truncated/legacy stays pending.
            raise ClosureSourceIncomplete("closure_scope_fields_incomplete")
        proofs.append(projected["disclosure_manifest"]["dependencies"])
        proof = dependencies(
            [v for p in proofs for v in p["evidence"]] +
                [dict(evidence_id=s["evidence_id"], envelope_hash=s["envelope_hash"]) for s in host["sources"]],
            [v for p in proofs for v in p["recall"]],
            [v for p in proofs for v in p.get("short_horizon", ())])
        async with self.reader(snapshot=False) as db:
            if not await policy.check_dependencies(db=db, primary_ref=host["run"]["primary_conversation_id"],
                    dependencies=proof, disclosure_context=disclosure):
                raise ClosureSourceIncomplete("closure_sources_not_visible")
        async with self.reader() as db:
            if await self.host_snapshot_tx(db, identity) != host:
                raise ClosureSourceIncomplete("closure_authority_changed")
        current = await resolve_current_disclosure(db_path=self.path, subject=subject,
            run_id=sdk_run_id, request_id=binding.request_id)
        if current != disclosure:
            raise ClosureSourceIncomplete("closure_disclosure_changed")
        return dict(identity=identity, host=host, answer=answer, binding=binding.to_record(),
            disclosure=disclosure.to_json(), dependencies=proof, events=events,
            scope_manifest=projected["disclosure_manifest"],
            terminal=dict(event_id=terminal.event_id, event_hash=terminal.event_hash, state=terminal.state))

    async def bind_attempt(self, prepared, row, request, *, db):
        """Same reservation TX; no public SDK/Memory awaits or own commit."""
        from deskpet.memory.human_memory_program import HumanMemoryProgramStore
        if await self.host_snapshot_tx(db, prepared["identity"]) != prepared["host"]:
            raise ClosureSourceIncomplete("closure_reservation_changed")
        # Match by immutable evidence identity as well as new exact source Run.
        # Legacy attempts mislabeled members with their consumer Run; they must
        # still block a second send. Do this again inside the reservation TX.
        for source in prepared["host"]["sources"]:
            conflicts = await rows(db, "SELECT 1 FROM post_turn_invocation_members m "
                "JOIN post_turn_invocation_attempts a ON a.attempt_id=m.attempt_id "
                "WHERE m.subject=? AND m.evidence_id=? AND a.attempt_id<>? "
                "AND (a.status IN ('reserved','handed_off') OR (a.status='unknown' AND a.unknown_class='sent_unknown')) LIMIT 1",
                (prepared["identity"]["subject"], source["evidence_id"], row["attempt_id"]))
            if conflicts:
                raise ClosureSourceIncomplete("closure_source_attempt_open")
        full = provider_request_json(request)
        body = dict(schema_version=1, attempt_id=row["attempt_id"], request_hash=row["request_hash"],
            attempt_ordinal=row["attempt_ordinal"], evidence_set_key=row["evidence_set_key"],
            model_config_hash=row["model_config_hash"],
            provider_request=full, provider_request_hash=canonical_hash(full), prepared=prepared)
        identity = input_id(row["attempt_id"]); digest = canonical_hash(body)
        refs = tuple(EvidenceRef(s["evidence_id"], s["envelope_hash"], i + 1)
                     for i, s in enumerate(prepared["host"]["sources"]))
        disclosure = DisclosureContext.from_json(prepared["disclosure"])
        envelope = SanitizedEvidenceEnvelope(evidence_id=identity, run_id=row["sdk_run_id"],
            subject=prepared["identity"]["subject"], source_kind=EvidenceSourceKind.RUNTIME_EVENT,
            source_ref=identity, source_hash=digest, sanitized_payload=body, sanitized_hash=digest,
            filter_policy_version=POLICY, removed_spans=(), disclosure_context=disclosure, evidence_refs=refs)
        receipt = SanitizedEvidenceReceipt(receipt_id="receipt:"+identity, run_id=envelope.run_id,
            subject=envelope.subject, evidence_id=identity, envelope_hash=envelope.envelope_hash,
            source_hash=digest, sanitized_hash=digest, filter_policy_version=POLICY, accepted=True,
            reason_codes=(EvidenceReasonCode.SANITIZED_AND_ACCEPTED,), disclosure_context=disclosure,
            evidence_refs=refs, admitted_at=float(row["reserved_at"]))
        await HumanMemoryProgramStore(self.path).append_evidence_tx(db, envelope, receipt,
            primary_conversation_id=prepared["host"]["run"]["primary_conversation_id"], committed_at=float(row["reserved_at"]))


class ClosurePhysicalRequestGuard:
    def __init__(self, authority, *, binding):
        self.authority, self.binding = authority, binding

    async def _carrier(self, actual):
        from deskpet.execution.semantic_closure import closure_request_hash, closure_plan_id, evidence_set_key, message_hash
        a = self.authority
        async with a.reader() as db:
            found = await rows(db, "SELECT a.*,r.subject,r.primary_conversation_id FROM post_turn_invocation_attempts a "
                "JOIN foreground_runs r ON r.host_run_id=a.host_run_id WHERE a.sdk_run_id=? AND a.purpose='closure' "
                "AND ('post-turn-closure-' || substr(a.request_hash,1,24) || '-' || a.attempt_ordinal)=? LIMIT 2",
                (self.binding.run_id, actual.request_id.value))
            if len(found) != 1:
                raise ClosureSourceIncomplete("closure_attempt_missing")
            row = dict(found[0])
            if row["status"] != "handed_off" or row["settled_at"] is not None or row["unknown_class"] is not None:
                raise ClosureSourceIncomplete("closure_attempt_not_handed_off")
            envelope, _ = await read_evidence_pair(db=db, subject=row["subject"],
                primary_ref=row["primary_conversation_id"], evidence_id=input_id(row["attempt_id"]))
            body = thaw_json(envelope.sanitized_payload); prepared = body["prepared"]
            expected_hash = closure_request_hash(self.binding.run_id, prepared["identity"]["scope"],
                row["closure_watermark"], message_hash(prepared["answer"]))
            if (envelope.filter_policy_version != POLICY or envelope.run_id != self.binding.run_id
                    or type(body["schema_version"]) is not int or body["schema_version"] != 1
                    or body["attempt_id"] != row["attempt_id"] or body["request_hash"] != row["request_hash"]
                    or body["attempt_ordinal"] != row["attempt_ordinal"]
                    or body["evidence_set_key"] != row["evidence_set_key"]
                    or row["evidence_set_key"] != evidence_set_key(prepared["host"]["event_ids"])
                    or row["request_hash"] != expected_hash or row["plan_id"] != closure_plan_id(expected_hash)
                    or body["model_config_hash"] != row["model_config_hash"]
                    or (row["provider_id"], row["model_id"]) != (self.binding.provider_id, self.binding.model_id)
                    or canonical_hash(body["provider_request"]) != body["provider_request_hash"]
                    or provider_request_json(actual) != body["provider_request"]
                    or SdkRunBindingV1.from_record(prepared["binding"]) != self.binding
                    or prepared["identity"]["host_run_id"] != row["host_run_id"]
                    or prepared["identity"]["generation"] != row["generation"]
                    or prepared["identity"]["scope"] != row["task_scope_id"]
                    or prepared["identity"]["subject"] != row["subject"]
                    or envelope.disclosure_context.to_json() != prepared["disclosure"]):
                raise ClosureSourceIncomplete("closure_carrier_differs")
            expected_refs = tuple(EvidenceRef(s["evidence_id"], s["envelope_hash"], i + 1)
                                  for i, s in enumerate(prepared["host"]["sources"]))
            if envelope.evidence_refs != expected_refs:
                raise ClosureSourceIncomplete("closure_carrier_refs_differ")
            members = await rows(db, "SELECT subject,run_id,evidence_id FROM post_turn_invocation_members WHERE attempt_id=? ORDER BY subject,run_id,evidence_id", (row["attempt_id"],))
            expected = sorted((row["subject"], s["run_id"], s["evidence_id"]) for s in prepared["host"]["sources"])
            if [tuple(m) for m in members] != expected:
                raise ClosureSourceIncomplete("closure_members_differ")
            if await a.host_snapshot_tx(db, prepared["identity"]) != prepared["host"]:
                raise ClosureSourceIncomplete("closure_carrier_stale")
            return body

    async def __call__(self, actual):
        try:
            body = await self._carrier(actual); p = body["prepared"]
            fresh = await self.authority.prepare(**p["identity"], observation=p["host"]["observation"],
                answer=p["answer"], binding_record=p["binding"], materialize=False)
            if fresh != p or await self._carrier(actual) != body:
                raise ClosureSourceIncomplete("closure_physical_sources_changed")
        except Exception as exc:
            raise ClosurePhysicalRequestRejected(private_cause=exc) from None
