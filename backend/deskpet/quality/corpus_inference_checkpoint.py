"""Bounded fixture recovery candidates, never an APPLIED authority ledger.

Each candidate is saved before SDK finalize. Every load must revalidate it via
the exact source/delivery checks and the SDK public idempotent finalize API.
The caller owns a single fixture process; this is not a multi-writer queue.
"""
import json
import os
from pathlib import Path
import tempfile

import simple_harness as h
from simple_harness_memory.core.jobs import AnalysisApplication, AnalysisBatchClaim
from deskpet.task_scope.protocol import canonical_hash


DOMAIN = 'host:corpus-inference-recovery/v1'
MAX_BYTES = 1024 * 1024


def _keys(value, names):
    if type(value) is not dict or set(value) != set(names):
        raise ValueError('inference_checkpoint_shape')


def _application_json(application):
    if application.decisions or application.reasoning_refs:
        raise ValueError('inference_checkpoint_only_no_mutation')
    return dict(invocation_id=application.invocation_id, turn_id=application.turn_id,
        receipt=application.receipt.to_json())


def _application(value):
    _keys(value, ('invocation_id', 'turn_id', 'receipt'))
    return AnalysisApplication(value['invocation_id'], value['turn_id'],
        h.MemoryAnalysisReceipt.from_json(value['receipt']), ())


def _encode(proof):
    claim = proof.claim
    if proof.job_ids != claim.job_ids or proof.request != claim.request:
        raise ValueError('inference_checkpoint_proof_differs')
    return dict(source_id=proof.source_id, application=_application_json(proof.application),
        claim=dict(batch_id=claim.batch_id, subject=claim.subject, batch_key=claim.batch_key,
            evidence_watermark=claim.evidence_watermark, job_ids=list(claim.job_ids),
            lease_token=claim.lease_token, lease_expires_at=claim.lease_expires_at,
            request=claim.request.to_json(),
            envelope=None if claim.envelope is None else claim.envelope.to_json(),
            application=None if claim.application is None else _application_json(claim.application),
            analysis_apply_head=claim.analysis_apply_head))


def _decode(value):
    from deskpet.quality.corpus_inference_drain import AppliedFixtureJob
    _keys(value, ('source_id', 'application', 'claim'))
    claim = value['claim']
    _keys(claim, ('batch_id', 'subject', 'batch_key', 'evidence_watermark', 'job_ids',
        'lease_token', 'lease_expires_at', 'request', 'envelope', 'application', 'analysis_apply_head'))
    if type(claim['job_ids']) is not list or len(claim['job_ids']) != 1:
        raise ValueError('inference_checkpoint_single_source_job_required')
    actual = AnalysisBatchClaim(claim['batch_id'], claim['subject'], claim['batch_key'],
        claim['evidence_watermark'], tuple(claim['job_ids']), claim['lease_token'],
        claim['lease_expires_at'], h.MemoryAnalysisRequest.from_json(claim['request']),
        None if claim['envelope'] is None else h.MemoryAnalysisResultEnvelope.from_json(claim['envelope']),
        None if claim['application'] is None else _application(claim['application']),
        analysis_apply_head=claim['analysis_apply_head'])
    return AppliedFixtureJob(value['source_id'], actual.job_ids, actual.request,
        _application(value['application']), actual)


class FixtureRecoveryFile:
    def __init__(self, path, *, binding):
        self.path = Path(path)
        self.binding = binding
        self.candidates = []

    def load(self):
        if not self.path.exists():
            return ()
        with self.path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('inference_checkpoint_too_large')
        def strict_object(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError('inference_checkpoint_duplicate_key')
                value[key] = item
            return value
        value = json.loads(raw, object_pairs_hook=strict_object)
        _keys(value, ('body', 'body_hash'))
        body = value['body']
        _keys(body, ('schema_version', 'domain', 'binding', 'candidates'))
        if (type(body['schema_version']) is not int or body['schema_version'] != 1
                or body['domain'] != DOMAIN or body['binding'] != self.binding
                or canonical_hash(body) != value['body_hash']):
            raise ValueError('inference_checkpoint_binding_or_hash_differs')
        candidates = body['candidates']
        if type(candidates) is not list or not 1 <= len(candidates) <= 2:
            raise ValueError('inference_checkpoint_candidate_count')
        self.candidates = [_decode(item) for item in candidates]
        if len({item.source_id for item in self.candidates}) != len(self.candidates):
            raise ValueError('inference_checkpoint_duplicate_source')
        return tuple(self.candidates)

    def save_candidate(self, proof):
        candidates = [p for p in self.candidates if p.source_id != proof.source_id] + [proof]
        if len(candidates) > 2:
            raise ValueError('inference_checkpoint_candidate_count')
        body = dict(schema_version=1, domain=DOMAIN, binding=self.binding,
            candidates=[_encode(p) for p in candidates])
        raw = json.dumps(dict(body=body, body_hash=canonical_hash(body)),
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        if len(raw) > MAX_BYTES:
            raise ValueError('inference_checkpoint_too_large')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.' + self.path.name, dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.candidates = candidates
