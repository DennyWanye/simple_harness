from __future__ import annotations

import inspect
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from deskpet.companion.contracts import GrowthEvent, OwnerRef
from deskpet.companion.detail_query import (
    CompanionDetailQueryError,
    CompanionDetailQueryPort,
    _bounded_item,
)
from deskpet.companion.evaluation_read_tools import (
    EvaluationMemoryFixtureStoreV1,
    EvaluationMemoryRecordV1,
    EvaluationReadAuthorizationV1,
    EvaluationReadToolAdapterV1,
    EvaluationReadToolBindingV1,
    ProductionReadToolIdentityV1,
)
from deskpet.companion.evaluation_suites import PackagedEvaluationSuiteLoader
from deskpet.companion.identity_gate import FrozenOwnerIdentity
from deskpet.companion.schema import (
    FORBIDDEN_AUTHORITY_NAMES,
    initialize_schema,
)
from deskpet.companion.signals import GrowthSignalV1, execution_outcome_signal
from deskpet.companion.store import CompanionStore, canonical_json
from deskpet.harness.drivers.react import ReActDriver, ReactToolBatch
from deskpet.harness.ports import DriverStart, ExecuteTools
from deskpet.harness.tool_executor import EffectBatchExecutor
from deskpet.memory.companion_message_projection import TrustedCompanionOwner
from deskpet.memory.retriever import Retriever
from deskpet.memory.session_db import SessionDB
from deskpet.permissions.effect_policy import IrreversibleEffectPolicy
from deskpet.permissions.policy import AuthorizationPolicyState
from deskpet.permissions.runtime import PreparedAuthorizationRuntime
from deskpet.permissions.task_grants import ResourceSelector
from deskpet.tools.build_identity import EffectClass, IdempotencyClass
from deskpet.tools.capabilities import (
    PreparedToolCapability,
    PreparedToolSet,
    ToolCapabilityRef,
    ToolCapabilityScopeStore,
    ToolEligibilityContext,
    ToolExecutionContext,
    canonical_hash,
)
from deskpet.tools.registry import ToolSpec
from deskpet.workflows.effects import PreparedToolCall


BACKEND = Path(__file__).resolve().parents[2]
_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64
_HASH_D = "d" * 64


def _tool_spec(name: str, effect: EffectClass) -> ToolSpec:
    schema = {
        "name": name,
        "description": name,
        "parameters": {"type": "object", "properties": {}},
    }
    return ToolSpec(
        name=name,
        toolset="privacy-test",
        schema=schema,
        handler=lambda _args, _task_id: "{}",
        schema_hash=canonical_hash(schema),
        stable_handler_id=f"core.{name}.v1",
        effect_class=effect,
        idempotency=(
            IdempotencyClass.IDEMPOTENT
            if effect is EffectClass.READ_ONLY
            else IdempotencyClass.NON_IDEMPOTENT
        ),
        target_normalizer_version="v1",
    )


def _prepared(*specs: ToolSpec) -> PreparedToolSet:
    capabilities = tuple(
        PreparedToolCapability(
            ToolCapabilityRef(
                capability_id=f"builtin:{spec.name}",
                name=spec.name,
                toolset=spec.toolset,
                source="builtin",
                description=spec.description_for_llm,
                schema_hash=spec.schema_hash,
            ),
            spec.schema,
        )
        for spec in specs
    )
    return PreparedToolSet.create(
        scope_id="privacy-scope",
        revision=1,
        registry_revision=1,
        direct=capabilities,
        deferred=(),
        activated=(),
        denied_names=(),
        policy_fingerprint="privacy-policy",
        decisions=(),
    )


def _production_memory_identity() -> ProductionReadToolIdentityV1:
    return ProductionReadToolIdentityV1(
        tool_name="memory_recall",
        tool_spec_ref="deskpet.tools.memory:memory_recall",
        tool_spec_hash=_HASH_A,
        input_schema_hash=_HASH_B,
        execution_build_fingerprint=_HASH_C,
        effect_policy_ref="deskpet.tools.effect-policy:memory_recall",
        effect_policy_hash=_HASH_D,
    )


def test_growth_evidence_rejects_secret_fields_and_keeps_only_settlement_facts() -> None:
    owner = OwnerRef("profile-a", 1)
    with pytest.raises(ValueError, match="growth_payload_sensitive_key"):
        GrowthSignalV1(
            owner=owner,
            kind="execution_outcome",
            source_ref="effect:1",
            context_key="run:1",
            root_run_id="run-1",
            reason_code="effect_settled",
            payload={"authorization": "Bearer must-never-be-stored"},
        )

    signal = execution_outcome_signal(
        owner=owner,
        source_ref="effect:1",
        context_key="run:1",
        root_run_id="run-1",
        status="succeeded",
        receipt_ref="receipt:1",
        evidence_verified=True,
        capability_audit_ref="audit:1",
        error_class=None,
    )
    assert set(signal.payload) == {
        "status",
        "receipt_ref",
        "evidence_verified",
        "capability_audit_ref",
        "error_class",
    }
    assert not {
        "raw_args",
        "raw_result",
        "authorization",
        "access_token",
        "credential",
    } & set(signal.payload)


def test_detail_payload_redacts_secrets_before_applying_the_byte_limit() -> None:
    secret = "sk_" + "sensitive-value-" * 32
    item = _bounded_item(
        {
            "authorization": f"Bearer {secret}",
            "api_key": secret,
            "body": "x" * 20_000,
        }
    )
    encoded = canonical_json(item).encode("utf-8")

    assert secret.encode("utf-8") not in encoded
    assert len(encoded) <= 8192
    assert item["truncated"] is True
    assert item["original_bytes"] > 8192


def test_packaged_evaluation_and_frozen_fixture_need_no_live_memory_store(
    tmp_path: Path,
) -> None:
    manifest = PackagedEvaluationSuiteLoader().validate_release()
    assert {"skill_v1", "workflow_v1"} == {
        suite.suite_id for suite in manifest.suites
    }

    fixture = EvaluationMemoryFixtureStoreV1.freeze(
        fixture_id="privacy-case",
        records=(
            EvaluationMemoryRecordV1(
                record_id="record-1",
                text="The preferred theme is midnight blue.",
                metadata={"kind": "preference"},
            ),
        ),
        sanitized=True,
    )
    binding = EvaluationReadToolBindingV1.bind(
        production_identity=_production_memory_identity(),
        fixture=fixture,
    )
    authorization = EvaluationReadAuthorizationV1.for_binding(binding)
    old = EvaluationReadToolAdapterV1(binding=binding, fixture=fixture)
    candidate = EvaluationReadToolAdapterV1(binding=binding, fixture=fixture)

    assert old.execute(
        {"query": "midnight blue", "limit": 1},
        authorization=authorization,
    ) == candidate.execute(
        {"query": "midnight blue", "limit": 1},
        authorization=authorization,
    )
    with pytest.raises(TypeError):
        fixture.records[0].metadata["kind"] = "changed"  # type: ignore[index]
    assert set(inspect.signature(EvaluationReadToolAdapterV1).parameters) == {
        "binding",
        "fixture",
    }

    isolated = tmp_path / "installed-resources"
    isolated.mkdir()
    package_root = (
        BACKEND / "deskpet" / "companion" / "eval_suites"
    )
    for name in ("manifest.json", "skill_v1.json", "workflow_v1.json"):
        isolated.joinpath(name).write_bytes(package_root.joinpath(name).read_bytes())
    isolated_manifest = PackagedEvaluationSuiteLoader(
        resource_root=isolated
    ).validate_release()
    assert isolated_manifest == manifest


class _AutoAuthorizationStore:
    async def get_policy_state(self) -> AuthorizationPolicyState:
        return AuthorizationPolicyState("auto", 3, 100.0)

    async def get_task_grant(self, _task_grant_id: str):
        return None


@pytest.mark.asyncio
async def test_irreversible_policy_is_recomputed_and_driver_executor_both_refuse_auto(
    tmp_path: Path,
) -> None:
    risky_specs = tuple(
        _tool_spec(f"risky_{effect.value}", effect)
        for effect in (
            EffectClass.EXTERNAL_SEND,
            EffectClass.DESTRUCTIVE,
            EffectClass.PAYMENT,
            EffectClass.CREDENTIAL,
            EffectClass.PRIVACY,
            EffectClass.UNKNOWN,
        )
    )
    for run_kind in ("foreground", "delegated_task"):
        prepared = IrreversibleEffectPolicy().apply(
            _prepared(*risky_specs),
            owner_key="companion:profile-a:1",
            run_kind=run_kind,
            specs=risky_specs,
        )
        assert prepared.confirm_only_names == tuple(
            sorted(spec.name for spec in risky_specs)
        )

    risky = risky_specs[0]
    prepared = IrreversibleEffectPolicy().apply(
        _prepared(risky),
        owner_key="companion:profile-a:1",
        run_kind="foreground",
        specs=(risky,),
    )
    scopes = ToolCapabilityScopeStore()
    scopes.open(
        prepared,
        ToolEligibilityContext("session-1", "request-1", "chat"),
    )

    class Registry:
        capability_scope_store = scopes

        @staticmethod
        def prepared_execution_policy(_call):
            return False, True

        @staticmethod
        def resolve_prepared_spec(_call):
            return SimpleNamespace(permission_category="external_action")

    runtime = PreparedAuthorizationRuntime(
        _AutoAuthorizationStore(), clock=lambda: 100.0
    )
    driver = ReActDriver(
        object(),
        None,  # type: ignore[arg-type]
        Registry(),
        auto_mode_check=lambda: True,
        authorization_runtime=runtime,
        capability_scope_store=scopes,
    )
    target = tmp_path / "external-target"
    call = PreparedToolCall.prepare(
        tool_name=risky.name,
        stable_call_id="call-1",
        final_params={"target": str(target)},
        tool_spec_version="v1",
        schema_hash=risky.schema_hash,
        permission_policy_version="v1",
        effect_type="external_send",
        resource_selectors=(ResourceSelector.filesystem(target, "write"),),
    )
    context = ToolExecutionContext(
        scope_id=prepared.scope_id,
        session_id="session-1",
        request_id="request-1",
        root_run_id="run-1",
        run_id="run-1",
        call_id="call-1",
        effect_id="effect-1",
        capability_hash=_HASH_C,
        scope_hash=_HASH_D,
    )
    request = DriverStart(
        run_id="run-1",
        session_id="session-1",
        canonical_messages=(),
        tool_set_snapshot_ref="tool-set:run-1",
    )
    boundary = driver._boundary_for_batch(
        request,
        ReactToolBatch("command-1", (call,), (context,)),
    )
    decision = driver._permission_decision(boundary, 0)
    waiting = await driver._plan_permission(boundary, decision, confirmed=False)

    assert boundary.confirm_only_names == (risky.name,)
    assert waiting.action == "wait"
    assert waiting.authorization_origin == "explicit_decision"

    command = ExecuteTools(
        "run-1",
        "command-1",
        (call,),
        (context,),
        (0,),
        effectful=(True,),
        confirm_only=(True,),
        confirm_only_snapshot_ref=boundary.confirm_only_snapshot_ref,
        confirm_only_snapshot_hash=boundary.confirm_only_snapshot_hash,
    )
    executor = EffectBatchExecutor(None, Registry())  # type: ignore[arg-type]
    assert executor._validate_confirm_only_snapshot(command) == (True,)


@pytest.mark.asyncio
async def test_memory_recall_is_owner_as_of_fenced_and_executes_zero_sql_writes(
    tmp_path: Path,
) -> None:
    db = SessionDB(tmp_path / "state.db")
    owner_a = TrustedCompanionOwner("profile-a", 1, 7)
    owner_b = TrustedCompanionOwner("profile-b", 1, 4)
    await db.ensure_session("a")
    await db.bind_session_owner_if_absent("a", owner_a)
    first = await db.append_message("a", "user", "needle owner a")
    await db.ensure_session("b")
    await db.bind_session_owner_if_absent("b", owner_b)
    await db.append_message("b", "user", "needle owner b")
    scope = await db.capture_owner_memory_read_scope("profile-a", 1, 7)
    late = await db.append_message("a", "user", "needle late")

    writes: list[int] = []
    traces: list[str] = []
    write_actions = {
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_ALTER_TABLE,
    }

    def observe(connection: sqlite3.Connection) -> None:
        connection.set_trace_callback(traces.append)

        def authorize(action, _arg1, _arg2, _db_name, _trigger):
            if action in write_actions:
                writes.append(action)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)

    hits = await Retriever(db, object()).recall_readonly(
        "needle", 20, scope, connection_observer=observe
    )
    assert [hit.message_id for hit in hits] == [first]
    assert late > scope.as_of_message_id
    assert writes == []
    assert traces


@pytest.mark.asyncio
async def test_memory_recall_uses_owner_scoped_recent_fallback_for_intent_query(
    tmp_path: Path,
) -> None:
    db = SessionDB(tmp_path / "state.db")
    owner = TrustedCompanionOwner("profile-a", 1, 7)
    await db.ensure_session("a")
    await db.bind_session_owner_if_absent("a", owner)
    first = await db.append_message("a", "user", "今天完成了成长闭环自动化测试。")
    second = await db.append_message("a", "user", "今天确认主消息页是唯一真人验收入口。")
    third = await db.append_message("a", "user", "明天要复核成长通知和回滚证据。")
    scope = await db.capture_owner_memory_read_scope("profile-a", 1, 7)
    late = await db.append_message("a", "user", "冻结范围之后的消息不能被读到。")

    writes: list[int] = []

    def observe(connection: sqlite3.Connection) -> None:
        def authorize(action, _arg1, _arg2, _db_name, _trigger):
            if action in {
                sqlite3.SQLITE_INSERT,
                sqlite3.SQLITE_UPDATE,
                sqlite3.SQLITE_DELETE,
            }:
                writes.append(action)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)

    hits = await Retriever(db, object()).recall_readonly(
        "今天聊过的重点",
        10,
        scope,
        connection_observer=observe,
    )

    assert [hit.message_id for hit in hits] == [third, second, first]
    assert {hit.source for hit in hits} == {"readonly_recency_fallback"}
    assert late > scope.as_of_message_id
    assert writes == []


def _detail_notification(store: CompanionStore, owner: OwnerRef) -> None:
    store.record_growth_event(
        GrowthEvent(
            owner=owner,
            event_id="event-1",
            source_kind="run",
            source_ref="run-1",
            context_key="context-1",
            root_run_id="run-1",
            reason_code="verified",
            payload={"authorization": "Bearer secret-secret-secret"},
        )
    )
    store.create_notification(
        owner,
        notification_id="notification-1",
        kind="growth_digest",
        source_refs=("event-1",),
        summary="summary",
        detail={},
        available_actions=(),
    )


@pytest.mark.asyncio
async def test_detail_query_enforces_limits_redaction_owner_and_change_fence(
    tmp_path: Path,
) -> None:
    owner = OwnerRef("profile-a", 1)
    store = CompanionStore(tmp_path / "companion.db")
    store.create_profile(
        profile_id=owner.profile_id,
        generation=owner.profile_generation,
        identity_namespace_hash="identity-a",
    )
    _detail_notification(store, owner)
    frozen = FrozenOwnerIdentity(owner, "companion:profile-a:1", 9)
    query = CompanionDetailQueryPort(
        store=store,
        platform_store=None,
        cursor_secret=b"s" * 32,
    )
    request = {
        "notification_id": "notification-1",
        "section": "evidence",
        "cursor": None,
        "page_size": 20,
        "expected_detail_version": None,
    }
    page = await query.query(
        frozen_identity=frozen,
        control_epoch=3,
        request=request,
    )
    encoded = canonical_json(page)
    assert "secret-secret-secret" not in encoded
    assert page["items"]

    with pytest.raises(CompanionDetailQueryError) as oversized:
        await query.query(
            frozen_identity=frozen,
            control_epoch=3,
            request={**request, "page_size": 21},
        )
    assert oversized.value.code == "unavailable"

    with pytest.raises(CompanionDetailQueryError) as wrong_owner:
        await query.query(
            frozen_identity=FrozenOwnerIdentity(
                OwnerRef("profile-a", 2),
                "companion:profile-a:2",
                10,
            ),
            control_epoch=3,
            request=request,
        )
    assert wrong_owner.value.code == "unavailable"

    original = store.get_detail_version
    calls = 0

    def changed_version(current_owner):
        nonlocal calls
        calls += 1
        if calls == 2:
            store.create_notification(
                owner,
                notification_id="notification-concurrent",
                kind="growth_digest",
                source_refs=(),
                summary="concurrent",
                detail={},
                available_actions=(),
            )
        return original(current_owner)

    store.get_detail_version = changed_version  # type: ignore[method-assign]
    with pytest.raises(CompanionDetailQueryError) as changed:
        await query.query(
            frozen_identity=frozen,
            control_epoch=3,
            request=request,
        )
    assert changed.value.code == "detail_changed"


def test_single_growth_authority_static_gate() -> None:
    db = sqlite3.connect(":memory:")
    initialize_schema(db)
    tables = {
        str(row[0])
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert tables.isdisjoint(FORBIDDEN_AUTHORITY_NAMES)

    production_files = (
        BACKEND / "main.py",
        BACKEND / "pipeline" / "voice_pipeline.py",
        BACKEND / "deskpet" / "agent" / "run_presenter.py",
        BACKEND / "agent" / "agent_loop.py",
        BACKEND / "context.py",
        BACKEND / "agent" / "harness_feedback.py",
    )
    forbidden = (
        "SkillLoader.invoke_script",
        ".invoke_script(",
        "ToolPathRecorder",
        "SkillCodifier",
        "skill_candidate_confirm",
        "skill_candidate_proposed",
    )
    findings = [
        f"{path.relative_to(BACKEND)}:{token}"
        for path in production_files
        for token in forbidden
        if token in path.read_text(encoding="utf-8")
    ]
    assert findings == []
    assert not (BACKEND / "deskpet" / "agent" / "tool_path.py").exists()
    assert not (
        BACKEND / "deskpet" / "skills" / "skill_codifier.py"
    ).exists()
