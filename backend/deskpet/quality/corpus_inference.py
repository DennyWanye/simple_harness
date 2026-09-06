"""Read a corpus inference from a completed actual Host/SDK assistant source.

The setup producer may use a deterministic Provider. This is source preparation,
not a real-model quality run. Never relabel a USER span as model output.
"""
import hashlib
import simple_harness as h
from deskpet.memory.conversation_registration import PrimaryConversationAuthority

async def inference_source(*, path, subject, batch, host_run_id, quote):
    if batch.case_id!='C02-19' or quote!='偏好云端':
        raise ValueError('corpus_inference_setup_not_registered')
    authority=PrimaryConversationAuthority(path,subject=subject)
    group=await authority.registrations_for_run(host_run_id)
    if group.registrations[0].envelope.sanitized_payload.get('text')!=batch.setup_text:
        raise ValueError('corpus_inference_original_input_differs')
    def source_text(registration):
        item=registration.recall_item_authority
        if item is None or item.item_json_pointer!='/source/message/content':
            return None
        return registration.envelope.sanitized_payload.get('source',{}).get('message',{}).get('content')
    choices=[r for r in group.registrations if r.envelope.source_kind is h.EvidenceSourceKind.ASSISTANT_MESSAGE
             and isinstance(source_text(r),str) and source_text(r).count(quote)==1]
    if len(choices)!=1:raise ValueError('corpus_inference_assistant_source_ambiguous')
    registration=choices[0]
    envelope,receipt=registration.envelope,registration.admission_receipt
    item=registration.recall_item_authority
    text=source_text(registration)
    start=len(text[:text.index(quote)].encode())
    span=h.EvidenceSpanRef(span_id='setup-B-inference',evidence_id=envelope.evidence_id,
        envelope_hash=envelope.envelope_hash,sanitized_hash=envelope.sanitized_hash,
        admission_receipt_id=receipt.receipt_id,admission_receipt_hash=receipt.receipt_hash,
        # Full cognitive ingestion admits one sanitized-payload item per envelope.
        # The recall registration ordinal is the separate conversation-group order.
        source_kind=envelope.source_kind,item_ordinal=1,item_id=item.item_id,item_json_pointer=item.item_json_pointer,
        start_byte=start,end_byte=start+len(quote.encode()),exact_quote=quote,
        quote_hash=hashlib.sha256(quote.encode()).hexdigest(),source_hash=envelope.source_hash,
        normalization_version=item.normalization_version,
        actor_role=item.actor_role,provenance=item.provenance,
        support_kind=h.EvidenceSupportKind.MODEL_INFERENCE,typed_observation=None)
    return envelope,receipt,span,group.terminal_source
