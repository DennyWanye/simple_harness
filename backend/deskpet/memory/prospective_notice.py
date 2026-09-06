"""New ACKs have a Host UI carrier, independent of the model's final answer.

Read-only Host facts + public Memory sources. A projected notice is neither a
SDK message, a new ACK, nor evidence that a person has seen the content.
"""
from __future__ import annotations

import inspect
import json
from dataclasses import asdict

from simple_harness_memory import OccurrenceInboxEntryV1

from deskpet.execution.terminal_identity import read_primary_terminal_identity_tx
from deskpet.memory.primary_visibility import read_evidence_pair
from deskpet.memory.prospective_occurrence import _identity, _presentation, _rows, decode_snapshot
from deskpet.memory.prospective_source_dependencies import ProspectiveSourceDependencies
from deskpet.memory.s5c_store import S5cConflict, S5cStore, _hash, _owner
from deskpet.task_scope.protocol import canonical_hash

NOTICE_CONTRACT = "host.prospective.notice/v1"


async def _fetch(db, sql, args=()):
    async with db.execute(sql, args) as cursor:
        return await cursor.fetchall()


class ProspectiveNoticeReader:
    def __init__(self, *, path, subject, runtime_getter, terminal_reader):
        self.path, self.subject = path, subject
        self.runtime_getter, self.terminal_reader = runtime_getter, terminal_reader

    async def _current(self, manager, principal, expected):
        """A bounded full public inbox read; missing/changed is not permission."""
        after = None
        found = {}
        for _ in range(16):
            page = await manager.read_occurrence_inbox(principal=principal, after=after, limit=200)
            prior = after
            for entry in page.entries:
                if type(entry) is not OccurrenceInboxEntryV1:
                    raise S5cConflict("prospective_notice_public_type_invalid")
                position = (entry.occurred_at, entry.event_id)
                if prior is not None and position <= prior:
                    raise S5cConflict("prospective_notice_public_order_invalid")
                prior = position
                if entry.occurrence_key in expected:
                    if entry.occurrence_key in found:
                        raise S5cConflict("prospective_notice_public_duplicate")
                    found[entry.occurrence_key] = entry
            if page.next_after is None:
                return found
            if not page.entries or page.next_after != prior:
                raise S5cConflict("prospective_notice_public_cursor_invalid")
            after = page.next_after
        raise S5cConflict("prospective_notice_inbox_scan_incomplete")

    async def read(self, *, db, primary, turn, disclosure_context):
        # Old schemas/no ACK/new runtime without this lane remain ordinary reads.
        if not await _fetch(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='prospective_occurrences'"):
            return []
        rows = await _fetch(db, "SELECT p.* FROM prospective_occurrences p "
            "JOIN foreground_run_sdk_bindings b ON b.sdk_run_id=p.sdk_run_id "
            "WHERE b.host_run_id=? AND p.phase='acknowledged' ORDER BY p.record_id LIMIT 65",
            (turn["host_run_id"],))
        if len(rows) > 64:
            raise S5cConflict("prospective_notice_run_limit")
        marked = []
        for row in rows:
            marker = json.loads(row["inbox_json"]).get("proof", {}).get("notice_contract")
            if marker is not None:
                if marker != NOTICE_CONTRACT:
                    raise S5cConflict("prospective_notice_contract_invalid")
                marked.append(row)
        if not marked:
            return []
        runtime = self.runtime_getter() if callable(self.runtime_getter) else None
        if runtime is None:
            raise S5cConflict("prospective_notice_runtime_unavailable")
        principal = runtime.principal()
        context = disclosure_context
        if (principal.actor_id != self.subject or context.subject != self.subject
                or context.recipient.value != "user_self" or context.recipient_id != self.subject
                or context.intended_audience.value != "user_self" or context.purpose.value != "user_review"
                or context.source.value != "authenticated_host" or context.trust.value != "trusted_authority"
                or context.generation.value != "current"):
            raise S5cConflict("prospective_notice_disclosure_unavailable")
        owner = _owner(principal)
        bindings = await _fetch(db, "SELECT b.*,r.subject,r.primary_conversation_id,r.turn_id "
            "FROM foreground_run_sdk_bindings b JOIN foreground_runs r ON r.host_run_id=b.host_run_id "
            "WHERE b.host_run_id=?", (turn["host_run_id"],))
        if len(bindings) != 1:
            raise S5cConflict("prospective_notice_run_missing")
        binding = bindings[0]
        payload = json.loads(binding["binding_json"])
        run = binding["sdk_run_id"]
        if (binding["subject"] != self.subject or binding["primary_conversation_id"] != primary
                or binding["turn_id"] != turn["turn_id"]
                or canonical_hash(payload) != binding["binding_hash"]
                or payload.get("host_run_id") != turn["host_run_id"] or payload.get("sdk_run_id") != run):
            raise S5cConflict("prospective_notice_run_binding_differs")
        terminal = await read_primary_terminal_identity_tx(db, subject=self.subject, primary_ref=primary,
            host_run_id=turn["host_run_id"], sdk_run_id=run)
        if terminal is not None:
            if not callable(self.terminal_reader):
                raise S5cConflict("prospective_notice_terminal_reader_missing")
            # Public transcript reader requires the true original current USER.
            envelope, _ = await read_evidence_pair(db=db, subject=self.subject, primary_ref=primary,
                evidence_id=turn["evidence_id"])
            text = envelope.sanitized_payload["text"]
            result = self.terminal_reader(run, current_text=text)
            actual, _ = await result if inspect.isawaitable(result) else result
            terminal.verify_sdk_terminal(actual)
        elif turn["turn_state"] == "SETTLED":
            raise S5cConflict("prospective_notice_terminal_missing")
        accepted = []
        for ack in marked:
            if ack["owner_key"] != owner or ack["sdk_run_id"] != run:
                raise S5cConflict("prospective_notice_owner_run_differs")
            key = ack["occurrence_key"]
            history = await _rows(db, owner, key)
            checked = [(r, b, e) for r, b, e in history if r["record_id"] == ack["record_id"]]
            if len(checked) != 1:
                raise S5cConflict("prospective_notice_ack_missing")
            _, body, entry = checked[0]
            presented, _, original = await _presentation(db, history, run)
            if entry.to_json() != original.to_json():
                raise S5cConflict("prospective_notice_ack_content_differs")
            snapshot = (await _fetch(db, "SELECT * FROM run_context_snapshot_receipts WHERE snapshot_id=?",
                (presented["snapshot_id"],)))[0]
            _, group = decode_snapshot(snapshot)
            exact = [] if group is None else [x.entry for x in group.items if x.entry.occurrence_key == key]
            if group is None or group.owner != owner or len(exact) != 1 or exact[0].to_json() != entry.to_json():
                raise S5cConflict("prospective_notice_snapshot_content_differs")
            exits = await _fetch(db, "SELECT memory_id,prospective_revision,presented_run_id,settled_reason "
                "FROM occurrence_presented WHERE occurrence_key=?", (key,))
            if len(exits) != 1 or tuple(exits[0]) != (entry.memory_id, entry.prospective_revision, run, "acknowledged"):
                raise S5cConflict("prospective_notice_exit_differs")
            settled = [b for r, b, _ in history if r["phase"] == "settled"]
            if terminal is not None:
                proof = dict(ack_id=ack["record_id"], ack_hash=ack["record_hash"], terminal_identity=asdict(terminal))
                if len(settled) != 1 or settled[0]["proof"] != proof or settled[0]["reason"] != "acknowledged":
                    raise S5cConflict("prospective_notice_terminal_binding_differs")
            elif settled:
                raise S5cConflict("prospective_notice_uncommitted_terminal")
            notice_id = _hash([NOTICE_CONTRACT, owner, run, key, ack["record_id"], ack["record_hash"]])
            accepted.append((entry, notice_id))
        manager = await runtime.manager()
        store = S5cStore(self.path, principal)
        sources = ProspectiveSourceDependencies(store=store, runtime_getter=lambda: runtime)
        evidence = {}
        for entry, _ in accepted:
            ids = await sources.evidence_ids(manager, entry.memory_id, entry.prospective_revision)
            if not ids or len(ids) > 256:
                raise S5cConflict("prospective_notice_source_missing_or_limit")
            for evidence_id in ids:
                # Complete real S1 bindings; final shared history policy checks
                # current visibility after this reader's slow public calls.
                await read_evidence_pair(db=db, subject=self.subject, primary_ref=primary, evidence_id=evidence_id)
            evidence[entry.occurrence_key] = tuple(sorted(set(ids)))
        current = await self._current(manager, principal, {e.occurrence_key for e, _ in accepted})
        result = []
        for original, notice_id in accepted:
            now = current.get(original.occurrence_key)
            if (now is None or now.suppressed or now.outcome != "matched"
                    or now.lifecycle_state not in {"triggered", "in_progress", "rescheduled"}
                    or now.effective_privacy_class not in {"public", "personal"}):
                continue
            if _identity(now) != _identity(original) or now.action_text != original.action_text:
                raise S5cConflict("prospective_notice_current_content_differs")
            # Public inbox action/origin are occurrence-pinned, but content_hash
            # belongs to the current memory head. A legal REVISE retires this
            # notice; do not replace its text or make ordinary history unreadable.
            if now.content_hash != original.content_hash:
                continue
            if type(now.action_text) is not str or not now.action_text.strip():
                raise S5cConflict("prospective_notice_content_missing")
            result.append((2**62 + int(notice_id[:15], 16), "reminder", now.action_text,
                notice_id, evidence[original.occurrence_key]))
        if len({r[0] for r in result}) != len(result):
            raise S5cConflict("prospective_notice_position_collision")
        return sorted(result)
