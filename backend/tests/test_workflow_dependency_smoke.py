from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
FORBIDDEN_IMPORT_ROOTS = frozenset({"langgraph", "langchain", "langchain_core"})
FORBIDDEN_DISTRIBUTIONS = frozenset(
    {
        "langchain-core",
        "langchain-protocol",
        "langchain",
        "langgraph",
        "langgraph-checkpoint",
        "langgraph-checkpoint-sqlite",
        "langgraph-prebuilt",
        "langgraph-sdk",
        "langsmith",
    }
)


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.partition(".")[0])
    return roots


def _locked_distribution_names() -> set[str]:
    payload = tomllib.loads((BACKEND_DIR / "uv.lock").read_text(encoding="utf-8"))
    return {str(package["name"]).lower() for package in payload.get("package", [])}


def test_workflow_sources_do_not_import_removed_frameworks() -> None:
    sources = sorted((BACKEND_DIR / "deskpet").rglob("*.py"))
    for package_root in ("agent", "llm", "pipeline"):
        sources.extend(sorted((BACKEND_DIR / package_root).rglob("*.py")))
    sources.extend(sorted(BACKEND_DIR.glob("*.py")))
    sources.append(REPO_ROOT / "scripts" / "workflow_eval_adapter.py")
    violations = {
        str(path.relative_to(REPO_ROOT)): sorted(_import_roots(path) & FORBIDDEN_IMPORT_ROOTS)
        for path in sources
        if _import_roots(path) & FORBIDDEN_IMPORT_ROOTS
    }
    assert violations == {}


def test_project_and_lock_exclude_removed_distributions() -> None:
    project = tomllib.loads((BACKEND_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = {
        str(value).split(";", 1)[0].split("[", 1)[0].split("=", 1)[0].split("<", 1)[0]
        .split(">", 1)[0]
        .strip()
        .lower()
        for value in project["project"]["dependencies"]
    }
    assert dependencies.isdisjoint(FORBIDDEN_DISTRIBUTIONS)
    assert _locked_distribution_names().isdisjoint(FORBIDDEN_DISTRIBUTIONS)


def test_pyinstaller_spec_has_no_removed_framework_hooks() -> None:
    spec_text = (BACKEND_DIR / "deskpet-backend.spec").read_text(encoding="utf-8").lower()
    assert "langgraph" not in spec_text
    assert "langchain" not in spec_text
    assert "runtime_hooks=[]" in spec_text.replace(" ", "")
    assert not (BACKEND_DIR / "pyinstaller_runtime_hook_langgraph.py").exists()


def test_pyinstaller_keeps_workflow_definition_source_for_stable_manifests() -> None:
    spec_text = (BACKEND_DIR / "deskpet-backend.spec").read_text(encoding="utf-8")
    assert 'module_collection_mode={"deskpet.workflows.definitions": "py"}' in spec_text
    assert '("uv.lock", ".")' in spec_text


def test_eval_adapter_uses_native_json_interrupt_contract() -> None:
    adapter = (REPO_ROOT / "scripts" / "workflow_eval_adapter.py").read_text(encoding="utf-8")
    assert "InMemorySaver" not in adapter
    assert "__interrupt__" not in adapter
    assert 'selected.bind(checkpointer=None)' in adapter
    assert 'output.get("interrupt")' in adapter


def test_workflow_imports_when_removed_frameworks_are_blocked() -> None:
    probe = textwrap.dedent(
        f"""
        import importlib.abc
        import sys

        blocked = {sorted(FORBIDDEN_IMPORT_ROOTS)!r}

        class BlockRemovedFrameworks(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.partition('.')[0] in blocked:
                    raise ModuleNotFoundError(
                        f"removed workflow framework imported: {{fullname}}"
                    )
                return None

        sys.meta_path.insert(0, BlockRemovedFrameworks())
        sys.path.insert(0, {str(BACKEND_DIR)!r})
        import deskpet.workflows
        from deskpet.workflows.definitions.v1 import (
            CODE_COMPLEX_V1,
            DEEP_RESEARCH_V1,
            PPT_PRO_V1,
        )

        for compiled in (CODE_COMPLEX_V1, DEEP_RESEARCH_V1, PPT_PRO_V1):
            executable = compiled.bind(checkpointer=None)
            assert callable(executable.ainvoke)
            assert callable(executable.resume)
        """
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", probe],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_frozen_verifier_checks_native_engine_and_removed_packages() -> None:
    verifier = (REPO_ROOT / "scripts" / "verify-frozen-backend.ps1").read_text(
        encoding="utf-8"
    )
    assert "ForbiddenPackages" in verifier
    assert "engine_kind=deskpet-native" in verifier
    assert "exit 1" in verifier
