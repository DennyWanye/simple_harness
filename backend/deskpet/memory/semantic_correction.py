"""Typed semantic candidates and exact Host action authority; no graph/private SDK reads."""
from __future__ import annotations

import time
import re
import json
from dataclasses import replace
from pathlib import Path

from simple_harness import thaw_json
from simple_harness.providers import ProviderRequestRejectedError
from deskpet.memory.analysis_proposal import stable_id
from deskpet.memory.evidence_authority import HostEvidenceAuthority, HostEvidenceUnavailable
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.task_scope.protocol import canonical_hash

POLICY = 'host-analysis-observation/v2'
ISSUER = 'host:semantic-correction/v2'

# Host vocabulary, not model-supplied aliases or a caller-selected target ID.
# Unknown predicates remain readable candidates but cannot grant Chinese intent.
_CHINESE_SLOT_ALIASES = {
    predicate: ('饮品偏好', '默认饮品偏好')
    for predicate in ('preferred_drink', 'drink_preference', 'default_drink',
                      'default_drink_preference', '饮品偏好', '默认饮品偏好')
}


class SemanticCorrectionDisclosureRejected(ProviderRequestRejectedError):
    """Raised only before invoking the delegate, never after transport starts."""
    error_code = 'analysis_candidate_disclosure_rejected'
    default_message = 'Current analysis sources cannot be verified.'


class _CheckedAdapter:
    def __init__(self, delegate, authority, request, snapshot):
        self._delegate = delegate
        self._authority = authority
        self._request = request
        self._snapshot = snapshot

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    async def invoke(self, request, *, cancel):
        try:
            await self._authority.check(self._request, self._snapshot)
        except Exception as exc:
            raise SemanticCorrectionDisclosureRejected(private_cause=exc) from None
        return await self._delegate.invoke(request, cancel=cancel)


def explicit_correction_intent(candidate, items):
    """Bounded Host grammar, independent of the model's action/quote selection."""
    payload = candidate['payload']
    if payload['subject_entity'] != 'user:self':
        return None
    label = re.escape(payload['predicate'].replace('_', ' '))
    old = re.escape(payload['object_value'])
    patterns = [
        rf'Correct my {label} from {old} to ([^.!?\n]+)\.',
    ]
    for alias in _CHINESE_SLOT_ALIASES.get(payload['predicate'], ()):
        patterns.append(rf'请把记忆里的{re.escape(alias)}从{old}改成([^。！？；\n]+)。')
    found = []
    for item in items:
        if item.envelope.source_kind.value != 'user_message' or item.text is None:
            continue
        for pattern in patterns:
            match = re.fullmatch(pattern, item.text.strip())
            if match:
                quote = item.text.strip()
                start = len(item.text[:item.text.index(quote)].encode('utf-8'))
                body = dict(evidence_id=item.evidence_id, envelope_hash=item.envelope.envelope_hash,
                    evidence_item_id=item.item_id, exact_quote=quote,
                    start_byte=start, end_byte=start+len(quote.encode('utf-8')),
                    subject_entity=payload['subject_entity'], predicate=payload['predicate'],
                    old_value=payload['object_value'], new_value=match[1].strip())
                found.append({**body, 'intent_hash':canonical_hash(body)})
    return found[0] if len(found) == 1 else None


class SemanticCorrectionAuthority:
    def __init__(self, db_path, *, manager_getter, principal_getter, clock=time.time):
        self._path = Path(db_path)
        self._manager = manager_getter
        self._principal = principal_getter
        self._clock = clock
        self._evidence = HostEvidenceAuthority(db_path)
        self._store = HumanMemoryProgramStore(db_path)

    def guard_adapter(self, delegate, request, snapshot):
        return _CheckedAdapter(delegate, self, request, snapshot)

    async def _load(self, identity):
        try:
            envelope, _ = await self._evidence.read_admitted(identity)
        except HostEvidenceUnavailable as exc:
            if str(exc) == HostEvidenceUnavailable.code:
                return None
            raise
        if envelope.filter_policy_version != POLICY or envelope.source_kind.value != 'runtime_event':
            raise ValueError('analysis_observation_identity_invalid')
        return thaw_json(envelope.sanitized_payload)

    async def _save(self, identity, request, payload, *, db=None):
        from simple_harness import (SanitizedEvidenceEnvelope, SanitizedEvidenceReceipt,
            EvidenceSourceKind, EvidenceReasonCode)
        digest = canonical_hash(payload)
        envelope = SanitizedEvidenceEnvelope(evidence_id=identity, run_id=request.run_id,
            subject=request.subject, source_kind=EvidenceSourceKind.RUNTIME_EVENT,
            source_ref=identity, source_hash=digest, sanitized_payload=payload,
            sanitized_hash=digest, filter_policy_version=POLICY, removed_spans=(),
            disclosure_context=request.disclosure_context, evidence_refs=request.ordered_evidence_refs)
        receipt = SanitizedEvidenceReceipt(receipt_id='receipt:'+identity, run_id=request.run_id,
            subject=request.subject, evidence_id=identity, envelope_hash=envelope.envelope_hash,
            source_hash=digest, sanitized_hash=digest, filter_policy_version=POLICY, accepted=True,
            reason_codes=(EvidenceReasonCode.SANITIZED_AND_ACCEPTED,),
            disclosure_context=request.disclosure_context, evidence_refs=request.ordered_evidence_refs,
            admitted_at=float(self._clock()))
        if db is None:
            await self._store.append_evidence(envelope, receipt)
        else:
            cursor = await db.execute("SELECT primary_conversation_id FROM human_memory_primary_conversations "
                                      "WHERE subject=? AND writable=1", (request.subject,))
            primary = await cursor.fetchone()
            await cursor.close()
            if primary is None:
                raise ValueError('analysis_primary_missing')
            await self._store.append_evidence_tx(db, envelope, receipt,
                primary_conversation_id=primary['primary_conversation_id'], committed_at=float(self._clock()))

    async def prepare(self, request, items):
        from simple_harness import (RecallContext, RecallPlan, RecallBudget, LongTermMemoryType,
            RecallSelectorDomain, RecallRetrievalMode, RecallReasonCode)
        from deskpet.memory.analysis_executor import evidence_set_key
        key = evidence_set_key(request)
        identity = stable_id('analysis-candidates', key)
        old = await self._load(identity)
        if old is not None:
            if old['evidence_set_key'] != key or old['subject'] != request.subject:
                raise ValueError('analysis_candidate_request_mismatch')
            return old
        principal = self._principal()
        if principal.actor_id != request.subject:
            raise ValueError('analysis_candidate_subject_mismatch')
        query = '\n'.join(i.text for i in items if i.text)[:4096]
        # Retrieval hint only: SQLite full-text tokenization does not segment a
        # whole Chinese sentence into the old value. Quoted/negative text may
        # retrieve candidates, but only full-sentence intent below grants action.
        old_terms = [m.group(1) for i in items if i.text
            for m in re.finditer(r'从([^。！？；\n]+?)改成', i.text)]
        if old_terms:
            query = ' '.join(dict.fromkeys(old_terms))[:4096]
        if not query:
            snapshot = {'evidence_set_key':key, 'subject':request.subject, 'candidates':[], 'result':None, 'prepared_at':float(self._clock())}
            await self._save(identity, request, snapshot)
            return snapshot
        now = float(self._clock())
        context = RecallContext(request.run_id, request.subject, stable_id('analysis-query', key),
            1, now+60, query, None, (LongTermMemoryType.SEMANTIC,), False,
            (RecallSelectorDomain.MEMORY_TYPE,), (RecallRetrievalMode.FULL_TEXT,),
            (), (), None, None, (), (), (), (), request.disclosure_context,
            request.ordered_evidence_refs, RecallBudget(8, 16384, 2048, 1000))
        plan = RecallPlan(stable_id('analysis-recall', key), context.run_id, context.subject,
            context.context_hash, context.context_revision, context.query,
            context.available_memory_types, False, context.allowed_selector_domains,
            context.allowed_retrieval_modes, (), (), None, None, (), (), (),
            context.disclosure_context, context.evidence_refs, context.budget,
            identity, (RecallReasonCode.USER_FACT_DEPENDENCY,))
        manager = await self._manager()
        from deskpet.operation_audit.memory_attempts import MemoryAttemptJournal
        journal = MemoryAttemptJournal(self._path.with_name("operation-audit.db"), clock=self._clock)
        from deskpet.memory.recall_authority import execute_typed_recall_recollecting
        # Same benign authority race as the foreground lane: this candidate
        # query runs while other lanes apply memories, so re-collect (bounded)
        # instead of failing the analysis attempt.
        execution = await execute_typed_recall_recollecting(journal, manager, principal=principal,
            context=context, plan=plan, now=now, caller="analysis_candidates")
        result = execution.result
        result.validate_decision(execution.decision)
        candidates = []
        if not result.truncated and not result.confirmation_groups:
            for item in result.items:
                selected = item.selected_item
                payload = thaw_json(item.public_payload)
                if selected.source_kind.value != 'cognitive_memory' or selected.memory_type != LongTermMemoryType.SEMANTIC:
                    continue
                if not isinstance(payload, dict) or not all(k in payload for k in ('subject_entity','predicate','object_value')):
                    continue  # semantic relations are outside this correction slice
                body = {'memory_id':selected.source_ref, 'revision':selected.source_revision,
                    'source_content_hash':selected.source_content_hash, 'payload':payload,
                    'result_id':result.result_id, 'result_hash':result.result_hash,
                    'item_id':selected.item_id, 'item_hash':item.result_item_hash,
                    'privacy_class':item.effective_privacy_class.value,
                    'information_attributes':[a.value for a in item.information_attributes]}
                body['correction_intent'] = explicit_correction_intent(body, items)
                candidates.append({'candidate_key':stable_id('semantic-candidate', key, canonical_hash(body)), **body})
        snapshot = dict(evidence_set_key=key, subject=request.subject, prepared_at=now, context=context.to_json(),
            plan=plan.to_json(), decision=execution.decision.to_json(), result=result.to_json(),
            result_hash=result.result_hash, candidates=candidates)
        await self._save(identity, request, snapshot)
        return snapshot

    async def bind_attempt(self, request, row, snapshot, provider_request, *, db):
        # This observation and the real attempt reserve commit together. It is
        # still input lineage, not a Provider-send fact or a mutation grant.
        from simple_harness.execution.provider_invocations import provider_request_json
        from deskpet.memory.analysis_request_guard import read_observation_tx, source_policy_snapshot_tx
        identity = stable_id('analysis-attempt-input', row['attempt_id'])
        content = dict(request_id=provider_request.request_id.value,
            messages=[{'role':m.role.value, 'content':thaw_json(m.content)} for m in provider_request.messages],
            tools=[{'name':t.name,'description':t.description,'schema':thaw_json(t.parameters)} for t in provider_request.tools],
            max_output_tokens=provider_request.max_output_tokens)
        body = dict(attempt_id=row['attempt_id'], request_hash=request.request_hash,
            evidence_set_key=snapshot['evidence_set_key'],
            snapshot_id=stable_id('analysis-candidates', snapshot['evidence_set_key']),
            snapshot_hash=canonical_hash(snapshot), input_hash=canonical_hash(content), input=content)
        full = provider_request_json(provider_request)
        body.update(physical_guard_version=1, analysis_request=request.to_json(),
            provider_request=full, provider_request_hash=canonical_hash(full),
            source_policy=await source_policy_snapshot_tx(db, request))
        prior = await read_observation_tx(db, identity=identity, subject=request.subject)
        old = None if prior is None else prior[1]
        if old is None:
            await self._save(identity, request, body, db=db)
        elif old != body:
            raise ValueError('analysis_attempt_input_conflict')

    async def snapshot_for_attempt(self, request, attempt_id, response):
        from deskpet.memory.analysis_executor import evidence_set_key
        from deskpet.sdk_adapters.post_turn_invoker import durable_result_json
        bound = await self._load(stable_id('analysis-attempt-input', attempt_id))
        if bound is None or bound['attempt_id'] != attempt_id or bound['request_hash'] != request.request_hash or bound['evidence_set_key'] != evidence_set_key(request):
            raise ValueError('analysis_attempt_input_missing')
        snapshot = await self._load(bound['snapshot_id'])
        if snapshot is None or canonical_hash(snapshot) != bound['snapshot_hash'] or canonical_hash(bound['input']) != bound['input_hash']:
            raise ValueError('analysis_attempt_snapshot_mismatch')
        identity = stable_id('analysis-attempt-response', attempt_id)
        body = dict(attempt_id=attempt_id, input_hash=bound['input_hash'],
            snapshot_hash=bound['snapshot_hash'],
            response_hash=canonical_hash(json.loads(durable_result_json(response=response, envelope_json=None))))
        old = await self._load(identity)
        if old is None:
            await self._save(identity, request, body)
        elif old != body:
            raise ValueError('analysis_attempt_response_conflict')
        return snapshot

    async def check(self, request, snapshot):
        from simple_harness_memory import HistoryEvidenceBinding, HistoryRecallBinding
        from deskpet.memory.primary_visibility import _binding_hash
        manager = await self._manager()
        principal = self._principal()
        if principal.actor_id != request.subject or snapshot['subject'] != request.subject:
            raise ValueError('analysis_candidate_subject_mismatch')
        bindings = []
        for ref in request.ordered_evidence_refs:
            envelope, receipt = await self._evidence.read_admitted(ref.evidence_id)
            if envelope.envelope_hash != ref.content_hash:
                raise ValueError('analysis_candidate_evidence_mismatch')
            bindings.append(HistoryEvidenceBinding(envelope, receipt))
        for row in snapshot['candidates']:
            bindings.append(HistoryRecallBinding(row['result_id'], row['result_hash'], row['item_id'], row['item_hash']))
        observed = await manager.check_history_visibility(principal=principal,
            disclosure_context=request.disclosure_context, bindings=tuple(bindings))
        if observed.subject != request.subject or tuple(r.binding_hash for r in observed.items) != tuple(_binding_hash(b) for b in bindings) or not all(r.visible for r in observed.items):
            raise ValueError('analysis_candidate_no_longer_visible')

    @staticmethod
    def prompt(snapshot):
        return [{'candidate_key':r['candidate_key'], 'semantic':r['payload']} for r in snapshot['candidates']]

    async def authorize_plan(self, request, snapshot, plan):
        from simple_harness import MemoryActionAuthorityRef, issue_memory_action_authority
        await self.check(request, snapshot)
        operations = []
        for operation in plan.operations:
            if operation.kind.value != 'revise':
                operations.append(operation)
                continue
            matches = [c for c in snapshot['candidates'] if c['memory_id'] == operation.target.memory_id
                       and c['revision'] == operation.target.revision]
            if len(matches) != 1 or operation.memory_type.value != 'semantic':
                raise ValueError('analysis_action_target_not_issued')
            candidate = matches[0]
            slot = (candidate['payload']['subject_entity'], candidate['payload']['predicate'])
            if sum((c['payload']['subject_entity'], c['payload']['predicate']) == slot for c in snapshot['candidates']) != 1:
                raise ValueError('analysis_action_target_ambiguous')
            if (tuple(operation.payload.qualifiers) != tuple(candidate['payload'].get('qualifiers', ()))
                or operation.proposed_privacy_class.value != candidate['privacy_class']
                or tuple(a.value for a in operation.proposed_information_attributes) != tuple(candidate['information_attributes'])):
                raise ValueError('analysis_action_classification_or_qualifiers_mismatch')
            approval = matches[0].get('correction_intent')
            if approval is None or operation.payload.object_value != approval['new_value'] or (operation.payload.subject_entity, operation.payload.predicate) != (approval['subject_entity'], approval['predicate']):
                raise ValueError('analysis_explicit_correction_intent_missing')
            if sum(c.get('correction_intent') is not None and c['correction_intent']['evidence_id'] == approval['evidence_id'] and c['correction_intent']['exact_quote'] == approval['exact_quote'] for c in snapshot['candidates']) != 1:
                raise ValueError('analysis_action_target_ambiguous')
            if len(operation.evidence_spans) != 1 or not all(getattr(operation.evidence_spans[0], k) == approval[k] for k in ('evidence_id','envelope_hash','exact_quote')):
                raise ValueError('analysis_explicit_correction_source_mismatch')
            from deskpet.memory.analysis_proposal import admitted_item, derive_span
            from simple_harness import EvidenceSupportKind
            if not any(r.evidence_id == approval['evidence_id'] and r.content_hash == approval['envelope_hash'] for r in request.ordered_evidence_refs):
                raise ValueError('analysis_explicit_correction_source_not_current')
            envelope, receipt = await self._evidence.read_admitted(approval['evidence_id'])
            item = admitted_item(envelope, receipt)
            if envelope.subject != request.subject or explicit_correction_intent(candidate, (item,)) != approval:
                raise ValueError('analysis_explicit_correction_intent_mismatch')
            expected_span = replace(derive_span(item, approval['exact_quote'], span_id=operation.evidence_spans[0].span_id),
                support_kind=EvidenceSupportKind.EXPLICIT_USER_CORRECTION)
            if operation.evidence_spans[0] != expected_span:
                raise ValueError('analysis_explicit_correction_span_mismatch')
            intent = plan.action_intent(operation.operation_id)
            identity = stable_id('semantic-action', intent.intent_hash)
            stored = await self._load(identity)
            if stored is None:
                now = float(self._clock())
                authority = issue_memory_action_authority(intent, authority_id=identity,
                    issued_at=now, expires_at=now+300, nonce=identity, issuer_ref=ISSUER)
                await self._save(identity, request, {'authority':authority.to_json(),
                    'candidate_key':matches[0]['candidate_key'], 'evidence_set_key':snapshot['evidence_set_key']})
            else:
                from simple_harness import MemoryActionAuthority
                authority = MemoryActionAuthority.from_json(stored['authority'])
                if authority.intent != intent:
                    raise ValueError('analysis_action_intent_mismatch')
            operations.append(replace(operation, action_authority_ref=MemoryActionAuthorityRef.from_authority(authority)))
        return replace(plan, operations=tuple(operations))

    async def resolve_memory_action_authority(self, reference):
        from simple_harness import MemoryActionAuthority, MemoryActionAuthorityRef
        if reference.issuer_ref != ISSUER:
            raise ValueError('analysis_action_issuer_mismatch')
        stored = await self._load(reference.authority_id)
        if stored is None:
            raise ValueError('analysis_action_authority_missing')
        authority = MemoryActionAuthority.from_json(stored['authority'])
        if reference != MemoryActionAuthorityRef.from_authority(authority):
            raise ValueError('analysis_action_reference_mismatch')
        return authority
