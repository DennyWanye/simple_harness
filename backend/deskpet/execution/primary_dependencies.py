"""Exact primary history sources, rebuilt from public SDK facts before use."""
from __future__ import annotations

import json
from collections.abc import Mapping

import aiosqlite
from simple_harness import (DisclosureContext, DeliveryRecipient, IntendedAudience,
    DisclosurePurpose, DisclosureSource, DisclosureTrust, DisclosureGeneration, DisclosureReasonCode)
from simple_harness.providers import ProviderRequestRejectedError
from deskpet.task_scope.protocol import canonical_json, digest, identifier


class PrimaryHistoryDisclosureRejected(ProviderRequestRejectedError):
    error_code = "primary_history_disclosure_rejected"
    default_message = "Current history dependencies cannot be verified for this request."


def dependencies(evidence=(), recall=()):
    """Canonical bounded proof; None/unknown carriers are never an empty allow."""
    rows = {"evidence": {}, "recall": {}}
    for kind, values, fields in (("evidence", evidence, ("evidence_id", "envelope_hash")),
                                 ("recall", recall, ("result_id", "result_hash", "item_id", "item_hash"))):
        for value in values:
            if not isinstance(value, Mapping) or set(value) != set(fields):
                raise ValueError("primary_dependencies_invalid")
            value = dict(value)
            for key in fields:
                (digest if key.endswith("hash") else identifier)(value[key], key)
            rows[kind][canonical_json(value)] = value
    if sum(map(len, rows.values())) > 256:
        raise ValueError("primary_dependencies_limit")
    return {"schema_version": 1, **{kind: [items[key] for key in sorted(items)] for kind, items in rows.items()}}


def parse_dependencies(value):
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "evidence", "recall"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not all(isinstance(value[k], (tuple, list)) for k in ("evidence", "recall"))):
        raise ValueError("primary_dependencies_missing")
    return dependencies(value["evidence"], value["recall"])


def current_disclosure(*, run_id, subject, request_id):
    # Host-selected local-owner task execution, as in the production typed
    # RecallPlan. The actual request identity is bound to this fresh observation.
    return DisclosureContext(run_id, subject, DeliveryRecipient.USER_SELF, subject,
        IntendedAudience.USER_SELF, DisclosurePurpose.TASK_EXECUTION,
        DisclosureSource.AUTHENTICATED_HOST, DisclosureTrust.TRUSTED_AUTHORITY,
        DisclosureGeneration.CURRENT, f"host:primary-request:{request_id}",
        (DisclosureReasonCode.MINIMUM_NECESSARY,))


async def read_run_dependencies(*, db, stack, sdk_run_id):
    """Retain ALL consumed recall routes, even after public Context pruning.

    Host SQL supplies only Host identities; start/effects come from the SDK's
    public ports. This reader never enumerates SDK private tables.
    """
    cursor = await db.execute(
        "SELECT r.host_run_id,r.subject,r.primary_conversation_id,t.evidence_id,t.evidence_hash "
        "FROM foreground_runs r JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id "
        "JOIN foreground_turns t ON t.turn_id=r.turn_id AND t.subject=r.subject WHERE b.sdk_run_id=?",
        (sdk_run_id,))
    run = await cursor.fetchone()
    await cursor.close()
    if run is None:
        start, _ = stack.read_primary_dependency_facts(sdk_run_id)
        metadata = start.get("input", {}).get("context_metadata", {})
        cursor = await db.execute("SELECT 1 FROM foreground_runs WHERE host_run_id=?",
            (metadata.get("root_run_id"),))
        owned_run = await cursor.fetchone()
        await cursor.close()
        if "visibility_dependencies" in metadata or owned_run is not None:
            raise ValueError("primary_dependencies_host_binding_missing")
        return None
    cursor = await db.execute(
        "SELECT receipt_json,origin,provider_turn_ordinal FROM context_route_decisions WHERE sdk_run_id=? ORDER BY rowid",
        (sdk_run_id,))
    decisions = [(json.loads(row[0]), row[1], row[2]) for row in await cursor.fetchall()]
    routes = [raw for raw, origin, _ in decisions if origin == "context_tool"]
    await cursor.close()
    start, effects = stack.read_primary_dependency_facts(sdk_run_id, tuple(r["effect_id"] for r in routes))
    metadata = start.get("input", {}).get("context_metadata", {})
    if metadata.get("root_run_id") != run["host_run_id"]:
        raise ValueError("primary_dependencies_run_mismatch")
    proof = parse_dependencies(metadata.get("visibility_dependencies"))
    current = {"evidence_id": run["evidence_id"], "envelope_hash": run["evidence_hash"]}
    if current not in proof["evidence"]:
        raise ValueError("primary_dependencies_current_user_missing")
    recall = list(proof["recall"])
    from simple_harness.execution.context_authority import ContextRouteReceipt
    from simple_harness.runtime.task_scope_protocol import TaskScopeRoute
    from simple_harness import thaw_json
    import uuid
    for raw, origin, ordinal in decisions:
        if origin == "context_tool":
            continue
        if origin == "no_recall":
            # ProductRuntimeDecisionSink records an exact Host synthetic marker,
            # not a tool effect. It carries no historical content or scope.
            marker = f"no-recall:{sdk_run_id}:{ordinal}"
            expected = ContextRouteReceipt(
                receipt_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"simple-harness:{marker}")),
                run_id=sdk_run_id, raw_call_id=marker, effect_id=marker,
                route=TaskScopeRoute.DIRECT_STANDALONE, task_scope_id=None,
                binding_set_revision=None)
            if raw != expected.to_json():
                raise ValueError("primary_dependencies_no_recall_mismatch")
        elif origin == "host_initial":
            # Initial scope authority is not a tool effect either. Its historical
            # snapshot still needs the scoped source contract; never exempt it.
            raise ValueError("primary_dependencies_initial_scope_sources_missing")
        else:
            raise ValueError("primary_dependencies_origin_invalid")
    for raw, fact in zip(routes, effects, strict=True):
        route = ContextRouteReceipt.from_json(raw)
        if (fact is None or not fact.terminal or fact.result is None
                or fact.run_id.value != sdk_run_id or fact.effect_id.value != route.effect_id
                or fact.raw_call_id != route.raw_call_id or fact.tool_name != "context_route"):
            raise ValueError("primary_dependencies_tool_unverified")
        value = thaw_json(fact.result.value)
        if not isinstance(value, Mapping) or value.get("context_route_receipt") != route.to_json():
            raise ValueError("primary_dependencies_route_mismatch")
        # A successful exact route authorizes task effects; it does not prove
        # visibility of the historical ResumePackage returned with that route.
        # Until its real sources are bound, deny BEFORE the next delegate call.
        # Inspect every durable route, so a later continue_active cannot hide an
        # earlier consumed package, even if public Context is subsequently pruned.
        if raw["route"] == "resume_existing" or "resume_package" in value:
            raise ValueError("primary_dependencies_resume_sources_missing")
        if raw["route"] != "memory_standalone":
            continue
        if not isinstance(value.get("fragments"), (list, tuple)):
            raise ValueError("primary_dependencies_route_mismatch")
        fragments = value["fragments"]
        if tuple(dict.fromkeys(f["ref"] for f in fragments)) != tuple(route.recall_refs):
            raise ValueError("primary_dependencies_recall_refs_mismatch")
        for fragment in fragments:
            if fragment.get("lane") != "long_term_typed":
                raise ValueError("primary_dependencies_carrier_unsupported")
            binding = fragment.get("history_binding")
            item = dependencies(recall=[binding])["recall"][0]
            if item["item_id"] != fragment["ref"]:
                raise ValueError("primary_dependencies_item_mismatch")
            recall.append(item)
    return run, dependencies(proof["evidence"], recall)


async def check_runtime_dependencies(*, db_path, stack, sdk_run_id, request, policy_factory):
    """Guard strictly before delegate invocation; typed definite failure only here."""
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            found = await read_run_dependencies(db=db, stack=stack, sdk_run_id=sdk_run_id)
            if found is None:
                return  # trusted Host lookup: this is not a foreground primary Run
            run, proof = found
            policy = policy_factory(run["subject"])
            allowed = await policy.check_dependencies(db=db, primary_ref=run["primary_conversation_id"],
                dependencies=proof, disclosure_context=current_disclosure(run_id=sdk_run_id,
                    subject=run["subject"], request_id=request.request_id.value))
            if not allowed:
                raise ValueError("primary_dependencies_not_visible")
    except Exception as exc:
        raise PrimaryHistoryDisclosureRejected(private_cause=exc) from None
