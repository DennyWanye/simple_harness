# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Synthetic source launcher contracts (DeepSeek only); no real credentials or processes."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/native/launch_source_orchestrator.py"


@pytest.fixture
def launcher(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("source_local_launcher_oracle", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "HOST_ROOT", tmp_path)
    monkeypatch.setitem(module.prepare_run.__globals__, "HOST_ROOT", tmp_path)
    monkeypatch.setitem(module.prepare_run.__globals__, "is_ignored", lambda path: True)
    monkeypatch.setattr(module, "read_key", lambda: pytest.fail("DeepSeek key must not be read"))
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **kw: pytest.fail("no launch"))
    return module


def test_cli_is_deepseek_only(launcher, tmp_path):
    required = ["--binary", "binary", "--carrier-config", "config",
                "--carrier-sha256", "a" * 64, "--dev-url", "http://localhost:15173",
                "--python", "python", "--sdk-attestation", "sdk", "--tokenizer", "tokenizer",
                "--resource-root", "resource", "--model-root", "model", "--run-dir", "run"]
    args = launcher.parse_args(required)
    assert not hasattr(args, "provider") and not hasattr(args, "local_profile")
    for removed in (["--provider", "local"], ["--provider", "deepseek"],
                    ["--local-profile", "profile"]):
        with pytest.raises(SystemExit):
            launcher.parse_args(required + removed)
    for name in ("read_local_credentials", "local_profile_identity", "LOCAL_CONTEXT"):
        assert not hasattr(launcher, name)


def test_direct_helpers_keep_deepseek_defaults(launcher, tmp_path):
    assert launcher.model_override_bytes() == launcher.MODEL_OVERRIDE
    run = tmp_path / ".local-test-evidence" / "deepseek-run"
    launcher.prepare_run(run, {"legacy": True}, 18140, False)
    assert "provider" not in json.loads((run / "prepared.json").read_text())
    launcher.bind_model_override(run, resume=False)
    launcher.prepare_run(run, {"legacy": True}, 18140, True)
    launcher.bind_model_override(run, resume=True)
    launcher.bind_slot_config(run, logical_slots=2, model_slots=1, resume=False)
    config_path = run / "userdata/config.toml"
    assert "local_model_profile" not in config_path.read_text()
    launcher.bind_slot_config(run, logical_slots=2, model_slots=1, resume=True)
