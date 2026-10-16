"""Forgetting a growth event."""

from __future__ import annotations

from ..contracts import CompanionStateError, OwnerRef
from .core import CompanionStoreCore
from .support import canonical_hash, canonical_json


class GrowthForgettingMixin(CompanionStoreCore):
    """Forgetting a growth event."""

    __slots__ = ()

    # ------------------------------------------------------------------
    # Redaction / forgetting

    def forget_growth_event(
        self,
        owner: OwnerRef,
        *,
        event_id: str,
        reason_code: str,
    ) -> bool:
        now = self._now()
        # This is the single fixed SessionDB tombstone payload.  Keep it
        # byte-for-byte equivalent to memory.companion_message_projection
        # without coupling CompanionStore to the memory package.
        tombstone = {
            "schema_version": 1,
            "kind": "companion_projection_tombstone",
            "summary": "此成长记录已被遗忘。",
            "detail_ref": None,
            "available_actions": [],
        }
        tombstone_json = canonical_json(tombstone)
        tombstone_hash = canonical_hash(tombstone)
        with self._write() as db:
            self._require_owner(db, owner)
            event = db.execute(
                """SELECT * FROM growth_events
                   WHERE profile_id=? AND profile_generation=? AND event_id=?""",
                (owner.profile_id, owner.profile_generation, event_id),
            ).fetchone()
            if event is None:
                raise CompanionStateError("growth_event_missing")
            if event["content_state"] == "tombstoned":
                return False
            db.execute(
                """UPDATE growth_events SET payload_json=NULL,content_state='tombstoned',
                     reason_code=?,updated_at=?
                   WHERE profile_id=? AND profile_generation=? AND event_id=?
                     AND content_state='live'""",
                (
                    reason_code,
                    now,
                    owner.profile_id,
                    owner.profile_generation,
                    event_id,
                ),
            )
            # Preference state and its evidence lineage share this Companion
            # transaction with the event tombstone.  A second async writer
            # would leave a crash window where a forgotten preference still
            # influences a new Run.
            # 按需导入：循环依赖（store ↔ preferences）
            from ..preferences import recompute_preferences_after_forget_db

            recompute_preferences_after_forget_db(
                db,
                store=self,
                owner=owner,
                event_id=event_id,
                now=now,
            )
            candidate_ids = [
                str(row["candidate_id"])
                for row in db.execute(
                    """SELECT candidate_id FROM candidate_evidence
                       WHERE profile_id=? AND profile_generation=? AND event_id=?""",
                    (owner.profile_id, owner.profile_generation, event_id),
                ).fetchall()
            ]
            if candidate_ids:
                placeholders = ",".join("?" for _ in candidate_ids)
                db.execute(
                    f"""UPDATE candidate_artifacts SET status='invalidated',
                         reason_code=?,updated_at=?
                         WHERE profile_id=? AND profile_generation=?
                           AND candidate_id IN ({placeholders})
                           AND status NOT IN ('rolled_back','disabled','invalidated')""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        *candidate_ids,
                    ),
                )
                db.execute(
                    f"""UPDATE candidate_package_sources SET provenance_state='forgotten',
                         reason_code=?,updated_at=?
                         WHERE profile_id=? AND profile_generation=?
                           AND candidate_id IN ({placeholders})""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        *candidate_ids,
                    ),
                )
                db.execute(
                    f"""UPDATE capability_version_supports
                        SET support_state='forgotten',reason_code=?,updated_at=?
                        WHERE profile_id=? AND profile_generation=?
                          AND candidate_id IN ({placeholders})
                          AND support_state IN ('eligible','active')""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        *candidate_ids,
                    ),
                )
                # Reports and decisions are durable lineage conclusions, not
                # evidence-independent facts.  Keep their hashes/rows for
                # audit, but make them unusable after their evidence is
                # forgotten.
                db.execute(
                    f"""UPDATE evaluation_reports
                        SET verdict='inconclusive',reason_code=?
                        WHERE profile_id=? AND profile_generation=?
                          AND evaluation_id IN (
                            SELECT evaluation_id FROM evaluation_runs
                            WHERE profile_id=? AND profile_generation=?
                              AND candidate_id IN ({placeholders})
                          )""",
                    (
                        reason_code,
                        owner.profile_id,
                        owner.profile_generation,
                        owner.profile_id,
                        owner.profile_generation,
                        *candidate_ids,
                    ),
                )
                db.execute(
                    f"""UPDATE growth_decisions SET decision='stale',reason_code=?
                        WHERE profile_id=? AND profile_generation=?
                          AND candidate_id IN ({placeholders})
                          AND decision<>'stale'""",
                    (
                        reason_code,
                        owner.profile_id,
                        owner.profile_generation,
                        *candidate_ids,
                    ),
                )
                evaluation_ids = [
                    str(row["evaluation_id"])
                    for row in db.execute(
                        f"""SELECT evaluation_id FROM evaluation_runs
                            WHERE profile_id=? AND profile_generation=?
                              AND candidate_id IN ({placeholders})""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            *candidate_ids,
                        ),
                    ).fetchall()
                ]
                if evaluation_ids:
                    e_placeholders = ",".join("?" for _ in evaluation_ids)
                    db.execute(
                        f"""UPDATE evaluation_runs SET status='inconclusive',
                             reason_code=?,updated_at=?
                             WHERE profile_id=? AND profile_generation=?
                               AND evaluation_id IN ({e_placeholders})
                               AND status IN ('queued','running')""",
                        (
                            reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            *evaluation_ids,
                        ),
                    )
                    db.execute(
                        f"""UPDATE evaluation_execution_permits SET status='revoked',
                             reason_code=?,updated_at=?
                             WHERE profile_id=? AND profile_generation=?
                               AND evaluation_id IN ({e_placeholders})
                               AND status IN ('issued','claimed')""",
                        (
                            reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            *evaluation_ids,
                        ),
                    )
                    db.execute(
                        f"""UPDATE evaluation_cases SET status='inconclusive',
                             reason_code=?,lease_expires_at=NULL,updated_at=?
                             WHERE profile_id=? AND profile_generation=?
                               AND evaluation_id IN ({e_placeholders})
                               AND status IN ('queued','leased')""",
                        (
                            reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            *evaluation_ids,
                        ),
                    )
                    launches = db.execute(
                        f"""SELECT launch_id FROM evaluation_case_launches
                            WHERE profile_id=? AND profile_generation=?
                              AND evaluation_id IN ({e_placeholders})
                              AND status IN ('claimed','started','unknown','cleanup_required')""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            *evaluation_ids,
                        ),
                    ).fetchall()
                    db.execute(
                        f"""UPDATE evaluation_case_launches SET status='aborting',
                             reason_code=?,updated_at=?
                             WHERE profile_id=? AND profile_generation=?
                               AND evaluation_id IN ({e_placeholders})
                               AND status IN ('claimed','started','unknown','cleanup_required')""",
                        (
                            reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            *evaluation_ids,
                        ),
                    )
                    for launch in launches:
                        launch_id = str(launch["launch_id"])
                        self._insert_outbox(
                            db,
                            owner,
                            outbox_id=canonical_hash(["abort_evaluation_launch", launch_id]),
                            event_kind="abort_evaluation_launch",
                            event_id=launch_id,
                            sink_kind="managed_process",
                            payload={
                                "schema_version": 1,
                                "launch_id": launch_id,
                                "reason_code": reason_code,
                            },
                            now=now,
                            reason_code=reason_code,
                        )

                # An already activated version cannot remain trusted after its
                # supporting evidence is forgotten.  Companion never changes
                # CapabilityStore here: quarantine the exact Manager receipt
                # fence, then enqueue one rollback when a complete fallback is
                # known, otherwise a conservative disable request.
                active_receipts = db.execute(
                    f"""SELECT r.*,q.candidate_id,q.candidate_mode,
                               q.target_scope_key,q.source_fence_json
                        FROM capability_activation_receipts r
                        JOIN capability_activation_requests q
                          ON q.profile_id=r.profile_id
                         AND q.profile_generation=r.profile_generation
                         AND q.activation_request_id=r.activation_request_id
                        WHERE r.profile_id=? AND r.profile_generation=?
                          AND q.candidate_id IN ({placeholders})
                          AND q.status='succeeded'
                          AND r.action IN ('install','update')""",
                    (
                        owner.profile_id,
                        owner.profile_generation,
                        *candidate_ids,
                    ),
                ).fetchall()
                for receipt in active_receipts:
                    binding_generation = int(receipt["binding_generation"])
                    support_set_hash = canonical_hash(
                        [
                            "forgotten_support",
                            event_id,
                            receipt["candidate_id"],
                            receipt["result_hash"],
                        ]
                    )
                    db.execute(
                        """INSERT INTO capability_quarantines(
                             profile_id,profile_generation,pack_id,version,manifest_hash,
                             reason_code,evidence_ref,fence_generation,support_set_hash,
                             status,schema_version,created_at,updated_at
                           ) VALUES (?,?,?,?,?,?,?,?,?,'active',1,?,?)
                           ON CONFLICT(profile_id,profile_generation,pack_id,version,manifest_hash)
                           DO UPDATE SET reason_code=excluded.reason_code,
                             evidence_ref=excluded.evidence_ref,
                             support_set_hash=excluded.support_set_hash,
                             status=CASE
                               WHEN capability_quarantines.status='released'
                               THEN 'active' ELSE capability_quarantines.status END,
                             updated_at=excluded.updated_at""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            receipt["pack_id"],
                            receipt["version"],
                            receipt["manifest_hash"],
                            reason_code,
                            f"growth_event:{event_id}",
                            binding_generation,
                            support_set_hash,
                            now,
                            now,
                        ),
                    )
                    db.execute(
                        """UPDATE capability_activation_guards
                           SET status='superseded',reason_code=?,updated_at=?
                           WHERE profile_id=? AND profile_generation=?
                             AND activation_request_id=? AND status='open'""",
                        (
                            reason_code,
                            now,
                            owner.profile_id,
                            owner.profile_generation,
                            receipt["activation_request_id"],
                        ),
                    )
                    in_progress = db.execute(
                        """SELECT activation_request_id
                           FROM capability_activation_requests
                           WHERE profile_id=? AND profile_generation=?
                             AND target_owner_key=? AND target_scope=?
                             AND target_scope_key=? AND pack_id=?
                             AND status IN (
                               'pending','claimed','staging','staged','publishing',
                               'unknown','cleanup_required'
                             )""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            receipt["target_owner_key"],
                            receipt["target_scope"],
                            receipt["target_scope_key"],
                            receipt["pack_id"],
                        ),
                    ).fetchone()
                    if in_progress is not None:
                        continue

                    has_fallback = all(
                        receipt[name] is not None
                        for name in (
                            "fallback_owner_key",
                            "fallback_scope",
                            "fallback_scope_key",
                            "fallback_pack_id",
                            "fallback_version",
                            "fallback_manifest_hash",
                        )
                    )
                    action = "rollback" if has_fallback else "disable"
                    rollback_kind = (
                        "remove_override"
                        if has_fallback and receipt["candidate_mode"] == "builtin_override"
                        else "same_owner_version"
                        if has_fallback
                        else None
                    )
                    target_version = receipt["fallback_version"] if has_fallback else None
                    target_manifest_hash = (
                        receipt["fallback_manifest_hash"] if has_fallback else None
                    )
                    source_fence = {
                        "owner_key": receipt["target_owner_key"],
                        "scope": receipt["target_scope"],
                        "scope_key": receipt["target_scope_key"],
                        "pack_id": receipt["pack_id"],
                        "version": receipt["version"],
                        "manifest_hash": receipt["manifest_hash"],
                        "binding_generation": binding_generation,
                    }
                    source_fence_json = canonical_json(source_fence)
                    mutation_identity = {
                        "action": action,
                        "cause": f"growth_event:{event_id}",
                        "source": source_fence,
                        "target_version": target_version,
                        "target_manifest_hash": target_manifest_hash,
                        "rollback_kind": rollback_kind,
                    }
                    request_fingerprint = canonical_hash(mutation_identity)
                    request_id = canonical_hash(
                        [
                            "forget_capability_mutation",
                            owner.profile_id,
                            owner.profile_generation,
                            receipt["activation_request_id"],
                            event_id,
                            action,
                        ]
                    )
                    manager_key = canonical_hash(
                        ["capability_manager", request_id, request_fingerprint]
                    )
                    db.execute(
                        """INSERT INTO capability_activation_requests(
                             profile_id,profile_generation,activation_request_id,
                             request_fingerprint,action,candidate_id,candidate_mode,
                             rollback_kind,activation_mode,target_owner_key,target_scope,
                             target_scope_key,pack_id,target_version,target_manifest_hash,
                             candidate_package_hash,archive_hash,source_fence_json,
                             target_expected_absent,target_expected_binding_generation,
                             report_id,risk_id,decision_id,cause_ref,
                             manager_idempotency_key,expected_quarantine_generation,
                             expected_support_set_hash,status,claim_epoch,attempt,
                             reason_code,schema_version,created_at,updated_at
                           ) VALUES (?,?,?,?,?,NULL,NULL,?,'normal',?,?,?,?,?,?,
                                     NULL,NULL,?,0,?,NULL,NULL,NULL,?,?,?,?,
                                     'pending',0,0,?,1,?,?)""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            request_id,
                            request_fingerprint,
                            action,
                            rollback_kind,
                            receipt["target_owner_key"],
                            receipt["target_scope"],
                            receipt["target_scope_key"],
                            receipt["pack_id"],
                            target_version,
                            target_manifest_hash,
                            source_fence_json,
                            binding_generation,
                            f"growth_event:{event_id}",
                            manager_key,
                            binding_generation,
                            support_set_hash,
                            reason_code,
                            now,
                            now,
                        ),
                    )
                    audit_id = canonical_hash(["forget_capability_mutation", request_id])
                    db.execute(
                        """INSERT INTO audit_events(
                             profile_id,profile_generation,audit_id,actor,action,
                             reason_code,after_hash,lineage_ref,audit_hash,
                             schema_version,created_at
                           ) VALUES (?,?,?,?,?,?,?,?,?,1,?)""",
                        (
                            owner.profile_id,
                            owner.profile_generation,
                            audit_id,
                            "companion_host",
                            f"capability_{action}_requested",
                            reason_code,
                            request_fingerprint,
                            request_id,
                            canonical_hash(
                                {
                                    "request_id": request_id,
                                    "fingerprint": request_fingerprint,
                                    "action": action,
                                }
                            ),
                            now,
                        ),
                    )
                    self._insert_outbox(
                        db,
                        owner,
                        outbox_id=canonical_hash(["capability_mutation", request_id]),
                        event_kind="capability_mutation",
                        event_id=request_id,
                        sink_kind="capability_platform",
                        payload={
                            "schema_version": 1,
                            "activation_request_id": request_id,
                            "request_fingerprint": request_fingerprint,
                            "action": action,
                        },
                        now=now,
                        reason_code=reason_code,
                    )

            snapshot_ids = [
                str(row["snapshot_id"])
                for row in db.execute(
                    """SELECT DISTINCT snapshot_id FROM run_growth_dependency_evidence
                       WHERE profile_id=? AND profile_generation=? AND event_id=?""",
                    (owner.profile_id, owner.profile_generation, event_id),
                ).fetchall()
            ]
            if snapshot_ids:
                placeholders = ",".join("?" for _ in snapshot_ids)
                db.execute(
                    f"""UPDATE run_growth_snapshots SET status='revoked',reason_code=?,updated_at=?
                        WHERE profile_id=? AND profile_generation=?
                          AND snapshot_id IN ({placeholders}) AND status<>'revoked'""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        *snapshot_ids,
                    ),
                )
                db.execute(
                    f"""UPDATE companion_run_bindings SET status='revoked',reason_code=?,updated_at=?
                        WHERE profile_id=? AND profile_generation=?
                          AND snapshot_id IN ({placeholders})
                          AND status IN ('prepared','active')""",
                    (
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        *snapshot_ids,
                    ),
                )

            affected_notifications = db.execute(
                """SELECT * FROM notifications
                   WHERE profile_id=? AND profile_generation=?
                     AND source_refs_json LIKE ? AND status<>'redacted'""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    f'%"{event_id}"%',
                ),
            ).fetchall()
            for notification in affected_notifications:
                redaction_version = int(notification["redaction_version"]) + 1
                db.execute(
                    """UPDATE notifications SET summary_json=?,detail_json=?,actions_json='[]',
                         payload_hash=?,redaction_version=?,status='redacted',
                         reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=? AND notification_id=?""",
                    (
                        tombstone_json,
                        tombstone_json,
                        tombstone_hash,
                        redaction_version,
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        notification["notification_id"],
                    ),
                )
                db.execute(
                    """UPDATE outbox SET payload_json=?,payload_hash=?,reason_code=?,updated_at=?
                       WHERE profile_id=? AND profile_generation=?
                         AND event_id=? AND status IN ('pending','claimed')""",
                    (
                        canonical_json(
                            {
                                "schema_version": 1,
                                "notification_id": notification["notification_id"],
                                "payload_hash": tombstone_hash,
                            }
                        ),
                        canonical_hash(
                            {
                                "schema_version": 1,
                                "notification_id": notification["notification_id"],
                                "payload_hash": tombstone_hash,
                            }
                        ),
                        reason_code,
                        now,
                        owner.profile_id,
                        owner.profile_generation,
                        notification["notification_id"],
                    ),
                )
                redaction_event_id = (
                    f"{owner.profile_id}:{owner.profile_generation}:"
                    f"{notification['notification_id']}:{redaction_version}"
                )
                self._insert_outbox(
                    db,
                    owner,
                    outbox_id=canonical_hash(["projection_redaction", redaction_event_id]),
                    event_kind="projection_redaction",
                    event_id=redaction_event_id,
                    sink_kind="session_projection",
                    payload={
                        "schema_version": 1,
                        "notification_id": notification["notification_id"],
                        "expected_old_payload_hash": notification["payload_hash"],
                        "tombstone_hash": tombstone_hash,
                        "redaction_version": redaction_version,
                    },
                    now=now,
                    reason_code=reason_code,
                )
            forget_audit_id = canonical_hash(
                [
                    "growth_event_forgotten",
                    owner.profile_id,
                    owner.profile_generation,
                    event_id,
                ]
            )
            forget_audit_hash = canonical_hash(
                {
                    "event_id": event_id,
                    "reason_code": reason_code,
                    "content_state": "tombstoned",
                }
            )
            db.execute(
                """INSERT INTO audit_events(
                     profile_id,profile_generation,audit_id,actor,action,
                     reason_code,before_hash,after_hash,lineage_ref,audit_hash,
                     schema_version,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)""",
                (
                    owner.profile_id,
                    owner.profile_generation,
                    forget_audit_id,
                    "companion_host",
                    "growth_event_forgotten",
                    reason_code,
                    str(event["event_hash"]),
                    forget_audit_hash,
                    f"growth_event:{event_id}",
                    forget_audit_hash,
                    now,
                ),
            )
            self._insert_outbox(
                db,
                owner,
                outbox_id=canonical_hash(["growth_event_forgotten", forget_audit_id]),
                event_kind="growth_event_forgotten",
                event_id=forget_audit_id,
                sink_kind="companion_notification",
                payload={
                    "schema_version": 1,
                    "forget_audit_id": forget_audit_id,
                    "reason_code": reason_code,
                },
                now=now,
                reason_code=reason_code,
            )
            self._bump_detail(
                db,
                owner,
                mutation_hash=canonical_hash(["forget_event", event_id, reason_code]),
                now=now,
            )
        return True
