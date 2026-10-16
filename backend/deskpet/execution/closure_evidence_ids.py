"""收口证据的稳定编号与响应摘要（阶段 6E 从 closure_request_guard、post_turn_invoker 下沉，结果来源记录共用）。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from simple_harness import thaw_json

from deskpet.task_scope.protocol import canonical_json


def input_id(attempt_id):
    return "closure-attempt-input:" + attempt_id


def _response_hash(response: Any) -> str:
    message = getattr(response, "message", None)
    calls = tuple(getattr(response, "tool_calls", ()) or ())
    payload = {
        "content": str(getattr(message, "content", "")),
        "tool_calls": [
            {"name": call.name, "arguments": json.loads(canonical_json(_thaw(call.arguments)))}
            for call in calls
        ],
        "finish_reason": getattr(response, "finish_reason", None),
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _thaw(value: Any) -> Any:

    return thaw_json(value)
