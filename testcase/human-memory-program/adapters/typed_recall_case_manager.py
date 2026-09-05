"""Small real public seed/recall fixture; never reads SDK private state.

Source tests may supply a backend factory externally. Public tests always use
package-root builder, DTOs, authority ports and Manager methods.
"""
import dataclasses as dc
import hashlib
import importlib.util
import json
import time
import inspect
from pathlib import Path
from datetime import datetime
import simple_harness as h
import simple_harness_memory as m


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seconds(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() if isinstance(value, str) else value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def H(value):
    return hashlib.sha256(canonical(value)).hexdigest()


class CaseManager:
    def __init__(self, path, *, privacy='personal', attributes=(), now=1788170400.0, backend_factory=None):
        self.path, self.now, self.backend_factory = path, now, backend_factory
        self.helpers = load('semantic_relation_public_manager')
        self.cases = load('typed_recall_public_cases')
        self.privacy, self.attributes = h.PrivacyClass(privacy.lower()), tuple(h.InformationAttribute(a) for a in attributes)
        self.principal = m.MemoryPrincipal('bridge-deployment', 'bridge-household', 'principal-1', 'bridge-session')
        self.disclosure = self.helpers._disclosure(self.principal.actor_id)
        self.events, self.sources, self.admitted, self.actions = [], [], {}, {}
        self.base_revision = 1
        self.audit_receipt = None
        self.clock_kwargs = {"clock":lambda:self.now} if "clock" in inspect.signature(m.MemoryManager.build_human_memory_v7).parameters else {}
        self.actual_now = now if backend_factory is not None or self.clock_kwargs else time.time()

    async def open(self):
        owner = self
        class Authority:
            async def resolve_admitted_evidence(self, span):
                return owner.admitted[span.evidence_id]
            async def resolve_memory_action_authority(self, ref):
                return owner.actions[ref.authority_id]
            async def resolve_typed_observation(self, ref):
                raise ValueError('typed observation authority must be explicitly registered')
        self.authority = Authority()
        audit, self.audit_ref = self.cases.audit_authority(self.principal, self.actual_now)
        kwargs = dict(evidence_authority=self.authority, memory_action_authority=self.authority,
            audit_access_authority=audit, classification_policy=m.InformationClassificationPolicy(
                policy_id='typed-recall-case-policy', policy_version='1', authority_ref='host-case-policy',
                required_privacy_class=self.privacy, required_information_attributes=self.attributes))
        if self.backend_factory:
            self.manager = await self.backend_factory(self.path, kwargs)
        else:
            self.manager = await m.build_human_memory_v7(self.path, **kwargs, **self.clock_kwargs)
        self.events.append({'call': 'build_human_memory_v7' if not self.backend_factory else 'source_backend_initialize'})
        return self

    async def evidence(self, text, evidence_id, epistemic='explicit_user'):
        envelope, receipt, span = self.helpers._evidence(self.principal.actor_id)
        payload = {'item_id': evidence_id + '-item', 'public_text': text}
        source_hash = hashlib.sha256(text.encode()).hexdigest()
        envelope = dc.replace(envelope, evidence_id=evidence_id, source_ref=evidence_id + '/user',
            sanitized_payload=payload, source_hash=source_hash, sanitized_hash=H(payload))
        receipt = dc.replace(receipt, receipt_id=evidence_id + '-admission', evidence_id=evidence_id,
            envelope_hash=envelope.envelope_hash, sanitized_hash=envelope.sanitized_hash, source_hash=source_hash)
        span = dc.replace(span, span_id=evidence_id+'-span', evidence_id=evidence_id,
            envelope_hash=envelope.envelope_hash, sanitized_hash=envelope.sanitized_hash,
            admission_receipt_id=receipt.receipt_id, admission_receipt_hash=receipt.receipt_hash,
            item_id=payload['item_id'], end_byte=len(text.encode()), exact_quote=text,
            source_hash=source_hash, quote_hash=source_hash)
        if epistemic == 'observed_behavior':
            span = dc.replace(span, actor_role=h.EvidenceActorRole.RUNTIME,
                provenance=h.EvidenceProvenance.HOST_RUNTIME, support_kind=h.EvidenceSupportKind.RUNTIME_EVENT)
        elif epistemic == 'llm_inference':
            span = dc.replace(span, actor_role=h.EvidenceActorRole.ASSISTANT,
                provenance=h.EvidenceProvenance.MODEL_OUTPUT, support_kind=h.EvidenceSupportKind.MODEL_INFERENCE)
        item = dc.replace(self.helpers._item_authority(span), authority_id=evidence_id+'-authority',
            required_privacy_class=self.privacy, required_information_attributes=self.attributes)
        self.admitted[evidence_id] = h.AdmittedEvidenceAuthority(envelope, receipt, item)
        self.events.append({'call': 'ingest_committed_evidence', 'evidence_id': evidence_id,
            'envelope':envelope.to_json(),'envelope_hash':envelope.envelope_hash,
            'admission':receipt.to_json(),'admission_hash':receipt.receipt_hash})
        await self.manager.ingest_committed_evidence(envelope, receipt)
        return envelope, span

    def payload(self, spec):
        kind = spec.get('memory_type', 'semantic')
        raw = spec['payload']
        if kind == 'semantic':
            return h.SemanticMemoryPayload(raw['subject_entity'], raw['predicate'], raw['object_value'], tuple(raw['qualifiers']))
        if kind == 'episode':
            interval = raw['occurred_interval']
            return h.EpisodeMemoryPayload(raw['title'], tuple(raw['participants']), tuple(raw['goals']),
                tuple(raw['actions']), tuple(raw['results']), tuple(raw['impacts']),
                seconds(interval['start']), seconds(interval['end']), None)
        if kind == 'procedure':
            applicability = raw['applicability']
            if isinstance(applicability, dict):
                applicability = [applicability['tool'] + '@' + applicability['version']]
            return h.ProcedureMemoryPayload(raw['name'], tuple(applicability), tuple(raw['steps']), h.ProcedureRiskLevel(raw['effective_risk']))
        if kind == 'prospective':
            trigger = raw['trigger']
            event = trigger.get('event', 'release_succeeded')
            return h.ProspectiveMemoryPayload(raw['action'], h.ProspectiveEventTrigger('host-event-release', event, H(event)))
        raise ValueError('short horizon requires public conversation registration, not cognitive mutation')

    async def seed(self, spec, *, operation_id='create-1', evidence_id='evidence-case-1', target=None,
                   kind='create', base_revision=None, no_evidence=False):
        payload = self.payload(spec)
        text = 'User memory assertion: ' + canonical(spec['payload']).decode()
        envelope, span = await self.evidence(text, evidence_id, spec.get('epistemic', 'explicit_user'))
        if kind in {'revise', 'supersede', 'suppress'}:
            span = dc.replace(span, support_kind=h.EvidenceSupportKind.EXPLICIT_USER_CORRECTION)
        memory_type = spec.get('memory_type', 'semantic')
        life = {'semantic': h.SemanticLifecycleState, 'episode': h.EpisodeLifecycleState,
                'procedure': h.ProcedureLifecycleState, 'prospective': h.ProspectiveLifecycleState}[memory_type]
        self.events.append({'call': 'MemoryMutationOperation', 'operation_id': operation_id, 'kind': kind,
                            'input': spec, 'target': None if target is None else target.to_json()})
        op = self.helpers._shared_operation(span, operation_id=operation_id, kind=h.MemoryMutationKind(kind),
            memory_type=h.LongTermMemoryType(memory_type), payload=None if kind=='suppress' else payload,
            lifecycle_state=life(spec.get('state', 'pending' if memory_type=='prospective' else 'active')),
            epistemic_status=h.EpistemicStatus(spec.get('epistemic', 'explicit_user')),
            verification_state=h.VerificationState(spec.get('verification', 'source_bound')),
            conflict_status=h.ConflictStatus(spec.get('conflict_status', 'uncontested')),
            proposed_privacy_class=self.privacy, proposed_information_attributes=self.attributes,
            valid_time_interval=h.ValidTimeInterval(seconds(spec.get('valid_from', 1.0)), seconds(spec.get('valid_until'))),
            evidence_spans=() if no_evidence else (span,), target=target)
        plan = h.MemoryMutationPlan(plan_id=operation_id+'-plan', run_id=envelope.run_id, turn_id='case-turn',
            subject=self.principal.actor_id, base_revision=self.base_revision if base_revision is None else base_revision,
            outcome=h.MemoryMutationPlanOutcome.MUTATE, operations=(op,), disclosure_context=self.disclosure,
            evidence_refs=(h.EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),), idempotency_key=operation_id+'-idem')
        if kind in {'revise', 'supersede', 'suppress'}:
            grant = h.issue_memory_action_authority(plan.action_intent(operation_id), authority_id=operation_id+'-grant',
                issued_at=self.actual_now-1, expires_at=self.actual_now+600, nonce=operation_id+'-nonce', issuer_ref='host-case-actions')
            self.actions[grant.authority_id] = grant
            plan = dc.replace(plan, operations=(dc.replace(op, action_authority_ref=h.MemoryActionAuthorityRef.from_authority(grant)),))
        self.events.append({'call': 'apply_memory_mutation_plan', 'plan': plan.to_json()})
        applied = await self.manager.apply_memory_mutation_plan(principal=self.principal,
            scope=m.MemoryScope.personal(self.principal.actor_id), plan=plan)
        self.events[-1].update(outcome=applied.outcome.value,result=applied.to_json(),result_hash=applied.result_hash)
        if applied.receipt_ref is None:
            raise ValueError('seed mutation returned ' + applied.outcome.value)
        receipt = await self.manager.get_memory_mutation_receipt_view(principal=self.principal, receipt_ref=applied.receipt_ref)
        self.events.append({'call': 'get_memory_mutation_receipt_view', 'receipt': receipt.to_json()})
        self.base_revision += 1
        source = {'input': spec, 'source_wire': payload.to_json(), 'receipt': receipt.to_json(), 'evidence_id': evidence_id}
        self.sources.append(source)
        return receipt.operations[0]

    async def snapshot(self):
        if self.audit_receipt is None:
            self.audit_receipt = await self.manager.authorize_audit_access(principal=self.principal, authority_ref=self.audit_ref)
        result = await self.manager.export_canonical_state_manifest(requester=self.principal,
            target_principal=self.principal, access_receipt=self.audit_receipt)
        return {'manifest': result.manifest.to_json(), 'payload_hash': result.manifest.payload_hash,
                'access_event_hash': result.access_event_hash}

    def request(self, *, query, memory_types=('semantic',), recipient='user_self', purpose='personalization',
                modes=('full_text',), budget=None, key='case-recall', fingerprint=()):
        disclosure = dc.replace(self.disclosure, recipient=h.DeliveryRecipient(recipient.lower()),
                                purpose=h.DisclosurePurpose(purpose.lower()))
        budget = h.RecallBudget(**(budget or dict(max_items=8,max_bytes=16384,max_tokens=2048,deadline_ms=2000)))
        context = h.RecallContext(run_id=disclosure.run_id, subject=self.principal.actor_id, turn_id='case-turn',
            context_revision=1, expires_at=self.now+300, query=query, active_task_scope_id=None,
            available_memory_types=tuple(h.LongTermMemoryType(x) for x in memory_types), short_horizon_allowed=False,
            allowed_selector_domains=(h.RecallSelectorDomain.MEMORY_TYPE,),
            allowed_retrieval_modes=tuple(h.RecallRetrievalMode(x) for x in modes), allowed_task_scope_ids=(),
            allowed_entity_constraints=(), earliest_occurred_at=None, latest_occurred_at=None,
            event_constraint_refs=(), environment_constraint_refs=(), task_phase_authority_refs=(),
            procedure_applicability_fingerprints=tuple(fingerprint), disclosure_context=disclosure,
            evidence_refs=tuple(h.EvidenceRef(eid, grant.envelope.envelope_hash, index) for index,(eid,grant) in enumerate(self.admitted.items(),1)), budget=budget)
        plan = h.RecallPlan(plan_id=key+'-plan', run_id=context.run_id, subject=context.subject,
            context_hash=context.context_hash, context_revision=context.context_revision, query=query,
            requested_memory_types=context.available_memory_types, include_short_horizon=False,
            selector_domains=context.allowed_selector_domains, retrieval_modes=context.allowed_retrieval_modes,
            task_scope_ids=(),entity_constraints=(),earliest_occurred_at=None,latest_occurred_at=None,
            event_constraint_refs=(),environment_constraint_refs=(),task_phase_authority_refs=(),
            disclosure_context=disclosure,evidence_refs=context.evidence_refs,budget=budget,idempotency_key=key,
            reason_codes=(h.RecallReasonCode.USER_FACT_DEPENDENCY,))
        return context, plan

    async def recall(self, **kwargs):
        self.events.append({'call':'RecallContext/RecallPlan','input':kwargs})
        context, plan = self.request(**kwargs)
        self.events.append({'call': 'execute_typed_recall', 'context': context.to_json(), 'plan': plan.to_json(), 'now':self.now})
        result = await self.manager.execute_typed_recall(principal=self.principal, context=context, plan=plan, now=self.now)
        wire = self.cases.execution_wire(result)
        wire['hashes'] = {'decision': result.decision.decision_hash, 'result': result.result.result_hash,
            'context': context.context_hash, 'plan':plan.plan_hash}
        return {'context':context.to_json(),'plan':plan.to_json(),'execution':wire,'now':self.now}

    async def close(self):
        await self.manager.close()
        self.events.append({'call':'close'})
