# SPDX-License-Identifier: Apache-2.0
"""Assurance creation protocol inside the original Mission creation UoW.

The deployment supplies the already approved requirements interpreter and
reconciliation adapters. Wire requests cannot select those collaborators or
promote an old Mission to the new profile.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from ..assurance.codec import AssuranceError, canonical, fingerprint, text
from ..assurance.policy import AssurancePolicy
from ..assurance.refs import AssuranceRef, Pin
from ..contracts import Event, Mission
from ..contracts.planning_decisions import PLANNING_DECISION_V1
from ..contracts.resolution import RequirementsRevision
from ..storage.assurance_store import AssuranceStore
from ..storage.assurance_work import CONSUMERS, WorkTarget
from ..storage.htn_store import HtnStore

if TYPE_CHECKING:
    from .commit_service import CommitService, MissionSpec


class AssuranceMissionFactory:
    def __init__(
        self,
        commit: CommitService,
        *,
        tenant_id: str,
        policy: AssurancePolicy,
        require_creation_root: Callable[[], None],
        requirements: Callable[[Mission, MissionSpec], RequirementsRevision],
        reconcile: Callable[[str], Mapping[str, Sequence[WorkTarget]]],
    ) -> None:
        if not isinstance(policy, AssurancePolicy) or not all(
            callable(value) for value in (require_creation_root, requirements, reconcile)
        ):
            raise AssuranceError("ASSURANCE_FACTORY_UNBOUND")
        self.commit = commit
        self.tenant_id = text(tenant_id)
        self.policy = policy
        self.require_creation_root = require_creation_root
        self.requirements = requirements
        self.reconcile = reconcile

    def selects(self, spec: MissionSpec) -> bool:
        """Whether this new Mission takes the assured lane (spec §11): every
        planning-decision Mission does (2026-10-03: there is no other lane)."""
        return spec.bound_planning_protocol == PLANNING_DECISION_V1

    def create(self, mission: Mission, spec: MissionSpec, event: Event) -> None:
        store = self.commit.store
        if not store.connection.in_transaction:
            raise AssuranceError("FACTORY_TRANSACTION_REQUIRED")
        self.require_creation_root()
        if (
            mission.tenant_id != self.tenant_id
            or spec.bound_planning_protocol != PLANNING_DECISION_V1
        ):
            raise AssuranceError("ASSURANCE_CREATION_PROTOCOL_MISMATCH")
        revision = self.requirements(mission, spec)
        if not isinstance(revision, RequirementsRevision) or revision.mission_id != mission.id:
            raise AssuranceError("FACTORY_REQUIREMENTS_MISMATCH")
        semantic = HtnStore(store)
        if semantic.latest_requirements_revision(mission.id) is not None:
            raise AssuranceError("FACTORY_REQUIREMENTS_ALREADY_EXIST")
        # Uses the original immutable Requirements writer, never a parallel body.
        semantic.insert_requirements_revision(revision)
        targets = self.reconcile(mission.id)
        if set(targets) != CONSUMERS or any(
            not isinstance(target, WorkTarget) for values in targets.values() for target in values
        ):
            raise AssuranceError("ACTIVATION_RECONCILIATION_INCOMPLETE")
        manifest = {
            consumer: [
                {"work_key": target.work_key, "fingerprint": target.fingerprint}
                for target in sorted(targets[consumer], key=lambda value: value.work_key)
            ]
            for consumer in sorted(CONSUMERS)
        }
        for entries in manifest.values():
            if len(entries) != len({entry["work_key"] for entry in entries}):
                raise AssuranceError("ACTIVATION_RECONCILIATION_DUPLICATE")
        canonical(manifest)  # bound the whole reconciliation before any cursor ACK
        policy_hash = fingerprint(self.policy.to_json())
        activation = self.commit._emit(
            "AssuranceProfileActivated",
            mission.id,
            key=mission.id,
            payload={
                "policy_hash": policy_hash,
                "creation_event_id": event.id,
                "requirements_hash": revision.content_hash(),
                "reconciliation_hash": fingerprint(manifest),
            },
        )
        receipt_id = "assurance-activation:" + mission.id
        body = {
            "schema_version": 1,
            "mission_id": mission.id,
            "creation_event_id": event.id,
            "creation_event_hash": fingerprint(event.to_json()),
            "policy_hash": policy_hash,
            "activation_event_id": activation.id,
            "requirements": {
                "id": str(revision.revision_id),
                "revision": revision.revision,
                "content_hash": revision.content_hash(),
            },
            "reconciliation": manifest,
            "reconciliation_hash": fingerprint(manifest),
        }
        ref = _receipt(self.commit, receipt_id, "AssuranceProfileActivated", mission.id, body)
        side = AssuranceStore(store)
        side.record_creation_contract(
            mission.id,
            lane="ASSURANCE_1_1",
            origin="FACTORY",
            source_hash=fingerprint(event.to_json()),
            receipt=ref,
            now_ms=int(store.now * 1000),
        )
        side.bind_profile_locked(
            mission.id,
            policy=self.policy,
            activation_receipt=ref,
            activation_event_id=activation.id,
            now_ms=int(store.now * 1000),
            reconciliation=targets,
        )


def _receipt(
    commit: CommitService, receipt_id: str, kind: str, mission_id: str, body: dict
) -> AssuranceRef:
    digest = fingerprint(body)
    commit.store.insert_receipt(
        commit_id=receipt_id,
        kind=kind,
        subject_id=mission_id,
        base_version=None,
        proposal_hash=digest,
        receipt=body,
    )
    return AssuranceRef("commit_receipt", Pin(receipt_id, 0, digest))


def record_mission_creation(
    commit: CommitService, mission: Mission, spec: MissionSpec, event: Event
) -> None:
    """Called only for a new row, after the actual original MissionCreated event."""
    if not commit.store.connection.in_transaction or event.mission_id != mission.id:
        raise AssuranceError("FACTORY_TRANSACTION_REQUIRED")
    if not commit.store.has_table("assurance_creation_contracts"):
        # A store whose schema predates Assurance (the schema 8/9 read-only audit
        # snapshots): no deployment, no lane to classify, nothing is recorded.
        if commit._assurance_factory is not None:
            raise AssuranceError("ASSURANCE_SCHEMA_REQUIRED")
        return
    # 2026-10-03: every Mission is assured; there is no ordinary completion lane.
    if commit._assurance_factory is None:
        raise AssuranceError("ASSURANCE_FACTORY_UNBOUND")
    if not commit._assurance_factory.selects(spec):
        raise AssuranceError("ASSURANCE_PLANNING_PROTOCOL_REQUIRED")
    commit._assurance_factory.create(mission, spec, event)


def validate_creation_replay(commit: CommitService, mission: Mission) -> None:
    """A Mission replays only through the installed Assurance factory it was created by."""
    row = commit.store.connection.execute(
        "SELECT lane FROM assurance_creation_contracts WHERE mission_id=?", (mission.id,)
    ).fetchone()
    if row is None:
        raise AssuranceError("CREATION_CONTRACT_UNRESOLVED")
    if AssuranceStore(commit.store).lane(mission.id) != "ASSURANCE_1_1":
        raise AssuranceError("ASSURANCE_LANE_REQUIRED")
    if commit._assurance_factory is None:
        raise AssuranceError("ASSURANCE_FACTORY_UNBOUND")
    if mission.tenant_id != commit._assurance_factory.tenant_id:
        raise AssuranceError("ASSURANCE_CREATION_PROTOCOL_MISMATCH")
    commit._assurance_factory.require_creation_root()