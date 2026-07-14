from __future__ import annotations

import hashlib

import pytest

from scripts.e2e.context_os_payload import build_compact_payload


def test_compact_payload_is_exact_and_deterministic() -> None:
    first, manifest = build_compact_payload(3, 6144)
    second, repeated_manifest = build_compact_payload(3, 6144)

    assert first == second
    assert manifest == repeated_manifest
    assert len(first) == 6144
    assert first.startswith(b"CTX-COMPACT-R03-BEGIN\n")
    assert b"\nCTX-COMPACT-R03-END sha256=" in first
    assert hashlib.sha256(first).hexdigest() == manifest["payload_sha256"]
    assert manifest["chunk_bytes"] == 64


@pytest.mark.parametrize("round_number,total_bytes", [(0, 6144), (1, 128)])
def test_compact_payload_rejects_invalid_bounds(round_number: int, total_bytes: int) -> None:
    with pytest.raises(ValueError):
        build_compact_payload(round_number, total_bytes)
