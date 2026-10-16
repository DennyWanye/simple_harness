"""SDK 能力桥接适配器：把冻结的运行工具权限接到 SDK 的能力查询与激活（从 tool_authority 挪出）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from simple_harness import RunId
from simple_harness.tools import CatalogRunToolExposure

from deskpet.sdk_adapters.run_route_state import ROUTED_TASK_STATE
from deskpet.sdk_adapters.tool_authority import (
    _DEFAULT_UNAVAILABLE_NEXT_ACTION,
    _UNSCOPED_ALTERNATIVES_ACTION,
    _UNSCOPED_PREPARE_ACTION,
    _UNSCOPED_UNREACHABLE_ACTION,
    _UNSCOPED_UNROUTED_ACTION,
    MAX_SDK_SEARCH_PAGE,
    PROJECT_EFFECT_ROUTE_REASON,
    UNAVAILABLE_CAPABILITY_NEXT_ACTIONS,
    WORKSPACE_READ_TOOL_NAMES,
    WORKSPACE_UNSCOPED_REASON,
    WORKSPACE_WRITE_TOOL_NAMES,
    SdkRunToolAuthorityRegistry,
    SdkRunToolAuthorityV1,
    _FrozenCapabilitySpec,
)
from deskpet.tools.capabilities import (
    PreparedToolSet,
    ToolCapabilityBridgeService,
    ToolEligibilityContext,
    ToolExecutionContext,
    ToolPolicySnapshot,
    reset_tool_execution_context,
    set_tool_execution_context,
)


class _FrozenCapabilityRegistry:
    """The exact SDK generation behind ``ToolCapabilityBridgeService``."""

    def __init__(
        self,
        authorities: SdkRunToolAuthorityRegistry,
        context_getter: Callable[[], ToolExecutionContext],
    ) -> None:
        self._authorities = authorities
        self._context_getter = context_getter

    def _authority(self) -> SdkRunToolAuthorityV1:
        return self._authorities.resolve(self._context_getter().run_id)

    def read_policy_snapshot(self, *, strict: bool = True) -> ToolPolicySnapshot:
        del strict
        authority = self._authority()
        return ToolPolicySnapshot(fingerprint=authority.prepared_tool_set.policy_fingerprint)

    def get(self, name: str) -> _FrozenCapabilitySpec | None:
        return self._authority().specs.get(str(name))

    def validate_prepared_tool_set(
        self,
        prepared: PreparedToolSet,
        *,
        eligibility: ToolEligibilityContext,
    ) -> None:
        authority = self._authority()
        if prepared.scope_id != authority.prepared_tool_set.scope_id:
            raise RuntimeError("capability_stale")
        if (
            eligibility.session_id != authority.session_id
            or eligibility.request_id != authority.request_id
        ):
            raise RuntimeError("capability_denied")
        for ref in (
            *(item.ref for item in (*prepared.direct, *prepared.activated)),
            *prepared.deferred,
        ):
            spec = authority.specs.get(ref.name)
            if spec is None or spec.schema_hash != ref.schema_hash:
                raise RuntimeError("capability_stale")


class SdkCapabilityBridgeAdapter:
    """Bind the real product capability service to the active SDK Run scope."""

    def __init__(
        self,
        authorities: SdkRunToolAuthorityRegistry,
        context_getter: Callable[[], ToolExecutionContext],
    ) -> None:
        self._authorities = authorities
        self._context_getter = context_getter
        self._service = ToolCapabilityBridgeService(
            _FrozenCapabilityRegistry(authorities, context_getter),
            authorities.scope_store,
        )

    def _invoke(self, method: str, *args: Any, **kwargs: Any) -> Any:
        context = self._context_getter()
        token = set_tool_execution_context(context)
        try:
            return getattr(self._service, method)(*args, **kwargs)
        finally:
            reset_tool_execution_context(token)

    def search(self, query: str, *, limit: int = 10, cursor: int = 0) -> dict[str, Any]:
        return self._invoke("search", query, limit=limit, cursor=cursor)

    def suggestions(self, capability_id: str, *, limit: int = 3) -> list[dict[str, Any]]:
        return self._invoke("suggestions", capability_id, limit=limit)

    def describe(self, capability_id: str) -> dict[str, Any]:
        return self._invoke("describe", capability_id)

    def activate(self, capability_id: str, schema_hash: str, describe_nonce: str) -> Any:
        context = self._context_getter()
        proposal = self._invoke("activate", capability_id, schema_hash, describe_nonce)
        store = self._authorities.scope_store
        current = store.get(
            context.scope_id,
            session_id=context.session_id,
            request_id=context.request_id,
        )
        if current is None or current.prepared.revision != proposal.base_scope_revision:
            raise RuntimeError("activation_scope_stale")
        self._authorities.commit_activation(
            context.run_id,
            current.prepared.activate(proposal.prepared_capability),
        )
        return proposal


@dataclass(frozen=True, slots=True)
class RunAvailabilityFacts:
    """The Run-start facts the ``workspace_unscoped`` guidance may depend on.

    Everything here is frozen at Run start, which is exactly why the guidance
    can promise what it promises: no in-Run action changes any of it.
    """

    routed: bool = False
    workspace_bound: bool = False
    exposed_tool_names: frozenset[str] = frozenset()

    def alternatives_for(self, capability_id: str) -> tuple[str, ...]:
        """Capabilities of the refused one's own class that this Run can activate.

        A read request must never be answered with ``edit_file``: naming a write
        tool as the substitute for ``read_file`` is the same class of wrong
        answer as naming ``read_file`` itself.
        """

        name = str(capability_id).rpartition(":")[2]
        if name in WORKSPACE_READ_TOOL_NAMES:
            family: tuple[str, ...] = WORKSPACE_READ_TOOL_NAMES
        elif name in WORKSPACE_WRITE_TOOL_NAMES:
            family = WORKSPACE_WRITE_TOOL_NAMES
        else:
            # A non-builtin (MCP) capability has no product class; every
            # workspace file tool this Run exposes is a candidate.
            family = (*WORKSPACE_READ_TOOL_NAMES, *WORKSPACE_WRITE_TOOL_NAMES)
        return tuple(
            f"builtin:{item}" for item in family if item != name and item in self.exposed_tool_names
        )


def unavailable_capability_next_action(
    reason: str,
    *,
    capability_id: str = "",
    facts: RunAvailabilityFacts | None = None,
) -> str:
    """One executable next step for a capability this Run cannot activate.

    Never names ``capability_id`` itself as the way forward (incident Z), and
    never names a capability this Run also reports unavailable.
    """

    reason = str(reason)
    if reason == WORKSPACE_UNSCOPED_REASON:
        if facts is None:
            return _UNSCOPED_UNREACHABLE_ACTION
        alternatives = facts.alternatives_for(capability_id)
        if alternatives:
            return _UNSCOPED_ALTERNATIVES_ACTION.format(names=", ".join(alternatives))
        if facts.routed and not facts.workspace_bound:
            return _UNSCOPED_PREPARE_ACTION
        if not facts.routed:
            return _UNSCOPED_UNROUTED_ACTION
        return _UNSCOPED_UNREACHABLE_ACTION
    return UNAVAILABLE_CAPABILITY_NEXT_ACTIONS.get(reason, _DEFAULT_UNAVAILABLE_NEXT_ACTION)


class SdkRuntimeCapabilityBridgeAdapter:
    """Delegate discovery and activation to the SDK-owned Runtime catalog."""

    def __init__(
        self,
        authorities: SdkRunToolAuthorityRegistry,
        context_getter: Callable[[], ToolExecutionContext],
        *,
        route_state_memo: Any = None,
    ) -> None:
        self._authorities = authorities
        self._context_getter = context_getter
        # HM-TO-A6 incident A: written by ``ProductRunContextAuthority`` for the
        # turn whose provider request produced this very tool call.  ``None``
        # (unwired, or no snapshot prepared yet) keeps the previous behaviour —
        # this memo shapes disclosure, never authority.
        self._route_state_memo = route_state_memo

    def _binding(self) -> tuple[RunId, CatalogRunToolExposure]:
        run_id = RunId(self._context_getter().run_id)
        return run_id, self._authorities.resolve_exposure(run_id)

    def _availability_facts(self, run_id: RunId) -> RunAvailabilityFacts:
        """Run-start facts behind the ``workspace_unscoped`` guidance.

        Incident Z: the guidance must describe *this* Run, so it is derived from
        the frozen authority rather than from a static table.  A Run without an
        authority record (restart, or a bridge wired without one) falls back to
        the "cannot be reached here" text, which is never wrong.
        """

        try:
            authority = self._authorities.resolve(run_id)
        except KeyError:
            return RunAvailabilityFacts()
        work = authority.task_work_context
        return RunAvailabilityFacts(
            routed=getattr(work, "task_scope_id", None) is not None,
            workspace_bound=bool(getattr(work, "workspace_root", None)),
            exposed_tool_names=frozenset(str(name) for name in authority.specs),
        )

    def unavailable_next_action(self, capability_id: str, reason: str) -> str:
        """Model-facing next step for a refusal, shaped by this Run's facts."""

        run_id = RunId(self._context_getter().run_id)
        return unavailable_capability_next_action(
            reason,
            capability_id=str(capability_id),
            facts=self._availability_facts(run_id),
        )

    def _route_blocked_capabilities(self, run_id: RunId) -> frozenset[str]:
        """PROJECT_EFFECT capability ids that this Run's route can never run."""

        if self._route_state_memo is None:
            return frozenset()
        state = self._route_state_memo.read(run_id)
        if state is None or str(state) == ROUTED_TASK_STATE:
            return frozenset()
        return self._authorities.project_effect_capabilities(run_id)

    def _unavailable_with_route(self, run_id: RunId) -> dict[str, str]:
        """Descriptor-only reasons plus this turn's route-blocked capabilities.

        A capability that already carries its own availability_reason keeps it:
        the durable Run-start reason is the more specific fact.
        """

        reasons = self._authorities.unavailable_capabilities(run_id)
        merged = dict(reasons)
        for capability_id in self._route_blocked_capabilities(run_id):
            merged.setdefault(capability_id, PROJECT_EFFECT_ROUTE_REASON)
        return merged

    def search(self, query: str, *, limit: int = 10, cursor: int = 0) -> dict[str, Any]:
        run_id, exposure = self._binding()
        unavailable = self._unavailable_with_route(run_id)
        if not unavailable:
            page = exposure.search(run_id, query, limit=limit, cursor=cursor)
            return {
                "matches": [item.to_json() for item in page.items],
                "count": len(page.items),
                "query": str(query),
                "next_cursor": page.next_cursor,
            }
        # Descriptor-only capabilities stay searchable so the model learns why
        # they are absent, but they must never crowd out activatable matches:
        # the SDK ranks by token hits, and a long MCP description outranked
        # every built-in file tool in UI-B.  Re-rank on the Host side (stable
        # partition, SDK order preserved within each group) and paginate the
        # re-ranked list so cursor semantics stay consistent.
        facts = self._availability_facts(run_id)
        activatable: list[dict[str, Any]] = []
        descriptor_only: list[dict[str, Any]] = []
        sdk_cursor = 0
        while True:
            page = exposure.search(run_id, query, limit=MAX_SDK_SEARCH_PAGE, cursor=sdk_cursor)
            for item in page.items:
                value = item.to_json()
                reason = unavailable.get(str(value.get("capability_id")))
                if reason is None:
                    activatable.append(value)
                else:
                    descriptor_only.append(
                        {
                            **value,
                            "activatable": False,
                            "availability_reason": reason,
                            # Incident Z: the model looped on tool_search because
                            # the per-match hint told it to activate the very
                            # capability the same page reported unavailable.
                            "next_action": unavailable_capability_next_action(
                                reason,
                                capability_id=str(value.get("capability_id") or ""),
                                facts=facts,
                            ),
                        }
                    )
            if page.next_cursor is None:
                break
            sdk_cursor = page.next_cursor
        ranked = [*activatable, *descriptor_only]
        start = max(0, int(cursor))
        stop = start + max(1, int(limit))
        matches = ranked[start:stop]
        result: dict[str, Any] = {
            "matches": matches,
            "count": len(matches),
            "query": str(query),
            "next_cursor": stop if stop < len(ranked) else None,
        }
        unavailable_count = sum(1 for item in matches if "availability_reason" in item)
        if unavailable_count:
            result["unavailable_count"] = unavailable_count
            reasons = sorted(
                {
                    str(item["availability_reason"])
                    for item in matches
                    if "availability_reason" in item
                }
            )
            first = next(
                str(item.get("capability_id") or "")
                for item in matches
                if str(item.get("availability_reason") or "") == reasons[0]
            )
            result["next_action"] = (
                f"{unavailable_count} match(es) carry availability_reason "
                f"({', '.join(reasons)}) and cannot be activated in this Run; "
                "prefer matches without availability_reason. "
                + unavailable_capability_next_action(reasons[0], capability_id=first, facts=facts)
            )
        return result

    def suggestions(self, capability_id: str, *, limit: int = 3) -> list[dict[str, Any]]:
        query = str(capability_id).replace(":", " ")
        return list(self.search(query, limit=limit)["matches"])

    def describe(self, capability_id: str) -> dict[str, Any]:
        run_id, exposure = self._binding()
        value = exposure.describe(run_id, str(capability_id)).to_json()
        descriptor = value["descriptor"]
        if not isinstance(descriptor, dict):
            raise TypeError("catalog_descriptor_invalid")
        projection = value.get("projection")
        if not isinstance(projection, dict):
            raise TypeError("catalog_projection_invalid")
        # The SDK projection carries its own provider-input-schema hash while
        # activation is intentionally bound to the broader capability hash.
        # Exposing both values under the same ``schema_hash`` label made real
        # models occasionally copy projection.schema_hash into tool_activate,
        # which correctly failed closed but looked like an authorization hang.
        # The provider hash is not an activation input, so omit it from the
        # model-facing projection and expose exactly one schema_hash to copy.
        value["projection"] = {
            key: item for key, item in projection.items() if key != "schema_hash"
        }
        value["schema_hash"] = value["capability_hash"]
        value["describe_nonce"] = value["nonce"]
        value["capability_id"] = descriptor["capability_id"]
        reason = self._unavailable_with_route(run_id).get(str(descriptor["capability_id"]))
        if reason is not None:
            # Descriptor-only in this Run: say so before the model wastes a
            # tool_activate turn (UI-B looped three times on exactly this).
            value["activatable"] = False
            value["activation_required"] = False
            value["availability_reason"] = reason
            value["next_action"] = (
                "Do not call tool_activate for this capability_id. "
                + unavailable_capability_next_action(
                    reason,
                    capability_id=str(descriptor["capability_id"]),
                    facts=self._availability_facts(run_id),
                )
            )
            return value
        value["activatable"] = True
        value["activation_required"] = True
        value["next_action"] = (
            "Call tool_activate now, copying the top-level capability_id, "
            "schema_hash, and describe_nonce from this response exactly. "
            "Do not call the described target tool until tool_activate "
            "succeeds and the next model iteration exposes it."
        )
        return value

    def activate(self, capability_id: str, schema_hash: str, describe_nonce: str) -> Any:
        run_id, exposure = self._binding()
        unavailable = self._authorities.unavailable_reason(run_id, capability_id)
        if unavailable is not None:
            raise RuntimeError(f"tool_unavailable:{unavailable}")
        if str(capability_id) in self._route_blocked_capabilities(run_id):
            # Deny is unchanged either way — a standalone route has no task
            # execution authority.  Refusing here makes it a model-visible
            # rejection it can act on, instead of a whole-Run fault later.
            raise RuntimeError(PROJECT_EFFECT_ROUTE_REASON)
        described = exposure.describe(run_id, str(capability_id))
        if str(schema_hash) != described.capability_hash:
            raise RuntimeError("activation_schema_hash_stale")
        return exposure.prepare_activation(
            run_id,
            str(capability_id),
            str(describe_nonce),
        )


__all__ = [
    "SdkCapabilityBridgeAdapter",
    "SdkRuntimeCapabilityBridgeAdapter",
]
