# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Host composition of the Memory SDK 0.6.1 cognitive (v7) backend (S5a read surfaces + S5b Task 4).

One fresh-only v7 store per user-data dir, owned by the single authenticated
local subject.  Surfaces:

- ``pending_occurrences`` — the mandatory pre-``no_recall`` inbox reconcile
  predicate: ``matched ∧ live/presentable ∧ eligible ∧ occurrence_key ∉
  presented set``.  Non-presentable or ineligible occurrences never block
  ``no_recall`` (S5c settles them); nothing here ever advances the presented
  set — that cursor authority is the Host v45 ledger's.
- ``typed_recall`` — the memory_standalone lane over ``execute_typed_recall``
  with Host-constructed DisclosureContext (recipient/purpose can never come
  from model payloads).
- The short-horizon vector lane runs on the production embedder when one is
  composed (hash/mock embedders are guarded off); without one it degrades
  deterministically and typed/FTS recall stays eligible.

S5b Task 4 wiring (design-freeze §8): ``build_human_memory_v7(...,
supported_filter_policies={credential-filter/v1, host-public-turn/v1,
host-typed-ingress/v1}, evidence_authority=<Host state.db resolver>,
analysis_delivery_authority=<HostMemoryAnalysisExecutor>, classification_policy)``
and, on first build, the idempotent ``register_principal_owner(local principal,
personal scope)`` — the S5a fail-open reconcile branch is gone: a fresh install
registers its owner before the first read.
"""

from __future__ import annotations

from collections.abc import Mapping

import asyncio
import hashlib
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Lifecycle states that keep an occurrence "live/presentable"; everything else
# (cancelled / expired / forgotten / completed) must not block no_recall and
# must never surface content (challenge ledger S5A-DS-F1).
_NON_PRESENTABLE_STATES = frozenset(
    {"cancelled", "expired", "forgotten", "completed", "superseded"}
)
_ELIGIBLE_PRIVACY_CLASSES = frozenset({"public", "personal"})

HOST_SUPPORTED_FILTER_POLICIES: frozenset[str] = frozenset(
    {"credential-filter/v1", "host-public-turn/v1", "host-typed-ingress/v1", "host-primary-runtime-v1"}
)
HOST_CLASSIFICATION_POLICY_ID = "deskpet-host-classification"
HOST_CLASSIFICATION_POLICY_VERSION = "1"
HOST_CLASSIFICATION_AUTHORITY_REF = "host:classification/v1"


def local_memory_principal() -> Any:
    from simple_harness_memory.core.identity import MemoryPrincipal

    return MemoryPrincipal(
        deployment_id="deskpet-local",
        household_id="deskpet-local-household",
        actor_id="deskpet-local-owner-v1",
        session_id="primary-conversation",
    )


def local_memory_scope() -> Any:
    from simple_harness_memory.core.identity import MemoryScope

    return MemoryScope.personal(local_memory_principal().actor_id)


def host_classification_policy() -> Any:
    """Required by Memory 0.6.1 materialization (§8.3): Host evidence is PERSONAL by default."""

    from simple_harness.runtime import PrivacyClass
    from simple_harness_memory.core.mutations import InformationClassificationPolicy

    return InformationClassificationPolicy(
        policy_id=HOST_CLASSIFICATION_POLICY_ID,
        policy_version=HOST_CLASSIFICATION_POLICY_VERSION,
        authority_ref=HOST_CLASSIFICATION_AUTHORITY_REF,
        required_privacy_class=PrivacyClass.PERSONAL,
        required_information_attributes=(),
    )


class HumanMemoryV7Runtime:
    """Lazy singleton over ``build_human_memory_v7`` (fresh-only store)."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        embedder_getter: Any = None,
        evidence_authority: Any = None,
        analysis_authority: Any = None,
        memory_action_authority: Any = None,
        history_source_authority: Any = None,
        backend_factory: Callable[..., Any] | None = None,
        principal: Any = None,
    ) -> None:
        self._db_path = Path(db_path)
        from deskpet.operation_audit.memory_attempts import MemoryAttemptJournal
        self.operation_audit = MemoryAttemptJournal(self._db_path.with_name("operation-audit.db"))
        # Production: the single authenticated local owner.  Tests may bind a
        # subject-specific principal (same deployment/household shape).
        self._principal = principal
        self._manager: Any | None = None
        self._lock = asyncio.Lock()
        self._embedder_getter = embedder_getter
        self._evidence_authority = evidence_authority
        self._analysis_authority = analysis_authority
        self._memory_action_authority = memory_action_authority
        self._history_source_authority = history_source_authority
        # Test seam only: build the backend with an injected clock/fault injector
        # (the production path is always ``build_human_memory_v7``).
        self._backend_factory = backend_factory
        self.registration_receipt: Any | None = None
        self.schema_upgrade_receipt: Any | None = None

    @property
    def db_path(self) -> Path:
        return self._db_path

    def principal(self) -> Any:
        return self._principal if self._principal is not None else local_memory_principal()

    def scope(self) -> Any:
        from simple_harness_memory.core.identity import MemoryScope

        return MemoryScope.personal(self.principal().actor_id)

    @property
    def evidence_authority(self) -> Any:
        return self._evidence_authority

    @property
    def analysis_authority(self) -> Any:
        return self._analysis_authority

    def build_kwargs(self) -> dict[str, Any]:
        embedder = self._embedder_getter() if self._embedder_getter else None
        if getattr(embedder, "kind", None) in {"hash", "mock"}:
            # v7 production guard: deterministic test embeddings never
            # power the short-horizon vector lane.
            embedder = None
        return {
            "short_horizon_embedder": embedder,
            "supported_filter_policies": HOST_SUPPORTED_FILTER_POLICIES,
            "evidence_authority": self._evidence_authority,
            "analysis_delivery_authority": self._analysis_authority,
            "classification_policy": host_classification_policy(),
        }

    async def manager(self) -> Any:
        async with self._lock:
            if self._manager is None:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                # The successor SDK owns old-catalog recognition, WAL-aware
                # backup, migration and replay. Host never probes its schema.
                # Older SDKs retain their existing initializer behavior.
                if self._backend_factory is None and self._db_path.exists():
                    from simple_harness_memory import migrations

                    upgrade = getattr(migrations, "migrate_human_memory_v7_to_v7_2", None)
                    if upgrade is not None:
                        self.schema_upgrade_receipt = await upgrade(
                            self._db_path,
                            backup_path=self._db_path.with_name(
                                f"{self._db_path.name}.pre-schema-7.2.backup"
                            ),
                        )
                kwargs = self.build_kwargs()
                if self._memory_action_authority is not None:
                    kwargs["memory_action_authority"] = self._memory_action_authority
                if self._history_source_authority is not None:
                    kwargs["history_source_authority"] = self._history_source_authority
                if self._backend_factory is not None:
                    manager = await self._backend_factory(self._db_path, **kwargs)
                else:
                    from simple_harness_memory import build_human_memory_v7

                    manager = await build_human_memory_v7(self._db_path, **kwargs)
                if self._history_source_authority is not None:
                    enforcement = getattr(manager, "history_source_enforcement_version", None)
                    if type(enforcement) is not int or enforcement != 1:
                        await manager.close()
                        raise RuntimeError("memory_history_source_enforcement_unavailable")
                # Memory 0.6.1 §8.4: idempotent owner registration on every build
                # (fresh install → the first reconcile read succeeds; replay → same receipt).
                self.registration_receipt = await manager.register_principal_owner(
                    self.principal(), self.scope()
                )
                self._manager = manager
            return self._manager

    async def job_runner(self, executor: Any, config: Any, *, worker_id: str, now: Callable[[], float] = time.time) -> Any:
        """``DurableMemoryJobRunner`` over this store; ``executor`` must be the bound delivery authority."""

        from simple_harness_memory.core.jobs import DurableMemoryJobRunner

        if executor is not self._analysis_authority:
            raise RuntimeError("memory_analysis_delivery_authority_identity_differs")
        manager = await self.manager()
        return DurableMemoryJobRunner(
            repository=manager.backend,
            executor=executor,
            delivery_authority=executor,
            config=config,
            worker_id=worker_id,
            now=now,
        )

    async def close(self) -> None:
        async with self._lock:
            if self._manager is not None:
                await self._manager.close()
                self._manager = None

    # -- occurrence inbox reconcile --------------------------------------

    async def pending_occurrences(
        self, presented_keys: Iterable[str]
    ) -> tuple[Any, ...]:
        """Frozen reconcile predicate over the read-only occurrence inbox.

        The owner is registered by ``manager()`` (Memory 0.6.1
        ``register_principal_owner``), so any ``MemoryOwnershipConflict`` here is a
        real ownership fault and propagates (fail-closed).
        """

        manager = await self.manager()
        principal = self.principal()
        presented = set(presented_keys)
        pending: list[Any] = []
        after: tuple[float, str] | None = None
        while True:
            page = await manager.read_occurrence_inbox(
                principal=principal, after=after, limit=200
            )
            for entry in page.entries:
                if entry.outcome != "matched":
                    continue
                if entry.lifecycle_state.lower() in _NON_PRESENTABLE_STATES:
                    continue
                if entry.effective_privacy_class not in _ELIGIBLE_PRIVACY_CLASSES:
                    continue
                if getattr(entry, "suppressed", False):
                    continue
                if entry.occurrence_key in presented:
                    continue
                pending.append(entry)
            if page.next_after is None:
                break
            after = page.next_after
        return tuple(pending)

    # -- typed recall (memory_standalone lane) ---------------------------

    async def typed_recall(
        self,
        *,
        query: str,
        run_id: str,
        turn_ordinal: int,
        now: float | None = None,
    ) -> Any:
        """Execute a Host-authored typed RecallPlan; degraded lanes stay stable."""

        from simple_harness.runtime import (
            DeliveryRecipient,
            DisclosureContext,
            DisclosureGeneration,
            DisclosurePurpose,
            DisclosureReasonCode,
            DisclosureSource,
            DisclosureTrust,
            EvidenceRef,
            IntendedAudience,
            LongTermMemoryType,
            RecallBudget,
            RecallContext,
            RecallPlan,
            RecallReasonCode,
            RecallRetrievalMode,
            RecallSelectorDomain,
        )

        manager = await self.manager()
        principal = self.principal()
        moment = time.time() if now is None else float(now)
        subject = principal.actor_id
        # The Host is the only author of disclosure identity; model payloads
        # can never override recipient/purpose (program hard contract 89-91).
        disclosure = DisclosureContext(
            run_id,
            subject,
            DeliveryRecipient.USER_SELF,
            subject,
            IntendedAudience.USER_SELF,
            DisclosurePurpose.TASK_EXECUTION,
            DisclosureSource.AUTHENTICATED_HOST,
            DisclosureTrust.TRUSTED_AUTHORITY,
            DisclosureGeneration.CURRENT,
            "host:validated-control-channel:v1",
            (DisclosureReasonCode.MINIMUM_NECESSARY,),
        )
        evidence_ref = EvidenceRef(
            f"chat-turn:{run_id}:{turn_ordinal}",
            hashlib.sha256(
                f"{run_id}:{turn_ordinal}:{query}".encode()
            ).hexdigest(),
            1,
        )
        memory_types = (
            LongTermMemoryType.SEMANTIC,
            LongTermMemoryType.EPISODE,
            LongTermMemoryType.PROCEDURE,
        )
        context = RecallContext(
            run_id,
            subject,
            f"turn-{turn_ordinal}",
            turn_ordinal,
            moment + 60.0,
            query,
            None,
            memory_types,
            False,
            (RecallSelectorDomain.MEMORY_TYPE,),
            (RecallRetrievalMode.FULL_TEXT,),
            (),
            (),
            None,
            None,
            (),
            (),
            (),
            (),
            disclosure,
            (evidence_ref,),
            RecallBudget(8, 16_384, 2_048, 1_000),
        )
        plan = RecallPlan(
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"simple-harness:recall-plan:{run_id}:{turn_ordinal}",
                )
            ),
            context.run_id,
            context.subject,
            context.context_hash,
            context.context_revision,
            context.query,
            context.available_memory_types,
            context.short_horizon_allowed,
            context.allowed_selector_domains,
            context.allowed_retrieval_modes,
            (),
            context.allowed_entity_constraints,
            context.earliest_occurred_at,
            context.latest_occurred_at,
            context.event_constraint_refs,
            (),
            (),
            context.disclosure_context,
            context.evidence_refs,
            context.budget,
            f"context-route:{run_id}:{turn_ordinal}",
            (RecallReasonCode.USER_FACT_DEPENDENCY,),
        )
        execution = await self.operation_audit.execute_typed_recall(
            manager, principal=principal, context=context, plan=plan, now=moment,
            caller="foreground_recall",
        )
        try:
            short_horizon = await manager.recall_short_horizon(
                principal=principal,
                query=query,
                disclosure_context=disclosure,
                limit=8,
                now=moment,
            )
        except Exception:  # noqa: BLE001 - degraded lane must stay stable
            short_horizon = None
        return RecallLanes(execution=execution, short_horizon=short_horizon)


@dataclass(frozen=True, slots=True)
class RecallLanes:
    """Typed long-term execution plus the five-day short-horizon lane."""

    execution: Any
    short_horizon: Any | None
    short_history_dependencies: Mapping[str, Any] | None = None

    def __post_init__(self):
        if self.short_history_dependencies is not None:
            from simple_harness import freeze_json
            from deskpet.execution.primary_dependencies import parse_dependencies
            object.__setattr__(self, "short_history_dependencies",
                freeze_json(parse_dependencies(self.short_history_dependencies)))

    @property
    def degradation_codes(self) -> tuple[str, ...]:
        codes = tuple(
            getattr(code, "value", str(code))
            for code in self.execution.degradation_codes
        )
        if self.short_horizon is None:
            codes = (*codes, "short_horizon_unavailable")
        elif self.short_horizon.degradation_code is not None:
            codes = (
                *codes,
                getattr(
                    self.short_horizon.degradation_code,
                    "value",
                    str(self.short_horizon.degradation_code),
                ),
            )
        return codes

    @property
    def result(self) -> Any:
        return self.execution.result


def _fragment_size(payload: Any) -> tuple[int, int]:
    import json as _json

    from deskpet.sdk_adapters.context_partitions import text_tokens

    text = _json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return len(text.encode("utf-8")), text_tokens(text)


def project_recall_fragments(lanes: Any) -> tuple[dict[str, Any], ...]:
    """Host second-pass eligibility + dedup over typed recall items.

    The SDK already gated candidates; the Host re-checks privacy class before
    anything enters Context and deduplicates by public payload hash.
    """

    from simple_harness import thaw_json

    execution = getattr(lanes, "execution", lanes)
    short_horizon = getattr(lanes, "short_horizon", None)
    fragments: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in execution.result.items:
        privacy = getattr(
            item.effective_privacy_class, "value", str(item.effective_privacy_class)
        )
        if privacy not in _ELIGIBLE_PRIVACY_CLASSES:
            continue
        payload_hash = item.selected_item.public_payload_hash
        if payload_hash in seen:
            continue
        seen.add(payload_hash)
        bytes_len, tokens = _fragment_size(item.public_payload)
        fragments.append(
            {
                "ref": item.selected_item.item_id,
                "memory_type": getattr(
                    item.selected_item.memory_type,
                    "value",
                    str(item.selected_item.memory_type),
                ),
                "privacy_class": privacy,
                "score": float(item.score),
                "payload": thaw_json(item.public_payload),
                "payload_hash": payload_hash,
                "source_task_scope_ids": list(item.source_task_scope_ids),
                "bytes": bytes_len,
                "tokens": tokens,
                "lane": "long_term_typed",
                "history_binding": {
                    "result_id": execution.result.result_id,
                    "result_hash": execution.result.result_hash,
                    "item_id": item.selected_item.item_id,
                    "item_hash": item.result_item_hash,
                },
            }
        )
    for hit in getattr(short_horizon, "hits", ()) or ():
        privacy = getattr(
            hit.effective_privacy_class, "value", str(hit.effective_privacy_class)
        )
        if privacy not in _ELIGIBLE_PRIVACY_CLASSES:
            continue
        if hit.content_hash in seen:
            continue
        seen.add(hit.content_hash)
        bytes_len, tokens = _fragment_size(hit.content)
        fragments.append(
            {
                "ref": hit.chunk_ref,
                "memory_type": "short_horizon",
                "privacy_class": privacy,
                "score": float(hit.score),
                "payload": thaw_json(hit.content),
                "payload_hash": hit.content_hash,
                "source_task_scope_ids": [],
                "bytes": bytes_len,
                "tokens": tokens,
                "lane": "short_horizon",
                "history_binding": {"audit_id": short_horizon.audit_id,
                    "chunk_ref": hit.chunk_ref, "content_hash": hit.content_hash},
                **({"history_source_dependencies": thaw_json(lanes.short_history_dependencies)}
                   if getattr(lanes, "short_history_dependencies", None) is not None else {}),
            }
        )
    return tuple(fragments)


__all__ = [
    "HOST_SUPPORTED_FILTER_POLICIES",
    "HumanMemoryV7Runtime",
    "RecallLanes",
    "host_classification_policy",
    "local_memory_principal",
    "local_memory_scope",
    "project_recall_fragments",
]
