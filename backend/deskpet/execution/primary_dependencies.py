"""Exact primary history sources, rebuilt from public SDK facts before use."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping

import aiosqlite
from simple_harness import (DisclosureContext, DeliveryRecipient, IntendedAudience,
    DisclosurePurpose, DisclosureSource, DisclosureTrust, DisclosureGeneration, DisclosureReasonCode)
from simple_harness.providers import ProviderRequestRejectedError
from deskpet.task_scope.protocol import canonical_json, digest, identifier

_REASON_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class PrimaryHistoryDisclosureRejected(ProviderRequestRejectedError):
    error_code = "primary_history_disclosure_rejected"
    default_message = "Current history dependencies cannot be verified for this request."


# The guard redacts its private cause, which left every rejection opaque at the
# provider boundary (corpus C12-19 surfaced only ``EXECUTION_FAILED``). These are
# the module's own literal failure codes: no path, identifier, hash, payload or
# message text from the request or the stores can reach this set.
_PUBLIC_REJECTION_REASONS = frozenset({
    "primary_dependencies_carrier_unsupported", "primary_dependencies_draft_sink_missing",
    "primary_dependencies_invalid", "primary_dependencies_item_mismatch",
    "primary_dependencies_limit", "primary_dependencies_missing",
    "primary_dependencies_not_visible", "primary_dependencies_short_bytes_mismatch",
    "primary_dependencies_short_sources_missing", "primary_disclosure_changed_during_check",
    "primary_input_claim_changed_during_check",
    # Raised by the physical request comparison this guard calls
    # (``composition.verify_current_input_provider_request``).
    "current_input_provider_store_unavailable", "current_input_provider_request_unbound",
    "current_input_provider_request_mismatch",
})
_GENERIC_REJECTION_REASON = "primary_dependencies_rejected"


def rejection_reason(exc):
    """Stable, payload-free reason for the redacted rejection; never a cause dump."""
    from deskpet.memory.current_input_source import CurrentInputSourceError

    if isinstance(exc, CurrentInputSourceError):
        # ``code`` is always ``"host_current_input_" + <literal>`` built in code.
        code = getattr(exc, "code", None)
        if isinstance(code, str) and _REASON_CODE.fullmatch(code):
            return code
        return _GENERIC_REJECTION_REASON
    if type(exc) is ValueError and str(exc) in _PUBLIC_REJECTION_REASONS:
        return str(exc)
    return _GENERIC_REJECTION_REASON


def dependencies(evidence=(), recall=(), short_horizon=(), *, schema_version=None, procedure_drafts=()):
    """Canonical bounded proof; v1 remains readable without rewriting archives."""
    short_horizon = tuple(short_horizon)
    procedure_drafts = tuple(procedure_drafts)
    version = (3 if procedure_drafts else 2 if short_horizon else 1) if schema_version is None else schema_version
    if type(version) is not int or version not in {1, 2, 3} or (version == 1 and short_horizon) or (version != 3 and procedure_drafts):
        raise ValueError("primary_dependencies_invalid")
    rows = {"evidence": {}, "recall": {}}
    fields_by_kind = [("evidence", evidence, ("evidence_id", "envelope_hash")),
                      ("recall", recall, ("result_id", "result_hash", "item_id", "item_hash"))]
    if version >= 2:
        rows["short_horizon"] = {}
        fields_by_kind.append(("short_horizon", short_horizon, ("audit_id", "chunk_ref", "content_hash")))
    if version == 3:
        rows["procedure_drafts"] = {}
        fields_by_kind.append(("procedure_drafts", procedure_drafts, ("memory_id", "revision", "candidate_hash")))
    for kind, values, fields in fields_by_kind:
        for value in values:
            if not isinstance(value, Mapping) or set(value) != set(fields):
                raise ValueError("primary_dependencies_invalid")
            value = dict(value)
            for key in fields:
                if key == "revision":
                    if type(value[key]) is not int or value[key] < 1: raise ValueError("primary_dependencies_invalid")
                else:
                    (digest if key.endswith("hash") else identifier)(value[key], key)
            rows[kind][canonical_json(value)] = value
    if sum(map(len, rows.values())) > 256:
        raise ValueError("primary_dependencies_limit")
    return {"schema_version": version, **{kind: [items[key] for key in sorted(items)] for kind, items in rows.items()}}


def parse_dependencies(value):
    if not isinstance(value, Mapping):
        raise ValueError("primary_dependencies_missing")
    version = value.get("schema_version")
    if type(version) is not int or version not in {1, 2, 3}:
        raise ValueError("primary_dependencies_missing")
    fields = {"schema_version", "evidence", "recall"} | ({"short_horizon"} if version >= 2 else set()) | ({"procedure_drafts"} if version == 3 else set())
    if set(value) != fields or not all(isinstance(value[k], (tuple, list)) for k in fields - {"schema_version"}):
        raise ValueError("primary_dependencies_missing")
    return dependencies(value["evidence"], value["recall"], value.get("short_horizon", ()), schema_version=version, procedure_drafts=value.get("procedure_drafts", ()))


def current_disclosure(*, run_id, subject, request_id):
    # Compatibility constructor for persisted pre-v48 unbound turns. Production
    # consumers must resolve their durable binding through trusted_disclosure.
    return DisclosureContext(run_id, subject, DeliveryRecipient.USER_SELF, subject,
        IntendedAudience.USER_SELF, DisclosurePurpose.TASK_EXECUTION,
        DisclosureSource.AUTHENTICATED_HOST, DisclosureTrust.TRUSTED_AUTHORITY,
        DisclosureGeneration.CURRENT, f"host:primary-request:{request_id}",
        (DisclosureReasonCode.MINIMUM_NECESSARY,))


async def read_run_dependencies(*, db, stack, sdk_run_id, before_effect_id=None, consumed_occurrences=frozenset()):
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
    cutoff = None
    if before_effect_id is not None:
        # The producer need not itself be context_route (e.g. a successful
        # task_scope_update). Use the real append-only primary effect order,
        # never the position of an unrelated subset of route receipts.
        from deskpet.task_scope.protocol import canonical_hash
        cursor = await db.execute("SELECT * FROM primary_effect_identities WHERE sdk_run_id=? AND effect_id=?",
            (sdk_run_id, before_effect_id))
        target = await cursor.fetchone()
        await cursor.close()
        if target is None:
            raise ValueError("primary_effect_index_prefix_missing")
        identity = dict(host_run_id=run["host_run_id"], sdk_run_id=sdk_run_id,
            effect_id=before_effect_id, tool_name=target["tool_name"])
        if (target["host_run_id"] != run["host_run_id"] or target["identity_json"] != canonical_json(identity)
                or target["identity_hash"] != canonical_hash(identity)):
            raise ValueError("primary_effect_index_identity_mismatch")
        _, (target_fact,) = stack.read_primary_dependency_facts(sdk_run_id, (before_effect_id,))
        if (target_fact is None or target_fact.run_id.value != sdk_run_id
                or target_fact.effect_id.value != before_effect_id or target_fact.tool_name != target["tool_name"]):
            raise ValueError("primary_effect_index_prefix_unverified")
        cutoff = target["sequence"]
    cursor = await db.execute(
        "SELECT receipt_json,origin,provider_turn_ordinal FROM context_route_decisions WHERE sdk_run_id=? ORDER BY rowid",
        (sdk_run_id,))
    decisions = [(json.loads(row[0]), row[1], row[2]) for row in await cursor.fetchall()]
    await cursor.close()
    if before_effect_id is not None:
        prefix = []
        for raw, origin, ordinal in decisions:
            if origin == "context_tool":
                cursor = await db.execute("SELECT * FROM primary_effect_identities WHERE sdk_run_id=? AND effect_id=?",
                    (sdk_run_id, raw.get("effect_id")))
                indexed = await cursor.fetchone()
                await cursor.close()
                if indexed is None:
                    raise ValueError("primary_effect_index_route_missing")
                identity = dict(host_run_id=run["host_run_id"], sdk_run_id=sdk_run_id,
                    effect_id=raw["effect_id"], tool_name="context_route")
                if (indexed["host_run_id"] != run["host_run_id"] or indexed["tool_name"] != "context_route"
                        or indexed["identity_json"] != canonical_json(identity) or indexed["identity_hash"] != canonical_hash(identity)):
                    raise ValueError("primary_effect_index_identity_mismatch")
                if indexed["sequence"] >= cutoff:
                    continue
            # Initial scope and no-recall identity markers retain their exact
            # validations below; they don't add a later tool source payload.
            prefix.append((raw, origin, ordinal))
        decisions = prefix
    routes = [raw for raw, origin, _ in decisions if origin == "context_tool"]
    start, effects = stack.read_primary_dependency_facts(sdk_run_id, tuple(r["effect_id"] for r in routes))
    metadata = start.get("input", {}).get("context_metadata", {})
    if metadata.get("root_run_id") != run["host_run_id"]:
        raise ValueError("primary_dependencies_run_mismatch")
    proof = parse_dependencies(metadata.get("visibility_dependencies"))
    current = {"evidence_id": run["evidence_id"], "envelope_hash": run["evidence_hash"]}
    if current not in proof["evidence"]:
        raise ValueError("primary_dependencies_current_user_missing")
    from deskpet.execution.primary_context_pages import verify_history_projections
    await verify_history_projections(db=db, stack=stack, run=run, sdk_run_id=sdk_run_id,
                                     start=start, proof=proof)
    recall = list(proof["recall"])
    evidence = list(proof["evidence"])
    short = list(proof.get("short_horizon", ()))
    drafts = list(proof.get("procedure_drafts", ()))
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
        drafts.extend(scope_proof.get("procedure_drafts", ()))
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
            drafts.extend(scope_proof.get("procedure_drafts", ()))
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
                drafts.extend(sources.get("procedure_drafts", ()))
                short.append(binding)
                continue
            append_typed_fragment_dependencies(fragment, evidence=evidence, recall=recall,
                procedure_drafts=drafts,
                consumed=(route.effect_id, fragment.get("ref")) in consumed_occurrences)
    # Every actual primary handler records its exact SDK identity, including
    # unscoped search/page-in. TaskScope reservations are not a complete index.
    if metadata.get("primary_effect_index_version") != 1:
        raise ValueError("primary_effect_index_contract_missing")
    cursor = await db.execute("SELECT * FROM primary_effect_identities WHERE sdk_run_id=? "
        "AND tool_name IN ('task_scope_search','context_page_in','procedure_discover') AND (? IS NULL OR sequence<?) ORDER BY sequence LIMIT 257",
        (sdk_run_id, cutoff, cutoff))
    searches = await cursor.fetchall()
    await cursor.close()
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
        failed_carrier = (fact.state.value == "failed" and fact.result.outcome.value == "failed"
                          and value is None)
        if failed_carrier and fact.result.error_code in ("missing_required_argument", "invalid_tool_arguments"):
            # The tool wrapper rejected the arguments before any handler ran, so
            # no candidate content was ever read; there is nothing to verify.
            # (r10: an omitted first-page cursor must not make the Run unverifiable.)
            continue
        if row["tool_name"] == "context_page_in" and failed_carrier:
            # The SDK failure carrier has no value. Independently reconstruct
            # this deterministic rejection by replaying the exact same
            # arguments; never interpret an arbitrary failed effect or a
            # claimed error code as a successful content read. (F07: the model
            # passing a typed recall item id as reference_id must not make the
            # whole Run unverifiable.)
            from deskpet.execution.primary_context_pages import (
                PREFIX as HISTORY_PAGE_PREFIX, PrimaryContextPageUnavailable, admitted_page)
            from deskpet.execution.current_tool_pages import PREFIX as CURRENT_PAGE_PREFIX, admitted_current_page
            arguments = thaw_json(fact.arguments)
            reference = arguments.get("reference_id") if isinstance(arguments, Mapping) else None
            # Only the primary page handler publishes a stable rejection code;
            # every other reference lands in the request-scoped store, whose
            # rejection the wrapper collapses into an opaque ``tool_failed``.
            primary_reference = isinstance(reference, str) and reference.startswith(
                (HISTORY_PAGE_PREFIX, CURRENT_PAGE_PREFIX))
            if primary_reference:
                # 事件 AF：公共消息现在是「固定前缀 + 可执行的下一步」。前缀逐字
                # 不变，所以升级前只有前缀本身的记录照样通过；改成前缀判定只是
                # 允许同一条拒绝多讲一句该拿什么 offset。
                from deskpet.tools.context_page_in_tools import PRIMARY_PAGE_PUBLIC_MESSAGE
                if not str(fact.result.public_message or "").startswith(PRIMARY_PAGE_PUBLIC_MESSAGE):
                    raise ValueError("scope_search_result_unverified")
            elif fact.result.error_code != "tool_failed":
                raise ValueError("scope_search_result_unverified")
            try:
                if isinstance(reference, str) and reference.startswith(CURRENT_PAGE_PREFIX):
                    await admitted_current_page(db=db, stack=stack, run=run, sdk_run_id=sdk_run_id,
                        page_effect=fact, arguments=arguments)
                else:
                    await admitted_page(db=db, stack=stack, run=run, sdk_run_id=sdk_run_id,
                        start=start, arguments=arguments)
            except PrimaryContextPageUnavailable as exc:
                # 事件 AF：一次纯措辞升级不得把还在飞的 Run 判成重放不一致。
                # ``rejection_code_matches`` 只认冻结的「新码 → 它取代的旧码」这
                # 一个方向，语义没有放宽（review M1 同款理由，见 legacy_summary）。
                from deskpet.execution.current_tool_pages import rejection_code_matches
                if primary_reference and not rejection_code_matches(fact.result.error_code, str(exc)):
                    raise ValueError("primary_page_rejection_mismatch") from exc
            else:
                # A replay that succeeds means the recorded failure was not
                # deterministic, so admitted content may have been read.
                raise ValueError("scope_search_result_unverified")
            continue
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, Mapping):
            raise ValueError("scope_search_result_unverified")
        if "error" in value:
            continue  # errors contain no candidate content
        if row["tool_name"] == "procedure_discover":
            from simple_harness_memory import ProcedureDraftCandidate
            if (set(value) != {"kind","execution_authorized","candidates","next_after","omitted_oversize"}
                    or value["kind"] != "procedure_draft_preview" or value["execution_authorized"] is not False
                    or type(value["omitted_oversize"]) is not int or not 0 <= value["omitted_oversize"] <= 128
                    or not isinstance(value["candidates"], (list,tuple)) or len(value["candidates"])>8):
                raise ValueError("procedure_draft_result_unverified")
            for item in value["candidates"]:
                if not isinstance(item, Mapping) or set(item) != {"candidate", "history_binding"}:
                    raise ValueError("procedure_draft_result_unverified")
                candidate = ProcedureDraftCandidate.from_json(dict(item["candidate"]))
                expected = {"memory_id":candidate.memory_id,"revision":candidate.revision,"candidate_hash":candidate.source_hash}
                if item["history_binding"] != expected: raise ValueError("procedure_draft_result_unverified")
                drafts.append(expected)
            if value["next_after"] is not None:
                identifier(value["next_after"], "next_after", 1024)
            continue
        if row["tool_name"] == "context_page_in":
            if value.get("kind") == "primary_current_tool_page_v1":
                from deskpet.execution.current_tool_pages import LEGACY_PAGE_SIZE, admitted_current_page
                expected = await admitted_current_page(db=db, stack=stack, run=run,
                    sdk_run_id=sdk_run_id, page_effect=fact, arguments=thaw_json(fact.arguments))
                if value != expected:
                    # 2026-09-09 事件 AG：页大小 1024 -> 4096 是一次纯容量升级。准入
                    # 身份（``canonical_hash(descriptor)`` + 字节 offset）一个字没变，
                    # 变的只有一页返回多少字节，所以升级瞬间还在飞的 Run 里已记录的
                    # 1 KiB 页必须仍然按旧页大小重算得一模一样——否则一次扩容会把它们
                    # 永久判死（理由与 ``legacy_summary`` 完全一致）。只在新页大小对不
                    # 上时才多算这一次，稳态零成本。
                    legacy = await admitted_current_page(db=db, stack=stack, run=run,
                        sdk_run_id=sdk_run_id, page_effect=fact, arguments=thaw_json(fact.arguments),
                        page_bytes=LEGACY_PAGE_SIZE)
                    if value != legacy:
                        raise ValueError("primary_current_page_effect_result_mismatch")
                # Original start and every earlier output source are already
                # included by this ordered reader; never assert a fake S1.
                continue
            if value.get("kind") == "primary_tool_history_page_v1":
                from deskpet.execution.primary_context_pages import LEGACY_PAGE_BYTES, admitted_page
                expected = await admitted_page(db=db, stack=stack, run=run, sdk_run_id=sdk_run_id,
                    start=start, arguments=thaw_json(fact.arguments))
                if value != expected:
                    # 事件 AG，同上：历史页也只是"一页多少字节"变了。
                    legacy = await admitted_page(db=db, stack=stack, run=run, sdk_run_id=sdk_run_id,
                        start=start, arguments=thaw_json(fact.arguments), page_bytes=LEGACY_PAGE_BYTES)
                    if value != legacy:
                        raise ValueError("primary_page_effect_result_mismatch")
                    expected = legacy
                source = expected["source"]
                evidence.append(dict(evidence_id=source["evidence_id"], envelope_hash=source["envelope_hash"]))
                continue
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
            # MM-D3: ``is_active`` is the Host's own route-ledger cursor, not
            # scope content. It carries no disclosure and is deliberately NOT
            # re-derived here — the active cursor moves independently of the
            # package, so a later re-read is a different fact, not a check.
            # Admit exactly one boolean annotation; every content field below
            # stays an exact re-derivation of the verified package.
            content = {key: value_ for key, value_ in item.items() if key != "is_active"}
            if (not isinstance(package, Mapping)
                    or not isinstance(item.get("is_active", False), bool)
                    or content != {"task_scope_id":package["task_scope_id"], "source_id":package["source_id"], "source_hash":package["source_hash"], "scope_disclosure":package}):
                raise ValueError("scope_search_candidate_unverified")
            scope_proof = await verify_scope_disclosure(db_path=host_path, package=package, subject=run["subject"], stack=stack)
            evidence.extend(scope_proof["evidence"])
            recall.extend(scope_proof["recall"])
            short.extend(scope_proof.get("short_horizon", ()))
            drafts.extend(scope_proof.get("procedure_drafts", ()))
    return run, dependencies(evidence, recall, short, schema_version=3 if drafts else 2 if short else proof["schema_version"], procedure_drafts=drafts)


def append_typed_fragment_dependencies(fragment, *, evidence, recall, consumed, procedure_drafts=None):
    """Append this occurrence's dependencies without deleting any base proof.

    In particular, an identical 4-tuple already present in history survives a
    consumed current occurrence. Host short group/indirect roots always survive.
    """
    if fragment.get("lane") not in {"long_term_typed", "short_horizon_typed"}:
        raise ValueError("primary_dependencies_carrier_unsupported")
    item = dependencies(recall=[fragment.get("history_binding")])["recall"][0]
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
        recall.extend(value for value in sources["recall"] if not consumed or value != item)
        if sources.get("procedure_drafts"):
            if procedure_drafts is None:
                raise ValueError("primary_dependencies_draft_sink_missing")
            procedure_drafts.extend(sources["procedure_drafts"])
    if not consumed:
        recall.append(item)


async def check_runtime_dependencies(*, db_path, stack, sdk_run_id, request, policy_factory, typed_use_authority=None):
    """Guard strictly before delegate invocation; typed definite failure only here."""
    from deskpet.memory.trusted_disclosure import resolve_current_disclosure
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            consumed = frozenset()
            if typed_use_authority is not None:
                consumed = await typed_use_authority.consumed_occurrences(
                    db=db, run_id=sdk_run_id, request=request,
                )
            found = await read_run_dependencies(db=db, stack=stack, sdk_run_id=sdk_run_id,
                                                 consumed_occurrences=consumed)
            if found is None:
                return  # trusted Host lookup: this is not a foreground primary Run
            run, proof = found
            from deskpet.execution.current_tool_pages import verify_control_stubs, verify_request
            verify_request(stack, sdk_run_id, request.messages)
            # F-E2: an elided control carrier is re-derived against the same
            # public audit/effect facts before the request may leave, in its own
            # pass — a control notice must never enter the page-admission map
            # ``verify_request`` returns, or eliding an attestation would make
            # its body ``context_page_in``-readable.
            verify_control_stubs(stack, sdk_run_id, request.messages)
            policy = policy_factory(run["subject"])
            disclosure = await resolve_current_disclosure(db_path=db_path, run_id=sdk_run_id,
                subject=run["subject"], request_id=request.request_id.value)
            original_input_claim = None
            if ":input-v1:" in disclosure.authority_ref:
                from deskpet.memory.current_input_visibility import claim_stamp
                # ``CLAIMED`` is the admitted hand-off state for this very
                # request (the ``SDK_START`` boundary), not a pre-claim read.
                original_input_claim = await claim_stamp(db_path, run["host_run_id"], sdk_run_id)
                stack.verify_current_input_provider_request(sdk_run_id, request)
            allowed = await policy.check_dependencies(db=db, primary_ref=run["primary_conversation_id"],
                dependencies=proof, disclosure_context=disclosure)
            if not allowed:
                raise ValueError("primary_dependencies_not_visible")
        # Fresh connection/snapshot AFTER the asynchronous checker and its DB
        # cleanup: a true result for an earlier generation is not current use.
        current = await resolve_current_disclosure(db_path=db_path, run_id=sdk_run_id,
            subject=run["subject"], request_id=request.request_id.value)
        if current != disclosure:
            raise ValueError("primary_disclosure_changed_during_check")
        if original_input_claim is not None:
            from deskpet.memory.current_input_visibility import same_physical_claim
            # G1->G2 still rejects rather than substitutes; the sole tolerated
            # movement is the Host recording the start of THIS hand-off.
            if not same_physical_claim(original_input_claim,
                                       await claim_stamp(db_path, run["host_run_id"], sdk_run_id)):
                raise ValueError("primary_input_claim_changed_during_check")
            stack.verify_current_input_provider_request(sdk_run_id, request)
    except Exception as exc:
        raise PrimaryHistoryDisclosureRejected(
            public_message=rejection_reason(exc), private_cause=exc) from None
