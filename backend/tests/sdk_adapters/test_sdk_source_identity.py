# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Explicit source admission does not pretend that editable code is a wheel."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from importlib import metadata
from types import ModuleType, SimpleNamespace

import pytest
from deskpet.sdk_adapters import runtime_paths as paths
from deskpet.sdk_adapters import sdk_candidate as candidate


@pytest.fixture
def source_install(tmp_path, monkeypatch):
    root = tmp_path / "sdk"
    root.mkdir()
    (root / "pyproject.toml").write_text('[project]\nname="simple-harness-sdk"\n')
    modules = {}
    for name in ("simple_harness", "agent_orchestrator"):
        package = root / "src" / name
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(
            f'__version__ = "{candidate.SDK_VERSION}"\n'
        )
        (package / "schema.sql").write_text("CREATE TABLE example(id INTEGER);\n")
        module = ModuleType(name)
        module.__file__ = str(package / "__init__.py")
        module.__path__ = [str(package)]
        module.__version__ = candidate.SDK_VERSION
        module.__spec__ = SimpleNamespace(origin=module.__file__)
        modules[name] = module
    for args in (
        ("init", "-q"), ("add", "."),
        ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-qm", "fixture"),
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    dist = tmp_path / f"simple_harness_sdk-{candidate.SDK_VERSION}.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: simple-harness-sdk\n"
        f"Version: {candidate.SDK_VERSION}\n"
    )
    (dist / "direct_url.json").write_text(json.dumps({
        "url": root.as_uri(), "dir_info": {"editable": True},
    }))
    monkeypatch.setattr(paths.metadata, "distribution", lambda _name: metadata.Distribution.at(dist))
    monkeypatch.setattr(paths, "sys", SimpleNamespace(modules=modules, frozen=False))
    wheel = tmp_path / "baseline.whl"
    wheel.write_bytes(b"fixture baseline: never the editable implementation")
    baseline = paths.SdkCandidateIdentity(
        candidate.SDK_VERSION, hashlib.sha256(wheel.read_bytes()).hexdigest(), wheel
    )
    monkeypatch.setattr(candidate, "build_candidate_identity", lambda: baseline)
    attestation = tmp_path / "source-attestation.json"
    attestation.write_text(json.dumps(paths.capture_sdk_source_attestation(root)))
    monkeypatch.setenv(candidate.SDK_RUNTIME_MODE_ENV, "editable-source")
    monkeypatch.setenv(candidate.SDK_SOURCE_ATTESTATION_ENV, str(attestation))
    return SimpleNamespace(root=root, dist=dist, modules=modules, baseline=baseline,
                           attestation=attestation)


def test_explicit_editable_identity_is_verified_without_claiming_wheel_match(source_install):
    identity = candidate.build_runtime_identity()
    assert isinstance(identity, paths.SdkSourceIdentity)
    assert paths.verify_runtime_identity(identity) == identity
    report = candidate.runtime_identity_report()
    assert report["mode"] == "editable-source"
    assert report["source_verified"] is True
    assert report["baseline_artifact_verified"] is True
    assert report["installed_wheel_verified"] is False
    assert report["source"]["root"] == str(source_install.root)
    assert report["source"]["inputs_sha256"] == identity.inputs_sha256
    # The original production verifier must still reject this installation.
    with pytest.raises(RuntimeError, match="origin mismatch"):
        paths.verify_sdk_candidate(source_install.baseline)


@pytest.mark.parametrize("change", ["new", "changed", "deleted", "data", "commit", "metadata"])
def test_stale_source_attestation_fails_closed(source_install, change):
    identity = candidate.build_runtime_identity()
    package = source_install.root / "src/simple_harness"
    if change == "new":
        (package / "new_module.py").write_text("NEW = True\n")
    elif change == "changed":
        (package / "__init__.py").write_text("CHANGED = True\n")
    elif change == "deleted":
        (package / "schema.sql").unlink()
    elif change == "data":
        (package / "schema.sql").write_text("DROP TABLE example;\n")
    elif change == "metadata":
        (source_install.root / "pyproject.toml").write_text("# changed package metadata\n")
    else:
        subprocess.run(["git", "-C", str(source_install.root), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty",
                        "-qm", "new commit"], check=True, capture_output=True)
    with pytest.raises(RuntimeError, match="SDK source"):
        paths.verify_runtime_identity(identity)


def test_attested_uncommitted_inputs_allowed_and_outer_docs_do_not_block(source_install):
    root = source_install.root
    (root / "src/simple_harness/new.py").write_text("VALUE = 2\n")
    payload = paths.capture_sdk_source_attestation(root)
    assert "src/simple_harness/new.py" in payload["inputs"]
    source_install.attestation.write_text(json.dumps(payload))
    (root / "docs").mkdir()
    (root / "docs/note.md").write_text("uncommitted documentation")
    (root / "src/simple_harness/__pycache__").mkdir()
    (root / "src/simple_harness/__pycache__/ignored.pyc").write_bytes(b"cache")
    assert paths.verify_runtime_identity(candidate.build_runtime_identity())


@pytest.mark.parametrize("problem", ["wheel_url", "not_editable", "version", "imported_version",
                                    "root_origin", "root_inside",
                                    "leaf_origin", "package_path", "spec_origin", "frozen", "symlink"])
def test_wrong_installation_or_frozen_source_is_refused(source_install, problem, monkeypatch, tmp_path):
    identity = candidate.build_runtime_identity()
    module = source_install.modules["simple_harness"]
    if problem in ("wheel_url", "not_editable"):
        (source_install.dist / "direct_url.json").write_text(json.dumps({
            "url": (source_install.baseline.wheel_path if problem == "wheel_url"
                    else source_install.root).as_uri(),
            "dir_info": {"editable": problem != "not_editable"},
        }))
    elif problem == "version":
        (source_install.dist / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: simple-harness-sdk\nVersion: 0.0.0\n"
        )
    elif problem == "imported_version":
        module.__version__ = "0.0.0"
    elif problem == "root_origin":
        module.__file__ = str(tmp_path / "wheel/simple_harness/__init__.py")
    elif problem == "root_inside":
        module.__file__ = str(source_install.root / "src/simple_harness/schema.sql")
        module.__spec__.origin = module.__file__
    elif problem == "leaf_origin":
        leaf = ModuleType("simple_harness.runtime")
        leaf.__file__ = str(tmp_path / "wheel/simple_harness/runtime.py")
        source_install.modules[leaf.__name__] = leaf
    elif problem == "package_path":
        module.__path__.append(str(tmp_path / "other"))
    elif problem == "spec_origin":
        module.__spec__.origin = str(tmp_path / "other.py")
    elif problem == "frozen":
        monkeypatch.setattr(paths.sys, "frozen", True)
    else:
        (source_install.root / "src/simple_harness/escape.py").symlink_to(
            source_install.root / "pyproject.toml"
        )
    with pytest.raises(RuntimeError, match="SDK source"):
        paths.verify_runtime_identity(identity)


@pytest.mark.parametrize("problem", ["missing", "invalid_json", "wrong_root", "bad_hash", "unknown_mode",
                                    "implicit_mode", "frozen"])
def test_source_selection_requires_explicit_valid_attestation(source_install, problem, monkeypatch):
    if problem == "missing":
        monkeypatch.delenv(candidate.SDK_SOURCE_ATTESTATION_ENV)
    elif problem == "invalid_json":
        source_install.attestation.write_text("not json")
    elif problem in ("wrong_root", "bad_hash"):
        payload = json.loads(source_install.attestation.read_text())
        if problem == "wrong_root":
            payload["root"] = str(source_install.root / "src")
        else:
            payload["inputs"]["pyproject.toml"] = "0" * 64
        source_install.attestation.write_text(json.dumps(payload))
    elif problem == "unknown_mode":
        monkeypatch.setenv(candidate.SDK_RUNTIME_MODE_ENV, "anything")
    elif problem == "implicit_mode":
        monkeypatch.delenv(candidate.SDK_RUNTIME_MODE_ENV)
    else:
        monkeypatch.setattr(candidate.sys, "frozen", True, raising=False)
    with pytest.raises(RuntimeError, match="SDK (source|runtime)"):
        paths.verify_runtime_identity(candidate.build_runtime_identity())


def test_bad_source_is_rejected_before_dependencies_or_database(source_install, tmp_path):
    from deskpet.sdk_adapters.composition import ProductSdkRuntimeStack

    identity = candidate.build_runtime_identity()
    (source_install.root / "src/simple_harness/new.py").write_text("CHANGED = True\n")
    loaded = []
    runtime_paths = paths.ProductRuntimePathsAdapter(tmp_path / "userdata")
    stack = ProductSdkRuntimeStack(paths=runtime_paths, candidate_identity=identity,
                                   dependency_loader=lambda: loaded.append(True))
    with pytest.raises(RuntimeError, match="SDK source"):
        asyncio.run(stack.start())
    assert loaded == []
    assert not runtime_paths.execution_database.exists()


def test_valid_source_reaches_real_composition_dependency_boundary(source_install, tmp_path):
    from deskpet.sdk_adapters.composition import ProductSdkRuntimeStack

    loaded = []

    def dependencies():
        loaded.append(True)
        raise RuntimeError("fixture dependency boundary reached")

    stack = ProductSdkRuntimeStack(
        paths=paths.ProductRuntimePathsAdapter(tmp_path / "userdata"),
        candidate_identity=candidate.build_runtime_identity(), dependency_loader=dependencies,
    )
    with pytest.raises(RuntimeError, match="fixture dependency boundary reached"):
        asyncio.run(stack.start())
    assert loaded == [True]


def test_wheel_mode_is_default_and_still_runs_original_verifier(monkeypatch):
    monkeypatch.delenv(candidate.SDK_RUNTIME_MODE_ENV, raising=False)
    monkeypatch.delenv(candidate.SDK_SOURCE_ATTESTATION_ENV, raising=False)
    identity = candidate.build_runtime_identity()
    assert type(identity) is paths.SdkCandidateIdentity
    seen = []
    monkeypatch.setattr(paths, "verify_sdk_candidate", lambda value: seen.append(value) or value)
    assert paths.verify_runtime_identity(identity) == identity
    assert seen == [identity]


def test_source_mode_still_rejects_tampered_baseline_wheel(source_install):
    identity = candidate.build_runtime_identity()
    source_install.baseline.wheel_path.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="baseline wheel"):
        paths.verify_runtime_identity(identity)


def test_manifest_distinguishes_version_from_verified_source_and_rejects_drift(source_install):
    from deskpet.orchestration.manifest import distributions

    report = distributions()
    assert report["version_match"] is True
    assert report["consistent"] is True
    assert report["pin"] == {"version": candidate.SDK_VERSION,
                             "wheel_sha256": candidate.SDK_WHEEL_SHA256}
    assert report["runtime_identity"]["source_verified"] is True
    assert report["runtime_identity"]["installed_wheel_verified"] is False
    (source_install.root / "src/agent_orchestrator/new.json").write_text('{"new":true}')
    report = distributions()
    assert report["version_match"] is True
    assert report["consistent"] is False
    assert report["runtime_identity"]["verification"] == "refused"


def test_wheel_manifest_does_not_claim_byte_verification(monkeypatch):
    monkeypatch.delenv(candidate.SDK_RUNTIME_MODE_ENV, raising=False)
    monkeypatch.delenv(candidate.SDK_SOURCE_ATTESTATION_ENV, raising=False)
    report = candidate.runtime_identity_report()
    assert report == {"mode": "wheel", "source_verified": False,
                      "installed_wheel_verified": False, "verification": "version-only"}
