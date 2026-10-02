# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""NEXT-TG-1.0 §9 "多任务并发": raising the model-call slot count keeps a library running.

The physical slot count is capacity, not the accounting identity of one frozen request:
the same estimator / protocol / prices account a request the same way whatever the slot
count.  2026-09-28 a Host raised it from 1 to 2 and the orchestration service refused to
start ("provider admission identity differs from a persisted intent"), because the slot
count was hashed into every frozen intent.
"""

from __future__ import annotations

from test_provider_budget_guard import Counter

from agent_orchestrator.runtime.provider_budget_guard import ProviderBudgetGuard


def test_the_slot_count_is_capacity_not_the_frozen_request_identity():
    one = ProviderBudgetGuard(_Commit(), owner="o", estimator=Counter(1), max_slots=1)
    two = ProviderBudgetGuard(_Commit(), owner="o", estimator=Counter(1), max_slots=2, profile_slots={"default": 2})
    assert one.fingerprint == two.fingerprint
    # a request frozen by an earlier build (the slot count was hashed in) is still this identity
    # byte-exact: the identity opt.84 froze for this estimator under one slot
    legacy = one.legacy_fingerprint(max_slots=1, profile_slots=None)
    assert legacy == "provider-budget-admission-v2:1c1ed19809e428fe86e141ada28e08017d7113cc9123622624a5e857f59f50a8"
    assert two.accepts(legacy)
    assert not two.accepts("provider-budget-admission-v2:" + "0" * 64)
    other = ProviderBudgetGuard(_Commit(), owner="o", estimator=_OtherCounter(1), max_slots=2)
    assert other.fingerprint != two.fingerprint and not other.accepts(one.legacy_fingerprint(max_slots=1, profile_slots=None))


class _Commit:
    store = None


class _OtherCounter(Counter):
    fingerprint = "fixture-text-count-v2"
