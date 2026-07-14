import json
from types import SimpleNamespace

import pytest

from deskpet.tools.context_page_in_tools import (
    ContextPageInStore,
    build_context_page_in_handler,
)


@pytest.mark.asyncio
async def test_page_in_is_exact_and_request_scope_bound() -> None:
    store = ContextPageInStore()
    ref = store.put(kind="memory_l3", source="memory:42", content="exact memory",
                    session_id="s1", request_id="r1", scope_id="p1")
    runtime = SimpleNamespace(session_id="s1", request_id="r1", scope_id="p1")
    handler = build_context_page_in_handler(store, execution_context_getter=lambda: runtime)
    result = json.loads(await handler({"reference_id": ref.reference_id,
                                       "source_hash": ref.source_hash}, "task"))
    assert result["ok"] is True
    assert result["content"] == "exact memory"
    assert store.is_active(ref.reference_id, session_id="s1", request_id="r1", scope_id="p1")

    runtime.request_id = "other"
    denied = json.loads(await handler({"reference_id": ref.reference_id,
                                       "source_hash": ref.source_hash}, "task"))
    assert denied == {"ok": False, "error": "reference_scope_denied", "retriable": False}


@pytest.mark.asyncio
async def test_skill_page_in_revalidates_body_hash_and_fails_closed_when_stale() -> None:
    body = {"value": "skill body v1"}
    store = ContextPageInStore()
    ref = store.put(kind="skill", source="review", content=body["value"],
                    session_id="s1", request_id="r1", scope_id="p1")
    runtime = SimpleNamespace(session_id="s1", request_id="r1", scope_id="p1")
    handler = build_context_page_in_handler(
        store, execution_context_getter=lambda: runtime,
        skill_body_getter=lambda name: body["value"],
    )
    body["value"] = "skill body v2"
    result = json.loads(await handler({"reference_id": ref.reference_id,
                                       "source_hash": ref.source_hash}, "task"))
    assert result == {"ok": False, "error": "reference_stale", "retriable": True}
    assert not store.is_active(ref.reference_id, session_id="s1", request_id="r1", scope_id="p1")


@pytest.mark.asyncio
async def test_page_in_rejects_missing_scope_and_forged_hash() -> None:
    store = ContextPageInStore()
    ref = store.put(kind="memory_l3", source="m", content="secret",
                    session_id="s", request_id="r", scope_id="p")
    missing = build_context_page_in_handler(store, execution_context_getter=lambda: None)
    assert json.loads(await missing({"reference_id": ref.reference_id,
                                     "source_hash": ref.source_hash}, "t"))["error"] == "context_scope_missing"
    scoped = build_context_page_in_handler(
        store,
        execution_context_getter=lambda: SimpleNamespace(session_id="s", request_id="r", scope_id="p"),
    )
    forged = json.loads(await scoped({"reference_id": ref.reference_id,
                                      "source_hash": "0" * 64}, "t"))
    assert forged["error"] == "reference_hash_mismatch"
    assert not store.is_active(ref.reference_id, session_id="s", request_id="r", scope_id="p")
