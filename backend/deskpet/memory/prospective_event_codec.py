"""Event journal codec, not a publication authority or a receipt authenticator.

A configured source must read actual SDK effects and Host bindings before returning
confirmation. No production source is installed by this module.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Protocol

from simple_harness.runtime import ProspectiveSignalAuthority
from deskpet.task_scope.protocol import canonical_hash

DOMAIN = 'host:prospective-event/v1'


@dataclass(frozen=True)
class PreparedEvent:
    authority: ProspectiveSignalAuthority
    observation: dict


@dataclass(frozen=True)
class EventRead:
    status: str
    reason: str
    confirmation: dict | None = None
    causal_cut: dict | None = None

    def __post_init__(self):
        if self.status not in {'confirmed', 'not_observed', 'unverifiable'} or not self.reason:
            raise ValueError('prospective_event_read_status_invalid')
        if self.status != 'confirmed' and (self.confirmation is not None or self.causal_cut is not None):
            raise ValueError('prospective_event_unconfirmed_payload')


def check_cut(value):
    # This codec validates commitments, not existence. Only a production source
    # adapter that reads the authoritative Host journal can authenticate a cut.
    fields = {'kind', 'namespace', 'registration_authority_hash', 'cut_sequence',
              'admission_sequence', 'cut_record_hash', 'admission_record_hash'}
    if type(value) is not dict or set(value) != fields or value['kind'] != 'host_effect_admission/v1':
        raise ValueError('prospective_event_cut_shape_invalid')
    if (type(value['namespace']) is not str or not value['namespace']
            or type(value['cut_sequence']) is not int or value['cut_sequence'] < 0
            or type(value['admission_sequence']) is not int
            or value['admission_sequence'] <= value['cut_sequence']):
        raise ValueError('prospective_event_cut_order_invalid')
    for key in ('registration_authority_hash', 'cut_record_hash', 'admission_record_hash'):
        if type(value[key]) is not str or re.fullmatch('[0-9a-f]{64}', value[key]) is None:
            raise ValueError('prospective_event_cut_hash_invalid')
    return dict(value)


class ConfirmedEventReader(Protocol):
    async def read_confirmed_event(self, *, principal, registration) -> EventRead:
        """Return explicit confirmed / not_observed / unverifiable status.

        Implementations MUST read actual SDK effect Run/id/arguments/result through
        public ports, validate Host principal/project binding and concrete destination
        confirmation. A Host cut must have been captured with registration ACK, before
        the event admission in the SAME authoritative journal; neither publisher
        arguments nor timestamps are modified for reminders. Unknown/pending,
        partial pages, missing bindings and historical confirmations are not success.
        The returned dictionary is the confirmation shape checked below. This port
        is trusted composition, never a model/tool/user supplied receipt parser.
        """
        ...


def event_signal_id(owner, intent):
    # One first occurrence per exact registration; different confirmations cannot
    # create a second signal for the same pending trigger.
    return canonical_hash([DOMAIN, owner, intent.scheduler_registration_ref,
        intent.registration_revision, intent.target_memory_id,
        intent.target_revision, intent.trigger_hash])


def check_confirmation(value):
    fields = {'sdk_run_id', 'effect_id', 'raw_call_id', 'tool_name',
        'arguments_hash', 'result_hash',
        'event_authority_ref', 'condition_hash', 'destination_id',
        'configuration_hash', 'artifact_hash', 'confirmation_id', 'confirmation_hash'}
    if type(value) is not dict or set(value) != fields:
        raise ValueError('prospective_event_confirmation_shape_invalid')
    for key, item in value.items():
        if type(item) is not str or not item.strip():
            raise ValueError('prospective_event_confirmation_field_invalid')
        if key.endswith('_hash') and re.fullmatch('[0-9a-f]{64}', item) is None:
            raise ValueError('prospective_event_confirmation_hash_invalid')
    return dict(value)


def check_event_observation(authority, observation, owner):
    i = authority.intent
    if (i.signal_kind.value != 'event_occurred'
            or i.trigger.to_json()['trigger_kind'] != 'event'
            or i.transition_from.value != 'pending' or i.transition_to.value != 'triggered'
            or i.outbox_id is not None or i.outbox_payload_hash is not None
            or i.signal_id != event_signal_id(owner, i)
            or authority.issuer_ref != DOMAIN
            or authority.authority_id != 'host:event-authority:' + i.signal_id):
        raise ValueError('prospective_event_authority_invalid')
    if type(observation) is not dict or type(observation.get('schema_version')) is not int:
        raise ValueError('prospective_event_observation_version_invalid')
    confirmation = check_confirmation(observation.get('confirmation'))
    cut = check_cut(observation.get('causal_cut'))
    if (confirmation['event_authority_ref'] != i.trigger.event_authority_ref
            or confirmation['condition_hash'] != i.trigger.condition_hash):
        raise ValueError('prospective_event_confirmation_domain_differs')
    expected = dict(schema_version=1, kind='host_event_observation', owner_key=owner,
        subject=i.subject, memory_id=i.target_memory_id, target_revision=i.target_revision,
        scheduler_registration_ref=i.scheduler_registration_ref,
        registration_revision=i.registration_revision, trigger_hash=i.trigger_hash,
        observed_at=i.observed_at, run_id=i.run_id, operation_id=i.operation_id,
        confirmation=confirmation, causal_cut=cut)
    if (observation != expected or not math.isfinite(i.observed_at)
            or i.signal_receipt_hash != canonical_hash(expected)
            or i.signal_receipt_id != 'host:event-observation:' + canonical_hash(expected)):
        raise ValueError('prospective_event_observation_binding_differs')
    return expected
