#!/usr/bin/env python3
"""Generate deterministic text payloads for Context OS manual E2E.

This helper only writes fixture files.  It never sends a DeskPet request; the
operator must still paste the generated text through the real UI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def build_compact_payload(round_number: int, total_bytes: int) -> tuple[bytes, dict[str, object]]:
    if round_number <= 0:
        raise ValueError("round must be positive")
    if total_bytes < 256:
        raise ValueError("bytes must be at least 256")

    marker = f"CTX-COMPACT-R{round_number:02d}"
    begin = f"{marker}-BEGIN\n".encode("ascii")
    end_prefix = f"\n{marker}-END sha256=".encode("ascii")
    body_bytes = total_bytes - len(begin) - len(end_prefix) - 64
    if body_bytes <= 0:
        raise ValueError("bytes is too small for compact payload envelope")

    seed = f"{marker}|DESKPET-CONTEXT-OS-E2E|".encode("ascii")
    chunk = (seed * ((64 + len(seed) - 1) // len(seed)))[:64]
    body = (chunk * ((body_bytes + 63) // 64))[:body_bytes]
    body_hash = hashlib.sha256(body).hexdigest().encode("ascii")
    payload = begin + body + end_prefix + body_hash
    assert len(payload) == total_bytes
    manifest: dict[str, object] = {
        "schema_version": 1,
        "kind": "compact",
        "round": round_number,
        "marker": marker,
        "bytes": total_bytes,
        "chunk_bytes": 64,
        "body_bytes": body_bytes,
        "body_sha256": body_hash.decode("ascii"),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    return payload, manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    compact = subparsers.add_parser("compact")
    compact.add_argument("--round", type=int, required=True, dest="round_number")
    compact.add_argument("--bytes", type=int, default=6144, dest="total_bytes")
    compact.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    payload, manifest = build_compact_payload(args.round_number, args.total_bytes)
    output = args.out.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(payload)
    manifest_path = output.with_suffix(output.suffix + ".json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"out": str(output), "manifest": str(manifest_path), **manifest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
