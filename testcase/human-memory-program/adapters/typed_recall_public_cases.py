"""Real public replay/capability calls. Inputs contain attacks, never outcomes.

No private imports, SQL, instrumentation of SDK internals, or model loading.
"""
import dataclasses
import simple_harness as harness
import simple_harness_memory as memory


def execution_wire(value):
    return {"decision": value.decision.to_json(), "result": value.result.to_json(),
            "candidate_query_started": value.candidate_query_started,
            "candidate_query_count": value.candidate_query_count, "replayed": value.replayed,
            "degradation_codes": list(value.degradation_codes),
            "decision_hash": value.decision.decision_hash, "result_hash": value.result.result_hash,
            "result_item_hashes": [item.result_item_hash for item in value.result.items],
            "unsupported_capabilities": list(value.unsupported_capabilities)}


def plan_for(context, plan):
    return dataclasses.replace(plan, run_id=context.run_id, context_hash=context.context_hash,
        context_revision=context.context_revision, query=context.query,
        disclosure_context=context.disclosure_context, budget=context.budget,
        selector_domains=context.allowed_selector_domains,
        retrieval_modes=context.allowed_retrieval_modes,
        event_constraint_refs=context.event_constraint_refs,
        environment_constraint_refs=context.environment_constraint_refs,
        task_phase_authority_refs=context.task_phase_authority_refs)


async def replay_cases(manager, principal, context, plan, inputs, now, snapshot):
    cells = []
    # Baseline authority/budget match the original frozen mutation values.
    context = dataclasses.replace(context, run_id="run-1", context_revision=7,
        disclosure_context=dataclasses.replace(context.disclosure_context, run_id="run-1",
            purpose=harness.DisclosurePurpose.TASK_EXECUTION),
        budget=harness.RecallBudget(4, 4096, 1024, 2000))
    plan = dataclasses.replace(plan_for(context, plan), plan_id="plan-1", idempotency_key="a2-replay")
    first = await manager.execute_typed_recall(principal=principal, context=context, plan=plan, now=now)
    replay = await manager.execute_typed_recall(principal=principal, context=context, plan=plan, now=now)
    cells.append({"cell_id": "unsupported-replay/exact-replay", "status": "OBSERVED", "reason": "",
        "observations": {"calls": ["execute_typed_recall", "execute_typed_recall"],
        "context": context.to_json(), "plan": plan.to_json(), "first": execution_wire(first),
        "replay": execution_wire(replay)}})
    for attack in inputs["mutations"]:
        name = "unsupported-replay/request-hash:" + attack["original_attack"]
        if attack["original_attack"] == "protocol_version":
            cells.append({"cell_id": name, "status": "BLOCKED",
                "reason": "NO_PUBLIC_REQUEST_PROTOCOL_VERSION_INPUT", "observations": {}})
            continue
        c, p, identity = context, plan, principal
        field, value = attack["original_attack"], attack["mutation"]
        observed = {"calls": [], "memory_called": False}
        try:
            if field == "principal_id":
                identity = dataclasses.replace(principal, actor_id=value)
            elif field == "context_hash":
                p = dataclasses.replace(p, context_hash=value)
            elif field == "plan_id":
                p = dataclasses.replace(p, plan_id=value)
            elif field == "plan_hash":
                p = dataclasses.replace(p, reason_codes=tuple(harness.RecallReasonCode(v) for v in value))
            else:
                if field in {"run_id", "context_revision"}:
                    if field == "run_id":
                        c = dataclasses.replace(c, run_id=value,
                            disclosure_context=dataclasses.replace(c.disclosure_context, run_id=value))
                    else:
                        c = dataclasses.replace(c, context_revision=value)
                elif field.startswith("budget."):
                    c = dataclasses.replace(c, budget=dataclasses.replace(c.budget, **{field.split('.')[1]: value}))
                else:
                    key = "authority_ref" if field == "disclosure_hash" else field
                    if key == "recipient":
                        value = harness.DeliveryRecipient(value)
                    elif key == "purpose":
                        value = harness.DisclosurePurpose(value)
                    c = dataclasses.replace(c, disclosure_context=dataclasses.replace(c.disclosure_context, **{key: value}))
                p = plan_for(c, p)
            observed.update(context=c.to_json(), plan=p.to_json(), principal_actor_id=identity.actor_id)
            observed["before_manifest"] = await snapshot()
            observed["memory_called"] = True
            observed["calls"].append("execute_typed_recall")
            execution = await manager.execute_typed_recall(principal=identity, context=c, plan=p, now=now)
            observed["execution"] = execution_wire(execution)
        except Exception as exc:
            observed.update(exception_type=type(exc).__name__, exception_reason=str(exc),
                            exception_code=getattr(exc, "code", None))
        observed["after_manifest"] = await snapshot()
        cells.append({"cell_id": name, "status": "OBSERVED", "reason": "", "observations": observed})
    # Same-idempotency changed valid query: actual original conflict entrypoint.
    changed = dataclasses.replace(context, query=context.query + " changed")
    observed = {"calls": ["execute_typed_recall"], "memory_called": True}
    try:
        result = await manager.execute_typed_recall(principal=principal, context=changed,
            plan=plan_for(changed, plan), now=now)
        observed["execution"] = execution_wire(result)
    except Exception as exc:
        observed.update(exception_type=type(exc).__name__, exception_reason=str(exc))
    cells.append({"cell_id": "unsupported-replay/conflicting-replay", "status": "OBSERVED",
                  "reason": "", "observations": observed})
    for case in inputs["unsupported"]:
        observed = {"calls": [], "memory_called": False}
        try:
            # Frozen lists describe unsupported attacks. MEMORY_TYPE and the
            # default FULL_TEXT mode complete their mandatory typed carriers;
            # neither contributes an unsupported reason. Preserve original lists.
            selectors = (harness.RecallSelectorDomain.MEMORY_TYPE,) + tuple(
                harness.RecallSelectorDomain(v.lower()) for v in case["selectors"])
            modes = tuple(harness.RecallRetrievalMode(v.lower()) for v in case["modes"]) or (
                harness.RecallRetrievalMode.FULL_TEXT,)
            observed["input_carrier"] = {"original_selectors": case["selectors"],
                "original_modes": case["modes"], "added_supported_selector": "memory_type",
                "default_supported_mode": "full_text" if not case["modes"] else None}
            c = dataclasses.replace(context, allowed_selector_domains=selectors, allowed_retrieval_modes=modes,
                event_constraint_refs=("event-1",) if harness.RecallSelectorDomain.EVENT in selectors else (),
                environment_constraint_refs=("environment-1",) if harness.RecallSelectorDomain.ENVIRONMENT in selectors else (),
                task_phase_authority_refs=("task-phase-1",) if harness.RecallSelectorDomain.TASK_PHASE in selectors else ())
            p = dataclasses.replace(plan_for(c, plan), idempotency_key="unsupported-" + case["id"])
            observed.update(context=c.to_json(), plan=p.to_json(), memory_called=True,
                            calls=["execute_typed_recall"])
            value = await manager.execute_typed_recall(principal=principal, context=c, plan=p, now=now)
            observed["execution"] = execution_wire(value)
        except Exception as exc:
            observed.update(exception_type=type(exc).__name__, exception_reason=str(exc))
        cells.append({"cell_id": "unsupported-replay/" + case["id"], "status": "OBSERVED",
                      "reason": "", "observations": observed})
    return cells


def audit_authority(principal, now):
    context = harness.DisclosureContext.from_json({"schema_version": 1, "run_id": "a2-audit",
        "subject": principal.actor_id, "recipient": "audit_reviewer", "recipient_id": principal.actor_id,
        "intended_audience": "auditor", "purpose": "audit", "source": "audit_access_decision",
        "trust": "trusted_authority", "generation": "current", "authority_ref": "a2-audit-authority",
        "reason_codes": ["disclosure_audit_grant_required"]})
    decision = memory.SealedAuditAccessDecision(decision_id="a2-audit-decision", subject=principal.actor_id,
        scope_kind="subject", scope_ref=principal.actor_id, reason_code="user_requested_audit",
        disclosure_context=context, max_reads=32, issued_at=now - 1, expires_at=now + 600)
    reference = memory.AuditAccessAuthorityRefV1(authority_id="a2-audit-authority", issuer_ref="host-audit-authority",
        nonce="a2-audit-nonce", replay_identity="a2-audit-replay", requester_deployment_id=principal.deployment_id,
        requester_household_id=principal.household_id, requester_actor_id=principal.actor_id,
        requester_session_id=principal.session_id, target_deployment_id=principal.deployment_id,
        target_household_id=principal.household_id, target_actor_id=principal.actor_id,
        target_subject=principal.actor_id, decision_id=decision.decision_id, decision_hash=decision.decision_hash,
        scope_kind="subject", scope_ref=principal.actor_id, issued_at=decision.issued_at, expires_at=decision.expires_at)

    class Authority:
        async def resolve_audit_access(self, received):
            if received != reference:
                raise ValueError("unknown audit reference")
            return decision

    return Authority(), reference
