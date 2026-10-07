"""规划轮"等证据重签"的计时（opt.169 发版前评估建议 1 与增量复核）。

只有连续签不出才累计等待：到上限按名停下；中间断开超过上限的旧开始时刻不算进来。
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_orchestrator.orchestrator.event_handler import Orchestrator


def _harness(now: list[float]) -> SimpleNamespace:
    stopped: list[dict] = []
    fake = SimpleNamespace(
        store=None,
        _config=SimpleNamespace(profile_wait_seconds=10.0),
        _planning_evidence_waits={},
        _note=lambda _message: None,
        stopped=stopped,
    )
    fake.store = type("S", (), {"now": property(lambda _self: now[0])})()
    fake._stop_planning_round = lambda mission_id, **kwargs: stopped.append({"mission_id": mission_id, **kwargs})
    return fake


REFUSED = SimpleNamespace(use=SimpleNamespace(refusals=("TIME_DISCONTINUITY",)))


def _wait(fake: SimpleNamespace) -> None:
    Orchestrator._planning_round_waits(fake, "m1", REFUSED, path="ask")  # type: ignore[arg-type]


def test_continuous_refusals_stop_by_name_at_the_limit() -> None:
    now = [100.0]
    fake = _harness(now)
    for at in (100.0, 104.0, 108.0):
        now[0] = at
        _wait(fake)
    assert fake.stopped == []
    now[0] = 111.0
    _wait(fake)
    [stop] = fake.stopped
    assert stop["reason"] == "planning_evidence_uncertifiable"
    assert stop["detail"]["refusals"] == ["TIME_DISCONTINUITY"] and stop["detail"]["waited_seconds"] == 11.0


def test_a_wait_that_lapsed_long_ago_does_not_count_toward_a_new_one() -> None:
    """**改坏检验**：不按"断开"重新计时 → 很久以后一次短暂签不出当场停任务 → 变红。"""
    now = [100.0]
    fake = _harness(now)
    _wait(fake)  # 一次签不出，之后再没开规划轮
    now[0] = 1000.0
    _wait(fake)  # 很久以后又一次签不出
    assert fake.stopped == []
    assert fake._planning_evidence_waits["m1"] == (1000.0, 1000.0)
