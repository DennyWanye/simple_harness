"""Current DeskPet route policy adapted to generic harness profiles."""

from __future__ import annotations

from ...workflows.routing import WorkflowRoute, route_task
from ..router import ClassifiedRoute, RouteProfile, RouteRequest


_PROFILE_BY_ROUTE = {
    WorkflowRoute.REACT: "react.default",
    WorkflowRoute.DEEP_RESEARCH: "workflow.deep_research",
    WorkflowRoute.PPT_PRO: "workflow.ppt_pro",
    WorkflowRoute.CODE_COMPLEX: "workflow.code_complex",
}


class DeskPetRouteClassifier:
    def classify(self, request: RouteRequest) -> ClassifiedRoute:
        current = route_task(
            request.text,
            mode=request.mode,
            proposed_tools=request.proposed_tools,
            workspace_context=request.workspace_context,
        )
        return ClassifiedRoute(
            profile_key=_PROFILE_BY_ROUTE[current.route],
            reason=current.reason,
            confidence=current.confidence,
        )


def deskpet_route_profiles() -> tuple[RouteProfile, ...]:
    return (
        RouteProfile("react.default", "react"),
        RouteProfile(
            "workflow.deep_research",
            "workflow",
            frozenset({"workflow", "deep_research"}),
        ),
        RouteProfile(
            "workflow.ppt_pro",
            "workflow",
            frozenset({"workflow", "ppt_pro"}),
        ),
        RouteProfile(
            "workflow.code_complex",
            "workflow",
            frozenset({"workflow", "code_complex"}),
        ),
    )


__all__ = ["DeskPetRouteClassifier", "deskpet_route_profiles"]
