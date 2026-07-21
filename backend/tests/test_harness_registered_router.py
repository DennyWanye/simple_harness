from __future__ import annotations

from types import SimpleNamespace

import pytest

from deskpet.harness.adapters.product_profiles import build_product_profile_registry
from deskpet.harness.adapters.routing import DeskPetRouteClassifier
from deskpet.harness.contracts import RunRequest
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.router import (
    ClassifiedRoute,
    RegisteredRouter,
    RouteUnavailable,
)


ALL = frozenset({"workflow", "deep_research", "ppt_pro", "code_complex"})


class _AdapterRegistry:
    def get(self, workflow_name, workflow_version):
        return SimpleNamespace(state_factory=dict, context_factory=dict)


def _product_router() -> RegisteredRouter:
    profiles = build_product_profile_registry(_AdapterRegistry(), blob_root=".")
    return RegisteredRouter(DeskPetRouteClassifier(profiles), profiles)


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
    router = _product_router()
    decision = router.route(
        RunRequest(text, "req-1", "turn-1", workspace_context=workspace),
        available_capabilities=ALL,
    )
    assert decision.profile_key == profile


def test_code_venue_research_and_ppt_do_not_fall_back_to_react() -> None:
    router = _product_router()
    for text, expected in (
        ("深入调研 Tokio 的调度模型", "workflow.deep_research"),
        ("做一份 Tokio 架构 PPT", "workflow.ppt_pro"),
    ):
        decision = router.route(
            RunRequest(text, f"req-{expected}", "turn-1", venue="code", workspace_context=True),
            available_capabilities=ALL,
        )
        assert decision.profile_key == expected


def test_selected_durable_profile_fails_closed_when_capability_is_missing() -> None:
    router = _product_router()
    with pytest.raises(RouteUnavailable, match="deep_research"):
        router.route(
            RunRequest("做一次深度调研", "req-1", "turn-1"),
            available_capabilities=frozenset({"workflow"}),
        )


def test_router_calls_classifier_once_and_keeps_a_stable_decision_key() -> None:
    class CountingClassifier:
        calls = 0

        def classify(self, request: RunRequest) -> ClassifiedRoute:
            self.calls += 1
            return ClassifiedRoute("react.default", "test", 1.0)

    classifier = CountingClassifier()
    profiles = ProfileRegistry((ProfileSpec("react.default", "react", "react"),))
    router = RegisteredRouter(classifier, profiles)
    request = RunRequest("hello", "req-1", "turn-1")
    first = router.route(request, available_capabilities=frozenset())

    assert classifier.calls == 1
    assert first.decision_key.startswith("route:")
