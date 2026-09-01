# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Product-owned immutable Context snapshot contracts.

The full snapshot is private and may be persisted only in the SDK execution
store.  ``DefaultDenySnapshotRedactor`` produces the bounded projection that
SessionDB and the Inspector may retain.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from simple_harness import freeze_json, thaw_json


class SnapshotContractConflict(RuntimeError):
    code = "sdk_context_snapshot_conflict"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


def canonical_json(value: object) -> str:
    """Return deterministic UTF-8 JSON for hashing and CAS comparisons."""

    return json.dumps(
        thaw_json(freeze_json(value)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


@dataclass(frozen=True, slots=True)
class PreparedSdkContextSnapshotV1:
    snapshot_id: str
    snapshot_version: int
    snapshot_fingerprint: str
    session_id: str
    request_id: str
    root_run_id: str
    sdk_run_id: str
    turn_id: str
    provider_binding: object
    provider_messages: tuple[object, ...]
    catalog: object
    attachments: tuple[object, ...]
    sections: object
    budget: object
    lineage: object
    memory: object | None
    current_message: object
    memory_projection_version: int

    @classmethod
    def build(
        cls,
        *,
        session_id: str,
        request_id: str,
        root_run_id: str,
        sdk_run_id: str,
        turn_id: str,
        provider_binding: Mapping[str, Any],
        provider_messages: Sequence[Mapping[str, Any]],
        catalog: Mapping[str, Any],
        attachments: Sequence[Mapping[str, Any]] = (),
        sections: Mapping[str, Any] | None = None,
        budget: Mapping[str, Any] | None = None,
        snapshot_version: int = 1,
        lineage: Mapping[str, Any] | None = None,
        memory: Mapping[str, Any] | None = None,
        current_message: Mapping[str, Any] | None = None,
        memory_projection_version: int = 2,
    ) -> PreparedSdkContextSnapshotV1:
        if snapshot_version != 1:
            raise ValueError("PreparedSdkContextSnapshotV1 requires version 1")
        identity = {
            "snapshot_version": snapshot_version,
            "session_id": _required_text(session_id, "session_id"),
            "request_id": _required_text(request_id, "request_id"),
            "root_run_id": _required_text(root_run_id, "root_run_id"),
            "sdk_run_id": _required_text(sdk_run_id, "sdk_run_id"),
            "turn_id": _required_text(turn_id, "turn_id"),
            "provider_binding": provider_binding,
            "provider_messages": list(provider_messages),
            "catalog": catalog,
            "attachments": list(attachments),
            "sections": sections or {},
            "budget": budget or {},
            "lineage": lineage or {},
            "memory": memory,
            "current_message": current_message or provider_messages[-1],
            "memory_projection_version": int(memory_projection_version),
        }
        fingerprint = canonical_sha256(identity)
        return cls(
            snapshot_id=f"sdk-context:{fingerprint}",
            snapshot_version=1,
            snapshot_fingerprint=fingerprint,
            session_id=identity["session_id"],
            request_id=identity["request_id"],
            root_run_id=identity["root_run_id"],
            sdk_run_id=identity["sdk_run_id"],
            turn_id=identity["turn_id"],
            provider_binding=freeze_json(provider_binding),
            provider_messages=tuple(freeze_json(item) for item in provider_messages),
            catalog=freeze_json(catalog),
            attachments=tuple(freeze_json(item) for item in attachments),
            sections=freeze_json(sections or {}),
            budget=freeze_json(budget or {}),
            lineage=freeze_json(lineage or {}),
            memory=None if memory is None else freeze_json(memory),
            current_message=freeze_json(current_message or provider_messages[-1]),
            memory_projection_version=int(memory_projection_version),
        )

    @classmethod
    def from_private_record(
        cls, value: Mapping[str, Any]
    ) -> PreparedSdkContextSnapshotV1:
        snapshot = cls.build(
            session_id=str(value["session_id"]),
            request_id=str(value["request_id"]),
            root_run_id=str(value["root_run_id"]),
            sdk_run_id=str(value["sdk_run_id"]),
            turn_id=str(value["turn_id"]),
            provider_binding=value["provider_binding"],
            provider_messages=value["provider_messages"],
            catalog=value["catalog"],
            attachments=value.get("attachments") or (),
            sections=value.get("sections") or {},
            budget=value.get("budget") or {},
            lineage=value.get("lineage") or {},
            memory=value.get("memory"),
            current_message=value.get("current_message"),
            memory_projection_version=int(
                value.get("memory_projection_version") or 2
            ),
        )
        if (
            str(value.get("snapshot_id") or "") != snapshot.snapshot_id
            or str(value.get("snapshot_fingerprint") or "")
            != snapshot.snapshot_fingerprint
        ):
            raise SnapshotContractConflict()
        return snapshot

    def private_record(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "snapshot_version": self.snapshot_version,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "root_run_id": self.root_run_id,
            "sdk_run_id": self.sdk_run_id,
            "turn_id": self.turn_id,
            "provider_binding": thaw_json(self.provider_binding),
            "provider_messages": [thaw_json(item) for item in self.provider_messages],
            "catalog": thaw_json(self.catalog),
            "attachments": [thaw_json(item) for item in self.attachments],
            "sections": thaw_json(self.sections),
            "budget": thaw_json(self.budget),
            "schema_version": 1,
            "lineage": thaw_json(self.lineage),
            "memory": None if self.memory is None else thaw_json(self.memory),
            "current_message": thaw_json(self.current_message),
            "memory_projection_version": self.memory_projection_version,
        }

    def canonical_json(self) -> str:
        return canonical_json(self.private_record())


_SENSITIVE_TEXT = re.compile(
    r"(?i)(authorization\s*:|bearer\s+[a-z0-9._-]+|api[_ -]?key|cookie\s*:|"
    r"(?:^|\s)(?:/Users/|/home/|[A-Za-z]:\\)|reasoning_content|private[_ -]?reasoning)"
)


class DefaultDenySnapshotRedactor:
    """Build a public snapshot from a small explicit allowlist.

    No provider messages, attachment body/path, headers, secret fields or
    reasoning fields are ever traversed into the result.
    """

    _SECTION_FIELDS = frozenset(
        {"kind", "label", "count", "estimated_tokens", "availability", "ref"}
    )

    @staticmethod
    def _bounded_text(value: object, limit: int = 160) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text or _SENSITIVE_TEXT.search(text):
            return None
        return text[:limit]

    def redact(self, snapshot: PreparedSdkContextSnapshotV1) -> dict[str, Any]:
        binding = thaw_json(snapshot.provider_binding)
        catalog = thaw_json(snapshot.catalog)
        sections = thaw_json(snapshot.sections)
        budget = thaw_json(snapshot.budget)
        attachments = [thaw_json(item) for item in snapshot.attachments]

        public_sections: list[dict[str, Any]] = []
        if isinstance(sections, Mapping):
            iterable = (
                {"kind": kind, **(value if isinstance(value, Mapping) else {})}
                for kind, value in sections.items()
            )
        elif isinstance(sections, list):
            iterable = (item for item in sections if isinstance(item, Mapping))
        else:
            iterable = ()
        for raw in iterable:
            item: dict[str, Any] = {}
            for key in self._SECTION_FIELDS:
                value = raw.get(key)
                if key in {"kind", "label", "availability", "ref"}:
                    bounded = self._bounded_text(value)
                    if bounded is not None:
                        item[key] = bounded
                elif isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    item[key] = value
            if item.get("kind"):
                public_sections.append(item)

        public_attachments: list[dict[str, Any]] = []
        for raw in attachments:
            if not isinstance(raw, Mapping):
                continue
            item: dict[str, Any] = {}
            kind = self._bounded_text(raw.get("kind"), 40)
            size = raw.get("size")
            if kind:
                item["kind"] = kind
            if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
                item["size"] = size
            if item:
                public_attachments.append(item)

        public_binding: dict[str, Any] = {}
        if isinstance(binding, Mapping):
            for key in ("provider_id", "model_id", "reasoning_mode", "reasoning_effort"):
                value = self._bounded_text(binding.get(key))
                if value is not None:
                    public_binding[key] = value
            for key in ("binding_epoch", "context_window"):
                value = binding.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    public_binding[key] = value

        public_catalog: dict[str, Any] = {}
        if isinstance(catalog, Mapping):
            for key in ("content_fingerprint",):
                value = self._bounded_text(catalog.get(key))
                if value is not None:
                    public_catalog[key] = value
            for key in ("generation", "schema_token_count", "tool_count"):
                value = catalog.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    public_catalog[key] = value
            names = catalog.get("tool_names")
            if isinstance(names, (list, tuple)):
                public_catalog["tool_names"] = [
                    bounded for raw in names
                    if (bounded := self._bounded_text(raw, 80)) is not None
                ][:512]

        public_budget: dict[str, Any] = {}
        if isinstance(budget, Mapping):
            for key in (
                "estimated_prompt_tokens", "context_window", "effective_ceiling",
                "compact_at",
            ):
                value = budget.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    public_budget[key] = value
            if isinstance(budget.get("truncated"), bool):
                public_budget["truncated"] = budget["truncated"]
        return {
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_version": snapshot.snapshot_version,
            "snapshot_fingerprint": snapshot.snapshot_fingerprint,
            "session_id": snapshot.session_id,
            "request_id": snapshot.request_id,
            "root_run_id": snapshot.root_run_id,
            "sdk_run_id": snapshot.sdk_run_id,
            "turn_id": snapshot.turn_id,
            "provider_binding": public_binding,
            "catalog": public_catalog,
            "attachments": public_attachments,
            "sections": public_sections,
            "budget": public_budget,
        }


__all__ = [
    "DefaultDenySnapshotRedactor",
    "PreparedSdkContextSnapshotV1",
    "SnapshotContractConflict",
    "canonical_json",
    "canonical_sha256",
]


# ---------------------------------------------------------------------------
# S5a — per-turn Run Context authority, no-recall decision sink, v45 ledger.
#
# ``ProductRunContextAuthority`` mirrors the SDK react loop's bare-path
# ProviderRequest exactly (same messages / tools / temperature /
# max_output_tokens, empty metadata) so registering the authority does not
# change approved provider-visible behavior; it adds the durable per-turn
# snapshot receipt chain (three-hash equality) on top.
# ---------------------------------------------------------------------------


class ContextRouteLedgerError(RuntimeError):
    code = "sdk_context_route_ledger_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class ContextRouteLedgerStore:
    """Append-only v45 context/route ledger on the Host human-memory state.db."""

    def __init__(self, db_path: Any, *, clock: Any = None) -> None:
        import time as _time
        from pathlib import Path

        self._db_path = Path(db_path)
        self._clock = clock or _time.time

    def verify_schema(self) -> None:
        """Fail closed when the database exists without the v45 ledger."""

        import sqlite3

        if not self._db_path.exists():
            return
        with sqlite3.connect(
            f"file:{self._db_path.resolve()}?mode=ro", uri=True
        ) as db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version < 45:
                raise ContextRouteLedgerError(
                    "sdk_context_route_ledger_schema_missing"
                )

    async def _connect(self):
        import aiosqlite

        db = await aiosqlite.connect(self._db_path)
        db.row_factory = None
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        return db

    async def record_snapshot_receipt(
        self,
        *,
        sdk_run_id: str,
        provider_turn_ordinal: int,
        prior_context_revision: int,
        payload_hash: str,
        expected_request_fingerprint: str,
        source_revisions: Mapping[str, int],
    ) -> tuple[str, int]:
        """Allocate (snapshot_id, snapshot_revision); idempotent on replay."""

        run = _required_text(sdk_run_id, "sdk_run_id")
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT snapshot_id,snapshot_revision FROM run_context_snapshot_receipts "
                "WHERE sdk_run_id=? AND provider_turn_ordinal=? AND payload_hash=? "
                "ORDER BY snapshot_revision DESC LIMIT 1",
                (run, provider_turn_ordinal, payload_hash),
            )
            existing = await cursor.fetchone()
            await cursor.close()
            if existing is not None:
                await db.commit()
                return str(existing[0]), int(existing[1])
            cursor = await db.execute(
                "SELECT COALESCE(MAX(snapshot_revision),0) FROM "
                "run_context_snapshot_receipts WHERE sdk_run_id=?",
                (run,),
            )
            head = int((await cursor.fetchone())[0])
            await cursor.close()
            revision = head + 1
            snapshot_id = f"ctx-snap:{run}:{revision}:{payload_hash[:16]}"
            receipt_hash = canonical_sha256(
                {
                    "expected_request_fingerprint": expected_request_fingerprint,
                    "payload_hash": payload_hash,
                    "prior_context_revision": prior_context_revision,
                    "provider_turn_ordinal": provider_turn_ordinal,
                    "sdk_run_id": run,
                    "snapshot_id": snapshot_id,
                    "snapshot_revision": revision,
                    "source_revisions": dict(source_revisions),
                }
            )
            await db.execute(
                "INSERT INTO run_context_snapshot_receipts("
                "snapshot_id,sdk_run_id,provider_turn_ordinal,prior_context_revision,"
                "snapshot_revision,source_revisions_json,payload_hash,"
                "expected_request_fingerprint,receipt_hash,recorded_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot_id,
                    run,
                    provider_turn_ordinal,
                    prior_context_revision,
                    revision,
                    canonical_json(dict(source_revisions)),
                    payload_hash,
                    expected_request_fingerprint,
                    receipt_hash,
                    float(self._clock()),
                ),
            )
            await db.commit()
            return snapshot_id, revision
        except BaseException:
            await db.rollback()
            raise
        finally:
            await db.close()

    async def record_route_decision(
        self,
        *,
        receipt: Any,
        provider_turn_ordinal: int,
        origin: str,
        idempotency_key: str,
        request_fingerprint: str | None = None,
    ) -> None:
        """Durably record one route / no-recall decision (idempotent)."""

        if origin not in {"context_tool", "host_initial", "no_recall"}:
            raise ContextRouteLedgerError("sdk_context_route_origin_invalid")
        receipt_json = receipt.to_json()
        decision_hash = canonical_sha256(
            {
                "idempotency_key": idempotency_key,
                "origin": origin,
                "provider_turn_ordinal": provider_turn_ordinal,
                "receipt": receipt_json,
                "request_fingerprint": request_fingerprint,
            }
        )
        db = await self._connect()
        try:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                "SELECT decision_hash FROM context_route_decisions "
                "WHERE sdk_run_id=? AND idempotency_key=?",
                (receipt.run_id, idempotency_key),
            )
            existing = await cursor.fetchone()
            await cursor.close()
            if existing is not None:
                await db.commit()
                if str(existing[0]) != decision_hash:
                    raise ContextRouteLedgerError(
                        "sdk_context_route_decision_immutable"
                    )
                return
            await db.execute(
                "INSERT INTO context_route_decisions("
                "decision_id,sdk_run_id,provider_turn_ordinal,route,origin,"
                "task_scope_id,binding_set_revision,binding_set_receipt_id,"
                "binding_set_receipt_hash,recall_refs_json,receipt_id,receipt_hash,"
                "receipt_json,raw_call_id,effect_id,request_fingerprint,"
                "idempotency_key,decision_hash,recorded_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"route-decision:{receipt.run_id}:{idempotency_key}",
                    receipt.run_id,
                    provider_turn_ordinal,
                    receipt.route.value,
                    origin,
                    receipt.task_scope_id,
                    receipt.binding_set_revision,
                    receipt.binding_set_receipt_id,
                    receipt.binding_set_receipt_hash,
                    canonical_json(list(receipt.recall_refs)),
                    receipt.receipt_id,
                    receipt.receipt_hash,
                    canonical_json(receipt_json),
                    receipt.raw_call_id,
                    receipt.effect_id,
                    request_fingerprint,
                    idempotency_key,
                    decision_hash,
                    float(self._clock()),
                ),
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
        finally:
            await db.close()


class ProductRunContextAuthority:
    """Per-turn Host Context authority for the SDK 0.7 react barrier."""

    def __init__(
        self,
        *,
        ports_resolver: Any,
        exposure_resolver: Any,
        ledger: ContextRouteLedgerStore,
    ) -> None:
        self._ports_resolver = ports_resolver
        self._exposure_resolver = exposure_resolver
        self._ledger = ledger

    async def prepare_snapshot(self, request: Any) -> Any:
        from simple_harness import RequestId
        from simple_harness.execution.context_authority import RunContextSnapshot
        from simple_harness.execution.provider_invocations import (
            provider_request_fingerprint,
        )
        from simple_harness.providers import ProviderRequest

        ports = self._ports_resolver()
        context = ports.context.load(request.run_id)
        if context.revision != request.prior_context_revision:
            raise SnapshotContractConflict("sdk_context_authority_revision_drift")
        exposure = self._exposure_resolver(request.run_id)
        tools = tuple(exposure.provider_specs(request.run_id))
        start = ports.react_checkpoint.read_start_snapshot(request.run_id.value)
        start_input = start.get("input") if isinstance(start, Mapping) else None
        temperature: float | None = None
        max_output_tokens: int | None = None
        if isinstance(start_input, Mapping):
            raw_temperature = start_input.get("temperature")
            if raw_temperature is not None:
                temperature = float(raw_temperature)
            raw_max = start_input.get("max_output_tokens")
            if raw_max is not None:
                max_output_tokens = int(raw_max)
        probe = ProviderRequest(
            RequestId("hash-only"),
            tuple(context.messages),
            tools=tools,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        expected = provider_request_fingerprint(probe)
        source_revisions = {"context": int(request.prior_context_revision)}
        snapshot_id, snapshot_revision = await self._ledger.record_snapshot_receipt(
            sdk_run_id=request.run_id.value,
            provider_turn_ordinal=request.provider_turn_ordinal,
            prior_context_revision=request.prior_context_revision,
            payload_hash=expected,
            expected_request_fingerprint=expected,
            source_revisions=source_revisions,
        )
        snapshot = RunContextSnapshot(
            snapshot_id,
            request.run_id.value,
            request.provider_turn_ordinal,
            request.prior_context_revision,
            snapshot_revision,
            source_revisions,
            tuple(context.messages),
            tools,
            temperature,
            max_output_tokens,
            {},
            expected,
        )
        if snapshot.payload_hash != expected:
            raise SnapshotContractConflict(
                "sdk_context_authority_payload_fingerprint_drift"
            )
        return snapshot


class ProductRuntimeDecisionSink:
    """Durable terminal no-recall decision sink (DIRECT_STANDALONE only)."""

    def __init__(self, *, ledger: ContextRouteLedgerStore) -> None:
        self._ledger = ledger

    async def record_no_recall(
        self,
        *,
        run_id: Any,
        provider_turn_ordinal: int,
        request_fingerprint: str,
    ) -> Any:
        import uuid

        from simple_harness.execution.context_authority import ContextRouteReceipt
        from simple_harness.runtime.task_scope_protocol import TaskScopeRoute

        marker = f"no-recall:{run_id.value}:{provider_turn_ordinal}"
        receipt = ContextRouteReceipt(
            receipt_id=str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"simple-harness:{marker}")
            ),
            run_id=run_id.value,
            raw_call_id=marker,
            effect_id=marker,
            route=TaskScopeRoute.DIRECT_STANDALONE,
            task_scope_id=None,
            binding_set_revision=None,
        )
        await self._ledger.record_route_decision(
            receipt=receipt,
            provider_turn_ordinal=provider_turn_ordinal,
            origin="no_recall",
            idempotency_key=marker,
            request_fingerprint=request_fingerprint,
        )
        return receipt
