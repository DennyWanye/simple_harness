"""Durable, run-bound primary turn observations; no legacy Session selectors.

The existing append-only evidence store holds the public transcript. Visibility
requires the foreground terminal receipt: an observer crash cannot publish a
half-settled turn. No new schema or second ingestion queue is introduced.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from pathlib import Path

import aiosqlite
from simple_harness import (
    DeliveryRecipient, DisclosureContext, DisclosureGeneration, DisclosurePurpose,
    DisclosureReasonCode, DisclosureSource, DisclosureTrust, EvidenceReasonCode,
    EvidenceSourceKind, IntendedAudience, SanitizedEvidenceEnvelope, SanitizedEvidenceReceipt,
)
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.writer_fence import assert_human_memory_ingress_open_tx
from deskpet.task_scope.protocol import (
    canonical_hash, canonical_json, identifier, reject_private_payload,
)

POLICY = "host-primary-runtime-v1"

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 2026-09-08 HM-TO-A6 根因侧：终态观察不得写出 Memory 之后一定会拒绝的 envelope
#
# 事故里工具密集的一轮把整条 Run 的 12 条 tool_result（每条 8–9 KB）内联进
# 终态 S1，canonical payload 74196 字节，越过 SDK 的 64 KiB 内联上限
# （`MAX_INLINE_EVIDENCE_BYTES`）。Host 收下了它，Memory 之后每一次读都拒绝，
# 整条主对话不可读、前台驱动永死。
#
# 取舍（记于 plans/2026-09-08-hm-to-a6/DECISION-PRIMARY-VISIBILITY-STALL.md）：
#   · 不「写入时报错拒绝落库」——长回合的 Run 会永远无法结算，比现在更糟；
#   · 不改 envelope 形状（新增顶层字段或改走 controlled blob ref）——
#     `terminal_observation_tx` / `primary_message_v2|v3.pairs` /
#     `conversation_registration` / `primary_context_pages` 都按现有形状逐字节
#     复算，属于需要与 Memory SDK 协同的契约变更（F-A6-1）；
#   · 选择：**确定性降级**——把最大的 tool_result 正文替换成内容寻址的省略标记
#     （`sha256` + 原字节数），直到整条 payload 落回上限内。标记本身可校验：
#     拿到原始 transcript 就能重算出同一个标记（见 `transcript_matches`）。
#     角色/键集/call_id/ordinal 全部保留，因此 `representable()`、逐条消息 S1、
#     短期索引车道照旧成立；messages[0]（USER 原文）永不改动。
# --------------------------------------------------------------------------

ELISION_PREFIX = "[deskpet:elided-tool-result "


def elided_content(content: str) -> str:
    """The deterministic, verifiable stand-in for one oversized message body."""
    raw = content.encode("utf-8")
    return f"{ELISION_PREFIX}sha256={hashlib.sha256(raw).hexdigest()} bytes={len(raw)}]"


def transcript_matches(settled, stored) -> bool:
    """Stored transcript equals the settled SDK one, allowing recorded elisions.

    Readers that re-verify the archived transcript against the live SDK run
    must accept an elided body — and only an elided body whose marker actually
    hashes back to what the SDK returned.
    """

    settled, stored = list(settled), list(stored)
    if len(settled) != len(stored):
        return False
    for actual, kept in zip(settled, stored):
        if actual == kept:
            continue
        if not isinstance(actual, dict) or not isinstance(kept, dict):
            return False
        if set(actual) != set(kept) or any(
            actual[key] != kept[key] for key in actual if key != "content"
        ):
            return False
        body = actual.get("content")
        if not isinstance(body, str) or kept.get("content") != elided_content(body):
            return False
    return True


def bound_terminal_payload(payload: dict):
    """Shrink the transcript **only** when the real payload is over the ceiling.

    The budget is exact, not a reserve: ``canonical_json`` is key-sorted with
    fixed separators, so the payload's size is the size with an empty
    ``messages`` array plus the size of the array itself.  Anything that fits
    is returned byte-for-byte unchanged — a large tool result that Memory
    would have admitted must keep its body, because `context_page_in` pages
    into exactly those bytes.

    Sizing must never be what stops a Run from settling: an unsizeable payload
    falls back to the original behaviour (write it, let the write-side SDK
    validation report it) rather than raising from a new place in the
    terminal transaction.
    """

    from deskpet.memory.primary_visibility import inline_evidence_limit

    limit = inline_evidence_limit()
    messages = payload.get("messages")
    if limit is None or not isinstance(messages, list):
        return payload, ()
    try:
        if len(canonical_json(payload).encode("utf-8")) <= limit:
            return payload, ()
        empty = len(canonical_json({**payload, "messages": []}).encode("utf-8"))
    except Exception:  # noqa: BLE001 - the real payload hash below still raises
        return payload, ()
    # `- empty` removes everything but the array; `+ 2` gives back the "[]" the
    # empty rendering still contained.
    budget = limit - empty + 2
    bounded, elided = bound_terminal_messages(messages, budget)
    if not elided or len(canonical_json(bounded).encode("utf-8")) > budget:
        # Either it already fits, or the rest of the payload is what overflows
        # and giving up transcript bodies buys nothing. Keep every byte and let
        # the write-side validation report it.
        return payload, ()
    return {**payload, "messages": bounded}, elided


def bound_terminal_messages(messages, budget: int | None):
    """Deterministically shrink a transcript that Memory could never admit.

    Pure in ``messages`` and ``budget``, so a replayed observation reduces to
    exactly the same bytes and its idempotency comparison still holds.  Tool
    results go first (largest, then lowest ordinal); assistant bodies only if
    the tool results alone are not enough.  ``messages[0]`` — the USER text
    other readers bind against — is never touched.
    """

    original = list(messages)
    if budget is None:
        return original, ()
    items = [dict(message) for message in original]

    def size() -> int:
        return len(canonical_json(items).encode("utf-8"))

    if size() <= budget:
        return original, ()
    elided: list[int] = []
    for roles in (("tool",), ("assistant",)):
        candidates = sorted(
            (
                index
                for index, item in enumerate(items)
                if index
                and item.get("role") in roles
                and isinstance(item.get("content"), str)
                and item["content"]
                and not item["content"].startswith(ELISION_PREFIX)
            ),
            key=lambda index: (-len(items[index]["content"].encode("utf-8")), index),
        )
        for index in candidates:
            if size() <= budget:
                break
            items[index] = {**items[index], "content": elided_content(items[index]["content"])}
            elided.append(index + 1)
        if size() <= budget:
            break
    return items, tuple(sorted(elided))


def _warn_if_unadmissible(sdk_run_id: str, envelope, receipt) -> None:
    """Validate with the SDK's own admission rule before the row is written.

    Last-resort report for an observation even the degrade could not bring
    back (the transcript is not the only thing that can overflow — a very large
    ``tool_scope_sources`` can, too).  The bounded observation is written
    regardless: Host S1 stays the archive of record and refusing to commit
    would leave the Run unsettled forever, which is strictly worse.  The
    read-side guard keeps such a source from poisoning anyone else's batch.
    Identifiers, a stable reason and byte counts only — never transcript
    content.
    """

    from deskpet.memory.primary_visibility import (
        SOURCE_UNADMISSIBLE,
        PrimaryVisibilityError,
        assert_source_admissible,
    )

    try:
        assert_source_admissible(envelope, receipt)
        return
    except PrimaryVisibilityError as exc:
        if exc.code != SOURCE_UNADMISSIBLE:
            return  # no SDK on this build; every read already fails loudly
        reason = exc.cause_detail or exc.cause_type or exc.code
    except (TypeError, ValueError) as exc:  # diagnostics must never fail a commit
        reason = type(exc).__name__
    payload = envelope.to_json()["sanitized_payload"]
    log.warning(
        "primary_terminal_observation_unadmissible sdk_run_id=%s reason=%s payload_bytes=%d "
        "message_count=%d",
        sdk_run_id, reason, len(canonical_json(payload).encode("utf-8")),
        len(payload.get("messages") or ()),
    )


def observation_id(sdk_run_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"primary-runtime:{sdk_run_id}"))


def evidence_pair(subject: str, sdk_run_id: str, payload: dict, occurred_at: float):
    digest = canonical_hash(payload)
    disclosure = DisclosureContext(
        sdk_run_id, subject, DeliveryRecipient.USER_SELF, subject, IntendedAudience.USER_SELF,
        DisclosurePurpose.TASK_EXECUTION, DisclosureSource.AUTHENTICATED_HOST,
        DisclosureTrust.TRUSTED_AUTHORITY, DisclosureGeneration.CURRENT,
        "host:primary-runtime-observer:v1", (DisclosureReasonCode.MINIMUM_NECESSARY,),
    )
    evidence_id = observation_id(sdk_run_id)
    envelope = SanitizedEvidenceEnvelope(
        evidence_id=evidence_id, run_id=sdk_run_id, subject=subject,
        source_kind=EvidenceSourceKind.RUNTIME_EVENT, source_ref=f"primary-runtime:{sdk_run_id}",
        source_hash=digest, sanitized_payload=payload, sanitized_hash=digest,
        filter_policy_version=POLICY, removed_spans=(), disclosure_context=disclosure, evidence_refs=(),
    )
    receipt = SanitizedEvidenceReceipt(
        receipt_id=f"primary-runtime-receipt:{evidence_id}", run_id=sdk_run_id, subject=subject,
        evidence_id=evidence_id, envelope_hash=envelope.envelope_hash, source_hash=digest,
        sanitized_hash=digest, filter_policy_version=POLICY, accepted=True,
        reason_codes=(EvidenceReasonCode.SANITIZED_AND_ACCEPTED,), disclosure_context=disclosure,
        evidence_refs=(), admitted_at=occurred_at,
    )
    return envelope, receipt


async def terminal_observation_tx(db, *, host_run_id, sdk_run_id, subject):
    cursor = await db.execute(
        "SELECT e.* FROM human_memory_evidence e "
        "JOIN foreground_runs r ON r.host_run_id=? "
        "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id AND b.sdk_run_id=e.run_id "
        "WHERE e.evidence_id=? AND e.run_id=? AND e.subject=? "
        "AND e.primary_conversation_id=r.primary_conversation_id AND r.subject=e.subject "
        "AND e.source_kind='runtime_event' AND e.source_ref=?",
        (host_run_id, observation_id(sdk_run_id), sdk_run_id, subject, f"primary-runtime:{sdk_run_id}"),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        return None
    payload = json.loads(row["payload_json"])
    envelope = json.loads(row["envelope_json"])
    expected, _ = evidence_pair(subject, sdk_run_id, payload, float(row["occurred_at"]))
    if (envelope != dict(expected.to_json())
            or canonical_hash(payload) != row["sanitized_sha256"]
            or expected.envelope_hash != row["envelope_sha256"]
            or payload.get("host_run_id") != host_run_id
            or payload.get("sdk_run_id") != sdk_run_id):
        raise RuntimeError("primary_runtime_observation_corrupt")
    return row, payload


async def record_terminal_observation(db_path, *, host_run_id, sdk_run_id, subject,
                                      owner_id, generation, terminal, sdk_evidence, messages, error_code=None,
                                      visibility_dependencies=None, tool_causal_sources=None, occurrence_sources=None):
    from deskpet.execution.foreground_queue import ForegroundQueueStore
    queue = ForegroundQueueStore(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("BEGIN IMMEDIATE")
        await assert_human_memory_ingress_open_tx(db)
        await queue._validate_lease_tx(db, host_run_id, owner_id, generation, time.time())
        await queue._validate_sdk_binding_tx(db, host_run_id, sdk_run_id)
        prior = await terminal_observation_tx(db, host_run_id=host_run_id, sdk_run_id=sdk_run_id, subject=subject)
        if prior is not None:
            row, payload = prior
            # 2026-09-08 HM-TO-A6：既有观察的 transcript 可能被降级过（见
            # bound_terminal_payload），所以这里不能再要求逐字节相等——但也只放行
            # 「该条正文的 sha256 标记」这一种差异，标记可复算，其余键必须一字不差。
            if (payload["sdk_event_id"] != sdk_evidence.event_id
                    or payload["sdk_event_hash"] != sdk_evidence.event_hash
                    or payload["terminal_state"] != terminal.value
                    or not transcript_matches(messages, payload["messages"])):
                raise RuntimeError("primary_runtime_terminal_conflict")
            marker = payload.get("message_source_contract")
            if marker is not None:
                if marker not in {"primary-message-v1", "primary-message-v2", "primary-message-v3"}:
                    raise RuntimeError("primary_message_contract_unknown")
                from deskpet.memory.primary_message_evidence import verify_new_primary_message_evidence_tx
                prior_envelope, prior_receipt = evidence_pair(subject, sdk_run_id, payload, float(row["occurred_at"]))
                await verify_new_primary_message_evidence_tx(db, host_run_id=host_run_id,
                    terminal_envelope=prior_envelope, terminal_receipt=prior_receipt)
            await db.commit()
            return row["evidence_id"], row["envelope_sha256"]
        run = await queue._run_tx(db, host_run_id)
        if run["subject"] != subject:
            raise RuntimeError("primary_runtime_subject_mismatch")
        payload = {
            "schema_version": 1, "kind": "primary_run_terminal", "subject": subject,
            "host_run_id": host_run_id, "sdk_run_id": sdk_run_id, "turn_id": run["turn_id"],
            "generation": generation, "terminal_state": terminal.value,
            "sdk_event_id": sdk_evidence.event_id, "sdk_event_hash": sdk_evidence.event_hash,
            "messages": list(messages), "error_code": error_code,
            "visibility_dependencies": visibility_dependencies,
            "message_source_contract": "primary-message-v1",
        }
        if occurrence_sources is not None:
            payload['prospective_source_dependencies'] = occurrence_sources
        from deskpet.memory.primary_message_v2 import representable
        if terminal.value == "COMPLETED" and representable(messages, tool_causal_sources):
            payload.update(message_source_contract="primary-message-v2",
                           tool_causal_sources=list(tool_causal_sources))
            cursor = await db.execute("PRAGMA user_version")
            if (await cursor.fetchone())[0] in (53, 54):
                from deskpet.memory.primary_message_v3 import CONTRACT, read_scope_sources_tx
                scopes = await read_scope_sources_tx(db, subject=subject, sdk_run_id=sdk_run_id, facts=tool_causal_sources)
                payload.update(message_source_contract=CONTRACT, tool_scope_sources=scopes)
        # 2026-09-08 HM-TO-A6：payload 组齐（含 tool_causal_sources /
        # tool_scope_sources / visibility_dependencies）之后再按**真实**字节数判定，
        # 只有确实越过 Memory 内联上限时才降级 transcript。
        payload, elided = bound_terminal_payload(payload)
        if elided:
            log.warning(
                "primary_terminal_observation_degraded sdk_run_id=%s elided_ordinals=%s "
                "message_count=%d",
                sdk_run_id, list(elided), len(payload["messages"]),
            )
        reject_private_payload(payload)
        envelope, receipt = evidence_pair(subject, sdk_run_id, payload, float(sdk_evidence.occurred_at))
        _warn_if_unadmissible(sdk_run_id, envelope, receipt)
        committed = await HumanMemoryProgramStore(db_path).append_evidence_tx(
            db, envelope, receipt, primary_conversation_id=run["primary_conversation_id"], committed_at=time.time(),
        )
        # New observations only: per-message S1 shares this transaction. The
        # existing-observation branch above deliberately never backfills it.
        from deskpet.memory.primary_message_evidence import append_new_primary_message_evidence_tx
        await append_new_primary_message_evidence_tx(
            db, store=HumanMemoryProgramStore(db_path), host_run_id=host_run_id,
            terminal_envelope=envelope, terminal_receipt=receipt,
        )
        await db.commit()
        return committed.evidence_id, committed.envelope_sha256


class PrimaryHistoryStore:
    def __init__(self, db_path: str | Path, *, settled_run_reader=None, policy=None):
        self._db_path = db_path
        self._settled_run_reader = settled_run_reader
        self._policy = policy

    async def read(self, *, subject: str, primary_ref: str, before_sequence: int,
                   limit: int = 10, completed_only: bool = False, disclosure_context=None) -> tuple[dict, ...]:
        identifier(subject, "subject")
        identifier(primary_ref, "primary_ref")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("primary_history_limit_invalid")
        if type(before_sequence) is not int or before_sequence < 1:
            raise ValueError("primary_history_sequence_invalid")
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT r.host_run_id,r.task_scope_id,b.sdk_run_id,t.enqueue_sequence,t.turn_id,t.turn_json,t.evidence_id,t.evidence_hash,"
                "f.terminal_receipt_id,f.receipt_hash,f.receipt_json,f.terminal_state "
                "FROM foreground_runs r JOIN foreground_turns t ON t.turn_id=r.turn_id "
                "JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id "
                "JOIN foreground_terminal_receipts f ON f.host_run_id=r.host_run_id AND f.sdk_run_id=b.sdk_run_id "
                "WHERE r.subject=? AND r.primary_conversation_id=? AND t.enqueue_sequence<? "
                "AND (?=0 OR f.terminal_state='COMPLETED') "
                "ORDER BY t.enqueue_sequence DESC LIMIT ?",
                (subject, primary_ref, before_sequence, int(completed_only), limit),
            )
            rows = await cursor.fetchall()
            await cursor.close()
            result = []
            for run in reversed(rows):
                from deskpet.execution.terminal_identity import read_primary_terminal_identity_tx
                identity = await read_primary_terminal_identity_tx(db, subject=subject, primary_ref=primary_ref,
                    host_run_id=run["host_run_id"], sdk_run_id=run["sdk_run_id"])
                if identity is None:
                    raise RuntimeError("primary_history_terminal_receipt_corrupt")
                receipt = json.loads(run["receipt_json"])
                if canonical_hash(receipt) != run["receipt_hash"]:
                    raise RuntimeError("primary_history_terminal_receipt_corrupt")
                found = await terminal_observation_tx(db, host_run_id=run["host_run_id"],
                                                      sdk_run_id=run["sdk_run_id"], subject=subject)
                if found is None:
                    if (receipt.get("primary_observation_ref") is not None
                            or receipt.get("terminal_authority_kind") == "primary_runtime_observation"
                            or run["task_scope_id"] is None
                            or not receipt.get("terminal_gate_receipt_id")):
                        raise RuntimeError("primary_history_observation_corrupt")
                    # Pre-S6 scoped Runs have no primary observation. Re-read
                    # their actual SDK terminal + Context, anchored by the
                    # already committed Host binding and terminal receipt.
                    if self._settled_run_reader is None:
                        raise RuntimeError("primary_history_observation_missing")
                    if canonical_hash(json.loads(run["receipt_json"])) != run["receipt_hash"]:
                        raise RuntimeError("primary_history_terminal_receipt_corrupt")
                    terminal, messages = self._settled_run_reader(
                        run["sdk_run_id"], current_text=json.loads(run["turn_json"])["payload"]["text"],
                    )
                    identity.verify_sdk_terminal(terminal)
                    result.append({
                        "source_ref": f"primary-terminal:{run['terminal_receipt_id']}",
                        "source_hash": canonical_hash({"host_receipt_hash": run["receipt_hash"],
                                                       "sdk_event_hash": terminal.event_hash, "messages": messages}),
                        "turn_id": run["turn_id"], "terminal_state": run["terminal_state"],
                        "messages": list(messages),
                    })
                    continue
                row, payload = found
                if self._settled_run_reader is not None:
                    terminal, messages = self._settled_run_reader(
                        run["sdk_run_id"], current_text=json.loads(run["turn_json"])["payload"]["text"])
                    identity.verify_sdk_terminal(terminal)
                    if list(messages) != payload["messages"]:
                        raise RuntimeError("primary_history_transcript_mismatch")
                if (receipt.get("primary_observation_ref") != row["evidence_id"]
                        or receipt.get("primary_observation_hash") != row["envelope_sha256"]):
                    raise RuntimeError("primary_history_observation_corrupt")
                result.append({"source_ref": row["evidence_id"], "source_hash": row["envelope_sha256"],
                               "turn_id": payload["turn_id"], "terminal_state": payload["terminal_state"],
                               "messages": payload["messages"]})
            if self._policy is None or disclosure_context is None:
                raise RuntimeError("primary_history_policy_unavailable")
            # Policy runs after potentially slow public SDK reads. Check both
            # generated and original USER sources in the same current batch.
            roots = tuple(dict.fromkeys([r["evidence_id"] for r in rows] +
                [g["source_ref"] for g in result if not g["source_ref"].startswith("primary-terminal:")]))
            visible = await self._policy.check_evidence_ids(db=db, primary_ref=primary_ref,
                evidence_ids=roots, disclosure_context=disclosure_context)
            from deskpet.memory.primary_visibility import read_evidence_pair
            filtered = []
            by_turn = {r["turn_id"]: r for r in rows}
            for group in result:
                if visible.get(group["source_ref"], False):
                    filtered.append(group)
                else:
                    original = by_turn[group["turn_id"]]
                    eid = original["evidence_id"]
                    if visible.get(eid, False):
                        envelope, _ = await read_evidence_pair(db=db, subject=subject,
                            primary_ref=primary_ref, evidence_id=eid)
                        if envelope.envelope_hash != original["evidence_hash"]:
                            raise RuntimeError("primary_history_user_hash_mismatch")
                        filtered.append({"source_ref": eid, "source_hash": envelope.envelope_hash,
                            "turn_id": group["turn_id"], "terminal_state": group["terminal_state"],
                            "messages": [{"role": "user", "content": envelope.sanitized_payload["text"]}]})
            return tuple(filtered)
