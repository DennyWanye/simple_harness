"""Foreground and orchestration bind one pool, with conservative foreground weight."""
import json

import pytest

from deskpet.orchestration.local_capacity import bind_local_capacity, bind_selected_local_capacity
from simple_harness import RequestId, Message, MessageRole
from simple_harness.providers import ProviderRequest


class Provider:
    target = object()


@pytest.fixture
def profile(tmp_path):
    path = tmp_path / 'profile.json'
    path.write_text(json.dumps({'base_url': 'http://127.0.0.1:11434/v1',
                                'model': 'local-test', 'max_total_tokens': 262144}))
    return path


def test_foreground_and_orchestration_share_pool_and_endpoint_identity(profile):
    provider = Provider()
    class Counter:
        fingerprint = "fixture-counter-v1"
        def estimate_input_tokens(self, request):
            return 160000
    kwargs = {'base_url': 'http://127.0.0.1:11434/v1', 'model': 'local-test'}
    chat = bind_local_capacity(provider, profile, **kwargs)
    orch = bind_local_capacity(provider, profile, counter=Counter(), **kwargs)
    assert chat.target is orch.target is provider.target
    assert chat.deployment_capacity.ledger.path == orch.deployment_capacity.ledger.path
    assert chat.deployment_capacity.ledger.pool_id == orch.deployment_capacity.ledger.pool_id
    request = ProviderRequest(RequestId('r'), (Message(MessageRole.USER, 'hello'),), max_output_tokens=1024)
    assert chat.deployment_capacity.estimate(request) == 262144
    assert orch.deployment_capacity.estimate(request) == 161024
    snapshot = orch.deployment_capacity.ledger.snapshot()
    assert (snapshot.max_slots, snapshot.max_tokens) == (2, 393216)


def test_only_selected_local_endpoint_gets_capacity(profile, tmp_path):
    config = tmp_path / 'config.toml'
    config.write_text('[orchestration]\nlocal_model_profile=' + json.dumps(str(profile)))
    provider = Provider()
    other = bind_selected_local_capacity(provider, config, base_url='https://example.org/v1',
                                         model='cloud')
    assert other is provider
    local = bind_selected_local_capacity(provider, config, base_url='http://127.0.0.1:11434/v1',
                                         model='local-test')
    assert local is not provider and local.provider is provider


def test_mismatched_model_and_unbounded_request_are_rejected(profile):
    with pytest.raises(ValueError, match='differs'):
        bind_local_capacity(Provider(), profile, base_url='http://127.0.0.1:11434/v1', model='wrong')
    bound = bind_local_capacity(Provider(), profile, base_url='http://127.0.0.1:11434/v1',
                                model='local-test')
    with pytest.raises(ValueError, match='output'):
        bound.deployment_capacity.estimate(ProviderRequest(RequestId('r'), (Message(MessageRole.USER, 'hello'),)))


def test_repeated_binding_is_idempotent_and_conflicting_counter_rejected(profile):
    kwargs = {'base_url': 'http://127.0.0.1:11434/v1', 'model': 'local-test'}
    first = bind_local_capacity(Provider(), profile, **kwargs)
    second = bind_local_capacity(first, profile, **kwargs)
    assert second is first
    class Counter:
        fingerprint = 'different-counter-v1'
    with pytest.raises(ValueError, match='different local capacity'):
        bind_local_capacity(first, profile, counter=Counter(), **kwargs)
    assert first.deployment_capacity.ledger.snapshot().held_slots == 0
