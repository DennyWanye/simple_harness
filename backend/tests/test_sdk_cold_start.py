# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Cold-start regression: a fresh install has no LLM provider configured, and
``get_chain()`` raises ``NoProviderConfiguredError`` in that state. The SDK
runtime activation must skip gracefully instead of crashing the whole app
startup (lifespan). Found by the COLD-1 clean-userdata gate on 2026-08-19.
"""

from __future__ import annotations

import main
from llm.provider_registry import NoProviderConfiguredError


class _RaisingRegistry:
    def get_chain(self):
        raise NoProviderConfiguredError()


class _EmptyRegistry:
    def get_chain(self):
        return []


class _ConfiguredRegistry:
    def get_chain(self):
        return ["relay-cloud"]


def test_no_provider_configured_returns_none_instead_of_raising() -> None:
    assert main._provider_chain_or_none(_RaisingRegistry()) is None


def test_empty_chain_returns_falsy() -> None:
    assert not main._provider_chain_or_none(_EmptyRegistry())


def test_configured_chain_passes_through() -> None:
    assert main._provider_chain_or_none(_ConfiguredRegistry()) == ["relay-cloud"]
