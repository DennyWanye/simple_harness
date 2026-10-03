# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""知识是否仍然当前——读取时当场判定，不落库（HTN 补齐阶段 C）。

知识行建成后不改；"已过时"没有写方。每次被读（读工具、上下文推送、``used_knowledge``
核对、审查包取条目）都由 :func:`knowledge_standing` 判一次，依据只有这条知识的支持集合
（知识记录自带的 ``support`` 字段，与知识行同一次写入，建成后不改）：

* 来源验收仍然当前——义务没被取消或取代，所在步骤在现行计划里仍由这次验收撑着
  （与完成度读取同一个推法，:func:`acceptance_is_current`）；
* 每个证据产物仍是它那条路径上被接受的最新版本；
* 引用的上游知识仍然当前（只回溯更早的知识，不成环）。

这是秩序判断：只看引用还在不在、版本对不对，不看内容。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..contracts.evidence_state import Validity
from ..storage.store import StoreConflict, StoreError

if TYPE_CHECKING:
    from ..storage.store import Store

CURRENT = "CURRENT"
SUPERSEDED = "SUPERSEDED"
STALE = "STALE"


def acceptance_is_current(store: Store, mission_id: str, acceptance_id: str) -> tuple[bool, str]:
    """Whether this Acceptance still holds its step up in the active plan.

    An Acceptance row is never rewritten; "current" is derived the way completion is:
    its obligation is live, and the step's completion scope in the active plan still
    retains this acceptance with its official review (``read_occurrence_completion``).
    """
    from ..contracts.htn import ObligationId
    from ..contracts.models import ContractError
    from ..orchestrator.completion_status import read_occurrence_completion
    from ..storage.htn_store import HtnStore
    from ..storage.obligation_store import ObligationStore

    htn = HtnStore(store)
    try:
        acceptance = htn.get_acceptance(acceptance_id)
    except StoreError:
        return False, "acceptance_missing"
    if acceptance.mission_id != mission_id or acceptance.validity is not Validity.CURRENT:
        return False, "acceptance_not_current"
    duties = ObligationStore(store)
    duty = ObligationId(str(acceptance.obligation_id))
    if not duties.exists(mission_id, duty):
        return False, "obligation_missing"
    lifecycle = str(duties.account(mission_id, duty).lifecycle)
    if lifecycle in {"CANCELLED", "SUPERSEDED"}:
        return False, "obligation_" + lifecycle.lower()
    active = htn.active_plan_revision(mission_id)
    if active is None:
        return False, "no_active_plan"
    for member in htn.list_plan_memberships(mission_id, active.revision):
        if str(member.task_id) != str(acceptance.task_id):
            continue
        try:
            status = read_occurrence_completion(store, mission_id, str(member.occurrence_id))
        except (ContractError, StoreConflict, StoreError):
            return False, "completion_unreadable"
        if acceptance_id in status.preparation_acceptance_ids:
            return True, ""
        return False, "step_no_longer_rests_on_this_acceptance"
    return False, "step_not_in_active_plan"


def _artifact_is_current(store: Store, mission_id: str, artifact_id: str) -> bool:
    artifact = store.get_artifact(artifact_id)
    if artifact is None or artifact.mission_id != mission_id:
        return False
    return not any(
        other.path == artifact.path and other.version > artifact.version
        and other.verification_status == "VERIFIED"
        for other in store.list_mission_artifacts(mission_id)
    )


def knowledge_standing(store: Store, record: Any, *, _seen: frozenset[str] = frozenset()) -> str:
    """``CURRENT`` / ``SUPERSEDED`` / ``STALE:<reason>`` for one knowledge record."""
    if record.status != "VERIFIED" or record.superseded_by is not None:
        return SUPERSEDED
    support = record.support
    if not support:
        return f"{STALE}:no_support"  # nothing vouches for it (开发期不兼容旧数据)
    current, reason = acceptance_is_current(store, record.mission_id, support["acceptance_id"])
    if not current:
        return f"{STALE}:{reason}"
    for item in support["artifacts"]:
        if not _artifact_is_current(store, record.mission_id, item["id"]):
            return f"{STALE}:artifact_replaced:{item['id']}"
    for item in support["knowledge"]:
        if item["id"] in _seen:
            continue
        upstream = store.get_knowledge(item["id"])
        if upstream is None or upstream.version != item["version"] or knowledge_standing(
                store, upstream, _seen=_seen | {record.id}) != CURRENT:
            return f"{STALE}:upstream_knowledge:{item['id']}"
    return CURRENT


__all__ = ("CURRENT", "STALE", "SUPERSEDED", "acceptance_is_current", "knowledge_standing")
