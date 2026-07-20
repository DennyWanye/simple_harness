"""Current DeskPet route policy adapted to generic harness profiles."""

from __future__ import annotations

from ...workflows.routing import WorkflowRoute, route_task
from ..profiles import ProfileRegistry
from ..router import ClassifiedRoute, RouteRequest


class DeskPetRouteClassifier:
    def __init__(self, profiles: ProfileRegistry) -> None:
        self._profiles = profiles

    def classify(self, request: RouteRequest) -> ClassifiedRoute:
        current = route_task(
            request.text,
            mode=request.mode,
            proposed_tools=request.proposed_tools,
            workspace_context=request.workspace_context,
        )
        return ClassifiedRoute(
            profile_key=self._profiles.profile_for_route(current.route.value).profile_key,
            reason=current.reason,
            confidence=current.confidence,
        )
__all__ = ["DeskPetRouteClassifier"]
