# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Reuse the existing real Commit Service graph fixture."""

import sys
from pathlib import Path

STEP07 = Path(__file__).resolve().parents[1] / "step07"
if str(STEP07) not in sys.path:
    sys.path.insert(0, str(STEP07))

# 部分 P3.5 用例直接复用 P3.3 的真实运行用例（test_p33_*_runtime）；单独跑 p35 时
# pytest 不会把 p33 目录放进 sys.path，这里显式加上。
P33 = Path(__file__).resolve().parents[1] / "p33"
if str(P33) not in sys.path:
    sys.path.append(str(P33))
