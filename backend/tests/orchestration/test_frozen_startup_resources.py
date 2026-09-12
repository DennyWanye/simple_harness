"""Startup data must survive packaging with the real semantic guards intact."""

import ast
import importlib.metadata
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"


@pytest.fixture(scope="module")
def resource_map():
    from PyInstaller.building.utils import format_binaries_and_datas
    from PyInstaller.utils.hooks import collect_data_files

    spec = BACKEND / "deskpet-backend.spec"
    tree = ast.parse(spec.read_text())
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_collect_startup_resources"
    )
    wiring = [
        node
        for node in tree.body
        if isinstance(node, ast.AugAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "datas"
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == helper.name
    ]
    assert len(wiring) == 1
    namespace = {
        "Path": Path,
        "subprocess": subprocess,
        "collect_data_files": collect_data_files,
        "_repo_root": REPO,
        "datas": [],
    }
    exec(  # noqa: S102 -- execute only local collection helper, never Analysis
        compile(ast.Module(body=[helper, *wiring], type_ignores=[]), str(spec), "exec"),
        namespace,
    )
    result = {}
    for destination, source in format_binaries_and_datas(namespace["datas"]):
        if destination in result:
            assert result[destination].read_bytes() == Path(source).read_bytes()
        result[destination.replace("\\", "/")] = Path(source)
    return result


def tracked(paths):
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", *paths],
        cwd=REPO,
        capture_output=True,
        check=True,
        timeout=10,
    )
    return [Path(name) for name in result.stdout.decode().split("\0") if name]


def test_host_startup_resource_inventory_has_no_unclassified_data(resource_map):
    # Independent repository inventory, broader than the spec's directory list.
    # These two trees are tool-build templates/offline legacy evaluations, not
    # lifespan inputs. New production resource locations must be classified.
    non_startup = ("deskpet/capabilities/templates/", "deskpet/workflows/evaluation/")
    suffixes = {".json", ".jsonl", ".yaml", ".yml", ".sql", ".dsl", ".lark", ".toml"}
    required = {}
    for path in tracked(
        ["backend/deskpet", "backend/memory/migrations", "backend/verify"]
    ):
        relative = path.relative_to("backend").as_posix()
        if path.suffix not in suffixes or relative.startswith(non_startup):
            continue
        required[relative] = REPO / path
    for path in tracked(["capability-packs"]):
        required[path.as_posix()] = REPO / path
    required.update(
        {
            "config.toml": REPO / "config.toml",
            "uv.lock": BACKEND / "uv.lock",
            "resources/diagnostic-redaction.json": REPO
            / "resources/diagnostic-redaction.json",
        }
    )
    assert "deskpet/tool_catalog/real_tool_manifest.json" in required
    assert "deskpet/tool_catalog/schema_migrations.json" in required
    assert set(required) <= set(resource_map), sorted(set(required) - set(resource_map))
    for destination, source in required.items():
        assert resource_map[destination].read_bytes() == source.read_bytes(), (
            destination
        )


@pytest.mark.parametrize(
    "package,distribution",
    [
        ("simple_harness", "simple-harness-sdk"),
        ("agent_orchestrator", "simple-harness-sdk"),
        ("simple_harness_service", "simple-harness-service-sdk"),
    ],
)
def test_every_sdk_package_data_file_is_collected(resource_map, package, distribution):
    installed = importlib.metadata.distribution(distribution)
    required = [
        path
        for path in installed.files
        if path.parts[0] == package and path.suffix not in {".py", ".pyc"}
    ]
    assert required
    for path in required:
        name = path.as_posix()
        assert name in resource_map, name
        assert (
            resource_map[name].read_bytes()
            == Path(installed.locate_file(path)).read_bytes()
        ), name


@pytest.fixture
def staged(tmp_path, resource_map):
    for destination, source in resource_map.items():
        target = tmp_path / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return tmp_path


def bind_tool_catalog(monkeypatch, root):
    from deskpet.tool_catalog import manifest

    monkeypatch.setattr(
        manifest,
        "_MANIFEST_PATH",
        root / "deskpet/tool_catalog/real_tool_manifest.json",
    )
    monkeypatch.setattr(
        manifest,
        "_MIGRATIONS_PATH",
        root / "deskpet/tool_catalog/schema_migrations.json",
    )
    return manifest


def test_real_tool_catalog_and_migration_ledger_load_from_bundle(staged, monkeypatch):
    manifest = bind_tool_catalog(monkeypatch, staged)
    catalog = manifest.load_tool_manifest()
    schemas, records = manifest.migrate_tool_schemas(catalog)
    assert len(catalog.tools) == 71 and len(schemas) == 71
    assert records and catalog.manifest_sha256 == manifest.MANIFEST_SHA256


@pytest.mark.parametrize(
    "damage", ["missing_catalog", "missing_ledger", "changed_catalog", "changed_ledger"]
)
def test_real_tool_catalog_guards_reject_missing_or_changed_data(
    staged, monkeypatch, damage
):
    manifest = bind_tool_catalog(monkeypatch, staged)
    target = (
        manifest._MANIFEST_PATH
        if damage.endswith("catalog")
        else manifest._MIGRATIONS_PATH
    )
    if damage.startswith("missing"):
        target.unlink()
    else:
        value = json.loads(target.read_text())
        if damage.endswith("catalog"):
            value["tool_count"] = 0
        else:
            value["schema_version"] = 0
        target.write_text(json.dumps(value))
    with pytest.raises((FileNotFoundError, RuntimeError)):
        manifest.migrate_tool_schemas(manifest.load_tool_manifest())


def test_sdk_fresh_schema_reads_bundled_sql_and_rejects_missing_source(
    staged, monkeypatch
):
    from simple_harness.execution.sqlite import schema

    expected = schema.fresh_descriptor()
    root = staged / "simple_harness/execution/sqlite/migrations"

    def files(package):
        assert package == "simple_harness.execution.sqlite.migrations"
        return root

    monkeypatch.setattr(schema, "files", files)
    actual = schema.fresh_descriptor()
    assert actual == expected
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(actual.sql)
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            > 0
        )
    (root / "0005_fresh.sql").unlink()
    with pytest.raises(FileNotFoundError):
        schema.fresh_descriptor()


def test_packaged_policy_and_evaluation_readers_use_shipped_data(staged, monkeypatch):
    from deskpet.agent.assembler import policy
    from deskpet.agent.verify_gate import load_claim_patterns
    from deskpet.companion.evaluation_suites import PackagedEvaluationSuiteLoader
    from deskpet.memory import schema_chain
    from deskpet.tools import build_identity

    def no_fallback():
        pytest.fail("packaged policy fell back to synthetic defaults")

    monkeypatch.setattr(policy, "_builtin_defaults_raw", no_fallback)
    assert policy.load_policies(
        default_path=staged / "deskpet/agent/assembler/policies/default.yaml"
    )
    assert load_claim_patterns(staged / "verify/claim_patterns.yaml")
    PackagedEvaluationSuiteLoader(
        resource_root=staged / "deskpet/companion/eval_suites"
    ).validate_release()
    monkeypatch.setattr(
        schema_chain, "MIGRATIONS_DIR", staged / "deskpet/memory/migrations"
    )
    assert schema_chain._discover() == schema_chain.DOMAIN_CHAIN
    for attribute, filename in (
        ("_SOURCES_PATH", "execution_build_sources.json"),
        ("_BUILD_PATH", "execution_build_manifest.json"),
        ("_EFFECT_PATH", "tool_effect_policy_manifest.json"),
    ):
        monkeypatch.setattr(
            build_identity, attribute, staged / "deskpet/tools" / filename
        )
    build_identity.load_core_handler_authorities.cache_clear()
    try:
        assert build_identity.load_core_handler_authorities()
    finally:
        build_identity.load_core_handler_authorities.cache_clear()


def test_all_bundled_pack_semantic_outcomes_match_source(staged):
    from deskpet.capabilities.manifest import PackManifestError, load_and_validate_pack

    def outcome(root):
        try:
            load_and_validate_pack(root)
        except PackManifestError as exc:
            return ("rejected", exc.code)
        return ("accepted",)

    manifests = sorted((REPO / "capability-packs").rglob("deskpet-pack.json"))
    assert manifests
    accepted = []
    for path in manifests:
        relative = path.parent.relative_to(REPO)
        expected = outcome(path.parent)
        assert outcome(staged / relative) == expected, relative
        if expected == ("accepted",):
            accepted.append(staged / relative)
    assert accepted
    # Mutating a shipped declared file must still hit the product's real hash
    # guard. No fallback fixture manifest or validation bypass is supplied.
    pack = next(root for root in accepted if list(root.rglob("SKILL.md")))
    skill = next(pack.rglob("SKILL.md"))
    skill.write_bytes(skill.read_bytes() + b"\nchanged\n")
    assert outcome(pack)[0] == "rejected"


@pytest.mark.parametrize(
    "module", ["deskpet.capabilities.platform", "deskpet.mcp.manager"]
)
def test_ondemand_adapter_self_hash_keeps_real_source_and_pyz(module):
    from PyInstaller.building.build_main import (
        _get_module_collection_mode,
        _ModuleCollectionMode,
    )

    tree = ast.parse((BACKEND / "deskpet-backend.spec").read_text())
    analysis = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Analysis"
    )
    modes = ast.literal_eval(
        next(
            item.value
            for item in analysis.keywords
            if item.arg == "module_collection_mode"
        )
    )
    mode = _get_module_collection_mode(modes, module)
    assert mode & _ModuleCollectionMode.PY
    assert mode & _ModuleCollectionMode.PYZ
    assert (BACKEND / (module.replace(".", "/") + ".py")).is_file()
