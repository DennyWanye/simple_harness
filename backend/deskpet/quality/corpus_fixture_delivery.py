"""Durable Host S1 delivery proof for explicit local corpus fixture executors.

No real model/Provider claim. Never install as the production analysis authority.
"""
import simple_harness as h
from deskpet.task_scope.protocol import canonical_hash

DOMAIN='host:corpus-fixture-analysis/v1'
CONFIG_HASH=canonical_hash({'executor':DOMAIN,'mode':'deterministic-setup-only','version':1})


class FixtureAnalysisDelivery:
    def _identity(self,request):
        return 'corpus-analysis:'+canonical_hash([request.request_hash,request.attempt])

    def _envelope(self,request,saved,proof):
        body=h.thaw_json(saved.sanitized_payload)
        result=h.MemoryAnalysisResult.from_json(body['result'])
        if (saved.filter_policy_version!=DOMAIN or saved.source_kind!=h.EvidenceSourceKind.RUNTIME_EVENT
                or body['mode']!='deterministic-setup-only' or body['setup_hash']!=self.setup_hash
                or body['request']!=request.to_json() or body['result_hash']!=result.result_hash):
            raise ValueError('corpus_analysis_saved_delivery_differs')
        delivery=h.MemoryAnalysisDeliveryReceipt('delivery:'+saved.evidence_id,DOMAIN,request.run_id,
            request.job_id,request.request_hash,result.result_hash,request.attempt,result.provider_response_id,
            canonical_hash(h.thaw_json(result.structured_result)),proof.admitted_at,proof.receipt_id,proof.receipt_hash)
        envelope=h.MemoryAnalysisResultEnvelope(result,delivery)
        envelope.verify_request(request)
        return envelope

    async def deliver(self, request, result):
        identity=self._identity(request)
        body=dict(mode='deterministic-setup-only',setup_hash=self.setup_hash,
            request=request.to_json(),result=result.to_json(),result_hash=result.result_hash)
        digest=canonical_hash(body)
        saved=h.SanitizedEvidenceEnvelope(evidence_id=identity,run_id=request.run_id,subject=request.subject,
            source_kind=h.EvidenceSourceKind.RUNTIME_EVENT,source_ref=identity,source_hash=digest,
            sanitized_payload=body,sanitized_hash=digest,filter_policy_version=DOMAIN,removed_spans=(),
            disclosure_context=request.disclosure_context,evidence_refs=request.ordered_evidence_refs)
        proof=h.SanitizedEvidenceReceipt(receipt_id='receipt:'+identity,run_id=request.run_id,subject=request.subject,
            evidence_id=identity,envelope_hash=saved.envelope_hash,source_hash=digest,sanitized_hash=digest,
            filter_policy_version=DOMAIN,accepted=True,reason_codes=(h.EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
            disclosure_context=request.disclosure_context,evidence_refs=request.ordered_evidence_refs,admitted_at=float(self.clock()))
        await self.store.append_evidence(saved,proof)
        return self._envelope(request,saved,proof)

    async def verify_analysis_delivery(self,request,envelope):
        actual,receipt=await self.evidence.read_admitted(self._identity(request))
        if self._envelope(request,actual,receipt)!=envelope:
            raise ValueError('corpus_analysis_delivery_not_durable')
