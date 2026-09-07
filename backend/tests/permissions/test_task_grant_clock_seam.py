"""C04-15 回归：TaskGrant 的铸造侧与激活侧必须共用同一个时钟接缝。

裁定见 ``plans/2026-09-07-corpus-c01-local/DECISION-C04-15-TASKGRANT-EXPIRY.md``：
``PreparedAuthorizationRuntime`` 用真实墙钟铸造 ``expires_at``（8h TTL），而
``ProductAuthorizationAdapter`` 用跑道注入的语料场景时钟激活；场景时钟晚于真实时间
超过 8h 时，grant 在激活前必然"过期"。修复只补 ``clock=`` 注入，绝不放宽
``deskpet/product_state/task_grants.py`` 的过期判定。
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import time
from pathlib import Path

import pytest

from deskpet.permissions.policy import AuthorizationPolicyState
from deskpet.permissions.runtime import PreparedAuthorizationRuntime
from deskpet.product_state.database import ProductStateDatabase
from deskpet.product_state.task_grants import (
    DurableTaskGrantAuthority,
    TaskGrantConflict,
)
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.types.task_grants import ResourceSelector, TaskGrant
from deskpet.workflows.effects import PreparedToolCall

TASK_GRANT_TTL_SECONDS = 8 * 60 * 60
DAY = 86400.0
POLICY_GENERATION = 4


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class _PolicyStore:
    """Minimal authorization store: only the policy state is read while planning."""

    def __init__(self, state: AuthorizationPolicyState) -> None:
        self.state = state

    async def get_policy_state(self) -> AuthorizationPolicyState:
        return self.state

    async def get_task_grant(self, task_grant_id: str) -> TaskGrant | None:
        return None


def _context(workspace: Path) -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id=_hash("scope"),
        session_id="session-1",
        request_id="request-1",
        root_run_id="root-1",
        turn_id="turn-1",
        workspace=str(workspace),
        write_scope_root=str(workspace),
        capability_hash=_hash("capability"),
        scope_hash=_hash("scope"),
        provider_plan=("provider-1",),
        run_id="run-1",
        call_id="call-1",
        effect_id=_hash("effect:call-1"),
        trace_id="trace-1",
    )


def _call(path: Path) -> PreparedToolCall:
    return PreparedToolCall.prepare(
        tool_name="write_file",
        stable_call_id="call-1",
        final_params={"path": str(path), "content": "ok"},
        tool_spec_version="v1",
        schema_hash=_hash("schema"),
        permission_policy_version="v1",
        effect_type="staged_file",
        resource_selectors=(ResourceSelector.filesystem(path, "write"),),
    )


def _product_database(tmp_path: Path) -> ProductStateDatabase:
    database = ProductStateDatabase(tmp_path / "sdk-product-state.db")
    database.initialize()
    database.connection.execute(
        "UPDATE authorization_policy_state SET generation=?,updated_at=? "
        "WHERE singleton_id=1",
        (POLICY_GENERATION, 1.0),
    )
    database.connection.commit()
    return database


async def _mint(tmp_path: Path, mint_clock) -> TaskGrant:
    """Mint a TaskGrant exactly the way the capability composition does."""

    runtime = PreparedAuthorizationRuntime(
        _PolicyStore(AuthorizationPolicyState("auto", POLICY_GENERATION, 100.0)),
        clock=mint_clock,
    )
    plan = await runtime.plan_prepared_call(
        call=_call(tmp_path / "note.txt"),
        context=_context(tmp_path),
        permission_category="write_file",
        task_grant_id=None,
        principal_id="user-1",
    )
    assert plan.action == "allow", plan.reason
    assert plan.proposed_task_grant is not None
    return plan.proposed_task_grant


def _activate(database: ProductStateDatabase, grant: TaskGrant, *, now: float):
    """Activate the way ``ProductAuthorizationAdapter.prepare`` does."""

    authority = DurableTaskGrantAuthority(database)
    authority.prepare(grant, now=now)
    return authority.activate(
        grant.task_grant_id,
        version=grant.version,
        policy_generation=grant.policy_generation,
        now=now,
    )


# --------------------------------------------------------------------------
# 时钟接缝的静态断言（修复前 red：函数根本没有 clock= 形参）
# --------------------------------------------------------------------------


def _main_source() -> str:
    import main

    return Path(inspect.getfile(main)).read_text(encoding="utf-8")


def _call_keywords(source: str, func_name: str, callee: str) -> list[list[str]]:
    """Keyword names of every ``callee(...)`` call inside ``func_name``."""

    tree = ast.parse(source)
    found: list[list[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != func_name:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call) and _callee_name(inner.func) == callee:
                found.append([kw.arg for kw in inner.keywords if kw.arg])
    return found


def _callee_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _assert_capability_runtime_clock_seam() -> None:
    import main

    parameters = inspect.signature(main._initialize_capability_runtime).parameters
    assert "clock" in parameters, (
        "_initialize_capability_runtime 必须暴露 clock= 形参，否则铸造侧只能用真实墙钟"
    )
    assert parameters["clock"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["clock"].default is time.time, "生产默认必须仍是真实墙钟"


# --------------------------------------------------------------------------
# T1
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_t1_injected_scenario_clock_mints_and_activates(tmp_path: Path) -> None:
    """+30d 场景时钟下，铸造与激活共用同一时钟 → grant 正常激活。"""

    _assert_capability_runtime_clock_seam()

    scenario_now = time.time() + 30 * DAY
    scenario_clock = lambda: scenario_now  # noqa: E731

    grant = await _mint(tmp_path, scenario_clock)
    assert grant.expires_at == pytest.approx(scenario_now + TASK_GRANT_TTL_SECONDS)

    durable = _activate(_product_database(tmp_path), grant, now=scenario_clock())
    assert durable.status == "active"


@pytest.mark.asyncio
async def test_t1_witness_mixed_clocks_still_expire(tmp_path: Path) -> None:
    """反向见证：铸造用真实墙钟、激活用 +30d 场景时钟 → 仍必须过期。

    这条锁住"没有放宽 task_grants.py 的过期判定"：修复只是统一时钟源。
    """

    scenario_now = time.time() + 30 * DAY

    grant = await _mint(tmp_path, time.time)  # 修复前的铸造侧
    assert grant.expires_at is not None and grant.expires_at < scenario_now

    with pytest.raises(TaskGrantConflict, match="expired before activation"):
        _activate(_product_database(tmp_path), grant, now=scenario_now)


# --------------------------------------------------------------------------
# T2
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offset",
    [-30 * DAY, -DAY, 0.0, DAY, 30 * DAY],
    ids=["minus_30d", "minus_1d", "now", "plus_1d", "plus_30d"],
)
async def test_t2_clock_offset_matrix_all_active(tmp_path: Path, offset: float) -> None:
    """任意场景时钟偏移下，单一时钟源都必须让 grant 保持 active。"""

    _assert_capability_runtime_clock_seam()

    scenario_now = time.time() + offset
    scenario_clock = lambda: scenario_now  # noqa: E731

    grant = await _mint(tmp_path, scenario_clock)
    assert grant.expires_at == pytest.approx(scenario_now + TASK_GRANT_TTL_SECONDS)

    durable = _activate(_product_database(tmp_path), grant, now=scenario_clock())
    assert durable.status == "active"


# --------------------------------------------------------------------------
# T3
# --------------------------------------------------------------------------


def test_t3_clock_is_threaded_through_the_host_composition() -> None:
    """注入贯通性：clock= 一路下传到铸造侧，生产调用点保持真实墙钟。"""

    _assert_capability_runtime_clock_seam()
    source = _main_source()

    for callee in ("PreparedAuthorizationRuntime", "AdmissionTaskGrantRuntime"):
        calls = _call_keywords(source, "_initialize_capability_runtime", callee)
        assert calls, f"_initialize_capability_runtime 未构造 {callee}"
        for keywords in calls:
            assert "clock" in keywords, f"{callee} 未收到注入的 clock"

    policy_calls = _call_keywords(
        source, "_build_product_sdk_runtime_stack", "SdkPreparedAuthorizationPolicy"
    )
    assert policy_calls
    for keywords in policy_calls:
        assert "clock" in keywords, "SdkPreparedAuthorizationPolicy 未收到注入的 clock"

    # 生产启动路径不传 clock → 默认真实墙钟，生产行为零变化。
    tree = ast.parse(source)
    production_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _callee_name(node.func) == "_initialize_capability_runtime"
    ]
    assert production_calls, "main.py 应保留生产调用点"
    for node in production_calls:
        assert not node.keywords, "生产调用点不得注入 clock"


def test_t3_corpus_runway_injects_the_scenario_clock() -> None:
    """跑道两侧（铸造 + 激活）都必须注入同一个场景时钟。"""

    from deskpet.quality import corpus_scoring_session

    source = Path(inspect.getfile(corpus_scoring_session)).read_text(encoding="utf-8")
    for callee in ("_initialize_capability_runtime", "_activate_product_sdk_runtime"):
        calls = _call_keywords(source, "run", callee)
        assert calls, f"跑道未调用 {callee}"
        for keywords in calls:
            assert "clock" in keywords, f"跑道调用 {callee} 时未注入场景时钟"
