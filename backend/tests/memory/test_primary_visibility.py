"""Host S1 lineage and public Memory history batches; no SDK private storage."""

import sqlite3
from dataclasses import replace

import aiosqlite
import pytest
import simple_harness as h
import simple_harness_memory as m
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.primary_visibility import (
    PrimaryHistoryPolicy,
    read_evidence_pair,
)
from tests.memory.test_primary_read_api import (
    AUTH,
    error,
    result,
    settled,
    setup,
)

FILTERS = frozenset(
    {"host-public-turn/v1", "host-typed-ingress/v1", "host-primary-runtime-v1"}
)


def classification_policy():
    return m.InformationClassificationPolicy(
        "host-history-policy",
        "1",
        "host:classification/v1",
        h.PrivacyClass.PERSONAL,
        (),
    )


def disclosure(subject=AUTH.subject):
    return h.DisclosureContext(
        "actual-history-request",
        subject,
        h.DeliveryRecipient.USER_SELF,
        subject,
        h.IntendedAudience.USER_SELF,
        h.DisclosurePurpose.USER_REVIEW,
        h.DisclosureSource.AUTHENTICATED_HOST,
        h.DisclosureTrust.TRUSTED_AUTHORITY,
        h.DisclosureGeneration.CURRENT,
        AUTH.authority_ref,
        (h.DisclosureReasonCode.MINIMUM_NECESSARY,),
    )


async def pairs(f):
    async with aiosqlite.connect(f.path) as db:
        db.row_factory = aiosqlite.Row
        ids = await db.execute_fetchall(
            "SELECT evidence_id FROM foreground_turns ORDER BY rowid"
        )
        return [
            await read_evidence_pair(
                db=db, subject=AUTH.subject, primary_ref=f.primary, evidence_id=row[0]
            )
            for row in ids
        ]


def proof(*envelopes, recall=()):
    return {
        "schema_version": 1,
        "evidence": [
            {"evidence_id": e.evidence_id, "envelope_hash": e.envelope_hash}
            for e in envelopes
        ],
        "recall": list(recall),
    }


async def check_proof(f, dependencies, checker=None, primary=None):
    async with aiosqlite.connect(f.path) as db:
        db.row_factory = aiosqlite.Row
        return await PrimaryHistoryPolicy(
            f.path, AUTH.subject, checker or f.policy.history
        ).check_dependencies(
            db=db,
            primary_ref=primary or f.primary,
            dependencies=dependencies,
            disclosure_context=disclosure(),
        )


@pytest.mark.asyncio
async def test_current_run_proof_one_union_batch_without_fake_terminal(tmp_path):
    f = await setup(tmp_path)
    for i in range(2):
        result(await f.send("queue.enqueue", {"text": f"source {i}"}, key=f"t{i}"))
    admitted = await pairs(f)
    recall = {
        "result_id": "real-result",
        "result_hash": "a" * 64,
        "item_id": "real-item",
        "item_hash": "b" * 64,
    }
    dependencies = proof(*(e for e, _ in admitted), recall=(recall,))
    assert await check_proof(f, dependencies)
    assert len(f.policy.history_calls) == 1
    _, ctx, bindings = f.policy.history_calls[0]
    assert ctx == disclosure()
    assert len(bindings) == 3
    assert [b.to_json() for b in bindings if isinstance(b, m.HistoryRecallBinding)] == [
        {"kind": "recall", **recall}
    ]
    f.policy.denied.add("real-item")
    assert not await check_proof(f, dependencies)
    assert (
        len(f.policy.history_calls) == 2
    )  # unchanged snapshot metadata cannot cache ALLOW
    with sqlite3.connect(f.path) as db:
        assert (
            db.execute("SELECT count(*) FROM human_memory_evidence").fetchone()[0] == 2
        )
        assert db.execute("SELECT count(*) FROM foreground_runs").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        "hash",
        "missing",
        "foreign-primary",
        "schema-bool",
        "unknown-short",
        "empty",
        "too-many",
    ],
)
async def test_dependency_negative_controls(tmp_path, bad):
    f = await setup(tmp_path)
    result(await f.send("queue.enqueue", {"text": "original"}))
    env, _ = (await pairs(f))[0]
    body = proof(env)
    primary = None
    if bad == "hash":
        body["evidence"][0]["envelope_hash"] = "0" * 64
    if bad == "missing":
        body["evidence"][0]["evidence_id"] = "not-admitted"
    if bad == "foreign-primary":
        primary = "other-primary"
    if bad == "schema-bool":
        body["schema_version"] = True
    if bad == "unknown-short":
        body["short_horizon"] = [{"content": "unproved"}]
    if bad == "empty":
        body["evidence"] = []
    if bad == "too-many":
        body["evidence"] *= 257
    assert not await check_proof(f, body, primary=primary)
    assert f.policy.history_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["wrong-subject", "wrong-order", "non-bool", "untyped", "throws"]
)
async def test_malformed_sdk_result_never_discloses(tmp_path, change):
    f = await setup(tmp_path)
    for i in range(2):
        result(await f.send("queue.enqueue", {"text": str(i)}, key=f"t{i}"))
    envs = [e for e, _ in await pairs(f)]

    async def checker(**kwargs):
        snap = await f.policy.history(**kwargs)
        if change == "wrong-subject":
            return replace(snap, subject="other")
        if change == "wrong-order":
            return replace(snap, items=tuple(reversed(snap.items)))
        if change == "non-bool":
            return replace(
                snap, items=(replace(snap.items[0], visible=1), *snap.items[1:])
            )
        if change == "untyped":
            return snap.to_json()
        raise RuntimeError("policy unavailable")

    assert not await check_proof(f, proof(*envs), checker)


@pytest.mark.asyncio
async def test_legacy_terminal_preserves_original_user_without_reader(tmp_path):
    f = await setup(
        tmp_path,
        reader=lambda *a, **kw: pytest.fail("legacy group has no dependency proof"),
    )
    await settled(f, dependencies=False)
    page = result(await f.send("primary.messages.page", {"primary_ref": f.primary}))
    assert [(i["role"], i["text"]) for i in page["items"]] == [
        ("user", "real admitted source")
    ]
    assert len(f.policy.history_calls) == 1
    assert len(f.policy.history_calls[0][2]) == 1


@pytest.mark.asyncio
async def test_bound_request_disclosure_is_derived_from_auth_not_wire(tmp_path):
    f = await setup(tmp_path)
    result(await f.send("queue.enqueue", {"text": "plain USER"}, key="input"))
    result(
        await f.send(
            "primary.messages.page", {"primary_ref": f.primary}, key="read-exact-id"
        )
    )
    subject, context, _ = f.policy.history_calls[-1]
    assert subject == AUTH.subject and context.subject == AUTH.subject
    assert (
        context.run_id == "read-exact-id"
        and context.authority_ref == AUTH.authority_ref
    )
    assert context.purpose is h.DisclosurePurpose.USER_REVIEW
    assert context.recipient_id == AUTH.subject
    error(
        await f.send(
            "primary.messages.page",
            {"primary_ref": f.primary, "disclosure_context": context.to_json()},
        ),
        "human_memory_public_authority_field_rejected",
    )
    for bad in (5, True, "", "\x00bad"):
        error(await f.send("primary.state", key=bad), "human_memory_invalid_request")


async def materialize(manager, principal, envelope, receipt):
    from deskpet.memory.analysis_proposal import admitted_item, derive_span

    item = admitted_item(envelope, receipt)
    span = derive_span(item, item.text, span_id="actual-user-span")
    operation = h.MemoryMutationOperation(
        operation_id="create",
        kind=h.MemoryMutationKind.CREATE,
        memory_type=h.LongTermMemoryType.SEMANTIC,
        payload=h.SemanticMemoryPayload(
            "user:self", "response_style", "concise", ("default",)
        ),
        target=None,
        depends_on_operation_ids=(),
        lifecycle_state=h.SemanticLifecycleState.ACTIVE,
        epistemic_status=h.EpistemicStatus.EXPLICIT_USER,
        conflict_status=h.ConflictStatus.UNCONTESTED,
        verification_state=h.VerificationState.SOURCE_BOUND,
        valid_time_interval=h.ValidTimeInterval(None, None),
        proposed_privacy_class=h.PrivacyClass.PERSONAL,
        proposed_information_attributes=(h.InformationAttribute.PREFERENCE,),
        evidence_spans=(span,),
        reason_code="explicit_user_assertion",
    )
    plan = h.MemoryMutationPlan(
        "history-memory",
        envelope.run_id,
        "actual-user-turn",
        AUTH.subject,
        1,
        h.MemoryMutationPlanOutcome.MUTATE,
        (operation,),
        envelope.disclosure_context,
        (h.EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),),
        "history-memory-idempotency",
    )
    applied = await manager.apply_memory_mutation_plan(
        principal=principal,
        scope=m.MemoryScope.personal(AUTH.subject),
        plan=plan,
    )
    assert applied.outcome is h.MemoryMutationApplyOutcome.COMMITTED
    view = await manager.get_memory_mutation_receipt_view(
        principal=principal, receipt_ref=applied.receipt_ref
    )
    return view.operations[0].memory_id


@pytest.mark.asyncio
async def test_real_memory_only_forget_cold_user_and_terminal_no_host_rewrite(tmp_path):
    facts = {}
    calls = []

    async def checker(*, subject, disclosure_context, bindings):
        assert subject == principal.actor_id
        calls.append(bindings)
        return await manager.check_history_visibility(
            principal=principal,
            disclosure_context=disclosure_context,
            bindings=bindings,
        )

    f = await setup(
        tmp_path / "host",
        history_checker=checker,
        reader=lambda run_id, *, current_text: (
            facts[run_id],
            (
                {"role": "user", "content": current_text},
                {"role": "assistant", "content": "concise answer"},
            ),
        ),
    )
    _, terminal = await settled(f, text="Please keep replies concise")
    facts[terminal.run_id] = terminal
    from deskpet.execution.primary_history import observation_id

    async with aiosqlite.connect(f.path) as db:
        db.row_factory = aiosqlite.Row
        previous, _ = await read_evidence_pair(
            db=db,
            subject=AUTH.subject,
            primary_ref=f.primary,
            evidence_id=observation_id(terminal.run_id),
        )
    _, second_terminal = await settled(
        f,
        key="inheriting",
        text="Second independent USER",
        inherited=(
            {
                "evidence_id": previous.evidence_id,
                "envelope_hash": previous.envelope_hash,
            },
        ),
    )
    facts[second_terminal.run_id] = second_terminal
    result(await f.send("queue.enqueue", {"text": "unrelated source"}, key="unrelated"))
    env, receipt = (await pairs(f))[0]
    principal = m.MemoryPrincipal("host", "household", AUTH.subject, "ui")
    kwargs = {
        "classification_policy": classification_policy(),
        "supported_filter_policies": FILTERS,
        "evidence_authority": HostEvidenceAuthority(f.path),
        "clock": lambda: 200.0,
    }
    manager = await m.build_human_memory_v7(tmp_path / "memory.db", **kwargs)

    async def page():
        return result(await f.send("primary.messages.page", {"primary_ref": f.primary}))

    def archive():
        with sqlite3.connect(f.path) as db:
            return db.execute(
                "SELECT envelope_json,receipt_json FROM human_memory_evidence JOIN human_memory_sanitization_receipts USING(receipt_id) ORDER BY human_memory_evidence.evidence_id"
            ).fetchall()

    original = archive()
    try:
        cold = await page()
        assert [i["role"] for i in cold["items"]] == [
            "user",
            "assistant",
            "user",
            "assistant",
            "user",
        ]
        assert len(calls) == 1 and len(calls[0]) == 5
        oldref = cold["items"][0]["message_ref"]
        await manager.ingest_committed_evidence(env, receipt)
        assert (await page())["items"] == cold["items"]  # no analysis required for USER
        memory_id = await materialize(manager, principal, env, receipt)
        assert memory_id
        assert (await page())["items"] == cold["items"]
        recall = await real_recall(manager, principal, env)
        second_user = (await pairs(f))[1][0]
        current_proof = proof(second_user, recall=(recall,))
        assert await check_proof(f, current_proof, checker)
        # A payload hash cannot substitute for the actual result-item commitment.
        assert not await check_proof(
            f, proof(second_user, recall=({**recall, "item_hash": "0" * 64},)), checker
        )
        await manager.suppress(
            principal=principal,
            request=m.SuppressionRequest(
                "forget-memory-only",
                AUTH.subject,
                m.SuppressionScopeKind.MEMORY,
                memory_id,
                "user_forget",
                200.0,
            ),
        )
        assert not await check_proof(f, current_proof, checker)
        forgotten = await page()
        assert [i["text"] for i in forgotten["items"]] == [
            "Second independent USER",
            "unrelated source",
        ]
        assert forgotten["revision"] == cold["revision"]
        error(
            await f.send(
                "primary.messages.detail",
                {"primary_ref": f.primary, "message_ref": oldref},
            ),
            "primary_message_unavailable",
        )
        await manager.close()
        manager = await m.build_human_memory_v7(tmp_path / "memory.db", **kwargs)
        assert [i["text"] for i in (await page())["items"]] == [
            "Second independent USER",
            "unrelated source",
        ]
        assert archive() == original
    finally:
        await manager.close()


async def real_recall(manager, principal, envelope):
    context = h.RecallContext(
        "actual-recall-run",
        AUTH.subject,
        "actual-recall-turn",
        1,
        1000.0,
        "concise",
        None,
        (h.LongTermMemoryType.SEMANTIC,),
        False,
        (h.RecallSelectorDomain.MEMORY_TYPE,),
        (h.RecallRetrievalMode.FULL_TEXT,),
        (),
        (),
        None,
        None,
        (),
        (),
        (),
        (),
        replace(
            disclosure(),
            run_id="actual-recall-run",
            purpose=h.DisclosurePurpose.PERSONALIZATION,
        ),
        (h.EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),),
        h.RecallBudget(8, 16384, 2048, 1000),
    )
    plan = h.RecallPlan(
        "actual-recall-plan",
        context.run_id,
        context.subject,
        context.context_hash,
        context.context_revision,
        context.query,
        context.available_memory_types,
        context.short_horizon_allowed,
        context.allowed_selector_domains,
        context.allowed_retrieval_modes,
        (),
        (),
        None,
        None,
        (),
        (),
        (),
        context.disclosure_context,
        context.evidence_refs,
        context.budget,
        "actual-recall-key",
        (h.RecallReasonCode.USER_FACT_DEPENDENCY,),
    )
    execution = await manager.execute_typed_recall(
        principal=principal, context=context, plan=plan
    )
    assert len(execution.result.items) == 1
    item = execution.result.items[0]
    return {
        "result_id": execution.result.result_id,
        "result_hash": execution.result.result_hash,
        "item_id": item.selected_item.item_id,
        "item_hash": item.result_item_hash,
    }
