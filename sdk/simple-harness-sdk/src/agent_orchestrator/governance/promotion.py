# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Policy versions (ORCH-BUILD §11; plan D9-1' / D9-2').

A *policy* is the versioned layer over the deployment configuration: the §29.3 allocator
weights, a few scheduling knobs, routing overrides and the prompt version of each role.
It is always stored **resolved** — every whitelisted item carries an explicit value taken
from the code constants and the deployment configuration at the time — so a version id
(the hash of the resolved parameters) always means the same behaviour.  Anything outside
the whitelist (safety boundaries, budgets, the deployment policy, timeouts,
layer switches) can never be part of a policy.

A library has one version: the one seeded from the deployment when the library was
created.  There is no proposal, evaluation or promotion (removed 2026-10-02); a later
configuration change is reported as drift.

Pure functions only: the registry itself is written by the Commit Service
(``orchestrator/policy_commits.py``)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from simple_harness.contracts import canonical_json

if TYPE_CHECKING:
    from ..storage.store import Store

PROMOTION_VERSION = "promotion-v1"
DEPLOYMENT_TIMELINE = "deployment"  # the sentinel event timeline of deployment-level facts

INT_RANGES: dict[str, tuple[int, int | None]] = {
    "exploration_slots": (0, 2),
    "mission_concurrency": (1, None),  # the upper bound is the deployment's max_concurrency
}
FLOAT_RANGES: dict[str, tuple[float, float]] = {"aging_window_seconds": (60.0, 1800.0)}
PROMOTABLE = frozenset(
    {"allocator_weights", *INT_RANGES, *FLOAT_RANGES, "routing", "prompt_versions"}
)
# named so a refusal can say *why*: core safety, budget and deployment rules (plan D9-1)
NON_PROMOTABLE = frozenset(
    {
        "permissions",
        "tool_gateway",
        "idempotency",
        "approvals",
        "deployment_policy",
        "secret_checks",
        "format_check",
        "rule_check",
        "code_test",
        "human_review",
        "budgets",
        "budget",
        "global_budget",
        "task_max_tokens",  # fixed per-leaf allowance (2026-09-25)
        "planner_reserve_tokens",
        "critic_reserve_tokens",
        "attempt_reserve_tokens",
        "knowledge_sharing",
        "max_concurrency",
        "max_running_attempts",
        "lease_seconds",
        "stall_seconds",
        "provider_call_seconds",  # 2026-10-10: the in-flight model-call bound, same family as stall
        "test_timeout_seconds",
        "turn_deadline_seconds",
        "verification_policy",
        "enabled_connectors",
        "allowed_tools",
    }
)


class PolicyError(ValueError):
    """A policy that may not exist: an item outside the whitelist or out of range."""


def _hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def builtin_weights() -> dict[str, float]:
    from ..scheduling.allocator import WEIGHTS

    return {name: float(value) for name, value in WEIGHTS.items()}


def builtin_prompt_versions() -> dict[str, str]:
    from ..runtime.role_templates import ROLES

    return {name: template.prompt_version for name, template in sorted(ROLES.items())}


def resolve_params(
    config: Any, partial: Mapping[str, Any] | None = None, *, routing: Any = None
) -> dict[str, Any]:
    """The complete parameter set: the deployment's values for every whitelisted item
    (plan D9-1'), then ``partial`` on top.  An item outside the whitelist is refused."""

    resolved: dict[str, Any] = {
        "allocator_weights": builtin_weights(),
        "exploration_slots": int(config.exploration_slots),
        "mission_concurrency": int(config.max_concurrency),
        "aging_window_seconds": float(config.aging_window_seconds),
        "routing": {
            "escalate_after_failures": int(getattr(routing, "escalate_after_failures", 1) or 1),
            "by_task_kind": {
                str(k): str(v) for k, v in dict(getattr(routing, "by_task_kind", {}) or {}).items()
            },
        },
        "prompt_versions": builtin_prompt_versions(),
    }
    return overlay(resolved, partial or {})


def overlay(base: Mapping[str, Any], partial: Mapping[str, Any]) -> dict[str, Any]:
    refused = sorted(set(partial) - PROMOTABLE)
    if refused:
        core = [k for k in refused if k in NON_PROMOTABLE]
        raise PolicyError(
            f"not promotable: {refused}"
            + (f" ({core} are core safety / budget / deployment rules)" if core else "")
        )
    merged = {key: value for key, value in base.items()}
    for key, value in partial.items():
        if key == "allocator_weights":
            merged[key] = {**dict(base[key]), **{str(k): float(v) for k, v in dict(value).items()}}
        elif key == "routing":
            routing = dict(value)
            merged[key] = {
                "escalate_after_failures": int(
                    routing.get("escalate_after_failures", base[key]["escalate_after_failures"])
                ),
                "by_task_kind": {
                    str(k): str(v)
                    for k, v in dict(routing.get("by_task_kind", base[key]["by_task_kind"])).items()
                },
            }
        elif key == "prompt_versions":
            merged[key] = {**dict(base[key]), **{str(k): str(v) for k, v in dict(value).items()}}
        elif key in FLOAT_RANGES:
            merged[key] = float(value)
        else:
            merged[key] = int(value)
    return merged


def params_hash(params: Mapping[str, Any]) -> str:
    return _hash(dict(params))


def version_id(params: Mapping[str, Any]) -> str:
    """Content-addressed: the same resolved parameters are the same version (D9-2')."""

    return "policy-" + params_hash(params)[:16]


def weights_hash(weights: Mapping[str, float]) -> str:
    return _hash({k: round(float(v), 6) for k, v in weights.items()})[:16]


def diff_params(a: Mapping[str, Any], b: Mapping[str, Any]) -> list[dict[str, Any]]:
    def flat(params: Mapping[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in params.items():
            if isinstance(value, Mapping):
                for sub, item in value.items():
                    if isinstance(item, Mapping):
                        for leaf, leaf_value in item.items():
                            out[f"{key}.{sub}.{leaf}"] = leaf_value
                    else:
                        out[f"{key}.{sub}"] = item
            else:
                out[key] = value
        return out

    fa, fb = flat(a), flat(b)
    return [
        {"key": key, "a": fa.get(key), "b": fb.get(key)}
        for key in sorted(set(fa) | set(fb))
        if fa.get(key) != fb.get(key)
    ]


def code_versions() -> dict[str, str]:
    from ..version import __version__ as orchestrator_version

    try:
        from simple_harness.version import __version__ as sdk_version
    except ImportError:  # pragma: no cover - the SDK always ships its version module
        sdk_version = "unknown"
    return {"agent_orchestrator": str(orchestrator_version), "simple_harness": str(sdk_version)}


def interpreter_versions() -> dict[str, str]:
    """The code that reads a policy (plan D9-1' / D9-4'): a version records them so a
    Mission resumed under other code can say so instead of changing silently."""

    from ..context.context_builder import CONTEXT_BUILDER_VERSION
    from ..context.retrieval import RETRIEVAL_VERSION
    from ..runtime.model_router import ROUTER_VERSION
    from ..scheduling.allocator import ALLOCATOR_VERSION
    from ..verification.verifier_router import VERIFIER_VERSION

    return {
        "allocator": ALLOCATOR_VERSION,
        "model_router": ROUTER_VERSION,
        "retrieval": RETRIEVAL_VERSION,
        "context_builder": CONTEXT_BUILDER_VERSION,
        "verifier": VERIFIER_VERSION,
        "promotion": PROMOTION_VERSION,
        **code_versions(),
    }


# ------------------------------------------------------------------ registry projection
def registry_projection(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Fold the deployment timeline into registry states (step 8 lesson: every event
    that changes formal state is projected, and the projection is compared with the
    tables)."""

    versions: dict[str, str] = {}
    active: str | None = None
    for event in sorted(events, key=lambda e: int(e.get("seq") or 0)):
        kind, p = str(event.get("type")), dict(event.get("payload") or {})
        if kind == "PolicySeeded":
            active = str(p["version_id"])
            versions[active] = "ACTIVE"
    return {"versions": versions, "active": active}


def registry_consistency(store: Store) -> list[dict[str, Any]]:
    """Every difference between the folded deployment timeline and the registry tables."""

    events = [e.to_json() | {"seq": e.seq} for e in store.iter_events(DEPLOYMENT_TIMELINE)]
    folded = registry_projection(events)
    problems: list[dict[str, Any]] = []
    for version in store.list_policy_versions():
        vid, status = version["version_id"], version["status"]
        if status == "LEGACY" or version.get("source") == "sandbox":  # pinned, not promoted
            continue
        if folded["versions"].get(vid) != status:
            problems.append(
                {
                    "object": "version",
                    "id": vid,
                    "table": status,
                    "events": folded["versions"].get(vid),
                }
            )
    active = store.active_policy()
    if (None if active is None else active["version_id"]) != folded["active"]:
        problems.append(
            {
                "object": "active",
                "table": active and active["version_id"],
                "events": folded["active"],
            }
        )
    return problems


__all__ = (
    "DEPLOYMENT_TIMELINE",
    "NON_PROMOTABLE",
    "PROMOTABLE",
    "PROMOTION_VERSION",
    "PolicyError",
    "code_versions",
    "diff_params",
    "interpreter_versions",
    "overlay",
    "params_hash",
    "registry_consistency",
    "registry_projection",
    "resolve_params",
    "version_id",
    "weights_hash",
)
