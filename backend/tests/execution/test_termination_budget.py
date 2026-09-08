# SPDX-License-Identifier: BUSL-1.1

"""C10-13: the driver's wall budget must fire before any external watchdog.

The Host built ``TerminationLimits`` without ``max_wall_seconds``, silently
inheriting the SDK default 900.0s — the very value the corpus batch used as its
external SIGTERM deadline. The supervisor won at 900.167s and the Run produced
no terminal receipt at all. These tests pin the whole ordering chain, in the
same spirit as the S5b consistency tests: every layer is read from its real
source (config.toml, main.py, provider.py, run_corpus_batch.py), never restated.
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import re
import tomllib
from pathlib import Path

import pytest

from deskpet.execution.termination_budget import (
    CASE_MULTI_RUN_ALLOWANCE_SECONDS,
    DEFAULT_MAX_WALL_SECONDS,
    FOREGROUND_ACTIVE_BUDGET_DEFAULT_SECONDS,
    MAX_MAX_WALL_SECONDS,
    MIN_MAX_WALL_SECONDS,
    PROVIDER_TRANSPORT_TIMEOUT_SECONDS,
    TERMINAL_SETTLEMENT_MARGIN_SECONDS,
    corpus_batch_deadline_seconds,
    external_deadline_floor_seconds,
    resolve_max_wall_seconds,
)

HOST_ROOT = Path(__file__).resolve().parents[3]
MAIN_PY = HOST_ROOT / "backend/main.py"
CONFIG_TOML = HOST_ROOT / "config.toml"
BATCH_SCRIPT = HOST_ROOT / "scripts/run_corpus_batch.py"


def _load_batch_script():
    spec = importlib.util.spec_from_file_location("_batch_under_test", BATCH_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_layer_ordering_invariant():
    """provider call < one Run's wall budget < active budget < external floor."""
    assert PROVIDER_TRANSPORT_TIMEOUT_SECONDS < DEFAULT_MAX_WALL_SECONDS
    assert DEFAULT_MAX_WALL_SECONDS < FOREGROUND_ACTIVE_BUDGET_DEFAULT_SECONDS
    # The wall clock is sampled only at reservation boundaries, so settlement
    # can trail the budget by one whole in-flight provider call.
    floor = external_deadline_floor_seconds()
    assert floor == (
        DEFAULT_MAX_WALL_SECONDS
        + PROVIDER_TRANSPORT_TIMEOUT_SECONDS
        + TERMINAL_SETTLEMENT_MARGIN_SECONDS
    )
    assert floor >= FOREGROUND_ACTIVE_BUDGET_DEFAULT_SECONDS
    # C10-13's actual failure: the two ends of the chain were the same number.
    assert corpus_batch_deadline_seconds() > floor
    assert corpus_batch_deadline_seconds() != 900


def test_provider_transport_timeout_mirror_does_not_drift():
    from deskpet.sdk_adapters.provider import ProductProviderAdapter

    default = inspect.signature(ProductProviderAdapter.__init__).parameters["timeout"].default
    assert float(default) == PROVIDER_TRANSPORT_TIMEOUT_SECONDS


def test_foreground_active_budget_mirror_does_not_drift():
    source = MAIN_PY.read_text(encoding="utf-8")
    match = re.search(r'get\("chat_turn_timeout_minutes",\s*(\d+)\)', source)
    assert match is not None, "chat_turn_timeout_minutes default disappeared from main.py"
    assert float(match.group(1)) * 60.0 == FOREGROUND_ACTIVE_BUDGET_DEFAULT_SECONDS


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, DEFAULT_MAX_WALL_SECONDS),
        ({}, DEFAULT_MAX_WALL_SECONDS),
        ({"agent": {}}, DEFAULT_MAX_WALL_SECONDS),
        ({"agent": {"max_wall_seconds": None}}, DEFAULT_MAX_WALL_SECONDS),
        ({"agent": {"max_wall_seconds": "600"}}, DEFAULT_MAX_WALL_SECONDS),
        ({"agent": {"max_wall_seconds": True}}, DEFAULT_MAX_WALL_SECONDS),
        ({"agent": {"max_wall_seconds": 0}}, DEFAULT_MAX_WALL_SECONDS),
        ({"agent": {"max_wall_seconds": float("nan")}}, DEFAULT_MAX_WALL_SECONDS),
        ({"agent": {"max_wall_seconds": 480}}, 480.0),
        ({"agent": {"max_wall_seconds": 5}}, MIN_MAX_WALL_SECONDS),
        ({"agent": {"max_wall_seconds": 99_999}}, MAX_MAX_WALL_SECONDS),
    ],
)
def test_resolve_max_wall_seconds_never_leaves_the_driver_unbounded(raw, expected):
    assert resolve_max_wall_seconds(raw) == expected
    # Whatever a user configures, one provider call still fits inside it.
    assert resolve_max_wall_seconds(raw) > PROVIDER_TRANSPORT_TIMEOUT_SECONDS


def test_shipped_config_declares_the_wall_budget_explicitly():
    raw = tomllib.loads(CONFIG_TOML.read_text(encoding="utf-8"))
    assert "max_wall_seconds" in raw["agent"], "[agent].max_wall_seconds must stay explicit"
    assert resolve_max_wall_seconds(raw) == DEFAULT_MAX_WALL_SECONDS


def test_main_passes_an_explicit_wall_budget_to_termination_limits():
    """The regression itself: a TerminationLimits(...) with no max_wall_seconds."""
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "TerminationLimits"
    ]
    assert calls, "main.py no longer builds TerminationLimits"
    for call in calls:
        keywords = {kw.arg for kw in call.keywords}
        assert "max_wall_seconds" in keywords, (
            "TerminationLimits without max_wall_seconds silently inherits the SDK "
            "default 900.0s (C10-13)"
        )


def test_corpus_batch_deadline_strictly_exceeds_the_host_floor():
    batch = _load_batch_script()
    budget = batch._termination_budget(HOST_ROOT)
    assert budget.DEFAULT_MAX_WALL_SECONDS == DEFAULT_MAX_WALL_SECONDS
    raw = batch._host_raw_config(HOST_ROOT)
    floor = budget.external_deadline_floor_seconds(budget.resolve_max_wall_seconds(raw))
    parser_default = _batch_seconds_default()
    assert parser_default > floor
    assert parser_default == corpus_batch_deadline_seconds()
    assert floor + CASE_MULTI_RUN_ALLOWANCE_SECONDS == parser_default


def _batch_seconds_default() -> int:
    """The parser is built inside main(); read its --seconds default from there."""
    import argparse
    import sys

    batch = _load_batch_script()
    captured: dict[str, object] = {}
    real_add = argparse.ArgumentParser.add_argument
    real_argv = sys.argv

    def spy(self, *args, **kwargs):
        if "--seconds" in args:
            captured["default"] = kwargs.get("default")
        return real_add(self, *args, **kwargs)

    argparse.ArgumentParser.add_argument = spy
    sys.argv = ["run_corpus_batch.py"]  # required args missing -> SystemExit(2)
    try:
        with pytest.raises(SystemExit):
            batch.main()
    finally:
        argparse.ArgumentParser.add_argument = real_add
        sys.argv = real_argv
    assert "default" in captured
    return int(captured["default"])


def test_corpus_batch_rejects_a_deadline_that_races_the_driver(tmp_path, monkeypatch):
    batch = _load_batch_script()
    case_file = tmp_path / "cases.txt"
    case_file.write_text("C10-13\n")
    argv = [
        "run_corpus_batch.py",
        "--host-root", str(HOST_ROOT),
        "--memory-sdk-root", str(tmp_path),
        "--installed-target", str(tmp_path),
        "--evidence-root", str(tmp_path / ".local-test-evidence" / "x"),
        "--case-file", str(case_file),
        "--preflight-model", "none",
        "--seconds", "900",
    ]
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit) as error:
        batch.main()
    assert "must strictly exceed" in str(error.value)
