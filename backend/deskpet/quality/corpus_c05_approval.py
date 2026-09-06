"""Exact setup decisions only; never the scoring or generic memory approver."""
from pathlib import Path
from dataclasses import dataclass
from simple_harness import thaw_json
from deskpet.quality.corpus_approval import CorpusApprovalBlocked
from deskpet.quality.corpus_trace import digest


@dataclass(frozen=True)
class PendingCallProof:
    sdk_run_id: str
    effect_id: str
    internal_call_id: str
    raw_call_id: str
    tool_name: str
    arguments: object
    waiting_source_ref: str
    waiting_source_hash: str
    provider_invocation_ref: str
    provider_response_hash: str


async def verify_pending_call(*, stack, ingress, sdk_run_id, decision_id, request):
    """Bind a real OPEN pre-effect decision to public audit + Provider response.

    REQUIRE_USER happens before prepare_effect; absence of EffectRecord here is
    expected, not evidence of a fabricated effect or a reason to derive IDs.
    """
    from simple_harness.execution.audit import audit_hash, audit_reference
    from simple_harness.execution.provider_invocations import provider_response_from_json
    original = ingress.read_authorization_decision(run_id=sdk_run_id, decision_id=decision_id)
    if (original is None or str(original.run_id) != sdk_run_id or original.state.value != 'open'
            or original.kind != 'tool_authorization' or thaw_json(original.request) != request):
        raise CorpusApprovalBlocked('c05_pending_decision_differs')
    if not all(isinstance(request.get(k), str) and request[k] for k in ('call_id', 'effect_id', 'tool_name')):
        raise CorpusApprovalBlocked('c05_pending_call_identity_missing')
    trace = stack.read_corpus_scoring_trace(sdk_run_id)
    audit = trace.get('operation_audit')
    if (trace.get('sdk_run_id') != sdk_run_id or trace.get('observation_errors', {}).get('providers')
            or not isinstance(audit, dict) or audit.get('run_id') != sdk_run_id or audit.get('truncated') is not False):
        raise CorpusApprovalBlocked('c05_pending_public_audit_unavailable')
    waiting = [op for op in audit['operations'] if op['kind'] == 'tool'
        and op['record_type'] == 'boundary' and op['state'] == 'waiting'
        and op['effect_id'] == audit_reference('effect', request['effect_id'])]
    if len(waiting) != 1:
        raise CorpusApprovalBlocked('c05_pending_waiting_fact_ambiguous')
    fact = waiting[0]
    if (fact['call_id'] != audit_reference('call', request['call_id'])
            or fact['operation_name_hash'] != audit_hash(request['tool_name'])
            or type(fact['turn_ordinal']) is not int or type(fact['call_ordinal']) is not int):
        raise CorpusApprovalBlocked('c05_pending_intent_differs')
    requested = [op for op in audit['operations'] if op['kind'] == 'tool'
        and op['record_type'] == 'boundary' and op['state'] == 'requested'
        and op['effect_id'] == fact['effect_id']]
    if (len(requested) != 1 or requested[0]['source_version'] >= fact['source_version']
            or any(requested[0][key] != fact[key] for key in
                ('call_id', 'effect_id', 'operation_name', 'operation_name_hash', 'raw_call_id_hash',
                 'turn_ordinal', 'call_ordinal'))):
        raise CorpusApprovalBlocked('c05_pending_requested_waiting_differs')
    proposals = [op for op in audit['operations'] if op['kind'] == 'tool'
        and op['record_type'] == 'proposal' and op['state'] == 'proposed'
        and op['turn_ordinal'] == fact['turn_ordinal'] and op['call_ordinal'] == fact['call_ordinal']
        and op['raw_call_id_hash'] == fact['raw_call_id_hash']]
    if len(proposals) != 1:
        raise CorpusApprovalBlocked('c05_pending_proposal_ambiguous')
    proposal = proposals[0]
    providers = [row for row in trace['providers']
        if audit_reference('provider', row['invocation_id']) == proposal['provider_invocation_id']]
    if len(providers) != 1 or providers[0]['response_json'] is None or providers[0]['state'] != 'succeeded':
        raise CorpusApprovalBlocked('c05_pending_provider_response_missing')
    provider = providers[0]
    response_json = provider['response_json']
    # corpus_trace.wire serializes the public RequestId dataclass as {value}.
    request_identity = provider['request_id']
    if not isinstance(request_identity, dict) or set(request_identity) != {'value'}:
        raise CorpusApprovalBlocked('c05_pending_provider_request_identity_invalid')
    provider_request_id = request_identity['value']
    if (audit_hash(response_json) != proposal['source_hash']
            or audit_reference('request', provider_request_id) != proposal['request_id']):
        raise CorpusApprovalBlocked('c05_pending_response_binding_differs')
    response = provider_response_from_json(response_json)
    if response.request_id.value != provider_request_id:
        raise CorpusApprovalBlocked('c05_pending_response_request_differs')
    ordinal = fact['call_ordinal']
    if not 0 <= ordinal < len(response.tool_calls):
        raise CorpusApprovalBlocked('c05_pending_response_call_missing')
    call = response.tool_calls[ordinal]
    if (audit_hash([sdk_run_id, fact['turn_ordinal'], ordinal, audit_hash(call.call_id.value)]) != fact['raw_call_id_hash']
            or call.name != request['tool_name'] or thaw_json(call.arguments) != request['arguments']):
        raise CorpusApprovalBlocked('c05_pending_raw_call_differs')
    # The boundary request_hash is not exported by H0710. Actual decision and
    # actual Provider response bind complete arguments directly above; audit
    # proves the exact waiting call association, not an unavailable intent hash.
    if ingress.read_authorization_decision(run_id=sdk_run_id, decision_id=decision_id) != original:
        raise CorpusApprovalBlocked('c05_pending_decision_changed')
    return PendingCallProof(sdk_run_id, request['effect_id'], request['call_id'], call.call_id.value,
        call.name, thaw_json(call.arguments), fact['source_id'], fact['source_hash'],
        proposal['provider_invocation_id'], proposal['source_hash'])


class C05SetupApproval:
    def __init__(self, *, ingress, stack, transport, ledger, binding_store, expected_configured_root, persist):
        self.ingress, self.transport = ingress, transport
        self.stack = stack
        self.ledger, self.binding_store = ledger, binding_store
        self.expected_root = Path(expected_configured_root).resolve(strict=True)
        self.persist = persist
        self.attempted = set()
        self.ordinal = 0

    async def _scope(self, sdk, name, args):
        configured = Path(self.binding_store.configured_root().canonical_path)
        if configured != self.expected_root:
            raise CorpusApprovalBlocked('c05_configured_root_differs')
        if name == 'context_route':
            if args.get('route') != 'create_new':
                raise CorpusApprovalBlocked('c05_initial_phase_route_not_supported')
            return
        if name not in {'tool_search', 'tool_describe', 'tool_activate', 'write_file', 'task_scope_update'}:
            raise CorpusApprovalBlocked('c05_setup_tool_not_supported')
        latest = await self.ledger.latest_route_decision_for_run(sdk, task_only=True)
        if latest is None:
            raise CorpusApprovalBlocked('c05_setup_scope_missing')
        route = await self.ledger.read_route_receipt(sdk, latest['receipt_id'])
        if route is None:
            raise CorpusApprovalBlocked('c05_setup_route_missing')
        if (route.task_scope_id is None or route.binding_set_revision is None
                or route.binding_set_receipt_id is None or route.binding_set_receipt_hash is None):
            raise CorpusApprovalBlocked('c05_setup_route_binding_missing')
        receipt = await self.binding_store.exact_receipt(task_scope_id=route.task_scope_id,
            binding_set_revision=route.binding_set_revision,
            binding_set_receipt_id=route.binding_set_receipt_id,
            binding_set_receipt_hash=route.binding_set_receipt_hash)
        if len(receipt.root_identity_hashes) != 1:
            raise CorpusApprovalBlocked('c05_setup_root_ambiguous')
        root = await self.binding_store.verify_effect_authority(task_scope_id=route.task_scope_id,
            binding_set_revision=receipt.binding_set_revision, binding_set_receipt_id=receipt.receipt_id,
            binding_set_receipt_hash=receipt.receipt_hash, root_identity_hash=receipt.root_identity_hashes[0])
        physical = Path(root.root.canonical_path)
        if physical == self.expected_root or not physical.is_relative_to(self.expected_root):
            raise CorpusApprovalBlocked('c05_setup_root_outside_fixture')
        if name == 'write_file':
            target = (physical / args['path']).resolve()
            if not target.is_relative_to(physical) or target.parent != physical:
                raise CorpusApprovalBlocked('c05_marker_target_outside_exact_scope')

    async def __call__(self, *, service, queued):
        if self.transport.queued is None or self.transport.queued['turn_ref'] != queued['turn_ref']:
            raise CorpusApprovalBlocked('c05_setup_queue_differs')
        state = await service.read_primary_state(request_id='c05-setup-approval-state')
        current = state.get('current_run')
        if current is None:
            return False
        sdk = current['sdk_run_ref']
        status = self.ingress.query(sdk).state.value
        if status in {'completed', 'failed', 'cancelled', 'stopped'}:
            return False
        if status != 'waiting':
            raise CorpusApprovalBlocked('c05_setup_not_decision_waiting')
        target = dict(primary_ref=state['primary_ref'], expected_run_ref=current['run_ref'],
                      expected_generation=current['generation'])
        listed = await service.list_primary_decisions(request_id='c05-setup-decisions', **target)
        public = {v.decision_id: v for v in self.ingress.list_open_authorizations(
            run_id=sdk, session_id=current['execution_session_ref'])}
        if not listed['pending']:
            raise CorpusApprovalBlocked('c05_setup_no_tool_decision')
        verified = []
        for item in listed['pending']:
            identifier = item['decision_id']
            projected = public.get(identifier)
            record = self.ingress.read_authorization_decision(run_id=sdk, decision_id=identifier)
            if (projected is None or projected.turn_id != queued['turn_ref']
                    or projected.sdk_run_id != sdk or projected.run_id != current['run_ref']
                    or record is None or str(record.run_id) != sdk
                    or record.kind != 'tool_authorization' or record.state.value != 'open'
                    or record.version != item['version']):
                raise CorpusApprovalBlocked('c05_setup_exact_identity_differs')
            request = thaw_json(record.request)
            proof = await verify_pending_call(stack=self.stack, ingress=self.ingress,
                sdk_run_id=sdk, decision_id=identifier, request=request)
            planned = self.transport.planned_calls.get(proof.raw_call_id)
            if (request.get('nonce') != item['nonce'] or planned is None
                    or planned['turn_ref'] != queued['turn_ref']
                    or planned['name'] != request.get('tool_name')
                    or planned['arguments'] != request.get('arguments')
                    or identifier in self.attempted or len(self.attempted) >= 50):
                raise CorpusApprovalBlocked('c05_setup_exact_action_differs')
            await self._scope(sdk, planned['name'], planned['arguments'])
            verified.append((item, dict(sdk_run_ref=sdk, turn_ref=queued['turn_ref'],
                decision_id=identifier, version=record.version, request_hash=digest(request),
                actual_fixture_wire_hash=planned['wire_request_hash'], tool_name=planned['name'],
                effect_id=proof.effect_id, internal_call_id=proof.internal_call_id, raw_call_id=proof.raw_call_id,
                waiting_source_ref=proof.waiting_source_ref, waiting_source_hash=proof.waiting_source_hash)))
        # Check the whole batch before the first response; no unknown allallow.
        for item, fact in verified:
            self.ordinal += 1
            self.attempted.add(item['decision_id'])
            self.persist(f'c05-approval-{self.ordinal:03d}-attempt', fact)
            response = await service.respond_primary_decision(request_id='c05-setup-response', **target,
                decision_id=item['decision_id'], nonce=item['nonce'], version=item['version'], decision='allow')
            if response['outcome'] != 'allowed' or response['duplicate']:
                raise CorpusApprovalBlocked('c05_setup_response_unconfirmed')
            self.persist(f'c05-approval-{self.ordinal:03d}-ack', dict(**fact, response=response))
        return True
