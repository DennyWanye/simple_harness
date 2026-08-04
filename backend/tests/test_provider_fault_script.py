from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import aiosqlite
import pytest

from deskpet.execution.provider_fault_script import (
    FAULT_SCRIPT_ENV,
    ProviderFaultAction,
    ProviderFaultInjectedError,
    ProviderFaultScriptRejected,
    ProviderFaultScriptV1,
)
from deskpet.execution.provider_workload_audit import ProviderWorkloadAuditStore
from deskpet.execution.provider_fault_scenario_runner import (
    ProviderFaultScenarioRunner,
)
from deskpet.execution.provider_workloads import (
    ProviderWorkloadRouter,
    ResolvedProviderWorkloadTarget,
    workload_context,
)
from deskpet.memory.migrator import run_migrations


def _rule(
    *,
    action: str = "http_401",
    occurrence: int = 1,
    injection_ref: str = "fault-ref-1",
) -> dict[str, object]:
    return {
        "rule_id": f"rule-{injection_ref}",
        "injection_ref": injection_ref,
        "session_id": "session-a",
        "workload_class": "session-auxiliary",
        "callsite_id": "agent.goal_check",
        "purpose": "supervisor",
        "occurrence": occurrence,
        "action": action,
        "retry_after_s": 3.0,
        "model_quota": action == "http_402",
    }


def _write_script(tmp_path: Path, rules: list[dict[str, object]]) -> Path:
    path = tmp_path / "provider-faults.json"
    path.write_text(
        json.dumps({"schema_version": 1, "rules": rules}), encoding="utf-8"
    )
    return path


def _context(*, root: str = "root-a", call_id: str = "call-a"):
    return workload_context(
        "agent.goal_check",
        request_id=f"request-{call_id}",
        session_id="session-a",
        root_run_id=root,
        call_id=call_id,
    )


def test_environment_discovery_rejects_fault_script_in_production(tmp_path: Path):
    path = _write_script(tmp_path, [_rule()])
    with pytest.raises(ProviderFaultScriptRejected, match="DESKPET_DEV_MODE=1"):
        ProviderFaultScriptV1.from_environment(
            {FAULT_SCRIPT_ENV: str(path), "DESKPET_DEV_MODE": "0"}
        )


def test_environment_without_script_is_a_noop_even_in_production():
    assert ProviderFaultScriptV1.from_environment({"DESKPET_DEV_MODE": "0"}) is None


@pytest.mark.parametrize(
    ("action", "status_code", "error_class"),
    [
        ("http_401", 401, "credential_rejected"),
        ("http_402", 402, "insufficient_balance"),
        ("http_429", 429, "rate_limited"),
    ],
)
@pytest.mark.asyncio
async def test_supported_actions_are_provider_shaped_and_consume_once(
    tmp_path: Path,
    action: str,
    status_code: int,
    error_class: str,
):
    script = ProviderFaultScriptV1.from_file(
        _write_script(tmp_path, [_rule(action=action)]),
        environ={"DESKPET_DEV_MODE": "1"},
    )
    match = await script.consume(_context())
    assert match is not None
    error = match.to_exception()
    assert isinstance(error, ProviderFaultInjectedError)
    assert error.action is ProviderFaultAction(action)
    assert error.status_code == status_code
    assert error.error_class == error_class
    assert error.injection_ref == "fault-ref-1"
    assert error.bound_root_id == "root-a"
    assert await script.consume(_context(call_id="call-b")) is None
    assert await script.active_injection_count() == 0


@pytest.mark.asyncio
async def test_occurrence_match_atomically_binds_one_actual_root(tmp_path: Path):
    script = ProviderFaultScriptV1.from_file(
        _write_script(tmp_path, [_rule(occurrence=2)]),
        environ={"DESKPET_DEV_MODE": "1"},
    )
    assert await script.consume(_context(root="root-first", call_id="one")) is None
    match = await script.consume(_context(root="root-bound", call_id="two"))
    assert match is not None
    assert match.bound_root_id == "root-bound"
    assert await script.consume(_context(root="root-late", call_id="three")) is None
    assert await script.snapshot() == (
        {
            "rule_id": "rule-fault-ref-1",
            "injection_ref": "fault-ref-1",
            "seen": 2,
            "bound_root_id": "root-bound",
            "bound_correlation_id": "root-bound",
            "consumed": True,
        },
    )


@pytest.mark.asyncio
async def test_concurrent_first_match_binds_exactly_one_root(tmp_path: Path):
    script = ProviderFaultScriptV1.from_file(
        _write_script(tmp_path, [_rule()]),
        environ={"DESKPET_DEV_MODE": "1"},
    )
    matches = await asyncio.gather(
        *(
            script.consume(_context(root=f"root-{index}", call_id=f"call-{index}"))
            for index in range(20)
        )
    )
    injected = [match for match in matches if match is not None]
    assert len(injected) == 1
    snapshot = await script.snapshot()
    assert snapshot[0]["bound_root_id"] == injected[0].bound_root_id
    assert snapshot[0]["consumed"] is True
    assert await script.active_injection_count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "status_code"),
    [
        ("http_401", 401),
        ("http_402", 402),
        ("http_429", 429),
    ],
)
async def test_router_injection_skips_provider_and_audits_bound_root(
    tmp_path: Path, action: str, status_code: int
):
    db_path = tmp_path / "state.db"
    await run_migrations(db_path)
    script = ProviderFaultScriptV1.from_file(
        _write_script(tmp_path, [_rule(action=action)]),
        environ={"DESKPET_DEV_MODE": "1"},
    )

    class Provider:
        calls = 0

        async def chat_with_tools(self, **_kwargs):
            self.calls += 1
            return {"content": "must-not-run"}

    provider = Provider()

    async def resolve(_context):
        return ResolvedProviderWorkloadTarget(
            provider=provider,
            provider_id="kimi",
            model="kimi-k3",
            incarnation_id="inc-a",
            config_revision=1,
            endpoint="https://relay.invalid/v1",
            account_ref="acct-a",
        )

    router = ProviderWorkloadRouter(
        resolve,
        audit=ProviderWorkloadAuditStore(db_path),
        fault_script=script,
    )
    with pytest.raises(ProviderFaultInjectedError) as captured:
        await router.invoke("prompt", workload_context=_context())
    assert captured.value.status_code == status_code
    assert provider.calls == 0
    async with aiosqlite.connect(db_path) as db:
        row = await (
            await db.execute(
                "SELECT injection_ref,root_run_id,status,error_class,"
                "injection_correlation_hash "
                "FROM provider_workload_audit"
            )
        ).fetchone()
    assert row == (
        "fault-ref-1",
        "root-a",
        "failed",
        "ProviderFaultInjectedError",
        hashlib.sha256(b"root-a").hexdigest(),
    )
    assert await script.active_injection_count() == 0


def test_production_composition_loads_and_passes_the_fault_script():
    source = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
    assert "ProviderFaultScriptV1.from_environment()" in source
    assert "fault_script=_provider_fault_script" in source


@pytest.mark.asyncio
async def test_rootless_system_maintenance_binds_request_correlation_and_autoruns(
    tmp_path: Path,
):
    rule = {
        "rule_id": "maintenance-401",
        "injection_ref": "maintenance-401-ref",
        "session_id": None,
        "workload_class": "system-maintenance",
        "callsite_id": "provider.maintenance_probe",
        "purpose": "auxiliary_unknown",
        "occurrence": 1,
        "action": "http_401",
        "autorun": True,
    }
    script = ProviderFaultScriptV1.from_file(
        _write_script(tmp_path, [rule]), environ={"DESKPET_DEV_MODE": "1"}
    )
    specs = await script.pending_autorun_specs()
    assert len(specs) == 1
    context = workload_context(
        specs[0].callsite_id,
        request_id=specs[0].request_id,
        call_id=specs[0].call_id,
    )
    assert context.session_id is None
    assert context.root_run_id is None
    match = await script.consume(context)
    assert match is not None
    assert match.bound_root_id is None
    assert match.bound_correlation_id == "provider-fault:maintenance-401-ref"
    assert await script.active_injection_count() == 0


@pytest.mark.asyncio
async def test_maintenance_scenario_runner_is_owned_and_audits_null_identity(
    tmp_path: Path,
):
    db_path = tmp_path / "state.db"
    await run_migrations(db_path)
    rule = {
        "rule_id": "maintenance-402",
        "injection_ref": "maintenance-402-ref",
        "session_id": None,
        "workload_class": "system-maintenance",
        "callsite_id": "provider.maintenance_probe",
        "purpose": "auxiliary_unknown",
        "occurrence": 1,
        "action": "http_402",
        "autorun": True,
        "model_quota": False,
    }
    script = ProviderFaultScriptV1.from_file(
        _write_script(tmp_path, [rule]), environ={"DESKPET_DEV_MODE": "1"}
    )

    class Provider:
        calls = 0

        async def chat_with_tools(self, **_kwargs):
            self.calls += 1
            return {"content": "must-not-run"}

    provider = Provider()

    async def resolve(_context):
        return ResolvedProviderWorkloadTarget(
            provider=provider,
            provider_id="maintenance-provider",
            model="maintenance-model",
            incarnation_id="maintenance-incarnation",
            config_revision=1,
            endpoint="https://relay.invalid/v1",
        )

    router = ProviderWorkloadRouter(
        resolve,
        audit=ProviderWorkloadAuditStore(db_path),
        fault_script=script,
    )
    owned: set[asyncio.Task[object]] = set()
    runner = ProviderFaultScenarioRunner(script, router, task_owner=owned)
    assert await runner.start() == 1
    assert len(owned) == 1
    await asyncio.gather(*tuple(owned))
    await asyncio.sleep(0)
    assert not owned
    assert provider.calls == 0
    expected_correlation = hashlib.sha256(
        b"provider-fault:maintenance-402-ref"
    ).hexdigest()
    async with aiosqlite.connect(db_path) as db:
        row = await (
            await db.execute(
                "SELECT session_id,root_run_id,injection_ref,"
                "injection_correlation_hash,status "
                "FROM provider_workload_audit"
            )
        ).fetchone()
    assert row == (
        None,
        None,
        "maintenance-402-ref",
        expected_correlation,
        "failed",
    )


@pytest.mark.asyncio
async def test_child_main_fault_matches_only_actual_child_run(tmp_path: Path):
    rule = {
        "rule_id": "child-main-failure",
        "injection_ref": "child-main-failure-ref",
        "session_id": "session-a",
        "workload_class": "main",
        "callsite_id": "agent.root_turn",
        "purpose": "agent_response",
        "occurrence": 1,
        "action": "child_provider_failure",
        "run_kind": "child",
    }
    script = ProviderFaultScriptV1.from_file(
        _write_script(tmp_path, [rule]), environ={"DESKPET_DEV_MODE": "1"}
    )
    assert await script.consume_main(
        run_id="root-1", session_id="session-a", root_run_id="root-1",
        parent_run_id=None, profile_key="agent.general", purpose="agent_response"
    ) is None
    match = await script.consume_main(
        run_id="run-without-child-prefix",
        session_id="session-a",
        root_run_id="root-1",
        parent_run_id="root-1",
        profile_key="agent.general",
        purpose="agent_response",
    )
    assert match is not None
    assert match.bound_root_id is None
    assert match.bound_correlation_id == "run-without-child-prefix"
    assert await script.consume_main(
        run_id="child-other", session_id="session-a", root_run_id="root-1",
        parent_run_id="root-1", profile_key="agent.general", purpose="agent_response"
    ) is None


@pytest.mark.asyncio
async def test_missing_provider_identity_fields_fail_closed_without_parent_guessing(
    tmp_path: Path,
):
    with pytest.raises(ValueError, match="model is required"):
        ResolvedProviderWorkloadTarget(
            provider=object(), provider_id="kimi", model="",
            incarnation_id="inc-a", config_revision=1,
            endpoint="https://relay.invalid/v1",
        )

    rule = {
        "rule_id": "missing-identity-child",
        "injection_ref": "missing-identity-child-ref",
        "session_id": "session-a",
        "workload_class": "main",
        "callsite_id": "agent.root_turn",
        "purpose": "agent_response",
        "occurrence": 1,
        "action": "child_provider_failure",
        "run_kind": "child",
    }
    script = ProviderFaultScriptV1.from_file(
        _write_script(tmp_path, [rule]), environ={"DESKPET_DEV_MODE": "1"}
    )
    common = {
        "run_id": "child-1",
        "session_id": "session-a",
        "root_run_id": "root-1",
        "profile_key": "agent.general",
    }
    assert await script.consume_main(
        **common, parent_run_id="root-1", purpose=""
    ) is None
    assert await script.consume_main(
        **common, parent_run_id=None, purpose="agent_response"
    ) is None
    assert await script.active_injection_count() == 1


def test_fault_rules_reject_identity_impersonation(tmp_path: Path):
    detached = _rule()
    detached.update(
        workload_class="system-maintenance",
        callsite_id="memory.reflection",
        purpose="memory_summarizer",
    )
    with pytest.raises(ProviderFaultScriptRejected, match="detached"):
        ProviderFaultScriptV1.from_file(
            _write_script(tmp_path, [detached]),
            environ={"DESKPET_DEV_MODE": "1"},
        )
    auxiliary_child = _rule(action="child_provider_failure")
    with pytest.raises(ProviderFaultScriptRejected, match="non-child"):
        ProviderFaultScriptV1.from_file(
            _write_script(tmp_path, [auxiliary_child]),
            environ={"DESKPET_DEV_MODE": "1"},
        )


def test_script_rejects_unknown_fields_and_duplicate_references(tmp_path: Path):
    bad = _rule()
    bad["unexpected"] = True
    with pytest.raises(ProviderFaultScriptRejected, match="shape"):
        ProviderFaultScriptV1.from_file(
            _write_script(tmp_path, [bad]), environ={"DESKPET_DEV_MODE": "1"}
        )
    with pytest.raises(ProviderFaultScriptRejected, match="unique"):
        ProviderFaultScriptV1.from_file(
            _write_script(tmp_path, [_rule(), _rule()]),
            environ={"DESKPET_DEV_MODE": "1"},
        )


@pytest.mark.parametrize("occurrence", [True, "1", 0])
def test_script_rejects_non_integer_or_non_positive_occurrence(
    tmp_path: Path, occurrence: object
):
    rule = _rule()
    rule["occurrence"] = occurrence
    with pytest.raises(ProviderFaultScriptRejected, match="occurrence"):
        ProviderFaultScriptV1.from_file(
            _write_script(tmp_path, [rule]),
            environ={"DESKPET_DEV_MODE": "1"},
        )
