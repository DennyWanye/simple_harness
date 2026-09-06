"""Two new P1 controls: actual SDK attempts, no remote Provider/model calls."""
import asyncio
import json
import pytest

from deskpet.quality.corpus_trace import collect_bound_observations
from deskpet.quality.corpus_scoring_session import write_result
from tests.execution.test_primary_foreground_runtime import (
    Provider, build, dispatch_startup_epoch, HumanMemoryHostServiceFactory,
    local_owner_auth, QueueTurnRequest,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["nonterminal", "failed"])
async def test_actual_provider_attempt_survives_missing_transcript(tmp_path, monkeypatch, phase):
    entered, release = asyncio.Event(), asyncio.Event()
    class HeldFailure(Provider):
        async def invoke(self, request, *, cancel):
            self.requests.append(request)
            entered.set()
            await release.wait()
            from simple_harness.providers.errors import ProviderAuthenticationError
            raise ProviderAuthenticationError()

    state = tmp_path / "state.db"
    epoch = await dispatch_startup_epoch(state, approved_fresh_lane=True)
    auth = local_owner_auth()
    service = HumanMemoryHostServiceFactory(state, epoch).bind(auth)
    await service.open_primary()
    adapter = HeldFailure()
    runtime, stack, queue = await build(tmp_path, state, adapter)
    drive = None
    try:
        text = "原始失败评分输入，不改写。"
        await service.enqueue_turn(QueueTurnRequest(None, "failure-evidence", text))
        drive = asyncio.create_task(runtime._drive_once())
        await asyncio.wait_for(entered.wait(), 15)
        active = await queue.current_snapshot(auth.subject)
        assert active is not None and active.sdk_run_id
        run_id = active.sdk_run_id
        assert stack.read_run_terminal_evidence(run_id) is None
        if phase == "failed":
            release.set()
            assert await asyncio.wait_for(drive, 15)
            assert stack.read_run_terminal_evidence(run_id) is not None
        before = stack.read_corpus_scoring_trace(run_id)
        assert before["providers"] and len(before["providers"]) == 1
        assert before["terminal_status"] == ("NONTERMINAL" if phase == "nonterminal" else "TERMINAL")
        assert before["providers"][0]["handed_off_at"] is not None
        if phase == "failed":
            assert before["providers"][0]["state"] == "failed"
        # Real public trace remains readable; only the later transcript surface
        # fails, reproducing the original evidence-loss seam precisely.
        def fail_transcript(*args, **kwargs):
            assert (tmp_path / "trace.json").exists(), "trace must be durable first"
            raise ValueError("injected_transcript_unavailable")
        monkeypatch.setattr(stack, "read_primary_run_messages", fail_transcript)
        result = await collect_bound_observations(stack=stack, run_id=run_id, text=text,
            route_reader=lambda: [], queue_reader=service.queue_snapshot,
            persist=lambda name, value: write_result(tmp_path / (name + ".json"), value))
        persisted = json.loads((tmp_path / "trace.json").read_text())
        assert persisted == result["trace"] == before
        assert result["observation_errors"]["transcript"] == "ValueError"
        assert result["trace"]["operation_audit"] is not None
        assert len(adapter.requests) == 1
    finally:
        release.set()
        if drive is not None:
            await asyncio.wait_for(asyncio.gather(drive, return_exceptions=True), 15)
        await runtime.close()
        await stack.close()
