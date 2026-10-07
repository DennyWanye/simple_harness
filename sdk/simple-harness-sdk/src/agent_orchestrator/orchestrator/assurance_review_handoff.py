# SPDX-License-Identifier: Apache-2.0
"""Current disclosure gate at the original create/submit/repair boundaries.

The frozen request carries complete pinned bytes, so dispatch need not reread
CAS under a write lock. This gate authorizes disclosure only; historical review
evidence is not a licence to accept, execute an action, or complete a Mission.
"""

from __future__ import annotations

from typing import Any

from ..assurance.certificates import UseIdentity
from ..assurance.codec import AssuranceError, canonical
from ..assurance.refs import AssuranceRef, Pin
from ..assurance.review_input import read_initial_materials
from ..contracts import TERMINAL_ATTEMPT
from ..storage.assurance_pins import require_live_pin_locked
from ..storage.assurance_reads import (
    AssuranceReader,
    _permission,
    read_epochs_locked,
    require_epochs_locked,
)
from ..storage.htn_store import HtnStore
from .assurance_review_import import review_scope_id, review_subject_stopped
from .assurance_review_policies import require_registered_policies_locked
from .assurance_review_transport import _validate_package


class AssuranceReviewHandoff:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.orchestrator = runtime.orchestrator
        self.consumer = runtime.consumer

    def require_current_locked(self, intent: Any, binding: Any) -> None:
        orch, store = self.orchestrator, self.consumer.store
        if not store.connection.in_transaction:
            raise AssuranceError("READ_TRANSACTION_REQUIRED")
        root = orch.commit._assurance_root_gate.require_execution()
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
        identity = UseIdentity(
            mission.id,
            "REVIEW",
            body["review_key"],
            review_scope_id(body),
            self.consumer.principal_id,
            "DISCLOSE",
            root.root_incarnation_id,
        )
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
        now_ms = int(store.now * 1000)
        require_epochs_locked(
            store.connection,
            mission.id,
            read_epochs_locked(store.connection, mission.id),
            now_ms=now_ms,
        )
        for entry in entries:
            reader.read_exact_metadata(entry.ref)
            _permission(self.consumer.authority, identity, entry.ref, now_ms)
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
        # Package and target authorization remain explicit even for an empty
        # catalogue. A caller cannot authorize a request merely by omitting refs.
        for ref in (
            AssuranceRef("review_package", Pin.from_json(body["package_ref"])),
            AssuranceRef.from_json(body["subject"]["target"]),
        ):
            _permission(self.consumer.authority, identity, ref, now_ms)
