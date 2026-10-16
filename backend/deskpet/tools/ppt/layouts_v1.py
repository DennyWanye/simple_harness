# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""First-generation slide renderers."""

from __future__ import annotations

import logging


log = logging.getLogger("deskpet.tools.ppt_tools")  # 拆包后仍用原模块名，日志过滤与告警规则不变

# ---- Per-layout renderers ------------------------------------------


def _normalize_chart_axis_ids(chart) -> None:
    """Keep axis references valid for strict Open XML consumers.

    python-pptx may generate signed 32-bit axis IDs. PowerPoint accepts those
    negative values, but the Open XML field is UInt32 and strict readers reject
    the deck. The unsigned representation preserves identity and cross-links.
    """

    try:
        for node in chart._chartSpace.xpath(".//c:axId | .//c:crossAx"):
            raw = int(node.get("val"))
            if raw < 0:
                node.set("val", str(raw & 0xFFFFFFFF))
    except Exception as exc:  # noqa: BLE001
        log.warning("chart axis id normalization failed: %s", exc, exc_info=True)
