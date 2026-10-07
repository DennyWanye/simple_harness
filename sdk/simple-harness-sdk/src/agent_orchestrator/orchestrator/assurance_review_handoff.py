# SPDX-License-Identifier: Apache-2.0
"""Current disclosure gate at the original create/submit/repair boundaries.

The frozen request carries complete pinned bytes, so dispatch need not reread
CAS under a write lock. This gate authorizes disclosure only; historical review
evidence is not a licence to accept, execute an action, or complete a Mission.

披露本身（推后第 2 批 A03）：初始材料、审阅包、审阅对象交给审阅模型之前，过使用证书签发方
（用途 DISCLOSE，消费方是这次派发意图）；真正发出请求的那一刻落证书，其余前置门只核不落。
"""

from __future__ import annotations

from typing import Any

from ..assurance.codec import AssuranceError, canonical
from ..assurance.refs import AssuranceRef, Pin
from ..assurance.review_input import read_initial_materials
from ..storage.assurance_pins import require_live_pin_locked
from ..storage.assurance_reads import AssuranceReader
from ..storage.htn_store import HtnStore
from .assurance_point_use import REVIEW_INTENT_CONSUMER, EvidenceClaim, PointUseRefused, certify_point_use_locked
from .assurance_review_import import review_subject_stopped
from .assurance_review_policies import require_registered_policies_locked
from .assurance_review_transport import _validate_package


class AssuranceReviewHandoff:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.orchestrator = runtime.orchestrator
        self.consumer = runtime.consumer

    def require_current_locked(self, intent: Any, binding: Any, *, record: bool = False) -> None:
        """``record``：这一刻真的把请求发给审阅模型（落 DISCLOSE 证书）；否则是前置门，只核不落。"""
        orch, store = self.orchestrator, self.consumer.store
        if not store.connection.in_transaction:
            raise AssuranceError("READ_TRANSACTION_REQUIRED")
        body = binding.to_json()
        mission = store.get_mission(body["mission_id"])
        task = store.get_task(body["subject"]["owner_task_ref"]["id"])
        if (
            mission is None
            or mission.tenant_id != self.consumer.tenant_id
            or mission.id != intent.mission_id
            or task is None
            or review_subject_stopped(store, binding)
        ):
            raise AssuranceError("REVIEW_SUBJECT_STOPPED")
        reader = AssuranceReader(store, tenant_id=mission.tenant_id, mission_id=mission.id)
        package = HtnStore(store).get_review_package(body["package_ref"]["id"])
        _validate_package(reader, package, body)
        # 两个政策钉住只读登记正文，再与当前部署比（推后第 2 批 A18）
        require_registered_policies_locked(
            orch, tenant_id=mission.tenant_id, binding=body, profile_id=orch.profile_of(intent),
            agent_config=intent.config["agent_config"],
        )
        if body["subject"]["purpose"] == "TASK_CONTENT":
            result = store.get_result(body["subject"]["target"]["pin"]["id"])
            attempt = None if result is None else store.get_attempt(result.envelope.attempt_id)
            # 存活（含"重审已验收结果"的情形）已由上面的 review_subject_stopped 判过，这里只核身份
            if (
                attempt is None
                or attempt.task_id != task.id
                or intent.config.get("attempt_id") != attempt.id
            ):
                raise AssuranceError("REVIEW_SUBJECT_STOPPED")
        entries = read_initial_materials(dict(intent.config["message"]), binding)
        for entry in entries:
            if entry.ref.kind in {"artifact", "source"}:
                pins = store.connection.execute(
                    "SELECT pin_id FROM assurance_blob_pins WHERE mission_id=? AND review_key=? "
                    "AND object_ref_json=? AND state='BOUND' LIMIT 2",
                    (mission.id, body["review_key"], canonical(entry.ref.to_json())),
                ).fetchall()
                if len(pins) != 1:
                    raise AssuranceError("LIVE_BLOB_PIN_REQUIRED")
                pin_id = pins[0][0]
                require_live_pin_locked(
                    store.connection,
                    mission_id=mission.id,
                    ref=entry.ref,
                    pin_id=pin_id,
                    review_key=body["review_key"],
                )
        # Package and target are disclosed even for an empty catalogue. A caller
        # cannot authorize a request merely by omitting refs.
        refs = (
            AssuranceRef("review_package", Pin.from_json(body["package_ref"])),
            AssuranceRef.from_json(body["subject"]["target"]),
            *(entry.ref for entry in entries),
        )
        use = certify_point_use_locked(
            orch.commit, mission_id=mission.id, purpose="DISCLOSE", consumer_kind=REVIEW_INTENT_CONSUMER,
            consumer_id=intent.intent_id, claims=tuple(EvidenceClaim.exact(ref) for ref in refs),
            subject=("task", task.id), record=record,
        )
        if not use.usable:
            raise PointUseRefused(use)
