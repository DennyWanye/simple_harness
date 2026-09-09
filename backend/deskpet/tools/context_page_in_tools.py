# SPDX-License-Identifier: BUSL-1.1
"""Strict request-scoped page-in references for retrieved context."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable


CONTEXT_PAGE_IN_SCHEMA: dict[str, Any] = {
    "name": "context_page_in",
    "description": (
        "Load one exact page reference already prepared for this request. Only a page "
        "reference is accepted: copy reference_id and source_hash verbatim from a truncation "
        "marker carrying page_tool=\"context_page_in\" (fields reference_id / source_hash), or "
        "from a \"[Context page-in reference: id=... hash=...]\" line. The ref of a recall "
        "fragment returned by context_route (fragments[].ref, e.g. \"recall-item:<id>:1\") is a "
        "memory item id, not a page reference; passing it fails. If this request offers no such "
        "reference_id, do not call this tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reference_id": {"type": "string", "minLength": 1,
                "description": "Verbatim reference_id from a page reference prepared for this "
                                "request; never a recall fragment ref such as \"recall-item:<id>:1\"."},
            "source_hash": {"type": "string", "minLength": 64, "maxLength": 64,
                "description": "The source_hash published beside that reference_id."},
        },
        "required": ["reference_id", "source_hash"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class ContextPageInReference:
    reference_id: str
    kind: str
    source: str
    source_hash: str
    session_id: str
    request_id: str
    scope_id: str
    content: str
    expires_at: float


class ContextPageInStore:
    """Bounded ephemeral authority; references never cross request scopes.

    事件 X-2：上限必须同时按**条数**与**字节**设。页内容是模型可能 page-in 的整
    份召回片段/工具结果，单条可以很大；只按 512 条设界时最坏情况能在进程里常驻
    几百 MB（事件 X 的 followup X-F3）。字节预算与条数预算都用同一条"按插入顺序
    淘汰最旧"规则，TTL 不变。单条大于整份预算时不拒绝——该引用正是本次请求刚发布
    给模型的那一条，淘汰掉其余后仍然收下它，下一次 ``put`` 会把它挤走。
    """

    DEFAULT_MAX_BYTES = 8 * 1024 * 1024

    def __init__(self, *, ttl_seconds: float = 300.0, max_refs: int = 512,
                 max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._ttl = max(1.0, float(ttl_seconds))
        self._max = max(1, int(max_refs))
        self._max_bytes = max(1, int(max_bytes))
        self._records: dict[str, ContextPageInReference] = {}
        self._sizes: dict[str, int] = {}
        self._bytes = 0
        self._active: set[str] = set()
        self.primary_reader = None  # composed Host reader; never an in-memory grant
        # Optional durable payload-free issue/consume receipts (operation-audit.db, G1).
        self.receipt_ledger = None

    @property
    def retained_bytes(self) -> int:
        """当前常驻的页内容字节数（UTF-8）。"""
        return self._bytes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def _drop(self, reference_id: str) -> None:
        self._records.pop(reference_id, None)
        self._bytes -= self._sizes.pop(reference_id, 0)
        if self._bytes < 0:  # 防御：任何路径都不允许把预算算成负数
            self._bytes = 0
        self._active.discard(reference_id)

    def put(self, *, kind: str, source: str, content: str, session_id: str,
            request_id: str, scope_id: str) -> ContextPageInReference:
        self.purge_expired()
        content = str(content)
        encoded = content.encode("utf-8")
        size = len(encoded)
        while self._records and (
            len(self._records) >= self._max or self._bytes + size > self._max_bytes
        ):
            self._drop(next(iter(self._records)))
        ref = ContextPageInReference(
            reference_id=uuid.uuid4().hex,
            kind=str(kind), source=str(source),
            source_hash=hashlib.sha256(encoded).hexdigest(),
            session_id=str(session_id), request_id=str(request_id),
            scope_id=str(scope_id), content=content,
            expires_at=time.monotonic() + self._ttl,
        )
        self._records[ref.reference_id] = ref
        self._sizes[ref.reference_id] = size
        self._bytes += size
        if self.receipt_ledger is not None:
            self.receipt_ledger.record_sync(
                phase="issued", reference_id=ref.reference_id, kind=ref.kind, source=ref.source,
                source_hash=ref.source_hash, session_id=ref.session_id, request_id=ref.request_id,
                scope_id=ref.scope_id, sdk_run_id=None, effect_id=None, outcome="issued")
        return ref

    def get(self, reference_id: str) -> ContextPageInReference | None:
        self.purge_expired()
        return self._records.get(reference_id)

    def mark_active(self, reference_id: str) -> None:
        if self.get(reference_id) is None:
            raise KeyError(reference_id)
        self._active.add(reference_id)

    def is_active(self, reference_id: str, *, session_id: str, scope_id: str,
                  request_id: str | None = None) -> bool:
        ref = self.get(reference_id)
        return bool(
            ref is not None and reference_id in self._active
            and ref.session_id == session_id
            and (request_id is None or ref.request_id == request_id)
            and ref.scope_id == scope_id
        )

    def purge_expired(self) -> None:
        now = time.monotonic()
        for key in [k for k, value in self._records.items() if value.expires_at <= now]:
            self._drop(key)


def build_context_page_in_handler(
    store: ContextPageInStore,
    *,
    execution_context_getter: Callable[[], Any],
    skill_body_getter: Callable[[str], str] | None = None,
):
    async def receipt(runtime: Any, *, reference_id: str, kind: str, outcome: str,
                      ref: ContextPageInReference | None = None) -> None:
        # Durable consume/deny receipt keyed by the SDK effect (coverage join);
        # payload-free and never able to change the result already computed.
        ledger = store.receipt_ledger
        if ledger is None:
            return
        effect = getattr(runtime, "effect_id", None)
        run = getattr(runtime, "run_id", None)
        await ledger.record(
            phase="consumed" if outcome == "ok" else "denied", reference_id=reference_id, kind=kind,
            source=ref.source if ref else None, source_hash=ref.source_hash if ref else None,
            session_id=getattr(runtime, "session_id", None), request_id=getattr(runtime, "request_id", None),
            scope_id=getattr(runtime, "scope_id", None),
            sdk_run_id=getattr(run, "value", run), effect_id=getattr(effect, "value", effect), outcome=outcome)

    async def handle(args: dict[str, Any], task_id: str) -> str:  # noqa: ARG001
        runtime = execution_context_getter()
        if runtime is None:
            return _error("context_scope_missing")
        from deskpet.execution.primary_context_pages import PREFIX, PrimaryContextPageUnavailable
        from deskpet.execution.current_tool_pages import PREFIX as CURRENT_PREFIX
        reference_id = str(args.get("reference_id", "") or "").strip()
        if isinstance(args.get("reference_id"), str) and args["reference_id"].startswith((PREFIX, CURRENT_PREFIX)):
            kind = "primary_page" if args["reference_id"].startswith(PREFIX) else "current_tool_page"
            if store.primary_reader is None:
                await receipt(runtime, reference_id=reference_id, kind=kind, outcome="primary_page_reader_unavailable")
                return _error("primary_page_reader_unavailable")
            try:
                result = json.dumps(await store.primary_reader(args), ensure_ascii=False)
            except PrimaryContextPageUnavailable as exc:
                await receipt(runtime, reference_id=reference_id, kind=kind, outcome=str(exc))
                return json.dumps({"ok": False, "error_code": str(exc),
                    "public_message": "Requested primary page is unavailable."})
            await receipt(runtime, reference_id=reference_id, kind=kind, outcome="ok")
            return result
        source_hash = str(args.get("source_hash", "") or "").strip()
        ref = store.get(reference_id)
        if ref is None:
            await receipt(runtime, reference_id=reference_id, kind="unknown", outcome="reference_stale")
            return _error("reference_stale")
        outcome = "ok"
        content = ref.content
        if (ref.session_id != str(getattr(runtime, "session_id", ""))
                or ref.request_id != str(getattr(runtime, "request_id", ""))
                or ref.scope_id != str(getattr(runtime, "scope_id", ""))):
            outcome = "reference_scope_denied"
        elif source_hash != ref.source_hash:
            outcome = "reference_hash_mismatch"
        elif ref.kind == "skill":
            if skill_body_getter is None:
                outcome = "reference_stale"
            else:
                try:
                    content = str(skill_body_getter(ref.source))
                except Exception:
                    outcome = "reference_stale"
                else:
                    if hashlib.sha256(content.encode("utf-8")).hexdigest() != ref.source_hash:
                        outcome = "reference_stale"
        if outcome != "ok":
            await receipt(runtime, reference_id=reference_id, kind=ref.kind, outcome=outcome, ref=ref)
            return _error(outcome)
        store.mark_active(ref.reference_id)
        await receipt(runtime, reference_id=reference_id, kind=ref.kind, outcome="ok", ref=ref)
        return json.dumps({"ok": True, "reference_id": ref.reference_id,
                           "kind": ref.kind, "source": ref.source,
                           "source_hash": ref.source_hash, "content": content},
                          ensure_ascii=False)
    return handle


def register_context_page_in(registry: object, store: ContextPageInStore, *,
                             execution_context_getter: Callable[[], Any],
                             skill_body_getter: Callable[[str], str] | None = None) -> None:
    registry.register(
        name="context_page_in", toolset="memory", schema=CONTEXT_PAGE_IN_SCHEMA,
        handler=build_context_page_in_handler(
            store, execution_context_getter=execution_context_getter,
            skill_body_getter=skill_body_getter,
        ), permission_category="read_file", source="builtin", concurrency_safe=True,
    )


def _error(code: str) -> str:
    return json.dumps({"ok": False, "error": code, "retriable": code == "reference_stale"})


__all__ = ["CONTEXT_PAGE_IN_SCHEMA", "ContextPageInReference", "ContextPageInStore",
           "build_context_page_in_handler", "register_context_page_in"]
