# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Tests for SDK adapter conformance host."""

from __future__ import annotations

import pytest

from deskpet.sdk_adapters.conformance import build_host


def test_build_host_exists():
    """Test that build_host function exists and is callable."""
    assert callable(build_host)


def test_build_host_returns_none_before_implementation():
    """Test build_host returns None before T6.1 implementation complete.

    This test will need updating when actual conformance host is implemented.
    """
    result = build_host()
    assert result is None


@pytest.mark.skip(reason="T6.1 implementation pending - requires SDK conformance protocol")
def test_build_host_implements_sdk_protocols():
    """Test build_host returns conformance host implementing SDK protocols.

    Will verify:
    - Provider protocol implementation
    - Tool protocol implementation
    - Runtime protocol implementation
    - Workflow protocol implementation
    """
    host = build_host()
    assert host is not None
    # TODO T6.1: Add protocol conformance checks
