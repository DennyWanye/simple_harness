"""Physical post-turn analysis preflight over Host facts and public Memory reads.

The source SDK Run is terminal. Its foreground provider grants do not authorize
this separate Host analysis attempt. No SDK handoff or permission is synthesized.
"""
from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
from simple_harness import MemoryAnalysisRequest, thaw_json
from simple_harness.execution.provider_invocations import provider_request_json
from simple_harness.providers import ProviderRequestRejectedError

from deskpet.memory.analysis_lineage import binding_model_config_hash
from deskpet.memory.analysis_proposal import stable_id
from deskpet.memory.primary_visibility import read_evidence_pair
from deskpet.memory.trusted_disclosure import (
    assert_executable, binding_token, bound_record_tx, current_record_tx,
)
from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1
from deskpet.task_scope.protocol import canonical_hash


class AnalysisRequestDisclosureRejected(ProviderRequestRejectedError):
    error_code = "analysis_request_disclosure_rejected"
    default_message = "Current analysis input cannot be verified."


async def _rows(db, sql, args=()):
    cursor = await db.execute(sql, args)
    try:
        return await cursor.fetchall()
    finally:
        await cursor.close()


async def read_observation_tx(db, *, identity, subject):
    """Verify the original Host S1 pair, not a caller-supplied JSON commitment."""
    rows = await _rows(db, "SELECT primary_conversation_id FROM human_memory_evidence "
                       "WHERE evidence_id=? AND subject=?", (identity, subject))
    if not rows:
        return None
    envelope, _ = await read_evidence_pair(db=db, subject=subject,
        primary_ref=rows[0]["primary_conversation_id"], evidence_id=identity)
    from deskpet.memory.semantic_correction import POLICY
    if envelope.filter_policy_version != POLICY or envelope.source_kind.value != "runtime_event":
        raise ValueError("analysis_input_observation_invalid")
    return envelope, thaw_json(envelope.sanitized_payload)


async def source_policy_snapshot_tx(db, request):
    """Each source's immutable turn permission AND the current head, in one TX."""
    refs = request.ordered_evidence_refs
    if not 1 <= len(refs) <= 256:
        raise ValueError("analysis_source_limit")
    current = await current_record_tx(db, request.subject)
    context = request.disclosure_context
    # Preserve the admitted context, including its authority_ref. Never replace
    # it with a constructed context for the post-turn provider request ID.
    if (context.subject != request.subject or context.run_id != request.run_id
            or context.recipient.value != "user_self" or context.recipient_id != request.subject
            or context.intended_audience.value != "user_self" or context.purpose.value != "task_execution"
            or context.source.value != "authenticated_host" or context.trust.value != "trusted_authority"
            or context.generation.value != "current"):
        raise ValueError("analysis_source_disclosure_incompatible")
    sources = []
    for ref in refs:
        links = await _rows(db, "SELECT o.*,t.turn_json,t.turn_hash,t.primary_conversation_id,"
            "r.subject AS run_subject,b.sdk_run_id AS bound_sdk_run "
            "FROM memory_ingestion_evidence_links l JOIN memory_ingestion_outbox o ON o.outbox_id=l.outbox_id "
            "JOIN foreground_runs r ON r.host_run_id=o.host_run_id AND r.turn_id=o.turn_id "
            "JOIN foreground_turns t ON t.turn_id=r.turn_id AND t.subject=r.subject "
            "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id "
            "WHERE l.evidence_id=? ORDER BY o.outbox_id LIMIT 257", (ref.evidence_id,))
        if not links or len(links) > 256:
            raise ValueError("analysis_source_origin_unavailable")
        for link in links:
            if (link["subject"] != request.subject or link["run_subject"] != request.subject
                    or link["sdk_run_id"] != link["bound_sdk_run"]
                    or ref.evidence_id not in json.loads(link["evidence_ids_json"])):
                raise ValueError("analysis_source_origin_differs")
            envelope, _ = await read_evidence_pair(db=db, subject=request.subject,
                primary_ref=link["primary_conversation_id"], evidence_id=ref.evidence_id)
            if (envelope.envelope_hash != ref.content_hash or envelope.run_id != request.run_id
                    or envelope.disclosure_context != context):
                raise ValueError("analysis_source_evidence_differs")
            turn = json.loads(link["turn_json"])
            if canonical_hash(turn) != link["turn_hash"] or turn["subject"] != request.subject:
                raise ValueError("analysis_source_turn_differs")
            token = turn.get("disclosure_binding")
            if "disclosure_binding" in turn:
                await bound_record_tx(db, subject=request.subject, token=token)
                if current is None or token != binding_token(current):
                    raise ValueError("analysis_source_policy_stale")
            elif current is not None and current["source_origin"] != "host_default":
                raise ValueError("analysis_source_legacy_policy_changed")
            if current is not None:
                assert_executable(current)
            lineage = json.loads(link["analysis_lineage_json"])
            config_hash = binding_model_config_hash(lineage["run_binding"],
                                                    endpoint_identity=lineage["endpoint_identity"])
            if ((lineage["provider_id"], lineage["model_id"], lineage["model_config_hash"])
                    != (request.provider_id, request.model_id, request.model_config_hash)
                    or config_hash != request.model_config_hash or link["model_config_hash"] != config_hash):
                raise ValueError("analysis_source_provider_differs")
            sources.append(dict(evidence_id=ref.evidence_id, envelope_hash=ref.content_hash,
                outbox_id=link["outbox_id"], host_run_id=link["host_run_id"], sdk_run_id=link["sdk_run_id"],
                turn_id=link["turn_id"], turn_hash=link["turn_hash"], original_token=token,
                current_token=None if current is None else binding_token(current),
                lineage=lineage))
    return {"schema_version": 1, "disclosure": context.to_json(), "sources": sources}


class AnalysisPhysicalRequestGuard:
    def __init__(self, state_db_path, *, binding, runtime_getter):
        if type(binding) is not SdkRunBindingV1 or not callable(runtime_getter):
            raise TypeError("analysis_guard_requires_binding_and_runtime")
        self.path = Path(state_db_path)
        self.binding = binding
        self.runtime_getter = runtime_getter

    async def _facts(self, actual, subject):
        async with aiosqlite.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            rows = await _rows(db, "SELECT a.*,r.subject,h.generation AS current_generation,h.current_state "
                "FROM post_turn_invocation_attempts a JOIN foreground_runs r ON r.host_run_id=a.host_run_id "
                "JOIN foreground_run_heads h ON h.host_run_id=r.host_run_id "
                "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id AND b.sdk_run_id=a.sdk_run_id "
                "WHERE a.sdk_run_id=? AND a.purpose='analysis' AND a.status='handed_off' "
                "AND ('post-turn-analysis-' || substr(a.request_hash,1,24) || '-' || a.attempt_ordinal)=? LIMIT 2",
                (self.binding.run_id, actual.request_id.value))
            if len(rows) != 1:
                raise ValueError("analysis_physical_attempt_missing")
            row = dict(rows[0])
            if (row["subject"] != subject or row["generation"] != row["current_generation"]
                    or row["current_state"] not in {"COMPLETED", "FAILED", "STOPPED", "CANCELLED"}
                    or row["unknown_class"] is not None or row["settled_at"] is not None):
                raise ValueError("analysis_physical_attempt_not_current")
            pair = await read_observation_tx(db, identity=stable_id("analysis-attempt-input", row["attempt_id"]),
                                             subject=subject)
            if pair is None:
                raise ValueError("analysis_physical_input_missing")
            envelope, bound = pair
            if type(bound.get("physical_guard_version")) is not int or bound["physical_guard_version"] != 1:
                raise ValueError("analysis_physical_legacy_input")
            request = MemoryAnalysisRequest.from_json(bound["analysis_request"])
            from deskpet.memory.analysis_executor import evidence_set_key
            if (request.subject != subject or envelope.run_id != request.run_id
                    or envelope.disclosure_context != request.disclosure_context
                    or envelope.evidence_refs != request.ordered_evidence_refs
                    or bound["attempt_id"] != row["attempt_id"] or request.request_hash != row["request_hash"]
                    or bound["request_hash"] != request.request_hash
                    or evidence_set_key(request) != row["evidence_set_key"]
                    or bound["evidence_set_key"] != row["evidence_set_key"]
                    or bound["input"]["request_id"] != actual.request_id.value
                    or canonical_hash(bound["input"]) != bound["input_hash"]
                    or canonical_hash(bound["provider_request"]) != bound["provider_request_hash"]
                    or canonical_hash(provider_request_json(actual)) != bound["provider_request_hash"]):
                raise ValueError("analysis_physical_input_differs")
            members = await _rows(db, "SELECT subject,run_id,evidence_id FROM post_turn_invocation_members "
                                  "WHERE attempt_id=? ORDER BY subject,run_id,evidence_id", (row["attempt_id"],))
            if [tuple(m) for m in members] != sorted((subject, request.run_id, ref.evidence_id)
                                                   for ref in request.ordered_evidence_refs):
                raise ValueError("analysis_physical_members_differ")
            snapshot_id = stable_id("analysis-candidates", row["evidence_set_key"])
            snap_pair = await read_observation_tx(db, identity=snapshot_id, subject=subject)
            if (bound["snapshot_id"] != snapshot_id or snap_pair is None
                    or canonical_hash(snap_pair[1]) != bound["snapshot_hash"]
                    or snap_pair[1]["subject"] != subject
                    or snap_pair[1]["evidence_set_key"] != row["evidence_set_key"]):
                raise ValueError("analysis_physical_snapshot_differs")
            policy = await source_policy_snapshot_tx(db, request)
            if policy != bound["source_policy"]:
                raise ValueError("analysis_physical_policy_changed")
            matching = [s for s in policy["sources"] if s["host_run_id"] == row["host_run_id"]
                        and s["sdk_run_id"] == self.binding.run_id]
            if (not matching or any(SdkRunBindingV1.from_record(s["lineage"]["run_binding"]) != self.binding
                                    for s in matching)
                    or (row["provider_id"], row["model_id"], row["model_config_hash"])
                    != (request.provider_id, request.model_id, request.model_config_hash)):
                raise ValueError("analysis_physical_binding_differs")
            return request, snap_pair[1], canonical_hash({"attempt": row, "carrier": bound})

    async def __call__(self, actual):
        try:
            runtime = self.runtime_getter()
            from deskpet.memory.semantic_correction import SemanticCorrectionAuthority
            authority = runtime._memory_action_authority
            if type(authority) is not SemanticCorrectionAuthority or authority._path.resolve() != self.path.resolve():
                raise ValueError("analysis_physical_authority_missing")
            principal = runtime.principal()
            request, snapshot, original = await self._facts(actual, principal.actor_id)
            await authority.check(request, snapshot)
            # The source check awaits public Memory; re-read Host attempt and
            # policy on a fresh connection. Terminal source Runs need no live lease.
            _, _, current = await self._facts(actual, principal.actor_id)
            if current != original or runtime.principal() != principal:
                raise ValueError("analysis_physical_authority_changed")
            # Independent final public read catches withdrawal during a slow
            # first visibility read. These two databases are not an atomic fence.
            await authority.check(request, snapshot)
            _, _, current = await self._facts(actual, principal.actor_id)
            if current != original or runtime.principal() != principal:
                raise ValueError("analysis_physical_authority_changed")
        except Exception as exc:
            raise AnalysisRequestDisclosureRejected(private_cause=exc) from None
