"""Actual Host queue/context, SDK durable reservation and physical MockTransport."""
import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from simple_harness.contracts.messages import ContentBlock, Message, MessageRole

from deskpet.execution.primary_dependencies import check_runtime_dependencies
from deskpet.memory.current_input_authority import HostCurrentInputAuthority
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.history_source_authority import HostHistorySourceAuthority
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from tests.sdk_adapters.test_product_host_ports import Registry
from tests.execution.test_primary_foreground_runtime import build
from tests.memory.test_current_input_source import env, configured, admit, ok
from tests.memory.test_trusted_disclosure import command, selection


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "request_bytes", "policy", "claim"])
async def test_current_input_actual_physical_boundary(env, tmp_path, monkeypatch, change):
    import main
    sends = []
    entered = []
    rejected = []
    config = await configured(env)
    queued = ok(await admit(env, config, text="整理本轮输入，不使用旧健康家庭记忆。"))
    visibility = HumanMemoryV7Runtime(tmp_path / "visibility-memory.db",
        evidence_authority=HostEvidenceAuthority(env.path), history_source_authority=HostHistorySourceAuthority(env.path))
    visibility = HumanMemoryV7Runtime(tmp_path / "visibility-memory.db",
        evidence_authority=HostEvidenceAuthority(env.path), history_source_authority=HostHistorySourceAuthority(env.path),
        current_input_authority=HostCurrentInputAuthority(env.path, principal=visibility.principal()))
    # Build-time real trusted port; this is not a replacement SDK backend.
    monkeypatch.setattr(main, "_state_db_path", env.path)
    from context import ServiceContext
    services = ServiceContext()
    services.register("human_memory_v7_runtime", visibility)
    monkeypatch.setattr(main, "service_context", services)

    def physical(request):
        sends.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "current-input-physical", "model": "model-a",
            "choices": [{"message": {"role": "assistant", "content": "已按当前材料整理。"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}})
    client = httpx.AsyncClient(transport=httpx.MockTransport(physical))
    inside_physical = False
    async def guard(request):
        nonlocal inside_physical
        entered.append(request)
        current = await queue.current_snapshot(env.auth.subject)
        inside_physical = True
        try:
            await check_runtime_dependencies(db_path=env.path, stack=stack, sdk_run_id=current.sdk_run_id,
                request=request, policy_factory=lambda _: runtime.history_policy)
        except Exception as error:
            rejected.append(error)
            raise
        finally:
            inside_physical = False

    class ChangedAdapter(ProductProviderAdapter):
        async def invoke(self, request, *, cancel):
            if change == "request_bytes":
                request = replace(request, messages=(*request.messages,
                    Message(MessageRole.USER, (ContentBlock("input_text", {"text": "UNBOUND_PRIVATE_INPUT"}),))))
            return await super().invoke(request, cancel=cancel)
    provider = ChangedAdapter(Registry("fixture-secret"), provider_id="relay", client=client,
        price_resolver=lambda *_: (1, 1, "price-v1"), pre_invoke_guard=guard)
    runtime = stack = None
    try:
        sdk = await visibility.manager()
        changed = False
        async def slow(**kwargs):
            nonlocal changed
            result = await original_policy_check(**kwargs)
            if inside_physical and not changed and change in {"policy", "claim"}:
                changed = True
                if change == "policy":
                    ok(await command(env, "disclosure.configure", selection(config["binding_ref"]), key="late-policy"))
                else:
                    current = await queue.current_snapshot(env.auth.subject)
                    await queue.request_control(host_run_id=current.host_run_id, subject=env.auth.subject,
                        generation=current.generation, control_kind="stop", reason="late-physical-stop", idempotency_key="late-stop")
            return result
        runtime, stack, queue = await build(tmp_path, env.path, provider, visibility_memory=visibility,
            visibility_checker=main._primary_history_visibility_checker)
        original_policy_check = runtime.history_policy.check_dependencies
        # Change facts after the entire real policy read returns, so the last
        # physical fence (not an earlier inner checker) must catch the change.
        monkeypatch.setattr(runtime.history_policy, "check_dependencies", slow)
        await asyncio.wait_for(runtime._drive_once(), 15)
        assert len(entered) == 1  # reached actual adapter immediately before network
        if change == "none":
            assert len(sends) == 1 and runtime.last_error is None
            assert "整理本轮输入" in json.dumps(sends, ensure_ascii=False)
        else:
            assert sends == []
            assert len(rejected) == 1
            assert rejected[0].error_code == "primary_history_disclosure_rejected"
            # Inspect the actual guard cause, never manufacture a refusal.
            causes = []
            error = rejected[0]
            while error is not None:
                causes.append(str(error))
                error = error.__cause__ or error.__context__
            expected = {"request_bytes": "current_input_provider_request_mismatch",
                        "policy": "binding_stale", "claim": "primary_input_claim_changed_during_check"}[change]
            assert expected in " ".join(causes), causes
            if change in {"policy", "claim"}:
                assert changed
        from deskpet.operation_audit.current_inputs import CurrentInputJournal
        rows = (await CurrentInputJournal(env.path.with_name("operation-audit.db")).page(principal=visibility.principal()))["items"]
        assert rows and all(row["observation_status"] == "captured_bound" for row in rows)
    finally:
        if runtime is not None:
            await runtime.close()
        if stack is not None:
            await stack.close()
        await visibility.close()
        await client.aclose()
