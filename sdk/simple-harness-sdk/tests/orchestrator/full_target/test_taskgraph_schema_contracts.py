# SPDX-License-Identifier: Apache-2.0
"""执行图对外合同 Schema 与 Python 边界 codec 对同一正负样本一致（原计划 §4 / 附录 E；第 2 批 T06、T07；
推后第 3 批 U09 加执行过程两份）。

* 十份 Schema 随 SDK 包交付，按名可读，``$id`` 与文件名一致；收敛视图是 v2（多 ``blocked_notifications``）。
* 正样本：``acceptance_assets/taskgraph_schema_examples.json``。五个只读视图（含执行过程主画面与回合详情）的 codec 必收；其余五份
  里带占位哈希的样本只用于 Schema 侧（原计划："八份结构样例的 hash 是测试占位"），codec 可因跨字段
  核对（哈希、同值）拒收——那是 codec 比 Schema 更严，计划允许。
* 负样本：从正样本机械变异（删必填字段、加未知字段、改类型、空串、超长、坏哈希、负数、越界整数、
  非法枚举）。**Schema 拒绝的，codec 必须拒绝**（十份都查）；五个视图 codec 与 Schema 的收拒完全相同。

**改坏检验**：收敛视图 Schema 去掉 ``blocked_notifications`` → 正样本在 Schema 侧不合规 → 变红；
视图 codec 放过未知字段 → 加未知字段的负样本 Schema 拒、codec 收 → 变红。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.contracts.models import ContractError
from agent_orchestrator.graph import schemas
from agent_orchestrator.graph.execution_contracts import PreviewBindingV1
from agent_orchestrator.graph.network_codec import decode as decode_network_document
from agent_orchestrator.graph.notification_contracts import FollowupV1, TaskGraphErrorV1
from agent_orchestrator.graph.structural_diff import TaskGraphDiffV1
from agent_orchestrator.graph.view_contracts import (
    TaskGraphConvergenceViewV2,
    TaskGraphExecutionDetailV1,
    TaskGraphExecutionViewV1,
    TaskGraphExplanationV1,
    TaskGraphViewV1,
)
from agent_orchestrator.testing.schema_oracle import conforms, violations

EXAMPLES = Path(__file__).parents[1] / "acceptance_assets" / "taskgraph_schema_examples.json"
VIEWS = ("taskgraph-view-v1", "taskgraph-explanation-v1", "taskgraph-convergence-view-v2",
         "taskgraph-execution-view-v1", "taskgraph-execution-detail-v1")
CODECS: dict[str, Any] = {
    "followup-v1": FollowupV1.from_json,
    "taskgraph-error-v1": TaskGraphErrorV1.from_json,
    "taskgraph-diff-v1": TaskGraphDiffV1.from_json,
    "network-document-v1": decode_network_document,
    "preview-binding-v1": PreviewBindingV1.from_json,
    "taskgraph-view-v1": TaskGraphViewV1.from_json,
    "taskgraph-explanation-v1": TaskGraphExplanationV1.from_json,
    "taskgraph-convergence-view-v2": TaskGraphConvergenceViewV2.from_json,
    "taskgraph-execution-view-v1": TaskGraphExecutionViewV1.from_json,
    "taskgraph-execution-detail-v1": TaskGraphExecutionDetailV1.from_json,
}


def _examples() -> dict[str, Any]:
    return json.loads(EXAMPLES.read_text())


def _codec_accepts(name: str, value: Any) -> bool:
    try:
        CODECS[name](json.loads(json.dumps(value)))
    except ContractError:
        return False
    return True


def _mutants(value: Any, path: str = "$") -> list[tuple[str, Any]]:
    """每个位置的机械变异：返回 (说明, 整份变异后的文档) 列表。"""
    out: list[tuple[str, Any]] = []

    def put(desc: str, replacement: Any) -> None:
        out.append((f"{path} {desc}", replacement))

    if isinstance(value, dict):
        for key in list(value):
            rest = {k: v for k, v in value.items() if k != key}
            out.append((f"{path}.{key} removed", rest))
            for desc, child in _mutants(value[key], f"{path}.{key}"):
                out.append((desc, {**value, key: child}))
        put("unknown field added", {**value, "unexpected_field": 1})
        put("object replaced by array", [])
    elif isinstance(value, list):
        for index, item in enumerate(value):
            for desc, child in _mutants(item, f"{path}[{index}]"):
                out.append((desc, [*value[:index], child, *value[index + 1:]]))
        put("array replaced by object", {})
    elif isinstance(value, bool):
        put("bool replaced by string", "yes")
        put("bool flipped", not value)
    elif isinstance(value, int):
        put("int replaced by string", "7")
        put("int replaced by bool", True)
        put("int negative", -1)
        put("int beyond 2^53-1", 2**53)
        put("int zero", 0)
    elif isinstance(value, str):
        put("string replaced by int", 7)
        put("string emptied", "")
        put("string over 512", "x" * 513)
        put("string over 2000", "x" * 2001)
        put("string replaced by other word", "NOPE_VALUE")
        if len(value) == 64:
            put("hash uppercased", "A" * 64)
            put("hash shortened", "a" * 63)
    elif value is None:
        put("null replaced by int", 0)
        put("null replaced by string", "x")
    return out


def test_the_eight_schemas_ship_with_the_sdk_and_name_themselves():
    names = set(schemas.SCHEMA_NAMES)
    assert names == set(_examples()) == set(CODECS)
    for name in names:
        schema = schemas.load_schema(name)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].endswith(":" + name.rsplit("-", 1)[1]), (name, schema["$id"])
        assert schema["additionalProperties"] is False
    convergence = schemas.load_schema("taskgraph-convergence-view-v2")
    assert convergence["properties"]["schema_version"] == {"const": 2}
    assert "blocked_notifications" in convergence["required"]
    item = convergence["properties"]["blocked_notifications"]["items"]
    assert set(item["required"]) == {"message_id", "row_version", "kind", "subject_key", "error_code", "attempts"}
    with pytest.raises(KeyError):
        schemas.load_schema("taskgraph-convergence-view-v1")


@pytest.mark.parametrize("name", sorted(CODECS))
def test_positive_samples_conform_to_their_schema(name):
    sample = _examples()[name]
    assert violations(schemas.load_schema(name), sample) == []


@pytest.mark.parametrize("name", VIEWS)
def test_view_codecs_accept_what_the_schema_accepts_and_round_trip(name):
    sample = _examples()[name]
    decoded = CODECS[name](sample)
    assert decoded.to_json() == sample


@pytest.mark.parametrize("name", sorted(CODECS))
def test_what_the_schema_rejects_the_codec_rejects(name):
    schema = schemas.load_schema(name)
    sample = _examples()[name]
    rejected = 0
    disagreements: list[str] = []
    for desc, mutant in _mutants(sample):
        if conforms(schema, mutant):
            continue
        rejected += 1
        if _codec_accepts(name, mutant):
            disagreements.append(desc)
    assert rejected >= 20, f"too few schema-rejected mutants for {name}: {rejected}"
    assert disagreements == [], f"{name}: schema rejects but codec accepts: {disagreements}"


@pytest.mark.parametrize("name", VIEWS)
def test_view_codecs_and_schemas_agree_on_every_mutant(name):
    schema = schemas.load_schema(name)
    sample = _examples()[name]
    disagreements = [desc for desc, mutant in _mutants(sample)
                     if conforms(schema, mutant) != _codec_accepts(name, mutant)]
    assert disagreements == [], f"{name}: {disagreements}"
