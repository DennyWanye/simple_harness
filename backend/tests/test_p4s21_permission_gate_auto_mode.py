# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""PermissionGate auto_mode short-circuit.

When ``auto_mode == True`` the gate returns ALLOW for every request
that passes host validation, without consulting cache or the responder.
"""

from __future__ import annotations

import pytest
from deskpet.permissions.gate import PermissionGate, PermissionGateConfig

# ---------------------------------------------------------------------------
# auto_mode = True → ALLOW everywhere
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_mode_allows_shell_without_responder():
    gate = PermissionGate()
    gate.auto_mode = True
    # Note: no responder set, no cache pre-populated, no deny pattern
    # match. If auto_mode didn't short-circuit we'd get a timeout-deny.
    decision = await gate.check("shell", {"command": "rm -rf /"}, session_id="test")
    assert decision.allow is True
    assert decision.source == "auto-mode"


@pytest.mark.asyncio
async def test_auto_mode_does_not_bypass_deny_patterns():
    """Auto removes approval waits, not host validation and policy guards."""
    gate = PermissionGate(
        config=PermissionGateConfig(
            shell_deny_patterns=["rm -rf"],
        )
    )
    gate.auto_mode = True
    decision = await gate.check("shell", {"command": "rm -rf /etc"}, session_id="test")
    assert decision.allow is False
    assert decision.source == "config-deny"
    assert decision.pattern == "rm -rf"


@pytest.mark.asyncio
async def test_auto_mode_off_default_falls_through_to_existing_layers():
    """Default behavior (auto_mode = False) must be unchanged — no
    accidental opt-in."""
    gate = PermissionGate()
    assert gate.auto_mode is False
    # default-allow categories still allow without responder
    decision = await gate.check("read_file", {"path": "C:/temp/foo.txt"}, session_id="test")
    assert decision.allow is True
    assert decision.source == "default-allow"


# ---------------------------------------------------------------------------
# auto_mode toggle is per-instance, not class-level
# ---------------------------------------------------------------------------


def test_auto_mode_state_is_isolated_between_instances():
    a = PermissionGate()
    b = PermissionGate()
    a.auto_mode = True
    assert b.auto_mode is False  # not infected
