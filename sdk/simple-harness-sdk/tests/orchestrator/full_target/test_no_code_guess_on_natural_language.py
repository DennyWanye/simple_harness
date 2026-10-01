# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""契约里的文字不再被猜"像不像代码"（HTN 精简 片 D 第 5 项）。

此前 ``reject_executable`` 对每个字符串做子串匹配（"update "、"select "、"delete from"……），
作用于整份做法 JSON，包括模型写的自由文字——英文一句 "update the README" 就会被拒。条件解释器
只走结构化节点，字符串从来不会被执行，这个匹配没有安全价值，只是在猜自然语言的意思。

留下的是秩序：不许出现可调用对象；条件必须是结构化节点，写成一段字符串或未知操作符照旧拒收。
"""
from __future__ import annotations

import pytest

from agent_orchestrator.contracts import semantic_base
from agent_orchestrator.contracts.htn import parse_condition
from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.contracts.semantic_base import reject_executable

ORDINARY_SENTENCES = (
    "update the README and select the best title",
    "Select three sources, then delete from the draft whatever is not cited",
    "explain what eval( does and why import os is needed in the script",
    "insert into the summary a note on the subprocess module; union all findings",
    "解释 lambda 表达式，并说明 drop table 的风险",
)


@pytest.mark.parametrize("text", ORDINARY_SENTENCES)
def test_ordinary_sentences_are_not_refused(text: str) -> None:
    reject_executable(text, "evidence_requirement")
    reject_executable({"statement": text, "notes": [text]}, "method_contract")


def test_the_substring_table_is_gone() -> None:
    assert not hasattr(semantic_base, "_CODE_MARKERS")
    assert not hasattr(semantic_base, "_looks_like_code")


def test_a_callable_is_still_refused_at_any_depth() -> None:
    with pytest.raises(ContractError, match="callable"):
        reject_executable(len, "value")
    with pytest.raises(ContractError, match="callable"):
        reject_executable({"steps": [{"check": print}]}, "method_contract")


@pytest.mark.parametrize("payload", [
    "truth_value == 'TRUE'",
    {"op": "python", "source": "return True"},
    {"op": "all", "items": [{"op": "sql", "query": "SELECT 1 FROM tasks"}]},
])
def test_a_condition_is_structure_or_it_is_refused(payload: object) -> None:
    with pytest.raises(ContractError):
        parse_condition(payload)


def test_a_constant_argument_is_data_and_may_say_anything() -> None:
    condition = parse_condition({
        "op": "predicate",
        "predicate_ref": {"id": "p", "version": 1, "content_hash": "a" * 64},
        "arguments": {"q": {"op": "constant", "value": "SELECT * FROM secrets"}},
    })
    assert condition is not None
