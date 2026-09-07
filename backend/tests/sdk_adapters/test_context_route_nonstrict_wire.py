"""One local HTTP wire→SDK arguments→Host route/ledger control; no model call.

Only HTTP responses and the recall executor's empty result are deterministic.
This proves optional-field routing, not memory quality or whole main startup.
"""
import json
import socket
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from simple_harness import CallId, RequestId, RunId, thaw_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.execution.context_authority import ContextRouteReceipt
from simple_harness.providers import CancelToken, ProviderRequest, ProviderToolSpec, Secret
from simple_harness.tools.schema import validate_arguments

from deskpet.memory.schema import initialize_human_memory_program_state_db
from deskpet.sdk_adapters import tools as host_tools
from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore, canonical_sha256
from deskpet.sdk_adapters.context_route import CONTEXT_ROUTE_SCHEMA, ContextRouteToolService
from deskpet.sdk_adapters.provider import _ProductOpenAICompatibleProvider


@pytest.mark.asyncio
async def test_nonstrict_wire_omitted_memory_and_fake_reuse_rejected(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    base = dict(route="memory_standalone", query="读取已有约定", memory_types=["semantic"])
    proposals = [base, dict(base, reuse_workspace_of="null", expected_source_hash="0" * 64)]
    wires, recall_calls = [], []
    def transport(request):
        assert request.url.path == "/v1/chat/completions"
        wires.append(json.loads(request.content))
        n = len(wires) - 1
        assert n < 2
        return httpx.Response(200, json={"id": f"response-{n}", "model": "gpt-5.5",
            "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant",
                "content": "", "tool_calls": [{"id": f"call-{n}", "type": "function",
                "function": {"name": "context_route", "arguments": json.dumps(proposals[n])}}]}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    async def recall(**kwargs):
        recall_calls.append(kwargs)
        return SimpleNamespace(result=SimpleNamespace(items=(), truncated=False), degradation_codes=())
    path = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(path)
    context = None
    service = ContextRouteToolService(service_factory_getter=lambda: None,
        binding_store_factory=lambda: None, binding_append_getter=lambda: None,
        ledger=ContextRouteLedgerStore(path), tool_context_getter=lambda: context,
        recall_executor=recall)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = _ProductOpenAICompatibleProvider(client, "https://wire-control.invalid/v1",
            "gpt-5.5", Secret("local-not-a-key"))
        for n, expected in enumerate(proposals):
            response = await provider.invoke(ProviderRequest(RequestId(f"request-{n}"),
                (Message(MessageRole.USER, "本地协议控制"),),
                tools=(ProviderToolSpec("context_route", "Route context", CONTEXT_ROUTE_SCHEMA),)),
                cancel=CancelToken())
            function = wires[n]["tools"][0]["function"]
            assert function["strict"] is False
            assert function["parameters"] == CONTEXT_ROUTE_SCHEMA
            assert function["parameters"]["required"] == ["route"]
            assert function["parameters"]["properties"]["reuse_workspace_of"]["type"] == ["string", "null"]
            args = thaw_json(response.tool_calls[0].arguments)
            assert args == expected  # No sentinel stripping or argument rewriting.
            validate_arguments(args, CONTEXT_ROUTE_SCHEMA)
            context = SimpleNamespace(run_id=RunId(f"run-{n}"),
                effect_id=SimpleNamespace(value=f"effect-{n}"),
                task_execution_envelope=SimpleNamespace(raw_call_id=f"call-{n}", turn_ordinal=1))
            raw = await service.handle_context_route(args)
            token = host_tools._current_call_id.set(CallId(f"call-{n}"))
            try:
                public = host_tools._result(raw)
            finally:
                host_tools._current_call_id.reset(token)
            if n == 0:
                assert "reuse_workspace_of" not in args and "expected_source_hash" not in args
                receipt = ContextRouteReceipt.from_json(raw["context_route_receipt"])
                assert receipt.route.value == "memory_standalone" and receipt.run_id == "run-0"
                assert public.error_code is None and len(recall_calls) == 1
            else:
                assert public.error_code == "context_route_workspace_reuse_requires_create_new"
                assert "omit both reuse_workspace_of and expected_source_hash entirely" in public.public_message
                assert len(recall_calls) == 1
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT sdk_run_id,proposal_hash,verdict FROM context_route_tool_invocations ORDER BY sdk_run_id").fetchall()
        assert rows == [(f"run-{n}", canonical_sha256(p), "accepted" if n == 0 else "rejected")
                        for n, p in enumerate(proposals)]
        assert db.execute("SELECT count(*) FROM context_route_decisions").fetchone()[0] == 1
    assert len(wires) == 2
