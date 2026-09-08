"""G2: search/open access receipts name the SDK effect/run that performed the access."""
from __future__ import annotations

import json
import sqlite3

import pytest

from deskpet.memory.human_memory_service import OpenTaskScopeRequest, SearchTaskScopesRequest
from deskpet.task_scope.protocol import TaskScopeProtocolError
from deskpet.task_scope.search import TaskScopeSearchError, TaskScopeSearchStore
from tests.task_scope.test_projections_search import _v40_db


def _receipts(db_path):
    with sqlite3.connect(db_path) as db:
        return [json.loads(row[0]) for row in db.execute(
            "SELECT receipt_json FROM task_scope_search_access_receipts ORDER BY created_at, receipt_id")]


@pytest.mark.asyncio
async def test_tool_path_receipts_carry_effect_refs_and_human_path_stays_null(tmp_path) -> None:
    db_path, store = await _v40_db(tmp_path)
    await store.create_task_scope(task_scope_id="allowed", subject="actor-1", title="Alpha launch")
    search = TaskScopeSearchStore(db_path)
    await search.rebuild_index()
    base = dict(subject="actor-1", allowed_scope_ids=["allowed"], query="Alpha", limit=10)
    tool_1 = {"effect_id": "effect:1", "sdk_run_id": "product-sdk-1"}
    tool_2 = {"effect_id": "effect:2", "sdk_run_id": "product-sdk-1"}
    human = await search.search(**base)
    first = await search.search(**base, access=tool_1)
    second = await search.search(**base, access=tool_2)
    assert human.receipt_hash != first.receipt_hash != second.receipt_hash
    await search.open_exact(subject="actor-1", allowed_scope_ids=["allowed"], task_scope_id="allowed",
                            expected_source_hash=first.candidates[0].source_hash, access=tool_1)
    receipts = _receipts(db_path)
    assert [r["schema_version"] for r in receipts] == [2, 2, 2, 2]
    assert [(r["operation"], r["effect_id"], r["sdk_run_id"]) for r in receipts] == [
        ("search", None, None), ("search", "effect:1", "product-sdk-1"),
        ("search", "effect:2", "product-sdk-1"), ("open", "effect:1", "product-sdk-1"),
    ]
    assert all(set(r) == {"schema_version", "operation", "subject", "request_hash", "result_hash", "effect_id", "sdk_run_id"} for r in receipts)
    assert "Alpha" not in json.dumps(receipts)
    # Identical tool-path access under the same effect is one receipt (idempotent).
    await search.search(**base, access=tool_1)
    assert len(_receipts(db_path)) == 4
    for bad in ({"effect_id": "effect:3"}, {"effect_id": "effect:3", "sdk_run_id": "r", "purpose": "x"}):
        with pytest.raises(TaskScopeSearchError, match="human_memory_search_access_ref_invalid"):
            await search.search(**base, access=bad)
    with pytest.raises(TaskScopeProtocolError, match="effect_id_invalid"):
        await search.search(**base, access={"effect_id": "", "sdk_run_id": "r"})
    assert len(_receipts(db_path)) == 4  # rejected access refs never write a receipt


def test_request_dtos_validate_access_refs_together() -> None:
    assert SearchTaskScopesRequest("q", effect_id="effect:1", sdk_run_id="run").effect_id == "effect:1"
    assert OpenTaskScopeRequest("scope").effect_id is None
    with pytest.raises(ValueError):
        SearchTaskScopesRequest("q", effect_id="effect:1")
    with pytest.raises(ValueError):
        OpenTaskScopeRequest("scope", sdk_run_id="run")
