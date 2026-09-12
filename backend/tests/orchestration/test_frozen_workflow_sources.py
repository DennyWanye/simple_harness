"""Real workflow registrations require shipped source, not only PYZ bytecode."""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]


def source_collection_modes():
    tree = ast.parse((BACKEND / "deskpet-backend.spec").read_text())
    analysis = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name) and node.func.id == "Analysis"
    )
    return ast.literal_eval(next(
        item.value for item in analysis.keywords
        if item.arg == "module_collection_mode"
    ))


@pytest.mark.parametrize("omit", [
    None,
    "deskpet.sdk_adapters.product_workflows",
    "simple_harness.workflows",
])
def test_all_registration_manifests_require_collected_source(tmp_path, omit):
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(BACKEND)
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), str(tmp_path),
         json.dumps(source_collection_modes()), json.dumps(omit)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=45, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def registration_source_probe(stage, modes, omit):
    """In a fresh process, rebase real callables onto a frozen source layout.

    No SDK/inspect patching: functions retain their code and globals, with only
    co_filename relocated. Modules cannot obtain ambient source from a loader.
    The production factories and SDK compile/hash checks run unchanged.
    """
    import inspect
    import linecache
    from types import CodeType, FunctionType

    from deskpet.sdk_adapters.workflows import (
        ACTIVE_PRODUCT_WORKFLOWS,
        build_product_workflow_registrations,
    )
    from simple_harness.workflow import (
        CapabilityBuildHostServices,
        DurableTaskHostServices,
        PersonalWorkflowHostServices,
        WorkflowHostServices,
        compile_workflow_registration,
        workflow_manifest_hash,
    )
    from simple_harness.workflow.errors import WorkflowDefinitionError
    from simple_harness.workflows import build_official_workflow_registrations

    # Factories only check availability; registration must never execute ports.
    class UnusedPorts:
        def forbidden(self, *args, **kwargs):
            raise AssertionError("registration invoked a physical port")
        propose = execute_tools = execute = search = authorize_source = forbidden
        build = store = activate = authorize_build = forbidden

    port = UnusedPorts()
    services = WorkflowHostServices(
        durable_task=DurableTaskHostServices(port, port),
        personal_v1=PersonalWorkflowHostServices(port),
        capability_build=CapabilityBuildHostServices(
            port, port, port, port, port, port, port, port,
        ),
    )
    owner = object()

    def registrations():
        return (
            *build_product_workflow_registrations(generation=1, transaction_owner=owner),
            *build_official_workflow_registrations(
                generation=1, transaction_owner=owner, host_services=services,
            ),
        )

    baseline = registrations()
    expected_keys = set(ACTIVE_PRODUCT_WORKFLOWS) | {
        "workflow.durable_task", "workflow.personal_v1", "workflow.capability_build",
    }
    assert {r.profile.descriptor.key for r in baseline} == expected_keys

    def callables(registration):
        return [node.handler for node in registration.definition.nodes] + [
            edge.selector for edge in registration.definition.conditional_edges
        ]

    functions = [fn for registration in baseline for fn in callables(registration)]
    assert functions and all(isinstance(fn, FunctionType) for fn in functions)
    source_before = {fn: inspect.getsource(fn) for fn in functions}
    modules = {fn.__module__: sys.modules[fn.__module__] for fn in functions}
    paths = {}
    omitted_modules = set()

    def relocate(code, filename):
        return code.replace(
            co_filename=str(filename),
            co_consts=tuple(relocate(v, filename) if isinstance(v, CodeType) else v
                            for v in code.co_consts),
        )

    for name, module in modules.items():
        source = Path(module.__file__).read_bytes()
        destination = stage.joinpath(*name.split(".")).with_suffix(".py")
        matching = [prefix for prefix in modes
                    if name == prefix or name.startswith(prefix + ".")]
        mode = modes[max(matching, key=len)] if matching else "pyz"
        ship_source = "py" in mode.split("+")
        if omit and (name == omit or name.startswith(omit + ".")):
            ship_source = False
            omitted_modules.add(name)
        if ship_source:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source)
        paths[name] = destination
        # Also relocate factory/outer functions so freshly created selectors
        # inherit the staged path rather than their original checkout path.
        for value in list(vars(module).values()):
            if isinstance(value, FunctionType) and value.__module__ == name:
                value.__code__ = relocate(value.__code__, destination)
        module.__file__ = str(destination)
        module.__loader__ = None
        if module.__spec__ is not None:
            module.__spec__.loader = None
            module.__spec__.origin = str(destination)
    for fn in functions:
        fn.__code__ = relocate(fn.__code__, paths[fn.__module__])
    linecache.clearcache()

    if omit:
        assert omitted_modules, "negative control did not remove real callable source"
        failed = set()
        for registration in baseline:
            affected = any(fn.__module__ in omitted_modules for fn in callables(registration))
            try:
                compile_workflow_registration(registration, transaction_owner=owner)
            except WorkflowDefinitionError as exc:
                assert affected and "Cannot create a stable manifest" in str(exc)
                failed.add(registration.profile.descriptor.key)
            else:
                assert not affected, "SDK accepted a callable with no bundled source"
        assert failed
        return

    for fn in functions:
        assert paths[fn.__module__].is_file(), fn.__module__
        assert Path(inspect.getsourcefile(fn)).is_relative_to(stage)
        assert inspect.getsource(fn) == source_before[fn]
    rebuilt = registrations()
    assert len(rebuilt) == len(baseline)
    for before, after in zip(baseline, rebuilt, strict=True):
        assert after.profile.descriptor.key == before.profile.descriptor.key
        assert after.expected_manifest_hash == before.expected_manifest_hash
        assert after.expected_implementation_fingerprint == before.expected_implementation_fingerprint
        compiled = compile_workflow_registration(after, transaction_owner=owner)
        assert workflow_manifest_hash(compiled.manifest) == before.expected_manifest_hash


if __name__ == "__main__":
    registration_source_probe(Path(sys.argv[1]), json.loads(sys.argv[2]), json.loads(sys.argv[3]))
