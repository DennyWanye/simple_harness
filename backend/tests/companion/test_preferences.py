from __future__ import annotations

import json
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import CompanionGrowthConfig
from deskpet.agent.turn_preparer import ProductTurnPreparer, TurnInput
from deskpet.agent.assembler.bundle import (
    AssemblyDecisions,
    ComponentTrace,
    ContextBundle,
)
from deskpet.companion.contracts import GrowthEvent, OwnerRef
from deskpet.companion.contracts import CompanionConflictError
from deskpet.companion.identity_gate import (
    FrozenOwnerIdentity,
    IdentityReadyGate,
)
from deskpet.companion.preferences import (
    ModelPreferenceTurnInterpreter,
    PreferencePolicy,
    PreferenceResolver,
    RequestPreferenceOverride,
)
from deskpet.companion.store import CompanionStore
from deskpet.companion.turn_authority import (
    CompanionCatalogLeaseCaptureV1,
    CompanionTurnAuthority,
    CompanionTurnPreparationRequestV1,
)
from deskpet.memory.facts import ExtractedFact, FactExtractor


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 25, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.value

    def store_now(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


def _typed_authority(
    owner: OwnerRef,
    resolver: PreferenceResolver,
    *,
    preference_interpreter=None,
    preference_overrides=None,
) -> CompanionTurnAuthority:
    gate = IdentityReadyGate()
    gate.bind(
        FrozenOwnerIdentity(
            owner=owner,
            owner_key=(
                f"companion:{owner.profile_id}:{owner.profile_generation}"
            ),
            binding_epoch=1,
        )
    )
    return CompanionTurnAuthority(
        identity_gate=gate,
        preference_resolver=resolver,
        preference_interpreter=preference_interpreter,
        preference_overrides=preference_overrides,
    )


def _empty_catalog_capture() -> CompanionCatalogLeaseCaptureV1:
    return CompanionCatalogLeaseCaptureV1(
        run_catalog_content_stamp="run-catalog",
        process_catalog_stamp="process-catalog",
        catalog_snapshot_ref="catalog-snapshot",
        capability_lease_intent_ref="lease-intent",
        exact_tools=(),
        lease_entries=(),
    )


@pytest.mark.asyncio
async def test_model_preference_interpreter_accepts_provider_markdown_wrapper() -> None:
    interpreter = ModelPreferenceTurnInterpreter(
        lambda _prompt: (
            "```json\n"
            '{"schema_version":1,"decision":"observe",'
            '"preference_key":"response.detail","durable_value":"brief",'
            '"signal_kind":"implicit","request_value":"none",'
            '"reason_code":"semantic_request_for_brief_result"}'
            "\n```"
        )
    )

    decision = await interpreter.interpret(
        user_text="再短一点，只留结论",
        current_preferences=(),
    )

    assert decision.decision == "observe"
    assert decision.preference_key == "response.detail"
    assert decision.durable_value == "brief"
    assert decision.signal_kind == "implicit"


@pytest.mark.asyncio
async def test_model_preference_interpreter_repairs_unambiguous_value_slot() -> None:
    interpreter = ModelPreferenceTurnInterpreter(
        lambda _prompt: json.dumps(
            {
                "schema_version": 1,
                "decision": "observe",
                "preference_key": "response.detail",
                "durable_value": "none",
                "signal_kind": "implicit",
                "request_value": "brief",
                "reason_code": "semantic_shorter_feedback",
            }
        )
    )

    decision = await interpreter.interpret(
        user_text="再短一点，只留结论。",
        current_preferences=(),
    )

    assert decision.decision == "observe"
    assert decision.durable_value == "brief"
    assert decision.request_value is None


@pytest.mark.asyncio
async def test_model_preference_interpreter_deduplicates_same_value_slots() -> None:
    interpreter = ModelPreferenceTurnInterpreter(
        lambda _prompt: json.dumps(
            {
                "schema_version": 1,
                "decision": "observe",
                "preference_key": "response.detail",
                "durable_value": "brief",
                "signal_kind": "implicit",
                "request_value": "brief",
                "reason_code": "semantic_shorter_feedback",
            }
        )
    )

    decision = await interpreter.interpret(
        user_text="再短一点，只留结论。",
        current_preferences=(),
    )

    assert decision.decision == "observe"
    assert decision.durable_value == "brief"
    assert decision.request_value is None


@pytest.mark.asyncio
async def test_model_preference_interpreter_ignores_existing_durable_echo_for_override() -> None:
    interpreter = ModelPreferenceTurnInterpreter(
        lambda _prompt: json.dumps(
            {
                "schema_version": 1,
                "decision": "request_override",
                "preference_key": "response.detail",
                "durable_value": "brief",
                "signal_kind": "none",
                "request_value": "detailed",
                "reason_code": "this_turn_only_detail",
            }
        )
    )

    decision = await interpreter.interpret(
        user_text="今天这一次写详细一点；以后还是保持简短。",
        current_preferences=(),
    )

    assert decision.decision == "request_override"
    assert decision.durable_value is None
    assert decision.request_value == "detailed"


@pytest.fixture
def preference_env(tmp_path: Path):
    clock = _Clock()
    store = CompanionStore(tmp_path / "companion.db", clock=clock.store_now)
    owner = store.create_profile(
        profile_id="profile-a",
        generation=1,
        identity_namespace_hash="identity-a",
    )
    resolver = PreferenceResolver(
        store,
        policy=PreferencePolicy.from_growth_config(CompanionGrowthConfig()),
        clock=clock.now,
    )
    return clock, store, owner, resolver


def _event(
    owner: OwnerRef,
    index: int,
    *,
    context: str | None = None,
    source_kind: str = "message_ingress",
) -> GrowthEvent:
    return GrowthEvent(
        owner=owner,
        event_id=f"event-{index}",
        source_kind=source_kind,
        source_ref=f"message-{index}",
        context_key=context or f"context-{index}",
        root_run_id=f"run-{index}",
        reason_code="preference_observed",
        payload={"verified": True},
    )


def test_default_threshold_promotes_only_on_third_independent_context(
    preference_env,
) -> None:
    _, _, owner, resolver = preference_env
    assert resolver.policy.independent_context_threshold == 3

    first = resolver.record(
        _event(owner, 1),
        preference_key="response.detail",
        value="brief",
        signal_kind="implicit",
    )
    second = resolver.record(
        _event(owner, 2),
        preference_key="response.detail",
        value="brief",
        signal_kind="implicit",
    )
    third = resolver.record(
        _event(owner, 3),
        preference_key="response.detail",
        value="brief",
        signal_kind="implicit",
    )

    assert first.layer == "recent"
    assert second.layer == "recent"
    assert third.layer == "long_term"
    assert third.winner_reason == "preference_promoted_independent_contexts"
    assert third.evidence_event_ids == ("event-1", "event-2", "event-3")


def test_same_context_replay_does_not_count_and_same_event_is_idempotent(
    preference_env,
) -> None:
    _, store, owner, resolver = preference_env
    for index in range(1, 4):
        resolver.record(
            _event(owner, index, context="same-context"),
            preference_key="response.detail",
            value="brief",
            signal_kind="implicit",
        )
    before = resolver.resolve(owner, request_id="request-before").items[0]
    replay = resolver.record(
        _event(owner, 3, context="same-context"),
        preference_key="response.detail",
        value="brief",
        signal_kind="implicit",
    )
    assert before.layer == "recent"
    assert replay.state_version == before.state_version
    with store.read() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM preference_evidence"
        ).fetchone()[0] == 3
        assert db.execute(
            """SELECT COUNT(*) FROM audit_events
               WHERE action='preference_transition'"""
        ).fetchone()[0] == 3


def test_same_event_replay_does_not_bump_detail_or_transition(preference_env) -> None:
    _, store, owner, resolver = preference_env
    event = _event(owner, 1)
    first = resolver.record(
        event,
        preference_key="tone",
        value="formal",
        signal_kind="implicit",
    )
    before_detail = store.get_detail_version(owner)["detail_version"]
    second = resolver.record(
        event,
        preference_key="tone",
        value="formal",
        signal_kind="implicit",
    )
    after_detail = store.get_detail_version(owner)["detail_version"]
    assert second.state_version == first.state_version
    assert after_detail == before_detail
    assert len(resolver.list_transition_audits(owner, preference_key="tone")) == 1


def test_same_event_with_changed_value_fails_closed(preference_env) -> None:
    _, _, owner, resolver = preference_env
    event = _event(owner, 1)
    resolver.record(
        event,
        preference_key="tone",
        value="formal",
        signal_kind="implicit",
    )
    with pytest.raises(CompanionConflictError, match="growth_event_conflict"):
        resolver.record(
            event,
            preference_key="tone",
            value="casual",
            signal_kind="implicit",
        )


@pytest.mark.parametrize("value", [1, 11])
def test_policy_rejects_out_of_range_threshold(value: int) -> None:
    with pytest.raises(ValueError, match="range 2..10"):
        PreferencePolicy(independent_context_threshold=value)


@pytest.mark.parametrize("value", [2, 10])
def test_policy_accepts_boundary_threshold(value: int) -> None:
    assert PreferencePolicy(
        independent_context_threshold=value
    ).independent_context_threshold == value


def test_decayed_evidence_neither_hits_nor_promotes(preference_env) -> None:
    clock, _, owner, resolver = preference_env
    for index in range(1, 4):
        resolver.record(
            _event(owner, index),
            preference_key="response.detail",
            value="brief",
            signal_kind="implicit",
        )
    assert resolver.resolve(owner, request_id="before").items[0].layer == "long_term"

    clock.advance(days=31)
    assert resolver.resolve(owner, request_id="after").items == ()

    new = resolver.record(
        _event(owner, 4),
        preference_key="response.detail",
        value="brief",
        signal_kind="implicit",
    )
    assert new.layer == "recent"


def test_conflict_evidence_blocks_promotion_and_preserves_reason(
    preference_env,
) -> None:
    _, _, owner, resolver = preference_env
    for index in range(1, 4):
        resolver.record(
            _event(owner, index),
            preference_key="tone",
            value="formal",
            signal_kind="implicit",
        )
    resolver.record(
        _event(owner, 4),
        preference_key="tone",
        value="casual",
        signal_kind="conflict",
    )
    item = resolver.resolve(owner, request_id="conflict").items[0]
    assert item.layer == "recent"
    assert item.value == "formal"
    assert item.winner_reason == "preference_conflict_retained_recent_only"


def test_explicit_correction_immediately_wins_over_implicit_and_assumption(
    preference_env,
) -> None:
    _, _, owner, resolver = preference_env
    resolver.record(
        _event(owner, 1),
        preference_key="tone",
        value="playful",
        signal_kind="model_assumption",
    )
    resolver.record(
        _event(owner, 2),
        preference_key="tone",
        value="casual",
        signal_kind="implicit",
    )
    corrected = resolver.record(
        _event(owner, 3, source_kind="explicit_user_command"),
        preference_key="tone",
        value="formal",
        signal_kind="explicit_long_term",
    )
    assert corrected.value == "formal"
    assert corrected.authority == "explicit"
    assert corrected.winner_reason == "explicit_long_term_correction_wins"


def test_request_override_wins_without_persisting(preference_env) -> None:
    _, store, owner, resolver = preference_env
    resolver.record(
        _event(owner, 1, source_kind="explicit_user_command"),
        preference_key="response.detail",
        value="brief",
        signal_kind="explicit_long_term",
    )
    current = resolver.resolve(
        owner,
        request_id="request-detailed",
        overrides=(
            RequestPreferenceOverride("response.detail", "detailed"),
        ),
    ).items[0]
    later = resolver.resolve(owner, request_id="request-later").items[0]
    assert current.value == "detailed"
    assert current.layer == "request"
    assert current.evidence_event_ids == ()
    assert later.value == "brief"
    with store.read() as db:
        row = db.execute(
            "SELECT explicit_value_json FROM preferences"
        ).fetchone()
        assert json.loads(row[0]) == "brief"


def test_same_turn_detailed_and_future_brief_has_two_independent_scopes(
    preference_env,
) -> None:
    _, _, owner, resolver = preference_env
    resolver.record(
        _event(owner, 1, source_kind="explicit_user_command"),
        preference_key="response.detail",
        value="brief",
        signal_kind="explicit_long_term",
    )
    this_turn = resolver.resolve(
        owner,
        request_id="request-1",
        overrides=(
            RequestPreferenceOverride(
                "response.detail",
                "detailed",
                "explicit_this_turn_detailed",
            ),
        ),
    )
    next_turn = resolver.resolve(owner, request_id="request-2")
    assert this_turn.items[0].value == "detailed"
    assert this_turn.items[0].layer == "request"
    assert next_turn.items[0].value == "brief"
    assert next_turn.items[0].layer == "long_term"


@pytest.mark.asyncio
async def test_model_interpreter_uses_semantic_decision_and_fails_safe() -> None:
    replies = iter(
        (
            json.dumps(
                {
                    "schema_version": 1,
                    "decision": "observe",
                    "preference_key": "response.detail",
                    "durable_value": "brief",
                    "signal_kind": "implicit",
                    "request_value": "none",
                    "reason_code": "user_requested_shorter_result",
                }
            ),
            "not-json",
        )
    )

    async def llm(prompt: str) -> str:
        assert "不使用关键词或正则路由" in prompt
        assert "只输出一个符合下面 schema 的 JSON 对象" in prompt
        assert '"decision"' in prompt
        return next(replies)

    interpreter = ModelPreferenceTurnInterpreter(llm)
    observed = await interpreter.interpret(
        user_text="换一种自然说法表达偏好",
        current_preferences=(),
    )
    assert observed.decision == "observe"
    assert observed.durable_value == "brief"
    assert observed.signal_kind == "implicit"

    failed = await interpreter.interpret(
        user_text="模型输出损坏也不能阻断主消息",
        current_preferences=(),
    )
    assert failed.decision == "abstain"
    assert failed.reason_code == "model_preference_interpretation_failed"


@pytest.mark.asyncio
async def test_turn_authority_promotes_three_tasks_inside_one_session(
    preference_env,
) -> None:
    _, store, owner, resolver = preference_env

    async def llm(_prompt: str) -> str:
        return json.dumps(
            {
                "schema_version": 1,
                "decision": "observe",
                "preference_key": "response.detail",
                "durable_value": "brief",
                "signal_kind": "implicit",
                "request_value": "none",
                "reason_code": "semantic_shorter_feedback",
            }
        )

    authority = _typed_authority(
        owner,
        resolver,
        preference_interpreter=ModelPreferenceTurnInterpreter(llm),
    )
    prepared = []
    for index in range(1, 4):
        prepared.append(
            await authority.prepare_turn(
                CompanionTurnPreparationRequestV1(
                    turn=TurnInput(
                        text="换一种方式表达：我更希望结果精炼",
                        session_id="session-shared",
                        request_id=f"request-{index}",
                        turn_id=f"turn-{index}",
                        root_run_id=f"run-{index}",
                        task_scope_id=f"task-{index}",
                    ),
                    services={},
                    current_message_id=index,
                )
            )
        )

    winner = prepared[-1].preference_resolution.items[0]
    assert winner.value == "brief"
    assert winner.layer == "long_term"
    assert winner.winner_reason == "preference_promoted_independent_contexts"
    with store.read() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM preference_evidence"
        ).fetchone()[0] == 3


@pytest.mark.asyncio
async def test_preference_observation_does_not_conflict_with_ingress_event(
    preference_env,
) -> None:
    _, store, owner, resolver = preference_env
    store.record_growth_event(
        GrowthEvent(
            owner=owner,
            event_id="generic-ingress-42",
            source_kind="message_ingress",
            source_ref="message:session-shared:42",
            context_key="session:session-shared",
            root_run_id="run-42",
            reason_code="committed_user_message",
            payload={"message_id": 42},
        )
    )

    async def llm(_prompt: str) -> str:
        return json.dumps(
            {
                "schema_version": 1,
                "decision": "observe",
                "preference_key": "response.detail",
                "durable_value": "brief",
                "signal_kind": "implicit",
                "request_value": "none",
                "reason_code": "semantic_shorter_feedback",
            }
        )

    authority = _typed_authority(
        owner,
        resolver,
        preference_interpreter=ModelPreferenceTurnInterpreter(llm),
    )
    prepared = await authority.prepare_turn(
        CompanionTurnPreparationRequestV1(
            turn=TurnInput(
                text="希望下一份回答自然地更精炼",
                session_id="session-shared",
                request_id="request-42",
                turn_id="turn-42",
                root_run_id="run-42",
                task_scope_id="task-42",
            ),
            services={},
            current_message_id=42,
        )
    )
    assert prepared.preference_resolution.items[0].value == "brief"
    with store.read() as db:
        rows = db.execute(
            """SELECT source_kind,source_ref FROM growth_events
               ORDER BY source_kind"""
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("message_ingress", "message:session-shared:42"),
        (
            "preference_observation",
            "preference-observation:message:session-shared:42:response.detail",
        ),
    ]


@pytest.mark.asyncio
async def test_preference_decision_receipt_replays_after_crash_before_evidence(
    preference_env,
    monkeypatch,
) -> None:
    clock, store, owner, resolver = preference_env
    model_calls = 0

    async def llm(_prompt: str) -> str:
        nonlocal model_calls
        model_calls += 1
        return json.dumps(
            {
                "schema_version": 1,
                "decision": "observe",
                "preference_key": "response.detail",
                "durable_value": "brief",
                "signal_kind": "implicit",
                "request_value": "none",
                "reason_code": "semantic_shorter_feedback",
            }
        )

    authority = _typed_authority(
        owner,
        resolver,
        preference_interpreter=ModelPreferenceTurnInterpreter(llm),
    )
    request = CompanionTurnPreparationRequestV1(
        turn=TurnInput(
            text="Please make the next answer shorter while keeping conclusions.",
            session_id="session-crash",
            request_id="request-crash",
            turn_id="turn-crash",
            root_run_id="run-crash",
            task_scope_id="task-crash",
        ),
        services={},
        current_message_id=7,
    )
    original_record = resolver.record

    def crash_before_evidence(*_args, **_kwargs):
        raise RuntimeError("injected_crash_before_preference_evidence")

    monkeypatch.setattr(resolver, "record", crash_before_evidence)
    with pytest.raises(
        RuntimeError, match="injected_crash_before_preference_evidence"
    ):
        await authority.prepare_turn(request)

    with store.read() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM preference_turn_decision_receipts"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM preference_evidence"
        ).fetchone()[0] == 0

    monkeypatch.setattr(resolver, "record", original_record)
    restarted_store = CompanionStore(store.path, clock=clock.store_now)
    restarted_resolver = PreferenceResolver(
        restarted_store,
        policy=resolver.policy,
        clock=clock.now,
    )
    restarted_authority = _typed_authority(
        owner,
        restarted_resolver,
        preference_interpreter=ModelPreferenceTurnInterpreter(llm),
    )
    replay = await restarted_authority.prepare_turn(request)
    assert replay.preference_resolution.items[0].value == "brief"
    assert model_calls == 1
    with store.read() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM preference_turn_decision_receipts"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM preference_evidence"
        ).fetchone()[0] == 1

    second_replay = await restarted_authority.prepare_turn(request)
    assert second_replay.preference_resolution.items[0].value == "brief"
    assert model_calls == 1
    with store.read() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM preference_evidence"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_preference_decision_receipt_fences_source_message_content(
    preference_env,
) -> None:
    _, _, owner, resolver = preference_env
    model_calls = 0

    async def llm(_prompt: str) -> str:
        nonlocal model_calls
        model_calls += 1
        return json.dumps(
            {
                "schema_version": 1,
                "decision": "abstain",
                "preference_key": "none",
                "durable_value": "none",
                "signal_kind": "none",
                "request_value": "none",
                "reason_code": "no_preference_intent",
            }
        )

    authority = _typed_authority(
        owner,
        resolver,
        preference_interpreter=ModelPreferenceTurnInterpreter(llm),
    )
    turn = dict(
        session_id="session-fence",
        request_id="request-fence",
        turn_id="turn-fence",
        root_run_id="run-fence",
        task_scope_id="task-fence",
    )
    await authority.prepare_turn(
        CompanionTurnPreparationRequestV1(
            turn=TurnInput(text="ordinary task", **turn),
            services={},
            current_message_id=3,
        )
    )
    with pytest.raises(
        CompanionConflictError,
        match="preference_turn_source_message_conflict",
    ):
        await authority.prepare_turn(
            CompanionTurnPreparationRequestV1(
                turn=TurnInput(text="mutated message content", **turn),
                services={},
                current_message_id=3,
            )
        )
    assert model_calls == 1


@pytest.mark.asyncio
async def test_model_failure_is_not_receipted_and_same_message_can_retry(
    preference_env,
) -> None:
    _, store, owner, resolver = preference_env
    replies = iter(
        (
            "not-json",
            json.dumps(
                {
                    "schema_version": 1,
                    "decision": "observe",
                    "preference_key": "response.detail",
                    "durable_value": "brief",
                    "signal_kind": "implicit",
                    "request_value": "none",
                    "reason_code": "semantic_shorter_feedback",
                }
            ),
        )
    )
    model_calls = 0

    async def llm(_prompt: str) -> str:
        nonlocal model_calls
        model_calls += 1
        return next(replies)

    authority = _typed_authority(
        owner,
        resolver,
        preference_interpreter=ModelPreferenceTurnInterpreter(llm),
    )
    request = CompanionTurnPreparationRequestV1(
        turn=TurnInput(
            text="Please make the next answer shorter while keeping conclusions.",
            session_id="session-retry",
            request_id="request-retry",
            turn_id="turn-retry",
            root_run_id="run-retry",
            task_scope_id="task-retry",
        ),
        services={},
        current_message_id=8,
    )

    failed = await authority.prepare_turn(request)
    assert failed.preference_resolution.items == ()
    with store.read() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM preference_turn_decision_receipts"
        ).fetchone()[0] == 0

    recovered = await authority.prepare_turn(request)
    assert recovered.preference_resolution.items[0].value == "brief"
    assert model_calls == 2
    with store.read() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM preference_turn_decision_receipts"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM preference_evidence"
        ).fetchone()[0] == 1


@pytest.mark.asyncio
async def test_turn_authority_rejects_owner_switch_after_ingress(
    preference_env,
) -> None:
    _, _, owner, resolver = preference_env
    authority = _typed_authority(owner, resolver)
    with pytest.raises(
        RuntimeError, match="companion_ingress_owner_fence_changed"
    ):
        await authority.prepare_turn(
            CompanionTurnPreparationRequestV1(
                turn=TurnInput(
                    text="普通消息",
                    session_id="session-owner-fence",
                    request_id="request-owner-fence",
                    turn_id="turn-owner-fence",
                    root_run_id="run-owner-fence",
                    task_scope_id="task-owner-fence",
                ),
                services={},
                current_message_id=3,
                ingress_owner=FrozenOwnerIdentity(
                    owner=OwnerRef("previous-profile", 1),
                    owner_key="companion:previous-profile:1",
                    binding_epoch=1,
                ),
            )
        )


@pytest.mark.asyncio
async def test_turn_authority_model_request_override_never_mutates_long_term(
    preference_env,
) -> None:
    _, store, owner, resolver = preference_env
    resolver.record(
        _event(owner, 1, source_kind="explicit_user_command"),
        preference_key="response.detail",
        value="brief",
        signal_kind="explicit_long_term",
    )
    with store.read() as db:
        before = db.execute(
            """SELECT state_version FROM preferences
               WHERE preference_key='response.detail'"""
        ).fetchone()[0]

    async def llm(_prompt: str) -> str:
        return json.dumps(
            {
                "schema_version": 1,
                "decision": "request_override",
                "preference_key": "response.detail",
                "durable_value": "none",
                "signal_kind": "none",
                "request_value": "detailed",
                "reason_code": "this_turn_only_detail",
            }
        )

    authority = _typed_authority(
        owner,
        resolver,
        preference_interpreter=ModelPreferenceTurnInterpreter(llm),
    )
    prepared = await authority.prepare_turn(
        CompanionTurnPreparationRequestV1(
            turn=TurnInput(
                text="这一次展开过程，之后仍保持原来的简短风格",
                session_id="session-once",
                request_id="request-once",
                turn_id="turn-once",
                root_run_id="run-once",
                task_scope_id="task-once",
            ),
            services={},
            current_message_id=9,
        )
    )
    assert prepared.preference_resolution.items[0].value == "detailed"
    assert prepared.preference_resolution.items[0].layer == "request"
    assert resolver.resolve(owner, request_id="later").items[0].value == "brief"
    with store.read() as db:
        after = db.execute(
            """SELECT state_version FROM preferences
               WHERE preference_key='response.detail'"""
        ).fetchone()[0]
        evidence_count = db.execute(
            "SELECT COUNT(*) FROM preference_evidence"
        ).fetchone()[0]
    assert after == before
    assert evidence_count == 1


def test_forget_recomputes_in_same_transaction_and_notifies_revocation(
    preference_env,
) -> None:
    _, store, owner, resolver = preference_env
    for index in range(1, 4):
        resolver.record(
            _event(owner, index),
            preference_key="response.detail",
            value="brief",
            signal_kind="implicit",
        )
    assert resolver.resolve(owner, request_id="before").items[0].layer == "long_term"

    assert store.forget_growth_event(
        owner,
        event_id="event-3",
        reason_code="user_deleted_evidence",
    )
    after = resolver.resolve(owner, request_id="after").items[0]
    assert after.layer == "recent"
    with store.read() as db:
        row = db.execute(
            """SELECT content_state FROM growth_events WHERE event_id='event-3'"""
        ).fetchone()
        pref = db.execute(
            """SELECT long_term_value_json,state_version FROM preferences
               WHERE preference_key='response.detail'"""
        ).fetchone()
        notice = db.execute(
            """SELECT event_kind,sink_kind,status FROM outbox
               WHERE event_kind='preference_changed'"""
        ).fetchone()
    assert row[0] == "tombstoned"
    assert pref[0] is None
    assert tuple(notice) == (
        "preference_changed",
        "companion_notification",
        "pending",
    )
    detail = store.get_detail_version(owner)["detail_version"]
    with store.read() as db:
        transitions = db.execute(
            """SELECT COUNT(*) FROM audit_events
               WHERE action='preference_transition'"""
        ).fetchone()[0]
        notices = db.execute(
            """SELECT COUNT(*) FROM outbox
               WHERE event_kind='preference_changed'"""
        ).fetchone()[0]
    assert store.forget_growth_event(
        owner,
        event_id="event-3",
        reason_code="user_deleted_evidence",
    ) is False
    assert store.get_detail_version(owner)["detail_version"] == detail
    with store.read() as db:
        assert db.execute(
            """SELECT COUNT(*) FROM audit_events
               WHERE action='preference_transition'"""
        ).fetchone()[0] == transitions
        assert db.execute(
            """SELECT COUNT(*) FROM outbox
               WHERE event_kind='preference_changed'"""
        ).fetchone()[0] == notices


def test_resolution_freezes_dependency_identity(preference_env) -> None:
    _, _, owner, resolver = preference_env
    resolver.record(
        _event(owner, 1, source_kind="explicit_user_command"),
        preference_key="tone",
        value="formal",
        signal_kind="explicit_long_term",
    )
    resolution = resolver.resolve(owner, request_id="request-1")
    assert len(resolution.dependencies) == 1
    dependency = resolution.dependencies[0]
    assert dependency.dependency_kind == "preference"
    assert dependency.dependency_id == "tone"
    assert dependency.content_hash == resolution.items[0].content_hash
    assert dependency.evidence_event_ids == ("event-1",)
    assert resolution.snapshot_hash
    assert "本次运行冻结快照" in resolution.prompt_block()
    assert "逐项核对并覆盖每个结论" in resolution.prompt_block()
    assert "完整优先" in resolution.prompt_block()


def test_legacy_json_import_is_idempotent(
    preference_env,
    tmp_path: Path,
) -> None:
    _, store, _, resolver = preference_env
    owner = store.create_profile(
        profile_id="legacy_local_profile",
        generation=1,
        identity_namespace_hash="legacy-local-identity",
    )
    source = tmp_path / "preference_memory.json"
    source.write_text(
        json.dumps(
            [
                {
                    "text": "生成计划后直接执行",
                    "label": "approved",
                    "kind": "plan",
                    "embedding": [0.1, 0.2],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert resolver.import_legacy_json(owner, source) == 1
    assert resolver.import_legacy_json(owner, source) == 0
    with store.read() as db:
        assert db.execute(
            """SELECT COUNT(*) FROM growth_events
               WHERE source_kind='legacy_preference_json'"""
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM preference_evidence"
        ).fetchone()[0] == 1


def test_legacy_json_import_missing_and_malformed(
    preference_env,
    tmp_path: Path,
) -> None:
    _, store, _, resolver = preference_env
    owner = store.create_profile(
        profile_id="legacy_local_profile",
        generation=1,
        identity_namespace_hash="legacy-local-identity",
    )
    assert resolver.import_legacy_json(owner, tmp_path / "missing.json") == 0
    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"not":"a list"}', encoding="utf-8")
    with pytest.raises(ValueError, match="must be a list"):
        resolver.import_legacy_json(owner, malformed)


def test_legacy_json_import_rejects_non_legacy_owner(
    preference_env,
    tmp_path: Path,
) -> None:
    _, _, owner, resolver = preference_env
    with pytest.raises(ValueError, match="legacy_local_profile generation 1"):
        resolver.import_legacy_json(owner, tmp_path / "missing.json")


def test_owner_generation_and_relevant_key_isolation(preference_env) -> None:
    _, store, owner, resolver = preference_env
    other = store.create_profile(
        profile_id="profile-b",
        generation=1,
        identity_namespace_hash="identity-b",
    )
    resolver.record(
        _event(owner, 1),
        preference_key="tone",
        value="formal",
        signal_kind="implicit",
    )
    resolver.record(
        GrowthEvent(
            owner=owner,
            event_id="event-detail",
            source_kind="message_ingress",
            source_ref="message-detail",
            context_key="context-detail",
            root_run_id="run-detail",
            reason_code="preference_observed",
            payload={},
        ),
        preference_key="detail",
        value="brief",
        signal_kind="implicit",
    )
    assert resolver.resolve(other, request_id="other").items == ()
    only_tone = resolver.resolve(
        owner,
        request_id="filtered",
        relevant_keys=("tone",),
    )
    assert [item.preference_key for item in only_tone.items] == ["tone"]


def test_resolver_uses_public_store_write_api_only() -> None:
    source = inspect.getsource(PreferenceResolver)
    for private_call in (
        "store._write(",
        "store._require_owner(",
        "store._now(",
        "store._bump_detail(",
        "store._insert_outbox(",
    ):
        assert private_call not in source


def test_production_authority_uses_companion_branch_after_durable_cutover() -> None:
    main_source = (
        Path(__file__).resolve().parents[2] / "main.py"
    ).read_text(encoding="utf-8")
    start = main_source.index("async def _initialize_growth_authority")
    end = main_source.index(
        "\n\nasync def _initialize_companion_projection_services",
        start,
    )
    composition = main_source[start:end]
    assert 'service_context.register(\n        "companion_preference_resolver_dormant"' in composition
    assert "legacy=RetiredLegacyGrowthAuthority()" in composition
    assert "companion=companion" in composition
    assert "GrowthAuthorityCutoverCoordinator(" in composition
    assert "configure_growth_authority_router" not in composition


def test_transition_audit_exposes_frozen_typed_policy(preference_env) -> None:
    _, _, owner, resolver = preference_env
    resolver.record(
        _event(owner, 1),
        preference_key="tone",
        value="formal",
        signal_kind="implicit",
    )
    audits = resolver.list_transition_audits(owner, preference_key="tone")
    assert len(audits) == 1
    assert audits[0]["policy"] == resolver.policy.to_dict()
    assert audits[0]["policy_hash"] == resolver.policy.policy_hash


def test_dependency_contains_only_winner_evidence(preference_env) -> None:
    _, _, owner, resolver = preference_env
    resolver.record(
        _event(owner, 1),
        preference_key="tone",
        value="formal",
        signal_kind="model_assumption",
    )
    resolver.record(
        _event(owner, 2),
        preference_key="tone",
        value="formal",
        signal_kind="implicit",
    )
    resolver.record(
        _event(owner, 3),
        preference_key="tone",
        value="casual",
        signal_kind="conflict",
    )
    item = resolver.resolve(owner, request_id="winner-only").items[0]
    assert item.evidence_event_ids == ("event-2",)


@pytest.mark.asyncio
async def test_preparer_test_composition_injects_exact_snapshot_and_dependency(
    preference_env,
) -> None:
    _, store, owner, resolver = preference_env
    resolver.record(
        _event(owner, 1, source_kind="explicit_user_command"),
        preference_key="response.detail",
        value="brief",
        signal_kind="explicit_long_term",
    )
    router = object()
    authority = _typed_authority(
        owner,
        resolver,
        preference_overrides=lambda _turn, _services: (
            RequestPreferenceOverride("response.detail", "detailed"),
        ),
    )
    preparer = ProductTurnPreparer(companion_turn_authority=authority)
    prepared = await preparer.prepare_context(
        TurnInput(
            text="本次展开说明",
            session_id="session-a",
            request_id="request-a",
            turn_id="turn-a",
            root_run_id="run-a",
        ),
        services={"growth_authority_router": router},
        config=SimpleNamespace(
            features=SimpleNamespace(summary_quality_loop=False),
            raw={},
        ),
        local_llm=SimpleNamespace(model="test", base_url=""),
        tool_registry=object(),
        current_message_id=1,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
    )
    assert prepared.growth_preference_reader is router
    assert prepared.preference_resolution.items[0].value == "detailed"
    assert prepared.preference_resolution.items[0].layer == "request"
    assert len(prepared.growth_dependencies) == 1
    assert prepared.growth_dependencies[0].dependency_id == "response.detail"
    assert prepared.growth_dependencies[0].version == str(
        prepared.preference_resolution.items[0].state_version
    )
    assert prepared.growth_dependencies[0].evidence_event_ids == ()
    finalized = await authority.finalize_after_catalog(
        prepared.companion_authority_state,
        _empty_catalog_capture(),
        run_id="run-a",
    )
    with store.read() as db:
        growth_snapshot = db.execute(
            """SELECT snapshot_id,snapshot_hash FROM run_growth_snapshots
               WHERE request_id='request-a'"""
        ).fetchone()
        dependency = db.execute(
            """SELECT dependency_id,version,content_hash
               FROM run_growth_dependency_items
               WHERE snapshot_id=?""",
            (growth_snapshot["snapshot_id"],),
        ).fetchone()
        dependency_evidence_count = db.execute(
            """SELECT COUNT(*) FROM run_growth_dependency_evidence
               WHERE snapshot_id=?""",
            (growth_snapshot["snapshot_id"],),
        ).fetchone()[0]
    assert finalized.product_snapshot_ref == growth_snapshot["snapshot_id"]
    assert finalized.product_snapshot_hash == growth_snapshot["snapshot_hash"]
    assert tuple(dependency) == (
        "response.detail",
        prepared.growth_dependencies[0].version,
        prepared.growth_dependencies[0].content_hash,
    )
    assert dependency_evidence_count == 0
    assert any(
        item.get("_is_companion_preference_snapshot")
        and '"value":"detailed"' in item["content"]
        for item in prepared.messages
    )
    assert resolver.resolve(owner, request_id="next").items[0].value == "brief"


@pytest.mark.asyncio
async def test_preparer_freezes_managed_auto_disclosure_scope(
    preference_env,
) -> None:
    _, _, owner, resolver = preference_env
    scope_hash = "1" * 64
    scope = {
        "owner_key": "builtin",
        "pack_id": "managed-pack",
        "skill_id": "managed-skill",
        "version": "1.0.0",
        "manifest_hash": "2" * 64,
        "content_hash": "3" * 64,
        "allowed_tools": ["memory_recall"],
        "scope_id": f"skill-scope:{scope_hash}",
        "scope_hash": scope_hash,
    }
    bundle = ContextBundle(
        task_type="chat",
        decisions=AssemblyDecisions(
            components={
                "skill": ComponentTrace(
                    included=True,
                    meta={
                        "skill_invocation_scopes": [scope],
                        "active_skill_scope_ids": [scope["scope_id"]],
                    },
                )
            }
        ),
    )
    managed = object()
    legacy = object()

    class Assembler:
        enabled = True
        seen_registry = None

        async def assemble(self, **kwargs):
            self.seen_registry = kwargs["skill_registry"]
            return bundle

    class Registry:
        @staticmethod
        def has(_name: str) -> bool:
            return False

    assembler = Assembler()
    authority = _typed_authority(owner, resolver)
    prepared = await ProductTurnPreparer(
        companion_turn_authority=authority,
    ).prepare_context(
        TurnInput(
            text="use managed skill",
            session_id="session-managed",
            request_id="request-managed",
            turn_id="turn-managed",
            root_run_id="run-managed",
        ),
        services={
            "context_assembler": assembler,
            "managed_skill_discovery_projection": managed,
            "skill_loader": legacy,
        },
        config=SimpleNamespace(
            features=SimpleNamespace(
                summary_quality_loop=False,
                context_os_v1=False,
            ),
            raw={},
        ),
        local_llm=SimpleNamespace(model="test", base_url=""),
        tool_registry=Registry(),
        current_message_id=1,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
    )

    assert assembler.seen_registry is managed
    state = prepared.companion_authority_state
    assert [dict(item) for item in state.skill_invocation_scopes] == [scope]
    assert list(state.active_skill_scope_ids) == [scope["scope_id"]]
    assert [dict(item) for item in state.selected_instruction_refs] == [
        {
            key: scope[key]
            for key in (
                "owner_key",
                "pack_id",
                "skill_id",
                "version",
                "manifest_hash",
                "content_hash",
                "scope_id",
                "scope_hash",
            )
        }
    ]


@pytest.mark.asyncio
async def test_preparer_persists_durable_winner_dependency(preference_env) -> None:
    _, store, owner, resolver = preference_env
    resolver.record(
        _event(owner, 1, source_kind="explicit_user_command"),
        preference_key="tone",
        value="formal",
        signal_kind="explicit_long_term",
    )
    authority = _typed_authority(owner, resolver)
    preparer = ProductTurnPreparer(companion_turn_authority=authority)
    prepared = await preparer.prepare_context(
        TurnInput(
            text="继续",
            session_id="session-a",
            request_id="request-b",
            turn_id="turn-b",
            root_run_id="run-b",
        ),
        services={},
        config=SimpleNamespace(
            features=SimpleNamespace(summary_quality_loop=False),
            raw={},
        ),
        local_llm=SimpleNamespace(model="test", base_url=""),
        tool_registry=object(),
        current_message_id=2,
        summary_user_is_confused=lambda _text: False,
        summary_latest_task_snapshot=lambda _entries: None,
        summary_build_reinject_msg=lambda _value: {},
    )
    assert len(prepared.growth_dependencies) == 1
    dependency = prepared.growth_dependencies[0]
    assert dependency.dependency_id == "tone"
    assert dependency.evidence_event_ids == ("event-1",)
    finalized = await authority.finalize_after_catalog(
        prepared.companion_authority_state,
        _empty_catalog_capture(),
        run_id="run-b",
    )
    with store.read() as db:
        snapshot = db.execute(
            """SELECT snapshot_id FROM run_growth_snapshots
               WHERE request_id='request-b'"""
        ).fetchone()
        item = db.execute(
            """SELECT dependency_kind,dependency_id,version,content_hash
               FROM run_growth_dependency_items
               WHERE snapshot_id=?""",
            (snapshot["snapshot_id"],),
        ).fetchone()
        evidence = db.execute(
            """SELECT event_id FROM run_growth_dependency_evidence
               WHERE snapshot_id=?""",
            (snapshot["snapshot_id"],),
        ).fetchall()
    assert finalized.product_snapshot_ref == snapshot["snapshot_id"]
    assert tuple(item) == (
        "preference",
        "tone",
        dependency.version,
        dependency.content_hash,
    )
    assert [row["event_id"] for row in evidence] == ["event-1"]


@pytest.mark.asyncio
async def test_facts_companion_branch_writes_observation_not_facts() -> None:
    class _FactsStore:
        async def find_active(self, **_kwargs):
            raise AssertionError("FactsStore preference writer must remain unused")

    observed = []

    async def observe(fact, message_id):
        observed.append((fact.key, fact.value, message_id))
        return "observation-1"

    async def llm(_prompt: str) -> str:
        return "[]"

    extractor = FactExtractor(
        _FactsStore(),
        extract_llm=llm,
        companion_preference_evidence_only=True,
        preference_observer=observe,
    )
    result = await extractor._persist_extracted(
        [
            ExtractedFact(
                category="preference",
                subject="user",
                key="Tone",
                value="Formal",
                confidence=0.9,
                evidence="explicit",
            )
        ],
        7,
    )
    assert observed == [("tone", "Formal", 7)]
    assert result[0]["action"] == "preference_evidence"
