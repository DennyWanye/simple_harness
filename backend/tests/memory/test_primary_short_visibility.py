"""Host v2 wire/recursive batch contract; fixture policy is not short ingestion proof."""

from dataclasses import replace

import pytest
import simple_harness_memory as m
from tests.memory.test_primary_read_api import AUTH, error, result, settled, setup
from tests.memory.test_primary_visibility import (
    FILTERS,
    check_proof,
    classification_policy,
    disclosure,
    pairs,
    proof,
)

SHORT = {"audit_id": "selected-audit", "chunk_ref": "selected-chunk", "content_hash": "a" * 64}
TYPED = {"result_id": "typed-result", "result_hash": "b" * 64,
         "item_id": "typed-item", "item_hash": "c" * 64}


def v2(envelope):
    return {**proof(envelope, recall=(TYPED,)), "schema_version": 2, "short_horizon": [SHORT]}


@pytest.mark.asyncio
async def test_mixed_v2_dependencies_use_one_batch_and_preserve_exact_short_identity(tmp_path):
    f = await setup(tmp_path)
    result(await f.send("queue.enqueue", {"text": "current USER"}))
    envelope, _ = (await pairs(f))[0]
    assert await check_proof(f, v2(envelope))
    assert len(f.policy.history_calls) == 1
    _, context, bindings = f.policy.history_calls[0]
    assert context == disclosure()
    assert len(bindings) == 3
    short, = (binding for binding in bindings if type(binding) is m.HistoryShortHorizonBinding)
    assert short.to_json() == {"kind": "short_horizon", **SHORT}
    typed, = (binding for binding in bindings if type(binding) is m.HistoryRecallBinding)
    assert typed.to_json() == {"kind": "recall", **TYPED}
    # v1 keeps its original closed shape; it is not silently upgraded in storage.
    assert await check_proof(f, proof(envelope))


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["v1_extra", "v2_missing", "typed_as_short", "wrong_hash", "combined_limit"])
async def test_invalid_short_carrier_never_reaches_policy(tmp_path, bad):
    f = await setup(tmp_path)
    result(await f.send("queue.enqueue", {"text": "current USER"}))
    envelope, _ = (await pairs(f))[0]
    value = v2(envelope)
    if bad == "v1_extra":
        value["schema_version"] = 1
    elif bad == "v2_missing":
        value.pop("short_horizon")
    elif bad == "typed_as_short":
        value["short_horizon"] = [TYPED]
    elif bad == "wrong_hash":
        value["short_horizon"] = [{**SHORT, "content_hash": "wrong"}]
    else:
        value["short_horizon"] = [SHORT] * 255  # USER + typed + short exceeds 256
    assert not await check_proof(f, value)
    assert f.policy.history_calls == []


@pytest.mark.asyncio
async def test_v2_terminal_short_denial_rechecks_page_and_saved_detail_without_hiding_user(tmp_path):
    terminal = None
    deny = False

    async def checker(**kwargs):
        snapshot = await f.policy.history(**kwargs)
        if not deny:
            return snapshot
        return replace(snapshot, items=tuple(
            replace(item, visible=False, reason="history_suppressed")
            if type(binding) is m.HistoryShortHorizonBinding else item
            for item, binding in zip(snapshot.items, kwargs["bindings"], strict=True)
        ))

    f = await setup(tmp_path, history_checker=checker,
                    reader=lambda _run, *, current_text: (
                        terminal, ({"role": "user", "content": current_text},
                                   {"role": "assistant", "content": "short-derived reply"}),
                    ))
    _, terminal = await settled(f, short_horizon=(SHORT,))
    before = result(await f.send("primary.messages.page", {"primary_ref": f.primary}))
    assert [item["role"] for item in before["items"]] == ["user", "assistant"]
    ref = before["items"][-1]["message_ref"]
    calls = len(f.policy.history_calls)
    deny = True
    after = result(await f.send("primary.messages.page", {"primary_ref": f.primary}))
    assert len(f.policy.history_calls) == calls + 1
    assert after["revision"] == before["revision"]
    assert [item["role"] for item in after["items"]] == ["user"]
    error(await f.send("primary.messages.detail", {
        "primary_ref": f.primary, "message_ref": ref,
    }), "primary_message_unavailable")


@pytest.mark.asyncio
async def test_real_installed_manager_denies_unselected_short_despite_cold_user_allow(tmp_path):
    manager = await m.build_human_memory_v7(tmp_path / "memory.db",
        supported_filter_policies=FILTERS, classification_policy=classification_policy())
    principal = m.MemoryPrincipal("host", "household", AUTH.subject, "ui")

    async def checker(*, subject, disclosure_context, bindings):
        assert subject == principal.actor_id
        return await manager.check_history_visibility(principal=principal,
            disclosure_context=disclosure_context, bindings=bindings)

    try:
        f = await setup(tmp_path / "host", history_checker=checker)
        result(await f.send("queue.enqueue", {"text": "cold original USER"}))
        envelope, _ = (await pairs(f))[0]
        assert await check_proof(f, proof(envelope), checker)
        value = {**v2(envelope), "recall": []}
        assert not await check_proof(f, value, checker)
    finally:
        await manager.close()
