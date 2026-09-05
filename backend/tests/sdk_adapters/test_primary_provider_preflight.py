import httpx
import pytest
from simple_harness import RequestId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import CancelToken, ProviderRequest, ProviderRequestRejectedError
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from tests.sdk_adapters.test_product_host_ports import Registry


@pytest.mark.asyncio
async def test_preflight_denial_prevents_physical_transport():
    sent = []
    def transport(request):
        sent.append(request)
        return httpx.Response(200, json={"id": "x", "model": "model-a", "choices": [
            {"message": {"role": "assistant", "content": "sent"}, "finish_reason": "stop"}]})
    async def deny(request):
        raise ProviderRequestRejectedError(public_message="Current history is no longer visible.")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        adapter = ProductProviderAdapter(Registry("secret"), provider_id="relay", client=client,
            price_resolver=lambda *_: (1, 1, "price-v1"))
        adapter._pre_invoke_guard = deny
        with pytest.raises(ProviderRequestRejectedError):
            await adapter.invoke(ProviderRequest(RequestId("actual-request"),
                (Message(MessageRole.USER, "source"),)), cancel=CancelToken())
        assert sent == []
