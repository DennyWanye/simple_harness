"""Bounded primary history over Host receipts and a public SDK transcript reader.

No Session selector, SDK database, projection cache, or implicit disclosure grant.
The composition owns the reader and current suppression resolver.
"""

from __future__ import annotations

import base64
import inspect
import json
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from simple_harness.runtime import SanitizedEvidenceEnvelope, SanitizedEvidenceReceipt
from simple_harness_memory.core.suppression import (
    OrdinaryMemoryPurpose,
    SuppressionCandidate,
    SuppressionResolution,
)

from deskpet.task_scope.protocol import (
    canonical_hash,
    canonical_json,
    identifier,
    redact_credential_shapes,
)


class PrimaryReadError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def bounded_int(value, *, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PrimaryReadError("primary_read_request_invalid")
    return value


def _token(body):
    return base64.urlsafe_b64encode(canonical_json(body).encode()).decode().rstrip("=")


def _decode(value, keys):
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 4096:
            raise ValueError()
        body = json.loads(
            base64.b64decode(
                value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
            )
        )
        if (
            type(body) is not dict
            or set(body) != set(keys)
            or type(body["v"]) is not int
            or body["v"] != 1
        ):
            raise ValueError()
        return body
    except (ValueError, TypeError, KeyError) as exc:
        raise PrimaryReadError("primary_read_ref_invalid") from exc


async def _resolved(value):
    return await value if inspect.isawaitable(value) else value


async def _rows(db, query, args=()):
    cursor = await db.execute(query, args)
    rows = await cursor.fetchall()
    await cursor.close()
    return rows


class PrimaryReadModel:
    def __init__(
        self,
        db_path: str | Path,
        *,
        subject: str,
        settled_run_reader=None,
        suppression_resolver=None,
        run_binding_reader=None,
    ):
        self.path, self.subject = Path(db_path), subject
        self.reader, self.policy = settled_run_reader, suppression_resolver
        self.run_binding_reader = run_binding_reader

    async def _visible(self, evidence_id=None):
        if self.policy is None:
            raise PrimaryReadError("primary_read_policy_unavailable")
        try:
            value = await _resolved(
                self.policy(
                    SuppressionCandidate(self.subject, evidence_id=evidence_id),
                    OrdinaryMemoryPurpose.READ,
                )
            )
            if type(value) is not SuppressionResolution:
                raise TypeError("typed suppression resolution required")
            return not value.denied
        except Exception as exc:
            raise PrimaryReadError("primary_read_policy_unavailable") from exc

    async def _subject_visible(self):
        if not await self._visible():
            raise PrimaryReadError("primary_read_suppressed")

    @asynccontextmanager
    async def _snapshot(self, primary_ref=None):
        if primary_ref is not None:
            identifier(primary_ref, "primary_ref", 512)
        async with aiosqlite.connect(
            f"{self.path.resolve().as_uri()}?mode=ro", uri=True
        ) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN")
            rows = await _rows(
                db,
                (
                    "SELECT primary_conversation_id FROM "
                    "human_memory_primary_conversations WHERE subject=? "
                    "AND writable=1"
                ),
                (self.subject,),
            )
            if len(rows) != 1 or (
                primary_ref is not None and rows[0][0] != primary_ref
            ):
                raise PrimaryReadError("primary_not_found")
            await self._subject_visible()
            yield db, rows[0][0]
            # Re-evaluate current policy after any asynchronous reader work.
            await self._subject_visible()

    async def _revision(self, db, primary):
        # Global append-only table tails avoid subject-filtered scans where the
        # schema has no subject/rowid index. Other owners can conservatively
        # invalidate cursors; no other owner's content enters the read model.
        facts = []
        for table in (
            "foreground_turns",
            "foreground_turn_transitions",
            "foreground_run_transitions",
            "foreground_control_intents",
        ):
            rows = await _rows(db, f"SELECT COALESCE(MAX(rowid),0) FROM {table}")
            facts.append(list(rows[0]))
        heads = await _rows(
            db,
            (
                "SELECT "
                "host_run_id,generation,current_state,sdk_run_id,"
                "last_transition_hash FROM foreground_run_heads WHERE subject=? "
                "AND primary_conversation_id=? AND current_state NOT IN "
                "('COMPLETED','FAILED','STOPPED','CANCELLED') LIMIT 2"
            ),
            (self.subject, primary),
        )
        return canonical_hash(
            {"primary": primary, "facts": facts, "heads": [list(r) for r in heads]}
        )

    async def state(self):
        async with self._snapshot() as (db, primary):
            active = await _rows(
                db,
                (
                    "SELECT "
                    "h.host_run_id,h.generation,h.current_state,"
                    "h.sdk_run_id,t.evidence_id "
                    "FROM foreground_run_heads h JOIN foreground_turns t "
                    "ON t.turn_id=h.turn_id AND t.subject=h.subject "
                    "AND t.primary_conversation_id=h.primary_conversation_id "
                    "WHERE h.subject=? AND h.primary_conversation_id=? "
                    "AND h.current_state NOT IN "
                    "('COMPLETED','FAILED','STOPPED','CANCELLED') LIMIT 2"
                ),
                (self.subject, primary),
            )
            if len(active) > 1:
                raise PrimaryReadError("primary_state_corrupt")
            queued = await _rows(
                db,
                (
                    "SELECT t.evidence_id FROM foreground_turn_heads h "
                    "INDEXED BY idx_foreground_turn_heads_pending CROSS "
                    "JOIN foreground_turns t ON t.turn_id=h.turn_id AND "
                    "t.subject=h.subject WHERE h.subject=? AND "
                    "h.current_state='QUEUED' AND "
                    "t.primary_conversation_id=? ORDER BY h.turn_id "
                    "LIMIT 101"
                ),
                (self.subject, primary),
            )
            current = None
            if active and await self._visible(active[0]["evidence_id"]):
                head = active[0]
                session = None
                if head["sdk_run_id"] is not None:
                    bindings = await _rows(
                        db,
                        (
                            "SELECT * FROM foreground_run_sdk_bindings "
                            "WHERE host_run_id=? AND sdk_run_id=?"
                        ),
                        (head["host_run_id"], head["sdk_run_id"]),
                    )
                    if len(bindings) != 1:
                        raise PrimaryReadError("primary_runtime_binding_mismatch")
                    binding = bindings[0]
                    wire = json.loads(binding["binding_json"])
                    if canonical_hash(wire) != binding["binding_hash"] or any(
                        wire.get(k) != head[k] for k in ("host_run_id", "sdk_run_id")
                    ):
                        raise PrimaryReadError("primary_runtime_binding_mismatch")
                    if self.run_binding_reader is None:
                        raise PrimaryReadError("primary_runtime_binding_unavailable")
                    from deskpet.sdk_adapters.run_bindings import SdkRunBindingV1

                    resolved = await _resolved(
                        self.run_binding_reader(head["sdk_run_id"])
                    )
                    if isinstance(resolved, Mapping):
                        resolved = SdkRunBindingV1.from_record(resolved)
                    if (
                        type(resolved) is not SdkRunBindingV1
                        or resolved.run_id != head["sdk_run_id"]
                    ):
                        raise PrimaryReadError("primary_runtime_binding_mismatch")
                    session = resolved.session_id
                current = {
                    "run_ref": head["host_run_id"],
                    "generation": head["generation"],
                    "state": head["current_state"],
                    "sdk_run_ref": head["sdk_run_id"],
                    "execution_session_ref": session,
                }
            revision = await self._revision(db, primary)
            count = 0
            for row in queued[:100]:
                count += int(await self._visible(row[0]))
            if current is not None and not await self._visible(
                active[0]["evidence_id"]
            ):
                current = None
            return {
                "primary_ref": primary,
                "revision": revision,
                "current_run": current,
                "queued_count": count,
                "queued_count_truncated": len(queued) > 100,
            }

    async def _evidence(self, db, primary, evidence_id):
        rows = await _rows(
            db,
            (
                "SELECT e.*,s.receipt_json,s.receipt_sha256 FROM "
                "human_memory_evidence e JOIN "
                "human_memory_sanitization_receipts s ON "
                "s.receipt_id=e.receipt_id AND "
                "s.evidence_id=e.evidence_id AND s.subject=e.subject "
                "WHERE e.subject=? AND e.primary_conversation_id=? AND "
                "e.evidence_id=?"
            ),
            (self.subject, primary, evidence_id),
        )
        if len(rows) != 1:
            raise PrimaryReadError("primary_source_missing")
        row = rows[0]
        try:
            envelope = SanitizedEvidenceEnvelope.from_json(
                json.loads(row["envelope_json"])
            )
            receipt = SanitizedEvidenceReceipt.from_json(
                json.loads(row["receipt_json"])
            )
            receipt.verify(envelope)
            if (
                not receipt.accepted
                or envelope.subject != self.subject
                or envelope.evidence_id != evidence_id
                or envelope.run_id != row["run_id"]
                or envelope.envelope_hash != row["envelope_sha256"]
                or receipt.receipt_hash != row["receipt_sha256"]
                or canonical_json(envelope.to_json()["sanitized_payload"])
                != canonical_json(json.loads(row["payload_json"]))
            ):
                raise ValueError()
        except (ValueError, TypeError, KeyError) as exc:
            raise PrimaryReadError("primary_source_corrupt") from exc
        return envelope

    @staticmethod
    def _public_message(raw):
        if not isinstance(raw, Mapping) or raw.get("role") not in {
            "assistant",
            "tool",
            "artifact",
        }:
            return None
        content = raw.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, (list, tuple)):
            parts = []
            for block in content:
                if not isinstance(block, Mapping):
                    continue
                data = block.get("data", block)
                if not isinstance(data, Mapping):
                    continue
                if block.get("type") in {
                    "text",
                    "input_text",
                    "output_text",
                } and isinstance(data.get("text"), str):
                    parts.append(data["text"])
                elif block.get("type") == "artifact":
                    artifact = {
                        k: data[k]
                        for k in ("artifact_ref", "name", "mime_type", "uri")
                        if isinstance(data.get(k), str)
                    }
                    if artifact:
                        parts.append(canonical_json(artifact))
            text = "\n".join(parts)
        else:
            text = ""
        if raw["role"] == "tool":
            public = {
                k: raw[k] for k in ("name", "call_id") if isinstance(raw.get(k), str)
            }
            if public:
                text = canonical_json(public) + "\n" + text
        return raw["role"], redact_credential_shapes(text)[0]

    async def _messages(self, db, primary, turn):
        if not await self._visible(turn["evidence_id"]):
            return []
        envelope = await self._evidence(db, primary, turn["evidence_id"])
        wire = json.loads(turn["turn_json"])
        if (
            canonical_hash(wire) != turn["turn_hash"]
            or wire.get("subject") != self.subject
            or wire.get("primary_conversation_id") != primary
            or wire.get("evidence_id") != envelope.evidence_id
            or wire.get("evidence_hash") != envelope.envelope_hash
            or turn["evidence_hash"] != envelope.envelope_hash
            or wire.get("payload") != dict(envelope.sanitized_payload)
            or envelope.source_kind.value != "user_message"
            or not isinstance(wire.get("payload", {}).get("text"), str)
        ):
            raise PrimaryReadError("primary_turn_corrupt")
        text = wire["payload"]["text"]
        source = envelope.envelope_hash
        messages = [
            (
                0,
                "user",
                redact_credential_shapes(text)[0],
                source,
                (turn["evidence_id"],),
            )
        ]
        if turn["turn_state"] != "SETTLED":
            return messages
        rows = await _rows(
            db,
            (
                "SELECT "
                "r.host_run_id,b.sdk_run_id,b.binding_hash,b.binding_json,f.* "
                "FROM foreground_runs r JOIN foreground_run_sdk_bindings b ON "
                "b.host_run_id=r.host_run_id JOIN foreground_terminal_receipts f "
                "ON f.host_run_id=r.host_run_id AND f.sdk_run_id=b.sdk_run_id "
                "WHERE r.host_run_id=? AND r.turn_id=? AND r.subject=? AND "
                "r.primary_conversation_id=?"
            ),
            (turn["host_run_id"], turn["turn_id"], self.subject, primary),
        )
        if len(rows) != 1:
            raise PrimaryReadError("primary_terminal_missing")
        run = rows[0]
        binding, terminal = (
            json.loads(run["binding_json"]),
            json.loads(run["receipt_json"]),
        )
        if (
            canonical_hash(binding) != run["binding_hash"]
            or any(binding.get(k) != run[k] for k in ("host_run_id", "sdk_run_id"))
            or canonical_hash(terminal) != run["receipt_hash"]
            or any(
                terminal.get(k) != run[k]
                for k in (
                    "host_run_id",
                    "sdk_run_id",
                    "terminal_state",
                    "generation",
                    "sdk_event_id",
                    "sdk_event_hash",
                )
            )
        ):
            raise PrimaryReadError("primary_terminal_corrupt")
        # S6 observation evidence is also a suppressible source. Pre-S6 runs
        # have no observation; their input evidence still gates the transcript.
        observations = await _rows(
            db,
            (
                "SELECT evidence_id FROM human_memory_evidence WHERE "
                "evidence_id=? AND subject=? AND "
                "primary_conversation_id=? AND run_id=? AND source_ref=? "
                "AND source_kind='runtime_event'"
            ),
            (
                str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL, "primary-runtime:" + run["sdk_run_id"]
                    )
                ),
                self.subject,
                primary,
                run["sdk_run_id"],
                "primary-runtime:" + run["sdk_run_id"],
            ),
        )
        for row in observations:
            if not await self._visible(row[0]):
                return messages
            observed = await self._evidence(db, primary, row[0])
            body = observed.sanitized_payload
            if (
                any(
                    body.get(k) != run[k]
                    for k in ("host_run_id", "sdk_run_id", "terminal_state")
                )
                or body.get("turn_id") != turn["turn_id"]
            ):
                raise PrimaryReadError("primary_terminal_corrupt")
        from deskpet.execution.terminal_identity import (
            read_primary_terminal_identity_tx,
        )

        try:
            identity = await read_primary_terminal_identity_tx(
                db,
                subject=self.subject,
                primary_ref=primary,
                host_run_id=run["host_run_id"],
                sdk_run_id=run["sdk_run_id"],
            )
        except RuntimeError as exc:
            raise PrimaryReadError(str(exc)) from exc
        if identity is None:
            raise PrimaryReadError("primary_terminal_missing")
        if self.reader is None:
            raise PrimaryReadError("primary_history_reader_unavailable")
        evidence, transcript = await _resolved(
            self.reader(run["sdk_run_id"], current_text=text)
        )
        try:
            identity.verify_sdk_terminal(evidence)
        except RuntimeError as exc:
            raise PrimaryReadError("primary_history_terminal_mismatch") from exc
        if (
            not isinstance(transcript, (list, tuple))
            or not transcript
            or not isinstance(transcript[0], Mapping)
            or transcript[0].get("role") != "user"
            or transcript[0].get("content") != text
        ):
            raise PrimaryReadError("primary_history_anchor_mismatch")
        for index, raw in enumerate(transcript[1:], start=1):
            public = self._public_message(raw)
            if public is not None:
                messages.append(
                    (
                        index,
                        *public,
                        run["receipt_hash"],
                        (turn["evidence_id"], *(row[0] for row in observations)),
                    )
                )
        # Never reuse a pre-await policy decision to disclose a new transcript.
        if not await self._visible(turn["evidence_id"]):
            return []
        for row in observations:
            if not await self._visible(row[0]):
                return messages[:1]
        return messages

    async def _turns(self, db, primary, *, before, limit, turn_ref=None):
        select = (
            "SELECT t.*,h.current_state AS turn_state,h.host_run_id FROM "
            "foreground_turns t JOIN foreground_turn_heads h ON "
            "h.turn_id=t.turn_id AND h.subject=t.subject "
        )
        if turn_ref is not None:
            # Detail uses the turn primary key, not a nullable OR over history.
            identifier(turn_ref, "turn_ref", 512)
            return await _rows(
                db,
                select
                + (
                    "WHERE t.turn_id=? AND t.subject=? AND "
                    "t.primary_conversation_id=? LIMIT 1"
                ),
                (turn_ref, self.subject, primary),
            )
        return await _rows(
            db,
            select
            + (
                "WHERE t.subject=? AND t.primary_conversation_id=? AND "
                "t.enqueue_sequence<=? ORDER BY t.enqueue_sequence DESC "
                "LIMIT ?"
            ),
            (self.subject, primary, before, limit),
        )

    def _item(self, primary, turn, message):
        index, role, text, source, _policy_sources = message
        ref = _token(
            {
                "v": 1,
                "p": primary,
                "t": turn["turn_id"],
                "i": index,
                "h": canonical_hash({"role": role, "text": text, "source": source}),
            }
        )
        return {
            "message_ref": ref,
            "turn_ref": turn["turn_id"],
            "run_ref": turn["host_run_id"],
            "delivery_key": turn["idempotency_key"],
            "role": role,
            "text": text[:1024],
            "total_chars": len(text),
            "has_more": len(text) > 1024,
        }

    async def page(self, *, primary_ref, cursor=None, limit=20):
        bounded_int(limit, minimum=1, maximum=50)
        async with self._snapshot(primary_ref) as (db, primary):
            revision = await self._revision(db, primary)
            before = (2**63 - 1, 2**63 - 1)
            if cursor is not None:
                decoded = _decode(cursor, ("v", "p", "r", "s", "i"))
                if decoded["p"] != primary:
                    raise PrimaryReadError("primary_read_ref_invalid")
                if decoded["r"] != revision:
                    raise PrimaryReadError("primary_cursor_stale")
                before = (
                    bounded_int(decoded["s"], minimum=1, maximum=2**63 - 1),
                    bounded_int(decoded["i"], minimum=0, maximum=2**63 - 1),
                )
            turns = await self._turns(db, primary, before=before[0], limit=10)
            selected, next_key = [], None
            for turn in turns:
                for message in reversed(await self._messages(db, primary, turn)):
                    key = (turn["enqueue_sequence"], message[0])
                    if key >= before:
                        continue
                    selected.append((self._item(primary, turn, message), message[4]))
                    if len(selected) == limit:
                        next_key = key
                        break
                if next_key is not None:
                    break
            if next_key is None and len(turns) == 10:
                next_key = (turns[-1]["enqueue_sequence"], 0)
            next_cursor = (
                None
                if next_key is None
                else _token(
                    {
                        "v": 1,
                        "p": primary,
                        "r": revision,
                        "s": next_key[0],
                        "i": next_key[1],
                    }
                )
            )
            # Reader awaits for older turns must not preserve an earlier
            # visibility decision for already-selected newer source text.
            visible = {}
            for _, sources in selected:
                for source in sources:
                    if source not in visible:
                        visible[source] = await self._visible(source)
            return {
                "primary_ref": primary,
                "revision": revision,
                "items": [
                    item
                    for item, sources in reversed(selected)
                    if all(visible[source] for source in sources)
                ],
                "next_cursor": next_cursor,
            }

    async def detail(self, *, primary_ref, message_ref, offset=0, limit=4096):
        bounded_int(offset, minimum=0, maximum=2**63 - 1)
        bounded_int(limit, minimum=1, maximum=4096)
        decoded = _decode(message_ref, ("v", "p", "t", "i", "h"))
        bounded_int(decoded["i"], minimum=0, maximum=2**63 - 1)
        async with self._snapshot(primary_ref) as (db, primary):
            if decoded["p"] != primary:
                raise PrimaryReadError("primary_read_ref_invalid")
            turns = await self._turns(
                db, primary, before=2**63 - 1, limit=1, turn_ref=decoded["t"]
            )
            if not turns:
                raise PrimaryReadError("primary_message_unavailable")
            turn = turns[0]
            for message in await self._messages(db, primary, turn):
                if message[0] != decoded["i"]:
                    continue
                if self._item(primary, turn, message)["message_ref"] != message_ref:
                    raise PrimaryReadError("primary_read_ref_invalid")
                text = message[2]
                if offset > len(text):
                    raise PrimaryReadError("primary_read_request_invalid")
                end = min(len(text), offset + limit)
                for source in message[4]:
                    if not await self._visible(source):
                        raise PrimaryReadError("primary_message_unavailable")
                return {
                    "message_ref": message_ref,
                    "text": text[offset:end],
                    "offset": offset,
                    "next_offset": end if end < len(text) else None,
                    "total_chars": len(text),
                }
            raise PrimaryReadError("primary_message_unavailable")
