# SPDX-License-Identifier: Apache-2.0
"""2026-10-22 Sol 复验局：规划器把一步换成后继步骤后，后继步骤的执行者看不到前一步已经做成的
动作（归档已移走的照片），于是说"回执缺失"、规划器转头问人。已结案的中途动作是这个任务里的事实，
同一任务里每一步的执行者都要看到，并标明是不是自己这一步提议的。"""

from types import SimpleNamespace

from agent_orchestrator.orchestrator import step_actions


def _action(task_id, key, state, created_at):
    return {
        "origin": "step",
        "task_id": task_id,
        "action_key": key,
        "request_id": f"req-{key}",
        "connector": "photos",
        "operation": "move",
        "target": f"/photos/{key}.jpg",
        "params": {"to": f"/archive/{key}.jpg"},
        "state": state,
        "receipt": {"applied": state == "SUCCEEDED", "after": {"path": f"/archive/{key}.jpg"}},
        "created_at": created_at,
    }


def test_a_successor_step_sees_the_actions_its_predecessor_already_executed():
    rows = [
        _action("task-old", "a", "SUCCEEDED", 1.0),
        _action("task-new", "b", "SUCCEEDED", 2.0),
        _action("task-new", "c", "PROPOSED", 3.0),  # 没结案的不给
        _action("task-old", "d", "REFUSED", 4.0),  # 门口拒掉的另走 refused_request_views
        {**_action("task-other", "e", "SUCCEEDED", 5.0), "origin": "handoff"},  # 不是中途动作
    ]
    store = SimpleNamespace(list_actions=lambda *_args: list(rows))
    views = step_actions.action_results(store, "m1", "task-new")
    assert [(v["action_key"], v["this_step"], v["task_id"]) for v in views] == [
        ("a", False, "task-old"),
        ("b", True, "task-new"),
    ]
    assert views[0]["state"] == "SUCCEEDED" and views[0]["applied"] is True
