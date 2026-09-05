"""Actual production authorization policy + installed SDK + authenticated Primary API.

No provider transport, no App, no fabricated durable decision. Credentials only
travel in memory; assertions/logs never dump request/decision payloads.
"""

import asyncio
import sqlite3
import time
from dataclasses import replace

import pytest
from deskpet.memory.control_binding import HumanMemoryControlBinding
from deskpet.memory.human_memory_api import handle_human_memory_command
from deskpet.memory.human_memory_service import QueueTurnRequest
from deskpet.permissions.runtime import PreparedAuthorizationRuntime
from deskpet.product_state.authorization_saga import AuthorizationSagaRepository
from deskpet.product_state.database import ProductStateDatabase
from deskpet.product_state.task_grants import DurableTaskGrantAuthority
from deskpet.sdk_adapters.authorization import ProductAuthorizationAdapter
from deskpet.sdk_adapters.tool_authority import SdkPreparedAuthorizationPolicy
from simple_harness_memory.core.suppression import SuppressionResolution
from tests.companion.test_window_control_credentials import _ingress
from tests.execution.test_primary_create_new_runtime import CreateProvider, fixture
from tests.execution.test_primary_foreground_runtime import build
from tests.memory.test_primary_control_binding import _bind


async def setup(tmp_path, *, expired=False, wake=False):
    state, factory, service, configured, authority = await fixture(tmp_path)
    product = ProductStateDatabase(tmp_path / "product.db")
    product.initialize()
    policy_state = await authority._policy.get_policy_state()

    def authorization(registry):
        policy = SdkPreparedAuthorizationPolicy(
            PreparedAuthorizationRuntime(authority._policy),
            registry,
            initial_policy_generation=policy_state.generation,
            clock=(lambda: time.time() - 600) if expired else time.time,
        )
        return ProductAuthorizationAdapter(
            AuthorizationSagaRepository(product, owner_id="primary-test"),
            policy=policy,
            identity_factory=policy.identity_factory,
            grant_authority=DurableTaskGrantAuthority(
                product, policy_generation_provider=policy.current_policy_generation
            ),
            grant_factory=policy.grant_factory,
        )

    provider = CreateProvider()
    runtime, stack, queue = await build(
        tmp_path,
        state,
        provider,
        dynamic=True,
        binding_authority=authority,
        configured_root=configured,
        authorization_factory=authorization,
    )
    private, _, _, control = _ingress(tmp_path / "control")
    connection = HumanMemoryControlBinding()
    challenge = await _bind(private, control, connection)
    auth = connection.authenticate(control, challenge)
    assert auth.subject == service._auth.subject
    disclosures = []

    async def history_checker(**kwargs):
        disclosures.append(kwargs["disclosure_context"])
        return await runtime.history_policy.checker(**kwargs)

    factory = replace(
        factory,
        decision_ingress_getter=lambda: runtime._ingress,
        history_visibility_checker=history_checker,
        run_binding_reader=lambda rid: stack.read_closure_run_facts(rid).binding_record,
        suppression_resolver=lambda *_: SuppressionResolution(False, (), 1.0),
    )
    await service.enqueue_turn(
        QueueTurnRequest(
            None, "authorized-create", "Create a project and write its file"
        )
    )
    await asyncio.wait_for(runtime._drive_once(), 10)
    current = await queue.current_snapshot(auth.subject)
    assert runtime._ingress.query(current.sdk_run_id).state.value == "waiting"
    target = {
        "primary_ref": current.primary_conversation_id,
        "expected_run_ref": current.host_run_id,
        "expected_generation": current.generation,
    }

    async def request(operation, fields):
        with connection.request_scope(control, challenge):
            return await handle_human_memory_command(
                {
                    "type": "human_memory_request",
                    "request_id": "bounded-request",
                    "operation": operation,
                    "request": fields,
                },
                factory=factory,
                auth=auth,
                scheduler_wake=runtime if wake else None,
            )

    return locals()


async def close(s):
    await s["runtime"].close()
    await s["stack"].close()
    s["product"].connection.close()


async def pending(s):
    response = await s["request"]("primary.decisions.list", s["target"])
    assert response["payload"]["ok"], response["payload"].get("error", {}).get("code")
    return response["payload"]["result"]["pending"]


def reply(s, item, decision="allow"):
    return {
        **s["target"],
        **{k: item[k] for k in ("decision_id", "nonce", "version")},
        "decision": decision,
    }


@pytest.mark.asyncio
async def test_production_challenge_exact_bound_response_resume_and_physical_effect(
    tmp_path,
):
    s = await setup(tmp_path, wake=True)
    try:
        first = (await pending(s))[0]
        assert s["disclosures"]
        assert all(
            context.run_id == "bounded-request" and context.subject == s["auth"].subject
            for context in s["disclosures"]
        )
        assert first["params"]["tool_name"] == "context_route"
        assert "create_new" in first["params"]["arguments_preview"]
        with sqlite3.connect(s["state"]) as db:
            assert db.execute("SELECT COUNT(*) FROM task_scopes").fetchone()[0] == 0
        # Actual decision is discoverable only through exact bound projection.
        seen = set()
        for _ in range(8):
            items = await pending(s)
            if not items:
                break
            for item in items:
                assert item["decision_id"] not in seen
                seen.add(item["decision_id"])
                response = await s["request"](
                    "primary.decisions.respond", reply(s, item)
                )
                assert response["payload"]["ok"], (
                    response["payload"].get("error", {}).get("code")
                )
                assert response["payload"]["result"]["outcome"] == "allowed"
            await asyncio.sleep(0)
            await s["runtime"]._ingress.wait_idle(s["current"].sdk_run_id)
            if len(seen) == 1:
                before_calls = len(s["provider"].requests)
                replay = await s["request"](
                    "primary.decisions.respond", reply(s, first)
                )
                assert replay["payload"]["ok"]
                assert replay["payload"]["result"]["duplicate"] is True
                assert len(s["provider"].requests) == before_calls
                wrong_version = reply(s, first)
                wrong_version["version"] += 1
                rejected = await s["request"](
                    "primary.decisions.respond", wrong_version
                )
                assert rejected["payload"]["ok"] is False
            await asyncio.wait_for(s["runtime"].drain(), 10)
            assert s["runtime"].last_error is None
            if (
                s["runtime"]._ingress.query(s["current"].sdk_run_id).state.value
                == "completed"
            ):
                break
        assert len(seen) >= 2  # Sequential production challenges, not an ALLOW stub.
        assert (
            s["runtime"]._ingress.query(s["current"].sdk_run_id).state.value
            == "completed"
        )
        files = list(s["configured"].glob("task-*/fresh.txt"))
        assert len(files) == 1 and files[0].read_text() == "created in exact task root"
        assert len(s["provider"].requests) == 7
        await asyncio.wait_for(s["runtime"].drain(), 10)
        assert await s["queue"].current_snapshot(s["auth"].subject) is None
    finally:
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "foreign_primary",
        "foreign_run",
        "generation",
        "boolean_generation",
        "nonce",
        "version",
        "boolean_version",
        "foreign_decision",
        "session_allow",
    ],
)
async def test_invalid_exact_request_never_changes_real_decision(tmp_path, mutation):
    s = await setup(tmp_path)
    try:
        item = (await pending(s))[0]
        fields = reply(s, item)
        key, value = {
            "foreign_primary": ("primary_ref", "other-primary"),
            "foreign_run": ("expected_run_ref", "other-run"),
            "generation": ("expected_generation", fields["expected_generation"] + 1),
            "boolean_generation": ("expected_generation", True),
            "nonce": ("nonce", "not-the-challenge"),
            "version": ("version", fields["version"] + 1),
            "boolean_version": ("version", False),
            "foreign_decision": ("decision_id", "other-decision"),
            "session_allow": ("decision", "allow_session"),
        }[mutation]
        fields[key] = value
        result = await s["request"]("primary.decisions.respond", fields)
        assert result["payload"]["ok"] is False
        actual = s["runtime"]._ingress.read_authorization_decision(
            run_id=s["current"].sdk_run_id, decision_id=item["decision_id"]
        )
        assert actual.state.value == "open"
        assert len(s["provider"].requests) == 1
        assert not list(s["configured"].glob("task-*"))
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_real_deny_has_no_route_effect_and_exact_replay_is_not_new_decision(
    tmp_path,
):
    s = await setup(tmp_path)
    try:
        item = (await pending(s))[0]
        fields = reply(s, item, "deny")
        first = await s["request"]("primary.decisions.respond", fields)
        assert first["payload"]["ok"]
        assert first["payload"]["result"]["outcome"] == "denied"
        retry = await s["request"]("primary.decisions.respond", fields)
        assert retry["payload"]["ok"]
        assert retry["payload"]["result"]["duplicate"] is True
        assert not await pending(s)
        assert len(s["provider"].requests) == 1
        assert not list(s["configured"].glob("task-*"))
    finally:
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["rebind", "stop", "foreign_subject"])
async def test_last_admission_rechecks_auth_and_host_target_after_slow_read(
    tmp_path, monkeypatch, race
):
    s = await setup(tmp_path)
    try:
        from deskpet.execution.foreground_queue import ControlKind
        from deskpet.memory.primary_read_model import PrimaryReadModel

        original = PrimaryReadModel.state
        item = (await pending(s))[0]

        async def changed(reader, **kwargs):
            result = await original(reader, **kwargs)
            if race == "rebind":
                replacement = HumanMemoryControlBinding()
                await _bind(s["private"], s["control"], replacement)
            elif race == "stop":
                await s["queue"].request_control(
                    host_run_id=s["current"].host_run_id,
                    subject=s["auth"].subject,
                    generation=s["current"].generation,
                    control_kind=ControlKind.STOP,
                    reason="explicit test stop",
                    idempotency_key="race-stop",
                )
            return result

        monkeypatch.setattr(PrimaryReadModel, "state", changed)
        if race == "foreign_subject":
            # A different authenticated subject cannot select another Primary.
            foreign = replace(s["auth"], subject="other-subject")
            result = await handle_human_memory_command(
                {
                    "type": "human_memory_request",
                    "request_id": "foreign",
                    "operation": "primary.decisions.respond",
                    "request": reply(s, item),
                },
                factory=s["factory"],
                auth=foreign,
            )
        else:
            result = await s["request"]("primary.decisions.respond", reply(s, item))
        assert result["payload"]["ok"] is False
        actual = s["runtime"]._ingress.read_authorization_decision(
            run_id=s["current"].sdk_run_id, decision_id=item["decision_id"]
        )
        assert actual.state.value == "open"
        assert len(s["provider"].requests) == 1
        assert not list(s["configured"].glob("task-*"))
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_cancelled_decision_cannot_be_approved_and_legacy_primary_target_is_fenced(
    tmp_path,
):
    s = await setup(tmp_path)
    try:
        from deskpet.memory.primary_decisions import is_primary_sdk_target

        item = (await pending(s))[0]
        assert is_primary_sdk_target(s["state"], s["current"].sdk_run_id)
        assert is_primary_sdk_target(s["state"], s["current"].host_run_id)
        assert not is_primary_sdk_target(s["state"], "legacy-unrelated")
        await s["runtime"]._ingress.cancel(s["current"].sdk_run_id)
        response = await s["request"]("primary.decisions.respond", reply(s, item))
        assert response["payload"]["ok"] is False
        assert not list(s["configured"].glob("task-*"))
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_committed_deny_ack_survives_wake_failure(tmp_path):
    s = await setup(tmp_path)
    try:

        class BrokenWake:
            async def after_control(self, **_):
                raise RuntimeError("wake unavailable")

        item = (await pending(s))[0]
        with s["connection"].request_scope(s["control"], s["challenge"]):
            response = await handle_human_memory_command(
                {
                    "type": "human_memory_request",
                    "request_id": "wake",
                    "operation": "primary.decisions.respond",
                    "request": reply(s, item, "deny"),
                },
                factory=s["factory"],
                auth=s["auth"],
                scheduler_wake=BrokenWake(),
            )
        assert response["payload"]["ok"]
        assert response["payload"]["result"]["outcome"] == "denied"
        assert not await pending(s)
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_expired_real_policy_challenge_reports_expired_not_allowed(tmp_path):
    s = await setup(tmp_path, expired=True)
    try:
        item = (await pending(s))[0]
        response = await s["request"]("primary.decisions.respond", reply(s, item))
        assert response["payload"]["ok"]
        assert response["payload"]["result"]["outcome"] == "expired"
        assert (
            s["runtime"]._ingress.query(s["current"].sdk_run_id).state.value == "failed"
        )
        assert not list(s["configured"].glob("task-*"))
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_actual_legacy_signal_entry_cannot_bypass_primary_authentication(
    tmp_path, monkeypatch
):
    s = await setup(tmp_path)
    try:
        monkeypatch.setenv("DESKPET_USER_DATA_DIR", str(tmp_path / "unused-app-data"))
        import main

        item = (await pending(s))[0]
        monkeypatch.setattr(main, "_state_db_path", s["state"])
        monkeypatch.setattr(main, "_sdk_ingress", s["runtime"]._ingress)
        monkeypatch.setattr(main, "_sdk_run_ids_by_root", {})
        fields = {**item, "run_id": s["current"].sdk_run_id}
        with pytest.raises(
            ValueError, match="primary_decision_requires_authenticated_control"
        ):
            await main._signal_product_harness_decision(
                item["session_id"], fields, {"decision": "allow"}, authorization=True
            )
        actual = s["runtime"]._ingress.read_authorization_decision(
            run_id=s["current"].sdk_run_id, decision_id=item["decision_id"]
        )
        assert actual.state.value == "open"
        assert len(s["provider"].requests) == 1
    finally:
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["primary.decisions.list", "primary.decisions.respond"]
)
async def test_forgotten_current_user_cannot_disclose_or_grant_pending_decision(
    tmp_path, operation
):
    from simple_harness_memory import SuppressionRequest, SuppressionScopeKind

    s = await setup(tmp_path)
    try:
        item = (await pending(s))[0]
        with sqlite3.connect(s["state"]) as db:
            evidence = db.execute(
                "SELECT t.evidence_id FROM foreground_turns t JOIN foreground_runs r "
                "ON r.turn_id=t.turn_id WHERE r.host_run_id=?",
                (s["current"].host_run_id,),
            ).fetchone()[0]
        memory = s["runtime"].history_memory
        manager = await memory.manager()
        await manager.suppress(
            principal=memory.principal(),
            request=SuppressionRequest(
                "forget-pending-user",
                s["auth"].subject,
                SuppressionScopeKind.EVIDENCE,
                evidence,
                "user_forget",
                time.time(),
            ),
        )
        fields = s["target"] if operation.endswith("list") else reply(s, item)
        response = await s["request"](operation, fields)
        assert response["payload"]["ok"] is False
        assert response["payload"]["error"]["code"] == "primary_decision_target_stale"
        assert "result" not in response["payload"]
        decision = s["runtime"]._ingress.read_authorization_decision(
            run_id=s["current"].sdk_run_id, decision_id=item["decision_id"]
        )
        assert decision.state.value == "open"
        assert len(s["provider"].requests) == 1
        with sqlite3.connect(s["state"]) as db:
            assert db.execute("SELECT COUNT(*) FROM task_scopes").fetchone()[0] == 0
    finally:
        await close(s)
