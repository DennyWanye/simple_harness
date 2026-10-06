# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0

"""Goal-text normalisation shared by the allocator's duplication score.

The proposal-level duplicate finder that used to live here had no product caller
(第 2 批 A24：无调用方的旧路径直接删); duplicate work is the planner's and the
reviewer's judgment, not a text-signature rule.
"""

from __future__ import annotations

import re
import unicodedata


def normalise_goal(goal: str) -> str:
    text = unicodedata.normalize("NFKC", goal).lower()
    text = re.sub(r"[\s\-_:：，,。.;；、（）()\[\]【】\"'`]+", "", text)
    return text


__all__ = ("normalise_goal",)
