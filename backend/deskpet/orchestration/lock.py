# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""One process per orchestration directory (plan review P0-2, HA-16).

The App has no single-instance guard, and the SDK treats live leases held under the same
owner as its own; a second process on the same directory must therefore stand aside.  A
``flock`` on ``.instance.lock`` is released by the kernel when its holder dies — the App
is ended with SIGKILL, so nothing here may rely on shutdown code.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

LOCK_NAME = ".instance.lock"


class InstanceLock:
    def __init__(self, root: str | Path) -> None:
        self._path = Path(root) / LOCK_NAME
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> bool:
        """True when this process now holds the directory; False when another does."""

        if self._fd is not None:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


__all__ = ("LOCK_NAME", "InstanceLock")
