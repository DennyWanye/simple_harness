"""scripts/benchmark/hm_benchmark.py 的单元测试：合成 sqlite 夹具 → 指标 → 契约判定 → CLI。"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from scripts.benchmark import hm_benchmark as hb

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "benchmark", "hm_benchmark.py")


@pytest.fixture(scope="module")
def synthetic_report(tmp_path_factory: pytest.TempPathFactory) -> dict:
    base = tmp_path_factory.mktemp("hm-benchmark")
    ev = hb.build_synthetic_fixture(str(base))
    return hb.build_report([ev], [os.path.join(ev, "review-verdicts.json")])


def test_percentile_nearest_rank() -> None:
    assert hb.percentile([], 95) is None
    assert hb.percentile([5], 50) == 5
    assert hb.percentile([1, 2, 3, 4], 50) == 2
    assert hb.percentile([1, 2, 3, 4], 95) == 4
    assert hb.percentile([10, 20, 30, 40, 50, 60, 70, 80, 90, 100], 95) == 100
    assert hb.percentile([10, 20, 30, 40, 50, 60, 70, 80, 90, 100], 90) == 90


def test_text_tokens_fallback_matches_host_formula() -> None:
    """兜底副本必须与 Host 的 ``text_tokens`` 逐 token 相同。

    事件 W-c(2026-09-09)：CJK 由「每字 1 token」改成 1.3 字/token
    (``ceil(chars * 10 / 13)``)，JSON ``\\uXXXX`` 转义的 CJK 先折回它编码的那个
    字符；非 CJK 的 4 字符/token 不变。这条用例直接拿 Host 的实现对账，
    所以下次再改口径时，漂移会在这里当场红掉，而不是让基准线悄悄量错一个数。
    """

    from deskpet.sdk_adapters.context_partitions import text_tokens

    for sample in (
        "",
        "abcd",
        "abcde",
        "记住三件事",
        "记住 abcd",
        "汉" * 100,
        "\\u6c49" * 100,
        "\\u0041" * 10,
        "汉" * 13 + "\\u6c49" * 13 + "x" * 8,
        "\\u6c4",
        "\\uZZZZ",
    ):
        assert hb._fallback_text_tokens(sample) == text_tokens(sample), repr(sample)
    # 数值锚点，免得两边一起漂。
    assert hb._fallback_text_tokens("") == 0
    assert hb._fallback_text_tokens("abcd") == 1
    assert hb._fallback_text_tokens("记住三件事") == 4
    assert hb._fallback_text_tokens("汉" * 100) == 77
    assert hb._fallback_effective_input_budget(32000) == 26752
    assert hb._fallback_budget_window(1_000_000) == 32768


def test_selftest_expectations_hold(synthetic_report: dict) -> None:
    assert hb.selftest_expectations(synthetic_report) == []


def test_metrics_from_synthetic_fixture(synthetic_report: dict) -> None:
    m = synthetic_report["pooled"]
    assert m["turn_wall_ms"]["all_settled"]["n"] == 3
    assert m["turn_wall_ms"]["all_settled"]["p95"] == 20000.0
    assert m["provider_latency_ms"]["db_succeeded"]["p50"] == 2000.0
    assert m["provider_latency_ms"]["db_unmeasured_zero_duration"] == 0
    assert m["provider_calls_per_turn"]["max"] == 2.0
    assert m["tokens"]["peak_input_tokens_single_call"] == 4000
    fg = m["typed_recall"]["by_caller"]["foreground_recall"]
    assert fg["latency_ms"]["p95"] == 2500.0
    assert fg["over_2000ms"] == 1
    assert m["typed_recall"]["sdk_timeout_rate"] == 0.25
    assert m["analysis_lane"]["invocation_failure_rate"] == 0.5
    assert m["analysis_lane"]["batch_failure_reasons"] == {"analysis_delivery_authority_rejected": 1}
    assert m["context_assembly"]["trimmed_groups_total"] == 3
    assert m["run_failures"]["by_reason"] == {"react_max_turns_exceeded": 1}
    assert m["budget"]["effective_input_budget"] == 26752
    assert m["warnings"] == []


def test_native_log_dedup_keeps_intra_file_repeats(tmp_path) -> None:
    line = json.dumps({"event": "context.preparing"})
    a = tmp_path / "a.log"
    b = tmp_path / "merged.log"
    a.write_text(line + "\n" + line + "\n", encoding="utf-8")
    b.write_text(line + "\n" + line + "\n" + line + "\n", encoding="utf-8")
    s = hb.extract_native_logs([str(a), str(b)])
    # 单文件内最大重复次数为 3；跨文件重复不叠加
    assert s.log_context_events["context.preparing"] == 3


def test_backend_log_is_fallback_only(tmp_path) -> None:
    ev = hb.build_synthetic_fixture(str(tmp_path))
    logs_dir = os.path.join(ev, "primary-ui-synth", "logs")
    os.makedirs(logs_dir, exist_ok=True)
    with open(os.path.join(logs_dir, "backend.log"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": "context.preparing", "extra": "backend"}) + "\n")
    with_native = hb.build_report([ev])["pooled"]["context_assembly"]["log_context_events"]
    assert with_native["context.preparing"] == 2  # backend.log 被忽略
    os.remove(os.path.join(ev, "primary-ui-synth", "native.log"))
    os.remove(os.path.join(ev, "merged", "native.log"))
    fallback = hb.build_report([ev])["pooled"]["context_assembly"]["log_context_events"]
    assert fallback == {"context.preparing": 1}


def test_frozen_clock_provider_latency_is_unmeasured(tmp_path) -> None:
    ev = hb.build_synthetic_fixture(str(tmp_path))
    exec_db = os.path.join(ev, "primary-ui-synth", "userdata", "data", "simple-harness-sdk", "execution-v6.sqlite3")
    import sqlite3

    conn = sqlite3.connect(exec_db)
    conn.execute("update provider_invocations set settled_at = handed_off_at")
    conn.commit()
    conn.close()
    report = hb.build_report([ev])
    prov = report["pooled"]["provider_latency_ms"]
    assert prov["db_succeeded"]["n"] == 0
    assert prov["db_unmeasured_zero_duration"] == 4
    verdict = {v["key"]: v["verdict"] for v in report["verdicts"]}
    assert verdict["provider_timeout"] == "N-A"


def test_pooling_concatenates_samples(tmp_path) -> None:
    ev1 = hb.build_synthetic_fixture(str(tmp_path / "one"))
    ev2 = hb.build_synthetic_fixture(str(tmp_path / "two"))
    report = hb.build_report([ev1, ev2])
    pooled = report["pooled"]
    assert pooled["runtime_roots"] == 2
    assert pooled["turn_wall_ms"]["all_settled"]["n"] == 6
    assert pooled["provider_calls_per_turn"]["n"] == 6  # 同 host_run_id 跨根不合并
    assert len(report["per_evidence"]) == 2
    assert set(report["per_evidence_verdicts"]) == set(report["per_evidence"])


def test_corpus_quality_handles_free_form_fields() -> None:
    verdicts = {
        "A": {"case_id": "A", "category": "C04 时间/提醒(time)", "verdict": "PASS", "required_hit": True, "extra_types": ["episode"], "privacy_violation": False, "hard_trigger_correct": True},
        "B": {"case_id": "B", "category": "no-match", "verdict": "FAIL", "required_hit": None, "extra_types": 0, "privacy_violation": False},
        "C": {"case_id": "C", "category": "exact", "verdict": "NOT_SCORED", "not_scored_reason": "setup", "required_hit": None, "extra_types": None, "privacy_violation": False},
        "D": {"case_id": "D", "category": "semantic", "verdict": "PASS", "required_hit": False, "extra_types": 0, "privacy_violation": True, "hard_trigger_correct": False},
    }
    q = hb.corpus_quality(verdicts)
    assert q["cases"] == 4 and q["scored"] == 3
    assert q["required_recall_rate"] == 0.5 and q["required_recall_n"] == 2
    assert q["extra_type_rate"] == round(1 / 3, 4)
    assert q["no_recall_accuracy"] == 0.0 and q["no_recall_n"] == 1
    assert q["privacy_violations"] == 1 and q["privacy_accuracy"] == round(2 / 3, 4)
    assert q["hard_trigger_accuracy"] == 0.5
    assert q["not_scored_reasons"] == {"setup": 1}


def test_markdown_render_contains_sections(synthetic_report: dict) -> None:
    md = hb.render_markdown(synthetic_report)
    for needle in ("每 turn 墙钟", "Provider 调用", "typed recall", "分析通道", "上下文组装", "Run 失败", "契约阈值判定", "**FAIL**", "**PASS**", "**N-A**"):
        assert needle in md


def test_cli_selftest_and_report(tmp_path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(REPO_ROOT, "backend")
    proc = subprocess.run([sys.executable, SCRIPT, "--selftest"], capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SELFTEST OK" in proc.stdout
    ev = hb.build_synthetic_fixture(str(tmp_path))
    out = tmp_path / "report.json"
    proc = subprocess.run(
        [sys.executable, SCRIPT, "--evidence", ev, "--label", f"合成={ev}", "--out", str(out), "--md"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema"] == hb.SCHEMA
    assert list(data["per_evidence"]) == ["合成"]
    assert (tmp_path / "report.md").exists()
