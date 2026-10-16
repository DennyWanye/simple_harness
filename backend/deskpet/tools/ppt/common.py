# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Private helpers shared by several families."""

from __future__ import annotations

# ---------------------------------------------------------------------
# python-pptx availability — defer the import so callers without the
# dep get a graceful fallback instead of an ImportError at module load.
# ---------------------------------------------------------------------
try:  # pragma: no cover — import probe
    from pptx import Presentation as _Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
    from pptx.enum.dml import MSO_FILL_TYPE
    from pptx.enum.shapes import MSO_SHAPE, PP_PLACEHOLDER
    from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
    from pptx.util import Emu, Inches, Pt

    _HAS_PPTX = True
except ImportError:  # pragma: no cover
    _Presentation = None  # type: ignore
    Inches = Pt = Emu = RGBColor = MSO_SHAPE = PP_PLACEHOLDER = PP_ALIGN = MSO_ANCHOR = (
        MSO_FILL_TYPE
    ) = MSO_AUTO_SIZE = None  # type: ignore
    CategoryChartData = XL_CHART_TYPE = XL_LEGEND_POSITION = None  # type: ignore
    _HAS_PPTX = False
