# SPDX-License-Identifier: Apache-2.0
"""终态写入点全枚举（Assurance 原计划 §7.1 AS-0；补齐清单 V03 / 保证 C-32）。

原计划："枚举所有 terminal 写入点，测试证明无旁路，不按某个文件名当完备。"此前的用例只数
``event_handler.py`` 里 4 个调用、再用正则扫 ``next_mission(...COMPLETED``——产品取消、接管停止、
判定写失败、命令行取消这些入口都不在枚举里，新增一条旁路测不出来。

这里按语法树扫整个 SDK 源码，列出每一处"把任务 / 步骤状态写成终态"的点和每一个"调终态写方"的
入口，与下面的显式白名单逐条比对：**多一处（新旁路）红，少一处（白名单过时）也红**。白名单每条
写明它为什么合法。扫描的写法（先 grep 全库列出）：

* ``next_mission(mission, MissionStatus.<终态>)``——唯一的任务状态变更构造（``state_machine.py``）；
* ``next_task(task, TaskStatus.<终态>)``——步骤同理；
* ``store.update_mission(...)`` 的每个调用者（终态与非终态都列，非终态写明"非终态"）；
* ``Store._cas("missions", ...)`` 与任何直接写 ``missions`` 表的 SQL 字面量；
* 调 ``cancel_mission / fail_mission / fail_planning / stop_task / judge_mission /
  finalize_assured_mission / request_assured_closeout`` 的每个入口。

数据类 ``replace(mission, status=...)`` 只在 ``next_mission`` 里出现（``test_only_the_final_writer_writes_completed``
之外另由 grep 确认：全库没有第二处）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import agent_orchestrator

SRC = Path(agent_orchestrator.__file__).resolve().parent
TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
ENTRY_METHODS = frozenset({
    "cancel_mission", "fail_mission", "fail_planning", "stop_task", "judge_mission",
    "finalize_assured_mission", "request_assured_closeout",
})
MISSIONS_SQL = ("UPDATE missions", "INTO missions", "DELETE FROM missions")

Site = tuple[str, str, str, str]  # (kind, file, enclosing function, detail)


def _terminal_status(call: ast.Call, enum: str) -> str | None:
    """``MissionStatus.X`` / ``TaskStatus.X`` passed positionally (2nd) or as ``status=``,
    including a conditional expression whose either branch is a terminal."""

    candidates = list(call.args[1:2]) + [kw.value for kw in call.keywords if kw.arg == "status"]

    def member(node: ast.AST) -> str | None:
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == enum and node.attr in TERMINAL):
            return node.attr
        return None

    for node in candidates:
        found = member(node)
        if found is None and isinstance(node, ast.IfExp):
            found = member(node.body) or member(node.orelse)
        if found is not None:
            return found
    return None


def _callee(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def terminal_write_sites(source: str, path: str = "<src>") -> list[Site]:
    """Every terminal write and every terminal-writer entry in one module."""

    out: list[Site] = []

    def visit(node: ast.AST, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, [*stack, child.name])
                continue
            where = ".".join(stack) or "<module>"
            if isinstance(child, ast.Call):
                name = _callee(child)
                if name == "next_mission":
                    status = _terminal_status(child, "MissionStatus")
                    if status is not None:
                        out.append(("mission", path, where, status))
                elif name == "next_task":
                    status = _terminal_status(child, "TaskStatus")
                    if status is not None:
                        out.append(("task", path, where, status))
                elif name == "update_mission":
                    out.append(("update_mission", path, where, ""))
                elif name == "_cas" and child.args and isinstance(child.args[0], ast.Constant) \
                        and child.args[0].value == "missions":
                    out.append(("sql", path, where, "_cas(missions)"))
                elif name in ENTRY_METHODS:
                    out.append(("entry", path, where, name))
            elif isinstance(child, ast.Constant) and isinstance(child.value, str):
                hit = next((verb for verb in MISSIONS_SQL if verb in child.value), None)
                if hit is not None:
                    out.append(("sql", path, where, hit))
            visit(child, stack)

    visit(ast.parse(source), [])
    return out


def scan_sdk() -> list[Site]:
    found: list[Site] = []
    for file in sorted(SRC.rglob("*.py")):
        found += terminal_write_sites(file.read_text(encoding="utf-8"), str(file.relative_to(SRC)).replace("\\", "/"))
    return found


# --------------------------------------------------------------------------- 白名单
#: 每条：(种类, 文件, 所在函数, 细节) → 为什么合法。
WHITELIST: dict[Site, str] = {
    # ---- 任务状态写成终态（6 处）
    ("mission", "orchestrator/assurance_final_writer.py", "finalize_assured_mission", "COMPLETED"):
        "唯一完成写方：收尾行重评为 READY 后，同一事务里写 COMPLETED、完成事件、回执、通知请求（§7.1）",
    ("mission", "orchestrator/commit_service.py", "CommitService.fail_planning", "FAILED"):
        "规划失败 / 池用尽停任务；只由主循环 _commit_fail_planning（先准备台账）调用；保留 UNKNOWN 与 hold，写未决动作",
    ("mission", "orchestrator/commit_service.py", "CommitService.cancel_mission", "CANCELLED"):
        "取消：人（门面 / SDK API / 主循环）或命令行；级联停止开着的工作、保留 UNKNOWN 与 hold、写未决动作、发终态通知",
    ("mission", "orchestrator/commit_service.py", "CommitService.fail_mission", "FAILED"):
        "任务级停止（预算 / 运行环境 / 运维条件）；只由主循环 _commit_fail_mission（先准备台账）调用",
    ("mission", "orchestrator/commit_service.py", "CommitService.judge_mission", "FAILED"):
        "判定'要求未满足'写失败并了结开着的动作；'满足'这一支不写完成、只请求收尾（request_assured_closeout）",
    ("mission", "orchestrator/commit_service.py", "CommitService.stop_task", "FAILED"):
        "一步停止连带任务失败（人接管停止 / 步骤重试用尽）；经 _commit_stop_task 台账钩子或 takeover",
    # ---- update_mission 的调用者（含非终态，列全以证明没有第二条写路径）
    ("update_mission", "orchestrator/assurance_final_writer.py", "request_assured_closeout", ""):
        "判定后把判定结果写进 final_report，状态仍 ACTIVE（非终态）",
    ("update_mission", "orchestrator/assurance_final_writer.py", "finalize_assured_mission", ""):
        "唯一完成写方落库",
    ("update_mission", "orchestrator/commit_service.py", "CommitService.begin_planning", ""):
        "CREATED→PLANNING（非终态）",
    ("update_mission", "orchestrator/commit_service.py", "CommitService.fail_planning", ""):
        "同上一组 fail_planning 落库",
    ("update_mission", "orchestrator/commit_service.py", "CommitService.cancel_mission", ""):
        "同上一组 cancel_mission 落库",
    ("update_mission", "orchestrator/commit_service.py", "CommitService.fail_mission", ""):
        "同上一组 fail_mission 落库",
    ("update_mission", "orchestrator/commit_service.py", "CommitService.judge_mission", ""):
        "同上一组 judge_mission 未满足落库",
    ("update_mission", "orchestrator/commit_service.py", "CommitService.stop_task", ""):
        "同上一组 stop_task 落库",
    ("update_mission", "orchestrator/plan_commits.py", "PlanCommitsMixin._activate_for_work", ""):
        "PLANNING→ACTIVE（非终态）",
    # ---- 直接写 missions 表的 SQL（只有存储层自己）
    ("sql", "storage/store.py", "Store.insert_mission", "INTO missions"):
        "建任务：出生态 CREATED，不是终态",
    ("sql", "storage/store.py", "Store.update_mission", "_cas(missions)"):
        "版本比对写回：所有 update_mission 调用者（上一组）都经过它",
    # ---- 步骤状态写成终态
    ("task", "orchestrator/commit_service.py", "CommitService._cascade_stop", "CANCELLED"):
        "任务终止（取消 / 失败 / 一步停止）时级联取消还开着的 READY / ACTIVE 步骤，开着的尝试一并关闭",
    ("task", "orchestrator/commit_service.py", "CommitService._accept_result", "COMPLETED"):
        "接受结果：没有效果的步骤直接完成；带效果的步骤只转 VERIFYING（条件表达式的另一支），等效果完成",
    ("task", "orchestrator/commit_service.py", "CommitService.judge_mission", "CANCELLED"):
        "判定时了结暂停着的 READY 路线（not_needed_paused）：任务不需要它了",
    ("task", "orchestrator/commit_service.py", "CommitService.stop_task", "FAILED"):
        "一步停止：步骤写失败，开着的尝试取消",
    ("task", "orchestrator/operation_outcomes.py", "_accept_operation_outcome", "COMPLETED"):
        "发布结果审阅通过、所属步骤完成范围齐备后，把 VERIFYING 的步骤写完成（原计划 L192 精确绑定）",
    # ---- 调终态写方的入口
    ("entry", "__main__.py", "cmd_mission", "cancel_mission"):
        "命令行取消（运维）：直接开库调 CommitService.cancel_mission",
    ("entry", "api/facade.py", "MissionControlV1.cancel", "cancel_mission"):
        "产品门面取消（人）：已终态幂等返回",
    ("entry", "api/missions.py", "MissionApi.cancel", "cancel_mission"):
        "SDK API 取消：先要求执行根",
    ("entry", "orchestrator/event_handler.py", "Orchestrator._commit_cancel_mission", "cancel_mission"):
        "主循环取消入口：先准备终态台账",
    ("entry", "orchestrator/event_handler.py", "Orchestrator._commit_fail_mission", "fail_mission"):
        "主循环任务级停止入口：先准备终态台账",
    ("entry", "orchestrator/event_handler.py", "Orchestrator._commit_fail_planning", "fail_planning"):
        "主循环规划失败入口：先准备终态台账",
    ("entry", "orchestrator/event_handler.py", "Orchestrator._commit_stop_task", "stop_task"):
        "主循环一步停止入口：先准备终态台账",
    ("entry", "orchestrator/human_commits.py", "HumanCommitsMixin.takeover", "stop_task"):
        "人接管停止一步（HumanOverride 先落库再 stop_task）",
    ("entry", "orchestrator/event_handler.py", "Orchestrator._judge", "judge_mission"):
        "主循环判定（纯内容任务）：判定只记'满足 / 不满足'，满足走收尾",
    ("entry", "orchestrator/event_handler.py", "Orchestrator._judge_with_actions", "judge_mission"):
        "主循环两段判定（带动作的任务）",
    ("entry", "orchestrator/commit_service.py", "CommitService.judge_mission", "request_assured_closeout"):
        "判定'满足'→请求收尾，不写完成",
    ("entry", "orchestrator/commit_service.py", "CommitService.finalize_assured_mission", "finalize_assured_mission"):
        "唯一完成写方的提交服务包装（生产只经收尾消费者的 finalizer 调用）",
}


def test_every_terminal_write_point_is_on_the_whitelist():
    """**改坏检验**：白名单去掉一条（比如命令行取消）→ 扫出来的点多于白名单 → 变红；源码里新加一处
    ``next_mission(..., MissionStatus.FAILED)`` 或新入口调 ``cancel_mission`` → 同样变红。"""
    found = scan_sdk()
    assert len(found) == len(set(found)), "同一函数里同一种写法出现两次：白名单按函数记，请拆开核对"
    unlisted = sorted(site for site in found if site not in WHITELIST)
    stale = sorted(site for site in WHITELIST if site not in set(found))
    assert not unlisted, f"终态写入点 / 入口不在白名单（新旁路？）：{unlisted}"
    assert not stale, f"白名单里的点源码里已不存在（白名单过时）：{stale}"
    assert all(reason.strip() for reason in WHITELIST.values())
    # 写 COMPLETED 的只有唯一完成写方；写终态的任务状态变更总共六处
    completed = [site for site in found if site[0] == "mission" and site[3] == "COMPLETED"]
    assert completed == [("mission", "orchestrator/assurance_final_writer.py", "finalize_assured_mission", "COMPLETED")]
    assert len([site for site in found if site[0] == "mission"]) == 6


def test_no_second_status_constructor_outside_the_state_machine():
    """``replace(mission, ...)`` 只在 ``state_machine.next_mission`` 里；``Mission(`` 不在编排层重建任务行。"""
    offenders = []
    for file in sorted(SRC.rglob("*.py")):
        rel = str(file.relative_to(SRC)).replace("\\", "/")
        text = file.read_text(encoding="utf-8")
        if "replace(mission" in text and rel != "orchestrator/state_machine.py":
            offenders.append(rel)
    assert offenders == []


def test_the_scan_catches_a_new_writer_in_every_written_form():
    snippet = (
        "def sneaky(m):\n"
        "    return next_mission(m, MissionStatus.COMPLETED)\n"
        "class Svc:\n"
        "    def stop(self, m, t):\n"
        "        self._store.update_mission(next_mission(m, status=MissionStatus.FAILED), expected_version=1)\n"
        "        self._store.update_task(next_task(t, TaskStatus.CANCELLED), expected_version=1)\n"
        "    def raw(self):\n"
        "        self._cas('missions', 'mission_id', 'x', 1, {})\n"
        "        self.connection.execute('UPDATE missions SET status=? WHERE mission_id=?', ('COMPLETED', 'x'))\n"
        "def api(commit):\n"
        "    return commit.cancel_mission('x')\n"
    )
    assert terminal_write_sites(snippet) == [
        ("mission", "<src>", "sneaky", "COMPLETED"),
        ("update_mission", "<src>", "Svc.stop", ""),
        ("mission", "<src>", "Svc.stop", "FAILED"),
        ("task", "<src>", "Svc.stop", "CANCELLED"),
        ("sql", "<src>", "Svc.raw", "_cas(missions)"),
        ("sql", "<src>", "Svc.raw", "UPDATE missions"),
        ("entry", "<src>", "api", "cancel_mission"),
    ]
    # 非终态的状态变更不算写入点
    assert terminal_write_sites("def go(m):\n    return next_mission(m, MissionStatus.ACTIVE)\n") == []
