"""Configured LAN provider reaches the same foreground SDK wire as HTTPS."""
import httpx
import pytest
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from simple_harness import RequestId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import CancelToken, ProviderRequest
from tests.sdk_adapters.test_product_host_ports import Registry


@pytest.mark.asyncio
async def test_selected_private_registry_endpoint_reaches_transport():
    sent = []
    def transport(request):
        sent.append(str(request.url))
        return httpx.Response(200, json={"id":"local-response","model":"model-a",
            "choices":[{"message":{"role":"assistant","content":"LOCAL_OK"},"finish_reason":"stop"}],
            "usage":{"prompt_tokens":10,"completion_tokens":3,"total_tokens":13}})
    registry = Registry("fake-local-only")
    registry.entry.base_url = "http://192.168.10.27:11434/v1"
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        adapter = ProductProviderAdapter(registry, provider_id="relay", client=client,
            price_resolver=lambda *_:(1,1,"test-price"))
        response = await adapter.invoke(ProviderRequest(RequestId("local-registry-turn"),
            (Message(MessageRole.USER,"hello"),)), cancel=CancelToken())
        assert response.message.content == "LOCAL_OK"
    assert sent == ["http://192.168.10.27:11434/v1/chat/completions"]

@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://8.8.8.8/v1", "http://169.254.169.254/v1", "http://example.com/v1"])
async def test_registry_does_not_permit_other_plaintext_targets(url):
    registry=Registry("fake-local-only");registry.entry.base_url=url
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="HTTPS"):
            ProductProviderAdapter(registry,provider_id="relay",client=client,
                price_resolver=lambda *_:(1,1,"test-price"))
