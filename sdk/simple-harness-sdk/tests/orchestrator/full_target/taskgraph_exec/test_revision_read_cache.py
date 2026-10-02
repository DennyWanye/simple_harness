"""2026-09-28 真机：有调用在跑时后台 CPU 持续约 100%——编排循环每轮在多处把同一计划版本
（连同全部祖先）完整校验一遍。校验过的结果只在"这条版本链依据的行一字未变"时复用（2026-10-03 改口径：原来以整库写入代数为键，
循环每轮都写，等于每轮重验）。

**改坏检验**：缓存键去掉版本记录行 → 篡改后仍拿旧结果 → 变红。"""

import asyncio
import sqlite3

from production_fixture import enabled_world
from agent_orchestrator.storage.taskgraph_store import TaskGraphStore


def test_a_verified_revision_is_reused_only_while_nothing_changed(tmp_path):
    async def case():
        async with enabled_world(tmp_path, key='tg-revision-cache', hold_worker=True) as world:
            await world.commit_seed()
            store, mission = world.loop.store, world.mission.id
            reader = TaskGraphStore(store)
            verified = []
            original = reader._read_revision_verified

            def counting(mission_id, revision):
                verified.append(revision)
                return original(mission_id, revision)

            reader._read_revision_verified = counting
            first = reader.read_revision(mission, 1)
            assert reader.read_revision(mission, 1) is first
            assert len(verified) == 1

            # 与这条版本链无关的写入（本连接、另一个连接）→ 复用（2026-10-03：此前任何写入都
            # 重新校验整条链，编排循环每轮都在写，CPU 随版本数增长）
            with store.transaction() as connection:
                connection.execute("CREATE TEMP TABLE IF NOT EXISTS probe(x)")
                connection.execute("INSERT INTO probe VALUES (1)")
            other = sqlite3.connect(store.path)
            other.execute("CREATE TABLE IF NOT EXISTS probe_other(x)")
            other.execute("INSERT INTO probe_other VALUES (1)")
            other.commit()
            other.close()
            assert reader.read_revision(mission, 1) is first
            assert len(verified) == 1

            # 这条链依据的任何一个字节变了（含损坏）→ 重新校验，不拿旧结果
            with store.transaction() as connection:
                connection.execute("DROP TRIGGER taskgraph_revision_records_no_update")
                connection.execute("UPDATE taskgraph_revision_records SET manifest_hash=? "
                                   "WHERE mission_id=? AND revision=1", ("f" * 64, mission))
            try:
                reader.read_revision(mission, 1)
            except Exception:
                pass
            assert len(verified) == 2
    asyncio.run(case())
