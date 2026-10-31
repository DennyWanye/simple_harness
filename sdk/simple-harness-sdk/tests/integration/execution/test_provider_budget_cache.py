# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""预算快照按行缓存：长期会话的 Agent 调用记录只增不减，每句话不能把全部记录重新解码。

改坏检验：`_provider_budget` 不再查缓存（每行都解码）→ 第一条"第二次读解码 0 行"变红。"""

from __future__ import annotations

from simple_harness.contracts import RequestId, RunId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.execution.budget import BudgetCharge, BudgetChargeKind, BudgetPolicy
from simple_harness.execution.provider_invocations import (
    ProviderInvocationRecord,
    provider_invocation_id,
    provider_request_fingerprint,
    provider_request_json,
)
from simple_harness.execution.sqlite import Database, SqliteExecutionUnitOfWork
from simple_harness.providers import ProviderRequest, ProviderTarget

RUN = RunId("run-1")


def _world(tmp_path):
    uow = SqliteExecutionUnitOfWork(Database.open(tmp_path / "budget.db"))
    uow.create_with_start_snapshot(
        execution_session_id="session-1",
        run_id=RUN.value,
        request_id="root-request",
        profile_key="agent.general",
        driver_kind="react",
        snapshot={"schema_version": 1},
        event_id="run-1:created",
        now=1.0,
    )
    _, lease = uow.claim_runtime_activation(
        run_id=RUN.value,
        owner_id="owner-a",
        namespace="runtime.kernel",
        now=2.0,
        lease_ttl_seconds=100.0,
    )
    return uow, lease


def _claim(uow, lease, ordinal: int) -> ProviderInvocationRecord:
    request = ProviderRequest(
        RequestId(f"provider-turn-{ordinal}"),
        (Message(MessageRole.USER, "hello"),),
        max_output_tokens=10,
    )
    record = ProviderInvocationRecord.claimed(
        invocation_id=provider_invocation_id(RUN, request.request_id),
        run_id=RUN,
        request_id=request.request_id,
        request_fingerprint=provider_request_fingerprint(request),
        target=ProviderTarget("fixture", "model", "fixture:model", "local", "fixture"),
        estimator_snapshot=None,
        estimator_digest=None,
        reservation=BudgetCharge.unknown(),
        claimed_at=10.0 + ordinal,
        request_json=provider_request_json(request),
    )
    # 这里只测快照缓存，不测预算拒绝：允许在途预留未知
    policy = BudgetPolicy(refuse_on_unknown=False)
    return uow.claim_provider_invocation(record, budget_policy=policy, execution_lease=lease)


def _handed_off(uow, lease, ordinal: int) -> ProviderInvocationRecord:
    record = _claim(uow, lease, ordinal)
    return uow.hand_off_provider_invocation(
        record.invocation_id,
        expected_version=record.version,
        handed_off_at=20.0 + ordinal,
        execution_lease=lease,
    )


def _settle_known(uow, record: ProviderInvocationRecord, micros: int) -> ProviderInvocationRecord:
    charge = BudgetCharge(BudgetChargeKind.TRUSTED_USAGE, micros, "price-v1")
    return uow.settle_provider_invocation(
        record.settle_succeeded(
            response_json={"text": "ok"},
            usage_json={
                "usage": {"input_tokens": 1, "output_tokens": 1},
                "budget": charge.to_json(),
            },
            at=30.0,
            expected_version=record.version,
            budget_charge=charge,
        ),
        expected_version=record.version,
    )


def _decodes(monkeypatch) -> list[str]:
    seen: list[str] = []
    original = SqliteExecutionUnitOfWork.read_provider_invocation

    def counting(self, invocation_id: str):
        seen.append(invocation_id)
        return original(self, invocation_id)

    monkeypatch.setattr(SqliteExecutionUnitOfWork, "read_provider_invocation", counting)
    return seen


def test_settled_rows_are_decoded_once_and_the_snapshot_stays_the_same(tmp_path, monkeypatch):
    uow, lease = _world(tmp_path)
    for ordinal in range(3):
        _settle_known(uow, _handed_off(uow, lease, ordinal), 7)
    seen = _decodes(monkeypatch)
    uow._budget_cache.clear()  # 当作刚启动的进程：第一次读要把三行都解码
    first = uow.read_provider_budget(RUN)
    # 读一行原始记录加一次记账回执核对，一行会读两次；看的是哪些行被解码
    assert len(set(seen)) == 3 and first.committed_micros == 21 and not first.has_unknown_charge
    seen.clear()
    assert uow.read_provider_budget(RUN) == first
    assert seen == []  # 结清且用量已知的行不再解码


def test_a_changed_row_is_decoded_again_and_a_row_awaiting_accounting_every_time(
    tmp_path, monkeypatch
):
    uow, lease = _world(tmp_path)
    _settle_known(uow, _handed_off(uow, lease, 0), 7)
    uow.read_provider_budget(RUN)
    seen = _decodes(monkeypatch)
    # 新领取的一行（在途、预留未知）：第一次读解码，第二次不解码；交付后 version 变了再解码
    # （写操作自己也会读记录，所以每次只看预算读之前清空后的那段）
    claimed = _claim(uow, lease, 1)
    seen.clear()
    snapshot = uow.read_provider_budget(RUN)
    assert set(seen) == {claimed.invocation_id} and snapshot.has_unknown_charge
    seen.clear()
    uow.read_provider_budget(RUN)
    assert seen == []
    uow.hand_off_provider_invocation(
        claimed.invocation_id,
        expected_version=claimed.version,
        handed_off_at=21.0,
        execution_lease=lease,
    )
    seen.clear()
    uow.read_provider_budget(RUN)
    assert set(seen) == {claimed.invocation_id}
    # 结清但没有用量的行还等着记账回执：不进缓存，每次都解码（回执一到就要算进来）
    handed = _handed_off(uow, lease, 2)
    pending = uow.settle_provider_invocation(
        handed.settle_succeeded(
            response_json={"text": "ok"},
            usage_json={"usage": None, "budget": BudgetCharge.unknown().to_json()},
            at=31.0,
            expected_version=handed.version,
        ),
        expected_version=handed.version,
    )
    for _ in range(2):
        seen.clear()
        assert uow.read_provider_budget(RUN).has_unknown_charge
        assert pending.invocation_id in seen
