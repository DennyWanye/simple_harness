"""不可变的完成映射 / 完成范围行，读出时要和它们的身份列对得上（2026-10-03 迁到产品同形世界）。

映射行由人在确认页确认写下，范围行由产品主循环提交第一版计划时冻结（:func:`plan_committed`）。
磁盘上的字节被改坏是外界真会发生的事（分诊裁决①b1）：这里先拆掉不可变触发器、再用 SQL 改字节，
模拟一次绕过数据库自身保护的损坏，断言读侧按名拒绝、不写任何东西。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from agent_orchestrator.contracts.operation_completion import OccurrenceCompletionScopeV1
from agent_orchestrator.storage.operation_completion_store import OperationCompletionStore
from agent_orchestrator.storage.store import StoreConflict
from simple_harness.contracts import canonical_json

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from publish_world import plan_committed, root_scope  # noqa: E402


def _remove_immutable_trigger(connection, trigger: str) -> None:
    """磁盘损坏模拟：只在这个测试库里拆掉触发器，产品的触发器不变。"""

    connection.execute(f"DROP TRIGGER {trigger}")  # noqa: S608 - fixed test-only names


def _scoped(tmp_path, body) -> None:
    async def run() -> None:
        async with plan_committed(tmp_path) as case:
            body(case, OperationCompletionStore(case.store), root_scope(case))

    asyncio.run(run())


def test_completion_store_reads_untampered_real_spec_and_scope_rows(tmp_path) -> None:
    def body(case, completion, scope_row) -> None:
        document = scope_row["document"]
        spec_row = completion.get_spec_exact(case.mission_id, int(document.requirements_ref.revision),
                                             document.requirements_ref.content_hash)
        assert spec_row is not None
        assert spec_row["spec_hash"] == document.spec_hash == spec_row["document"].content_hash()
        reread = completion.get_scope_exact(case.mission_id, document.plan_ref.revision, document.occurrence_id)
        assert reread is not None and reread["scope_hash"] == reread["document"].content_hash()

    _scoped(tmp_path, body)


def test_completion_store_rejects_scope_document_with_wrong_stored_hash_without_writes(tmp_path) -> None:
    def body(case, completion, scope_row) -> None:
        document = scope_row["document"]
        raw = document.to_json()
        raw["content_criterion_ids"] = []
        _remove_immutable_trigger(case.store.connection, "operation_completion_scopes_immutable_update")
        case.store.connection.execute("UPDATE operation_completion_scopes SET document_json=? WHERE scope_id=?",
                                      (canonical_json(raw), scope_row["scope_id"]))
        before = case.store.connection.total_changes
        events_before = tuple(case.store.list_events(case.mission_id))
        with pytest.raises(StoreConflict, match="scope"):
            completion.get_scope_exact(case.mission_id, document.plan_ref.revision, document.occurrence_id)
        assert case.store.connection.total_changes == before
        assert tuple(case.store.list_events(case.mission_id)) == events_before

    _scoped(tmp_path, body)


def test_completion_store_rejects_scope_identity_even_when_attacker_rehashes_document(tmp_path) -> None:
    def body(case, completion, scope_row) -> None:
        document = scope_row["document"]
        raw = document.to_json()
        raw["occurrence_id"] = "corrupt-occurrence"
        corrupt = OccurrenceCompletionScopeV1.from_json(raw)
        _remove_immutable_trigger(case.store.connection, "operation_completion_scopes_immutable_update")
        case.store.connection.execute(
            "UPDATE operation_completion_scopes SET document_json=?,scope_hash=? WHERE scope_id=?",
            (canonical_json(corrupt.to_json()), corrupt.content_hash(), scope_row["scope_id"]))
        before = case.store.connection.total_changes
        with pytest.raises(StoreConflict, match="scope"):
            completion.get_scope_exact(case.mission_id, document.plan_ref.revision, document.occurrence_id)
        assert case.store.connection.total_changes == before

    _scoped(tmp_path, body)


def test_completion_store_rejects_tampered_real_approved_spec_payload(tmp_path) -> None:
    def body(case, completion, scope_row) -> None:
        spec_hash = scope_row["document"].spec_hash
        row = case.store.connection.execute(
            "SELECT spec_id, document_json FROM operation_completion_specs WHERE mission_id=? AND spec_hash=?",
            (case.mission_id, spec_hash)).fetchone()
        assert row is not None
        raw = json.loads(str(row["document_json"]))
        raw["effects"][0]["required_milestone"] = "FILE_PUBLISHED"
        _remove_immutable_trigger(case.store.connection, "operation_completion_specs_immutable_update")
        case.store.connection.execute("UPDATE operation_completion_specs SET document_json=? WHERE spec_id=?",
                                      (canonical_json(raw), row["spec_id"]))
        before = case.store.connection.total_changes
        with pytest.raises(StoreConflict, match="Spec"):
            completion.get_spec_exact(case.mission_id, int(raw["requirements_ref"]["revision"]),
                                      str(raw["requirements_ref"]["content_hash"]))
        assert case.store.connection.total_changes == before

    _scoped(tmp_path, body)
