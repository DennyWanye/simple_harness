# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Frozen G launcher oracles: preparation never reads credentials or launches.

Only explicit launch admits the dedicated process credential. The bundle must
contain the macOS frozen backend; resume cannot adopt arbitrary old user data.
These tests fake application spawn and use pipes only for log transport, never
a provider, native application or OS credential store.
"""

import importlib.util
import io
import json
import os
import plistlib
import threading
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts/native/launch_frozen_orchestrator.py"
)


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "frozen_orchestrator_launcher", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "HOST_ROOT", tmp_path)
    monkeypatch.setattr(module, "is_ignored", lambda path: True)
    return module


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "Candidate.app"
    (root / "Contents/MacOS").mkdir(parents=True)
    backend = root / "Contents/Resources/backend"
    (backend / "_internal").mkdir(parents=True)
    for executable in (root / "Contents/MacOS/candidate", backend / "deskpet-backend"):
        executable.write_bytes(b"test executable; never run")
        executable.chmod(0o755)
    (root / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleExecutable": "candidate",
                "CFBundleIdentifier": "com.dennywanye.simpleharness.g-frozen-test",
                "CFBundleShortVersionString": "test",
            }
        )
    )
    return root


def args(tmp_path, bundle, *extra):
    return [
        "--bundle",
        str(bundle),
        "--run-dir",
        str(tmp_path / ".local-test-evidence/g/run"),
        "--port",
        "18130",
        *extra,
    ]


def test_dry_run_prepares_only_keyfree_local_configuration(
    launcher, bundle, tmp_path, monkeypatch
):
    def forbidden(*a, **kw):
        raise AssertionError(
            "dry run must not read credentials, launch, or make requests"
        )

    monkeypatch.setattr(launcher, "read_key", forbidden)
    monkeypatch.setattr(launcher.subprocess, "Popen", forbidden)
    assert launcher.main(args(tmp_path, bundle)) == 0
    run = tmp_path / ".local-test-evidence/g/run"
    import tomllib

    config = tomllib.loads((run / "userdata/config.toml").read_text())
    assert config["llm"] == {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
        "api_key": "$DESKPET_CLOUD_API_KEY",
    }
    assert config["voice"]["enabled"] is False
    assert config["orchestration"]["enabled"] is True
    assert "local" not in config["llm"] and "endpoints" not in config["llm"]
    assert json.loads((run / "userdata/llm_runtime.json").read_text()) == {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
    }
    assert not (run / "native.log").exists()
    assert not (run / "userdata/onboarding_done.json").exists()


@pytest.mark.parametrize(
    "damage",
    [
        "backend_missing",
        "exe_only",
        "internal_missing",
        "default_id",
        "unsafe_executable",
    ],
)
def test_invalid_bundle_refuses_before_preparation(launcher, bundle, tmp_path, damage):
    backend = bundle / "Contents/Resources/backend/deskpet-backend"
    if damage == "backend_missing":
        backend.unlink()
    elif damage == "exe_only":
        backend.rename(backend.with_suffix(".exe"))
    elif damage == "internal_missing":
        backend.parent.joinpath("_internal").rmdir()
    else:
        path = bundle / "Contents/Info.plist"
        info = plistlib.loads(path.read_bytes())
        info[
            "CFBundleIdentifier" if damage == "default_id" else "CFBundleExecutable"
        ] = "com.dennywanye.simpleharness" if damage == "default_id" else "../outside"
        path.write_bytes(plistlib.dumps(info))
    with pytest.raises(launcher.LauncherError):
        launcher.main(args(tmp_path, bundle))
    assert not (tmp_path / ".local-test-evidence/g/run").exists()


def test_run_dir_must_be_ignored_and_fresh(launcher, bundle, tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "is_ignored", lambda path: False)
    with pytest.raises(launcher.LauncherError):
        launcher.main(args(tmp_path, bundle))
    monkeypatch.setattr(launcher, "is_ignored", lambda path: True)
    launcher.main(args(tmp_path, bundle))
    with pytest.raises(launcher.LauncherError):
        launcher.main(args(tmp_path, bundle))


@pytest.mark.parametrize(
    "damage",
    [
        "internal_bytes",
        "resource_bytes",
        "external_file",
        "external_dir",
        "internal_link_target",
    ],
)
def test_resume_freezes_all_resources_before_reading_key_or_spawning(
    launcher, bundle, tmp_path, monkeypatch, damage
):
    internal = bundle / "Contents/Resources/backend/_internal"
    library = internal / "Python.framework/Versions/A/Python"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"frozen Python library")
    alternate = library.with_name("Python-copy")
    alternate.write_bytes(library.read_bytes())
    link = library.parent.parent / "Current"
    link.symlink_to("A", target_is_directory=True)
    public_link = internal / "Python.framework/Python"
    public_link.symlink_to("Versions/Current/Python")
    resource = bundle / "Contents/Resources/config.toml"
    resource.write_text("resource = 'frozen'\n")
    launcher.main(args(tmp_path, bundle))
    assert launcher.main(args(tmp_path, bundle, "--resume")) == 0
    marker = json.loads(
        (tmp_path / ".local-test-evidence/g/run/prepared.json").read_text()
    )
    assert len(marker["bundle"]["internal_tree_sha256"]) == 64
    assert len(marker["bundle"]["resource_tree_sha256"]) == 64

    if damage == "internal_bytes":
        library.write_bytes(b"replacement Python library")
    elif damage == "resource_bytes":
        resource.write_text("resource = 'replacement'\n")
    elif damage == "internal_link_target":
        public_link.unlink()
        public_link.symlink_to(
            "Versions/Current/Python-copy"
        )  # same bytes, different binding
    else:
        external = tmp_path / "outside"
        external.mkdir()
        (external / "module.py").write_text("outside code")
        (library.parent / "escape").symlink_to(
            external if damage == "external_dir" else external / "module.py",
            target_is_directory=damage == "external_dir",
        )

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "changed resources must fail before key read or process spawn"
        )

    monkeypatch.setattr(launcher, "read_key", forbidden)
    monkeypatch.setattr(launcher.subprocess, "Popen", forbidden)
    with pytest.raises(launcher.LauncherError):
        launcher.main(args(tmp_path, bundle, "--resume", "--launch"))
    if damage.startswith("external"):
        with pytest.raises(launcher.LauncherError):
            launcher.main(args(tmp_path, bundle, "--launch"))
    assert (
        json.loads((tmp_path / ".local-test-evidence/g/run/prepared.json").read_text())
        == marker
    )


@pytest.mark.parametrize(
    "damage", [None, "unowned", "runtime_key", "other_model", "other_bundle", "symlink"]
)
def test_resume_only_own_prepared_keyfree_identity(launcher, bundle, tmp_path, damage):
    launcher.main(args(tmp_path, bundle))
    run = tmp_path / ".local-test-evidence/g/run"
    if damage == "unowned":
        (run / "prepared.json").unlink()
    elif damage in {"runtime_key", "other_model"}:
        path = run / "userdata/llm_runtime.json"
        value = json.loads(path.read_text())
        value["api_key" if damage == "runtime_key" else "model"] = "must refuse"
        path.write_text(json.dumps(value))
    elif damage == "other_bundle":
        (bundle / "Contents/MacOS/candidate").write_bytes(b"changed candidate")
    elif damage == "symlink":
        user = run / "userdata"
        user.rename(run / "other")
        user.symlink_to(run / "other", target_is_directory=True)
    if damage:
        with pytest.raises(launcher.LauncherError):
            launcher.main(args(tmp_path, bundle, "--resume"))
    else:
        before = (run / "userdata/config.toml").read_bytes()
        assert launcher.main(args(tmp_path, bundle, "--resume")) == 0
        assert (run / "userdata/config.toml").read_bytes() == before


def test_child_environment_is_allowlisted_and_key_is_process_only(
    launcher, bundle, tmp_path, monkeypatch
):
    sentinel = "dedicated-fake-key-for-launch-test"
    monkeypatch.setenv("APIKEY", "other-credential")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "other-credential")
    monkeypatch.setenv("DESKPET_BACKEND_DIR", "/source/backend")
    monkeypatch.setenv("PYTHONPATH", "/source")
    monkeypatch.setenv("DESKPET_ORCHESTRATION_TEST_SCENARIO", "approval-action")
    monkeypatch.setenv("DESKPET_CLOUD_API_KEY", "inherited-wrong-key")
    monkeypatch.setattr(launcher, "read_key", lambda: sentinel)
    captured = {}

    class Child:
        pid = 12345
        returncode = 0

        def wait(self):
            return 0

    def spawn(command, **kwargs):
        captured.update(command=command, **kwargs)
        return Child()

    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)

    def drain(child, output, key):
        output.write(launcher.redact("Bearer opaque SHARED_SECRET=opaque " + key, key))
        return {"complete": True, "truncated": False}

    monkeypatch.setattr(launcher, "drain_log", drain)
    assert launcher.main(args(tmp_path, bundle, "--launch")) == 0
    assert captured["command"] == [str(bundle / "Contents/MacOS/candidate")]
    env = captured["env"]
    assert env["DESKPET_CLOUD_API_KEY"] == sentinel
    assert env["DESKPET_DEV_MODE"] == "0"
    for name in (
        "APIKEY",
        "AWS_SECRET_ACCESS_KEY",
        "DESKPET_BACKEND_DIR",
        "DESKPET_PYTHON",
        "PYTHONPATH",
        "PYTHONHOME",
        "DESKPET_ORCHESTRATION_TEST_SCENARIO",
    ):
        assert name not in env
    run = tmp_path / ".local-test-evidence/g/run"
    assert captured["cwd"] == str(run)
    assert not captured.get("start_new_session", False)  # main wrapper owns the group
    for path in run.rglob("*"):
        if path.is_file():
            assert sentinel.encode() not in path.read_bytes()


def test_dotenv_reads_only_dedicated_field_without_expansion(launcher, tmp_path):
    (tmp_path / ".env").write_text(
        'APIKEY=wrong\nexport DEEPSEEKER_APIKEY="fake-$literal"\n'
    )
    assert launcher.read_key() == "fake-$literal"
    (tmp_path / ".env").write_text("APIKEY=wrong\n")
    with pytest.raises(launcher.LauncherError):
        launcher.read_key()


def test_final_drain_keeps_buffered_tail_and_redacts_split_utf8_and_credentials(
    launcher,
):
    read_fd, write_fd = os.pipe()
    secret = "fake-dedicated-key"
    data = (
        "中文\n"
        + "tail " * 20000
        + secret
        + "\nBearer secret-token\nSHARED_SECRET=token\nfinal无换行"
    ).encode()

    def write():
        with os.fdopen(write_fd, "wb") as handle:
            for i in range(0, len(data), 3):
                handle.write(data[i : i + 3])

    writer = threading.Thread(target=write)

    class ExitedChild:
        stdout = os.fdopen(read_fd, "rb")

        def poll(self):
            return 0  # exited does not imply stdout has been drained

    writer.start()
    output = io.StringIO()
    try:
        result = launcher.drain_log(ExitedChild(), output, secret)
    finally:
        writer.join(timeout=5)
    assert not writer.is_alive()
    text = output.getvalue()
    assert result["complete"] is True
    assert text.endswith("final无换行") and text.startswith("中文\n")
    assert (
        secret not in text
        and "secret-token" not in text
        and "SHARED_SECRET=token" not in text
    )


def test_redactor_covers_bearer_and_handshake_tokens(launcher):
    assert launcher.redact("Bearer abc\nSHARED_SECRET:xyz\nfake-key", "fake-key") == (
        "Bearer [REDACTED]\nSHARED_SECRET:[REDACTED]\n[REDACTED]"
    )
    assert "hidden" not in launcher.redact(
        'Authorization: Bearer "hidden"\n{"SHARED_SECRET": "hidden"}', "fake-key"
    )


def test_oversized_line_is_omitted_without_leaking_a_split_secret(
    launcher, monkeypatch
):
    monkeypatch.setattr(launcher, "MAX_LINE_BYTES", 64)
    read_fd, write_fd = os.pipe()
    data = b"x" * 63 + b"fake-key" + b"x" * 1000 + b"\nsafe tail\n"

    def write():
        with os.fdopen(write_fd, "wb") as handle:
            for byte in data:
                handle.write(bytes([byte]))

    class ExitedChild:
        stdout = os.fdopen(read_fd, "rb")

        def poll(self):
            return 0

    writer = threading.Thread(target=write)
    writer.start()
    output = io.StringIO()
    try:
        result = launcher.drain_log(ExitedChild(), output, "fake-key")
    finally:
        writer.join(timeout=5)
    assert not writer.is_alive()
    assert result == {"complete": True, "truncated": True}
    assert output.getvalue() == "[oversized log line omitted]\nsafe tail\n"


def test_final_drain_deadline_does_not_wait_for_descendant_pipe(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "FINAL_DRAIN_SECONDS", 0)
    read_fd, write_fd = os.pipe()

    class ExitedChild:
        stdout = os.fdopen(read_fd, "rb")

        def poll(self):
            return 0

    try:
        assert launcher.drain_log(ExitedChild(), io.StringIO(), "fake-key") == {
            "complete": False,
            "truncated": True,
        }
    finally:
        os.close(write_fd)


def test_log_byte_limit_still_drains_to_eof(launcher, monkeypatch):
    monkeypatch.setattr(launcher, "MAX_LOG_BYTES", 8)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"visible\nfake-key\nlast\n")
    os.close(write_fd)

    class ExitedChild:
        stdout = os.fdopen(read_fd, "rb")

        def poll(self):
            return 0

    output = io.StringIO()
    assert launcher.drain_log(ExitedChild(), output, "fake-key") == {
        "complete": True,
        "truncated": True,
    }
    assert output.getvalue() == "visible\n"
