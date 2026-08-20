from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


EXPECTED_MANIFEST_SHA256 = (
    "891ae13615229ee98715f8b18f39a5a045c1f995a29e984a4b86c4eaa2f310bf"
)


@pytest.mark.asyncio
async def test_real_catalog_unrelated_manifest_change_preserves_tool_execution_identity(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from simple_harness import CallId, EffectId, RequestId, RunId, thaw_json
    from simple_harness.tools import CancellationToken, ToolCall, ToolContext

    from deskpet.sdk_adapters.context_authority import canonical_sha256
    from deskpet.sdk_adapters.tool_authority import SdkRunToolAuthorityRegistry
    from deskpet.sdk_adapters.tools import (
        ProductEffectExecutor,
        build_product_tool_registry,
    )
    from deskpet.tool_catalog import build_explicit_product_tool_catalog
    from deskpet.tools.capabilities import canonical_hash
    from deskpet.tools.context_page_in_tools import ContextPageInStore

    runtime_context = SimpleNamespace(
        session_id="session-stable",
        request_id="request-stable",
        scope_id="scope-stable",
    )
    product_catalog = build_explicit_product_tool_catalog(
        _catalog_dependencies(ContextPageInStore(), runtime_context)
    )
    base_registry, base_inventory = build_product_tool_registry(
        product_catalog.registrations
    )
    changed_registrations = []
    for registration in product_catalog.registrations:
        metadata = {
            **dict(registration.metadata),
            "manifest_sha256": "unrelated-global-manifest-v2",
        }
        if registration.name == "web_search":
            metadata["handler_id"] = "unrelated:web_search:v2"
        changed_registrations.append(replace(registration, metadata=metadata))
    rebuilt_registry, rebuilt_inventory = build_product_tool_registry(
        changed_registrations
    )
    base_by_name = {item.name: item for item in base_inventory}
    rebuilt_by_name = {item.name: item for item in rebuilt_inventory}
    assert (
        rebuilt_by_name["read_file"].execution_identity
        == base_by_name["read_file"].execution_identity
    )
    assert (
        rebuilt_by_name["web_search"].execution_identity
        != base_by_name["web_search"].execution_identity
    )

    specs = base_registry.specs
    raw_specs = [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": thaw_json(spec.input_schema),
        }
        for spec in specs
    ]
    authorities = SdkRunToolAuthorityRegistry()
    authorities.prepare_run(
        run_id="run-stable",
        session_id="session-stable",
        request_id="request-stable",
        root_run_id="root-stable",
        task_scope_id="task-stable",
        workspace_root=str(tmp_path),
        catalog={
            "generation": 1,
            "content_fingerprint": canonical_sha256(raw_specs),
            "specs": raw_specs,
            "schema_fingerprints": {
                item["name"]: canonical_hash(item["input_schema"])
                for item in raw_specs
            },
        },
        inventory=base_inventory,
    )
    rebuilt_registry.bind_run_authorities(authorities)
    executor = ProductEffectExecutor(
        uow=object(),
        registry=rebuilt_registry,
        authorization=object(),
        reconciliation=object(),
    )
    source = tmp_path / "stable.txt"
    source.write_text("stable-handler", encoding="utf-8")
    call = ToolCall(CallId("call-stable"), "read_file", {"path": str(source)})
    context = ToolContext(
        RunId("run-stable"),
        RequestId("request-stable"),
        CancellationToken(),
        {
            "session_id": "session-stable",
            "scope_id": "scope-stable",
            "workspace": str(tmp_path),
            "write_scope_root": str(tmp_path),
        },
    )

    await executor._prepared(
        effect_id=EffectId("effect-stable"), call=call, context=context
    )
    result = await rebuilt_registry.invoke(call, context)

    assert result.error_code is None
    assert "stable-handler" in str(thaw_json(result.value))


def test_checked_in_real_manifest_has_exact_77_plus_two_projection() -> None:
    from deskpet.tool_catalog import load_tool_manifest

    manifest = load_tool_manifest()

    assert manifest.manifest_sha256 == EXPECTED_MANIFEST_SHA256
    assert manifest.pre_cutover_count == 79
    assert len(manifest.tools) == 77
    assert tuple(sorted(manifest.workflows)) == (
        "workflow.deep_research",
        "workflow.presentation",
    )
    assert "deepresearch" not in manifest.tool_names
    assert "ppt_pro" not in manifest.tool_names
    assert "ppt_create" in manifest.tool_names
    with pytest.raises(TypeError):
        manifest.tools[0]["name"] = "tampered"  # type: ignore[index]
    with pytest.raises(TypeError):
        manifest.workflows["workflow.deep_research"]["workflow_version"] = "bad"  # type: ignore[index]


def test_schema_migrations_are_exact_closed_and_sdk_valid() -> None:
    from deskpet.tool_catalog import load_tool_manifest, migrate_tool_schemas

    manifest = load_tool_manifest()
    migrated, records = migrate_tool_schemas(manifest)

    assert len(records) == 70
    specialized = {
        record.name
        for record in records
        if record.disposition != "closed_object_contract"
    }
    assert specialized == {
        "app_launch",
        "capability_build",
        "capability_repair",
        "doc_create",
        "doc_edit",
        "download_file",
        "excel_create",
        "move_file",
        "ppt_create",
        "process_start",
        "window_capture",
        "window_focus",
        "window_key",
        "workflow_spawn",
    }
    assert all(record.old_hash != record.new_hash for record in records)
    assert len(migrated) == 77
    assert sum(len(record.closed_object_paths) for record in records) == 71

    def assert_closed(node, path="$" ) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, path
            for key, value in node.items():
                assert_closed(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                assert_closed(value, f"{path}[{index}]")

    # Construction is the SDK's public fail-closed schema validator.
    from simple_harness.tools import ToolSpec

    for name, schema in migrated.items():
        assert_closed(schema["parameters"])
        ToolSpec(name, schema["description"], schema["parameters"])


def test_tools_package_import_is_pure_in_new_process() -> None:
    backend = Path(__file__).resolve().parents[2]
    command = """
import json, sys
import deskpet.tools
loaded = sorted(
    name for name in sys.modules
    if name.startswith('deskpet.tools.') and name != 'deskpet.tools.registry'
)
print(json.dumps({'loaded': loaded}))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(backend)
    result = subprocess.run(
        [sys.executable, "-c", command],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    payload = json.loads(result.stdout)
    assert payload == {"loaded": []}


def test_closed_ingress_legacy_main_sequence_reaches_74_without_retired_memory_tools() -> None:
    backend = Path(__file__).resolve().parents[2]
    command = """
import importlib
from types import SimpleNamespace
from deskpet.tool_catalog import load_tool_manifest

dynamic = {
    'agent', 'agent_parallel', 'await_subagents', 'context_page_in',
    'memory_recall', 'memory_search', 'spawn_subagents', 'spawn_team', 'todo_write',
    'tool_activate', 'tool_describe', 'tool_search', 'web_search',
}
for item in load_tool_manifest().tools:
    if item['name'] in dynamic:
        continue
    identity = item.get('context_handler_id') or item['handler_id']
    if '<locals>' in identity:
        identity = item['handler_id']
    module_name, qualname = identity.split(':', 1)
    value = importlib.import_module(module_name)
    for component in qualname.split('.'):
        value = getattr(value, component)
    assert callable(value)

from deskpet.tools import registry
base_names = registry.list_tools()
assert len(base_names) == 40, (len(base_names), base_names)
assert {'generate_image', 'ppt_create', 'ppt_pro'} <= set(base_names)
from deskpet.tools.os_tools import register_os_tools
from deskpet.tools.code_tools import register_code_tools
from deskpet.tools.code_tools.spawn_subagents_tool import build_await_subagents_tool, product_delegation_tool_catalog
from deskpet.tools.code_tools.todo_write_tool import build_todo_write_tool
from deskpet.tools.context_page_in_tools import ContextPageInStore, register_context_page_in
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.tools.orchestration_controls import register_orchestration_controls
from deskpet.tools.capabilities import ToolCapabilityBridgeService, ToolCapabilityScopeStore
from deskpet.tools.tool_search import register_capability_bridge_tools
register_os_tools(registry)
delegates = product_delegation_tool_catalog()
todo_handler, todo_schema = build_todo_write_tool(object(), session_id_resolver=lambda: 's')
await_handler, await_schema = build_await_subagents_tool(lambda: SimpleNamespace(execution_uow=None))
register_code_tools(
    registry, todo_write_handler=todo_handler, todo_write_schema=todo_schema,
    agent_handler=delegates['agent'][0], agent_schema=delegates['agent'][1],
    agent_parallel_handler=delegates['agent_parallel'][0], agent_parallel_schema=delegates['agent_parallel'][1],
    spawn_team_handler=delegates['spawn_team'][0], spawn_team_schema=delegates['spawn_team'][1],
    spawn_subagents_handler=delegates['spawn_subagents'][0], spawn_subagents_schema=delegates['spawn_subagents'][1],
    await_subagents_handler=await_handler, await_subagents_schema=await_schema,
)
page_store = ContextPageInStore()
register_context_page_in(registry, page_store, execution_context_getter=lambda: SimpleNamespace(session_id='s', request_id='r', scope_id='scope'))
profiles = ProfileRegistry((ProfileSpec('agent.general', 'general', 'react', display_name='General'),))
register_orchestration_controls(registry, profiles)
register_capability_bridge_tools(registry, ToolCapabilityBridgeService(registry, ToolCapabilityScopeStore()))
names = registry.list_tools()
assert len(names) == 74, (len(names), names)
assert 'memory_recall' not in names
assert 'deepresearch' in names and 'ppt_pro' in names and 'ppt_create' in names
print(len(names))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(backend)
    result = subprocess.run(
        [sys.executable, "-c", command],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.stdout.strip() == "74"


def test_sdk_registry_builder_rejects_duplicate_without_partial_publish() -> None:
    from deskpet.sdk_adapters.tools import (
        PRODUCT_TOOL_NAMES,
        ProductToolRegistration,
        build_product_tool_registry,
    )

    async def handler(_arguments, _context):
        return {"ok": True}

    registrations = [
        ProductToolRegistration(
            name=name,
            description=name,
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=handler,
            dispatch_kind="sync",
            permission_category="read_file",
            metadata={"source": "real-manifest", "version": "1"},
        )
        for name in PRODUCT_TOOL_NAMES
    ]
    registrations.append(registrations[0])

    published = None
    with pytest.raises(ValueError, match="duplicate product Tool"):
        published = build_product_tool_registry(registrations)

    assert published is None


def _catalog_dependencies(page_store, execution_context):
    from deskpet.tool_catalog import ToolCatalogDependencies

    class SearchResponse:
        def to_dict(self):
            return {
                "results": [{"url": "https://example.invalid", "title": "real factory"}],
                "provider": "typed-double",
            }

    class SearchGateway:
        calls = 0

        async def search(self, request):
            self.calls += 1
            assert request.query == "phase-b"
            return SearchResponse()

    class TodoStore:
        async def replace_session_todos(self, _session_id, _items):
            return None

    class MemoryQuery:
        async def recall_readonly(self, _query, _limit, _scope):
            return []

    class MemoryScope:
        def resolve_for_run(self, _run_id):
            return object()

    class CapabilityBridge:
        def search(self, *_args, **_kwargs): return []
        def describe(self, *_args, **_kwargs): return {}
        def suggestions(self, *_args, **_kwargs): return []
        def activate(self, *_args, **_kwargs): raise AssertionError("not invoked")

    return ToolCatalogDependencies(
        todo_session_db=TodoStore(),
        workflow_service_provider=lambda: None,
        context_page_store=page_store,
        execution_context_getter=lambda: execution_context,
        memory_query=MemoryQuery(),
        memory_scope_resolver=MemoryScope(),
        capability_bridge_service=CapabilityBridge(),
        search_gateway=SearchGateway(),
    )


def test_explicit_catalog_builds_77_real_handlers_without_legacy_registry() -> None:
    backend = Path(__file__).resolve().parents[2]
    code = """
import json, sys
from types import SimpleNamespace
from deskpet.tool_catalog import ToolCatalogDependencies, build_explicit_product_tool_catalog
from deskpet.tools.context_page_in_tools import ContextPageInStore
class Search:
    async def search(self, request): raise AssertionError('not invoked')
class Todo:
    async def replace_session_todos(self, session_id, items): pass
class MemoryQuery:
    async def recall_readonly(self, query, limit, scope): return []
class MemoryScope:
    def resolve_for_run(self, run_id): return object()
class Bridge:
    def search(self, *args, **kwargs): return []
    def describe(self, *args, **kwargs): return {}
    def suggestions(self, *args, **kwargs): return []
    def activate(self, *args, **kwargs): raise AssertionError('not invoked')
deps = ToolCatalogDependencies(
    Todo(), lambda: None, ContextPageInStore(),
    lambda: SimpleNamespace(session_id='s', request_id='r', scope_id='scope'),
    MemoryQuery(), MemoryScope(), Bridge(), Search(),
)
catalog = build_explicit_product_tool_catalog(deps)
print(json.dumps({
    'count': len(catalog.registrations),
    'legacy_loaded': 'deskpet.tools.registry' in sys.modules,
    'config_loaded': 'config' in sys.modules,
    'blocked_loaded': sorted(name for name in sys.modules if name.startswith(
        ('deskpet.harness', 'deskpet.workflows')
    )),
    'handlers': len({item.metadata['handler_id'] for item in catalog.registrations}),
}))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(backend)
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert json.loads(result.stdout) == {
        "count": 77,
        "legacy_loaded": False,
        "config_loaded": False,
        "blocked_loaded": [],
        "handlers": 77,
    }


def test_each_of_64_static_handler_resolutions_is_import_pure() -> None:
    from deskpet.tool_catalog import load_tool_manifest

    backend = Path(__file__).resolve().parents[2]
    dynamic = {
        "agent", "agent_parallel", "await_subagents", "context_page_in",
        "memory_recall", "memory_search", "spawn_subagents", "spawn_team", "todo_write",
        "tool_activate", "tool_describe", "tool_search", "web_search",
    }
    descriptors = [
        item for item in load_tool_manifest().tools
        if str(item["name"]) not in dynamic
    ]
    assert len(descriptors) == 64
    env = dict(os.environ)
    env["PYTHONPATH"] = str(backend)
    probe = """
import importlib, json, sys
module_name, qualname = sys.argv[1], sys.argv[2]
value = importlib.import_module(module_name)
for component in qualname.split('.'):
    value = getattr(value, component)
assert callable(value)
print(json.dumps({
    'blocked': sorted(name for name in sys.modules if name == 'config' or name.startswith(
        ('deskpet.tools.registry', 'deskpet.tools._catalog_compat',
         'deskpet.harness', 'deskpet.workflows')
    )),
}))
"""
    failures: dict[str, object] = {}
    for item in descriptors:
        identity = str(item.get("context_handler_id") or item["handler_id"])
        if "<locals>" in identity:
            identity = str(item["handler_id"])
        module_name, qualname = identity.split(":", 1)
        result = subprocess.run(
            [sys.executable, "-c", probe, module_name, qualname],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        blocked = json.loads(result.stdout)["blocked"]
        if blocked:
            failures[str(item["name"])] = blocked
    assert failures == {}


def test_environment_schema_adapter_preserves_map_and_rejects_unsafe_entries() -> None:
    from deskpet.tool_catalog import adapt_model_arguments

    assert adapt_model_arguments(
        "process_start",
        {"environment": [{"key": "PATH_SUFFIX", "value": "safe"}]},
    )["environment"] == {"PATH_SUFFIX": "safe"}
    with pytest.raises(ValueError, match="unique"):
        adapt_model_arguments(
            "app_launch",
            {"environment": [{"key": "X", "value": "1"}, {"key": "X", "value": "2"}]},
        )
    with pytest.raises(ValueError, match="bounded"):
        adapt_model_arguments(
            "app_launch",
            {"environment": [{"key": f"K{index}", "value": "x"} for index in range(65)]},
        )


def test_all_specialized_schema_adapters_preserve_handler_values_and_reject_invalid() -> None:
    from deskpet.tool_catalog import adapt_model_arguments

    assert adapt_model_arguments(
        "capability_build",
        {
            "original_args": [
                {"key": "count", "value_json": "2"},
                {"key": "nested", "value_json": '{"ok":true}'},
            ]
        },
    )["original_args"] == {"count": 2, "nested": {"ok": True}}
    with pytest.raises(ValueError, match="unique"):
        adapt_model_arguments(
            "capability_build",
            {
                "original_args": [
                    {"key": "same", "value_json": "1"},
                    {"key": "same", "value_json": "2"},
                ]
            },
        )

    digest = "a" * 64
    for tool_name, field in (
        ("capability_repair", "failure_receipt_ref"),
        ("download_file", "expected_sha256"),
        ("move_file", "expected_source_hash"),
    ):
        assert adapt_model_arguments(tool_name, {field: digest})[field] == digest
        with pytest.raises(ValueError, match="64 hexadecimal"):
            adapt_model_arguments(tool_name, {field: "not-a-digest"})

    for tool_name, field, value in (
        ("doc_create", "spec", '{"title":"x"}'),
        ("doc_edit", "ops", '[{"op":"replace"}]'),
        ("excel_create", "spec", '{"sheets":[]}'),
        ("ppt_create", "outline", '[{"title":"x"}]'),
    ):
        assert adapt_model_arguments(tool_name, {field: value})[field] == value
        with pytest.raises(json.JSONDecodeError):
            adapt_model_arguments(tool_name, {field: "{"})

    for tool_name in ("window_capture", "window_focus", "window_key"):
        assert adapt_model_arguments(tool_name, {"creation_time": 1.25})[
            "creation_time"
        ] == 1.25
        with pytest.raises(ValueError, match="greater than zero"):
            adapt_model_arguments(tool_name, {"creation_time": 0})

    assert adapt_model_arguments("workflow_spawn", {}) == {}
    assert adapt_model_arguments(
        "workflow_spawn", {"workspace_ref": "/tmp/workspace"}
    )["workspace_ref"] == "/tmp/workspace"


def test_all_14_specialized_migrations_reach_equivalent_real_handlers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import importlib
    import inspect

    from simple_harness import CallId, RequestId, RunId, thaw_json
    from simple_harness.tools import CancellationToken, ToolCall, ToolContext, ToolOutcome

    from deskpet.sdk_adapters.tools import build_product_tool_registry
    from deskpet.tool_catalog import build_explicit_product_tool_catalog, load_tool_manifest
    from deskpet.tools.capabilities import ToolExecutionContext
    from deskpet.tools.context_page_in_tools import ContextPageInStore

    # The two environment migrations must be observed by the real handlers,
    # not merely by the argument adapter.
    environments: list[dict[str, str] | None] = []

    class ProcessService:
        async def start(self, **kwargs):
            environments.append(kwargs.get("environment"))
            return {
                "pid": 101,
                "creation_time": 10.0,
                "command_line": [kwargs["executable"]],
                "executable": kwargs["executable"],
                "parent_pid": None,
                "lease_id": "lease-1",
                "root_run_id": kwargs["root_run_id"],
            }

    from deskpet.tools.os_tools import app_tools, process_tools
    from deskpet.tools import window_use_tool

    service = ProcessService()
    monkeypatch.setattr(app_tools, "get_process_tool_service", lambda: service)
    monkeypatch.setattr(process_tools, "_PROCESS_SERVICE", service)
    monkeypatch.setattr(
        app_tools,
        "discover_app",
        lambda _query: [{
            "source": "fixture", "executable": "/bin/echo",
            "discovered": True, "launchable_probe": True,
        }],
    )
    class ProcessProbe:
        def __init__(self, _pid): pass
        def create_time(self): return 10.0
        def is_running(self): return True

    monkeypatch.setattr(app_tools.psutil, "Process", ProcessProbe)
    monkeypatch.setattr(
        window_use_tool,
        "_guard",
        lambda: json.dumps({"ok": False, "error": "window-boundary-double"}),
    )

    runtime_context = SimpleNamespace(
        session_id="session-1", request_id="request-1", scope_id="scope-1"
    )
    registry, _ = build_product_tool_registry(
        build_explicit_product_tool_catalog(
            _catalog_dependencies(ContextPageInStore(), runtime_context)
        ).registrations
    )
    sdk_context = ToolContext(
        RunId("run-1"), RequestId("request-1"), CancellationToken(),
        {"session_id": "session-1", "scope_id": "scope-1",
         "write_scope_root": str(tmp_path), "workspace": str(tmp_path)},
    )
    legacy_context = ToolExecutionContext(
        scope_id="scope-1", session_id="session-1", request_id="request-1",
        root_run_id="run-1", run_id="run-1", call_id="legacy-call",
        effect_id="legacy-call", workspace=str(tmp_path),
        write_scope_root=str(tmp_path), owner_key="",
    )
    manifest = {str(item["name"]): item for item in load_tool_manifest().tools}

    async def direct(name: str, arguments: dict):
        item = manifest[name]
        identity = str(item.get("context_handler_id") or item["handler_id"])
        if "<locals>" in identity:
            identity = str(item["handler_id"])
        module_name, qualname = identity.split(":", 1)
        value = importlib.import_module(module_name)
        for component in qualname.split("."):
            value = getattr(value, component)
        parameters = inspect.signature(value).parameters
        if (
            item.get("context_handler_id") == identity
            and item.get("context_handler_id") != item["handler_id"]
        ):
            result = value(arguments, legacy_context)
        elif "execution_context" in parameters:
            result = value(
                arguments, "legacy-call", execution_context=legacy_context
            )
        else:
            result = value(arguments, "legacy-call")
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, str):
            try:
                return json.loads(result)
            except ValueError:
                return result
        return result

    async def sdk(name: str, arguments: dict, index: int):
        return await registry.invoke(
            ToolCall(CallId(f"sdk-{index}"), name, arguments), sdk_context
        )

    async def case() -> None:
        environment = {"MIGRATION_EQ": "yes"}
        app_old = await direct(
            "app_launch", {"app": "/bin/echo", "argv": [], "environment": environment}
        )
        app_new = await sdk(
            "app_launch",
            {"app": "/bin/echo", "argv": [], "environment": [
                {"key": "MIGRATION_EQ", "value": "yes"}
            ]},
            1,
        )
        process_old = await direct(
            "process_start", {"executable": "/bin/echo", "argv": [], "environment": environment}
        )
        process_new = await sdk(
            "process_start",
            {"executable": "/bin/echo", "argv": [], "environment": [
                {"key": "MIGRATION_EQ", "value": "yes"}
            ]},
            2,
        )
        assert app_old["ok"] and process_old["ok"]
        assert app_new.outcome is process_new.outcome is ToolOutcome.SUCCEEDED
        assert environments == [environment, environment, environment, environment]

        digest = "a" * 64
        cases = (
            ("capability_build", {"objective": "build", "original_args": {"count": 2},
                                  "catalog_generation": 1},
             {"objective": "build", "original_args": [
                 {"key": "count", "value_json": "2"}
             ], "catalog_generation": 1}),
            ("capability_repair", {"failure_receipt_ref": digest, "catalog_generation": 1},
             {"failure_receipt_ref": digest, "catalog_generation": 1}),
            ("doc_create", {"spec": {"title": "x"}},
             {"spec": '{"title":"x"}'}),
            ("doc_edit", {"file_path": str(tmp_path / "missing.docx"), "ops": []},
             {"file_path": str(tmp_path / "missing.docx"), "ops": "[]"}),
            ("download_file", {"url": "invalid://url", "destination": str(tmp_path / "download"),
                               "max_bytes": 1024, "expected_sha256": digest},
             {"url": "invalid://url", "destination": str(tmp_path / "download"),
              "max_bytes": 1024, "expected_sha256": digest}),
            ("excel_create", {"spec": {"sheets": []}},
             {"spec": '{"sheets":[]}'}),
            ("move_file", {"source": str(tmp_path / "missing"),
                           "destination": str(tmp_path / "dest"),
                           "expected_source_hash": digest},
             {"source": str(tmp_path / "missing"),
              "destination": str(tmp_path / "dest"),
              "expected_source_hash": digest}),
            ("ppt_create", {"outline": [], "dry_run": True},
             {"outline": "[]", "dry_run": True}),
            ("window_capture", {"pid": 1, "creation_time": 1.0, "hwnd": 1},
             {"pid": 1, "creation_time": 1.0, "hwnd": 1}),
            ("window_focus", {"pid": 1, "creation_time": 1.0, "hwnd": 1},
             {"pid": 1, "creation_time": 1.0, "hwnd": 1}),
            ("window_key", {"pid": 1, "creation_time": 1.0, "hwnd": 1,
                            "keys": "ENTER"},
             {"pid": 1, "creation_time": 1.0, "hwnd": 1, "keys": "ENTER"}),
            ("workflow_spawn", {"profile_key": "agent.general", "objective": "x",
                                "output_refs": [], "catalog_generation": 1,
                                "workspace_ref": None},
             {"profile_key": "agent.general", "objective": "x",
              "output_refs": [], "catalog_generation": 1}),
        )
        assert len(cases) == 12
        for index, (name, old_arguments, new_arguments) in enumerate(cases, 3):
            old_result = await direct(name, old_arguments)
            new_result = await sdk(name, new_arguments, index)
            old_ok = not (
                isinstance(old_result, dict)
                and (old_result.get("ok") is False or old_result.get("error"))
            )
            assert (new_result.outcome is ToolOutcome.SUCCEEDED) is old_ok, (
                name, old_result, new_result
            )
            if name == "ppt_create" and old_ok:
                assert thaw_json(new_result.value)["slide_count"] == old_result["slide_count"]

    asyncio.run(case())


def test_sdk_await_subagents_uses_typed_port_and_never_loads_old_harness() -> None:
    import asyncio

    from simple_harness import CallId, RequestId, RunId, thaw_json
    from simple_harness.tools import CancellationToken, ToolCall, ToolContext, ToolOutcome

    from deskpet.sdk_adapters.tools import build_product_tool_registry
    from deskpet.tool_catalog import build_explicit_product_tool_catalog
    from deskpet.tools.context_page_in_tools import ContextPageInStore

    calls: list[tuple[dict, object, str]] = []

    class JoinPort:
        async def await_subagents(self, *, arguments, context, operation_key):
            calls.append((arguments, context, operation_key))
            return {"ok": True, "results": [{"run_id": "child-1"}]}

    dependencies = _catalog_dependencies(
        ContextPageInStore(),
        SimpleNamespace(session_id="session-1", request_id="request-1", scope_id="scope-1"),
    )
    dependencies = type(dependencies)(
        dependencies.todo_session_db,
        lambda: JoinPort(),
        dependencies.context_page_store,
        dependencies.execution_context_getter,
        dependencies.memory_query,
        dependencies.memory_scope_resolver,
        dependencies.capability_bridge_service,
        dependencies.search_gateway,
    )
    registry, _ = build_product_tool_registry(
        build_explicit_product_tool_catalog(dependencies).registrations
    )
    context = ToolContext(
        RunId("run-1"),
        RequestId("request-1"),
        CancellationToken(),
        {"session_id": "session-1", "scope_id": "scope-1"},
    )

    async def case() -> None:
        result = await registry.invoke(
            ToolCall(CallId("join-call-1"), "await_subagents", {"run_ids": ["child-1"]}),
            context,
        )
        assert result.outcome is ToolOutcome.SUCCEEDED
        assert thaw_json(result.value)["results"][0]["run_id"] == "child-1"

    before = set(sys.modules)
    asyncio.run(case())
    newly_blocked = sorted(
        name
        for name in set(sys.modules) - before
        if name.startswith(("deskpet.harness", "deskpet.workflows"))
    )
    assert newly_blocked == []
    assert calls[0][0] == {"run_ids": ["child-1"]}
    assert calls[0][2] == "join-call-1"
    assert calls[0][1].call_id == "join-call-1"


def test_six_dispatch_families_invoke_real_product_handlers(tmp_path: Path) -> None:
    import asyncio

    from simple_harness import CallId, RequestId, RunId, thaw_json
    from simple_harness.tools import CancellationToken, ToolCall, ToolContext, ToolOutcome

    from deskpet.sdk_adapters.tools import build_product_tool_registry
    from deskpet.tool_catalog import build_explicit_product_tool_catalog
    from deskpet.tools.context_page_in_tools import ContextPageInStore

    source = tmp_path / "source.txt"
    source.write_text("real-sync-handler", encoding="utf-8")
    destination = tmp_path / "destination.txt"
    workspace = tmp_path / "workspace"
    runtime_context = SimpleNamespace(
        session_id="session-1",
        request_id="request-1",
        scope_id="scope-1",
    )
    page_store = ContextPageInStore()
    page_ref = page_store.put(
        kind="segment",
        source="fixture",
        content="real-context-handler",
        session_id="session-1",
        request_id="request-1",
        scope_id="scope-1",
    )
    catalog = build_explicit_product_tool_catalog(
        _catalog_dependencies(page_store, runtime_context)
    )
    registry, inventory = build_product_tool_registry(catalog.registrations)
    by_name = {item.name: item for item in inventory}
    assert by_name["read_file"].dispatch_kind == "sync"
    assert by_name["process_list"].dispatch_kind == "async"
    assert by_name["context_page_in"].dispatch_kind == "context"
    assert by_name["write_file"].dispatch_kind == "staged"
    assert by_name["workspace_prepare"].dispatch_kind == "control"
    assert by_name["web_search"].dispatch_kind == "provider"

    context = ToolContext(
        RunId("run-1"),
        RequestId("request-1"),
        CancellationToken(),
        {
            "session_id": "session-1",
            "scope_id": "scope-1",
            "workspace": str(workspace),
            "write_scope_root": str(tmp_path),
        },
    )

    async def invoke(name: str, arguments: dict, index: int):
        result = await registry.invoke(
            ToolCall(CallId(f"call-{index}"), name, arguments),
            context,
        )
        assert result.outcome is ToolOutcome.SUCCEEDED, (name, result.error)
        return thaw_json(result.value)

    async def case() -> None:
        sync = await invoke("read_file", {"path": str(source)}, 1)
        assert "real-sync-handler" in json.dumps(sync)
        await invoke("process_list", {"max_entries": 1}, 2)
        page = await invoke(
            "context_page_in",
            {"reference_id": page_ref.reference_id, "source_hash": page_ref.source_hash},
            3,
        )
        assert page["content"] == "real-context-handler"
        await invoke(
            "write_file",
            {"path": str(destination), "content": "real-staged-handler", "overwrite": True},
            4,
        )
        assert destination.read_text(encoding="utf-8") == "real-staged-handler"
        control = await invoke("workspace_prepare", {}, 5)
        assert control["result"]["workspace_root"] == str(workspace)
        provider = await invoke("web_search", {"query": "phase-b", "max_results": 1}, 6)
        assert provider["provider"] == "typed-double"

    asyncio.run(case())


def test_required_provider_failure_occurs_before_catalog_publish() -> None:
    from deskpet.tool_catalog import ToolCatalogDependencies

    with pytest.raises(RuntimeError, match="search_gateway"):
        ToolCatalogDependencies(
            object(), lambda: None, object(), lambda: None,
            object(), object(), object(), None,
        )


def test_real_call_id_drives_idempotency_and_write_scope_fence(tmp_path: Path) -> None:
    import asyncio

    from simple_harness import CallId, RequestId, RunId
    from simple_harness.tools import CancellationToken, ToolCall, ToolContext, ToolOutcome

    from deskpet.sdk_adapters.tools import build_product_tool_registry
    from deskpet.tool_catalog import build_explicit_product_tool_catalog
    from deskpet.tools.context_page_in_tools import ContextPageInStore
    from deskpet.tools.project_group_send import configure_project_group_transport

    class Transport:
        def __init__(self) -> None:
            self.keys: list[str] = []

        def send_all_project_groups(self, *, content: str, idempotency_key: str):
            self.keys.append(idempotency_key)
            return {
                "ok": True,
                "delivery_ref": content,
                "physical_send_count": 1,
            }

    transport = Transport()
    configure_project_group_transport(transport)
    try:
        runtime_context = SimpleNamespace(
            session_id="session-1", request_id="request-1", scope_id="scope-1"
        )
        catalog = build_explicit_product_tool_catalog(
            _catalog_dependencies(ContextPageInStore(), runtime_context)
        )
        registry, _ = build_product_tool_registry(catalog.registrations)
        allowed = tmp_path / "allowed"
        outside = tmp_path / "outside.txt"
        context = ToolContext(
            RunId("run-1"),
            RequestId("same-request"),
            CancellationToken(),
            {
                "session_id": "session-1",
                "scope_id": "scope-1",
                "workspace": str(allowed),
                "write_scope_root": str(allowed),
            },
        )

        async def case() -> None:
            denied = await registry.invoke(
                ToolCall(
                    CallId("write-call"),
                    "write_file",
                    {"path": str(outside), "content": "must-not-write", "overwrite": True},
                ),
                context,
            )
            assert denied.outcome is ToolOutcome.FAILED
            assert not outside.exists()

            for call_id in ("delivery-call-1", "delivery-call-2"):
                result = await registry.invoke(
                    ToolCall(
                        CallId(call_id),
                        "project_group_send",
                        {"target_scope": "all_project_groups", "content": call_id},
                    ),
                    context,
                )
                assert result.outcome is ToolOutcome.SUCCEEDED
            assert transport.keys == ["delivery-call-1", "delivery-call-2"]

        asyncio.run(case())
    finally:
        configure_project_group_transport(None)


def test_sdk_tool_context_without_metadata_uses_app_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from simple_harness import CallId, RequestId, RunId
    from simple_harness.tools import CancellationToken, ToolCall, ToolContext, ToolOutcome

    from deskpet.sdk_adapters.tools import build_product_tool_registry
    from deskpet.tool_catalog import build_explicit_product_tool_catalog
    from deskpet.tools.context_page_in_tools import ContextPageInStore

    profile = tmp_path / "profile"
    monkeypatch.delenv("DESKPET_WORKSPACE_DIR", raising=False)
    monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(profile))
    runtime_context = SimpleNamespace(
        session_id="session-1", request_id="request-1", scope_id="scope-1"
    )
    registry, _ = build_product_tool_registry(
        build_explicit_product_tool_catalog(
            _catalog_dependencies(ContextPageInStore(), runtime_context)
        ).registrations
    )

    async def case() -> None:
        result = await registry.invoke(
            ToolCall(
                CallId("write-default-workspace"),
                "write_file",
                {"path": "sdk-bound.txt", "content": "bound"},
            ),
            ToolContext(
                RunId("run-default-workspace"),
                RequestId("request-default-workspace"),
                CancellationToken(),
            ),
        )
        assert result.outcome is ToolOutcome.SUCCEEDED

    asyncio.run(case())
    assert (profile / "workspace" / "sdk-bound.txt").read_text() == "bound"


def test_memory_recall_and_search_dispatch_to_live_memory_sdk_for_ordinary_run(
    tmp_path: Path,
) -> None:
    import asyncio

    from simple_harness import CallId, RequestId, RunId, thaw_json
    from simple_harness.tools import CancellationToken, ToolCall, ToolContext, ToolOutcome
    from simple_harness_memory.backends.sqlite import SQLiteMemoryBackend

    from deskpet.companion.contracts import CompanionStateError
    from deskpet.memory.recall_adapter import OwnerMemoryRecallQueryAdapter
    from deskpet.sdk_adapters.tools import build_product_tool_registry
    from deskpet.tool_catalog import build_explicit_product_tool_catalog
    from deskpet.tools.context_page_in_tools import ContextPageInStore

    class OrdinaryRunScopeResolver:
        def resolve_for_run(self, _run_id):
            raise CompanionStateError("owner_memory_scope_missing_or_ambiguous")

    async def case() -> None:
        memory_backend = SQLiteMemoryBackend(
            str(tmp_path / "memory.db"),
            auto_extract_facts=False,
        )
        await memory_backend.initialize()
        await memory_backend.append_message(
            "session-memory",
            "user",
            "The project codename is Aurora Zebra.",
        )
        dependencies = _catalog_dependencies(
            ContextPageInStore(),
            SimpleNamespace(
                session_id="session-memory",
                request_id="request-memory",
                scope_id="scope-memory",
            ),
        )
        dependencies = type(dependencies)(
            dependencies.todo_session_db,
            dependencies.workflow_service_provider,
            dependencies.context_page_store,
            dependencies.execution_context_getter,
            OwnerMemoryRecallQueryAdapter(memory_backend),
            OrdinaryRunScopeResolver(),
            dependencies.capability_bridge_service,
            dependencies.search_gateway,
        )
        registry, _ = build_product_tool_registry(
            build_explicit_product_tool_catalog(dependencies).registrations
        )
        context = ToolContext(
            RunId("ordinary-sdk-run"),
            RequestId("ordinary-sdk-request"),
            CancellationToken(),
        )
        try:
            recall = await registry.invoke(
                ToolCall(
                    CallId("memory-recall-call"),
                    "memory_recall",
                    {"query": "Aurora Zebra", "limit": 5},
                ),
                context,
            )
            search = await registry.invoke(
                ToolCall(
                    CallId("memory-search-call"),
                    "memory_search",
                    {"query": "Aurora Zebra", "top_k": 5},
                ),
                context,
            )
        finally:
            await memory_backend.close()

        assert recall.outcome is ToolOutcome.SUCCEEDED
        assert search.outcome is ToolOutcome.SUCCEEDED
        recall_items = thaw_json(recall.value)["items"]
        search_items = thaw_json(search.value)["items"]
        assert recall_items[0]["text"] == "The project codename is Aurora Zebra."
        assert search_items[0]["text"] == "The project codename is Aurora Zebra."

    asyncio.run(case())


@pytest.mark.parametrize("tool_name", ["memory_recall", "memory_search"])
@pytest.mark.parametrize(
    ("scope_mode", "expected_error"),
    [
        ("valid", None),
        ("none", "memory_scope_resolver_invalid_result"),
        ("wrong_type", "memory_scope_resolver_invalid_result"),
        ("other_companion_error", "owner_memory_scope_dependency_hash_mismatch"),
        ("ordinary_missing_scope", None),
    ],
)
def test_memory_read_handlers_fail_closed_except_exact_ordinary_scope_absence(
    tool_name: str,
    scope_mode: str,
    expected_error: str | None,
) -> None:
    import asyncio

    from deskpet.companion.companion_message_projection import (
        OwnerMemoryReadScopeV1,
        canonical_hash,
    )
    from deskpet.companion.contracts import CompanionStateError
    from deskpet.memory.recall_adapter import (
        build_memory_recall_handlers,
        build_memory_search_handler,
    )

    session_ids = ("session-owner",)
    session_set = {
        "profile_id": "profile-owner",
        "profile_generation": 1,
        "binding_epoch": 1,
        "session_set_version": 1,
        "session_ids": list(session_ids),
    }
    session_set_hash = canonical_hash(session_set)
    scope = OwnerMemoryReadScopeV1(
        profile_id="profile-owner",
        profile_generation=1,
        binding_epoch=1,
        session_ids=session_ids,
        session_set_version=1,
        session_set_hash=session_set_hash,
        as_of_message_id=7,
        scope_hash=canonical_hash({
            "schema_version": 1,
            "profile_id": "profile-owner",
            "profile_generation": 1,
            "binding_epoch": 1,
            "session_set_version": 1,
            "session_set_hash": session_set_hash,
            "as_of_message_id": 7,
        }),
    )

    class Query:
        def __init__(self) -> None:
            self.scopes = []

        async def recall_readonly(self, _query, _limit, owner_scope):
            self.scopes.append(owner_scope)
            return []

    class Resolver:
        def resolve_for_run(self, _run_id):
            if scope_mode == "valid":
                return scope
            if scope_mode == "none":
                return None
            if scope_mode == "wrong_type":
                return object()
            if scope_mode == "other_companion_error":
                raise CompanionStateError(
                    "owner_memory_scope_dependency_hash_mismatch"
                )
            raise CompanionStateError("owner_memory_scope_missing_or_ambiguous")

    query = Query()
    resolver = Resolver()
    if tool_name == "memory_recall":
        _reject, handler = build_memory_recall_handlers(query, resolver)
        arguments = {"query": "Aurora", "limit": 5}
    else:
        handler = build_memory_search_handler(query, resolver)
        arguments = {"query": "Aurora", "top_k": 5}
    context = SimpleNamespace(run_id="run-memory", root_run_id="run-memory")

    async def case() -> None:
        if expected_error is None:
            payload = json.loads(await handler(arguments, context))
            assert payload == {"items": [], "ok": True}
            assert query.scopes == [scope if scope_mode == "valid" else None]
            return
        error_type = (
            CompanionStateError
            if scope_mode == "other_companion_error"
            else RuntimeError
        )
        with pytest.raises(error_type, match=expected_error):
            await handler(arguments, context)
        assert query.scopes == []

    asyncio.run(case())
