# SPDX-License-Identifier: Apache-2.0
"""Process-exit fault driver; only run with a fresh ignored evidence directory.

The seed Mission runs on the product deployment (``production_fixture.enabled_world``);
the process exits at one point and leaves ``recovery-source.json`` naming what the
parent test needs to recover:

* ``inside_commit``: inside the plan commit transaction (before its TaskGraph record);
* ``after_commit`` / ``after_reserve``: at the first write after the plan commit — the
  Worker Attempt's own transaction, after its budget reservation, before its intent row;
* ``after_executor``: after the Worker's physical turn was committed.
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys

from production_fixture import enabled_world


def _reservations(connection):
    return [r[0] for r in connection.execute('SELECT reservation_id FROM budget_reservations ORDER BY reservation_id')]


def _accounts(connection):
    return [r[0] for r in connection.execute('SELECT account_id FROM budget_accounts ORDER BY account_id')]


def _real_provider():
    """联测 S4：真实模型下的同一条路径（``crash_seed.py <dir> <mode> real``）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'agents'))
    from real_provider_config import build_real_provider, resolve_real_provider

    config = resolve_real_provider()
    assert config is not None, 'no real provider configured (SH_BASEURL / SH_APIKEY / SH_MODEL)'
    return build_real_provider(config)


async def main(base: Path, mode: str, real: bool = False):
    base.mkdir(parents=True, exist_ok=True)
    async with enabled_world(base, key='tg-process-' + mode, provider=_real_provider() if real else None) as world:
        store = world.store

        def exit_with_original_reply(code):
            # The Planner reply being (or just) collected: the last planning decision this
            # connection sees (inside the commit transaction it is not on disk yet).
            row = store.connection.execute(
                'SELECT d.request_id, d.raw_artifact_ref, d.raw_output_hash, r.intent_id FROM planning_decisions d '
                'JOIN planning_requests r ON r.request_id=d.request_id ORDER BY d.created_at DESC LIMIT 1').fetchone()
            assert row is not None
            worker = store.connection.execute(
                "SELECT intent_id,subject_id FROM dispatch_intents WHERE kind='attempt' ORDER BY created_at LIMIT 1").fetchone()
            committed = sqlite3.connect(store.path.resolve().as_uri() + '?mode=ro', uri=True)
            try:
                before_worker = {'account_ids': _accounts(committed), 'reservation_ids': _reservations(committed),
                                 'reservation_ids_in_transaction': _reservations(store.connection)}
            finally:
                committed.close()
            (base / 'recovery-source.json').write_text(json.dumps({
                'mission_id': world.mission.id, 'intent_id': str(row[3]),
                'raw_artifact_ref': row[1], 'raw_output_hash': row[2], 'fault_mode': mode,
                'before_worker': before_worker,
                'worker_intent_id': None if worker is None else worker[0],
                'worker_attempt_id': None if worker is None else worker[1],
            }) + '\n')
            os._exit(code)

        if mode == 'inside_commit':
            store.connection.create_function('tg_exit', 0, lambda: exit_with_original_reply(81))
            store.connection.execute(
                'CREATE TEMP TRIGGER tg_exit_before_record BEFORE INSERT ON taskgraph_revision_records '
                'BEGIN SELECT tg_exit(); END')
        elif mode in {'after_commit', 'after_reserve'}:
            code = 82 if mode == 'after_commit' else 83
            store.connection.create_function('tg_exit', 0, lambda: exit_with_original_reply(code))
            store.connection.execute(
                "CREATE TEMP TRIGGER tg_exit_before_attempt_intent BEFORE INSERT ON dispatch_intents "
                "WHEN NEW.kind='attempt' BEGIN SELECT tg_exit(); END")
        elif mode == 'after_executor':
            original_fault = store.fault

            def fault(point, kind=None):
                if point == 'after_turn_committed' and kind == 'attempt':
                    exit_with_original_reply(84)
                return original_fault(point, kind)
            store.fault = fault
        else:
            raise AssertionError('unexpected fault mode')
        async with asyncio.timeout(900 if real else 30):
            while True:
                await world.step()
        raise AssertionError('process exit point was not reached')


if __name__ == '__main__':
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2], real=sys.argv[3:] == ['real']))

