# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""P4-S5: ToolRegistry unit tests (tool-framework spec).

Covers every Requirement in ``openspec/changes/p4-poseidon-agent-harness
/specs/tool-framework/spec.md``:

  * Auto-Discovery Tool Registry       — test_auto_discovery_loads_*
  * OpenAI-Format Tool Schemas         — test_schemas_* / test_openai_envelope
  * Tool Dispatch with Error Handling  — test_dispatch_* / test_exception_*
  * Environment-Based Tool Gating      — test_requires_env_*
  * Check Function for Runtime Validation — test_check_fn_*
  * Tool Search for Lazy Schema Loading — test_tool_search_*

We exercise both the module-level ``registry`` (which ran auto-discovery
at import) and fresh ``ToolRegistry()`` instances for isolated tests.
"""
from __future__ import annotations

import json
import threading

import pytest

from deskpet.tools import registry as module_registry
from deskpet.tools.registry import ToolRegistry


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------
def _fake_schema(name: str, desc: str = "fake") -> dict:
    return {
        "name": name,
        "description": desc,
        "parameters": {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        },
    }


def test_mcp_source_batch_has_registration_barrier() -> None:
    registry = ToolRegistry()
    second_started = threading.Event()
    allow_second = threading.Event()
    reader_done = threading.Event()
    observed: list[set[str]] = []

    def _register(name: str) -> None:
        registry.register(
            name,
            "mcp",
            _fake_schema(name),
            lambda _args, _task: "{}",
            source="mcp:srv",
            replace_allowed=True,
        )

    def _second() -> None:
        second_started.set()
        assert allow_second.wait(1)
        _register("mcp_srv_two")

    publisher = threading.Thread(
        target=lambda: registry.publish_source_batch(
            source="mcp:srv",
            registrations=(lambda: _register("mcp_srv_one"), _second),
        )
    )
    publisher.start()
    assert second_started.wait(1)

    def _read() -> None:
        observed.append({spec.name for spec in registry.catalog_snapshot().specs})
        reader_done.set()

    reader = threading.Thread(target=_read)
    reader.start()
    assert not reader_done.wait(0.05)
    allow_second.set()
    publisher.join(1)
    reader.join(1)

    assert reader_done.is_set()
    assert observed == [{"mcp_srv_one", "mcp_srv_two"}]


@pytest.fixture
def fresh() -> ToolRegistry:
    """Isolated registry so tests don't leak into the global singleton."""
    return ToolRegistry()


# ---------------------------------------------------------------------
# Auto-discovery (module-level registry already loaded)
# ---------------------------------------------------------------------
def test_auto_discovery_loads_file_tools():
    names = set(module_registry.list_tools())
    assert {"file_read", "file_write", "file_glob", "file_grep"} <= names


def test_auto_discovery_loads_web_tools():
    names = set(module_registry.list_tools())
    assert {
        "web_fetch",
        "web_crawl",
        "web_extract_article",
        "web_read_sitemap",
    } <= names


def test_auto_discovery_excludes_retired_stubs_and_keeps_search():
    """Legacy stubs stay out of the module registry after SDK catalog cutover.

    The product SDK catalog owns the real ``memory_search`` binding.  Keeping
    the old unavailable memory stubs in this auto-discovered registry would
    reintroduce a second, order-dependent authority.
    """
    names = set(module_registry.list_tools())
    assert "tool_search" in names
    assert {"memory_write", "memory_read", "memory_search"}.isdisjoint(names)
    assert "skill_invoke" in names
    # T3.3: mcp_call / delegate 不再 stub（无真 caller，0-release 删）
    assert "mcp_call" not in names
    assert "delegate" not in names


def test_auto_discovery_meets_mvp_16_tools_minimum():
    # Spec "MVP Built-in 16 Tools" — count includes tool_search which
    # brings us to 17 after the stubs; ≥16 satisfies the requirement.
    assert len(module_registry.list_tools()) >= 16


# ---------------------------------------------------------------------
# Register / schemas / filter
# ---------------------------------------------------------------------
def test_register_basic(fresh: ToolRegistry):
    fresh.register("a", "control", _fake_schema("a"), lambda args, tid: "{}")
    assert fresh.list_tools() == ["a"]


def test_register_validates_name(fresh: ToolRegistry):
    with pytest.raises(ValueError):
        fresh.register("", "control", _fake_schema("x"), lambda a, t: "{}")


def test_register_validates_handler(fresh: ToolRegistry):
    with pytest.raises(TypeError):
        fresh.register("a", "control", _fake_schema("a"), "not-callable")  # type: ignore[arg-type]


def test_register_duplicate_without_optin_raises(fresh: ToolRegistry):
    """WI-T4.1 v3：同名重注册且双方都未 opt-in → ToolNameConflictError."""
    from deskpet.tools.registry import ToolNameConflictError
    fresh.register("a", "control", _fake_schema("a"), lambda a, t: '"v1"')
    with pytest.raises(ToolNameConflictError):
        fresh.register("a", "control", _fake_schema("a"), lambda a, t: '"v2"')


def test_register_replaces_duplicate_with_optin(fresh: ToolRegistry, caplog):
    """WI-T4.1 v3：opt-in replace_allowed=True → 允许覆盖 + 仅 warn."""
    fresh.register("a", "control", _fake_schema("a"), lambda a, t: '"v1"')
    fresh.register(
        "a", "control", _fake_schema("a"), lambda a, t: '"v2"',
        replace_allowed=True,
    )
    assert fresh.dispatch("a", {}) == '"v2"'


def test_schemas_returns_openai_envelope(fresh: ToolRegistry):
    fresh.register("t1", "control", _fake_schema("t1"), lambda a, t: "{}")
    out = fresh.schemas()
    assert len(out) == 1
    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "t1"
    assert "parameters" in out[0]["function"]


def test_schemas_filtered_by_toolset(fresh: ToolRegistry):
    fresh.register("m1", "memory", _fake_schema("m1"), lambda a, t: "{}")
    fresh.register("t1", "todo", _fake_schema("t1"), lambda a, t: "{}")
    fresh.register("c1", "control", _fake_schema("c1"), lambda a, t: "{}")
    got = [s["function"]["name"] for s in fresh.schemas(
        enabled_toolsets=["memory", "todo"]
    )]
    assert sorted(got) == ["m1", "t1"]


def test_schemas_empty_toolset_whitelist_returns_nothing(fresh: ToolRegistry):
    fresh.register("m1", "memory", _fake_schema("m1"), lambda a, t: "{}")
    assert fresh.schemas(enabled_toolsets=[]) == []


# ---------------------------------------------------------------------
# requires_env gating
# ---------------------------------------------------------------------
def test_requires_env_hides_tool_when_missing(fresh: ToolRegistry, monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    fresh.register(
        "web_search_brave",
        "web",
        _fake_schema("web_search_brave"),
        lambda a, t: "{}",
        requires_env=["BRAVE_API_KEY"],
    )
    names = [s["function"]["name"] for s in fresh.schemas()]
    assert "web_search_brave" not in names


def test_requires_env_shows_tool_when_present(fresh: ToolRegistry, monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "k1")
    fresh.register(
        "web_search_brave",
        "web",
        _fake_schema("web_search_brave"),
        lambda a, t: "{}",
        requires_env=["BRAVE_API_KEY"],
    )
    names = [s["function"]["name"] for s in fresh.schemas()]
    assert "web_search_brave" in names


def test_requires_env_empty_string_still_hides(fresh: ToolRegistry, monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "")
    fresh.register(
        "web_search_brave",
        "web",
        _fake_schema("web_search_brave"),
        lambda a, t: "{}",
        requires_env=["BRAVE_API_KEY"],
    )
    assert "web_search_brave" not in [
        s["function"]["name"] for s in fresh.schemas()
    ]


# ---------------------------------------------------------------------
# check_fn gating
# ---------------------------------------------------------------------
def test_check_fn_blocks_dispatch_when_false(fresh: ToolRegistry):
    called = {"n": 0}

    def handler(a, t):
        called["n"] += 1
        return '"ran"'

    fresh.register(
        "t",
        "control",
        _fake_schema("t"),
        handler,
        check_fn=lambda: False,
    )
    out = fresh.dispatch("t", {})
    payload = json.loads(out)
    assert "error" in payload
    assert payload["retriable"] is True
    assert called["n"] == 0, "handler MUST NOT run when check_fn=False"


def test_check_fn_allows_dispatch_when_true(fresh: ToolRegistry):
    fresh.register(
        "t",
        "control",
        _fake_schema("t"),
        lambda a, t: '"ran"',
        check_fn=lambda: True,
    )
    assert fresh.dispatch("t", {}) == '"ran"'


def test_check_fn_exception_treated_as_not_ready(fresh: ToolRegistry):
    def boom():
        raise RuntimeError("boom")

    fresh.register(
        "t",
        "control",
        _fake_schema("t"),
        lambda a, t: '"ran"',
        check_fn=boom,
    )
    payload = json.loads(fresh.dispatch("t", {}))
    assert "error" in payload and "not ready" in payload["error"]


# ---------------------------------------------------------------------
# Dispatch error contract
# ---------------------------------------------------------------------
def test_dispatch_unknown_tool(fresh: ToolRegistry):
    payload = json.loads(fresh.dispatch("nope", {}))
    assert payload["error"].startswith("unknown tool")
    assert payload["retriable"] is False


def test_dispatch_catches_connection_error_as_retriable(fresh: ToolRegistry):
    def handler(a, t):
        raise ConnectionError("timeout")

    fresh.register("t", "control", _fake_schema("t"), handler)
    payload = json.loads(fresh.dispatch("t", {}))
    assert payload["retriable"] is True
    assert "ConnectionError" in payload["error"]
    assert "timeout" in payload["error"]


def test_dispatch_catches_value_error_as_non_retriable(fresh: ToolRegistry):
    def handler(a, t):
        raise ValueError("bad arg")

    fresh.register("t", "control", _fake_schema("t"), handler)
    payload = json.loads(fresh.dispatch("t", {}))
    assert payload["retriable"] is False
    assert "ValueError" in payload["error"]


def test_dispatch_handler_returning_dict_gets_serialized(fresh: ToolRegistry):
    fresh.register(
        "t",
        "control",
        _fake_schema("t"),
        lambda a, t: {"hi": "there"},  # type: ignore[return-value]
    )
    assert json.loads(fresh.dispatch("t", {})) == {"hi": "there"}


def test_dispatch_passes_args_and_task_id(fresh: ToolRegistry):
    captured: dict = {}

    def handler(args, task_id):
        captured.update(args)
        captured["__task_id__"] = task_id
        return '"ok"'

    fresh.register("t", "control", _fake_schema("t"), handler)
    fresh.dispatch("t", {"a": 1}, task_id="turn-42")
    assert captured == {"a": 1, "__task_id__": "turn-42"}


def test_dispatch_none_args_defaults_to_empty(fresh: ToolRegistry):
    fresh.register(
        "t",
        "control",
        _fake_schema("t"),
        lambda args, t: json.dumps({"n": len(args)}),
    )
    payload = json.loads(fresh.dispatch("t", None))  # type: ignore[arg-type]
    assert payload["n"] == 0
