"""Spawn-depth policy tests independent of the removed legacy runtime."""

from __future__ import annotations

import pytest

from deskpet.agent.task_kinds import (
    HARD_MAX_SPAWN_DEPTH,
    SpawnDepthExceeded,
    _DEPTH_ENV,
    check_spawn_depth,
    child_depth_env,
    current_spawn_depth,
    depth_gate_enabled,
    resolve_max_spawn_depth,
)


def test_flag_defaults_off_and_accepts_explicit_true():
    assert depth_gate_enabled(None) is False
    assert depth_gate_enabled({}) is False
    assert depth_gate_enabled({"subagent_explicit_depth": False}) is False
    assert depth_gate_enabled({"subagent_explicit_depth": True}) is True


def test_current_depth_defaults_zero_and_reads_env(monkeypatch):
    monkeypatch.delenv(_DEPTH_ENV, raising=False)
    assert current_spawn_depth() == 0
    monkeypatch.setenv(_DEPTH_ENV, "2")
    assert current_spawn_depth() == 2


@pytest.mark.parametrize("value", ["garbage", "-5"])
def test_current_depth_bad_value_falls_back_zero(monkeypatch, value):
    monkeypatch.setenv(_DEPTH_ENV, value)
    assert current_spawn_depth() == 0


def test_child_depth_env_increments(monkeypatch):
    monkeypatch.delenv(_DEPTH_ENV, raising=False)
    assert child_depth_env() == {_DEPTH_ENV: "1"}
    assert child_depth_env(2) == {_DEPTH_ENV: "3"}


def test_default_and_configured_max_depth():
    assert resolve_max_spawn_depth(None) == 1
    assert resolve_max_spawn_depth({}) == 1
    assert resolve_max_spawn_depth({"max_spawn_depth": 2}) == 2


@pytest.mark.parametrize("configured", [5, 99])
def test_hard_cap_cannot_be_exceeded(configured):
    assert HARD_MAX_SPAWN_DEPTH == 3
    assert resolve_max_spawn_depth({"max_spawn_depth": configured}) == 3


@pytest.mark.parametrize("configured", [0, -3])
def test_max_depth_is_clamped_to_at_least_one(configured):
    assert resolve_max_spawn_depth({"max_spawn_depth": configured}) == 1


def test_bad_max_depth_falls_back_default():
    assert resolve_max_spawn_depth({"max_spawn_depth": "x"}) == 1


def test_gate_off_is_noop_regardless_of_depth():
    check_spawn_depth(None, current_depth=99)
    check_spawn_depth({}, current_depth=99)
    check_spawn_depth({"max_spawn_depth": 1}, current_depth=99)


def test_gate_allows_child_at_limit_and_rejects_beyond_limit():
    cfg = {"subagent_explicit_depth": True, "max_spawn_depth": 1}
    check_spawn_depth(cfg, current_depth=0)
    with pytest.raises(SpawnDepthExceeded) as exc_info:
        check_spawn_depth(cfg, current_depth=1)
    assert exc_info.value.depth == 2
    assert exc_info.value.limit == 1


def test_gate_supports_controlled_nesting():
    cfg = {"subagent_explicit_depth": True, "max_spawn_depth": 2}
    check_spawn_depth(cfg, current_depth=0)
    check_spawn_depth(cfg, current_depth=1)
    with pytest.raises(SpawnDepthExceeded):
        check_spawn_depth(cfg, current_depth=2)


def test_gate_enforces_hard_cap():
    cfg = {"subagent_explicit_depth": True, "max_spawn_depth": 99}
    check_spawn_depth(cfg, current_depth=2)
    with pytest.raises(SpawnDepthExceeded) as exc_info:
        check_spawn_depth(cfg, current_depth=3)
    assert exc_info.value.limit == HARD_MAX_SPAWN_DEPTH


def test_gate_reads_depth_from_env(monkeypatch):
    cfg = {"subagent_explicit_depth": True, "max_spawn_depth": 1}
    monkeypatch.setenv(_DEPTH_ENV, "1")
    with pytest.raises(SpawnDepthExceeded):
        check_spawn_depth(cfg)
