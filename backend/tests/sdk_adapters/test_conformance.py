# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Tests for SDK adapter conformance host."""

from __future__ import annotations

import pytest
from simple_harness.testing import run_conformance

from deskpet.sdk_adapters.conformance import VENDORED_SDK_SHA256, build_host


def test_build_host_exists():
    """Test that build_host function exists and is callable."""
    assert callable(build_host)


def test_build_host_implements_sdk_protocols():
    host = build_host()
    assert host is not None
    assert host.metadata.capabilities == frozenset(
        {"provider", "tool", "runtime", "workflow"}
    )


@pytest.mark.asyncio
async def test_product_host_runs_all_required_cases_without_skip():
    report = await run_conformance(
        build_host,
        ("provider", "tool", "runtime", "workflow"),
        artifact_sha256=VENDORED_SDK_SHA256,
    )
    assert report.artifact_sha256 == VENDORED_SDK_SHA256
    assert len(report.cases) == 22
    assert not report.errors
    assert {case.status.value for case in report.cases} == {"pass"}
