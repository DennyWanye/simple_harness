# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""配置里不留没有读取方的压缩开关（压缩总是开）。"""

from __future__ import annotations


def test_config_has_no_dead_compaction_flag():
    """``compaction_enabled`` 没有任何读取方，写着"开"却不生效的假开关已删除。"""
    from config import AppConfig

    cfg = AppConfig()
    assert not hasattr(cfg.features, "compaction_enabled")
