"""Owner UI projection of exact Host Manual challenges, never a grant authority.

All SQL is over Host journals. SDK DTOs only validate their public wire bindings.
Original S1/proposal/challenge/tool journal writes are separate transactions;
unlinked crash residues are not synthesized into an actionable prompt.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from simple_harness import (ManualWorkspaceBindingChallenge,
    ManualWorkspaceBindingAuthorizationReceipt, WorkspaceBindingProposal,
    WorkspaceBindingAuthorityGrant, WorkspaceBindingSetReceipt)
from simple_harness.runtime import SanitizedEvidenceEnvelope, SanitizedEvidenceReceipt
from deskpet.memory.primary_read_model import PrimaryReadError
from deskpet.memory.writer_fence import human_memory_request_boundary
from deskpet.task_scope.protocol import canonical_hash, identifier
from deskpet.task_scope.workspace_bindings import canonical_workspace_root


IDENTITY_FIELDS = frozenset({"primary_ref", "run_ref", "sdk_run_ref", "generation",
    "effect_ref", "challenge_ref", "challenge_hash", "scope_ref", "proposal_hash"})


def require(ok, code="primary_binding_identity_invalid"):
    if not ok:
        raise PrimaryReadError(code)


class PrimaryWorkspaceBindings:
    def __init__(self, path, *, subject, authority, decide):
        self.path, self.subject = Path(path), subject
        self.authority, self.decide = authority, decide

    def _snapshot(self, primary_ref, context, *, challenge_ref=None):
        identifier(primary_ref, "primary_ref", 512)
        with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            primary = db.execute("SELECT subject FROM human_memory_primary_conversations "
                "WHERE primary_conversation_id=? AND writable=1", (primary_ref,)).fetchone()
            require(primary is not None and primary[0] == self.subject, "primary_binding_owner_mismatch")
            # Limit bounds disclosure, not total historical scan work. Never
            # silently omit further pending decisions behind a success result.
            rows = db.execute("SELECT i.*,h.host_run_id,h.generation,b.binding_json,b.binding_hash "
                "FROM context_route_tool_invocations i "
                "JOIN foreground_run_sdk_bindings b ON b.sdk_run_id=i.sdk_run_id "
                "JOIN foreground_run_heads h ON h.host_run_id=b.host_run_id AND h.sdk_run_id=b.sdk_run_id "
                "WHERE h.subject=? AND h.primary_conversation_id=? AND i.verdict='rejected' "
                "AND json_extract(i.detail_json,'$.code')='context_route_binding_authorization_required' "
                + ("AND json_extract(i.detail_json,'$.binding_challenge.challenge_ref')=? " if challenge_ref else "")
                + "ORDER BY i.recorded_at DESC,i.invocation_id DESC LIMIT 33",
                (self.subject, primary_ref, challenge_ref) if challenge_ref else (self.subject, primary_ref)).fetchall()
            require(len(rows) <= 32, "primary_binding_display_limit_exceeded")
            items = [self._item(db, row, primary_ref, context) for row in rows]
            require(len({v["challenge_ref"] for v in items}) == len(items))
            return {"primary_ref": primary_ref, "items": items, "truncated": False}

    def _item(self, db, row, primary_ref, context):
        detail = json.loads(row["detail_json"])
        invocation = {k: row[k] for k in ("decision_id", "effect_id", "proposal_hash", "raw_call_id", "sdk_run_id", "verdict")}
        require(canonical_hash({**invocation, "detail": detail}) == row["invocation_hash"])
        binding = json.loads(row["binding_json"])
        require(canonical_hash(binding) == row["binding_hash"] and
            binding.get("host_run_id") == row["host_run_id"] and binding.get("sdk_run_id") == row["sdk_run_id"])
        shown = detail["binding_challenge"]
        saved = db.execute("SELECT * FROM task_workspace_manual_challenges WHERE challenge_id=?",
            (shown["challenge_ref"],)).fetchone()
        require(saved is not None)
        challenge = ManualWorkspaceBindingChallenge.from_json(json.loads(saved["challenge_json"]))
        proposal_row = db.execute("SELECT * FROM task_workspace_binding_proposals WHERE proposal_id=?",
            (challenge.proposal_id,)).fetchone()
        require(proposal_row is not None)
        proposal = WorkspaceBindingProposal.from_json(json.loads(proposal_row["proposal_json"]))
        challenge.verify_proposal(proposal)
        # challenge.run_id intentionally belongs to the Manual proposal domain,
        # not either foreground Run. The actual relation is its durable key/S1.
        require(challenge.subject == self.subject and
            challenge.challenge_hash == saved["challenge_hash"] == shown["challenge_hash"] and
            proposal.proposal_hash == proposal_row["proposal_hash"] == shown["proposal_hash"] and
            proposal.task_scope_id == detail["task_scope_id"] == shown["scope_ref"] and
            proposal.idempotency_key == f"context-route:{row['sdk_run_id']}:{row['effect_id']}")
        evidence = db.execute("SELECT e.*,r.receipt_json,r.receipt_sha256 FROM human_memory_evidence e "
            "JOIN human_memory_sanitization_receipts r ON r.receipt_id=e.receipt_id WHERE e.evidence_id=?",
            (challenge.authorization_evidence_id,)).fetchone()
        require(evidence is not None)
        env = SanitizedEvidenceEnvelope.from_json(json.loads(evidence["envelope_json"]))
        receipt = SanitizedEvidenceReceipt.from_json(json.loads(evidence["receipt_json"]))
        receipt.verify(env)
        payload = json.loads(evidence["payload_json"])
        expected = {"schema_version": 1, "action": "binding.append", "scope_ref": proposal.task_scope_id,
            "root": proposal.root.canonical_path, "idempotency_key": proposal.idempotency_key}
        if "expected_filesystem_identity_hash" in payload:
            expected["expected_filesystem_identity_hash"] = proposal.root.filesystem_identity.identity_hash
        require(payload == expected and dict(env.sanitized_payload) == payload and
            env.subject == evidence["subject"] == self.subject and evidence["primary_conversation_id"] == primary_ref and
            evidence["source_ref"] == f"host-binding-append:{proposal.idempotency_key}" and
            env.envelope_hash == evidence["envelope_sha256"] == challenge.authorization_evidence_hash and
            receipt.receipt_hash == evidence["receipt_sha256"])
        target = {"primary_ref": primary_ref, "run_ref": row["host_run_id"], "sdk_run_ref": row["sdk_run_id"],
            "generation": row["generation"], "effect_ref": row["effect_id"],
            "challenge_ref": challenge.challenge_id, "challenge_hash": challenge.challenge_hash,
            "scope_ref": proposal.task_scope_id, "proposal_hash": proposal.proposal_hash}
        state, binding_receipt = self._decision_state(db, challenge, proposal, context)
        if state in {"pending", "allow_recorded"} and context["mode"] != "manual":
            state = "policy_changed"
        can_decide = state in {"pending", "allow_recorded"} and context["mode"] == "manual"
        if can_decide:
            current_root = canonical_workspace_root(proposal.root.canonical_path, root_id=proposal.root.root_id)
            if current_root != proposal.root:
                can_decide = False
                state = "root_changed"
        return {**target, "root_path": proposal.root.canonical_path,
            "root_identity_hash": proposal.root.root_identity_hash,
            "expires_at_millis": challenge.expires_at_millis, "state": state,
            "can_decide": can_decide, "binding_receipt_ref": binding_receipt}

    def _decision_state(self, db, challenge, proposal, context):
        head = db.execute("SELECT current_revision FROM task_workspace_binding_heads WHERE task_scope_id=?",
            (proposal.task_scope_id,)).fetchone()
        current_revision = 0 if head is None else head[0]
        row = db.execute("SELECT * FROM task_workspace_manual_decisions WHERE challenge_id=?",
            (challenge.challenge_id,)).fetchone()
        if row is None:
            if current_revision != proposal.base_binding_set_revision:
                return "binding_changed", None
            now = context["now_millis"]
            return ("pending" if challenge.not_before_millis <= now < challenge.expires_at_millis else "expired"), None
        decision = ManualWorkspaceBindingAuthorizationReceipt.from_json(json.loads(row["decision_json"]))
        require(decision.receipt_hash == row["receipt_hash"] and decision.challenge_id == challenge.challenge_id
            and decision.decided_by_actor_id == self.subject and decision.decision.value == row["decision"])
        try:
            decision.verify_challenge(challenge)
        except ValueError as exc:
            # Match the original store's exact DENY replay contract: all
            # challenge fields verify before its non-authorizing decision.
            if decision.decision.value != "deny" or "does not authorize" not in str(exc):
                raise
        if decision.decision.value == "deny":
            return "denied", None
        grant_row = db.execute("SELECT * FROM task_workspace_binding_grants WHERE proposal_id=?",
            (proposal.proposal_id,)).fetchone()
        if grant_row is None:
            return ("allow_recorded" if current_revision == proposal.base_binding_set_revision else "binding_changed"), None
        grant = WorkspaceBindingAuthorityGrant.from_json(json.loads(grant_row["grant_json"]))
        grant.verify_proposal(proposal)
        require(grant.grant_hash == grant_row["grant_hash"] and grant.source.value == "manual" and
            grant.source_authority_ref == decision.host_receipt_ref and grant.source_authority_hash == decision.host_receipt_hash)
        bound = db.execute("SELECT * FROM task_workspace_binding_revisions WHERE grant_id=?", (grant.grant_id,)).fetchone()
        if bound is None:
            return ("allow_recorded" if current_revision == proposal.base_binding_set_revision else "binding_changed"), None
        ack = WorkspaceBindingSetReceipt.from_json(json.loads(bound["receipt_json"]))
        require(ack.receipt_hash == bound["receipt_hash"] and ack.task_scope_id == proposal.task_scope_id and
            ack.grant_id == grant.grant_id and ack.grant_hash == grant.grant_hash and ack.appended_root == proposal.root)
        # A real binding ACK stays historical fact even after challenge expiry.
        return "bound", ack.receipt_id

    async def _context(self):
        reader = getattr(self.authority, "manual_binding_read_context", None)
        require(callable(reader), "primary_binding_reader_unavailable")
        return await reader(subject=self.subject)

    async def pending(self, *, primary_ref):
        async with human_memory_request_boundary():
            context = await self._context()
            async with human_memory_request_boundary():
                result = self._snapshot(primary_ref, context)
        return result

    async def respond(self, *, decision, **target):
        require(set(target) == IDENTITY_FIELDS and type(target["generation"]) is int and target["generation"] > 0)
        require(decision in {"allow", "deny"}, "primary_binding_decision_invalid")
        # The connection/epoch lease spans every await, including S1 write and
        # existing decision/grant/binding commits. Old terminal Runs are allowed.
        async with human_memory_request_boundary():
            context = await self._context()
            async with human_memory_request_boundary():
                items = self._snapshot(target["primary_ref"], context, challenge_ref=target["challenge_ref"])["items"]
            require(len(items) == 1 and all(items[0][k] == target[k] for k in IDENTITY_FIELDS), "primary_binding_target_stale")
            item = items[0]
            if item["state"] == "bound":
                require(decision == "allow", "primary_binding_decision_conflict")
                return {**target, "state": "bound", "binding_receipt_ref": item["binding_receipt_ref"]}
            if item["state"] == "denied":
                require(decision == "deny", "primary_binding_decision_conflict")
                return {**target, "state": "denied"}
            require(item["can_decide"], "primary_binding_not_actionable")
            require(item["state"] != "allow_recorded" or decision == "allow", "primary_binding_decision_conflict")
            from deskpet.memory.human_memory_service import DecideManualBindingRequest
            result = await self.decide(DecideManualBindingRequest(target["challenge_ref"], decision,
                f"primary-binding-ui:{target['challenge_ref']}:{decision}"))
            require(result.get("status") in {"bound", "denied"}, "primary_binding_result_unconfirmed")
            return {**target, "state": result["status"], "binding_receipt_ref": result.get("receipt_ref")}
