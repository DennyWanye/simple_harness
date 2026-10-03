# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""阶段 C3：主 Agent 替用户查看全库做法、按用户的话退役一条（``method_library``）。

工具只把调用整理成一条命令（命令号由运行号与调用号派生，重放不重复写），经
``OrchestrationService`` 转给 SDK 门面；拒绝一律是清楚的工具失败。确认规矩与建任务、改要求相同。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deskpet.orchestration.chat_tool import MissionStartRefused, method_library
from deskpet.orchestration.service import OrchestrationRequestError
from deskpet.sdk_adapters.tool_authority import SDK_DIRECT_TOOL_KERNEL, describe_call_zh
from deskpet.sdk_adapters.tools import HOST_COMPOSED_TOOL_NAMES, PRODUCT_TOOL_NAMES

BACKEND = Path(__file__).parents[2]
ENTRY = {"entry_id": "lib-1", "owner": "t/u", "goal_type_id": "desktop.user-goal", "purpose": "一步写完一份笔记",
         "state": "LISTED", "source_mission_id": "m-1", "based_on": None, "promoted_at": 1.0,
         "retired_by": None, "retired_reason": None, "method_ref": "proposed-x@1", "blamed_by_missions": []}


class _Service:
    def __init__(self) -> None:
        self.entries = {ENTRY["entry_id"]: dict(ENTRY)}
        self.receipts: dict[str, dict] = {}

    def method_library(self):
        return {"entries": list(self.entries.values())}

    def retire_library_entry(self, request):
        if request["command_id"] in self.receipts:
            return self.receipts[request["command_id"]]
        entry = self.entries.get(request["entry_id"])
        if entry is None:
            raise OrchestrationRequestError("LIBRARY_ENTRY_UNKNOWN", "没有这条全库做法")
        entry.update(state="RETIRED", retired_by="user", retired_reason=request["reason"])
        receipt = {"entry_id": entry["entry_id"], "purpose": entry["purpose"], "command_id": request["command_id"]}
        self.receipts[request["command_id"]] = receipt
        return receipt


def test_list_retire_unknown_and_replay():
    service = _Service()
    listing = method_library(lambda: service, {"action": "list"}, run_id="r", call_id="c0")
    assert [(item["entry_id"], item["purpose"], item["state"]) for item in listing["entries"]] == [
        ("lib-1", "一步写完一份笔记", "LISTED")]
    args = {"action": "retire", "entry_id": "lib-1", "reason": "用户说这个拆法不好用"}
    first = method_library(lambda: service, args, run_id="r", call_id="c1")
    again = method_library(lambda: service, args, run_id="r", call_id="c1")
    assert first == again and first["state"] == "RETIRED"
    assert list(service.receipts) == ["chat-method-retire:r:c1"]
    with pytest.raises(MissionStartRefused) as refused:
        method_library(lambda: service, {**args, "entry_id": "lib-9"}, run_id="r", call_id="c2")
    assert refused.value.code == "LIBRARY_ENTRY_UNKNOWN" and "chat-method-retire:r:c2" not in service.receipts


@pytest.mark.parametrize("args", [
    {"action": "retire", "entry_id": "lib-1"},
    {"action": "list", "entry_id": "lib-1"},
    {"action": "delete"},
])
def test_bad_arguments_are_refused(args):
    with pytest.raises(MissionStartRefused) as refused:
        method_library(lambda: _Service(), args, run_id="r", call_id="c")
    assert refused.value.code == "invalid_arguments"


def test_the_tool_is_registered_with_a_chinese_confirmation():
    assert "method_library" in PRODUCT_TOOL_NAMES and "method_library" in HOST_COMPOSED_TOOL_NAMES
    assert "method_library" in SDK_DIRECT_TOOL_KERNEL
    text = describe_call_zh("method_library", {"action": "retire", "entry_id": "lib-1", "reason": "不好用"})
    assert "lib-1" in text and "不再列给新任务" in text
    assert describe_call_zh("method_library", {"action": "list"}) == "查看全库做法"
    assert "core.method_library.v1" in (BACKEND / "main.py").read_text(encoding="utf-8")
