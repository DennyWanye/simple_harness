# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""STUB — embedder_worker。

原模块已移除，等待 simple-harness-memory-sdk 集成。
此 stub 防止 frozen_worker_dispatch.py 在运行时崩溃。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def main(argv: list[str]) -> int:
    """STUB: embedder worker 已移除，返回错误码。"""
    logger.error(
        "embedder_worker.main called but memory SDK not integrated yet. "
        "argv=%s", argv
    )
    return 1
