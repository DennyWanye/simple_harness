# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Source launcher identity oracles; no application, interpreter or key is invoked."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/native/launch_source_orchestrator.py"


@pytest.fixture
def launcher(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("source_launcher_oracle", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def forbidden(*args, **kwargs):
        raise AssertionError("identity checks must not launch or read credentials")

    monkeypatch.setattr(module, "read_key", forbidden)
    monkeypatch.setattr(module.subprocess, "Popen", forbidden)
    return module


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path / "source"
    (root / "backend").mkdir(parents=True)
    (root / "tauri-app").mkdir()
    exe = tmp_path / "python-real"
    exe.write_bytes(b"test interpreter, never executed")
    exe.chmod(0o755)
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").symlink_to(exe)
    (venv / "pyvenv.cfg").write_text("home = fixture\ninclude-system-site-packages = false\n")
    dist = venv / "lib/python3.12/site-packages/fixture-1.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Name: fixture\nVersion: 1\n")
    (dist / "RECORD").write_text("fixture.py,sha256=fixture,10\n")
    binary = tmp_path / "carrier"
    binary.write_bytes(b"test carrier, never executed")
    binary.chmod(0o755)
    config = tmp_path / "tauri-dev.json"
    config.write_text(json.dumps({"build": {"devUrl": "http://localhost:15173"}}))
    for directory in ("resources", "models"):
        folder = tmp_path / directory
        folder.mkdir()
        (folder / "payload.bin").write_bytes(b"fixture payload")
        (folder / "manifest.json").write_text('{"fixture":true}')
    for name in ("attestation.json", "tokenizer.json"):
        (tmp_path / name).write_text("{}")
    return argparse.Namespace(
        source_root=root, binary=binary, python=venv / "bin/python",
        sdk_attestation=tmp_path / "attestation.json", tokenizer=tmp_path / "tokenizer.json",
        resource_root=tmp_path / "resources", model_root=tmp_path / "models",
        carrier_config=config, carrier_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
        dev_url="http://localhost:15173", vite_port=15173,
        logical_slots=1, model_slots=1,
    )


def _required_cli(inputs):
    return [
        "--source-root", str(inputs.source_root),
        "--binary", str(inputs.binary),
        "--carrier-config", str(inputs.carrier_config),
        "--carrier-sha256", inputs.carrier_sha256,
        "--dev-url", inputs.dev_url,
        "--python", str(inputs.python),
        "--sdk-attestation", str(inputs.sdk_attestation),
        "--tokenizer", str(inputs.tokenizer),
        "--resource-root", str(inputs.resource_root),
        "--model-root", str(inputs.model_root),
        "--run-dir", str(inputs.source_root / ".local-test-evidence/run"),
    ]


def test_publish_test_option_has_distinct_identity_and_fixed_local_destination(
    launcher, inputs, tmp_path,
):
    original = launcher.source_identity(inputs, host_head="recorded-head")
    assert "publish_test_dir" not in original
    args = launcher.parse_args([*_required_cli(inputs), "--publish-test-reports"])
    assert launcher.source_identity(args, host_head="recorded-head")["publish_test_dir"] == str(
        args.run_dir.absolute() / "published"
    )
    user = tmp_path / "userdata"
    user.mkdir()
    config = user / "config.toml"
    config.write_text('[orchestration]\nenabled = true\n')
    launcher.bind_test_publish_config(tmp_path, enabled=False, resume=False)
    assert 'publish_dir' not in config.read_text()
    launcher.bind_test_publish_config(tmp_path, enabled=True, resume=False)
    before = config.read_bytes()
    launcher.bind_test_publish_config(tmp_path, enabled=True, resume=True)
    assert config.read_bytes() == before
    assert list((tmp_path / "published").iterdir()) == []  # directory ready, no publish action
    with pytest.raises(launcher.LauncherError, match="explicit opt-in"):
        launcher.bind_test_publish_config(tmp_path, enabled=False, resume=True)
    config.write_text(config.read_text().replace('/published', '/other'))
    with pytest.raises(launcher.LauncherError, match="publish directory changed"):
        launcher.bind_test_publish_config(tmp_path, enabled=True, resume=True)


@pytest.mark.parametrize("damage", ["missing", "symlink", "bad-toml"])
def test_publish_resume_does_not_repair_missing_or_changed_destination(launcher, tmp_path, damage):
    user = tmp_path / "userdata"
    user.mkdir()
    config = user / "config.toml"
    config.write_text('[orchestration]\nenabled = true\n')
    launcher.bind_test_publish_config(tmp_path, enabled=True, resume=False)
    destination = tmp_path / "published"
    if damage == "bad-toml":
        config.write_text('[orchestration\n')
    else:
        destination.rmdir()
        if damage == "symlink":
            destination.symlink_to(user, target_is_directory=True)
    with pytest.raises(launcher.LauncherError):
        launcher.bind_test_publish_config(tmp_path, enabled=True, resume=True)


@pytest.mark.parametrize("case", ["native-load-three-mission", "native-load-verifier-pressure",
                                  "p34-approved-compare", "native-context-rotation"])
def test_load_case_requires_fixture_and_binds_actual_slot_limits(launcher, inputs, case):
    argv = [*_required_cli(inputs), "--fixture-case", case,
            "--logical-slots", "3", "--model-slots", "2"]
    with pytest.raises(SystemExit):
        launcher.parse_args(argv)
    parsed = launcher.parse_args([*argv, "--fixture-dir", str(inputs.source_root)])
    assert parsed.fixture_case == case
    assert (parsed.logical_slots, parsed.model_slots) == (3, 2)


def test_identity_keeps_venv_entry_and_names_honest_hash_scopes(launcher, inputs, monkeypatch):
    original = Path.read_bytes

    def no_payload_read(path):
        assert path.name != "payload.bin", "do not hash large resource/model bytes"
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", no_payload_read)
    value = launcher.source_identity(inputs, host_head="recorded-head")
    py = value["python"]
    assert py["entry"] == str(inputs.python.absolute())
    assert py["executable"] == str(inputs.python.resolve())
    assert py["entry"] != py["executable"]
    assert py["venv_config_sha256"] == hashlib.sha256(
        (inputs.python.parent.parent / "pyvenv.cfg").read_bytes()
    ).hexdigest()
    assert py["distribution_manifest_count"] == 2
    assert py["distribution_scope"] == "manifest_hashes_not_installed_payload_verification"
    for name in ("resources", "models"):
        assert value[name]["payload_bytes_verified"] is False
        assert value[name]["manifest_count"] == 1
        assert value[name]["file_count"] == 2
    assert value["carrier"]["dev_url"] == inputs.dev_url
    assert value["carrier"]["basis"] == "operator_attested_config_binding_not_binary_extracted"


def test_same_inputs_and_model_override_resume_without_launch(launcher, inputs, tmp_path, monkeypatch):
    identity = launcher.source_identity(inputs, host_head="recorded-head")
    monkeypatch.setitem(launcher.prepare_run.__globals__, "HOST_ROOT", tmp_path)
    monkeypatch.setitem(launcher.prepare_run.__globals__, "is_ignored", lambda path: True)
    run = tmp_path / ".local-test-evidence/run"
    launcher.prepare_run(run, identity, 18140, False)
    launcher.bind_slot_config(run, logical_slots=1, model_slots=1, resume=False)
    launcher.bind_model_override(run, resume=False)
    assert launcher.prepare_run(
        run, launcher.source_identity(inputs, host_head="recorded-head"), 18140, True,
    ) == run
    launcher.bind_slot_config(run, logical_slots=1, model_slots=1, resume=True)
    launcher.bind_model_override(run, resume=True)
    assert not list(run.glob("launch-*.json"))


@pytest.mark.parametrize("changed", ["venv", "distribution", "resources", "models", "manifest", "pth", "fixture_case"])
def test_changed_runtime_input_cannot_resume_same_prepared_identity(launcher, inputs, changed, tmp_path, monkeypatch):
    frozen = launcher.source_identity(inputs, host_head="recorded-head")
    # Exercise the same persisted marker comparison used by the actual launcher.
    prepare_globals = launcher.prepare_run.__globals__
    monkeypatch.setitem(prepare_globals, "HOST_ROOT", tmp_path)
    monkeypatch.setitem(prepare_globals, "is_ignored", lambda path: True)
    run = tmp_path / ".local-test-evidence/run"
    launcher.prepare_run(run, frozen, 18140, False)
    launcher.bind_slot_config(run, logical_slots=1, model_slots=1, resume=False)
    launcher.bind_model_override(run, resume=False)
    if changed == "venv":
        (inputs.python.parent.parent / "pyvenv.cfg").write_text("home = changed\n")
    elif changed == "distribution":
        next(inputs.python.parent.parent.glob("lib/python*/site-packages/*.dist-info/RECORD")).write_text("changed")
    elif changed == "manifest":
        (inputs.resource_root / "manifest.json").write_text('{"changed":true}')
    elif changed == "fixture_case":
        inputs.fixture_case = "n6-two-thirds"
    elif changed == "pth":
        site = next(inputs.python.parent.parent.glob("lib/python*/site-packages"))
        (site / "editable.pth").write_text("/different/source/root\n")
    else:
        folder = inputs.resource_root if changed == "resources" else inputs.model_root
        (folder / "new.bin").write_bytes(b"new fixture")
    updated = launcher.source_identity(inputs, host_head="recorded-head")
    assert updated != frozen
    with pytest.raises(launcher.LauncherError, match="identity"):
        launcher.prepare_run(run, updated, 18140, True)


def test_slots_are_attested_and_written_only_to_a_fresh_launcher_config(launcher, inputs, tmp_path, monkeypatch):
    inputs.logical_slots = 3
    inputs.model_slots = 2
    identity = launcher.source_identity(inputs, host_head="recorded-head")
    assert identity["orchestration_slots"] == {"logical_slots": 3, "model_slots": 2}
    monkeypatch.setitem(launcher.prepare_run.__globals__, "HOST_ROOT", tmp_path)
    monkeypatch.setitem(launcher.prepare_run.__globals__, "is_ignored", lambda path: True)
    run = tmp_path / ".local-test-evidence/run"
    launcher.prepare_run(run, identity, 18140, False)
    launcher.bind_slot_config(run, logical_slots=3, model_slots=2, resume=False)
    config = run / "userdata/config.toml"
    section = launcher.tomllib.loads(config.read_text())["orchestration"]
    assert section == {"enabled": True, "max_concurrency": 3, "max_concurrent_model_calls": 2}


def test_slot_cli_accepts_explicit_bounds_and_keeps_defaults(launcher, inputs):
    defaults = launcher.parse_args(_required_cli(inputs))
    configured = launcher.parse_args(
        _required_cli(inputs) + ["--logical-slots", "3", "--model-slots", "2"]
    )
    assert (defaults.logical_slots, defaults.model_slots) == (1, 1)
    assert (configured.logical_slots, configured.model_slots) == (3, 2)


@pytest.mark.parametrize("logical_slots,model_slots", [(0, 1), (1, 0), (5, 1), (1, 5)])
def test_slot_cli_rejects_values_outside_one_through_four(launcher, inputs, capsys, logical_slots, model_slots):
    with pytest.raises(SystemExit):
        launcher.parse_args(_required_cli(inputs) + [
            "--logical-slots", str(logical_slots), "--model-slots", str(model_slots),
        ])
    assert "invalid choice" in capsys.readouterr().err


@pytest.mark.parametrize("damage", ["logical", "model", "missing", "bool"])
def test_resume_rejects_changed_slot_config_without_repairing_seeded_policy(launcher, inputs, tmp_path, monkeypatch, damage):
    identity = launcher.source_identity(inputs, host_head="recorded-head")
    monkeypatch.setitem(launcher.prepare_run.__globals__, "HOST_ROOT", tmp_path)
    monkeypatch.setitem(launcher.prepare_run.__globals__, "is_ignored", lambda path: True)
    run = tmp_path / ".local-test-evidence/run"
    launcher.prepare_run(run, identity, 18140, False)
    launcher.bind_slot_config(run, logical_slots=1, model_slots=1, resume=False)
    config = run / "userdata/config.toml"
    original = config.read_text()
    if damage == "logical":
        config.write_text(original.replace("max_concurrency = 1", "max_concurrency = 2"))
    elif damage == "model":
        config.write_text(original.replace("max_concurrent_model_calls = 1", "max_concurrent_model_calls = 2"))
    elif damage == "bool":
        config.write_text(original.replace("max_concurrency = 1", "max_concurrency = true"))
    else:
        config.write_text(original.replace("max_concurrency = 1\n", ""))
    changed = config.read_text()
    with pytest.raises(launcher.LauncherError, match="slot configuration"):
        launcher.bind_slot_config(run, logical_slots=1, model_slots=1, resume=True)
    assert config.read_text() == changed


def test_new_launcher_refuses_a_run_attested_by_the_pre_slot_launcher(launcher, inputs, tmp_path, monkeypatch):
    identity = launcher.source_identity(inputs, host_head="recorded-head")
    old_identity = dict(identity)
    del old_identity["orchestration_slots"]
    monkeypatch.setitem(launcher.prepare_run.__globals__, "HOST_ROOT", tmp_path)
    monkeypatch.setitem(launcher.prepare_run.__globals__, "is_ignored", lambda path: True)
    run = tmp_path / ".local-test-evidence/run"
    launcher.prepare_run(run, old_identity, 18140, False)
    with pytest.raises(launcher.LauncherError, match="identity"):
        launcher.prepare_run(run, identity, 18140, True)


@pytest.mark.parametrize("damage", ["content", "missing", "symlink"])
def test_model_override_is_fixed_and_resume_never_repairs_it(launcher, tmp_path, damage):
    (tmp_path / "userdata").mkdir()
    launcher.bind_model_override(tmp_path, resume=False)
    override = tmp_path / "userdata/model_overrides.toml"
    assert override.read_bytes() == launcher.MODEL_OVERRIDE
    if damage == "content":
        override.write_bytes(b"changed context limit")
    else:
        override.unlink()
        if damage == "symlink":
            target = tmp_path / "other.toml"
            target.write_bytes(launcher.MODEL_OVERRIDE)
            override.symlink_to(target)
    with pytest.raises(launcher.LauncherError, match="model override"):
        launcher.bind_model_override(tmp_path, resume=True)


@pytest.mark.parametrize("damage", ["missing_models", "file_resources", "bad_url", "wrong_binary", "wrong_config"])
def test_preflight_rejects_bad_paths_or_carrier_binding_without_key(launcher, inputs, damage):
    if damage == "missing_models":
        inputs.model_root = inputs.model_root / "absent"
    elif damage == "file_resources":
        inputs.resource_root = inputs.resource_root / "payload.bin"
    elif damage == "bad_url":
        inputs.dev_url = "http://localhost:5173"
    elif damage == "wrong_binary":
        inputs.binary.write_bytes(b"another executable")
    else:
        inputs.carrier_config.write_text('{"build":{"devUrl":"http://localhost:5173"}}')
    with pytest.raises(launcher.LauncherError):
        launcher.source_identity(inputs, host_head="recorded-head")


@pytest.mark.parametrize("damage", ["system_sites", "directory_escape", "dangling_file_link"])
def test_inventory_does_not_adopt_unlisted_system_sites_or_invalid_links(launcher, inputs, tmp_path, damage):
    if damage == "system_sites":
        (inputs.python.parent.parent / "pyvenv.cfg").write_text("include-system-site-packages = true\n")
    elif damage == "directory_escape":
        (inputs.resource_root / "outside").symlink_to(tmp_path, target_is_directory=True)
    else:
        (inputs.model_root / "missing.bin").symlink_to(tmp_path / "absent.bin")
    with pytest.raises(launcher.LauncherError):
        launcher.source_identity(inputs, host_head="recorded-head")
