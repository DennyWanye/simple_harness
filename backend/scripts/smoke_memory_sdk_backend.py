"""记忆 SDK 接入后端启动 smoke：import main.py → 初始化 SessionDB+MemoryBackend → 走一条消息。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile


async def _run() -> None:
    os.environ.setdefault("DESKPET_USER_DATA_DIR", tempfile.mkdtemp())
    os.environ.setdefault("DESKPET_DEV_MODE", "1")
    sys.path.insert(0, ".")

    import main  # noqa: PLC0415 — 需要在 env 之后 import

    sdb = main._session_db
    assert sdb is not None, "session_db is None"
    assert main._memory_backend is not None, "memory_backend is None"

    await sdb.initialize()
    sid = await sdb.ensure_session("smoke-backend-1")
    await sdb.append_message(sid, "user", "我养了一只叫Max的狗，很喜欢吃披萨")
    msgs = await sdb.get_recent_messages(sid, limit=10)
    assert msgs and msgs[0]["content"] == "我养了一只叫Max的狗，很喜欢吃披萨"
    assert any(f.key == "pet_name" and f.value == "Max" for f in await sdb.get_facts())
    assert len(await sdb.recall("Max")) >= 1
    assert "Max" in (await sdb.get_digital_twin()).relationships.entities
    await sdb.close()
    print("BACKEND_SMOKE_OK")


if __name__ == "__main__":
    asyncio.run(_run())
