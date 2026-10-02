"""Crashed SQLite evidence is inspected without recovering the original files."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

MAX_MARKER_SECONDS = 15  # Includes interpreter/import startup.
REAP_SECONDS = 3

CHILD = """
import json, sqlite3, sys, time
from pathlib import Path
path, marker, mode = sys.argv[1:]
connection = sqlite3.connect(path)
connection.execute('PRAGMA journal_mode=' + mode)
connection.execute('PRAGMA synchronous=FULL')
connection.execute('PRAGMA cache_size=2')
connection.execute('BEGIN IMMEDIATE')
connection.execute('UPDATE evidence SET payload=?', ('pending-' * 512,))
if mode == 'WAL':
    connection.commit()
Path(marker).write_text(json.dumps({'mode': mode}))
while True:
    time.sleep(1)
"""


def _stderr(log):
    with log.open("rb") as stream:
        stream.seek(max(0, log.stat().st_size - 8192))
        return stream.read().decode("utf-8", errors="replace")


def _marker(child, marker, log):
    deadline = time.monotonic() + MAX_MARKER_SECONDS
    while time.monotonic() < deadline:
        if marker.exists():
            return json.loads(marker.read_text(encoding="utf-8"))
        if child.poll() is not None:
            raise AssertionError(f"child exited {child.returncode}: {_stderr(log)}")
        time.sleep(0.02)
    raise AssertionError(f"durable marker timeout: {_stderr(log)}")


def _kill(child):
    os.killpg(child.pid, signal.SIGKILL)
    child.wait(timeout=REAP_SECONDS)
    assert child.returncode == -signal.SIGKILL


def _cleanup(child):
    if child.poll() is None:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    child.wait(timeout=REAP_SECONDS)


def _rows(path, query, args=()):
    # Only called after the owning process has exited. A hot rollback journal
    # requires recovery even for SELECT; let SQLite recover an evidence copy,
    # leaving the original DB and sidecars untouched for the actual cold owner.
    # Copy WAL too; immutable=1 or copying only the main file would lose facts.
    with tempfile.TemporaryDirectory(prefix="sqlite-observation-", dir=path.parent) as directory:
        copied = Path(directory) / path.name
        shutil.copyfile(path, copied)
        for suffix in ("-journal", "-wal"):
            sidecar = Path(str(path) + suffix)
            if sidecar.is_file():
                shutil.copyfile(sidecar, Path(str(copied) + suffix))
        connection = sqlite3.connect(copied)
        try:
            return connection.execute(query, args).fetchall()
        finally:
            connection.close()


def _identity(path):
    return {
        suffix: hashlib.sha256(Path(str(path) + suffix).read_bytes()).hexdigest()
        for suffix in ("", "-journal", "-wal", "-shm")
        if Path(str(path) + suffix).is_file()
    }


@pytest.mark.parametrize("mode", ["DELETE", "WAL"])
def test_observation_recovers_crashed_copy_and_preserves_original_for_cold_owner(tmp_path, mode):
    path = tmp_path / "evidence.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA page_size=1024")
        connection.execute("CREATE TABLE evidence(id INTEGER PRIMARY KEY, payload TEXT)")
        connection.executemany(
            "INSERT INTO evidence VALUES (?, ?)",
            [(number, "original" * 512) for number in range(64)],
        )
    connection.close()
    marker = tmp_path / "boundary.json"
    log = tmp_path / "child.log"
    with log.open("wb") as stderr:
        child = subprocess.Popen(
            [sys.executable, "-c", CHILD, str(path), str(marker), mode],
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            start_new_session=True,
        )
    try:
        assert _marker(child, marker, log) == {"mode": mode}
        _kill(child)
    finally:
        _cleanup(child)
    suffix = "-journal" if mode == "DELETE" else "-wal"
    assert Path(str(path) + suffix).stat().st_size > 512
    if mode == "DELETE":
        assert Path(str(path) + suffix).read_bytes()[:8] == bytes.fromhex("d9d505f920a163d7")
        readonly = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                readonly.execute("SELECT * FROM evidence").fetchall()
        finally:
            readonly.close()
    before = _identity(path)
    expected = "original" * 512 if mode == "DELETE" else "pending-" * 512
    query = "SELECT DISTINCT payload FROM evidence"
    assert _rows(path, query) == [(expected,)]
    assert _identity(path) == before
    assert not list(tmp_path.glob("sqlite-observation-*"))
    # Only now does the real owner recover the original crash state.
    with sqlite3.connect(path) as cold:
        assert cold.execute(query).fetchall() == [(expected,)]
        assert cold.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    cold.close()
