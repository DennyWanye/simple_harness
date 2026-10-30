# 任务书 F2（给 gpt-6.1-sol / Codex）：任务书 F 的补修

日期：2026-11-01。发包人与审核人：Claude 会话。在**同一工作树** `/Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-F`、同一分支 `sol/F-action-tools` 上继续，基线是你的 `4a31b143b`。先 `cd /Users/taiwan/PROJECTS/SimplaHarness/simple_harness-sol-F/sdk/simple-harness-sdk`。

## 0. 先并入 main，再动手

main 新提交 `668266aa7` 改了 `host/inbox.py::catch_up`：追完最后一页事件后把游标补到原始事件尾（对外事件页隐去 `RowsWritten` 等内部事件，游标以前永远追不上，三个子会话空闲 CPU 30%）。在工作树里先 `git merge main`，解掉 `inbox.py` 的冲突，保留两边改动。你的第 2 条改法要建立在这个结构上。

## 1. 审核裁定（你记录里的两条 BLOCKED）

1. **`HostSettings.max_actions_per_turn: int = 4` 正式加进 `host/settings.py`**（本任务书放宽车道允许改这个文件）。`action_tools.py` 直接读 `settings.max_actions_per_turn`，删掉 `getattr(..., 4)`。
2. **`runtime/connectors.py` 第 129 行的 E501 修掉**（把那行注释折行）。允许改。

## 2. 审阅子代理的阻断级（必须）

### 2.1 每拍全表扫描授权单并开写事务（`inbox.py:34` → `action_tools.recover`）

现状：`catch_up` 每 0.25 秒调 `recover()`，扫整张根库 `authorization_requests`，每条已批准/已拒绝的动作单都开一次 `BEGIN IMMEDIATE`。父会话和每个子会话各扫一遍，成本随历史无界增长。
改法：
- 批准/拒绝落账的那一刻就把 `action_followup` 排进队列——在 `Session.respond()` 处理动作单的同一事务里写 `queued_inputs`，不留"批准了但没入队"的间隙。
- `recover()` 只在会话打开/重开时跑一次（放进 `Session.recover()` 首次恢复那一段或 `_open_child`），**从 `catch_up` 里删掉**。
- `responded()` 先只读检查队列行是否已存在，缺了才开事务。
- 测试：50 条已批准的动作单，闲置 3 秒（泵跑 12 拍），账本写事务次数为 0（可数 `store.event` 或包一层计数）。

### 2.2 每拍重读已结束任务的执行库（`inbox.py:39` → `observe_task_actions`）

现状：`observe_task_actions` 放在"事件尾 ≤ 游标"跳过判断之前，对每个委派每拍 `list_actions(mission_id)`、重新解析全部回执、每条开一次写事务，已结束的任务也不例外。
改法：挪到跳过判断之后，只在该任务有新事件时观察；回执已在 `effects` 里的先只读查、不开事务。并入 main 之后游标能追到事件尾，终态任务自然只观察到最后一次。
测试：一个已完成、带 2 条动作的任务，连续 `catch_up()` 5 次，`list_actions` 只被调用 1 次（第一次追事件那回），`effects` 里恰好 2 行。

### 2.3 待批授权单的创建逻辑被复制（`action_tools.py:127-168`）

现状：几乎逐行复制了 `SessionAuthorization._apply` 的 `needs_approval` 分支（请求编号、版本、绑定、`needs_response` 事件），只差 `request_kind="action"`。
改法：给 `_apply` 加 `request_kind` 参数（默认 `"authorization"`），动作路径调用它，**删掉副本**。本任务书放宽车道允许改 `host/authorization.py` 的这一处。
测试：动作单与普通授权单除 `request_kind`/`request` 外字段集合相同（对同一个 `AuthorizationRequest` 断言两种路径产出的行的键集合一致）。

### 2.4 `getattr/hasattr` 回退掩盖缺失约定（违反"开发期不做兼容"）

- `action_tools.py:38` `getattr(host, "_session_handles", {})` → 直接 `host._session_handles`。
- `action_tools.py:178` `getattr(settings, "max_action_level", None)` → 直接 `settings.max_action_level`（它是已有字段）。
- `action_tools.py:297-299` `getattr(host, "store", None)` / `hasattr(store, "list_actions")` → 直接访问；缺了就报错，不静默跳过。
- 测试里的假宿主缺这些属性就补给假宿主，不在生产代码里兜。

## 3. 非阻断，一起改（都很小）

- 参数校验放在占槽位之前：无效调用（缺 target、缺必填参数）不消耗本回合动作次数。
- `denied_reason` 只在本次决定为 deny 时写入；进入新一次决定前先清掉旧值，别把以后别的原因报成"拒绝"。
- `ContinuationContext.delegation_id / mission_id` 标注改成 `str | None`（`host/continuation.py` 允许改这一处）。
- 文档 `docs/orchestration-host.md` 那句"子会话只得到同时声明 main 和 child 的操作"改准确：只声明 child 的操作仍进子会话目录、调用时被拒；或者改实现让它不进目录——选后者（目录与可调一致，少一条路径），并补一条断言。
- `invoke` 出错时全表扫 `authorization_requests` 的那处，改成按绑定键 `authorization_bindings` 直接取。

## 4. 完成标准与记录

- 改源码后先重生成部署清单（命令同记录里那条），再跑：`tests/orchestrator/host/test_action_tools.py`、`test_host_connectors.py`、`test_tool_catalog.py`、`test_authorization_port.py`、`test_session_tree.py`、`test_continuation.py`、`test_session.py`、`test_owner_and_close.py`、`test_host_recovery.py`、`tests/test_public_api.py`，全绿。交付前恢复清单，不提交清单。
- 改到的文件 `ruff check` / `ruff format --check` 绿（含 connectors.py）。
- 记录 `记录-任务书F-Sol.md` 末尾追加一节"F2 补修"：并入 main 的提交、每条改了什么、测试命令与通过数、提交号。中文。结尾一行 `VERDICT: PASS|BLOCKED — 原因 — 日期 — 提交号`。
- 拿不准的按最合理做法做完并记录，不停下问。

## 5. 不动的

命令表、池编号、工具说明、任务层申请单/审阅/执行器、连接器协议不动；不加兼容分支；不推送、不合并、不跑全量回归；`ARCHITECTURE/` 由审核人合并时回写。
