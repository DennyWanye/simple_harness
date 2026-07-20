"""DeskPet product profile catalog outside the product-neutral Kernel."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.ports import DriverStart


class RuntimeAdapterRegistry(Protocol):
    def get(self, workflow_name: str, workflow_version: str) -> Any: ...


def _text(request: DriverStart) -> str:
    return str(request.request_payload.get("text") or "").strip()


def _research_payload(blob_root: str):
    def build(request: DriverStart) -> Mapping[str, Any]:
        payload = dict(request.request_payload)
        topic = str(payload.get("topic") or _text(request)).strip()
        if not topic:
            raise ValueError("research profile requires a topic")
        return {
            "topic": topic,
            "mode": str(payload.get("mode") or "standard"),
            "research_config": dict(payload.get("research_config") or {}),
            "blob_root": str(payload.get("blob_root") or blob_root),
        }

    return build


def _ppt_payload(blob_root: str):
    def build(request: DriverStart) -> Mapping[str, Any]:
        payload = dict(request.request_payload)
        topic = str(payload.get("topic") or _text(request)).strip()
        if not topic:
            raise ValueError("ppt profile requires a topic")
        return {
            "topic": topic,
            "pages": int(payload.get("pages") or 8),
            "depth": str(payload.get("depth") or "deep"),
            "theme": str(payload.get("theme") or "minimal"),
            "image_mode": bool(payload.get("image_mode", True)),
            "title": str(payload.get("title") or topic),
            "author": str(payload.get("author") or "DeskPet"),
            "output_path": payload.get("output_path"),
            "blob_root": str(payload.get("blob_root") or blob_root),
        }

    return build


_CODE_REQUIRED = frozenset(
    {
        "session_ref",
        "capability_snapshot",
        "messages",
        "provider_snapshot",
        "model_snapshot",
        "started_at",
        "request_id",
        "turn_id",
    }
)


def _code_payload(request: DriverStart) -> Mapping[str, Any]:
    payload = dict(request.request_payload)
    missing = sorted(name for name in _CODE_REQUIRED if payload.get(name) is None)
    if missing:
        raise ValueError(f"code profile payload is incomplete: {','.join(missing)}")
    return {
        "request": str(payload.get("request") or _text(request)),
        "session_ref": dict(payload["session_ref"]),
        "capability_snapshot": list(payload["capability_snapshot"]),
        "messages": [dict(message) for message in payload["messages"]],
        "plan_steps": list(payload.get("plan_steps") or [_text(request)]),
        "approval_required": bool(payload.get("approval_required", True)),
        "started_at": float(payload["started_at"]),
        "request_id": str(payload["request_id"]),
        "turn_id": str(payload["turn_id"]),
        "provider_snapshot": dict(payload["provider_snapshot"]),
        "model_snapshot": dict(payload["model_snapshot"]),
    }


def build_product_profile_registry(
    registry: RuntimeAdapterRegistry,
    *,
    blob_root: str | Path,
) -> ProfileRegistry:
    specs = (
        ("workflow.deep_research", "deep_research", "research.deep.v7", "deep_research", "v7", _research_payload(str(blob_root))),
        ("workflow.ppt_pro", "ppt_pro", "ppt.create.v1", "ppt_pro", "v1", _ppt_payload(str(blob_root))),
        ("workflow.code_complex", "code_complex", "code.execute.v1", "code_complex", "v1", _code_payload),
    )
    profiles = [ProfileSpec("react.default", "react", "react")]
    for profile_key, route_tag, workflow_key, workflow_name, version, payload_factory in specs:
        adapter = registry.get(workflow_name, version)
        if adapter is None:
            raise RuntimeError(
                f"required workflow adapter is unavailable: {workflow_name}@{version}"
            )
        profiles.append(
            ProfileSpec(
                profile_key=profile_key,
                route_tag=route_tag,
                driver_kind="workflow",
                capabilities=frozenset({"workflow", route_tag}),
                workflow_key=workflow_key,
                workflow_name=workflow_name,
                workflow_version=version,
                state_factory=adapter.state_factory,
                context_factory=adapter.context_factory,
                request_factory=payload_factory,
            )
        )
    return ProfileRegistry(tuple(profiles))


__all__ = ["build_product_profile_registry"]
