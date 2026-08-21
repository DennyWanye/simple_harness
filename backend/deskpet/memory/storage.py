"""Owner-only regular-file admission for DeskPet's state database."""

from __future__ import annotations

import os
from pathlib import Path
import stat


class StateStorageUnsafe(RuntimeError):
    code = "state_storage_unsafe"


def ensure_owner_only_state_db(path: str | Path) -> Path:
    value = Path(path)
    value.parent.mkdir(parents=True, exist_ok=True)
    for component in (value, *value.parents):
        if component.exists() and component.is_symlink():
            raise StateStorageUnsafe("state database path contains a symlink")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(value, flags, 0o600)
    os.close(descriptor)
    info = value.stat()
    if not stat.S_ISREG(info.st_mode):
        raise StateStorageUnsafe("state database is not a regular file")
    if os.name == "posix":
        os.chmod(value, 0o600)
        if stat.S_IMODE(value.stat().st_mode) != 0o600:
            raise StateStorageUnsafe("state database is not owner-only")
    return value


__all__ = ("StateStorageUnsafe", "ensure_owner_only_state_db")
