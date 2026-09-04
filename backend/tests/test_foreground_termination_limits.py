"""前台链的终止护栏配置。

S5b 终验实测：`max_consecutive_same_tool` 漏设 → 取 SDK 默认值 3，而同处已放到
25 轮 / 50 次工具调用。三个不同厂商的模型都因此 `react_repeated_tool_exceeded`
终止（file_read 连挂 7~12 次），决定性场景无法取证。

本用例把「三项一起配」钉死：漏掉其一就会回到默认值，而默认值对文件型 agent
过紧（连读 4 个文件即触发）。
"""

from __future__ import annotations

import re
from pathlib import Path

from simple_harness.runtime.termination import TerminationLimits

_MAIN = Path(__file__).resolve().parents[1] / "main.py"


def _foreground_limits_source() -> str:
    src = _MAIN.read_text(encoding="utf-8")
    m = re.search(r"limits=TerminationLimits\((.*?)\)", src, re.S)
    assert m, "main.py 里找不到前台 driver 的 TerminationLimits"
    return m.group(1)


def test_sdk_default_is_still_three() -> None:
    """默认值变了要知道——本用例的前提就是它太紧。"""
    assert TerminationLimits().max_consecutive_same_tool == 3


def test_foreground_driver_sets_all_three_limits() -> None:
    body = _foreground_limits_source()
    for field in ("max_turns", "max_tool_calls", "max_consecutive_same_tool"):
        assert field in body, f"前台 TerminationLimits 漏设 {field}（会回落到 SDK 默认值）"


def test_consecutive_same_tool_allows_realistic_multi_file_work() -> None:
    """连读 5~10 个文件、纠错后重试 2~3 次，都属正常形态，不该被掐断。"""
    body = _foreground_limits_source()
    value = int(re.search(r"max_consecutive_same_tool\s*=\s*(\d+)", body).group(1))
    assert value >= 8, f"{value} 太紧：文件型 agent 连读几个文件就会被终止"


def test_consecutive_same_tool_still_catches_a_real_loop() -> None:
    """护栏目的不能丢：真死循环必须在烧掉整个调用预算之前被终止。"""
    body = _foreground_limits_source()
    same = int(re.search(r"max_consecutive_same_tool\s*=\s*(\d+)", body).group(1))
    total = int(re.search(r"max_tool_calls\s*=\s*(\d+)", body).group(1))
    assert same * 2 <= total, (
        f"max_consecutive_same_tool={same} 相对 max_tool_calls={total} 过松，"
        "死循环会吃掉大半预算才被拦下"
    )
