# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: Apache-2.0
"""人对一份"判不下来"的正式审阅记录的裁决（2026-09-30，审阅升级）。

正式记录不可改：两次审阅都判不下来时记录的结论仍是 INCONCLUSIVE，人的裁决写成回执
``AssuranceReviewAdjudicated``（主体 = 记录 id）。凡是拿"记录结论是不是通过"当门槛的地方，
都从这里问一句"人是否已裁决通过"——证书、验收公式、内容审阅、完成度读取各处口径一致。
"""
from __future__ import annotations

from typing import Any

from ..assurance.codec import decode
from ..contracts.resolution import ReviewRecord, ReviewVerdict

ADJUDICATED = "AssuranceReviewAdjudicated"


def adjudication_of(store: Any, record_id: str) -> dict[str, Any] | None:
    """The person's recorded ruling on this official record, or None."""
    row = store.connection.execute(
        "SELECT receipt_json FROM commit_receipts WHERE kind=? AND subject_id=?",
        (ADJUDICATED, str(record_id)),
    ).fetchone()
    return None if row is None else decode(row[0])


def adjudicated_pass(store: Any, record: ReviewRecord) -> bool:
    """An INCONCLUSIVE official record the person passed as a whole."""
    if record.verdict is not ReviewVerdict.INCONCLUSIVE:
        return False
    ruling = adjudication_of(store, str(record.record_id))
    return ruling is not None and ruling.get("decision") == "pass"


def accepted_or_adjudicated(store: Any, record: ReviewRecord) -> bool:
    """The reviewers accepted it, or they could not decide and the person passed it."""
    return record.verdict is ReviewVerdict.ACCEPT or adjudicated_pass(store, record)


__all__ = ("ADJUDICATED", "accepted_or_adjudicated", "adjudicated_pass", "adjudication_of")
