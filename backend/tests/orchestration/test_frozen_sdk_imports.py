"""Packaging must retain SDK public lazy APIs, including their indirect targets."""

import ast
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = Path(__file__).parents[2] / "deskpet-backend.spec"
PACKAGES = {
    "simple_harness": "simple-harness-sdk",
    "simple_harness_service": "simple-harness-service-sdk",
}


@pytest.fixture(scope="module")
def collected():
    # Execute only the real collection helper and its actual hiddenimports
    # assignment. Never execute Analysis, identity capture, or a frozen build.
    from PyInstaller.utils.hooks import collect_submodules

    tree = ast.parse(SPEC.read_text())
    helper = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_collect_sdk_production_modules"
    )
    wiring = [
        node for node in tree.body
        if isinstance(node, ast.AugAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "hiddenimports"
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == helper.name
    ]
    assert len(wiring) == 1, "SDK collection must feed Analysis hiddenimports"
    namespace = {"collect_submodules": collect_submodules, "hiddenimports": []}
    exec(compile(ast.Module(body=[helper, *wiring], type_ignores=[]), str(SPEC), "exec"), namespace)  # noqa: S102 -- execute only local spec helper, not Analysis
    return set(namespace["hiddenimports"])


@pytest.mark.parametrize("package,distribution", PACKAGES.items())
def test_every_installed_production_module_is_collected(collected, package, distribution):
    # Independent oracle: installed wheel RECORD, not the spec's walker/filter.
    required = set()
    excluded = set()
    for path in importlib.metadata.distribution(distribution).files:
        if path.parts[0] != package or path.suffix != ".py":
            continue
        parts = list(path.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        name = ".".join(parts)
        if len(parts) > 1 and parts[1] in {"testing", "cli", "__main__"}:
            excluded.add(name)
        else:
            required.add(name)
    assert required
    assert excluded
    assert not required - collected, sorted(required - collected)
    assert not excluded & collected, sorted(excluded & collected)


@pytest.mark.parametrize("remove_workspace_leaf", [False, True])
def test_public_lazy_exports_resolve_using_only_collected_sdk_modules(collected, remove_workspace_leaf):
    # A fresh interpreter prevents the test runner's cached imports from hiding
    # a missing target. Deny any SDK module absent from the actual spec output.
    # This checks public getattr -> runtime mapping -> leaf, not direct imports.
    script = r'''
import importlib
import importlib.abc
import json
import sys

allowed = set(json.loads(sys.argv[1]))
class CollectedOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"simple_harness", "simple_harness_service"}:
            if fullname not in allowed:
                raise ModuleNotFoundError("SDK module missing from hiddenimports: " + fullname)
sys.meta_path.insert(0, CollectedOnly())
harness = importlib.import_module("simple_harness")
# Reproduce the actual Host startup API before enumerating other exports.
getattr(harness, "WorkspaceBindingAuthorityGrant")
runtime = importlib.import_module("simple_harness.runtime")
service = importlib.import_module("simple_harness_service")
assert runtime._EXPORT_TO_MODULE
assert harness._RUNTIME_EXPORTS
for name, relative in runtime._EXPORT_TO_MODULE.items():
    target = importlib.util.resolve_name(relative, runtime.__name__)
    assert target in allowed, (name, target)
    getattr(runtime, name)
for name in harness._RUNTIME_EXPORTS | harness._AGENT_EXPORTS:
    getattr(harness, name)
for name in service.__all__:
    getattr(service, name)
assert harness.WorkspaceBindingAuthorityGrant is runtime.WorkspaceBindingAuthorityGrant
assert "simple_harness.runtime.workspace_binding_protocol" in sys.modules
'''
    allowed = set(collected)
    missing = "simple_harness.runtime.workspace_binding_protocol"
    assert missing in allowed
    if remove_workspace_leaf:
        allowed.remove(missing)
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, json.dumps(sorted(allowed))],
        capture_output=True, text=True, timeout=45, check=False,
    )
    if remove_workspace_leaf:
        assert result.returncode != 0
        assert "ModuleNotFoundError: SDK module missing from hiddenimports: " + missing in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
