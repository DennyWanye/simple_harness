# -*- coding: utf-8 -*-
"""run.py 判定口径的离线自测（不起后台、不调模型，用假结果喂判定函数）。

三条口径（原计划 §15 / model-scenarios.json）：
1. 预算从 model-scenarios.json 读，脚本里没有自己的数字；
2. 超预算的局判 FAIL 并写明原因；
3. 同局号再跑写成新的 attempt 目录，旧的原样保留。

跑法（在仓库根）::

    backend/.venv/bin/python backend/scripts/assurance_model_scenarios/test_verdict_offline.py
    # 或 backend/.venv/bin/python -m pytest -q backend/scripts/assurance_model_scenarios/test_verdict_offline.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run  # noqa: E402

GOOD_ORACLE = {"root_resolution_accepted": True, "closeout_finalized": True, "report_accepted": True}


def _usage(tokens: int, calls: int) -> dict:
    return {"model_calls": calls, "tokens": tokens, "token_column": "settled_tokens"}


def test_limits_come_only_from_plan_json() -> None:
    body = json.loads(run.PLAN_SCENARIOS.read_text(encoding="utf-8"))["limits"]
    assert run.LIMITS == {"tokens": body["total_model_tokens"], "model_calls": body["model_calls"],
                          "tool_calls": body["tool_calls"], "wall": body["wall_time_seconds"]}
    # 五个场景没有自己的预算；换一份 JSON 预算就跟着变，证明数字不在脚本里
    assert all("limits" not in spec for spec in run.SCENARIOS.values())
    with tempfile.TemporaryDirectory() as tmp:
        other = Path(tmp) / "scenarios.json"
        other.write_text(json.dumps({"limits": {"total_model_tokens": 7, "model_calls": 1, "tool_calls": 2,
                                                "wall_time_seconds": 3}}), encoding="utf-8")
        assert run.plan_limits(other) == {"tokens": 7, "model_calls": 1, "tool_calls": 2, "wall": 3}


def test_within_budget_and_all_checks_pass() -> None:
    limits = {"tokens": 300_000, "model_calls": 16, "tool_calls": 32, "wall": 900}
    result = run.verdict("COMPLETED", GOOD_ORACLE, _usage(210_000, 9), 180.0, limits)
    assert result == {"passed": True, "within_budget": True, "fail_reasons": []}


def test_over_budget_is_a_failed_trial_with_reason() -> None:
    limits = {"tokens": 300_000, "model_calls": 16, "tool_calls": 32, "wall": 900}
    # 任务完成、判定全对，只是 token 超了 → FAIL
    result = run.verdict("COMPLETED", GOOD_ORACLE, _usage(420_000, 9), 240.0, limits)
    assert result["passed"] is False and result["within_budget"] is False
    assert any("token 总量 420000 > 上限 300000" in r for r in result["fail_reasons"])
    # 模型调用数超 → FAIL
    result = run.verdict("COMPLETED", GOOD_ORACLE, _usage(100_000, 17), 240.0, limits)
    assert result["passed"] is False and any("模型调用数 17 > 上限 16" in r for r in result["fail_reasons"])
    # 墙钟超 → FAIL
    result = run.verdict("COMPLETED", GOOD_ORACLE, _usage(100_000, 9), 901.0, limits)
    assert result["passed"] is False and any("墙钟秒数 901 > 上限 900" in r for r in result["fail_reasons"])
    # 用量缺失不能当作在预算内
    result = run.verdict("COMPLETED", GOOD_ORACLE, {"model_calls": 9, "tokens": None}, 100.0, limits)
    assert result["passed"] is False and result["within_budget"] is False


def test_unfinished_or_wrong_oracle_still_fails() -> None:
    limits = {"tokens": 300_000, "model_calls": 16, "tool_calls": 32, "wall": 900}
    result = run.verdict("FAILED", GOOD_ORACLE, _usage(1000, 1), 10.0, limits)
    assert result["passed"] is False and result["within_budget"] is True
    assert any("不是 COMPLETED" in r for r in result["fail_reasons"])
    result = run.verdict("COMPLETED", {**GOOD_ORACLE, "total": False}, _usage(1000, 1), 10.0, limits)
    assert result["passed"] is False and any("判定 total 未成立" in r for r in result["fail_reasons"])


def test_rerun_same_trial_keeps_old_attempt() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        first, n1 = run.new_attempt_dir(out, "accurate-report", 2)
        assert n1 == 1 and first == out / "accurate-report/trial-2/attempt-1"
        (first / "record.json").write_text('{"attempt": 1, "passed": false}', encoding="utf-8")
        second, n2 = run.new_attempt_dir(out, "accurate-report", 2)
        assert n2 == 2 and second == out / "accurate-report/trial-2/attempt-2"
        # 旧记录一个字节没动，新目录是空的
        assert (first / "record.json").read_text(encoding="utf-8") == '{"attempt": 1, "passed": false}'
        assert list(second.iterdir()) == []
        # 别的局号互不影响
        third, n3 = run.new_attempt_dir(out, "accurate-report", 3)
        assert n3 == 1 and third.parent.name == "trial-3"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for test in tests:
        test()
        print(f"通过 {test.__name__}")
    print(f"{len(tests)} 条全部通过")
