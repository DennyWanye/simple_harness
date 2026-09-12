# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Prepare or launch a frozen native candidate, under the owner's bounded wrapper.

Preparation is local and credential-free. Only --launch reads the dedicated Host
credential, and only the child environment receives it. No provider probe runs.
Tauri owns its backend and handshake; this launcher inherits the wrapper's group.
"""

from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import plistlib
import re
import selectors
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path

import tomllib

HOST_ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-flash"
MAX_LINE_BYTES = 256 * 1024
MAX_LOG_BYTES = 16 * 1024 * 1024
FINAL_DRAIN_SECONDS = 3.0
OWNER = "frozen-orchestrator-launcher-v1"
STANDARD_ENV = (
    "HOME",
    "USER",
    "LOGNAME",
    "PATH",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "SHELL",
    "__CF_USER_TEXT_ENCODING",
)


class LauncherError(ValueError):
    """A failed local precondition; messages must never contain credentials."""


def is_ignored(path: Path) -> bool:
    return (
        subprocess.run(
            ["git", "check-ignore", "--quiet", "--", str(path)],
            cwd=HOST_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        ).returncode
        == 0
    )


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _resource_identity(root: Path, internal: Path) -> dict[str, str]:
    """Bind the whole bundle, including framework links, without following cycles.

    Real directories are visited once. Link entries bind both the stored target
    and its resolved bundle-relative target; linked files additionally bind bytes.
    The complete tree includes targets outside _internal but inside the bundle.
    No external target is opened or hashed.
    """
    rows = []
    pending = [root]
    while pending:
        directory = pending.pop()
        for item in sorted(directory.iterdir()):
            relative = item.relative_to(root).as_posix()
            mode = item.lstat().st_mode
            row = {"path": relative, "mode": stat.S_IMODE(mode)}
            if stat.S_ISLNK(mode):
                try:
                    target = item.resolve(strict=True)
                except (OSError, RuntimeError):
                    raise LauncherError(
                        "broken or cyclic frozen resource link"
                    ) from None
                if not target.is_relative_to(root):
                    raise LauncherError("frozen resource link escapes bundle")
                row.update(
                    kind="symlink",
                    target=os.readlink(item),
                    resolved=target.relative_to(root).as_posix(),
                )
                if target.is_file():
                    row["sha256"] = _sha256(target)
                elif not target.is_dir():
                    raise LauncherError("unsupported frozen resource link target")
            elif stat.S_ISDIR(mode):
                row["kind"] = "directory"
                pending.append(item)
            elif stat.S_ISREG(mode):
                row.update(kind="file", sha256=_sha256(item))
            else:
                raise LauncherError("unsupported frozen resource type")
            rows.append(row)
    rows.sort(key=lambda row: row["path"])
    internal_prefix = internal.relative_to(root).as_posix()

    def digest(entries):
        return hashlib.sha256(
            json.dumps(
                entries,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()

    return {
        "resource_tree_sha256": digest(rows),
        "internal_tree_sha256": digest(
            [
                row
                for row in rows
                if row["path"] == internal_prefix
                or row["path"].startswith(internal_prefix + "/")
            ]
        ),
    }


def inspect_bundle(path: Path) -> dict:
    root = path.resolve(strict=True)
    if root.suffix != ".app" or not root.is_dir():
        raise LauncherError("bundle must be an existing .app")
    info = plistlib.loads((root / "Contents/Info.plist").read_bytes())
    name = info.get("CFBundleExecutable", "")
    bundle_id = info.get("CFBundleIdentifier", "")
    if (
        not isinstance(name, str)
        or not name
        or name in {".", ".."}
        or re.search(r"[/\\]", name)
    ):
        raise LauncherError("unsafe bundle executable")
    if (
        not isinstance(bundle_id, str)
        or not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", bundle_id)
        or bundle_id in {"com.dennywanye.simpleharness", "com.dennywanye.deskpet"}
    ):
        raise LauncherError("candidate needs a distinct bundle identifier")
    binary = root / "Contents/MacOS" / name
    backend = root / "Contents/Resources/backend/deskpet-backend"
    internal = backend.parent / "_internal"
    for item in (binary, backend, internal):
        if not item.resolve().is_relative_to(root):
            raise LauncherError("frozen resource escapes bundle")
    if any(
        not item.is_file() or not os.access(item, os.X_OK) for item in (binary, backend)
    ):
        raise LauncherError("native executable or macOS frozen backend missing")
    if not internal.is_dir():
        raise LauncherError("frozen backend _internal missing")
    return {
        "path": str(root),
        "identifier": bundle_id,
        "version": info.get("CFBundleShortVersionString"),
        "binary": str(binary),
        "binary_sha256": _sha256(binary),
        "backend_sha256": _sha256(backend),
        **_resource_identity(root, internal),
    }


def read_key() -> str:
    """Read only the named field; never expand, export or copy the dotenv file."""
    found = []
    with (HOST_ROOT / ".env").open(encoding="utf-8") as handle:
        for line in handle:
            match = re.match(r"^\s*(?:export\s+)?DEEPSEEKER_APIKEY\s*=(.*)$", line)
            if match:
                try:
                    words = shlex.split(match.group(1), comments=True, posix=True)
                except ValueError:
                    raise LauncherError(
                        "dedicated credential field is malformed"
                    ) from None
                if (
                    len(words) != 1
                    or not words[0]
                    or any(c.isspace() for c in words[0])
                ):
                    raise LauncherError(
                        "dedicated credential field is empty or malformed"
                    )
                found.append(words[0])
    if len(found) != 1:
        raise LauncherError("exactly one DEEPSEEKER_APIKEY field is required")
    return found[0]


def _write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    path.chmod(0o600)


def _no_symlinks(path: Path) -> None:
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise LauncherError("run paths must not contain symlinks")


def prepare_run(path: Path, bundle: dict, port: int, resume: bool) -> Path:
    path = path.absolute()
    _no_symlinks(path)
    run = path.resolve()
    evidence = (HOST_ROOT / ".local-test-evidence").resolve()
    if run == evidence or not run.is_relative_to(evidence) or not is_ignored(run):
        raise LauncherError(
            "run-dir must be under the Host ignored .local-test-evidence directory"
        )
    marker = {"owner": OWNER, "run_dir": str(run), "bundle": bundle, "port": port}
    user = run / "userdata"
    if resume:
        try:
            for item in (
                user,
                run / "prepared.json",
                user / "config.toml",
                user / "llm_runtime.json",
            ):
                _no_symlinks(item)
            if json.loads((run / "prepared.json").read_text()) != marker:
                raise LauncherError("resume identity does not match prepared run")
            config = tomllib.loads((user / "config.toml").read_text())
            runtime = json.loads((user / "llm_runtime.json").read_text())
            llm = config["llm"]
            if (
                llm.get("base_url") != BASE_URL
                or llm.get("model") != MODEL
                or llm.get("api_key") != "$DESKPET_CLOUD_API_KEY"
                or llm.get("local")
                or llm.get("endpoints")
                or runtime != {"base_url": BASE_URL, "model": MODEL}
                or config["backend"]["port"] != port
            ):
                raise LauncherError(
                    "resume provider configuration changed or contains persisted credentials"
                )
        except (OSError, KeyError, ValueError) as error:
            if isinstance(error, LauncherError):
                raise
            raise LauncherError(
                "resume requires intact launcher-prepared user data"
            ) from None
    else:
        if run.exists():
            raise LauncherError(
                "run-dir already exists; use --resume only for an owned prepared run"
            )
        run.mkdir(parents=True, mode=0o700)
        user.mkdir(mode=0o700)
        config_path = user / "config.toml"
        config_path.write_text(
            f'[backend]\nhost = "127.0.0.1"\nport = {port}\n\n'
            f'[llm]\nbase_url = "{BASE_URL}"\nmodel = "{MODEL}"\n'
            'api_key = "$DESKPET_CLOUD_API_KEY"\n\n'
            "[voice]\nenabled = false\n\n[supervisor]\nenabled = false\n\n"
            "[orchestration]\nenabled = true\n",
            encoding="utf-8",
        )
        config_path.chmod(0o600)
        _write_json(user / "llm_runtime.json", {"base_url": BASE_URL, "model": MODEL})
        _write_json(run / "prepared.json", marker)
    for name in ("logs", "cache"):
        _no_symlinks(run / name)
        (run / name).mkdir(mode=0o700, exist_ok=True)
    return run


def redact(text: str, key: str) -> str:
    if key:
        text = text.replace(key, "[REDACTED]")
    text = re.sub(
        r"(?i)([?&](?:secret|token|api_key)=)[^&\s\"\\]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)(\bBearer[ \t]+[\"']?)[^\s\"']+", r"\1[REDACTED]", text)
    return re.sub(
        r"(?i)(\bSHARED_SECRET[\"']?[=: \t]+[\"']?)[^\s\"',}]+",
        r"\1[REDACTED]",
        text,
    )


def drain_log(child, output, key: str) -> dict:
    """Redact complete lines before writing; bound memory, output and final drain.

    poll()==exited does not imply EOF. Descendants can keep the pipe open, so the
    final drain has a deadline. Oversized lines are discarded in their entirety.
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = ""
    discarding = False
    written = 0
    truncated = False
    complete = False
    deadline = None

    def persist(text):
        nonlocal written, truncated
        safe = redact(text, key)
        size = len(safe.encode("utf-8"))
        if written + size <= MAX_LOG_BYTES:
            output.write(safe)
            output.flush()
            written += size
        else:
            truncated = True

    def consume(text):
        nonlocal pending, discarding, truncated
        for part in text.splitlines(keepends=True):
            # Only LF ends a transport line; splitlines' other Unicode breaks
            # must not make a credential fragment persist separately.
            ended = part.endswith("\n")
            if not discarding:
                pending += part
                if len(pending.encode("utf-8")) > MAX_LINE_BYTES:
                    pending = ""
                    discarding = True
                    truncated = True
            if ended:
                if discarding:
                    persist("[oversized log line omitted]\n")
                else:
                    persist(pending)
                pending = ""
                discarding = False

    stream = child.stdout
    fd = stream.fileno()
    os.set_blocking(fd, False)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(fd, selectors.EVENT_READ)
            while True:
                if child.poll() is not None and deadline is None:
                    deadline = time.monotonic() + FINAL_DRAIN_SECONDS
                if deadline is not None and time.monotonic() >= deadline:
                    truncated = True
                    break
                if not selector.select(timeout=0.1):
                    continue
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    consume(decoder.decode(b"", final=True))
                    if pending and not discarding:
                        persist(pending)
                    complete = True
                    break
                consume(decoder.decode(chunk))
    finally:
        stream.close()
    return {"complete": complete, "truncated": truncated}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        raise LauncherError("port must be between 1024 and 65535")
    try:
        bundle = inspect_bundle(args.bundle)
    except (OSError, ValueError, plistlib.InvalidFileException):
        raise LauncherError("invalid frozen candidate bundle") from None
    run = prepare_run(args.run_dir, bundle, args.port, args.resume)
    if not args.launch:
        print(json.dumps({"prepared": str(run), "launched": False}))
        return 0
    key = read_key()
    env = {name: os.environ[name] for name in STANDARD_ENV if name in os.environ}
    env.update(
        DESKPET_USER_DATA_DIR=str(run / "userdata"),
        DESKPET_CONFIG=str(run / "userdata/config.toml"),
        DESKPET_BACKEND_PORT=str(args.port),
        DESKPET_DEV_MODE="0",
        DESKPET_USER_LOG_DIR=str(run / "logs"),
        DESKPET_USER_LOG=str(run / "logs"),
        DESKPET_USER_CACHE_DIR=str(run / "cache"),
        DESKPET_CLOUD_API_KEY=key,
    )
    # Unique files preserve previous cold-start evidence and refuse symlink reuse.
    stamp = time.time_ns()
    log = run / f"native-{stamp}.log"
    with log.open("x", encoding="utf-8") as output:
        log.chmod(0o600)
        child = subprocess.Popen(
            [bundle["binary"]],
            cwd=str(run),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        _write_json(
            run / f"launch-{stamp}.json",
            {
                "pid": child.pid,
                "inherited_process_group": os.getpgrp(),
                "bundle": bundle,
                "log": str(log),
                "provider": {"base_url": BASE_URL, "model": MODEL},
            },
        )
        drained = drain_log(child, output, key)
        returncode = child.wait()
    _write_json(
        run / f"exit-{stamp}.json", {"returncode": returncode, "log_drain": drained}
    )
    return returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LauncherError, OSError) as error:
        # OS errors can contain user-controlled paths; do not dump exceptions/env.
        print(f"frozen launcher failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(2) from None
