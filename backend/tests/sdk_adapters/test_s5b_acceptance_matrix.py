# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""S5b 黑盒验收矩阵骨架（Task 0，oracle 先于实现）。

每个用例的断言口径来自 acceptance S5B-AC-1/2/3/6 与 design-freeze.md；实装前保持 strict xfail，
实装某条后把对应 xfail 去掉——禁止照实现改断言。
"""

from __future__ import annotations

import pytest

XF = pytest.mark.xfail(strict=True, reason="S5b 实装前占位（NOT_IMPLEMENTED）")


# ---- S5B-AC-3 / Task 1：effect gate 最小闭环 ----
@XF
def test_project_effect_list_frozen_and_route_required() -> None:
    """design-freeze §1：清单内每个工具 policy=(project_effect, required, required)，读取类不变。"""
    raise NotImplementedError


@XF
def test_write_file_envelope_reverified_then_executed_and_host_file_event() -> None:
    """ROUTED_TASK 单 root：write_file 经 EffectGate 重验通过 → execution_effects 行 + host.file 事件同事务。"""
    raise NotImplementedError


@XF
def test_reroute_to_other_scope_then_write_rejected_frozen_scope_mismatch() -> None:
    raise NotImplementedError


@XF
def test_standalone_route_project_effect_is_run_fault_with_stable_code() -> None:
    """standalone 下写工具不在 snapshot tools；强制调用 → durable FAILED + sdk_task_execution_route_authority_missing。"""
    raise NotImplementedError


# ---- S5B-AC-1 / Task 2-3：客观事件、脏标记、终态门、兜底 ----
@XF
def test_material_event_sets_dirty_and_terminal_gate_requires_closure_receipt() -> None:
    raise NotImplementedError


@XF
def test_harness_evidence_reservations_drained_before_run_terminal_no_row_loss() -> None:
    """probe A9 场景：迟到 seq 不再被 terminal 永久拒绝。"""
    raise NotImplementedError


@XF
def test_task_scope_update_mutate_closes_and_no_mutation_requires_reason() -> None:
    raise NotImplementedError


@XF
def test_missed_call_fallback_invokes_once_and_unknown_never_resends() -> None:
    raise NotImplementedError


@XF
def test_pending_closure_belongs_to_admission_scope_and_next_run_merges() -> None:
    raise NotImplementedError


# ---- S5B-AC-2 / Task 4：终态同事务 outbox、analysis executor、幂等 ----
@XF
def test_terminal_commit_writes_ingestion_outbox_same_tx() -> None:
    raise NotImplementedError


@XF
def test_analysis_executor_three_key_lookup_zero_second_provider_call() -> None:
    raise NotImplementedError


@XF
def test_analysis_proposal_span_derivation_rejects_paraphrase() -> None:
    raise NotImplementedError


# ---- S5B-AC-6 / Task 6：composition、cutover ----
@XF
def test_composition_missing_piece_startup_fail_each() -> None:
    raise NotImplementedError


@XF
def test_v46_forward_migration_and_old_runtime_rejects() -> None:
    raise NotImplementedError
