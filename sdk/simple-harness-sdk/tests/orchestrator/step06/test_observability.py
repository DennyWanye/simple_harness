# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501

"""Step 6 · S6-09 (D6-9 / D6-10 / D6-11): the evidence carries no credential (a file
that would is refused, a model package that would is refused), every Result can be
traced to its prompt / model / retrieval / allocator / verifier versions under the
Mission's trace id, the metrics are filled only with what the records hold, and a
required verifier that is not deployed blocks instead of passing or retrying."""

from __future__ import annotations

import pytest

from agent_orchestrator.context.context_builder import assert_no_secrets
from agent_orchestrator.observability.secrets import find_secrets

FAKE_KEY = "sk-" + "a1b2c3d4e5f6g7h8i9j0k1l2"  # shaped like a key, not a key


def test_s6_09_a_model_package_carrying_a_credential_value_is_refused():
    assert find_secrets(f"token={FAKE_KEY}") == ["api_key_sk"]
    assert find_secrets("Authorization: Bearer abcdefghijklmnop0123") == ["bearer_token"]
    assert find_secrets("ordinary text sk-short") == []
    with pytest.raises(ValueError, match="credential-like value"):
        assert_no_secrets({"task": {"notes": [f"use {FAKE_KEY}"]}})
    with pytest.raises(ValueError, match="credential-like field"):
        assert_no_secrets({"api_key": "x"})
    assert_no_secrets({"task": {"notes": ["nothing secret here"]}})


# ------------------------------------------------------------------ D6-9' verifier routing
