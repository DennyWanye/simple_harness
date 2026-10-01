# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""The durable planning-protocol binding of a hierarchical Mission.

2026-10-01: there is one planning protocol (``planning-decision-v1``) and one package
version.  The proposal-text protocol and every historical package pairing are gone
(development phase, no old-data compatibility), so a hierarchical Mission whose
library row is missing or names anything else is refused by name — see
:func:`current_planning_protocol` — never served on a fallback path.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from simple_harness.contracts import canonical_json

from ..contracts.models import ContractError
from ..contracts.planning_decisions import (
    PLANNING_DECISION_V1,
    UnsupportedPlanningPackage,
)
from ..runtime.role_templates import (
    hierarchical_planner_pairing_is_valid,
    PLANNING_DECISION_PACKAGE_VERSION,
    PLANNING_DECISION_PROMPT_VERSION,
)
from ..storage.planning_decision_store import PlanningDecisionStore
from ..storage.store import Store

#: The package/prompt pair every binding shares.  The protocol *name* is not part of
#: this constant: it is the argument of :func:`binding_document`, so there is exactly
#: one place that assembles the three-field document and no value here can be silently
#: shadowed by an override.
PLANNING_PROTOCOL_BINDING: dict[str, Any] = {
    "package_version": PLANNING_DECISION_PACKAGE_VERSION,
    # derived from the pairing table, so the binding can never name a prompt the
    # current package was not written for
    "prompt_version": PLANNING_DECISION_PROMPT_VERSION,
}


#: The wire names a Mission charter may name (§8.1).  Nothing else is accepted: the
#: name is part of the frozen charter, so a typo must fail at the door.
PLANNING_PROTOCOLS = frozenset({PLANNING_DECISION_V1})

#: The proposal-text protocol removed on 2026-10-01.  Only its *name* survives, so
#: the door can say why it refuses it instead of calling it unknown.
REMOVED_PROPOSAL_PROTOCOL = "legacy-plan-proposal-v1"


def checked_planning_protocol(protocol_version: object) -> str:
    """Return a known protocol name, or raise the contract error the door reports.

    ``MissionSpec.__post_init__`` runs this once, but a spec can also be built through
    ``dataclasses.replace`` (which skips ``__post_init__``) or by writing the dataclass
    fields directly.  ``create_mission`` runs it again so an unvalidated spec is refused
    before it reaches ``spec_hash`` or the library.
    """

    if protocol_version == REMOVED_PROPOSAL_PROTOCOL:
        raise ContractError(
            f"planning protocol {REMOVED_PROPOSAL_PROTOCOL!r} was removed; "
            f"the only planning protocol is {PLANNING_DECISION_V1!r}"
        )
    if not isinstance(protocol_version, str) or protocol_version not in PLANNING_PROTOCOLS:
        raise ContractError(f"unknown planning protocol version {protocol_version!r}")
    return protocol_version


def binding_document(protocol_version: str) -> dict[str, Any]:
    """Return the exact three-field binding document for a checked protocol name.

    The single assembly point: the digest, the storage write and the replay comparison all
    describe the same document, so a package or prompt bump cannot update one and miss the
    others.
    """

    return {
        "protocol_version": checked_planning_protocol(protocol_version),
        **PLANNING_PROTOCOL_BINDING,
    }


def planning_protocol_binding_hash(protocol_version: str) -> str:
    """Return the digest of the exact three-field binding document."""

    document = binding_document(protocol_version)
    return sha256(canonical_json(document).encode("utf-8")).hexdigest()


def bind_planning_protocol(store: Store, mission_id: str, protocol_version: str) -> None:
    """Write the binding through the existing storage writer and transaction."""

    PlanningDecisionStore(store).bind_mission_protocol(
        mission_id,
        protocol_version=protocol_version,
        package_version=PLANNING_PROTOCOL_BINDING["package_version"],
        prompt_version=PLANNING_PROTOCOL_BINDING["prompt_version"],
        binding_hash=planning_protocol_binding_hash(protocol_version),
    )


def planning_protocol_for_mission(store: Store, mission_id: str) -> dict[str, Any] | None:
    """Read a Mission's persisted binding row, or ``None`` when it has none.

    Only a hierarchical Mission is bound; a flat-mode Mission has no planning protocol
    at all.  The read goes through the storage module's own reader, which is the
    documented gate for ``mission_planning_protocols``.
    """

    return PlanningDecisionStore(store).get_mission_protocol(mission_id)


def current_planning_protocol(store: Store, mission_id: str) -> dict[str, Any]:
    """The binding of a hierarchical Mission, which must be the one this build serves.

    A missing row means the Mission was created under the removed proposal-text
    protocol; another protocol name or package version means a contract this build no
    longer has.  Both are refused by name (:class:`UnsupportedPlanningPackage`), so the
    Mission stops loudly instead of running on a path that no longer exists.
    """

    stored = planning_protocol_for_mission(store, mission_id)
    if stored is None:
        raise UnsupportedPlanningPackage(
            f"mission {mission_id} has no planning-protocol binding: it was created under "
            f"the removed {REMOVED_PROPOSAL_PROTOCOL!r} protocol"
        )
    if (
        stored["protocol_version"] != PLANNING_DECISION_V1
        or int(stored["package_version"]) != PLANNING_DECISION_PACKAGE_VERSION
    ):
        raise UnsupportedPlanningPackage(
            f"mission {mission_id} is bound to planning protocol "
            f"{stored['protocol_version']!r}/package {stored['package_version']}; this build "
            f"serves only {PLANNING_DECISION_V1!r}/package {PLANNING_DECISION_PACKAGE_VERSION}"
        )
    return stored


def planning_protocol_replay_conflict(
    store: Store, mission_id: str, protocol_version: str
) -> str | None:
    """Return a replay conflict for a hierarchical Mission, or ``None``.

    A Mission replays idempotently only when the wire it asks for is the wire that is
    durably bound (§8.2).  The stored pair and its digest are validated as well, so a
    row that was tampered with is a conflict too, not just one bound to another name.
    """

    checked = checked_planning_protocol(protocol_version)
    stored = planning_protocol_for_mission(store, mission_id)
    if stored is None:
        return f"mission {mission_id} has no durable planning protocol binding"
    # A software upgrade does not rebind an existing Mission to a new prompt.
    # Validate the stored pair and its digest; the caller only chooses the protocol.
    frozen = {key: stored[key] for key in ("protocol_version", "package_version", "prompt_version")}
    valid_pair = hierarchical_planner_pairing_is_valid(
        str(stored["prompt_version"]), int(stored["package_version"])
    )
    valid_hash = stored.get("binding_hash") == sha256(canonical_json(frozen).encode("utf-8")).hexdigest()
    if stored.get("protocol_version") != checked or not valid_pair or not valid_hash:
        return (
            f"mission {mission_id} is durably bound to protocol"
            f" {stored['protocol_version']!r}/package {stored['package_version']}, not"
            f" {checked!r}/package {PLANNING_PROTOCOL_BINDING['package_version']}"
        )
    return None


__all__ = (
    "PLANNING_PROTOCOL_BINDING",
    "PLANNING_PROTOCOLS",
    "REMOVED_PROPOSAL_PROTOCOL",
    "binding_document",
    "bind_planning_protocol",
    "checked_planning_protocol",
    "current_planning_protocol",
    "planning_protocol_binding_hash",
    "planning_protocol_for_mission",
    "planning_protocol_replay_conflict",
)
