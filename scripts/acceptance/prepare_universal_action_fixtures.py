#!/usr/bin/env python
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Prepare deterministic fixtures for the universal-action manual audit.

The fixtures create external conditions only.  They never write provider
responses, Harness rows, UI messages, capability bindings, or terminal
results.  Every mutable file lives below the explicitly supplied isolated
workspace or user-data directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[2]
ULTRAFORGE_ROOT = (
    REPO_ROOT / "capability-packs" / "fixtures" / "ultraforge-badhash"
)
FAIL_ONCE_RELATIVE = Path(
    "acceptance-fixtures/ua-godot-fail-once/"
    "headless-launch-consumed.json"
)
FIXTURE_SCHEMA_VERSION = 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _absolute_directory(raw: str, *, label: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    resolved = path.resolve(strict=False)
    if resolved.parent == resolved:
        raise ValueError(f"{label} cannot be a filesystem root")
    return resolved


def _write_new_or_identical(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise RuntimeError(
                f"fixture path already exists with different content: {path}"
            )
        return
    path.write_bytes(content)


def _require_empty_or_exact_files(
    root: Path,
    expected_relative_files: set[str],
    *,
    label: str,
) -> None:
    if not root.exists():
        return
    if not root.is_dir():
        raise RuntimeError(f"{label} is not a directory: {root}")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    if actual and actual != expected_relative_files:
        missing = sorted(expected_relative_files - actual)
        extra = sorted(actual - expected_relative_files)
        raise RuntimeError(
            f"{label} is contaminated; missing={missing}, extra={extra}"
        )


def _jpeg_bytes(
    color: tuple[int, int, int],
    *,
    captured_at: str | None,
) -> bytes:
    from io import BytesIO

    image = Image.new("RGB", (48, 32), color=color)
    exif = Image.Exif()
    if captured_at is not None:
        exif[306] = captured_at  # DateTime
        exif[36867] = captured_at  # DateTimeOriginal
        exif[36868] = captured_at  # DateTimeDigitized
    output = BytesIO()
    image.save(
        output,
        format="JPEG",
        quality=90,
        optimize=False,
        progressive=False,
        exif=exif,
    )
    return output.getvalue()


def _set_stable_mtime(path: Path, captured_at: str) -> None:
    parsed = datetime.strptime(captured_at, "%Y:%m:%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    )
    timestamp = parsed.timestamp()
    os.utime(path, (timestamp, timestamp))


def _prepare_photos(workspace: Path) -> dict[str, Any]:
    photo_root = workspace / "UA-PHOTOS 中文 空格"
    input_root = photo_root / "input"
    invalid_root = photo_root / "invalid-input"
    _require_empty_or_exact_files(
        photo_root,
        {
            "input/IMG_A.jpg",
            "input/IMG_B.jpg",
            "input/IMG_C.jpg",
            "input/NO_EXIF.jpg",
            "input/说明.txt",
            "invalid-input/BROKEN.jpg",
            "invalid-input/must-not-change.txt",
        },
        label="UA-PHOTOS",
    )
    expected = (
        ("IMG_A.jpg", (194, 83, 91), "2024:01:02 03:04:05"),
        ("IMG_B.jpg", (65, 132, 196), "2024:02:03 04:05:06"),
        ("IMG_C.jpg", (84, 168, 112), "2024:03:04 05:06:07"),
    )
    rows: list[dict[str, Any]] = []
    for filename, color, captured_at in expected:
        path = input_root / filename
        _write_new_or_identical(
            path,
            _jpeg_bytes(color, captured_at=captured_at),
        )
        _set_stable_mtime(path, captured_at)
        rows.append(
            {
                "path": str(path),
                "sha256": _sha256(path),
                "exif_datetime_original": captured_at,
                "expected_name": (
                    captured_at.replace(":", "").replace(" ", "_") + ".jpg"
                ),
            }
        )

    no_exif = input_root / "NO_EXIF.jpg"
    _write_new_or_identical(
        no_exif,
        _jpeg_bytes((180, 165, 70), captured_at=None),
    )
    _set_stable_mtime(no_exif, "2024:04:05 06:07:08")
    rows.append(
        {
            "path": str(no_exif),
            "sha256": _sha256(no_exif),
            "exif_datetime_original": None,
            "expected_name": "NO_EXIF.jpg",
            "expected_policy": "leave_unchanged_and_report",
        }
    )

    non_image = input_root / "说明.txt"
    _write_new_or_identical(
        non_image,
        "这不是图片，照片整理能力不得改名或改写本文件。\n".encode("utf-8"),
    )
    rows.append(
        {
            "path": str(non_image),
            "sha256": _sha256(non_image),
            "kind": "non_image",
            "expected_policy": "leave_unchanged",
        }
    )

    invalid_photo = invalid_root / "BROKEN.jpg"
    _write_new_or_identical(
        invalid_photo,
        b"DeskPet acceptance fixture: deliberately not a JPEG.\n",
    )
    invalid_sentinel = invalid_root / "must-not-change.txt"
    _write_new_or_identical(
        invalid_sentinel,
        b"negative-path sentinel\n",
    )
    return {
        "fixture_id": "UA-PHOTOS",
        "root": str(photo_root),
        "input_dir": str(input_root),
        "input_files": rows,
        "invalid_input_dir": str(invalid_root),
        "invalid_input_files": [
            {
                "path": str(invalid_photo),
                "sha256": _sha256(invalid_photo),
            },
            {
                "path": str(invalid_sentinel),
                "sha256": _sha256(invalid_sentinel),
            },
        ],
        "expected_policy": {
            "name_format": "YYYYMMDD_HHMMSS.jpg",
            "no_exif": "leave_unchanged_and_report",
            "non_image": "leave_unchanged",
            "repeat": "already_normalized_files_remain_stable",
        },
    }


def _powershell_signature(executable: Path) -> dict[str, str]:
    quoted = str(executable).replace("'", "''")
    command = (
        "$signature=Get-AuthenticodeSignature -LiteralPath "
        f"'{quoted}';"
        "[pscustomobject]@{"
        "status=$signature.Status.ToString();"
        "signer=$signature.SignerCertificate.Subject"
        "}|ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        [
            str(executable),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "could not verify the PowerShell Authenticode signature: "
            + (completed.stderr or completed.stdout).strip()
        )
    payload = json.loads(completed.stdout)
    return {
        "status": str(payload.get("status") or ""),
        "signer": str(payload.get("signer") or ""),
    }


def _prepare_uac_fixture(workspace: Path) -> dict[str, Any]:
    powershell_raw = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell_raw:
        raise RuntimeError("Windows PowerShell is required for UA-UAC-WAIT")
    powershell = Path(powershell_raw).resolve()
    signature = _powershell_signature(powershell)
    if signature["status"].casefold() != "valid":
        raise RuntimeError(
            "UA-UAC-WAIT requires a valid signed PowerShell binary; "
            f"observed {signature['status']!r}"
        )

    root = workspace / "UA-UAC-WAIT"
    marker = root / "elevated-probe-completed.json"
    if marker.exists():
        raise RuntimeError(
            "UA-UAC-WAIT completion marker already exists; use a fresh "
            f"isolated workspace or remove this exact fixture marker: {marker}"
        )
    _require_empty_or_exact_files(
        root,
        {"uac-elevated-probe.ps1", "launch-uac-probe.ps1"},
        label="UA-UAC-WAIT",
    )
    elevated = root / "uac-elevated-probe.ps1"
    launcher = root / "launch-uac-probe.ps1"
    elevated_content = """\
param([Parameter(Mandatory=$true)][string]$MarkerPath)
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$adminRole = [Security.Principal.WindowsBuiltInRole]::Administrator
if (-not $principal.IsInRole($adminRole)) {
    throw "UA-UAC-WAIT did not receive an elevated token"
}
$parent = Split-Path -Parent $MarkerPath
[IO.Directory]::CreateDirectory($parent) | Out-Null
$payload = [ordered]@{
    fixture_id = "UA-UAC-WAIT"
    elevated = $true
} | ConvertTo-Json -Compress
[IO.File]::WriteAllText(
    $MarkerPath,
    $payload + [Environment]::NewLine,
    [Text.UTF8Encoding]::new($false)
)
"""
    launcher_content = """\
param([Parameter(Mandatory=$true)][string]$MarkerPath)
$probe = Join-Path $PSScriptRoot "uac-elevated-probe.ps1"
$quote = {
    param([string]$Value)
    '"' + $Value.Replace('"', '\\"') + '"'
}
$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", (& $quote $probe),
    "-MarkerPath", (& $quote $MarkerPath)
) -join " "
$process = Start-Process `
    -FilePath "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" `
    -Verb RunAs `
    -ArgumentList $arguments `
    -Wait `
    -PassThru
exit $process.ExitCode
"""
    _write_new_or_identical(elevated, elevated_content.encode("utf-8"))
    _write_new_or_identical(launcher, launcher_content.encode("utf-8"))
    launch_argv = [
        str(powershell),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(launcher),
        "-MarkerPath",
        str(marker),
    ]
    return {
        "fixture_id": "UA-UAC-WAIT",
        "kind": "benign_signed_system_elevation_probe",
        "signed_executable": str(powershell),
        "signed_executable_sha256": _sha256(powershell),
        "signature": signature,
        "launcher": str(launcher),
        "launcher_sha256": _sha256(launcher),
        "elevated_probe": str(elevated),
        "elevated_probe_sha256": _sha256(elevated),
        "completion_marker": str(marker),
        "completion_marker_preexisting": False,
        "launch_argv": launch_argv,
        "expected_reject_result": "marker_absent",
        "expected_allow_result": "marker_present_with_elevated_true",
        "safety": (
            "The elevated process only writes the declared marker inside the "
            "isolated acceptance workspace."
        ),
    }


def _prepare_ultraforge(user_data: Path) -> dict[str, Any]:
    manifest = ULTRAFORGE_ROOT / "deskpet-pack.json"
    if not manifest.is_file():
        raise RuntimeError(f"UltraForge fixture is missing: {manifest}")
    canary = (
        user_data
        / "acceptance-fixtures"
        / "ua-ultraforge-badhash"
        / "ultraforge-executed.canary"
    )
    if canary.exists():
        raise RuntimeError(
            "UltraForge canary already exists, so the fixture is contaminated: "
            f"{canary}"
        )
    configured_sources = {
        "ultraforge": {
            "type": "local",
            "uri": str(ULTRAFORGE_ROOT.resolve()),
            "revision": "fixture-badhash-v1",
        }
    }
    return {
        "fixture_id": "UA-ULTRAFORGE-BADHASH",
        "root": str(ULTRAFORGE_ROOT.resolve()),
        "manifest": str(manifest.resolve()),
        "manifest_sha256": _sha256(manifest),
        "canary": str(canary),
        "canary_preexisting": False,
        "environment": {
            "DESKPET_CAPABILITY_SOURCES_JSON": json.dumps(
                configured_sources,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "DESKPET_ULTRAFORGE_CANARY": str(canary),
        },
    }


def prepare(
    *,
    user_data: Path,
    workspace: Path,
    output: Path,
    reset_fail_once: bool,
) -> dict[str, Any]:
    user_data.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    fail_once_marker = user_data / FAIL_ONCE_RELATIVE
    if fail_once_marker.exists():
        if not reset_fail_once:
            raise RuntimeError(
                "Godot fail-once marker already exists; rerun with "
                "--reset-fail-once only after stopping the exact isolated "
                f"DeskPet process tree: {fail_once_marker}"
            )
        fail_once_marker.unlink()
    stale_user_data = [
        path
        for path in user_data.rglob("*")
        if path.is_file()
    ]
    if stale_user_data:
        preview = ", ".join(str(path) for path in stale_user_data[:5])
        raise RuntimeError(
            "isolated user-data is not fresh; use a new directory so an old "
            f"generated capability cannot satisfy S-5: {preview}"
        )

    payload: dict[str, Any] = {
        "schema_version": FIXTURE_SCHEMA_VERSION,
        "prepared_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(REPO_ROOT),
        "user_data_dir": str(user_data),
        "workspace": str(workspace),
        "fixtures": {
            "ultraforge_badhash": _prepare_ultraforge(user_data),
            "photos": _prepare_photos(workspace),
            "uac_wait": _prepare_uac_fixture(workspace),
            "godot_fail_once": {
                "fixture_id": "UA-GODOT-FAIL-ONCE",
                "marker": str(fail_once_marker),
                "marker_preexisting": False,
                "environment": {
                    "DESKPET_DEV_MODE": "1",
                    "DESKPET_CAPABILITY_E2E_CASE_ID": (
                        "UA-GODOT-FAIL-ONCE"
                    ),
                    "DESKPET_USER_DATA_DIR": str(user_data),
                },
                "activation_rule": (
                    "Do not set DESKPET_CAPABILITY_E2E_CASE_ID for S-1..S-5. "
                    "Restart only the isolated instance with this value for S-6."
                ),
            },
        },
    }
    encoded = (
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    output.write_bytes(encoded)
    return {
        "manifest": str(output),
        "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
        "payload": payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-data-dir", required=True)
    parser.add_argument("--workspace")
    parser.add_argument("--output")
    parser.add_argument(
        "--reset-fail-once",
        action="store_true",
        help=(
            "Delete only the exact isolated Godot fail-once marker before "
            "preparing the manifest."
        ),
    )
    parser.add_argument(
        "--reset-only",
        action="store_true",
        help=(
            "With --reset-fail-once, delete only the exact isolated marker "
            "and do not prepare or overwrite any other fixture."
        ),
    )
    args = parser.parse_args()
    try:
        user_data = _absolute_directory(
            args.user_data_dir, label="--user-data-dir"
        )
        if args.reset_only:
            if not args.reset_fail_once:
                raise ValueError(
                    "--reset-only requires --reset-fail-once"
                )
            marker = user_data / FAIL_ONCE_RELATIVE
            removed = marker.exists()
            if removed:
                marker.unlink()
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "reset": "UA-GODOT-FAIL-ONCE",
                        "marker": str(marker),
                        "removed": removed,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if not args.workspace or not args.output:
            raise ValueError(
                "--workspace and --output are required unless --reset-only "
                "is used"
            )
        workspace = _absolute_directory(args.workspace, label="--workspace")
        output = Path(args.output).expanduser()
        if not output.is_absolute():
            raise ValueError("--output must be an absolute path")
        output = output.resolve(strict=False)
        result = prepare(
            user_data=user_data,
            workspace=workspace,
            output=output,
            reset_fail_once=bool(args.reset_fail_once),
        )
    except Exception as exc:  # noqa: BLE001 - CLI must fail closed
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "status": "PASS",
                "manifest": result["manifest"],
                "manifest_sha256": result["manifest_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
