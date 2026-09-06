"""A7 occurrence facts in the existing Host journal, never SDK mutation authority.

The caller owns the snapshot/terminal transaction. This module never commits
those connections and never projects an occurrence as exited on presentation.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import re

from simple_harness_memory.core.occurrence import OccurrenceInboxEntryV1
from deskpet.memory.s5c_store import S5cConflict, _hash, _owner
from simple_harness.contracts import canonical_json

DOMAIN = 'host:prospective-occurrence/v1'
_KEY = re.compile(r'[0-9a-f]{64}\Z')


def _load_json(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise S5cConflict('s5c_occurrence_json_duplicate_key')
            result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=unique)


def _required(value, label):
    if type(value) is not str or not value.strip() or '\x00' in value:
        raise S5cConflict('s5c_occurrence_'+label+'_invalid')
    return value


def _key(value):
    if type(value) is not str or not _KEY.fullmatch(value):
        raise S5cConflict('s5c_occurrence_key_invalid')
    return value


def _live(entry):
    if type(entry) is not OccurrenceInboxEntryV1:
        raise TypeError('OccurrenceInboxEntryV1 required')
    if entry.suppressed or entry.outcome != 'matched' or entry.lifecycle_state not in {
        'triggered', 'in_progress', 'rescheduled',
    }:
        raise S5cConflict('s5c_occurrence_not_live')


def _identity(entry):
    # Current lifecycle and effective privacy can change between honest reads.
    # Immutable origin remains exactly bound; visibility is checked separately.
    return {key: entry.to_json()[key] for key in ('occurrence_key','memory_id',
        'prospective_revision','event_id','event_hash','event_ref','trigger_fingerprint','occurred_at')}


@dataclass(frozen=True)
class CurrentOccurrenceRead:
    owner: str
    sdk_run_id: str
    disclosure_identity_hash: str
    visible: tuple[OccurrenceInboxEntryV1, ...]
    exits: tuple[OccurrenceInboxEntryV1, ...] = ()
    unverifiable_keys: tuple[str, ...] = ()

    def validate(self, *, owner, sdk_run_id):
        if self.owner!=owner or self.sdk_run_id!=sdk_run_id:
            raise S5cConflict('s5c_occurrence_current_owner_run_differs')
        _key(self.disclosure_identity_hash)
        if any(type(values) is not tuple for values in (self.visible,self.exits,self.unverifiable_keys)):
            raise TypeError('immutable current occurrence groups required')
        keys=[]
        for entry in self.visible:
            _live(entry);keys.append(_key(entry.occurrence_key))
        for entry in self.exits:
            if type(entry) is not OccurrenceInboxEntryV1:
                raise TypeError('public exit entry required')
            if not entry.suppressed and entry.lifecycle_state not in {'expired','cancelled','completed','superseded','forgotten'}:
                raise S5cConflict('s5c_occurrence_exit_not_proven')
            keys.append(_key(entry.occurrence_key))
        keys.extend(_key(key) for key in self.unverifiable_keys)
        if len(keys)!=len(set(keys)):
            raise S5cConflict('s5c_occurrence_current_duplicate')


@dataclass(frozen=True)
class PresentationItem:
    entry_json: str
    head_hash: str
    count: int
    overdue: bool

    @property
    def entry(self):
        return OccurrenceInboxEntryV1(**json.loads(self.entry_json))


@dataclass(frozen=True)
class PreparedPresentation:
    owner: str
    sdk_run_id: str
    items: tuple[PresentationItem, ...]
    disclosure_identity_hash: str


def presentation_payload(prepared):
    return dict(schema_version=1,owner=prepared.owner,sdk_run_id=prepared.sdk_run_id,
        disclosure_identity_hash=prepared.disclosure_identity_hash,items=[dict(index=index,
            entry=json.loads(item.entry_json),head_hash=item.head_hash,count=item.count,overdue=item.overdue)
            for index,item in enumerate(prepared.items)])


def presentation_from_payload(payload):
    if type(payload) is not dict or set(payload)!={'schema_version','owner','sdk_run_id','disclosure_identity_hash','items'} or type(payload['schema_version']) is not int or payload['schema_version']!=1:
        raise S5cConflict('s5c_occurrence_group_shape_invalid')
    if type(payload['items']) is not list or len(payload['items'])>8:
        raise S5cConflict('s5c_occurrence_group_bound')
    _key(payload['owner']);_key(payload['disclosure_identity_hash']);_required(payload['sdk_run_id'],'run')
    items=[];keys=set()
    for index,item in enumerate(payload['items']):
        if type(item) is not dict or set(item)!={'index','entry','head_hash','count','overdue'}:
            raise S5cConflict('s5c_occurrence_group_item_shape_invalid')
        if type(item['index']) is not int or item['index']!=index or type(item['count']) is not int or item['count']<1 or type(item['overdue']) is not bool or item['overdue']!=(item['count']>=3):
            raise S5cConflict('s5c_occurrence_group_index_invalid')
        entry=OccurrenceInboxEntryV1(**item['entry']);_live(entry);_key(item['head_hash'])
        if entry.occurrence_key in keys or entry.to_json()!=item['entry']:
            raise S5cConflict('s5c_occurrence_group_entry_invalid')
        keys.add(entry.occurrence_key)
        items.append(PresentationItem(canonical_json(item['entry']),item['head_hash'],item['count'],item['overdue']))
    return PreparedPresentation(payload['owner'],payload['sdk_run_id'],tuple(items),payload['disclosure_identity_hash'])


def snapshot_receipt_body(row, source_revisions, group=None):
    body={key:row[key] for key in ('expected_request_fingerprint','payload_hash','prior_context_revision',
        'provider_turn_ordinal','sdk_run_id','snapshot_id','snapshot_revision')}
    body['source_revisions']=source_revisions
    if group is not None:
        body['host_snapshot_schema_version']=2
        body['host_occurrence_group']=group
    return body


def decode_snapshot(row):
    stored=_load_json(row['source_revisions_json'])
    if type(stored) is not dict:raise S5cConflict('s5c_snapshot_storage_invalid')
    if 'host_snapshot_schema_version' in stored:
        if set(stored)!={'host_snapshot_schema_version','source_revisions','host_occurrence_group'} or type(stored['host_snapshot_schema_version']) is not int or stored['host_snapshot_schema_version']!=2:
            raise S5cConflict('s5c_snapshot_storage_shape_invalid')
        revisions=stored['source_revisions'];group=stored['host_occurrence_group']
        prepared=presentation_from_payload(group)
        if prepared.sdk_run_id!=row['sdk_run_id']:
            raise S5cConflict('s5c_snapshot_group_run_differs')
    else:
        revisions=stored;group=None;prepared=None
    if type(revisions) is not dict or any(type(k) is not str or type(v) is not int or v<0 for k,v in revisions.items()):
        raise S5cConflict('s5c_snapshot_revisions_invalid')
    if _hash(snapshot_receipt_body(row,revisions,group))!=row['receipt_hash']:
        raise S5cConflict('s5c_snapshot_receipt_corrupt')
    return revisions,prepared


async def _rows(db, owner, key):
    async with db.execute('SELECT * FROM prospective_occurrences WHERE owner_key=? AND occurrence_key=? '
                          'ORDER BY record_id', (owner,key)) as query:
        rows=await query.fetchall()
    parsed=[]
    for row in rows:
        body=_load_json(row['inbox_json'])
        if body.get('domain') != DOMAIN:
            # Frozen claim format; it never proves presentation or ACK.
            if (row['phase']!='claimed' or row['sdk_run_id'] is not None or row['snapshot_id'] is not None
                    or row['reason'] is not None or _hash([owner,body])!=row['record_hash']
                    or row['record_id']!=_hash(['s5c:claim',owner,key])):
                raise S5cConflict('s5c_occurrence_legacy_corrupt')
            entry=OccurrenceInboxEntryV1(**body)
        else:
            expected_id=_hash([DOMAIN,owner,key,row['phase'],row['sdk_run_id']
                if row['phase'] in {'presented','acknowledged'} else None])
            if (set(body)!={'domain','owner','occurrence_key','phase','sdk_run_id','snapshot_id',
                    'reason','entry','proof'} or body['owner']!=owner or body['occurrence_key']!=key
                    or any(body[name]!=row[name] for name in ('phase','sdk_run_id','snapshot_id','reason'))
                    or _hash(body)!=row['record_hash'] or row['record_id']!=expected_id):
                raise S5cConflict('s5c_occurrence_record_corrupt')
            entry=OccurrenceInboxEntryV1(**body['entry'])
        if entry.occurrence_key!=key:
            raise S5cConflict('s5c_occurrence_origin_differs')
        parsed.append((dict(row),body,entry))
    records={row['record_id']:(row,body) for row,body,_ in parsed}
    for row,body,_ in parsed:
        if row['phase']=='presented':
            async with db.execute('SELECT * FROM run_context_snapshot_receipts WHERE snapshot_id=?',
                                  (row['snapshot_id'],)) as query:
                snapshot=await query.fetchone()
            if snapshot is None or (snapshot['sdk_run_id'],snapshot['receipt_hash'])!=(row['sdk_run_id'],body['proof']['snapshot_receipt_hash']):
                raise S5cConflict('s5c_occurrence_snapshot_differs')
            decode_snapshot(snapshot)
        if row['phase']=='acknowledged':
            origin=records.get(body['proof']['presented_id'])
            if (origin is None or origin[0]['phase']!='presented'
                    or origin[0]['record_hash']!=body['proof']['presented_hash']
                    or origin[0]['sdk_run_id']!=row['sdk_run_id']
                    or origin[0]['snapshot_id']!=row['snapshot_id']):
                raise S5cConflict('s5c_occurrence_ack_source_differs')
    counts=sorted(body['proof']['presented_count'] for row,body,_ in parsed if row['phase']=='presented')
    if counts!=list(range(1,len(counts)+1)):
        raise S5cConflict('s5c_occurrence_presented_count_corrupt')
    if any(row['phase']=='overdue' for row,_,_ in parsed) and len(counts)<3:
        raise S5cConflict('s5c_occurrence_overdue_corrupt')
    if parsed and any(_identity(e)!=_identity(parsed[0][2]) for _,_,e in parsed):
        raise S5cConflict('s5c_occurrence_origin_differs')
    return parsed


def _head(rows):
    return _hash([[row['record_id'],row['record_hash']] for row,_,_ in rows])


async def _append(db, *, owner, entry, phase, run=None, snapshot=None, reason=None, proof):
    key=entry.occurrence_key
    identity=_hash([DOMAIN,owner,key,phase,run if phase in {'presented','acknowledged'} else None])
    body=dict(domain=DOMAIN,owner=owner,occurrence_key=key,phase=phase,sdk_run_id=run,
        snapshot_id=snapshot,reason=reason,entry=entry.to_json(),proof=proof)
    digest=_hash(body)
    await db.execute('INSERT INTO prospective_occurrences VALUES (?,?,?,?,?,?,?,?,?)',
        (identity,owner,key,phase,run,snapshot,reason,canonical_json(body),digest))
    return dict(receipt_id=identity,receipt_hash=digest,occurrence_key=key,state=phase)


async def prepare_presentation(db, *, principal, sdk_run_id, entries, disclosure_identity_hash):
    """Read-only; entries must come from the trusted Run-bound disclosure reader."""
    run=_required(sdk_run_id,'run')
    owner=_owner(principal)
    _key(disclosure_identity_hash)
    if type(entries) is not tuple or len(entries)>8:
        raise S5cConflict('s5c_occurrence_presentation_bound')
    seen=set(); items=[]
    for entry in entries:
        _live(entry);key=_key(entry.occurrence_key)
        if key in seen: raise S5cConflict('s5c_occurrence_duplicate_key')
        seen.add(key)
        rows=await _rows(db,owner,key)
        if rows and _identity(entry)!=_identity(rows[0][2]):
            raise S5cConflict('s5c_occurrence_origin_differs')
        if any(row['phase'] in {'acknowledged','settled'} for row,_,_ in rows):
            continue
        presented=[(row,body) for row,body,_ in rows if row['phase']=='presented']
        own=next((body for row,body in presented if row['sdk_run_id']==run),None)
        if own is not None and canonical_json(own['entry'])!=canonical_json(entry.to_json()):
            raise S5cConflict('s5c_occurrence_presented_content_changed')
        count=own['proof']['presented_count'] if own is not None else len(presented)+1
        items.append(PresentationItem(canonical_json(entry.to_json()),_head(rows),count,count>=3))
    return PreparedPresentation(owner,run,tuple(items),disclosure_identity_hash)


async def record_presentations_tx(db, *, prepared, snapshot_id, snapshot_receipt_hash):
    if type(prepared) is not PreparedPresentation:
        raise TypeError('PreparedPresentation required')
    async with db.execute('SELECT sdk_run_id,receipt_hash FROM run_context_snapshot_receipts '
                          'WHERE snapshot_id=?',(snapshot_id,)) as query:
        snapshot=await query.fetchone()
    if snapshot is None or tuple(snapshot)!=(prepared.sdk_run_id,snapshot_receipt_hash):
        raise S5cConflict('s5c_occurrence_snapshot_differs')
    for item in prepared.items:
        entry=item.entry;rows=await _rows(db,prepared.owner,entry.occurrence_key)
        existing=next((row for row,_,_ in rows if row['phase']=='presented'
                       and row['sdk_run_id']==prepared.sdk_run_id),None)
        if rows and _identity(entry)!=_identity(rows[0][2]):
            raise S5cConflict('s5c_occurrence_origin_differs')
        if existing:
            if any(row['phase'] in {'acknowledged','settled'} for row,_,_ in rows):
                raise S5cConflict('s5c_occurrence_exited')
            continue
        count=1+sum(row['phase']=='presented' for row,_,_ in rows)
        if item.count!=count or item.overdue!=(count>=3):
            raise S5cConflict('s5c_occurrence_count_differs')
        if _head(rows)!=item.head_hash:
            raise S5cConflict('s5c_occurrence_head_changed')
        if not rows:
            await _append(db,owner=prepared.owner,entry=entry,phase='claimed',proof={})
        await _append(db,owner=prepared.owner,entry=entry,phase='presented',run=prepared.sdk_run_id,
            snapshot=snapshot_id,proof=dict(snapshot_receipt_hash=snapshot_receipt_hash,
                prior_head_hash=item.head_hash,presented_count=item.count,
                disclosure_identity_hash=prepared.disclosure_identity_hash))
        if item.overdue and not any(row['phase']=='overdue' for row,_,_ in rows):
            await _append(db,owner=prepared.owner,entry=entry,phase='overdue',run=prepared.sdk_run_id,
                snapshot=snapshot_id,proof=dict(presented_count=item.count))


async def _presentation(db, rows, run):
    selected=next(((row,body,entry) for row,body,entry in rows
                   if row['phase']=='presented' and row['sdk_run_id']==run),None)
    if selected is None: raise S5cConflict('s5c_occurrence_not_presented_in_run')
    row,body,_=selected
    async with db.execute('SELECT * FROM run_context_snapshot_receipts WHERE snapshot_id=?',
                          (row['snapshot_id'],)) as query:
        snapshot=await query.fetchone()
    if snapshot is None or (snapshot['sdk_run_id'],snapshot['receipt_hash'])!=(run,body['proof']['snapshot_receipt_hash']):
        raise S5cConflict('s5c_occurrence_snapshot_differs')
    decode_snapshot(snapshot)
    return selected


async def _exit_projection(db, *, entry, run, now, reason):
    # This legacy table means mandatory exit, not display. Never call at inject.
    async with db.execute('SELECT * FROM occurrence_presented WHERE occurrence_key=?',(entry.occurrence_key,)) as q:
        old=await q.fetchone()
    expected=(entry.occurrence_key,entry.memory_id,entry.prospective_revision,float(now),run,float(now),reason)
    if old is None:
        await db.execute('INSERT INTO occurrence_presented VALUES (?,?,?,?,?,?,?)',expected)
    elif (old['memory_id'],old['prospective_revision'],old['settled_reason'])!=(
            entry.memory_id,entry.prospective_revision,reason):
        raise S5cConflict('s5c_occurrence_exit_differs')


async def ack_presented_tx(db, *, principal, sdk_run_id, occurrence_key, current_entry, now):
    owner=_owner(principal);key=_key(occurrence_key);run=_required(sdk_run_id,'run')
    rows=await _rows(db,owner,key)
    presented,_,original=await _presentation(db,rows,run)
    existing=next((row for row,_,_ in rows if row['phase']=='acknowledged' and row['sdk_run_id']==run),None)
    if existing:
        async with db.execute('SELECT memory_id,prospective_revision,presented_run_id,settled_reason FROM occurrence_presented WHERE occurrence_key=?',(key,)) as q:
            exited=await q.fetchone()
        if exited is None or tuple(exited)!=(original.memory_id,original.prospective_revision,run,'acknowledged'):
            raise S5cConflict('s5c_occurrence_ack_projection_corrupt')
        return dict(receipt_id=existing['record_id'],receipt_hash=existing['record_hash'],occurrence_key=key,state='acknowledged')
    _live(current_entry)
    if canonical_json(current_entry.to_json())!=canonical_json(original.to_json()):
        raise S5cConflict('s5c_occurrence_origin_differs')
    if any(row['phase'] in {'settled','acknowledged'} for row,_,_ in rows):
        raise S5cConflict('s5c_occurrence_exited')
    receipt=await _append(db,owner=owner,entry=current_entry,phase='acknowledged',run=run,
        snapshot=presented['snapshot_id'],proof=dict(presented_id=presented['record_id'],
            presented_hash=presented['record_hash'],acknowledged_at=float(now)))
    await _exit_projection(db,entry=current_entry,run=run,now=now,reason='acknowledged')
    return receipt


async def settle_acknowledged_tx(db, *, principal, sdk_run_id, terminal_identity, actual_sdk_terminal):
    """Caller supplies verified raw SDK identity inside its actual terminal TX."""
    owner=_owner(principal);run=_required(sdk_run_id,'run')
    from deskpet.execution.terminal_identity import PrimaryTerminalIdentity
    if type(terminal_identity) is not PrimaryTerminalIdentity or terminal_identity.sdk_run_id!=run:
        raise S5cConflict('s5c_occurrence_terminal_differs')
    terminal_identity.verify_sdk_terminal(actual_sdk_terminal)
    terminal_identity=asdict(terminal_identity)
    async with db.execute("SELECT occurrence_key FROM prospective_occurrences WHERE owner_key=? "
                          "AND sdk_run_id=? AND phase='acknowledged'",(owner,run)) as q:
        keys=[row[0] for row in await q.fetchall()]
    for key in keys:
        rows=await _rows(db,owner,key)
        ack,body,entry=next((r,b,e) for r,b,e in rows if r['phase']=='acknowledged' and r['sdk_run_id']==run)
        await _presentation(db,rows,run)
        settled=next((b for r,b,_ in rows if r['phase']=='settled'),None)
        proof=dict(ack_id=ack['record_id'],ack_hash=ack['record_hash'],terminal_identity=terminal_identity)
        if settled is not None:
            if settled['reason']!='acknowledged' or settled['proof']!=proof:
                raise S5cConflict('s5c_occurrence_terminal_replay_differs')
            continue
        await _append(db,owner=owner,entry=entry,phase='settled',run=run,
            snapshot=ack['snapshot_id'],reason='acknowledged',proof=proof)


class ProspectiveOccurrenceCoordinator:
    """Trusted composition owns the run reader; no model-supplied identity/body.

    read_current(*,principal,sdk_run_id) must authenticate the actual Run and
    perform current disclosure checks, returning only public visible entries.
    Missing capability is an error, never implicit permission to read content.
    """
    def __init__(self, *, store, read_current, clock):
        if not callable(read_current) or not callable(clock):
            raise TypeError('current occurrence reader and clock required')
        self.store,self.read_current,self.clock=store,read_current,clock

    async def applies_to_run(self, sdk_run_id):
        resolver=getattr(self.read_current,'applies_to_run',None)
        return True if resolver is None else await resolver(sdk_run_id)

    async def _current(self, sdk_run_id, requested_keys=()):
        current=await self.read_current(principal=self.store.principal,sdk_run_id=sdk_run_id,
                                       requested_keys=requested_keys)
        if type(current) is not CurrentOccurrenceRead:
            raise TypeError('CurrentOccurrenceRead required')
        current.validate(owner=self.store.owner,sdk_run_id=sdk_run_id)
        return current

    async def restore_snapshot(self, *, sdk_run_id, provider_turn_ordinal, prior_context_revision, snapshot_id=None):
        async with self.store._transaction() as db:
            async with db.execute('SELECT * FROM run_context_snapshot_receipts '
                'WHERE sdk_run_id=? AND provider_turn_ordinal=? AND prior_context_revision=? '
                'AND (? IS NULL OR snapshot_id=?) ORDER BY snapshot_revision DESC LIMIT 1',
                (sdk_run_id,provider_turn_ordinal,prior_context_revision,snapshot_id,snapshot_id)) as query:
                row=await query.fetchone()
            if row is None:return None
            _,prepared=decode_snapshot(row)
            if prepared is None:raise S5cConflict('s5c_occurrence_snapshot_proof_missing')
            if prepared.owner!=self.store.owner:raise S5cConflict('s5c_occurrence_owner_differs')
            for item in prepared.items:
                rows=await _rows(db,self.store.owner,item.entry.occurrence_key)
                _,body,entry=await _presentation(db,rows,sdk_run_id)
                if canonical_json(entry.to_json())!=item.entry_json or item.count!=body['proof']['presented_count']:
                    raise S5cConflict('s5c_occurrence_snapshot_entry_corrupt')
            return prepared

    async def prepare(self, sdk_run_id):
        current=await self._current(sdk_run_id)
        if current.unverifiable_keys:
            raise S5cConflict('s5c_occurrence_current_unverifiable')
        async with self.store._transaction() as db:
            for entry in current.exits:
                await settle_exited_tx(db,principal=self.store.principal,sdk_run_id=sdk_run_id,
                    current_entry=entry,now=self.clock())
            items=[]
            # Filter processed entries before choosing the bounded actual group.
            for offset in range(0,len(current.visible),8):
                chunk=await prepare_presentation(db,principal=self.store.principal,sdk_run_id=sdk_run_id,
                    entries=current.visible[offset:offset+8],disclosure_identity_hash=current.disclosure_identity_hash)
                items.extend(chunk.items)
                if len(items)>=8: break
            return PreparedPresentation(self.store.owner,sdk_run_id,tuple(items[:8]),current.disclosure_identity_hash)

    async def recheck(self, prepared):
        if type(prepared) is not PreparedPresentation or prepared.owner!=self.store.owner:
            raise S5cConflict('s5c_occurrence_owner_differs')
        keys=tuple(item.entry.occurrence_key for item in prepared.items)
        current=await self._current(prepared.sdk_run_id,keys)
        if current.disclosure_identity_hash!=prepared.disclosure_identity_hash:
            raise S5cConflict('s5c_occurrence_disclosure_changed')
        by_key={entry.occurrence_key:entry for entry in current.visible}
        for item in prepared.items:
            entry=by_key.get(item.entry.occurrence_key)
            if entry is None or canonical_json(entry.to_json())!=item.entry_json:
                raise S5cConflict('s5c_occurrence_current_read_changed')
        return current

    async def record_tx(self, db, *, prepared, snapshot_id, snapshot_receipt_hash):
        # External current read belongs before the writer TX; only captured
        # Host facts/head checks run here. Physical send rechecks independently.
        if type(prepared) is not PreparedPresentation or prepared.owner!=self.store.owner:
            raise S5cConflict('s5c_occurrence_owner_differs')
        await record_presentations_tx(db,prepared=prepared,snapshot_id=snapshot_id,
            snapshot_receipt_hash=snapshot_receipt_hash)

    async def ack(self, *, sdk_run_id, occurrence_key):
        current=await self._current(sdk_run_id,(_key(occurrence_key),))
        entry=next((entry for entry in current.visible if entry.occurrence_key==occurrence_key),None)
        async with self.store._transaction() as db:
            rows=await _rows(db,self.store.owner,occurrence_key)
            _,presented,_=await _presentation(db,rows,sdk_run_id)
            replay=any(r['phase']=='acknowledged' and r['sdk_run_id']==sdk_run_id for r,_,_ in rows)
            if not replay and presented['proof']['disclosure_identity_hash']!=current.disclosure_identity_hash:
                raise S5cConflict('s5c_occurrence_disclosure_changed')
            result=await ack_presented_tx(db,principal=self.store.principal,sdk_run_id=sdk_run_id,
                occurrence_key=occurrence_key,current_entry=entry,now=self.clock())
            if self.store.fault: self.store.fault('s5c.ack.before_commit')
        if self.store.fault: self.store.fault('s5c.ack.after_commit')
        return result


async def settle_exited_tx(db, *, principal, sdk_run_id, current_entry, now):
    """Explicit public current state only; absence from a filtered page is no proof."""
    if type(current_entry) is not OccurrenceInboxEntryV1:
        raise TypeError('actual public current occurrence required')
    if current_entry.suppressed:
        reason='suppressed'
    elif current_entry.lifecycle_state=='expired':
        reason='expired'
    elif current_entry.lifecycle_state in {'cancelled','completed','superseded','forgotten'}:
        reason='suppressed' if current_entry.lifecycle_state=='forgotten' else 'superseded'
    else:
        raise S5cConflict('s5c_occurrence_exit_not_proven')
    owner=_owner(principal);run=_required(sdk_run_id,'run');key=_key(current_entry.occurrence_key)
    rows=await _rows(db,owner,key)
    if not rows: return None
    if _identity(rows[0][2])!=_identity(current_entry):
        raise S5cConflict('s5c_occurrence_origin_differs')
    prior=next((r for r,_,_ in rows if r['phase']=='settled'),None)
    if prior:
        return dict(receipt_id=prior['record_id'],receipt_hash=prior['record_hash'],occurrence_key=key,state='settled')
    if any(r['phase']=='acknowledged' for r,_,_ in rows):
        return None  # ACK terminal path owns its terminal receipt; no rewriting.
    receipt=await _append(db,owner=owner,entry=current_entry,phase='settled',run=run,reason=reason,
        proof=dict(current_event_hash=current_entry.event_hash,observed_at=float(now)))
    if reason!='suppressed':
        await _exit_projection(db,entry=current_entry,run=run,now=now,reason=reason)
    return receipt
