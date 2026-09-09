# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HM-TO-A6 事件 AL：本轮证据 id 必须在**第一次**就交到模型手里。

第 13 次整跑 T17（``.local-test-evidence/2026-09-09/native-a6-run13/
primary-ui-hwbyjnym``）：``context_route`` 成功（``continue_active``），回执里只有
``recall_refs: []``，没有任何本轮证据 id。模型于是拿回执自己的
``binding_set_receipt_id``（``2cc4a020-…``）去填 ``task_scope_update.evidence_refs``，
被 ``task_scope_update_refs_outside_scope`` 拒掉 —— 而 ``allowed_evidence_refs`` 与
``current_turn_evidence_ref`` 是在**这条拒绝的公开消息里**才第一次公布的。

工具描述当时明写「没拿到那张表就先发一次最佳载荷，拒绝会公布允许的 id」，模型的思考
里也逐字复述了这套两步流程。可在 32000 窗口上这不是协议，是预算炸弹：第一步那次
被拒的调用逐字带着用户 26 KB 目标（6256 output token），重发那一轮就死在
``sdk_provider_wire_input_budget_exceeded``（floor=27176 > effective=26752）。

本文件锁定：路由进任务时，**被接受的** ``context_route`` 结果就带上本轮
``current_turn_evidence_ref``，且它与拒绝回执会公布的那个 id 逐字相同（同一条 Host
查询）；standalone 路由没有 scope，什么也不带。
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
from deskpet.sdk_adapters.context_route import (
    CURRENT_TURN_EVIDENCE_REF_KEY,
    ContextRouteToolService,
)
from deskpet.sdk_adapters.task_scope_mutation import (
    ClosureRejected,
    TaskScopeUpdateService,
    read_current_turn_evidence_ref,
)
from deskpet.sdk_adapters import task_scope_mutation as tsm
from tests.sdk_adapters import s5b_closure_harness as ch

RUN = "sdk-run-event-al"
SCOPE = "scope-active-1"


# ── 单元：被接受的路由结果带上 id，standalone 不带 ────────────────────────


def _tool_context(effect: str = "effect-al-1", raw: str = "raw-al-1"):
    return SimpleNamespace(
        run_id=RunId(RUN),
        effect_id=SimpleNamespace(value=effect),
        task_execution_envelope=SimpleNamespace(raw_call_id=raw, turn_ordinal=1),
    )


class _FakeBindingStore:
    def __init__(self) -> None:
        self.receipts = {
            SCOPE: SimpleNamespace(
                binding_set_revision=2, receipt_id="bind-al", receipt_hash="b" * 64
            )
        }

    def configured_root(self):
        return SimpleNamespace(canonical_path="/tmp/workspace")

    async def current_receipt(self, task_scope_id: str):
        try:
            return self.receipts[task_scope_id]
        except KeyError as exc:
            raise RuntimeError("workspace_binding_set_not_found") from exc


class _FakeService:
    async def open_task_scope(self, request):
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


class _FakeDisclosureReader:
    async def __call__(self, run_id, package, effect_id):
        return {**package, "source_id": "authorized-source", "source_hash": "f" * 64,
                "read_views": {"RESUME": {"content": "Authorized projection"}}}


@pytest_asyncio.fixture()
async def state_db(tmp_path: Path) -> Path:
    path = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(path)
    return path


def _service(
    state_db: Path,
    *,
    evidence_reader=None,
    binding_store=None,
    effect: str = "effect-al-1",
    raw: str = "raw-al-1",
    disclosure_reader=None,
) -> ContextRouteToolService:
    return ContextRouteToolService(
        service_factory_getter=lambda: SimpleNamespace(
            bind=lambda auth, **kw: _FakeService()
        ),
        binding_store_factory=lambda: binding_store or _FakeBindingStore(),
        binding_append_getter=lambda: None,
        ledger=ContextRouteLedgerStore(state_db),
        tool_context_getter=lambda: _tool_context(effect=effect, raw=raw),
        scope_disclosure_reader=disclosure_reader,
        current_turn_evidence_reader=evidence_reader,
    )


async def _seed_active_route(state_db: Path, store: _FakeBindingStore) -> None:
    """先真的路由进这条 scope，``continue_active`` 才有 active task 可续。"""

    seeded = await _service(
        state_db, binding_store=store, effect="effect-al-0", raw="raw-al-0",
        disclosure_reader=_FakeDisclosureReader(),
    ).handle_context_route({"route": "resume_existing", "task_scope_id": SCOPE})
    assert "context_route_receipt" in seeded


@pytest.mark.asyncio
async def test_accepted_task_route_publishes_this_turns_evidence_ref(
    state_db: Path,
) -> None:
    """事件 AL 主属性：路由进任务，第一次就拿到要引用的 id 和怎么用它。"""

    store = _FakeBindingStore()
    await _seed_active_route(state_db, store)
    asked: list[tuple[str, str]] = []

    async def reader(run_id: str, task_scope_id: str) -> str:
        asked.append((run_id, task_scope_id))
        return "96e7d785-d954-5efe-b38e-6a0567a5285b"

    tool = _service(state_db, evidence_reader=reader, binding_store=store)
    result = await tool.handle_context_route({"route": "continue_active"})
    assert "error" not in result
    assert result[CURRENT_TURN_EVIDENCE_REF_KEY] == "96e7d785-d954-5efe-b38e-6a0567a5285b"
    # 光有 id 不够：T17 手里也有一堆 id，缺的是「哪一个、拿去干什么」。
    next_step = result["current_turn_evidence_next_step"]
    assert "task_scope_update" in next_step and "evidence_refs" in next_step
    assert asked == [(RUN, SCOPE)]


@pytest.mark.asyncio
async def test_standalone_route_publishes_nothing(state_db: Path) -> None:
    """没有 scope 就没有闭合要写，也就没有要引用的证据。"""

    calls: list[tuple[str, str]] = []

    async def reader(run_id: str, task_scope_id: str) -> str:
        calls.append((run_id, task_scope_id))
        return "should-never-be-read"

    tool = _service(state_db, evidence_reader=reader)
    result = await tool.handle_context_route({"route": "direct_standalone"})
    assert CURRENT_TURN_EVIDENCE_REF_KEY not in result
    assert calls == []


@pytest.mark.asyncio
async def test_an_unavailable_reader_only_costs_the_hint(state_db: Path) -> None:
    """读不到就退回事件 AL 之前的结果，绝不影响路由本身。"""

    store = _FakeBindingStore()
    await _seed_active_route(state_db, store)

    async def broken(run_id: str, task_scope_id: str) -> str:
        raise RuntimeError("evidence read unavailable")

    tool = _service(state_db, evidence_reader=broken, binding_store=store)
    result = await tool.handle_context_route({"route": "continue_active"})
    receipt = ContextRouteReceipt.from_json(result["context_route_receipt"])
    assert receipt.task_scope_id == SCOPE
    assert CURRENT_TURN_EVIDENCE_REF_KEY not in result

    plain = await _service(
        state_db, binding_store=store, effect="effect-al-2", raw="raw-al-2",
    ).handle_context_route({"route": "continue_active"})
    assert CURRENT_TURN_EVIDENCE_REF_KEY not in plain


# ── 集成：路由公布的 id 与拒绝会公布的那个逐字相同 ────────────────────────


@pytest.mark.asyncio
async def test_the_published_id_is_the_id_the_rejection_would_have_disclosed(
    tmp_path: Path,
) -> None:
    """两条披露必须是同一条 Host 查询 —— 否则「提前给」等于给错。"""

    env = await ch.bound_run(tmp_path, "sdk-run-al-parity")
    published = await read_current_turn_evidence_ref(env.db_path, env.run_id, ch.SCOPE)
    assert published

    service = TaskScopeUpdateService(
        env.db_path, tool_context_getter=lambda: None, route_ledger=None
    )
    with pytest.raises(ClosureRejected) as rejected:
        await service.apply_closure(
            {
                "outcome": "mutate",
                "base_revision": 1,
                # T17 引用的正是路由回执 id。
                "evidence_refs": ["2cc4a020-cd05-561e-8e15-c87ccae10df2"],
                "idempotency_key": "goal-set-al-1",
                "operations": [
                    {
                        "operation_id": "op-al-1",
                        "kind": "goal.set",
                        "value": "逐字保留用户目标。",
                        "reason_code": "user_requests_verbatim_goal",
                        "evidence_refs": ["2cc4a020-cd05-561e-8e15-c87ccae10df2"],
                    }
                ],
            },
            run_id=env.run_id,
            host_run_id=env.admission.host_run_id,
            task_scope_id=ch.SCOPE,
            subject=ch.SUBJECT,
            source_turn_id=f"sdk-run:{env.run_id}:turn:17",
            reason_code=tsm.MODEL_CLOSURE_REASON_CODE,
        )
    detail = rejected.value.detail
    assert rejected.value.code == "task_scope_update_refs_outside_scope"
    assert detail[CURRENT_TURN_EVIDENCE_REF_KEY] == published
    # 与库里那一行本轮 USER 证据也逐字相同。
    with sqlite3.connect(env.db_path) as db:
        rows = db.execute(
            "SELECT evidence_id FROM foreground_turns WHERE subject=? "
            "ORDER BY enqueue_sequence DESC LIMIT 1",
            (ch.SUBJECT,),
        ).fetchall()
    assert published == rows[0][0]


@pytest.mark.asyncio
async def test_the_reader_fails_closed_on_an_unknown_run(tmp_path: Path) -> None:
    """认不出的 Run / 空 scope 一律返回空串，绝不拿别的 Run 的证据顶上。"""

    env = await ch.bound_run(tmp_path, "sdk-run-al-failclosed")
    assert await read_current_turn_evidence_ref(env.db_path, "some-other-run", ch.SCOPE) == ""
    assert await read_current_turn_evidence_ref(env.db_path, env.run_id, "") == ""
    assert await read_current_turn_evidence_ref(env.db_path, "", ch.SCOPE) == ""
    # 跨 scope 也不行：本轮证据必须绑在这条 Run 的准入 scope 上。
    assert await read_current_turn_evidence_ref(
        env.db_path, env.run_id, "scope-someone-else"
    ) == ""
