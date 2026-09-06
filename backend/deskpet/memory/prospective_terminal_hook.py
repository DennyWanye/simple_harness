"""Capture actual public SDK terminal before entering the Host writer transaction."""
from deskpet.execution.terminal_identity import read_primary_terminal_identity_tx
from deskpet.memory.prospective_occurrence import settle_acknowledged_tx
from deskpet.memory.s5c_store import S5cConflict


def prepare_occurrence_terminal_hook(*, principal, sdk_run_id, actual_sdk_terminal):
    if actual_sdk_terminal is None or actual_sdk_terminal.run_id!=sdk_run_id:
        raise S5cConflict('s5c_occurrence_sdk_terminal_missing')

    async def observe_tx(db, *, host_run_id, sdk_run_id):
        async with db.execute('SELECT subject,primary_conversation_id FROM foreground_runs WHERE host_run_id=?',
                              (host_run_id,)) as query:
            run=await query.fetchone()
        if run is None or run['subject']!=principal.actor_id:
            raise S5cConflict('s5c_occurrence_terminal_owner_differs')
        identity=await read_primary_terminal_identity_tx(db,subject=principal.actor_id,
            primary_ref=run['primary_conversation_id'],host_run_id=host_run_id,sdk_run_id=sdk_run_id)
        await settle_acknowledged_tx(db,principal=principal,sdk_run_id=sdk_run_id,
            terminal_identity=identity,actual_sdk_terminal=actual_sdk_terminal)
    return observe_tx
