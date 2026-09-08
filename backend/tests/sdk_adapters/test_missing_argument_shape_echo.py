# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HM-TO-A6 incident B：缺必填参数的回执必须回显参数形状。

证据：2026-09-08 native run ``product-sdk-cba43a68…``（
``.local-test-evidence/2026-09-08/native-a6-b3682fe1/``）turn 7。真实 DeepSeek 连发
8 次 ``task_scope_search {}`` 与 6 次 ``task_scope_update {}``（其中一次带被 XML
噪声污染的 ``outcome``），每次都收到同一句「see the tool description for the
expected values」，最终 ``react_max_turns_exceeded`` 打掉整轮。

参照 ``procedure_use``（bcd3bb15）的裁决：稳定码与 schema 都不动，只把**已发布
schema 里已有的形状**（类型 / 枚举 / 最小值 / 数组元素必填项）回显进拒绝文案，
让下一轮就能补齐。
"""

from __future__ import annotations

import pytest
from simple_harness import CallId, RequestId, RunId
from simple_harness.tools import CancellationToken, ToolCall, ToolContext, ToolOutcome

from deskpet.sdk_adapters.task_scope_mutation import TASK_SCOPE_UPDATE_SCHEMA
from deskpet.sdk_adapters.tools import (
    MISSING_ARGUMENT_ERROR_CODE,
    ProductToolRegistration,
    ProductToolsAdapter,
    _expected_arguments_hint,
    _sdk_tool,
)

RUN = RunId("product-sdk-b-shape")


async def _invoke(schema: dict, arguments: dict, *, name: str = "task_scope_update"):
    async def handler(_arguments, _context):  # pragma: no cover - never reached
        raise AssertionError("handler must not see invalid arguments")

    tools = ProductToolsAdapter(
        (
            _sdk_tool(
                ProductToolRegistration(
                    name=name,
                    description=f"{name} under test",
                    input_schema=schema,
                    handler=handler,
                    dispatch_kind="async",
                    permission_category="read_file",
                    metadata={"source": "builtin", "version": "v1"},
                    projectless_admission="safe",
                )
            ),
        )
    )
    call_id = CallId("call-shape-1")
    return await tools.invoke(
        ToolCall(call_id, name, arguments),
        ToolContext(RUN, RequestId("request-shape"), CancellationToken(), {},
                    call_id=call_id),
    )


@pytest.mark.asyncio
async def test_task_scope_update_empty_call_echoes_every_missing_shape() -> None:
    """事故中一模一样的 ``task_scope_update {}``。"""

    result = await _invoke(dict(TASK_SCOPE_UPDATE_SCHEMA), {})
    assert result.outcome is ToolOutcome.FAILED
    # 稳定码不变。
    assert result.error_code == MISSING_ARGUMENT_ERROR_CODE
    message = str(result.public_message)
    assert "outcome, base_revision, evidence_refs, idempotency_key" in message
    # 事故里那句无从下手的兜底文案已经不在了。
    assert "see the tool description for the expected values" not in message
    # 形状：枚举、整数下界、数组元素类型与最小项数。
    assert 'outcome (string, one of "mutate"|"no_mutation")' in message
    assert "base_revision (integer, min 1)" in message
    assert "evidence_refs (array, of string, min items 1)" in message
    assert "idempotency_key (string, min length 1)" in message


@pytest.mark.asyncio
async def test_partial_call_echoes_only_the_missing_arguments() -> None:
    """事故 turn 21：模型补了 outcome、其余三个仍缺。"""

    result = await _invoke(dict(TASK_SCOPE_UPDATE_SCHEMA), {"outcome": "mutate"})
    assert result.error_code == MISSING_ARGUMENT_ERROR_CODE
    message = str(result.public_message)
    assert "base_revision, evidence_refs, idempotency_key" in message
    assert "outcome (" not in message


def test_operations_item_shape_names_the_goal_set_fields() -> None:
    """要求 (b)：``operations`` 的元素必填项要能从工具面上读出来。"""

    hint = _expected_arguments_hint(
        {"properties": dict(TASK_SCOPE_UPDATE_SCHEMA["properties"])}, ["operations"]
    )
    assert "operations (array, of object" in hint
    for field in ("operation_id", "kind", "value", "reason_code", "evidence_refs"):
        assert field in hint


def test_hint_is_empty_when_the_schema_cannot_describe_the_argument() -> None:
    assert _expected_arguments_hint({"properties": {}}, ["query"]) == ""
    assert _expected_arguments_hint({}, ["query"]) == ""


def test_hint_is_bounded_so_a_wide_schema_cannot_flood_the_tool_message() -> None:
    schema = {
        "properties": {
            f"field_{index}": {"type": "string", "minLength": 1}
            for index in range(200)
        }
    }
    hint = _expected_arguments_hint(schema, list(schema["properties"]))
    assert len(hint) <= 600
    assert hint.endswith("…")


@pytest.mark.asyncio
async def test_non_missing_schema_violation_keeps_its_own_stable_code() -> None:
    """类型/枚举等其余违规仍走 ``invalid_tool_arguments``（回归护栏）。"""

    from deskpet.sdk_adapters.tools import INVALID_ARGUMENTS_ERROR_CODE

    result = await _invoke(
        dict(TASK_SCOPE_UPDATE_SCHEMA),
        {
            "outcome": "not_a_valid_enum_member",
            "base_revision": 1,
            "evidence_refs": ["e1"],
            "idempotency_key": "k1",
        },
    )
    assert result.outcome is ToolOutcome.FAILED
    assert result.error_code == INVALID_ARGUMENTS_ERROR_CODE
