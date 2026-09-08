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


async def materialize(manager, principal, envelope, receipt, *, semantic_value="concise"):
    from deskpet.memory.analysis_proposal import admitted_item, derive_span

    item = admitted_item(envelope, receipt)
    span = derive_span(item, item.text, span_id="actual-user-span")
    operation = h.MemoryMutationOperation(
        operation_id="create",
        kind=h.MemoryMutationKind.CREATE,
        memory_type=h.LongTermMemoryType.SEMANTIC,
        payload=h.SemanticMemoryPayload(
            "user:self", "response_style", semantic_value, ("default",)
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
        principal.actor_id,
        1,
        h.MemoryMutationPlanOutcome.MUTATE,
        (operation,),
        envelope.disclosure_context,
        (h.EvidenceRef(envelope.evidence_id, envelope.envelope_hash, 1),),
        "history-memory-idempotency",
    )
    applied = await manager.apply_memory_mutation_plan(
        principal=principal,
        scope=m.MemoryScope.personal(principal.actor_id),
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


# --------------------------------------------------------------------------
# 2026-09-08 HM-TO-A6：单条超出 Memory 内联上限的 S1 证据，曾让整个主对话的
# 每一次 history 批次都抛 primary_read_policy_unavailable（实测证据
# .local-test-evidence/2026-09-08/native-a6-7cec5249）。
# --------------------------------------------------------------------------


def oversized_user_evidence():
    """A real Host user-turn envelope whose inline payload exceeds Memory's ceiling."""
    from deskpet.memory.human_memory_service import build_foreground_turn_evidence
    from deskpet.memory.primary_visibility import inline_evidence_limit

    limit = inline_evidence_limit()
    assert limit is not None
    return build_foreground_turn_evidence(
        subject=AUTH.subject,
        authority_ref=AUTH.authority_ref,
        delivery_key="oversized-source",
        text="x" * (limit + 4096),
    )


@pytest.mark.asyncio
async def test_oversized_source_is_invisible_and_never_fails_the_batch(tmp_path):
    """One unadmissible envelope withholds only its own root, not the batch."""
    from deskpet.memory.human_memory_program import HumanMemoryProgramStore

    f = await setup(tmp_path)
    result(await f.send("queue.enqueue", {"text": "real admitted source"}))
    good, _ = (await pairs(f))[0]
    envelope, receipt = oversized_user_evidence()
    await HumanMemoryProgramStore(f.path).append_evidence(envelope, receipt)

    async with aiosqlite.connect(f.path) as db:
        db.row_factory = aiosqlite.Row
        visible = await PrimaryHistoryPolicy(
            f.path, AUTH.subject, f.policy.history
        ).check_evidence_ids(
            db=db,
            primary_ref=f.primary,
            evidence_ids=(good.evidence_id, envelope.evidence_id),
            disclosure_context=disclosure(),
        )
    assert visible == {good.evidence_id: True, envelope.evidence_id: False}
    # The unadmissible envelope never entered the SDK batch; the good root was
    # still decided by one real snapshot.
    assert len(f.policy.history_calls) == 1
    _, _, bindings = f.policy.history_calls[0]
    assert [b.envelope.evidence_id for b in bindings] == [good.evidence_id]


@pytest.mark.asyncio
async def test_oversized_source_reaches_the_sdk_batch_without_the_guard(
    tmp_path, monkeypatch
):
    """Control: without the admissibility guard the poison envelope is batched."""
    from deskpet.memory import primary_visibility as pv
    from deskpet.memory.human_memory_program import HumanMemoryProgramStore

    monkeypatch.setattr(pv, "assert_source_admissible", lambda envelope, receipt: None)
    f = await setup(tmp_path)
    result(await f.send("queue.enqueue", {"text": "real admitted source"}))
    good, _ = (await pairs(f))[0]
    envelope, receipt = oversized_user_evidence()
    await HumanMemoryProgramStore(f.path).append_evidence(envelope, receipt)

    async with aiosqlite.connect(f.path) as db:
        db.row_factory = aiosqlite.Row
        await PrimaryHistoryPolicy(
            f.path, AUTH.subject, f.policy.history
        ).check_evidence_ids(
            db=db,
            primary_ref=f.primary,
            evidence_ids=(good.evidence_id, envelope.evidence_id),
            disclosure_context=disclosure(),
        )
    _, _, bindings = f.policy.history_calls[0]
    assert envelope.evidence_id in [b.envelope.evidence_id for b in bindings]


@pytest.mark.asyncio
async def test_real_sdk_batch_rejects_the_oversized_envelope(tmp_path):
    """The SDK really refuses it — the guard mirrors an actual admission rule."""
    from simple_harness_memory.core.errors import MemoryLimitError
    from simple_harness_memory.core.evidence import validate_sanitized_evidence

    envelope, receipt = oversized_user_evidence()
    with pytest.raises(MemoryLimitError) as raised:
        validate_sanitized_evidence(
            envelope, receipt, supported_filter_policies=tuple(FILTERS)
        )
    assert str(raised.value) == "evidence_payload_requires_controlled_blob_ref"


def _nested(depth):
    value = "leaf"
    for _ in range(depth):
        value = {"n": value}
    return value


@pytest.mark.parametrize(
    "name,payload",
    [
        # Every one of these is rejected by the SDK on *every* read and each one
        # reproduced the stall. The superseded hand-rolled 64 KiB guard admitted
        # all six (Task 6 review F-1).
        ("oversized_inline", {"kind": "x", "t": "y" * (64 * 1024 + 1)}),
        (
            "malformed_blob_ref",
            # `blob:sha256:` is not the controlled prefix; the SDK requires
            # `memory-blob:` and raises rather than falling back to inline.
            {
                "blob_ref": "blob:sha256:" + "a" * 64,
                "content_hash": "b" * 64,
                "byte_length": 4096,
            },
        ),
        ("blob_length_invalid", {
            "blob_ref": "memory-blob:" + "a" * 64,
            "content_hash": "b" * 64,
            "byte_length": 0,
        }),
        ("too_many_nodes", {"kind": "x", "items": [{"i": i} for i in range(5000)]}),
        ("too_deep", {"kind": "x", "deep": _nested(40)}),
        ("credential_boundary_key", {"kind": "x", "authorization": "public-looking"}),
    ],
)
def test_structurally_doomed_sources_are_all_withheld(name, payload):
    """The guard mirrors the SDK's whole admission rule, not just its size rule."""
    from simple_harness_memory.core.errors import MemoryLimitError, MemoryValidationError
    from simple_harness_memory.core.evidence import validate_sanitized_evidence

    from deskpet.execution.primary_history import evidence_pair
    from deskpet.memory.primary_visibility import (
        PrimaryVisibilityError,
        assert_source_admissible,
    )

    envelope, receipt = evidence_pair(AUTH.subject, f"sdk-{name}", payload, 1.0)
    # Control: the real SDK really refuses this pair.
    with pytest.raises((MemoryValidationError, MemoryLimitError)):
        validate_sanitized_evidence(
            envelope, receipt, supported_filter_policies=("host-primary-runtime-v1",)
        )
    with pytest.raises(PrimaryVisibilityError) as raised:
        assert_source_admissible(envelope, receipt)
    assert raised.value.code == "primary_visibility_source_unadmissible"


def test_admissible_sources_pass_the_guard():
    from deskpet.execution.primary_history import evidence_pair
    from deskpet.memory.primary_visibility import (
        assert_source_admissible,
        inline_evidence_limit,
    )

    from simple_harness.contracts import canonical_json as sdk_json

    limit = inline_evidence_limit()
    ordinary, receipt = evidence_pair(
        AUTH.subject, "sdk-small", {"kind": "x", "t": "y"}, 1.0
    )
    assert_source_admissible(ordinary, receipt)

    empty, _ = evidence_pair(AUTH.subject, "sdk-edge", {"t": ""}, 1.0)
    overhead = len(sdk_json(empty.to_json()["sanitized_payload"]).encode("utf-8"))
    edge, edge_receipt = evidence_pair(
        AUTH.subject, "sdk-edge", {"t": "y" * (limit - overhead)}, 1.0
    )
    assert (
        len(sdk_json(edge.to_json()["sanitized_payload"]).encode("utf-8")) == limit
    )
    assert_source_admissible(edge, edge_receipt)  # exactly at the ceiling

    blob, blob_receipt = evidence_pair(
        AUTH.subject,
        "sdk-blob",
        {
            "blob_ref": "memory-blob:" + "a" * 64,
            "content_hash": "b" * 64,
            "byte_length": limit * 4,
        },
        1.0,
    )
    assert_source_admissible(blob, blob_receipt)  # size-independent controlled ref


@pytest.mark.asyncio
async def test_visibility_error_carries_payload_free_cause(tmp_path):
    from simple_harness_memory.core.errors import MemoryLimitError

    from deskpet.memory.primary_visibility import PrimaryVisibilityError

    f = await setup(tmp_path)
    result(await f.send("queue.enqueue", {"text": "real admitted source"}))
    env, _ = (await pairs(f))[0]

    async def checker(**kwargs):
        raise MemoryLimitError("evidence_payload_requires_controlled_blob_ref")

    async with aiosqlite.connect(f.path) as db:
        db.row_factory = aiosqlite.Row
        with pytest.raises(PrimaryVisibilityError) as raised:
            await PrimaryHistoryPolicy(f.path, AUTH.subject, checker).check_evidence_ids(
                db=db,
                primary_ref=f.primary,
                evidence_ids=(env.evidence_id,),
                disclosure_context=disclosure(),
            )
    exc = raised.value
    assert exc.code == "primary_read_policy_unavailable"
    assert exc.cause_type == "MemoryLimitError"
    assert exc.cause_detail == "evidence_payload_requires_controlled_blob_ref"


def test_cause_detail_only_survives_for_stable_codes():
    """Task 6 review F-3: an opaque cause contributes its type and nothing else."""
    from simple_harness_memory.core.errors import MemoryLimitError, MemoryValidationError

    from deskpet.memory.primary_visibility import cause_fields

    # Free-form runtime text — an HTTP body, a SQL statement, a provider URL —
    # must never reach a durable audit row.
    opaque = sqlite3.IntegrityError(
        "UNIQUE constraint failed: https://relay.example/v1?token=abc"
    )
    assert cause_fields(opaque) == {
        "cause_type": "IntegrityError",
        "cause_detail": None,
    }
    assert cause_fields(RuntimeError("connection reset by peer")) == {
        "cause_type": "RuntimeError",
        "cause_detail": None,
    }
    # Allowlisted SDK stable-code types: the *message* is the specific code and
    # wins over a generic class-level `.code`.
    assert cause_fields(MemoryLimitError("evidence_structure_limit_exceeded")) == {
        "cause_type": "MemoryLimitError",
        "cause_detail": "evidence_structure_limit_exceeded",
    }
    assert MemoryValidationError.code == "memory_validation_error"  # generic
    assert cause_fields(
        MemoryValidationError("evidence_credential_boundary_rejected")
    ) == {
        "cause_type": "MemoryValidationError",
        "cause_detail": "evidence_credential_boundary_rejected",
    }
    # Anything exposing `.code` is a stable code by construction.
    class _Coded(RuntimeError):
        code = "short_group_source_unadmissible"

    assert cause_fields(_Coded("ignored free text")) == {
        "cause_type": "_Coded",
        "cause_detail": "short_group_source_unadmissible",
    }


def test_cause_detail_is_redacted_and_bounded():
    """Even an allowlisted message goes through the credential red line."""
    from simple_harness_memory.core.errors import MemoryLimitError

    from deskpet.memory.primary_visibility import MAX_CAUSE_DETAIL, cause_fields

    fields = cause_fields(MemoryLimitError("rejected sk-" + "A" * 32 + " tail"))
    assert "sk-" not in fields["cause_detail"]
    assert "[redacted:credential]" in fields["cause_detail"]

    long = cause_fields(MemoryLimitError("e" * (MAX_CAUSE_DETAIL + 500)))
    assert len(long["cause_detail"]) == MAX_CAUSE_DETAIL


@pytest.mark.asyncio
async def test_read_error_reexports_the_visibility_cause(tmp_path):
    """PrimaryReadModel re-wraps without losing the real cause."""
    from simple_harness_memory.core.errors import MemoryLimitError

    from deskpet.memory.primary_read_model import PrimaryReadError, PrimaryReadModel

    f = await setup(tmp_path)
    result(await f.send("queue.enqueue", {"text": "real admitted source"}))

    async def checker(**kwargs):
        raise MemoryLimitError("evidence_payload_requires_controlled_blob_ref")

    model = PrimaryReadModel(
        f.path,
        subject=AUTH.subject,
        suppression_resolver=f.policy,
        history_visibility_checker=checker,
    )
    with pytest.raises(PrimaryReadError) as raised:
        await model.state(disclosure_context=disclosure())
    exc = raised.value
    assert exc.code == "primary_read_policy_unavailable"
    assert exc.cause_type == "MemoryLimitError"
    assert exc.cause_detail == "evidence_payload_requires_controlled_blob_ref"
