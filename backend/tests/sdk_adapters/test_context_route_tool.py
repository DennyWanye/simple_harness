# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""S5a black-box tests for the five-route ``context_route`` tool service.

Oracle source: TC-HM-10 rev2 (five-route adjudication, search hits never
authorize) + s5a-context-route-verification-spec.json S5A-S1/S5 subsets.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from simple_harness.contracts import RunId
from simple_harness.execution.context_authority import ContextRouteReceipt

from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
from deskpet.sdk_adapters.context_route import ROUTES, ContextRouteToolService

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
        return None if self._root is None else SimpleNamespace(canonical_path=self._root)

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

    async def decide_manual_binding(self, request):
        return await self.binding_append.decide_manual_binding(
            challenge_ref=request.challenge_ref, decision=request.decision)

    async def append_binding(self, request):
        return await self.binding_append.append_binding(task_scope_id=request.scope_ref,
            root=request.root, idempotency_key=request.idempotency_key)

    # HM-TO-A6 incident B: the zero-hit case is the one the real model looped on.
    empty_search = False

    async def search_task_scopes(self, request):
        return {
            "candidates": (
                []
                if self.empty_search
                else [
                    {"scope_ref": "scope-a", "title": "Task A", "rank": -1.0,
                     "source_hash": "e" * 64}
                ]
            ),
            "next_cursor": None,
            "receipt_hash": "c" * 64,
        }


class _FakeBindingAppend:
    def __init__(self, *, manual: bool = False) -> None:
        self.manual = manual
        self.calls: list[dict] = []
        self.decisions: list[dict] = []

    async def append_binding(self, **kwargs):
        self.calls.append(kwargs)
        if self.manual:
            return {"status": "authorization_required", "challenge_ref": "challenge-1"}
        return {"status": "committed", "binding_set_revision": 1}

    async def decide_manual_binding(self, **kwargs):
        self.decisions.append(kwargs)
        return {"status": "bound", "binding_set_revision": 1}


class _FakeDisclosureReader:
    """Protocol fixture only; real source/privacy checks have runtime tests."""

    def __init__(self):
        self.calls = []

    async def __call__(self, run_id, package, effect_id):
        self.calls.append((run_id, package, effect_id))
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
    factory=None,
    binding_store=None,
    binding_append=None,
    context=None,
    disclosure_reader=None,
    user_confirmed=None,
) -> ContextRouteToolService:
    service = _FakeService()
    service.binding_append = binding_append
    bound = SimpleNamespace(bind=lambda auth, **kw: service) if factory is None else factory
    return ContextRouteToolService(
        service_factory_getter=lambda: bound,
        binding_store_factory=lambda: binding_store or _FakeBindingStore(),
        binding_append_getter=lambda: binding_append,
        ledger=ContextRouteLedgerStore(state_db),
        tool_context_getter=lambda: context or _tool_context(),
        scope_disclosure_reader=disclosure_reader,
        user_confirmation_reader=user_confirmed,
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


# 2026-09-10 removed with the Memory SDK: test_memory_standalone_is_stable_blocked_not_faked


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
    reader = _FakeDisclosureReader()
    tool = _service(state_db, binding_store=binding_store, disclosure_reader=reader)
    result = await tool.handle_context_route(
        {"route": "resume_existing", "task_scope_id": "scope-a"}
    )
    receipt = ContextRouteReceipt.from_json(result["context_route_receipt"])
    assert receipt.task_scope_id == "scope-a"
    assert receipt.binding_set_revision == 2
    assert receipt.binding_set_receipt_id == "bind-a"
    assert result["resume_package"]["read_views"]["RESUME"]["content"] == "Authorized projection"
    assert reader.calls[0][::2] == (RUN, "effect-1")
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
    tool = _service(state_db, binding_store=binding_store,
                    disclosure_reader=_FakeDisclosureReader())
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
    assert append.decisions == []
    assert _decision_rows(state_db) == []


@pytest.mark.asyncio
async def test_create_new_manual_user_confirmation_is_the_binding_decision(
    state_db: Path,
) -> None:
    # 2026-09-25 UI 全量点击：人已在弹窗里批准这次"新建任务"，同一次确认就是目录
    # 绑定的人工决定，不再让工具失败后另弹一张绑定卡。
    binding_store = _FakeBindingStore()
    binding_store.receipts["scope-new-1"] = SimpleNamespace(
        binding_set_revision=1, receipt_id="bind-new", receipt_hash="a" * 64
    )
    append = _FakeBindingAppend(manual=True)
    asked: list[tuple[str, str]] = []
    tool = _service(state_db, binding_store=binding_store, binding_append=append,
                    user_confirmed=lambda run_id, effect_id: asked.append((run_id, effect_id)) or True)
    result = await tool.handle_context_route(
        {"route": "create_new", "title": "Write weekly report"}
    )
    receipt = ContextRouteReceipt.from_json(result["context_route_receipt"])
    assert receipt.task_scope_id == "scope-new-1"
    assert asked and asked[0][0] == RUN
    assert append.decisions == [{"challenge_ref": "challenge-1", "decision": "allow"}]


@pytest.mark.asyncio
async def test_create_new_manual_without_user_confirmation_stays_rejected(
    state_db: Path,
) -> None:
    append = _FakeBindingAppend(manual=True)
    tool = _service(state_db, binding_append=append, user_confirmed=lambda *_: False)
    result = await tool.handle_context_route(
        {"route": "create_new", "title": "Write weekly report"}
    )
    assert result["error"]["code"] == "context_route_binding_authorization_required"
    assert append.decisions == []


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
    reader = _FakeDisclosureReader()
    tool = _service(state_db, disclosure_reader=reader)
    result = await tool.handle_task_scope_search({"query": "以前的 A"})
    candidate = result["candidates"][0]
    assert candidate["task_scope_id"] == "scope-a"
    assert candidate["source_hash"] == "f" * 64
    assert candidate["scope_disclosure"]["read_views"]["RESUME"]["content"] == "Authorized projection"
    assert reader.calls[0][::2] == (RUN, "effect-1")
    assert "context_route_receipt" not in result
    assert _decision_rows(state_db) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["resume", "search"])
async def test_scope_disclosure_reader_missing_rejects(state_db, operation):
    binding_store = _FakeBindingStore()
    binding_store.receipts["scope-a"] = SimpleNamespace(
        binding_set_revision=2, receipt_id="bind-a", receipt_hash="b" * 64
    )
    tool = _service(state_db, binding_store=binding_store)
    if operation == "resume":
        result = await tool.handle_context_route(
            {"route": "resume_existing", "task_scope_id": "scope-a"})
    else:
        result = await tool.handle_task_scope_search({"query": "Task A"})
    assert result["error"]["code"] == "scope_disclosure_reader_missing"
    assert "resume_package" not in result and "candidates" not in result
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


@pytest.mark.asyncio
async def test_resume_malformed_pin_gets_guidance_not_stale(state_db: Path) -> None:
    """LLM hash-copy slips (63 chars etc.) get explicit guidance, not a
    misleading stale error (real-provider lane finding, 2026-09-02)."""

    tool = _service(state_db)
    result = await tool.handle_context_route(
        {
            "route": "resume_existing",
            "task_scope_id": "scope-a",
            "expected_source_hash": "6de69ef2" * 8,
        }
    )
    # 64 hex chars is fine shape-wise; now drop one char → guidance.
    bad = await ContextRouteToolService(
        service_factory_getter=lambda: None,
        binding_store_factory=lambda: None,
        binding_append_getter=lambda: None,
        ledger=ContextRouteLedgerStore(state_db),
        tool_context_getter=lambda: _tool_context(effect="effect-badpin"),
    ).handle_context_route(
        {
            "route": "resume_existing",
            "task_scope_id": "scope-a",
            "expected_source_hash": ("6de69ef2" * 8)[:-1],
        }
    )
    assert bad["error"]["code"] == "context_route_expected_source_hash_malformed"
    del result


# -- P1: typed recall withholds unbound Procedures; say so ---------------------
# Oracle source: run-01g review §4 P1 — 12/18 C06 cases requested
# memory_types=[semantic, procedure], got semantic only with no signal, and
# concluded no workflow had ever been saved instead of calling
# procedure_discover. Typed recall's behaviour is correct per
# DECISION-PROCEDURE-USE-CHAIN; only its silence is not.


# 2026-09-10 removed with the Memory SDK: _recall_tool helper


# 2026-09-10 removed with the Memory SDK: _fragment helper


# 2026-09-10 removed with the Memory SDK: test_requested_procedure_with_no_procedure_item_points_at_discovery


# 2026-09-10 removed with the Memory SDK: test_hint_is_present_even_when_typed_recall_returns_nothing_at_all


# 2026-09-10 removed with the Memory SDK: test_returned_procedure_item_needs_no_hint


# 2026-09-10 removed with the Memory SDK: test_unrequested_procedure_type_gets_no_unsolicited_hint


# 2026-09-10 removed with the Memory SDK: test_persona_tells_the_model_what_the_two_host_hint_fields_mean


def test_persona_enumerates_every_context_route_the_tool_accepts() -> None:
    """corpus run-02: PERSONA only ever named create_new / memory_standalone.

    The model then routed a pure text rewrite to ``create_new`` (C10-17), skipped
    ``task_scope_search`` when asked to find earlier work by an old name
    (C05-05/06/15), and opened a duplicate scope instead of ``continue_active``
    (native A6 attempt 4, turn 7). A route the persona never names is a route the
    model has to rediscover from the schema on its own.
    """
    from deskpet.execution.primary_context import PERSONA

    for route in ROUTES:
        assert route in PERSONA, f"PERSONA never names the {route} route"
    # The three behaviours the corpus caught, each pinned to its own wording.
    assert "an active scope needs no search" in PERSONA
    assert "task_scope_search first" in PERSONA
    assert "rewriting" in PERSONA and "not a new project task" in PERSONA
    # Bounded: the persona is protected context on every single request, and the
    # 8192-token provider tier in tests/execution/test_current_tool_megabyte.py
    # only completes while the protected block stays this small.
    assert len(PERSONA) <= 4900


def test_persona_tells_the_model_a_self_rewrite_needs_no_context_tool() -> None:
    """F-NC1（HM-TO-A6 NC-1）：改写用户自己上一句话不得触发任何 context 工具。

    NC-1 要求 T3「把我上一句话改得更简洁一点。」以 ``origin='no_recall'`` 作答，
    但尝试 9 / 10 里 ``deepseek-v4-flash`` 仍然调了 ``context_route``，
    origin 变成 ``context_tool``，负控不成立。原 PERSONA 只说了这类改写
    「不是新建项目任务」（把它从 ``create_new`` 拉回 ``direct_standalone``），
    没说**根本不需要调工具**——待改写的文本本来就在当前上下文里。
    """
    from deskpet.execution.primary_context import PERSONA

    assert "Rewriting or shortening the user's own words needs no context tool or recall." in PERSONA
    # 与既有的 direct_standalone 指引同段，不重复也不矛盾。
    assert "not a new project task" in PERSONA


# ---- HM-TO-A6 incident B：零命中搜索必须给出唯一的下一步 ----------------------
#
# 证据：2026-09-08 native run ``product-sdk-cba43a68…`` turn 7。真实 DeepSeek 用
# 三种措辞把同一条零命中查询发了 10 次（每次都 succeeded、candidates 为空），再用
# ``task_scope_search {}`` 空参 8 次，最终 ``react_max_turns_exceeded`` 打掉整轮。
# 当时系统里**有**一个当前活跃任务，而 ``continue_active`` 根本不需要搜索——工具面
# （描述 + 返回体）从未说过这件事。错误码与 schema 不变，只补可执行文案。


def _empty_search_tool(state_db: Path, **kwargs) -> ContextRouteToolService:
    inner = _FakeService()
    inner.empty_search = True
    inner.binding_append = kwargs.get("binding_append")
    return _service(
        state_db,
        factory=SimpleNamespace(bind=lambda auth, **kw: inner),
        disclosure_reader=_FakeDisclosureReader(),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_empty_search_without_active_task_points_at_create_new(
    state_db: Path,
) -> None:
    tool = _empty_search_tool(state_db)
    result = await tool.handle_task_scope_search({"query": "主清单 清单核对"})
    assert result["candidates"] == []
    action = result["next_action"]
    assert "Do not repeat the same search" in action
    assert "no current active task" in action
    assert "create_new" in action
    # 引导不改变任何路由事实。
    assert _decision_rows(state_db) == []


@pytest.mark.asyncio
async def test_empty_search_with_active_task_names_continue_active(
    state_db: Path,
) -> None:
    binding_store = _FakeBindingStore()
    binding_store.receipts["scope-new-1"] = SimpleNamespace(
        binding_set_revision=1, receipt_id="bind-new", receipt_hash="a" * 64
    )
    tool = _empty_search_tool(
        state_db, binding_store=binding_store, binding_append=_FakeBindingAppend()
    )
    await tool.handle_context_route({"route": "create_new", "title": "核对主清单 A"})

    result = await tool.handle_task_scope_search({"query": "主清单 清单核对"})
    assert result["candidates"] == []
    action = result["next_action"]
    assert "continue_active" in action
    assert "no search is needed" in action
    assert "scope-new-1" in action


@pytest.mark.asyncio
async def test_non_empty_search_keeps_its_contract_unchanged(state_db: Path) -> None:
    """命中时的返回体不新增引导字段（回归护栏）。"""

    tool = _service(state_db, disclosure_reader=_FakeDisclosureReader())
    result = await tool.handle_task_scope_search({"query": "以前的 A"})
    assert result["candidates"]
    assert "next_action" not in result


def test_task_scope_search_description_tells_the_model_to_skip_the_search() -> None:
    """工具描述本身必须说明「续做当前活跃任务无需搜索」。"""

    import re

    source = Path(__file__).resolve().parents[2] / "main.py"
    block = re.search(
        r'name="task_scope_search",\s*\n\s*description=\((.*?)\n\s*\),',
        source.read_text(encoding="utf-8"),
        re.S,
    )
    assert block is not None
    description = block.group(1)
    assert "continue_active" in description
    assert "zero candidates" in description


# ---- HM-TO-A6 incident O：争议值必须带着两个候选值和"先确认"指令进 Context ----
# Oracle source: attempt 4 T22 「那你现在按哪个版本执行这套校对流程？」 得到
# 「按 Python 3.13 执行」。SDK 对 contested head 返回 needs_user_confirmation +
# 空 items + 一个原子 confirmation group（S3 §5.3），Host 只投影 items，模型看到
# 的是"什么都没存过"。


# 2026-09-10 removed with the Memory SDK: _contested_notice helper


# 2026-09-10 removed with the Memory SDK: _conflict_tool helper


def _invocation_detail(state_db: Path, effect_id: str = "effect-1") -> dict:
    with sqlite3.connect(state_db) as db:
        row = db.execute(
            "SELECT detail_json FROM context_route_tool_invocations "
            "WHERE sdk_run_id=? AND effect_id=?", (RUN, effect_id),
        ).fetchone()
    return json.loads(row[0])


# 2026-09-10 removed with the Memory SDK: test_contested_recall_returns_both_candidates_and_a_confirmation_instruction


# 2026-09-10 removed with the Memory SDK: test_contested_recall_records_a_stable_reason_code_without_the_values


# 2026-09-10 removed with the Memory SDK: test_uncontested_recall_carries_no_conflict_notice_or_audit_row


# 2026-09-10 removed with the Memory SDK: test_persona_tells_the_model_what_conflict_notice_means


# -- F-ETR-5: the hint must not depend on the model's `memory_types` -----------
# Oracle source: RUN-RERUN-FLASH-01 review §2 — after MEMORY_TYPE_SELECTION_POLICY
# rule R4 correctly stopped the model requesting a type typed recall cannot
# serve, the hint keyed on that selection stopped firing and C06's
# `procedure_discover` call rate fell 18/19 → 14/19 (in-batch A/B: with the hint
# 4/4 called discover, without it 10/15). The trigger is the request, not the
# selection.


# 2026-09-10 removed with the Memory SDK: _mutable_context helper


# 2026-09-10 removed with the Memory SDK: _hint_tool helper


# 2026-09-10 removed with the Memory SDK: test_workflow_query_gets_the_hint_without_requesting_procedure


# 2026-09-10 removed with the Memory SDK: test_run_inside_a_task_scope_gets_the_hint_for_any_query


# 2026-09-10 removed with the Memory SDK: test_plain_question_outside_any_task_scope_still_gets_no_hint


# 2026-09-10 removed with the Memory SDK: test_a_returned_procedure_still_suppresses_every_trigger


# 2026-09-10 removed with the Memory SDK: test_task_scope_read_failure_never_fails_an_already_successful_recall


# ---- MM-D3：点名任务 ≠ 活跃任务（manual 旅程 run4 T7） -----------------------
#
# 证据：`.local-test-evidence/2026-09-09/native-manual-run4/primary-ui-dys43uzo/`
# Run ``product-sdk-169be262…``。用户说「把这个目录也纳入二号任务的工作范围」，
# 活跃任务是一号。``task_scope_search "二号任务 Task No.2"`` 三个命中里唯一带标题
# 的恰好是一号，三个 ``scope_disclosure.status`` 全是 ``active``（那是 scope 自身
# 的生命周期，不是「谁是本 Run 的活跃任务」）。模型据此选了 ``continue_active``，
# 把 Run 绑到一号；随后 ``resume_existing`` 二号被 ``task_scope_conflict /
# execution_run_scope_conflict`` 拒绝，而该拒绝**只有这两个字符串**——不报被绑
# scope、不报被请求 scope、不给下一步。整轮 MM-4/MM-5 无法执行。
#
# 修法与事故 B 同口径：稳定码与 schema 不动，只补披露与可行动文案。


def _multi_hit_service(*scope_refs: str) -> _FakeService:
    inner = _FakeService()

    async def search_task_scopes(request):
        return {
            "candidates": [
                {"scope_ref": ref, "title": ref, "rank": -1.0, "source_hash": "e" * 64}
                for ref in scope_refs
            ],
            "next_cursor": None,
            "receipt_hash": "c" * 64,
        }

    inner.search_task_scopes = search_task_scopes
    return inner


async def _tool_with_active_scope(
    state_db: Path, *scope_refs: str, resumable: str | None = None
):
    """A service whose Run already committed ``create_new`` → ``scope-new-1``."""

    binding_store = _FakeBindingStore()
    binding_store.receipts["scope-new-1"] = SimpleNamespace(
        binding_set_revision=1, receipt_id="bind-new", receipt_hash="a" * 64
    )
    if resumable is not None:
        binding_store.receipts[resumable] = SimpleNamespace(
            binding_set_revision=2, receipt_id="bind-resume", receipt_hash="b" * 64
        )
    inner = _multi_hit_service(*scope_refs)
    append = _FakeBindingAppend()
    inner.binding_append = append
    tool = _service(
        state_db,
        factory=SimpleNamespace(bind=lambda auth, **kw: inner),
        binding_store=binding_store,
        binding_append=append,
        disclosure_reader=_FakeDisclosureReader(),
    )
    created = await tool.handle_context_route({"route": "create_new", "title": "一号"})
    assert "error" not in created
    return tool


@pytest.mark.asyncio
async def test_search_hits_flag_which_candidate_is_the_runs_active_task(
    state_db: Path,
) -> None:
    tool = await _tool_with_active_scope(state_db, "scope-new-1", "scope-b")
    result = await tool.handle_task_scope_search({"query": "二号任务 Task No.2"})

    flags = {c["task_scope_id"]: c["is_active"] for c in result["candidates"]}
    assert flags == {"scope-new-1": True, "scope-b": False}
    hint = result["route_hint"]
    assert "is_active" in hint and "resume_existing" in hint
    assert "Never continue_active" in hint
    assert "task_scope_id=scope-new-1" in hint
    # 披露不改变任何路由事实：搜索仍不写决策。
    assert _decision_rows(state_db) == [("create_new", "context_tool", "effect-1")]


@pytest.mark.asyncio
async def test_search_hints_say_so_when_no_task_is_active_yet(state_db: Path) -> None:
    inner = _multi_hit_service("scope-a", "scope-b")
    tool = _service(
        state_db,
        factory=SimpleNamespace(bind=lambda auth, **kw: inner),
        disclosure_reader=_FakeDisclosureReader(),
    )
    result = await tool.handle_task_scope_search({"query": "以前的 A"})
    assert all(c["is_active"] is False for c in result["candidates"])
    assert "No task is active in this Run yet" in result["route_hint"]


@pytest.mark.asyncio
async def test_active_flag_degrades_to_false_when_the_ledger_is_unavailable(
    state_db: Path, monkeypatch
) -> None:
    """一条不可用的账本只能让披露退化为「不知道」，不能让搜索失败或标错。"""

    tool = await _tool_with_active_scope(state_db, "scope-new-1", "scope-b")

    async def unavailable():
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(tool._ledger, "latest_task_route_decision", unavailable)
    result = await tool.handle_task_scope_search({"query": "二号任务"})
    assert [c["is_active"] for c in result["candidates"]] == [False, False]
    assert "No task is active in this Run yet" in result["route_hint"]


@pytest.mark.asyncio
async def test_run_scope_conflict_names_both_scopes_and_the_only_next_step(
    state_db: Path, monkeypatch
) -> None:
    """``task_scope_conflict`` 必须点名被绑 scope、被请求 scope 与下一步。"""

    from deskpet.sdk_adapters.context_route import RUN_SCOPE_BOUND_ELSEWHERE
    from deskpet.task_scope.store import TaskScopeConflict

    tool = await _tool_with_active_scope(state_db, "scope-new-1", resumable="scope-b")
    tool._tool_context_getter = lambda: _tool_context(effect="effect-2", raw="raw-2", turn=2)

    async def conflicting(**kwargs):
        raise TaskScopeConflict("execution_run_scope_conflict")

    monkeypatch.setattr(tool._ledger, "record_route_decision", conflicting)
    result = await tool.handle_context_route(
        {"route": "resume_existing", "task_scope_id": "scope-b"}
    )

    error = result["error"]
    # 稳定码不动（S4 store 的冻结码），新增的是可机读的原因码与两个 scope。
    assert error["code"] == "task_scope_conflict"
    assert error["message"] == "execution_run_scope_conflict"
    assert error["reason_code"] == RUN_SCOPE_BOUND_ELSEWHERE
    assert error["bound_task_scope_id"] == "scope-new-1"
    assert error["requested_task_scope_id"] == "scope-b"
    assert "can never rebind" in error["next_step"]
    assert "resume_existing" in error["next_step"]
    assert "本 Run 已绑定 task_scope_id=scope-new-1" in error["next_step_zh"]
    assert "resume_existing task_scope_id=scope-b" in error["next_step_zh"]
    # 拒绝按原样落库，含新的原因码。
    with sqlite3.connect(state_db) as db:
        detail = json.loads(db.execute(
            "SELECT detail_json FROM context_route_tool_invocations "
            "WHERE sdk_run_id=? AND verdict='rejected'", (RUN,)).fetchone()[0])
    assert detail["reason_code"] == RUN_SCOPE_BOUND_ELSEWHERE
    assert detail["bound_task_scope_id"] == "scope-new-1"


def test_persona_routes_a_named_task_that_is_not_the_active_one() -> None:
    """PERSONA 必须一句话说清「点名任务 ≠ 活跃任务」的路由。"""

    from deskpet.execution.primary_context import PERSONA

    assert "For any other task the user names" in PERSONA
    assert "never continue_active" in PERSONA
    assert "irreversibly" in PERSONA
    # 事故 B 与 F-NC1 钉住的既有口径不得被这次压缩改掉。
    assert "an active scope needs no search" in PERSONA
    assert "Rewriting or shortening the user's own words needs no context tool or recall." in PERSONA
    assert len(PERSONA) <= 4900


def test_context_route_description_names_the_named_task_rule() -> None:
    """工具描述本身也要说明这条规则（模型不一定读得到 PERSONA 的每一句）。"""

    import re

    source = Path(__file__).resolve().parents[2] / "main.py"
    route_block = re.search(
        r'name="context_route",\s*\n\s*description=\((.*?)\n\s*\),',
        source.read_text(encoding="utf-8"), re.S)
    assert route_block is not None
    assert "is_active=false" in route_block.group(1)
    assert "can never rebind" in route_block.group(1)

    search_block = re.search(
        r'name="task_scope_search",\s*\n\s*description=\((.*?)\n\s*\),',
        source.read_text(encoding="utf-8"), re.S)
    assert search_block is not None
    assert "is_active" in search_block.group(1)
