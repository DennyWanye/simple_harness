from __future__ import annotations

import pytest

from deskpet.harness.adapters.routing import DeskPetRouteClassifier, deskpet_route_profiles
from deskpet.harness.router import (
    ClassifiedRoute,
    RegisteredRouter,
    RouteProfile,
    RouteRequest,
    RouteUnavailable,
)


ALL = frozenset({"workflow", "deep_research", "ppt_pro", "code_complex"})


@pytest.mark.parametrize(
    ("text", "workspace", "profile"),
    [
        ("不要修改任何文件，只解释这个测试为什么失败", True, "react.default"),
        ("请深入调研本地 AI 编程助手", False, "workflow.deep_research"),
        ("帮我生成一份 DeskPet 架构 PPT", False, "workflow.ppt_pro"),
        ("修复这个模块并运行测试", True, "workflow.code_complex"),
    ],
)
def test_registered_route_profiles_cover_golden_intents(text: str, workspace: bool, profile: str) -> None:
    router = RegisteredRouter(DeskPetRouteClassifier(), deskpet_route_profiles())
    decision = router.route(
        RouteRequest(text, "req-1", "turn-1", workspace_context=workspace),
        available_capabilities=ALL,
    )
    assert decision.profile_key == profile


def test_code_venue_research_and_ppt_do_not_fall_back_to_react() -> None:
    router = RegisteredRouter(DeskPetRouteClassifier(), deskpet_route_profiles())
    for text, expected in (
        ("深入调研 Tokio 的调度模型", "workflow.deep_research"),
        ("做一份 Tokio 架构 PPT", "workflow.ppt_pro"),
    ):
        decision = router.route(
            RouteRequest(text, f"req-{expected}", "turn-1", venue="code", workspace_context=True),
            available_capabilities=ALL,
        )
        assert decision.profile_key == expected


def test_selected_durable_profile_fails_closed_when_capability_is_missing() -> None:
    router = RegisteredRouter(DeskPetRouteClassifier(), deskpet_route_profiles())
    with pytest.raises(RouteUnavailable, match="deep_research"):
        router.route(
            RouteRequest("做一次深度调研", "req-1", "turn-1"),
            available_capabilities=frozenset({"workflow"}),
        )


def test_router_calls_classifier_once_and_keeps_a_stable_decision_key() -> None:
    class CountingClassifier:
        calls = 0

        def classify(self, request: RouteRequest) -> ClassifiedRoute:
            self.calls += 1
            return ClassifiedRoute("react.default", "test", 1.0)

    classifier = CountingClassifier()
    router = RegisteredRouter(classifier, [RouteProfile("react.default", "react")])
    request = RouteRequest("hello", "req-1", "turn-1")
    first = router.route(request, available_capabilities=frozenset())

    assert classifier.calls == 1
    assert first.decision_key.startswith("route:")
