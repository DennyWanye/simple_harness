# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""Assurance delta registered as migration 26 in the isolated merged candidate.

HTN descriptors 1–24 and TaskGraph descriptor 25 retain their original bytes.
This candidate is not installed in the shared Host; release identity is still WIP.
Use the existing Store runner; never executescript inside a Store transaction.
"""

from importlib.resources import files

from .assurance_upgrade import DDL as UPGRADE_DDL

# The import barrier is frozen as the literal text migration 26 shipped with
# (删旧平面模式第三刀第 5 步): its generator read the live source-table inventory, so
# shrinking the inventory would have rewritten an already-applied migration.
DDL = (
    files(__package__).joinpath("assurance_schema.sql").read_text(encoding="utf-8")
    + "\n"
    + files(__package__).joinpath("assurance_barrier_v26.sql").read_text(encoding="utf-8")
    + "\n"
    + UPGRADE_DDL
)
