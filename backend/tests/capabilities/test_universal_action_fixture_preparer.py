from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = (
    REPO_ROOT
    / "scripts"
    / "acceptance"
    / "prepare_universal_action_fixtures.py"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(
    user_data: Path,
    workspace: Path,
    output: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--user-data-dir",
            str(user_data),
            "--workspace",
            str(workspace),
            "--output",
            str(output),
            *extra,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )


def test_preparer_emits_real_hashed_fixtures_and_launch_environment(
    tmp_path: Path,
) -> None:
    user_data = tmp_path / "user-data"
    workspace = tmp_path / "workspace"
    output = tmp_path / "evidence" / "fixture-manifest.json"

    completed = _run(user_data, workspace, output)

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["status"] == "PASS"
    assert summary["manifest_sha256"] == _sha256(output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    fixtures = payload["fixtures"]

    photos = fixtures["photos"]
    assert "中文 空格" in photos["input_dir"]
    assert len(photos["input_files"]) == 5
    exif_rows = [
        row
        for row in photos["input_files"]
        if row.get("exif_datetime_original")
    ]
    assert len(exif_rows) == 3
    for row in photos["input_files"]:
        path = Path(row["path"])
        assert path.is_file()
        assert row["sha256"] == _sha256(path)
    with Image.open(exif_rows[0]["path"]) as image:
        assert image.getexif().get(36867) == (
            exif_rows[0]["exif_datetime_original"]
        )

    ultraforge = fixtures["ultraforge_badhash"]
    configured = json.loads(
        ultraforge["environment"]["DESKPET_CAPABILITY_SOURCES_JSON"]
    )
    assert configured["ultraforge"]["type"] == "local"
    assert not Path(ultraforge["canary"]).exists()

    uac = fixtures["uac_wait"]
    assert uac["signature"]["status"] == "Valid"
    assert uac["signed_executable_sha256"] == _sha256(
        Path(uac["signed_executable"])
    )
    assert not Path(uac["completion_marker"]).exists()

    fail_once = fixtures["godot_fail_once"]
    assert fail_once["environment"][
        "DESKPET_CAPABILITY_E2E_CASE_ID"
    ] == "UA-GODOT-FAIL-ONCE"
    assert not Path(fail_once["marker"]).exists()


def test_preparer_is_idempotent_until_a_fixture_is_contaminated(
    tmp_path: Path,
) -> None:
    user_data = tmp_path / "user-data"
    workspace = tmp_path / "workspace"
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    assert _run(user_data, workspace, first).returncode == 0
    assert _run(user_data, workspace, second).returncode == 0

    payload = json.loads(first.read_text(encoding="utf-8"))
    canary = Path(
        payload["fixtures"]["ultraforge_badhash"]["canary"]
    )
    canary.parent.mkdir(parents=True, exist_ok=True)
    canary.write_text("fixture was executed\n", encoding="utf-8")
    failed = _run(user_data, workspace, tmp_path / "third.json")
    assert failed.returncode == 1
    assert (
        "user-data is not fresh" in failed.stderr
        or "contaminated" in failed.stderr
    )


def test_preparer_rejects_photo_outputs_or_stale_user_data(
    tmp_path: Path,
) -> None:
    user_data = tmp_path / "user-data"
    workspace = tmp_path / "workspace"
    first = tmp_path / "first.json"
    assert _run(user_data, workspace, first).returncode == 0
    payload = json.loads(first.read_text(encoding="utf-8"))
    input_dir = Path(payload["fixtures"]["photos"]["input_dir"])
    (input_dir / "20240102_030405.jpg").write_bytes(
        (input_dir / "IMG_A.jpg").read_bytes()
    )
    contaminated = _run(
        user_data,
        workspace,
        tmp_path / "contaminated.json",
    )
    assert contaminated.returncode == 1
    assert "UA-PHOTOS is contaminated" in contaminated.stderr

    clean_workspace = tmp_path / "clean-workspace"
    stale = user_data / "capabilities" / "generated-pack.json"
    stale.parent.mkdir(parents=True)
    stale.write_text("{}\n", encoding="utf-8")
    stale_result = _run(
        user_data,
        clean_workspace,
        tmp_path / "stale.json",
    )
    assert stale_result.returncode == 1
    assert "user-data is not fresh" in stale_result.stderr


def test_fail_once_reset_is_explicit_and_exact(tmp_path: Path) -> None:
    user_data = tmp_path / "user-data"
    workspace = tmp_path / "workspace"
    marker = (
        user_data
        / "acceptance-fixtures"
        / "ua-godot-fail-once"
        / "headless-launch-consumed.json"
    )
    marker.parent.mkdir(parents=True)
    marker.write_text('{"consumed":true}\n', encoding="utf-8")
    sibling = marker.parent / "keep-me.txt"
    sibling.write_text("keep\n", encoding="utf-8")

    failed = _run(user_data, workspace, tmp_path / "failed.json")
    assert failed.returncode == 1
    assert marker.exists()

    passed = _run(
        user_data,
        workspace,
        tmp_path / "passed.json",
        "--reset-fail-once",
        "--reset-only",
    )
    assert passed.returncode == 0, passed.stderr
    assert not marker.exists()
    assert sibling.read_text(encoding="utf-8") == "keep\n"
