from __future__ import annotations

import pytest

from agent.context_messages import ProviderAttemptOptions


def test_session_auxiliary_attempt_requires_root_identity():
    with pytest.raises(ValueError, match="session_id/root_run_id"):
        ProviderAttemptOptions(
            workload_class="session-auxiliary",
            session_id="session-a",
            callsite_id="agent.goal_check",
        )


def test_session_auxiliary_attempt_carries_full_bottom_seam_identity():
    options = ProviderAttemptOptions(
        purpose="supervisor",
        workload_class="session-auxiliary",
        callsite_id="agent.goal_check",
        session_id="session-a",
        root_run_id="root-a",
        request_id="request-a",
        attempt_id="aux:call-a",
    )
    assert options.root_run_id == "root-a"
    assert options.workload_class == "session-auxiliary"
    assert options.callsite_id == "agent.goal_check"
    assert options.detached is False


def test_maintenance_attempt_cannot_claim_session_identity():
    with pytest.raises(ValueError, match="without Session/Root"):
        ProviderAttemptOptions(
            workload_class="system-maintenance",
            callsite_id="memory.reflection",
            detached=True,
            session_id="session-a",
        )
