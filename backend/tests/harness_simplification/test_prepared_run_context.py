from __future__ import annotations

from dataclasses import dataclass

import pytest

from deskpet.execution.contracts import DeliveryPolicy, DeliverySpec
from deskpet.harness.contracts import (
    HostExtensionRefV1,
    PreparedRunContextV1,
)


@dataclass(frozen=True)
class _Callback:
    descriptor: HostExtensionRefV1


def _descriptor(kind: str) -> HostExtensionRefV1:
    return HostExtensionRefV1(kind, f"ref:{kind}", "a" * 64)


def test_prepared_context_fingerprint_contains_stable_descriptors_only():
    callback = _Callback(_descriptor("deskpet.test.start"))
    value = PreparedRunContextV1(
        persistence_required=True,
        prepared_tool_ref="prepared-tools:1",
        prepared_tool_hash="b" * 64,
        product_snapshot_ref="product-snapshot:1",
        product_snapshot_hash="c" * 64,
        frozen_terminal_deliveries=(
            DeliverySpec(
                "sink",
                "instance",
                "target",
                DeliveryPolicy.DURABLE_REQUIRED,
            ),
        ),
        host_extensions={
            "deskpet.test.payload": _descriptor("deskpet.test.payload")
        },
        start_commit_extensions=(callback,),
    )

    assert len(value.prepared_fingerprint) == 64
    assert str(id(callback)) not in value.prepared_fingerprint
    assert value.prepared_fingerprint == value.prepared_fingerprint


def test_prepared_context_rejects_unnamespaced_or_duplicate_extensions():
    with pytest.raises(ValueError, match="namespaced"):
        HostExtensionRefV1("plain", "ref", "a" * 64)

    callback = _Callback(_descriptor("deskpet.test.start"))
    with pytest.raises(ValueError, match="unique"):
        PreparedRunContextV1(
            start_commit_extensions=(callback, callback),
        )


def test_prepared_context_requires_ref_hash_pairs():
    with pytest.raises(ValueError, match="appear together"):
        PreparedRunContextV1(prepared_tool_ref="tools:1")
