"""LC1: actual Host create/read/restart of the context capacity, no paid provider calls.

2026-09-30 用户决定：旧式执行池（``default`` / ``deepseek-context-*``）已删除，上下文容量
只在原生池（``deepseek-native-256k-v1`` / ``deepseek-native-512k-v1``）上选择。原来
依赖旧式池的"旧空白请求升级""旧协议在长上下文池上跑完全部角色"两条用例随旧池一并删除。
计数器是受信任测试组合里的 ``FixtureWordCounter``，不需要本机的 DeepSeek 分词器。
"""

from dataclasses import replace

import pytest
from ._word_counter import FixtureWordCounter
from deskpet.orchestration.service import (
    OrchestrationRequestError,
    OrchestrationService,
)
from deskpet.orchestration.settings import OrchestrationSettings

from ._support import notes_provider, notes_request


def service(root, principal, settings=None):
    return OrchestrationService(
        root,
        settings or OrchestrationSettings(),
        provider=notes_provider(),
        principal=principal,
        drive=False,
        native_test_counter=FixtureWordCounter(),
    )


def request(key, **extra):
    result = notes_request(key)
    result.pop("budget", None)
    return {**result, **extra}


@pytest.mark.asyncio
async def test_default_and_selected_capacity_persist_and_retry_across_default_change(
    orchestration_root,
    principal,
):
    first = service(orchestration_root, principal)
    await first.start()
    try:
        assert first.status()["available"], first.status()["reason"]
        profiles = first.status()["context_profiles"]
        assert [p["profile_id"] for p in profiles] == ["deepseek-native-256k-v1", "deepseek-native-512k-v1"]
        assert [p["max_input_tokens"] for p in profiles] == [262144, 524288]
        a = first.create_mission(request("long-default"))
        b = first.create_mission(
            request("long-selected", runtime_profile_id="deepseek-native-512k-v1")
        )
        for receipt, identifier, tokens in [(a, "deepseek-native-256k-v1", 262144),
                                            (b, "deepseek-native-512k-v1", 524288)]:
            detail = first.mission_detail(receipt["mission_id"])
            assert detail["runtime_context"]["profile_id"] == identifier
            assert detail["runtime_context"]["max_input_tokens"] == tokens
            assert detail["mission"]["budget"]["max_tokens"] == 20_000_000
        before = first.mission_detail(a["mission_id"])["runtime_context"]
        # A pool that does not exist (the deleted legacy ids included) is refused.
        for bad in ("default", "deepseek-context-256k-v1", "missing", 524288, None):
            with pytest.raises(OrchestrationRequestError):
                first.create_mission(request("bad", runtime_profile_id=bad))
        assert len(first.list_missions()) == 2
    finally:
        await first.close()
    cold = service(
        orchestration_root,
        principal,
        replace(OrchestrationSettings(), context_input_tokens=524288),
    )
    await cold.start()
    try:
        assert cold.status()["available"], cold.status()["reason"]
        retry = cold.create_mission(request("long-default"))
        assert retry == {**a, "created": False}
        assert cold.mission_detail(a["mission_id"])["runtime_context"] == before
        new = cold.create_mission(request("new-default"))
        assert (
            cold.mission_detail(new["mission_id"])["runtime_context"]["max_input_tokens"]
            == 524288
        )
        with pytest.raises(OrchestrationRequestError) as err:
            cold.create_mission(
                request("long-default", runtime_profile_id="deepseek-native-512k-v1")
            )
        assert err.value.code == "conflict"
    finally:
        await cold.close()
