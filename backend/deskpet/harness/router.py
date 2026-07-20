"""Single-decision routing without product branches in the Kernel."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterable, Mapping, Protocol


class RouteUnavailable(RuntimeError):
    """Raised when a selected profile cannot run without semantic downgrade."""


@dataclass(frozen=True, slots=True)
class RouteRequest:
    text: str
    request_id: str
    turn_id: str
    venue: str = "text"
    mode: str = "auto"
    workspace_context: bool = False
    proposed_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("text", "request_id", "turn_id", "venue"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")


@dataclass(frozen=True, slots=True)
class RouteProfile:
    key: str
    driver_kind: str
    required_capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.driver_kind.strip():
            raise ValueError("route profile key and driver_kind are required")


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
    def classify(self, request: RouteRequest) -> ClassifiedRoute: ...


class RegisteredRouter:
    """Resolve exactly one classifier result against an immutable catalog."""

    def __init__(self, classifier: RouteClassifier, profiles: Iterable[RouteProfile]) -> None:
        catalog: dict[str, RouteProfile] = {}
        for profile in profiles:
            if profile.key in catalog:
                raise ValueError(f"duplicate route profile: {profile.key}")
            catalog[profile.key] = profile
        if not catalog:
            raise ValueError("at least one route profile is required")
        self._classifier = classifier
        self._profiles: Mapping[str, RouteProfile] = MappingProxyType(catalog)

    @property
    def profiles(self) -> Mapping[str, RouteProfile]:
        return self._profiles

    def route(
        self,
        request: RouteRequest,
        *,
        available_capabilities: frozenset[str],
    ) -> RouteDecision:
        classified = self._classifier.classify(request)
        profile = self._profiles.get(classified.profile_key)
        if profile is None:
            raise RouteUnavailable(f"route profile is not registered: {classified.profile_key}")
        missing = profile.required_capabilities - available_capabilities
        if missing:
            names = ",".join(sorted(missing))
            raise RouteUnavailable(f"route profile {profile.key} is unavailable: {names}")
        digest = hashlib.sha256(
            f"{request.request_id}\0{request.turn_id}\0{profile.key}".encode("utf-8")
        ).hexdigest()
        return RouteDecision(
            decision_key=f"route:{digest}",
            profile_key=profile.key,
            driver_kind=profile.driver_kind,
            reason=classified.reason,
            confidence=classified.confidence,
            required_capabilities=profile.required_capabilities,
        )


__all__ = [
    "ClassifiedRoute",
    "RegisteredRouter",
    "RouteClassifier",
    "RouteDecision",
    "RouteProfile",
    "RouteRequest",
    "RouteUnavailable",
]
