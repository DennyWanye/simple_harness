# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""F-MMD-1：`main.py` 注册的每个 service 名都必须在 `context.py::_VALID_SERVICES` 里。

缺陷形状（`context.py:48` / `:57` 的注释已经记过两次同样的事故）：
`ServiceContext.register()` 对白名单外的名字抛 `ValueError`，而 lifespan 的启动块
外面包着 `try/except`，于是漏登记的名字会被吞成一条 boot warning——
真正的症状要到运行期 `service_context.get(...)` 才炸（UI 弹红条 / 整条链路静默 no-op）。
MM-D2 这一轮新增 `memory_display_invalidation` 时又踩到同一条路径，因此把不变量钉死：

    main.py 里所有 service_context.register(<name>, …) 的 <name>  ⊆  _VALID_SERVICES

解析用 `ast` 读源码，不 import `main.py`——导入它会拉起整个 FastAPI 应用与
所有重依赖。名字来源只有三种，且三种都必须能被静态解析：字符串字面量；
「先把名字装进一个字面量元组/列表、再 for 循环 register」
（`_activate_human_memory_host_ports` 的 4 个 human_memory_*）；
以及「for k, v in <字面量 dict>.items(): register(k, …)」
（companion growth 的 5 个 unavailable 占位服务）。
出现第四种（真正动态的名字）会让本用例失败——那正是需要人来看一眼的时刻。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

MAIN_PY = Path(__file__).resolve().parents[1] / "main.py"
CONTEXT_PY = Path(__file__).resolve().parents[1] / "context.py"

# ServiceContext 上真正的注册入口名（context.py 只有 register()，没有 set()）。
REGISTRATION_METHODS = ("register",)
SERVICE_CONTEXT_NAMES = ("service_context", "sc")


def _string_sequence(node: ast.AST) -> list[str] | None:
    """字面量元组/列表 -> 里面的字符串；否则 None。"""
    if not isinstance(node, (ast.Tuple, ast.List)):
        return None
    values: list[str] = []
    for element in node.elts:
        if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
            return None
        values.append(element.value)
    return values


def _dict_literal_keys(node: ast.AST) -> list[str] | None:
    """字面量 dict -> 它的字符串键；出现非字面量键则 None。"""
    if not isinstance(node, ast.Dict):
        return None
    keys: list[str] = []
    for key in node.keys:
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            return None
        keys.append(key.value)
    return keys


def _literal_name_bundles(tree: ast.AST) -> dict[str, list[str]]:
    """`names = ("a", "b")` / `d = {"a": …}` 形式的赋值，供 for 循环回溯名字。"""
    bundles: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        values = _string_sequence(node.value)
        if values is None:
            values = _dict_literal_keys(node.value)
        if values is not None:
            bundles[target.id] = values
    return bundles


def _loop_bound_names(tree: ast.AST) -> dict[str, list[str]]:
    """for <var> in <字面量元组/列表 或 已知 bundle>: -> <var> 可能取到的字符串。"""
    bundles = _literal_name_bundles(tree)
    bound: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        # for name in ("a", "b") / for name in names
        if isinstance(node.target, ast.Name):
            values = _string_sequence(node.iter)
            if values is None and isinstance(node.iter, ast.Name):
                values = bundles.get(node.iter.id)
            if values is not None:
                bound.setdefault(node.target.id, []).extend(values)
            continue
        # for key, value in <字面量 dict>.items()
        if not (isinstance(node.target, ast.Tuple) and node.target.elts
                and isinstance(node.target.elts[0], ast.Name)):
            continue
        iterator = node.iter
        if not (isinstance(iterator, ast.Call)
                and isinstance(iterator.func, ast.Attribute)
                and iterator.func.attr == "items"
                and isinstance(iterator.func.value, ast.Name)):
            continue
        values = bundles.get(iterator.func.value.id)
        if values is not None:
            bound.setdefault(node.target.elts[0].id, []).extend(values)
    return bound


def _registration_calls(tree: ast.AST) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in REGISTRATION_METHODS:
            continue
        # `tool_registry.register(...)` 之类的同名方法不属于 ServiceContext。
        if not (isinstance(func.value, ast.Name) and func.value.id in SERVICE_CONTEXT_NAMES):
            continue
        calls.append(node)
    return calls


def _registered_service_names() -> tuple[set[str], list[int]]:
    """返回 (静态可解析的名字集合, 无法解析的调用行号)。"""
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"), filename=str(MAIN_PY))
    bound = _loop_bound_names(tree)
    names: set[str] = set()
    unresolved: list[int] = []
    for call in _registration_calls(tree):
        if not call.args:
            unresolved.append(call.lineno)
            continue
        first = call.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.add(first.value)
        elif isinstance(first, ast.Name) and first.id in bound:
            names.update(bound[first.id])
        else:
            unresolved.append(call.lineno)
    return names, unresolved


@pytest.fixture(scope="module")
def registered() -> set[str]:
    names, unresolved = _registered_service_names()
    assert not unresolved, (
        f"main.py 里有无法静态解析的 service_context.register 名字（行 {unresolved}）。"
        " 本用例是白名单的唯一防线，动态名字会绕开它：要么改成字面量，"
        " 要么在这里补一条解析规则。"
    )
    return names


def test_main_registers_a_meaningful_number_of_services(registered: set[str]) -> None:
    """先证明解析真的抓到了东西，否则下面那条断言会因为空集合而假绿。"""
    assert len(registered) >= 80, f"只解析出 {len(registered)} 个注册名，解析规则可能失效了"


def test_every_registered_service_is_whitelisted(registered: set[str]) -> None:
    from context import _VALID_SERVICES

    missing = sorted(registered - set(_VALID_SERVICES))
    assert not missing, (
        "main.py 注册了不在 context.py::_VALID_SERVICES 里的 service："
        f"{missing}。register() 会抛 ValueError，被 lifespan 的 try/except 吞成 boot warning，"
        "运行期 get() 才炸。把它们加进白名单（并加一个同名 dataclass 槽位）。"
    )


def test_the_mm_d2_display_invalidation_slot_is_actually_covered(
    registered: set[str],
) -> None:
    """本用例的自证：MM-D2 新增的那个名字确实走在被检查的路径上。

    把 `memory_display_invalidation` 从 `_VALID_SERVICES` 里删掉，
    `test_every_registered_service_is_whitelisted` 必须由绿转红。
    """
    from context import _VALID_SERVICES

    assert "memory_display_invalidation" in registered
    assert "memory_display_invalidation" in _VALID_SERVICES


def test_registration_entrypoint_name_has_not_drifted() -> None:
    """context.py 若改了注册方法名/新增 set()，上面的 AST 匹配会静默失效。"""
    tree = ast.parse(CONTEXT_PY.read_text(encoding="utf-8"), filename=str(CONTEXT_PY))
    service_context = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == "ServiceContext"
    )
    public_methods = {
        node.name for node in service_context.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    assert "register" in public_methods
    unexpected_writers = public_methods & {"set", "put", "bind", "provide"}
    assert not unexpected_writers, (
        f"ServiceContext 多了写入口 {sorted(unexpected_writers)}，"
        " 请把它加进 REGISTRATION_METHODS，否则本文件的检查会漏掉它。"
    )
