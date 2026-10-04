# SPDX-License-Identifier: Apache-2.0
"""安全层拒绝之后"什么都没发生"的四件事实（联测 F2，原 TaskGraph 计划 §15）。

一次被拒绝的计划改动，不能只断言"报了拒绝"：还要看库里——没有新增计划版本、没有新的派发意图、
没有新的对外交接、没有预留被放掉。拒绝前后各取一次，逐项相等。
"""
from __future__ import annotations

from typing import Any


def safety_facts(store: Any, mission_id: str) -> dict[str, Any]:
    connection = store.connection
    return {
        "plan_revisions": [row[0] for row in connection.execute(
            "SELECT revision FROM plan_revisions WHERE mission_id=? ORDER BY revision", (mission_id,))],
        "attempt_intents": [row[0] for row in connection.execute(
            "SELECT intent_id FROM dispatch_intents WHERE mission_id=? AND kind='attempt' ORDER BY intent_id",
            (mission_id,))],
        "external_handoffs": sorted((str(action["action_key"]), int(action.get("handoffs") or 0))
                                    for action in store.list_actions(mission_id)),
        "released_reservations": [row[0] for row in connection.execute(
            "SELECT reservation_id FROM budget_reservations WHERE mission_id=? AND state='RELEASED' "
            "ORDER BY reservation_id", (mission_id,))],
    }
