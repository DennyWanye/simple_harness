# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""事件 AH：``grep``/``glob`` 必须接受**文件**路径，拒因必须可行动。

HM-TO-A6 第 12 次尝试 turn 11（证据
``.local-test-evidence/2026-09-09/native-a6-run12/primary-ui-z9j48osx/``）：
F-Z1b 已把 ``a6-fixture/`` 作为第二个根绑好，读闸门也照常放行，但
``grep {"path": ".../qiufen-checklist-a.md", "pattern": "ANCHOR-ALPHA",
"output_mode": "content", "context": 2}`` **连挂 4 次**，
``error_code=tool_failed`` / ``public_message="Tool execution failed."``，
``native.log`` 只有 ``product_tool.failed tool=grep code=tool_failed``、无 traceback。

真因**不是异常**：两个 handler 唯一的目标守卫是 ``if not root.is_dir()``，文件
路径直接落到 ``{"error": "path is not a directory: <path>"}``；这个信封没有
``error_code``/``public_message``，于是 ``sdk_adapters/tools.py::_result`` 走默认
分支压成 ``tool_failed`` + "Tool execution failed."，而 ``ToolResult.failed``
**不携带 value**，payload 里的拒因就此蒸发。模型无从自纠，改用 1 KiB 分页硬读
40 KB 文件，把预算打光。

本用例用**真** ``WorkspaceReadGate``（复用 F-Z1 的生产装配：真 v45 state.db、真
``WorkspaceBindingAuthorityStore``、真路由决定行）钉住四条：

1. 文件路径：闸门放行 + 投影出**包含该路径的那个根**，handler 搜这一个文件；
2. 目录路径：照旧遍历；
3. 根外路径：闸门拒 ``path_outside_workspace_root``，handler 根本不被调用；
4. 根内符号链接指向根外：遍历结果逐条复判，越根结果不出现（F-Z1 遗留 3）。

外加拒因浮现：稳定码 + 有界去路径的 ``public_message`` + ``reason=`` 日志字段。
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest
from simple_harness import CallId
from simple_harness.execution.context_authority import TaskScopeRoute
from simple_harness.tools import ToolOutcome

from deskpet.sdk_adapters import tools as product_tools
from deskpet.sdk_adapters.read_gate import (
    READ_OUTSIDE_REASON,
    project_read_execution_context,
    read_root_projection,
)
from deskpet.tools.capabilities import ToolExecutionContext
from deskpet.tools.code_tools._search_scope import (
    NOT_A_SEARCHABLE_TARGET,
    PATH_NOT_FOUND,
    PATTERN_INVALID,
    PATTERN_REQUIRED,
    SEARCH_ROOT_MISSING,
)
from deskpet.tools.code_tools.glob_tool import glob_tool
from deskpet.tools.code_tools.grep_tool import grep_tool

from tests.sdk_adapters.test_read_tool_call_gate_f_z1 import (
    REQUEST,
    RUN,
    _bound_scope,
    _build,
    _context,
    _record_route,
)

ANCHOR = "ANCHOR-ALPHA"


@contextlib.contextmanager
def _sdk_call(call_id: str = "call-1"):
    token = product_tools._current_call_id.set(CallId(call_id))
    try:
        yield
    finally:
        product_tools._current_call_id.reset(token)


def _fixture_file(workspace: Path) -> Path:
    """A6 夹具的形状：40 KB 量级、锚点在中段。"""

    target = workspace / "qiufen-checklist-a.md"
    filler = "\n".join(f"第 {i} 行 秋分清单占位" for i in range(200))
    target.write_text(f"{filler}\n{ANCHOR} 在这一行\n{filler}\n", encoding="utf-8")
    return target


async def _routed(tmp_path: Path):
    env = await _build(tmp_path)
    scope_id = await _bound_scope(env)
    await _record_route(env, route=TaskScopeRoute.CONTINUE_ACTIVE, scope_id=scope_id)
    return env


def _base_context(call_id: str = "call-1") -> ToolExecutionContext:
    return ToolExecutionContext(
        scope_id="scope",
        session_id="session-z",
        request_id=REQUEST.value,
        run_id=RUN.value,
        call_id=call_id,
    )


# --------------------------------------------------------------------------
# 1. 事件 AH 的原样重放：文件路径 + content + context=2
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grep_on_a_file_path_is_admitted_and_searches_that_one_file(
    tmp_path: Path,
) -> None:
    env = await _routed(tmp_path)
    target = _fixture_file(env.workspace)
    arguments = {
        "context": 2,
        "output_mode": "content",
        "path": str(target),
        "pattern": ANCHOR,
    }

    context = _context()
    assert (
        await env.gate.verify(
            context, "grep", call_id=CallId("call-1"), arguments=arguments
        )
        is None
    )

    async with env.gate.execution_scope(context, "grep", call_id=CallId("call-1")):
        assert read_root_projection() is not None
        projected = project_read_execution_context(_base_context())
        # 投影的是**包含该路径的那个根**（F-Z1b 多根），不是 roots[0]。
        assert Path(str(projected.workspace)).resolve() == env.workspace.resolve()
        out = json.loads(
            grep_tool(dict(arguments), "task", execution_context=projected)
        )

    # 事件 AH 里这里是 {"error": "path is not a directory: ..."}。
    assert "error" not in out
    assert out["target_kind"] == "file"
    assert out["files_scanned"] == 1
    assert out["files_with_matches"] == 1
    lines = out["matches"][str(target)]
    assert any(ANCHOR in entry["text"] for entry in lines)
    # context=2 → 命中行前后各两行都在
    assert len(lines) == 5


@pytest.mark.asyncio
async def test_glob_on_a_file_path_matches_that_one_file(tmp_path: Path) -> None:
    env = await _routed(tmp_path)
    target = _fixture_file(env.workspace)
    arguments = {"pattern": "*.md", "path": str(target)}

    context = _context("call-glob")
    assert (
        await env.gate.verify(
            context, "glob", call_id=CallId("call-glob"), arguments=arguments
        )
        is None
    )
    async with env.gate.execution_scope(context, "glob", call_id=CallId("call-glob")):
        projected = project_read_execution_context(_base_context("call-glob"))
        out = json.loads(
            glob_tool(dict(arguments), "task", execution_context=projected)
        )
    assert out["target_kind"] == "file"
    assert out["files"] == [str(target)]

    # 名字对不上时是"零结果"，不是错误——模型据此知道文件在但不匹配。
    miss = json.loads(glob_tool({"pattern": "*.py", "path": str(target)}))
    assert miss["files"] == []
    assert "error" not in miss


# --------------------------------------------------------------------------
# 2. 目录路径：既有语义不变
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_grep_on_a_directory_path_still_walks_the_tree(tmp_path: Path) -> None:
    env = await _routed(tmp_path)
    _fixture_file(env.workspace)
    (env.workspace / "sub").mkdir()
    (env.workspace / "sub" / "b.md").write_text(f"{ANCHOR}\n", encoding="utf-8")

    arguments = {"path": str(env.workspace), "pattern": ANCHOR}
    context = _context("call-dir")
    assert (
        await env.gate.verify(
            context, "grep", call_id=CallId("call-dir"), arguments=arguments
        )
        is None
    )
    out = json.loads(grep_tool(dict(arguments)))
    assert out["target_kind"] == "dir"
    assert out["files_with_matches"] == 2


# --------------------------------------------------------------------------
# 3. 根外：闸门先拒，handler 不被调用
# --------------------------------------------------------------------------




# --------------------------------------------------------------------------
# 4. F-Z1 遗留 3：根内符号链接指向根外，遍历结果逐条复判
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_symlinks_pointing_out_of_the_root_never_reach_the_results(
    tmp_path: Path,
) -> None:
    """两条真实逃逸路径都要堵上（实测 CPython 3.12 的 ``rglob`` 行为）：

    * 根内**文件**链接指向根外：``rglob('*')`` / ``rglob('**/*.md')`` 直接产出它；
    * 根内**目录**链接指向根外：``**`` 不下钻，但显式 ``escape/*.md`` 会穿过去。

    闸门只校验入参 ``path`` 与 pattern 里的 ``..``，两者都过得去 —— 逐条结果复判
    是唯一的补口（F-Z1 遗留 3）。
    """

    env = await _routed(tmp_path)
    (env.workspace / "inside.md").write_text(f"{ANCHOR} inside\n", encoding="utf-8")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "secret.md").write_text(f"{ANCHOR} secret\n", encoding="utf-8")
    (env.workspace / "escape").symlink_to(outside, target_is_directory=True)
    (env.workspace / "link.md").symlink_to(outside / "secret.md")

    # 闸门放行：不带 path，pattern 里也没有 ``..``。
    for pattern in (ANCHOR, "escape/*.md"):
        assert (
            await env.gate.verify(
                _context("call-link"),
                "grep",
                call_id=CallId("call-link"),
                arguments={"pattern": pattern},
            )
            is None
        )

    # ① 文件链接
    grepped = json.loads(grep_tool({"path": str(env.workspace), "pattern": ANCHOR}))
    assert any(name.endswith("inside.md") for name in grepped["files"])
    assert not any("link.md" in name for name in grepped["files"])
    assert grepped["escaped_results"] >= 1

    globbed = json.loads(glob_tool({"path": str(env.workspace), "pattern": "**/*.md"}))
    assert not any("link.md" in name for name in globbed["files"])
    assert globbed["escaped_results"] >= 1

    # ② 目录链接 + 显式穿越 pattern
    through = json.loads(
        glob_tool({"path": str(env.workspace), "pattern": "escape/*.md"})
    )
    assert through["files"] == []
    assert through["escaped_results"] >= 1

    filtered = json.loads(
        grep_tool(
            {"path": str(env.workspace), "pattern": ANCHOR, "glob": "escape/*.md"}
        )
    )
    assert filtered["files"] == []
    assert filtered["escaped_results"] >= 1


# --------------------------------------------------------------------------
# 5. 拒因浮现：稳定码 + 有界去路径的 public_message + reason 日志字段
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("arguments", "expected"),
    (
        ({"pattern": "x", "path": "__no_such_dir__/__no_such_file__.md"}, PATH_NOT_FOUND),
        ({"pattern": "[bad", "path": "."}, PATTERN_INVALID),
        ({"path": "."}, PATTERN_REQUIRED),
        ({"pattern": "x"}, SEARCH_ROOT_MISSING),
    ),
)
def test_grep_rejections_carry_a_stable_pathless_code(arguments, expected) -> None:
    out = json.loads(grep_tool(dict(arguments)))
    assert out["error_code"] == expected
    code = out["error_code"]
    assert "/" not in code and "~" not in code
    assert code.islower() and code.replace("_", "").isalnum()
    assert out["public_message"]


def test_a_device_target_is_not_a_searchable_target() -> None:
    out = json.loads(grep_tool({"pattern": "x", "path": "/dev/null"}))
    assert out["error_code"] == NOT_A_SEARCHABLE_TARGET


def test_the_rejection_reaches_the_model_instead_of_tool_execution_failed(
    tmp_path: Path,
) -> None:
    raw = grep_tool({"pattern": "x", "path": str(tmp_path / "missing.md")})
    with _sdk_call():
        result = product_tools._result(raw)
    assert result.outcome is ToolOutcome.FAILED
    assert result.error_code == PATH_NOT_FOUND
    assert result.public_message != "Tool execution failed."
    assert "list_directory" in str(result.public_message)


def test_a_codeless_handler_envelope_no_longer_collapses_to_a_bare_message() -> None:
    """事件 AH 的原始信封：没有 error_code，也必须给出有界、去路径的原因。"""

    raw = json.dumps({"error": "path is not a directory: /Users/taiwan/a6/x.md"})
    with _sdk_call():
        result = product_tools._result(raw)
    assert result.error_code == "tool_failed"
    message = str(result.public_message)
    assert message != "Tool execution failed."
    assert "not a directory" in message
    # 路径整体折叠，绝不出现在 public_message 或日志 reason 里。
    assert "/Users/taiwan" not in message
    reason = product_tools.failure_log_reason(raw)
    assert "/Users/taiwan" not in reason
    assert "not a directory" in reason


def test_an_empty_failure_envelope_still_names_a_next_step() -> None:
    with _sdk_call():
        result = product_tools._result({"ok": False})
    assert result.error_code == "tool_failed"
    assert str(result.public_message) == product_tools.NO_REASON_FAILURE_MESSAGE
    assert "do not repeat the identical call" in str(result.public_message)


def test_the_failure_log_reason_keeps_the_exception_class(tmp_path: Path) -> None:
    raw = grep_tool({"pattern": "[bad", "path": str(tmp_path)})
    reason = product_tools.failure_log_reason(raw)
    assert reason.startswith("re.error")
    assert "/" not in reason.split(":")[0]


# 2026-09-26: tests asserting the removed workspace boundary were deleted (plans/2026-09-26-permission-open-by-default); the protected-file rules are covered by tests/permissions/test_protected_paths.py.
