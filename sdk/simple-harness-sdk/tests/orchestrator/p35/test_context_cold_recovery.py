"""Warm-marker watchdog used by the cold-recovery child: startup without progress is
rejected at the stage bound, continuous progress is capped at the child bound."""

import json
import time

import pytest

# 看门狗的两个上限（原先从冷恢复子进程脚本导入；那个子进程靠平面任务图造现场，删旧平面
# 模式 第三刀随平面删，这里只留看门狗本身）。
STAGE_SECONDS = 10
CHILD_SECONDS = 20


def _tail(log):
    with log.open("rb") as stream:
        stream.seek(max(0, log.stat().st_size - 8192))
        return stream.read().decode(errors="replace")


def _progress_path(marker):
    return marker.with_suffix(".progress.json")


def _wait_for_warm_marker(
    child,
    marker,
    log,
    *,
    monotonic=time.monotonic,
    sleep=time.sleep,
):
    started = monotonic()
    hard_deadline = started + CHILD_SECONDS
    last_progress_at = started
    last_progress = None
    progress_path = _progress_path(marker)
    while not marker.exists():
        assert child.poll() is None, _tail(log)
        now = monotonic()
        if progress_path.exists():
            progress = json.loads(progress_path.read_text())
            if progress != last_progress:
                last_progress = progress
                last_progress_at = now
        if last_progress is None:
            assert now - started < STAGE_SECONDS, (
                f"warm child startup made no progress for {STAGE_SECONDS}s: " + _tail(log)
            )
        else:
            assert now - last_progress_at < STAGE_SECONDS, (
                f"warm child runtime made no progress for {STAGE_SECONDS}s: "
                + json.dumps(last_progress, sort_keys=True)
                + "; stderr="
                + _tail(log)
            )
        assert now < hard_deadline, (
            f"warm child exceeded {CHILD_SECONDS}s hard limit: progress="
            + json.dumps(last_progress, sort_keys=True)
            + "; stderr="
            + _tail(log)
        )
        sleep(0.01)
    return json.loads(marker.read_text())


class _RunningChild:
    @staticmethod
    def poll():
        return None


def test_warm_marker_watchdog_rejects_no_startup_progress(tmp_path):
    marker, log = tmp_path / "warm.json", tmp_path / "warm.stderr.log"
    log.write_text("")
    now = [0.0]

    def advance(_seconds):
        now[0] += 1.0

    with pytest.raises(AssertionError, match="startup made no progress for 10s"):
        _wait_for_warm_marker(
            _RunningChild(), marker, log, monotonic=lambda: now[0], sleep=advance
        )
    assert now[0] == STAGE_SECONDS


def test_warm_marker_watchdog_caps_continuous_progress_at_child_limit(tmp_path):
    marker, log = tmp_path / "warm.json", tmp_path / "warm.stderr.log"
    progress = _progress_path(marker)
    log.write_text("")
    now = [0.0]

    def advance(_seconds):
        now[0] += 1.0
        progress.write_text(json.dumps({"phase": "still_running", "tick": now[0]}))

    with pytest.raises(AssertionError, match="exceeded 20s hard limit"):
        _wait_for_warm_marker(
            _RunningChild(), marker, log, monotonic=lambda: now[0], sleep=advance
        )
    assert now[0] == CHILD_SECONDS
