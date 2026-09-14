# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Synthetic source-local launcher contracts; no real credentials or processes."""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

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


def profile_fixture(launcher, tmp_path):
    tokenizer = tmp_path / "tokenizer"
    tokenizer.mkdir()
    for name in ("config.json", "tokenizer_config.json", "tokenizer.json"):
        (tokenizer / name).write_bytes(b"synthetic " + name.encode())
    base_url = "http://127.0.0.1:8000/OpenAI/v1"
    model = "qwen38-flash-next"
    profile = tmp_path / "local-profile.json"
    payload = {
        "base_url": base_url, "model": model, "max_total_tokens": 262144,
        "tokenizer_path": str(tokenizer),
        "tokenizer_files": {
            name: hashlib.sha256((tokenizer / name).read_bytes()).hexdigest()
            for name in ("config.json", "tokenizer_config.json", "tokenizer.json")
        },
        "chat_template_kwargs": {},
    }
    profile.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / ".env").write_text(
        "UNRELATED_SECRET=do-not-read\n"
        f"BaseURLLOCAL={base_url}\nAPIKeyLOCAL=synthetic-key\n"
        f"MODELLOCAL={model}\nAPIPATH=/chat/completions\n", encoding="utf-8",
    )
    return profile, payload


def test_cli_defaults_local_and_requires_profile(launcher, tmp_path):
    required = ["--binary", "binary", "--carrier-config", "config",
                "--carrier-sha256", "a" * 64, "--dev-url", "http://localhost:15173",
                "--python", "python", "--sdk-attestation", "sdk", "--tokenizer", "tokenizer",
                "--resource-root", "resource", "--model-root", "model", "--run-dir", "run"]
    assert launcher.parse_args(required).provider == "local"
    assert launcher.parse_args(required + ["--local-profile", "profile"]).provider == "local"
    assert launcher.parse_args(required + ["--provider", "deepseek"]).local_profile is None
    with pytest.raises(launcher.LauncherError, match="local-profile"):
        launcher.source_identity(SimpleNamespace(local_profile=None), host_head="fixture", provider="local")


@pytest.mark.parametrize("damage", ["missing_key", "missing_path", "duplicate", "wrong_path", "expansion"])
def test_local_fields_fail_closed_without_deepseek(launcher, tmp_path, damage):
    profile_fixture(launcher, tmp_path)
    dotenv = tmp_path / ".env"
    original = dotenv.read_text()
    changes = {
        "missing_key": original.replace("APIKeyLOCAL=synthetic-key\n", ""),
        "missing_path": original.replace("APIPATH=/chat/completions\n", ""),
        "duplicate": original + "APIKeyLOCAL=another-key\n",
        "wrong_path": original.replace("/chat/completions", "/responses"),
        "expansion": original.replace("synthetic-key", "$UNRELATED_SECRET"),
    }
    dotenv.write_text(changes[damage])
    with pytest.raises(launcher.LauncherError) as failure:
        launcher.read_local_credentials()
    assert "synthetic-key" not in str(failure.value)


def test_local_preparation_and_resume_bind_profile_and_endpoint(launcher, tmp_path):
    profile, payload = profile_fixture(launcher, tmp_path)
    base_url, model, key = launcher.read_local_credentials()
    assert (base_url, model, key) == (payload["base_url"], payload["model"], "synthetic-key")
    local = launcher.local_profile_identity(profile, base_url=base_url, model=model)
    identity = {"provider": "local", "local_profile": local}
    run = tmp_path / ".local-test-evidence" / "local-run"
    launcher.prepare_run(run, identity, 18140, False, base_url=base_url, model=model)
    launcher.bind_slot_config(run, logical_slots=2, model_slots=1, resume=False,
                              local_profile=Path(local["path"]))
    launcher.bind_model_override(run, resume=False, model=model)
    config_path = run / "userdata/config.toml"
    config = launcher.tomllib.loads(config_path.read_text())
    assert config["orchestration"]["local_model_profile"] == str(profile.resolve())
    assert config["llm"] == {"base_url": base_url, "model": model,
                             "api_key": "$DESKPET_CLOUD_API_KEY"}
    assert json.loads((run / "userdata/llm_runtime.json").read_text()) == {
        "base_url": base_url, "model": model,
    }
    assert f'[models."{model}"]\ncontext_window = 262144\n'.encode() == (
        run / "userdata/model_overrides.toml").read_bytes()
    assert "synthetic-key" not in config_path.read_text()
    assert "synthetic-key" not in (run / "prepared.json").read_text()
    assert launcher.prepare_run(run, identity, 18140, True, base_url=base_url, model=model) == run
    launcher.bind_slot_config(run, logical_slots=2, model_slots=1, resume=True,
                              local_profile=Path(local["path"]))
    launcher.bind_model_override(run, resume=True, model=model)
    with pytest.raises(launcher.LauncherError):
        launcher.prepare_run(run, identity, 18140, True)  # no DeepSeek fallback
    config_path.write_text(config_path.read_text().replace(str(profile.resolve()), "/other/profile"))
    with pytest.raises(launcher.LauncherError, match="slot configuration"):
        launcher.bind_slot_config(run, logical_slots=2, model_slots=1, resume=True,
                                  local_profile=Path(local["path"]))


def test_changed_profile_bytes_cannot_resume_prepared_identity(launcher, tmp_path):
    profile, payload = profile_fixture(launcher, tmp_path)
    before = launcher.local_profile_identity(
        profile, base_url=payload["base_url"], model=payload["model"])
    run = tmp_path / ".local-test-evidence" / "local-run"
    launcher.prepare_run(run, {"local_profile": before}, 18140, False,
                         base_url=payload["base_url"], model=payload["model"])
    profile.write_bytes(profile.read_bytes() + b"\n")
    after = launcher.local_profile_identity(
        profile, base_url=payload["base_url"], model=payload["model"])
    with pytest.raises(launcher.LauncherError, match="identity"):
        launcher.prepare_run(run, {"local_profile": after}, 18140, True,
                             base_url=payload["base_url"], model=payload["model"])


@pytest.mark.parametrize("damage", ["profile_bytes", "tokenizer_bytes", "model", "context", "extra_field"])
def test_profile_identity_rejects_drift(launcher, tmp_path, damage):
    profile, payload = profile_fixture(launcher, tmp_path)
    original = launcher.local_profile_identity(
        profile, base_url=payload["base_url"], model=payload["model"])
    if damage == "profile_bytes":
        profile.write_bytes(profile.read_bytes() + b"\n")
        assert launcher.local_profile_identity(
            profile, base_url=payload["base_url"], model=payload["model"])["sha256"] != original["sha256"]
        return
    if damage == "tokenizer_bytes":
        (tmp_path / "tokenizer/tokenizer.json").write_bytes(b"changed")
    else:
        if damage == "model":
            payload["model"] = "another-model"
        elif damage == "context":
            payload["max_total_tokens"] = 32000
        else:
            payload["api_key"] = "should never be accepted"
        profile.write_text(json.dumps(payload))
    with pytest.raises(launcher.LauncherError, match="profile"):
        launcher.local_profile_identity(profile, base_url="http://127.0.0.1:8000/OpenAI/v1",
                                        model="qwen38-flash-next")


def test_direct_helpers_keep_deepseek_defaults(launcher, tmp_path):
    assert launcher.model_override_bytes() == launcher.MODEL_OVERRIDE
    run = tmp_path / ".local-test-evidence" / "deepseek-run"
    launcher.prepare_run(run, {"legacy": True}, 18140, False)
    assert "provider" not in json.loads((run / "prepared.json").read_text())
    launcher.bind_model_override(run, resume=False)
    launcher.prepare_run(run, {"legacy": True}, 18140, True)
    launcher.bind_model_override(run, resume=True)
