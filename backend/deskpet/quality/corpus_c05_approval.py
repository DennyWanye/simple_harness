"""Exact setup decisions only; never the scoring or generic memory approver."""
from pathlib import Path
from simple_harness import thaw_json
from deskpet.quality.corpus_approval import CorpusApprovalBlocked
from deskpet.quality.corpus_trace import digest


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
        receipt = await self.binding_store.verify_route_receipt(route)
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
            effect_id = request.get('effect_id')
            if not isinstance(effect_id, str) or not effect_id:
                raise CorpusApprovalBlocked('c05_setup_effect_identity_missing')
            _, (effect,) = self.stack.read_primary_dependency_facts(sdk, (effect_id,))
            if (effect is None or effect.run_id.value != sdk or effect.effect_id.value != effect_id
                    or effect.call_id.value != request.get('call_id')
                    or effect.tool_name != request.get('tool_name')
                    or thaw_json(effect.arguments) != request.get('arguments')
                    or not effect.raw_call_id):
                raise CorpusApprovalBlocked('c05_setup_public_effect_differs')
            planned = self.transport.planned_calls.get(effect.raw_call_id)
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
                effect_id=effect_id, internal_call_id=effect.call_id.value, raw_call_id=effect.raw_call_id)))
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
