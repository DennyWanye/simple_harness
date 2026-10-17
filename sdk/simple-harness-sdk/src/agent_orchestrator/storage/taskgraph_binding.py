# SPDX-License-Identifier: Apache-2.0
"""The one check that a Mission is TaskGraph-bound, and the kernel identity it checks against.

Split out of :mod:`.taskgraph_store` (代码质量整治阶段 5G) so the source-change events the
HTN store writes can check the binding without importing the TaskGraph store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..contracts.error_table import CodedFault, RoundFaultCode
from .store import StoreError

if TYPE_CHECKING:
    from .store import Store

#: The one kernel identity (a CHECK on ``taskgraph_policy_bindings`` pins it).  A
#: Mission is bound in its creation transaction and the policy
#: no longer carries a planning-grant reference (nothing read it — every plan commit keeps its
#: own planning-authority gate); an older binding still carrying it is refused by name
#: (``TASKGRAPH_POLICY_FIELDS_INVALID``), so the kernel string itself did not need to move.
KERNEL_VERSION = "taskgraph-exec-v2"


class NotBoundError(StoreError, CodedFault):
    """The Mission has no TaskGraph binding.  Every user Mission is bound when it is
    created (2026-10-03); only an older Mission of a development library is not."""

    code = RoundFaultCode.TASKGRAPH_NOT_BOUND


class KernelUnsupportedError(StoreError, CodedFault):
    code = RoundFaultCode.TASKGRAPH_KERNEL_UNSUPPORTED


def require_bound(store: Store, mission_id: str) -> None:
    """The one check that a Mission is TaskGraph-bound; raises by name otherwise.  A
    caller that must not raise (a startup skip, a read page, the contract gate that
    stops such a Mission by name) catches :class:`NotBoundError` — nobody asks a
    yes/no question instead (TaskGraph 补全第二批)."""
    row = store.connection.execute(
        "SELECT kernel_version FROM taskgraph_policy_bindings WHERE mission_id=?", (mission_id,)
    ).fetchone()
    if row is None:
        raise NotBoundError("TASKGRAPH_NOT_BOUND")
    if row[0] != KERNEL_VERSION:
        raise KernelUnsupportedError("TASKGRAPH_KERNEL_UNSUPPORTED")


__all__ = ("KERNEL_VERSION", "KernelUnsupportedError", "NotBoundError", "require_bound")
