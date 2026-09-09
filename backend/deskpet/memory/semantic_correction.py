"""Typed semantic candidates and exact Host action authority; no graph/private SDK reads."""
from __future__ import annotations

import logging
import time
import inspect
import re
import json
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from simple_harness import thaw_json
from simple_harness.providers import ProviderRequestRejectedError
from deskpet.memory.analysis_proposal import stable_id
from deskpet.memory.evidence_authority import HostEvidenceAuthority, HostEvidenceUnavailable
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.task_scope.protocol import canonical_hash

logger = logging.getLogger(__name__)

POLICY = 'host-analysis-observation/v2'
ISSUER = 'host:semantic-correction/v2'

# ---------------------------------------------------------------- relation audit
#
# Incident S (HM-TO-A6 attempt 8): the relation-endpoint channel had exactly one
# durable outcome — ``relation_candidates_unavailable: true`` — and one log line
# carrying only ``KeyError('<uuid>')``.  Every batch of that run degraded to "no
# relation candidates" and nothing recorded *why*.  These codes are the Host's
# stable vocabulary for that question; every one of them lands in the batch's
# candidate snapshot (``relation_candidate_reasons``), which ``bind_attempt``
# hashes into the attempt, so a missing endpoint is always attributable.
RELATION_APPLICABILITY_UNAVAILABLE = 'relation_procedure_applicability_unavailable'
RELATION_APPLICABILITY_ABSENT = 'relation_procedure_applicability_absent'
RELATION_RECALL_UNAVAILABLE = 'relation_candidate_recall_unavailable'
RELATION_RESULT_TRUNCATED = 'relation_candidate_result_truncated'
RELATION_CONFIRMATION_REQUIRED = 'relation_candidate_confirmation_required'
RELATION_MEMBER_SOURCE_KIND_UNSUPPORTED = 'relation_candidate_source_kind_unsupported'
RELATION_MEMBER_TYPE_UNSUPPORTED = 'relation_candidate_memory_type_unsupported'
RELATION_MEMBER_PAYLOAD_UNREADABLE = 'relation_candidate_payload_unreadable'
RELATION_MEMBER_ENDPOINT_UNVERIFIABLE = 'relation_candidate_endpoint_unverifiable'

# F-S1b (SDK 0.6.36).  ``check_history_visibility`` used to hand its source
# re-validation an empty applicability set on purpose ("never reuse old runtime
# fingerprints"), so a Procedure item was reported stale there no matter what — which
# is why every Procedure endpoint had to be withheld before issue.  0.6.36 adds an
# optional, explicitly-provenanced entry point for a lane with no live Run, and admits
# a Procedure only where Memory's own immutable observation audit carries the same
# fingerprint.  We feature-detect it rather than pin a version, so this file keeps
# working against 0.6.34/0.6.35 (where the withholding stays in force).
# The withheld reason names the *binding* blocker, and that is the visibility gate, not
# endpoint resolution: SDK 0.6.35 already fixed resolution (nearest classified ancestor
# governs), so on 0.6.35 "the SDK cannot resolve this endpoint" would be false and would
# send the next reader to the wrong fix.  0.6.31–0.6.34 additionally could not resolve it;
# the gate is the one fact true across every SDK that lacks the 0.6.36 entry point.
SDK_PROCEDURE_APPLICABILITY_GATE_UNAVAILABLE = 'sdk_procedure_applicability_gate_unavailable'
SDK_PROCEDURE_APPLICABILITY_ABSENT = 'sdk_procedure_applicability_absent'


@lru_cache(maxsize=1)
def sdk_offline_applicability_capable():
    """Whether the installed Memory SDK accepts a caller-presented applicability set.

    Attribute/signature detection, not a version comparison: the capability is exactly
    "the root exports the attestation types AND the public facade takes the kwarg".
    Absent, `_endpoint_unverifiable` keeps withholding every Procedure endpoint and
    `check` keeps failing a batch that somehow carries one, so an older SDK degrades to
    the 0.6.35 behaviour rather than to an unchecked one.

    This probe is necessary, not sufficient: a name in a signature proves nothing about
    what the callee does with it.  ``check`` therefore also verifies the returned
    ``procedure_applicability`` receipt before accepting any Procedure binding, so a
    backend that accepts the kwarg and ignores it cannot admit one.

    Cached: the installed distribution cannot change inside one process, and this is
    asked once per relation candidate and again per ``check``.
    """
    import simple_harness_memory as sdk

    attestation = getattr(sdk, 'ProcedureApplicabilityAttestation', None)
    provenance = getattr(sdk, 'ProcedureApplicabilityProvenance', None)
    member = getattr(provenance, 'APPLIED_USE_FINGERPRINTS', None)
    if attestation is None or member is None or not isinstance(member, provenance):
        return False
    try:
        parameters = inspect.signature(sdk.MemoryManager.check_history_visibility).parameters
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return False
    return 'procedure_applicability' in parameters


def _relation_reason(reason, *, memory_id=None, revision=None, detail=None):
    """One audit row. Fixed key set so the snapshot hash stays canonical."""
    return {'reason': reason, 'memory_id': memory_id, 'revision': revision, 'detail': detail}

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


# ------------------------------------------------------------------ intent grammar
#
# Incident H (HM-TO-A6 turn 20): the only Host grammar was the two full-sentence
# templates below, so a real explicit correction —
# 「更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12。」 — produced no intent and
# the model's otherwise valid ``revise_semantic`` was rejected with
# ``analysis_explicit_correction_intent_missing``.  The template path is kept byte
# identical (persisted candidate snapshots must re-derive to the same intent_hash);
# a second, predicate-agnostic path is added below.

# A correction cue must OPEN the current USER message: reported, hypothetical and
# historical sentences ("Yesterday I said Correct my ...") therefore never grant intent.
_CORRECTION_CUE = re.compile(
    r'^(?P<cue>更正一下|更正|纠正一下|纠正|改正一下|改正|修正一下|修正|订正|我说错了|说错了|'
    r'correction|i misspoke|correcting myself)'
    r'[\s:：,，、。\-—]+(?P<body>.+)$',
    re.IGNORECASE | re.DOTALL,
)

# Any of these anywhere in the message withdraws correction intent (fail closed).
# Hedges are listed here on purpose: a hedged contradiction is a CONTEST, never a
# silent overwrite (see ``hedged_contradiction_marker``).
_CORRECTION_DISQUALIFIERS = (
    '“', '”', '‘', '’', '"', "'", '「', '」', '《', '》',
    '如果', '假如', '要是', '假设', '万一', '不要', '别把', '不用',
    '吗', '呢', '吧', '？', '?',
    '好像', '也许', '大概', '可能', '似乎', '印象里', '印象中', '我记得', '记不清', '记不太清',
    '不确定', '不太确定', '他说', '她说', '有人说', '听说', '据说',
    'if ', 'suppose', 'would you', 'someone said', 'yesterday i said', 'do not ',
    'maybe', 'perhaps', 'i think', 'i recall', 'i believe', 'seem', 'as far as i remember',
)

_VALUE_TOKEN_SPLIT = re.compile(r'[\s，,。、；;：:（）()\[\]【】]+')


def _value_tokens(value):
    """Tokens of a stored ``object_value`` that can anchor a correction to that slot."""
    return tuple(token for token in _VALUE_TOKEN_SPLIT.split(str(value).strip()) if len(token) >= 2)


def _discriminating_anchor(candidate, body, all_candidates):
    """The longest old-value token quoted in ``body`` that no other candidate shares.

    This is the Host's slot anchor: it replaces the per-predicate alias table, so
    ``omitted old values do not authorize`` still holds for every predicate, and two
    candidates whose values share the quoted token stay ambiguous (no intent at all).
    """
    memory_id = candidate.get('memory_id')
    others = [str(c['payload']['object_value']) for c in all_candidates
              if c.get('memory_id') != memory_id]
    anchors = [token for token in _value_tokens(candidate['payload']['object_value'])
               if token in body and not any(token in other for other in others)]
    if not anchors:
        return None
    return sorted(anchors, key=lambda token: (-len(token), token))[0]


def _intent_body(item, payload, quote, **extra):
    start = len(item.text[:item.text.index(quote)].encode('utf-8'))
    return dict(evidence_id=item.evidence_id, envelope_hash=item.envelope.envelope_hash,
        evidence_item_id=item.item_id, exact_quote=quote,
        start_byte=start, end_byte=start + len(quote.encode('utf-8')),
        subject_entity=payload['subject_entity'], predicate=payload['predicate'],
        old_value=payload['object_value'], **extra)


def _template_correction_intent(candidate, items):
    """Frozen v2 grammar — the Host also fixes the new value; do not change its body."""
    payload = candidate['payload']
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
                body = _intent_body(item, payload, quote, new_value=match[1].strip())
                found.append({**body, 'intent_hash':canonical_hash(body)})
    return found[0] if len(found) == 1 else None


def _cue_anchor_correction_intent(candidate, items, all_candidates):
    """Predicate-agnostic grammar: opening correction cue + quoted old-value anchor.

    The Host still decides *whether* the current USER corrects *this* slot; only the
    replacement value is left to the model, and it must be a verbatim span of this very
    sentence (compiler + issuer both re-check that).  ``new_value`` is therefore None.
    """
    payload = candidate['payload']
    found = []
    for item in items:
        if item.envelope.source_kind.value != 'user_message' or item.text is None:
            continue
        quote = item.text.strip()
        lowered = quote.lower()
        if any(bad in lowered for bad in _CORRECTION_DISQUALIFIERS):
            continue
        match = _CORRECTION_CUE.match(quote)
        if match is None:
            continue
        anchor = _discriminating_anchor(candidate, match.group('body'), all_candidates)
        if anchor is None:
            continue
        body = _intent_body(item, payload, quote, new_value=None,
                            grammar='cue-anchor/v1', cue=match.group('cue'), anchor=anchor)
        found.append({**body, 'intent_hash':canonical_hash(body)})
    return found[0] if len(found) == 1 else None


def explicit_correction_intent(candidate, items, all_candidates=()):
    """Bounded Host grammar, independent of the model's action/quote selection."""
    if candidate['payload']['subject_entity'] != 'user:self':
        return None
    return (_template_correction_intent(candidate, items)
            or _cue_anchor_correction_intent(candidate, items, all_candidates))


# A hedged contradiction is HM-S3 「含糊冲突 contested」: it must not overwrite the head,
# and it must not be dropped either.  CONTEST is non-destructive (the SDK freezes payload,
# lifecycle, epistemic, verification and valid-time, and needs no MemoryActionAuthority),
# so the Host gates it on the hedge marker + source binding and lets the model pick which
# claim the user is hesitating about; the worst case is a contested head awaiting
# confirmation, never a lost or rewritten value.
_HEDGE_MARKERS = (
    '印象里', '印象中', '好像', '似乎', '大概', '也许', '可能', '我记得', '记不清', '记不太清',
    '不确定', '不太确定', '应该是吧',
    'i think', 'i recall', 'maybe', 'perhaps', 'seems', 'i believe', 'as far as i remember',
)
# Reported / hypothetical / quoted speech is not the current user hesitating.
_HEDGE_DISQUALIFIERS = (
    '“', '”', '‘', '’', '"', '「', '」', '《', '》',
    '如果', '假如', '要是', '假设', '万一', '他说', '她说', '有人说', '听说', '据说',
    'if ', 'suppose', 'someone said', 'he said', 'she said',
)


def hedged_contradiction_marker(text):
    """The single hedge marker of a current-USER hedged statement, else ``None``."""
    if not isinstance(text, str):
        return None
    quote = text.strip()
    lowered = quote.lower()
    if any(bad in lowered for bad in _HEDGE_DISQUALIFIERS):
        return None
    if _CORRECTION_CUE.match(quote) is not None:
        return None  # an explicit correction is a REVISE, never a contest
    markers = [marker for marker in _HEDGE_MARKERS if marker in lowered]
    if not markers:
        return None
    return sorted(markers, key=lambda marker: (-len(marker), marker))[0]


class SemanticCorrectionAuthority:
    def __init__(self, db_path, *, manager_getter, principal_getter, clock=time.time,
                 procedure_fingerprints_getter=None):
        self._path = Path(db_path)
        self._manager = manager_getter
        self._principal = principal_getter
        self._clock = clock
        self._procedure_fingerprints = procedure_fingerprints_getter
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
            snapshot = {'evidence_set_key':key, 'subject':request.subject, 'candidates':[],
                'relation_candidates':[], 'relation_candidates_unavailable':False,
                'relation_candidate_reasons':[], 'relation_applicability_fingerprints':[],
                'result':None, 'prepared_at':float(self._clock())}
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
                candidates.append({'memory_id':selected.source_ref, 'revision':selected.source_revision,
                    'source_content_hash':selected.source_content_hash, 'payload':payload,
                    'result_id':result.result_id, 'result_hash':result.result_hash,
                    'item_id':selected.item_id, 'item_hash':item.result_item_hash,
                    'privacy_class':item.effective_privacy_class.value,
                    'information_attributes':[a.value for a in item.information_attributes]})
        # Second pass: the cue+anchor grammar needs the whole issued set to tell an
        # anchor that identifies exactly one slot from one several candidates share.
        issued = list(candidates)
        candidates = []
        for body in issued:
            body['correction_intent'] = explicit_correction_intent(body, items, issued)
            candidates.append({'candidate_key':stable_id('semantic-candidate', key, canonical_hash(body)), **body})
        (relation_candidates, relation_unavailable, relation_reasons,
         relation_fingerprints) = await self._relation_candidates(
            request, key, principal, manager, journal, now, query)
        snapshot = dict(evidence_set_key=key, subject=request.subject, prepared_at=now, context=context.to_json(),
            plan=plan.to_json(), decision=execution.decision.to_json(), result=result.to_json(),
            result_hash=result.result_hash, candidates=candidates,
            relation_candidates=relation_candidates,
            relation_candidates_unavailable=relation_unavailable,
            relation_candidate_reasons=relation_reasons,
            # F-S1b: `check` must re-verify a Procedure endpoint with the SAME
            # applicability set the recall that issued it was bound to. The snapshot is
            # the durable, hash-bound record of this batch, so it is the only honest
            # place to carry them; re-deriving them at check time would be a different
            # fact under a different clock.
            relation_applicability_fingerprints=list(relation_fingerprints))
        await self._save(identity, request, snapshot)
        return snapshot

    async def _relation_candidates(self, request, key, principal, manager, journal, now, query):
        """Existing Procedure/Prospective memories the model may use as a relation endpoint.

        Incident L: an ``applies_to`` relation needs a Procedure/Prospective on the target
        side, and before v8 that endpoint had to be created by the same proposal — so
        「这套流程按我前面说的 Python 环境执行」 had no relation shape at all and degraded to a
        literal claim.  This is an *additive* channel: it never grants a mutation by itself
        (the SDK re-resolves every endpoint at apply time), so a failure here degrades to an
        empty list and is recorded, rather than failing a turn that used to work.

        Incident S: "recorded" used to mean one boolean.  Resolving the Procedure
        applicability fingerprints raised ``KeyError(<analysis run id>)`` (see
        ``ProcedureRuntime.applied_use_fingerprints``) and the single ``except`` around
        the whole method threw away the *recall as well* — including the Prospective
        endpoints, which do not depend on applicability at all.  The two phases are
        separated below, and every endpoint that cannot be offered leaves a named reason
        in the returned audit rows instead of vanishing.  Returns
        ``(rows, recall_unavailable, reasons, fingerprints)`` — the fingerprints being
        exactly the set the recall below was bound to, which `prepare` persists so that
        `check` re-verifies with the same facts (F-S1b).
        """
        from simple_harness import (RecallContext, RecallPlan, RecallBudget, LongTermMemoryType,
            RecallSelectorDomain, RecallRetrievalMode, RecallReasonCode)
        from deskpet.memory.recall_authority import execute_typed_recall_recollecting

        types = (LongTermMemoryType.PROCEDURE, LongTermMemoryType.PROSPECTIVE)
        reasons = []
        # Phase 1 — applicability. Its failure only costs the Procedure endpoints:
        # an empty set is exactly what the SDK gate reads as "no Procedure qualifies",
        # so the recall below still runs and the Prospective lane is unaffected.
        fingerprints = ()
        answered = self._procedure_fingerprints is None
        if self._procedure_fingerprints is not None:
            try:
                fingerprints = tuple(await self._procedure_fingerprints())
                answered = True
            except Exception as exc:  # noqa: BLE001 - additive channel, never fails the turn
                reasons.append(_relation_reason(RELATION_APPLICABILITY_UNAVAILABLE,
                                                detail=type(exc).__name__))
                logger.warning("memory.analysis_relation_applicability_unavailable key=%s", key,
                               exc_info=True)
        if answered and not fingerprints:
            # F-L1 is now explicit rather than silent: no Procedure use with a consumed
            # observation exists, so the SDK gate cannot let any Procedure surface this
            # batch.  A getter that *raised* is a different fact and already has its own
            # code above; the two are never both recorded.
            reasons.append(_relation_reason(RELATION_APPLICABILITY_ABSENT))
        # Phase 2 — the public typed recall itself. Only this one degrades the channel.
        try:
            context = RecallContext(request.run_id, request.subject,
                stable_id('analysis-relation-query', key), 1, now + 60, query, None, types, False,
                (RecallSelectorDomain.MEMORY_TYPE,), (RecallRetrievalMode.FULL_TEXT,),
                (), (), None, None, (), (), (), fingerprints, request.disclosure_context,
                request.ordered_evidence_refs, RecallBudget(8, 16384, 2048, 1000))
            plan = RecallPlan(stable_id('analysis-relation-recall', key), context.run_id,
                context.subject, context.context_hash, context.context_revision, context.query,
                context.available_memory_types, False, context.allowed_selector_domains,
                context.allowed_retrieval_modes, (), (), None, None, (), (), (),
                context.disclosure_context, context.evidence_refs, context.budget,
                stable_id('analysis-relation-candidates', key), (RecallReasonCode.USER_FACT_DEPENDENCY,))
            execution = await execute_typed_recall_recollecting(journal, manager, principal=principal,
                context=context, plan=plan, now=now, caller="analysis_relation_candidates")
            result = execution.result
            result.validate_decision(execution.decision)
        except Exception as exc:  # noqa: BLE001 - additive channel, never fails the analysis turn
            logger.warning("memory.analysis_relation_candidates_unavailable key=%s reason=%s", key,
                           RELATION_RECALL_UNAVAILABLE, exc_info=True)
            reasons.append(_relation_reason(RELATION_RECALL_UNAVAILABLE, detail=type(exc).__name__))
            return [], True, reasons, fingerprints
        rows = []
        if result.truncated:
            reasons.append(_relation_reason(RELATION_RESULT_TRUNCATED))
        if result.confirmation_groups:
            reasons.append(_relation_reason(RELATION_CONFIRMATION_REQUIRED))
        if not result.truncated and not result.confirmation_groups:
            for item in result.items:
                selected = item.selected_item
                # Per-member skips are audited one by one: a single unusable member
                # never costs the other endpoints of the same batch — and rendering a
                # member is itself inside the guard, so a malformed payload/class is a
                # named reason rather than a raise that ends the analysis turn.
                try:
                    payload = thaw_json(item.public_payload)
                    if selected.source_kind.value != 'cognitive_memory':
                        reasons.append(_relation_reason(RELATION_MEMBER_SOURCE_KIND_UNSUPPORTED,
                            memory_id=selected.source_ref, revision=selected.source_revision,
                            detail=selected.source_kind.value))
                        continue
                    if selected.memory_type not in types:
                        reasons.append(_relation_reason(RELATION_MEMBER_TYPE_UNSUPPORTED,
                            memory_id=selected.source_ref, revision=selected.source_revision,
                            detail=selected.memory_type.value))
                        continue
                    if not isinstance(payload, dict):
                        reasons.append(_relation_reason(RELATION_MEMBER_PAYLOAD_UNREADABLE,
                            memory_id=selected.source_ref, revision=selected.source_revision))
                        continue
                    body = {'memory_id': selected.source_ref, 'revision': selected.source_revision,
                        'memory_type': selected.memory_type.value,
                        'source_content_hash': selected.source_content_hash, 'payload': payload,
                        'result_id': result.result_id, 'result_hash': result.result_hash,
                        'item_id': selected.item_id, 'item_hash': item.result_item_hash,
                        'privacy_class': item.effective_privacy_class.value,
                        'information_attributes': [a.value for a in item.information_attributes]}
                    candidate_key = stable_id('relation-candidate', key, canonical_hash(body))
                except Exception as exc:  # noqa: BLE001 - additive channel, per-member
                    reasons.append(_relation_reason(RELATION_MEMBER_PAYLOAD_UNREADABLE,
                        memory_id=getattr(selected, 'source_ref', None),
                        revision=getattr(selected, 'source_revision', None),
                        detail=type(exc).__name__))
                    continue
                unverifiable = self._endpoint_unverifiable(body, fingerprints=fingerprints)
                if unverifiable is not None:
                    reasons.append(_relation_reason(RELATION_MEMBER_ENDPOINT_UNVERIFIABLE,
                        memory_id=selected.source_ref, revision=selected.source_revision,
                        detail=unverifiable))
                    continue
                rows.append({'candidate_key': candidate_key, **body})
        if reasons:
            logger.info("memory.analysis_relation_candidate_reasons key=%s codes=%s", key,
                        ','.join(sorted({str(row['reason']) for row in reasons})))
        return rows, False, reasons, fingerprints

    @staticmethod
    def _endpoint_unverifiable(body, *, fingerprints):
        """Whether one existing relation endpoint can be offered at all. ``None`` = it can.

        Incident S, second half.  Two SDK facts, both measured, both of which used to be
        invisible because incident L only ever exercised an **empty**
        ``relation_candidates`` list:

        1. ``check_history_visibility`` is the right gate for a semantic candidate, but
           through 0.6.35 the SDK deliberately handed its source re-validation *no*
           procedure applicability ("never reuse old runtime fingerprints",
           ``backends/history_visibility.py``), so a Procedure item was always reported
           stale there and ``check()`` failed the **whole batch** with
           ``analysis_candidate_no_longer_visible``.
        2. Even past that gate, SDK 0.6.31–0.6.34 could not resolve any Procedure relation
           endpoint.  A Procedure is recallable only at ``active``/``reinforced``
           (``_cognitive_recall_state_allowed``), which takes three independent
           observations, and an observation commits a revision that carries evidence
           spans but **no** ``cognitive_classification_decisions`` row — so
           ``_resolve_semantic_relation_payload_unlocked`` raised
           ``MemoryCorruptionError('relation endpoint classification is missing')`` on
           the current head and killed the batch, losing every memory of that turn.
           ``test_analysis_relation_applied`` measured it: head revision 4,
           classification rows only for revision 1.

        Fact 2 is fixed in SDK 0.6.35 (nearest classified ancestor governs) and fact 1 in
        0.6.36 (this lane may present its applied-use fingerprints, and Memory corroborates
        them against its own ``procedure_observations`` — per memory and fingerprint, not
        per revision, so an observation of an older revision corroborates the head).  So
        from 0.6.35 the withheld reason is the *visibility gate*, not endpoint resolution:
        naming resolution there would be false on 0.6.35 and would send the next reader to
        the wrong SDK fix.  The withholding is lifted **only** where the installed SDK
        advertises the 0.6.36 entry point.  The second branch is belt-and-braces: with an
        empty fingerprint set the SDK recall gate already drops every Procedure, so no
        Procedure member reaches it through the production path — it exists so a future
        caller cannot issue an endpoint this batch could not re-verify in ``check``.
        Prospective endpoints were never affected by either fact.
        """
        if body.get('memory_type') != 'procedure':
            return None
        if not sdk_offline_applicability_capable():
            return SDK_PROCEDURE_APPLICABILITY_GATE_UNAVAILABLE
        if not fingerprints:
            return SDK_PROCEDURE_APPLICABILITY_ABSENT
        return None

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
        rows = (*snapshot['candidates'], *snapshot.get('relation_candidates', ()))
        # F-S1b.  Through SDK 0.6.35 ``check_history_visibility`` structurally could not
        # pass a Procedure item, so a Procedure reaching this gate meant the withholding
        # rule and this check had drifted apart and the batch had to fail.  From 0.6.36
        # there is a real re-validation path: present the *same* applicability set the
        # recall that issued these candidates was bound to (same *set* — the snapshot is
        # the only persisted source, and `applied_use_fingerprints` already returns it
        # sorted and deduplicated), with an explicit provenance that says "these uses
        # really happened", and let Memory corroborate it against its own observation
        # audit.  Every other gate (head/status/hash/expiry/disclosure/suppression) is
        # unchanged.  Without the capability — or without fingerprints to present — the
        # old fail-closed branch stands, so a snapshot prepared under a newer SDK and
        # replayed under an older one fails rather than skipping the gate.
        procedure_applicability = None
        if any(row.get('memory_type') == 'procedure' for row in rows):
            fingerprints = tuple(sorted({f for f in
                snapshot.get('relation_applicability_fingerprints', ())
                if isinstance(f, str) and f.strip()}))
            if not fingerprints or not sdk_offline_applicability_capable():
                raise ValueError('analysis_candidate_no_longer_visible')
            from simple_harness_memory import (ProcedureApplicabilityAttestation,
                ProcedureApplicabilityProvenance)
            try:
                procedure_applicability = ProcedureApplicabilityAttestation(
                    ProcedureApplicabilityProvenance.APPLIED_USE_FINGERPRINTS, fingerprints)
            except Exception as exc:  # noqa: BLE001 - stay inside this gate's vocabulary
                # The SDK bounds the set at 256; the Host bound is the `LIMIT 128` in
                # `applied_use_fingerprints`, stated in another module. Whatever the
                # reason, an attestation we cannot build is a failed re-verification,
                # not a stray SDK-shaped error escaping through `authorize_plan`.
                raise ValueError('analysis_candidate_no_longer_visible') from exc
        first_recall_binding = len(bindings)
        for row in rows:
            bindings.append(HistoryRecallBinding(row['result_id'], row['result_hash'], row['item_id'], row['item_hash']))
        extra = ({} if procedure_applicability is None
                 else {'procedure_applicability': procedure_applicability})
        observed = await manager.check_history_visibility(principal=principal,
            disclosure_context=request.disclosure_context, bindings=tuple(bindings), **extra)
        if observed.subject != request.subject or tuple(r.binding_hash for r in observed.items) != tuple(_binding_hash(b) for b in bindings) or not all(r.visible for r in observed.items):
            raise ValueError('analysis_candidate_no_longer_visible')
        if procedure_applicability is not None:
            # The capability probe only proves a parameter *name* exists. The receipt is
            # what proves the widening was actually paid for: a backend that accepted the
            # kwarg and ignored it returns no receipt (or one for another attestation),
            # and a Procedure binding that is visible without appearing in
            # `admitted_binding_hashes` was admitted by something other than this
            # attestation. Both are refused here rather than trusted.
            receipt = getattr(observed, 'procedure_applicability', None)
            if receipt is None or getattr(receipt, 'attestation_hash', None) != (
                    procedure_applicability.attestation_hash):
                raise ValueError('analysis_candidate_no_longer_visible')
            admitted = set(receipt.admitted_binding_hashes)
            for row, binding in zip(rows, bindings[first_recall_binding:], strict=True):
                if row.get('memory_type') == 'procedure' and _binding_hash(binding) not in admitted:
                    raise ValueError('analysis_candidate_no_longer_visible')
            logger.info("memory.analysis_relation_applicability_admitted key=%s "
                        "attestation=%s fingerprints=%d admitted=%d",
                        snapshot.get('evidence_set_key'), receipt.attestation_hash,
                        receipt.fingerprint_count, len(receipt.admitted_binding_hashes))
        return None if procedure_applicability is None else observed.procedure_applicability

    @staticmethod
    def prompt(snapshot):
        return [{'candidate_key':r['candidate_key'], 'semantic':r['payload']} for r in snapshot['candidates']]

    @staticmethod
    def relation_prompt(snapshot):
        """Existing Procedure/Prospective endpoints, named only well enough to be picked."""
        rows = []
        for r in snapshot.get('relation_candidates', ()):
            payload = r['payload'] if isinstance(r.get('payload'), dict) else {}
            rows.append({'candidate_key': r['candidate_key'], 'memory_type': r['memory_type'],
                         'name': str(payload.get('name') or payload.get('action') or '')})
        return rows

    @staticmethod
    def relation_candidates(snapshot):
        return list(snapshot.get('relation_candidates', ()))

    async def authorize_plan(self, request, snapshot, plan):
        from simple_harness import MemoryActionAuthorityRef, issue_memory_action_authority
        await self.check(request, snapshot)
        operations = []
        for operation in plan.operations:
            if operation.kind.value == 'contest':
                await self._verify_contest(request, snapshot, operation)
                operations.append(operation)
                continue
            if operation.kind.value != 'revise':
                operations.append(operation)
                continue
            matches = [c for c in snapshot['candidates'] if c['memory_id'] == operation.target.memory_id
                       and c['revision'] == operation.target.revision]
            if len(matches) != 1 or operation.memory_type.value != 'semantic':
                raise ValueError('analysis_action_target_not_issued')
            candidate = matches[0]
            # Event AJ: the unique-target test is the intent count below, not a predicate
            # count.  A predicate is a free-form string the model minted when the memory was
            # written; two unrelated facts sharing one is a naming collision, not an
            # ambiguous correction, and the Host's own grammar (old-value template /
            # discriminating anchor) has already picked exactly one candidate for this
            # sentence.  Rejecting here left HM-TO-A6 attempt 12's memory at revision 1.
            if (tuple(operation.payload.qualifiers) != tuple(candidate['payload'].get('qualifiers', ()))
                or operation.proposed_privacy_class.value != candidate['privacy_class']
                or tuple(a.value for a in operation.proposed_information_attributes) != tuple(candidate['information_attributes'])):
                raise ValueError('analysis_action_classification_or_qualifiers_mismatch')
            approval = matches[0].get('correction_intent')
            if approval is None or (operation.payload.subject_entity, operation.payload.predicate) != (approval['subject_entity'], approval['predicate']):
                raise ValueError('analysis_explicit_correction_intent_missing')
            if approval['new_value'] is None:
                # cue+anchor grammar: the Host fixed the slot, the model supplies the
                # replacement, which must be a verbatim span of that same sentence and
                # must not merely restate the value the user just disowned.
                if (not operation.payload.object_value
                        or operation.payload.object_value not in approval['exact_quote']
                        or operation.payload.object_value in approval['old_value']
                        or approval['old_value'] in operation.payload.object_value):
                    raise ValueError('analysis_explicit_correction_new_value_unsupported')
            elif operation.payload.object_value != approval['new_value']:
                raise ValueError('analysis_explicit_correction_intent_missing')
            if sum(c.get('correction_intent') is not None and c['correction_intent']['evidence_id'] == approval['evidence_id'] and c['correction_intent']['exact_quote'] == approval['exact_quote'] for c in snapshot['candidates']) != 1:
                raise ValueError('analysis_action_target_ambiguous')
            from deskpet.memory.analysis_proposal import admitted_item, derive_span
            from simple_harness import EvidenceSupportKind
            if len(operation.evidence_spans) != 1 or not all(getattr(operation.evidence_spans[0], k) == approval[k] for k in ('evidence_id','envelope_hash')):
                raise ValueError('analysis_explicit_correction_source_mismatch')
            operation_quote = operation.evidence_spans[0].exact_quote
            # Template grammar keeps its frozen whole-sentence span; the cue+anchor
            # grammar allows any verbatim span *inside* the very sentence the Host
            # recognized as the correction, never another item and never a paraphrase.
            quote_ok = (operation_quote == approval['exact_quote'] if approval['new_value'] is not None
                        else bool(operation_quote) and operation_quote in approval['exact_quote'])
            if not quote_ok:
                raise ValueError('analysis_explicit_correction_source_mismatch')
            if not any(r.evidence_id == approval['evidence_id'] and r.content_hash == approval['envelope_hash'] for r in request.ordered_evidence_refs):
                raise ValueError('analysis_explicit_correction_source_not_current')
            envelope, receipt = await self._evidence.read_admitted(approval['evidence_id'])
            item = admitted_item(envelope, receipt)
            if envelope.subject != request.subject or explicit_correction_intent(candidate, (item,), snapshot['candidates']) != approval:
                raise ValueError('analysis_explicit_correction_intent_mismatch')
            expected_span = replace(derive_span(item, operation_quote, span_id=operation.evidence_spans[0].span_id),
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

    async def _verify_contest(self, request, snapshot, operation):
        """HM-S3 hedged contradiction → contested head.  No action authority is issued.

        CONTEST cannot destroy or rewrite anything (the SDK pins payload slot, lifecycle,
        epistemic, verification and valid-time to the incumbent and refuses an
        ``action_authority_ref``), so the Host verifies source binding, slot preservation
        and a current-USER hedge marker instead of deriving the challenger value itself.
        """
        from deskpet.memory.analysis_proposal import admitted_item, derive_span
        if operation.target is None or operation.memory_type.value != 'semantic':
            raise ValueError('analysis_contest_target_not_issued')
        matches = [c for c in snapshot['candidates'] if c['memory_id'] == operation.target.memory_id
                   and c['revision'] == operation.target.revision]
        if len(matches) != 1:
            raise ValueError('analysis_contest_target_not_issued')
        candidate = matches[0]
        old = candidate['payload']
        if ((operation.payload.subject_entity, operation.payload.predicate) != (old['subject_entity'], old['predicate'])
                or tuple(operation.payload.qualifiers) != tuple(old.get('qualifiers', ()))):
            raise ValueError('analysis_contest_slot_mismatch')
        if (operation.proposed_privacy_class.value != candidate['privacy_class']
                or tuple(a.value for a in operation.proposed_information_attributes) != tuple(candidate['information_attributes'])):
            raise ValueError('analysis_contest_classification_mismatch')
        if not operation.payload.object_value or operation.payload.object_value == old['object_value']:
            raise ValueError('analysis_contest_challenger_not_distinct')
        if operation.action_authority_ref is not None:
            raise ValueError('analysis_contest_authority_not_allowed')
        if len(operation.evidence_spans) != 1:
            raise ValueError('analysis_contest_source_mismatch')
        span = operation.evidence_spans[0]
        if not any(r.evidence_id == span.evidence_id and r.content_hash == span.envelope_hash
                   for r in request.ordered_evidence_refs):
            raise ValueError('analysis_contest_source_not_current')
        envelope, receipt = await self._evidence.read_admitted(span.evidence_id)
        if envelope.subject != request.subject or envelope.source_kind.value != 'user_message':
            raise ValueError('analysis_contest_source_mismatch')
        item = admitted_item(envelope, receipt)
        if hedged_contradiction_marker(item.text) is None:
            raise ValueError('analysis_contest_hedge_missing')
        if operation.payload.object_value not in str(item.text):
            raise ValueError('analysis_contest_challenger_not_quoted')
        if span != derive_span(item, span.exact_quote, span_id=span.span_id):
            raise ValueError('analysis_contest_span_mismatch')

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


# ---------------------------------------------------------------- anaphora grammar
#
# Incident L (HM-TO-A6 turn 15): 「记住：秋分资料整理这套校对流程，就按我前面说的 Python
# 环境执行。」 was stored as the semantic claim
# ``秋分资料整理校对流程 · execution_environment · "前面说的 Python 环境"``.  That value is
# not a fact, it is a dangling pointer: recall returns a phrase nobody can act on, and a
# later correction of the real value (turn 20, Python 3.13) can never supersede it because
# the two live in different slots.  The Host therefore refuses an anaphoric ``object_value``
# outright, and offers the model two legal shapes instead — resolve the reference to the
# exact value of an issued candidate (``object_value_candidate_key``), or state the link as
# an ``applies_to`` relation.  The marker list is bounded and Host-owned: it recognises
# *reference* phrases only, never ordinary values.
_ANAPHORA_MARKERS = (
    '前面说的', '前面提到的', '前面讲的', '前面那个', '上面说的', '上面提到的', '上面那个',
    '之前说的', '之前提到的', '之前那个', '先前说的', '先前提到的',
    '刚才说的', '刚刚说的', '刚才提到的', '刚说的', '我说过的', '说过的那个',
    '上述', '前述', '如前所述', '同上', '同前',
    'as mentioned', 'as i mentioned', 'aforementioned', 'previously mentioned',
    'mentioned earlier', 'mentioned above', 'said earlier', 'said before',
    'same as above', 'as above', 'as before', 'the one i mentioned', 'the one i said',
)


def anaphoric_reference_marker(text):
    """The longest reference marker in ``text``, else ``None``.

    Used twice: to reject an anaphoric ``object_value`` (a reference can never be a claim),
    and to require that a Host-verified reference resolution really answers a referring
    sentence rather than importing an unrelated candidate's value.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    lowered = text.lower()
    markers = [marker for marker in _ANAPHORA_MARKERS if marker in lowered]
    if not markers:
        return None
    return sorted(markers, key=lambda marker: (-len(marker), marker))[0]
