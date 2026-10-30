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
import tempfile
from pathlib import Path

from .session_ledger import SessionError

LOCK_NAME = ".instance.lock"
GENERATION_NAME = ".instance.generation"


class OwnerConflict(SessionError):
    """冲突时仍暴露当前持有代次，宿主无需从错误文本猜。"""

    def __init__(self, owner_generation: int) -> None:
        super().__init__("owner_conflict")
        self.owner_generation = owner_generation

    def public(self):
        return {**super().public(), "owner_generation": self.owner_generation}


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
        self._generation_path = self._path.parent / GENERATION_NAME
        self.owner_generation = 0

    def observed_generation(self) -> int:
        try:
            value = int(self._generation_path.read_text(encoding="ascii").strip())
        except FileNotFoundError:
            return 0
        if value < 1:
            raise ValueError("invalid owner generation")
        return value

    def _advance_generation(self) -> int:
        generation = self.observed_generation() + 1
        fd, temporary = tempfile.mkstemp(prefix=GENERATION_NAME + ".", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="ascii") as stream:
                stream.write(f"{generation}\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._generation_path)
            if sys.platform != "win32":
                directory = os.open(self._path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return generation

    @property
    def held(self) -> bool:
        if self._fd is None:
            return False
        probe = None
        try:
            opened, current = os.fstat(self._fd), self._path.stat()
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                return False
            if self.observed_generation() != self.owner_generation:
                return False
            # fd 尚在不代表内核锁尚在；另开描述符试取，成功说明原锁已丢失。
            probe = os.open(self._path, os.O_RDWR)
            if _try_lock(probe):
                _unlock(probe)
                return False
            return True
        except (OSError, ValueError):
            return False
        finally:
            if probe is not None:
                os.close(probe)

    def acquire(self) -> bool:
        """True：本进程现在持有这个目录；False：别的进程在用。"""

        if self._fd is not None:
            return self.held
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        if not _try_lock(fd):
            os.close(fd)
            self.owner_generation = self.observed_generation()
            return False
        try:
            self.owner_generation = self._advance_generation()
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            os.fsync(fd)
        except BaseException:
            _unlock(fd)
            os.close(fd)
            raise
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
