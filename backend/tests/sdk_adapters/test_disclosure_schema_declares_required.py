"""三跳披露工具：发布的 schema 必须如实声明处理器强制的必填字段。

S5b 终验实测：`tool_describe` / `tool_activate` 的 schema 不声明 `required`，
处理器却强制校验并以 `missing_required_argument` 拒绝。依赖 schema 的模型因此
反复踩空——DeepSeek 连挂 10 次 `tool_activate:missing_required_argument` 直到
撞上重复护栏，决定性场景取不到证。

省略 `required` 原本的理由（缺项会被冻结 SDK 判 driver_failed、打死整个 Run）
已在本增量失效：`ProductToolsAdapter.validate` 接住该异常并转成可恢复拒绝。
本用例同时钉死这两件事——否则恢复 `required` 就会变成回归。
"""

from __future__ import annotations

import inspect
import json

import pytest

from deskpet.sdk_adapters.tools import ProductToolsAdapter
from deskpet.tools.tool_search import (
    _ACTIVATE_SCHEMA,
    _DESCRIBE_SCHEMA,
    _SCHEMA as _SEARCH_SCHEMA,
)


def _required(schema) -> list[str]:
    return list(schema["parameters"].get("required") or [])


def test_activate_schema_declares_all_enforced_fields() -> None:
    assert set(_required(_ACTIVATE_SCHEMA)) == {
        "capability_id",
        "schema_hash",
        "describe_nonce",
    }


def test_describe_schema_declares_its_enforced_field() -> None:
    assert _required(_DESCRIBE_SCHEMA) == ["capability_id"]


def test_search_schema_declares_its_enforced_field() -> None:
    """tool_search 的处理器要求 query 非空——schema 同样要说。"""
    assert _required(_SEARCH_SCHEMA) == ["query"]


@pytest.mark.parametrize(
    "schema",
    [_ACTIVATE_SCHEMA, _DESCRIBE_SCHEMA, _SEARCH_SCHEMA],
    ids=["activate", "describe", "search"],
)
def test_every_required_field_is_a_declared_property(schema) -> None:
    props = schema["parameters"]["properties"]
    for name in _required(schema):
        assert name in props, f"{name} 声明为必填却不在 properties 里"


def test_missing_arguments_stay_recoverable_not_run_fatal() -> None:
    """恢复 required 的前提：缺参必须被 Host 接住，而不是打死整个 Run。

    这条前提一旦回退，上面的 required 声明就会重新变成 Run 杀手。
    """
    src = inspect.getsource(ProductToolsAdapter.validate)
    assert "MalformedToolArgumentsError" in src
    assert "except" in src
    assert "return tool" in src, "validate 必须返回 Tool 让包装层接管，而非让异常穿出"


def test_activate_handler_still_rejects_missing_fields() -> None:
    """schema 声明了不代表处理器可以不校验——两道都要在。"""
    from deskpet.tools import tool_search as ts

    src = inspect.getsource(ts)
    assert '"capability_id", "schema_hash", "describe_nonce"' in src
    assert "_missing_argument_rejection" in src
