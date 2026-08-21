import os
from pathlib import Path
import stat

import pytest

from deskpet.memory.session_db import SessionDB
from deskpet.memory.storage import StateStorageUnsafe


@pytest.mark.asyncio
async def test_state_database_is_regular_owner_only(tmp_path):
    path = tmp_path / "state.db"
    session = SessionDB(path)
    await session.initialize()
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    await session.close()


@pytest.mark.asyncio
async def test_state_database_symlink_is_rejected(tmp_path):
    target = tmp_path / "target.db"
    target.write_bytes(b"")
    link = tmp_path / "state.db"
    link.symlink_to(target)
    with pytest.raises(StateStorageUnsafe):
        await SessionDB(link).initialize()
