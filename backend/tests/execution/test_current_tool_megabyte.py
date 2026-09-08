"""TC-HM11 large-result boundary in the actual runtime, with a fixture producer.

The controlled tool writes/reads an isolated real file. This is component HTTP
evidence, not a native test or a claim about the standard write_file output.
"""
import importlib
import inspect
import json
from pathlib import Path

import httpx
import pytest

from tests.execution.test_current_tool_pages import (
    test_actual_current_effect_page_and_physical_guard as run_pages,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("context_window", [4096, 8192, 32768])
async def test_actual_megabyte_result_pages_without_resending_full_body(tmp_path, monkeypatch, context_window):
    module = importlib.import_module("deskpet.tools.os_tools.write_file")
    original_write = module.write_file
    sizes, wire_sizes = [], []
    expanded = False

    def produce(arguments, *, execution_context):
        nonlocal expanded
        result = original_write(arguments, execution_context=execution_context)
        if "error" not in json.loads(result) and arguments.get("mode") == "append":
            path = Path(execution_context.write_scope_root) / arguments["path"]
            if not expanded:
                # Expand the first already-large append result, retaining the
                # two distinct effect results expected by the source scenario.
                with path.open("a", encoding="utf-8") as handle:
                    handle.write("λ" * (512 * 1024))
                expanded = True
            sizes.append(path.stat().st_size)
        return result

    monkeypatch.setattr(module, "write_file", produce)
    original_transport = httpx.MockTransport

    def transport(handler):
        async def record(request):
            wire_sizes.append(len(request.content))
            assert ("λ" * 4096).encode() not in request.content
            result = handler(request)
            return await result if inspect.isawaitable(result) else result
        return original_transport(record)

    monkeypatch.setattr(httpx, "MockTransport", transport)
    # Small input calls isolate large tool-result pressure. The earlier
    # oversized assistant-arguments/small-window failures remain separate.
    chunks = ("seed", "append", "EXACT_PAGE_TAIL") if context_window < 32768 else None
    # Incident N (2026-09-08): the tool schemas travel in every request and are
    # now charged to protected_tokens.  For this 8-tool catalog that is ~1.5 K
    # tokens, which the 4096 and 8192 tiers cannot spare on top of a paged
    # megabyte result — those Runs now stop safely on the budget instead of
    # shipping a request the Host had mis-measured.  32768 still completes.
    budget_stop = context_window < 32768
    budget_rejections = []
    if budget_stop:
        from deskpet.sdk_adapters import context_authority
        from deskpet.sdk_adapters.context_partitions import ContextBudgetExceeded
        actual_plan = context_authority._plan_turn_messages
        def record_budget(*args, **kwargs):
            try:
                return actual_plan(*args, **kwargs)
            except ContextBudgetExceeded as error:
                budget_rejections.append({"code": str(error), "wire_count": len(wire_sizes)})
                raise
        monkeypatch.setattr(context_authority, "_plan_turn_messages", record_budget)
    await run_pages(tmp_path, monkeypatch, mode="allow", provider_context_window=context_window,
                    write_chunks=chunks, expected_budget_stop=budget_stop)
    if budget_stop:
        assert budget_rejections and budget_rejections[0]["code"] == "sdk_context_budget_exceeded"
        assert budget_rejections[0]["wire_count"] == len(wire_sizes) < 8
    else:
        assert len(sizes) == 2 and all(size > 1024 * 1024 for size in sizes)
        assert len(wire_sizes) == 8
    assert max(wire_sizes) < 256 * 1024
    (tmp_path / "megabyte-metrics.json").write_text(json.dumps({
        "file_bytes": sizes, "physical_request_bytes": wire_sizes,
        "provider_context_window": context_window, "native": False,
        "budget_stop_expected": budget_stop,
        "input_variant": "small_calls" if chunks is not None else "large_calls",
        "budget_rejections": budget_rejections,
        "outcome": "safe_budget_refusal" if budget_rejections else "paged_and_completed",
    }, indent=2))
