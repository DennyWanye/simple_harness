"""Contract-owned durable identities for DeepResearch v6 retrieval operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Mapping

from ..contracts import JsonValue, NodeExecutionIdentity, canonical_json
from ..deadlines import (
    DurableDeadlineV1,
    checkpoint_deadline,
    create_child_deadline,
    resume_deadline,
)
from ..effects import (
    EffectAction,
    EffectJournal,
    EffectStateConflict,
    PreparedToolCall,
)
from ..store import RegisteredBlobStore, StaleRunFence
from ..definitions.deep_research_v6_retrieval_contracts import (
    OfficialSearchResultV1,
    PageExtractionResultV1,
    SourceLocatorV1,
)
from .research_runtime import BoundResearchEffectContext


V6_READ_POLICY_ID = "deep-research-v6-page-read-v1"
V6_READ_POLICY_HASH = hashlib.sha256(V6_READ_POLICY_ID.encode("utf-8")).hexdigest()
V6_AUTO_CAP_POLICY_HASH = hashlib.sha256(
    b"deep-research-v6-automatic-cap-v1"
).hexdigest()


@dataclass(frozen=True, slots=True)
class V6ReadAttemptContext:
    logical_effect_id: str
    attempt_no: int
    deadline_id: str
    deadline_remaining_ms: int | None = None
    deadline_monotonic_ns: int | None = None


V6TypedReadResult = OfficialSearchResultV1 | PageExtractionResultV1


def v6_read_logical_effect_id(
    *,
    run_id: str,
    route_id: str,
    operation_kind: str,
    target_or_page_id: str,
    ordinal: int,
) -> str:
    if operation_kind not in {"official_search", "page_fetch"}:
        raise ValueError("unsupported v6 retrieval operation kind")
    if not run_id or not route_id or not target_or_page_id:
        raise ValueError("v6 retrieval logical identity fields are required")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("v6 retrieval ordinal must be non-negative")
    return hashlib.sha256(
        f"{run_id}|{route_id}|{operation_kind}|{target_or_page_id}|{ordinal}".encode(
            "utf-8"
        )
    ).hexdigest()


class DurableV6DeadlinePort:
    """Persist/resume the existing DurableDeadlineV1 contract under the run fence."""

    def __init__(
        self,
        *,
        journal: EffectJournal,
        resolve_effect_context: Callable[
            [NodeExecutionIdentity], BoundResearchEffectContext | Awaitable[BoundResearchEffectContext]
        ],
        wall_clock: Callable[[], datetime] | None = None,
        monotonic_ns: Callable[[], int] | None = None,
    ) -> None:
        self.journal = journal
        self.resolve_effect_context = resolve_effect_context
        self.wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        import time

        self.monotonic_ns = monotonic_ns or time.monotonic_ns

    async def _context(self, identity: NodeExecutionIdentity) -> BoundResearchEffectContext:
        value = self.resolve_effect_context(identity)
        if hasattr(value, "__await__"):
            value = await value  # type: ignore[assignment]
        if not isinstance(value, BoundResearchEffectContext) or value.identity != identity:
            raise EffectStateConflict("v6 deadline resolver identity mismatch")
        return value

    async def observe_automatic(
        self,
        *,
        identity: NodeExecutionIdentity,
    ) -> tuple[DurableDeadlineV1, object | None]:
        """Persist and observe the run-wide automatic cap before any v6 dispatch."""

        context = await self._context(identity)
        automatic = DurableDeadlineV1.create(
            owner_id=identity.run_id,
            logical_scope="run:automatic",
            policy_hash=V6_AUTO_CAP_POLICY_HASH,
            budget_ms=900_000,
            now_wall=self.wall_clock(),
        )
        raw = await self.journal.load_v6_deadline(identity.run_id, automatic.deadline_id)
        if raw is None:
            state = automatic
            await self.journal.persist_v6_deadline(context.effect_context.fence, state.to_json())
        else:
            state = DurableDeadlineV1.from_json(raw)
        transition = resume_deadline(
            state,
            now_wall=self.wall_clock(),
            now_monotonic_ns=self.monotonic_ns(),
        )
        if transition.state.revision != state.revision:
            await self.journal.persist_v6_deadline(
                context.effect_context.fence,
                transition.state.to_json(),
                expected_revision=state.revision,
            )
        automatic = transition.state
        return automatic, transition.lease

    async def allow_dispatch(self, identity: NodeExecutionIdentity) -> bool:
        """Return whether the durable run cap still permits an upstream call."""

        _, lease = await self.observe_automatic(identity=identity)
        return lease is not None

    async def resume_root(
        self,
        *,
        identity: NodeExecutionIdentity,
        route_id: str,
        policy_hash: str,
        budget_ms: int,
    ) -> tuple[DurableDeadlineV1, object | None]:
        automatic, automatic_lease = await self.observe_automatic(identity=identity)
        if automatic_lease is None:
            return automatic, None
        context = await self._context(identity)
        proposed = create_child_deadline(
            automatic,
            logical_key=f"retrieval:{route_id}",
            logical_scope="deep_research_v6.retrieval",
            budget_ms=budget_ms,
            now_wall=self.wall_clock(),
            policy_hash=policy_hash,
        )
        raw = await self.journal.load_v6_deadline(identity.run_id, proposed.deadline_id)
        if raw is None:
            retrieval = proposed
            await self.journal.persist_v6_deadline(
                context.effect_context.fence, retrieval.to_json()
            )
        else:
            retrieval = DurableDeadlineV1.from_json(raw)
        retrieval_transition = resume_deadline(
            retrieval,
            now_wall=self.wall_clock(),
            now_monotonic_ns=self.monotonic_ns(),
        )
        if retrieval_transition.state.revision != retrieval.revision:
            await self.journal.persist_v6_deadline(
                context.effect_context.fence,
                retrieval_transition.state.to_json(),
                expected_revision=retrieval.revision,
            )
        return retrieval_transition.state, retrieval_transition.lease

    async def prepare_route(
        self,
        *,
        identity: NodeExecutionIdentity,
        route_id: str,
        policy_hash: str,
        budgets: Mapping[str, int],
        retrieval_budget_ms: int = 120_000,
    ) -> None:
        context = await self._context(identity)
        await self.journal.ensure_v6_resource_budgets(
            context.effect_context.fence,
            policy_hash=policy_hash,
            budgets={str(key): int(value) for key, value in budgets.items()},
        )
        await self.resume_root(
            identity=identity,
            route_id=route_id,
            policy_hash=policy_hash,
            budget_ms=retrieval_budget_ms,
        )

    async def resume_child(
        self,
        *,
        identity: NodeExecutionIdentity,
        parent: DurableDeadlineV1,
        logical_key: str,
        logical_scope: str,
        budget_ms: int,
        policy_hash: str,
    ) -> tuple[DurableDeadlineV1, object | None]:
        context = await self._context(identity)
        proposed = create_child_deadline(
            parent,
            logical_key=logical_key,
            logical_scope=logical_scope,
            budget_ms=budget_ms,
            now_wall=self.wall_clock(),
            policy_hash=policy_hash,
        )
        raw = await self.journal.load_v6_deadline(identity.run_id, proposed.deadline_id)
        if raw is None:
            state = proposed
            await self.journal.persist_v6_deadline(context.effect_context.fence, state.to_json())
        else:
            state = DurableDeadlineV1.from_json(raw)
        transition = resume_deadline(
            state,
            now_wall=self.wall_clock(),
            now_monotonic_ns=self.monotonic_ns(),
        )
        if transition.state.revision != state.revision:
            await self.journal.persist_v6_deadline(
                context.effect_context.fence,
                transition.state.to_json(),
                expected_revision=state.revision,
            )
        return transition.state, transition.lease

    async def checkpoint(
        self,
        *,
        identity: NodeExecutionIdentity,
        state: DurableDeadlineV1,
        lease: object,
    ) -> tuple[DurableDeadlineV1, object | None]:
        context = await self._context(identity)
        persisted_raw = await self.journal.load_v6_deadline(
            identity.run_id, state.deadline_id
        )
        if persisted_raw is None:
            raise EffectStateConflict("v6 deadline disappeared before checkpoint")
        persisted = DurableDeadlineV1.from_json(persisted_raw)
        if persisted.revision != state.revision:
            transition = resume_deadline(
                persisted,
                now_wall=self.wall_clock(),
                now_monotonic_ns=self.monotonic_ns(),
            )
            if transition.state.revision != persisted.revision:
                await self.journal.persist_v6_deadline(
                    context.effect_context.fence,
                    transition.state.to_json(),
                    expected_revision=persisted.revision,
                )
            return transition.state, transition.lease
        transition = checkpoint_deadline(
            state,
            lease,  # type: ignore[arg-type]
            now_wall=self.wall_clock(),
            now_monotonic_ns=self.monotonic_ns(),
        )
        await self.journal.persist_v6_deadline(
            context.effect_context.fence,
            transition.state.to_json(),
            expected_revision=state.revision,
        )
        return transition.state, transition.lease


class DurableV6PageReadEffectAdapter:
    """One journal row and one route-budget reservation per search/page operation."""

    def __init__(
        self,
        *,
        journal: EffectJournal,
        blobs: RegisteredBlobStore,
        resolve_effect_context: Callable[
            [NodeExecutionIdentity], BoundResearchEffectContext | Awaitable[BoundResearchEffectContext]
        ],
        deadline_port: DurableV6DeadlinePort,
        fault_injector: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.journal = journal
        self.blobs = blobs
        self.resolve_effect_context = resolve_effect_context
        self.deadline_port = deadline_port
        self.fault_injector = fault_injector

    async def _fault(self, stage: str) -> None:
        if self.fault_injector is not None:
            await self.fault_injector(stage)

    async def _context(self, identity: NodeExecutionIdentity) -> BoundResearchEffectContext:
        value = self.resolve_effect_context(identity)
        if hasattr(value, "__await__"):
            value = await value  # type: ignore[assignment]
        if not isinstance(value, BoundResearchEffectContext) or value.identity != identity:
            raise EffectStateConflict("v6 read resolver identity mismatch")
        return value

    async def _json_blob(self, wire_ref: str) -> Mapping[str, object]:
        raw = await self.blobs.get(wire_ref.removeprefix("sha256:"))
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, Mapping):
            raise EffectStateConflict("v6 read dependency blob is not an object")
        return value

    async def _validate_typed_dependencies(
        self, result: V6TypedReadResult, *, input_source_locator_ref: str | None
    ) -> None:
        if isinstance(result, OfficialSearchResultV1):
            for candidate in result.candidates:
                locator = SourceLocatorV1.from_json(
                    await self._json_blob(candidate.source_locator_ref)
                )
                if locator.verification_status != candidate.authority_match:
                    raise EffectStateConflict(
                        "v6 search candidate authority does not match its locator"
                    )
            return
        if input_source_locator_ref is None:
            raise EffectStateConflict("v6 page attempt lacks its frozen input locator")
        input_locator = SourceLocatorV1.from_json(
            await self._json_blob(input_source_locator_ref)
        )
        if input_locator.verification_status != "unverified":
            raise EffectStateConflict("v6 page input locator must be unverified")
        locator = SourceLocatorV1.from_json(
            await self._json_blob(result.source_locator_ref)
        )
        page = result.page_record
        if page is None:
            return
        if (
            page["source_locator_ref"] != result.source_locator_ref
            or page["canonical_url_hash"] != locator.canonical_url_hash
            or page["final_url_hash"] != locator.final_url_hash
            or page["authority_id"] != locator.authority_id
            or locator.verification_status != "verified"
        ):
            raise EffectStateConflict("v6 page record does not match its verified locator")
        body_ref = str(page["body_ref"])
        body = await self.blobs.get(body_ref.removeprefix("sha256:"))
        if hashlib.sha256(body).hexdigest() != page["body_hash"]:
            raise EffectStateConflict("v6 page body hash does not match its record")
        for span in result.spans:
            span_body = await self.blobs.get(str(span["body_ref"]).removeprefix("sha256:"))
            start, end = int(span["start_byte"]), int(span["end_byte"])
            if end > len(span_body):
                raise EffectStateConflict("v6 evidence span exceeds its body")
            try:
                excerpt = span_body[start:end].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise EffectStateConflict("v6 evidence span is not UTF-8 aligned") from exc
            if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != span["excerpt_hash"]:
                raise EffectStateConflict("v6 evidence span excerpt hash mismatch")

    async def execute(
        self,
        *,
        operation_kind: str,
        route_id: str,
        target_or_page_id: str,
        ordinal: int,
        resource_kind: str,
        resource_hard_limit: int,
        resource_policy_hash: str,
        deadline: DurableDeadlineV1,
        identity: NodeExecutionIdentity,
        transport: Callable[[V6ReadAttemptContext], Awaitable[V6TypedReadResult]],
        input_source_locator_ref: str | None = None,
    ) -> dict[str, JsonValue]:
        context = await self._context(identity)
        logical = v6_read_logical_effect_id(
            run_id=identity.run_id,
            route_id=route_id,
            operation_kind=operation_kind,
            target_or_page_id=target_or_page_id,
            ordinal=ordinal,
        )
        result_kind = (
            "official_search" if operation_kind == "official_search" else "page_extraction"
        )
        def prepared_for(revision: int) -> PreparedToolCall:
            return PreparedToolCall.prepare(
                tool_name=(
                    "official_source_search"
                    if operation_kind == "official_search"
                    else "page_fetch"
                ),
                stable_call_id=logical,
                final_params={
                    "route_id": route_id,
                    "operation_kind": operation_kind,
                    "target_or_page_id": target_or_page_id,
                    "ordinal": ordinal,
                    "deadline_id": deadline.deadline_id,
                    "deadline_revision": revision,
                    "input_source_locator_ref": input_source_locator_ref,
                },
                tool_spec_version=(
                    "official-source-search-v1"
                    if operation_kind == "official_search"
                    else "page-fetch-v1"
                ),
                schema_hash=f"deep-research-v6-{result_kind}-result-v1",
                permission_policy_version="research-readonly-v1",
                effect_type=f"deep_research_v6_{operation_kind}",
            )
        latest_deadline = await self.journal.load_v6_deadline(
            identity.run_id, deadline.deadline_id
        )
        if latest_deadline is None:
            raise EffectStateConflict("v6 read deadline disappeared before begin")
        deadline_revision = DurableDeadlineV1.from_json(latest_deadline).revision

        async def begin(attempt_no: int):
            nonlocal deadline_revision
            # Parallel logical reads share one durable deadline revision. A
            # winner advances it in the same transaction as its reservation;
            # losers reload that canonical revision and retry their own CAS.
            # This preserves the deadline fence while allowing the resource
            # budget transaction to decide which read receives the last slot.
            for _ in range(8):
                try:
                    return await self.journal.begin_idempotent_read_attempt(
                        context.effect_context.fence,
                        node_execution_id=context.effect_context.node_execution_id,
                        node_id=identity.node_id,
                        logical_effect_id=logical,
                        attempt_no=attempt_no,
                        deadline_id=deadline.deadline_id,
                        deadline_revision=deadline_revision,
                        resource_kind=resource_kind,
                        resource_hard_limit=resource_hard_limit,
                        resource_policy_hash=resource_policy_hash,
                        prepared=prepared_for(deadline_revision),
                        deadline_now_wall=self.deadline_port.wall_clock(),
                        deadline_now_monotonic_ns=self.deadline_port.monotonic_ns(),
                    )
                except EffectStateConflict as exc:
                    if str(exc) != "v6 read deadline revision differs":
                        raise
                    latest = await self.journal.load_v6_deadline(
                        identity.run_id, deadline.deadline_id
                    )
                    if latest is None:
                        raise EffectStateConflict(
                            "v6 read deadline disappeared during concurrent begin"
                        ) from exc
                    deadline_revision = DurableDeadlineV1.from_json(latest).revision
            raise EffectStateConflict("v6 read deadline revision contention exceeded")

        begun = await begin(1)
        if begun.action is EffectAction.IN_FLIGHT:
            reconciled = await self.journal.reconcile_idempotent_read_for_retry(
                context.effect_context.fence,
                logical_effect_id=logical,
                attempt_no=begun.attempt_no,
            )
            if reconciled.action is EffectAction.REUSE:
                begun = reconciled
            elif begun.attempt_no == 1:
                latest_deadline = await self.journal.load_v6_deadline(
                    identity.run_id, deadline.deadline_id
                )
                if latest_deadline is None:
                    raise EffectStateConflict("v6 retry deadline disappeared")
                deadline_revision = DurableDeadlineV1.from_json(latest_deadline).revision
                begun = await begin(2)
            else:
                raise EffectStateConflict("v6 read retry is already in flight or exhausted")
        if begun.action is EffectAction.REUSE:
            assert begun.canonical_result_ref is not None
            raw = await self.blobs.get(begun.canonical_result_ref.removeprefix("sha256:"))
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, Mapping):
                raise EffectStateConflict("v6 canonical read result is not an object")
            typed = (
                OfficialSearchResultV1.from_json(value)
                if result_kind == "official_search"
                else PageExtractionResultV1.from_json(value)
            )
            return typed.to_json()
        if begun.action is not EffectAction.EXECUTE:
            raise EffectStateConflict("v6 read attempt is already in flight")
        if input_source_locator_ref is not None:
            locator_bytes = await self.blobs.get(
                input_source_locator_ref.removeprefix("sha256:")
            )
            staged = await self.blobs.put(
                locator_bytes,
                identity,
                media_type="application/vnd.deskpet.source-locator.v1+json",
            )
            if f"sha256:{staged.sha256}" != input_source_locator_ref:
                raise EffectStateConflict("v6 page input locator restaging changed digest")
        lease = begun.deadline_lease
        result = await transport(
            V6ReadAttemptContext(
                logical_effect_id=logical,
                attempt_no=begun.attempt_no,
                deadline_id=deadline.deadline_id,
                deadline_remaining_ms=(
                    begun.deadline_state.remaining_ms if begun.deadline_state is not None else None
                ),
                deadline_monotonic_ns=(
                    lease.deadline_monotonic_ns if lease is not None else None
                ),
            )
        )
        expected_type = OfficialSearchResultV1 if result_kind == "official_search" else PageExtractionResultV1
        if not isinstance(result, expected_type):
            raise EffectStateConflict("v6 read transport returned the wrong typed result")
        if (
            result.logical_effect_id != logical
            or result.attempt_no != begun.attempt_no
            or result.deadline_id != deadline.deadline_id
        ):
            raise EffectStateConflict("v6 typed read identity does not match its attempt")
        if isinstance(result, OfficialSearchResultV1):
            if result.target_id != target_or_page_id:
                raise EffectStateConflict("v6 search result target does not match its attempt")
        elif result.logical_page_id != target_or_page_id:
            raise EffectStateConflict("v6 page result identity does not match its attempt")
        if operation_kind == "official_search" and input_source_locator_ref is not None:
            raise EffectStateConflict("v6 search attempt cannot carry a page input locator")
        await self._validate_typed_dependencies(
            result, input_source_locator_ref=input_source_locator_ref
        )
        result_json = result.to_json()
        await self._fault("v6_read.after_typed_result_before_blob_put")
        ref = await self.blobs.put(
            canonical_json(result_json).encode("utf-8"),
            identity,
            media_type=(
                "application/vnd.deskpet.official-search-result+json"
                if result_kind == "official_search"
                else "application/vnd.deskpet.page-extraction-result+json"
            ),
        )
        wire = f"sha256:{ref.sha256}"
        # The closure is derived solely from the strict object that was just
        # serialized.  Provider/caller supplied `blob_refs` are never trusted.
        dependencies = {wire, *result.dependency_refs()}
        if input_source_locator_ref is not None:
            dependencies.add(input_source_locator_ref)
        await self._fault("v6_read.after_result_blob_before_effect_commit")
        try:
            await self.journal.commit_idempotent_read_attempt(
                context.effect_context.fence,
                effect_id=begun.effect_id,
                canonical_result_ref=wire,
                dependency_refs=tuple(sorted(dependencies)),
                result_kind=result_kind,
                deadline_now_wall=self.deadline_port.wall_clock(),
                deadline_now_monotonic_ns=self.deadline_port.monotonic_ns(),
            )
        except StaleRunFence as exc:
            # The takeover transaction already settled this attempt's budget
            # conservatively.  A late worker may retain only its staging blob;
            # it can never replace the canonical attempt head.
            raise EffectStateConflict("late v6 read result lost the run fence") from exc
        await self._fault("v6_read.after_effect_commit")
        return result_json


__all__ = [
    "DurableV6DeadlinePort",
    "DurableV6PageReadEffectAdapter",
    "V6_READ_POLICY_HASH",
    "V6_READ_POLICY_ID",
    "V6_AUTO_CAP_POLICY_HASH",
    "V6ReadAttemptContext",
    "v6_read_logical_effect_id",
]
