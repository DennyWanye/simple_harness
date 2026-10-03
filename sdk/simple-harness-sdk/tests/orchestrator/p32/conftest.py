# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""P3.2 tests that need a running Mission build it on the product deployment
(``full_target/h1i_seed.run_until``；HTN 补齐阶段 A′，叶子世界已退役)."""

from __future__ import annotations

import sys
from pathlib import Path

FULL_TARGET = Path(__file__).resolve().parents[1] / "full_target"
if str(FULL_TARGET) not in sys.path:
    sys.path.append(str(FULL_TARGET))
