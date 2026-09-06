"""One local transport control for bounded rejection diagnostics; no model."""
import hashlib
import json
import logging
import socket

import httpx
import pytest
from simple_harness import RequestId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import CancelToken, ProviderRequest, Secret
from simple_harness.providers.errors import ProviderRequestRejectedError

from deskpet.sdk_adapters.provider import _ProductOpenAICompatibleProvider


@pytest.mark.asyncio
async def test_http_rejection_diagnostic_redacted_bounded_and_original_error(tmp_path, caplog, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    key = "diagnostic-secret-never-log"
    unselected = "UNSELECTED_PRIVATE_BODY"
    bodies = [json.dumps({"error": {"code": "invalid_schema", "type": "invalid_request_error",
        "param": "tools[0].function.parameters", "message": "x" * 1010 + key + "\nextra",
        "nested": {"private": unselected}}, "headers": unselected}).encode(),
        ("not-json-" + key).encode(), (key * 4000).encode()]
    sent = []
    def transport(request):
        assert request.headers["authorization"] == "Bearer " + key
        sent.append(request)
        assert len(sent) <= len(bodies), "diagnostic must never retry"
        return httpx.Response(400, content=bodies[len(sent) - 1],
            headers={"x-private-header": unselected, "content-type": "application/json"})
    caplog.set_level(logging.WARNING, logger="deskpet.sdk_adapters.provider")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = _ProductOpenAICompatibleProvider(client, "https://local.invalid/v1",
            "gpt-5.5", Secret(key))
        for n in range(len(bodies)):
            with pytest.raises(ProviderRequestRejectedError) as rejected:
                await provider.invoke(ProviderRequest(RequestId(f"diagnostic-{n}"),
                    (Message(MessageRole.USER, "local error control"),)), cancel=CancelToken())
            assert rejected.value.status_code == 400
            assert not client.is_closed  # Wrapper borrows the original client.
    lines = [r.getMessage() for r in caplog.records if r.name == "deskpet.sdk_adapters.provider"
             and r.getMessage().startswith("product_provider_http_rejected ")]
    assert len(lines) == len(bodies) == len(sent)
    assert key not in "\n".join(lines) and unselected not in "\n".join(lines)
    facts = [json.loads(line.split(" diagnostic=", 1)[1]) for line in lines]
    assert facts[0]["code"] == "invalid_schema"
    assert facts[0]["type"] == "invalid_request_error"
    assert facts[0]["param"] == "tools[0].function.parameters"
    assert "[REDACTED]" in facts[0]["message"] and len(facts[0]["message"]) <= 1024
    assert [f["body_format"] for f in facts] == ["structured_error", "unstructured", "oversized"]
    assert set(facts[1]) == set(facts[2]) == {"body_format", "body_bytes", "body_sha256"}
    for n, fact in enumerate(facts):
        assert fact["body_sha256"] == hashlib.sha256(bodies[n]).hexdigest()
        assert fact["body_bytes"] == len(bodies[n])
        assert "\n" not in lines[n] and len(lines[n]) < 3000
    (tmp_path / "diagnostic-summary.json").write_text(json.dumps({
        "local_http_calls": len(sent), "remote_calls": 0, "diagnostics": facts,
        "error_type": "ProviderRequestRejectedError", "status_code": 400,
    }, indent=2))
