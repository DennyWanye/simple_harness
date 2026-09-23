"""Pure recovery decision over *actual* path inspections; does not delete files."""
from pathlib import PurePosixPath
from .runtime_rules import RuleError

def need(v):
    if not v:raise RuleError('PURGE_STATE_INVALID')

def validate_progress(p):
    for field in ('source_relative_directory','trash_relative_directory'):
        path=p[field];parts=PurePosixPath(path).parts
        need(path and not path.startswith('/') and '\\'not in path and ':'not in path)
        need('..'not in parts and '.'not in path.split('/') and str(PurePosixPath(path))==path)
    need(p['source_relative_directory']!=p['trash_relative_directory'])
    if p['phase']in('RENAMED','DELETE_CONFIRMED'):need(p['rename_receipt_ref']is not None)
    if p['phase']=='DELETE_CONFIRMED':need(p['delete_receipt_ref']is not None)
    else:need(p['delete_receipt_ref']is None)
    if p['phase']in('DRAINING','RENAME_PENDING'):need(p['rename_receipt_ref']is None)
    b=p['blocking']
    if b:
        need(p['inspection_receipt_ref']is not None)
        need(b['retry_mode']==('SAME_DESTROY_BACKOFF'if b['code']=='FILE_BUSY'else'MANUAL'))
        need((b['retry_at_ms']is not None)==(b['retry_mode']=='SAME_DESTROY_BACKOFF'))

def recovery_action(progress, observation):
    validate_progress(progress)
    for key in ('session_id','destroy_command_id','control_generation','expected_marker_hash'):
        if progress[key]!=observation[key]:raise RuleError('PURGE_IDENTITY_MISMATCH')
    source,trash=observation['source_state'],observation['trash_state']
    if 'MISMATCH'in(source,trash):return 'BLOCK_MARKER_MISMATCH'
    if source!='ABSENT'and trash!='ABSENT':return 'BLOCK_DUAL_DIRECTORY'
    phase=progress['phase']
    if phase=='DRAINING':return 'WAIT_DISPOSAL_PROOF'
    if source=='MATCH'and trash=='ABSENT':
        return 'RENAME_EXACT'if phase=='RENAME_PENDING'else'BLOCK_DELETE_STATE_AMBIGUOUS'
    if source=='ABSENT'and trash=='MATCH':
        if phase=='RENAME_PENDING':return 'RECORD_RECOVERED_RENAME'
        return 'DELETE_EXACT'if phase=='RENAMED'else'BLOCK_DELETE_STATE_AMBIGUOUS'
    if source=='ABSENT'and trash=='ABSENT':
        if phase=='RENAMED':return 'RECORD_DELETION_ABSENCE'
        if phase=='DELETE_CONFIRMED':return 'FINALIZE_SAME_DESTROY'
    return 'BLOCK_DELETE_STATE_AMBIGUOUS'

def can_rebuild(session):
    return session['state']=='QUARANTINED'and session.get('destroy_command_id')is None and session.get('purge_progress')is None
