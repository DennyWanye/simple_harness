# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""一个编排目录同一时刻只许一个进程用。

SDK 把同一个 owner 名下还活着的租约当作自己的，所以第二个进程开同一个目录必须让开。锁是文件锁，
持有者死掉时由内核释放——宿主可能被 SIGKILL 结束，这里不能依赖任何关闭代码。
POSIX 用 ``flock``，Windows 用 ``msvcrt.locking``。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

LOCK_NAME = ".instance.lock"

if sys.platform == "win32":  # pragma: no cover - 本机是 macOS
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class InstanceLock:
    def __init__(self, root: str | Path) -> None:
        self._path = Path(root) / LOCK_NAME
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> bool:
        """True：本进程现在持有这个目录；False：别的进程在用。"""

        if self._fd is not None:
            return True
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        if not _try_lock(fd):
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            _unlock(self._fd)
        finally:
            os.close(self._fd)
            self._fd = None


__all__ = ("LOCK_NAME", "InstanceLock")
