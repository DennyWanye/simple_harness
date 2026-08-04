"""Single-decision routing without product branches in the Kernel."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping, Protocol

from .contracts import RunRequest
from .profiles import ProfileRegistry, ProfileSpec


class RouteUnavailable(RuntimeError):
    """Raised when a selected profile cannot run without semantic downgrade."""


@dataclass(frozen=True, slots=True)
class ClassifiedRoute:
    profile_key: str
    reason: str
    confidence: float

    def __post_init__(self) -> None:
        if not self.profile_key.strip() or not self.reason.strip():
            raise ValueError("classified route must identify profile and reason")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("route confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class RouteDecision:
    decision_key: str
    profile_key: str
    driver_kind: str
    reason: str
    confidence: float
    required_capabilities: frozenset[str]


class RouteClassifier(Protocol):
    def classify(self, request: RunRequest) -> ClassifiedRoute: ...


class RegisteredRouter:
    """Resolve exactly one classifier result against an immutable catalog."""

    def __init__(self, classifier: RouteClassifier, profiles: ProfileRegistry) -> None:
        self._classifier = classifier
        self._profiles = profiles.specs

    @property
    def profiles(self) -> Mapping[str, ProfileSpec]:
        return self._profiles

    def route(
        self,
        request: RunRequest,
        *,
        available_capabilities: frozenset[str],
    ) -> RouteDecision:
        classified = self._classifier.classify(request)
        profile = self._profiles.get(classified.profile_key)
        if profile is None:
            raise RouteUnavailable(f"route profile is not registered: {classified.profile_key}")
        missing = profile.capabilities - available_capabilities
        if missing:
            names = ",".join(sorted(missing))
            raise RouteUnavailable(f"route profile {profile.profile_key} is unavailable: {names}")
        digest = hashlib.sha256(
            f"{request.request_id}\0{request.turn_id}\0{profile.profile_key}".encode("utf-8")
        ).hexdigest()
        return RouteDecision(
            decision_key=f"route:{digest}",
            profile_key=profile.profile_key,
            driver_kind=profile.driver_kind,
            reason=classified.reason,
            confidence=classified.confidence,
            required_capabilities=profile.capabilities,
        )
