"""Immutable actual-use commitments and exact SDK effect facts for Procedure."""
from __future__ import annotations

import hashlib
import json
import math
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from simple_harness.contracts import canonical_json, thaw_json
from simple_harness.runtime import ProcedureObservationAuthority, ProcedureObservationAuthorityRef
from deskpet.memory import schema_chain
from deskpet.memory.procedure_recovery_schema import SCHEMA_VERSION as RECOVERY_SCHEMA_VERSION
from deskpet.memory.procedure_schema import SCHEMA_VERSION as PROCEDURE_SCHEMA_VERSION
from deskpet.memory.writer_fence import human_memory_connection
from deskpet.memory.procedure_applicability import ProcedureUseRejected, current_snapshot, inferred_hazard
from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx
from deskpet.task_scope.protocol import canonical_hash

# The attempt lineage arrives with v54 and every later chain version keeps it.
# Derived from the chain: an `== 54` literal here would reject the whole retry
# lineage the moment a later migration bumps user_version (as v55 did).
_ATTEMPT_LINEAGE_VERSIONS = schema_chain.accepted_versions(RECOVERY_SCHEMA_VERSION)


def _checked(row):
    value = json.loads(row["body_json"])
    if canonical_json(value) != row["body_json"] or canonical_hash(value) != row["body_hash"]:
        raise ProcedureUseRejected("procedure_persisted_body_corrupt")
    return value


class ProcedureUseStore:
    def __init__(self, path, *, principal, clock):
        self.path, self.principal, self.clock = Path(path), principal, clock
        self.source_verifier = None

    @asynccontextmanager
    async def connection(self):
        async with human_memory_connection(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys=ON")
            yield db

    async def _use(self, db, sdk_run_id):
        async with db.execute("SELECT * FROM procedure_uses WHERE sdk_run_id=?", (sdk_run_id,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        value = _checked(row)
        if (value["subject"] != self.principal.actor_id
                or any(value[key] != row[key] for key in (
                    "use_id", "subject", "task_scope_id", "sdk_run_id", "memory_id", "target_revision"))):
            raise ProcedureUseRejected("procedure_use_owner_or_binding_differs")
        return value

    async def bind(self, *, authority, registry, target, steps, use_id, context):
        from simple_harness_memory.core.procedure_use import ProcedureUseTarget
        from simple_harness_memory.core.lifecycle_results import UNBOUND_PROCEDURE_APPLICABILITY

        if type(target) is not ProcedureUseTarget or type(steps) is not list or not 1 <= len(steps) <= 16:
            raise ProcedureUseRejected("procedure_use_input_invalid")
        if any(type(step) is not dict or set(step) != {"text", "tool", "arguments"}
               or type(step["text"]) is not str or type(step["arguments"]) is not dict for step in steps):
            raise ProcedureUseRejected("procedure_use_steps_invalid")
        if tuple(hashlib.sha256(step["text"].encode()).hexdigest() for step in steps) != target.step_hashes:
            raise ProcedureUseRejected("procedure_use_steps_do_not_match_revision")
        from deskpet.memory.procedure_route import resolve_procedure_route
        async with self.connection() as db:
            await db.execute("BEGIN")
            route = await resolve_procedure_route(self.path, db, run_id=authority.run_id,
                subject=self.principal.actor_id, envelope=context.task_execution_envelope)
        applicability, snapshots = current_snapshot(authority, registry,
            tool_names=[step["tool"] for step in steps], route=route)
        hazard = inferred_hazard(snapshots)
        if target.applicability_fingerprint not in (UNBOUND_PROCEDURE_APPLICABILITY, applicability.fingerprint):
            raise ProcedureUseRejected("procedure_current_applicability_drift")
        if target.bound_hazard not in (None, hazard.value):
            raise ProcedureUseRejected("procedure_current_hazard_drift")
        now = float(self.clock())
        if not math.isfinite(now) or now < 0:
            raise ProcedureUseRejected("procedure_clock_invalid")
        scope_id = route.task_scope_id
        body = dict(use_id=use_id, subject=self.principal.actor_id, task_scope_id=scope_id,
            sdk_run_id=authority.run_id, memory_id=target.memory_id, target_revision=target.revision,
            target=target.to_json(), target_source_hash=target.source_hash,
            applicability=applicability.to_json(), applicability_fingerprint=applicability.fingerprint,
            hazard=hazard.value, tools=[snapshot.to_json() for snapshot in snapshots],
            # r14: ``arguments`` is persisted only so a deny can echo the exact
            # call the model itself bound. Matching still runs off
            # ``arguments_hash`` alone; nothing reads ``arguments`` to decide.
            steps=[dict(text_hash=digest, tool=step["tool"], arguments=step["arguments"],
                        arguments_hash=canonical_hash(step["arguments"]))
                   for step, digest in zip(steps, target.step_hashes, strict=True)])
        async with self.connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await assert_human_memory_ingress_open_tx(db)
                final_route = await resolve_procedure_route(self.path, db, run_id=authority.run_id,
                    subject=self.principal.actor_id, envelope=context.task_execution_envelope)
                if final_route != route:
                    raise ProcedureUseRejected("procedure_use_route_changed")
                existing = await self._use(db, authority.run_id)
                if existing is not None:
                    if existing != body:
                        from deskpet.memory.procedure_guidance import bound_step_calls
                        raise ProcedureUseRejected("procedure_same_run_changed_use",
                            detail={"bound_steps": bound_step_calls(existing["steps"])})
                    await db.commit()
                    return existing
                async with db.execute("SELECT subject FROM task_scopes WHERE task_scope_id=?", (scope_id,)) as cursor:
                    row = await cursor.fetchone()
                if row is None or row[0] != self.principal.actor_id:
                    raise ProcedureUseRejected("procedure_foreign_task_scope")
                await db.execute("INSERT INTO procedure_uses VALUES (?,?,?,?,?,?,?,?,?)",
                    (use_id, self.principal.actor_id, scope_id, authority.run_id, target.memory_id,
                     target.revision, canonical_json(body), canonical_hash(body), now))
                await db.commit()
                return body
            except BaseException:
                await db.rollback()
                raise

    async def use_for_run(self, run_id):
        async with self.connection() as db:
            return await self._use(db, run_id)

    async def reserve(self, *, authority, registry, call, context):
        """Before physical SDK dispatch; two concurrent calls cannot own a step."""
        async with self.connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await assert_human_memory_ingress_open_tx(db)
                use = await self._use(db, authority.run_id)
                if use is None:
                    await db.commit()
                    return None
                from deskpet.memory.procedure_route import resolve_procedure_route
                route = await resolve_procedure_route(self.path, db, run_id=authority.run_id,
                    subject=self.principal.actor_id, envelope=context.task_execution_envelope)
                if route.task_scope_id != use["task_scope_id"]:
                    raise ProcedureUseRejected("procedure_use_scope_changed")
                current, _ = current_snapshot(authority, registry,
                    tool_names=[step["tool"] for step in use["steps"]], route=route)
                if current.fingerprint != use["applicability_fingerprint"]:
                    raise ProcedureUseRejected("procedure_current_applicability_drift")
                call_id = call.call_id.value
                async with db.execute("SELECT * FROM procedure_use_reservations WHERE call_id=?", (call_id,)) as cursor:
                    prior = await cursor.fetchone()
                signature = dict(tool=call.name, arguments_hash=canonical_hash(thaw_json(call.arguments)))
                if prior is not None:
                    record = _checked(prior)
                    if prior["use_id"] != use["use_id"] or record["call"] != signature:
                        raise ProcedureUseRejected("procedure_call_replay_changed")
                    await db.commit()
                    return record
                async with db.execute("SELECT * FROM procedure_use_reservations WHERE use_id=? ORDER BY step_ordinal",
                                      (use["use_id"],)) as cursor:
                    reservations = await cursor.fetchall()
                if reservations:
                    async with db.execute("SELECT * FROM procedure_use_effects WHERE use_id=? AND step_ordinal=?",
                                          (use["use_id"], len(reservations))) as cursor:
                        previous = await cursor.fetchone()
                    if previous is None or _checked(previous)["state"] != "succeeded":
                        raise ProcedureUseRejected("procedure_previous_step_not_successful",
                            detail={"failed_ordinal": len(reservations), "total": len(use["steps"])})
                ordinal = len(reservations) + 1
                if ordinal > len(use["steps"]):
                    # r15: every bound step is reserved and the last one settled
                    # ``succeeded`` (the check above), so the binding is complete
                    # and its observation is already determined -
                    # ``_observed_outcome`` reads reservations only. The Run may
                    # make ordinary calls again (the user asked to read both
                    # files back and compare in the same turn). Nothing relaxes:
                    # the binding stays immutable, this call reserves no step and
                    # is attributed to no Procedure step, and a step that failed
                    # still stops here with ``procedure_previous_step_not_successful``.
                    await db.commit()
                    return None
                step = use["steps"][ordinal - 1]
                if signature != {key: step[key] for key in ("tool", "arguments_hash")}:
                    from deskpet.memory.procedure_guidance import bound_step_calls
                    raise ProcedureUseRejected("procedure_call_not_bound_step", detail={
                        "expected": bound_step_calls(use["steps"])[ordinal - 1],
                        "total": len(use["steps"]), "received_tool": call.name})
                record = dict(use_id=use["use_id"], step_ordinal=ordinal, call_id=call_id, call=signature,
                              applicability_fingerprint=current.fingerprint)
                await db.execute("INSERT INTO procedure_use_reservations VALUES (?,?,?,?,?)",
                    (use["use_id"], ordinal, call_id, canonical_json(record), canonical_hash(record)))
                await db.commit()
                return record
            except BaseException:
                await db.rollback()
                raise

    async def commit_effect(self, *, record):
        """Called only with the real settled SDK effect by ProductEffectExecutor."""
        async with self.connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await assert_human_memory_ingress_open_tx(db)
                async with db.execute("SELECT * FROM procedure_use_reservations WHERE call_id=?",
                                      (record.call_id.value,)) as cursor:
                    row = await cursor.fetchone()
                if row is None:
                    await db.commit()
                    return
                reservation = _checked(row)
                use = await self._use(db, record.run_id.value)
                signature = dict(tool=record.tool_name, arguments_hash=canonical_hash(thaw_json(record.arguments)))
                if use is None or use["use_id"] != row["use_id"] or signature != reservation["call"]:
                    raise ProcedureUseRejected("procedure_effect_source_differs")
                body = dict(use_id=use["use_id"], step_ordinal=row["step_ordinal"],
                    call_id=record.call_id.value, effect_id=record.effect_id.value,
                    sdk_run_id=record.run_id.value, state=record.state.value, call=signature)
                async with db.execute("SELECT * FROM procedure_use_effects WHERE use_id=? AND step_ordinal=?",
                                      (use["use_id"], row["step_ordinal"])) as cursor:
                    previous = await cursor.fetchone()
                if previous is not None:
                    if _checked(previous) != body:
                        raise ProcedureUseRejected("procedure_effect_replay_changed")
                else:
                    await db.execute("INSERT INTO procedure_use_effects VALUES (?,?,?,?,?)",
                        (use["use_id"], row["step_ordinal"], record.effect_id.value,
                         canonical_json(body), canonical_hash(body)))
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def _prepared_tx(self, db, use_id):
        async with db.execute("SELECT * FROM procedure_observation_journal WHERE use_id=? AND phase='prepared'", (use_id,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        value = _checked(row)
        authority = ProcedureObservationAuthority.from_json(value["authority"])
        if authority.authority_id != use_id:
            raise ProcedureUseRejected("procedure_initial_authority_identity_differs")
        async with db.execute("PRAGMA user_version") as cursor:
            version = (await cursor.fetchone())[0]
        if version not in _ATTEMPT_LINEAGE_VERSIONS:
            if version == PROCEDURE_SCHEMA_VERSION:
                return value  # Legacy prepared bytes remain an immutable first attempt.
            raise ProcedureUseRejected("procedure_recovery_schema_required")
        async with db.execute("SELECT * FROM procedure_observation_attempts WHERE use_id=? ORDER BY attempt_ordinal LIMIT 129", (use_id,)) as cursor:
            attempts = await cursor.fetchall()
        if len(attempts) > 127:
            raise ProcedureUseRejected("procedure_recovery_attempt_limit")
        for ordinal, row in enumerate(attempts, 2):
            previous = ProcedureObservationAuthorityRef.from_authority(authority)
            value = _checked(row)
            authority = ProcedureObservationAuthority.from_json(value["authority"])
            if (row["attempt_ordinal"] != ordinal or row["authority_id"] != authority.authority_id
                    or row["previous_authority_id"] != previous.authority_id
                    or row["previous_ref_hash"] != previous.ref_hash
                    or value.get("previous_reference") != previous.to_json()):
                raise ProcedureUseRejected("procedure_recovery_attempt_chain_differs")
        return value

    async def prepared(self, use_id):
        async with self.connection() as db:
            await db.execute("BEGIN")
            return await self._prepared_tx(db, use_id)

    async def append_attempt(self, use_id, previous_reference, body):
        async with self.connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await assert_human_memory_ingress_open_tx(db)
                async with db.execute("PRAGMA user_version") as cursor:
                    if (await cursor.fetchone())[0] not in _ATTEMPT_LINEAGE_VERSIONS:
                        raise ProcedureUseRejected("procedure_recovery_schema_required")
                current = await self._prepared_tx(db, use_id)
                if current is None:
                    raise ProcedureUseRejected("procedure_initial_authority_missing")
                current_ref = ProcedureObservationAuthorityRef.from_authority(
                    ProcedureObservationAuthority.from_json(current["authority"]))
                if current_ref != previous_reference:
                    await db.commit()
                    return current  # The first durable concurrent recovery wins.
                async with db.execute("SELECT count(*) FROM procedure_observation_attempts WHERE use_id=?", (use_id,)) as cursor:
                    ordinal = (await cursor.fetchone())[0] + 2
                if ordinal > 128:
                    raise ProcedureUseRejected("procedure_recovery_attempt_limit")
                authority = ProcedureObservationAuthority.from_json(body["authority"])
                if body.get("previous_reference") != previous_reference.to_json():
                    raise ProcedureUseRejected("procedure_recovery_previous_reference_differs")
                await db.execute("INSERT INTO procedure_observation_attempts VALUES (?,?,?,?,?,?,?)", (
                    authority.authority_id, use_id, ordinal, previous_reference.authority_id,
                    previous_reference.ref_hash, canonical_json(body), canonical_hash(body)))
                await db.commit()
                return body
            except BaseException:
                await db.rollback()
                raise

    async def resolve_procedure_observation_authority(self, reference):
        if type(reference) is not ProcedureObservationAuthorityRef:
            raise TypeError("ProcedureObservationAuthorityRef required")
        async with self.connection() as db:
            await db.execute("BEGIN")
            async with db.execute("SELECT * FROM procedure_observation_journal WHERE use_id=? AND phase='prepared'", (reference.authority_id,)) as cursor:
                row = await cursor.fetchone()
            if row is None:
                async with db.execute("PRAGMA user_version") as cursor:
                    if (await cursor.fetchone())[0] not in _ATTEMPT_LINEAGE_VERSIONS:
                        raise ProcedureUseRejected("procedure_observation_source_missing")
                async with db.execute("SELECT * FROM procedure_observation_attempts WHERE authority_id=?", (reference.authority_id,)) as cursor:
                    row = await cursor.fetchone()
            if row is None:
                raise ProcedureUseRejected("procedure_observation_source_missing")
            value = _checked(row)
            authority = ProcedureObservationAuthority.from_json(value["authority"])
            if (authority.intent.subject != self.principal.actor_id
                    or ProcedureObservationAuthorityRef.from_authority(authority) != reference):
                raise ProcedureUseRejected("procedure_observation_reference_differs")
            use = await self._use(db, authority.intent.run_id)
            if use is None or use["use_id"] != row["use_id"] or not callable(self.source_verifier):
                raise ProcedureUseRejected("procedure_observation_actual_source_verifier_required")
            await self._prepared_tx(db, use["use_id"])  # Validate the whole persisted attempt chain.
        await self.source_verifier(use, value, authority)
        return authority

    async def journal(self, use_id, phase, body=None):
        if phase not in {"prepared", "applied", "rejected"}:
            raise ValueError("procedure_journal_phase_invalid")
        async with self.connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                if body is not None:
                    await assert_human_memory_ingress_open_tx(db)
                async with db.execute("SELECT * FROM procedure_observation_journal WHERE use_id=? AND phase=?",
                                      (use_id, phase)) as cursor:
                    row = await cursor.fetchone()
                if row is not None:
                    value = _checked(row)
                    if body is not None and value != body:
                        raise ProcedureUseRejected("procedure_journal_replay_changed")
                elif body is not None:
                    await db.execute("INSERT INTO procedure_observation_journal VALUES (?,?,?,?)",
                        (use_id, phase, canonical_json(body), canonical_hash(body)))
                    value = body
                else:
                    value = None
                await db.commit()
                return value
            except BaseException:
                await db.rollback()
                raise

    async def reservations(self, use_id):
        async with self.connection() as db:
            async with db.execute("SELECT * FROM procedure_use_reservations WHERE use_id=? ORDER BY step_ordinal",
                                  (use_id,)) as cursor:
                return tuple(_checked(row) for row in await cursor.fetchall())
