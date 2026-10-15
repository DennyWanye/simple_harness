# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (SQL statement literals)

"""``HtnStore``: the durable side of the full-target semantic network.

Everything here is *storage*, not judgement.  The store writes and reads the P1.1
contract objects as canonical JSON next to the columns the orchestrator queries or
guards, exactly like :class:`~agent_orchestrator.storage.store.Store` does for the
legacy entities.  What it does enforce is identity and atomicity:

* one ACTIVE plan revision per Mission (:meth:`HtnStore.activate_plan_revision`),
* one official review record per package, one adopted resolution per obligation,
* one ``request_hash`` per ``OperationId`` (AER §14.3 payload conflict),
* an idempotent plan commit receipt (:meth:`HtnStore.record_commit_receipt`).

Whether a plan *should* be committed — coverage, cycles, budget — is decided in
P2.3a's ``plan_commits``; this module would happily store a plan that fails those
rules, and says so on purpose so no caller mistakes persistence for admission.

The store owns no connection: it composes an existing ``Store`` and runs inside
that store's ``transaction()``, so a write here rolls back together with whatever
the Commit Service wrote beside it.
"""

from __future__ import annotations

from .htn_store_domains.dirty_bindings import HtnDirtyBindingMixin
from .htn_store_domains.plan_structure import HtnPlanStructureMixin
from .htn_store_domains.receipts import HtnReceiptMixin
from .htn_store_domains.records import (
    ACCEPTANCE_RECEIPT_KINDS,
    DIRTY_STATES,
    METHOD_INSTANCE_STATES,
    PLAN_REVISION_STATES,
    AcceptanceCommitReceipt,
    DirtyEntry,
    PlanCommitReceipt,
    StoredMethod,
    StoredPlanRevision,
    StoredReviewRecord,
)
from .htn_store_domains.reviews_acceptance import HtnReviewAcceptanceMixin
from .htn_store_domains.semantics_methods import HtnSemanticsMethodMixin


class HtnStore(
    HtnSemanticsMethodMixin,
    HtnPlanStructureMixin,
    HtnReviewAcceptanceMixin,
    HtnDirtyBindingMixin,
    HtnReceiptMixin,
):
    """Insert / get / list for the full-target tables, in contract objects."""


__all__ = (
    "ACCEPTANCE_RECEIPT_KINDS",
    "DIRTY_STATES",
    "METHOD_INSTANCE_STATES",
    "PLAN_REVISION_STATES",
    "AcceptanceCommitReceipt",
    "DirtyEntry",
    "HtnStore",
    "PlanCommitReceipt",
    "StoredMethod",
    "StoredPlanRevision",
    "StoredReviewRecord",
)
