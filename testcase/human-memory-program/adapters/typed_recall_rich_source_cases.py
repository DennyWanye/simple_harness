"""Public rich-source S1 registration; no private SDK reads or fabricated outcome."""
import copy
import dataclasses as dc
import hashlib
import json
import simple_harness as h


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',', ':'),allow_nan=False).encode()).hexdigest()


async def register(case, envelope, eid):
    admitted = case.admitted[eid]
    admission = admitted.receipt
    scope_id = 'rich-source-task'
    manifest = [{'evidence_id': eid, 'envelope_hash': envelope.envelope_hash, 'item_ordinal': 1}]
    metadata = h.ConversationEvidenceMetadata(metadata_id='rich-metadata',
        authority_issuer_id='host-rich-fixture', evidence_id=eid,
        envelope_hash=envelope.envelope_hash, admission_receipt_id=admission.receipt_id,
        admission_receipt_hash=admission.receipt_hash, run_id=envelope.run_id,
        subject=envelope.subject, source_hash=envelope.source_hash, sanitized_hash=envelope.sanitized_hash,
        conversation_id='rich-fixture-conversation', primary_conversation_id='rich-fixture-conversation',
        causal_group_id='rich-fixture-group', causal_group_sequence=1, item_ordinal=1,
        group_item_count=1, ordered_group_manifest_hash=digest(manifest),
        role=h.ConversationEvidenceRole.USER, occurred_at=case.now, task_scope_id=scope_id,
        tool_causal_link=None, entities=())
    metadata = h.authorize_conversation_public_text(metadata, admitted)
    receipt = h.ConversationEvidenceMetadataReceipt(receipt_id='rich-metadata-receipt',
        metadata_id=metadata.metadata_id, authority_issuer_id=metadata.authority_issuer_id,
        evidence_id=eid, envelope_hash=envelope.envelope_hash,
        admission_receipt_id=admission.receipt_id, admission_receipt_hash=admission.receipt_hash,
        run_id=envelope.run_id, subject=envelope.subject, source_hash=envelope.source_hash,
        sanitized_hash=envelope.sanitized_hash, metadata_hash=metadata.metadata_hash,
        issuer_ref=metadata.authority_issuer_id, accepted=True)
    registration = h.ConversationEvidenceRegistration('rich-registration', envelope,
        admission, metadata, receipt, admitted.item_authority)
    regref = h.ConversationEvidenceRegistrationRef(registration.registration_id,
        registration.registration_hash, eid, envelope.envelope_hash)
    case.conversations[registration.registration_id] = registration
    event = {'call': 'register_procedure_conversation', 'registration': registration.to_json(),
        'registration_hash': registration.registration_hash, 'reference': dc.asdict(regref)}
    case.events.append(event)
    registered = await case.manager.register_conversation_evidence(regref)
    event['result'] = dc.asdict(registered)

    return registered


async def run(row, workspace):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('rich_case_manager',Path(__file__).with_name('typed_recall_case_manager.py'))
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    if row['memory_type'] != 'episode':
        raise ValueError('rich source leaf currently covers original episode only')
    rich = copy.deepcopy(row['source_record'])
    class RichCase(helper.CaseManager):
        async def evidence(self, text, evidence_id, epistemic='explicit_user', verification='source_bound'):
            text = 'User memory assertion: ' + helper.canonical(rich).decode()
            envelope, span = await super().evidence(text,evidence_id,epistemic,verification)
            await register(self,envelope,evidence_id)
            return envelope,span
    case = RichCase(workspace/'rich-episode.sqlite',privacy='sensitive')
    await case.open()
    try:
        seed = {'memory_type':'episode','state':'active',
            'payload':{k:copy.deepcopy(rich[k]) for k in row['allowed_payload_fields']}}
        target = await case.seed(seed,evidence_id='secret-evidence')
        params = dict(query=rich['title'],memory_types=('episode',))
        recall = await case.recall(**params)
        await case.close(); await case.open()
        replay = await case.recall(**params)
        fresh = await case.recall(**params,key='fresh-after-reopen')
        return {'original':copy.deepcopy(row),'sources':case.sources,'calls':case.events,
            'label_mapping':{'original_source_ref':rich['source_ref'],'actual_memory_id':target.memory_id,
                'actual_revision':target.revision,'evidence_id':'secret-evidence','source_task_scope_id':'rich-source-task'},
            'recalls':[recall],'replay':replay,'fresh':fresh,
            'actual_public_payload':recall['execution']['result']['items'][0]['public_payload'],
            'actual_public_payload_hash':recall['execution']['result']['items'][0]['selected_item']['public_payload_hash'],
            'legacy_status':'BLOCKED','legacy_reason':'ORIGINAL_LITERAL_WIRE_DIFFERS'}
    finally:
        await case.close()
