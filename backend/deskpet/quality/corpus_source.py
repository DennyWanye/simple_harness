"""Fixture setup source admission without a foreground queue/history turn.

Uses existing Host producer + public evidence journal, not a synthetic authority
port. The returned S1 is a synthetic corpus source, not an actual user UI event.
"""
from dataclasses import replace

from deskpet.memory.human_memory_service import build_foreground_turn_evidence
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.quality.corpus_c01 import SetupBatch,compile_setup
from datetime import datetime,timezone


async def admit_setup_source(*, path, subject, authority_ref, batch: SetupBatch):
    if type(batch) is not SetupBatch:
        raise TypeError('SetupBatch required, never a complete Case')
    expected=compile_setup(batch.case_id,batch.setup_text,
        scenario_clock=datetime.fromtimestamp(batch.scenario_time,timezone.utc).isoformat())
    if expected!=batch:raise ValueError('corpus_setup_manifest_differs')
    pair=build_foreground_turn_evidence(subject=subject,authority_ref=authority_ref,
        delivery_key='corpus-fixture:'+batch.case_id+':'+batch.setup_hash,
        text=batch.setup_text)
    envelope,receipt=pair
    receipt=replace(receipt,admitted_at=batch.scenario_time)
    # This does not enqueue a Turn or fabricate an SDK Run/terminal/history row.
    # The producer's run_id is its existing per-subject evidence-stream identity.
    await HumanMemoryProgramStore(path).append_evidence(envelope,receipt)
    actual=await HostEvidenceAuthority(path).read_admitted(envelope.evidence_id)
    if actual!=(envelope,receipt):raise ValueError('corpus_source_readback_differs')
    return actual


async def import_setup_conversation_sources(*, source_path, scoring_path, subject, host_run_id):
    """Replicate exact admitted S1 bytes, never source Run/queue/history rows.

    This is corpus fixture preparation, not a production cross-store importer.
    Full conversation authority remains in source_path. Partial interrupted
    imports are replayable, and must not be treated as completed setup.
    """
    from pathlib import Path
    from deskpet.memory.conversation_registration import PrimaryConversationAuthority

    source = Path(source_path).resolve()
    scoring = Path(scoring_path).resolve()
    if source == scoring or (scoring.exists() and source.samefile(scoring)):
        raise ValueError('corpus_source_scoring_store_must_differ')
    group = await PrimaryConversationAuthority(source, subject=subject).registrations_for_run(host_run_id)
    # This fixture importer is deliberately limited to a fresh ordinary setup
    # exchange. A terminal's envelope refs do not cover its payload dependencies.
    from deskpet.memory.primary_visibility import _dependencies
    terminal = group.terminal_source[0]
    terminal_body = terminal.to_json()['sanitized_payload']
    registrations = group.registrations
    if ([r.envelope.source_kind.value for r in registrations]
            != ['user_message', 'assistant_message']
            or terminal_body.get('message_source_contract') != 'primary-message-v1'
            or terminal_body.get('prospective_source_dependencies') is not None):
        raise ValueError('corpus_source_requires_fresh_ordinary_group')
    dependency_evidence, derived = _dependencies(terminal_body.get('visibility_dependencies'))
    user = registrations[0].envelope
    if derived or any(key != user.evidence_id or digest != user.envelope_hash
                      for key, digest in dependency_evidence.items()):
        raise ValueError('corpus_source_has_external_dependencies')
    pairs = [group.terminal_source]
    pairs.extend((r.envelope, r.admission_receipt) for r in group.registrations)
    identities = {}
    for envelope, receipt in pairs:
        receipt.verify(envelope)
        if envelope.subject != subject:
            raise ValueError('corpus_source_subject_differs')
        old = identities.setdefault(envelope.evidence_id, (envelope, receipt))
        if old != (envelope, receipt):
            raise ValueError('corpus_source_identity_collision')
    for envelope, _ in identities.values():
        for ref in envelope.evidence_refs:
            parent = identities.get(ref.evidence_id)
            if parent is None or parent[0].envelope_hash != ref.content_hash:
                raise ValueError('corpus_source_group_not_closed')
    store = HumanMemoryProgramStore(scoring)
    authority = HostEvidenceAuthority(scoring)
    for envelope, receipt in identities.values():
        await store.append_evidence(envelope, receipt)
        if await authority.read_admitted(envelope.evidence_id) != (envelope, receipt):
            raise ValueError('corpus_source_replica_differs')
    return group
