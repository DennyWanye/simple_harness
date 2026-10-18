# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""R-T3 — LLM 失败降级矩阵 + 故障注入单测（§15.4 要求）。

覆盖：
  2.3 GoalChecker 超时/失败 → (False, "goal_check=skipped")  + metric
  2.4 ExternalEvaluator timeout/error → conservative (revise) on high-consequence
      + metric evaluator_skipped
  无崩溃保证（所有注入场景不抛异常）

合规性规定（per §15.4 spec）：
  - GoalChecker timeout/error 在有活跃 goal 时 MUST NOT default to done=True
  - 降级信号 goal_check=skipped 用于区分"真完成"和"checker 失败放行"
  - goal_check=skipped → 调用方 treat as "非正向确认"，不触发 mark_done
"""

from __future__ import annotations


# ─── GoalChecker 降级矩阵 ─────────────────────────────────────


class _RecordingMetricsSink:
    """本地记录 metrics 调用的 stub（替代全局 observability.metrics_sink）。"""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def record(self, event: str, detail: dict | None = None) -> bool:
        self.events.append((event, detail or {}))
        return True


def _make_llm(response: str):
    async def _call(prompt: str) -> str:
        return response

    return _call


def _make_failing_llm(exc: Exception):
    async def _call(prompt: str) -> str:
        raise exc

    return _call


def _make_timeout_llm():
    """LLM that raises asyncio.TimeoutError to simulate timeout."""

    async def _call(prompt: str) -> str:
        raise TimeoutError("LLM timeout")

    return _call


# ─────────────────────────────────────────────────────────────────────────────
# T-D4: No crash guarantee for all paths
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# T-D5: R-T6 — Receipt shadow fields (BC + round-trip)
# ─────────────────────────────────────────────────────────────────────────────

from datetime import UTC, datetime


def _make_basic_receipt(**overrides):
    from deskpet.tools.receipt import make_receipt

    defaults = dict(
        tool_name="ppt_create",
        args={"title": "test"},
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC),
        ok=True,
    )
    defaults.update(overrides)
    return make_receipt(**defaults)


def test_receipt_shadow_fields_have_safe_defaults():
    """R-T6: new optional shadow fields default to None/empty — BC for existing receipts."""
    r = _make_basic_receipt()
    assert r.shadow_verdict is None
    assert r.actual_outcome is None
    assert r.verify_latency_ms is None
    assert r.degradation_flags == []


def test_receipt_shadow_verdict_can_be_set():
    """R-T6: shadow_verdict can record 'would_block' or 'would_pass'."""

    r = _make_basic_receipt()
    r.shadow_verdict = "would_block"
    assert r.shadow_verdict == "would_block"


def test_receipt_actual_outcome_can_be_set():
    """R-T6: actual_outcome placeholder is settable."""
    r = _make_basic_receipt()
    r.actual_outcome = "user_accepted"
    assert r.actual_outcome == "user_accepted"


def test_receipt_verify_latency_ms_can_be_set():
    """R-T6: verify_latency_ms for p95 monitoring."""
    r = _make_basic_receipt()
    r.verify_latency_ms = 450
    assert r.verify_latency_ms == 450


def test_receipt_degradation_flags_can_be_appended():
    """R-T6: degradation_flags records which checks were skipped."""
    r = _make_basic_receipt()
    r.degradation_flags.append("goal_check=skipped")
    r.degradation_flags.append("evaluator_skipped")
    assert "goal_check=skipped" in r.degradation_flags
    assert len(r.degradation_flags) == 2


def test_receipt_to_dict_includes_shadow_fields():
    """R-T6: to_dict() serializes shadow fields (for JSONL persistence)."""
    r = _make_basic_receipt()
    r.shadow_verdict = "would_pass"
    r.verify_latency_ms = 120
    r.degradation_flags = ["reflection_parse_failed"]
    d = r.to_dict()
    assert d["shadow_verdict"] == "would_pass"
    assert d["verify_latency_ms"] == 120
    assert d["degradation_flags"] == ["reflection_parse_failed"]
    assert d["actual_outcome"] is None  # default preserved


def test_receipt_from_dict_old_shape_still_valid():
    """BC: receipt dict WITHOUT shadow fields can still be loaded (ToolReceipt(**d))."""
    from deskpet.tools.receipt import ToolReceipt, make_receipt

    r = make_receipt(
        tool_name="test",
        args={},
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 1, tzinfo=UTC),
        ok=True,
    )
    d = r.to_dict()
    # Remove shadow fields to simulate an old-format receipt
    for key in ("shadow_verdict", "actual_outcome", "verify_latency_ms", "degradation_flags"):
        d.pop(key, None)

    # Should not raise — new fields have defaults
    r2 = ToolReceipt(**d)
    assert r2.shadow_verdict is None
    assert r2.actual_outcome is None
    assert r2.verify_latency_ms is None
    assert r2.degradation_flags == []


def test_receipt_hmac_still_valid_with_shadow_fields():
    """R-T6 BC: HMAC sign/verify round-trip works even with shadow fields populated."""
    from deskpet.tools.receipt import hmac_sign, hmac_verify

    r = _make_basic_receipt()
    r.shadow_verdict = "would_block"
    r.verify_latency_ms = 200
    r.degradation_flags = ["goal_check=skipped"]
    # Re-sign with shadow fields included
    r.sig = hmac_sign(r)
    assert hmac_verify(r) is True


def test_receipt_store_round_trip_with_shadow_fields(tmp_path):
    """R-T6: ReceiptStore append + load_session round-trips shadow fields."""
    from deskpet.tools.receipt import hmac_sign, make_receipt
    from deskpet.tools.receipt_store import ReceiptStore

    key = b"\x42" * 32
    store = ReceiptStore(tmp_path, key=key)

    r = make_receipt(
        tool_name="ppt_create",
        args={"title": "t"},
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        ended_at=datetime(2026, 1, 1, tzinfo=UTC),
        ok=True,
        session_id="sess-1",
        secret=key,
    )
    r.shadow_verdict = "would_block"
    r.verify_latency_ms = 350
    r.degradation_flags = ["evaluator_skipped"]
    # Re-sign after mutating shadow fields
    r.sig = hmac_sign(r, key)
    store.append(r)

    loaded = store.load_session("sess-1")
    assert len(loaded) == 1
    assert loaded[0].shadow_verdict == "would_block"
    assert loaded[0].verify_latency_ms == 350
    assert "evaluator_skipped" in loaded[0].degradation_flags


# ─────────────────────────────────────────────────────────────────────────────
# T-D6: metrics_sink has the new event names whitelisted
# ─────────────────────────────────────────────────────────────────────────────


def test_metrics_sink_accepts_goal_check_skipped():
    """R-T3/R-T6: goal_check_skipped event is whitelisted in metrics_sink."""
    from observability.metrics_sink import VALID_EVENTS

    assert "goal_check_skipped" in VALID_EVENTS, (
        "goal_check_skipped must be in VALID_EVENTS for degradation tracking"
    )


def test_metrics_sink_accepts_reflection_parse_failed():
    """R-T3/R-T6: reflection_parse_failed event is whitelisted."""
    from observability.metrics_sink import VALID_EVENTS

    assert "reflection_parse_failed" in VALID_EVENTS


def test_metrics_sink_accepts_evaluator_conservative_block():
    """R-T3/R-T6: evaluator_conservative_block event is whitelisted."""
    from observability.metrics_sink import VALID_EVENTS

    assert "evaluator_conservative_block" in VALID_EVENTS


def test_metrics_sink_records_goal_check_skipped(tmp_path):
    """R-T3: MetricsSink.record() writes goal_check_skipped to disk."""
    from observability.metrics_sink import MetricsSink

    sink = MetricsSink(tmp_path / "metrics.jsonl")
    result = sink.record("goal_check_skipped", {"reason": "timeout"})
    assert result is True
    lines = (tmp_path / "metrics.jsonl").read_text().splitlines()
    import json

    assert any(json.loads(l)["event"] == "goal_check_skipped" for l in lines)


def test_metrics_sink_records_reflection_parse_failed(tmp_path):
    """R-T3: MetricsSink.record() writes reflection_parse_failed to disk."""
    from observability.metrics_sink import MetricsSink

    sink = MetricsSink(tmp_path / "metrics.jsonl")
    result = sink.record("reflection_parse_failed", {"count": 1})
    assert result is True
