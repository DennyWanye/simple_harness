import hashlib
import json

import pytest

from deskpet.sdk_adapters.context_authority import PreparedSdkContextSnapshotV1
from deskpet.sdk_adapters.context_preparation import SdkContextPreparationService, SdkContextSources


@pytest.mark.asyncio
async def test_memory_projection_v2_is_user_untrusted_with_lineage():
    payload = {"items": [{"text": "memory-canary"}], "status": "complete"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result_hash = hashlib.sha256(canonical.encode()).hexdigest()
    snapshot = await SdkContextPreparationService(
        SdkContextSources(history=lambda _sid: [], memory=lambda _sid, _text: payload["items"])
    ).prepare(
        session_id="s",
        request_id="r",
        root_run_id="root",
        sdk_run_id="sdk",
        turn_id="t",
        text="current",
        provider_binding={"context_window": 1000},
        catalog={"tool_count": 0, "schema_token_count": 0},
        context_query_id="query-1",
        memory_result_id="result-1",
        memory_result_hash=result_hash,
        memory_result_payload=payload,
    )
    private = snapshot.private_record()
    assert private["memory_projection_version"] == 2
    recalled = next(
        item
        for item in private["provider_messages"]
        if str(item.get("content", "")).startswith("Untrusted recalled memory data:")
    )
    assert recalled["role"] == "user"
    assert recalled["metadata"] == {
        "source": "memory",
        "trust": "untrusted_data",
    }
    assert private["memory"]["trust"] == "untrusted_data"
    assert private["lineage"]["context_query_id"] == "query-1"
    assert PreparedSdkContextSnapshotV1.from_private_record(private) == snapshot


@pytest.mark.asyncio
async def test_empty_memory_result_still_projects_tagged_untrusted_message():
    payload = {"items": [], "status": "complete"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result_hash = hashlib.sha256(canonical.encode()).hexdigest()
    snapshot = await SdkContextPreparationService(
        SdkContextSources(history=lambda _sid: [], memory=lambda _sid, _text: [])
    ).prepare(
        session_id="s",
        request_id="r",
        root_run_id="root",
        sdk_run_id="sdk",
        turn_id="t",
        text="current",
        provider_binding={"context_window": 1000},
        catalog={"tool_count": 0, "schema_token_count": 0},
        context_query_id="query-empty",
        memory_result_id="result-empty",
        memory_result_hash=result_hash,
        memory_result_payload=payload,
    )

    private = snapshot.private_record()
    recalled = next(
        item
        for item in private["provider_messages"]
        if item.get("metadata", {}).get("source") == "memory"
    )
    assert recalled["role"] == "user"
    assert recalled["metadata"]["trust"] == "untrusted_data"
    assert '"items":[]' in recalled["content"]
