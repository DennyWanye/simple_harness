# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5a black-box tests for the five-route ``context_route`` tool service.

Oracle source: TC-HM-10 rev2 (five-route adjudication, search hits never
authorize) + s5a-context-route-verification-spec.json S5A-S1/S5 subsets.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from simple_harness.contracts import RunId
from simple_harness.execution.context_authority import ContextRouteReceipt

from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
from deskpet.sdk_adapters.context_route import ContextRouteToolService

RUN = "run-route-1"


def _tool_context(effect: str = "effect-1", raw: str = "raw-1", turn: int = 1):
    return SimpleNamespace(
        run_id=RunId(RUN),
        effect_id=SimpleNamespace(value=effect),
        task_execution_envelope=SimpleNamespace(raw_call_id=raw, turn_ordinal=turn),
    )


class _FakeBindingStore:
    def __init__(self, *, root: str | None = "/tmp/workspace") -> None:
        self._root = root
        self.receipts: dict[str, SimpleNamespace] = {}

    def configured_root(self):
        return self._root

    async def current_receipt(self, task_scope_id: str):
        try:
            return self.receipts[task_scope_id]
        except KeyError as exc:
            raise RuntimeError("workspace_binding_set_not_found") from exc


class _FakeService:
    def __init__(self) -> None:
        self.opened: list[str] = []

    async def open_task_scope(self, request):
        self.opened.append(request.scope_ref)
        return {
            "scope_ref": request.scope_ref,
            "resume_package": {
                "task_scope_id": request.scope_ref,
                "binding_set_revision": 2,
                "binding_receipt_hash": "b" * 64,
                "read_views": {"RESUME": {"content": "goal A next step"}},
            },
            "resume_sha256": "e" * 64,
            "drift_report": None,
        }

    async def create_task_scope(self, request):
        return {"scope_ref": "scope-new-1", "revision": 1, "goal": request.goal}

    async def search_task_scopes(self, request):
        return {
            "candidates": [
                {"scope_ref": "scope-a", "title": "Task A", "rank": -1.0}
            ],
            "next_cursor": None,
            "receipt_hash": "c" * 64,
        }


class _FakeBindingAppend:
    def __init__(self, *, manual: bool = False) -> None:
        self.manual = manual
        self.calls: list[dict] = []

    async def append_binding(self, **kwargs):
        self.calls.append(kwargs)
        if self.manual:
            return {"status": "authorization_required"}
        return {"status": "committed", "binding_set_revision": 1}


@pytest_asyncio.fixture()
async def state_db(tmp_path: Path) -> Path:
    path = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(path)
    return path


def _service(
    state_db: Path,
    *,
    factory=None,
    binding_store=None,
    binding_append=None,
    context=None,
) -> ContextRouteToolService:
    service = _FakeService()
    bound = SimpleNamespace(bind=lambda auth, **kw: service) if factory is None else factory
    return ContextRouteToolService(
        service_factory_getter=lambda: bound,
        binding_store_factory=lambda: binding_store or _FakeBindingStore(),
        binding_append_getter=lambda: binding_append,
        ledger=ContextRouteLedgerStore(state_db),
        tool_context_getter=lambda: context or _tool_context(),
    )


def _decision_rows(state_db: Path):
    with sqlite3.connect(state_db) as db:
        return db.execute(
            "SELECT route,origin,effect_id FROM context_route_decisions "
            "WHERE sdk_run_id=? ORDER BY recorded_at",
            (RUN,),
        ).fetchall()


def _invocation_rows(state_db: Path):
    with sqlite3.connect(state_db) as db:
        return db.execute(
            "SELECT verdict,effect_id FROM context_route_tool_invocations "
            "WHERE sdk_run_id=? ORDER BY recorded_at",
            (RUN,),
        ).fetchall()


@pytest.mark.asyncio
async def test_direct_standalone_commits_receipt_and_ledger(state_db: Path) -> None:
    tool = _service(state_db)
    result = await tool.handle_context_route({"route": "direct_standalone"})
    receipt = ContextRouteReceipt.from_json(result["context_route_receipt"])
    assert receipt.run_id == RUN
    assert receipt.raw_call_id == "raw-1"
    assert receipt.effect_id == "effect-1"
    assert receipt.task_scope_id is None
    assert "ok" not in result and "error" not in result
    assert _decision_rows(state_db) == [
        ("direct_standalone", "context_tool", "effect-1")
    ]
    assert _invocation_rows(state_db) == [("accepted", "effect-1")]


@pytest.mark.asyncio
async def test_memory_standalone_is_stable_blocked_not_faked(state_db: Path) -> None:
    tool = _service(state_db)
    result = await tool.handle_context_route({"route": "memory_standalone"})
    assert result["ok"] is False
    assert result["error"]["code"] == "context_route_memory_standalone_unavailable"
    assert _decision_rows(state_db) == []
    assert _invocation_rows(state_db) == [("rejected", "effect-1")]


@pytest.mark.asyncio
async def test_resume_requires_exact_id_search_never_authorizes(
    state_db: Path,
) -> None:
    tool = _service(state_db)
    result = await tool.handle_context_route({"route": "resume_existing"})
    assert result["ok"] is False
    assert result["error"]["code"] == "context_route_exact_task_scope_required"
    assert _decision_rows(state_db) == []


@pytest.mark.asyncio
async def test_resume_exact_open_returns_receipt_and_resume_package(
    state_db: Path,
) -> None:
    binding_store = _FakeBindingStore()
    binding_store.receipts["scope-a"] = SimpleNamespace(
        binding_set_revision=2, receipt_id="bind-a", receipt_hash="b" * 64
    )
    tool = _service(state_db, binding_store=binding_store)
    result = await tool.handle_context_route(
        {"route": "resume_existing", "task_scope_id": "scope-a"}
    )
    receipt = ContextRouteReceipt.from_json(result["context_route_receipt"])
    assert receipt.task_scope_id == "scope-a"
    assert receipt.binding_set_revision == 2
    assert receipt.binding_set_receipt_id == "bind-a"
    assert result["resume_package"]["read_views"]["RESUME"]["content"]
    assert _decision_rows(state_db) == [
        ("resume_existing", "context_tool", "effect-1")
    ]


@pytest.mark.asyncio
async def test_resume_binding_lineage_stale_rejected(state_db: Path) -> None:
    binding_store = _FakeBindingStore()
    binding_store.receipts["scope-a"] = SimpleNamespace(
        binding_set_revision=3, receipt_id="bind-a", receipt_hash="d" * 64
    )
    tool = _service(state_db, binding_store=binding_store)
    result = await tool.handle_context_route(
        {"route": "resume_existing", "task_scope_id": "scope-a"}
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "context_route_binding_lineage_stale"
    assert _decision_rows(state_db) == []


@pytest.mark.asyncio
async def test_continue_active_requires_prior_task_route(state_db: Path) -> None:
    tool = _service(state_db)
    result = await tool.handle_context_route({"route": "continue_active"})
    assert result["ok"] is False
    assert result["error"]["code"] == "context_route_no_active_task_scope"


@pytest.mark.asyncio
async def test_continue_active_uses_latest_task_decision_and_live_head(
    state_db: Path,
) -> None:
    binding_store = _FakeBindingStore()
    binding_store.receipts["scope-a"] = SimpleNamespace(
        binding_set_revision=2, receipt_id="bind-a", receipt_hash="b" * 64
    )
    tool = _service(state_db, binding_store=binding_store)
    resumed = await tool.handle_context_route(
        {"route": "resume_existing", "task_scope_id": "scope-a"}
    )
    assert "context_route_receipt" in resumed

    continuing = ContextRouteToolService(
        service_factory_getter=lambda: SimpleNamespace(
            bind=lambda auth, **kw: _FakeService()
        ),
        binding_store_factory=lambda: binding_store,
        binding_append_getter=lambda: None,
        ledger=ContextRouteLedgerStore(state_db),
        tool_context_getter=lambda: _tool_context(effect="effect-2", raw="raw-2"),
    )
    result = await continuing.handle_context_route({"route": "continue_active"})
    receipt = ContextRouteReceipt.from_json(result["context_route_receipt"])
    assert receipt.task_scope_id == "scope-a"
    assert receipt.binding_set_revision == 2

    mismatch = ContextRouteToolService(
        service_factory_getter=lambda: SimpleNamespace(
            bind=lambda auth, **kw: _FakeService()
        ),
        binding_store_factory=lambda: binding_store,
        binding_append_getter=lambda: None,
        ledger=ContextRouteLedgerStore(state_db),
        tool_context_getter=lambda: _tool_context(effect="effect-3", raw="raw-3"),
    )
    rejected = await mismatch.handle_context_route(
        {"route": "continue_active", "task_scope_id": "scope-other"}
    )
    assert rejected["error"]["code"] == "context_route_active_scope_mismatch"


@pytest.mark.asyncio
async def test_create_new_binds_and_commits(state_db: Path) -> None:
    binding_store = _FakeBindingStore()
    binding_store.receipts["scope-new-1"] = SimpleNamespace(
        binding_set_revision=1, receipt_id="bind-new", receipt_hash="a" * 64
    )
    append = _FakeBindingAppend()
    tool = _service(state_db, binding_store=binding_store, binding_append=append)
    result = await tool.handle_context_route(
        {"route": "create_new", "title": "Write weekly report"}
    )
    receipt = ContextRouteReceipt.from_json(result["context_route_receipt"])
    assert receipt.task_scope_id == "scope-new-1"
    assert append.calls and append.calls[0]["task_scope_id"] == "scope-new-1"


@pytest.mark.asyncio
async def test_create_new_manual_authorization_is_stable_rejection(
    state_db: Path,
) -> None:
    append = _FakeBindingAppend(manual=True)
    tool = _service(state_db, binding_append=append)
    result = await tool.handle_context_route(
        {"route": "create_new", "title": "Write weekly report"}
    )
    assert result["ok"] is False
    assert (
        result["error"]["code"] == "context_route_binding_authorization_required"
    )
    assert _decision_rows(state_db) == []


@pytest.mark.asyncio
async def test_composition_unavailable_is_stable_failure(state_db: Path) -> None:
    tool = ContextRouteToolService(
        service_factory_getter=lambda: None,
        binding_store_factory=lambda: _FakeBindingStore(),
        binding_append_getter=lambda: None,
        ledger=ContextRouteLedgerStore(state_db),
        tool_context_getter=lambda: _tool_context(),
    )
    result = await tool.handle_context_route(
        {"route": "resume_existing", "task_scope_id": "scope-a"}
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "context_route_composition_unavailable"


@pytest.mark.asyncio
async def test_task_scope_search_returns_candidates_without_authority(
    state_db: Path,
) -> None:
    tool = _service(state_db)
    result = await tool.handle_task_scope_search({"query": "以前的 A"})
    assert result["candidates"][0]["scope_ref"] == "scope-a"
    assert "context_route_receipt" not in result
    assert _decision_rows(state_db) == []


def test_context_route_policy_is_context_control() -> None:
    from simple_harness.tools.runtime_catalog import (
        ToolEffectClass,
        ToolRouteRequirement,
    )

    from deskpet.sdk_adapters.tool_authority import (
        SDK_TOOL_EXECUTION_POLICY_OVERRIDES,
    )

    effect, route, scope = SDK_TOOL_EXECUTION_POLICY_OVERRIDES["context_route"]
    assert effect == ToolEffectClass.CONTEXT_CONTROL.value
    assert route == ToolRouteRequirement.FORBIDDEN.value
    assert scope == ToolRouteRequirement.FORBIDDEN.value
    assert "task_scope_search" not in SDK_TOOL_EXECUTION_POLICY_OVERRIDES
