# SPDX-License-Identifier: Apache-2.0
"""审阅员提示词把"回复长什么样"讲清楚（用户 2026-10-02）。

真机库 453 次审阅员回复里 75 次因格式被拒（56 次不是单独一个 JSON 对象、14 次多写字段、
4 次文字超长），原来的提示词只有一句话列出字段和取值：没有示例、没说不要代码围栏、没说
长度上限。解码规则没有变——这里钉住的是"提示词说的和解码器收的是同一份东西"。
"""

from __future__ import annotations

import json

import pytest

from agent_orchestrator.assurance.checks import ReviewReply
from agent_orchestrator.assurance.codec import AssuranceError
from agent_orchestrator.assurance.review_input import REVIEW_INSTRUCTIONS
from agent_orchestrator.orchestrator.assurance_review_transport import _FORMAT_FEEDBACK


def _example() -> dict:
    """The one JSON object the instructions show, read the way a model would read it."""

    start = REVIEW_INSTRUCTIONS.index("{", REVIEW_INSTRUCTIONS.index("形状如下"))
    value, _ = json.JSONDecoder().raw_decode(REVIEW_INSTRUCTIONS[start:])
    return value


def test_the_example_in_the_instructions_is_a_reply_the_decoder_accepts() -> None:
    reply = ReviewReply.from_json(_example())
    assert reply.verdict == "ACCEPT"
    assert len(reply.assessments) == 1 and reply.findings == ()


def test_the_example_shows_every_field_and_nothing_else() -> None:
    example = _example()
    assert set(example) == {"schema_version", "verdict", "assessments", "findings"}
    assert set(example["assessments"][0]) == {
        "criterion_id", "verdict", "evidence_ids", "reason", "limitations"
    }
    # 多写一个字段（哪怕值是空的）解码器就拒收——示例不能教它多写。
    with pytest.raises(AssuranceError):
        ReviewReply.from_json({**example, "notes": ""})


@pytest.mark.parametrize("word", (
    # 字段
    "schema_version", "verdict", "assessments", "findings", "criterion_id", "evidence_ids",
    "reason", "limitations", "severity",
    # 取值
    "ACCEPT", "REWORK", "INCONCLUSIVE", "REJECTED", "PASS", "FAIL", "UNKNOWN",
    "BLOCKER", "WARNING", "INFO",
))
def test_every_field_and_every_allowed_value_is_named_and_explained(word: str) -> None:
    assert word in REVIEW_INSTRUCTIONS
    # 每个取值后面跟着"＝含义"，每个字段有一行自己的说明。
    assert f"{word}＝" in REVIEW_INSTRUCTIONS or f"{word}：" in REVIEW_INSTRUCTIONS \
        or f"{word}（" in REVIEW_INSTRUCTIONS, word


@pytest.mark.parametrize("rule", ("代码围栏", "前后不要写", "不要添加"))
def test_the_three_refusals_seen_on_real_runs_are_said_up_front(rule: str) -> None:
    assert rule in REVIEW_INSTRUCTIONS


def test_the_length_limits_in_the_instructions_are_the_decoders() -> None:
    example = _example()
    assessment = example["assessments"][0]

    def with_assessment(**changes):
        return {**example, "assessments": [{**assessment, **changes}]}

    # 理由 2000 字以内、证据标签最多 64 个、局限说明最多 16 条、发现项最多 128 个。
    for number in ("2000", "64", "16", "128"):
        assert number in REVIEW_INSTRUCTIONS, number
    ReviewReply.from_json(with_assessment(reason="理" * 2000))
    with pytest.raises(AssuranceError):
        ReviewReply.from_json(with_assessment(reason="理" * 2001))
    ReviewReply.from_json(with_assessment(evidence_ids=[f"ev-{n}" for n in range(64)]))
    with pytest.raises(AssuranceError):
        ReviewReply.from_json(with_assessment(evidence_ids=[f"ev-{n}" for n in range(65)]))
    ReviewReply.from_json(with_assessment(limitations=[f"l{n}" for n in range(16)]))
    with pytest.raises(AssuranceError):
        ReviewReply.from_json(with_assessment(limitations=[f"l{n}" for n in range(17)]))


def test_a_label_seen_only_in_a_listing_is_said_to_need_a_full_read_first() -> None:
    assert "先整段读" in REVIEW_INSTRUCTIONS


def test_the_retry_hint_for_a_non_json_reply_names_fences_and_surrounding_text() -> None:
    hint = _FORMAT_FEEDBACK["JSON_INVALID"]
    assert "code fence" in hint and "before or after" in hint
