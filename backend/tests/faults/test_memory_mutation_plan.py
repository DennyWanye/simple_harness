# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
"""fault-matrix lane `memory-mutation-plan` runner（S5b Task 0 骨架；oracle 先于实现）。

terminal oracle 见 fixtures/fault-matrix.json；本文件在对应 Task 实装前保持 strict xfail，
runner_contract 输出由 `_runner_contract.emit` 统一产生。
"""

from __future__ import annotations

import pytest

from tests.faults._runner_contract import LANE_SEAMS

LANE = "memory-mutation-plan"


@pytest.mark.parametrize("seam", LANE_SEAMS[LANE])
@pytest.mark.xfail(strict=True, reason="S5b 实装前：seam 注入点尚未接线（NOT_IMPLEMENTED）")
def test_seam_kill_replay_converges(seam: str) -> None:
    raise NotImplementedError(f"{LANE}:{seam}")
