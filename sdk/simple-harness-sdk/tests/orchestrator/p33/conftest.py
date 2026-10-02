# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P3.3 tests reuse the step-7 ledger helpers (one ACTIVE Mission, a Task, the people
who decide) the same way the P3.2 suite does."""

from __future__ import annotations

import sys
from pathlib import Path

STEP07 = Path(__file__).resolve().parents[1] / "step07"
if str(STEP07) not in sys.path:
    sys.path.insert(0, str(STEP07))

# 删旧平面模式 第三刀：只需一个任务行的测试迁到分层世界，夹具在 full_target/leaf_world.py。
FULL_TARGET = Path(__file__).resolve().parents[1] / "full_target"
if str(FULL_TARGET) not in sys.path:
    sys.path.append(str(FULL_TARGET))
