"""Current occurrence decisions from the public inbox and existing Host SELF authority.

No private Memory SQL and no inference of an exit from a missing/page-filtered key.
"""
from deskpet.memory.prospective_occurrence import CurrentOccurrenceRead
from deskpet.memory.s5c_store import S5cConflict, _hash
from deskpet.memory.trusted_disclosure import resolve_current_disclosure


class PublicOccurrenceCurrentReader:
    def __init__(self, *, store, runtime_getter):
        self.store,self.runtime_getter=store,runtime_getter

    async def applies_to_run(self, sdk_run_id):
        # A7 primary inbox is attached to a real Host foreground root. Child /
        # workflow SDK Runs retain their existing authority path; do not make
        # a missing primary turn into a new blanket provider rejection.
        async with self.store._transaction() as db:
            async with db.execute('SELECT r.subject FROM foreground_runs r '
                'JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id WHERE b.sdk_run_id=?',
                (sdk_run_id,)) as q:
                row=await q.fetchone()
        if row is None:return False
        if row['subject']!=self.store.principal.actor_id:
            raise S5cConflict('s5c_occurrence_run_owner_differs')
        return True

    async def _non_self_head(self, sdk_run_id):
        """The owner's reminders are never presented into a non-self audience turn.

        A declared non-self input turn (`input_use`, nonself-input contract) is
        bound to the current Host disclosure head. That head is a Host fact read
        here directly: when it names a non-self recipient this run presents no
        occurrence, instead of rejecting the whole run. The self lane below is
        byte-for-byte unchanged, and no `:input-v1:` reference is built from
        this reader's long request id (it would exceed the SDK 256-byte bound).
        """
        import sqlite3
        from pathlib import Path
        import aiosqlite
        from deskpet.memory.trusted_disclosure import current_record_tx, binding_token
        if not Path(self.store.path).is_file():
            return None
        try:
            async with aiosqlite.connect(f'file:{self.store.path}?mode=ro',uri=True) as db:
                db.row_factory=aiosqlite.Row
                head=await current_record_tx(db,self.store.principal.actor_id)
        except (sqlite3.Error, aiosqlite.Error):
            # No Host disclosure schema/head in this store: the self lane below
            # resolves the turn's disclosure authoritatively, exactly as before.
            return None
        if head is None or head['recipient']=='user_self':
            return None
        return CurrentOccurrenceRead(self.store.owner,sdk_run_id,_hash(dict(
            domain='host.prospective-current.non-self-audience.v1',binding=binding_token(head),
            run_id=sdk_run_id,presentation='no_occurrence_for_non_self_audience')),(),(),())

    async def __call__(self, *, principal, sdk_run_id, requested_keys=()):
        runtime=self.runtime_getter()
        if runtime is None or principal!=self.store.principal or principal!=runtime.principal():
            raise S5cConflict('s5c_occurrence_current_principal_differs')
        non_self=await self._non_self_head(sdk_run_id)
        if non_self is not None:
            return non_self
        context=await resolve_current_disclosure(db_path=self.store.path,subject=principal.actor_id,
            run_id=sdk_run_id,request_id='host:prospective-current:'+sdk_run_id)
        # Match the currently implemented Host disclosure lane, not a new
        # generic permit. Wider recipients remain rejected by existing policy.
        if (context.subject!=principal.actor_id or context.run_id!=sdk_run_id
                or context.recipient.value!='user_self' or context.recipient_id!=principal.actor_id
                or context.intended_audience.value!='user_self' or context.purpose.value!='task_execution'
                or context.trust.value!='trusted_authority' or context.generation.value!='current'):
            raise S5cConflict('s5c_occurrence_disclosure_unavailable')
        async with self.store._transaction() as db:
            async with db.execute("SELECT DISTINCT occurrence_key FROM prospective_occurrences "
                "WHERE owner_key=? AND phase IN ('claimed','presented') AND occurrence_key NOT IN "
                "(SELECT occurrence_key FROM prospective_occurrences WHERE owner_key=? AND phase IN ('acknowledged','settled'))",
                (self.store.owner,self.store.owner)) as q:
                tracked={row[0] for row in await q.fetchall()}
        manager=await runtime.manager()
        visible=[];exits=[];seen=set();unknown=set(requested_keys)|tracked
        after=None
        for _ in range(16):
            page=await manager.read_occurrence_inbox(principal=principal,after=after,limit=200)
            prior=after
            for entry in page.entries:
                position=(entry.occurred_at,entry.event_id)
                if (prior is not None and position<=prior) or entry.occurrence_key in seen:
                    raise S5cConflict('s5c_occurrence_current_page_invalid')
                prior=position;seen.add(entry.occurrence_key)
                if entry.suppressed or entry.lifecycle_state in {'cancelled','expired','forgotten','completed','superseded'}:
                    exits.append(entry);unknown.discard(entry.occurrence_key)
                elif (entry.outcome=='matched' and entry.lifecycle_state in {'triggered','in_progress','rescheduled'}
                      and entry.effective_privacy_class in {'public','personal'}):
                    visible.append(entry);unknown.discard(entry.occurrence_key)
                elif entry.occurrence_key in tracked or entry.occurrence_key in requested_keys:
                    unknown.add(entry.occurrence_key)
            if page.next_after is None:break
            if not page.entries or page.next_after!=prior:
                raise S5cConflict('s5c_occurrence_current_cursor_invalid')
            after=page.next_after
        else:
            # An unseen key may be mandatory even when no tracked key is missing.
            # A bounded prefix is not an authoritative empty inbox.
            raise S5cConflict("s5c_occurrence_inbox_scan_incomplete")
        # Recheck current Host config after public Memory reads. It is not an
        # atomic Memory/Host lease; physical guard performs a further fresh read.
        checked=await resolve_current_disclosure(db_path=self.store.path,subject=principal.actor_id,
            run_id=sdk_run_id,request_id='host:prospective-current:'+sdk_run_id)
        if checked.to_json()!=context.to_json():
            raise S5cConflict('s5c_occurrence_disclosure_changed')
        return CurrentOccurrenceRead(self.store.owner,sdk_run_id,_hash(context.to_json()),
            tuple(visible),tuple(exits),tuple(sorted(unknown)))
