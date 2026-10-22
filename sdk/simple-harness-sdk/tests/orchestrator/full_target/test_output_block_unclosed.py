# SPDX-License-Identifier: Apache-2.0
"""2026-10-22 Sol 复验局：模型把 <result_envelope>{…} 写完整了却漏掉收尾标签，原来被判"没有信封块"，
连续两次后规划器把这一步换掉。开了标签、块内是一个完整 JSON 对象、后面没有别的东西——就收下；
对象后面还有东西或对象没写完，才按"开了没关"打回，并把缺的标签名告诉模型。"""

import pytest

from agent_orchestrator.runtime.output_blocks import (
    BlockError,
    extract_block,
    outside_text,
    repair_hint,
)


def test_an_unclosed_block_with_one_complete_object_is_accepted():
    text = '说明\n<result_envelope>{"outcome": "blocked", "claims": [{"a": 1}]}'
    assert extract_block(text, "result_envelope") == {"outcome": "blocked", "claims": [{"a": 1}]}
    assert outside_text(text, "result_envelope") == "说明"
    assert extract_block('<x>{"a": 1}\n  ', "x") == {"a": 1}


@pytest.mark.parametrize(
    "bad",
    [
        '<x>{"a": 1} 还有话',  # 对象后面还有别的
        '<x>{"a": ',  # 对象没写完
        "<x>[1]",  # 不是对象
    ],
)
def test_an_unclosed_block_that_is_not_one_complete_object_is_reported_as_unclosed(bad):
    with pytest.raises(BlockError) as exc:
        extract_block(bad, "x")
    assert exc.value.reason == "block_unclosed"
    assert "</x>" in repair_hint(exc.value, "x")
