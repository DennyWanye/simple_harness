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


@pytest.mark.asyncio
async def test_page_in_receipts_are_durable_payload_free_and_never_change_the_result(tmp_path) -> None:
    import sqlite3

    from deskpet.operation_audit.page_in_receipts import ContextPageInReceiptLedger, reference_ref_hash

    store = ContextPageInStore()
    store.receipt_ledger = ContextPageInReceiptLedger(tmp_path / "operation-audit.db")
    ref = store.put(kind="memory_l3", source="memory:42", content="exact memory SECRET",
                    session_id="s1", request_id="r1", scope_id="p1")
    runtime = SimpleNamespace(session_id="s1", request_id="r1", scope_id="p1",
                              run_id="product-sdk-1", effect_id=SimpleNamespace(value="effect:abc"))
    handler = build_context_page_in_handler(store, execution_context_getter=lambda: runtime)
    args = {"reference_id": ref.reference_id, "source_hash": ref.source_hash}
    assert json.loads(await handler(args, "task"))["ok"] is True
    assert json.loads(await handler({**args, "source_hash": "0" * 64}, "task"))["error"] == "reference_hash_mismatch"
    with sqlite3.connect(tmp_path / "operation-audit.db") as db:
        rows = db.execute("SELECT phase,reference_ref_hash,kind,source_hash,sdk_run_id,effect_id,outcome "
                          "FROM context_page_in_receipts ORDER BY created_at").fetchall()
    assert [r[0] for r in rows] == ["issued", "consumed", "denied"]
    assert {r[1] for r in rows} == {reference_ref_hash(ref.reference_id)}
    assert rows[1][2:] == ("memory_l3", ref.source_hash, "product-sdk-1", "effect:abc", "ok")
    assert rows[2][6] == "reference_hash_mismatch" and rows[0][4:6] == (None, None)
    dumped = json.dumps(rows)
    assert "SECRET" not in dumped and ref.reference_id not in dumped and "memory:42" not in dumped

    # A broken ledger (path is a directory) is logged, never surfaced to the tool.
    broken = ContextPageInStore()
    broken.receipt_ledger = ContextPageInReceiptLedger(tmp_path / "unwritable")
    (tmp_path / "unwritable").mkdir()
    ref2 = broken.put(kind="memory_l3", source="m", content="x", session_id="s1", request_id="r1", scope_id="p1")
    assert broken.receipt_ledger.last_code == "page_in_receipt_unavailable"
    result = json.loads(await build_context_page_in_handler(broken, execution_context_getter=lambda: runtime)(
        {"reference_id": ref2.reference_id, "source_hash": ref2.source_hash}, "task"))
    assert result["ok"] is True and result["content"] == "x"
