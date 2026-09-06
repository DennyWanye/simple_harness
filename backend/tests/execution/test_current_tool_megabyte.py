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
async def test_actual_megabyte_result_pages_without_resending_full_body(tmp_path, monkeypatch):
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
    await run_pages(tmp_path, monkeypatch, mode="allow")
    assert len(sizes) == 2 and all(size > 1024 * 1024 for size in sizes)
    assert len(wire_sizes) == 8 and max(wire_sizes) < 256 * 1024
    (tmp_path / "megabyte-metrics.json").write_text(json.dumps({
        "file_bytes": sizes, "physical_request_bytes": wire_sizes,
        "provider_context_window": 32768, "native": False,
    }, indent=2))
