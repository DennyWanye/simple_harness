# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Test-side alias of the shipped fixture provider (``agent_orchestrator.testing.fixtures``)."""

from agent_orchestrator.testing.fixtures import (  # noqa: F401
    MODEL,
    RoleScriptedProvider,
    UnknownAfterHandoff,
    critic_step,
    envelope_step,
    package_of,
    role_of,
)
