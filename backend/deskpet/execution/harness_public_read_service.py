"""Complete, bounded and redacted Inspector read boundary.

The service intentionally takes two independent SQLite read cuts.  It never
claims cross-database atomicity; the captured versions and unmatched refs are
part of the public manifest.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import aiosqlite

from deskpet.execution.run_read_model import (
    CONTEXT_VISIBILITY_EXCLUDE,
    DETAIL_QUERY_KINDS,
    ProjectionManifestV1,
    ProjectionTotalsV1,
    PublicActivityItem,
    PublicDetailPageV1,
    PublicFactEnvelope,
    PublicReadError,
    PublicRunSnapshotV3,
    ReadCutV1,
    ReadSourceCutV1,
)
from deskpet.security.provider_public_projection import ProviderPublicProjectorV1
from deskpet.security.redaction import TraceRedactor
from deskpet.security.sensitive_text import redact_sensitive_text
from deskpet.security.tool_public_projection import (
    ToolPresentationPolicyV1,
    ToolPublicProjectorV1,
    legacy_unknown_tool_policy,
)

_EVENT_PUBLIC_FIELDS = frozenset(
    {
        "role",
        "status",
        "parent_run_id",
        "terminal_event_id",
        "event_kind",
        "attempt_id",
        "trigger_failure_set_id",
        "supersedes_attempt_id",
        "failure_set_id",
        "report_ref",
        "child_run_id",
        "boundary_kind",
        "resolved",
        "reason_code",
        "evidence_refs",
        "activity_kind",
        "workflow_step_id",
        "step_index",
        "step_total",
        "phase_hint",
        "provider_stage",
        "purpose",
        "detached",
        "plan_version",
        "parent_refs",
    }
)
_REDACTOR = TraceRedactor()
_THINK_RX = re.compile(r"<think>[\s\S]*?(?:</think>|$)", re.IGNORECASE)
_PUBLIC_HIDDEN_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "cookie",
        "device_key",
        "token",
        "reasoning_content",
        "provider_payload",
        "raw_payload",
    }
)


async def _read_public_message_projection_page_tx(
    db: aiosqlite.Connection,
    *,
    table: str,
    session_id: str,
    root_run_id: str,
    after_created_at: float,
    after_id: int,
    limit: int = 256,
) -> list[aiosqlite.Row]:
    """Read one bounded keyset page inside the caller-owned state cut."""

    if table not in {"messages", "messages_archive"}:
        raise ValueError("unsupported public message projection table")
    page_limit = min(512, max(1, int(limit)))
    cursor = await db.execute(
        f"SELECT id,role,content,created_at,workflow_event_id,projection_kind "
        f"FROM {table} WHERE session_id=? AND root_run_id=? "
        "AND (created_at>? OR (created_at=? AND id>?)) "
        "ORDER BY created_at,id LIMIT ?",
        (
            session_id,
            root_run_id,
            float(after_created_at),
            float(after_created_at),
            int(after_id),
            page_limit,
        ),
    )
    rows = await cursor.fetchall()
    await cursor.close()
    return rows


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value:
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _wire_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _wire_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_wire_json(item) for item in value]
    return value


def _public_event_payload(value: Any) -> dict[str, Any]:
    raw = _json_object(value)
    selected = {key: raw[key] for key in _EVENT_PUBLIC_FIELDS if key in raw}
    return _REDACTOR.redact(selected)


def _public_content(value: Any, *, cap: int = 16_384) -> tuple[str, str | None]:
    # Thinking-model wrappers are never a public message, even if a provider
    # accidentally persisted them alongside the user-facing answer.
    text = _THINK_RX.sub("", str(value or "")).strip()
    text = redact_sensitive_text(text)
    text = str(_REDACTOR.redact(text))
    encoded = text.encode("utf-8")
    if len(encoded) <= cap:
        return text, None
    clipped = encoded[:cap]
    while clipped:
        try:
            result = clipped.decode("utf-8")
            break
        except UnicodeDecodeError:
            clipped = clipped[:-1]
    else:
        result = ""
    return result + "…", hashlib.sha256(encoded).hexdigest()


def _bounded_public_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Apply a final recursive byte/shape fence to every public payload."""

    hashes: dict[str, str] = {}

    def visit(item: Any, path: str) -> Any:
        item = _REDACTOR.redact(item)
        if isinstance(item, str):
            encoded = redact_sensitive_text(item).encode("utf-8")
            if len(encoded) <= 2048:
                return encoded.decode("utf-8")
            hashes[path] = hashlib.sha256(encoded).hexdigest()
            clipped = encoded[:2048]
            while clipped:
                try:
                    return clipped.decode("utf-8") + "…"
                except UnicodeDecodeError:
                    clipped = clipped[:-1]
            return "…"
        if isinstance(item, Mapping):
            keys = sorted(
                str(key)
                for key in item
                if str(key).casefold() not in _PUBLIC_HIDDEN_KEYS
            )
            if len(keys) > 64:
                hashes[path] = hashlib.sha256(_canonical(item).encode("utf-8")).hexdigest()
                keys = keys[:64]
            return {key: visit(item[key], f"{path}/{key}") for key in keys}
        if isinstance(item, (list, tuple)):
            values = list(item)
            if len(values) > 128:
                hashes[path] = hashlib.sha256(_canonical(values).encode("utf-8")).hexdigest()
                values = values[:128]
            return [visit(child, f"{path}/{index}") for index, child in enumerate(values)]
        if item is None or isinstance(item, (bool, int, float)):
            return item
        return "[REDACTED]"

    result = visit(dict(value), "$")
    assert isinstance(result, dict)
    if hashes:
        result["_truncation_hashes"] = hashes
    return result


def _bounded_public_value(value: Any) -> tuple[Any, bool]:
    """Bound a safe detail whether the projector received a mapping or scalar."""
    if value is None:
        return None, False
    if isinstance(value, Mapping):
        bounded = _bounded_public_mapping(value)
        return bounded, bool(bounded.get("_truncation_hashes"))
    bounded = _bounded_public_mapping({"value": value})
    return bounded.get("value"), bool(bounded.get("_truncation_hashes"))


def _public_views(
    facts: tuple[PublicFactEnvelope, ...],
    phases: tuple[Mapping[str, Any], ...],
    aggregate: Mapping[str, Any],
    root_run_id: str,
) -> tuple[tuple[PublicActivityItem, ...], tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]]:
    """Build bounded eager views from the same immutable public read cut.

    This function only sees ``PublicFactEnvelope.public_payload``.  It never
    serializes the envelope itself, so raw ledger/provider fields cannot cross
    the V3 wire boundary.
    """
    phase_by_fact: dict[str, str] = {}
    for phase in phases:
        phase_id = str(phase.get("phase_id", ""))
        for item in phase.get("items", ()):
            if isinstance(item, Mapping) and item.get("stable_id"):
                phase_by_fact[str(item["stable_id"])] = phase_id

    title_by_kind = {
        "tool": "执行工具",
        "provider": "处理请求",
        "workflow_step": "执行工作流步骤",
        "child": "执行子任务",
        "child_command": "执行子任务",
        "narration": "更新进度",
        "content": "更新消息",
        "user_request": "理解需求",
        "boundary": "等待确认",
        "failure_report": "记录失败",
        "failure_set": "整理失败证据",
        "run": "建立任务",
        "run_terminal": "任务结束",
    }
    activities: list[PublicActivityItem] = []
    tools: list[Mapping[str, Any]] = []
    messages: list[Mapping[str, Any]] = []
    seen_fact_ids: set[str] = set()
    for fact in facts:
        if not fact.stable_id or fact.stable_id in seen_fact_ids:
            continue
        seen_fact_ids.add(fact.stable_id)
        payload = fact.public_payload
        kind = fact.kind
        phase_id = phase_by_fact.get(fact.stable_id)
        status = str(payload.get("status") or "unknown")
        text = payload.get("content") or payload.get("safe_text")
        safe_text, text_digest = _public_content(text) if isinstance(text, str) and text.strip() else (None, None)
        tool_view: dict[str, Any] | None = None
        if kind == "tool":
            safe_input, input_truncated = _bounded_public_value(
                payload.get("safe_input", payload.get("public_input"))
            )
            safe_result, result_truncated = _bounded_public_value(
                payload.get("bounded_result", payload.get("public_result"))
            )
            truncation_hashes = payload.get("truncation_hashes", payload.get("_truncation_hashes", {}))
            tool_view = {
                "tool_ref": fact.stable_id,
                "stable_id": fact.stable_id,
                "phase_id": phase_id,
                "public_name": str(payload.get("tool_name") or payload.get("public_name") or "工具"),
                "action_label": str(payload.get("action_code") or payload.get("action_label") or "执行操作"),
                "safe_target_label": payload.get("safe_target_label"),
                "status": status,
                "public_input": safe_input,
                "public_result": safe_result,
                "details_available": safe_input is not None,
                "result_available": safe_result is not None,
                "truncated": bool(truncation_hashes) or input_truncated or result_truncated,
                "detail_ref": fact.stable_id,
                "created_at": fact.created_at,
                "context_visibility": CONTEXT_VISIBILITY_EXCLUDE,
            }
            tools.append(tool_view)
        if kind in {"content", "narration", "user_request"} and safe_text:
            messages.append({
                "message_id": fact.stable_id,
                "stable_id": fact.stable_id,
                "phase_id": phase_id,
                "text": safe_text,
                "created_at": fact.created_at,
                "context_visibility": CONTEXT_VISIBILITY_EXCLUDE,
            })
        activity = PublicActivityItem(
            stable_id=fact.stable_id,
            kind=kind,
            title=(tool_view or {}).get("public_name") if tool_view else title_by_kind.get(kind, "执行记录"),
            status=status,
            phase_id=phase_id,
            action_code=str(payload.get("action_code")) if payload.get("action_code") is not None else None,
            tool_name=str(payload.get("tool_name")) if payload.get("tool_name") is not None else None,
            safe_text=safe_text,
            safe_target_label=(tool_view or {}).get("safe_target_label") if tool_view else None,
            detail_ref=fact.stable_id if kind == "tool" else None,
            public_input=(tool_view or {}).get("public_input") if tool_view else None,
            public_result=(tool_view or {}).get("public_result") if tool_view else None,
            duration_ms=payload.get("duration_ms") if isinstance(payload.get("duration_ms"), (int, float)) else None,
            created_at=fact.created_at,
            truncated=bool((tool_view or {}).get("truncated")) or text_digest is not None,
        )
        activities.append(activity)
    terminal_status = str(aggregate.get("status") or "unknown")
    terminal_id = f"run-terminal:{root_run_id}"
    if terminal_id not in {item.stable_id for item in activities}:
        activities.append(PublicActivityItem(
            stable_id=terminal_id,
            kind="run_terminal",
            title="任务结束",
            status=terminal_status,
            safe_text=None,
            created_at=aggregate.get("ended_at") if isinstance(aggregate.get("ended_at"), (int, float)) else None,
        ))
    activities.sort(key=lambda item: (item.created_at is None, item.created_at or 0.0, item.stable_id))
    tools.sort(key=lambda item: (item.get("created_at") is None, item.get("created_at") or 0.0, str(item.get("stable_id"))))
    messages.sort(key=lambda item: (item.get("created_at") is None, item.get("created_at") or 0.0, str(item.get("stable_id"))))
    return tuple(activities), tuple(tools), tuple(messages)


class HarnessPublicReadService:
    def __init__(
        self,
        *,
        workflow_db_path: str | Path,
        state_db_path: str | Path,
        cursor_secret: bytes,
        clock: Callable[[], float] = time.time,
        manifest_ttl_seconds: float = 120.0,
        max_manifests: int = 32,
        max_public_facts: int = 50_000,
        max_manifest_bytes: int = 32 * 1024 * 1024,
        max_page_bytes: int = 256 * 1024,
        reducer: Callable[
            [
                tuple[PublicFactEnvelope, ...],
                ReadCutV1,
                str,
                str,
            ],
            tuple[
                Mapping[str, Any],
                tuple[Mapping[str, Any], ...],
                bool,
                tuple[str, ...],
            ],
        ]
        | None = None,
    ) -> None:
        if len(cursor_secret) < 16:
            raise ValueError("Inspector cursor secret must contain at least 16 bytes")
        if manifest_ttl_seconds <= 0 or max_manifests < 1:
            raise ValueError("invalid manifest cache configuration")
        self.workflow_db_path = Path(workflow_db_path)
        self.state_db_path = Path(state_db_path)
        self._secret = bytes(cursor_secret)
        self._clock = clock
        self._ttl = float(manifest_ttl_seconds)
        self._max_manifests = int(max_manifests)
        self._max_public_facts = min(50_000, int(max_public_facts))
        self._max_manifest_bytes = min(32 * 1024 * 1024, int(max_manifest_bytes))
        self._max_page_bytes = max(4096, int(max_page_bytes))
        self._cache: OrderedDict[str, ProjectionManifestV1] = OrderedDict()
        self._cache_lock = asyncio.Lock()
        self._provider_projector = ProviderPublicProjectorV1()
        self._tool_projector = ToolPublicProjectorV1()
        self._reducer = reducer

    async def create_manifest(
        self,
        *,
        session_id: str,
        root_run_id: str,
    ) -> ProjectionManifestV1:
        session_id = str(session_id).strip()
        root_run_id = str(root_run_id).strip()
        if not session_id or not root_run_id:
            raise PublicReadError("invalid_request", "session_id and root_run_id are required")

        workflow_facts, workflow_cut, workflow_meta = await self._read_workflow_cut(
            session_id=session_id,
            root_run_id=root_run_id,
        )
        state_facts, state_cut, state_meta = await self._read_state_cut(
            session_id=session_id,
            root_run_id=root_run_id,
        )
        facts = tuple(workflow_facts + state_facts)
        if len(facts) > self._max_public_facts:
            raise PublicReadError(
                "projection_too_large",
                f"public fact count {len(facts)} exceeds {self._max_public_facts}",
            )
        fact_bytes = len(_canonical([fact.to_dict() for fact in facts]).encode("utf-8"))
        if fact_bytes > self._max_manifest_bytes:
            raise PublicReadError(
                "projection_too_large",
                f"public manifest size {fact_bytes} exceeds {self._max_manifest_bytes}",
            )

        unmatched = tuple(
            sorted(
                set(workflow_meta.get("unmatched_refs", ()))
                | set(state_meta.get("unmatched_refs", ()))
            )
        )
        read_cut = ReadCutV1(
            workflow=workflow_cut,
            state=state_cut,
            unmatched_refs=unmatched,
        )
        aggregate, phases, reducer_complete, reducer_diagnostics = self._reduce(
            facts,
            read_cut,
            session_id,
            root_run_id,
        )
        source_complete = workflow_cut.complete and state_cut.complete
        diagnostics = tuple(
            dict.fromkeys(
                (*workflow_meta.get("diagnostics", ()), *state_meta.get("diagnostics", ()), *reducer_diagnostics)
            )
        )
        detail_rows = self._detail_rows(facts)
        activity_items, tool_public_views, public_messages = _public_views(
            facts, phases, aggregate, root_run_id
        )
        totals = ProjectionTotalsV1(
            **{kind: len(detail_rows[kind]) for kind in DETAIL_QUERY_KINDS}
        )
        created_at = float(self._clock())
        projection_seed = {
            "session_id": session_id,
            "root_run_id": root_run_id,
            "fact_ids": [fact.stable_id for fact in facts],
            "cuts": read_cut.to_dict(),
        }
        projection_id = hashlib.sha256(_canonical(projection_seed).encode("utf-8")).hexdigest()
        manifest = ProjectionManifestV1(
            projection_id=projection_id,
            session_id=session_id,
            root_run_id=root_run_id,
            facts=facts,
            aggregate_outcome=aggregate,
            semantic_phases=phases,
            detail_rows=detail_rows,
            totals=totals,
            projection_complete=bool(source_complete and reducer_complete and not unmatched),
            diagnostics=diagnostics,
            read_cut=read_cut,
            created_at=created_at,
            expires_at=created_at + self._ttl,
            activity_items=activity_items,
            tool_public_views=tool_public_views,
            public_messages=public_messages,
        )
        await self._put_manifest(manifest)
        return manifest

    @staticmethod
    def snapshot(manifest: ProjectionManifestV1) -> PublicRunSnapshotV3:
        return PublicRunSnapshotV3(
            projection_id=manifest.projection_id,
            session_id=manifest.session_id,
            root_run_id=manifest.root_run_id,
            aggregate_outcome=manifest.aggregate_outcome,
            semantic_phases=manifest.semantic_phases,
            totals=manifest.totals,
            projection_complete=manifest.projection_complete,
            diagnostics=manifest.diagnostics,
            read_cut=manifest.read_cut,
            context_visibility=CONTEXT_VISIBILITY_EXCLUDE,
            activity_items=manifest.activity_items,
            tool_public_views=manifest.tool_public_views,
            public_messages=manifest.public_messages,
        )

    async def query_details(
        self,
        *,
        projection_id: str,
        session_id: str,
        root_run_id: str,
        query_kind: str,
        page_size: int = 50,
        cursor: str | None = None,
        schema_version: str = "3",
    ) -> PublicDetailPageV1:
        if schema_version != "3":
            raise PublicReadError("unsupported_schema", "only Inspector schema V3 is supported")
        if query_kind not in DETAIL_QUERY_KINDS:
            raise PublicReadError("invalid_request", "unknown Inspector detail query kind")
        page_size = int(page_size)
        if not 1 <= page_size <= 200:
            raise PublicReadError("invalid_request", "page_size must be between 1 and 200")
        manifest = await self._get_manifest(projection_id)
        if (manifest.session_id, manifest.root_run_id) != (session_id, root_run_id):
            raise PublicReadError("invalid_cursor", "manifest owner fence mismatch")
        offset = 0
        if cursor:
            payload = self._decode_cursor(cursor)
            expected = {
                "schema_version": "3",
                "projection_id": projection_id,
                "session_id": session_id,
                "root_run_id": root_run_id,
                "query_kind": query_kind,
                "page_size": page_size,
            }
            if any(payload.get(key) != value for key, value in expected.items()):
                raise PublicReadError("invalid_cursor", "cursor query fence mismatch")
            if float(payload.get("expires_at", 0)) <= self._clock():
                raise PublicReadError("manifest_expired", "Inspector manifest has expired")
            offset = int(payload.get("offset", -1))
            if offset < 0:
                raise PublicReadError("invalid_cursor", "cursor offset is invalid")

        all_items = manifest.detail_rows[query_kind]
        items: list[Mapping[str, Any]] = []
        page_bytes = 0
        index = offset
        while index < len(all_items) and len(items) < page_size:
            item = all_items[index]
            size = len(_canonical(_wire_json(item)).encode("utf-8"))
            if items and page_bytes + size > self._max_page_bytes:
                break
            if size > self._max_page_bytes:
                raise PublicReadError("projection_too_large", "one public detail exceeds page limit")
            items.append(item)
            page_bytes += size
            index += 1
        next_cursor = None
        if index < len(all_items):
            next_cursor = self._encode_cursor(
                {
                    "schema_version": "3",
                    "projection_id": projection_id,
                    "session_id": session_id,
                    "root_run_id": root_run_id,
                    "query_kind": query_kind,
                    "offset": index,
                    "page_size": page_size,
                    "expires_at": manifest.expires_at,
                }
            )
        return PublicDetailPageV1(
            projection_id=projection_id,
            query_kind=query_kind,
            items=tuple(items),
            total=len(all_items),
            next_cursor=next_cursor,
            projection_complete=manifest.projection_complete,
        )

    async def _read_workflow_cut(
        self, *, session_id: str, root_run_id: str
    ) -> tuple[list[PublicFactEnvelope], ReadSourceCutV1, dict[str, Any]]:
        facts: list[PublicFactEnvelope] = []
        diagnostics: list[str] = []
        unmatched: list[str] = []
        captured_at = float(self._clock())
        if not self.workflow_db_path.exists():
            return facts, ReadSourceCutV1(captured_at, None, False), {
                "diagnostics": ("workflow_source_missing",), "unmatched_refs": ()
            }
        async with aiosqlite.connect(self.workflow_db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA query_only=ON")
            await db.execute("BEGIN")
            try:
                data_version_row = await (await db.execute("PRAGMA data_version")).fetchone()
                data_version = int(data_version_row[0]) if data_version_row else None
                root = await (
                    await db.execute(
                        "SELECT * FROM execution_runs WHERE run_id=? AND root_run_id=?",
                        (root_run_id, root_run_id),
                    )
                ).fetchone()
                if root is None or str(root["session_id"]) != session_id:
                    return facts, ReadSourceCutV1(captured_at, data_version, False), {
                        "diagnostics": ("root_not_found",), "unmatched_refs": ()
                    }
                runs = await (
                    await db.execute(
                        "SELECT * FROM execution_runs WHERE root_run_id=? ORDER BY created_at,run_id",
                        (root_run_id,),
                    )
                ).fetchall()
                run_ids = tuple(str(row["run_id"]) for row in runs)
                for row in runs:
                    payload = {
                        "run_id": str(row["run_id"]),
                        "role": "root" if row["run_id"] == root_run_id else "child",
                        "status": str(row["status"]),
                        "started_at": (
                            row["created_at"]
                            if row["started_at"] is None
                            else row["started_at"]
                        ),
                        "ended_at": row["ended_at"],
                        "parent_run_id": row["parent_run_id"],
                        "terminal_event_id": row["terminal_event_id"],
                        "child_run_id": None if row["run_id"] == root_run_id else row["run_id"],
                        "profile_key": str(row["profile_key"]),
                    }
                    facts.append(self._fact("run", row["run_id"], root_run_id, row["created_at"], payload))
                placeholders = ",".join("?" for _ in run_ids)
                facts.extend(await self._workflow_table_facts(db, root_run_id, run_ids, placeholders))
            finally:
                await db.rollback()
        return facts, ReadSourceCutV1(captured_at, data_version, True), {
            "diagnostics": tuple(diagnostics), "unmatched_refs": tuple(unmatched)
        }

    async def _workflow_table_facts(
        self,
        db: aiosqlite.Connection,
        root_run_id: str,
        run_ids: tuple[str, ...],
        placeholders: str,
    ) -> list[PublicFactEnvelope]:
        facts: list[PublicFactEnvelope] = []
        rows = await (await db.execute(
            f"SELECT * FROM execution_events WHERE run_id IN ({placeholders}) ORDER BY created_at,event_id",
            run_ids,
        )).fetchall()
        for row in rows:
            payload = _public_event_payload(row["payload_json"])
            payload.update(
                {
                    "event_kind": str(row["kind"]),
                    "status": str(row["status"]),
                    "run_id": str(row["run_id"]),
                    "role": "root" if row["run_id"] == root_run_id else "child",
                    "source_stream": f"execution_events:{row['run_id']}",
                }
            )
            event_status = str(row["status"])
            event_kind = str(row["kind"])
            canonical_kind = (
                "child"
                if "child" in event_kind or "delegate" in event_kind
                else "run_terminal"
                if "terminal" in event_kind or "final" in event_kind
                else "boundary"
                if event_status == "waiting" or "decision" in event_kind
                else "workflow_step"
            )
            if canonical_kind == "run_terminal" and payload["status"] == "succeeded":
                payload["status"] = "completed"
            if canonical_kind == "child" and payload.get("child_run_id"):
                payload["run_id"] = str(payload["child_run_id"])
                payload["role"] = "child"
            facts.append(self._fact(canonical_kind, row["event_id"], root_run_id, row["created_at"], payload, workflow_event_id=str(row["event_id"]), source_seq=int(row["durable_seq"])))

        table_specs = (
            ("execution_attempt_records", "attempt_id", "created_at", "attempt", ("run_id", "status", "plan_version", "provider_turn_id", "provider_batch_id", "trigger_failure_set_id", "supersedes_attempt_id")),
            ("execution_provider_action_batches", "provider_batch_id", "created_at", "provider", ("status", "failure_set_id", "provider_turn_id")),
            ("execution_provider_action_calls", "call_record_id", "created_at", "tool", ("provider_batch_id", "provider_call_id", "raw_tool_name", "admission_state", "terminal_outcome_ref")),
            ("execution_task_failure_reports", "report_ref", "created_at", "failure_report", ("run_id", "attempt_id", "plan_version", "failure_set_id", "child_run_id", "reason_code", "evidence_refs_json")),
            ("execution_attempt_failure_sets", "failure_set_id", "created_at", "failure_set", ("failed_attempt_id", "primary_report_ref", "provider_resume_state")),
            ("execution_task_external_waits", "wait_ref", "created_at", "boundary", ("attempt_id", "wait_kind", "state", "evidence_refs_json")),
            ("execution_run_block_signals", "signal_id", "created_at", "block_signal", ("root_run_id", "reason_code", "evidence_refs_json", "producer", "created_event_id")),
        )
        for table, identity, created, kind, fields in table_specs:
            if not await self._table_exists(db, table):
                continue
            if table in {"execution_provider_action_batches", "execution_provider_action_calls", "execution_task_failure_reports", "execution_attempt_failure_sets", "execution_task_external_waits", "execution_run_block_signals"}:
                query = f"SELECT * FROM {table} WHERE root_run_id=? ORDER BY {created},{identity}"
                params: Sequence[Any] = (root_run_id,)
            else:
                query = f"SELECT * FROM {table} WHERE run_id IN ({placeholders}) ORDER BY {created},{identity}"
                params = run_ids
            for row in await (await db.execute(query, params)).fetchall():
                payload: dict[str, Any] = {}
                for field in fields:
                    if field in row.keys() and row[field] is not None:
                        if field.endswith("_json"):
                            payload[field.removesuffix("_json")] = _REDACTOR.redact(_json_object(row[field]) or self._json_list(row[field]))
                        else:
                            payload[field] = row[field]
                payload[identity] = row[identity]
                if "root_run_id" in row.keys():
                    payload["root_run_id"] = row["root_run_id"]
                payload["role"] = (
                    "root"
                    if str(row["run_id"] if "run_id" in row.keys() else row["root_run_id"])
                    == root_run_id
                    else "child"
                )
                if table == "execution_attempt_failure_sets":
                    member_rows = (
                        await (await db.execute(
                            "SELECT report_ref FROM execution_attempt_failure_set_members "
                            "WHERE failure_set_id=? ORDER BY provider_call_order,report_ref",
                            (row["failure_set_id"],),
                        )).fetchall()
                        if await self._table_exists(
                            db, "execution_attempt_failure_set_members"
                        )
                        else ()
                    )
                    report_refs = [str(member["report_ref"]) for member in member_rows]
                    payload["report_ref"] = str(row["primary_report_ref"])
                    payload["report_refs"] = report_refs or [str(row["primary_report_ref"])]
                if table == "execution_task_external_waits":
                    payload["boundary_kind"] = (
                        "human" if str(row["wait_kind"]) == "user_content" else "admission"
                    )
                    payload["resolved"] = str(row["state"]) != "open"
                    payload["status"] = str(row["state"])
                if table == "execution_provider_action_calls":
                    payload.update(
                        {
                            "run_id": root_run_id,
                            "call_id": str(row["provider_call_id"]),
                            "source_kind": "provider_call",
                            "tool_name": str(row["raw_tool_name"]),
                            "activity_kind": "mutate",
                            "action_code": "execute",
                            "safe_target_label": str(row["raw_tool_name"]),
                            "status": str(row["admission_state"]),
                            "safe_input": {},
                            "bounded_result": {},
                            "mapping_reason": "legacy_unknown_tool",
                        }
                    )
                facts.append(self._fact(kind, row[identity], root_run_id, row[created], payload))

        if await self._table_exists(db, "execution_decisions"):
            rows = await (await db.execute(
                f"SELECT * FROM execution_decisions WHERE run_id IN ({placeholders}) "
                "ORDER BY created_at,decision_id",
                run_ids,
            )).fetchall()
            for row in rows:
                decision_status = str(row["status"])
                public_status = {
                    "allowed": "resolved",
                    "denied": "rejected",
                    "expired": "expired",
                }.get(decision_status, "open")
                facts.append(self._fact(
                    "boundary",
                    row["decision_id"],
                    root_run_id,
                    row["created_at"],
                    {
                        "decision_id": str(row["decision_id"]),
                        "run_id": str(row["run_id"]),
                        "role": "root" if row["run_id"] == root_run_id else "child",
                        "boundary_kind": "human",
                        "decision_kind": str(row["kind"]),
                        "status": public_status,
                        "resolved": decision_status != "open",
                    },
                ))

        projection_by_effect: dict[str, Mapping[str, Any]] = {}
        if await self._table_exists(db, "execution_tool_public_projections"):
            rows = await (await db.execute(
                "SELECT * FROM execution_tool_public_projections WHERE root_run_id=? ORDER BY created_at,effect_id",
                (root_run_id,),
            )).fetchall()
            for row in rows:
                projection_by_effect[str(row["effect_id"])] = _json_object(row["projection_json"])
        policies = await self._read_frozen_policies(db, root_run_id)
        rows = await (await db.execute(
            f"SELECT e.*,r.root_run_id FROM execution_effects e JOIN execution_runs r ON r.run_id=e.run_id WHERE e.run_id IN ({placeholders}) ORDER BY e.created_at,e.effect_id",
            run_ids,
        )).fetchall()
        for row in rows:
            effect_id = str(row["effect_id"])
            projection = projection_by_effect.get(effect_id)
            tool_name = str(row["tool_name"])
            if projection is None:
                policy = policies.get(tool_name)
                if policy is not None and row["outcome_json"] is not None:
                    projection = self._tool_projector.project(
                        policy,
                        arguments=_json_object(row["prepared_json"]),
                        result=_json_object(row["outcome_json"]),
                        status=str(row["status"]),
                    ).to_dict()
                else:
                    projection = self._tool_projector.project(
                        legacy_unknown_tool_policy(tool_name),
                        arguments={}, result={}, status=str(row["status"]),
                    ).to_dict()
                    projection["mapping_reason"] = "legacy_unknown_tool"
            projection = dict(projection)
            projection.update(
                {
                    "effect_id": effect_id,
                    "run_id": str(row["run_id"]),
                    "role": "root" if row["run_id"] == root_run_id else "child",
                    "call_id": str(row["call_id"]),
                }
            )
            link_rows = await (await db.execute(
                "SELECT node_execution_id FROM execution_effect_links "
                "WHERE run_id=? AND effect_id=? ORDER BY node_execution_id",
                (row["run_id"], effect_id),
            )).fetchall()
            node_refs = [
                str(link["node_execution_id"])
                for link in link_rows
                if str(link["node_execution_id"])
            ]
            if node_refs:
                projection["workflow_step_id"] = node_refs[0]
                projection["parent_refs"] = node_refs
            facts.append(self._fact("tool", effect_id, root_run_id, row["created_at"], projection))

        rows = await (await db.execute(
            f"SELECT * FROM execution_provider_invocations WHERE run_id IN ({placeholders}) ORDER BY updated_at,invocation_id",
            run_ids,
        )).fetchall()
        for row in rows:
            outcome_row = await (await db.execute(
                "SELECT payload_json FROM execution_provider_invocation_outcomes WHERE run_id=? AND invocation_id=?",
                (row["run_id"], row["invocation_id"]),
            )).fetchone()
            outcome = _json_object(outcome_row["payload_json"]) if outcome_row else None
            projection = self._provider_projector.project(dict(row), outcome=outcome)
            projection["role"] = "root" if row["run_id"] == root_run_id else "child"
            facts.append(self._fact("provider", f"{row['run_id']}:{row['invocation_id']}", root_run_id, row["updated_at"], projection, invocation_id=str(row["invocation_id"])))

        if await self._table_exists(db, "workflow_runs"):
            rows = await (await db.execute(
                f"SELECT * FROM workflow_runs WHERE run_id IN ({placeholders}) ORDER BY created_at,run_id",
                run_ids,
            )).fetchall()
            for row in rows:
                facts.append(self._fact(
                    "workflow_step",
                    f"workflow_run:{row['run_id']}",
                    root_run_id,
                    row["created_at"],
                    {
                        "run_id": str(row["run_id"]),
                        "role": "root" if row["run_id"] == root_run_id else "child",
                        "status": row["status"],
                        "parent_run_id": row["parent_run_id"],
                        "workflow_name": row["workflow_name"],
                        "event_kind": "workflow.run",
                    },
                ))
            event_rows = await (await db.execute(
                f"SELECT * FROM workflow_events WHERE run_id IN ({placeholders}) ORDER BY created_at,event_id",
                run_ids,
            )).fetchall()
            for row in event_rows:
                payload = _public_event_payload(row["payload_json"])
                payload.update(
                    {
                        "event_kind": str(row["event_type"]),
                        "run_id": str(row["run_id"]),
                        "role": "root" if row["run_id"] == root_run_id else "child",
                        "source_stream": f"workflow_events:{row['run_id']}",
                    }
                )
                facts.append(self._fact(
                    "workflow_step",
                    row["event_id"],
                    root_run_id,
                    row["created_at"],
                    payload,
                    workflow_event_id=str(row["event_id"]),
                    source_seq=int(row["seq"]),
                ))
            node_rows = await (await db.execute(
                f"SELECT * FROM workflow_nodes WHERE run_id IN ({placeholders}) ORDER BY updated_at,node_execution_id",
                run_ids,
            )).fetchall()
            for row in node_rows:
                facts.append(self._fact(
                    "workflow_step",
                    row["node_execution_id"],
                    root_run_id,
                    row["updated_at"],
                    {
                        "run_id": str(row["run_id"]),
                        "role": "root" if row["run_id"] == root_run_id else "child",
                        "status": row["latest_status"],
                        "workflow_step_id": row["node_id"],
                    },
                ))
        return facts

    async def _read_state_cut(
        self, *, session_id: str, root_run_id: str
    ) -> tuple[list[PublicFactEnvelope], ReadSourceCutV1, dict[str, Any]]:
        facts: list[PublicFactEnvelope] = []
        diagnostics: list[str] = []
        unmatched: list[str] = []
        captured_at = float(self._clock())
        if not self.state_db_path.exists():
            return facts, ReadSourceCutV1(captured_at, None, False), {
                "diagnostics": ("state_source_missing",), "unmatched_refs": ()
            }
        async with aiosqlite.connect(self.state_db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA query_only=ON")
            await db.execute("BEGIN")
            try:
                version_row = await (await db.execute("PRAGMA data_version")).fetchone()
                data_version = int(version_row[0]) if version_row else None
                active_rows = await self._keyset_messages(db, "messages", session_id, root_run_id)
                archive_rows = await self._keyset_messages(db, "messages_archive", session_id, root_run_id)
                legacy_row = await (await db.execute(
                    "SELECT COUNT(*) FROM messages_archive WHERE session_id=? AND root_run_id IS NULL",
                    (session_id,),
                )).fetchone()
                if legacy_row and int(legacy_row[0]):
                    legacy_ref = f"legacy_unscoped:{int(legacy_row[0])}"
                    diagnostics.append(legacy_ref)
                    unmatched.append(legacy_ref)
                for source, rows in (("message", active_rows), ("message_archive", archive_rows)):
                    for row in rows:
                        content, digest = _public_content(row["content"])
                        payload = {
                            "role": str(row["role"]),
                            "author": str(row["role"]),
                            "content": content,
                            "projection_kind": str(row["projection_kind"] or "legacy_message"),
                            "workflow_event_id": row["workflow_event_id"],
                            "archived": source == "message_archive",
                        }
                        if digest:
                            payload["truncation_hash"] = digest
                        content_kind = (
                            "user_request" if str(row["role"]) == "user" else "content"
                        )
                        facts.append(self._fact(content_kind, f"{source}:{row['id']}", root_run_id, row["created_at"], payload, workflow_event_id=row["workflow_event_id"], source="state"))
                if await self._table_exists(db, "provider_workload_audit"):
                    rows = await (await db.execute(
                        "SELECT * FROM provider_workload_audit WHERE session_id=? AND root_run_id=? ORDER BY created_at,stable_call_id",
                        (session_id, root_run_id),
                    )).fetchall()
                    for row in rows:
                        projection = self._provider_projector.project(dict(row))
                        facts.append(self._fact("provider", f"audit:{row['stable_call_id']}", root_run_id, row["created_at"], projection, source="state"))
            finally:
                await db.rollback()
        return facts, ReadSourceCutV1(captured_at, data_version, True), {
            "diagnostics": tuple(diagnostics), "unmatched_refs": tuple(unmatched)
        }

    async def _keyset_messages(
        self, db: aiosqlite.Connection, table: str, session_id: str, root_run_id: str
    ) -> list[aiosqlite.Row]:
        rows: list[aiosqlite.Row] = []
        cursor_time = -1.0
        cursor_id = -1
        while True:
            batch = await _read_public_message_projection_page_tx(
                db,
                table=table,
                session_id=session_id,
                root_run_id=root_run_id,
                after_created_at=cursor_time,
                after_id=cursor_id,
            )
            if not batch:
                break
            rows.extend(batch)
            cursor_time = float(batch[-1]["created_at"])
            cursor_id = int(batch[-1]["id"])
        return rows

    async def _read_frozen_policies(
        self, db: aiosqlite.Connection, root_run_id: str
    ) -> dict[str, ToolPresentationPolicyV1]:
        if not await self._table_exists(db, "execution_run_tool_presentation_specs"):
            return {}
        rows = await (await db.execute(
            "SELECT tool_name,policy_json FROM execution_run_tool_presentation_specs WHERE root_run_id=?",
            (root_run_id,),
        )).fetchall()
        policies: dict[str, ToolPresentationPolicyV1] = {}
        for row in rows:
            raw = _json_object(row["policy_json"])
            try:
                raw["safe_arg_paths"] = tuple(raw.get("safe_arg_paths", ()))
                raw["safe_result_paths"] = tuple(raw.get("safe_result_paths", ()))
                policies[str(row["tool_name"])] = ToolPresentationPolicyV1(**raw)
            except (TypeError, ValueError):
                continue
        return policies

    @staticmethod
    async def _table_exists(db: aiosqlite.Connection, table: str) -> bool:
        row = await (await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )).fetchone()
        return row is not None

    @staticmethod
    def _json_list(value: Any) -> list[Any]:
        try:
            decoded = json.loads(str(value or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return decoded if isinstance(decoded, list) else []

    @staticmethod
    def _fact(
        kind: str,
        identity: Any,
        root_run_id: str,
        created_at: Any,
        payload: Mapping[str, Any],
        *,
        workflow_event_id: str | None = None,
        invocation_id: str | None = None,
        source_seq: int | None = None,
        source: str = "workflow",
    ) -> PublicFactEnvelope:
        stable_id = f"{kind}:{identity}"
        return PublicFactEnvelope(
            source=source,
            stable_id=stable_id,
            root_run_id=root_run_id,
            kind=kind,
            public_payload=_bounded_public_mapping(payload),
            created_at=float(created_at or 0.0),
            workflow_event_id=workflow_event_id,
            invocation_id=invocation_id,
            source_seq=source_seq,
        )

    @staticmethod
    def _detail_rows(facts: tuple[PublicFactEnvelope, ...]) -> dict[str, tuple[Mapping[str, Any], ...]]:
        grouped: dict[str, list[Mapping[str, Any]]] = {kind: [] for kind in DETAIL_QUERY_KINDS}
        for fact in facts:
            if fact.kind == "tool":
                bucket = "tool_details"
            elif fact.kind == "provider":
                bucket = "provider_details"
            elif fact.kind in {"user_request", "content", "narration"}:
                bucket = "content_facts"
            else:
                bucket = "workflow_facts"
            grouped[bucket].append(fact.to_dict())
        return {key: tuple(value) for key, value in grouped.items()}

    def _reduce(
        self,
        facts: tuple[PublicFactEnvelope, ...],
        read_cut: ReadCutV1,
        session_id: str,
        root_run_id: str,
    ) -> tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...], bool, tuple[str, ...]]:
        reducer = self._reducer
        if reducer is None:
            try:
                from deskpet.execution.semantic_projection import (
                    reduce_public_manifest,
                )
            except ImportError as exc:
                raise PublicReadError(
                    "semantic_reducer_unavailable",
                    "Inspector semantic projection is unavailable",
                ) from exc
            reducer = reduce_public_manifest
        return reducer(facts, read_cut, session_id, root_run_id)

    async def _put_manifest(self, manifest: ProjectionManifestV1) -> None:
        async with self._cache_lock:
            self._evict_expired_locked()
            self._cache[manifest.projection_id] = manifest
            self._cache.move_to_end(manifest.projection_id)
            while len(self._cache) > self._max_manifests:
                self._cache.popitem(last=False)

    async def _get_manifest(self, projection_id: str) -> ProjectionManifestV1:
        async with self._cache_lock:
            self._evict_expired_locked()
            manifest = self._cache.get(projection_id)
            if manifest is None:
                raise PublicReadError("manifest_expired", "Inspector manifest is unavailable; create a new snapshot")
            self._cache.move_to_end(projection_id)
            return manifest

    def _evict_expired_locked(self) -> None:
        now = self._clock()
        for key in tuple(self._cache):
            if self._cache[key].expires_at <= now:
                del self._cache[key]

    def _encode_cursor(self, payload: Mapping[str, Any]) -> str:
        raw = _canonical(dict(payload)).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
        signature = hmac.new(self._secret, encoded, hashlib.sha256).hexdigest().encode("ascii")
        return (encoded + b"." + signature + b"v").decode("ascii")

    def _decode_cursor(self, token: str) -> dict[str, Any]:
        try:
            encoded, signature = token.encode("ascii").rsplit(b".", 1)
            if not signature.endswith(b"v"):
                raise ValueError
            signature = signature[:-1]
            expected = hmac.new(self._secret, encoded, hashlib.sha256).hexdigest().encode("ascii")
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            padding = b"=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (UnicodeError, ValueError, json.JSONDecodeError, TypeError):
            raise PublicReadError("invalid_cursor", "Inspector cursor is invalid") from None


__all__ = ["HarnessPublicReadService"]
