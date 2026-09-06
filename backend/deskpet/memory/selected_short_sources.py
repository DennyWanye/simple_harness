"""Selected short-hit provenance through public Memory and immutable Host S1.

No indexing, SDK storage reads, chunk-hash reconstruction, or execution grants.
The caller must carry the returned dependencies through the final outbound fence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiosqlite
import simple_harness as h

from deskpet.execution.primary_dependencies import dependencies
from deskpet.memory.conversation_registration import (
    ConversationRegistrationUnavailable,
    PrimaryConversationAuthority,
)
from deskpet.memory.primary_visibility import (
    PrimaryHistoryPolicy,
    PrimaryVisibilityError,
)

if TYPE_CHECKING:
    from simple_harness_memory import HistoryRecallBinding, HistoryShortHorizonBinding

MAX_SELECTED = 256
MAX_GROUP_REFS = 256


class SelectedShortSourcesUnavailable(ValueError):
    pass


def _short_ref(binding):
    return {
        "audit_id": binding.audit_id,
        "chunk_ref": binding.chunk_ref,
        "content_hash": binding.content_hash,
    }


def _recall_ref(binding):
    return {name: getattr(binding, name) for name in
            ("result_id", "result_hash", "item_id", "item_hash")}


def _binding_dependencies(bindings, evidence=()):
    from simple_harness_memory import HistoryRecallBinding, HistoryShortHorizonBinding
    bindings = tuple(bindings)
    return dependencies(
        evidence,
        recall=(_recall_ref(binding) for binding in bindings
                if type(binding) is HistoryRecallBinding),
        short_horizon=(_short_ref(binding) for binding in bindings
                       if type(binding) is HistoryShortHorizonBinding),
        schema_version=2,
    )


@dataclass(frozen=True, slots=True)
class SelectedShortSourceItem:
    binding: HistoryShortHorizonBinding | HistoryRecallBinding
    visible: bool
    reason: str
    evidence: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class SelectedShortSources:
    items: tuple[SelectedShortSourceItem, ...]

    @property
    def accepted_bindings(self):
        return tuple(item.binding for item in self.items if item.visible)

    @property
    def visibility_dependencies(self):
        return _binding_dependencies(
            self.accepted_bindings,
            (
                {"evidence_id": eid, "envelope_hash": digest}
                for item in self.items
                if item.visible
                for eid, digest in item.evidence
            ),
        )


class SelectedShortSourceReader:
    def __init__(self, authority: PrimaryConversationAuthority, *, manager, principal):
        if principal.actor_id != authority.subject:
            raise ValueError("selected_short_subject_mismatch")
        self.authority, self.manager, self.principal = authority, manager, principal

    async def resolve(self, *, disclosure_context, bindings) -> SelectedShortSources:
        return await self._resolve(disclosure_context=disclosure_context, bindings=bindings, typed=False)

    async def resolve_typed(self, *, disclosure_context, bindings) -> SelectedShortSources:
        return await self._resolve(disclosure_context=disclosure_context, bindings=bindings, typed=True)

    async def _resolve(self, *, disclosure_context, bindings, typed) -> SelectedShortSources:
        import simple_harness_memory as m

        if (
            type(disclosure_context) is not h.DisclosureContext
            or disclosure_context.subject != self.authority.subject
        ):
            raise ValueError("selected_short_disclosure_mismatch")
        if (
            type(bindings) is not tuple
            or len(bindings) > MAX_SELECTED
            or any(
                type(binding) is not (m.HistoryRecallBinding if typed else m.HistoryShortHorizonBinding)
                for binding in bindings
            )
        ):
            raise ValueError("selected_short_bindings_invalid")
        resolver = getattr(self.manager, (
            "resolve_typed_short_horizon_sources" if typed else "resolve_short_horizon_sources"
        ), None)
        checker = getattr(self.manager, "check_history_visibility", None)
        snapshot_type = getattr(m, "ShortHorizonSourceSnapshot", None)
        if not callable(resolver) or not callable(checker) or snapshot_type is None:
            raise SelectedShortSourcesUnavailable(
                "selected_short_source_port_unavailable"
            )
        if not bindings:
            return SelectedShortSources(())
        # The public local call defines positional binding, including duplicates.
        # Host does not implement the SDK binding/chunk/request hash algorithms.
        snapshot = await resolver(
            principal=self.principal,
            disclosure_context=disclosure_context,
            bindings=bindings,
        )
        if (
            type(snapshot) is not snapshot_type
            or type(snapshot.schema_version) is not int
            or snapshot.schema_version != 1
            or snapshot.subject != self.authority.subject
            or type(snapshot.items) is not tuple
            or len(snapshot.items) != len(bindings)
        ):
            raise SelectedShortSourcesUnavailable("selected_short_snapshot_mismatch")

        async def check(*, subject, disclosure_context, bindings):
            if subject != self.authority.subject:
                raise ValueError("selected_short_subject_mismatch")
            return await checker(
                principal=self.principal,
                disclosure_context=disclosure_context,
                bindings=bindings,
            )

        policy = PrimaryHistoryPolicy(
            self.authority.db_path, self.authority.subject, check
        )
        results = []
        for binding, observed in zip(bindings, snapshot.items, strict=True):
            if (
                type(observed) is not m.ShortHorizonSourceItem
                or type(observed.visible) is not bool
                or type(observed.complete) is not bool
            ):
                raise SelectedShortSourcesUnavailable(
                    "selected_short_snapshot_mismatch"
                )
            if not observed.visible or not observed.complete:
                results.append(SelectedShortSourceItem(binding, False, observed.reason))
                continue
            try:
                evidence = await self._verify_group(observed.source_refs)
                proof = _binding_dependencies(
                    (binding,),
                    (
                        {"evidence_id": eid, "envelope_hash": digest}
                        for eid, digest in evidence
                    ),
                )
                # Reuse Host's actual recursive S1/terminal/USER proof and public
                # visibility reader after slow registration reads. Per-hit limits
                # keep an invalid/oversized lineage from rejecting other hits.
                async with aiosqlite.connect(
                    f"file:{self.authority.db_path}?mode=ro", uri=True
                ) as db:
                    db.row_factory = aiosqlite.Row
                    await db.execute("BEGIN")
                    visible = await policy.check_dependencies(
                        db=db,
                        primary_ref=self.authority.primary_ref,
                        dependencies=proof,
                        disclosure_context=disclosure_context,
                    )
                if not visible:
                    raise ValueError("selected_short_history_denied")
                item = SelectedShortSourceItem(
                    binding, True, "history_visible", evidence
                )
                # Preserve the existing combined dependency limit; never truncate
                # a group or silently omit one of its required sources.
                _ = SelectedShortSources((*results, item)).visibility_dependencies
                results.append(item)
            except (
                ConversationRegistrationUnavailable,
                PrimaryVisibilityError,
                ValueError,
                TypeError,
                KeyError,
                RuntimeError,
            ) as exc:
                reason = (
                    exc.code
                    if isinstance(
                        exc,
                        (ConversationRegistrationUnavailable, PrimaryVisibilityError),
                    )
                    else "selected_short_host_proof_unavailable"
                )
                results.append(SelectedShortSourceItem(binding, False, reason))
        return SelectedShortSources(tuple(results))

    async def _verify_group(self, refs) -> tuple[tuple[str, str], ...]:
        import simple_harness_memory as m

        if type(refs) is not tuple or not refs or len(refs) > MAX_GROUP_REFS:
            raise ValueError("selected_short_group_limit")
        seen, evidence = set(), []
        group_key = None
        for ordinal, ref in enumerate(refs, 1):
            if type(ref) is not m.ShortHorizonSourceRef or ref.evidence_id in seen:
                raise ValueError("selected_short_group_invalid")
            seen.add(ref.evidence_id)
            registration = await self.authority.resolve_conversation_registration(
                h.ConversationEvidenceRegistrationRef(
                    ref.registration_id,
                    ref.registration_hash,
                    ref.evidence_id,
                    ref.envelope_hash,
                )
            )
            env, receipt, metadata = (
                registration.envelope,
                registration.admission_receipt,
                registration.metadata,
            )
            receipt.verify(env)
            actual = (
                env.evidence_id,
                env.envelope_hash,
                env.source_ref,
                env.source_hash,
                env.sanitized_hash,
                receipt.receipt_id,
                receipt.receipt_hash,
                registration.registration_id,
                registration.registration_hash,
                metadata.item_ordinal,
                metadata.role.value,
            )
            expected = (
                ref.evidence_id,
                ref.envelope_hash,
                ref.source_ref,
                ref.source_hash,
                ref.sanitized_hash,
                ref.admission_receipt_id,
                ref.admission_receipt_hash,
                ref.registration_id,
                ref.registration_hash,
                ref.item_ordinal,
                ref.role,
            )
            if (
                actual != expected
                or type(ref.item_ordinal) is not int
                or ref.item_ordinal != ordinal
                or metadata.group_item_count != len(refs)
                or metadata.subject != self.authority.subject
                or env.subject != self.authority.subject
                or not receipt.accepted
                or metadata.primary_conversation_id != self.authority.primary_ref
                or metadata.conversation_id != self.authority.primary_ref
            ):
                raise ValueError("selected_short_registration_mismatch")
            key = (
                metadata.causal_group_id,
                metadata.causal_group_sequence,
                metadata.ordered_group_manifest_hash,
            )
            if group_key is not None and key != group_key:
                raise ValueError("selected_short_group_mismatch")
            group_key = key
            evidence.append((env.evidence_id, env.envelope_hash))
        return tuple(evidence)
