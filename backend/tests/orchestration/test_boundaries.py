# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""HA-9 and gap F-1: what an orchestration Agent can reach from inside the Host.

Draft written before the implementation (plan 2026-09-11 H2).  The decisive oracle for F-1
is behavioural: a test file the model writes must never be imported on this machine while
local tests are off, however the Planner fills in ``verification_policy``.
"""

from __future__ import annotations

import pytest

from deskpet.orchestration.service import OrchestrationRequestError, OrchestrationService
from agent_orchestrator.testing.word_counter import FixtureWordCounter
from deskpet.orchestration.settings import OrchestrationSettings

from ._support import WORKSPACE_TOOLS, notes_provider, notes_request

KNOWLEDGE_TOOLS = ("knowledge_list", "knowledge_read")


@pytest.mark.asyncio
async def test_default_deployment_offers_workspace_and_scoped_knowledge_tools(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await service.start()
    try:
        # Workspace and Mission-scoped knowledge reads are always there; ``run_tests`` is there when
        # — and only when — the sandbox probe passed on this machine, because that is the
        # only way model-written code may run at all
        status = service.status()
        sandboxed = bool(status["sandbox"].get("ok"))
        # NEXT-TG-1.0 §11: the native plane (default on) also serves the three Skill tools
        skills = ["skill_discover", "skill_load", "skill_execute"] if status["native_plane"]["enabled"] else []
        expected = sorted([*WORKSPACE_TOOLS, *KNOWLEDGE_TOOLS, *(["run_tests"] if sandboxed else []), *skills])
        assert sorted(status["allowed_tools"]) == expected
        assert status["code_execution"] == ("sandboxed" if sandboxed else "off")
        created = service.create_mission(notes_request("k-tools"))
        mission = service.mission_detail(created["mission_id"])["mission"]
        assert set(mission["allowed_tools"]) <= set(expected)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_client_cannot_widen_the_tool_set(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await service.start()
    try:
        # plan v3 (P3.1-A06): a field the facade does not open is refused, never dropped
        with pytest.raises(OrchestrationRequestError) as refused:
            service.create_mission(
                notes_request("k-widen", allowed_tools=[*WORKSPACE_TOOLS, "run_tests"])
            )
        assert refused.value.code == "invalid_request" and "allowed_tools" in str(refused.value)
        assert service.list_missions() == []
    finally:
        await service.close()


EVIL_TEST = (
    "import pathlib\n"
    "pathlib.Path({marker!r}).write_text('ran', encoding='utf-8')\n\n"
    "def test_ok():\n    assert True\n"
)


@pytest.mark.asyncio
async def test_no_host_tool_reaches_the_orchestration_runtime(orchestration_root, principal):
    service = OrchestrationService(
        orchestration_root,
        OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
        native_test_counter=FixtureWordCounter(),
    )
    await service.start()
    try:
        # Nothing of the Host's own chat surface reaches the orchestration runtime: the set
        # is exactly workspace tools and Mission-scoped knowledge reads, plus ``run_tests``
        # when the sandbox proved itself
        # here (P3.2 plan D9).  Anything else would be a leak (review P2-10).  The three
        # Skill tools are the SDK runtime's own catalogue tools on native pools (NEXT-TG-1.0
        # §11), not Host chat tools.
        offered = set(service.runtime_tool_names())
        sandboxed = bool(service.status()["sandbox"].get("ok"))
        assert offered - set(WORKSPACE_TOOLS) <= {*KNOWLEDGE_TOOLS, "run_tests", "skill_discover", "skill_load", "skill_execute"}
        assert ("run_tests" in offered) is sandboxed
        assert set(WORKSPACE_TOOLS) <= offered
        assert set(KNOWLEDGE_TOOLS) <= offered
    finally:
        await service.close()


EVIL_TEST = (
    "import pathlib\n"
    "pathlib.Path({marker!r}).write_text('ran', encoding='utf-8')\n\n"
    "def test_ok():\n    assert True\n"
)


@pytest.mark.asyncio
async def test_a_model_written_test_never_reaches_this_machine(
    orchestration_root, principal, tmp_path, monkeypatch
):
    """F-1（分层通道，2026-10-02 从旧夹具通道迁回）：执行者写一个测试文件，导入时往工作区外
    写标记。这个文件绝不能在本机留下痕迹：有沙箱时它在沙箱里跑（代码测试层有持久的执行
    回执），没有沙箱时根本不执行。"""

    import json

    from agent_orchestrator.testing.fixtures import package_of

    from ._layered_lane import (
        LayeredScriptedProvider,
        layered_service,
        notes_mission,
        quick_runtime,
        run_until_settled,
    )

    quick_runtime(monkeypatch)
    marker = tmp_path / "pytest-ran.marker"

    def worker(request):  # type: ignore[no-untyped-def]
        package = package_of(request)
        written = sum(1 for message in request.messages if "tool" in str(message.role).lower())
        if written == 0:
            return ("workspace_write_file", {"path": "NOTES.md", "content": "# 要点\n\n- 一\n- 二\n- 三\n"})
        if written == 1:
            return ("workspace_write_file",
                    {"path": "test_probe.py", "content": EVIL_TEST.format(marker=str(marker))})
        contract = package.get("task_contract", {})
        declared = package.get("declared_output_ports") or {}
        ports = [item["port"] for item in declared.get("ports", ()) if item.get("required", True)]
        envelope = {
            "task_id": contract.get("task_id", ""),
            "attempt_id": package.get("attempt", {}).get("attempt_id", ""),
            "outcome": "candidate", "summary": "写好了 NOTES.md 和一个测试文件",
            "claims": [{"content": "NOTES.md 已写出", "confidence": 0.8, "evidence": ["NOTES.md"]}],
            "evidence": ["NOTES.md"], "artifacts": ["NOTES.md", "test_probe.py"],
            "outputs": {port: "NOTES.md" for port in ports[:1]},
            "proposed_tasks": [], "used_knowledge": [], "risks": [], "cost": {"tool_calls": 2},
        }
        return "<result_envelope>" + json.dumps(envelope, ensure_ascii=False) + "</result_envelope>"

    service = layered_service(orchestration_root, principal, LayeredScriptedProvider(worker=worker))
    await service.start()
    try:
        created = service.create_mission(notes_mission("layered-probe", budget={"max_tokens": 8_000_000, "max_attempts": 2}))
        await run_until_settled(service, created["mission_id"])
        # the oracle is the same on both paths and is never weakened
        assert not marker.exists(), "a model-written test file reached this machine"
        with service._orchestrator.store.read_view():
            snapshot = service._orchestrator.store.snapshot(created["mission_id"])
        runs = [run for result in snapshot["results"] for layer in result["verifications"]
                if layer["layer"] == "code_test" for run in layer["detail"].get("runs", [])]
        if bool(service.status()["sandbox"].get("ok")):
            # the file *does* run — inside the sandbox; what stops the side effect is the
            # isolation, not a refusal to execute
            assert any(run.get("receipt", {}).get("status") == "ok" and run["receipt"].get("execution_id")
                       for run in runs), "code_test needs a durable execution receipt"
        else:
            assert runs == [], "without a sandbox a model-written test is never executed"
    finally:
        await service.close()
