from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from deskpet.workflows.contracts import NodeExecutionIdentity
from deskpet.workflows.contracts import canonical_json
from deskpet.workflows.adapters.deep_research_v6_retrieval_runtime import (
    DurableV6DeadlinePort,
    DurableV6PageReadEffectAdapter,
    v6_read_logical_effect_id,
)
from deskpet.workflows.adapters.research_runtime import BoundResearchEffectContext
from deskpet.workflows.effects import (
    BudgetReservationExceeded,
    EffectExecutionContext,
    EffectJournal,
    EffectStateConflict,
    StagingPreconditionFailed,
)
from deskpet.workflows.store import RegisteredBlobStore, WorkflowRunStore
from deskpet.workflows.adapters.deep_research_v6_evidence_runtime import (
    DeepResearchV6EvidenceRuntime,
)
from deskpet.workflows.definitions.deep_research_v6_compiler import compile_official_exact_fact
from deskpet.workflows.definitions.deep_research_v6_retrieval_contracts import (
    OfficialSearchResultV1,
    PageAttemptOutcomeV1,
    PageExtractionResultV1,
    SearchCandidateV1,
    SourceLocatorV1,
)


def _contract_search_result() -> OfficialSearchResultV1:
    ref = "sha256:" + "1" * 64
    candidate = SearchCandidateV1.create(
        ordinal=0, source_locator_ref=ref,
        title_hash="2" * 64, snippet_hash="3" * 64,
        authority_match="unverified", reason_codes=("candidate_unverified",),
    )
    return OfficialSearchResultV1.create(
        logical_effect_id="logical-search", attempt_no=1, request_id="request-1",
        query_hash="4" * 64, target_id="target-1", candidates=(candidate,),
        outcome="succeeded", error_code=None, deadline_id="deadline-1",
    )


def _contract_page_result() -> PageExtractionResultV1:
    return PageExtractionResultV1.create(
        logical_page_id="page-plan-1", logical_effect_id="logical-page",
        attempt_no=1, source_locator_ref="sha256:" + "5" * 64,
        page_record=None, spans=(), bindings=(), outcome="timeout",
        error_code="deadline_expired", deadline_id="deadline-1",
        control_command_id=None,
    )


def test_v6_typed_read_contracts_have_strict_roundtrip_and_frozen_golden_bytes():
    search = _contract_search_result()
    page = _contract_page_result()
    attempt = PageAttemptOutcomeV1.create(
        logical_page_id="page-plan-1", ordinal=0,
        logical_effect_id="logical-page", attempt_no=1,
        canonical_effect_id="effect-1", result_ref="sha256:" + "6" * 64,
        status="committed", deadline_id="deadline-1", control_command_id=None,
    )
    assert OfficialSearchResultV1.from_json(search.to_json()) == search
    assert PageExtractionResultV1.from_json(page.to_json()) == page
    assert PageAttemptOutcomeV1.from_json(attempt.to_json()) == attempt
    digest = hashlib.sha256(
        (canonical_json(search.to_json()) + "\n" + canonical_json(page.to_json())
         + "\n" + canonical_json(attempt.to_json())).encode()
    ).hexdigest()
    assert digest == "b5cdde995437c3312365aa566cc1d89f936822674302af327206f67e07b58370"


@pytest.mark.parametrize(
    ("loader", "value"),
    [
        (OfficialSearchResultV1.from_json, _contract_search_result().to_json()),
        (PageExtractionResultV1.from_json, _contract_page_result().to_json()),
        (PageAttemptOutcomeV1.from_json, PageAttemptOutcomeV1.create(
            logical_page_id="page-plan-1", ordinal=0, logical_effect_id=None,
            attempt_no=None, canonical_effect_id=None, result_ref=None,
            status="not_started", deadline_id="deadline-1", control_command_id=None,
        ).to_json()),
    ],
)
def test_v6_typed_read_contracts_reject_unknown_keys_and_identity_tampering(loader, value):
    unknown = dict(value); unknown["extra"] = True
    with pytest.raises(ValueError):
        loader(unknown)
    tampered = dict(value)
    identity_key = next(key for key in ("result_id", "outcome_id") if key in tampered)
    tampered[identity_key] = "tampered"
    with pytest.raises(ValueError):
        loader(tampered)


@pytest.mark.asyncio
async def test_v6_search_commit_rejects_candidate_locator_authority_tampering(tmp_path):
    database, _, identity, deadlines, reads = await _runtime(tmp_path)
    route_id, policy_hash = "route_" + "9" * 24, "8" * 64
    await deadlines.prepare_route(
        identity=identity, route_id=route_id, policy_hash=policy_hash,
        budgets={"query": 1, "fetch": 0, "browser": 0, "llm": 0, "lane": 1},
    )
    root, _ = await deadlines.resume_root(
        identity=identity, route_id=route_id, policy_hash=policy_hash, budget_ms=120_000
    )
    url = "https://example.test/tamper"
    digest = hashlib.sha256(url.encode()).hexdigest()
    locator = SourceLocatorV1.create(
        canonical_url=url, final_url=url, canonical_url_hash=digest,
        final_url_hash=digest, authority_id="authority",
        verification_status="unverified", verification_policy_hash=policy_hash,
        redirect_chain_hashes=(),
    )
    locator_blob = await reads.blobs.put(
        canonical_json(locator.to_json()).encode(), identity,
        media_type="application/vnd.deskpet.source-locator.v1+json",
    )
    candidate = SearchCandidateV1.create(
        ordinal=0, source_locator_ref=f"sha256:{locator_blob.sha256}",
        title_hash="1" * 64, snippet_hash="2" * 64,
        authority_match="verified", reason_codes=("tampered",),
    )

    async def transport(attempt):
        return OfficialSearchResultV1.create(
            logical_effect_id=attempt.logical_effect_id, attempt_no=attempt.attempt_no,
            request_id="tamper", query_hash="3" * 64, target_id="target",
            candidates=(candidate,), outcome="succeeded", error_code=None,
            deadline_id=attempt.deadline_id,
        )

    with pytest.raises(EffectStateConflict, match="authority"):
        await reads.execute(
            operation_kind="official_search", route_id=route_id,
            target_or_page_id="target", ordinal=0, resource_kind="query",
            resource_hard_limit=1, resource_policy_hash=policy_hash,
            deadline=root, identity=identity, transport=transport,
        )
from tests.test_deep_research_v6_evidence_runtime import (
    FakeFetchService,
    FakeGateway,
    NO_SEED_SOURCE_RESOLVER,
    QUESTION,
    _candidate,
    _document,
)


async def _runtime(tmp_path):
    database = tmp_path / "workflow.db"
    store = WorkflowRunStore(database)
    run_id, _ = await store.create_run(
        request_key="v6-read",
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name="deep_research",
        workflow_version="v6",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="capability",
        capability_snapshot={},
        state_schema_version=6,
    )
    fence = await store.claim(run_id, "runner-v6-read")
    identity = NodeExecutionIdentity(
        workflow_name="deep_research",
        workflow_version="v6",
        thread_id=run_id,
        run_id=run_id,
        checkpoint_id="checkpoint-load-pages",
        checkpoint_ns="",
        task_id="task-load-pages",
        node_id="load_pages",
        attempt=1,
    )
    journal = EffectJournal(database)
    blobs = RegisteredBlobStore(tmp_path / "blobs", database)

    async def resolve(requested):
        assert requested == identity
        return BoundResearchEffectContext(
            identity,
            EffectExecutionContext(
                journal=journal,
                fence=fence,
                node_execution_id="node-load-pages",
                workflow_name="deep_research",
                workflow_version="v6",
                node_id="load_pages",
            ),
        )

    deadlines = DurableV6DeadlinePort(
        journal=journal, resolve_effect_context=resolve
    )
    reads = DurableV6PageReadEffectAdapter(
        journal=journal,
        blobs=blobs,
        resolve_effect_context=resolve,
        deadline_port=deadlines,
    )
    return database, fence, identity, deadlines, reads


@pytest.mark.asyncio
async def test_v6_search_and_page_use_frozen_per_operation_identity_deadlines_and_budget(tmp_path):
    database, _, identity, deadlines, reads = await _runtime(tmp_path)
    route_id = "route_" + "a" * 24
    policy_hash = "b" * 64
    budgets = {"query": 1, "fetch": 1, "browser": 0, "llm": 0, "lane": 1}
    await deadlines.prepare_route(
        identity=identity,
        route_id=route_id,
        policy_hash=policy_hash,
        budgets=budgets,
    )
    retrieval, _ = await deadlines.resume_root(
        identity=identity,
        route_id=route_id,
        policy_hash=policy_hash,
        budget_ms=120_000,
    )
    calls = {"search": 0, "page": 0}

    url = "https://www.stats.gov.cn/search-result"
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    locator = SourceLocatorV1.create(
        canonical_url=url, final_url=url,
        canonical_url_hash=url_hash, final_url_hash=url_hash,
        authority_id="cn-nbs", verification_status="unverified",
        verification_policy_hash=policy_hash, redirect_chain_hashes=(),
    )
    locator_blob = await reads.blobs.put(
        canonical_json(locator.to_json()).encode(), identity,
        media_type="application/vnd.deskpet.source-locator.v1+json",
    )
    candidate = SearchCandidateV1.create(
        ordinal=0, source_locator_ref=f"sha256:{locator_blob.sha256}",
        title_hash=hashlib.sha256(b"title").hexdigest(),
        snippet_hash=hashlib.sha256(b"snippet").hexdigest(),
        authority_match="unverified", reason_codes=("candidate_unverified",),
    )

    async def search_transport(attempt):
        calls["search"] += 1
        return OfficialSearchResultV1.create(
            logical_effect_id=attempt.logical_effect_id, attempt_no=attempt.attempt_no,
            request_id="request-0", query_hash=hashlib.sha256(b"query").hexdigest(),
            target_id="target-cn-nbs", candidates=(candidate,), outcome="succeeded",
            error_code=None, deadline_id=attempt.deadline_id,
        )

    first = await reads.execute(
        operation_kind="official_search",
        route_id=route_id,
        target_or_page_id="target-cn-nbs",
        ordinal=0,
        resource_kind="query",
        resource_hard_limit=1,
        resource_policy_hash=policy_hash,
        deadline=retrieval,
        identity=identity,
        transport=search_transport,
    )
    replay = await reads.execute(
        operation_kind="official_search",
        route_id=route_id,
        target_or_page_id="target-cn-nbs",
        ordinal=0,
        resource_kind="query",
        resource_hard_limit=1,
        resource_policy_hash=policy_hash,
        deadline=retrieval,
        identity=identity,
        transport=search_transport,
    )
    assert first == replay
    assert calls["search"] == 1

    child, _ = await deadlines.resume_child(
        identity=identity,
        parent=retrieval,
        logical_key="logical-page-0",
        logical_scope="deep_research_v6.page_fetch",
        budget_ms=20_000,
        policy_hash=policy_hash,
    )

    final_locator = SourceLocatorV1.create(
        canonical_url=url, final_url=url,
        canonical_url_hash=url_hash, final_url_hash=url_hash,
        authority_id="cn-nbs", verification_status="verified",
        verification_policy_hash=policy_hash, redirect_chain_hashes=(),
    )
    final_locator_blob = await reads.blobs.put(
        canonical_json(final_locator.to_json()).encode(), identity,
        media_type="application/vnd.deskpet.source-locator.v1+json",
    )
    body = b"typed page body"
    body_blob = await reads.blobs.put(body, identity, media_type="text/plain")
    page_id = "page_" + hashlib.sha256((url_hash + body_blob.sha256).encode("ascii")).hexdigest()[:24]

    async def page_transport(attempt):
        calls["page"] += 1
        return PageExtractionResultV1.create(
            logical_page_id="logical-page-0",
            logical_effect_id=attempt.logical_effect_id, attempt_no=attempt.attempt_no,
            source_locator_ref=f"sha256:{final_locator_blob.sha256}",
            page_record={
                "schema_version": 1, "page_id": page_id,
                "source_locator_ref": f"sha256:{final_locator_blob.sha256}",
                "canonical_url_hash": url_hash, "final_url_hash": url_hash,
                "authority_id": "cn-nbs", "source_family_id": "cn-nbs",
                "source_tier": "first_party", "body_ref": f"sha256:{body_blob.sha256}",
                "body_hash": body_blob.sha256, "fetched_at": "2026-07-18T00:00:00Z",
                "media_type": "text/plain", "admission_status": "admitted",
                "reason_codes": ["official_domain_match"],
            },
            spans=(), bindings=(), outcome="succeeded", error_code=None,
            deadline_id=attempt.deadline_id, control_command_id=None,
        )

    await reads.execute(
        operation_kind="page_fetch",
        route_id=route_id,
        target_or_page_id="logical-page-0",
        ordinal=0,
        resource_kind="fetch",
        resource_hard_limit=1,
        resource_policy_hash=policy_hash,
        deadline=child,
        identity=identity,
        transport=page_transport,
        input_source_locator_ref=f"sha256:{locator_blob.sha256}",
    )
    assert calls["page"] == 1

    search_logical = v6_read_logical_effect_id(
        run_id=identity.run_id,
        route_id=route_id,
        operation_kind="official_search",
        target_or_page_id="target-cn-nbs",
        ordinal=0,
    )
    page_logical = v6_read_logical_effect_id(
        run_id=identity.run_id,
        route_id=route_id,
        operation_kind="page_fetch",
        target_or_page_id="logical-page-0",
        ordinal=0,
    )
    async with aiosqlite.connect(database) as db:
        effects = await (await db.execute(
            """SELECT logical_effect_id,effect_id,effect_fingerprint,attempt_no,status,
            artifact_refs_json,outcome_json
            FROM workflow_effects ORDER BY logical_effect_id"""
        )).fetchall()
        deadline_rows = await (await db.execute(
            "SELECT logical_scope,budget_ms,parent_deadline_id FROM workflow_research_deadlines"
        )).fetchall()
        budget_rows = await (await db.execute(
            """SELECT resource_kind,hard_limit,reserved,consumed
            FROM workflow_research_resource_budgets ORDER BY resource_kind"""
        )).fetchall()
    assert {row[0] for row in effects} == {search_logical, page_logical}
    closure_sizes = set()
    canonical_refs = {}
    for logical, effect_id, fingerprint, attempt_no, status, artifact_json, outcome_json in effects:
        assert effect_id == hashlib.sha256(
            f"{identity.run_id}|{logical}|1".encode()
        ).hexdigest()
        assert fingerprint == hashlib.sha256(f"{logical}|1".encode()).hexdigest()
        assert (attempt_no, status) == (1, "committed")
        artifact_digests = json.loads(artifact_json)
        outcome = json.loads(outcome_json)
        assert artifact_digests == sorted(
            ref.removeprefix("sha256:") for ref in outcome["dependency_refs"]
        )
        assert outcome["canonical_result_ref"] in outcome["dependency_refs"]
        closure_sizes.add((outcome["result_kind"], len(artifact_digests)))
        canonical_refs[outcome["result_kind"]] = outcome["canonical_result_ref"]
    assert closure_sizes == {("official_search", 2), ("page_extraction", 4)}
    assert ("run:automatic", 900_000, None) in deadline_rows
    assert any(scope == "deep_research_v6.retrieval" and budget == 120_000 for scope, budget, _ in deadline_rows)
    assert any(scope == "deep_research_v6.page_fetch" and budget <= 20_000 for scope, budget, _ in deadline_rows)
    assert budget_rows == [
        ("browser", 0, 0, 0),
        ("fetch", 1, 0, 1),
        ("lane", 1, 0, 0),
        ("llm", 0, 0, 0),
        ("query", 1, 0, 1),
    ]
    for result_kind, result_ref in canonical_refs.items():
        await reads.journal.assert_canonical_v6_read_result_owner(
            run_id=identity.run_id,
            canonical_result_ref=result_ref,
            result_kind=result_kind,
        )
    wrapper = await reads.blobs.put(
        canonical_json({"schema_version": 1, "wrapper": True}).encode(),
        identity,
        media_type="application/json",
    )
    with pytest.raises(StagingPreconditionFailed):
        await reads.journal.assert_canonical_v6_read_result_owner(
            run_id=identity.run_id,
            canonical_result_ref=f"sha256:{wrapper.sha256}",
            result_kind="page_extraction",
        )


@pytest.mark.asyncio
async def test_v6_route_query_budget_denies_a_second_logical_search(tmp_path):
    _, _, identity, deadlines, reads = await _runtime(tmp_path)
    route_id = "route_" + "c" * 24
    policy_hash = "d" * 64
    await deadlines.prepare_route(
        identity=identity,
        route_id=route_id,
        policy_hash=policy_hash,
        budgets={"query": 1, "fetch": 0, "browser": 0, "llm": 0, "lane": 1},
    )
    retrieval, _ = await deadlines.resume_root(
        identity=identity, route_id=route_id, policy_hash=policy_hash, budget_ms=120_000
    )

    async def transport(attempt):
        return OfficialSearchResultV1.create(
            logical_effect_id=attempt.logical_effect_id, attempt_no=attempt.attempt_no,
            request_id="empty", query_hash=hashlib.sha256(b"empty").hexdigest(),
            target_id="target-0", candidates=(), outcome="empty", error_code=None,
            deadline_id=attempt.deadline_id,
        )

    await reads.execute(
        operation_kind="official_search", route_id=route_id,
        target_or_page_id="target-0", ordinal=0, resource_kind="query",
        resource_hard_limit=1, resource_policy_hash=policy_hash,
        deadline=retrieval, identity=identity, transport=transport,
    )
    with pytest.raises(BudgetReservationExceeded):
        await reads.execute(
            operation_kind="official_search", route_id=route_id,
            target_or_page_id="target-1", ordinal=1, resource_kind="query",
            resource_hard_limit=1, resource_policy_hash=policy_hash,
            deadline=retrieval, identity=identity, transport=transport,
        )


@pytest.mark.asyncio
async def test_v6_automatic_deadline_wall_anchors_survive_retry_resume_and_restart(
    tmp_path,
):
    _, _, identity, deadlines, reads = await _runtime(tmp_path)
    now = [datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)]
    monotonic = [1_000_000_000]
    deadlines.wall_clock = lambda: now[0]
    deadlines.monotonic_ns = lambda: monotonic[0]

    created, _ = await deadlines.observe_automatic(identity=identity)
    now[0] += timedelta(seconds=5)
    monotonic[0] += 5_000_000_000
    retried, _ = await deadlines.observe_automatic(identity=identity)

    restarted = DurableV6DeadlinePort(
        journal=reads.journal,
        resolve_effect_context=deadlines.resolve_effect_context,
        wall_clock=lambda: now[0],
        monotonic_ns=lambda: monotonic[0],
    )
    now[0] += timedelta(seconds=7)
    monotonic[0] += 7_000_000_000
    resumed, _ = await restarted.observe_automatic(identity=identity)

    assert {created.deadline_id, retried.deadline_id, resumed.deadline_id} == {
        created.deadline_id
    }
    assert retried.created_at == resumed.created_at == created.created_at
    assert retried.wall_not_after == resumed.wall_not_after == created.wall_not_after
    assert resumed.remaining_ms == 888_000
    assert created.revision < retried.revision < resumed.revision


@pytest.mark.asyncio
async def test_v6_different_logical_reads_contend_atomically_for_last_resource_slot(
    tmp_path,
):
    database, _, identity, deadlines, reads = await _runtime(tmp_path)
    route_id = "route_" + "e" * 24
    policy_hash = "f" * 64
    await deadlines.prepare_route(
        identity=identity,
        route_id=route_id,
        policy_hash=policy_hash,
        budgets={"query": 1, "fetch": 0, "browser": 0, "llm": 0, "lane": 2},
    )
    root, _ = await deadlines.resume_root(
        identity=identity,
        route_id=route_id,
        policy_hash=policy_hash,
        budget_ms=120_000,
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holding_transport(attempt):
        entered.set()
        await release.wait()
        return OfficialSearchResultV1.create(
            logical_effect_id=attempt.logical_effect_id,
            attempt_no=attempt.attempt_no,
            request_id="winner",
            query_hash=hashlib.sha256(b"winner").hexdigest(),
            target_id="target-winner",
            candidates=(),
            outcome="empty",
            error_code=None,
            deadline_id=attempt.deadline_id,
        )

    winner = asyncio.create_task(
        reads.execute(
            operation_kind="official_search",
            route_id=route_id,
            target_or_page_id="target-winner",
            ordinal=0,
            resource_kind="query",
            resource_hard_limit=1,
            resource_policy_hash=policy_hash,
            deadline=root,
            identity=identity,
            transport=holding_transport,
        )
    )
    await entered.wait()
    loser = asyncio.create_task(
        reads.execute(
            operation_kind="official_search",
            route_id=route_id,
            target_or_page_id="target-loser",
            ordinal=1,
            resource_kind="query",
            resource_hard_limit=1,
            resource_policy_hash=policy_hash,
            deadline=root,
            identity=identity,
            transport=lambda _attempt: pytest.fail("budget loser reached upstream"),
        )
    )
    with pytest.raises(BudgetReservationExceeded):
        await loser
    release.set()
    assert (await winner)["outcome"] == "empty"

    async with aiosqlite.connect(database) as db:
        query = await (
            await db.execute(
                """SELECT hard_limit,reserved,consumed
                FROM workflow_research_resource_budgets
                WHERE run_id=? AND resource_kind='query'""",
                (identity.run_id,),
            )
        ).fetchone()
    assert tuple(query) == (1, 0, 1)


@pytest.mark.asyncio
async def test_evidence_runtime_journals_each_search_and_page_without_aggregate_load_effect(
    tmp_path,
):
    database, _, identity, deadlines, reads = await _runtime(tmp_path)
    url = "https://www.stats.gov.cn/v6-durable.html"
    fetch = FakeFetchService({url: _document(url, body="durable official body")})
    gateway = FakeGateway((_candidate(url),), fetch)
    runtime = DeepResearchV6EvidenceRuntime(
        gateway,
        blobs=reads.blobs,
        source_resolver=NO_SEED_SOURCE_RESOLVER,
        durable_reads=reads,
    )
    spec = compile_official_exact_fact(QUESTION)
    route = runtime.plan_route(spec, identity=identity)
    route_value = route.to_json()
    await deadlines.prepare_route(
        identity=identity,
        route_id=str(route_value["route_id"]),
        policy_hash=str(route_value["policy_hash"]),
        budgets=dict(route_value["budget"]),
    )

    result = await runtime.retrieve(spec, identity=identity, route_decision=route)
    assert result.pages, (
        result.search_count,
        result.fetch_count,
        result.timeout_count,
        result.rejected_count,
        result.failed_count,
        len(gateway.requests),
        len(fetch.requests),
    )

    async with aiosqlite.connect(database) as db:
        rows = await (await db.execute(
            """SELECT effect_type,logical_effect_id,attempt_no,status
            FROM workflow_effects ORDER BY effect_type,logical_effect_id"""
        )).fetchall()
    assert sum(row[0] == "deep_research_v6_official_search" for row in rows) == result.search_count
    assert sum(row[0] == "deep_research_v6_page_fetch" for row in rows) == result.fetch_count
    assert all(logical and attempt == 1 and status == "committed" for _, logical, attempt, status in rows)
    assert not any(effect_type in {"research_search", "research_static_fetch"} for effect_type, *_ in rows)


async def _takeover_reads(database, identity, blobs):
    async with aiosqlite.connect(database) as db:
        await db.execute(
            "UPDATE workflow_runs SET lease_expires_at=0 WHERE run_id=?",
            (identity.run_id,),
        )
        await db.commit()
    store = WorkflowRunStore(database)
    fence = await store.claim(identity.run_id, "runner-v6-read-takeover")
    journal = EffectJournal(database)

    async def resolve(requested):
        assert requested == identity
        return BoundResearchEffectContext(
            identity,
            EffectExecutionContext(
                journal=journal, fence=fence,
                node_execution_id="node-load-pages-takeover",
                workflow_name="deep_research", workflow_version="v6",
                node_id="load_pages",
            ),
        )

    deadlines = DurableV6DeadlinePort(journal=journal, resolve_effect_context=resolve)
    return DurableV6PageReadEffectAdapter(
        journal=journal, blobs=blobs, resolve_effect_context=resolve,
        deadline_port=deadlines,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault_stage", "expected_calls"),
    [
        ("v6_read.after_result_blob_before_effect_commit", 2),
        ("v6_read.after_effect_commit", 1),
    ],
)
async def test_v6_read_fault_before_or_after_commit_has_deterministic_replay(
    tmp_path, fault_stage, expected_calls
):
    database, _, identity, deadlines, reads = await _runtime(tmp_path)
    route_id, policy_hash = "route_" + "7" * 24, "6" * 64
    await deadlines.prepare_route(
        identity=identity, route_id=route_id, policy_hash=policy_hash,
        budgets={"query": 2, "fetch": 0, "browser": 0, "llm": 0, "lane": 1},
    )
    root, _ = await deadlines.resume_root(
        identity=identity, route_id=route_id, policy_hash=policy_hash, budget_ms=120_000
    )
    url = "https://example.test/fault"
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    locator = SourceLocatorV1.create(
        canonical_url=url, final_url=url, canonical_url_hash=url_hash,
        final_url_hash=url_hash, authority_id="authority",
        verification_status="unverified", verification_policy_hash=policy_hash,
        redirect_chain_hashes=(),
    )
    locator_blob = await reads.blobs.put(
        canonical_json(locator.to_json()).encode(), identity,
        media_type="application/vnd.deskpet.source-locator.v1+json",
    )
    candidate = SearchCandidateV1.create(
        ordinal=0, source_locator_ref=f"sha256:{locator_blob.sha256}",
        title_hash="1" * 64, snippet_hash="2" * 64,
        authority_match="unverified", reason_codes=("candidate_unverified",),
    )
    calls = 0

    async def transport(attempt):
        nonlocal calls
        calls += 1
        return OfficialSearchResultV1.create(
            logical_effect_id=attempt.logical_effect_id, attempt_no=attempt.attempt_no,
            request_id="fault", query_hash="3" * 64, target_id="target",
            candidates=(candidate,), outcome="succeeded", error_code=None,
            deadline_id=attempt.deadline_id,
        )

    fired = False

    async def fault(stage):
        nonlocal fired
        if stage == fault_stage and not fired:
            fired = True
            raise asyncio.CancelledError

    reads.fault_injector = fault
    with pytest.raises(asyncio.CancelledError):
        await reads.execute(
            operation_kind="official_search", route_id=route_id,
            target_or_page_id="target", ordinal=0, resource_kind="query",
            resource_hard_limit=2, resource_policy_hash=policy_hash,
            deadline=root, identity=identity, transport=transport,
        )
    if fault_stage.endswith("before_effect_commit"):
        reads = await _takeover_reads(database, identity, reads.blobs)
    else:
        reads.fault_injector = None
    replay = await reads.execute(
        operation_kind="official_search", route_id=route_id,
        target_or_page_id="target", ordinal=0, resource_kind="query",
        resource_hard_limit=2, resource_policy_hash=policy_hash,
        deadline=root, identity=identity, transport=transport,
    )
    assert OfficialSearchResultV1.from_json(replay).attempt_no == expected_calls
    assert calls == expected_calls


@pytest.mark.asyncio
async def test_v6_stale_attempt_reconciles_once_and_late_result_cannot_take_head(tmp_path):
    database, _, identity, deadlines, reads = await _runtime(tmp_path)
    route_id, policy_hash = "route_" + "5" * 24, "4" * 64
    await deadlines.prepare_route(
        identity=identity, route_id=route_id, policy_hash=policy_hash,
        budgets={"query": 2, "fetch": 0, "browser": 0, "llm": 0, "lane": 1},
    )
    root, _ = await deadlines.resume_root(
        identity=identity, route_id=route_id, policy_hash=policy_hash, budget_ms=120_000
    )
    url = "https://example.test/late"
    url_hash = hashlib.sha256(url.encode()).hexdigest()
    locator = SourceLocatorV1.create(
        canonical_url=url, final_url=url, canonical_url_hash=url_hash,
        final_url_hash=url_hash, authority_id="authority",
        verification_status="unverified", verification_policy_hash=policy_hash,
        redirect_chain_hashes=(),
    )
    locator_blob = await reads.blobs.put(
        canonical_json(locator.to_json()).encode(), identity,
        media_type="application/vnd.deskpet.source-locator.v1+json",
    )
    candidate = SearchCandidateV1.create(
        ordinal=0, source_locator_ref=f"sha256:{locator_blob.sha256}",
        title_hash="1" * 64, snippet_hash="2" * 64,
        authority_match="unverified", reason_codes=("candidate_unverified",),
    )
    first_started, release_first = asyncio.Event(), asyncio.Event()
    calls: list[int] = []

    async def transport(attempt):
        calls.append(attempt.attempt_no)
        if attempt.attempt_no == 1:
            first_started.set()
            await release_first.wait()
        return OfficialSearchResultV1.create(
            logical_effect_id=attempt.logical_effect_id, attempt_no=attempt.attempt_no,
            request_id="late", query_hash="3" * 64, target_id="target",
            candidates=(candidate,), outcome="succeeded", error_code=None,
            deadline_id=attempt.deadline_id,
        )

    first_task = asyncio.create_task(reads.execute(
        operation_kind="official_search", route_id=route_id,
        target_or_page_id="target", ordinal=0, resource_kind="query",
        resource_hard_limit=2, resource_policy_hash=policy_hash,
        deadline=root, identity=identity, transport=transport,
    ))
    await first_started.wait()
    takeover = await _takeover_reads(database, identity, reads.blobs)
    second = await takeover.execute(
        operation_kind="official_search", route_id=route_id,
        target_or_page_id="target", ordinal=0, resource_kind="query",
        resource_hard_limit=2, resource_policy_hash=policy_hash,
        deadline=root, identity=identity, transport=transport,
    )
    assert OfficialSearchResultV1.from_json(second).attempt_no == 2
    release_first.set()
    with pytest.raises(EffectStateConflict):
        await first_task
    replay = await takeover.execute(
        operation_kind="official_search", route_id=route_id,
        target_or_page_id="target", ordinal=0, resource_kind="query",
        resource_hard_limit=2, resource_policy_hash=policy_hash,
        deadline=root, identity=identity, transport=transport,
    )
    assert replay == second
    assert calls == [1, 2]
    async with aiosqlite.connect(database) as db:
        attempts = await (await db.execute(
            "SELECT attempt_no,status,supersedes_effect_id FROM workflow_effects ORDER BY attempt_no"
        )).fetchall()
        head = await (await db.execute(
            "SELECT latest_attempt_no,canonical_effect_id FROM workflow_effect_attempt_heads"
        )).fetchone()
        budget = await (await db.execute(
            "SELECT reserved,consumed FROM workflow_research_resource_budgets WHERE resource_kind='query'"
        )).fetchone()
    assert [(row[0], row[1]) for row in attempts] == [(1, "failed"), (2, "committed")]
    assert attempts[1][2] is not None
    assert head == (2, hashlib.sha256(f"{identity.run_id}|{v6_read_logical_effect_id(run_id=identity.run_id, route_id=route_id, operation_kind='official_search', target_or_page_id='target', ordinal=0)}|2".encode()).hexdigest())
    assert budget == (0, 2)
