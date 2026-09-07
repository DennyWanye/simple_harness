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
    """Bounded ephemeral authority; references never cross request scopes."""

    def __init__(self, *, ttl_seconds: float = 300.0, max_refs: int = 512) -> None:
        self._ttl = max(1.0, float(ttl_seconds))
        self._max = max(1, int(max_refs))
        self._records: dict[str, ContextPageInReference] = {}
        self._active: set[str] = set()
        self.primary_reader = None  # composed Host reader; never an in-memory grant

    def put(self, *, kind: str, source: str, content: str, session_id: str,
            request_id: str, scope_id: str) -> ContextPageInReference:
        self.purge_expired()
        while len(self._records) >= self._max:
            evicted = next(iter(self._records))
            self._records.pop(evicted)
            self._active.discard(evicted)
        ref = ContextPageInReference(
            reference_id=uuid.uuid4().hex,
            kind=str(kind), source=str(source),
            source_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            session_id=str(session_id), request_id=str(request_id),
            scope_id=str(scope_id), content=str(content),
            expires_at=time.monotonic() + self._ttl,
        )
        self._records[ref.reference_id] = ref
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
            self._records.pop(key, None)
            self._active.discard(key)


def build_context_page_in_handler(
    store: ContextPageInStore,
    *,
    execution_context_getter: Callable[[], Any],
    skill_body_getter: Callable[[str], str] | None = None,
):
    async def handle(args: dict[str, Any], task_id: str) -> str:  # noqa: ARG001
        runtime = execution_context_getter()
        if runtime is None:
            return _error("context_scope_missing")
        from deskpet.execution.primary_context_pages import PREFIX, PrimaryContextPageUnavailable
        from deskpet.execution.current_tool_pages import PREFIX as CURRENT_PREFIX
        if isinstance(args.get("reference_id"), str) and args["reference_id"].startswith((PREFIX, CURRENT_PREFIX)):
            if store.primary_reader is None:
                return _error("primary_page_reader_unavailable")
            try:
                return json.dumps(await store.primary_reader(args), ensure_ascii=False)
            except PrimaryContextPageUnavailable as exc:
                return json.dumps({"ok": False, "error_code": str(exc),
                    "public_message": "Requested primary page is unavailable."})
        reference_id = str(args.get("reference_id", "") or "").strip()
        source_hash = str(args.get("source_hash", "") or "").strip()
        ref = store.get(reference_id)
        if ref is None:
            return _error("reference_stale")
        if (ref.session_id != str(getattr(runtime, "session_id", ""))
                or ref.request_id != str(getattr(runtime, "request_id", ""))
                or ref.scope_id != str(getattr(runtime, "scope_id", ""))):
            return _error("reference_scope_denied")
        if source_hash != ref.source_hash:
            return _error("reference_hash_mismatch")
        content = ref.content
        if ref.kind == "skill":
            if skill_body_getter is None:
                return _error("reference_stale")
            try:
                content = str(skill_body_getter(ref.source))
            except Exception:
                return _error("reference_stale")
            if hashlib.sha256(content.encode("utf-8")).hexdigest() != ref.source_hash:
                return _error("reference_stale")
        store.mark_active(ref.reference_id)
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
