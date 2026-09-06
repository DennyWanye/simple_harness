"""Standalone no-network lifecycle control; no pytest or global lane teardown."""
import asyncio
import json
import socket
import sys
import threading
from pathlib import Path

from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderUsage

from deskpet.quality.corpus_scoring_session import configure_process, run
from deskpet.sdk_adapters.provider import _ProductOpenAICompatibleProvider


def main():
    directory, host = map(Path, sys.argv[1:])
    directory.mkdir()
    # Local control data only. The reviewed setup ID selects the existing
    # public seed adapter; this is not a re-evaluation of any original case.
    clock = dict(instant="2026-09-06T10:00:00+08:00", timezone="Asia/Shanghai")
    (directory / "input.json").write_text(json.dumps(dict(
        current_user_message="本地进程退出控制，请回复收到。", recent_messages=[],
        scenario_clock=clock, unresolved_source_text=None), ensure_ascii=False))
    (directory / "setup.json").write_text(json.dumps(dict(case_id="C01-10",
        scenario_clock=clock,
        setup_source_text="A：本人的待办清单按截止时间升序，没日期的放最后。"), ensure_ascii=False))
    def no_network(*args, **kwargs):
        raise AssertionError("lifecycle control forbids network")
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    requests = []
    async def response(self, request, *, cancel):
        requests.append(request)
        assert len(requests) == 1
        return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, "收到。"),
            model="gpt-5.5", usage=ProviderUsage(10,10,20))
    _ProductOpenAICompatibleProvider.invoke = response
    key, endpoint = configure_process(directory, host, initialize_only=True)
    async def execute():
        code = await run(directory, host, key, endpoint)
        import main as product
        workflow = product.service_context.get("workflow_service")
        # Same public close is idempotent after actual production cleanup.
        await workflow.close()
        await workflow.runner.close()
        return code
    code = asyncio.run(execute())
    result = json.loads((directory / "execution.json").read_text())
    assert code == 0 and result["execution_status"] == "COMPLETED"
    assert result["cleanup_errors"] == [] and len(requests) == 1
    assert result["trace"]["provider_observation_complete"] is True
    assert result["trace"]["terminal_status"] == "TERMINAL"
    # Diagnostic inventory is evidence, never a blanket thread terminator.
    (directory / "thread-exit-inventory.json").write_text(json.dumps([
        dict(name=t.name, daemon=t.daemon, alive=t.is_alive()) for t in threading.enumerate()
    ], ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
