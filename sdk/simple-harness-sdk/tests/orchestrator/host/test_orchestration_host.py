# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""宿主入口 ``agent_orchestrator.host``：一个外部 Python 应用只靠它把一个任务跑完。

系统一侧全是产品那一份部署组装（``OrchestrationHost`` 自己拼）；只有模型回复是脚本、用量计数器是
认证测试计数器（经 ``provider=`` / ``counter=`` 注入；正常用法是 ``model=`` 加
``tokenizer_path=``）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_orchestrator.host import (
    NO_COUNTER_REASON,
    HostRequestError,
    HostSettings,
    HostUnavailable,
    OrchestrationHost,
    Principal,
)
from agent_orchestrator.testing.scripted_replies import LayeredScriptedProvider
from agent_orchestrator.testing.word_counter import FixtureWordCounter


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    import agent_orchestrator.orchestrator.event_handler as event_handler

    monkeypatch.setattr(event_handler, "WAIT_BACKOFF_MAX", 0.05)


def _host(root: Path, **options) -> OrchestrationHost:
    return OrchestrationHost(
        root,
        principal=Principal("host-test-user", "测试用户"),
        provider=options.pop("provider", LayeredScriptedProvider()),
        counter=options.pop("counter", FixtureWordCounter()),
        settings=options.pop("settings", HostSettings(lease_seconds=5.0)),
        require_acceptance=False,
        **options,
    )


def test_a_mission_runs_to_completion_through_the_host_entry(tmp_path):
    async def case():
        provider = LayeredScriptedProvider()
        notices = []
        host = await OrchestrationHost.open(
            tmp_path / "root",
            principal=Principal("host-test-user", "测试用户"),
            provider=provider,
            counter=FixtureWordCounter(),
            settings=HostSettings(lease_seconds=5.0),
            require_acceptance=False,
            notify=notices.append,
        )
        try:
            assert host.state == "available"
            status = host.status()
            assert status["available"] and status["default_context_profile_id"]
            assert status["mission_budget_defaults"] == {
                "max_tokens": 20_000_000,
                "max_attempts": 12,
            }

            created = host.create_mission(
                {
                    "goal": "写一份 NOTES.md，列出三个要点",
                    "success_criteria": ["file:NOTES.md"],
                    "idempotency_key": "host-entry-1",
                }
            )
            mission_id = created["mission_id"]
            assert created["created"] is True
            listed = host.missions()
            assert [m["mission_id"] for m in listed] == [mission_id]

            final = await host.run_until_settled(mission_id, timeout=60.0)
            mission = final["snapshot"]["mission"]
            assert str(mission["status"]) == "COMPLETED", mission
            # 门口补的默认值进了任务：预算与运行档
            assert mission["budget"]["max_tokens"] == 20_000_000
            assert (
                mission["final_report"]["runtime_profile_id"]
                == status["default_context_profile_id"]
            )
            assert set(provider.asked) >= {"planner", "worker"}
            # 结束通知到了调用方
            assert any(n.get("mission_id") == mission_id for n in notices)

            events = host.events(mission_id)
            assert events["events"] and events["mission_id"] == mission_id
            artifacts = [a for a in final["snapshot"].get("artifacts", []) if a.get("id")]
            assert artifacts, final["snapshot"].keys()
            body = host.artifact_read(artifacts[0]["id"])
            assert body["artifact_id"] == artifacts[0]["id"] and body["content"] is not None
        finally:
            await host.close()
        assert host.state == "closed" and not host.lock.held

    asyncio.run(case())


def test_the_door_refuses_empty_goals_and_secrets(tmp_path):
    async def case():
        async with _host(tmp_path / "root") as host:
            with pytest.raises(HostRequestError) as refused:
                host.create_mission({"goal": "  ", "success_criteria": ["file:a"]})
            assert refused.value.code == "invalid_request"
            with pytest.raises(HostRequestError) as refused:
                host.create_mission({"goal": "x", "success_criteria": []})
            assert refused.value.code == "invalid_request"
            with pytest.raises(HostRequestError) as refused:
                host.create_mission(
                    {
                        "goal": "用这个 key sk-abcdefghijklmnopqrstuvwxyz0123",
                        "success_criteria": ["file:a"],
                    }
                )
            assert refused.value.code == "secret_rejected"
            # 没有沙箱的部署不接受 pytest: 要求；没启用发布连接器不接受 action:
            if host.policy.code_execution != "sandboxed":
                with pytest.raises(HostRequestError) as refused:
                    host.create_mission({"goal": "x", "success_criteria": ["pytest: tests/"]})
                assert refused.value.code == "local_tests_disabled"
            with pytest.raises(HostRequestError) as refused:
                host.create_mission(
                    {"goal": "x", "success_criteria": ["action:file_publish.publish:out.md"]}
                )
            assert refused.value.code == "action_criteria_disabled"

    asyncio.run(case())


def test_a_second_host_on_the_same_directory_stands_aside(tmp_path):
    async def case():
        async with _host(tmp_path / "root") as first:
            assert first.state == "available"
            second = _host(tmp_path / "root")
            await second.start()
            assert second.state == "unavailable" and "另一个实例" in str(second.reason)
            with pytest.raises(HostRequestError) as refused:
                second.missions()
            assert refused.value.code == "orchestration_unavailable"
        # 关了以后目录可以再用
        async with _host(tmp_path / "root") as again:
            assert again.state == "available"

    asyncio.run(case())


def test_without_a_certified_counter_there_is_no_pool(tmp_path):
    async def case():
        host = _host(tmp_path / "root", counter=None)
        await host.start()
        assert host.state == "unavailable" and host.reason == NO_COUNTER_REASON
        with pytest.raises(HostUnavailable):
            await OrchestrationHost.open(
                tmp_path / "other",
                principal=Principal("p"),
                provider=LayeredScriptedProvider(),
                require_acceptance=False,
            )

    asyncio.run(case())


def test_publish_requires_an_authorised_directory_that_exists(tmp_path):
    async def case():
        missing = tmp_path / "nowhere"
        async with _host(
            tmp_path / "root", settings=HostSettings(lease_seconds=5.0, publish_dir=str(missing))
        ) as host:
            assert host.publish == {
                "enabled": False,
                "root": str(missing),
                "reason": "授权目录不存在",
            }
            assert "file_publish" not in host.connectors

    asyncio.run(case())
