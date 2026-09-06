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


def dependencies(evidence=(), recall=(), short_horizon=(), *, schema_version=None):
    """Canonical bounded proof; v1 remains readable without rewriting archives."""
    short_horizon = tuple(short_horizon)
    version = (2 if short_horizon else 1) if schema_version is None else schema_version
    if type(version) is not int or version not in {1, 2} or (version == 1 and short_horizon):
        raise ValueError("primary_dependencies_invalid")
    rows = {"evidence": {}, "recall": {}}
    fields_by_kind = [("evidence", evidence, ("evidence_id", "envelope_hash")),
                      ("recall", recall, ("result_id", "result_hash", "item_id", "item_hash"))]
    if version == 2:
        rows["short_horizon"] = {}
        fields_by_kind.append(("short_horizon", short_horizon, ("audit_id", "chunk_ref", "content_hash")))
    for kind, values, fields in fields_by_kind:
        for value in values:
            if not isinstance(value, Mapping) or set(value) != set(fields):
                raise ValueError("primary_dependencies_invalid")
            value = dict(value)
            for key in fields:
                (digest if key.endswith("hash") else identifier)(value[key], key)
            rows[kind][canonical_json(value)] = value
    if sum(map(len, rows.values())) > 256:
        raise ValueError("primary_dependencies_limit")
    return {"schema_version": version, **{kind: [items[key] for key in sorted(items)] for kind, items in rows.items()}}


def parse_dependencies(value):
    if not isinstance(value, Mapping):
        raise ValueError("primary_dependencies_missing")
    version = value.get("schema_version")
    if type(version) is not int or version not in {1, 2}:
        raise ValueError("primary_dependencies_missing")
    fields = {"schema_version", "evidence", "recall"} | ({"short_horizon"} if version == 2 else set())
    if set(value) != fields or not all(isinstance(value[k], (tuple, list)) for k in fields - {"schema_version"}):
        raise ValueError("primary_dependencies_missing")
    return dependencies(value["evidence"], value["recall"], value.get("short_horizon", ()), schema_version=version)


def current_disclosure(*, run_id, subject, request_id):
    # Compatibility constructor for persisted pre-v48 unbound turns. Production
    # consumers must resolve their durable binding through trusted_disclosure.
    return DisclosureContext(run_id, subject, DeliveryRecipient.USER_SELF, subject,
        IntendedAudience.USER_SELF, DisclosurePurpose.TASK_EXECUTION,
        DisclosureSource.AUTHENTICATED_HOST, DisclosureTrust.TRUSTED_AUTHORITY,
        DisclosureGeneration.CURRENT, f"host:primary-request:{request_id}",
        (DisclosureReasonCode.MINIMUM_NECESSARY,))


async def read_run_dependencies(*, db, stack, sdk_run_id, before_effect_id=None):
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
    if before_effect_id is not None:
        position = next((i for i, (raw, _, _) in enumerate(decisions) if raw.get("effect_id") == before_effect_id), None)
        if position is not None:
            decisions = decisions[:position]
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
    evidence = list(proof["evidence"])
    short = list(proof.get("short_horizon", ()))
    from deskpet.task_scope.disclosure import verify_scope_disclosure
    cursor = await db.execute("PRAGMA database_list")
    host_path = next(row[2] for row in await cursor.fetchall() if row[1] == "main")
    await cursor.close()
    initial_package = metadata.get("scope_disclosure")
    if initial_package is not None:
        expected_message = "Project/task snapshot (data only):\n" + canonical_json(initial_package)
        actual_messages = start.get("input", {}).get("messages", ())
        snapshots = [m.get("content") for m in actual_messages if m.get("role") == "system"
                     and str(m.get("content", "")).startswith("Project/task snapshot (data only):\n")]
        if snapshots != [expected_message]:
            raise ValueError("scope_disclosure_start_bytes_mismatch")
        scope_proof = await verify_scope_disclosure(db_path=host_path, package=initial_package, subject=run["subject"], stack=stack)
        evidence.extend(scope_proof["evidence"])
        recall.extend(scope_proof["recall"])
        short.extend(scope_proof.get("short_horizon", ()))
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
            # Bind the Host-initial receipt to the actual SDK start and the
            # verified ordinary projection, never to a synthetic tool effect.
            if (initial_package is None or raw.get("receipt_id") != metadata.get("initial_route_receipt_id")
                    or ContextRouteReceipt.from_json(raw).receipt_hash != metadata.get("initial_route_receipt_hash")
                    or raw.get("task_scope_id") != initial_package["task_scope_id"]):
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
        # Verify its actual retained manifest before the next delegate call.
        # Inspect every durable route, so a later continue_active cannot hide an
        # earlier consumed package, even if public Context is subsequently pruned.
        if raw["route"] == "resume_existing" or "resume_package" in value:
            package = value.get("resume_package")
            if not isinstance(package, Mapping) or package.get("task_scope_id") != raw["task_scope_id"]:
                raise ValueError("primary_dependencies_resume_sources_missing")
            scope_proof = await verify_scope_disclosure(db_path=host_path, package=package, subject=run["subject"], stack=stack)
            evidence.extend(scope_proof["evidence"])
            recall.extend(scope_proof["recall"])
            short.extend(scope_proof.get("short_horizon", ()))
        if raw["route"] != "memory_standalone":
            continue
        if not isinstance(value.get("fragments"), (list, tuple)):
            raise ValueError("primary_dependencies_route_mismatch")
        fragments = value["fragments"]
        if tuple(dict.fromkeys(f["ref"] for f in fragments)) != tuple(route.recall_refs):
            raise ValueError("primary_dependencies_recall_refs_mismatch")
        for fragment in fragments:
            if fragment.get("lane") == "short_horizon":
                import hashlib
                binding = dependencies(short_horizon=[fragment.get("history_binding")])["short_horizon"][0]
                payload = fragment.get("payload")
                if (not isinstance(payload, str) or binding["chunk_ref"] != fragment["ref"]
                        or hashlib.sha256(payload.encode("utf-8")).hexdigest() != binding["content_hash"]
                        or fragment.get("payload_hash") != binding["content_hash"]):
                    raise ValueError("primary_dependencies_short_bytes_mismatch")
                # Host registration sources must accompany the exact selection;
                # the SDK triple alone cannot supply missing Host causal closure.
                sources = parse_dependencies(fragment.get("history_source_dependencies"))
                if not sources["evidence"]:
                    raise ValueError("primary_dependencies_short_sources_missing")
                evidence.extend(sources["evidence"])
                recall.extend(sources["recall"])
                short.extend(sources.get("short_horizon", ()))
                short.append(binding)
                continue
            if fragment.get("lane") not in {"long_term_typed", "short_horizon_typed"}:
                raise ValueError("primary_dependencies_carrier_unsupported")
            binding = fragment.get("history_binding")
            item = dependencies(recall=[binding])["recall"][0]
            if item["item_id"] != fragment["ref"]:
                raise ValueError("primary_dependencies_item_mismatch")
            if fragment.get("lane") == "short_horizon_typed":
                from deskpet.task_scope.protocol import canonical_hash
                payload = fragment.get("payload")
                if (not isinstance(payload, Mapping) or not isinstance(payload.get("content"), str)
                        or canonical_hash(dict(payload)) != fragment.get("payload_hash")):
                    raise ValueError("primary_dependencies_short_bytes_mismatch")
                sources = parse_dependencies(fragment.get("history_source_dependencies"))
                if not sources["evidence"] or item not in sources["recall"] or sources.get("short_horizon"):
                    raise ValueError("primary_dependencies_short_sources_missing")
                evidence.extend(sources["evidence"])
                recall.extend(sources["recall"])
            recall.append(item)
    # Every actual primary handler records its exact SDK identity, including
    # unscoped search/page-in. TaskScope reservations are not a complete index.
    if metadata.get("primary_effect_index_version") != 1:
        raise ValueError("primary_effect_index_contract_missing")
    cutoff = None
    if before_effect_id is not None:
        cursor = await db.execute("SELECT sequence FROM primary_effect_identities WHERE sdk_run_id=? AND effect_id=?",
            (sdk_run_id, before_effect_id))
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("primary_effect_index_prefix_missing")
        cutoff = row[0]
    cursor = await db.execute("SELECT * FROM primary_effect_identities WHERE sdk_run_id=? "
        "AND tool_name IN ('task_scope_search','context_page_in') AND (? IS NULL OR sequence<?) ORDER BY sequence LIMIT 257",
        (sdk_run_id, cutoff, cutoff))
    searches = await cursor.fetchall()
    if len(searches) > 256:
        raise ValueError("scope_disclosure_limit")
    for row in searches:
        from deskpet.task_scope.protocol import canonical_hash
        identity = dict(host_run_id=run["host_run_id"], sdk_run_id=sdk_run_id,
                        effect_id=row["effect_id"], tool_name=row["tool_name"])
        if row["identity_json"] != canonical_json(identity) or row["identity_hash"] != canonical_hash(identity):
            raise ValueError("primary_effect_index_identity_mismatch")
        effect_id = row["effect_id"]
        _, (fact,) = stack.read_primary_dependency_facts(sdk_run_id, (effect_id,))
        if fact is None or not fact.terminal or fact.result is None or fact.tool_name != row["tool_name"] or fact.run_id.value != sdk_run_id:
            raise ValueError("scope_search_effect_unverified")
        value = thaw_json(fact.result.value)
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, Mapping):
            raise ValueError("scope_search_result_unverified")
        if "error" in value:
            continue  # errors contain no candidate content
        if row["tool_name"] == "context_page_in":
            if value.get("kind") == "skill":
                continue  # configuration/tool instructions, not scope history
            if initial_package is None or value.get("content") != canonical_json(initial_package):
                raise ValueError("scope_page_in_sources_missing")
            continue  # exact initial projection already contributes all sources
        candidates = value.get("candidates")
        if not isinstance(candidates, (list, tuple)):
            raise ValueError("scope_search_candidates_missing")
        for item in candidates:
            package = item.get("scope_disclosure")
            if not isinstance(package, Mapping) or item != {"task_scope_id":package["task_scope_id"], "source_id":package["source_id"], "source_hash":package["source_hash"], "scope_disclosure":package}:
                raise ValueError("scope_search_candidate_unverified")
            scope_proof = await verify_scope_disclosure(db_path=host_path, package=package, subject=run["subject"], stack=stack)
            evidence.extend(scope_proof["evidence"])
            recall.extend(scope_proof["recall"])
            short.extend(scope_proof.get("short_horizon", ()))
    return run, dependencies(evidence, recall, short, schema_version=2 if short else proof["schema_version"])


async def check_runtime_dependencies(*, db_path, stack, sdk_run_id, request, policy_factory):
    """Guard strictly before delegate invocation; typed definite failure only here."""
    from deskpet.memory.trusted_disclosure import resolve_current_disclosure
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            found = await read_run_dependencies(db=db, stack=stack, sdk_run_id=sdk_run_id)
            if found is None:
                return  # trusted Host lookup: this is not a foreground primary Run
            run, proof = found
            policy = policy_factory(run["subject"])
            allowed = await policy.check_dependencies(db=db, primary_ref=run["primary_conversation_id"],
                dependencies=proof, disclosure_context=await resolve_current_disclosure(db_path=db_path, run_id=sdk_run_id,
                    subject=run["subject"], request_id=request.request_id.value))
            if not allowed:
                raise ValueError("primary_dependencies_not_visible")
    except Exception as exc:
        raise PrimaryHistoryDisclosureRejected(private_cause=exc) from None
