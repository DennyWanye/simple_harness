# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""Same-machine capacity for the configured local 256K deployment."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit


def bind_local_capacity(provider, profile_path, *, base_url, model, counter=None):
    from simple_harness.execution.deployment_capacity import CapacityProvider, DeploymentCapacity
    from simple_harness.execution.shared_capacity import CapacityLedger

    path = Path(profile_path)
    if not path.is_absolute():
        raise ValueError('local capacity profile must be absolute')
    path = path.resolve(strict=True)
    raw = json.loads(path.read_text(encoding='utf-8'))
    if (raw['base_url'].rstrip('/') != base_url.rstrip('/') or raw['model'] != model
            or type(raw['max_total_tokens']) is not int or raw['max_total_tokens'] != 262144):
        raise ValueError('local capacity profile differs from selected deployment')
    endpoint = urlsplit(base_url)
    if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError('local capacity endpoint contains non-public identity')
    # Model aliases on one service must not create independent physical pools.
    port = endpoint.port or (443 if endpoint.scheme == 'https' else 80)
    pool = sha256(f'{endpoint.scheme}://{endpoint.hostname}:{port}'.encode()).hexdigest()
    counter_identity = 'server-context-262144' if counter is None else counter.fingerprint
    if not isinstance(counter_identity, str) or not counter_identity:
        raise ValueError('local capacity counter requires a stable fingerprint')
    identity = (str(path), pool, model, counter_identity)
    existing = getattr(provider, 'deployment_capacity', None)
    if existing is not None:
        if getattr(existing, 'local_binding_identity', None) == identity:
            return provider
        raise ValueError('provider already has a different local capacity binding')
    ledger = CapacityLedger(path.parent / 'capacity-v1.sqlite3', pool_id=pool,
                            max_slots=2, max_tokens=393216)

    def weight(request):
        output = request.max_output_tokens
        if type(output) is not int or not 0 < output <= 262144:
            raise ValueError('local capacity requires a positive bounded output cap')
        if counter is None:
            # Foreground uses a different payload restorer. Reserve its entire
            # server context instead of claiming that an unbound tokenizer is exact.
            return 262144
        total = counter.estimate_input_tokens(request) + output
        if total > 262144:
            raise ValueError('request exceeds configured local context')
        return total

    capacity = DeploymentCapacity(ledger, estimate=weight)
    capacity.local_binding_identity = identity
    return CapacityProvider(provider, capacity)


def bind_selected_local_capacity(provider, config_path, *, base_url, model):
    from .wiring import read_section
    profile_path = read_section(config_path).get('local_model_profile')
    if not profile_path:
        return provider
    raw = json.loads(Path(profile_path).read_text(encoding='utf-8'))
    if raw['base_url'].rstrip('/') != base_url.rstrip('/'):
        return provider
    return bind_local_capacity(provider, profile_path, base_url=base_url, model=model)
