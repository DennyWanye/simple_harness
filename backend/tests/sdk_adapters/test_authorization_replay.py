# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""架构方案 A（2026-09-30）：重启后重做同一次工具调用，沿用原授权或具名拒绝。

此前：重放重新签发票据，票据编号确定不变、只有过期时间变了，指纹一变授权身份就变，
授权记录按"同一调用、不同授权"判冲突（旧记录被隔离、运行失败）。现在：授权记录里已有
这条调用时，适配器先按记录判定——终态具名拒绝、策略代数变了具名拒绝、票据过期具名
拒绝并作废——其余用记录里那张票据恢复冻结事实，身份与首次完全相同。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from simple_harness import CallId, EffectId, RunId
from simple_harness.tools import AuthorizationDecision, PreparedToolEffect, ToolCall, ToolSpec
from test_tool_authority import _AuthorizationStore, _prepare

from deskpet.permissions.runtime import PreparedAuthorizationRuntime
from deskpet.product_state.authorization_saga import (
    AuthorizationSagaRepository,
    AuthorizationSagaState,
    SagaConflict,
)
from deskpet.product_state.database import ProductStateDatabase
from deskpet.product_state.task_grants import DurableTaskGrantAuthority
from deskpet.sdk_adapters.authorization import ProductAuthorizationAdapter
from deskpet.sdk_adapters.tool_authority import (
    SdkPreparedAuthorizationPolicy,
    SdkRunToolAuthorityRegistry,
)

EIGHT_HOURS = 8 * 60 * 60.0


def _effect(path: str = "/tmp/input.txt") -> PreparedToolEffect:
    return PreparedToolEffect(
        EffectId("effect-a"), RunId("run-a"),
        ToolCall(CallId("call-a"), "read_file", {"path": path}),
        ToolSpec("read_file", "Read one file", {
            "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}),
        {"session_id": "session-a", "root_run_id": "root-run-a"},
    )


class _Process:
    """One process lifetime: its own registry + policy + adapter over the shared durable state."""

    def __init__(self, database: ProductStateDatabase, *, now: float, generation: int = 0) -> None:
        self.authorities = SdkRunToolAuthorityRegistry()
        _prepare(self.authorities, "run-a")
        self.policy = SdkPreparedAuthorizationPolicy(
            PreparedAuthorizationRuntime(_AuthorizationStore("auto", generation), clock=lambda: now),
            self.authorities, clock=lambda: now, initial_policy_generation=generation,
        )
        self.repository = AuthorizationSagaRepository(database, owner_id="test-sdk")
        self.grants = DurableTaskGrantAuthority(
            database, policy_generation_provider=self.policy.current_policy_generation)
        self.adapter = ProductAuthorizationAdapter(
            self.repository, policy=self.policy, identity_factory=self.policy.identity_factory,
            grant_authority=self.grants, grant_factory=self.policy.grant_factory, clock=lambda: now,
        )


def _database(tmp_path: Path) -> ProductStateDatabase:
    database = ProductStateDatabase(tmp_path / "product.db")
    database.initialize()
    return database


@pytest.mark.asyncio
async def test_replay_after_restart_reuses_the_frozen_grant_and_identity(tmp_path: Path) -> None:
    database = _database(tmp_path)
    first = _Process(database, now=100.0)
    assert (await first.adapter.prepare(_effect())).decision is AuthorizationDecision.ALLOW
    before = first.repository.read_for_effect("effect-a", "call-a")
    grant = first.policy.facts_for(_effect()).grant

    # 重启：新进程、新的运行授权（同一目录），时钟走了一段但票据没过期
    second = _Process(database, now=100.0 + 3600.0)
    result = await second.adapter.prepare(_effect())

    assert result.decision is AuthorizationDecision.ALLOW
    restored = second.policy.facts_for(_effect()).grant
    assert restored == grant and restored.expires_at == grant.expires_at
    after = second.repository.read_for_effect("effect-a", "call-a")
    assert after.identity.authorization_id == before.identity.authorization_id
    assert after.state is not AuthorizationSagaState.QUARANTINED
    assert second.policy.identity_factory(_effect(), None).authorization_id == before.identity.authorization_id
    assert second.grants.read(grant.task_grant_id).status == "active"


@pytest.mark.asyncio
async def test_replay_with_an_expired_grant_is_refused_by_name_and_the_record_closed(tmp_path: Path) -> None:
    database = _database(tmp_path)
    first = _Process(database, now=100.0)
    await first.adapter.prepare(_effect())
    grant_id = first.policy.facts_for(_effect()).grant.task_grant_id

    second = _Process(database, now=100.0 + EIGHT_HOURS + 1.0)
    result = await second.adapter.prepare(_effect())

    assert result.decision is AuthorizationDecision.DENY and result.reason_code == "grant_expired"
    assert second.repository.read_for_effect("effect-a", "call-a").state is AuthorizationSagaState.ABORTED
    assert second.grants.read(grant_id).status == "expired"


@pytest.mark.asyncio
async def test_replay_of_a_terminal_record_is_refused_by_name_without_a_new_grant(tmp_path: Path) -> None:
    database = _database(tmp_path)
    first = _Process(database, now=100.0)
    await first.adapter.prepare(_effect())
    record = first.repository.read_for_effect("effect-a", "call-a")
    first.repository.abort(record.identity.authorization_id, expected_version=record.version,
                           reason_hash="a" * 64, now=101.0)

    second = _Process(database, now=200.0)
    result = await second.adapter.prepare(_effect())

    assert result.decision is AuthorizationDecision.DENY
    assert result.reason_code == "authorization_replay_refused:aborted"
    with pytest.raises(RuntimeError, match="facts are unavailable"):
        second.policy.facts_for(_effect())
    assert second.repository.read_for_effect("effect-a", "call-a").state is AuthorizationSagaState.ABORTED


@pytest.mark.asyncio
async def test_replay_under_a_moved_policy_generation_is_refused_by_name(tmp_path: Path) -> None:
    database = _database(tmp_path)
    first = _Process(database, now=100.0, generation=0)
    await first.adapter.prepare(_effect())

    second = _Process(database, now=200.0, generation=1)
    result = await second.adapter.prepare(_effect())

    assert result.decision is AuthorizationDecision.DENY
    assert result.reason_code == "policy_generation_moved"


@pytest.mark.asyncio
async def test_a_different_call_under_the_same_ids_is_still_refused_by_identity(tmp_path: Path) -> None:
    database = _database(tmp_path)
    first = _Process(database, now=100.0)
    await first.adapter.prepare(_effect())

    second = _Process(database, now=200.0)
    with pytest.raises(SagaConflict):
        await second.adapter.prepare(_effect(path="/tmp/other.txt"))
