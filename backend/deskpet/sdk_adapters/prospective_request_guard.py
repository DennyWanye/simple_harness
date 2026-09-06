"""Fresh occurrence disclosure check before ProductProviderAdapter delegates.

There is a check-to-send interval, not a cross-database atomic revocation fence.
"""
import json
from simple_harness.providers.errors import ProviderRequestRejectedError
from simple_harness.execution.provider_invocations import provider_request_fingerprint
from deskpet.memory.prospective_occurrence import presentation_revisions
from deskpet.sdk_adapters.context_authority import _pending_occurrence_message


class ProspectiveRequestRejected(ProviderRequestRejectedError):
    pass


class ProspectiveRequestGuard:
    def __init__(self, *, sdk_run_id, coordinator, read_provider_context_use):
        self.run=sdk_run_id
        self.coordinator=coordinator
        self.read=read_provider_context_use

    async def __call__(self, request):
        try:
            view=self.read(self.run,request.request_id.value)
            if (view is None or view.run_id!=self.run or view.provider_request_id!=request.request_id.value
                    or view.subject!=self.coordinator.store.principal.actor_id
                    or view.invocation_state!='handed_off' or view.handoff_attempt!=view.handoff_ordinal
                    or view.request_fingerprint!=provider_request_fingerprint(request)):
                raise ValueError('s5c_occurrence_actual_handoff_missing')
            async with self.coordinator.store._transaction() as db:
                async with db.execute('SELECT * FROM run_context_snapshot_receipts WHERE snapshot_id=?',
                                      (view.context_snapshot_id,)) as q:
                    snapshot=await q.fetchone()
                if (snapshot is None or snapshot['sdk_run_id']!=self.run
                        or snapshot['snapshot_revision']!=view.context_snapshot_revision
                        or snapshot['expected_request_fingerprint']!=view.request_fingerprint):
                    raise ValueError('s5c_occurrence_actual_snapshot_missing')
                ordinal,prior=snapshot['provider_turn_ordinal'],snapshot['prior_context_revision']
                revisions=json.loads(snapshot['source_revisions_json'])
            group=await self.coordinator.restore_snapshot(sdk_run_id=self.run,provider_turn_ordinal=ordinal,
                prior_context_revision=prior,snapshot_id=view.context_snapshot_id)
            if group is None or any(revisions.get(k)!=v for k,v in presentation_revisions(group).items()):
                raise ValueError('s5c_occurrence_actual_group_differs')
            messages=[message for message in request.messages if message.metadata.get('source')=='prospective_inbox']
            expected=[]
            if group.items:
                expected=[_pending_occurrence_message(tuple(item.entry for item in group.items),
                    overdue_keys=frozenset(item.entry.occurrence_key for item in group.items if item.overdue))]
            if messages!=expected:
                raise ValueError('s5c_occurrence_actual_message_differs')
            await self.coordinator.recheck(group)
        except (ValueError,TypeError,KeyError,AttributeError) as error:
            raise ProspectiveRequestRejected('s5c_occurrence_preflight_rejected') from error
