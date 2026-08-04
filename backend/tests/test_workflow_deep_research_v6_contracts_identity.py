from __future__ import annotations

import hashlib
import json
from pathlib import Path

from deskpet.workflows.adapters.research_runtime import (
    DurableResearchCallEffectAdapter,
    V6_RESEARCH_RESPONSE_FORMATS,
    build_v6_research_llm_profiles,
    research_response_format_hash,
)
from deskpet.workflows.definitions.v6 import DEEP_RESEARCH_V6
from deskpet.workflows.runner import manifest_hash


FIXTURE = Path(__file__).parent / "fixtures" / "deep_research_v6_release_identity.json"
V5_FIXTURE = Path(__file__).parent / "fixtures" / "deep_research_v5_identity.json"


def _release_identity() -> dict[str, object]:
    roles = tuple(V6_RESEARCH_RESPONSE_FORMATS)
    profiles = build_v6_research_llm_profiles(
        {
            role: research_response_format_hash(V6_RESEARCH_RESPONSE_FORMATS[role])
            for role in roles
        }
    )
    prepared_calls = {}
    for index, role in enumerate(roles, 1):
        digest_char = "abc"[index - 1]
        prepared_calls[role] = DurableResearchCallEffectAdapter._prepared(
            role=role,
            payload_ref=f"sha256:{digest_char * 64}",
            max_output_tokens=4096,
            stable_call_id=str(index) * 64,
            response_format=V6_RESEARCH_RESPONSE_FORMATS[role],
            profile=profiles[role],
        ).to_dict()
    manifest = DEEP_RESEARCH_V6.manifest
    return {
        "fixture_schema_version": 1,
        "release_version": "v6",
        "manifest_hash": manifest_hash(manifest),
        "manifest": manifest.to_dict(),
        "identity_hashes": {
            "implementation": manifest.implementation_bundle_hash,
            "state": manifest.state_hash,
            "prompt": manifest.prompt_hash,
            "policy": manifest.policy_hash,
        },
        "effect_profiles": {
            role: profiles[role].to_json() for role in roles
        },
        "prepared_calls": prepared_calls,
    }


def test_v6_release_identity_is_frozen() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture == _release_identity()


def test_v5_identity_fixture_bytes_remain_unchanged_at_v6_release() -> None:
    # Git may materialize the JSON fixture with CRLF on Windows.  The release
    # identity is the canonical LF byte stream, independent of checkout mode.
    canonical_bytes = V5_FIXTURE.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(canonical_bytes).hexdigest() == (
        "20868ac0d6e1bd1cf14033389224df16d0019165575b4d2e5a0d7892d0d3b1c6"
    )
