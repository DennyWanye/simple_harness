# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""NEXT-TG-1.0 §9 "多任务并发": raising the model-call slot count keeps a library running.

The physical slot count is capacity, not the accounting identity of one frozen request:
the same estimator / protocol account a request the same way whatever the slot count.  2026-09-28 a Host raised it from 1 to 2 and the orchestration service refused to
start ("provider admission identity differs from a persisted intent"), because the slot
count was hashed into every frozen intent.
"""

from __future__ import annotations

from agent_orchestrator.runtime.provider_budget_guard import ProviderBudgetGuard


class Counter:
    """一个固定的测试计数器（原从已删的 ``test_provider_budget_guard`` 导入，2026-10-03 搬进来）。"""

    fingerprint = "fixture-text-count-v1"
    bound_protocol = "fixture-text-only-v1"
    requires_prior_output_reserve = True

    def __init__(self, tokens):
        self.tokens = tokens

    def estimate_input_tokens(self, request):
        return self.tokens


def test_the_slot_count_is_capacity_not_the_frozen_request_identity():
    one = ProviderBudgetGuard(_Commit(), owner="o", estimator=Counter(1), max_slots=1)
    two = ProviderBudgetGuard(_Commit(), owner="o", estimator=Counter(1), max_slots=2, profile_slots={"default": 2})
    assert one.fingerprint == two.fingerprint and two.accepts(one.fingerprint)
    # v4 accounts tokens only; it is the one identity accepted (no earlier version is)
    assert two.fingerprint.startswith("provider-budget-admission-v4:")
    assert not two.accepts("provider-budget-admission-v3:" + two.fingerprint.split(":", 1)[1])
    assert not two.accepts("provider-budget-admission-v2:" + "0" * 64)
    other = ProviderBudgetGuard(_Commit(), owner="o", estimator=_OtherCounter(1), max_slots=2)
    assert other.fingerprint != two.fingerprint and not other.accepts(two.fingerprint)


class _Commit:
    store = None


class _OtherCounter(Counter):
    fingerprint = "fixture-text-count-v2"
